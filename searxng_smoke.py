"""Cross-container smoke for the forage-searxng image (forage-ci-and-image US-004).

The companion image is upstream's SearXNG plus one baked config, and the only
thing that can tell you whether that config is right is running it. This module
stands the image up on a **network with no egress**, points a client container
at it, and asserts what a Forage consumer actually depends on.

Blocking phases, all hermetic — no engine on the internet is contacted, so a
throttled DuckDuckGo can never turn a publish red:

1. ``secret`` — with ``SEARXNG_SECRET`` unset the container must **fail to
   start**, loudly. Upstream ships a placeholder ``secret_key`` and refuses to
   serve with it; the baked config bakes no literal of its own, so the refusal
   is what a consumer who forgot the variable gets. A silently-started instance
   signing sessions with a public constant is the failure this asserts against.
2. ``envelope`` — the shipped default answers ``format=json`` with HTTP 200 and
   a parseable JSON object carrying ``results``, not a bot-detection block
   page. This is the exact call ``pipeline/orchestrator.py`` makes.
3. ``budget`` — more than ``API_MAX`` consecutive JSON requests all succeed.
   The shipped default has no limiter, and this is the guard that notices if
   someone turns one on: with a limiter installed, request 5 in an hour is a
   429 and Forage's search stage starts failing intermittently in production
   with nothing in Forage's own logs to explain it.
4. ``limiter-on`` — with ``SEARXNG_LIMITER=true`` **and** a Valkey backend, the
   limiter really installs: it writes to the Valkey DB (asserted by reading
   that DB's key count, not by grepping a log line), and it refuses an
   API-shaped client. This is the half that proves the documented opt-in works.
5. ``limiter-inert`` — with ``SEARXNG_LIMITER=true`` and **no** backend,
   upstream logs its "limiter requires Valkey" error, writes nothing, and
   serves every request unthrottled. The differential between 4 and 5 is what
   makes "the limiter initialised" a measurement instead of an assertion.

The advisory phase (``--live``, non-blocking in CI) is the only one that leaves
the machine: it runs the same image on an ordinary network and asks whether the
engines the baked config enables still return results. It is advisory because
a live third-party query inside a publish gate chain reproduces exactly the
every-engine-throttled outage ``kit_tools/docs/GOTCHAS.md`` records.

**Why a script and not a pile of shell in the workflow.** Same reason as
``contract_smoke.py``: the assertions are testable this way. ``docker`` is
reached through an injected runner, every judgement is a pure function over
captured output, and ``tests/test_searxng_smoke.py`` drives all of it without a
daemon. It also means a maintainer bumping the base pin reproduces CI with one
command instead of reconstructing a job::

    docker build -t forage-searxng:ci searxng/
    uv run python searxng_smoke.py --image forage-searxng:ci
    uv run python searxng_smoke.py --image forage-searxng:ci --live

test_mapping:
  searxng_smoke.py: tests/test_searxng_smoke.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = REPO_ROOT / "searxng" / "config" / "settings.yml"

# --- Pinned helper images -------------------------------------------------
#
# Digest-pinned for the same reason the base image is: a gate whose supporting
# cast can move is not reproducible. Both are multi-arch indexes, so the same
# pin resolves on the amd64 runner and on an arm64 laptop.

# valkey/valkey:8.1-alpine — Valkey 8.1.10.
VALKEY_IMAGE = (
    "valkey/valkey@sha256:"
    "d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1"
)
# curlimages/curl:latest as of 2026-09-07 — curl 8.22.0.
CLIENT_IMAGE = (
    "curlimages/curl@sha256:"
    "58adaa4e8dca9c988bae2aba4ab3434a0bb2da16bbe3f92dec39ec7785166777"
)

# --- Upstream facts this smoke is built around ----------------------------
#
# Every one of these was read out of the pinned image, not remembered. They are
# named here so a failure message can explain itself; the assertions below
# measure the behaviour rather than trusting the constants.

# searx/limiter.py logs this at ERROR when `limiter: true` has no backend, then
# serves unthrottled. Round 2 called it inert theatre; phase 5 reproduces it.
INERT_LIMITER_MARKER = "The limiter requires Valkey"

# searx/settings_defaults.py: `secret_key` carries no default and reads this
# environment name. With `use_default_settings: true` the upstream placeholder
# fills in, and searx/webapp.py refuses to serve with it.
SECRET_ENV = "SEARXNG_SECRET"
SECRET_REFUSAL_MARKER = "secret_key"

# THE VERIFIED NAME. searx/settings_defaults.py maps `valkey.url` to this, and
# the deprecated `redis.url` to SEARXNG_REDIS_URL — searx/valkeydb.py prefers
# valkey.url, falls back to redis.url, and emits a DeprecationWarning for it.
# `SEARXNG_REDIS_URL` still works on this pin; it is not the name to document.
VALKEY_URL_ENV = "SEARXNG_VALKEY_URL"
DEPRECATED_VALKEY_URL_ENV = "SEARXNG_REDIS_URL"

# searx/settings_defaults.py maps `server.limiter` to this, so the documented
# opt-in needs no rebuild and no config overlay.
LIMITER_ENV = "SEARXNG_LIMITER"

# searx/botdetection/ip_limit.py: API_MAX requests per API_WINDOW (3600 s) for
# any `format != html` request, per client network. Module constants — not
# settable from limiter.toml. Phase 3 probes past it.
UPSTREAM_API_MAX = 4
JSON_BUDGET_PROBES = UPSTREAM_API_MAX + 2

# --- Probe shapes ---------------------------------------------------------
#
# The client container's own headers, i.e. plain curl. This is the *shape*
# botdetection refuses: curl trips `http_accept_encoding` and `http_user_agent`,
# and Forage's httpx client trips `http_accept_language` (httpx sends no
# Accept-Language). Different method, same verdict — an API client is not a
# browser and botdetection is a browser detector.
API_CLIENT_HEADERS: tuple[tuple[str, str], ...] = ()

# A request shaped like a browser, used only to show the limiter working at
# all: it passes every header method and reaches `ip_limit`, which is the one
# that touches Valkey. Nothing in Forage sends these.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

BROWSER_HEADERS: tuple[tuple[str, str], ...] = (
    ("User-Agent", _BROWSER_USER_AGENT),
    ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    ("Accept-Language", "en-US,en;q=0.9"),
    ("Accept-Encoding", "gzip, deflate"),
    ("Sec-Fetch-Site", "same-origin"),
    ("Sec-Fetch-Mode", "navigate"),
    ("Sec-Fetch-Dest", "document"),
)

NETWORK_NAME = "forage-searxng-smoke"
SEARXNG_CONTAINER = "forage-searxng-smoke-sx"
VALKEY_CONTAINER = "forage-searxng-smoke-valkey"
SEARXNG_PORT = 8080
SMOKE_SECRET = "forage-ci-smoke-secret-not-a-real-deployment"
SMOKE_QUERY = "forage smoke query"

READY_TIMEOUT_SECONDS = 90.0
READY_POLL_SECONDS = 2.0
EXIT_TIMEOUT_SECONDS = 45.0
REQUEST_TIMEOUT_SECONDS = 20.0

_BODY_EXCERPT_CHARS = 400
_LOG_EXCERPT_CHARS = 4000

Logger = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One completed subprocess: what it exited with and what it said."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def out(self) -> str:
        """Both streams, for the many docker commands that mix them."""
        return f"{self.stdout}{self.stderr}"


Runner = Callable[[Sequence[str]], CommandResult]


@dataclass(frozen=True, slots=True)
class Probe:
    """One HTTP attempt made from the client container.

    ``status`` is ``0`` when curl produced no HTTP response at all — a refused
    connection while the container is still starting, a DNS failure inside the
    isolated network. The evaluators treat that as a violation with the body
    attached rather than as a separate error path.
    """

    status: int
    body: str


def run_command(argv: Sequence[str]) -> CommandResult:
    """Run *argv*, capturing both streams. Never raises on a non-zero exit."""
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


# --------------------------------------------------------------------------
# Pure evaluators. Each returns the list of ways its input violates the
# contract; an empty list is a pass. They take captured output and nothing
# else, which is what lets tests/test_searxng_smoke.py cover every branch
# without a Docker daemon.
# --------------------------------------------------------------------------


def _excerpt(text: str, limit: int = _BODY_EXCERPT_CHARS) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def evaluate_secret_refusal(exited: bool, exit_code: int, logs: str) -> list[str]:
    """A container started with no ``SEARXNG_SECRET`` must refuse to serve."""
    failures: list[str] = []
    if not exited:
        failures.append(
            f"the image kept running with {SECRET_ENV} unset — it must refuse "
            "to start rather than serve with the placeholder secret_key that "
            "`use_default_settings: true` supplies, which is a constant every "
            "puller of this image knows"
        )
        return failures
    if exit_code == 0:
        failures.append(
            f"the image exited 0 with {SECRET_ENV} unset; a refusal has to be a "
            "failure, or an orchestrator restarts it forever without saying why"
        )
    if SECRET_REFUSAL_MARKER not in logs:
        failures.append(
            f"the log of the {SECRET_ENV}-less start never mentions "
            f"{SECRET_REFUSAL_MARKER!r}, so an operator cannot tell what is "
            f"missing. Log: {_excerpt(logs, _LOG_EXCERPT_CHARS)!r}"
        )
    return failures


def evaluate_json_envelope(probe: Probe) -> list[str]:
    """The shipped default must answer the call Forage's search stage makes."""
    failures: list[str] = []

    if probe.status == 429:
        failures.append(
            "the JSON API answered HTTP 429 — a bot-detection block page. The "
            "shipped config must serve its own consumer; see settings.yml's "
            f"`limiter:` note. Body: {_excerpt(probe.body)!r}"
        )
        return failures
    if probe.status != 200:
        failures.append(
            f"the JSON API answered HTTP {probe.status}, expected 200. "
            f"Body: {_excerpt(probe.body)!r}"
        )
        return failures

    try:
        parsed: object = json.loads(probe.body)
    except json.JSONDecodeError as exc:
        failures.append(
            f"the JSON API answered 200 with a body that is not JSON ({exc}) — "
            "an HTML block page returned with a 200 would look like this. "
            f"Body: {_excerpt(probe.body)!r}"
        )
        return failures
    if not isinstance(parsed, dict):
        failures.append(
            f"the JSON API returned a {type(parsed).__name__}, not an object: "
            f"{_excerpt(probe.body)!r}"
        )
        return failures

    envelope = cast(dict[str, object], parsed)
    if "results" not in envelope:
        failures.append(
            f"the JSON envelope has no 'results' key (keys: {sorted(envelope)}) "
            "— pipeline/orchestrator.py reads `data.get('results', [])` and "
            "would silently see an empty search instead of an error"
        )
    return failures


