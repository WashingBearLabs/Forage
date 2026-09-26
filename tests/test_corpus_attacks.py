"""The attack corpus (spec 2): what each story authored, and that it says what it is.

US-001 is the structural families — nine categories, ten obfuscation variants,
three surfaces. US-002 is the hidden-markup carriers — eight placements, each
carrying the three phrasing shapes. US-005 is the two classifier-only categories,
`natural_language` and `authority_seo`, authored in several languages. Every
record is data (ruling 8): no
assertion here quotes a payload, and every failure message is a record id, a
rule or a closed token.

The measurements are not asserted except where the spec pins them. A record's
outcome under ``fallback=0.0`` is what the corpus exists to *report*; only the
pins — ``plain`` records of the regex families and the three URL shapes — are
checked, and they are read from the records, never from this file. The rest of
what this file asserts is that a record's labels tell the truth: the variant
it names is the transform it carries, the regex it names is one that can fire
on it, and its marker survives to the wire.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import cast
from urllib.parse import urlsplit

from pipeline import contract, orchestrator
from pipeline.stage1_extraction import extract_html
from pipeline.stage1_upload import extract_upload_text
from pipeline.stage2_structural import _PATTERNS, scan_structural
from scripts.corpus import vocab
from scripts.corpus.drivers import drive_all
from scripts.corpus.records import (
    CorpusRecord,
    lint_corpus,
    load_corpus,
    normalise_for_leak_check,
    page_document,
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


# ---------------------------------------------------------------------------
# US-002: hidden-markup carriers — the eight in-the-wild placements
# ---------------------------------------------------------------------------

# This story's records: `atk-0183` … `atk-0214`, four per carrier. The seed's six
# (`atk-0014` … `atk-0019`) sit in the same category and are checked alongside.
_HIDDEN_FIRST_ID = 183
_HIDDEN_LAST_ID = 214
_HIDDEN_STORY_IDS = tuple(
    f"atk-{number:04d}" for number in range(_HIDDEN_FIRST_ID, _HIDDEN_LAST_ID + 1)
)

# What a record's payload says: the three phrasing shapes (spec hint), named at
# the head of its `notes` so the report can split a carrier by phrasing.
_PHRASINGS = ("instruction_override", "authority_seo", "natural_language")
_PHRASING_NOTE = re.compile(r"phrasing: ([a-z_]+)(?:;|$)")
_REGEX_NOTE = re.compile(r"; regex: ([a-z_]+)(?:;|$)")

# Measured at story start, `fallback=0.0`, both rule configs (2026-09-25): every
# phrasing of these carriers came back `neutralised` on `/retrieve`. Stage 1
# removes them — `script`, `meta`, comments — or never reads them (attribute
# values are not text). Only these carry pins; the other three keep their text
# and are recorded, not asserted.
_STRIPPED_CARRIERS = frozenset(
    {"jsonld", "meta_description", "og_description", "html_comment", "alt_text"}
)
_CARRIER_PIN = {"blocked", "flagged", "neutralised"}

# Keys of a JSON-LD object the carrier may sit in (landscape finding 11).
_JSONLD_CARRIER_KEYS = frozenset(
    {"offers", "publisher", "applicationCategory", "description"}
)
_JSONLD_TYPES = frozenset({"SoftwareApplication", "Organization"})
_JSONLD_SCRIPT = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)


def _hidden_records() -> list[CorpusRecord]:
    return [r for r in load_corpus() if r.category == "hidden_markup"]


def _hidden_story_records() -> list[CorpusRecord]:
    by_id = {record.id: record for record in _hidden_records()}
    missing = [story_id for story_id in _HIDDEN_STORY_IDS if story_id not in by_id]
    assert not missing, f"story ids missing from the corpus: {missing}"
    return [by_id[story_id] for story_id in _HIDDEN_STORY_IDS]


def _carrier(record: CorpusRecord) -> str:
    value = record.params.get("carrier")
    assert isinstance(value, str), record.id
    return value


def _phrasing(record: CorpusRecord) -> str:
    found = _PHRASING_NOTE.match(record.notes)
    assert found is not None, record.id
    return found.group(1)


def _marker(record: CorpusRecord) -> str:
    assert record.marker is not None, record.id
    return record.marker


def _json_strings(
    node: object, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Every string in a decoded JSON document with the key path that reaches it."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in cast(dict[str, object], node).items():
            yield from _json_strings(value, (*path, key))
    elif isinstance(node, list):
        for item in cast(list[object], node):
            yield from _json_strings(item, path)


def _sits_in_jsonld_carrier(record: CorpusRecord) -> bool:
    """The marker is a string under a carrier key of a typed JSON-LD object."""
    marker = _marker(record)
    for script in _JSONLD_SCRIPT.findall(record.payload["head_html"]):
        document: object = json.loads(script)
        if not isinstance(document, dict):
            continue
        typed = cast(dict[str, object], document)
        if typed.get("@type") not in _JSONLD_TYPES:
            continue
        for path, value in _json_strings(typed):
            if marker in value:
                return bool(set(path) & _JSONLD_CARRIER_KEYS)
    return False


def _placement_pattern(carrier: str, marker: str) -> re.Pattern[str]:
    """The markup construct the marker must sit inside for ``carrier`` (raw HTML)."""
    inside = re.escape(marker)
    flags = re.DOTALL | re.IGNORECASE
    patterns = {
        "meta_description": rf'<meta\s+name="description"\s+content="[^"]*{inside}',
        "og_description": (
            rf'<meta\s+property="og:description"\s+content="[^"]*{inside}'
        ),
        "css_offscreen": (
            r'<\w+\s+style="[^"]*(?:left:-9999px|font-size:0)[^"]*"[^>]*>'
            rf"[^<]*{inside}"
        ),
        "hidden_div": (
            r'<\w+\s+(?:hidden\b[^>]*|style="[^"]*display:none[^"]*"[^>]*)>'
            rf"(?:<p>)?[^<]*{inside}"
        ),
        "html_comment": rf"<!--(?:(?!-->).)*{inside}(?:(?!-->).)*-->",
        "alt_text": rf'<img\b[^>]*\balt="[^"]*{inside}',
    }
    return re.compile(patterns[carrier], flags)


def _sits_in_placement(record: CorpusRecord) -> bool:
    carrier = _carrier(record)
    payload = record.payload
    marker = _marker(record)
    if carrier == "jsonld":
        return _sits_in_jsonld_carrier(record)
    if carrier == "title_stuffing":
        words = payload["title"].lower().split()
        return marker in payload["title"] and max(Counter(words).values()) >= 3
    pattern = _placement_pattern(carrier, marker)
    haystack = payload["head_html"] + payload["body_html"]
    if carrier in {"meta_description", "og_description"}:
        haystack = payload["head_html"]
    return pattern.search(haystack) is not None


def _raw_payload_text(record: CorpusRecord) -> str:
    return html.unescape("\n".join(record.payload.values()))


def test_there_are_at_least_32_hidden_markup_records_four_per_carrier_all_page() -> (
    None
):
    records = _hidden_records()
    assert len(records) >= 32
    assert {record.surface for record in records} == {"page"}
    assert {_carrier(record) for record in records} == set(vocab.ATTACK_CARRIERS)
    story = _hidden_story_records()
    assert len(story) == 32
    assert [str(error) for error in lint_corpus(records)] == []
    assert all(record.source["kind"] == "owned" for record in story)
    for carrier in vocab.ATTACK_CARRIERS:
        assert sum(_carrier(r) == carrier for r in records) >= 4, carrier
        # The floor holds on this story's own records, seed or no seed.
        assert sum(_carrier(r) == carrier for r in story) == 4, carrier


def test_every_note_names_a_phrasing_and_each_carrier_spans_all_three() -> None:
    records = _hidden_records()
    for record in records:
        assert _phrasing(record) in _PHRASINGS, record.id
    for carrier in vocab.ATTACK_CARRIERS:
        shapes = {_phrasing(r) for r in records if _carrier(r) == carrier}
        assert shapes == set(_PHRASINGS), carrier


def test_the_phrasing_a_note_names_is_what_stage_two_sees_in_the_raw_payload() -> None:
    """``instruction_override`` trips its named pattern alone; the others trip none.

    The raw payload is scanned, not the extracted text: a carrier stage 1 strips
    would hide the trigger from any post-pipeline scan, and the point of the
    label is what stage 2 *would* see if the carrier survived extraction.
    """
    for record in _hidden_records():
        hits = stage2_hits(_raw_payload_text(record))
        if _phrasing(record) != "instruction_override":
            assert hits == frozenset(), record.id
            assert _REGEX_NOTE.search(record.notes) is None, record.id
            continue
        found = _REGEX_NOTE.search(record.notes)
        assert found is not None, record.id
        assert hits == {found.group(1)}, record.id
        assert _STAGE2_CATEGORY_OF[found.group(1)] == "instruction_override", record.id


def test_each_instruction_override_carrier_is_covered_by_two_different_patterns() -> (
    None
):
    patterns: dict[str, set[str]] = {c: set() for c in vocab.ATTACK_CARRIERS}
    for record in _hidden_story_records():
        found = _REGEX_NOTE.search(record.notes)
        if found is not None:
            patterns[_carrier(record)].add(found.group(1))
    for carrier, names in patterns.items():
        assert len(names) == 2, carrier


def test_each_record_hides_its_marker_in_the_placement_its_carrier_names() -> None:
    for record in _hidden_records():
        assert _sits_in_placement(record), (record.id, _carrier(record))


def test_the_placement_check_rejects_a_marker_in_the_wrong_place() -> None:
    """Negative control: a record moved to a carrier it does not use fails the check."""
    for record in _hidden_story_records():
        for carrier in vocab.ATTACK_CARRIERS:
            if carrier == _carrier(record):
                continue
            moved = replace(record, params={"carrier": carrier})
            assert not _sits_in_placement(moved), (record.id, carrier)


def test_the_jsonld_records_cover_all_four_carrier_keys() -> None:
    """Between them the ``jsonld`` records use ``offers``, ``publisher``,
    ``applicationCategory`` and ``description`` — both object types, too."""
    keys: set[str] = set()
    types: set[object] = set()
    for record in _hidden_records():
        if _carrier(record) != "jsonld":
            continue
        marker = _marker(record)
        for script in _JSONLD_SCRIPT.findall(record.payload["head_html"]):
            document = cast(dict[str, object], json.loads(script))
            types.add(document.get("@type"))
            for path, value in _json_strings(document):
                if marker in value:
                    keys |= set(path) & _JSONLD_CARRIER_KEYS
    assert keys == _JSONLD_CARRIER_KEYS
    assert types == _JSONLD_TYPES


def test_every_hidden_markup_marker_is_distinct() -> None:
    markers = [normalise_for_leak_check(_marker(r)) for r in _hidden_records()]
    assert len(set(markers)) == len(markers)


def test_only_the_carriers_measured_neutralised_for_every_phrasing_carry_pins() -> None:
    records = _hidden_records()
    pinned_carriers = {_carrier(r) for r in records if r.pinned is not None}
    assert pinned_carriers == _STRIPPED_CARRIERS
    for record in records:
        if _carrier(record) in _STRIPPED_CARRIERS:
            assert record.pinned is not None, record.id
            assert set(record.pinned) == _CARRIER_PIN, record.id
            assert record.pinned_reason, record.id
        else:
            assert record.pinned is None, record.id
            assert record.pinned_reason is None, record.id


async def test_every_hidden_markup_pin_holds_on_the_real_routes() -> None:
    """The generic pinned-outcome test, over the category, under both configs."""
    records = _hidden_records()
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
        assert result.signals.marker_on_wire is False, result.summary()
    assert pinned == 2 * sum(record.pinned is not None for record in records)


def test_the_readme_defines_every_carrier_and_phrasing() -> None:
    readme = (vocab.TESTS_CORPUS_ROOT / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    for name in (*vocab.ATTACK_CARRIERS, *_PHRASINGS):
        rows = [line for line in lines if line.startswith(f"| `{name}` |")]
        assert len(rows) == 1, name
        assert len(rows[0].split("|")[2].strip()) > 20, name


# ---------------------------------------------------------------------------
# US-003: window-shaped families
# ---------------------------------------------------------------------------

_WINDOW_FIRST_ID = 215
_WINDOW_LAST_ID = 272
_WINDOW_STORY_IDS = tuple(
    f"atk-{number:04d}" for number in range(_WINDOW_FIRST_ID, _WINDOW_LAST_ID + 1)
)
_WINDOW_FLOORS = {
    "boundary_straddle": 10,
    "density_thinned": 20,
    "repetition_camouflage": 16,
    "sustained_midband": 6,
}
# Each family's own `params` keys (the vocabulary's allowlist is shared).
_WINDOW_PARAM_KEYS = {
    "boundary_straddle": {"placement", "windows_min"},
    "density_thinned": {"density", "placement", "windows_min"},
    "repetition_camouflage": {"repeat", "windows_min"},
    "sustained_midband": {"windows_min"},
}
_STRADDLE_STEPS = {"split_448": 1, "split_896": 2, "split_1344": 3}
# density -> (windows_min, payload sentences): `d` sentences per window.
_DENSITY_LEVELS = {"1/1": (4, 4), "1/2": (4, 2), "1/4": (4, 1), "1/8": (8, 1)}
_DENSITY_PLACEMENTS = ("head", "tail", "interleave")
_DENSITY_BASES = ("natural_language", "authority_seo")
_REPEAT_LEVELS = (1, 2, 3, 5)
_REPEAT_BASES = ("cookie_banner", "footer", "plain")

# The planning budget (not a measured PG2 ratio): 4 characters per token, a
# 448-token step, 64 tokens of overlap, and 30 % headroom inside one window.
_CHARS_PER_TOKEN = 4
_STEP_TOKENS = 448
_OVERLAP_TOKENS = 64
_WINDOW_TOKENS = 512
_ONE_WINDOW_MAX_CHARS = int(_CHARS_PER_TOKEN * _WINDOW_TOKENS / 1.3)
_MAX_EXCERPT_CHARS = 6_000
_GUTENBERG_URL = "https://www.gutenberg.org/ebooks/{}"
_EXCERPT_REF = re.compile(r"offset ([0-9]+), ([0-9]+) chars")
_BASE_NOTE = re.compile(r"base: ([a-z_]+);")


def _window_records() -> list[CorpusRecord]:
    return [r for r in load_corpus() if r.category in vocab.WINDOW_FAMILIES]


def _window_story_records() -> list[CorpusRecord]:
    by_id = {record.id: record for record in _window_records()}
    missing = [story_id for story_id in _WINDOW_STORY_IDS if story_id not in by_id]
    assert not missing, f"story ids missing from the corpus: {missing}"
    return [by_id[story_id] for story_id in _WINDOW_STORY_IDS]


def _stage2_form(record: CorpusRecord) -> str:
    if record.surface == "page":
        return extract_html(page_document(record), record.payload["url"]).raw_text
    return extract_upload_text(record.payload["text"].encode("utf-8")).raw_text


def _windows_min(record: CorpusRecord) -> int:
    value = record.params["windows_min"]
    assert isinstance(value, int), record.id
    return value


def _base(record: CorpusRecord) -> str:
    found = _BASE_NOTE.match(record.notes)
    assert found is not None, record.id
    return found.group(1)


def _marker_count(record: CorpusRecord) -> int:
    return normalise_for_leak_check(_stage2_form(record)).count(
        normalise_for_leak_check(_marker(record))
    )


def test_window_family_counts_surfaces_and_params() -> None:
    records = _window_records()
    story = _window_story_records()
    assert [str(error) for error in lint_corpus(records)] == []
    for category, floor in _WINDOW_FLOORS.items():
        assert sum(r.category == category for r in records) >= floor, category
        assert sum(r.category == category for r in story) >= floor, category
    for record in records:
        assert record.surface in {"page", "text"}, record.id
        assert set(record.params) == _WINDOW_PARAM_KEYS[record.category], record.id
        assert record.pinned is None, record.id
        windows = _windows_min(record)
        if record.category == "boundary_straddle":
            step = _STRADDLE_STEPS[str(record.params["placement"])]
            assert windows == step + 1 >= 2, record.id
        elif record.category == "density_thinned":
            assert record.params["placement"] in _DENSITY_PLACEMENTS, record.id
            density = str(record.params["density"])
            assert density in _DENSITY_LEVELS, record.id
            if density in {"1/4", "1/8"}:
                assert windows >= 4, record.id
        elif record.category == "repetition_camouflage":
            assert record.params["repeat"] in _REPEAT_LEVELS, record.id
            assert windows == 1, record.id
        else:
            assert record.surface == "page", record.id
            assert windows >= 3, record.id
    for surface in ("page", "text"):
        assert any(
            r.category == "boundary_straddle" and r.surface == surface for r in story
        ), surface


def test_the_density_and_repetition_sweeps_are_full_grids() -> None:
    story = _window_story_records()
    density = Counter(
        (_base(r), r.params["density"], r.params["placement"])
        for r in story
        if r.category == "density_thinned"
    )
    assert set(density) == {
        (base, level, placement)
        for base in _DENSITY_BASES
        for level in _DENSITY_LEVELS
        for placement in _DENSITY_PLACEMENTS
    }
    assert set(density.values()) == {1}
    repetition = [r for r in story if r.category == "repetition_camouflage"]
    by_marker: dict[str, set[object]] = {}
    for record in repetition:
        by_marker.setdefault(_marker(record), set()).add(record.params["repeat"])
    assert len(by_marker) == 4
    assert all(levels == set(_REPEAT_LEVELS) for levels in by_marker.values())
    # Half the bases are camouflaged as page boilerplate.
    bases = Counter(_base(r) for r in repetition)
    assert bases == {"cookie_banner": 4, "footer": 4, "plain": 8}
    assert set(bases) == set(_REPEAT_BASES)


def test_window_records_meet_their_character_budget() -> None:
    """Planning budget only; spec 4 measures the real window count."""
    step_chars = _CHARS_PER_TOKEN * _STEP_TOKENS
    for record in _window_records():
        text = _stage2_form(record)
        windows = _windows_min(record)
        budget = _CHARS_PER_TOKEN * (_STEP_TOKENS * (windows - 1) + _OVERLAP_TOKENS)
        assert len(text) >= budget, record.id
        if record.category == "repetition_camouflage":
            assert len(text) <= _ONE_WINDOW_MAX_CHARS, record.id
        if record.category == "boundary_straddle":
            boundary = step_chars * _STRADDLE_STEPS[str(record.params["placement"])]
            folded = normalise_for_leak_check(text)
            needle = normalise_for_leak_check(_marker(record))
            first = folded.index(needle)
            second = folded.index(needle, first + 1)
            assert first + len(needle) <= boundary <= second, record.id


def test_the_marker_is_in_every_payload_fragment() -> None:
    for record in _window_story_records():
        count = _marker_count(record)
        if record.category == "boundary_straddle":
            assert count == 2, record.id
        elif record.category == "density_thinned":
            level = str(record.params["density"])
            assert count == _DENSITY_LEVELS[level][1], record.id
        elif record.category == "repetition_camouflage":
            assert count == record.params["repeat"], record.id
        else:
            assert count >= 1, record.id


def test_filler_provenance_is_public_domain_with_its_ebook_number() -> None:
    for record in _window_story_records():
        source = record.source
        if record.category == "sustained_midband":
            assert source["kind"] == "owned", record.id
            continue
        assert source["kind"] == "third_party", record.id
        assert source["licence"] == "LicenseRef-PublicDomain", record.id
        revision = source["revision"]
        assert revision is not None and revision.isdigit(), record.id
        assert source["url"] == _GUTENBERG_URL.format(revision), record.id
        assert (source["name"] or "").startswith("Project Gutenberg: "), record.id
        excerpts = _EXCERPT_REF.findall(source["record_ref"] or "")
        assert excerpts, record.id
        assert all(int(chars) <= _MAX_EXCERPT_CHARS for _, chars in excerpts), record.id


def test_every_window_record_is_stage_two_clean_on_its_route_form() -> None:
    for record in _window_records():
        assert scan_structural(_stage2_form(record)).flags == [], record.id


def test_the_lint_rejects_a_stage_two_shaped_sweep_base() -> None:
    """Every stage-2 probe, spliced into a clean sweep record, fails the lint."""
    story = _window_story_records()
    text_record = next(r for r in story if r.surface == "text")
    page_record = next(r for r in story if r.surface == "page")
    assert lint_corpus([text_record, page_record]) == []
    for name, probe in vocab.STAGE2_REGEX_PROBES.items():
        text = f"{probe}\n{text_record.payload['text']}"
        shaped = replace(text_record, payload={**text_record.payload, "text": text})
        rules = {error.rule for error in lint_corpus([shaped])}
        assert "sweep_stage2_clean" in rules, name
    # The page form is the one stage 2 receives: an entity-encoded trigger is
    # clean in the raw HTML and BLOCK-shaped once `/retrieve` decodes it.
    encoded = f"<p>{html.escape('<system>')}</p>{page_record.payload['body_html']}"
    assert stage2_hits(encoded) == frozenset()
    shaped = replace(page_record, payload={**page_record.payload, "body_html": encoded})
    assert [e.rule for e in lint_corpus([shaped])] == ["sweep_stage2_clean"]


async def test_every_window_record_reaches_the_classifier_under_replay() -> None:
    """No sweep record is structurally blocked; each one is scored.

    Under ``fallback=0.0`` the replay answers one window; spec 4's cassette
    carries the real per-window entries for the same texts.
    """
    records = _window_records()
    results = await drive_all(records, _fallback(), configs=["default", "contiguity"])
    assert len(results) == 2 * len(records)
    for result in results:
        signals = result.signals
        assert signals.promptguard_state == "scanned", result.summary()
        assert signals.promptguard_state != "structural_blocked", result.summary()
        assert signals.structural_flags == (), result.summary()
        assert signals.windows >= 1 and signals.score is not None, result.summary()


def test_the_regex_floor_pins_live_in_the_stage_two_categories() -> None:
    """The floor paired with the repetition curve: pinned `blocked` records in
    the BLOCK families, none inside any window family."""
    records = load_corpus()
    for category in _BLOCK_FAMILIES:
        assert any(
            r.category == category and r.pinned == ("blocked",) for r in records
        ), category
    assert all(r.pinned is None for r in records if r.category in vocab.WINDOW_FAMILIES)


def test_the_readme_documents_every_window_family_and_the_budget_rule() -> None:
    readme = (vocab.TESTS_CORPUS_ROOT / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    for family, keys in _WINDOW_PARAM_KEYS.items():
        rows = [line for line in lines if line.startswith(f"| `{family}` |")]
        assert len(rows) == 1, family
        for key in keys:
            assert f"`{key}`" in rows[0], (family, key)
    assert "4 characters per token" in readme
    assert "LicenseRef-PublicDomain" in readme


# ---------------------------------------------------------------------------
# US-005: natural-language and authority/SEO poisoning
# ---------------------------------------------------------------------------

# This story's records: `atk-0403` … `atk-0460`, 32 `natural_language` then 26
# `authority_seo`, appended after the ingested rows. The seed's `atk-0012` and
# `atk-0013` are owned too; the 122 ingested `natural_language` rows are
# `third_party`. Every floor below counts *owned* records, so it holds even when
# ingestion is skipped.
_CLASSIFIER_FIRST_ID = 403
_CLASSIFIER_LAST_ID = 460
_CLASSIFIER_STORY_IDS = tuple(
    f"atk-{number:04d}"
    for number in range(_CLASSIFIER_FIRST_ID, _CLASSIFIER_LAST_ID + 1)
)
_CLASSIFIER_CATEGORIES = ("natural_language", "authority_seo")
_CLASSIFIER_FLOORS = {"natural_language": 24, "authority_seo": 16}
_REQUIRED_LANGUAGES = frozenset({"en", "de", "fr", "es", "pt", "ja"})
_STORY_LANGUAGES = _REQUIRED_LANGUAGES | {"zh", "it"}
_PER_LANGUAGE_FLOOR = 2
_INGESTED_SOURCES = frozenset({"AgentDojo", "LLMail-Inject", "CyberSecEval"})

# What `notes` names: the context and the ask of a `natural_language` record
# (the hint's five contexts and four verbs), the claim and the vector of an
# `authority_seo` one (the Zscaler shape and where the provider surfaces it).
_NL_CONTEXTS = frozenset(
    {"article", "product_review", "forum_reply", "readme", "changelog"}
)
_NL_ASKS = frozenset({"summarise_falsely", "recommend", "visit_url", "include_phrase"})
_AS_CLAIMS = frozenset(
    {"verified_source", "rank_first", "publisher_claim", "payment_policy"}
)
_AS_VECTOR_SURFACE = {
    "surfaced_snippet": "search",
    "og_description_snippet": "search",
    "stuffed_title": "search",
    "page_body": "page",
    "text_file": "text",
}
_NL_NOTE = re.compile(r"context: ([a-z_]+); ask: ([a-z_]+); ")
_AS_NOTE = re.compile(
    r"claim: ([a-z_]+); vector: ([a-z_]+)"
    r"(?:; sibling of (atk-[0-9]{4}) \(([a-z_]+)\))?; "
)
_HOST = re.compile(r"https?://([A-Za-z0-9.\-]+)")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[^\W\d_]+")
# Common function words per Latin-script language. A word two languages share
# is dropped from both, so a hit is evidence for exactly one language.
_FUNCTION_WORDS: dict[str, frozenset[str]] = {
    lang: frozenset(words.split())
    for lang, words in {
        "en": "the and of to is are this that with for your you be on it as at by "
        "or an in a not will",
        "de": "der die das und nicht ist für mit den dem auf sind muss müssen diese "
        "dieser anderen zu ein eine einen im vom zum bei nach wie oder auch nur "
        "werden wird kann von des es sich",
        "fr": "le les des une est pour vous dans cette sont doit doivent aucun autre "
        "sur qui cet aux du au ne pas ce ses et ou par avec il elle plus mais comme "
        "son sa leur",
        "es": "el los las y según deben debe más son cualquier otros otra otro para "
        "por con del una un esta este estos como que se su sus al lo no es ha muy "
        "pero también todo todos",
        "pt": "os as uma não você são pelo pela devem seu sua dos das também outros "
        "outro é do da no na nos nas ao aos para por com um em que se está este esta "
        "estes deste desta como mais muito já lá",
        "it": "il gli che della è sono devono dei delle questo questa nel tutti altri "
        "dell per con un una non di lo le la si più anche come ha sui sul nella negli",
    }.items()
}
_STOPWORDS: dict[str, frozenset[str]] = {
    lang: words
    - frozenset(
        word
        for other, others in _FUNCTION_WORDS.items()
        if other != lang
        for word in others
    )
    for lang, words in _FUNCTION_WORDS.items()
}


def _owned_classifier_only() -> list[CorpusRecord]:
    """Every owned `natural_language` / `authority_seo` record, seed included."""
    return [
        record
        for record in load_corpus()
        if record.category in _CLASSIFIER_CATEGORIES
        and record.source["kind"] == "owned"
    ]


def _classifier_story_records() -> list[CorpusRecord]:
    by_id = {record.id: record for record in load_corpus()}
    missing = [story_id for story_id in _CLASSIFIER_STORY_IDS if story_id not in by_id]
    assert not missing, f"story ids missing from the corpus: {missing}"
    return [by_id[story_id] for story_id in _CLASSIFIER_STORY_IDS]


def _written_in(record: CorpusRecord) -> str:
    """The language a record's payload is written in, from its script and words."""
    text = _raw_text(record)
    if _KANA.search(text):
        return "ja"
    if _HAN.search(text):
        return "zh"
    words = _WORD.findall(text.casefold())
    ranked = sorted(
        (
            (sum(word in stop for word in words), lang)
            for lang, stop in _STOPWORDS.items()
        ),
        reverse=True,
    )
    assert ranked[0][0] > ranked[1][0], record.id
    return ranked[0][1]


