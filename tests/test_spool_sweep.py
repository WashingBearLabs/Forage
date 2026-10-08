"""Startup sweep of stale spool files (`release-resource-bounds` US-004)."""

from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI

import retrieval_app
from pipeline import pdf_subprocess, worker_launch
from pipeline.extraction_limits import (
    MAX_EXTRACTION_WALL_SECONDS,
    ExtractionSettings,
    extraction_settings_from_config,
)
from pipeline.worker_launch import SPOOL_SWEEP_MARGIN_SECONDS, sweep_stale_spool
from retrieval_app import _spool_upload, lifespan

# The age gate: anything older than this is an orphan, anything younger is live.
_GATE = MAX_EXTRACTION_WALL_SECONDS + SPOOL_SWEEP_MARGIN_SECONDS
_AGED = _GATE + 30
_FRESH = 5


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real per-euid spool directory under a private ``TMPDIR``."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: dict[str, Any]())
    return worker_launch.spool_dir()


def _plant(directory: Path, name: str, *, age: float) -> Path:
    path = directory / name
    path.write_bytes(b"spooled content")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


async def _boot() -> None:
    async with lifespan(FastAPI()):
        pass


# --- prefixes ---------------------------------------------------------------


async def test_extract_upload_spool_prefix_is_forage_extract(
    spool: Path,
) -> None:
    class OneChunkUpload:
        def __init__(self) -> None:
            self._chunks = iter([b"abc"])

        async def read(self, size: int = -1) -> bytes:
            del size
            return next(self._chunks, b"")

    result = await _spool_upload(cast(Any, OneChunkUpload()), max_bytes=10)
    try:
        assert result.path.parent == spool
        assert result.path.name.startswith("forage-extract-")
        assert "poppy" not in result.path.name
    finally:
        result.path.unlink(missing_ok=True)


def test_pdf_worker_spool_prefix_is_forage_retrieve_pdf(spool: Path) -> None:
    assert pdf_subprocess._SPOOL_PREFIX == "forage-retrieve-pdf-"
    with worker_launch.spooled_bytes(
        b"%PDF-", prefix=pdf_subprocess._SPOOL_PREFIX
    ) as path:
        assert path.parent == spool
        assert path.name.startswith("forage-retrieve-pdf-")


# --- startup removes aged orphans --------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "forage-extract-abc.upload",
        "forage-retrieve-pdf-abc",
        "forage-retrieve-html-abc",
        "poppy-extract-abc.upload",
    ],
)
async def test_startup_removes_an_aged_prefixed_regular_file(
    spool: Path, name: str
) -> None:
    planted = _plant(spool, name, age=_AGED)

    await _boot()

    assert not planted.exists()


async def test_startup_keeps_a_fresh_prefixed_file(spool: Path) -> None:
    fresh = _plant(spool, "forage-extract-live.upload", age=_FRESH)

    await _boot()

    assert fresh.exists()


async def test_startup_keeps_a_symlink(spool: Path, tmp_path: Path) -> None:
    outside = _plant(tmp_path, "outside", age=_AGED)
    link = spool / "forage-retrieve-pdf-link"
    link.symlink_to(outside)
    stamp = time.time() - _AGED
    os.utime(link, (stamp, stamp), follow_symlinks=False)

    await _boot()

    assert link.is_symlink()
    assert outside.exists()


async def test_startup_keeps_a_foreign_prefix_file(spool: Path) -> None:
    foreign = _plant(spool, "other-tool-abc", age=_AGED)

    await _boot()

    assert foreign.exists()


async def test_startup_keeps_an_entry_in_a_subdirectory(spool: Path) -> None:
    subdirectory = spool / "forage-extract-dir"
    subdirectory.mkdir()
    nested = _plant(subdirectory, "forage-extract-nested.upload", age=_AGED)
    stamp = time.time() - _AGED
    os.utime(subdirectory, (stamp, stamp))

    await _boot()

    assert subdirectory.is_dir()
    assert nested.exists()