def evaluate_json_budget(statuses: Sequence[int]) -> list[str]:
    """Consecutive JSON requests must not run into a per-hour API budget."""
    failures: list[str] = []
    refused = [i for i, status in enumerate(statuses, start=1) if status != 200]
    if refused:
        failures.append(
            f"{len(refused)} of {len(statuses)} consecutive JSON requests were "
            f"refused (first at request {refused[0]}, statuses {list(statuses)}). "
            f"Upstream's ip_limit caps `format != html` at {UPSTREAM_API_MAX} "
            "requests per hour per client network once a limiter is installed, "
            "so this is what a limiter looks like from the consumer's side: not "
            "an outage, an intermittent one"
        )
    return failures


def evaluate_limiter_backed(
    logs: str, api_client: Probe, browser: Probe, valkey_keys: int
) -> list[str]:
    """With the opt-in set and a backend attached, the limiter must be real."""
    failures: list[str] = []

    if INERT_LIMITER_MARKER in logs:
        failures.append(
            f"the limiter logged {INERT_LIMITER_MARKER!r} despite "
            f"{VALKEY_URL_ENV} being set — it did not reach the backend, so it "
            "is running unthrottled while reading as enabled"
        )
    if browser.status != 200:
        failures.append(
            f"a browser-shaped request was answered HTTP {browser.status}; the "
            "probe that is supposed to pass botdetection did not, so the "
            "backend assertion below proves nothing. "
            f"Body: {_excerpt(browser.body)!r}"
        )
    if valkey_keys <= 0:
        failures.append(
            "the Valkey DB is empty after a request that reaches ip_limit — the "
            "limiter is not using the backend it was given. This is the "
            "assertion that a log line cannot make: ip_limit writes its sliding "
            "windows here, so a non-empty DB is the limiter's own fingerprint"
        )
    if api_client.status != 429:
        failures.append(
            f"an API-shaped request was answered HTTP {api_client.status}, "
            "expected 429. With the limiter installed, botdetection refuses a "
            "client that does not look like a browser — if it did not here, the "
            "limiter is not actually filtering requests"
        )
    return failures


