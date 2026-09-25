"""Host-side, single-in-flight benchmark of a running Forage service.

Run with ``uv run python -m scripts.bench_promptguard --help``. No weights,
Docker or network are needed for help. Memory readings are after warm-up, not
peak. Each invocation measures only --input (1w or budget). The owner starts a
separate fresh container for each input, with no earlier inference requests;
this tool never restarts it. Only the selected input's cold field is populated.

test_mapping:
  scripts/bench_promptguard.py: tests/test_bench_promptguard.py
  bench/config.yaml: tests/test_bench_promptguard.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import urlsplit

import httpx
import yaml

from contract_smoke import (
    DEFAULT_BASE_URL,
    STATUS_HEALTHY,
    CommandResult,
    Fetcher,
    HttpResponse,
    Runner,
    http_get,
    run_command,
    wait_for_health,
)
from pipeline.contract import EXTRACT_422_ERROR_CODES
from pipeline.extraction_limits import (
    extraction_settings_from_config,
    max_extracted_characters,
)
from pipeline.orchestrator import DOCUMENT_FAILURE_REASONS
from promptguard.classifier import CHUNK_OVERLAP, DEFAULT_MODEL_ID, MAX_SEQ_LEN

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

BENCH_CONFIG = Path(__file__).resolve().parents[1] / "bench" / "config.yaml"
_WORDS = [
    "garden",
    "river",
    "meadow",
    "forest",
    "stone",
    "bird",
    "leaf",
    "water",
    "spring",
    "summer",
    "autumn",
    "winter",
    "morning",
    "evening",
    "sunlight",
    "gentle",
    "quiet",
    "green",
    "blue",
    "brown",
    "grows",
    "flows",
    "rests",
]
_SEED = 20260919
_MIB = 1024 * 1024
Outcome = Literal["ok", "non_2xx", "timeout", "container_gone"]
InputName = Literal["1w", "budget"]


class Post(Protocol):
    def __call__(
        self,
        url: str,
        *,
        file_name: str,
        file_bytes: bytes,
        fields: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse: ...


class BenchmarkConfigurationError(ValueError):
    """A run that must not publish a benchmark row."""


@dataclass(frozen=True)
class Document:
    text: str
    tokens: int
    windows: int


def seeded_words() -> Iterator[str]:
    rng = random.Random(_SEED)
    while True:
        yield rng.choice(_WORDS)


def window_count(tokens: int) -> int:
    return 1 + max(0, math.ceil((tokens - MAX_SEQ_LEN) / (MAX_SEQ_LEN - CHUNK_OVERLAP)))


def generate_documents(
    tokenizer: PreTrainedTokenizerBase, max_chunks: int
) -> tuple[Document, Document]:
    """Find whole-word prefixes under the real token and character gates."""
    char_limit = max_extracted_characters(max_chunks)
    words: list[str] = []
    characters = -1
    for word in seeded_words():
        words.append(word)
        characters += len(word) + 1
        if characters > char_limit:
            break

    def longest(token_limit: int) -> Document:
        low, high = 0, len(words)
        while low < high:
            middle = (low + high + 1) // 2
            text = " ".join(words[:middle])
            if (
                len(text) <= char_limit
                and len(tokenizer.encode(text, add_special_tokens=False)) <= token_limit
            ):
                low = middle
            else:
                high = middle - 1
        text = " ".join(words[:low])
        tokens = len(tokenizer.encode(text, add_special_tokens=False))
        if not text or not tokens:
            raise BenchmarkConfigurationError(
                "tokenizer_invalid: no classifiable document"
            )
        return Document(text, tokens, window_count(tokens))

    return (
        longest(MAX_SEQ_LEN),
        longest(MAX_SEQ_LEN + (max_chunks - 1) * (MAX_SEQ_LEN - CHUNK_OVERLAP)),
    )


def nearest_rank(samples: Sequence[float], percentile: int) -> float | None:
    if not samples or (percentile == 95 and len(samples) < 5):
        return None
    return sorted(samples)[math.ceil(percentile / 100 * len(samples)) - 1]


def post_extract(
    url: str,
    *,
    file_name: str,
    file_bytes: bytes,
    fields: dict[str, str],
    timeout_seconds: float,
) -> HttpResponse:
    response = httpx.post(
        url,
        files={"file": (file_name, file_bytes, "text/plain")},
        data=fields,
        timeout=timeout_seconds,
        trust_env=False,
        follow_redirects=False,
    )
    return HttpResponse(response.status_code, response.text)


def _object(body: str) -> dict[str, object]:
    try:
        value: object = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _warn(message: str) -> None:
    print(f"WARNING {message}", file=sys.stderr)


def parse_memory_mib(result: CommandResult) -> float | None:
    if result.exit_code != 0:
        return None
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*/[^\n]+\s*",
        result.stdout,
    )
    if match is None:
        return None
    factors = {
        "B": 1,
        "kB": 1000,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": _MIB,
        "GiB": 1024**3,
        "TiB": 1024**4,
    }
    value = float(match[1]) * factors[match[2]] / _MIB
    return value if math.isfinite(value) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _memory(
    row: dict[str, object],
    base_url: str,
    container: str | None,
    fetch: Fetcher,
    stats_fn: Runner,
) -> None:
    response = fetch(f"{base_url}/metrics")
    extraction = _object(response.body).get("extraction")
    if response.status != 200 or not isinstance(extraction, dict):
        _warn("metrics_unavailable: after warm-up, not peak")
    else:
        metrics = cast(dict[str, object], extraction)
        current = _number(metrics.get("cgroup_memory_current_bytes"))
        row["cgroup_mem_mib"] = None if current is None else current / _MIB
        row["oom_proximity_ratio"] = _number(metrics.get("oom_proximity_ratio"))
        if any(
            key not in metrics
            or (metrics[key] is not None and _number(metrics[key]) is None)
            for key in ("cgroup_memory_current_bytes", "oom_proximity_ratio")
        ):
            _warn("metrics_invalid: after warm-up, not peak")
    memory = None
    if container is not None:
        memory = parse_memory_mib(
            stats_fn(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.MemUsage}}",
                    container,
                ]
            )
        )
    row["container_mem_mib"] = memory
    if memory is None:
        _warn("container_memory_unavailable: after warm-up, not peak")


def _closed_reason(response: HttpResponse) -> str | None:
    if response.status != 422:
        return None
    reason = _object(response.body).get("reason")
    allowed = EXTRACT_422_ERROR_CODES | frozenset(DOCUMENT_FAILURE_REASONS.values())
    return reason if isinstance(reason, str) and reason in allowed else None


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _positive_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be finite and positive") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def _base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        httpx.URL(value)
        valid = (
            re.match(r"^https?://", value) is not None
            and bool(parsed.hostname)
            and parsed.port != 0
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in ("", "/")
            and not any(c.isspace() or ord(c) < 32 for c in value)
        )
    except (ValueError, httpx.InvalidURL):
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError(
            "must be an http(s) origin without credentials, path, query or fragment"
        )
    return value.rstrip("/")


def _container(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None:
        raise argparse.ArgumentTypeError("invalid container name")
    return value


def _tokenizer_dir(value: str) -> Path:
    path = Path(value)
    if not path.is_dir():
        raise argparse.ArgumentTypeError("tokenizer directory must exist")
    return path


@dataclass(frozen=True)
class Options:
    input_name: InputName
    tokenizer_dir: Path
    model_id: str
    base_url: str
    container: str | None
    runs: int
    json_path: Path | None
    label: str
    timeout_seconds: float
    health_timeout_seconds: float


def _options(argv: Sequence[str] | None) -> Options:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        dest="input_name",
        choices=("1w", "budget"),
        required=True,
        help="measure this input only, against a fresh service with no prior inference",
    )
    parser.add_argument("--tokenizer-dir", type=_tokenizer_dir, required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--base-url", type=_base_url, default=DEFAULT_BASE_URL)
    parser.add_argument("--container", type=_container)
    parser.add_argument("--runs", type=_positive_int, default=20)
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--label", default="")
    parser.add_argument("--timeout-seconds", type=_positive_seconds, default=300.0)
    parser.add_argument(
        "--health-timeout-seconds", type=_positive_seconds, default=900.0
    )
    args = parser.parse_args(argv)
    return Options(**vars(args))


def _run(
    options: Options,
    *,
    post_fn: Post,
    stats_fn: Runner,
    fetch: Fetcher,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, object]:
    from transformers import AutoTokenizer

    try:
        config: object = yaml.safe_load(BENCH_CONFIG.read_text())
        if not isinstance(config, dict):
            raise ValueError
        settings = extraction_settings_from_config(cast(dict[str, object], config))
    except (OSError, ValueError, yaml.YAMLError):
        raise BenchmarkConfigurationError(
            "bench_config_invalid: restore bench/config.yaml"
        ) from None
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            options.tokenizer_dir, local_files_only=True
        )
        one, budget = generate_documents(tokenizer, settings.max_promptguard_chunks)
    except (OSError, ValueError, KeyError):
        raise BenchmarkConfigurationError(
            "tokenizer_invalid: use the selected model's local snapshot"
        ) from None

    response = wait_for_health(
        options.base_url,
        expect_status=STATUS_HEALTHY,
        timeout_seconds=options.health_timeout_seconds,
        poll_interval_seconds=5,
        fetch=fetch,
        clock=clock,
        sleep=sleep,
        log=lambda message: print(message, file=sys.stderr),
    )
    health = _object(response.body)
    status = health.get("status")
    if (
        response.status != 200
        or status != STATUS_HEALTHY
        or health.get("promptguard_loaded") is not True
    ):
        last_status = status if status in ("healthy", "degraded") else "unknown"
        container = f" --container={options.container}" if options.container else ""
        raise BenchmarkConfigurationError(
            f"never_healthy: timeout={options.health_timeout_seconds:g}s "
            f"last_status={last_status} http_status={response.status} "
            f"url={options.base_url}{container}"
        )
    if health.get("promptguard_model") != options.model_id:
        raise BenchmarkConfigurationError(
            "model_mismatch: --model-id differs from /health"
        )
    if any(
        not isinstance(health.get(key), str) or not health[key]
        for key in ("sanitizer_revision", "contract_version")
    ):
        raise BenchmarkConfigurationError(
            "health_invalid: missing benchmark provenance"
        )

    samples: dict[str, list[float]] = {"1w": [], "budget": []}
    row: dict[str, object] = {
        "label": options.label,
        "base_url": options.base_url,
        "model_id": options.model_id,
        "runs": options.runs,
        "concurrency": 1,
        "outcome": "ok",
        "non_2xx_reason": None,
        "cold_ms_1w": None,
        "warm_p50_ms_1w": None,
        "warm_p95_ms_1w": None,
        "cold_ms_budget": None,
        "warm_p50_ms_budget": None,
        "warm_p95_ms_budget": None,
        "budget_tokens": budget.tokens,
        "budget_windows": budget.windows,
        "budget_chars": len(budget.text),
        "samples_collected": samples,
        "cgroup_mem_mib": None,
        "oom_proximity_ratio": None,
        "container_mem_mib": None,
        **{
            key: health[key]
            for key in (
                "promptguard_loaded",
                "promptguard_model",
                "sanitizer_revision",
                "contract_version",
            )
        },
    }
    name = options.input_name
    document = one if name == "1w" else budget
    file_name = f"forage-bench-{name}.txt"
    content = document.text.encode("utf-8")
    for sample in range(options.runs + 1):
        outcome: Outcome = "ok"
        reason = None
        started = clock()
        try:
            response = post_fn(
                f"{options.base_url}/extract",
                file_name=file_name,
                file_bytes=content,
                fields={"filename": file_name, "extract_mode": "full"},
                timeout_seconds=options.timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError):
            outcome = "timeout"
        except (httpx.RequestError, OSError):
            outcome = "container_gone"
        else:
            if response.status == 0:
                outcome = "container_gone"
            elif not 200 <= response.status < 300:
                outcome = "non_2xx"
                reason = _closed_reason(response)
                body = _object(response.body)
                if (
                    response.status == 422
                    and sample == 0
                    and (
                        body.get("error") == "content_too_large_to_classify"
                        or reason
                        in (
                            "content_too_large_to_classify",
                            DOCUMENT_FAILURE_REASONS["content_too_large_to_classify"],
                        )
                    )
                ):
                    raise BenchmarkConfigurationError(
                        "tokenizer_mismatch: status=422 "
                        "reason=content_too_large_to_classify; use the container's "
                        "tokenizer and mount bench/config.yaml at /app/config.yaml"
                    )
        elapsed = (clock() - started) * 1000
        if outcome != "ok":
            if not samples[name]:
                raise BenchmarkConfigurationError(
                    f"extract_unavailable: {outcome} status="
                    f"{response.status if outcome == 'non_2xx' else 0} "
                    f"reason={reason or 'unknown'} "
                    f"url={options.base_url} timeout={options.timeout_seconds:g}s; "
                    "mount bench/config.yaml at /app/config.yaml"
                )
            if outcome == "timeout" and options.container:
                state = stats_fn(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.State.Status}} {{.State.ExitCode}}",
                        options.container,
                    ]
                )
                if state.exit_code == 0 and state.stdout.split()[:1] in (
                    ["exited"],
                    ["dead"],
                    ["removing"],
                ):
                    outcome = "container_gone"
            row["outcome"] = outcome
            row["non_2xx_reason"] = reason
            _warn(f"measurement_failed: {outcome}")
            break
        samples[name].append(elapsed)
        if sample == 0:
            row[f"cold_ms_{name}"] = elapsed
    if row["outcome"] == "ok":
        row[f"warm_p50_ms_{name}"] = nearest_rank(samples[name][1:], 50)
        row[f"warm_p95_ms_{name}"] = nearest_rank(samples[name][1:], 95)
    _memory(row, options.base_url, options.container, fetch, stats_fn)
    return row


def main(
    argv: Sequence[str] | None = None,
    *,
    post_fn: Post = post_extract,
    stats_fn: Runner = run_command,
    fetch: Fetcher = http_get,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    options = _options(argv)
    try:
        row = _run(
            options,
            post_fn=post_fn,
            stats_fn=stats_fn,
            fetch=fetch,
            clock=clock,
            sleep=sleep,
        )
    except BenchmarkConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    output = json.dumps(row, indent=2, allow_nan=False) + "\n"
    if options.json_path is not None:
        try:
            options.json_path.write_text(output, encoding="utf-8")
        except OSError:
            print("json_write_failed: benchmark row follows on stdout", file=sys.stderr)
            print(output, end="")
            return 1
    print(output, end="")
    return 0 if row["outcome"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
