"""Golden schema test pinning the retrieval sidecar's wire contract (US-001)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from models import ExtractedContent, RetrievedContent, SearchRequest, SearchResponse
from pipeline.contract import CONTRACT_VERSION, PIPELINE_422_ERROR_CODES
from retrieval_app import (
    HealthResponse,
    Pipeline422ErrorResponse,
    SearchMetricsResponse,
)

_GOLDEN_DIR = Path(__file__).parent / "golden"
_GOLDEN_PATH = _GOLDEN_DIR / f"contract_{CONTRACT_VERSION.replace('.', '_')}.json"
# The 1.2.0 golden is frozen (an image has published it) — every reader below
# except test_contract_schema_matches_golden pins against this literal path
# rather than against _GOLDEN_PATH, so a later version bump does not silently
# repoint them at a golden that does not carry the 1.2.0 coverage sweep.
_GOLDEN_1_2_0_PATH = _GOLDEN_DIR / "contract_1_2_0.json"

# The four response models the golden has always pinned, plus two surfaces a
# response-only fixture cannot see: `SearchRequest`, so a request-model
# addition (spec 4's per-request policy) is a recorded wire change rather than
# an invisible one, and `Pipeline422ErrorResponse`, whose `error` enum is the
# rendered form of the /search and /retrieve vocabulary — adding a code moves
# this fixture, which is how `search_unavailable` became a classified change.
_SCHEMA_MODELS = {
    "HealthResponse": HealthResponse,
    "SearchRequest": SearchRequest,
    "SearchResponse": SearchResponse,
    "RetrievedContent": RetrievedContent,
    "ExtractedContent": ExtractedContent,
    "Pipeline422ErrorResponse": Pipeline422ErrorResponse,
}


def _current_schemas() -> dict[str, object]:
    """Return the live JSON schema for every pinned wire model."""
    return {name: model.model_json_schema() for name, model in _SCHEMA_MODELS.items()}


def test_contract_schema_matches_golden() -> None:
    """Any unrecorded wire-shape change must be recorded here in the same commit.

    The fixture filename is derived from ``CONTRACT_VERSION``, so a bump is
    only complete once the matching golden file exists: a failure here means
    either a story forgot to write it in the same commit, or the wire shape
    changed and nobody classified the change. Superseded fixtures stay
    (``contract_1_0_0.json`` alongside ``contract_1_1_0.json``); the retention
    rule itself is ``feature-forage-contract``'s to write down.

    **A failure does not automatically mean "bump CONTRACT_VERSION".** This
    fixture pins ``model_json_schema()``, which covers strictly more than the
    wire: field descriptions and enum-vs-string renderings move it without
    moving a single response byte. ``feature-forage-contract`` US-001 is
    exactly that case — the error-surface documentation pass tightened
    ``degraded_reasons`` to its closed vocabulary and described
    ``capabilities``/``omitted_by_reason``, with per-site parity tests
    (``tests/test_contract_errors.py``) proving zero wire-byte change, and the
    recorded ruling is that such a pass does **not** bump the contract. So:
    regenerate the fixture for a documentation-only change, and bump
    ``CONTRACT_VERSION`` (writing the new fixture alongside the old) when a
    field is added, removed, renamed, or changes meaning. ``contract/`` 's
    governance doc is the classifier.
    """
    golden = json.loads(_GOLDEN_PATH.read_text())
    current = _current_schemas()
    assert current == golden, (
        "wire shape changed — bump CONTRACT_VERSION and update "
        f"{_GOLDEN_PATH.name} in the same commit"
    )


# ---------------------------------------------------------------------------
# 1.2.0 coverage sweep (search-policy-and-health US-003)
#
# Two halves, per the story's hint (3). The presence half confirms every
# 1.2.0 addition actually reached the golden (or, for the three /metrics
# counters, the handler's pinned response model — they are guarded by
# tests/test_contract_metrics.py, not _SCHEMA_MODELS, so MetricsResponse is
# deliberately absent from that dict). The completeness half is the other
# direction: a key-path diff of 1.2.0 against 1.1.0 must equal *exactly* the
# golden-visible subset of that same list, so an addition nobody wrote down
# here fails the gate instead of riding through silently.
# ---------------------------------------------------------------------------

_ONE_TWO_ZERO_DIFFED_SCHEMAS = (
    "HealthResponse",
    "SearchResponse",
    "RetrievedContent",
    "ExtractedContent",
)

# The golden-visible additions the diff half must reproduce exactly.
# `SearchRequest` and the 422 error model have no 1.1.0 golden entry to diff
# against (they are new to 1.2.0 outright), so they are pinned to their exact
# shape separately below rather than diffed.
_EXPECTED_ONE_TWO_ZERO_DIFF = frozenset(
    {
        "SearchResult.content_kind",
        "SearchResult.date",
        "SearchResult.domain",
        "SearchResponse.provider_used",
        "SearchResponse.fallback_fired",
        "SearchResponse.provider_errors",
        "HealthResponse.search_providers",
    }
)


def _as_map(node: object) -> dict[str, Any]:
    """``node`` as a JSON object, or empty when it is anything else (or absent)."""
    return cast(dict[str, Any], node) if isinstance(node, dict) else {}


def _as_list(node: object) -> list[Any]:
    """``node`` as a JSON array, or empty when it is anything else (or absent)."""
    return cast("list[Any]", node) if isinstance(node, list) else []


# Subschema-bearing keywords the walk descends into besides ``properties`` and
# ``$defs``. Without them an enum rendered inline under a list's ``items`` —
# ``HealthResponse.degraded_reasons``' closed `DegradedReason` vocabulary — or
# under an ``anyOf`` branch of an optional field could gain a member unseen.
_SINGLE_SUBSCHEMA_KEYWORDS = ("items", "additionalProperties")
_INDEXED_SUBSCHEMA_KEYWORDS = ("anyOf", "oneOf", "allOf")


def _added_paths(old: object, new: object, label: str) -> set[str]:
    """New ``properties`` keys and new ``enum`` members, per schema/``$defs`` unit.

    A wholly new property is reported once, at its own path, and is not
    recursed into further — anything nested inside a brand-new field is
    definitionally new too, so descending into it would only relabel the same
    addition. A ``$defs`` entry is diffed as its own named unit (``label`` is
    reset to the def's own name), because a shared def can move between
    schemas without that being a shape change. ``items`` and
    ``additionalProperties`` are descended into as ``label[keyword]``, and
    ``anyOf``/``oneOf``/``allOf`` branch by branch as ``label[keyword][i]`` —
    a branch with no 1.1.0 counterpart at that index diffs against nothing,
    so everything it carries counts as added.
    """
    added: set[str] = set()
    if not isinstance(new, dict):
        return added
    new_map = cast(dict[str, Any], new)
    old_map = _as_map(old)

    old_props = _as_map(old_map.get("properties"))
    for key, sub_new in _as_map(new_map.get("properties")).items():
        if key not in old_props:
            added.add(f"{label}.{key}")
        else:
            added |= _added_paths(old_props[key], sub_new, f"{label}.{key}")

    old_enum = _as_list(old_map.get("enum"))
    for member in _as_list(new_map.get("enum")):
        if member not in old_enum:
            added.add(f"{label}[enum]={member}")

    for keyword in _SINGLE_SUBSCHEMA_KEYWORDS:
        added |= _added_paths(
            old_map.get(keyword), new_map.get(keyword), f"{label}[{keyword}]"
        )

    for keyword in _INDEXED_SUBSCHEMA_KEYWORDS:
        old_branches = _as_list(old_map.get(keyword))
        for index, sub_new in enumerate(_as_list(new_map.get(keyword))):
            sub_old: object = old_branches[index] if index < len(old_branches) else None
            added |= _added_paths(sub_old, sub_new, f"{label}[{keyword}][{index}]")

    old_defs = _as_map(old_map.get("$defs"))
    for defname, sub_new in _as_map(new_map.get("$defs")).items():
        added |= _added_paths(old_defs.get(defname, {}), sub_new, defname)

    return added


def _diff_against_1_1_0(current: dict[str, Any]) -> set[str]:
    """Every addition in ``current`` over the 1.1.0 golden, across its schemas."""
    previous = json.loads((_GOLDEN_DIR / "contract_1_1_0.json").read_text())
    added: set[str] = set()
    for name in _ONE_TWO_ZERO_DIFFED_SCHEMAS:
        added |= _added_paths(previous[name], current[name], name)
    return added


def test_the_fourteen_1_2_0_additions_are_all_golden_pinned() -> None:
    """Presence half: every addition hint (3) lists is somewhere checkable.

    Eleven live in the golden fixture; the three ``/metrics`` `search`
    counters live on `SearchMetricsResponse`, pinned against the handler by
    ``tests/test_contract_metrics.py`` rather than by this golden.
    """
    golden = json.loads(_GOLDEN_1_2_0_PATH.read_text())
    search_result_props = golden["SearchResponse"]["$defs"]["SearchResult"][
        "properties"
    ]
    search_response_props = golden["SearchResponse"]["properties"]
    search_request_props = golden["SearchRequest"]["properties"]
    health_response_props = golden["HealthResponse"]["properties"]
    error_enum = golden["Pipeline422ErrorResponse"]["properties"]["error"]["enum"]

    assert "content_kind" in search_result_props
    assert "date" in search_result_props
    assert "domain" in search_result_props
    assert "provider_used" in search_response_props
    assert "fallback_fired" in search_response_props
    assert "provider_errors" in search_response_props
    assert "search_unavailable" in error_enum
    assert "providers" in search_request_props
    assert "allow_paid_fallback" in search_request_props
    assert "search_providers" in health_response_props
    assert "brave_api_key" in health_response_props["capabilities"]["description"]
    assert {"fallback_fired", "paid_calls", "policy_unknown_provider"} <= set(
        SearchMetricsResponse.model_fields
    )


def test_the_1_1_0_to_1_2_0_diff_has_no_unlisted_additions() -> None:
    """Completeness half: the diff is exactly the golden-visible addition list.

    An addition that landed in the wire shape but was never added to
    ``_EXPECTED_ONE_TWO_ZERO_DIFF`` fails here — the fix is to extend that
    set (and hint (3)'s count), never to loosen the comparison. The top-level
    pins keep the diff honest about its own scope: every 1.1.0 schema is
    diffed, the only new top-level entries are the two pinned exactly below,
    and ``MetricsResponse`` stays out of ``_SCHEMA_MODELS``.
    """
    previous = json.loads((_GOLDEN_DIR / "contract_1_1_0.json").read_text())
    current = json.loads(_GOLDEN_1_2_0_PATH.read_text())
    assert set(_ONE_TWO_ZERO_DIFFED_SCHEMAS) == set(previous)
    assert set(current) == set(previous) | {"SearchRequest", "Pipeline422ErrorResponse"}
    assert "MetricsResponse" not in _SCHEMA_MODELS
    assert _diff_against_1_1_0(current) == _EXPECTED_ONE_TWO_ZERO_DIFF


def _inject(
    document: dict[str, Any], path: tuple[str | int, ...], value: object
) -> None:
    """Add ``value`` at ``path``: set as a new key, or appended to an existing array."""
    parent: Any = document
    for step in path[:-1]:
        parent = parent[step]
    leaf = path[-1]
    if isinstance(parent, dict) and leaf not in parent:
        cast(dict[str | int, Any], parent)[leaf] = value
    else:
        cast("list[Any]", parent[leaf]).append(value)


@pytest.mark.parametrize(
    ("path", "value", "reported_as"),
    [
        # An inline enum under a list's `items` — `DegradedReason`.
        (
            ("HealthResponse", "properties", "degraded_reasons", "items", "enum"),
            "injected",
            "HealthResponse.degraded_reasons[items][enum]=injected",
        ),
        # An enum inside an existing `anyOf` branch of an optional field.
        (
            ("RetrievedContent", "properties", "title", "anyOf", 0, "enum"),
            ["injected"],
            "RetrievedContent.title[anyOf][0][enum]=injected",
        ),
        # A whole new `anyOf` branch with no 1.1.0 counterpart at its index.
        (
            (
                "SearchResponse",
                "$defs",
                "SearchResult",
                "properties",
                "engine",
                "anyOf",
            ),
            {"enum": ["injected"], "type": "string"},
            "SearchResult.engine[anyOf][2][enum]=injected",
        ),
        # An enum under a map's `additionalProperties`.
        (
            (
                "HealthResponse",
                "properties",
                "capabilities",
                "additionalProperties",
                "enum",
            ),
            [99],
            "HealthResponse.capabilities[additionalProperties][enum]=99",
        ),
        # A new member on a shared `$defs` enum.
        (
            ("RetrievedContent", "$defs", "TrustTier", "enum"),
            "injected",
            "TrustTier[enum]=injected",
        ),
        # A new top-level property, and a new property on a `$defs` model.
        (
            ("ExtractedContent", "properties", "injected"),
            {"type": "string"},
            "ExtractedContent.injected",
        ),
        (
            ("ExtractedContent", "$defs", "UploadProvenance", "properties", "injected"),
            {"type": "string"},
            "UploadProvenance.injected",
        ),
    ],
)
def test_an_unlisted_addition_anywhere_in_a_diffed_schema_moves_the_diff(
    path: tuple[str | int, ...], value: object, reported_as: str
) -> None:
    """The completeness gate is only as good as the walk: each shape is seen.

    Injects one addition the expected set does not list into a copy of the
    golden and requires the diff to grow by exactly that path — so a member
    added to an enum rendered under ``items`` or ``anyOf`` cannot slip past.
    """
    mutated = json.loads(_GOLDEN_1_2_0_PATH.read_text())
    assert reported_as not in _diff_against_1_1_0(mutated)
    _inject(mutated, path, value)
    assert _diff_against_1_1_0(mutated) == _EXPECTED_ONE_TWO_ZERO_DIFF | {reported_as}


def test_search_request_1_2_0_shape_is_pinned_exactly() -> None:
    """``SearchRequest`` has no 1.1.0 golden entry, so its shape is pinned directly."""
    golden = json.loads(_GOLDEN_1_2_0_PATH.read_text())
    assert set(golden["SearchRequest"]["properties"]) == {
        "query",
        "num_results",
        "promptguard_fail_closed",
        "providers",
        "allow_paid_fallback",
    }


def test_pipeline_422_error_code_is_pinned_to_eleven_members() -> None:
    """The document-wide eighteen (``ERROR_CODES``) is a different gate.

    Never widen ``Pipeline422ErrorCode`` to match that number — it is
    ``/retrieve``'s eight codes plus ``/search``'s three. The 1.2.0 addition
    was exactly one more member (``search_unavailable``); the 1.3.0 ones are
    ``busy`` (``hardening-retrieve-parity`` US-002) and ``extraction_failed``
    (US-003), read here off the held 1.3.0 golden because the frozen 1.2.0 one
    never carried either.
    """
    golden = json.loads(_GOLDEN_PATH.read_text())
    enum = set(golden["Pipeline422ErrorResponse"]["properties"]["error"]["enum"])
    # A literal set, not `set(PIPELINE_422_ERROR_CODES)`: the golden is
    # regenerated from the code, so comparing code to code would let a renamed
    # member — a MAJOR change — pass. The sibling `SearchRequest` and
    # `SearchMetricsResponse` pins are literal for the same reason.
    assert enum == {
        # `/retrieve`'s eight
        "blocked_domain",
        "busy",
        "content_too_large",
        "extraction_failed",
        "fetch_error",
        "fetch_timeout",
        "invalid_url",
        "private_ip",
        # `/search`'s three
        "searxng_error",
        "searxng_unavailable",
        "search_unavailable",
    }
    assert enum == set(PIPELINE_422_ERROR_CODES)


def test_search_metrics_response_1_2_0_field_set_is_pinned_exactly() -> None:
    """1.1.0's four fields plus the three fallback/policy counters, no more.

    ``tests/test_contract_metrics.py`` already pins this model's field set
    against the handler and the served document with a mandatory
    description per field, so ``MetricsResponse`` stays out of
    ``_SCHEMA_MODELS`` — this only pins the *set* the sweep promises.
    """
    assert set(SearchMetricsResponse.model_fields) == {
        "requests",
        "errors",
        "omitted_by_reason",
        "unscanned_results",
        "fallback_fired",
        "paid_calls",
        "policy_unknown_provider",
        # 1.3.0, hardening-retrieve-parity US-006. The `_1_2_0_` in this
        # test's name is left as history, the way the sibling pins are.
        "classification_wait_timeouts",
    }


# ---------------------------------------------------------------------------
# 1.3.0 coverage sweep (hardening-search-sanitization US-004)
#
# The window opens here: unlike the 1.2.0 sweep above, this one covers all six
# _SCHEMA_MODELS entries from the start — the 1.2.0 golden (unlike 1.1.0's)
# already carries SearchRequest and Pipeline422ErrorResponse, so there is no
# schema excluded for want of a counterpart. _diff_against_1_2_0 reuses the
# same generic _added_paths engine; only the schema list and the golden it
# diffs against are version-bound.
# ---------------------------------------------------------------------------

_ONE_THREE_ZERO_DIFFED_SCHEMAS = (
    "HealthResponse",
    "SearchRequest",
    "SearchResponse",
    "RetrievedContent",
    "ExtractedContent",
    "Pipeline422ErrorResponse",
)

# This story's two moves — SearchResult.engine's maxLength: 64 bound and the
# rewritten omitted_by_reason description — are neither a new properties key
# nor a new enum member, so _added_paths cannot see either one (verified by
# running it over contract_1_2_0.json with both mutations applied: every run
# returned set()). The window opens empty; it stays that way for a
# description or bound move and grows only when a later story in this epic
# adds a field or enum member, until spec 8 US-002 freezes it.
_EXPECTED_ONE_THREE_ZERO_DIFF: frozenset[str] = frozenset(
    {
        # hardening-retrieve-parity US-002: `/retrieve`'s admission refusal.
        # One path covers `/search`'s 422 as well, through the shared model.
        "Pipeline422ErrorResponse.error[enum]=busy",
        # hardening-retrieve-parity US-003: a fetched PDF the worker could not
        # parse or spool. Its four reasons are free text on `reason` and the
        # description rewrite is not a properties key, so neither appends.
        "Pipeline422ErrorResponse.error[enum]=extraction_failed",
    }
)


def _diff_against_1_2_0(current: dict[str, Any]) -> set[str]:
    """Every addition in ``current`` over the frozen 1.2.0 golden, across schemas."""
    previous = json.loads(_GOLDEN_1_2_0_PATH.read_text())
    added: set[str] = set()
    for name in _ONE_THREE_ZERO_DIFFED_SCHEMAS:
        added |= _added_paths(previous[name], current[name], name)
    return added


def test_the_1_2_0_to_1_3_0_diff_has_no_unlisted_additions() -> None:
    """Completeness sweep: the diff is exactly the (currently empty) expected set.

    Mirrors the 1.2.0 sweep's own guard line: every schema the frozen 1.2.0
    golden carries is diffed, and none is missing.
    """
    previous = json.loads(_GOLDEN_1_2_0_PATH.read_text())
    current = json.loads(_GOLDEN_PATH.read_text())
    assert set(_ONE_THREE_ZERO_DIFFED_SCHEMAS) == set(previous)
    assert _diff_against_1_2_0(current) == _EXPECTED_ONE_THREE_ZERO_DIFF