def _is_reserved_host(host: str) -> bool:
    host = host.rstrip(".").lower()
    return (
        any(
            host == name or host.endswith(f".{name}") for name in vocab.RESERVED_DOMAINS
        )
        or host.rsplit(".", 1)[-1] in vocab.RESERVED_TLDS
    )


def test_this_story_authored_58_owned_records_in_the_two_classifier_only_files() -> (
    None
):
    records = _classifier_story_records()
    assert len(records) == 58
    assert Counter(record.category for record in records) == {
        "natural_language": 32,
        "authority_seo": 26,
    }
    assert all(record.kind == "attack" for record in records)
    assert all(record.source["kind"] == "owned" for record in records)
    assert all(record.pinned is None for record in records)
    assert [str(error) for error in lint_corpus(records)] == []


def test_owned_records_meet_the_floors_the_language_floor_and_all_three_surfaces() -> (
    None
):
    owned = _owned_classifier_only()
    counts = Counter(record.category for record in owned)
    for category, floor in _CLASSIFIER_FLOORS.items():
        assert counts[category] >= floor, category
    assert len({record.lang for record in owned}) >= vocab.MIN_RECORDS["languages"]
    assert {record.lang for record in owned} >= _STORY_LANGUAGES
    for category in _CLASSIFIER_CATEGORIES:
        in_category = [record for record in owned if record.category == category]
        assert {record.surface for record in in_category} == set(vocab.SURFACES)
        for lang in sorted(_STORY_LANGUAGES):
            of_lang = [record for record in in_category if record.lang == lang]
            assert len(of_lang) >= _PER_LANGUAGE_FLOOR, (category, lang)
            # Each language is authored on every route, not only counted.
            assert {r.surface for r in of_lang} == set(vocab.SURFACES), (category, lang)


