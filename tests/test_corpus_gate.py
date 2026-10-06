"""The corpus gate: exact baseline, floors, pins, completeness and counts.

One replay of the whole corpus under every committed cassette and rule config
is cached at module scope; the drift, floors, pins and completeness-of-floors
tests read it. A failure names a record id, route, model, config and numbers —
never payload text (a sentinel test forces each red and checks).

What each red means, and the two commands, are in ``tests/corpus/README.md``.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands or call arguments (which
could carry record text). ``tests/test_corpus_lint.py`` requires this marker
in every corpus test module.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from scripts.corpus import vocab
from scripts.corpus.drivers import (
    UnrecordedRecordError,
    _drive_one,
    corpus_app,
    drive,
)
from scripts.corpus.outcomes import RuleConfig
from scripts.corpus.records import CorpusRecord, load_corpus
from scripts.corpus.replay import CASSETTES_DIR, ReplayClassifier, read_cassette
from scripts.corpus.report import (
    BASELINE_PATH,
    DEFAULT_CONFIGS,
    FLOORS_PATH,
    REGEN_COMMAND,
    Report,
    _diff,
    build_floors,
    build_report,
    ceil_up,
    floor_down,
    load_cassettes,
    pooled_benign,
    render_json,
)

_MAX_LISTED = 40
"""A failure lists at most this many offending rows."""

_report_cache: list[Report] = []
_records_cache: list[CorpusRecord] = []


def _records() -> list[CorpusRecord]:
    if not _records_cache:
        _records_cache.extend(load_corpus())
    return _records_cache


def live_report() -> Report:
    """The one replay: every record x cassette x config, built once per module."""
    if not _report_cache:
        classifiers, headers = load_cassettes()
        _report_cache.append(
            build_report(
                _records(), classifiers, DEFAULT_CONFIGS, cassette_headers=headers
            )
        )
    return _report_cache[0]


def _listed(lines: Sequence[str]) -> str:
    shown = list(lines[:_MAX_LISTED])
    if len(lines) > _MAX_LISTED:
        shown.append(f"... {len(lines) - _MAX_LISTED} more")
    return "\n".join(shown)


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _number(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _load_floors(path: Path = FLOORS_PATH) -> dict[str, object]:
    return _as_dict(json.loads(path.read_text(encoding="utf-8")))


def _dig(tree: Mapping[str, object], *keys: str) -> dict[str, object] | None:
    node: dict[str, object] = dict(tree)
    for key in keys:
        child = node.get(key)
        if not isinstance(child, dict):
            return None
        node = cast(dict[str, object], child)
    return node


# ---------------------------------------------------------------------------
# Drift: the live render is the committed baseline, byte for byte
# ---------------------------------------------------------------------------


def changed_records(committed: str, report: Report) -> list[str]:
    """``id route [config] model: was -> now`` for every per-record outcome that moved.

    The unified diff of an indented JSON map does not carry the record id on
    the changed line, so the ids are listed explicitly. Outcomes and booleans
    only — never text.
    """
    try:
        old = _as_dict(_as_dict(json.loads(committed)).get("records"))
    except (ValueError, AssertionError):
        return ["committed baseline is unreadable"]
    new = cast(dict[str, object], report.records_map())
    lines: list[str] = []
    for record_id in sorted(set(old) | set(new)):
        old_routes = _as_dict(old.get(record_id, {}))
        new_routes = _as_dict(new.get(record_id, {}))
        for route in sorted(set(old_routes) | set(new_routes)):
            old_cfgs = _as_dict(old_routes.get(route, {}))
            new_cfgs = _as_dict(new_routes.get(route, {}))
            for config in sorted(set(old_cfgs) | set(new_cfgs)):
                old_models = _as_dict(old_cfgs.get(config, {}))
                new_models = _as_dict(new_cfgs.get(config, {}))
                for model in sorted(set(old_models) | set(new_models)):
                    before = old_models.get(model, "absent")
                    after = new_models.get(model, "absent")
                    if before != after:
                        lines.append(
                            f"{record_id} {route} [{config}] {model}: "
                            f"{before} -> {after}"
                        )
    return lines


def drift_report(committed: str, report: Report) -> str:
    """The failure message of a drifted baseline: ids, a capped diff, the command."""
    live = render_json(report)
    moved = changed_records(committed, report)
    return (
        f"the committed baseline is not what the corpus measures now "
        f"({len(moved)} per-record outcome(s) moved):\n"
        + _listed(moved)
        + "\n"
        + _diff(committed, live)
        + f"\nregenerate with: {REGEN_COMMAND}"
    )


def test_the_committed_baseline_is_what_the_corpus_measures() -> None:
    committed = BASELINE_PATH.read_text(encoding="utf-8")
    report = live_report()
    assert committed == render_json(report), drift_report(committed, report)


def test_the_baseline_leads_with_its_regeneration_command() -> None:
    first = BASELINE_PATH.read_text(encoding="utf-8").splitlines()[1]
    assert first.strip() == f'"_regenerate": "{REGEN_COMMAND}",'


def _tampered(committed: str, mutate: str) -> tuple[str, str]:
    """``(tampered baseline, record id)``: one record's first row changed."""
    data = _as_dict(json.loads(committed))
    records = _as_dict(data["records"])
    record_id = sorted(records)[0]
    route = sorted(_as_dict(records[record_id]))[0]
    config = sorted(_as_dict(_as_dict(records[record_id])[route]))[0]
    models = _as_dict(_as_dict(_as_dict(records[record_id])[route])[config])
    row = cast(list[object], models[sorted(models)[0]])
    if mutate == "marker":
        row[1] = not row[1]
    else:
        row[0] = "blocked" if row[0] != "blocked" else "leaked"
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n", (
        record_id
    )


