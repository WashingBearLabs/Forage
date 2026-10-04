"""The corpus documentation: nine sections, honest SECURITY.md, filed findings.

Pins what ``docs/corpus.md``, the README paragraph, ``kit_tools/arch/SECURITY.md``
and ``kit_tools/AUDIT_FINDINGS.md`` must say, so the "injection defence is
measured" claim cannot quietly drift from the corpus it describes. Findings are
checked against the committed ``tests/corpus/baseline.json``, by id only.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands. ``tests/test_corpus_lint.py``
requires this marker in every corpus test module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_DOC = _ROOT / "docs" / "corpus.md"
_MODEL_22M = "meta-llama/Llama-Prompt-Guard-2-22M"
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

_SECTIONS = (
    "What is measured and why",
    "The record format",
    "Adding a record",
    "Recording and re-recording",
    "The gate",
    "Sources and licences",
    "Decision inputs",
    "Reading the results",
    "Consumers",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(document: str, title: str) -> str:
    match = re.search(
        rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", document, re.S | re.M
    )
    assert match is not None, title
    return match.group(1)


def _baseline_rows(key: str) -> list[dict[str, object]]:
    baseline = json.loads(_text(_ROOT / "tests" / "corpus" / "baseline.json"))
    return cast(list[dict[str, object]], baseline[key])


def test_corpus_guide_has_all_nine_sections_in_order() -> None:
    headings = re.findall(r"^## (.+)$", _text(_CORPUS_DOC), re.M)
    assert headings == list(_SECTIONS)


def test_reading_the_results_carries_the_disclosure_reasoning() -> None:
    section = _section(_text(_CORPUS_DOC), "Reading the results")
    for needle in (
        "standard defensive practice",
        "not a trust boundary",
        "gate nobody can check",
        "rejected / examined",
        "not comparable across routes",
        "half-worked defence",
        "reached stage 3",
    ):
        assert needle in section, needle


def test_numbers_appear_only_in_the_decision_inputs_table() -> None:
    document = _text(_CORPUS_DOC)
    inputs = _section(document, "Decision inputs")
    assert "`4f13c52`" in inputs
    outside = document.replace(inputs, "")
    assert re.findall(r"\b\d+\.\d{2,}\b", outside) == []


def test_rejected_sources_are_listed_with_a_reason() -> None:
    section = _section(_text(_CORPUS_DOC), "Sources and licences")
    for source in (
        "BIPIA",
        "WASP",
        "HackAPrompt",
        "PIGuard",
        "Wikipedia",
        "Stack Exchange",
        "MDN",
        "OWASP",
        "Reddit",
        "Hacker News",
    ):
        rows = [line for line in section.splitlines() if line.startswith(f"| {source}")]
        assert len(rows) == 1, source
        assert len(rows[0].split("|")[2].strip()) > 0, source


def test_every_relative_link_in_the_corpus_guide_resolves() -> None:
    broken: list[str] = []
    for target in _MARKDOWN_LINK_RE.findall(_text(_CORPUS_DOC)):
        link = target.split("#", 1)[0]
        if not link or link.startswith(("http://", "https://", "mailto:")):
            continue
        if not (_CORPUS_DOC.parent / link).resolve().exists():
            broken.append(link)
    assert broken == [], f"Broken relative links: {broken}"


def test_readme_has_the_measured_injection_defence_paragraph() -> None:
    readme = _text(_ROOT / "README.md")
    section = readme.split("### Measured injection defence", 1)[1].split(
        "### HTTP surface", 1
    )[0]
    assert "docs/corpus.md" in section
    assert "tests/corpus/baseline.json" in section
    assert re.findall(r"\b\d+\.\d+\b", section) == []


def test_security_md_states_the_corpus_and_keeps_the_two_gaps() -> None:
    security = _text(_ROOT / "kit_tools" / "arch" / "SECURITY.md")
    assert "No fuzz harness or adversarial corpus exists" not in security
    observations = security.split("### Observations", 1)[1].split("\n---", 1)[0]
    assert "adversarial corpus now exists" in observations
    assert "docs/corpus.md" in observations
    assert "no fuzz harness" in observations
    assert "PDF-borne text is outside the corpus" in observations
    assert '**Fuzzing.** None found (see "Security Testing")' in security
    testing = security.split("## Security Testing", 1)[1].split("### Observations", 1)[
        0
    ]
    row = next(
        line
        for line in testing.splitlines()
        if line.startswith("| Injection signalling")
    )
    assert "tests/test_corpus_*.py" in row
    assert "### The injection corpus gate" in testing
    assert "cassettes" in testing


def _findings_text() -> str:
    # AUDIT_FINDINGS.md is gitignored (a local working record), so a clean
    # checkout has none; the findings checks run wherever the owner keeps it.
    path = _ROOT / "kit_tools" / "AUDIT_FINDINGS.md"
    if not path.exists():
        pytest.skip("kit_tools/AUDIT_FINDINGS.md is gitignored and absent here")
    return _text(path)


def _finding_entries() -> list[str]:
    return re.findall(r"^\*\*2026-10-04-\d{3}\*\* — (.+)$", _findings_text(), re.M)


def test_every_leaked_category_and_route_is_filed() -> None:
    entries = _finding_entries()
    missing = [
        f"{row['category']} {row['route']}"
        for row in _baseline_rows("attacks")
        if row["model"] == _MODEL_22M
        and row["config"] == "default"
        and cast(int, row["leaked"]) > 0
        and not any(
            f"`{row['category']}` on `{row['route']}`:" in entry and "leaked" in entry
            for entry in entries
        )
    ]
    assert missing == []


def test_every_nonzero_fpr_genre_and_route_is_filed() -> None:
    entries = _finding_entries()
    rows = [
        row
        for row in _baseline_rows("benign")
        if row["model"] == _MODEL_22M
        and row["config"] == "default"
        and cast(float, row["fpr"]) > 0
    ]
    assert rows != []
    missing = [
        f"{row['genre']} {row['route']}"
        for row in rows
        if not any(
            f"`{row['genre']}` on `{row['route']}`: FPR" in entry for entry in entries
        )
    ]
    assert missing == []


def test_every_blocked_but_leaked_record_is_filed() -> None:
    baseline = json.loads(_text(_ROOT / "tests" / "corpus" / "baseline.json"))
    records = cast(
        dict[str, dict[str, dict[str, dict[str, list[object]]]]], baseline["records"]
    )
    findings = _findings_text()
    ids = sorted(
        record_id
        for record_id, routes in records.items()
        for configs in routes.values()
        for models in configs.values()
        for outcome in models.values()
        if outcome[0] == "blocked" and outcome[1] is True
    )
    assert ids != []
    missing = [
        record_id
        for record_id in set(ids)
        if not re.search(rf"Blocked-but-leaked:.*`{record_id}`", findings)
    ]
    assert missing == []


def test_every_leaked_hidden_markup_carrier_is_filed() -> None:
    entries = [
        entry
        for entry in _finding_entries()
        if entry.startswith("`hidden_markup` carrier")
    ]
    carriers = {re.search(r"carrier `(\w+)`", entry) for entry in entries}
    assert {match.group(1) for match in carriers if match} == {
        "css_offscreen",
        "hidden_div",
        "title_stuffing",
    }
