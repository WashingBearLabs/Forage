"""The corpus report (``scripts/corpus/report.py``, ``corpus-gates`` US-001).

Stage attribution is a total function: every caught result lands in exactly one
bucket or raises naming its id, route and signals. The report is deterministic
and carries no payload text; a sentinel marker proves the second.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands or call arguments (which
could carry record text). ``tests/test_corpus_lint.py`` requires this marker
in every corpus test module.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from typing import cast

import pytest

import model_fetcher
from pipeline import contract
from scripts.corpus import poolers
from scripts.corpus.drivers import drive_all
from scripts.corpus.outcomes import (
    BLOCKING_ERRORS,
    Outcome,
    Route,
    RouteResult,
    RuleConfig,
    Signals,
)
from scripts.corpus.records import CorpusRecord, load_corpus, record_from_mapping
from scripts.corpus.replay import ReplayClassifier
from scripts.corpus.report import (
    DEFAULT_CONFIGS,
    REGEN_COMMAND,
    STAGES,
    AttributionError,
    Report,
    _offline_model,
    attribute_catch,
    build_parser,
    build_report,
    load_cassettes,
    main,
    render_json,
    render_markdown,
    render_sweep_json,
    render_sweep_markdown,
    sweep_groups,
    sweep_poolers,
)
from tests.corpus_stage2 import stage2_record_hits

_MODEL_22M = "meta-llama/Llama-Prompt-Guard-2-22M"
_MODEL_86M = "meta-llama/Llama-Prompt-Guard-2-86M"
_SENTINEL = "SENTINEL-zq7-do-not-print-7f3a"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _signals(
    *,
    omit_reason: str | None = None,
    suspicious: bool | None = None,
    structural_flags: tuple[str, ...] = (),
    injection_detected: bool | None = None,
    promptguard_state: str | None = None,
    window_scores: tuple[float, ...] = (),
    refusal: bool = False,
) -> Signals:
    return Signals(
        omit_reason=omit_reason,
        suspicious=suspicious,
        structural_flags=structural_flags,
        injection_detected=injection_detected,
        promptguard_state=promptguard_state,
        score=max(window_scores) if window_scores else None,
        window_scores=window_scores,
        windows=len(window_scores),
        status_code=422 if refusal else 200,
        refusal=refusal,
        marker_on_wire=False,
    )


def _result(
    route: Route,
    outcome: Outcome,
    signals: Signals,
    config: RuleConfig = "default",
) -> RouteResult:
    return RouteResult("atk-0001", route, config, _MODEL_22M, outcome, signals)


def _mapping(
    record_id: str, surface: str, payload: dict[str, str], *, kind: str = "attack"
) -> dict[str, object]:
    attack = kind == "attack"
    return {
        "id": record_id,
        "kind": kind,
        "category": "natural_language" if attack else "news",
        "surface": surface,
        "payload": payload,
        "marker": _SENTINEL if attack else None,
        "pinned": None,
        "pinned_reason": None,
        "source": {"kind": "synthetic", "name": "forage", "framing": "indirect"},
        "lang": "en",
        "params": {},
        "notes": "",
    }


def _tiny_corpus() -> list[CorpusRecord]:
    return [
        record_from_mapping(
            _mapping(
                "atk-0001",
                "search",
                {
                    "title": "Garden tips",
                    "url": "https://tips.example.org/garden",
                    "content": f"Note: {_SENTINEL} for the reader.",
                },
            )
        ),
        record_from_mapping(
            _mapping(
                "atk-0002",
                "page",
                {
                    "url": "https://tips.example.org/page",
                    "title": "Harvest",
                    "head_html": "",
                    "body_html": f"<p>Note: {_SENTINEL} for the reader.</p>",
                },
            )
        ),
        record_from_mapping(
            _mapping(
                "atk-0003",
                "text",
                {"filename": "a.txt", "text": f"Note: {_SENTINEL} for the reader."},
            )
        ),
        record_from_mapping(
            _mapping(
                "ben-0001",
                "text",
                {"filename": "b.txt", "text": "A calm paragraph about weather."},
                kind="benign",
            )
        ),
    ]


def _fallback(score: float, model_id: str = _MODEL_22M) -> ReplayClassifier:
    return ReplayClassifier({}, model_id=model_id, revision="test", fallback=score)


@functools.cache
def _full_report() -> Report:
    classifiers, headers = load_cassettes()
    return build_report(
        load_corpus(), classifiers, DEFAULT_CONFIGS, cassette_headers=headers
    )


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "outcome", "signals", "stage"),
    [
        (
            "/search",
            "blocked",
            _signals(omit_reason=contract.OMIT_STRUCTURAL_BLOCKED),
            "stage2",
        ),
        (
            "/search",
            "blocked",
            _signals(
                omit_reason=contract.OMIT_INJECTION_DETECTED, window_scores=(0.9,)
            ),
            "stage3",
        ),
        ("/search", "blocked", _signals(omit_reason=contract.OMIT_INVALID_URL), "url"),
        ("/search", "blocked", _signals(omit_reason=contract.OMIT_BLOCKED_URL), "url"),
        (
            "/search",
            "blocked",
            _signals(omit_reason=contract.OMIT_PROMPTGUARD_UNAVAILABLE),
            "unavailable",
        ),
        (
            "/search",
            "flagged",
            _signals(suspicious=True, window_scores=(0.7,)),
            "stage3",
        ),
        (
            "/search",
            "flagged",
            _signals(suspicious=True, window_scores=(0.5,)),
            "stage2",
        ),
        ("/search", "flagged", _signals(suspicious=True), "stage2"),
        (
            "/retrieve",
            "blocked",
            _signals(
                injection_detected=True,
                promptguard_state="structural_blocked",
                structural_flags=("x",),
            ),
            "stage2",
        ),
        (
            "/retrieve",
            "blocked",
            _signals(
                injection_detected=True,
                promptguard_state="scanned",
                window_scores=(0.9,),
            ),
            "stage3",
        ),
        (
            "/retrieve",
            "blocked",
            _signals(injection_detected=False, promptguard_state="unavailable_blocked"),
            "unavailable",
        ),
        (
            "/extract",
            "flagged",
            _signals(
                injection_detected=False,
                promptguard_state="scanned",
                structural_flags=("x",),
                window_scores=(0.1,),
            ),
            "stage2",
        ),
    ],
)
def test_attribution_buckets(
    route: Route, outcome: Outcome, signals: Signals, stage: str
) -> None:
    assert attribute_catch(_result(route, outcome, signals)).stage == stage


@pytest.mark.parametrize(
    "row", BLOCKING_ERRORS, ids=lambda row: f"{row.route}-{row.error}"
)
def test_every_blocking_error_row_lands_in_refused(row: object) -> None:
    from scripts.corpus.outcomes import BlockingError

    assert isinstance(row, BlockingError)
    result = _result(
        row.route, "blocked", _signals(omit_reason=row.error, refusal=True)
    )
    assert attribute_catch(result).stage == "refused"


def test_a_flagged_search_result_counts_once_under_stage3_when_both_sources_fire() -> (
    None
):
    result = _result(
        "/search", "flagged", _signals(suspicious=True, window_scores=(0.6,))
    )
    attribution = attribute_catch(result)
    assert (attribution.stage, attribution.rule) == ("stage3", "sub_threshold")


def test_the_flagged_search_boundary_is_strict() -> None:
    at = _result("/search", "flagged", _signals(suspicious=True, window_scores=(0.5,)))
    above = _result(
        "/search", "flagged", _signals(suspicious=True, window_scores=(0.5001,))
    )
    assert attribute_catch(at).stage == "stage2"
    assert attribute_catch(above).stage == "stage3"


@pytest.mark.parametrize(
    ("config", "scores", "rule"),
    [
        ("default", (0.9, 0.1), "max_score"),
        ("default", (0.85, 0.1), None),
        ("contiguity", (0.6, 0.5), "contiguity"),
        ("contiguity", (0.9, 0.5, 0.5), "both"),
        ("contiguity", (0.9, 0.1, 0.6), "max_score"),
        ("default", (0.6, 0.6), None),
    ],
)
def test_stage3_rule_is_recomputed_from_the_windows(
    config: RuleConfig, scores: tuple[float, ...], rule: str | None
) -> None:
    result = _result(
        "/retrieve",
        "blocked",
        _signals(
            injection_detected=True, promptguard_state="scanned", window_scores=scores
        ),
        config,
    )
    if rule is None:
        with pytest.raises(AttributionError, match="atk-0001"):
            attribute_catch(result)
    else:
        assert attribute_catch(result).rule == rule


def test_an_unattributable_catch_raises_naming_id_route_and_signals() -> None:
    result = _result("/retrieve", "blocked", _signals(injection_detected=False))
    with pytest.raises(AttributionError) as caught:
        attribute_catch(result)
    message = str(caught.value)
    assert "atk-0001" in message and "/retrieve" in message and "score=" in message


def test_skipped_trusted_is_loud() -> None:
    result = _result(
        "/retrieve",
        "blocked",
        _signals(injection_detected=True, promptguard_state="skipped_trusted"),
    )
    with pytest.raises(AttributionError, match=r"skipped_trusted.*atk-0001"):
        attribute_catch(result)


def test_poolers_pin_the_live_operators() -> None:
    assert not poolers.max_score(0.85)([0.85])
    assert poolers.max_score(0.85)([0.8501])
    assert poolers.contiguity(2, 0.5)([0.5, 0.5])
    assert not poolers.contiguity(2, 0.5)([0.6, 0.2, 0.6])
    assert not poolers.contiguity(2, 0.5)([0.4999, 0.9])


# ---------------------------------------------------------------------------
# build_report on a small corpus
# ---------------------------------------------------------------------------


def test_build_report_on_fallback_scores_attributes_stage3() -> None:
    report = build_report(
        _tiny_corpus(), [_fallback(0.9)], ["default"], cassette_headers={}
    )
    cells = {(cell.route, cell.category): cell for cell in report.attacks}
    for route in ("/search", "/retrieve", "/extract"):
        cell = cells[(route, "natural_language")]
        assert (cell.n, cell.blocked, cell.stage3, cell.rule_max_score) == (
            1,
            1,
            1,
            1,
        )
    assert report.headline[0].n_attacks == 3


def test_a_search_score_between_the_thresholds_is_a_flag_with_sub_threshold_rule() -> (
    None
):
    report = build_report(
        _tiny_corpus(), [_fallback(0.7)], ["default"], cassette_headers={}
    )
    (cell,) = [c for c in report.attacks if c.route == "/search"]
    assert (cell.flagged, cell.stage3, cell.rule_sub_threshold) == (1, 1, 1)


def test_blocked_but_leaked_is_counted_from_the_marker_signal() -> None:
    report = build_report(
        _tiny_corpus(), [_fallback(0.9)], ["default"], cassette_headers={}
    )
    cells = [c for c in report.attacks if c.blocked]
    assert sum(c.blocked_but_leaked for c in cells) == sum(
        1 for row in report.records if row.outcome == "blocked" and row.marker_on_wire
    )


def test_unmeasured_model_is_a_warning_and_a_row() -> None:
    report = build_report(
        _tiny_corpus(), [_fallback(0.0)], ["default"], cassette_headers={}
    )
    assert report.models == (_MODEL_22M,)
    assert report.warnings.unmeasured_models == tuple(
        sorted(set(model_fetcher.ALLOWED_MODEL_IDS) - {_MODEL_22M})
    )
    markdown = render_markdown(report)
    assert "unmeasured_models" in markdown
    assert _MODEL_86M in markdown and "unmeasured" in markdown


def test_version_difference_is_a_rendered_warning() -> None:
    headers = {_MODEL_22M: {"torch": "0.0.1", "transformers": "0.0.1"}}
    report = build_report(
        _tiny_corpus(), [_fallback(0.0)], ["default"], cassette_headers=headers
    )
    assert len(report.warnings.cassette_versions_differ) == 2
    assert "cassette_versions_differ" in render_markdown(report)


def test_the_renderers_are_deterministic_and_carry_no_payload() -> None:
    first = build_report(
        _tiny_corpus(), [_fallback(0.9)], DEFAULT_CONFIGS, cassette_headers={}
    )
    second = build_report(
        _tiny_corpus(), [_fallback(0.9)], DEFAULT_CONFIGS, cassette_headers={}
    )
    assert render_json(first) == render_json(second)
    assert render_markdown(first) == render_markdown(second)
    for text in (render_json(first), render_markdown(first)):
        assert _SENTINEL not in text
        assert "Garden tips" not in text
        assert text.endswith("\n")


def test_render_json_shape() -> None:
    report = build_report(
        _tiny_corpus(), [_fallback(0.9)], ["default"], cassette_headers={}
    )
    text = render_json(report)
    data = json.loads(text)
    assert next(iter(data)) == "_regenerate" and data["_regenerate"] == REGEN_COMMAND
    assert text == json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert data["records"]["atk-0001"]["/search"]["default"][_MODEL_22M] == [
        "blocked",
        False,
    ]
    for cell in data["attacks"]:
        for key, value in cell.items():
            if isinstance(value, float):
                assert value == round(value, 4), key


def test_the_parser_names_its_flags() -> None:
    args = build_parser().parse_args(["--json", "--check"])
    assert args.json and args.check and not args.markdown


def test_main_requires_a_mode() -> None:
    with pytest.raises(SystemExit):
        main([])


# ---------------------------------------------------------------------------
# The committed corpus
# ---------------------------------------------------------------------------


def test_stage_rates_sum_to_catch_in_every_committed_cell() -> None:
    for cell in _full_report().attacks:
        total = sum(cell.stage_count(stage) for stage in STAGES)
        assert total == cell.caught, (
            cell.model,
            cell.config,
            cell.route,
            cell.category,
        )
        rules = (
            cell.rule_max_score
            + cell.rule_contiguity
            + cell.rule_both
            + cell.rule_sub_threshold
        )
        assert rules == cell.stage3, (
            cell.model,
            cell.config,
            cell.route,
            cell.category,
        )


def test_every_committed_record_has_a_row_per_model_and_config() -> None:
    report = _full_report()
    corpus = load_corpus()
    assert len(report.records) == len(corpus) * len(report.models) * len(report.configs)


def test_committed_full_report_renders_without_payload() -> None:
    report = _full_report()
    text = render_json(report) + render_markdown(report)
    for record in load_corpus():
        if record.marker is not None:
            assert record.marker not in text, record.id


@functools.cache
def _search_results() -> tuple[tuple[CorpusRecord, RouteResult], ...]:
    import asyncio

    classifiers, _ = load_cassettes()
    records = [r for r in load_corpus() if r.surface == "search"]
    by_id = {r.id: r for r in records}
    pairs: list[tuple[CorpusRecord, RouteResult]] = []
    for classifier in classifiers:
        results = asyncio.run(drive_all(records, classifier, configs=["default"]))
        pairs.extend((by_id[r.record_id], r) for r in results)
    return tuple(pairs)


def test_a_flagged_search_result_at_or_below_the_flag_score_carries_a_stage2_hit() -> (
    None
):
    for record, result in _search_results():
        signals = result.signals
        if result.outcome != "flagged" or (signals.score or 0.0) > 0.5:
            continue
        assert stage2_record_hits(record), record.id


def test_search_attribution_matches_the_stage2_scanner_on_flagged_results() -> None:
    for record, result in _search_results():
        if result.outcome == "flagged":
            attribution = attribute_catch(result)
            if attribution.stage == "stage2":
                assert stage2_record_hits(record), record.id


def test_the_unavailable_model_is_in_the_allowlist_set_used_for_unmeasured() -> None:
    assert _MODEL_22M in model_fetcher.ALLOWED_MODEL_IDS
    assert _full_report().warnings.unmeasured_models == tuple(
        sorted(set(model_fetcher.ALLOWED_MODEL_IDS) - set(_full_report().models))
    )


def test_headline_names_every_model_and_config() -> None:
    report = _full_report()
    keys: Sequence[tuple[str, str]] = [(h.model, h.config) for h in report.headline]
    assert keys == [(m, c) for m in report.models for c in report.configs]


# ---------------------------------------------------------------------------
# The offline pooling sweep (corpus-gates US-004)
# ---------------------------------------------------------------------------


def test_the_comparison_operators_are_pinned_at_the_threshold() -> None:
    assert not poolers.max_score(0.5)([0.5])  # strict
    assert poolers.max_score(0.5)([0.5, 0.75])
    assert poolers.contiguity(2, 0.5)([0.5, 0.5])  # inclusive
    assert not poolers.contiguity(2, 0.5)([0.5, 0.4999])
    assert poolers.k_anywhere(2, 0.5)([0.5, 0.0, 0.5])  # inclusive
    assert not poolers.k_anywhere(2, 0.5)([0.5, 0.0, 0.4999])
    assert poolers.mean_aggregate(0.5)([0.25, 0.75])  # inclusive
    assert not poolers.mean_aggregate(0.5)([0.25, 0.7499])
    assert poolers.smoothed(2, 0.5)([0.0, 0.25, 0.75, 0.0])  # inclusive
    assert not poolers.smoothed(2, 0.5)([0.0, 0.25, 0.7499, 0.0])


def test_the_poolers_differ_on_their_defining_shapes() -> None:
    split = [0.6, 0.2, 0.6]
    assert poolers.k_anywhere(2, 0.5)(split)
    assert not poolers.contiguity(2, 0.5)(split)
    assert not poolers.k_anywhere(3, 0.5)(split)
    assert poolers.contiguity(3, 0.5)([0.6, 0.6, 0.6, 0.1])
    # A smoothed run fires where a mean over the whole text would not.
    spike = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    assert poolers.smoothed(2, 0.5)(spike)
    assert not poolers.mean_aggregate(0.5)(spike)
    assert not poolers.max_score(1.0)(spike)


def test_the_live_composite_is_max_or_run() -> None:
    live = poolers.live_contiguity()
    assert live([0.9])  # a lone window above 0.85: the bare run misses it
    assert not poolers.contiguity(2, 0.5)([0.9])
    assert live([0.5, 0.5])  # a run with no window above 0.85
    assert not live([0.85])
    assert not live([0.6, 0.2, 0.6])


def test_empty_and_short_sequences_never_fire_by_accident() -> None:
    for fires in (
        poolers.max_score(0.0),
        poolers.contiguity(2, 0.0),
        poolers.k_anywhere(1, 0.0),
        poolers.mean_aggregate(0.0),
        poolers.smoothed(2, 0.0),
        poolers.live_contiguity(),
    ):
        assert not fires([])
    assert poolers.smoothed(2, 0.5)([0.5])  # shorter than the span: averaged whole
    assert not poolers.contiguity(2, 0.5)([0.9])


def _swept(
    spec: Sequence[tuple[dict[str, object], Sequence[float]]],
    config: RuleConfig = "default",
) -> dict[str, object]:
    """Sweep records built from ``(overrides, scores)``; returns the JSON shape."""
    pairs: list[tuple[CorpusRecord, RouteResult]] = []
    for index, (overrides, scores) in enumerate(spec):
        kind = overrides.get("kind", "attack")
        record_id = f"{'atk' if kind == 'attack' else 'ben'}-{index + 1:04d}"
        mapping = _mapping(
            record_id,
            "text",
            {"filename": "a.txt", "text": "x"},
            kind=str(kind),
        )
        mapping.update(overrides)
        record = record_from_mapping(mapping)
        signals = _signals(window_scores=tuple(scores))
        pairs.append(
            (
                record,
                RouteResult(
                    record_id, "/extract", config, _MODEL_22M, "clean", signals
                ),
            )
        )
    return _offline_model(_MODEL_22M, pairs).to_json()


def _table_of(data: dict[str, object]) -> dict[str, dict[str, dict[str, object]]]:
    return cast(dict[str, dict[str, dict[str, object]]], data["poolers"])


def _fired(data: dict[str, object], pooler: str, group: str) -> object:
    return _table_of(data)[pooler][group]["fired"]


def test_the_sweep_counts_per_group_and_pooler_on_synthetic_scores() -> None:
    data = _swept(
        [
            ({"category": "boundary_straddle"}, [0.6, 0.2, 0.6]),
            ({"category": "boundary_straddle"}, [0.6, 0.6, 0.1]),
            ({"category": "sustained_midband"}, [0.1, 0.1]),
            ({"kind": "benign", "category": "long_form"}, [0.6, 0.6, 0.6]),
        ]
    )
    assert data["reached"] == {
        "attack": 3,
        "boundary_straddle": 2,
        "sustained_midband": 1,
        "long_form": 1,
    }
    assert _fired(data, "k_anywhere(2,0.5)", "boundary_straddle") == 2
    assert _fired(data, "contiguity(2,0.5)", "boundary_straddle") == 1
    assert _fired(data, "contiguity(2,0.5)", "attack") == 1
    assert _fired(data, "contiguity(2,0.5)", "long_form") == 1
    assert _fired(data, "max@0.85", "attack") == 0
    assert _fired(data, "contiguity(3,0.5)", "long_form") == 1


def test_a_level_group_names_its_parameter() -> None:
    thinned = record_from_mapping(
        {
            **_mapping("atk-0001", "text", {"filename": "a.txt", "text": "x"}),
            "category": "density_thinned",
            "params": {"density": "1/4"},
        }
    )
    assert sweep_groups(thinned) == [
        "attack",
        "density_thinned",
        "density_thinned density=1/4",
    ]


def test_a_text_that_never_reached_stage_3_is_outside_the_sweep() -> None:
    data = _swept([({"category": "boundary_straddle"}, [])])
    assert data["reached"] == {}


def test_the_sweep_reads_only_the_default_config() -> None:
    data = _swept([({"category": "boundary_straddle"}, [0.9])], config="contiguity")
    assert data["reached"] == {}


def test_rows_that_need_more_windows_than_any_text_has_are_not_applicable() -> None:
    data = _swept([({"category": "boundary_straddle"}, [0.9])])
    table = _table_of(data)
    assert table["max@0.85"]["boundary_straddle"] == {"fired": 1, "rate": 1.0}
    for name in ("contiguity(2,0.5)", "k_anywhere(3,0.4)", "live_contiguity"):
        assert table[name]["boundary_straddle"] == {"fired": None, "rate": None}


def test_every_swept_setting_is_named_once_and_the_grid_is_complete() -> None:
    names = [name for name, _, _ in sweep_poolers()]
    assert len(names) == len(set(names))
    for expected in (
        "max@0.5",
        "max@0.85",
        "max@0.95",
        "live_contiguity",
        "contiguity(2,0.4)",
        "contiguity(3,0.7)",
        "k_anywhere(2,0.5)",
        "k_anywhere(3,0.7)",
        "mean@0.3",
        "mean@0.7",
        "smoothed(2)@0.5",
    ):
        assert expected in names


def test_the_report_carries_the_offline_section_and_names_unmeasured_models() -> None:
    report = build_report(
        _tiny_corpus(), [_fallback(0.9)], DEFAULT_CONFIGS, cassette_headers={}
    )
    offline = json.loads(render_json(report))["offline"]
    assert [entry["model"] for entry in offline] == [
        _MODEL_22M,
        *report.warnings.unmeasured_models,
    ]
    assert offline[0]["poolers"]["max@0.85"]["attack"]["fired"] == 3
    assert offline[1:] == [
        {"model": model, "status": "unmeasured"}
        for model in report.warnings.unmeasured_models
    ]
    markdown = render_sweep_markdown(report)
    assert "unmeasured (no cassette)" in markdown
    assert _SENTINEL not in markdown + render_sweep_json(report)


def test_the_sweep_json_is_the_offline_section_alone_and_deterministic() -> None:
    report = _full_report()
    sweep = render_sweep_json(report)
    assert list(json.loads(sweep)) == ["offline"]
    assert json.loads(sweep)["offline"] == json.loads(render_json(report))["offline"]
    assert sweep == render_sweep_json(report)


def test_main_sweep_prints_the_offline_section(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--sweep"]) == 0
    assert list(json.loads(capsys.readouterr().out)) == ["offline"]


@functools.cache
def _full_results() -> tuple[RouteResult, ...]:
    import asyncio

    classifiers, _ = load_cassettes()
    records = load_corpus()
    results: list[RouteResult] = []
    for classifier in classifiers:
        results.extend(
            asyncio.run(drive_all(records, classifier, configs=DEFAULT_CONFIGS))
        )
    return tuple(results)


def _live_stage3_catch(result: RouteResult) -> bool:
    """Whether the wire shows a stage-3 catch, read without any pooler.

    On ``/search`` that is the ``injection_detected`` omission only: a 0.5-0.85
    flag has no omission reason and is not a stage-3 rule firing.
    """
    signals = result.signals
    if result.route == "/search":
        return signals.omit_reason == contract.OMIT_INJECTION_DETECTED
    return signals.injection_detected is True and signals.promptguard_state == "scanned"


def test_offline_poolers_equal_the_live_stage_3_catch_on_every_record() -> None:
    by_config: dict[RuleConfig, poolers.Pooler] = {
        "default": poolers.max_score(0.85),
        "contiguity": poolers.live_contiguity(),
    }
    results = _full_results()
    assert results
    for result in results:
        offline = by_config[result.config](result.signals.window_scores)
        assert offline == _live_stage3_catch(result), (
            result.record_id,
            result.route,
            result.config,
            result.model_id,
        )