@pytest.mark.parametrize("mutate", ["outcome", "marker"])
def test_a_drifted_baseline_names_the_record_and_the_command(mutate: str) -> None:
    committed = BASELINE_PATH.read_text(encoding="utf-8")
    tampered, record_id = _tampered(committed, mutate)
    assert tampered != committed
    message = drift_report(tampered, live_report())
    assert record_id in message
    assert REGEN_COMMAND in message
    assert len(message.splitlines()) < _MAX_LISTED + 200 + 10


# ---------------------------------------------------------------------------
# Floors
# ---------------------------------------------------------------------------


def floor_violations(report: Report, floors: Mapping[str, object]) -> list[str]:
    """Every cell under a catch / block floor or over an FPR ceiling."""
    out: list[str] = []
    attacks = _as_dict(floors["attacks"])
    for cell in report.attacks:
        entry = _dig(attacks, cell.category, cell.route, cell.model, cell.config)
        if entry is None:
            continue  # a missing floor is the completeness test's red
        where = f"{cell.category} {cell.route} {cell.model} [{cell.config}]"
        for name, measured, caught in (
            ("catch", round(cell.caught / cell.n, 4), cell.caught),
            ("block", round(cell.blocked / cell.n, 4), cell.blocked),
        ):
            floor = _number(entry[f"min_{name}"])
            if measured < floor:
                out.append(
                    f"attack {where}: {name} {measured:.4f} < min_{name} "
                    f"{floor:.2f} ({caught}/{cell.n})"
                )
    benign = _as_dict(floors["benign"])
    for (genre, route, model, config), (n, hit) in sorted(
        pooled_benign(report).items()
    ):
        entry = _dig(benign, genre, route, model, config)
        if entry is None:
            continue
        measured = round(hit / n, 4)
        ceiling = _number(entry["max_fpr"])
        if measured > ceiling:
            out.append(
                f"benign {genre} {route} {model} [{config}]: fpr {measured:.4f} > "
                f"max_fpr {ceiling:.2f} ({hit}/{n})"
            )
    headline = _as_dict(floors["headline"])
    for row in report.headline:
        entry = _dig(headline, row.model, row.config)
        if entry is None:
            continue
        where = f"headline {row.model} [{row.config}]"
        catch = round(row.attacks_caught / row.n_attacks, 4)
        if catch < _number(entry["min_catch_all"]):
            out.append(
                f"{where}: catch_all {catch:.4f} < min_catch_all "
                f"{_number(entry['min_catch_all']):.2f} "
                f"({row.attacks_caught}/{row.n_attacks})"
            )
        fpr = round(row.external_fp / row.n_external, 4) if row.n_external else 0.0
        if fpr > _number(entry["max_fpr_external"]):
            out.append(
                f"{where}: fpr_external {fpr:.4f} > max_fpr_external "
                f"{_number(entry['max_fpr_external']):.2f} "
                f"({row.external_fp}/{row.n_external})"
            )
    return out


def test_every_measured_cell_clears_its_floor() -> None:
    violations = floor_violations(live_report(), _load_floors())
    assert not violations, _listed(violations)


def test_rounding_goes_to_the_safe_side_of_the_grid() -> None:
    assert floor_down(0.3174) == 0.30
    assert floor_down(0.65) == 0.65
    assert floor_down(0.7) == 0.7
    assert floor_down(0.0) == 0.0
    assert floor_down(1.0) == 1.0
    assert ceil_up(0.0426) == 0.05
    assert ceil_up(0.05) == 0.05
    assert ceil_up(0.4286) == 0.45
    assert ceil_up(0.0) == 0.0
    assert ceil_up(0.6641) == 0.70


