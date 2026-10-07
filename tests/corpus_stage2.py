"""Which stage-2 regex a corpus record trips — by running the patterns.

Tests-side on purpose: ``_PATTERNS`` and the search-form helpers are private,
and ``tests/`` is the one pyright environment with ``reportPrivateUsage``
relaxed. ``scripts/`` never imports this module.

Entry point (spec 3 US-001)::

    uv run python -m tests.corpus_stage2 name-variants <file.jsonl>

fills ``params.variant`` / ``pinned`` / ``pinned_reason`` for benign records
that trip a stage-2 regex and carry no variant yet, printing ids and regex
names only — never record text.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from pipeline import orchestrator
from pipeline.stage1_extraction import extract_html
from pipeline.stage1_upload import extract_upload_text
from pipeline.stage2_structural import (
    _PATTERNS,
    fold_scan_forms,
    structural_scan_forms,
)
from scripts.corpus.records import (
    CorpusRecord,
    page_document,
    record_from_mapping,
    record_to_mapping,
)
from scripts.corpus.vocab import RECORD_KEYS, STAGE2_REGEX_NAMES
from url_validator import hostname_matches

_MARKUP_NAMES = frozenset({"system_tag", "envelope_breakout", "private_ip_href"})


def stage2_hits(text: str) -> frozenset[str]:
    """The names of every stage-2 pattern that matches ``text``."""
    return frozenset(
        name
        for name, (_, pattern) in zip(STAGE2_REGEX_NAMES, _PATTERNS, strict=True)
        if pattern.search(text)
    )


def stage2_forms(
    record: CorpusRecord, *, blocklist: Sequence[str] = ()
) -> tuple[str, ...]:
    """The exact strings stage 2 receives for ``record`` on its route.

    ``search``: ``()`` when the URL is omitted by the rule chain or the
    effective blocklist (``seed_blocklist`` + ``blocked_domains``) — the result
    ``continue``s before the stage-2 loop — else the loop's forms in order (each text
    field's fold and inline-joined forms follow its wire form).
    ``page``: the builder's forms of ``raw_text``, then of the inline-joined text.
    ``text``: the builder's forms (``structural_scan_forms``), as-is first.
    """
    payload = record.payload
    if record.surface == "search":
        title, title_scan, title_inline = orchestrator._scan_forms_for_search_text(
            payload.get("title", ""),
            max_length=orchestrator._MAX_SEARCH_TITLE_LENGTH,
        )
        outcome = orchestrator._canonicalize_search_url(payload.get("url", ""))
        if outcome.domain is not None and any(
            hostname_matches(outcome.domain, entry, allow_suffix=True)
            for entry in blocklist
        ):
            return ()
        if outcome.omission_reason is not None or outcome.domain is None:
            return ()
        snippet, snippet_scan, snippet_inline = (
            orchestrator._scan_forms_for_search_text(
                payload.get("content", ""),
                max_length=orchestrator._MAX_SEARCH_SNIPPET_LENGTH,
            )
        )
        return (
            title_scan,
            title,
            *fold_scan_forms(title_scan).forms,
            *structural_scan_forms(title_inline, html_parsed=True),
            outcome.scan_texts[0],
            outcome.scan_texts[1],
            snippet_scan,
            snippet,
            *fold_scan_forms(snippet_scan).forms,
            *structural_scan_forms(snippet_inline, html_parsed=True),
        )
    if record.surface == "page":
        extraction = extract_html(
            page_document(record),
            payload.get("url"),
            with_inline=True,
            prune_hidden=False,
        )
        return (
            *structural_scan_forms(extraction.raw_text, html_parsed=True),
            *structural_scan_forms(extraction.scan_text_inline or "", html_parsed=True),
        )
    raw = extract_upload_text(payload.get("text", "").encode("utf-8")).raw_text
    return tuple(structural_scan_forms(raw, html_parsed=False))


def stage2_record_hits(
    record: CorpusRecord, *, blocklist: Sequence[str] = ()
) -> frozenset[str]:
    """The union of ``stage2_hits`` over every form stage 2 scans."""
    hits: frozenset[str] = frozenset()
    for form in stage2_forms(record, blocklist=blocklist):
        hits |= stage2_hits(form)
    return hits


def stage2_markup_hits(record: CorpusRecord) -> frozenset[str]:
    """The regex names ``scan_raw_markup`` finds in the raw markup of ``record``.

    ``page``: the whole document; ``search``: each raw provider field as the
    loop's markup entry holds it; ``text`` has no markup route.
    """
    payload = record.payload
    if record.surface == "page":
        markups = [page_document(record)]
    elif record.surface == "search":
        markups = [
            orchestrator._search_markup_entry(
                payload.get("title", ""),
                max_length=orchestrator._MAX_SEARCH_TITLE_LENGTH,
            ),
            orchestrator._search_markup_entry(
                payload.get("content", ""),
                max_length=orchestrator._MAX_SEARCH_SNIPPET_LENGTH,
            ),
        ]
    else:
        return frozenset()
    names = {
        name
        for markup in markups
        for name in stage2_hits(re.sub(r"\s+", " ", markup))
        if name in _MARKUP_NAMES
    }
    return frozenset(names)


def name_variants(path: Path) -> list[tuple[str, str]]:
    """Tag untagged benign records in ``path`` that trip a regex; rewrite the file."""
    named: list[tuple[str, str]] = []
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = record_from_mapping(json.loads(line))
        mapping = record_to_mapping(record)
        hits = stage2_record_hits(record)
        if record.kind == "benign" and hits and "variant" not in record.params:
            name = next(n for n in STAGE2_REGEX_NAMES if n in hits)
            params = dict(record.params)
            params["variant"] = name
            rebuilt: dict[str, object] = {}
            for key in RECORD_KEYS:
                if key == "pinned":
                    rebuilt[key] = ["flagged", "blocked"]
                elif key == "pinned_reason":
                    rebuilt[key] = f"trips stage-2 regex {name}"
                elif key == "params":
                    rebuilt[key] = params
                elif key in mapping:
                    rebuilt[key] = mapping[key]
            mapping = rebuilt
            named.append((record.id, name))
        lines.append(json.dumps(mapping, ensure_ascii=False))
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return named


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2 or argv[0] != "name-variants":
        print("usage: python -m tests.corpus_stage2 name-variants <file.jsonl>")
        return 2
    for record_id, name in name_variants(Path(argv[1])):
        print(f"{record_id}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