def test_the_attack_corpus_meets_its_totals_after_this_story() -> None:
    attacks = [record for record in load_corpus() if record.kind == "attack"]
    assert len(attacks) >= vocab.MIN_RECORDS["attack_total"]
    counts = Counter(record.category for record in attacks)
    assert set(counts) == set(vocab.ATTACK_CATEGORIES)
    for category in vocab.ATTACK_CATEGORIES:
        assert counts[category] >= vocab.MIN_RECORDS["attack_per_category"], category


def test_a_records_lang_label_is_the_language_it_is_written_in() -> None:
    for record in _classifier_story_records():
        assert _written_in(record) == record.lang, record.id


def test_the_language_check_rejects_a_mislabelled_record() -> None:
    records = _classifier_story_records()
    for lang in sorted(_STORY_LANGUAGES):
        target = next(record for record in records if record.lang == lang)
        for other in sorted(_STORY_LANGUAGES - {lang}):
            relabelled = replace(target, lang=other)
            assert _written_in(relabelled) != relabelled.lang, (target.id, other)


def test_a_story_record_carries_no_structural_marker_and_no_square_bracket() -> None:
    """The category's defining invariant, checked on the raw payload and every form.

    A record that trips a stage-2 regex belongs in US-001; the hint also rules
    out brackets, the shape of the `[SYSTEM]` / `[admin]` triggers.
    """
    for record in _classifier_story_records():
        assert stage2_hits(_raw_text(record)) == frozenset(), record.id
        assert stage2_record_hits(record) == frozenset(), record.id
        assert "[" not in _raw_text(record), record.id
        assert "]" not in _raw_text(record), record.id


