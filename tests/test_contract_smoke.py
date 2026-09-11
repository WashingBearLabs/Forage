"""Tests for ``contract_smoke.py`` — CI's published-image contract smoke.

The smoke itself talks to a running container, which this suite may never do:
``tests/conftest.py`` installs an autouse ``pytest-socket`` guard. So the
module is built as pure evaluators plus an injectable fetcher, and everything
below drives those directly.

Two groups of tests, doing different jobs:

* :class:`TestSingleSourceOfTruth` is the story's real guard. US-005 requires
  the smoke's field expectations to come from the *same source* as the
  golden-schema test rather than from a hand-written copy in bash. That is
  asserted here mechanically — same model object, same version object, and no
  wire literal restated in the smoke's own code — instead of being asserted in
  a comment.
* the rest pin the behaviour of each check, so a mutation that makes the smoke
  tolerant (accepting ``"healthy"``, ignoring a missing degraded reason,
  hardcoding a version) turns the suite red.

test_mapping:
  contract_smoke.py: tests/test_contract_smoke.py
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

import contract_smoke
from contract_smoke import (
    HttpResponse,
    evaluate_health,
    evaluate_metrics,
    run_smoke,
    wait_for_health,
)
from pipeline import contract
from pipeline.contract import (
    CONTRACT_VERSION,
    DEGRADED_CACHE_UNAVAILABLE,
    DEGRADED_PROMPTGUARD_UNAVAILABLE,
)
from promptguard.classifier import PromptGuardClassifier
from retrieval_app import CAPABILITY_SEARCH_SANITIZATION, HealthResponse

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_PATH = _REPO_ROOT / "contract_smoke.py"

# A plausible derived revision: 64 hex characters, as
# `derive_sanitizer_revision` returns.
_REVISION = "0" * 64


def _health_body(**overrides: object) -> str:
    """A valid weights-free ``/health`` body, with optional field overrides."""
    payload: dict[str, object] = {
        "status": "degraded",
        "promptguard_loaded": False,
        "cache_connected": False,
        "capabilities": {},
        "sanitizer_revision": _REVISION,
        "contract_version": CONTRACT_VERSION,
        # The smoke runs the image with no `-e` of any kind, so the container
        # it reads has no `VALKEY_URL` and reports the memory backend
        # (`feature-forage-cache-fallback` US-002/US-003).
        "cache_backend": "memory",
        "degraded_reasons": [
            DEGRADED_PROMPTGUARD_UNAVAILABLE,
            DEGRADED_CACHE_UNAVAILABLE,
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _health_response(**overrides: object) -> HttpResponse:
    return HttpResponse(200, _health_body(**overrides))


def _metrics_body(**overrides: object) -> str:
    payload: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "extraction": {"requests": 0},
    }
    payload.update(overrides)
    return json.dumps(payload)


def _joined(failures: list[str]) -> str:
    return "\n".join(failures)


# ---------------------------------------------------------------------------
# The AC that matters: one source of truth
# ---------------------------------------------------------------------------


def _code_string_literals(path: Path) -> set[str]:
    """Every string constant in a module *except* its docstrings.

    The distinction is the whole point: the smoke's prose is free to name
    ``promptguard_unavailable`` while its code must not, because a literal in
    the code is a second copy of the contract that can drift from the first.
    """
    tree = ast.parse(path.read_text())
    docstrings: list[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.append(first.value)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and not any(node is doc for doc in docstrings)
    }


class TestSingleSourceOfTruth:
    """The smoke's expectations are the golden test's, not a copy of them."""

    def test_health_model_is_the_golden_schema_model(self) -> None:
        from tests.test_contract_schema import _SCHEMA_MODELS

        assert contract_smoke.HEALTH_MODEL is _SCHEMA_MODELS["HealthResponse"], (
            "The smoke must validate against the same HealthResponse object "
            "tests/test_contract_schema.py pins against the golden fixture. A "
            "separate field list in the smoke — in Python or in bash — is a "
            "second contract that can drift from the first."
        )

    def test_health_model_is_the_served_response_model(self) -> None:
        assert contract_smoke.HEALTH_MODEL is HealthResponse

    def test_contract_version_comes_from_pipeline_contract(self) -> None:
        assert contract_smoke.CONTRACT_VERSION is contract.CONTRACT_VERSION

    def test_degraded_reason_comes_from_pipeline_contract(self) -> None:
        assert (
            contract_smoke.DEGRADED_PROMPTGUARD_UNAVAILABLE
            is contract.DEGRADED_PROMPTGUARD_UNAVAILABLE
        )

    @pytest.mark.parametrize(
        "wire_value",
        (
            CONTRACT_VERSION,
            DEGRADED_PROMPTGUARD_UNAVAILABLE,
            CAPABILITY_SEARCH_SANITIZATION,
        ),
    )
    def test_no_wire_value_is_restated_as_a_code_literal(self, wire_value: str) -> None:
        literals = _code_string_literals(_SMOKE_PATH)
        assert wire_value not in literals, (
            f"contract_smoke.py spells {wire_value!r} out as a literal. Every "
            "wire value it checks must be imported at run time — a hardcoded "
            "one keeps passing after the real contract moves, which is exactly "
            "the drift this job exists to catch. (Docstrings are exempt; this "
            "looks only at code.)"
        )

    async def test_capability_key_is_the_one_health_advertises(self) -> None:
        """The constant is tied to behaviour, not to a matching spelling.

        ``search_sanitization`` is not part of the JSON *schema* — the field is
        typed ``dict[str, int]`` — so the golden fixture cannot pin the key.
        This does it the only way that stays true: stand the real app up with a
        loaded classifier and read back what it advertises.
        """
        from retrieval_app import app

        classifier = MagicMock(spec=PromptGuardClassifier)
        classifier.loaded = True
        app.state.classifier = classifier
        app.state.cache = None
        app.state.config = {}
        app.state.sanitizer_revision = _REVISION
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/health")
            capabilities: dict[str, int] = response.json()["capabilities"]
        finally:
            app.state.classifier = PromptGuardClassifier()

        assert set(capabilities) == {contract_smoke.CAPABILITY_SEARCH_SANITIZATION}


# ---------------------------------------------------------------------------
# /health evaluation
# ---------------------------------------------------------------------------


class TestEvaluateHealth:
    """Each contract clause, and the failure it produces when violated."""

    def test_a_degraded_body_passes(self) -> None:
        assert evaluate_health(_health_response()) == []

    def test_healthy_status_fails(self) -> None:
        failures = evaluate_health(
            _health_response(status="healthy", degraded_reasons=[], capabilities={})
        )
        assert failures, "status 'healthy' must never pass a weights-free smoke"
        assert "healthy" in _joined(failures)

    def test_an_ok_status_fails_validation(self) -> None:
        # "ok" is outside the Literal the model declares, so this is caught by
        # the shared model rather than by a check written here.
        failures = evaluate_health(_health_response(status="ok"))
        assert failures
        assert "does not validate" in _joined(failures)

    def test_missing_promptguard_reason_fails(self) -> None:
        failures = evaluate_health(
            _health_response(degraded_reasons=[DEGRADED_CACHE_UNAVAILABLE])
        )
        assert failures
        assert DEGRADED_PROMPTGUARD_UNAVAILABLE in _joined(failures)

    def test_empty_degraded_reasons_fails(self) -> None:
        assert evaluate_health(_health_response(degraded_reasons=[])) != []

    def test_advertised_capability_fails(self) -> None:
        failures = evaluate_health(
            _health_response(capabilities={CAPABILITY_SEARCH_SANITIZATION: 1})
        )
        assert failures
        assert CAPABILITY_SEARCH_SANITIZATION in _joined(failures)

    def test_an_unrelated_capability_is_tolerated(self) -> None:
        # The clause is "does not advertise search_sanitization", not "has no
        # capabilities" — a future capability must not fail this smoke.
        assert evaluate_health(_health_response(capabilities={"pdf_intake": 1})) == []

    def test_wrong_contract_version_fails(self) -> None:
        failures = evaluate_health(_health_response(contract_version="9.9.9"))
        assert failures
        assert CONTRACT_VERSION in _joined(failures)

    def test_empty_sanitizer_revision_fails(self) -> None:
        failures = evaluate_health(_health_response(sanitizer_revision=""))
        assert failures
        assert "sanitizer_revision" in _joined(failures)

    def test_whitespace_sanitizer_revision_fails(self) -> None:
        assert evaluate_health(_health_response(sanitizer_revision="   ")) != []

    def test_underived_sanitizer_revision_fails(self) -> None:
        failures = evaluate_health(
            _health_response(sanitizer_revision=contract_smoke.UNDERIVED_REVISION)
        )
        assert failures
        assert contract_smoke.UNDERIVED_REVISION in _joined(failures)

    def test_an_unexpected_field_fails(self) -> None:
        failures = evaluate_health(_health_response(surprise=1))
        assert failures
        assert "surprise" in _joined(failures)

    def test_a_missing_field_fails(self) -> None:
        payload: dict[str, Any] = json.loads(_health_body())
        del payload["cache_connected"]
        failures = evaluate_health(HttpResponse(200, json.dumps(payload)))
        assert failures
        assert "cache_connected" in _joined(failures)

    def test_field_expectations_track_the_model(self) -> None:
        # Not a restatement of the field list: the point is that whatever the
        # model declares is what the smoke demands.
        for field in HealthResponse.model_fields:
            payload: dict[str, Any] = json.loads(_health_body())
            del payload[field]
            assert evaluate_health(HttpResponse(200, json.dumps(payload))) != [], (
                f"dropping {field!r} from /health passed the smoke"
            )

    @pytest.mark.parametrize("status", (0, 404, 500, 503))
    def test_a_non_200_status_fails(self, status: int) -> None:
        failures = evaluate_health(HttpResponse(status, _health_body()))
        assert len(failures) == 1
        assert str(status) in failures[0]

    def test_a_non_json_body_fails(self) -> None:
        failures = evaluate_health(HttpResponse(200, "<html>502 Bad Gateway</html>"))
        assert failures
        assert "not valid JSON" in _joined(failures)

    def test_a_json_array_body_fails(self) -> None:
        failures = evaluate_health(HttpResponse(200, "[]"))
        assert failures
        assert "not an object" in _joined(failures)

    def test_every_violation_is_reported_not_just_the_first(self) -> None:
        failures = evaluate_health(
            _health_response(
                status="healthy",
                degraded_reasons=[],
                capabilities={CAPABILITY_SEARCH_SANITIZATION: 1},
                contract_version="9.9.9",
                sanitizer_revision="",
            )
        )
        assert len(failures) >= 5, (
            "One run should report the whole picture, not the first thing that "
            f"broke; got {failures}"
        )


# ---------------------------------------------------------------------------
# /metrics evaluation
# ---------------------------------------------------------------------------


class TestEvaluateMetrics:
    def test_a_valid_metrics_body_passes(self) -> None:
        assert evaluate_metrics(HttpResponse(200, _metrics_body())) == []

    def test_missing_contract_version_fails(self) -> None:
        failures = evaluate_metrics(HttpResponse(200, json.dumps({"search": {}})))
        assert failures
        assert "contract_version" in _joined(failures)

    def test_wrong_contract_version_fails(self) -> None:
        failures = evaluate_metrics(
            HttpResponse(200, _metrics_body(contract_version="9.9.9"))
        )
        assert failures
        assert CONTRACT_VERSION in _joined(failures)

    def test_a_non_200_status_fails(self) -> None:
        failures = evaluate_metrics(HttpResponse(404, _metrics_body()))
        assert len(failures) == 1
        assert "404" in failures[0]

    def test_a_non_json_body_fails(self) -> None:
        assert evaluate_metrics(HttpResponse(200, "not json")) != []


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


class _ScriptedFetcher:
    """Returns a queued response per call; repeats the last one forever."""

    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url: str) -> HttpResponse:
        self.urls.append(url)
        index = min(len(self.urls) - 1, len(self.responses) - 1)
        return self.responses[index]


class TestWaitForHealth:
    def test_returns_as_soon_as_health_answers_200(self) -> None:
        fetch = _ScriptedFetcher(_health_response())
        result = wait_for_health(
            "http://host:8020", fetch=fetch, sleep=lambda _: None, log=lambda _: None
        )
        assert result.status == 200
        assert len(fetch.urls) == 1

    def test_polls_the_health_path(self) -> None:
        fetch = _ScriptedFetcher(_health_response())
        wait_for_health(
            "http://host:8020/", fetch=fetch, sleep=lambda _: None, log=lambda _: None
        )
        assert fetch.urls == ["http://host:8020/health"]

    def test_retries_while_the_container_is_starting(self) -> None:
        fetch = _ScriptedFetcher(
            HttpResponse(0, "ConnectionRefusedError"),
            HttpResponse(0, "ConnectionRefusedError"),
            _health_response(),
        )
        delays: list[float] = []
        result = wait_for_health(
            "http://host:8020",
            fetch=fetch,
            sleep=delays.append,
            log=lambda _: None,
        )
        assert result.status == 200
        assert len(fetch.urls) == 3
        assert delays == [contract_smoke.DEFAULT_POLL_INTERVAL_SECONDS] * 2

    def test_gives_up_at_the_deadline_and_returns_the_last_response(self) -> None:
        fetch = _ScriptedFetcher(HttpResponse(503, "still starting"))
        result = wait_for_health(
            "http://host:8020",
            timeout_seconds=0.0,
            fetch=fetch,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert result == HttpResponse(503, "still starting")
        assert len(fetch.urls) == 1, "a zero budget still gets one attempt"

    def test_a_timeout_is_reported_as_a_contract_failure(self) -> None:
        # The two failure modes converge on one path: whatever the poll gives
        # back goes to the evaluator, so "never came up" reads as a contract
        # violation with the last body attached rather than as a second,
        # separately-handled error.
        fetch = _ScriptedFetcher(HttpResponse(0, "ConnectionRefusedError"))
        result = wait_for_health(
            "http://host:8020",
            timeout_seconds=0.0,
            fetch=fetch,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert evaluate_health(result) != []


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------


class _EndpointFetcher:
    """Serves a canned response per path."""

    def __init__(self, health: HttpResponse, metrics: HttpResponse) -> None:
        self.health = health
        self.metrics = metrics
        self.urls: list[str] = []

    def __call__(self, url: str) -> HttpResponse:
        self.urls.append(url)
        return self.metrics if url.endswith("/metrics") else self.health


class TestRunSmoke:
    def test_a_degraded_service_passes(self) -> None:
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020", fetch=fetch, sleep=lambda _: None, log=lambda _: None
        )
        assert failures == []
        assert fetch.urls == ["http://host:8020/health", "http://host:8020/metrics"]

    def test_health_and_metrics_failures_are_both_reported(self) -> None:
        fetch = _EndpointFetcher(
            _health_response(status="healthy", degraded_reasons=[]),
            HttpResponse(500, "boom"),
        )
        failures = run_smoke(
            "http://host:8020", fetch=fetch, sleep=lambda _: None, log=lambda _: None
        )
        assert any("healthy" in failure for failure in failures)
        assert any("/metrics" in failure for failure in failures)

    def test_metrics_is_probed_even_when_health_never_comes_up(self) -> None:
        # A zero budget stands in for "the container never answered": the run
        # must still probe /metrics and report both, not stop at the first
        # failure.
        fetch = _EndpointFetcher(
            HttpResponse(500, "boom"), HttpResponse(200, _metrics_body())
        )
        failures = run_smoke(
            "http://host:8020",
            timeout_seconds=0.0,
            fetch=fetch,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert "http://host:8020/metrics" in fetch.urls
        assert failures != []

    def test_the_log_carries_the_bodies(self) -> None:
        lines: list[str] = []
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        run_smoke(
            "http://host:8020", fetch=fetch, sleep=lambda _: None, log=lines.append
        )
        logged = "\n".join(lines)
        assert "/health" in logged and "/metrics" in logged
        assert CONTRACT_VERSION in logged


def _stub_run(failures: list[str]) -> Callable[..., list[str]]:
    """A `run_smoke` replacement returning a fixed failure list."""

    def _run(_base_url: str, **_kwargs: Any) -> list[str]:
        return failures

    return _run


class TestMain:
    def test_exit_zero_when_there_are_no_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contract_smoke, "run_smoke", _stub_run([]))
        assert contract_smoke.main([]) == 0

    def test_exit_nonzero_when_the_contract_is_violated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contract_smoke, "run_smoke", _stub_run(["violation"]))
        assert contract_smoke.main([]) == 1

    def test_cli_arguments_reach_the_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def _capture(base_url: str, **kwargs: Any) -> list[str]:
            seen["base_url"] = base_url
            seen.update(kwargs)
            return []

        monkeypatch.setattr(contract_smoke, "run_smoke", _capture)
        contract_smoke.main(
            ["--base-url", "http://elsewhere:9", "--timeout-seconds", "7"]
        )
        assert seen["base_url"] == "http://elsewhere:9"
        assert seen["timeout_seconds"] == 7.0

    def test_the_default_budget_is_120_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _capture(base_url: str, **kwargs: Any) -> list[str]:
            seen.update(kwargs)
            return []

        monkeypatch.setattr(contract_smoke, "run_smoke", _capture)
        contract_smoke.main([])
        assert seen["timeout_seconds"] == 120.0, (
            "The budget is an acceptance criterion: 30 s spans a cold torch "
            "import too tightly (round-2 finding)"
        )
