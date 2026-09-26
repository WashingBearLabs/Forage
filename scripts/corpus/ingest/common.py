"""Shared sampler plumbing: the input guard, the reason vocabulary, the CLI.

Every failure a sampler surfaces is a closed reason code
(``failure_reason``, the shape of ``model_fetcher._fetch_reason``): never the
exception's message, never a URL, since either can carry a signed URL or a
token. A sampler downloads nothing — it reads a local file the operator
fetched by hand, and ``huggingface_hub`` alone reads ``HF_TOKEN`` for that
fetch, so no token is ever a sampler argument.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from model_fetcher import redact_reference
from scripts.corpus import vocab
from scripts.corpus.ingest import render
from scripts.corpus.records import LINT_RULES, record_from_mapping

REPO_ROOT: Final[Path] = vocab.TESTS_CORPUS_ROOT.parents[1]
DEFAULT_OUT: Final[Path] = vocab.TESTS_CORPUS_ROOT / "attacks"
DEFAULT_SEED: Final[int] = 20260919
MAX_TEXT_CHARS: Final[int] = 20_000

# The closed vocabulary of refusal reasons a sampler may print.
REFUSAL_REASONS: Final[frozenset[str]] = frozenset(
    {
        "input_inside_repo",
        "input_missing",
        "input_malformed",
        "input_sha256_mismatch",
        "already_ingested",
        "io_failed",
        "timeout",
        "ingest_failed",
    }
)


class IngestError(Exception):
    """A refusal carrying one ``REFUSAL_REASONS`` code and nothing else."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


def failure_reason(exc: BaseException) -> str:
    """Map any failure to a closed, credential-free reason code — never ``str(exc)``."""
    if isinstance(exc, IngestError):
        return exc.reason
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    if isinstance(exc, ValueError | SyntaxError | KeyError | TypeError):
        return "input_malformed"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "io_failed"
    return "ingest_failed"


def check_input_path(path: Path, repo_root: Path = REPO_ROOT) -> Path:
    """The canonical input path; refused when it resolves inside the repository.

    Symlinks and ``..`` are followed first (``Path.resolve``), so a link outside
    the tree that points into it is refused too — raw downloads never become
    committable files.
    """
    resolved = path.expanduser().resolve()
    root = repo_root.resolve()
    if resolved == root or root in resolved.parents:
        raise IngestError("input_inside_repo")
    if not resolved.exists():
        raise IngestError("input_missing")
    return resolved


def check_sha256(path: Path, expected: str | None) -> None:
    """Refuse a file input whose sha256 is not the recorded one."""
    if expected is None:
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected.lower():
        raise IngestError("input_sha256_mismatch")


@dataclass(frozen=True, slots=True)
class Row:
    """One upstream row, as read — never printed."""

    ref: str
    text: str
    subject: str = ""
    marker_text: str | None = None


# (row, url-rewritten text, url-rewritten subject, record number) -> (surface, payload)
Renderer = Callable[[Row, str, str, int], tuple[str, dict[str, str]]]


@dataclass(frozen=True, slots=True)
class Source:
    """What a sampler writes into every record's ``source`` and how it renders."""

    name: str
    url: str
    licence: str
    framing: str
    shape: str
    default_limit: int
    render: Renderer


@dataclass(slots=True)
class Report:
    """Counts only: what was read, written and skipped, and the id range."""

    rows_read: int = 0
    written: int = 0
    skipped: Counter[str] = field(default_factory=Counter[str])
    ids: list[str] = field(default_factory=list[str])
    categories: Counter[str] = field(default_factory=Counter[str])

    def lines(self, name: str, revision: str) -> list[str]:
        span = f"{self.ids[0]}..{self.ids[-1]}" if self.ids else "none"
        skipped = ", ".join(f"{k}={v}" for k, v in sorted(self.skipped.items()))
        categories = ", ".join(f"{k}={v}" for k, v in sorted(self.categories.items()))
        return [
            f"{name} @ {redact_reference(revision)}",
            f"rows read: {self.rows_read}",
            f"records written: {self.written} ({span})",
            f"categories: {categories or 'none'}",
            f"rows skipped: {skipped or 'none'}",
        ]


def shuffled(rows: Sequence[Row], seed: int) -> list[Row]:
    """``rows`` in a seed-fixed order independent of input order."""
    ordered = sorted(rows, key=lambda row: row.ref)
    random.Random(seed).shuffle(ordered)
    return ordered