def _first_attack_cell_below_one(report: Report) -> tuple[str, str, str, str]:
    for cell in report.attacks:
        if cell.caught < cell.n:
            return cell.category, cell.route, cell.model, cell.config
    raise AssertionError("every attack cell catches everything")


def test_raising_a_floor_is_red_and_names_category_route_and_model() -> None:
    report = live_report()
    category, route, model, config = _first_attack_cell_below_one(report)
    raised = copy.deepcopy(_load_floors())
    entry = _dig(_as_dict(raised["attacks"]), category, route, model, config)
    assert entry is not None
    entry["min_catch"] = 1.0
    violations = floor_violations(report, raised)
    assert violations
    message = _listed(violations)
    for expected in (category, route, model, config):
        assert expected in message


def test_lowering_a_floor_below_the_measured_value_stays_green() -> None:
    report = live_report()
    lowered = copy.deepcopy(_load_floors())
    for by_route in _as_dict(lowered["attacks"]).values():
        for by_model in _as_dict(by_route).values():
            for by_config in _as_dict(by_model).values():
                for entry in _as_dict(by_config).values():
                    _as_dict(entry)["min_catch"] = 0.0
                    _as_dict(entry)["min_block"] = 0.0
    assert floor_violations(report, lowered) == []


def test_breaching_a_benign_ceiling_and_the_headline_is_red() -> None:
    report = live_report()
    tightened = copy.deepcopy(_load_floors())
    for row in report.headline:
        entry = _dig(_as_dict(tightened["headline"]), row.model, row.config)
        assert entry is not None
        if row.external_fp:
            entry["max_fpr_external"] = 0.0
        entry["min_catch_all"] = 1.0
    message = _listed(floor_violations(report, tightened))
    assert "fpr_external" in message and "catch_all" in message
    for model in report.models:
        assert model in message


# ---------------------------------------------------------------------------
# Completeness: a cell with no floor is a gate that silently passes
# ---------------------------------------------------------------------------


def _floor_cells(floors: Mapping[str, object]) -> tuple[set[str], set[str], set[str]]:
    def walk(tree: object, depth: int, prefix: tuple[str, ...]) -> set[str]:
        if depth == 0:
            return {" | ".join(prefix)}
        cells: set[str] = set()
        for key, child in _as_dict(tree).items():
            cells |= walk(child, depth - 1, (*prefix, key))
        return cells

    return (
        walk(floors["attacks"], 4, ()),
        walk(floors["benign"], 4, ()),
        walk(floors["headline"], 2, ()),
    )


def _measured_cells(report: Report) -> tuple[set[str], set[str], set[str]]:
    attacks = {
        f"{c.category} | {c.route} | {c.model} | {c.config}" for c in report.attacks
    }
    benign = {
        f"{genre} | {route} | {model} | {config}"
        for genre, route, model, config in pooled_benign(report)
    }
    headline = {f"{row.model} | {row.config}" for row in report.headline}
    return attacks, benign, headline


def test_every_cell_the_corpus_produces_has_a_floor_and_no_floor_is_orphaned() -> None:
    measured = _measured_cells(live_report())
    committed = _floor_cells(_load_floors())
    for kind, have, want in zip(
        ("attack", "benign", "headline"), committed, measured, strict=True
    ):
        assert not want - have, f"{kind} cells with no floor:\n" + _listed(
            sorted(want - have)
        )
        assert not have - want, f"{kind} floors for a cell nothing produces:\n" + (
            _listed(sorted(have - want))
        )


def test_the_scaffold_covers_exactly_the_cells_the_committed_floors_do() -> None:
    scaffold = build_floors(live_report())
    assert _floor_cells(scaffold) == _floor_cells(_load_floors())


def test_a_floors_file_missing_one_cell_is_red() -> None:
    report = live_report()
    trimmed = copy.deepcopy(_load_floors())
    cell = report.attacks[0]
    del _as_dict(_as_dict(_as_dict(trimmed["attacks"])[cell.category])[cell.route])[
        cell.model
    ]
    have = _floor_cells(trimmed)[0]
    missing = _measured_cells(report)[0] - have
    assert any(
        cell.category in line and cell.route in line and cell.model in line
        for line in missing
    )


# ---------------------------------------------------------------------------
# Pins: the record carries the pin; the real cassettes, no fallback, every config
# ---------------------------------------------------------------------------