def test_every_story_marker_is_distinct_and_new_to_the_corpus() -> None:
    records = _classifier_story_records()
    markers = [record.marker for record in records]
    assert len(set(markers)) == len(markers)
    story_ids = {record.id for record in records}
    elsewhere = {
        record.marker for record in load_corpus() if record.id not in story_ids
    }
    assert not elsewhere & set(markers)


def test_every_url_a_story_payload_carries_is_reserved_including_prose_urls() -> None:
    """The lint reads `payload.url` and URL attributes; this reads the prose too."""
    for record in _classifier_story_records():
        hosts = _HOST.findall(_raw_text(record))
        assert hosts or record.surface == "search", record.id
        for host in hosts:
            assert _is_reserved_host(host), record.id


def test_a_natural_language_note_names_its_context_and_its_ask() -> None:
    records = [
        r for r in _classifier_story_records() if r.category == "natural_language"
    ]
    contexts: set[str] = set()
    asks: set[str] = set()
    for record in records:
        found = _NL_NOTE.match(record.notes)
        assert found is not None, record.id
        context, ask = found.groups()
        assert context in _NL_CONTEXTS and ask in _NL_ASKS, record.id
        contexts.add(context)
        asks.add(ask)
        if ask == "visit_url":
            body = "\n".join(
                value for key, value in record.payload.items() if key != "url"
            )
            assert _HOST.search(body) is not None, record.id
        if context in {"readme", "changelog"}:
            assert record.surface == "text", record.id
        if context == "forum_reply":
            assert record.surface == "page", record.id
    assert contexts == _NL_CONTEXTS
    assert asks == _NL_ASKS


