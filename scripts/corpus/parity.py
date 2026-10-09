"""Backend parity: prove a live classifier against a recorded cassette.

``uv run python -m scripts.corpus.parity --model-id <id> [--device cpu|cuda]
[--batch-size N] [--json PATH]``

A cassette holds no text, so a backend is proven by **re-driving the corpus**:
``drive_all`` with ``ParityClassifier`` (the live classifier, recording each
text's live and recorded scores side by side) and again with a plain
``ReplayClassifier`` over the cassette. The two drives are compared by the real
stage-3 rules, as a tuple of verdict signals per ``(record, route, config)``;
scores are compared per text hash and reported as drift, never as a verdict.

The run is valid only if it really happened as requested: after the live drive
the classifier's ``device_state()`` must show no failover, no OOM batch
reduction, the requested effective batch size and the requested device. A
synthetic long-text check then classifies a generated text of ``2 * batch + 3``
windows at batch 1 and at the requested batch on the **same** instance.

Exit 0 is clean, 1 is a failed run (the report's ``failures`` names why), 2 is a
refusal with one fixed reason word on stderr and nothing run. Output is ids,
hashes and numbers; no corpus text is ever printed or written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Final, Protocol, cast

from promptguard.classifier import DeviceState, PromptGuardBudgetExceededError
from promptguard.device import DeviceSettings
from scripts.corpus.drivers import drive_all
from scripts.corpus.live import Refusal, resolve_and_load
from scripts.corpus.outcomes import RouteResult, RuleConfig
from scripts.corpus.poolers import LIVE_THRESHOLD
from scripts.corpus.records import CorpusRecord, load_corpus
from scripts.corpus.replay import (
    CASSETTES_DIR,
    ReplayCall,
    ReplayClassifier,
    cassette_filename,
    read_cassette,
    text_sha256,
)

EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2

NEAR_THRESHOLD: Final = 0.01
"""A live score within this distance of the threshold is near it."""

_STEP: Final = 448
"""Tokens between window starts (``MAX_SEQ_LEN - CHUNK_OVERLAP``)."""

_CALIBRATION_ATTEMPTS: Final = 12

_ALL_CONFIGS: Final[tuple[RuleConfig, ...]] = ("default", "contiguity")

# Plain, invented filler: a short vocabulary, shuffled by a fixed stride. It is
# generated here and owes nothing to any corpus record.
_VOCABULARY: Final[tuple[str, ...]] = (
    "garden", "river", "window", "copper", "lantern", "meadow", "harbor",
    "pencil", "orchard", "velvet", "cabin", "marble", "ribbon", "island",
    "basket", "compass", "thistle", "anchor", "button", "candle", "dolphin",
    "engine", "feather", "glacier", "hammock", "iceberg", "jacket", "kettle",
    "ladder", "magnet", "needle", "oyster", "pillow", "quilt", "rocket",
    "saddle", "teapot", "umbrella", "violin", "walnut", "yogurt", "zipper",
    "bridge", "cotton", "desert", "ember", "forest", "granite", "honey",
    "ivory", "jungle",
)  # fmt: skip
_STRIDE: Final = 7


class LiveClassifier(Protocol):
    """What the tool needs of a live classifier (``PromptGuardClassifier``)."""

    @property
    def loaded(self) -> bool: ...

    def classify_windows(
        self, text: str, *, max_chunks: int | None = None
    ) -> tuple[list[float], list[str]]: ...

    def configure_batch_size(self, batch_size: int) -> None: ...

    def device_state(self) -> DeviceState: ...


class ParityClassifier(ReplayClassifier):
    """Wraps a live classifier; records ``(live, recorded)`` scores per text hash.

    Like ``RecordingClassifier`` it asks the live classifier for the **full**
    window list (never budgeted) and applies ``max_chunks`` itself afterwards,
    so the live drive raises the same budget error the replay does. A hash the
    cassette lacks is remembered as unrecorded and answered with the live score
    — the live drive completes, and the miss is the report's.
    """

    def __init__(
        self,
        live: LiveClassifier,
        scores: Mapping[str, Sequence[float]],
        *,
        model_id: str,
        revision: str,
    ) -> None:
        super().__init__(scores, model_id=model_id, revision=revision)
        self.live = live
        self.pairs: dict[str, tuple[tuple[float, ...], tuple[float, ...] | None]] = {}
        self.unrecorded: set[str] = set()

    @property
    def loaded(self) -> bool:
        """Mirrors the wrapped classifier."""
        return self.live.loaded

    def device_state(self) -> DeviceState:
        """The live classifier's device state."""
        return self.live.device_state()

    def classify_windows(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[list[float], list[str]]:
        """Classify unbudgeted, pair with the recording, then apply ``max_chunks``."""
        scores, chunks = self.live.classify_windows(text, max_chunks=None)
        sha = text_sha256(text)
        live = tuple(scores)
        recorded = self._scores.get(sha)
        self.pairs[sha] = (live, recorded)
        if recorded is None:
            self.unrecorded.add(sha)
        if max_chunks is not None and len(live) > max_chunks:
            raise PromptGuardBudgetExceededError(
                "PromptGuard classification input exceeds the chunk budget"
            )
        self.calls.append(ReplayCall(sha, len(text), live, recorded is not None))
        return list(live), chunks


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

_VERDICT_FIELDS: Final[tuple[str, ...]] = (
    "outcome",
    "injection_detected",
    "promptguard_state",
    "omit_reason",
    "refusal",
    "status_code",
)


def verdict_tuple(result: RouteResult) -> tuple[object, ...]:
    """The signals a stage-3 flip can move, in ``_VERDICT_FIELDS`` order."""
    signals = result.signals
    return (
        result.outcome,
        signals.injection_detected,
        signals.promptguard_state,
        signals.omit_reason,
        signals.refusal,
        signals.status_code,
    )


def verdict_changes(
    live: Iterable[RouteResult], replay: Iterable[RouteResult]
) -> list[dict[str, object]]:
    """Every ``(record, route, config)`` whose verdict tuple differs."""
    replayed = {(r.record_id, r.route, r.config): verdict_tuple(r) for r in replay}
    changes: list[dict[str, object]] = []
    for result in live:
        key = (result.record_id, result.route, result.config)
        other = replayed.get(key)
        if other is None:
            continue
        mine = verdict_tuple(result)
        if mine != other:
            changed = [
                name
                for name, a, b in zip(_VERDICT_FIELDS, mine, other, strict=True)
                if a != b
            ]
            changes.append(
                {"id": key[0], "route": key[1], "config": key[2], "fields": changed}
            )
    return changes


def shas_by_record(
    results: Sequence[RouteResult], calls: Sequence[ReplayCall]
) -> dict[str, set[str]] | None:
    """Which text hashes each record's drives classified; ``None`` if misaligned.

    A drive answers at most one call, and answers one exactly when its result
    carries window scores, so the two sequences walk together.
    """
    mapping: dict[str, set[str]] = {}
    cursor = 0
    for result in results:
        shas = mapping.setdefault(result.record_id, set())
        if result.signals.window_scores:
            if cursor >= len(calls):
                return None
            shas.add(calls[cursor].sha256)
            cursor += 1
    return mapping if cursor == len(calls) else None


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Drift:
    """Score drift over every text that has a recording."""

    max_window: float
    p99_window: float
    max_page: float
    count_mismatches: tuple[str, ...]
    """Hashes whose live and recorded window counts differ."""


def score_drift(
    pairs: Mapping[str, tuple[tuple[float, ...], tuple[float, ...] | None]],
) -> Drift:
    """Absolute window drift, and the drift of each text's maximum."""
    window: list[float] = []
    page = 0.0
    mismatched: list[str] = []
    for sha, (live, recorded) in sorted(pairs.items()):
        if recorded is None:
            continue
        if len(live) != len(recorded):
            mismatched.append(sha)
            continue
        window.extend(abs(a - b) for a, b in zip(live, recorded, strict=True))
        if live:
            page = max(page, abs(max(live) - max(recorded)))
    window.sort()
    p99 = window[max(0, math.ceil(0.99 * len(window)) - 1)] if window else 0.0
    return Drift(
        max_window=window[-1] if window else 0.0,
        p99_window=p99,
        max_page=page,
        count_mismatches=tuple(mismatched),
    )


def near_threshold_ids(results: Iterable[RouteResult]) -> list[str]:
    """Record ids with a live window score near the threshold (``NEAR_THRESHOLD``)."""
    return sorted(
        {
            result.record_id
            for result in results
            if any(
                abs(score - LIVE_THRESHOLD) <= NEAR_THRESHOLD
                for score in result.signals.window_scores
            )
        }
    )


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------


def validity_failures(state: DeviceState, *, device: str, batch_size: int) -> list[str]:
    """Why a run did not really happen as requested (empty when it did)."""
    failures: list[str] = []
    if state.failed_over or state.device_failovers > 0:
        failures.append("failed_over")
    if state.oom_batch_reductions > 0:
        failures.append("oom_batch_reduction")
    if state.oom_refused or state.oom_refusals > 0:
        failures.append("oom_refused")
    if state.effective_batch_size != batch_size:
        failures.append("batch_mismatch")
    if state.device != device:
        failures.append("device_mismatch")
    return failures


# ---------------------------------------------------------------------------
# The long-text check
# ---------------------------------------------------------------------------


def synthetic_text(words: int) -> str:
    """A benign text of ``words`` plain words, generated; no corpus text."""
    size = len(_VOCABULARY)
    return " ".join(_VOCABULARY[(index * _STRIDE) % size] for index in range(words))


def long_text_windows(batch_size: int) -> int:
    """How many windows the long-text check must produce."""
    return 2 * batch_size + 3


def _calibrated_batch_one(
    classifier: LiveClassifier, target: int
) -> tuple[str, list[float]]:
    """A synthetic text, and its scores at the instance's current batch (1).

    Words are not tokens, so the word count is nudged until the classifier
    itself reports ``target`` windows; the last attempt is returned even if it
    never did, and the caller's exact-count assertion then fails the run.
    """
    words = _STEP * (target - 1) + _STEP // 2 + 64
    text = synthetic_text(words)
    scores, _ = classifier.classify_windows(text, max_chunks=None)
    for _ in range(_CALIBRATION_ATTEMPTS):
        got = len(scores)
        if got == target or got == 0:
            break
        words = max(1, words + (target - got) * (_STEP // 2))
        text = synthetic_text(words)
        scores, _ = classifier.classify_windows(text, max_chunks=None)
    return text, list(scores)


def long_text_check(classifier: LiveClassifier, batch_size: int) -> dict[str, object]:
    """One synthetic text at batch 1, then at ``batch_size``, on the same instance."""
    target = long_text_windows(batch_size)
    classifier.configure_batch_size(1)
    text, single = _calibrated_batch_one(classifier, target)
    classifier.configure_batch_size(batch_size)
    batched, _ = classifier.classify_windows(text, max_chunks=None)
    failures: list[str] = []
    if len(single) != target or len(batched) != target:
        failures.append("long_text_window_count")
    drift = 0.0
    crossings = 0
    if len(single) == len(batched):
        drift = max(
            (abs(a - b) for a, b in zip(single, batched, strict=True)), default=0.0
        )
        crossings = sum(
            1
            for a, b in zip(single, batched, strict=True)
            if (a > LIVE_THRESHOLD) != (b > LIVE_THRESHOLD)
        )
    if crossings:
        failures.append("long_text_threshold_crossing")
    return {
        "target_windows": target,
        "windows_batch_1": len(single),
        "windows_requested_batch": len(batched),
        "max_window_drift": drift,
        "threshold_crossings": crossings,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _device_block(state: DeviceState, *, requested_batch: int) -> dict[str, object]:
    return {
        "device": state.device,
        "requested_device": state.requested_device,
        "torch": version("torch"),
        "fp32_precision": state.fp32_precision,
        "requested_batch_size": requested_batch,
        "effective_batch_size": state.effective_batch_size,
        "oom_batch_reductions": state.oom_batch_reductions,
        "oom_refusals": state.oom_refusals,
        "device_failovers": state.device_failovers,
        "failed_over": state.failed_over,
    }


async def _drives(
    records: Sequence[CorpusRecord],
    parity: ParityClassifier,
    replay: ReplayClassifier,
    configs: Sequence[RuleConfig],
) -> tuple[list[RouteResult], list[RouteResult], dict[str, list[str]]]:
    live_results = await drive_all(records, parity, configs=configs)
    owners = shas_by_record(live_results, parity.calls)
    excluded: dict[str, list[str]] = {}
    if owners is not None:
        for record_id, shas in owners.items():
            missing = sorted(shas & parity.unrecorded)
            if missing:
                excluded[record_id] = missing
    kept = [record for record in records if record.id not in excluded]
    replay_results = await drive_all(kept, replay, configs=configs)
    return live_results, replay_results, excluded


def run_parity(
    live: LiveClassifier,
    *,
    records: Sequence[CorpusRecord],
    cassette: Mapping[str, Sequence[float]],
    configs: Sequence[RuleConfig],
    model_id: str,
    revision: str,
    device: str,
    batch_size: int,
) -> tuple[dict[str, object], int]:
    """Drive, compare, validate; return the report and the exit status."""
    parity = ParityClassifier(live, cassette, model_id=model_id, revision=revision)
    replay = ReplayClassifier(cassette, model_id=model_id, revision=revision)
    live_results, replay_results, excluded = asyncio.run(
        _drives(records, parity, replay, configs)
    )
    failures: list[str] = []

    state = live.device_state()
    failures.extend(validity_failures(state, device=device, batch_size=batch_size))
    if shas_by_record(live_results, parity.calls) is None:
        failures.append("call_alignment")

    changes = verdict_changes(live_results, replay_results)
    drift = score_drift(parity.pairs)
    produced = set(parity.pairs)
    never_produced = sorted(set(cassette) - produced)
    unrecorded = sorted(parity.unrecorded)

    long_text = long_text_check(live, batch_size)
    after = live.device_state()
    failures.extend(
        reason
        for reason in validity_failures(after, device=device, batch_size=batch_size)
        if reason not in failures
    )
    failures.extend(cast(list[str], long_text["failures"]))

    if changes:
        failures.append("verdict_change")
    if drift.count_mismatches:
        failures.append("window_count_mismatch")
    if unrecorded:
        failures.append("unrecorded_sha")

    report: dict[str, object] = {
        **_device_block(after, requested_batch=batch_size),
        "model_id": model_id,
        "revision": revision,
        "configs": list(configs),
        "records_driven": len(records),
        "results_compared": len(replay_results),
        "max_window_drift": drift.max_window,
        "p99_window_drift": drift.p99_window,
        "max_page_drift": drift.max_page,
        "verdict_changes": changes,
        "window_count_mismatches": list(drift.count_mismatches),
        "near_threshold_ids": near_threshold_ids(live_results),
        "unrecorded_shas": unrecorded,
        "excluded_records": {key: excluded[key] for key in sorted(excluded)},
        "never_produced_shas": never_produced,
        "long_text": long_text,
        "failures": failures,
    }
    return report, EXIT_FAILED if failures else 0


def summary_line(report: Mapping[str, object]) -> str:
    """One numbers-only line for the terminal."""
    failures = cast(list[str], report["failures"])
    return (
        f"device={report['device']} batch={report['effective_batch_size']} "
        f"records={report['records_driven']} "
        f"max_window_drift={report['max_window_drift']:.3g} "
        f"verdict_changes={len(cast(list[object], report['verdict_changes']))} "
        f"unrecorded={len(cast(list[object], report['unrecorded_shas']))} "
        f"never_produced={len(cast(list[object], report['never_produced_shas']))} "
        f"failures={','.join(failures) if failures else 'none'}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.corpus.parity",
        description=(
            "Drive the corpus with a live classifier and compare it with the "
            "recorded cassette by the real stage-3 rules."
        ),
    )
    parser.add_argument("--model-id", required=True, help="an allowlisted model id")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--batch-size", type=int, default=1, help="windows per pass (cuda only)"
    )
    parser.add_argument("--json", type=Path, default=None, help="write the report")
    return parser


def _refuse(reason: str) -> int:
    print(reason, file=sys.stderr)
    return EXIT_REFUSED


def _cassette_configs(data: Mapping[str, object]) -> list[RuleConfig]:
    names = data.get("configs")
    if not isinstance(names, list):
        return []
    known: dict[str, RuleConfig] = {config: config for config in _ALL_CONFIGS}
    return [
        known[name]
        for name in cast(list[object], names)
        if isinstance(name, str) and name in known
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tool; return the process exit status."""
    args = _parser().parse_args(argv)
    model_id: str = args.model_id
    device: str = args.device
    batch_size: int = args.batch_size
    json_path: Path | None = args.json

    if batch_size < 1:
        return _refuse("batch_size_invalid")
    if device == "cpu" and batch_size != 1:
        return _refuse("batch_size_cpu")

    settings = DeviceSettings(
        device="cuda" if device == "cuda" else "cpu",
        fallback="refuse" if device == "cuda" else "cpu",
    )
    loaded = resolve_and_load(model_id, device_settings=settings, batch_size=batch_size)
    if isinstance(loaded, Refusal):
        return _refuse(loaded.reason)

    path = CASSETTES_DIR / cassette_filename(loaded.model_id, loaded.revision)
    if not path.is_file():
        return _refuse("no_cassette")
    data = read_cassette(path)
    if data["model_id"] != loaded.model_id or data["revision"] != loaded.revision:
        return _refuse("cassette_mismatch")
    configs = _cassette_configs(data)
    if not configs:
        return _refuse("cassette_configs")
    records = cast(dict[str, dict[str, list[float]]], data["records"])

    report, status = run_parity(
        loaded.classifier,
        records=load_corpus(),
        cassette={sha: entry["scores"] for sha, entry in records.items()},
        configs=configs,
        model_id=loaded.model_id,
        revision=loaded.revision,
        device=device,
        batch_size=batch_size,
    )
    if json_path is not None:
        json_path.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    print(summary_line(report))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