def evaluate_limiter_inert(logs: str, api_client: Probe, valkey_keys: int) -> list[str]:
    """With the opt-in set and no backend, upstream must say so and serve on."""
    failures: list[str] = []

    if INERT_LIMITER_MARKER not in logs:
        failures.append(
            f"a limiter with no backend did not log {INERT_LIMITER_MARKER!r}. "
            "That error is the only warning an operator gets that their rate "
            f"limiting is decorative. Log: {_excerpt(logs, _LOG_EXCERPT_CHARS)!r}"
        )
    if api_client.status != 200:
        failures.append(
            f"a limiter with no backend answered HTTP {api_client.status}; it is "
            "supposed to be inert, and this smoke's whole differential rests on "
            "the contrast with the backed case"
        )
    if valkey_keys != 0:
        failures.append(
            f"the Valkey DB holds {valkey_keys} key(s) in the unbacked phase — "
            "the phase is not isolated from the previous one and its result "
            "means nothing"
        )
    return failures


def enabled_engines(settings: Mapping[str, Any]) -> list[str]:
    """Engine names the baked settings leave enabled, in file order."""
    raw: object = settings.get("engines")
    entries: list[object] = cast(list[object], raw) if isinstance(raw, list) else []
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        engine = cast(dict[str, Any], entry)
        if engine.get("disabled") is True:
            continue
        name = engine.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def load_enabled_engines(path: Path = SETTINGS_PATH) -> list[str]:
    """Read the enabled engine names out of the config the image bakes."""
    parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return enabled_engines(cast(dict[str, Any], parsed))


