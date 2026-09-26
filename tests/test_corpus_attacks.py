"""The attack corpus (spec 2): what each story authored, and that it says what it is.

US-001 is the structural families — nine categories, ten obfuscation variants,
three surfaces. Every record is data (ruling 8): no assertion here quotes a
payload, and every failure message is a record id, a rule or a closed token.

The measurements are not asserted except where the spec pins them. A record's
outcome under ``fallback=0.0`` is what the corpus exists to *report*; only the
pins — ``plain`` records of the regex families and the three URL shapes — are
checked, and they are read from the records, never from this file. The rest of
what this file asserts is that a record's labels tell the truth: the variant
it names is the transform it carries, the regex it names is one that can fire
on it, and its marker survives to the wire.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from urllib.parse import urlsplit

from pipeline import contract, orchestrator
from pipeline.stage2_structural import _PATTERNS
from scripts.corpus import vocab
from scripts.corpus.drivers import drive_all
from scripts.corpus.records import (
    CorpusRecord,
    lint_corpus,
    load_corpus,
    normalise_for_leak_check,
)
from scripts.corpus.replay import ReplayClassifier
from tests.corpus_stage2 import stage2_forms, stage2_hits, stage2_record_hits

# ---------------------------------------------------------------------------
# US-001: structural families with obfuscation variants
# ---------------------------------------------------------------------------

# This story's records: the ids it authored, from the first free one after the
# seed (`atk-0025`) to its last. An id is never reused, so later specs append
# past `atk-0182` and this range never grows.
_FIRST_ID = 26
_LAST_ID = 182
_STORY_IDS = tuple(f"atk-{number:04d}" for number in range(_FIRST_ID, _LAST_ID + 1))

_FAMILIES = (
    "instruction_override",
    "authority_impersonation",
    "prompt_boundary",
    "encoded_payload",
    "suspicious_url",
    "exfil_beacon",
    "envelope_breakout",
    "line_anchored_role",
    "url_borne_envelope",
)
_BLOCK_FAMILIES = frozenset(
    {
        "instruction_override",
        "authority_impersonation",
        "prompt_boundary",
        "line_anchored_role",
    }
)

# The stage-2 categories a family's patterns live in. `line_anchored_role` is a
# corpus category, not a stage-2 one: its role markers are the
# `authority_impersonation` line-anchored patterns. A URL-borne envelope's BLOCK
# token is an `instruction_override` or `authority_impersonation` pattern.
_STAGE2_CATEGORIES: dict[str, frozenset[str]] = {
    "instruction_override": frozenset({"instruction_override"}),
    "authority_impersonation": frozenset({"authority_impersonation"}),
    "prompt_boundary": frozenset({"prompt_boundary"}),
    "encoded_payload": frozenset({"encoded_payload"}),
    "suspicious_url": frozenset({"suspicious_url"}),
    "exfil_beacon": frozenset({"exfil_beacon"}),
    "envelope_breakout": frozenset({"envelope_breakout"}),
    "line_anchored_role": frozenset({"authority_impersonation"}),
    "url_borne_envelope": frozenset(
        {"envelope_breakout", "instruction_override", "authority_impersonation"}
    ),
}
_STAGE2_CATEGORY_OF: dict[str, str] = {
    name: category
    for name, (category, _) in zip(vocab.STAGE2_REGEX_NAMES, _PATTERNS, strict=True)
}

_DETERMINISTIC = "stage-2 regex, deterministic"
_AUDIT_016 = "2026-09-16-016"
_AUDIT_032 = "2026-09-16-032"
_WIRE_FORM = "stage-2 regex on the collapsed wire form, deterministic"
_RAW_CHARS = "search-url rule raw_chars (invalid_url), not stage 2"

_CYRILLIC = frozenset(chr(code) for code in (0x430, 0x435, 0x43E))  # a / e / o
_ZERO_WIDTH = frozenset(chr(code) for code in (0x200B, 0x200C))
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|lt|gt|amp);")
_SPLIT_TAG = re.compile(r"<(?:b|i|code)>")
_OMIT_REASON_NOTE = re.compile(r"omit_reason ([a-z_]+)")


def _story_records() -> list[CorpusRecord]:
    by_id = {record.id: record for record in load_corpus()}
    missing = [story_id for story_id in _STORY_IDS if story_id not in by_id]
    assert not missing, f"story ids missing from the corpus: {missing}"
    return [by_id[story_id] for story_id in _STORY_IDS]


def _variant(record: CorpusRecord) -> str:
    value = record.params.get("variant")
    assert isinstance(value, str), record.id
    return value


def _regex_name(record: CorpusRecord) -> str:
    head = record.notes.split("; ")[0]
    assert head.startswith("regex: "), record.id
    return head.removeprefix("regex: ")


def _raw_text(record: CorpusRecord) -> str:
    return "\n".join(record.payload.values())


def _fallback() -> ReplayClassifier:
    return ReplayClassifier(
        {}, model_id="replay/structural-only", revision="0" * 40, fallback=0.0
    )


def _expected_pin(record: CorpusRecord) -> tuple[tuple[str, ...] | None, str | None]:
    """The pin the spec's table gives ``record`` (the hypothesis the records hold)."""
    notes = record.notes
    if record.category == "url_borne_envelope":
        if "shape (i):" in notes:
            return ("blocked",), _AUDIT_032
        if "shape (ii):" in notes:
            return ("flagged",), _AUDIT_032
        assert "shape (iii):" in notes, record.id
        return ("blocked",), _RAW_CHARS
    if "break-inside-trigger" in notes:
        if record.surface != "search":
            return None, None
        outcome = "blocked" if record.category in _BLOCK_FAMILIES else "flagged"
        return (outcome,), _WIRE_FORM
    if _variant(record) != "plain" or "tag-consumed" in notes:
        return None, None
    if record.category in _BLOCK_FAMILIES:
        audit = record.category == "line_anchored_role" and record.surface == "search"
        return ("blocked",), _AUDIT_016 if audit else _DETERMINISTIC
    return ("flagged",), _DETERMINISTIC


