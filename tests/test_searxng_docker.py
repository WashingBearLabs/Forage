"""Guards on the ``forage-searxng`` companion image Forage publishes.

Validates ``searxng/Dockerfile`` and ``searxng/config/`` without needing
Docker. The compose half of this module (the ``poppy-searxng`` service
definition and its ``compose_helpers.load_compose`` dependency) stayed behind
in Poppy at the repo split; the example compose is spec 4's work.

**What this module is really for.** The baked config is the only thing that
distinguishes this image from upstream's, and every one of its interesting
properties is a *removal*: no wildcard pass list, no baked secret, no
client-header trust, no limiter that would refuse the image's own consumer. A
removal leaves nothing behind to notice, so a future edit that quietly restores
one would be invisible at review. These are the negatives that make it visible.

The behavioural half lives in ``searxng_smoke.py`` (run in CI's
``searxng-smoke`` job): this module asserts what the config *says*, that one
asserts what the image *does*. Neither substitutes for the other — a config
with no ``pass_ip`` wildcard can still be a config whose limiter blocks Forage,
and only a running container can tell you.

test_mapping:
  searxng/config/settings.yml: tests/test_searxng_docker.py
  searxng/config/limiter.toml: tests/test_searxng_docker.py
  searxng/Dockerfile: tests/test_searxng_docker.py
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from pipeline.orchestrator import _SEARXNG_ENGINES

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SEARXNG_DIR = _REPO_ROOT / "searxng"
_SEARXNG_CONFIG = _SEARXNG_DIR / "config"
_SETTINGS_PATH = _SEARXNG_CONFIG / "settings.yml"
_LIMITER_PATH = _SEARXNG_CONFIG / "limiter.toml"
_DOCKERFILE_PATH = _SEARXNG_DIR / "Dockerfile"

# `searxng/searxng@sha256:<64 hex>` and nothing looser.
_BASE_IMAGE_RE = re.compile(r"^searxng/searxng@sha256:[0-9a-f]{64}$")
# The upstream release naming: a date-based version plus a short revision.
_BASE_VERSION_RE = re.compile(r"\b20\d{2}\.\d{1,2}\.\d{1,2}-[0-9a-f]{7,}\b")

# Settings keys that hand a caller control over its own identity. Present in
# the config this replaced; not schema keys in the pinned release at all, which
# is a second reason they must never come back.
_HEADER_TRUST_SETTINGS = ("forwarded_for_header", "real_ip_header")

# limiter.toml keys that widen who is believed or who is exempt. `pass_ip` is
# checked by value rather than by presence — an empty list is the honest way to
# say "no exceptions" — but `trusted_proxies` is checked by presence, because
# any value at all is a statement that some peer's headers can be believed.
_HEADER_TRUST_LIMITER_KEYS = ("trusted_proxies", "real_ip")

# Any route to "every address on the internet".
_WILDCARD_NETWORKS = {"0.0.0.0/0", "::/0", "0.0.0.0", "*"}


def _settings() -> dict[str, Any]:
    parsed: object = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "settings.yml must be a YAML mapping"
    return cast(dict[str, Any], parsed)


def _limiter() -> dict[str, Any]:
    return tomllib.loads(_LIMITER_PATH.read_text(encoding="utf-8"))


def _engine_entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    raw: object = settings.get("engines")
    assert isinstance(raw, list), "settings.yml must declare an `engines` list"
    entries = cast(list[object], raw)
    return [cast(dict[str, Any], e) for e in entries if isinstance(e, dict)]


def _dockerfile_instructions() -> list[str]:
    """Logical instructions: comments dropped, continuations joined."""
    instructions: list[str] = []
    buffer = ""
    for raw_line in _DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#") or (not buffer and not stripped):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        if buffer.strip():
            instructions.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        instructions.append(buffer.strip())
    return instructions


@pytest.fixture(scope="module")
def settings() -> dict[str, Any]:
    assert _SETTINGS_PATH.exists(), "settings.yml must exist at searxng/config/"
    return _settings()


@pytest.fixture(scope="module")
def limiter() -> dict[str, Any]:
    assert _LIMITER_PATH.exists(), "limiter.toml must exist at searxng/config/"
    return _limiter()


@pytest.fixture(scope="module")
def instructions() -> list[str]:
    assert _DOCKERFILE_PATH.exists(), "searxng/Dockerfile must exist"
    return _dockerfile_instructions()


# ---------------------------------------------------------------------------
# The settings the image bakes
# ---------------------------------------------------------------------------


class TestSearxngSettings:
    """Validate searxng/config/settings.yml."""

    def test_json_format_enabled(self, settings: dict[str, Any]) -> None:
        formats = settings.get("search", {}).get("formats", [])
        assert "json" in formats, (
            "`json` in search.formats is the reason this config exists — "
            "upstream's generated default ships html only, and "
            "pipeline/orchestrator.py is a JSON API client"
        )

    def test_server_port_8080(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["port"] == 8080

    def test_server_binds_all_interfaces(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["bind_address"] == "0.0.0.0"

    def test_request_timeout(self, settings: dict[str, Any]) -> None:
        assert settings["outgoing"]["request_timeout"] == 10

    def test_default_lang_english(self, settings: dict[str, Any]) -> None:
        assert settings["search"]["default_lang"] == "en"

    def test_overlays_upstream_defaults(self, settings: dict[str, Any]) -> None:
        # Without this the file *replaces* upstream's settings.yml rather than
        # overlaying it, and every default this config does not restate
        # disappears.
        assert settings.get("use_default_settings") is True


class TestNoBakedSecret:
    """``test_secret_key_set``, inverted — the point is now its absence.

    The old assertion was that a ``secret_key`` was present. It was — a fixed
    literal, in a repository that is about to be public, and still Poppy's live
    compose value until spec 6 rotates it. A known secret in a published image
    is not a secret; it signs the HTML UI's session cookies for every instance
    that ever pulled it. (The value is not written out here either; the third
    test below asserts its absence by name without quoting it twice.) Upstream
    declares ``secret_key`` with no default and the environment name
    ``SEARXNG_SECRET``, so leaving it out makes an unset variable a loud
    refusal to start — verified against the pinned digest by
    ``searxng_smoke.py``'s first phase.
    """

    def test_no_secret_key_in_the_baked_settings(
        self, settings: dict[str, Any]
    ) -> None:
        assert "secret_key" not in settings.get("server", {}), (
            "searxng/config/settings.yml bakes a server.secret_key. A published "
            "image must not ship one: every puller would share it. Require "
            "SEARXNG_SECRET at runtime instead (docs/searxng.md)."
        )

    def test_no_secret_key_literal_anywhere_in_the_file(self) -> None:
        # Whole file, comments included. A commented-out secret is still a
        # committed secret, and a copy-paste away from being live again.
        raw = _SETTINGS_PATH.read_text(encoding="utf-8")
        offenders = [
            line
            for line in raw.splitlines()
            if re.search(r"^\s*#?\s*secret_key\s*:", line)
        ]
        assert offenders == [], (
            f"`secret_key:` appears in settings.yml: {offenders}. Not even "
            "commented out — this file is published inside a public image."
        )

    def test_the_old_poppy_secret_is_gone(self) -> None:
        raw = _SETTINGS_PATH.read_text(encoding="utf-8")
        assert "poppy-searxng-internal" not in raw, (
            "The literal Poppy secret is back in the baked config. It is still "
            "Poppy's live compose value until spec 6 rotates it, and this image "
            "is going to a public registry."
        )


class TestNoIpTrustRelaxations:
    """The config-regression guard: no relaxation may return quietly."""

    def test_no_header_trust_settings(self, settings: dict[str, Any]) -> None:
        server = settings.get("server", {})
        offenders = [key for key in _HEADER_TRUST_SETTINGS if key in server]
        assert offenders == [], (
            f"settings.yml trusts client-supplied headers: {offenders}. Once "
            "this image is reachable directly, any caller can set "
            "X-Forwarded-For and choose its own rate-limit identity. Header "
            "trust belongs to whatever proxy actually terminates the "
            "connection, in that deployment's own overlay — and as of the "
            "pinned release these are not even schema keys any more."
        )

    def test_no_pass_ip_wildcard(self, limiter: dict[str, Any]) -> None:
        pass_ip = limiter.get("botdetection", {}).get("ip_lists", {}).get("pass_ip", [])
        offenders = [
            entry for entry in pass_ip if str(entry).strip() in _WILDCARD_NETWORKS
        ]
        assert offenders == [], (
            f"limiter.toml passlists {offenders}. `pass_ip = ['0.0.0.0/0']` — "
            "the value this config used to carry — gives every address on the "
            "internet unrestricted access, which disables the limiter for "
            "everyone while `limiter:` still reads as though it were on. The "
            "ip_lists method has priority over every other botdetection method."
        )

    def test_no_wildcard_anywhere_in_the_limiter_file(self) -> None:
        # The parsed check above only sees `pass_ip`. This one covers the whole
        # file, comments included, so a wildcard cannot arrive under a key this
        # module does not know about yet.
        raw = _LIMITER_PATH.read_text(encoding="utf-8")
        assert "0.0.0.0/0" not in raw and "::/0" not in raw, (
            "limiter.toml mentions an all-addresses network. Even in a comment "
            "it is one uncomment away from disabling bot detection entirely."
        )

    def test_no_header_trust_keys_in_the_limiter(self, limiter: dict[str, Any]) -> None:
        botdetection = limiter.get("botdetection", {})
        offenders = [key for key in _HEADER_TRUST_LIMITER_KEYS if key in botdetection]
        assert offenders == [], (
            f"limiter.toml sets {offenders}. `trusted_proxies` is the pinned "
            "release's replacement for the old forwarded-header settings, and "
            "any value in it says some peer's X-Forwarded-For may be believed. "
            "Upstream's default is loopback only, which is correct for an image "
            "reached directly over a container network; a deployment behind a "
            "proxy adds that proxy in its own overlay."
        )

    def test_searxng_org_passlist_disabled(self, limiter: dict[str, Any]) -> None:
        ip_lists = limiter.get("botdetection", {}).get("ip_lists", {})
        assert ip_lists.get("pass_searxng_org") is False, (
            "Upstream defaults `pass_searxng_org` to true, which grants a "
            "hardcoded set of SearXNG organisation IPs unrestricted access. "
            "Reasonable for a public instance that wants to be monitored by "
            "check.searx.space; an unasked-for relaxation in a sidecar nobody "
            "monitors."
        )


class TestSearxngLimiter:
    """The limiter settings, re-pointed at what the story actually landed.

    These two tests used to assert that ``limiter.toml`` existed and had more
    than ten characters of content, which no edit could ever fail. They now
    assert the two decisions the file encodes — both of them measured against
    the pinned digest by ``searxng_smoke.py`` rather than assumed.
    """

    def test_limiter_is_off_in_the_baked_config(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["limiter"] is False, (
            "settings.yml enables the limiter. Measured against the pinned "
            "digest with a real Valkey attached: Forage's httpx client is "
            "refused 429 on its FIRST request (botdetection's "
            "http_accept_language method — httpx sends no Accept-Language), and "
            "even a browser-shaped client gets four `format!=html` requests per "
            "hour (ip_limit.API_MAX, a module constant limiter.toml cannot "
            "raise). A limiter here does not rate-limit abuse of this API, it "
            "refuses this API. The opt-in for a deployment that needs one is "
            "SEARXNG_LIMITER=true plus SEARXNG_VALKEY_URL, documented in "
            "docs/searxng.md and exercised by searxng_smoke.py."
        )

    def test_public_instance_is_off(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["public_instance"] is False, (
            "`public_instance: true` forces link_token on in code and calls "
            "sys.exit(1) when no Valkey backend is configured. Both are wrong "
            "for a sidecar serving one API client on a private network."
        )

    def test_link_token_is_off(self, limiter: dict[str, Any]) -> None:
        ip_limit = limiter.get("botdetection", {}).get("ip_limit", {})
        assert ip_limit.get("link_token") is False, (
            "link_token treats any client that never fetches a token-carrying "
            "link as suspicious and drops it to BURST_MAX_SUSPICIOUS = 2. "
            "Measured on the pinned digest, a browser-shaped JSON client goes "
            "200 200 429 302 with it on, versus 200 200 200 429 with it off. It "
            "strictly narrows an already-narrow API budget."
        )

    def test_block_and_pass_lists_are_explicitly_empty(
        self, limiter: dict[str, Any]
    ) -> None:
        ip_lists = limiter.get("botdetection", {}).get("ip_lists", {})
        assert ip_lists.get("block_ip") == [], "block_ip must be explicitly empty"
        assert ip_lists.get("pass_ip") == [], "pass_ip must be explicitly empty"


# ---------------------------------------------------------------------------
# The engine set, and its tie to the code that queries it
# ---------------------------------------------------------------------------


class TestEngineParity:
    """The enabled engines and the engines Forage asks for must be one set.

    ``pipeline/orchestrator.py`` names the engines on every query, so the two
    lists are a contract with two ends. A name enabled here that Forage never
    asks for is dead config; a name Forage asks for that is disabled here is a
    request SearXNG answers with nothing, and the failure surfaces as thin
    results rather than as an error.

    ``_SEARXNG_ENGINES`` is a comma-joined **string**, not a list — reading it
    as a sequence would compare a set of engine names against a set of single
    characters and pass for the wrong reason.
    """

    def test_orchestrator_engines_is_a_comma_joined_string(self) -> None:
        assert isinstance(_SEARXNG_ENGINES, str)
        assert "," in _SEARXNG_ENGINES

    def test_enabled_engines_match_the_orchestrator(
        self, settings: dict[str, Any]
    ) -> None:
        enabled = {
            entry["name"]
            for entry in _engine_entries(settings)
            if entry.get("disabled") is not True
        }
        requested = {name.strip() for name in _SEARXNG_ENGINES.split(",")}
        assert enabled == requested, (
            f"searxng/config/settings.yml enables {sorted(enabled)} but "
            f"pipeline/orchestrator.py queries {sorted(requested)}. Keep the "
            "two in step: an engine in one and not the other is either dead "
            "config or a silently empty half of every search."
        )

    def test_engines_are_pinned_rather_than_inherited(
        self, settings: dict[str, Any]
    ) -> None:
        assert _engine_entries(settings), (
            "The config must name its engines. With `use_default_settings: "
            "true` and no engine list, upstream releases keep enabling engines "
            "this repository never vetted (observed live 2026-08-19: aol, "
            "'karmasearch videos')."
        )


class TestDisabledEngines:
    """``test_engines_configured``, inverted — bing is now the negative.

    The old assertion was ``"bing" in engine_names``, which passed on an entry
    reading ``disabled: true``: membership in the list says nothing about
    whether an engine runs. What matters about bing is precisely that it does
    *not* — it blocks homelab and cloud IP ranges aggressively — so the
    assertion is now about its state, not its presence.
    """

    def test_bing_is_listed_and_disabled(self, settings: dict[str, Any]) -> None:
        entries = {entry["name"]: entry for entry in _engine_entries(settings)}
        assert "bing" in entries, (
            "bing must stay *listed* even though it is off: with "
            "`use_default_settings: true`, an entry is what stops an upstream "
            "release re-enabling it."
        )
        assert entries["bing"].get("disabled") is True, (
            "bing is enabled. It blocks homelab and cloud IP ranges "
            "aggressively, which shows up as an engine that is simply always "
            "unresponsive."
        )

    def test_every_disabled_engine_is_disabled_for_a_reason(
        self, settings: dict[str, Any]
    ) -> None:
        disabled = {
            entry["name"]
            for entry in _engine_entries(settings)
            if entry.get("disabled") is True
        }
        assert disabled == {"bing", "ahmia"}, (
            f"Disabled engines are {sorted(disabled)}, expected "
            "['ahmia', 'bing']. `torch` and `karmasearch` were removed on "
            "purpose: neither module exists in the pinned release, and naming a "
            "missing engine is not a no-op — SearXNG logs `Cannot load engine` "
            "with a traceback at startup for each one, disabled or not. Adding "
            "a name here is only meaningful if upstream still ships it."
        )


# ---------------------------------------------------------------------------
# searxng/Dockerfile
# ---------------------------------------------------------------------------


class TestSearxngDockerfile:
    """The companion image is a pinned base plus a COPY, and nothing else."""

    def test_single_from_instruction(self, instructions: list[str]) -> None:
        froms = [
            line[5:].strip()
            for line in instructions
            if line.upper().startswith("FROM ")
        ]
        assert len(froms) == 1, f"Expected exactly one FROM; found {froms}"

    def test_base_image_is_digest_pinned(self, instructions: list[str]) -> None:
        (base,) = [
            line[5:].strip()
            for line in instructions
            if line.upper().startswith("FROM ")
        ]
        assert _BASE_IMAGE_RE.match(base), (
            f"FROM {base!r} must be `searxng/searxng@sha256:<64 hex>`. Poppy "
            "pulled `:latest` at deploy time, so the config a smoke proved and "
            "the image an operator ran were different builds. Bumping the pin "
            "is a deliberate act with its own `searxng-v*` tag — see "
            "docs/searxng.md."
        )

    def test_base_version_recorded_in_an_adjacent_comment(self) -> None:
        # Dockerfile has no inline comments (a `#` after an instruction's
        # arguments is another argument, and FROM rejects it — proven in
        # US-003), so the release a digest denotes lives on a comment line
        # above it. A bare 64-hex string cannot be maintained.
        lines = _DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()
        from_index = next(
            (i for i, line in enumerate(lines) if line.startswith("FROM ")), None
        )
        assert from_index is not None, "No FROM instruction found"
        preceding = lines[max(0, from_index - 6) : from_index]
        assert any(
            line.lstrip().startswith("#") and _BASE_VERSION_RE.search(line)
            for line in preceding
        ), (
            "The comment lines above FROM must record the upstream release the "
            f"pinned digest resolved to (e.g. `2026.9.7-3e454637f`). Got: "
            f"{preceding!r}"
        )

    def test_config_is_baked_not_mounted(self, instructions: list[str]) -> None:
        copies = [
            line[5:].strip()
            for line in instructions
            if line.upper().startswith("COPY ")
        ]
        assert any("config/" in text and "/etc/searxng" in text for text in copies), (
            f"The image must COPY config/ to /etc/searxng/; got {copies!r}. A "
            "bind mount would mean consumers carry the config themselves, "
            "which is the arrangement this image exists to replace."
        )

    def test_no_extra_build_steps(self, instructions: list[str]) -> None:
        keywords = {line.split(maxsplit=1)[0].upper() for line in instructions}
        assert keywords <= {"FROM", "COPY"}, (
            f"searxng/Dockerfile grew instructions beyond FROM/COPY: "
            f"{sorted(keywords)}. Everything this image needs to say, it says "
            "in the baked config; an entrypoint wrapper or a RUN step here is a "
            "fork of upstream by another name, and one more thing to re-verify "
            "at every pin bump."
        )
