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

    docker run -d --name forage-smoke -p 8020:8020 forage:ci
    uv run python contract_smoke.py --base-url http://127.0.0.1:8020

test_mapping:
  contract_smoke.py: tests/test_contract_smoke.py
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError

from pipeline.contract import CONTRACT_VERSION, DEGRADED_PROMPTGUARD_UNAVAILABLE
from retrieval_app import CAPABILITY_SEARCH_SANITIZATION, HealthResponse

# The model the golden-schema test pins. Bound to a name here so
# tests/test_contract_smoke.py can assert it is the *same object*, which is
# what makes "shared with the golden-schema machinery" a checked fact rather
# than a comment.
HEALTH_MODEL = HealthResponse

# A weights-free image must report this and only this. The literal is spelled
# out because it *is* the assertion; every other wire string in this module is
# imported.
EXPECTED_STATUS = "degraded"

# `retrieval_app.health` falls back to this string when it cannot derive a
# revision. It is non-empty, so "non-empty" alone would accept the failure it
# represents.
UNDERIVED_REVISION = "unknown"

# 120 s, not 30: a cold start imports torch before uvicorn binds, and the
# PromptGuard load attempt reaches Hugging Face and is refused before the app
# can finish coming up.
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BASE_URL = "http://127.0.0.1:8020"

# Per-request ceiling. Well under the overall budget so a hung connection
# costs one poll, not the whole window.
REQUEST_TIMEOUT_SECONDS = 10.0

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


Fetcher = Callable[[str], HttpResponse]
Logger = Callable[[str], None]


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


def wait_for_health(
    base_url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    fetch: Fetcher = http_get,
    sleep: Callable[[float], None] = time.sleep,
    log: Logger = print,
) -> HttpResponse:
    """Poll ``/health`` until it answers 200 or the budget expires.

    Returns the last response seen either way — a timeout is reported by the
    evaluator as a contract failure with the last body attached, so the caller
    has one failure path rather than two.
    """
    url = f"{base_url.rstrip('/')}/health"
    deadline = time.monotonic() + timeout_seconds
    response = HttpResponse(0, "no request was attempted")
    attempts = 0

    while True:
        attempts += 1
        response = fetch(url)
        if response.status == 200:
            log(f"/health answered 200 after {attempts} attempt(s)")
            return response
        if time.monotonic() >= deadline:
            log(
                f"/health never answered 200 within {timeout_seconds:g}s "
                f"({attempts} attempt(s)); last status {response.status}"
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


def evaluate_health(response: HttpResponse) -> list[str]:
    """Return every way *response* violates the degraded ``/health`` contract.

    An empty list is a pass. Every check runs that can run, so one failing run
    reports the whole picture instead of the first thing that broke.
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

    if health.status != EXPECTED_STATUS:
        failures.append(
            f"/health reports status {health.status!r}, expected "
            f"{EXPECTED_STATUS!r}. A weights-free image has no PromptGuard, and "
            "reporting anything else is the silent failure that ran unnoticed "
            "for nine days in production."
        )
    if DEGRADED_PROMPTGUARD_UNAVAILABLE not in health.degraded_reasons:
        failures.append(
            f"/health degraded_reasons {health.degraded_reasons!r} does not "
            f"contain {DEGRADED_PROMPTGUARD_UNAVAILABLE!r}"
        )
    if CAPABILITY_SEARCH_SANITIZATION in health.capabilities:
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
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    fetch: Fetcher = http_get,
    sleep: Callable[[float], None] = time.sleep,
    log: Logger = print,
) -> list[str]:
    """Poll, probe and evaluate. Returns the failure list (empty is a pass)."""
    log(f"Expecting contract_version {CONTRACT_VERSION} (pipeline/contract.py)")
    health = wait_for_health(
        base_url,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        fetch=fetch,
        sleep=sleep,
        log=log,
    )
    log(f"--- GET /health -> {health.status} ---")
    log(health.body[:_BODY_EXCERPT_CHARS])

    failures = evaluate_health(health)

    metrics = fetch(f"{base_url.rstrip('/')}/metrics")
    log(f"--- GET /metrics -> {metrics.status} ---")
    log(metrics.body[:_BODY_EXCERPT_CHARS])
    failures.extend(evaluate_metrics(metrics))

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
        help="How long to wait for /health to answer 200",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Delay between /health attempts",
    )
    args = parser.parse_args(argv)
    base_url: str = args.base_url
    timeout_seconds: float = args.timeout_seconds
    poll_interval_seconds: float = args.poll_interval_seconds

    failures = run_smoke(
        base_url,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    if failures:
        for failure in failures:
            print(f"::error::{failure}")
        print(f"Contract smoke FAILED with {len(failures)} violation(s).")
        return 1

    print("Contract smoke PASSED: degraded, honest, and on-contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
