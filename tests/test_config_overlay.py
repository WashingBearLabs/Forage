"""Tests for pipeline/config_overlay.py — overlay reading, validation and merge."""

from __future__ import annotations

import copy
import os
import traceback
from pathlib import Path
from typing import Any

import pytest
import yaml

from pipeline.config_overlay import (
    MAX_OVERLAY_BYTES,
    ConfigOverlayError,
    merge_config_overlay,
    read_overlay_file,
)

ROOT = Path(__file__).resolve().parent.parent
SENTINEL_VAL = "SENTINELVAL"
SENTINEL_DIR = "sentinelpathdir"

KNOWN = {
    "user_agents",
    "news_domains",
    "seed_blocklist",
    "promptguard_threshold",
    "extract_route_enabled",
    "cache",
    "extraction",
    "retrieve",
    "cache.ttl_seconds",
    "cache.max_entries",
    "extraction.classification_concurrency",
    "extraction.max_pages",
    "retrieve.fetch_concurrency",
}


def _write(tmp_path: Path, content: bytes | str, name: str = "ov.yaml") -> str:
    directory = tmp_path / SENTINEL_DIR
    directory.mkdir(exist_ok=True)
    path = directory / name
    if isinstance(content, str):
        content = content.encode()
    path.write_bytes(content)
    return str(path)


def _refusal(fn: Any, *args: Any) -> ConfigOverlayError:
    with pytest.raises(ConfigOverlayError) as info:
        fn(*args)
    exc = info.value
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    return exc


def _assert_clean(exc: ConfigOverlayError) -> None:
    rendered = "".join(traceback.format_exception(exc))
    assert SENTINEL_DIR not in rendered
    assert SENTINEL_VAL not in rendered


def _read_token(path: str) -> str:
    exc = _refusal(read_overlay_file, path)
    _assert_clean(exc)
    assert SENTINEL_DIR not in str(exc)
    return exc.token


# -- read_overlay_file --


def test_missing_directory_and_dangling_symlink(tmp_path: Path) -> None:
    assert _read_token(str(tmp_path / SENTINEL_DIR / "nope.yaml")) == "overlay_missing"
    d = tmp_path / SENTINEL_DIR
    d.mkdir()
    assert _read_token(str(d)) == "overlay_missing"
    link = tmp_path / "link.yaml"
    link.symlink_to(tmp_path / "gone")
    assert _read_token(str(link)) == "overlay_missing"
    assert _read_token(str(d / "file" / "child")) == "overlay_missing"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_permission_denied_on_open_and_stat(tmp_path: Path) -> None:
    path = Path(_write(tmp_path, "a: 1\n"))
    path.chmod(0)
    try:
        assert _read_token(str(path)) == "overlay_unreadable"
    finally:
        path.chmod(0o600)
    path.parent.chmod(0)
    try:
        assert _read_token(str(path)) == "overlay_unreadable"
    finally:
        path.parent.chmod(0o700)


def test_size_limit(tmp_path: Path) -> None:
    ok = _write(tmp_path, b"#" * MAX_OVERLAY_BYTES, "ok.yaml")
    assert read_overlay_file(ok) == {}
    big = _write(tmp_path, b"#" * (MAX_OVERLAY_BYTES + 1), "big.yaml")
    assert _read_token(big) == "overlay_unreadable"


def test_non_utf8(tmp_path: Path) -> None:
    assert _read_token(_write(tmp_path, b"a: \xff\xfe\n")) == "overlay_unreadable"