def pin_violations(report: Report, records: Sequence[CorpusRecord]) -> list[str]:
    pins = {record.id: record for record in records if record.pinned}
    out: list[str] = []
    seen = 0
    for row in report.records:
        record = pins.get(row.record_id)
        if record is None or record.pinned is None:
            continue
        seen += 1
        if row.outcome not in record.pinned:
            out.append(
                f"{row.record_id} {row.route} [{row.config}] {row.model}: "
                f"outcome {row.outcome} not in pinned {list(record.pinned)}"
            )
    expected = len(pins) * len(report.models) * len(report.configs)
    if seen != expected:
        out.append(f"pinned rows seen {seen} != expected {expected}")
    return out


def test_every_pinned_record_holds_on_every_model_and_config() -> None:
    violations = pin_violations(live_report(), _records())
    assert not violations, _listed(violations)


def test_a_slipped_pin_is_red_and_names_the_record() -> None:
    records = _records()
    pinned = next(r for r in records if r.pinned is not None)
    assert pinned.pinned is not None
    slipped = [
        r
        if r.id != pinned.id
        else _with_pin(r, ("clean",) if r.kind == "attack" else ("leaked",))
        for r in records
    ]
    message = _listed(pin_violations(live_report(), slipped))
    assert pinned.id in message


def _with_pin(record: CorpusRecord, pin: tuple[str, ...]) -> CorpusRecord:
    return replace(record, pinned=pin)


# ---------------------------------------------------------------------------
# Completeness of the cassettes: a miss is reported as a miss
# ---------------------------------------------------------------------------


async def _unrecorded_in(
    directory: Path,
    records: Sequence[CorpusRecord],
    configs: Sequence[RuleConfig],
) -> list[str]:
    misses: list[str] = []
    for path in sorted(directory.glob("*.json")):
        classifier = ReplayClassifier.from_cassette(path)
        for config in configs:
            async with corpus_app(classifier=classifier, config=config) as client:
                for record in records:
                    try:
                        await _drive_one(client, record, classifier, config)
                    except UnrecordedRecordError as error:
                        misses.append(str(error))
    return misses


def unrecorded(
    directory: Path = CASSETTES_DIR,
    records: Sequence[CorpusRecord] | None = None,
    configs: Sequence[RuleConfig] = DEFAULT_CONFIGS,
) -> list[str]:
    batch = _records() if records is None else records
    return asyncio.run(_unrecorded_in(directory, batch, configs))


def test_every_cassette_answers_every_record_under_every_config() -> None:
    misses = unrecorded()
    assert not misses, f"{len(misses)} unrecorded record(s):\n" + _listed(misses)


def _a_record_that_reaches_the_classifier() -> tuple[CorpusRecord, str]:
    probe = ReplayClassifier({}, model_id="probe", revision="0", fallback=0.0)
    for record in _records():
        if record.kind != "benign":
            continue
        answered = len(probe.calls)
        asyncio.run(drive(record, probe, config="default"))
        if len(probe.calls) > answered:
            return record, probe.calls[-1].sha256
    raise AssertionError("no record reaches the classifier")


def test_a_cassette_missing_one_entry_is_red_naming_the_record(tmp_path: Path) -> None:
    record, sha = _a_record_that_reaches_the_classifier()
    source = next(CASSETTES_DIR.glob("*22M@*.json"))
    data = read_cassette(source)
    entries = cast(dict[str, object], data["records"])
    assert sha in entries
    del entries[sha]
    (tmp_path / source.name).write_text(json.dumps(data), encoding="utf-8")
    misses = unrecorded(tmp_path, [record], ["default"])
    assert len(misses) == 1
    assert misses[0].startswith(record.id)
    assert "/" in misses[0] and "[default]" in misses[0]
    assert str(data["model_id"]) in misses[0]
    assert f"sha256:{sha[:8]}" in misses[0]
    assert sha not in misses[0]


# ---------------------------------------------------------------------------
# Counts: the corpus does not shrink (ruling 14)
# ---------------------------------------------------------------------------


