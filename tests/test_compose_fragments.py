"""Guards on the example compose fragments under ``compose/``.

``compose/minimal.yml`` is the extraction epic's completion-criterion artifact
— "a third party can ``docker compose up`` with only ``HF_TOKEN``" — and
``compose/full.yml`` is the same deployment with a real Valkey under the
content cache. Both are *configuration this repository ships for other people
to run*, and configuration regressions are the ones that bit the companion
SearXNG image twice: a removal leaves nothing behind to notice at review, and
an example that stops working fails on a stranger's laptop rather than in CI.

Three layers guard the fragments, and they answer different questions:

* CI's ``lint`` job runs ``docker compose config -q`` on both — *Compose
  accepts them*;
* this module parses them — *they say the right things*;
* US-004's recorded manual smoke — *they actually serve traffic*.

Only the middle one runs on every commit and can fail on the specific line
that moved, which is why the checks below are properties rather than a
snapshot. **Like ``tests/test_dockerfile.py``, this module needs no Docker.**
Shelling out to the Compose CLI here would make the whole suite depend on a
daemon being installed; the workflow step is the right home for that half.

The properties that matter most are the ones a well-meaning edit would undo:

* **service names are load-bearing.** Forage's ``SEARXNG_URL`` default is the
  neutral ``http://searxng:8080``, so a service renamed ``forage-searxng``
  breaks the fragment's own ``/search`` round-trip while still validating;
* **``VALKEY_URL`` must be absent from ``minimal.yml`` entirely** — only a
  *fully unset* variable selects memory mode, and ``VALKEY_URL=${VALKEY_URL}``
  in a compose file is the canonical way to produce the empty string that
  reads as a Valkey you asked for and did not get;
* **no limiter, anywhere.** Measured on the pinned digest, SearXNG's limiter
  refuses this image's only client with HTTP 429 on the first request;
* **loopback-only publishing.** Forage ships no authentication;
* **the duplication between the two fragments must not drift.** ``full.yml``
  deliberately repeats ``minimal.yml`` rather than extending it, so the shared
  invariants are asserted across both.

test_mapping:
  compose/minimal.yml: tests/test_compose_fragments.py
  compose/full.yml: tests/test_compose_fragments.py
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
import yaml

from cache import ContentCache
from pipeline.orchestrator import _DEFAULT_SEARXNG_URL

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_DIR = _REPO_ROOT / "compose"
_MINIMAL_PATH = _COMPOSE_DIR / "minimal.yml"
_FULL_PATH = _COMPOSE_DIR / "full.yml"
_DOCKERFILE_PATH = _REPO_ROOT / "Dockerfile"
_WEIGHTS_DOC_PATH = _REPO_ROOT / "docs" / "weights.md"
_README_PATH = _REPO_ROOT / "README.md"
_CI_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

_FRAGMENT_PATHS = {"minimal": _MINIMAL_PATH, "full": _FULL_PATH}
_FRAGMENTS = sorted(_FRAGMENT_PATHS)

# The service name is a contract with `pipeline.orchestrator`, not a label.
_SEARXNG_SERVICE = "searxng"
_FORAGE_SERVICE = "forage"

# `127.0.0.1:8020:8020` and nothing looser. A two-part `8020:8020` publishes on
# every interface, which for an unauthenticated SSRF-capable service is the
# whole of the deployment posture undone in one edit.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})

# `<repo>:<tag>` where the tag is an explicit semver-shaped version, optionally
# followed by a digest. Deliberately refuses `latest` (does not exist for
# either image yet, and is a moving pointer when it does), a bare repository,
# and the `sha-<short>` tags every main push produces — a real tag, but not one
# an example should teach anyone to pull.
_PINNED_IMAGE_RE = re.compile(
    r"^(?P<repo>[a-z0-9][a-z0-9._/-]*)"
    r":(?P<tag>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)"
    r"(?:@sha256:(?P<digest>[0-9a-f]{64}))?$"
)
# Anything that is not one of the two images this repository publishes must be
# pinned all the way to a digest: a third party runs these files, and a
# floating third-party tag means the image they get is not the one anyone here
# ever looked at.
_DIGEST_PINNED_RE = re.compile(r"@sha256:[0-9a-f]{64}$")

# `${NAME:?message}` — the required-or-fail interpolation form.
_REQUIRED_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")

# docs/weights.md § "The model cache volume" pins the canonical spelling:
# "The weights live in a Docker volume named **`forage-model-cache`**".
_DOCUMENTED_VOLUME_RE = re.compile(r"volume named \*\*`([a-z0-9][a-z0-9-]*)`\*\*")

_DOCKERFILE_HF_HOME_RE = re.compile(r"^ENV\s+HF_HOME=(\S+)\s*$", re.MULTILINE)


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


@pytest.fixture(scope="module")
def fragments() -> dict[str, dict[str, Any]]:
    return {name: _load(path) for name, path in _FRAGMENT_PATHS.items()}


@pytest.fixture(scope="module")
def raw_fragments() -> dict[str, str]:
    return {name: path.read_text() for name, path in _FRAGMENT_PATHS.items()}


def _services(fragment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], fragment.get("services") or {})


def _environment(service: dict[str, Any]) -> dict[str, str | None]:
    """Normalise both `environment:` forms to a mapping.

    Compose accepts a mapping (`NAME: value`) and a list (`NAME=value`, or a
    bare `NAME` that passes the host's value through and leaves the variable
    genuinely unset when the host has none). The fragments use both, on
    purpose, so every assertion below has to read them the same way.
    """
    raw = service.get("environment")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        mapping = cast(dict[str, Any], raw)
        return {
            str(key): None if value is None else str(value)
            for key, value in mapping.items()
        }
    entries = cast(list[Any], raw)
    normalised: dict[str, str | None] = {}
    for entry in entries:
        text = str(entry)
        name, separator, value = text.partition("=")
        normalised[name] = value if separator else None
    return normalised


def _published_ports(service: dict[str, Any]) -> list[str]:
    return [str(entry) for entry in cast(list[Any], service.get("ports") or [])]


def _volume_mounts(service: dict[str, Any]) -> list[str]:
    return [str(entry) for entry in cast(list[Any], service.get("volumes") or [])]


def _comment_prose(raw_text: str) -> str:
    """Every whole-line comment, flowed into one lowercase string.

    The fragments' comments wrap at ~75 columns, so a sentence a human reads as
    one runs across several lines. Flowing them lets a test assert that
    something is *documented* without also asserting where it was wrapped.
    """
    return " ".join(
        line.strip().lstrip("#").strip()
        for line in raw_text.splitlines()
        if line.strip().startswith("#")
    ).lower()


def _documented_volume_name() -> str:
    match = _DOCUMENTED_VOLUME_RE.search(_WEIGHTS_DOC_PATH.read_text())
    assert match is not None, (
        "docs/weights.md § 'The model cache volume' no longer states the "
        "volume name in the form these fragments cite it from. It is the "
        "canonical spelling; if it moved, move this regex with it rather than "
        "restating the literal here."
    )
    return match.group(1)


def _image_hf_home() -> str:
    match = _DOCKERFILE_HF_HOME_RE.search(_DOCKERFILE_PATH.read_text())
    assert match is not None, "Dockerfile no longer sets HF_HOME with a plain ENV"
    return match.group(1)


def _workflow_env(name: str) -> str:
    workflow = cast(dict[str, Any], yaml.safe_load(_CI_WORKFLOW_PATH.read_text()))
    env_block = cast(dict[str, Any], workflow.get("env") or {})
    return str(env_block[name])


class TestTheFragmentsAreCommitted:
    """They ship, or the completion criterion has no artifact."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_fragment_exists(self, name: str) -> None:
        assert _FRAGMENT_PATHS[name].exists(), (
            f"compose/{name}.yml must be committed — CI's lint job runs "
            "`docker compose config -q` against it and the epic's completion "
            "criterion is demonstrated with it"
        )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_fragment_declares_services(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        services = _services(fragments[name])
        assert _FORAGE_SERVICE in services, f"compose/{name}.yml defines no forage"
        assert _SEARXNG_SERVICE in services, f"compose/{name}.yml defines no searxng"

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_fragment_sets_a_project_name(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        # Without it the project name comes from the directory — `compose` for
        # both files — and the two fragments would fight over one project's
        # containers and networks.
        project = str(fragments[name].get("name", ""))
        assert project, f"compose/{name}.yml must set a top-level `name:`"
        assert name in project, (
            f"compose/{name}.yml's project name {project!r} should name the "
            "mode, so `docker compose ls` distinguishes the two"
        )

    def test_the_two_fragments_use_different_project_names(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        names = {str(fragment.get("name", "")) for fragment in fragments.values()}
        assert len(names) == len(fragments), (
            f"Both fragments claim the same compose project {names}; bringing "
            "one up would tear the other's containers down"
        )


class TestDeploymentPosture:
    """Forage ships no authentication — the port binding is the first control."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_every_published_port_binds_to_loopback(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        for service_name, service in _services(fragments[name]).items():
            for entry in _published_ports(service):
                parts = entry.split(":")
                assert len(parts) == 3, (
                    f"compose/{name}.yml publishes {entry!r} from "
                    f"{service_name}: a two-part mapping binds every "
                    "interface, which for an unauthenticated SSRF-capable "
                    "service is the deployment posture undone in one edit"
                )
                assert parts[0] in _LOOPBACK_HOSTS, (
                    f"compose/{name}.yml publishes {entry!r} from "
                    f"{service_name} on {parts[0]!r}; the fragments bind "
                    f"loopback only ({sorted(_LOOPBACK_HOSTS)})"
                )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_only_forage_publishes_anything(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        # SearXNG is not built for direct exposure (its limiter refuses API
        # clients, so it has no working rate limit to be exposed behind) and
        # the Valkey in `full.yml` is passwordless *because* nothing outside
        # the compose network can reach it. Both facts stop being true the
        # moment either publishes a port.
        for service_name, service in _services(fragments[name]).items():
            if service_name == _FORAGE_SERVICE:
                continue
            assert _published_ports(service) == [], (
                f"compose/{name}.yml publishes ports from {service_name}. "
                "Only Forage's own port is published, and only to loopback."
            )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_forage_publishes_its_service_port(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        ports = _published_ports(_services(fragments[name])[_FORAGE_SERVICE])
        assert ports, f"compose/{name}.yml publishes no Forage port at all"
        assert any(entry.endswith(":8020") for entry in ports), (
            f"compose/{name}.yml never maps container port 8020, which is the "
            f"port the image EXPOSEs and serves on; got {ports}"
        )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_posture_is_stated_in_the_file(
        self, raw_fragments: dict[str, str], name: str
    ) -> None:
        prose = _comment_prose(raw_fragments[name])
        assert "no authentication" in prose, (
            f"compose/{name}.yml must repeat the deployment posture. A reader "
            "who copies a compose file may never open the README."
        )
        assert "127.0.0.1" in prose, (
            f"compose/{name}.yml must say why the binding is loopback-only, "
            "not merely be loopback-only"
        )


class TestServiceNamesMatchTheCode:
    """The neutral hostnames in the code are what make these fragments work."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_searxng_service_is_named_for_the_code_default(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        expected = urlsplit(_DEFAULT_SEARXNG_URL).hostname
        assert expected == _SEARXNG_SERVICE, (
            "pipeline.orchestrator._DEFAULT_SEARXNG_URL no longer points at a "
            f"host called {_SEARXNG_SERVICE!r} ({_DEFAULT_SEARXNG_URL!r}); the "
            "fragments' service name must move with it"
        )
        assert expected in _services(fragments[name]), (
            f"compose/{name}.yml has no service called {expected!r}. Forage's "
            f"SEARXNG_URL default is {_DEFAULT_SEARXNG_URL!r}, so a service "
            "renamed `forage-searxng` validates fine and then fails the "
            "fragment's own /search round-trip."
        )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_fragments_do_not_set_searxng_url(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        # Setting it would work, and would also hide the coupling above: the
        # point of the neutral default is that a fragment needs no wiring at
        # all, and that property is only true while nothing wires it.
        environment = _environment(_services(fragments[name])[_FORAGE_SERVICE])
        assert "SEARXNG_URL" not in environment, (
            f"compose/{name}.yml sets SEARXNG_URL. The code default "
            f"({_DEFAULT_SEARXNG_URL!r}) already names the service; wiring it "
            "explicitly makes the service name look renameable when it is not."
        )


class TestTheSearxngSecret:
    """Unset, upstream exits 1 — so the fragment has to pass it, loudly."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_secret_is_passed_through(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        environment = _environment(_services(fragments[name])[_SEARXNG_SERVICE])
        assert "SEARXNG_SECRET" in environment, (
            f"compose/{name}.yml does not pass SEARXNG_SECRET. There is no "
            "baked secret and no default: upstream's own schema makes an "
            "unset one a hard start failure, so `docker compose up` would die "
            "on the first try — which is exactly what this fragment exists to "
            "make impossible."
        )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_secret_is_required_not_defaulted(
        self, raw_fragments: dict[str, str], name: str
    ) -> None:
        assert "${SEARXNG_SECRET:?" in raw_fragments[name], (
            f"compose/{name}.yml must demand SEARXNG_SECRET with the "
            "required-or-fail form `${SEARXNG_SECRET:?…}`. A `:-` default "
            "would ship a known secret, which is worse than no secret; a bare "
            "`${SEARXNG_SECRET}` renders the empty string and moves the "
            "failure from Compose's message into a container's exit code."
        )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_no_secret_value_is_baked_in(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        value = _environment(_services(fragments[name])[_SEARXNG_SERVICE])[
            "SEARXNG_SECRET"
        ]
        assert value is not None and value.startswith("${"), (
            f"compose/{name}.yml carries a literal SEARXNG_SECRET value "
            f"({value!r}). It must interpolate from the operator's "
            "environment, never ship one."
        )


class TestNoLimiter:
    """Turning SearXNG's limiter on is an outage, not protection."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_no_service_enables_the_limiter(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        for service_name, service in _services(fragments[name]).items():
            keys = set(_environment(service))
            assert "SEARXNG_LIMITER" not in keys, (
                f"compose/{name}.yml wires SEARXNG_LIMITER on {service_name}. "
                "Measured on the pinned digest: a working limiter 429s "
                "Forage's own httpx client on its FIRST request, so this "
                "fragment would fail its own /search round-trip. The opt-in "
                "belongs in an operator's overlay — docs/searxng.md."
            )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_no_service_wires_a_limiter_backend(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        # `SEARXNG_VALKEY_URL` is only meaningful with the limiter on, so
        # wiring it is either inert or the first half of the outage above. It
        # is also the variable most likely to be added by mistake once
        # `full.yml` has a Valkey sitting right there — a *different* Valkey,
        # for the content cache.
        for service_name, service in _services(fragments[name]).items():
            keys = set(_environment(service))
            for key in ("SEARXNG_VALKEY_URL", "SEARXNG_REDIS_URL"):
                assert key not in keys, (
                    f"compose/{name}.yml wires {key} on {service_name}. It "
                    "only matters together with SEARXNG_LIMITER, which these "
                    "fragments deliberately leave off; the Valkey in full.yml "
                    "is the content cache's, not SearXNG's."
                )


class TestTheModelCacheVolume:
    """One literal, pinned by docs/weights.md, cited rather than restated."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_volume_is_declared_under_the_documented_name(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        documented = _documented_volume_name()
        volumes = cast(dict[str, Any], fragments[name].get("volumes") or {})
        assert documented in volumes, (
            f"compose/{name}.yml declares no {documented!r} volume. "
            "docs/weights.md pins that spelling and these fragments cite it."
        )
        declared = cast(dict[str, Any], volumes[documented] or {})
        assert declared.get("name") == documented, (
            f"compose/{name}.yml must give the volume an explicit "
            f"`name: {documented}`, or Compose namespaces it per project and "
            "the two fragments stop sharing one verified weight set — a "
            "~270 MiB re-download every time you switch modes."
        )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_forage_mounts_it_at_the_images_hf_home(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        expected = f"{_documented_volume_name()}:{_image_hf_home()}"
        mounts = _volume_mounts(_services(fragments[name])[_FORAGE_SERVICE])
        assert expected in mounts, (
            f"compose/{name}.yml must mount {expected!r} — the Dockerfile's "
            f"own HF_HOME. Mounting anywhere else writes the weights where "
            f"the loader does not read them; got {mounts}"
        )


class TestImagePins:
    """A third party runs these files: every tag has to be pullable today."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_every_image_is_pinned(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        for service_name, service in _services(fragments[name]).items():
            image = str(service.get("image", ""))
            assert image, f"compose/{name}.yml: {service_name} declares no image"
            assert ":" in image, (
                f"compose/{name}.yml: {service_name} pulls {image!r} with no "
                "tag, which means `latest`"
            )
            assert not image.endswith(":latest"), (
                f"compose/{name}.yml: {service_name} pulls {image!r}. `latest` "
                "does not exist for either published image yet — it starts at "
                "the first non-pre-release `v*` tag — and it is a moving "
                "pointer when it does."
            )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_published_images_carry_explicit_version_tags(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        expected_repos = {
            _FORAGE_SERVICE: _workflow_env("IMAGE_NAME"),
            _SEARXNG_SERVICE: _workflow_env("SEARXNG_IMAGE_NAME"),
        }
        for service_name, expected_repo in expected_repos.items():
            image = str(_services(fragments[name])[service_name]["image"])
            match = _PINNED_IMAGE_RE.match(image)
            assert match is not None, (
                f"compose/{name}.yml: {service_name} pulls {image!r}, which is "
                "not `<repo>:<x.y.z[-pre]>`. A `sha-<short>` tag is real but "
                "teaches the wrong habit, and an unpinned one is not an "
                "example anyone can reproduce."
            )
            assert match.group("repo") == expected_repo, (
                f"compose/{name}.yml: {service_name} pulls "
                f"{match.group('repo')!r}, but ci.yml publishes "
                f"{expected_repo!r}. The fragments must name the repository "
                "this repository actually pushes to."
            )

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_third_party_images_are_digest_pinned(
        self, fragments: dict[str, dict[str, Any]], name: str
    ) -> None:
        ours = {_workflow_env("IMAGE_NAME"), _workflow_env("SEARXNG_IMAGE_NAME")}
        for service_name, service in _services(fragments[name]).items():
            image = str(service.get("image", ""))
            if any(image.startswith(f"{repo}:") for repo in ours):
                continue
            assert _DIGEST_PINNED_RE.search(image), (
                f"compose/{name}.yml: {service_name} pulls {image!r} without a "
                "digest. Third-party images get pinned all the way down, the "
                "same way searxng/Dockerfile pins its base — a floating tag "
                "means the image a reader runs is not one anyone here looked "
                "at."
            )


class TestMinimalIsGenuinelyValkeyFree:
    """Only a *fully unset* VALKEY_URL selects the in-memory backend."""

    def test_no_service_carries_valkey_url(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        for service_name, service in _services(fragments["minimal"]).items():
            assert "VALKEY_URL" not in _environment(service), (
                f"compose/minimal.yml sets VALKEY_URL on {service_name}. Any "
                "value at all — including the empty string a bare "
                "`${VALKEY_URL}` renders — is a Valkey the operator asked for, "
                "and an unreachable one reports `degraded: cache_unavailable` "
                "rather than the healthy memory mode this fragment exists to "
                "demonstrate."
            )

    def test_the_fragment_runs_no_cache_backend_of_its_own(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        services = _services(fragments["minimal"])
        assert set(services) == {_FORAGE_SERVICE, _SEARXNG_SERVICE}, (
            "compose/minimal.yml is the two-container deployment: Forage plus "
            f"SearXNG and nothing else; got {sorted(services)}"
        )

    def test_the_header_names_the_completion_criterion(
        self, raw_fragments: dict[str, str]
    ) -> None:
        prose = _comment_prose(raw_fragments["minimal"])
        assert "completion criterion" in prose, (
            "compose/minimal.yml's header must say that it is the extraction "
            "epic's completion-criterion artifact. A file nobody knows is "
            "load-bearing gets edited as if it were not."
        )
        assert "hf_token" in prose, (
            "The header must state the criterion itself — `docker compose up` "
            "with only HF_TOKEN — not merely refer to it"
        )

    def test_the_header_explains_the_absent_valkey(
        self, raw_fragments: dict[str, str]
    ) -> None:
        # The absence is the feature, and an absence leaves nothing behind to
        # notice: without this note, "the example forgot to configure a cache"
        # is the obvious reading and a helpful edit follows.
        prose = _comment_prose(raw_fragments["minimal"])
        assert "valkey_url" in prose and "unset" in prose, (
            "compose/minimal.yml must explain why VALKEY_URL is absent rather "
            "than leaving the absence to be read as an omission"
        )


class TestFullWiresValkeyLiterally:
    """The one line that distinguishes the two fragments, and its traps."""

    def test_valkey_url_is_set_on_forage(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        environment = _environment(_services(fragments["full"])[_FORAGE_SERVICE])
        assert environment.get("VALKEY_URL"), (
            "compose/full.yml must set VALKEY_URL on the forage service — it "
            "is the whole difference between the two fragments"
        )

    def test_valkey_url_is_a_literal_not_an_interpolation(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        value = _environment(_services(fragments["full"])[_FORAGE_SERVICE])[
            "VALKEY_URL"
        ]
        assert value is not None and "${" not in value, (
            f"compose/full.yml interpolates VALKEY_URL ({value!r}). "
            "`VALKEY_URL=${VALKEY_URL}` renders the empty string whenever the "
            "host variable is unset, and an empty value is a configured "
            "Valkey that cannot be reached — `degraded: cache_unavailable`, "
            "silently, in the fragment whose entire point is a working one."
        )

    def test_valkey_url_names_the_service_in_this_file(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        services = _services(fragments["full"])
        value = _environment(services[_FORAGE_SERVICE])["VALKEY_URL"]
        assert value is not None
        host = urlsplit(value).hostname
        assert host in services, (
            f"compose/full.yml points VALKEY_URL at {host!r}, which is not a "
            f"service in this file ({sorted(services)}). Forage would report "
            "`degraded: cache_unavailable` on every start."
        )
        assert urlsplit(value).port == 6379, (
            f"compose/full.yml points VALKEY_URL at port {urlsplit(value).port}"
            "; the valkey image serves 6379 and nothing in this file remaps it"
        )

    def test_the_database_index_matches_the_codes_own_default(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        # `ContentCache`'s constructor default is the only other place a
        # database index is written down in this repository. It is exempted
        # from US-002's "no baked default" rule as a test-facing convenience,
        # which is exactly why it is worth tying to: a deployment moved between
        # the constructor default and this fragment must not land on a
        # different keyspace and silently see an empty cache.
        default_url = str(
            inspect.signature(ContentCache.__init__).parameters["valkey_url"].default
        )
        expected_db = urlsplit(default_url).path
        value = _environment(_services(fragments["full"])[_FORAGE_SERVICE])[
            "VALKEY_URL"
        ]
        assert value is not None
        assert urlsplit(value).path == expected_db, (
            f"compose/full.yml selects database {urlsplit(value).path!r} but "
            f"ContentCache's own default is {expected_db!r} "
            f"({default_url!r}). Two keyspaces is a cache that looks empty "
            "after a configuration change nobody thought was one."
        )

    def test_the_valkey_service_is_pinned_by_digest(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        services = _services(fragments["full"])
        value = _environment(services[_FORAGE_SERVICE])["VALKEY_URL"]
        assert value is not None
        backend = services[str(urlsplit(value).hostname)]
        image = str(backend["image"])
        assert image.startswith("valkey/valkey:"), (
            f"compose/full.yml's cache backend runs {image!r}; the spec names "
            "valkey/valkey:8"
        )
        assert _DIGEST_PINNED_RE.search(image), (
            f"compose/full.yml's valkey image {image!r} is not digest-pinned"
        )

    def test_the_valkey_service_persists_its_data(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        # Surviving a restart is the reason to run the third container at all.
        # A Valkey with no volume is indistinguishable from memory mode the
        # moment it is recreated, which would make full.yml pure overhead.
        services = _services(fragments["full"])
        value = _environment(services[_FORAGE_SERVICE])["VALKEY_URL"]
        assert value is not None
        backend = services[str(urlsplit(value).hostname)]
        assert _volume_mounts(backend), (
            "compose/full.yml's valkey mounts no volume, so its cache does not "
            "survive a recreate — which is the one thing this fragment offers "
            "over minimal.yml"
        )


class TestTheDuplicationDoesNotDrift:
    """`full.yml` repeats `minimal.yml` on purpose; the repeat is asserted."""

    @pytest.mark.parametrize("service_name", [_FORAGE_SERVICE, _SEARXNG_SERVICE])
    def test_the_shared_services_run_the_same_image(
        self, fragments: dict[str, dict[str, Any]], service_name: str
    ) -> None:
        images = {
            name: str(_services(fragment)[service_name]["image"])
            for name, fragment in fragments.items()
        }
        assert len(set(images.values())) == 1, (
            f"The two fragments pin different {service_name} images: {images}. "
            "full.yml duplicates minimal.yml rather than extending it, so a "
            "pin bump has to move in both."
        )

    def test_the_shared_services_publish_the_same_ports(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        published = {
            name: _published_ports(_services(fragment)[_FORAGE_SERVICE])
            for name, fragment in fragments.items()
        }
        assert len(set(map(tuple, published.values()))) == 1, (
            f"The two fragments publish Forage differently: {published}"
        )

    def test_the_shared_services_mount_the_same_model_cache(
        self, fragments: dict[str, dict[str, Any]]
    ) -> None:
        mounted = {
            name: _volume_mounts(_services(fragment)[_FORAGE_SERVICE])
            for name, fragment in fragments.items()
        }
        assert len(set(map(tuple, mounted.values()))) == 1, (
            f"The two fragments mount the weights differently: {mounted}. The "
            "fixed volume name only buys a shared weight set while both mount "
            "it identically."
        )


class TestTheFragmentsAreDocumented:
    """An example nobody is pointed at is an example nobody runs."""

    @pytest.mark.parametrize("name", _FRAGMENTS)
    def test_the_readme_points_at_the_fragment(self, name: str) -> None:
        readme = _README_PATH.read_text()
        assert f"compose/{name}.yml" in readme, (
            f"README.md must reference compose/{name}.yml from the quickstart "
            "— the fragments are the quickstart's worked form"
        )

    def test_the_readme_names_both_cache_modes(self) -> None:
        readme = _README_PATH.read_text()
        for literal in ('`"memory"`', '`"valkey"`'):
            assert literal in readme, (
                f"README.md's mode matrix must name the {literal} backend by "
                "its `cache_backend` wire value, so a reader can check which "
                "mode they are in rather than infer it"
            )
