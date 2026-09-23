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
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

import contract_smoke
from contract_smoke import (
    CommandResult,
    HttpResponse,
    evaluate_health,
    evaluate_image_contract,
    evaluate_metrics,
    read_image_file,
    run_smoke,
    served_contract_version,
    wait_for_health,
)
from pipeline import contract
from pipeline.contract import (
    CONTRACT_VERSION,
    DEGRADED_CACHE_UNAVAILABLE,
    DEGRADED_PROMPTGUARD_UNAVAILABLE,
)
from promptguard.classifier import DEFAULT_MODEL_ID, PromptGuardClassifier
from retrieval_app import CAPABILITY_SEARCH_SANITIZATION, HealthResponse
from scripts.export_contract import ANCHOR_PATH, CONTRACT_PATH, render_anchor

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
        "promptguard_model": DEFAULT_MODEL_ID,
        "cache_connected": False,
        "capabilities": {},
        "sanitizer_revision": _REVISION,
        "contract_version": CONTRACT_VERSION,
        # The smoke runs the image with no `-e` of any kind, so the container
        # it reads has no `VALKEY_URL` and reports the memory backend
        # (`feature-forage-cache-fallback` US-002/US-003).
        "cache_backend": "memory",
        "search_providers": ["searxng"],
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

    def test_the_in_image_paths_are_where_the_dockerfile_puts_them(self) -> None:
        """US-004's two constants, tied to the Dockerfile's COPY destination.

        ``tests/test_dockerfile.py`` asserts the Dockerfile copies ``contract/``
        to ``IMAGE_CONTRACT_DIR``; this ties the paths this module reads to that
        same constant. Without it, moving the destination leaves the smoke
        reading a path nothing writes — which fails loudly in CI, but only after
        a build, and with a message about a missing file rather than a moved
        one.
        """
        from tests.test_dockerfile import CONTRACT_SHIPPED_FILES, IMAGE_CONTRACT_DIR

        read_paths = [
            contract_smoke.IMAGE_CONTRACT_PATH,
            contract_smoke.IMAGE_ANCHOR_PATH,
        ]
        assert read_paths == [
            f"{IMAGE_CONTRACT_DIR}{name}" for name in CONTRACT_SHIPPED_FILES
        ]

    def test_the_anchor_format_is_the_exporters_own(self) -> None:
        """One definition of "what an anchor line looks like", not two.

        The smoke hashes the in-image document and compares the result to the
        committed anchor; if it built that line itself, a change to the anchor
        format would leave the smoke comparing against a shape nothing writes.
        """
        from scripts import export_contract

        assert contract_smoke.render_anchor is export_contract.render_anchor
        assert contract_smoke.ANCHOR_PATH is export_contract.ANCHOR_PATH

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
# The contract the image itself carries (US-004)
# ---------------------------------------------------------------------------

_IMAGE = "forage:ci"


def _committed_contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def _committed_anchor() -> str:
    return ANCHOR_PATH.read_text(encoding="utf-8")


def _cat(text: str) -> CommandResult:
    """A successful ``cat`` of *text* out of the image."""
    return CommandResult(0, text, "")


class _RecordingRunner:
    """Serves a canned result per in-image path, recording every argv."""

    def __init__(self, **by_path: CommandResult) -> None:
        self.by_path = by_path
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        self.calls.append(list(argv))
        path = argv[-1]
        if path == contract_smoke.IMAGE_CONTRACT_PATH:
            return self.by_path.get("contract", _cat(_committed_contract()))
        return self.by_path.get("anchor", _cat(_committed_anchor()))