def count_violations(
    records: Sequence[CorpusRecord], cassette_windows: Mapping[str, int]
) -> list[str]:
    floors = vocab.MIN_RECORDS
    attacks = [r for r in records if r.kind == "attack"]
    benign = [r for r in records if r.kind == "benign"]
    out: list[str] = []
    if len(attacks) < floors["attack_total"]:
        out.append(f"attacks {len(attacks)} < {floors['attack_total']}")
    if len(benign) < floors["benign_total"]:
        out.append(f"benign {len(benign)} < {floors['benign_total']}")
    per_category = Counter(r.category for r in attacks)
    for category in vocab.ATTACK_CATEGORIES:
        if per_category[category] < floors["attack_per_category"]:
            out.append(
                f"category {category}: {per_category[category]} < "
                f"{floors['attack_per_category']}"
            )
    per_genre = Counter(r.category for r in benign)
    for genre in vocab.BENIGN_GENRES:
        if per_genre[genre] < floors["benign_per_genre"]:
            out.append(
                f"genre {genre}: {per_genre[genre]} < {floors['benign_per_genre']}"
            )
    if per_genre["over_defence_probe"] < _MIN_PROBES:
        out.append(
            f"over_defence_probe {per_genre['over_defence_probe']} < {_MIN_PROBES}"
        )
    languages = len({r.lang for r in records})
    if languages < floors["languages"]:
        out.append(f"languages {languages} < {floors['languages']}")
    declared = sum(_windows_min(r) >= 3 for r in records)
    if declared < floors["multi_window"]:
        out.append(
            f"records declaring windows_min >= 3: {declared} < {floors['multi_window']}"
        )
    for model, recorded in sorted(cassette_windows.items()):
        if recorded < floors["multi_window"]:
            out.append(
                f"{model}: cassette entries with >= 3 windows {recorded} < "
                f"{floors['multi_window']}"
            )
    return out


_MIN_PROBES = 30


def _windows_min(record: CorpusRecord) -> int:
    value = record.params.get("windows_min")
    return value if isinstance(value, int) else 0


def _cassette_multi_window_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(CASSETTES_DIR.glob("*.json")):
        data = read_cassette(path)
        entries = cast(dict[str, dict[str, object]], data["records"])
        counts[str(data["model_id"])] = sum(
            cast(int, entry["windows"]) >= 3 for entry in entries.values()
        )
    return counts


def test_the_corpus_meets_every_count_floor() -> None:
    violations = count_violations(_records(), _cassette_multi_window_counts())
    assert not violations, _listed(violations)


def test_a_shrunk_corpus_is_red_and_names_what_shrank() -> None:
    records = _records()
    category = vocab.ATTACK_CATEGORIES[0]
    shrunk = [r for r in records if r.category != category]
    message = _listed(count_violations(shrunk, {"some/model": 0}))
    assert f"category {category}" in message
    assert "some/model" in message


# ---------------------------------------------------------------------------
# Message hygiene: every red is ids and numbers, never payload
# ---------------------------------------------------------------------------


def _payload_needles(records: Sequence[CorpusRecord]) -> list[str]:
    needles: set[str] = set()
    for record in records:
        if record.marker:
            needles.add(record.marker)
        for value in record.payload.values():
            if len(value) >= 12:
                needles.add(value)
    return sorted(needles)


def test_forced_failures_carry_no_payload_text() -> None:
    records = _records()
    report = live_report()
    needles = _payload_needles(records)
    assert needles

    committed = BASELINE_PATH.read_text(encoding="utf-8")
    messages = [
        drift_report(_tampered(committed, "outcome")[0], report),
        drift_report(_tampered(committed, "marker")[0], report),
    ]
    category, route, model, config = _first_attack_cell_below_one(report)
    raised = copy.deepcopy(_load_floors())
    entry = _dig(_as_dict(raised["attacks"]), category, route, model, config)
    assert entry is not None
    entry["min_catch"] = 1.0
    for row in report.headline:
        headline = _dig(_as_dict(raised["headline"]), row.model, row.config)
        assert headline is not None
        headline["max_fpr_external"] = 0.0
        headline["min_catch_all"] = 1.0
    messages.append(_listed(floor_violations(report, raised)))
    pinned = next(r for r in records if r.pinned is not None)
    slipped = [
        r
        if r.id != pinned.id
        else _with_pin(r, ("clean",) if r.kind == "attack" else ("leaked",))
        for r in records
    ]
    messages.append(_listed(pin_violations(report, slipped)))
    messages.append(_listed(count_violations(records[:3], {"some/model": 0})))
    record, sha = _a_record_that_reaches_the_classifier()
    source = next(CASSETTES_DIR.glob("*22M@*.json"))
    probe = ReplayClassifier({}, model_id="meta-llama/probe", revision="0")
    asyncio.run(_collect(record, probe, messages))
    assert sha  # the probe record exists; its text never reaches a message

    for message in messages:
        assert message
        for needle in needles:
            assert needle not in message
    assert source.name


async def _collect(
    record: CorpusRecord, classifier: ReplayClassifier, messages: list[str]
) -> None:
    async with corpus_app(classifier=classifier, config="default") as client:
        try:
            await _drive_one(client, record, classifier, "default")
        except UnrecordedRecordError as error:
            messages.append(str(error))
