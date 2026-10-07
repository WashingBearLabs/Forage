"""The floors ratchet check: a regenerated ``floors.json`` may only tighten.

``--write-floors`` overwrites every floor with the live measurement, so a run
that regressed would quietly lower the bar it is gated by. This module compares
an old and a new ``floors.json`` and refuses any loosening it cannot prove is
benign from the per-record outcomes in the matching ``baseline.json`` files:

* a ``min_*`` floor (catch, block) may fall only when **no record** of that
  attack category on that route, model and config moved from caught to
  uncaught. The rate then fell because records left the cell's denominator
  (caught earlier, by a stage the cell does not count), not because any attack
  got through;
* a ``max_fpr`` ceiling may rise only when **every** benign record of that
  genre on that route, model and config that newly flags or blocks is in
  :data:`EXEMPT_BENIGN_IDS`;
* a headline floor may never loosen, and an unrecognised key or a changed cell
  set always fails.

``--baseline-fpr`` compares the exact measured benign false-positive rate per
(genre, route, model, config) between two baselines, with the exempt records
removed from both, because the 0.05 floor grid can hide a one-record rise.

Nothing here prints record text: identifiers, routes, models, configs and
numbers only.

Usage::

    uv run python -m scripts.corpus.floors_diff OLD_FLOORS NEW_FLOORS \\
        --old-baseline OLD_BASELINE --new-baseline NEW_BASELINE
    uv run python -m scripts.corpus.floors_diff --baseline-fpr OLD NEW \\
        --exempt ben-0288,ben-0289
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from scripts.corpus.records import load_corpus

EXEMPT_BENIGN_IDS: Final = frozenset({"ben-0288", "ben-0289"})
"""Benign records the owner exempted from the over-defence ratchet.

