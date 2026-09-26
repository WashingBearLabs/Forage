"""LLMail-Inject sampler (MIT; ``microsoft/llmail-inject-challenge``) — spec 2 US-004.

``--input`` is one JSONL shard of the dataset, fetched on the host with
``huggingface_hub.hf_hub_download`` into ``$FORAGE_CORPUS_INPUTS`` — never into
the repository; the set is ~462 k attacker-authored e-mails and is never
vendored. The sample is **stratified** on the row's ``defense.undetected``
objective: half the cap from e-mails the challenge's defence caught, half from
ones it missed. Each e-mail (subject and body) is re-rendered as a forum post.

    uv run python -m scripts.corpus.ingest.llmail_inject \\
        --input ~/.cache/forage-corpus-inputs/llmail-inject/data/<shard>.jsonl \\
        --revision <dataset-revision>
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

from scripts.corpus.ingest import render
from scripts.corpus.ingest.common import IngestError, Row, Source, run, shuffled

CAUGHT: Final[str] = "caught"
MISSED: Final[str] = "missed"


def _stratum(obj: dict[str, object]) -> str | None:
    raw = obj.get("objectives")
    if isinstance(raw, str):
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        decoded = raw
    if not isinstance(decoded, dict):
        return None
    undetected = cast(dict[str, object], decoded).get("defense.undetected")
    if not isinstance(undetected, bool):
        return None
    return MISSED if undetected else CAUGHT


def read_rows(path: Path) -> tuple[list[Row], Counter[str], int]:
    """One row per distinct e-mail; the stratum is carried in the row ref."""
    if not path.is_file():
        raise IngestError("input_malformed")
    skipped: Counter[str] = Counter()
    rows: list[Row] = []
    seen: set[str] = set()
    read = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            read += 1
            try:
                decoded: object = json.loads(line)
            except json.JSONDecodeError:
                skipped["malformed_row"] += 1
                continue
            if not isinstance(decoded, dict):
                skipped["malformed_row"] += 1
                continue
            obj = cast(dict[str, object], decoded)
            body, subject, key = obj.get("body"), obj.get("subject"), obj.get("RowKey")
            stratum = _stratum(obj)
            if not isinstance(body, str) or not isinstance(key, str) or stratum is None:
                skipped["malformed_row"] += 1
                continue
            subject_text = subject if isinstance(subject, str) else ""
            identity = f"{subject_text}\n{body}".strip()
            if identity in seen:
                skipped["duplicate"] += 1
                continue
            seen.add(identity)
            rows.append(
                Row(ref=f"{stratum} RowKey {key}", text=body, subject=subject_text)
            )
    return rows, skipped, read


def sample(rows: list[Row], seed: int, limit: int) -> list[tuple[Sequence[Row], int]]:
    """Half the cap from caught e-mails, half from missed, each in seed order."""
    caught = [row for row in rows if row.ref.startswith(f"{CAUGHT} ")]
    missed = [row for row in rows if row.ref.startswith(f"{MISSED} ")]
    half = limit // 2
    return [(shuffled(caught, seed), half), (shuffled(missed, seed), limit - half)]


def _render(
    row: Row, text: str, subject: str, number: int
) -> tuple[str, dict[str, str]]:
    del row
    url = f"https://forum.example.com/t/llmail-{number}"
    return "page", render.forum_post(url, subject, text)


SOURCE: Final[Source] = Source(
    name="LLMail-Inject",
    url="https://huggingface.co/datasets/microsoft/llmail-inject-challenge",
    licence="MIT",
    framing="indirect",
    shape="e-mail",
    default_limit=60,
    render=_render,
)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    return run(
        SOURCE,
        read_rows,
        sample,
        argv,
        prog="python -m scripts.corpus.ingest.llmail_inject",
        description=(
            "Sample an LLMail-Inject shard, stratified by defence outcome, into "
            "re-rendered forum-post records (ids and counts only are printed)."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
