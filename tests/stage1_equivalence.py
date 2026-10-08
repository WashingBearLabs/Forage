"""Digest helpers for the stage-1 equivalence pin (``release-resource-bounds`` US-001).

The frozen file stores one SHA-256 per (page, combination) so no corpus text is
committed. Synthetic fixtures cover the paths the corpus may not: a hidden
element, and a page where trafilatura returns ``None`` so the pruned fallback
runs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from scripts.corpus.records import load_corpus, page_document

COMBINATIONS: tuple[tuple[bool, bool], ...] = (
    (False, False),
    (False, True),
    (True, False),
    (True, True),
)

_VISIBLE = "Visible sentence for the reader."

SYNTHETIC_PAGES: dict[str, str] = {
    "hidden_element": (
        f"<html><head><title>T</title></head><body><article><p>{_VISIBLE}</p>"
        "<div hidden>HIDDENTEXT</div><p>After.</p></article></body></html>"
    ),
    "hidden_style_nested": (
        f"<html><body><p>{_VISIBLE}</p>"
        '<div style="display:none"><span>HIDDENTEXT</span></div>'
        '<p style="visibility:hidden">HIDDENTEXT<b style="visibility:visible">'
        "shown</b></p></body></html>"
    ),
    "dangerous_and_comments": (
        f"<html><body><p>{_VISIBLE}</p><script>var a=1;</script>"
        "<!-- a comment --><style>p{}</style><p>tail</p></body></html>"
    ),
    "pruned_fallback": (
        "<html><body><div hidden>HIDDENTEXT</div><script>x</script>"
        "<!-- c --><span>one</span><span>two</span></body></html>"
    ),
    "no_body": "<p>bare fragment</p>",
    "inline_split": "<html><body><p>ig<b></b>nore pre<i>vious</i></p></body></html>",
}


def corpus_pages() -> dict[str, str]:
    return {
        record.id: page_document(record)
        for record in load_corpus()
        if record.surface == "page"
    }


def digest(extract: Callable[..., Any], html: str, inline: bool, prune: bool) -> str:
    result = extract(html, with_inline=inline, prune_hidden=prune)
    blob = json.dumps(dataclasses.asdict(result), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def digests(extract: Callable[..., Any]) -> dict[str, str]:
    pages = {**SYNTHETIC_PAGES, **corpus_pages()}
    found = {
        f"{name}|inline={inline}|prune={prune}": digest(extract, html, inline, prune)
        for name, html in pages.items()
        for inline, prune in COMBINATIONS
    }
    # trafilatura returns None: the pruned (or raw) fallback path runs.
    module = sys.modules[extract.__module__]
    with patch.object(module, "_extract_main_content", return_value=None):
        for name, html in SYNTHETIC_PAGES.items():
            for inline, prune in COMBINATIONS:
                key = f"{name}|inline={inline}|prune={prune}|fallback"
                found[key] = digest(extract, html, inline, prune)
    return found