def test_an_authority_seo_note_names_its_claim_its_vector_and_the_model_card() -> None:
    records = [r for r in _classifier_story_records() if r.category == "authority_seo"]
    claims: set[str] = set()
    vectors: set[str] = set()
    for record in records:
        found = _AS_NOTE.match(record.notes)
        assert found is not None, record.id
        claim, vector = found.group(1), found.group(2)
        assert claim in _AS_CLAIMS, record.id
        assert _AS_VECTOR_SURFACE.get(vector) == record.surface, record.id
        assert "PG2 model card" in record.notes and "finding 6" in record.notes
        assert "finding 11" in record.notes, record.id
        claims.add(claim)
        vectors.add(vector)
    assert claims == _AS_CLAIMS
    assert vectors == set(_AS_VECTOR_SURFACE)


def test_the_two_search_siblings_of_the_hidden_markup_carriers_are_authored() -> None:
    """US-002 left the `search` siblings of two carriers to this story."""
    by_id = {record.id: record for record in load_corpus()}
    siblings = [
        record
        for record in _classifier_story_records()
        if record.category == "authority_seo" and "sibling of" in record.notes
    ]
    assert len(siblings) == 2
    carriers: set[str] = set()
    for record in siblings:
        found = _AS_NOTE.match(record.notes)
        assert found is not None and found.group(3) is not None, record.id
        origin = by_id[found.group(3)]
        assert origin.category == "hidden_markup", record.id
        assert origin.params["carrier"] == found.group(4), record.id
        assert record.surface == "search", record.id
        assert record.marker is not None
        marker = normalise_for_leak_check(record.marker)
        field = "title" if found.group(4) == "title_stuffing" else "content"
        other = "content" if field == "title" else "title"
        assert marker in normalise_for_leak_check(record.payload[field]), record.id
        assert marker not in normalise_for_leak_check(record.payload[other]), record.id
        carriers.add(found.group(4))
    assert carriers == {"og_description", "title_stuffing"}