``/search`` ``over_defence_probe`` parser-strip controls that the raw-markup
scan now flags or blocks, because they are exactly the markup-consumed shape it
exists to catch (``kit_tools/arch/DECISIONS.md``, 2026-10-06; structural-markup
-surface US-011). Adding an id here is an owner decision, never a fix.
"""

_CAUGHT: Final = frozenset({"blocked", "flagged", "neutralised"})
_POSITIVE: Final = frozenset({"blocked", "flagged"})
_SKIP_TOP: Final = frozenset({"_regenerate", "format"})

Path_ = tuple[str, ...]
Outcomes = dict[tuple[str, str, str, str], str]
"""(record id, route, config, model) -> outcome token."""


@dataclass
class Diff:
    """What changed between two floors files, and what could not be justified."""

    tightened: list[str] = field(default_factory=list[str])
    justified: list[str] = field(default_factory=list[str])
    problems: list[str] = field(default_factory=list[str])


def _leaves(node: object, path: Path_ = ()) -> dict[Path_, float]:
    out: dict[Path_, float] = {}
    if isinstance(node, Mapping):
        for key, value in cast(Mapping[str, object], node).items():
            if not path and key in _SKIP_TOP:
                continue
            out.update(_leaves(value, (*path, key)))
    elif isinstance(node, int | float) and not isinstance(node, bool):
        out[path] = float(node)
    else:
        raise ValueError(f"non-numeric floor at {'/'.join(path)}")
    return out


def _outcomes(baseline: Mapping[str, object]) -> Outcomes:
    records = cast(
        Mapping[str, Mapping[str, Mapping[str, Mapping[str, Sequence[object]]]]],
        baseline["records"],
    )
    return {
        (record_id, route, config, model): str(result[0])
        for record_id, routes in records.items()
        for route, configs in routes.items()
        for config, models in configs.items()
        for model, result in models.items()
    }


def _ids_by_category() -> dict[str, set[str]]:
    by_category: dict[str, set[str]] = {}
    for record in load_corpus():
        by_category.setdefault(record.category, set()).add(record.id)
    return by_category


def _regressed(
    old: Outcomes, new: Outcomes, ids: set[str], route: str, model: str, config: str
) -> list[str]:
    """Records of ``ids`` caught in ``old`` and not caught in ``new`` for one cell."""
    return sorted(
        record_id
        for record_id in ids
        if old.get((record_id, route, config, model)) in _CAUGHT
        and new.get((record_id, route, config, model)) not in _CAUGHT
    )


def _newly_positive(
    old: Outcomes, new: Outcomes, ids: set[str], route: str, model: str, config: str
) -> list[str]:
    """Records of ``ids`` newly flagged or blocked in ``new`` for one cell."""
    return sorted(
        record_id
        for record_id in ids
        if new.get((record_id, route, config, model)) in _POSITIVE
        and old.get((record_id, route, config, model)) not in _POSITIVE
    )


def compare_floors(
    old_floors: Mapping[str, object],
    new_floors: Mapping[str, object],
    old_baseline: Mapping[str, object] | None = None,
    new_baseline: Mapping[str, object] | None = None,
    ids_by_category: Mapping[str, set[str]] | None = None,
) -> Diff:
    """Classify each changed floor: a tightening, a justified loosening or a problem."""
    old = _leaves(old_floors)
    new = _leaves(new_floors)
    diff = Diff()
    if set(old) != set(new):
        added = sorted("/".join(p) for p in set(new) - set(old))
        removed = sorted("/".join(p) for p in set(old) - set(new))
        diff.problems.append(f"cell set changed: added={added} removed={removed}")
    have_baselines = old_baseline is not None and new_baseline is not None
    old_out = _outcomes(old_baseline) if old_baseline is not None else {}
    new_out = _outcomes(new_baseline) if new_baseline is not None else {}
    categories: Mapping[str, set[str]] = (
        ids_by_category if ids_by_category is not None else {}
    )
    if have_baselines and ids_by_category is None:
        categories = _ids_by_category()
    for path in sorted(set(old) & set(new)):
        before, after = old[path], new[path]
        name, key = "/".join(path), path[-1]
        if not key.startswith(("min_", "max_")):
            diff.problems.append(f"unrecognised floor key: {name}")
            continue
        if before == after:
            continue
        tightened = after > before if key.startswith("min_") else after < before
        if tightened:
            diff.tightened.append(f"{name}: {before} -> {after}")
            continue
        loosened = f"{name}: {before} -> {after}"
        if path[0] == "headline" or len(path) != 6 or not have_baselines:
            diff.problems.append(f"loosened (unprovable): {loosened}")
            continue
        section, category, route, model, config = path[0], *path[1:5]
        ids = categories.get(category, set())
        if not ids:
            diff.problems.append(f"loosened, no records for {category}: {loosened}")
        elif section == "attacks" and key.startswith("min_"):
            regressed = _regressed(old_out, new_out, ids, route, model, config)
            if regressed:
                diff.problems.append(
                    f"loosened, caught->uncaught {regressed}: {loosened}"
                )
            else:
                diff.justified.append(
                    f"{loosened} (denominator only; no record regressed)"
                )
        elif section == "benign" and key == "max_fpr":
            newly = _newly_positive(old_out, new_out, ids, route, model, config)
            unexplained = [r for r in newly if r not in EXEMPT_BENIGN_IDS]
            if unexplained:
                diff.problems.append(
                    f"loosened, new false positives {unexplained}: {loosened}"
                )
            else:
                diff.justified.append(f"{loosened} (exempt records only: {newly})")
        else:
            diff.problems.append(f"loosened (unprovable): {loosened}")
    return diff


def _positives(
    outcomes: Outcomes, ids: set[str], route: str, config: str, model: str
) -> tuple[int, int]:
    """(flagged-or-blocked count, measured count) of ``ids`` in one cell."""
    present = [
        outcomes[(i, route, config, model)]
        for i in ids
        if (i, route, config, model) in outcomes
    ]
    return sum(o in _POSITIVE for o in present), len(present)


def compare_baseline_fpr(
    old_baseline: Mapping[str, object],
    new_baseline: Mapping[str, object],
    exempt: frozenset[str],
    ids_by_category: Mapping[str, set[str]] | None = None,
) -> list[str]:
    """Every (genre, route, model, config) whose exact FPR rose, exempt ids removed."""
    unknown = exempt - EXEMPT_BENIGN_IDS
    if unknown:
        raise ValueError(f"not an owner-granted exemption: {sorted(unknown)}")
    categories = ids_by_category if ids_by_category is not None else _ids_by_category()
    old_out, new_out = _outcomes(old_baseline), _outcomes(new_baseline)
    cells = {(route, config, model) for (_, route, config, model) in new_out}
    problems: list[str] = []
    for genre, ids in sorted(categories.items()):
        if not any(i.startswith("ben-") for i in ids):
            continue
        counted = ids - exempt
        for route, config, model in sorted(cells):
            old_pos, old_n = _positives(old_out, counted, route, config, model)
            new_pos, new_n = _positives(new_out, counted, route, config, model)
            if not new_n or not old_n:
                continue
            if new_pos * old_n > old_pos * new_n:
                problems.append(
                    f"FPR rose: {genre} {route} {model} {config}: "
                    f"{old_pos}/{old_n} -> {new_pos}/{new_n}"
                )
    return problems


def _load(path: str) -> Mapping[str, object]:
    return cast(
        Mapping[str, object], json.loads(Path(path).read_text(encoding="utf-8"))
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.corpus.floors_diff")
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--old-baseline")
    parser.add_argument("--new-baseline")
    parser.add_argument(
        "--baseline-fpr",
        action="store_true",
        help="OLD and NEW are baseline.json files; compare exact benign FPR",
    )
    parser.add_argument("--exempt", default="", help="comma-separated record ids")
    args = parser.parse_args(argv)
    if args.baseline_fpr:
        exempt = frozenset(i for i in str(args.exempt).split(",") if i)
        problems = compare_baseline_fpr(_load(args.old), _load(args.new), exempt)
        for line in problems:
            print(line)
        print(f"baseline-fpr: {len(problems)} rise(s), exempt={sorted(exempt)}")
        return 1 if problems else 0
    if bool(args.old_baseline) != bool(args.new_baseline):
        parser.error("--old-baseline and --new-baseline go together")
    diff = compare_floors(
        _load(args.old),
        _load(args.new),
        _load(args.old_baseline) if args.old_baseline else None,
        _load(args.new_baseline) if args.new_baseline else None,
    )
    for line in diff.tightened:
        print(f"TIGHTENED {line}")
    for line in diff.justified:
        print(f"JUSTIFIED {line}")
    for line in diff.problems:
        print(f"PROBLEM   {line}")
    print(
        f"floors: {len(diff.tightened)} tightened, {len(diff.justified)} justified, "
        f"{len(diff.problems)} problem(s)"
    )
    return 1 if diff.problems else 0


if __name__ == "__main__":
    sys.exit(main())
