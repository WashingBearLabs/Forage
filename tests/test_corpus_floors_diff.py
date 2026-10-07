"""The floors ratchet check (``scripts/corpus/floors_diff.py``).

Synthetic floors and baselines only: record ids, categories, routes, models and
numbers, never record text.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands or call arguments (which
could carry record text). ``tests/test_corpus_lint.py`` requires this marker
in every corpus test module.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.corpus import floors_diff
from scripts.corpus.floors_diff import (
    EXEMPT_BENIGN_IDS,
    compare_baseline_fpr,
    compare_floors,
)

_M = "model-a"
JSON = dict[str, Any]
_IDS = {
    "authority_impersonation": {"atk-1", "atk-2"},
    "over_defence_probe": {"ben-0288", "ben-0289", "ben-9001"},
}


def _floors(
    min_catch: float = 0.5, max_fpr: float = 0.5, headline: float = 0.3
) -> JSON:
    return {
        "_regenerate": "cmd",
        "format": 1,
        "attacks": {
            "authority_impersonation": {
                "/extract": {
                    _M: {"default": {"min_catch": min_catch, "min_block": min_catch}}
                }
            }
        },
        "benign": {
            "over_defence_probe": {"/search": {_M: {"default": {"max_fpr": max_fpr}}}}
        },
        "headline": {
            _M: {"default": {"min_catch_all": headline, "max_fpr_external": 0.1}}
        },
    }


def _baseline(outcomes: dict[str, tuple[str, str]]) -> JSON:
    """``outcomes`` maps record id -> (route, outcome) under model _M / default."""
    return {
        "records": {
            rid: {route: {"default": {_M: [outcome, False]}}}
            for rid, (route, outcome) in outcomes.items()
        }
    }


_OLD_B: JSON = _baseline(
    {
        "atk-1": ("/extract", "blocked"),
        "atk-2": ("/extract", "leaked"),
        "ben-0288": ("/search", "clean"),
        "ben-0289": ("/search", "clean"),
        "ben-9001": ("/search", "flagged"),
    }
)


def test_tightening_is_reported_not_a_problem() -> None:
    diff = compare_floors(_floors(), _floors(min_catch=1.0, max_fpr=0.4, headline=0.4))
    assert not diff.problems, "pure tightening must pass"
    assert len(diff.tightened) == 4, "two min_* rises, one max_fpr fall, one headline"


def test_a_lowered_min_without_baselines_fails() -> None:
    diff = compare_floors(_floors(min_catch=0.5), _floors(min_catch=0.4))
    assert diff.problems, "an unprovable loosening must fail"


def test_a_lowered_min_with_a_regressed_record_fails() -> None:
    new_b: JSON = copy.deepcopy(_OLD_B)
    new_b["records"]["atk-1"]["/extract"]["default"][_M] = ["leaked", False]
    diff = compare_floors(
        _floors(min_catch=0.5), _floors(min_catch=0.4), _OLD_B, new_b, _IDS
    )
    assert any("caught->uncaught" in p for p in diff.problems), "regression must fail"


def test_a_lowered_min_with_only_denominator_movement_is_justified() -> None:
    new_b: JSON = copy.deepcopy(_OLD_B)
    del new_b["records"]["atk-2"]  # left the cell; no caught record lost
    diff = compare_floors(
        _floors(min_catch=0.5), _floors(min_catch=0.4), _OLD_B, new_b, _IDS
    )
    assert not diff.problems, "denominator-only movement is provable"
    assert len(diff.justified) == 2, "min_catch and min_block both justified"


def test_a_raised_max_fpr_from_exempt_records_only_is_justified() -> None:
    new_b: JSON = copy.deepcopy(_OLD_B)
    new_b["records"]["ben-0288"]["/search"]["default"][_M] = ["blocked", False]
    diff = compare_floors(
        _floors(max_fpr=0.5), _floors(max_fpr=0.8), _OLD_B, new_b, _IDS
    )
    assert not diff.problems, "exempt-only rise is provable"


def test_a_raised_max_fpr_from_a_non_exempt_record_fails() -> None:
    new_b: JSON = copy.deepcopy(_OLD_B)
    new_b["records"]["ben-9001"]["/search"]["default"][_M] = ["clean", False]
    new_b["records"]["ben-0288"]["/search"]["default"][_M] = ["flagged", False]
    extra = copy.deepcopy(_IDS)
    extra["over_defence_probe"] = {"ben-0288", "ben-9001", "ben-9002"}
    new_b["records"]["ben-9002"] = {"/search": {"default": {_M: ["blocked", False]}}}
    diff = compare_floors(
        _floors(max_fpr=0.5), _floors(max_fpr=0.8), _OLD_B, new_b, extra
    )
    assert any("ben-9002" in p for p in diff.problems), "non-exempt rise must fail"


def test_a_loosened_headline_always_fails() -> None:
    diff = compare_floors(
        _floors(headline=0.3), _floors(headline=0.2), _OLD_B, _OLD_B, _IDS
    )
    assert any("headline" in p for p in diff.problems), "headline never loosens"


def test_an_unrecognised_key_and_a_changed_cell_set_fail() -> None:
    new: JSON = _floors()
    new["attacks"]["authority_impersonation"]["/extract"][_M]["default"]["weird"] = 1.0
    diff = compare_floors(_floors(), new)
    assert any("cell set changed" in p for p in diff.problems), "cell set change fails"
    old: JSON = _floors()
    old["attacks"]["authority_impersonation"]["/extract"][_M]["default"]["weird"] = 1.0
    diff = compare_floors(old, copy.deepcopy(old))
    assert any("unrecognised" in p for p in diff.problems), "unknown key fails"


def test_baseline_fpr_catches_a_one_record_rise_and_honours_the_exemption() -> None:
    new_b: JSON = copy.deepcopy(_OLD_B)
    new_b["records"]["ben-0289"]["/search"]["default"][_M] = ["flagged", False]
    assert compare_baseline_fpr(_OLD_B, new_b, frozenset(), _IDS), "rise detected"
    assert not compare_baseline_fpr(_OLD_B, new_b, EXEMPT_BENIGN_IDS, _IDS), (
        "exempt record removed from both sides"
    )


def test_baseline_fpr_refuses_an_exemption_the_owner_did_not_grant() -> None:
    with pytest.raises(ValueError, match="not an owner-granted exemption"):
        compare_baseline_fpr(_OLD_B, _OLD_B, frozenset({"ben-9001"}), _IDS)


def test_the_exemption_list_is_exactly_the_owner_ruling() -> None:
    assert frozenset({"ben-0288", "ben-0289"}) == EXEMPT_BENIGN_IDS, (
        "changing the exemption is an owner decision (DECISIONS.md 2026-10-06)"
    )


def test_the_cli_exit_codes(tmp_path: Path) -> None:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(_floors()), encoding="utf-8")
    new.write_text(json.dumps(_floors(min_catch=1.0)), encoding="utf-8")
    assert floors_diff.main([str(old), str(new)]) == 0, "tightening exits 0"
    new.write_text(json.dumps(_floors(min_catch=0.1)), encoding="utf-8")
    assert floors_diff.main([str(old), str(new)]) == 1, "unprovable loosening exits 1"
