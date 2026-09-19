"""Contract smoke for a built Forage image (forage-ci-and-image US-005).

CI's ``smoke`` job runs the *candidate image* — the one ``build-amd64``
produced and handed on as an artifact — with no Hugging Face token, and then
runs this module against it. What it asserts is Epic 1's wire contract for a
weights-free image, the handshake Poppy depends on:

* ``/health`` answers **HTTP 200** — always, even degraded, because the
  container healthcheck is a bare ``curl -f`` and a non-2xx would flap the
  container instead of surfacing the problem;
* ``status`` is exactly ``"degraded"`` — never ``"healthy"``, never ``"ok"``,
  and never a crash;
* ``promptguard_unavailable`` is in ``degraded_reasons``;
* ``capabilities`` does **not** advertise ``search_sanitization``;
* ``contract_version`` equals ``pipeline.contract.CONTRACT_VERSION``;
* ``sanitizer_revision`` is present and derived, not the "unknown" fallback;
* ``/metrics`` answers and carries the same ``contract_version``.

Since ``feature-forage-contract`` US-004 the image also **carries the frozen
contract** at ``/app/contract/``, and ``--image <ref>`` adds the checks that
make that copy trustworthy rather than merely present:

* the in-image ``openapi.yaml``'s ``info.version`` equals the version the
  running container reports on ``/health`` — the document and the service in
  one artifact, saying the same thing;
* the in-image ``openapi.yaml.sha256`` is the anchor this tree committed, and
  the in-image document hashes to it. That is ``sha256sum -c
  openapi.yaml.sha256`` run against the image, which is the same check
  ``contract/GOVERNANCE.md`` tells a consumer to run against whichever copy
  they fetched.

Those two together are the in-image leg of US-004's three-way sha256 equality
(repo ↔ Release asset ↔ image); the Release-asset leg is asserted by the
``publish`` job, against the same committed anchor.

**Two modes (``search-release`` US-004).** ``--expect-status`` picks which
container is under test, and the flag must match how the container was
started:

* ``--expect-status degraded`` (the default, and what CI runs) — a container
  started with no Hugging Face token and no weights. The checks above apply as
  written.
* ``--expect-status healthy`` — a container started with weights (an
  ``--env-file`` carrying the token, say) **and** a reachable cache when
  ``VALKEY_URL`` is set: ``/health`` reports ``degraded`` for
  ``cache_unavailable`` just as it does for ``promptguard_unavailable``, so a
  weights-loaded container with an unreachable Valkey never reaches
  ``healthy``. The three PromptGuard-coupled checks
  invert: ``status`` is exactly ``"healthy"``, ``promptguard_unavailable`` is
  *absent* from ``degraded_reasons``, and ``capabilities`` *does* advertise
  ``search_sanitization``. Every other check — contract version, sanitizer
  revision, ``/metrics``, the in-image contract and anchor — is identical.

The wait knows what it is waiting for: ``/health`` answers 200 the moment
uvicorn binds, while PromptGuard is still loading in the background, so the
poll continues until the body's ``status`` equals the expected one (or the
deadline passes, returning the last response for the evaluator to report).
The default ``--timeout-seconds`` of 120 was sized for a token-less start;
raise it for a container fetching weights cold.

**``--anchor``** names the committed anchor the in-image copy is verified
against — by default this checkout's ``contract/openapi.yaml.sha256``. To verify
a release image from any checkout, pass the anchor committed at that tag
(``git show v1.1.0:contract/openapi.yaml.sha256 > anchor.sha256``, or a clean
checkout of the tag). Take it from the git history only —
never from the Release assets and never from the image. Both are mutable
copies, and a tampered document-plus-anchor pair verifies against itself.

**One source of truth for the field expectations.** The shape check is
``HealthResponse.model_validate`` plus a field-name comparison against
``HealthResponse.model_fields`` — the very model
``tests/test_contract_schema.py`` pins against the golden fixture — and the
values come from ``pipeline.contract`` and ``retrieval_app`` at run time.
Nothing here restates a wire string or a version, so this smoke cannot drift
away from the golden schema: ``tests/test_contract_smoke.py`` asserts the
identity of both sources rather than trusting the convention.

This module lives flat at the repo root, beside the service modules it
imports, because that is how this repo resolves first-party imports
(``kit_tools/docs/CONVENTIONS.md``: flat layout, and never a per-module
``sys.path.insert``). It is a CI utility, not part of the image — the
Dockerfile's ``COPY`` list is explicit and does not include it.

It is deliberately *not* a pytest module: the suite runs under an autouse
``pytest-socket`` guard that blocks exactly the loopback request this needs to
make. ``tests/test_contract_smoke.py`` covers the logic instead, driving the
pure evaluators and an injected fetcher.

Run it by hand against a container, or anything else serving the contract::

    docker run -d --name forage-smoke -p 127.0.0.1:8020:8020 forage:ci
    uv run python contract_smoke.py --base-url http://127.0.0.1:8020 \
        --image forage:ci --expect-status degraded

    # a weights-loaded container, verified against the anchor at its tag
    anchor="$(mktemp)"
    git show v1.1.0:contract/openapi.yaml.sha256 > "$anchor"
    uv run python contract_smoke.py --base-url http://127.0.0.1:8020 \
        --image <ref> --expect-status healthy --anchor "$anchor" \
        --timeout-seconds 600

The loopback bind is deliberate: a container smoked with an ``--env-file`` is
an unauthenticated, SSRF-capable service that can spend the operator's Brave
credit, so it is never published on every interface (``kit_tools/arch/SECURITY.md``).
``mktemp`` rather than a fixed ``/tmp`` name because on a shared host a
pre-planted file at a predictable path is exactly the tampered anchor the
paragraph above warns about.

``--image`` is optional and needs a local Docker daemon that can see the
reference; without it the endpoint checks run exactly as before.

test_mapping:
  contract_smoke.py: tests/test_contract_smoke.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import ValidationError

from pipeline.contract import CONTRACT_VERSION, DEGRADED_PROMPTGUARD_UNAVAILABLE
from retrieval_app import CAPABILITY_SEARCH_SANITIZATION, HealthResponse
from scripts.export_contract import ANCHOR_PATH, render_anchor

# The model the golden-schema test pins. Bound to a name here so
# tests/test_contract_smoke.py can assert it is the *same object*, which is
# what makes "shared with the golden-schema machinery" a checked fact rather
# than a comment.
HEALTH_MODEL = HealthResponse

# The two `/health` states `--expect-status` can assert. The literals are
# spelled out because they *are* the assertion; every other wire string in this
# module is imported. `ExpectStatus` closes the type as well as the CLI: argparse
# `choices` guards only `main()`, and a caller of `run_smoke` gets the same two
# values and nothing else.
ExpectStatus = Literal["healthy", "degraded"]
STATUS_DEGRADED: ExpectStatus = "degraded"
STATUS_HEALTHY: ExpectStatus = "healthy"
EXPECT_STATUS_CHOICES: tuple[ExpectStatus, ...] = (STATUS_HEALTHY, STATUS_DEGRADED)

# A weights-free image must report this and only this — the default mode, and
# the one CI's smoke job runs.
EXPECTED_STATUS: ExpectStatus = STATUS_DEGRADED

# `retrieval_app.health` falls back to this string when it cannot derive a
# revision. It is non-empty, so "non-empty" alone would accept the failure it
# represents.
UNDERIVED_REVISION = "unknown"

# 120 s, not 30: a cold start imports torch before uvicorn binds, and the
# PromptGuard load attempt reaches Hugging Face and is refused before the app
# can finish coming up. Sized for a token-less start: a `healthy` run against a
# container fetching weights cold needs a larger `--timeout-seconds`.
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BASE_URL = "http://127.0.0.1:8020"

# Per-request ceiling. Well under the overall budget so a hung connection
# costs one poll, not the whole window.
REQUEST_TIMEOUT_SECONDS = 10.0

# Where the Dockerfile puts the frozen contract (US-004).
# `tests/test_dockerfile.py` asserts the Dockerfile's COPY destination and
# `tests/test_contract_smoke.py` ties these two constants to it, so the path
# this module reads and the path the image writes cannot drift apart.
IMAGE_CONTRACT_PATH = "/app/contract/openapi.yaml"
IMAGE_ANCHOR_PATH = "/app/contract/openapi.yaml.sha256"

# Reading a file out of an image, without starting the service in it. `--rm`
# because this leaves nothing behind, and `--entrypoint cat` because the
# image's own entrypoint would serve HTTP instead. It is the exact command
# `contract/GOVERNANCE.md` gives consumers, so what CI checks and what a
# consumer runs are the same operation.
IMAGE_READ_TIMEOUT_SECONDS = 120.0

_BODY_EXCERPT_CHARS = 500


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One HTTP attempt: its status, and whatever body came back.

    ``status`` is ``0`` when the request never produced an HTTP response at
    all (connection refused while the container is still starting, DNS
    failure, timeout) — the poll below treats that identically to a bad
    status, and the ``body`` carries the reason for the failure report.
    """

    status: int
    body: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One completed command: what it exited with, and what it wrote.

    ``exit_code`` is ``-1`` when the command could not be run at all (no
    ``docker`` on PATH, a timeout) — treated exactly like a non-zero exit, with
    the reason in ``stderr``.
    """

    exit_code: int
    stdout: str
    stderr: str


Fetcher = Callable[[str], HttpResponse]
Logger = Callable[[str], None]
Runner = Callable[[Sequence[str]], CommandResult]


def http_get(url: str) -> HttpResponse:
    """GET *url*, returning the response even when it is an error.

    Never raises: a smoke that dies on a connection error while the container
    is still booting would report a startup race as a contract violation.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            status = int(response.status)
            payload: bytes = response.read()
    except urllib.error.HTTPError as exc:
        error_body: bytes = exc.read()
        return HttpResponse(int(exc.code), error_body.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return HttpResponse(0, f"{type(exc).__name__}: {exc}")
    return HttpResponse(status, payload.decode("utf-8", "replace"))


def reported_status(response: HttpResponse) -> str | None:
    """The ``status`` in a ``/health`` body, or ``None`` if it cannot be read."""
    try:
        payload: object = json.loads(response.body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    status = cast(dict[str, object], payload).get("status")
    return status if isinstance(status, str) else None


def wait_for_health(
    base_url: str,
    *,
    expect_status: ExpectStatus = EXPECTED_STATUS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    fetch: Fetcher = http_get,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Logger = print,
) -> HttpResponse:
    """Poll ``/health`` until it answers 200 reporting *expect_status*.

    A 200 alone is not enough: ``/health`` answers 200 as soon as uvicorn
    binds, while PromptGuard is still loading, so a ``healthy`` run that
    stopped at the first 200 would fail a container seconds from healthy. A
    body that does not parse counts as "not yet".

    Returns the last response seen either way — a timeout is reported by the
    evaluator as a contract failure with the last body attached, so the caller
    has one failure path rather than two.
    """
    url = f"{base_url.rstrip('/')}/health"
    deadline = clock() + timeout_seconds
    response = HttpResponse(0, "no request was attempted")
    attempts = 0

    while True:
        attempts += 1
        response = fetch(url)
        if response.status == 200 and reported_status(response) == expect_status:
            log(
                f"/health answered 200 with status {expect_status!r} after "
                f"{attempts} attempt(s)"
            )
            return response
        if clock() >= deadline:
            log(
                f"/health never answered 200 with status {expect_status!r} within "
                f"{timeout_seconds:g}s ({attempts} attempt(s)); last status "
                f"{response.status}"
            )
            return response
        sleep(poll_interval_seconds)


def _json_object(
    label: str, response: HttpResponse, failures: list[str]
) -> dict[str, object] | None:
    """Parse a response body as a JSON object, recording why if it is not."""
    excerpt = response.body[:_BODY_EXCERPT_CHARS]
    try:
        parsed: object = json.loads(response.body)
    except json.JSONDecodeError as exc:
        failures.append(f"{label} body is not valid JSON ({exc}): {excerpt!r}")
        return None
    if not isinstance(parsed, dict):
        failures.append(
            f"{label} body is a JSON {type(parsed).__name__}, not an object: "
            f"{excerpt!r}"
        )
        return None
    return cast(dict[str, object], parsed)


def evaluate_health(
    response: HttpResponse, *, expect_status: ExpectStatus = EXPECTED_STATUS
) -> list[str]:
    """Return every way *response* violates the ``/health`` contract.

    *expect_status* selects the mode: under ``degraded`` (weights-free) and
    ``healthy`` (weights loaded) the three PromptGuard-coupled checks invert;
    every other check is identical. An empty list is a pass. Every check runs
    that can run, so one failing run reports the whole picture instead of the
    first thing that broke.
    """
    failures: list[str] = []
    excerpt = response.body[:_BODY_EXCERPT_CHARS]

    if response.status != 200:
        failures.append(
            f"/health returned HTTP {response.status}, expected 200 — /health is "
            "always 200, even degraded, because the container healthcheck only "
            f"reads the status code. Body: {excerpt!r}"
        )
        return failures

    payload = _json_object("/health", response, failures)
    if payload is None:
        return failures

    expected_fields = set(HEALTH_MODEL.model_fields)
    actual_fields = set(payload)
    missing = sorted(expected_fields - actual_fields)
    unexpected = sorted(actual_fields - expected_fields)
    if missing:
        failures.append(
            f"/health is missing field(s) {missing} declared by {HEALTH_MODEL.__name__}"
        )
    if unexpected:
        failures.append(
            f"/health carries field(s) {unexpected} that {HEALTH_MODEL.__name__} "
            "does not declare — an unrecorded wire change needs a "
            "CONTRACT_VERSION bump and a golden-fixture update"
        )

    try:
        health = HEALTH_MODEL.model_validate(payload)
    except ValidationError as exc:
        failures.append(
            f"/health body does not validate against {HEALTH_MODEL.__name__}: {exc}"
        )
        return failures

    healthy = expect_status == STATUS_HEALTHY
    if health.status != expect_status:
        # `degraded_reasons` is the body's own account of *why* — PromptGuard
        # absent, the cache unreachable, or both — so the message names it
        # rather than guessing at one cause.
        if healthy:
            failures.append(
                f"/health reports status {health.status!r}, expected "
                f"{expect_status!r}; degraded_reasons={health.degraded_reasons!r}. "
                "--expect-status healthy is for a container started with weights "
                "and, if VALKEY_URL is set, a reachable cache; every reason "
                "listed must be cleared before /health reports healthy."
            )
        else:
            failures.append(
                f"/health reports status {health.status!r}, expected "
                f"{expect_status!r}; degraded_reasons={health.degraded_reasons!r}. "
                "A weights-free image has no PromptGuard, and reporting anything "
                "else is the silent failure that ran unnoticed for nine days in "
                "production. Against a container started with weights, pass "
                "--expect-status healthy."
            )
    promptguard_reason = DEGRADED_PROMPTGUARD_UNAVAILABLE in health.degraded_reasons
    if healthy and promptguard_reason:
        failures.append(
            f"/health degraded_reasons {health.degraded_reasons!r} contains "
            f"{DEGRADED_PROMPTGUARD_UNAVAILABLE!r} under --expect-status healthy"
        )
    if not healthy and not promptguard_reason:
        failures.append(
            f"/health degraded_reasons {health.degraded_reasons!r} does not "
            f"contain {DEGRADED_PROMPTGUARD_UNAVAILABLE!r}"
        )
    advertised = CAPABILITY_SEARCH_SANITIZATION in health.capabilities
    if healthy and not advertised:
        failures.append(
            f"/health does not advertise {CAPABILITY_SEARCH_SANITIZATION!r} in "
            f"capabilities {health.capabilities!r} under --expect-status "
            "healthy — PromptGuard is not serving search sanitization"
        )
    if not healthy and advertised:
        failures.append(
            f"/health advertises {CAPABILITY_SEARCH_SANITIZATION!r} in "
            f"capabilities {health.capabilities!r} while PromptGuard is "
            "unavailable — a consumer would send it unscannable work"
        )
    if health.contract_version != CONTRACT_VERSION:
        failures.append(
            f"/health contract_version is {health.contract_version!r}, but this "
            f"tree's pipeline/contract.py says {CONTRACT_VERSION!r} — the image "
            "under test was not built from this commit"
        )
    if not health.sanitizer_revision.strip():
        failures.append("/health sanitizer_revision is empty")
    elif health.sanitizer_revision == UNDERIVED_REVISION:
        failures.append(
            f"/health sanitizer_revision is the {UNDERIVED_REVISION!r} fallback — "
            "the service could not derive one, so Poppy's file-recall cache has "
            "nothing to key on"
        )

    return failures


def run_command(argv: Sequence[str]) -> CommandResult:
    """Run *argv*, never raising. A failure is a result, not an exception."""
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=IMAGE_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(-1, "", f"{type(exc).__name__}: {exc}")
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def read_image_file(
    image: str, path: str, *, run: Runner = run_command
) -> CommandResult:
    """Read *path* out of *image* with ``docker run --rm --entrypoint cat``."""
    return run(["docker", "run", "--rm", "--entrypoint", "cat", image, path])


def served_contract_version(response: HttpResponse) -> str | None:
    """The ``contract_version`` in a ``/health`` body, or ``None``.

    Deliberately silent about a body it cannot read: :func:`evaluate_health`
    has already reported that, and a second copy of the same failure would
    make one broken container look like two problems.
    """
    try:
        payload: object = json.loads(response.body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    version = cast(dict[str, object], payload).get("contract_version")
    return version if isinstance(version, str) else None


def _document_version(contract_text: str, failures: list[str]) -> str | None:
    """Return the in-image document's ``info.version``, recording why if not."""
    try:
        document: object = yaml.safe_load(contract_text)
    except yaml.YAMLError as exc:
        failures.append(f"the in-image contract is not valid YAML: {exc}")
        return None
    if not isinstance(document, dict):
        failures.append(
            f"the in-image contract is a {type(document).__name__}, not a "
            f"mapping: {contract_text[:_BODY_EXCERPT_CHARS]!r}"
        )
        return None
    info = cast(dict[str, Any], document).get("info")
    if not isinstance(info, dict):
        failures.append("the in-image contract has no `info` object")
        return None
    version = cast(dict[str, Any], info).get("version")
    if not isinstance(version, str):
        failures.append(
            f"the in-image contract's info.version is {version!r}, not a string"
        )
        return None
    return version


def evaluate_image_contract(
    contract: CommandResult,
    anchor: CommandResult,
    *,
    served_version: str | None,
    committed_anchor: str,
) -> list[str]:
    """Return every way the image's own copy of the contract falls short.

    Three claims, in the order a consumer would make them:

    1. the files are **there** — a `COPY` that silently shipped an empty
       directory (an over-broad `.dockerignore` does exactly that) fails here;
    2. the in-image anchor is **this tree's** anchor, and the in-image document
       hashes to it — together, that the shipped document is byte-identical to
       the committed one;
    3. the document's ``info.version`` is what the running container reports on
       ``/health`` — the one claim that ties the artifact to the service.

    Passing the *committed* anchor in, rather than re-deriving it from a fresh
    render, is the point of an anchor: a rendered-vs-rendered comparison passes
    on a tree where every copy is stale together.
    """
    failures: list[str] = []

    for label, result in (
        (IMAGE_CONTRACT_PATH, contract),
        (IMAGE_ANCHOR_PATH, anchor),
    ):
        if result.exit_code != 0:
            failures.append(
                f"could not read {label} out of the image (exit "
                f"{result.exit_code}): {result.stderr.strip()[:_BODY_EXCERPT_CHARS]!r}"
                " — the image is expected to carry the frozen contract; check "
                "the Dockerfile's `COPY contract/` and .dockerignore"
            )
    if failures:
        return failures

    if anchor.stdout != committed_anchor:
        failures.append(
            f"the image's {IMAGE_ANCHOR_PATH} is {anchor.stdout.strip()!r} but "
            f"this tree committed {committed_anchor.strip()!r} — the image was "
            "not built from this commit, or the anchor was not regenerated with "
            "the contract"
        )

    rendered = render_anchor(contract.stdout)
    if rendered != committed_anchor:
        failures.append(
            f"the image's {IMAGE_CONTRACT_PATH} hashes to {rendered.strip()!r}, "
            f"which is not the committed anchor {committed_anchor.strip()!r}. "
            "`sha256sum -c openapi.yaml.sha256` would refuse this copy."
        )

    version = _document_version(contract.stdout, failures)
    if version is None:
        return failures
    if served_version is None:
        failures.append(
            f"the in-image contract declares info.version {version!r}, but "
            "/health yielded no contract_version to compare it against (see "
            "the /health failures above)"
        )
    elif version != served_version:
        failures.append(
            f"the in-image contract declares info.version {version!r} while the "
            f"running container reports contract_version {served_version!r} — "
            "the image is serving one contract and shipping another"
        )

    return failures


def evaluate_metrics(response: HttpResponse) -> list[str]:
    """Return every way ``/metrics`` fails its part of the contract."""
    failures: list[str] = []
    excerpt = response.body[:_BODY_EXCERPT_CHARS]

    if response.status != 200:
        failures.append(
            f"/metrics returned HTTP {response.status}, expected 200. Body: {excerpt!r}"
        )
        return failures

    payload = _json_object("/metrics", response, failures)
    if payload is None:
        return failures

    version = payload.get("contract_version")
    if version != CONTRACT_VERSION:
        failures.append(
            f"/metrics contract_version is {version!r}, expected {CONTRACT_VERSION!r}"
        )

    return failures


def run_smoke(
    base_url: str,
    *,
    expect_status: ExpectStatus = EXPECTED_STATUS,
    anchor_path: Path = ANCHOR_PATH,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    image: str | None = None,
    fetch: Fetcher = http_get,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    run: Runner = run_command,
    log: Logger = print,
) -> list[str]:
    """Poll, probe and evaluate. Returns the failure list (empty is a pass).

    *sleep* and *clock* are forwarded to :func:`wait_for_health` together: a
    test that injects one without the other would either spin on the real
    clock or sleep for real, so the two seams travel as a pair.
    """
    log(f"Expecting contract_version {CONTRACT_VERSION} (pipeline/contract.py)")
    log(f"Expecting /health status {expect_status!r} (--expect-status)")
    health = wait_for_health(
        base_url,
        expect_status=expect_status,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        fetch=fetch,
        sleep=sleep,
        clock=clock,
        log=log,
    )
    log(f"--- GET /health -> {health.status} ---")
    log(health.body[:_BODY_EXCERPT_CHARS])

    failures = evaluate_health(health, expect_status=expect_status)

    metrics = fetch(f"{base_url.rstrip('/')}/metrics")
    log(f"--- GET /metrics -> {metrics.status} ---")
    log(metrics.body[:_BODY_EXCERPT_CHARS])
    failures.extend(evaluate_metrics(metrics))

    if image is not None:
        # The --anchor file is the trust root the image is judged against, so
        # it is read first: a bad path or a non-UTF-8 file is reported before
        # any `docker run`, and as a result rather than a traceback (this
        # module's rule that a failure is a finding, not an exception).
        log(f"committed anchor: {anchor_path} (--anchor)")
        try:
            committed_anchor = anchor_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(
                f"could not read the --anchor file {anchor_path}: "
                f"{type(exc).__name__}: {exc}"
            )
            return failures
        log(f"--- reading {IMAGE_CONTRACT_PATH} out of {image} ---")
        contract = read_image_file(image, IMAGE_CONTRACT_PATH, run=run)
        anchor = read_image_file(image, IMAGE_ANCHOR_PATH, run=run)
        log(f"in-image anchor: {anchor.stdout.strip() or anchor.stderr.strip()}")
        failures.extend(
            evaluate_image_contract(
                contract,
                anchor,
                served_version=served_contract_version(health),
                committed_anchor=committed_anchor,
            )
        )

    return failures


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    parser = argparse.ArgumentParser(
        description="Assert a running Forage image's /health contract."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the running service (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "How long to wait for /health to answer 200 with the expected status "
            f"(default: {DEFAULT_TIMEOUT_SECONDS:g}, sized for a token-less start; "
            "raise it for a container fetching weights cold)"
        ),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Delay between /health attempts",
    )
    parser.add_argument(
        "--image",
        default=None,
        help=(
            "Image reference to read the frozen contract out of "
            f"({IMAGE_CONTRACT_PATH}). Needs a Docker daemon that can see it; "
            "omit to check the served endpoints only."
        ),
    )
    parser.add_argument(
        "--expect-status",
        choices=EXPECT_STATUS_CHOICES,
        default=EXPECTED_STATUS,
        help=(
            "Which /health status the container must report (default: "
            f"{EXPECTED_STATUS}). Match how it was started: '{STATUS_DEGRADED}' "
            "for a container with no token or weights (what CI runs), "
            f"'{STATUS_HEALTHY}' for one started with weights and, if VALKEY_URL "
            "is set, a reachable cache — /health lists every unmet condition in "
            "degraded_reasons."
        ),
    )
    parser.add_argument(
        "--anchor",
        type=Path,
        default=ANCHOR_PATH,
        help=(
            "The committed anchor the in-image contract is verified against "
            "(default: this checkout's contract/openapi.yaml.sha256). For a "
            "release image, pass the anchor committed at its tag — `git show "
            "vX.Y.Z:contract/openapi.yaml.sha256` or a clean checkout of the "
            "tag — never from the Release assets and never from the image: "
            "both are mutable copies, and a tampered pair verifies against "
            "itself."
        ),
    )
    args = parser.parse_args(argv)
    base_url: str = args.base_url
    timeout_seconds: float = args.timeout_seconds
    poll_interval_seconds: float = args.poll_interval_seconds
    image: str | None = args.image
    expect_status: ExpectStatus = args.expect_status
    anchor_path: Path = args.anchor

    failures = run_smoke(
        base_url,
        expect_status=expect_status,
        anchor_path=anchor_path,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        image=image,
    )

    if failures:
        for failure in failures:
            print(f"::error::{failure}")
        print(f"Contract smoke FAILED with {len(failures)} violation(s).")
        return 1

    print(f"Contract smoke PASSED: {expect_status}, honest, and on-contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