def _existing(out: Path) -> tuple[int, set[str]]:
    """The highest ``atk`` number under ``out`` and the source names already there."""
    highest = 0
    names: set[str] = set()
    for path in sorted(out.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = cast(dict[str, object], json.loads(line))
            record_id = obj.get("id")
            if isinstance(record_id, str) and record_id.startswith("atk-"):
                highest = max(highest, int(record_id.removeprefix("atk-")))
            source = obj.get("source")
            if isinstance(source, dict):
                name = cast(dict[str, object], source).get("name")
                if isinstance(name, str):
                    names.add(name)
    return highest, names


def build_record(
    row: Row, source: Source, revision: str, record_id: str, number: int
) -> dict[str, object] | str:
    """The record for ``row``, or the closed reason it was skipped."""
    if render.has_secret_shape(f"{row.subject}\n{row.text}"):
        return "secret_shape"
    text = render.rewrite_urls(row.text)
    subject = render.rewrite_urls(row.subject)
    if not text.strip():
        return "empty"
    whole = f"{subject}\n{text}" if subject else text
    if len(whole) > MAX_TEXT_CHARS:
        return "oversize"
    marker = render.choose_marker(render.rewrite_urls(row.marker_text or row.text))
    if marker is None:
        return "no_marker"
    category = render.assign_category(whole)
    surface, payload = source.render(row, text, subject, number)
    stage2 = "none" if category == "natural_language" else category
    record: dict[str, object] = {
        "id": record_id,
        "kind": "attack",
        "category": category,
        "surface": surface,
        "payload": payload,
        "marker": marker,
        "pinned": None,
        "pinned_reason": None,
        "source": {
            "kind": "third_party",
            "name": source.name,
            "url": source.url,
            "licence": source.licence,
            "revision": revision,
            "record_ref": row.ref,
            "framing": source.framing,
        },
        "lang": "en",
        "params": {"variant": "plain"},
        "notes": (
            f"third-party {source.shape} re-rendered as {surface}; "
            f"stage-2 at ingest: {stage2}"
        ),
    }
    parsed = record_from_mapping(record)
    for name, rule in LINT_RULES.items():
        if not rule(parsed):
            return f"lint_{name}"
    return record


def ingest(
    source: Source,
    strata: Sequence[tuple[Sequence[Row], int]],
    *,
    revision: str,
    out: Path,
    report: Report,
) -> Report:
    """Render each stratum's rows in order until its quota; append to ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    highest, names = _existing(out)
    if source.name in names:
        raise IngestError("already_ingested")
    by_category: dict[str, list[dict[str, object]]] = {}
    number = highest
    for rows, quota in strata:
        taken = 0
        for row in rows:
            if taken >= quota:
                break
            record_id = f"atk-{number + 1:04d}"
            built = build_record(row, source, revision, record_id, number + 1)
            if isinstance(built, str):
                report.skipped[built] += 1
                continue
            number += 1
            taken += 1
            category = cast(str, built["category"])
            by_category.setdefault(category, []).append(built)
            report.ids.append(record_id)
            report.categories[category] += 1
    for category, records in sorted(by_category.items()):
        with (out / f"{category}.jsonl").open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    report.written = len(report.ids)
    return report


def build_parser(
    prog: str, description: str, default_limit: int
) -> argparse.ArgumentParser:
    """The CLI every sampler shares. There is deliberately no token argument."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Local download, outside the repository "
        "(default location: $FORAGE_CORPUS_INPUTS, ~/.cache/forage-corpus-inputs/)",
    )
    parser.add_argument(
        "--revision",
        required=True,
        help="Pinned upstream commit SHA or dataset revision the input was fetched at",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Sampling seed")
    parser.add_argument(
        "--limit", type=int, default=default_limit, help="Records to write (the cap)"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="Attack corpus directory"
    )
    parser.add_argument(
        "--input-sha256",
        default=None,
        help="Refuse a file input whose sha256 differs from this recorded value",
    )
    return parser


# Reads the canonical input: (rows, per-row skip counts, rows read).
Reader = Callable[[Path], tuple[list[Row], Counter[str], int]]
# Orders rows under the seed and splits the cap: [(rows, quota), ...].
Sampler = Callable[[list[Row], int, int], list[tuple[Sequence[Row], int]]]


def run(
    source: Source,
    reader: Reader,
    sampler: Sampler,
    argv: Sequence[str] | None,
    *,
    prog: str,
    description: str,
) -> int:
    """Parse, guard, read, sample, write, and print counts. Exit 2 on refusal."""
    args = build_parser(prog, description, source.default_limit).parse_args(argv)
    try:
        path = check_input_path(cast(Path, args.input))
        if path.is_file():
            check_sha256(path, cast("str | None", args.input_sha256))
        rows, skipped, rows_read = reader(path)
        report = Report(rows_read=rows_read, skipped=skipped)
        seed = cast(int, args.seed)
        limit = cast(int, args.limit)
        ingest(
            source,
            sampler(rows, seed, limit),
            revision=cast(str, args.revision),
            out=cast(Path, args.out),
            report=report,
        )
    except Exception as exc:  # every failure leaves as a closed reason code
        print(f"{source.name}: refused ({failure_reason(exc)})", file=sys.stderr)
        return 2
    for line in report.lines(source.name, cast(str, args.revision)):
        print(line)
    return 0