def evaluate_live_results(probe: Probe, engines: Sequence[str]) -> list[str]:
    """Advisory: did the engines this image enables actually answer?"""
    failures = evaluate_json_envelope(probe)
    if failures:
        return failures
    envelope = cast(dict[str, object], json.loads(probe.body))
    results = envelope.get("results")
    if not isinstance(results, list) or not results:
        unresponsive = envelope.get("unresponsive_engines")
        failures.append(
            f"no engine returned a result for {list(engines)} — "
            f"unresponsive_engines: {unresponsive!r}. Engine rot, a throttled "
            "IP, or a config that no longer matches upstream's engine names"
        )
    return failures


# --------------------------------------------------------------------------
# The harness. Everything that touches Docker goes through `self.run`.
# --------------------------------------------------------------------------


@dataclass
class SearxngHarness:
    """Stands the image up beside a Valkey and talks to it from a third box."""

    image: str
    run: Runner = run_command
    log: Logger = print
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    network: str = NETWORK_NAME
    started: list[str] = field(default_factory=lambda: cast(list[str], []))

    # -- lifecycle --------------------------------------------------------

    def docker(self, *args: str) -> CommandResult:
        return self.run(["docker", *args])

    def create_network(self, *, internal: bool) -> None:
        """Create the smoke network.

        ``--internal`` is what makes the blocking phases hermetic: containers
        on it reach each other and nothing else, so every engine query fails
        DNS resolution and the JSON envelope comes back with an empty
        ``results`` list. Note that Docker also silently ignores ``-p`` on such
        a network — which is why the client is a container rather than the
        runner itself.
        """
        self.docker("network", "rm", "-f", self.network)
        argv = ["network", "create"]
        if internal:
            argv.append("--internal")
        argv.append(self.network)
        result = self.docker(*argv)
        if result.returncode != 0:
            raise RuntimeError(f"could not create network {self.network}: {result.out}")

    def start_valkey(self) -> None:
        self.remove(VALKEY_CONTAINER)
        result = self.docker(
            "run",
            "-d",
            "--name",
            VALKEY_CONTAINER,
            "--network",
            self.network,
            VALKEY_IMAGE,
        )
        if result.returncode != 0:
            raise RuntimeError(f"could not start Valkey: {result.out}")
        self.started.append(VALKEY_CONTAINER)

    def flush_valkey(self) -> None:
        self.docker("exec", VALKEY_CONTAINER, "valkey-cli", "FLUSHALL")

    def valkey_keys(self) -> int:
        """Number of keys in the Valkey DB, or -1 if it could not be read."""
        result = self.docker("exec", VALKEY_CONTAINER, "valkey-cli", "DBSIZE")
        if result.returncode != 0:
            return -1
        try:
            return int(result.stdout.strip())
        except ValueError:
            return -1

    def start_searxng(self, env: Mapping[str, str]) -> None:
        self.remove(SEARXNG_CONTAINER)
        argv = ["run", "-d", "--name", SEARXNG_CONTAINER, "--network", self.network]
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        argv.append(self.image)
        result = self.docker(*argv)
        if result.returncode != 0:
            raise RuntimeError(f"could not start {self.image}: {result.out}")
        self.started.append(SEARXNG_CONTAINER)

    def searxng_logs(self) -> str:
        return self.docker("logs", SEARXNG_CONTAINER).out

    def container_state(self, name: str) -> tuple[str, int]:
        """``(status, exit code)`` for *name*; ``("missing", -1)`` if gone."""
        result = self.docker(
            "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", name
        )
        if result.returncode != 0:
            return ("missing", -1)
        parts = result.stdout.split()
        if len(parts) != 2:
            return ("missing", -1)
        try:
            return (parts[0], int(parts[1]))
        except ValueError:
            return (parts[0], -1)

    def remove(self, name: str) -> None:
        self.docker("rm", "-f", name)

    def teardown(self) -> None:
        for name in (SEARXNG_CONTAINER, VALKEY_CONTAINER):
            self.remove(name)
        self.docker("network", "rm", "-f", self.network)

    # -- probing ----------------------------------------------------------

    def probe(
        self,
        path: str,
        *,
        headers: Sequence[tuple[str, str]] = API_CLIENT_HEADERS,
    ) -> Probe:
        """One HTTP GET from a throwaway client container on the network."""
        argv = [
            "run",
            "--rm",
            "--network",
            self.network,
            CLIENT_IMAGE,
            "-s",
            "-S",
            "-m",
            str(int(REQUEST_TIMEOUT_SECONDS)),
            "-w",
            "\n%{http_code}",
        ]
        for name, value in headers:
            argv += ["-H", f"{name}: {value}"]
        argv.append(f"http://{SEARXNG_CONTAINER}:{SEARXNG_PORT}{path}")
        result = self.docker(*argv)
        body, _, status_text = result.stdout.rpartition("\n")
        try:
            status = int(status_text.strip())
        except ValueError:
            return Probe(0, f"curl exited {result.returncode}: {_excerpt(result.out)}")
        return Probe(status, body)

    def search_path(self, *, engines: Sequence[str] | None = None) -> str:
        query = SMOKE_QUERY.replace(" ", "+")
        path = f"/search?q={query}&format=json&pageno=1"
        if engines:
            path += f"&engines={','.join(engines)}"
        return path

    def wait_until_ready(self) -> bool:
        """Poll ``/healthz`` until it answers 200 or the budget expires.

        ``/healthz`` rather than ``/search``: it answers even with a limiter
        installed, so the same readiness probe works in all four phases.
        """
        deadline = self.monotonic() + READY_TIMEOUT_SECONDS
        attempts = 0
        while True:
            attempts += 1
            status, _ = self.container_state(SEARXNG_CONTAINER)
            if status == "exited":
                self.log(f"container exited while waiting (attempt {attempts})")
                return False
            if self.probe("/healthz").status == 200:
                self.log(f"searxng answered /healthz after {attempts} attempt(s)")
                return True
            if self.monotonic() >= deadline:
                self.log(
                    f"searxng never answered /healthz within "
                    f"{READY_TIMEOUT_SECONDS:g}s ({attempts} attempts)"
                )
                return False
            self.sleep(READY_POLL_SECONDS)

    def wait_until_exited(self) -> tuple[bool, int]:
        """Wait for the container to stop. ``(exited, exit code)``."""
        deadline = self.monotonic() + EXIT_TIMEOUT_SECONDS
        while True:
            status, code = self.container_state(SEARXNG_CONTAINER)
            if status == "exited":
                return (True, code)
            if self.monotonic() >= deadline:
                return (False, code)
            self.sleep(READY_POLL_SECONDS)

    def dump_logs(self, label: str) -> None:
        self.log(f"--- {label}: docker logs {SEARXNG_CONTAINER} ---")
        self.log(_excerpt(self.searxng_logs(), _LOG_EXCERPT_CHARS))
        self.log(f"--- end {label} ---")


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------