@pytest.mark.parametrize(
    "text",
    [
        f"a: 'unterminated {SENTINEL_VAL}\n",
        f"a: 1\na: {SENTINEL_VAL}\n",
        f"a: 1\n1: x\ntrue: {SENTINEL_VAL}\n1.0: y\n",
        f"a: &x {SENTINEL_VAL}\nb: *x\n",
        "a: &x [1]\nb: [*x]\n",
        "a: &x 1\n{b: *x}: 1\n",
        "? &x k\n: *x\n",
        "base: &b {a: 1}\nc:\n  <<: *b\n",
        f"c:\n  <<: {{a: {SENTINEL_VAL}}}\n",
        f"a: !!python/object:os.system {SENTINEL_VAL}\n",
        "a: " + "[" * 5000 + "]" * 5000 + "\n",
        "a: 2024-99-99\n",
    ],
)
def test_unreadable_documents(tmp_path: Path, text: str) -> None:
    assert _read_token(_write(tmp_path, text)) == "overlay_unreadable"


def test_anchor_without_alias_is_fine(tmp_path: Path) -> None:
    assert read_overlay_file(_write(tmp_path, "a: &x 1\nb: 2\n")) == {"a": 1, "b": 2}


@pytest.mark.parametrize("text", ["[]", "0", "false", "''", "- a\n", "text"])
def test_not_mapping(tmp_path: Path, text: str) -> None:
    exc = _refusal(read_overlay_file, _write(tmp_path, text))
    assert exc.token == "overlay_not_mapping"
    _assert_clean(exc)


@pytest.mark.parametrize("text", ["", "# only a comment\n", "---\n"])
def test_empty_documents(tmp_path: Path, text: str) -> None:
    assert read_overlay_file(_write(tmp_path, text)) == {}


def test_reads_a_mapping(tmp_path: Path) -> None:
    assert read_overlay_file(_write(tmp_path, "cache:\n  ttl_seconds: 5\n")) == {
        "cache": {"ttl_seconds": 5}
    }


# -- merge_config_overlay --


def _baked() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def test_block_merges_leaf_by_leaf_over_baked_config() -> None:
    base = _baked()
    known = _known_from_app()
    merged, keys = merge_config_overlay(
        base, {"extraction": {"classification_concurrency": 2}}, known
    )
    expected = copy.deepcopy(base)
    expected["extraction"]["classification_concurrency"] = 2
    assert merged == expected
    assert keys == ("extraction.classification_concurrency",)


def _known_from_app() -> frozenset[str]:
    import retrieval_app

    return retrieval_app.KNOWN_CONFIG_KEYS


def test_empty_block_changes_nothing() -> None:
    base = _baked()
    merged, keys = merge_config_overlay(base, {"cache": {}}, _known_from_app())
    assert merged == base
    assert keys == ()


def test_lists_replace_outright() -> None:
    base = _baked()
    merged, keys = merge_config_overlay(
        base, {"seed_blocklist": [".example"], "news_domains": []}, _known_from_app()
    )
    assert merged["seed_blocklist"] == [".example"]
    assert merged["news_domains"] == []
    assert keys == ("news_domains", "seed_blocklist")


def test_block_added_over_empty_base_and_keys_sorted() -> None:
    merged, keys = merge_config_overlay(
        {},
        {
            "retrieve": {"fetch_concurrency": 1},
            "cache": {"ttl_seconds": 3, "max_entries": 2},
        },
        KNOWN,
    )
    assert merged == {
        "retrieve": {"fetch_concurrency": 1},
        "cache": {"ttl_seconds": 3, "max_entries": 2},
    }
    assert keys == (
        "cache.max_entries",
        "cache.ttl_seconds",
        "retrieve.fetch_concurrency",
    )


def test_same_value_still_counts_as_set() -> None:
    _, keys = merge_config_overlay(
        {"promptguard_threshold": 0.85}, {"promptguard_threshold": 0.85}, KNOWN
    )
    assert keys == ("promptguard_threshold",)


def _merge_token(overlay: dict[Any, Any], base: dict[str, Any] | None = None) -> str:
    original = copy.deepcopy(overlay)
    exc = _refusal(merge_config_overlay, base or {}, overlay, KNOWN)
    assert overlay == original
    _assert_clean(exc)
    return exc.token