async def test_every_owned_natural_language_and_authority_seo_record_is_leaked() -> (
    None
):
    """The category's defining invariant, asserted on every **owned** record.

    They carry no structural marker by construction, so under ``fallback=0.0``
    nothing but stage 3 could stop them, and the assertion is what makes the
    later classifier number mean something. The filter is ``source.kind`` on
    purpose: the ingested rows in these categories are attacker-authored text,
    and the next test states what holds for them instead.
    """
    records = _owned_classifier_only()
    assert len(records) >= sum(_CLASSIFIER_FLOORS.values())
    results = await drive_all(records, _fallback(), configs=["default", "contiguity"])
    assert len(results) == 2 * len(records)
    assert {result.route for result in results} == {"/search", "/retrieve", "/extract"}
    for result in results:
        signals = result.signals
        assert result.outcome == "leaked", result.summary()
        assert signals.marker_on_wire is True, result.summary()
        assert signals.structural_flags == (), result.summary()
        assert signals.omit_reason is None, result.summary()
        assert signals.windows >= 1 and signals.score is not None, result.summary()


def test_every_third_party_row_is_ingested_or_window_filler() -> None:
    """A new sampler must be named, so the ingested-row assertion cannot miss it.

    Attack records only: the benign counter-corpus has its own third-party
    sources (spec 3), checked in ``tests/test_corpus_ingest.py``.
    """
    for record in load_corpus():
        if record.kind != "attack" or record.source["kind"] != "third_party":
            continue
        assert (
            record.source["name"] in _INGESTED_SOURCES
            or record.category in vocab.WINDOW_FAMILIES
        ), record.id