def phase_secret_required(harness: SearxngHarness) -> list[str]:
    harness.log(f"[1/4] {SECRET_ENV} unset — the image must refuse to serve")
    harness.start_searxng({VALKEY_URL_ENV: _valkey_url()})
    exited, code = harness.wait_until_exited()
    logs = harness.searxng_logs()
    failures = evaluate_secret_refusal(exited, code, logs)
    harness.log(f"      exited={exited} code={code}")
    if failures:
        harness.dump_logs("secret phase")
    return failures


def phase_envelope_and_budget(harness: SearxngHarness) -> list[str]:
    harness.log(
        f"[2/4] shipped defaults — JSON envelope and {JSON_BUDGET_PROBES} "
        "consecutive requests"
    )
    # A Valkey URL is deliberately supplied here too. The shipped config has no
    # limiter, and this phase proves that a *present* backend does not quietly
    # switch one on: the opt-in is SEARXNG_LIMITER, and nothing else.
    harness.start_searxng({SECRET_ENV: SMOKE_SECRET, VALKEY_URL_ENV: _valkey_url()})
    if not harness.wait_until_ready():
        harness.dump_logs("envelope phase")
        return ["searxng never became ready under the shipped defaults"]

    probe = harness.probe(harness.search_path())
    harness.log(f"      format=json -> HTTP {probe.status}")
    harness.log(f"      {_excerpt(probe.body)}")
    failures = evaluate_json_envelope(probe)

    statuses = [
        harness.probe(harness.search_path()).status for _ in range(JSON_BUDGET_PROBES)
    ]
    harness.log(f"      {JSON_BUDGET_PROBES} consecutive JSON requests: {statuses}")
    failures.extend(evaluate_json_budget(statuses))

    if failures:
        harness.dump_logs("envelope phase")
    return failures