@pytest.mark.parametrize(
    "overlay",
    [
        {"nope": 1},
        {"cache": {"nope": 1}},
        {"cache.max_entries": 1},
        {"extraction.max_pages": 1},
        {1: "x"},
        {"cache": {1: "x"}},
        {"a": 1, 2: 3},
        {"cache": {"ttl_seconds": 1, 7: 2}},
    ],
)
def test_unknown_keys(overlay: dict[Any, Any]) -> None:
    assert _merge_token(overlay) == "overlay_unknown_key"


def test_safe_unknown_key_is_echoed() -> None:
    exc = _refusal(merge_config_overlay, {}, {"typo_key": 1}, KNOWN)
    assert "typo_key" in str(exc)
    exc = _refusal(merge_config_overlay, {}, {"cache": {"typo": 1}}, KNOWN)
    assert "cache.typo" in str(exc)


@pytest.mark.parametrize(
    "bad",
    [
        "foo\n",
        "foo\n.bar",
        "Foo",
        "foo-bar",
        "foo;rm",
        "a" * 65,
        "a" * 65 + ".b",
        "a.b.c",
        "",
    ],
)
def test_unsafe_unknown_key_is_not_echoed(bad: str) -> None:
    for overlay in ({bad: 1}, {"cache": {bad: 1}}):
        exc = _refusal(merge_config_overlay, {}, overlay, KNOWN)
        assert exc.token == "overlay_unknown_key"
        assert "<unprintable key>" in str(exc)
        assert bad.strip() not in str(exc) or bad.strip() == ""
        assert "\n" not in str(exc)


@pytest.mark.parametrize(
    "overlay",
    [
        {"promptguard_threshold": None},
        {"cache": {"ttl_seconds": None}},
        {"seed_blocklist": [None]},
        {"seed_blocklist": ["a", [None]]},
        {"cache": None},
    ],
)
def test_null_values(overlay: dict[str, Any]) -> None:
    assert _merge_token(overlay) == "overlay_null_value"


def test_shape_mismatches() -> None:
    base = {"user_agents": ["a"], "promptguard_threshold": 0.85}
    assert _merge_token({"extraction": 5}) == "overlay_shape_mismatch"
    assert _merge_token({"promptguard_threshold": {"a": 1}}) == "overlay_shape_mismatch"
    assert _merge_token({"user_agents": "x"}, base) == "overlay_shape_mismatch"
    assert (
        _merge_token({"promptguard_threshold": [1]}, base) == "overlay_shape_mismatch"
    )
    assert (
        _merge_token({"cache": {"ttl_seconds": {"a": 1}}}) == "overlay_shape_mismatch"
    )


def test_list_rules_skip_none_or_absent_base() -> None:
    merged, _ = merge_config_overlay(
        {"seed_blocklist": None},
        {"seed_blocklist": [".a"], "user_agents": ["u"]},
        KNOWN,
    )
    assert merged["seed_blocklist"] == [".a"]
    assert merged["user_agents"] == ["u"]


def test_first_failing_key_in_sorted_order_decides() -> None:
    # "aaa_unknown" sorts before "promptguard_threshold" (null).
    overlay = {"promptguard_threshold": None, "aaa_unknown": 1}
    assert _merge_token(overlay) == "overlay_unknown_key"
    # name, then null, then shape within one key
    assert _merge_token({"cache": {"max_entries": None, "ttl_seconds": {}}}) == (
        "overlay_null_value"
    )


def test_inputs_not_mutated_and_output_independent() -> None:
    base = {"cache": {"ttl_seconds": 1}, "seed_blocklist": ["a"]}
    overlay = {"cache": {"max_entries": 2}, "news_domains": [".x"]}
    base_copy, overlay_copy = copy.deepcopy(base), copy.deepcopy(overlay)
    merged, _ = merge_config_overlay(base, overlay, KNOWN)
    assert base == base_copy
    assert overlay == overlay_copy
    merged["cache"]["ttl_seconds"] = 99
    merged["news_domains"].append(".y")
    merged["seed_blocklist"].append("b")
    assert base == base_copy
    assert overlay == overlay_copy