# --- dir_fd-relative unlink after a no-follow stat ---------------------------


def test_a_symlink_swapped_in_after_the_stat_never_redirects_the_unlink(
    spool: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam swaps the entry for a symlink to an outside file post-stat."""
    name = "forage-retrieve-pdf-raced"
    _plant(spool, name, age=_AGED)
    outside = _plant(tmp_path, "outside-target", age=_AGED)
    real_stat = os.stat
    calls: list[dict[str, Any]] = []

    def stat_then_swap(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)
        if path == name and "dir_fd" in kwargs:
            calls.append(kwargs)
            os.unlink(spool / name)
            os.symlink(outside, spool / name)
        return result

    monkeypatch.setattr(worker_launch.os, "stat", stat_then_swap)
    removed = sweep_stale_spool(
        spool, max_wall_clock_seconds=MAX_EXTRACTION_WALL_SECONDS
    )
    monkeypatch.undo()

    assert calls and calls[0]["follow_symlinks"] is False
    assert removed == 1
    assert outside.read_bytes() == b"spooled content"
    assert not os.path.lexists(spool / name)


@pytest.mark.parametrize(
    "error", [FileNotFoundError, IsADirectoryError, PermissionError]
)
def test_unlink_errors_are_swallowed_without_logging_a_name(
    spool: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: type[OSError],
) -> None:
    name = "forage-extract-undeletable.upload"
    planted = _plant(spool, name, age=_AGED)
    seen: list[dict[str, Any]] = []

    def failing_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        seen.append({"path": path, **kwargs})
        raise error(13, "refused", str(path))

    monkeypatch.setattr(worker_launch.os, "unlink", failing_unlink)
    with caplog.at_level(logging.DEBUG):
        removed = sweep_stale_spool(
            spool, max_wall_clock_seconds=MAX_EXTRACTION_WALL_SECONDS
        )
    monkeypatch.undo()

    assert removed == 0
    assert seen == [{"path": name, "dir_fd": seen[0]["dir_fd"]}]
    assert isinstance(seen[0]["dir_fd"], int)
    assert planted.exists()
    assert name not in caplog.text
    assert str(spool) not in caplog.text


# --- the age gate is the maximum permitted wall clock ------------------------


async def test_a_lowered_running_wall_clock_does_not_narrow_the_gate(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aged past running+60 but not past the bound+60: a sibling's live spool."""
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {"extraction": {"wall_clock_seconds": 10}},
    )
    survivor = _plant(
        spool, "forage-retrieve-pdf-sibling", age=10 + SPOOL_SWEEP_MARGIN_SECONDS + 40
    )

    await _boot()

    assert survivor.exists()


async def test_a_raised_running_wall_clock_does_not_widen_the_gate(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a running value above the bound leaves the gate at the bound."""

    def raised(config: dict[str, Any]) -> ExtractionSettings:
        return dataclasses.replace(
            extraction_settings_from_config(config), wall_clock_seconds=3600
        )

    monkeypatch.setattr(retrieval_app, "extraction_settings_from_config", raised)
    orphan = _plant(spool, "forage-retrieve-pdf-orphan", age=_AGED)

    await _boot()

    assert not orphan.exists()


# --- logging -----------------------------------------------------------------


async def test_the_lifespan_logs_one_closed_token_with_a_count(
    spool: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _plant(spool, "forage-extract-a.upload", age=_AGED)
    _plant(spool, "poppy-extract-b.upload", age=_AGED)
    _plant(spool, "forage-extract-live.upload", age=_FRESH)

    with caplog.at_level(logging.DEBUG):
        await _boot()

    sweeps = [
        record
        for record in caplog.records
        if record.getMessage().startswith("spool_sweep")
    ]
    assert [(r.name, r.getMessage()) for r in sweeps] == [
        ("retrieval_app", "spool_sweep removed=2")
    ]
    for fragment in ("forage-extract-", "poppy-extract-", str(spool)):
        assert fragment not in caplog.text