async def test_an_ingested_row_is_leaked_or_keeps_its_structural_category() -> None:
    """Why the assertion above is scoped to owned records.

    The ingested rows land in ``natural_language`` by US-004's one mapping rule
    — the first stage-2 category that fires, else ``natural_language`` — and
    attacker-authored corpora are dense with BLOCK-category phrasing (LLMail-
    Inject alone is ~462 k real attempts), so a share of them legitimately
    comes back ``blocked``. What must hold is narrower: no ingested row sits
    in ``natural_language`` / ``authority_seo`` while stage 2 still trips on
    it, and every other ingested row's category is one its route form fires.
    """
    rows = [
        record
        for record in load_corpus()
        if record.source["kind"] == "third_party"
        and record.source["name"] in _INGESTED_SOURCES
    ]
    by_id = {record.id: record for record in rows}
    results = await drive_all(rows, _fallback(), configs=["default"])
    assert len(results) == len(rows)
    for result in results:
        record = by_id[result.record_id]
        hits = stage2_record_hits(record)
        if record.category in _CLASSIFIER_CATEGORIES:
            assert hits == frozenset(), result.summary()
            assert result.outcome == "leaked", result.summary()
        else:
            fired = {_STAGE2_CATEGORY_OF[name] for name in hits}
            assert record.params.get("variant") == "plain", result.summary()
            assert record.category in fired, result.summary()
            expected = "blocked" if record.category in _BLOCK_FAMILIES else "flagged"
            assert result.outcome == expected, result.summary()


def test_the_readme_lists_the_languages_and_the_classifier_only_vocabularies() -> None:
    readme = (vocab.TESTS_CORPUS_ROOT / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    for lang in sorted({record.lang for record in _owned_classifier_only()}):
        rows = [line for line in lines if line.startswith(f"| `{lang}` |")]
        assert len(rows) == 1, lang
    for token in (*_NL_CONTEXTS, *_NL_ASKS, *_AS_CLAIMS, *_AS_VECTOR_SURFACE):
        assert f"`{token}`" in readme, token
