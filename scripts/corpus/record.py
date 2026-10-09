"""The host-side recorder: drive the corpus through the real model, write a cassette.

``uv run python -m scripts.corpus.record --model-id <id> [--out DIR]
[--configs default,contiguity]``

The recorder is the harness in record mode: ``drive_all`` over every record
with ``RecordingClassifier`` in place of the replay classifier. The wrapper
asks the real classifier for the **full** window list of every text stage 3
sends it (never budgeted, ruling 10), keeps ``sha256 -> scores``, and applies
the route's ``max_chunks`` itself, so the drive's outcome while recording is
the replay's.

Refusals are exit 2 with one fixed reason word on stderr and nothing written:
``model_env_set``, ``model_id_not_allowed``, ``not_pinned``, ``not_loaded``,
``unscanned``. No model-selection environment variable is read —
``--model-id`` is not an alias for ``FORAGE_MODEL_ID``, and the recorder
refuses to run with it or ``FORAGE_MODEL_REVISION`` set, because the booted
app would hash one model while the recorder loaded another. Credentials
(``HF_TOKEN``, ``FORAGE_MIRROR_TOKEN``) are read by ``acquire_and_load`` from
the environment exactly as in the container; nothing here parses or prints
them. Output is ids and numbers only; a cassette holds no text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Final

from pipeline.sanitizer_revision import derive_sanitizer_revision
from promptguard.classifier import (
    PromptGuardBudgetExceededError,
    PromptGuardClassifier,
)
from scripts.corpus.drivers import drive_all
from scripts.corpus.live import Refusal, resolve_and_load
from scripts.corpus.outcomes import RouteResult, RuleConfig
from scripts.corpus.records import load_corpus
from scripts.corpus.replay import (
    CASSETTE_FORMAT,
    CASSETTES_DIR,
    ReplayCall,
    ReplayClassifier,
    cassette_filename,
    text_sha256,
)

EXIT_REFUSED: Final = 2

# The `promptguard_state`s and the `/search` omission reason that mean stage 3
# was expected to see a text and never did (the `model_unavailable` path).
_UNSCANNED_STATES: Final[frozenset[str]] = frozenset(
    {"unavailable_blocked", "unavailable_allowed"}
)
_UNSCANNED_OMISSION: Final = "promptguard_unavailable"

_ALL_CONFIGS: Final[tuple[RuleConfig, ...]] = ("default", "contiguity")


class RecordingClassifier(ReplayClassifier):
    """Wraps the real classifier; records full window lists keyed by text hash.

    ``classify_windows`` always calls the inner classifier unbudgeted, stores
    the scores, then applies ``max_chunks`` exactly as the real method does —
    so a recorded drive and its replay raise the same budget error at the same
    point. It is a ``ReplayClassifier`` so the drivers read its call log and
    ``model_id`` unchanged; it never answers from a table.
    """

    def __init__(
        self,
        inner: PromptGuardClassifier,
        *,
        model_id: str,
        revision: str,
    ) -> None:
        super().__init__({}, model_id=model_id, revision=revision)
        self.inner = inner
        self.records: dict[str, tuple[float, ...]] = {}

    @property
    def loaded(self) -> bool:
        """Mirrors the wrapped classifier."""
        return self.inner.loaded

    def classify_windows(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[list[float], list[str]]:
        """Classify unbudgeted, record, then apply ``max_chunks``."""
        scores, chunks = self.inner.classify_windows(text, max_chunks=None)
        sha = text_sha256(text)
        self.records[sha] = tuple(scores)
        if max_chunks is not None and len(scores) > max_chunks:
            raise PromptGuardBudgetExceededError(
                "PromptGuard classification input exceeds the chunk budget"
            )
        self.calls.append(ReplayCall(sha, len(text), tuple(scores), True))
        return list(scores), chunks


def unscanned_count(results: Sequence[RouteResult]) -> int:
    """Drive results stage 3 was expected to classify and never saw."""
    return sum(
        1
        for result in results
        if result.signals.promptguard_state in _UNSCANNED_STATES
        or result.signals.omit_reason == _UNSCANNED_OMISSION
    )


def cassette_document(
    recorder: RecordingClassifier,
    *,
    configs: Sequence[RuleConfig],
    recorded_at: datetime,
) -> dict[str, object]:
    """The cassette object for everything ``recorder`` saw."""
    return {
        "format": CASSETTE_FORMAT,
        "model_id": recorder.model_id,
        "revision": recorder.revision,
        "recorded_at": recorded_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sanitizer_revision": derive_sanitizer_revision({}),
        "torch": version("torch"),
        "transformers": version("transformers"),
        "configs": list(configs),
        "records": {
            sha: {"scores": list(scores), "windows": len(scores)}
            for sha, scores in recorder.records.items()
        },
    }


def write_cassette(document: Mapping[str, object], out_dir: Path) -> Path:
    """Write ``document`` atomically (same-directory temp file, ``os.replace``)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / cassette_filename(
        str(document["model_id"]), str(document["revision"])
    )
    text = json.dumps(document, sort_keys=True, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


def summary_line(records: int, recorder: RecordingClassifier, unscanned: int) -> str:
    """``records=N texts=M windows_histogram={1: a, …} unscanned=K`` — numbers only."""
    histogram = Counter(len(scores) for scores in recorder.records.values())
    ordered = {windows: histogram[windows] for windows in sorted(histogram)}
    return (
        f"records={records} texts={len(recorder.records)} "
        f"windows_histogram={ordered} unscanned={unscanned}"
    )


def _parse_configs(value: str) -> list[RuleConfig]:
    configs: list[RuleConfig] = []
    for name in value.split(","):
        known: dict[str, RuleConfig] = {config: config for config in _ALL_CONFIGS}
        config = known.get(name.strip())
        if config is None or config in configs:
            raise argparse.ArgumentTypeError("unknown or repeated config")
        configs.append(config)
    return configs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.corpus.record",
        description=(
            "Record the real classifier's per-window scores for every corpus "
            "text into a cassette. Credentials come from the environment only."
        ),
    )
    parser.add_argument("--model-id", required=True, help="an allowlisted model id")
    parser.add_argument(
        "--out", type=Path, default=CASSETTES_DIR, help="cassette directory"
    )
    parser.add_argument(
        "--configs",
        type=_parse_configs,
        default=list(_ALL_CONFIGS),
        help="comma-separated rule configs (default: default,contiguity)",
    )
    return parser


def _refuse(reason: str) -> int:
    print(reason, file=sys.stderr)
    return EXIT_REFUSED


def main(argv: Sequence[str] | None = None) -> int:
    """Run the recorder; return the process exit status."""
    args = _parser().parse_args(argv)
    model_id: str = args.model_id
    out_dir: Path = args.out
    configs: list[RuleConfig] = args.configs

    loaded = resolve_and_load(model_id)
    if isinstance(loaded, Refusal):
        return _refuse(loaded.reason)
    classifier = loaded.classifier
    revision = loaded.revision

    records = load_corpus()
    recorder = RecordingClassifier(classifier, model_id=model_id, revision=revision)
    results = asyncio.run(drive_all(records, recorder, configs=configs))
    unscanned = unscanned_count(results)
    if unscanned:
        return _refuse("unscanned")

    document = cassette_document(
        recorder, configs=configs, recorded_at=datetime.now(UTC)
    )
    write_cassette(document, out_dir)
    print(summary_line(len(records), recorder, unscanned))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
