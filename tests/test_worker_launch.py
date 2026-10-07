"""Tests for the shared extraction-worker launcher (release-resource-bounds US-002)."""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pipeline import worker_launch
from pipeline.extraction_limits import ExtractionSettings
from pipeline.pdf_subprocess import extract_pdf_bytes_in_subprocess
from pipeline.stage1_pdf import PDFExtractionError
from tests.conftest import _CLEARED_ENV_VARS
from tests.test_stage1_pdf import _make_text_pdf

# Names the platform adds to a child on its own; not part of the allowlist.
_PLATFORM_INJECTED = frozenset({"__CF_USER_TEXT_ENCODING"})
_SECRET_PATTERNS = ("*_API_KEY", "*_TOKEN", "*_SECRET", "*_PASSWORD")
_SENTINEL_PATTERN_NAMES = (
    "SOMETHING_API_KEY",
    "SOMETHING_TOKEN",
    "SOMETHING_SECRET",
    "SOMETHING_PASSWORD",
)
_PERMITTED = frozenset(worker_launch.WORKER_ENV_ALLOWLIST) | _PLATFORM_INJECTED


def _is_excluded(name: str) -> bool:
    return name in _CLEARED_ENV_VARS or any(
        fnmatch.fnmatch(name, pattern) for pattern in _SECRET_PATTERNS
    )


@pytest.fixture
def spool_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import tempfile

    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(spool))
    return spool


def _install_environment_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make every worker dump its environment as it starts, via ``sitecustomize``.

    ``PYTHONPATH`` is allowlisted, so the probe reaches the child without a
    production seam; it runs before ``pipeline.worker_entry`` does anything.
    """
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    dump = tmp_path / "environment.json"
    (probe_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            f"""
            import json, os
            record = {{"environ": sorted(os.environ)}}
            try:
                with open("/proc/self/environ", "rb") as source:
                    record["proc"] = sorted(
                        entry.split(b"=", 1)[0].decode()
                        for entry in source.read().split(b"\\0")
                        if entry
                    )
            except OSError:
                record["proc"] = None
            with open({str(dump)!r}, "w") as sink:
                json.dump(record, sink)
            """
        )
    )
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([str(probe_dir), *([existing] if existing else [])]),
    )
    return dump


class TestWorkerEnvironment:
    def test_only_allowlisted_names_are_copied(self) -> None:
        parent = {name: "x" for name in _CLEARED_ENV_VARS}
        parent.update({"PATH": "/bin", "LANG": "C", "SOMETHING_TOKEN": "x"})

        assert worker_launch.worker_environment(parent) == {"PATH": "/bin", "LANG": "C"}

    def test_the_allowlist_holds_no_excluded_name(self) -> None:
        assert not [n for n in worker_launch.WORKER_ENV_ALLOWLIST if _is_excluded(n)]

    def _run_probed_worker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> dict[str, list[str] | None]:
        dump = _install_environment_probe(tmp_path, monkeypatch)
        for name in (*_CLEARED_ENV_VARS, *_SENTINEL_PATTERN_NAMES):
            monkeypatch.setenv(name, "sentinel-not-a-secret")

        result = extract_pdf_bytes_in_subprocess(
            _make_text_pdf(["Parsed under a stripped environment."]),
            ExtractionSettings(),
        )

        # The parsers imported and ran under the stripped environment.
        assert "Parsed under a stripped environment." in result.raw_text
        return json.loads(dump.read_text())

    def test_a_real_worker_sees_no_credential_and_still_imports_its_parsers(
        self, tmp_path: Path, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._run_probed_worker(tmp_path, monkeypatch)

        assert seen["environ"] is not None
        assert set(seen["environ"]) <= _PERMITTED
        assert not [n for n in seen["environ"] if _is_excluded(n)]

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="/proc/self/environ is Linux-only"
    )
    def test_the_workers_proc_environ_holds_no_excluded_name(
        self, tmp_path: Path, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._run_probed_worker(tmp_path, monkeypatch)

        assert seen["proc"] is not None
        assert set(seen["proc"]) <= _PERMITTED
        assert not [n for n in seen["proc"] if _is_excluded(n)]


class TestWorkingDirectory:
    def test_a_worker_launches_while_the_parent_is_in_an_unrelated_directory(
        self, tmp_path: Path, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.delenv("PYTHONPATH", raising=False)

        result = extract_pdf_bytes_in_subprocess(
            _make_text_pdf(["Launched from elsewhere."]), ExtractionSettings()
        )

        assert "Launched from elsewhere." in result.raw_text

    def test_the_project_root_holds_the_pipeline_package(self) -> None:
        assert (worker_launch.project_root() / "pipeline" / "worker_entry.py").is_file()


class TestSpawnFailure:
    def test_a_popen_oserror_maps_to_the_pdf_worker_failure(
        self, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_args: object, **_kwargs: object) -> None:
            raise BlockingIOError("EAGAIN")

        monkeypatch.setattr(subprocess, "Popen", refuse)

        with pytest.raises(PDFExtractionError, match="worker failed"):
            extract_pdf_bytes_in_subprocess(_make_text_pdf(["x"]), ExtractionSettings())

        spool_files = list((spool_root / f"forage-spool-{os.geteuid()}").iterdir())
        assert spool_files == []


class TestFrames:
    def test_an_oversize_body_becomes_a_failed_frame(self) -> None:
        frame = worker_launch.encode_frame(
            {"status": "ok", "raw_text": "x" * 100}, ensure_ascii=True, max_bytes=20
        )

        assert frame[4:] == b'{"status":"failed"}'
        assert int.from_bytes(frame[:4], "big") == len(frame) - 4

    def test_ensure_ascii_is_a_parameter(self) -> None:
        payload = {"raw_text": "é"}

        ascii_frame = worker_launch.encode_frame(
            payload, ensure_ascii=True, max_bytes=99
        )
        utf8_frame = worker_launch.encode_frame(
            payload, ensure_ascii=False, max_bytes=99
        )

        assert b"\\u00e9" in ascii_frame
        assert "é".encode() in utf8_frame