def phase_limiter_backed(harness: SearxngHarness) -> list[str]:
    harness.log(f"[3/4] {LIMITER_ENV}=true with {VALKEY_URL_ENV} — a real limiter")
    harness.flush_valkey()
    harness.start_searxng(
        {
            SECRET_ENV: SMOKE_SECRET,
            LIMITER_ENV: "true",
            VALKEY_URL_ENV: _valkey_url(),
        }
    )
    if not harness.wait_until_ready():
        harness.dump_logs("limiter-on phase")
        return ["searxng never became ready with the limiter enabled"]

    browser = harness.probe(harness.search_path(), headers=BROWSER_HEADERS)
    keys = harness.valkey_keys()
    api_client = harness.probe(harness.search_path())
    logs = harness.searxng_logs()
    harness.log(
        f"      browser-shaped -> HTTP {browser.status}; Valkey keys after it: "
        f"{keys}; API-shaped -> HTTP {api_client.status}"
    )
    failures = evaluate_limiter_backed(logs, api_client, browser, keys)
    if failures:
        harness.dump_logs("limiter-on phase")
    return failures


def phase_limiter_inert(harness: SearxngHarness) -> list[str]:
    harness.log(f"[4/4] {LIMITER_ENV}=true with no backend — inert, and loud")
    harness.flush_valkey()
    harness.start_searxng({SECRET_ENV: SMOKE_SECRET, LIMITER_ENV: "true"})
    if not harness.wait_until_ready():
        harness.dump_logs("limiter-inert phase")
        return ["searxng never became ready with an unbacked limiter"]

    api_client = harness.probe(harness.search_path())
    keys = harness.valkey_keys()
    logs = harness.searxng_logs()
    harness.log(f"      API-shaped -> HTTP {api_client.status}; Valkey keys: {keys}")
    failures = evaluate_limiter_inert(logs, api_client, keys)
    if failures:
        harness.dump_logs("limiter-inert phase")
    return failures


