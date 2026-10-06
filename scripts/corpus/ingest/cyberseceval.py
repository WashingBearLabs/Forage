"""CyberSecEval sampler (MIT at ``CybersecurityBenchmarks/LICENSE``) — spec 2 US-004.

Only files under ``CybersecurityBenchmarks/`` of ``meta-llama/PurpleLlama`` are
taken: that directory's LICENSE is MIT, although the repository root is the
Llama 3.2 Community License. ``--input`` is
``CybersecurityBenchmarks/datasets/prompt_injection/prompt_injection.json`` at
the pinned commit. Only ``injection_type == "indirect"`` cases are sampled
(direct, user-turn cases are excluded and counted); each case's submitted
content is re-rendered as a ``text`` upload.

    uv run python -m scripts.corpus.ingest.cyberseceval \\
        --input ~/.cache/forage-corpus-inputs/cyberseceval/prompt_injection.json \\
        --revision <sha>
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

from scripts.corpus.ingest import render
from scripts.corpus.ingest.common import IngestError, Row, Source, run, shuffled


def read_rows(path: Path) -> tuple[list[Row], Counter[str], int]:
    """One row per indirect case; direct cases are counted and set aside."""
    decoded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, list):
        raise IngestError("input_malformed")
    skipped: Counter[str] = Counter()
    rows: list[Row] = []
    items = cast(list[object], decoded)
    for item in items:
        if not isinstance(item, dict):
            skipped["malformed_row"] += 1
            continue
        obj = cast(dict[str, object], item)
        prompt_id, text = obj.get("prompt_id"), obj.get("user_input")
        if not isinstance(prompt_id, int) or not isinstance(text, str):
            skipped["malformed_row"] += 1
            continue
        if obj.get("injection_type") != "indirect":
            skipped["direct_excluded"] += 1
            continue
        rows.append(Row(ref=f"prompt_id {prompt_id:04d}", text=text))
    return rows, skipped, len(items)


def _render(
    row: Row, text: str, subject: str, number: int
) -> tuple[str, dict[str, str]]:
    del row, subject
    return "text", render.text_upload(f"submitted-content-{number}.txt", text)


def sample(rows: list[Row], seed: int, limit: int) -> list[tuple[Sequence[Row], int]]:
    """One stratum: every indirect case in a seed-fixed order, capped at ``limit``."""
    return [(shuffled(rows, seed), limit)]


SOURCE: Final[Source] = Source(
    name="CyberSecEval",
    url="https://github.com/meta-llama/PurpleLlama",
    licence="MIT",
    framing="indirect",
    shape="submitted content",
    default_limit=30,
    render=_render,
)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    return run(
        SOURCE,
        read_rows,
        sample,
        argv,
        prog="python -m scripts.corpus.ingest.cyberseceval",
        description=(
            "Sample CyberSecEval's indirect prompt-injection cases into "
            "re-rendered text records (ids and counts only are printed)."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