def _pattern_of(name: str) -> re.Pattern[str]:
    return _PATTERNS[vocab.STAGE2_REGEX_NAMES.index(name)][1]


def _has_a_query(record: CorpusRecord) -> bool:
    return urlsplit(record.payload["url"]).query != ""


def _is_re_cased(record: CorpusRecord) -> bool:
    """A ``case`` record matches its regex only when case is ignored, or re-cases it.

    The regex, forced case-insensitive, must match the payload, and the probe
    literal the vocabulary gives that regex must not appear verbatim.
    """
    name = _regex_name(record)
    pattern = _pattern_of(name)
    forced = re.compile(pattern.pattern, pattern.flags | re.IGNORECASE)
    text = _raw_text(record)
    return (
        forced.search(text) is not None and vocab.STAGE2_REGEX_PROBES[name] not in text
    )


# What each variant's label promises the payload carries.
_VARIANT_SHAPE: dict[str, Callable[[CorpusRecord], bool]] = {
    "plain": lambda record: True,
    "case": _is_re_cased,
    "entity": lambda record: _ENTITY.search(_raw_text(record)) is not None,
    "zwsp": lambda record: any(char in _ZERO_WIDTH for char in _raw_text(record)),
    "split_tags": lambda record: _SPLIT_TAG.search(_raw_text(record)) is not None,
    "confusable": lambda record: any(char in _CYRILLIC for char in _raw_text(record)),
    "second_paragraph": lambda record: (
        "\n" in _raw_text(record) or "</p><p>" in _raw_text(record)
    ),
    "url_query": _has_a_query,
    "url_path": lambda record: urlsplit(record.payload["url"]).path not in ("", "/"),
    "title_field": lambda record: (
        _regex_name(record) in stage2_hits(record.payload["title"])
    ),
}


def test_this_story_authored_at_least_72_records_and_they_are_lint_clean() -> None:
    records = _story_records()
    assert len(records) >= 72
    assert {record.category for record in records} == set(_FAMILIES)
    assert all(record.source["kind"] == "owned" for record in records)
    assert [str(error) for error in lint_corpus(records)] == []


