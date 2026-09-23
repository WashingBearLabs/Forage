"""Hermetic host-benchmark checks; no Docker, service, weights or HTTP client.

test_mapping:
  scripts/bench_promptguard.py: tests/test_bench_promptguard.py
  bench/config.yaml: tests/test_bench_promptguard.py
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
import yaml
from transformers import AutoTokenizer, PreTrainedTokenizerBase

import contract_smoke
from cache import cache_settings_from_config
from contract_smoke import CommandResult, HttpResponse
from models import Stage2Verdict
from pipeline.extraction_limits import (
    extraction_settings_from_config,
    max_extracted_characters,
)
from pipeline.orchestrator import DOCUMENT_FAILURE_REASONS
from pipeline.search_providers.brave import brave_settings_from_config
from pipeline.stage1_upload import extract_upload_text
from pipeline.stage2_structural import scan_structural
from pipeline.stage3_promptguard import promptguard_settings_from_config
from promptguard.classifier import (
    CHUNK_OVERLAP,
    DEFAULT_MODEL_ID,
    MAX_SEQ_LEN,
    PromptGuardClassifier,
)
from retrieval_app import KNOWN_CONFIG_KEYS
from scripts import bench_promptguard as bench
from tests.fakes import ManualClock

ROOT = Path(__file__).resolve().parents[1]
TOKENIZER_DIR = ROOT / "tests/fixtures/tiny_model"
SECRET = "hf_do_not_echo_this_value"
KEYS = {
    "label",
    "base_url",
    "model_id",
    "runs",
    "concurrency",
    "outcome",
    "non_2xx_reason",
    "cold_ms_1w",
    "warm_p50_ms_1w",
    "warm_p95_ms_1w",
    "cold_ms_budget",
    "warm_p50_ms_budget",
    "warm_p95_ms_budget",
    "budget_tokens",
    "budget_windows",
    "budget_chars",
    "samples_collected",
    "cgroup_mem_mib",
    "oom_proximity_ratio",
    "container_mem_mib",
    "promptguard_loaded",
    "promptguard_model",
    "sanitizer_revision",
    "contract_version",
}


def health(**updates: object) -> HttpResponse:
    body: dict[str, object] = {
        "status": "healthy",
        "promptguard_loaded": True,
        "promptguard_model": DEFAULT_MODEL_ID,
        "sanitizer_revision": "a" * 64,
        "contract_version": "1.3.0",
    }
    body.update(updates)
    return HttpResponse(200, json.dumps(body))


@pytest.fixture(scope="module")
def tokenizer() -> PreTrainedTokenizerBase:
    return AutoTokenizer.from_pretrained(TOKENIZER_DIR, local_files_only=True)


@pytest.fixture(scope="module")
def documents(
    tokenizer: PreTrainedTokenizerBase,
) -> tuple[bench.Document, bench.Document]:
    settings = extraction_settings_from_config(
        yaml.safe_load(bench.BENCH_CONFIG.read_text())
    )
    return bench.generate_documents(tokenizer, settings.max_promptguard_chunks)


@dataclass
class Harness:
    output: Path
    clock: ManualClock = field(default_factory=ManualClock)
    health_responses: list[HttpResponse] = field(default_factory=lambda: [health()])
    metrics: HttpResponse = field(
        default_factory=lambda: HttpResponse(
            200,
            json.dumps(
                {
                    "extraction": {
                        "cgroup_memory_current_bytes": 123 * 1024**2,
                        "oom_proximity_ratio": 0.25,
                    },
                }
            ),
        )
    )
    failure_at: int | None = None
    failure: HttpResponse | Exception = field(
        default_factory=lambda: HttpResponse(500, SECRET)
    )
    stats_result: CommandResult = field(
        default_factory=lambda: CommandResult(0, "124MiB / 1GiB\n", "")
    )
    inspect_result: CommandResult = field(
        default_factory=lambda: CommandResult(0, "running 0", "")
    )
    posts: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    gets: list[str] = field(default_factory=list[str])
    commands: list[list[str]] = field(default_factory=list[list[str]])
    events: list[str] = field(default_factory=list[str])

    def fetch(self, url: str) -> HttpResponse:
        self.events.append(f"GET {url}")
        self.gets.append(url)
        if url.endswith("/metrics"):
            return self.metrics
        assert url.endswith("/health")
        if len(self.health_responses) > 1:
            return self.health_responses.pop(0)
        return self.health_responses[0]

    def post(
        self,
        url: str,
        *,
        file_name: str,
        file_bytes: bytes,
        fields: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        self.events.append(f"POST {file_name}")
        self.posts.append(
            {
                "url": url,
                "file_name": file_name,
                "file_bytes": file_bytes,
                "fields": fields,
                "timeout_seconds": timeout_seconds,
            }
        )
        position = len(self.posts) - 1
        self.clock.advance((50 if position == 0 else position) / 1000)
        if len(self.posts) == self.failure_at:
            if isinstance(self.failure, Exception):
                raise self.failure
            return self.failure
        return HttpResponse(200, "{}")

    def stats(self, argv: Sequence[str]) -> CommandResult:
        self.events.append(f"docker {argv[1]}")
        assert isinstance(argv, list)
        self.commands.append(list(argv))
        return self.inspect_result if argv[1] == "inspect" else self.stats_result

    def run(self, *extra: str) -> int:
        return bench.main(
            [
                "--input",
                "1w",
                "--tokenizer-dir",
                str(TOKENIZER_DIR),
                "--json",
                str(self.output),
                "--label",
                "22m-cpus1",
                *extra,
            ],
            post_fn=self.post,
            stats_fn=self.stats,
            fetch=self.fetch,
            clock=self.clock,
            sleep=self.clock.advance,
        )

    def row(self) -> dict[str, Any]:
        return json.loads(self.output.read_text())


@pytest.fixture
def harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tokenizer: PreTrainedTokenizerBase,
    documents: tuple[bench.Document, bench.Document],
) -> Harness:
    def load(path: Path, *, local_files_only: bool) -> PreTrainedTokenizerBase:
        assert path == TOKENIZER_DIR
        assert local_files_only is True
        return tokenizer

    monkeypatch.setattr(AutoTokenizer, "from_pretrained", load)

    def generate(
        selected: PreTrainedTokenizerBase, max_chunks: int
    ) -> tuple[bench.Document, bench.Document]:
        assert selected is tokenizer
        assert max_chunks == 64
        return documents

    monkeypatch.setattr(bench, "generate_documents", generate)
    return Harness(tmp_path / "row.json")


def test_shared_driver_public_imports() -> None:
    assert bench.http_get is contract_smoke.http_get
    assert bench.wait_for_health is contract_smoke.wait_for_health
    assert bench.run_command is contract_smoke.run_command
    assert bench.HttpResponse is contract_smoke.HttpResponse
    assert bench.CommandResult is contract_smoke.CommandResult
    source = Path(bench.__file__).read_text()
    assert "_json_object" not in source
    assert "CHARACTERS_PER_PROMPTGUARD_TOKEN" not in source
    assert "test_mapping:" in source


@pytest.mark.parametrize("chunks", [1, 3, 64])
def test_seeded_documents_are_maximal_and_match_classifier(
    tokenizer: PreTrainedTokenizerBase,
    chunks: int,
) -> None:
    one, budget = bench.generate_documents(tokenizer, chunks)
    assert (one, budget) == bench.generate_documents(tokenizer, chunks)
    classifier = PromptGuardClassifier()
    classifier._tokenizer = tokenizer
    for document, limit in ((one, 1), (budget, chunks)):
        assert len(document.text) <= max_extracted_characters(chunks)
        assert document.tokens == len(
            tokenizer.encode(document.text, add_special_tokens=False)
        )
        assert document.windows == bench.window_count(document.tokens)
        assert len(classifier._chunk_text(document.text)) == document.windows <= limit
        words = list(
            itertools.islice(bench.seeded_words(), len(document.text.split()) + 1)
        )
        extended = " ".join(words)
        assert extended.startswith(document.text + " ")
        assert (
            len(extended) > max_extracted_characters(chunks)
            or len(classifier._chunk_text(extended)) > limit
        )
        assert extract_upload_text(document.text.encode()).raw_text == document.text
        assert scan_structural(document.text).verdict == Stage2Verdict.CLEAN
    assert one.windows == 1


@pytest.mark.parametrize(
    "count", [0, 1, MAX_SEQ_LEN, MAX_SEQ_LEN + 1, 960, 961, 28736, 28737]
)
def test_window_arithmetic_at_boundaries(count: int) -> None:
    starts: list[int] = []
    for start in range(0, count, MAX_SEQ_LEN - CHUNK_OVERLAP):
        starts.append(start)
        if start + MAX_SEQ_LEN >= count:
            break
    assert bench.window_count(count) == max(1, len(starts))


def test_percentiles_are_nearest_rank() -> None:
    assert bench.nearest_rank(list(range(1, 21)), 50) == 10
    assert bench.nearest_rank(list(range(20, 0, -1)), 95) == 19
    assert bench.nearest_rank([1, 2, 3, 4], 95) is None
    assert bench.nearest_rank([], 50) is None
    assert bench.nearest_rank([1, 2, 3, 4, 5], 95) == 5


@pytest.mark.parametrize("input_name", ["1w", "budget"])
def test_full_measurement_shape_and_multipart_fields(
    harness: Harness,
    documents: tuple[bench.Document, bench.Document],
    capsys: pytest.CaptureFixture[str],
    input_name: str,
) -> None:
    assert harness.run("--container", "forage-bench", "--input", input_name) == 0
    row = harness.row()
    assert set(row) == KEYS
    assert row["outcome"] == "ok"
    assert row["non_2xx_reason"] is None
    assert row["label"] == "22m-cpus1"
    assert row["base_url"] == contract_smoke.DEFAULT_BASE_URL
    assert row["model_id"] == row["promptguard_model"] == DEFAULT_MODEL_ID
    assert row["promptguard_loaded"] is True
    assert row["contract_version"] == "1.3.0"
    assert row["sanitizer_revision"] == "a" * 64
    assert row["concurrency"] == 1
    assert row["runs"] == 20
    for suffix in ("1w", "budget"):
        if suffix == input_name:
            assert row[f"cold_ms_{suffix}"] == pytest.approx(50)
            assert row[f"warm_p50_ms_{suffix}"] == pytest.approx(10)
            assert row[f"warm_p95_ms_{suffix}"] == pytest.approx(19)
            assert row["samples_collected"][suffix] == pytest.approx(
                [50, *range(1, 21)]
            )
        else:
            assert row[f"cold_ms_{suffix}"] is None
            assert row[f"warm_p50_ms_{suffix}"] is None
            assert row[f"warm_p95_ms_{suffix}"] is None
            assert row["samples_collected"][suffix] == []
    assert row["budget_chars"] == len(documents[1].text)
    assert row["budget_tokens"] == documents[1].tokens
    assert row["budget_windows"] == documents[1].windows
    assert row["cgroup_mem_mib"] == 123
    assert row["oom_proximity_ratio"] == 0.25
    assert row["container_mem_mib"] == 124
    assert harness.gets == [
        "http://127.0.0.1:8020/health",
        "http://127.0.0.1:8020/metrics",
    ]
    assert harness.commands == [
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}}",
            "forage-bench",
        ]
    ]
    assert len(harness.posts) == 21
    name = f"forage-bench-{input_name}.txt"
    for call in harness.posts:
        assert call == {
            "url": "http://127.0.0.1:8020/extract",
            "file_name": name,
            "file_bytes": documents[0 if input_name == "1w" else 1].text.encode(),
            "fields": {"filename": name, "extract_mode": "full"},
            "timeout_seconds": 300,
        }
    assert harness.events == [
        "GET http://127.0.0.1:8020/health",
        *[f"POST {name}"] * 21,
        "GET http://127.0.0.1:8020/metrics",
        "docker stats",
    ]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == row
    assert "WARNING" not in captured.err


def test_httpx_adapter_without_client(monkeypatch: pytest.MonkeyPatch) -> None:
    post = Mock(return_value=httpx.Response(201, text="result"))
    monkeypatch.setattr(httpx, "post", post)
    fields = {"filename": "fixture.txt", "extract_mode": "full"}
    assert bench.post_extract(
        "http://localhost/extract",
        file_name="fixture.txt",
        file_bytes=b"synthetic",
        fields=fields,
        timeout_seconds=12,
    ) == HttpResponse(201, "result")
    post.assert_called_once_with(
        "http://localhost/extract",
        files={"file": ("fixture.txt", b"synthetic", "text/plain")},
        data=fields,
        timeout=12,
        trust_env=False,
        follow_redirects=False,
    )


@pytest.mark.parametrize("runs", [1, 4, 5])
@pytest.mark.parametrize("input_name", ["1w", "budget"])
def test_small_run_counts_and_timeout_forwarding(
    harness: Harness, runs: int, input_name: str
) -> None:
    assert (
        harness.run(
            "--input", input_name, "--runs", str(runs), "--timeout-seconds", "123"
        )
        == 0
    )
    row = harness.row()
    assert len(harness.posts) == runs + 1
    assert row[f"warm_p50_ms_{input_name}"] is not None
    assert (row[f"warm_p95_ms_{input_name}"] is None) is (runs < 5)
    assert all(call["timeout_seconds"] == 123 for call in harness.posts)


def test_health_wait_calls_shared_helper_with_healthy(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Mock(wraps=contract_smoke.wait_for_health)
    monkeypatch.setattr(bench, "wait_for_health", spy)
    harness.health_responses = [
        health(status="degraded", promptguard_loaded=False),
        health(),
    ]
    assert harness.run("--health-timeout-seconds", "17") == 0
    assert spy.call_count == 1
    assert spy.call_args.kwargs["expect_status"] == contract_smoke.STATUS_HEALTHY
    assert spy.call_args.kwargs["poll_interval_seconds"] == 5
    assert spy.call_args.kwargs["timeout_seconds"] == 17
    assert spy.call_args.kwargs["fetch"] == harness.fetch
    assert len(harness.gets) == 3


@pytest.mark.parametrize(
    "response",
    [
        health(status="degraded", promptguard_loaded=False),
        health(promptguard_loaded=False),
        health(promptguard_loaded=1),
        HttpResponse(200, "[]"),
        HttpResponse(200, SECRET),
        HttpResponse(503, "{}"),
        HttpResponse(0, SECRET),
    ],
)
def test_never_healthy_writes_no_row(
    harness: Harness,
    response: HttpResponse,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.health_responses = [response]
    assert harness.run("--container", "bench", "--health-timeout-seconds", "5") == 2
    assert not harness.output.exists()
    assert not harness.posts
    captured = capsys.readouterr()
    assert not captured.out
    for text in ("never_healthy", "timeout=5s", "last_status=", "--container=bench"):
        assert text in captured.err
    assert SECRET not in captured.err


def test_model_mismatch_is_not_a_measurement(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.health_responses = [health(promptguard_model=SECRET)]
    assert harness.run() == 2
    assert not harness.posts and not harness.output.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert "model_mismatch" in captured.err
    assert SECRET not in captured.err


@pytest.mark.parametrize("status", [404, 500, 302])
def test_first_non_2xx_explains_mount_and_writes_nothing(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    status: int,
) -> None:
    harness.failure_at = 1
    harness.failure = HttpResponse(status, SECRET)
    assert harness.run() == 2
    assert not harness.output.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert f"status={status}" in captured.err
    assert "bench/config.yaml" in captured.err and "/app/config.yaml" in captured.err
    assert SECRET not in captured.err


@pytest.mark.parametrize("input_name", ["1w", "budget"])
@pytest.mark.parametrize(
    "body",
    [
        {"reason": "content_too_large_to_classify"},
        {
            "error": "content_too_large_to_classify",
            "reason": DOCUMENT_FAILURE_REASONS["content_too_large_to_classify"],
        },
    ],
)
def test_first_document_budget_refusal_is_tokenizer_configuration_failure(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    input_name: str,
    body: dict[str, str],
) -> None:
    harness.failure_at = 1
    harness.failure = HttpResponse(422, json.dumps(body))
    assert harness.run("--input", input_name) == 2
    assert not harness.output.exists()
    captured = capsys.readouterr()
    assert not captured.out
    for text in (
        "tokenizer_mismatch",
        "422",
        "content_too_large_to_classify",
        "bench/config.yaml",
    ):
        assert text in captured.err


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError(SECRET),
        httpx.ReadTimeout(SECRET),
        OSError(SECRET),
        HttpResponse(0, SECRET),
    ],
)
def test_initial_connection_or_timeout_is_configuration_failure(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    failure: HttpResponse | Exception,
) -> None:
    harness.failure_at, harness.failure = 1, failure
    assert harness.run() == 2
    assert not harness.output.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert "http://127.0.0.1:8020" in captured.err
    assert SECRET not in captured.err


@pytest.mark.parametrize(
    ("failure", "outcome", "reason"),
    [
        (HttpResponse(500, SECRET), "non_2xx", None),
        (HttpResponse(422, json.dumps({"reason": SECRET})), "non_2xx", None),
        (
            HttpResponse(422, json.dumps({"reason": "content_too_large_to_classify"})),
            "non_2xx",
            "content_too_large_to_classify",
        ),
        (
            HttpResponse(
                422,
                json.dumps({"reason": DOCUMENT_FAILURE_REASONS["extraction_failed"]}),
            ),
            "non_2xx",
            DOCUMENT_FAILURE_REASONS["extraction_failed"],
        ),
        (httpx.ReadTimeout(SECRET), "timeout", None),
        (httpx.ConnectError(SECRET), "container_gone", None),
        (HttpResponse(0, SECRET), "container_gone", None),
    ],
)
@pytest.mark.parametrize("input_name", ["1w", "budget"])
def test_service_failure_retains_partial_samples_and_null_percentiles(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    failure: HttpResponse | Exception,
    outcome: str,
    reason: str | None,
    input_name: str,
) -> None:
    harness.failure_at, harness.failure = 4, failure
    assert harness.run("--input", input_name) == 1
    row = harness.row()
    assert set(row) == KEYS
    assert row["outcome"] == outcome
    assert row["non_2xx_reason"] == reason
    other_input = "budget" if input_name == "1w" else "1w"
    assert row["samples_collected"][input_name] == pytest.approx([50, 1, 2])
    assert row["samples_collected"][other_input] == []
    assert row[f"cold_ms_{input_name}"] == pytest.approx(50)
    for key in (
        f"cold_ms_{other_input}",
        "warm_p50_ms_1w",
        "warm_p95_ms_1w",
        "warm_p50_ms_budget",
        "warm_p95_ms_budget",
    ):
        assert row[key] is None
    captured = capsys.readouterr()
    assert json.loads(captured.out) == row
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("failure_at", [None, 1, 4])
def test_separate_fresh_services_give_process_cold_samples_not_first_for_input(
    harness: Harness,
    failure_at: int | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert harness.run("--input", "1w") == 0
    one_window_output = harness.output.read_bytes()
    assert harness.row()["cold_ms_budget"] is None
    capsys.readouterr()

    # Same loopback port, but an owner-replaced process with no inference history.
    fresh_service = Harness(harness.output.with_name("budget.json"))
    fresh_service.failure_at = failure_at
    fresh_service.health_responses = [
        health(status="degraded", promptguard_loaded=False),
        health(),
    ]
    expected_exit = 0 if failure_at is None else 2 if failure_at == 1 else 1
    assert fresh_service.run("--input", "budget") == expected_exit
    assert harness.output.read_bytes() == one_window_output
    assert len(harness.posts) == 21
    assert fresh_service.events[:3] == [
        "GET http://127.0.0.1:8020/health",
        "GET http://127.0.0.1:8020/health",
        "POST forage-bench-budget.txt",
    ]
    assert all(
        post["file_name"] == "forage-bench-budget.txt" for post in fresh_service.posts
    )
    captured = capsys.readouterr()
    if failure_at == 1:
        assert not fresh_service.output.exists()
        assert not captured.out
        return
    row = fresh_service.row()
    assert set(row) == KEYS
    assert row["cold_ms_1w"] is None
    assert row["cold_ms_budget"] == pytest.approx(50)
    assert row["samples_collected"]["1w"] == []
    assert row["samples_collected"]["budget"][0] == pytest.approx(50)
    assert row["warm_p50_ms_1w"] is None
    assert row["warm_p95_ms_1w"] is None
    assert row["warm_p50_ms_budget"] == (
        pytest.approx(10) if failure_at is None else None
    )
    assert row["warm_p95_ms_budget"] == (
        pytest.approx(19) if failure_at is None else None
    )


@pytest.mark.parametrize(
    ("inspect", "outcome"),
    [
        (CommandResult(0, "exited 137", ""), "container_gone"),
        (CommandResult(0, "dead 1", ""), "container_gone"),
        (CommandResult(0, "running 0", ""), "timeout"),
        (CommandResult(1, "", SECRET), "timeout"),
    ],
)
def test_timeout_checks_container_exit_without_echoing_docker(
    harness: Harness,
    inspect: CommandResult,
    outcome: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.failure_at, harness.failure = 2, httpx.ReadTimeout(SECRET)
    harness.inspect_result = inspect
    assert harness.run("--container", "bench") == 1
    assert harness.row()["outcome"] == outcome
    assert harness.commands[0] == [
        "docker",
        "inspect",
        "--format",
        "{{.State.Status}} {{.State.ExitCode}}",
        "bench",
    ]
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("container", [False, True])
@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, "", SECRET),
        CommandResult(0, SECRET, ""),
        CommandResult(0, "", ""),
    ],
)
def test_optional_stats_failure_warns_once_and_completes(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    container: bool,
    result: CommandResult,
) -> None:
    harness.stats_result = result
    assert harness.run(*(["--container", "bench"] if container else [])) == 0
    assert harness.row()["container_mem_mib"] is None
    captured = capsys.readouterr()
    assert captured.err.count("WARNING") == 1
    assert SECRET not in captured.err
    assert bool(harness.commands) is container


@pytest.mark.parametrize(
    ("text", "mib"),
    [
        ("1.5GiB / 2GiB\n", 1536),
        ("512KiB / 1GiB", 0.5),
        ("1048576B / 1GiB", 1),
        ("1MB / 1GB", 1000000 / 1024**2),
        ("1kB / 1GB", 1000 / 1024**2),
        ("-1MiB / 1GiB", None),
        ("NaNMiB / 1GiB", None),
        ("1MiB / 1GiB\n2MiB / 1GiB\n", None),
    ],
)
def test_docker_memory_units(text: str, mib: float | None) -> None:
    assert bench.parse_memory_mib(CommandResult(0, text, "")) == mib


@pytest.mark.parametrize(
    "metrics",
    [
        HttpResponse(0, SECRET),
        HttpResponse(500, SECRET),
        HttpResponse(200, "[]"),
        HttpResponse(200, '{"extraction":{"cgroup_memory_current_bytes":-1}}'),
        HttpResponse(200, '{"extraction":{"cgroup_memory_current_bytes":true}}'),
    ],
)
def test_bad_metrics_are_loud_but_preserve_latency_row(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    metrics: HttpResponse,
) -> None:
    harness.metrics = metrics
    assert harness.run("--container", "bench") == 0
    assert harness.row()["cgroup_mem_mib"] is None
    assert harness.row()["oom_proximity_ratio"] is None
    captured = capsys.readouterr()
    assert "WARNING metrics_" in captured.err
    assert SECRET not in captured.err


def test_null_cgroup_is_valid_outside_container(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.metrics = HttpResponse(
        200,
        json.dumps(
            {
                "extraction": {
                    "cgroup_memory_current_bytes": None,
                    "oom_proximity_ratio": None,
                }
            }
        ),
    )
    assert harness.run("--container", "bench") == 0
    row = harness.row()
    assert row["cgroup_mem_mib"] is None and row["oom_proximity_ratio"] is None
    assert "WARNING" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [
        ["--base-url", "ftp://localhost"],
        ["--base-url", "http://"],
        ["--base-url", "http://localhost:bad"],
        ["--base-url", f"http://user:{SECRET}@localhost"],
        ["--base-url", f"http://localhost?token={SECRET}"],
        ["--base-url", "http://localhost/\n"],
        ["--base-url", "http://localhost\x7f"],
        ["--container", "x;echo"],
        ["--container", "-x"],
        ["--container", "x y"],
        ["--runs", "0"],
        ["--runs", "-1"],
        ["--runs", "one"],
        ["--timeout-seconds", "nan"],
        ["--timeout-seconds", "0"],
        ["--health-timeout-seconds", "inf"],
        ["--health-timeout-seconds", "-1"],
        ["--tokenizer-dir", "/nonexistent/forage-tokenizer"],
        ["--input", "both"],
    ],
)
def test_invalid_arguments_fail_before_any_request(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
) -> None:
    with pytest.raises(SystemExit, match="2"):
        harness.run(*extra)
    assert not harness.gets and not harness.posts and not harness.commands
    assert not harness.output.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert SECRET not in captured.err


def test_tokenizer_and_single_input_required_and_help_is_offline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        bench.main(["--help"])
    help_text = capsys.readouterr().out
    assert "--tokenizer-dir" in help_text
    assert "--input {1w,budget}" in help_text
    with pytest.raises(SystemExit, match="2"):
        bench.main([])
    with pytest.raises(SystemExit, match="2"):
        bench.main(["--tokenizer-dir", str(TOKENIZER_DIR)])
    with pytest.raises(SystemExit, match="2"):
        bench.main(["--input", "budget"])


def test_bad_tokenizer_fails_before_request(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        AutoTokenizer, "from_pretrained", Mock(side_effect=OSError(SECRET))
    )
    assert harness.run() == 2
    assert not harness.gets and not harness.output.exists()
    captured = capsys.readouterr()
    assert "tokenizer_invalid" in captured.err and SECRET not in captured.err


def test_missing_provenance_does_not_publish_a_row(harness: Harness) -> None:
    harness.health_responses = [health(sanitizer_revision=None)]
    assert harness.run() == 2
    assert not harness.posts and not harness.output.exists()


def test_json_write_failure_reports_error_and_retains_stdout_row(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert harness.run("--json", str(harness.output.parent)) == 1
    captured = capsys.readouterr()
    assert "json_write_failed" in captured.err
    assert json.loads(captured.out)["outcome"] == "ok"


def test_stdout_only_run_and_selected_model(
    harness: Harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = "fixture/selected-model"
    harness.health_responses = [health(promptguard_model=model)]
    assert (
        bench.main(
            [
                "--input",
                "budget",
                "--tokenizer-dir",
                str(TOKENIZER_DIR),
                "--model-id",
                model,
                "--base-url",
                "https://localhost:8020/",
                "--runs",
                "1",
            ],
            post_fn=harness.post,
            stats_fn=harness.stats,
            fetch=harness.fetch,
            clock=harness.clock,
            sleep=harness.clock.advance,
        )
        == 0
    )
    assert not harness.output.exists()
    row = json.loads(capsys.readouterr().out)
    assert row["model_id"] == row["promptguard_model"] == model
    assert row["base_url"] == "https://localhost:8020"
    assert row["runs"] == 1


@pytest.mark.parametrize(
    "content", ["[not a mapping]", "extraction: [", "extraction: []"]
)
def test_invalid_benchmark_config_fails_before_requests(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(content)
    monkeypatch.setattr(bench, "BENCH_CONFIG", path)
    assert harness.run() == 2
    assert not harness.gets and not harness.output.exists()
    assert "bench_config_invalid" in capsys.readouterr().err


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 10**1000])
def test_nonfinite_or_overflowing_metrics_preserve_the_row(
    harness: Harness,
    value: int | float,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.metrics = HttpResponse(
        200,
        json.dumps(
            {
                "extraction": {
                    "cgroup_memory_current_bytes": value,
                    "oom_proximity_ratio": value,
                }
            }
        ),
    )
    assert harness.run("--container", "bench") == 0
    row = harness.row()
    assert row["cgroup_mem_mib"] is None and row["oom_proximity_ratio"] is None
    assert "WARNING metrics_invalid" in capsys.readouterr().err


def test_configuration_failure_does_not_relabel_an_old_output(harness: Harness) -> None:
    harness.output.write_text('{"label":"previous-run"}\n')
    harness.health_responses = [health(promptguard_loaded=False)]
    assert harness.run() == 2
    assert harness.output.read_text() == '{"label":"previous-run"}\n'


def test_benchmark_config_is_full_valid_reference_copy() -> None:
    text = bench.BENCH_CONFIG.read_text()
    assert text.splitlines()[0] == (
        "# BENCHMARK ONLY — enables the release-gated upload route on a throwaway, "
        "loopback-bound container; never a deployment config."
    )
    config: dict[str, Any] = yaml.safe_load(text)
    shipped: dict[str, Any] = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert config.keys() == shipped.keys()
    assert {key for key in config if config[key] != shipped[key]} == {
        "extract_route_enabled"
    }
    assert extraction_settings_from_config(config).route_enabled is True
    cache_settings_from_config(config)
    brave_settings_from_config(config)
    pg = promptguard_settings_from_config(config)
    assert pg.contiguity_windows == 0
    assert pg.contiguity_threshold == 0.5
    assert "# promptguard_contiguity_windows: 2" in text
    for key, value in config.items():
        assert key in KNOWN_CONFIG_KEYS
        if isinstance(value, dict):
            for child in cast(dict[str, object], value):
                assert f"{key}.{child}" in KNOWN_CONFIG_KEYS
    assert (ROOT / ".dockerignore").read_text().splitlines().count("bench/") == 1
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "bench/*.json" in ignored and "bench/tokenizer-*/" in ignored
    assert "bench" not in (ROOT / "Dockerfile").read_text()
    assert all(
        "bench/config" not in path.read_text()
        for path in (ROOT / "compose").glob("*.yml")
    )
