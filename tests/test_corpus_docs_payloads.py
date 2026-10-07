"""No tracked Markdown file quotes an attack record's payload.

``feature-structural-closeout`` US-002. The docs name corpus findings by record
id and technique class only; attack payload text lives in ``tests/corpus/``
and nowhere else. This check makes that mechanical: for every attack record,
every 24-character window of every payload value (whitespace collapsed to
single spaces) must be absent from every tracked ``*.md`` file (normalised the
same way). A failure names the record id and the file path, never the text.

Two refinements keep the check honest rather than noisy:

* a window that also occurs in any **benign** record is page boilerplate shared
  with ordinary text, not attack content, and is ignored;
* (record id, path) pairs that predate this check are frozen in
  ``tests/corpus/payload_quote_allowlist.json`` (ids and paths only). The list
  may only shrink: a new pair fails, and a listed pair that no longer occurs
  fails too, so scrubbing a doc must delete its entry. Scrubbing the frozen
  quotes is audit finding 2026-10-07-001.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands or call arguments (which
could carry record text). ``tests/test_corpus_lint.py`` requires this marker
in every corpus test module.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.corpus.records import load_corpus

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WINDOW = 24
_ALLOWLIST = _REPO_ROOT / "tests" / "corpus" / "payload_quote_allowlist.json"
"""Shortest quoted run that counts as a leak."""


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _windows(text: str) -> set[str]:
    return {text[i : i + _WINDOW] for i in range(len(text) - _WINDOW + 1)}


def _tracked_markdown() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return sorted(path for path in listed.split("\0") if path)


def _attack_windows() -> dict[str, set[str]]:
    """Each attack-only window, mapped to the ids of the records it came from."""
    owners: dict[str, set[str]] = {}
    benign: set[str] = set()
    records = load_corpus()
    attacks = [record for record in records if record.kind == "attack"]
    assert attacks, "no attack records loaded"
    for record in records:
        if record.kind == "attack":
            continue
        for value in record.payload.values():
            benign |= _windows(_collapse(value))
    for record in attacks:
        for value in record.payload.values():
            for window in _windows(_collapse(value)) - benign:
                owners.setdefault(window, set()).add(record.id)
    return owners


def _leaks() -> set[tuple[str, str]]:
    owners = _attack_windows()
    paths = _tracked_markdown()
    assert paths, "git ls-files found no tracked Markdown"
    leaks: set[tuple[str, str]] = set()
    for path in paths:
        file = _REPO_ROOT / path
        if not file.is_file():
            continue
        doc = _collapse(file.read_text(encoding="utf-8", errors="replace"))
        for i in range(len(doc) - _WINDOW + 1):
            ids = owners.get(doc[i : i + _WINDOW])
            if ids is not None:
                leaks.update((record_id, path) for record_id in ids)
    return leaks


def _allowlist() -> set[tuple[str, str]]:
    raw = json.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    pairs = {(str(entry[0]), str(entry[1])) for entry in raw["pairs"]}
    assert all(i.startswith("atk-") and p.endswith(".md") for i, p in pairs), (
        "allowlist holds record ids and Markdown paths only"
    )
    return pairs


def test_no_new_tracked_markdown_quotes_an_attack_payload() -> None:
    new = _leaks() - _allowlist()
    assert not new, "attack payload text in tracked Markdown: " + ", ".join(
        f"{record_id} in {path}" for record_id, path in sorted(new)
    )


def test_the_frozen_allowlist_only_shrinks() -> None:
    stale = _allowlist() - _leaks()
    assert not stale, "scrubbed; delete from the allowlist: " + ", ".join(
        f"{record_id} in {path}" for record_id, path in sorted(stale)
    )


def test_the_check_sees_a_quoted_window() -> None:
    # The detector is not vacuous: one window lifted from a real record is
    # found again once whitespace is re-flowed. The text never leaves memory.
    owners = _attack_windows()
    window = min(owners)
    reflowed = "prefix\n" + window.replace(" ", "\n  ") + "\nsuffix"
    doc = _collapse(reflowed)
    found = any(doc[i : i + _WINDOW] in owners for i in range(len(doc) - _WINDOW + 1))
    assert found, "a quoted window was not detected"