def test_every_family_has_at_least_eight_records_each_with_a_variant_and_a_regex() -> (
    None
):
    records = _story_records()
    counts = Counter(record.category for record in records)
    for family in _FAMILIES:
        assert counts[family] >= 8, family
    for record in records:
        assert _variant(record) in vocab.ATTACK_VARIANTS, record.id
        assert _regex_name(record) in vocab.STAGE2_REGEX_NAMES, record.id


def test_every_variant_of_the_vocabulary_is_authored_somewhere() -> None:
    authored = {_variant(record) for record in _story_records()}
    assert authored == set(vocab.ATTACK_VARIANTS)


def test_each_regex_family_reaches_every_route() -> None:
    """At least 3 ``search``, 3 ``page`` and 2 ``text`` records per regex family."""
    surfaces: dict[str, Counter[str]] = {family: Counter() for family in _FAMILIES}
    for record in _story_records():
        surfaces[record.category][record.surface] += 1
    for family in _FAMILIES:
        if family == "url_borne_envelope":
            continue
        assert surfaces[family]["search"] >= 3, family
        assert surfaces[family]["page"] >= 3, family
        assert surfaces[family]["text"] >= 2, family


def test_url_borne_envelope_is_search_only_and_covers_the_three_shapes() -> None:
    records = [r for r in _story_records() if r.category == "url_borne_envelope"]
    assert len(records) >= 8
    assert {record.surface for record in records} == {"search"}
    shapes = Counter(
        shape
        for record in records
        for shape in ("shape (i):", "shape (ii):", "shape (iii):")
        if shape in record.notes
    )
    assert set(shapes) == {"shape (i):", "shape (ii):", "shape (iii):"}
    assert all(count >= 2 for count in shapes.values())
    for shape in shapes:
        variants = {_variant(record) for record in records if shape in record.notes}
        assert variants == {"url_path", "url_query"}, shape


def test_a_url_borne_envelope_record_names_the_stage_that_answered() -> None:
    """The canonicalisation chain, run directly, agrees with each record's shape."""
    records = [r for r in _story_records() if r.category == "url_borne_envelope"]
    for record in records:
        outcome = orchestrator._canonicalize_search_url(record.payload["url"])
        if "shape (iii):" in record.notes:
            assert outcome.omission_reason == contract.OMIT_INVALID_URL, record.id
            assert outcome.rule == "raw_chars", record.id
            assert "omit_reason invalid_url" in record.notes, record.id
            continue
        assert outcome.omission_reason is None, record.id
        assert stage2_record_hits(record), record.id
        if "shape (i):" in record.notes:
            categories = {
                _STAGE2_CATEGORY_OF[name] for name in stage2_record_hits(record)
            }
            assert categories & {"instruction_override", "authority_impersonation"}
            assert "omit_reason structural_blocked" in record.notes, record.id
        else:
            categories = {
                _STAGE2_CATEGORY_OF[name] for name in stage2_record_hits(record)
            }
            assert categories == {"envelope_breakout"}, record.id


def test_every_records_pin_is_the_one_the_spec_table_gives_it() -> None:
    for record in _story_records():
        pinned, reason = _expected_pin(record)
        assert record.pinned == pinned, record.id
        assert record.pinned_reason == reason, record.id


def test_a_plain_record_is_pinned_unless_the_tag_is_consumed_and_others_are_not() -> (
    None
):
    """The pin table's own shape: what is pinned, what is deliberately not."""
    records = _story_records()
    pinned = [record for record in records if record.pinned is not None]
    unpinned = [record for record in records if record.pinned is None]
    assert pinned and unpinned
    for record in unpinned:
        assert (
            _variant(record) != "plain"
            or "tag-consumed" in record.notes
            or "break-inside-trigger" in record.notes
        ), record.id
    for record in pinned:
        assert record.pinned_reason is not None
        assert record.pinned in (("blocked",), ("flagged",)), record.id


def test_a_tag_consumed_record_carries_the_literal_and_stage_two_never_sees_it() -> (
    None
):
    tagged = [r for r in _story_records() if "tag-consumed" in r.notes]
    assert len(tagged) >= 4
    assert {record.surface for record in tagged} == {"page", "search"}
    for record in tagged:
        name = _regex_name(record)
        assert _variant(record) == "plain", record.id
        assert record.pinned is None, record.id
        assert name in stage2_hits(_raw_text(record)), record.id
        assert name not in stage2_record_hits(record), record.id