def phase_live_engines(harness: SearxngHarness) -> list[str]:
    engines = load_enabled_engines()
    harness.log(f"[live] querying the engines the baked config enables: {engines}")
    harness.start_searxng({SECRET_ENV: SMOKE_SECRET})
    if not harness.wait_until_ready():
        harness.dump_logs("live phase")
        return ["searxng never became ready on the egress-capable network"]
    probe = harness.probe(harness.search_path(engines=engines))
    harness.log(f"      format=json -> HTTP {probe.status}")
    harness.log(f"      {_excerpt(probe.body)}")
    failures = evaluate_live_results(probe, engines)
    if failures:
        harness.dump_logs("live phase")
    return failures


def _valkey_url() -> str:
    return f"valkey://{VALKEY_CONTAINER}:6379/0"


BLOCKING_PHASES: tuple[Callable[[SearxngHarness], list[str]], ...] = (
    phase_secret_required,
    phase_envelope_and_budget,
    phase_limiter_backed,
    phase_limiter_inert,
)


def run_blocking_smoke(harness: SearxngHarness) -> list[str]:
    """Every hermetic phase, in order. Returns the failure list."""
    harness.create_network(internal=True)
    harness.start_valkey()
    failures: list[str] = []
    for phase in BLOCKING_PHASES:
        failures.extend(phase(harness))
    return failures


def run_live_smoke(harness: SearxngHarness) -> list[str]:
    """The advisory phase: an ordinary network, real engines."""
    harness.create_network(internal=False)
    return phase_live_engines(harness)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke the forage-searxng companion image."
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Image reference to smoke, e.g. forage-searxng:ci",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Run the advisory live-engine probe instead of the blocking "
            "hermetic phases. Non-blocking in CI: engine availability is not "
            "this repository's to guarantee."
        ),
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the containers and network behind for debugging.",
    )
    args = parser.parse_args(argv)
    image: str = args.image
    live: bool = args.live
    keep: bool = args.keep

    harness = SearxngHarness(image=image)
    label = "live-engine probe" if live else "hermetic smoke"
    print(f"Smoking {image} — {label}")
    try:
        failures = run_live_smoke(harness) if live else run_blocking_smoke(harness)
    finally:
        if not keep:
            harness.teardown()

    if failures:
        for failure in failures:
            print(f"::error::{failure}")
        print(f"searxng {label} FAILED with {len(failures)} violation(s).")
        return 1

    print(f"searxng {label} PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