class TestReadImageFile:
    """The seam: one `docker run`, no service started, no container left."""

    def test_it_cats_the_path_out_of_the_image(self) -> None:
        runner = _RecordingRunner()
        read_image_file(_IMAGE, contract_smoke.IMAGE_CONTRACT_PATH, run=runner)
        assert runner.calls == [
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "cat",
                _IMAGE,
                contract_smoke.IMAGE_CONTRACT_PATH,
            ]
        ], (
            "The read must override the entrypoint — the image's own CMD serves "
            "HTTP — and must use --rm, because a smoke that leaves containers "
            "behind on a shared runner is a slow leak. It is also the command "
            "contract/GOVERNANCE.md hands consumers, so it is worth pinning."
        )

    def test_a_real_command_reports_its_output(self) -> None:
        result = contract_smoke.run_command(
            [sys.executable, "-c", "print('in-image bytes')"]
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "in-image bytes"

    def test_a_command_that_cannot_run_is_a_result_not_an_exception(self) -> None:
        """No Docker on PATH must read as a failure, not a traceback.

        The smoke's whole design is "collect every failure and report them
        together"; an exception here would lose the endpoint findings that had
        already been collected.
        """
        result = contract_smoke.run_command(["forage-no-such-binary-exists"])
        assert result.exit_code != 0
        assert result.stderr


class TestEvaluateImageContract:
    """Present, byte-identical to the committed file, and self-consistent."""

    def test_the_committed_pair_passes(self) -> None:
        assert (
            evaluate_image_contract(
                _cat(_committed_contract()),
                _cat(_committed_anchor()),
                served_version=CONTRACT_VERSION,
                committed_anchor=_committed_anchor(),
            )
            == []
        )

    @pytest.mark.parametrize("missing", ("contract", "anchor"))
    def test_a_file_the_image_does_not_carry_fails(self, missing: str) -> None:
        absent = CommandResult(1, "", "cat: No such file or directory")
        failures = evaluate_image_contract(
            absent if missing == "contract" else _cat(_committed_contract()),
            absent if missing == "anchor" else _cat(_committed_anchor()),
            served_version=CONTRACT_VERSION,
            committed_anchor=_committed_anchor(),
        )
        assert failures
        assert "COPY contract/" in _joined(failures), (
            "The failure must name the cause a reader can act on: an image "
            "without the contract is a Dockerfile or .dockerignore problem, not "
            "a service one"
        )

    def test_an_unreadable_image_reports_nothing_downstream(self) -> None:
        """One cause, one failure — not a cascade of derived ones.

        A missing file would otherwise also fail the anchor comparison, the
        hash comparison and the version comparison, and the reader would have
        to work out which of the four was the actual problem.
        """
        unreadable = CommandResult(-1, "", "FileNotFoundError: docker")
        failures = evaluate_image_contract(
            unreadable,
            unreadable,
            served_version=CONTRACT_VERSION,
            committed_anchor=_committed_anchor(),
        )
        assert len(failures) == 2, failures

    def test_an_image_anchor_that_is_not_this_trees_anchor_fails(self) -> None:
        stale = f"{'0' * 64}  openapi.yaml\n"
        failures = evaluate_image_contract(
            _cat(_committed_contract()),
            _cat(stale),
            served_version=CONTRACT_VERSION,
            committed_anchor=_committed_anchor(),
        )
        assert failures
        assert "not built from this commit" in _joined(failures)

    def test_an_edited_in_image_document_fails_the_hash(self) -> None:
        """The check a consumer runs as `sha256sum -c`, run here for them."""
        failures = evaluate_image_contract(
            _cat(_committed_contract() + "# tampered\n"),
            _cat(_committed_anchor()),
            served_version=CONTRACT_VERSION,
            committed_anchor=_committed_anchor(),
        )
        assert failures
        assert "sha256sum -c" in _joined(failures)

    def test_a_version_disagreement_with_the_running_service_fails(self) -> None:
        """US-004's acceptance criterion, and the one that needs both halves.

        The hash checks compare the image against the *repository*; only this
        one compares the document the image ships against the contract the
        container is actually serving.
        """
        failures = evaluate_image_contract(
            _cat(_committed_contract()),
            _cat(_committed_anchor()),
            served_version="9.9.9",
            committed_anchor=_committed_anchor(),
        )
        assert failures
        assert "9.9.9" in _joined(failures)
        assert CONTRACT_VERSION in _joined(failures)

    def test_an_unreadable_health_body_is_reported_not_skipped(self) -> None:
        failures = evaluate_image_contract(
            _cat(_committed_contract()),
            _cat(_committed_anchor()),
            served_version=None,
            committed_anchor=_committed_anchor(),
        )
        assert failures, (
            "A /health body that yielded no contract_version must not make the "
            "version comparison silently pass — the check would be green for "
            "the one container it can say least about"
        )

    def test_a_document_that_is_not_yaml_fails(self) -> None:
        failures = evaluate_image_contract(
            CommandResult(0, "\tnot: [valid", ""),
            _cat(_committed_anchor()),
            served_version=CONTRACT_VERSION,
            committed_anchor=_committed_anchor(),
        )
        assert failures
        assert "not valid YAML" in _joined(failures)

    @pytest.mark.parametrize(
        ("document", "expected"),
        (
            ("- a list, not a mapping\n", "not a mapping"),
            ("openapi: 3.1.0\n", "no `info` object"),
            ("info:\n  version: 3\n", "not a string"),
        ),
    )
    def test_a_document_without_a_usable_version_fails(
        self, document: str, expected: str
    ) -> None:
        # The anchor is made to match so the only failure is the version's.
        failures = evaluate_image_contract(
            _cat(document),
            _cat(render_anchor(document)),
            served_version=CONTRACT_VERSION,
            committed_anchor=render_anchor(document),
        )
        assert failures
        assert expected in _joined(failures)

    def test_every_violation_is_reported_not_just_the_first(self) -> None:
        failures = evaluate_image_contract(
            _cat(_committed_contract()),
            _cat(f"{'0' * 64}  openapi.yaml\n"),
            served_version="9.9.9",
            committed_anchor=f"{'1' * 64}  openapi.yaml\n",
        )
        assert len(failures) == 3, failures


class TestServedContractVersion:
    def test_it_reads_the_version_the_container_reports(self) -> None:
        assert served_contract_version(_health_response()) == CONTRACT_VERSION

    @pytest.mark.parametrize(
        "body", ("not json", "[]", '{"contract_version": 3}', "{}")
    )
    def test_an_unusable_body_yields_none_rather_than_a_second_failure(
        self, body: str
    ) -> None:
        assert served_contract_version(HttpResponse(200, body)) is None


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
# --expect-status healthy (search-release US-004)
# ---------------------------------------------------------------------------


def _healthy_response(**overrides: object) -> HttpResponse:
    """A weights-loaded ``/health`` body: PromptGuard up, sanitization offered."""
    fields: dict[str, object] = {
        "status": contract_smoke.STATUS_HEALTHY,
        "promptguard_loaded": True,
        "capabilities": {CAPABILITY_SEARCH_SANITIZATION: True},
        "degraded_reasons": [],
    }
    fields.update(overrides)
    return _health_response(**fields)


class _Clock:
    """An injected monotonic clock that advances one second per reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class TestExpectStatus:
    def test_the_choices_are_the_two_health_states(self) -> None:
        assert contract_smoke.EXPECT_STATUS_CHOICES == ("healthy", "degraded")
        assert contract_smoke.EXPECTED_STATUS == "degraded"

    def test_a_healthy_body_passes_under_healthy(self) -> None:
        assert (
            evaluate_health(
                _healthy_response(), expect_status=contract_smoke.STATUS_HEALTHY
            )
            == []
        )

    def test_a_degraded_body_fails_under_healthy(self) -> None:
        failures = evaluate_health(
            _health_response(), expect_status=contract_smoke.STATUS_HEALTHY
        )
        joined = _joined(failures)
        assert len(failures) == 3, joined
        assert "'degraded', expected 'healthy'" in joined
        assert DEGRADED_PROMPTGUARD_UNAVAILABLE in joined
        assert CAPABILITY_SEARCH_SANITIZATION in joined

    def test_a_cache_degraded_body_under_healthy_names_the_cache_not_promptguard(
        self,
    ) -> None:
        """A weights-loaded container with an unreachable Valkey is degraded for
        the cache; the status-mismatch message must say so rather than blame
        PromptGuard, which the three PromptGuard-coupled checks show is fine."""
        failures = evaluate_health(
            _healthy_response(
                status="degraded", degraded_reasons=[DEGRADED_CACHE_UNAVAILABLE]
            ),
            expect_status=contract_smoke.STATUS_HEALTHY,
        )
        assert len(failures) == 1, _joined(failures)
        assert "'degraded', expected 'healthy'" in failures[0]
        assert DEGRADED_CACHE_UNAVAILABLE in failures[0]
        assert "PromptGuard" not in failures[0]

    def test_a_healthy_body_fails_under_the_default(self) -> None:
        failures = evaluate_health(_healthy_response())
        joined = _joined(failures)
        assert len(failures) == 3, joined
        assert "'healthy', expected 'degraded'" in joined

    def test_the_other_checks_are_identical_under_healthy(self) -> None:
        failures = evaluate_health(
            _healthy_response(contract_version="9.9.9"),
            expect_status=contract_smoke.STATUS_HEALTHY,
        )
        assert len(failures) == 1 and "9.9.9" in failures[0]

    def test_the_wait_keeps_polling_past_a_degraded_200(self) -> None:
        fetch = _ScriptedFetcher(
            _health_response(), _health_response(), _healthy_response()
        )
        delays: list[float] = []
        result = wait_for_health(
            "http://host:8020",
            expect_status=contract_smoke.STATUS_HEALTHY,
            fetch=fetch,
            sleep=delays.append,
            clock=_Clock(),
            log=lambda _: None,
        )
        assert result == _healthy_response()
        assert len(fetch.urls) == 3
        assert len(delays) == 2

    def test_an_unparseable_200_counts_as_not_yet(self) -> None:
        fetch = _ScriptedFetcher(HttpResponse(200, "not json"), _healthy_response())
        result = wait_for_health(
            "http://host:8020",
            expect_status=contract_smoke.STATUS_HEALTHY,
            fetch=fetch,
            sleep=lambda _: None,
            clock=_Clock(),
            log=lambda _: None,
        )
        assert result == _healthy_response()
        assert len(fetch.urls) == 2

    def test_the_wait_returns_the_last_body_at_the_deadline(self) -> None:
        fetch = _ScriptedFetcher(_health_response())
        clock = _Clock()
        result = wait_for_health(
            "http://host:8020",
            expect_status=contract_smoke.STATUS_HEALTHY,
            timeout_seconds=5.0,
            fetch=fetch,
            sleep=lambda _: None,
            clock=clock,
            log=lambda _: None,
        )
        assert result == _health_response()
        assert clock.now >= 6.0, "the wait must run to the injected deadline"
        failures = evaluate_health(result, expect_status=contract_smoke.STATUS_HEALTHY)
        assert any("'degraded', expected 'healthy'" in f for f in failures)

    def test_a_healthy_run_passes_end_to_end(self) -> None:
        fetch = _EndpointFetcher(
            _healthy_response(), HttpResponse(200, _metrics_body())
        )
        failures = run_smoke(
            "http://host:8020",
            expect_status=contract_smoke.STATUS_HEALTHY,
            image=_IMAGE,
            fetch=fetch,
            run=_RecordingRunner(),
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert failures == []


class TestAnchorFlag:
    def test_the_in_image_anchor_is_compared_against_the_anchor_file(
        self, tmp_path: Path
    ) -> None:
        # The image carries the committed pair; the --anchor file names another
        # tag's anchor. The run must judge the image by the file it was handed,
        # not by this checkout's committed anchor.
        other = tmp_path / "openapi.yaml.sha256"
        other.write_text(render_anchor("a different contract\n"), encoding="utf-8")
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            anchor_path=other,
            image=_IMAGE,
            fetch=fetch,
            run=_RecordingRunner(),
            sleep=lambda _: None,
            log=lambda _: None,
        )
        joined = _joined(failures)
        assert failures, "an image that disagrees with the --anchor file must fail"
        assert other.read_text(encoding="utf-8").strip() in joined

    def test_a_matching_anchor_file_passes(self, tmp_path: Path) -> None:
        copy = tmp_path / "openapi.yaml.sha256"
        copy.write_text(_committed_anchor(), encoding="utf-8")
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            anchor_path=copy,
            image=_IMAGE,
            fetch=fetch,
            run=_RecordingRunner(),
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert failures == []

    def test_an_unreadable_anchor_file_is_a_failure(self, tmp_path: Path) -> None:
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            anchor_path=tmp_path / "missing.sha256",
            image=_IMAGE,
            fetch=fetch,
            run=_RecordingRunner(),
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert any("--anchor" in failure for failure in failures)

    def test_a_non_utf8_anchor_file_is_a_failure_reported_before_any_image_read(
        self, tmp_path: Path
    ) -> None:
        """A failure is a result, not a traceback — and the trust root is read
        first, so a bad --anchor is reported before a single `docker run`."""
        garbage = tmp_path / "anchor.sha256"
        garbage.write_bytes(b"\xff\xfe not utf-8 \x80")
        runner = _RecordingRunner()
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            anchor_path=garbage,
            image=_IMAGE,
            fetch=fetch,
            run=runner,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert any(
            "--anchor" in failure and "UnicodeDecodeError" in failure
            for failure in failures
        ), _joined(failures)
        assert runner.calls == [], "the image is not read once the anchor failed"

    def test_the_default_anchor_is_the_committed_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _capture(base_url: str, **kwargs: Any) -> list[str]:
            seen.update(kwargs)
            return []

        monkeypatch.setattr(contract_smoke, "run_smoke", _capture)
        contract_smoke.main([])
        assert seen["anchor_path"] == ANCHOR_PATH
        assert seen["expect_status"] == "degraded"

    def test_both_flags_reach_the_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: dict[str, Any] = {}

        def _capture(base_url: str, **kwargs: Any) -> list[str]:
            seen.update(kwargs)
            return []

        monkeypatch.setattr(contract_smoke, "run_smoke", _capture)
        anchor = tmp_path / "anchor.sha256"
        contract_smoke.main(["--expect-status", "healthy", "--anchor", str(anchor)])
        assert seen["expect_status"] == "healthy"
        assert seen["anchor_path"] == anchor

    def test_the_anchor_rule_is_stated_where_the_flag_is(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            contract_smoke.main(["--help"])
        help_text = " ".join(capsys.readouterr().out.split())
        assert "never from the Release assets" in help_text
        assert "never from the Release assets" in (contract_smoke.__doc__ or "")


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
        # An injected clock: the wait is status-aware, so a body that never
        # reports the expected status polls to the deadline before it is
        # evaluated — on the fake clock, not the real one.
        clock = _Clock()
        failures = run_smoke(
            "http://host:8020",
            timeout_seconds=3.0,
            fetch=fetch,
            sleep=lambda _: None,
            clock=clock,
            log=lambda _: None,
        )
        assert clock.now >= 4.0, "the wait must run to the injected deadline"
        assert any("healthy" in failure for failure in failures)
        assert any("/metrics" in failure for failure in failures)

    def test_metrics_is_probed_even_when_health_never_comes_up(self) -> None:
        # A deadline on the injected clock stands in for "the container never
        # answered": the run must still probe /metrics and report both, not
        # stop at the first failure.
        fetch = _EndpointFetcher(
            HttpResponse(500, "boom"), HttpResponse(200, _metrics_body())
        )
        failures = run_smoke(
            "http://host:8020",
            timeout_seconds=3.0,
            fetch=fetch,
            sleep=lambda _: None,
            clock=_Clock(),
            log=lambda _: None,
        )
        assert "http://host:8020/metrics" in fetch.urls
        assert failures != []

    def test_no_image_is_read_unless_one_is_named(self) -> None:
        """`--image` is opt-in: the endpoint checks must not need a daemon.

        `docs/releases.md` tells arm64 consumers to run this script against
        their own container, and they may be running it from a checkout with no
        local copy of the image.
        """
        runner = _RecordingRunner()
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            fetch=fetch,
            run=runner,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert failures == []
        assert runner.calls == []

    def test_naming_an_image_reads_both_contract_files_from_it(self) -> None:
        runner = _RecordingRunner()
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            image=_IMAGE,
            fetch=fetch,
            run=runner,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert failures == []
        assert [call[-1] for call in runner.calls] == [
            contract_smoke.IMAGE_CONTRACT_PATH,
            contract_smoke.IMAGE_ANCHOR_PATH,
        ]
        assert all(_IMAGE in call for call in runner.calls)

    def test_an_image_carrying_the_wrong_contract_fails_the_run(self) -> None:
        # The whole-run wiring, not the evaluator: a smoke that read the image
        # and then dropped the findings would be the worst of both.
        runner = _RecordingRunner(contract=_cat("info:\n  version: 9.9.9\n"))
        fetch = _EndpointFetcher(_health_response(), HttpResponse(200, _metrics_body()))
        failures = run_smoke(
            "http://host:8020",
            image=_IMAGE,
            fetch=fetch,
            run=runner,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        assert failures
        assert "9.9.9" in _joined(failures)

    def test_endpoint_and_image_failures_are_reported_together(self) -> None:
        runner = _RecordingRunner(anchor=CommandResult(1, "", "no such file"))
        fetch = _EndpointFetcher(
            _health_response(status="healthy", degraded_reasons=[]),
            HttpResponse(200, _metrics_body()),
        )
        failures = run_smoke(
            "http://host:8020",
            timeout_seconds=3.0,
            image=_IMAGE,
            fetch=fetch,
            run=runner,
            sleep=lambda _: None,
            clock=_Clock(),
            log=lambda _: None,
        )
        assert any("healthy" in failure for failure in failures)
        assert any(contract_smoke.IMAGE_ANCHOR_PATH in failure for failure in failures)

    def test_the_run_forwards_sleep_and_clock_together(self) -> None:
        """`run_smoke` hands both seams to the wait, so a test never spins for real."""
        fetch = _EndpointFetcher(
            _health_response(status="healthy", degraded_reasons=[]),
            HttpResponse(200, _metrics_body()),
        )
        clock = _Clock()
        delays: list[float] = []
        run_smoke(
            "http://host:8020",
            timeout_seconds=2.0,
            poll_interval_seconds=0.25,
            fetch=fetch,
            sleep=delays.append,
            clock=clock,
            log=lambda _: None,
        )
        assert clock.now >= 3.0, "the wait read the injected clock to its deadline"
        assert delays and set(delays) == {0.25}, "every pause went through `sleep`"

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

    def test_the_image_reference_reaches_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _capture(base_url: str, **kwargs: Any) -> list[str]:
            seen.update(kwargs)
            return []

        monkeypatch.setattr(contract_smoke, "run_smoke", _capture)
        contract_smoke.main(["--image", _IMAGE])
        assert seen["image"] == _IMAGE

    def test_no_image_is_read_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def _capture(base_url: str, **kwargs: Any) -> list[str]:
            seen.update(kwargs)
            return []

        monkeypatch.setattr(contract_smoke, "run_smoke", _capture)
        contract_smoke.main([])
        assert seen["image"] is None

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