def test_a_pinned_record_fires_the_regex_it_names() -> None:
    for record in _story_records():
        if record.pinned is None or "shape (iii):" in record.notes:
            continue
        assert _regex_name(record) in stage2_record_hits(record), record.id


def test_a_record_trips_no_stage_two_category_outside_its_own_family() -> None:
    for record in _story_records():
        categories = {_STAGE2_CATEGORY_OF[name] for name in stage2_record_hits(record)}
        assert categories <= _STAGE2_CATEGORIES[record.category], record.id


def test_a_record_carries_the_transform_its_variant_names() -> None:
    for record in _story_records():
        assert _VARIANT_SHAPE[_variant(record)](record), (record.id, _variant(record))


def test_the_break_inside_records_break_the_two_patterns_without_dotall() -> None:
    """Both non-``DOTALL`` patterns get a record per route with the break inside.

    Recorded, not asserted, is what each route makes of them: that is the
    measurement (the Implementation Notes carry it). This only checks the
    records are what they say — the break falls inside the named trigger.
    """
    broken = [r for r in _story_records() if "break-inside-trigger" in r.notes]
    assert {(_regex_name(record), record.surface) for record in broken} == {
        (name, surface)
        for name in ("disregard_instructions", "exfil_image")
        for surface in vocab.SURFACES
    }
    for record in broken:
        assert _variant(record) == "second_paragraph", record.id
        pattern = _pattern_of(_regex_name(record))
        assert not pattern.flags & re.DOTALL, record.id
        dotall = re.compile(pattern.pattern, pattern.flags | re.DOTALL)
        match = dotall.search(_raw_text(record))
        assert match is not None and "\n" in match.group(), record.id


def test_every_marker_survives_the_variant_it_sits_beside() -> None:
    """The marker is on a stretch of the payload the obfuscation does not touch.

    Checked against the strings the pipeline actually hands stage 2 — the same
    normalisation the leak check applies — plus the raw URL, because a record
    whose URL is refused before stage 2 (and one whose marker *is* the URL)
    has no scan form to read it from.
    """
    for record in _story_records():
        assert record.marker is not None, record.id
        needle = normalise_for_leak_check(record.marker)
        candidates = list(stage2_forms(record))
        if record.surface == "search":
            candidates.append(record.payload["url"])
        assert any(needle in normalise_for_leak_check(c) for c in candidates), record.id


async def test_every_pin_this_story_sets_holds_on_the_real_routes() -> None:
    """The generic pinned-outcome test, over this story's records, both configs."""
    records = _story_records()
    by_id = {record.id: record for record in records}
    results = await drive_all(records, _fallback(), configs=["default", "contiguity"])
    assert len(results) == 2 * len(records)
    pinned = 0
    for result in results:
        record = by_id[result.record_id]
        if record.pinned is None:
            continue
        pinned += 1
        assert result.outcome in record.pinned, (
            f"{result.summary()} pinned={list(record.pinned)}"
        )
    assert pinned == 2 * sum(record.pinned is not None for record in records)


async def test_a_url_borne_envelope_note_names_what_the_wire_answered() -> None:
    """``notes`` records the omission reason or verdict, so spec 5 can attribute it."""
    records = [r for r in _story_records() if r.category == "url_borne_envelope"]
    results = await drive_all(records, _fallback(), configs=["default"])
    for record, result in zip(records, results, strict=True):
        found = _OMIT_REASON_NOTE.search(record.notes)
        if "shape (ii):" in record.notes:
            # Cleared canonicalisation and was served, flagged.
            assert found is None, record.id
            assert result.signals.omit_reason is None, result.summary()
            assert result.signals.suspicious is True, result.summary()
        else:
            assert found is not None, record.id
            assert result.signals.omit_reason == found.group(1), result.summary()


def test_the_readme_defines_every_attack_variant() -> None:
    readme = (vocab.TESTS_CORPUS_ROOT / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    for variant in vocab.ATTACK_VARIANTS:
        rows = [line for line in lines if line.startswith(f"| `{variant}` |")]
        assert len(rows) == 1, variant
        assert len(rows[0].split("|")[2].strip()) > 20, variant
