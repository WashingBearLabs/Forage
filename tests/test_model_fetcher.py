"""Integrity verification and quarantine for runtime-fetched weights.

``feature-forage-model-bootstrap`` US-002. The story is fixture-driven on
purpose: the real manifest arrives with US-003's vendoring run, and the
verifier must be provably correct before any of the three acquisition paths
(HF, mirror, warm start) is wired to it.

What is under test, in the order the module applies it:

* the manifest **fails closed** — absent, empty, unparseable and
  schema-invalid are refusals, never "nothing to verify";
* the file set is **exact** — missing, extra and mismatched are all refusals;
* the format **allowlist** admits safetensors, tokenizer and config files and
  nothing else, at the manifest layer as well as on disk, with
  ``use_safetensors=True`` pinned at the loader as the second lock;
* the walk hashes **the bytes the loader opens**, resolving Hugging Face's
  ``blobs/`` symlinks and refusing any link that leaves the model directory;
* a refused set is **quarantined** outside the loader's scan tree, bounded to
  one generation;
* the ``model.*`` counters move.

test_mapping:
  model_fetcher.py: tests/test_model_fetcher.py
  weights_manifest.json: tests/test_model_fetcher.py
  promptguard/classifier.py: tests/test_model_fetcher.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import model_fetcher
from model_fetcher import (
    ALLOW_PATTERNS,
    ALLOWED_SUFFIXES,
    MANIFEST_PATH,
    QUARANTINE_DIRNAME,
    REASON_DISALLOWED_ENTRY,
    REASON_DISALLOWED_FORMAT,
    REASON_FILE_EXTRA,
    REASON_FILE_MISSING,
    REASON_HASH_MISMATCH,
    REASON_MANIFEST_DISALLOWED_FORMAT,
    REASON_MANIFEST_EMPTY,
    REASON_MANIFEST_INVALID,
    REASON_MANIFEST_MISSING,
    REASON_MANIFEST_UNPARSEABLE,
    REASON_SIZE_MISMATCH,
    REASON_SNAPSHOT_MISSING,
    REASON_SYMLINK_ESCAPE,
    ModelMetrics,
    is_allowed_filename,
    quarantine_root,
    repo_dirname,
    snapshot_path,
    verify_weights,
)
from promptguard.classifier import MODEL_ID, PromptGuardClassifier

_REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MODEL_DIR = Path(__file__).resolve().parent / "fixtures" / "tiny_model"

_MODEL_ID = "acme/tiny-guard"
_REVISION = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
_FILES: Mapping[str, bytes] = {
    "config.json": b'{"model_type": "deberta-v2", "num_labels": 2}',
    "model.safetensors": b"\x00pretend-safetensors-payload\x00" * 8,
    "tokenizer.json": b'{"version": "1.0"}',
}


# ---------------------------------------------------------------------------
# Helpers — a real Hugging Face cache layout, built from bytes
# ---------------------------------------------------------------------------


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _materialize(
    cache_root: Path,
    files: Mapping[str, bytes] = _FILES,
    *,
    model_id: str = _MODEL_ID,
    revision: str = _REVISION,
    symlinks: bool = True,
) -> Path:
    """Build ``hub/models--…/{blobs,snapshots/<rev>}`` the way the hub does.

    ``symlinks=True`` is the real shape: content lives in ``blobs/`` and the
    snapshot is a directory of links into it. The flat form exists so a test
    can prove the walk handles both.
    """
    repo = cache_root / "hub" / repo_dirname(model_id)
    blobs = repo / "blobs"
    snapshot = repo / "snapshots" / revision
    blobs.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        link = snapshot / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if symlinks:
            blob = blobs / _sha256(payload)
            blob.write_bytes(payload)
            link.symlink_to(os.path.relpath(blob, link.parent))
        else:
            link.write_bytes(payload)
    return snapshot


def _manifest_document(
    files: Mapping[str, bytes] = _FILES,
    *,
    model_id: str = _MODEL_ID,
    revision: str = _REVISION,
) -> dict[str, Any]:
    """The manifest that exactly describes *files*."""
    return {
        "model_id": model_id,
        "revision": revision,
        "files": [
            {"path": name, "sha256": _sha256(payload), "size": len(payload)}
            for name, payload in sorted(files.items())
        ],
    }


def _write_manifest(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _cache_with_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    """A cache root holding the canonical good snapshot, and its manifest."""
    cache_root = tmp_path / "model-cache"
    _materialize(cache_root)
    return cache_root, _write_manifest(tmp_path, _manifest_document())


# ---------------------------------------------------------------------------
# The manifest fails closed
# ---------------------------------------------------------------------------


class TestManifestFailsClosed:
    """A manifest that cannot be trusted is a refusal, never a free pass."""

    def test_absent_manifest_is_a_verification_failure(self, tmp_path: Path) -> None:
        cache_root, _ = _cache_with_snapshot(tmp_path)
        metrics = ModelMetrics()

        result = verify_weights(
            cache_root,
            manifest_path=tmp_path / "does-not-exist.json",
            metrics=metrics,
        )

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_MISSING,)
        assert metrics.verify_failures == 1

    def test_empty_manifest_file_is_a_verification_failure(
        self, tmp_path: Path
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        manifest.write_text("   \n", encoding="utf-8")

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert REASON_MANIFEST_EMPTY in result.reasons

    def test_manifest_pinning_no_files_is_a_verification_failure(
        self, tmp_path: Path
    ) -> None:
        """An empty file list would "verify" an empty snapshot. Refuse it."""
        cache_root, _ = _cache_with_snapshot(tmp_path)
        manifest = _write_manifest(
            tmp_path, {"model_id": _MODEL_ID, "revision": _REVISION, "files": []}
        )

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert REASON_MANIFEST_EMPTY in result.reasons

    def test_unparseable_manifest_is_a_verification_failure(
        self, tmp_path: Path
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        manifest.write_text('{"model_id": "acme/tiny-guard",', encoding="utf-8")

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_UNPARSEABLE,)

    def test_manifest_that_is_not_an_object_is_a_verification_failure(
        self, tmp_path: Path
    ) -> None:
        cache_root, _ = _cache_with_snapshot(tmp_path)
        manifest = _write_manifest(tmp_path, ["model.safetensors"])

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_INVALID,)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("model_id", "../../../etc"),
            ("model_id", ""),
            ("model_id", 7),
            ("revision", "main"),
            ("revision", "A1B2C3D4E5F60718293A4B5C6D7E8F9012345678"),
            ("revision", "../escape"),
        ],
    )
    def test_manifest_header_is_validated(
        self, tmp_path: Path, key: str, value: object
    ) -> None:
        """A movable or traversing pin never reaches the filesystem."""
        cache_root, _ = _cache_with_snapshot(tmp_path)
        document = _manifest_document()
        document[key] = value
        manifest = _write_manifest(tmp_path, document)

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_INVALID,)

    @pytest.mark.parametrize(
        "entry",
        [
            {"path": "model.safetensors", "sha256": "not-hex", "size": 4},
            {"path": "model.safetensors", "sha256": "ab" * 32, "size": -1},
            {"path": "model.safetensors", "sha256": "ab" * 32, "size": "4"},
            {"path": "model.safetensors", "sha256": "ab" * 32},
            {"path": "../escape.json", "sha256": "ab" * 32, "size": 4},
            {"path": "/abs.json", "sha256": "ab" * 32, "size": 4},
            {"path": "sub/../escape.json", "sha256": "ab" * 32, "size": 4},
            {"path": "", "sha256": "ab" * 32, "size": 4},
            "model.safetensors",
        ],
    )
    def test_manifest_entries_are_validated(
        self, tmp_path: Path, entry: object
    ) -> None:
        cache_root, _ = _cache_with_snapshot(tmp_path)
        manifest = _write_manifest(
            tmp_path,
            {"model_id": _MODEL_ID, "revision": _REVISION, "files": [entry]},
        )

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_INVALID,)

    def test_duplicate_manifest_paths_are_rejected(self, tmp_path: Path) -> None:
        cache_root, _ = _cache_with_snapshot(tmp_path)
        document = _manifest_document()
        entries = cast(list[dict[str, Any]], document["files"])
        entries.append(dict(entries[0]))
        manifest = _write_manifest(tmp_path, document)

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_INVALID,)

    def test_a_manifest_failure_never_quarantines(self, tmp_path: Path) -> None:
        """Our bug must not destroy a 270 MiB download that may be perfect."""
        cache_root, _ = _cache_with_snapshot(tmp_path)
        metrics = ModelMetrics()

        result = verify_weights(
            cache_root,
            manifest_path=tmp_path / "missing.json",
            metrics=metrics,
        )

        assert result.quarantined_to is None
        assert metrics.quarantines == 0
        assert quarantine_root(cache_root).exists() is False
        assert snapshot_path(cache_root, _MODEL_ID, _REVISION).is_dir()


# ---------------------------------------------------------------------------
# Exact-set verification
# ---------------------------------------------------------------------------


class TestExactSetVerification:
    """Missing, extra and mismatched are the same answer: refuse."""

    def test_matching_snapshot_verifies(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        metrics = ModelMetrics()

        result = verify_weights(cache_root, manifest_path=manifest, metrics=metrics)

        assert result.ok is True
        assert result.failures == ()
        assert result.quarantined_to is None
        assert result.snapshot_dir == snapshot_path(cache_root, _MODEL_ID, _REVISION)
        assert result.manifest is not None
        assert result.manifest.total_bytes == sum(len(v) for v in _FILES.values())
        assert (metrics.verify_failures, metrics.quarantines) == (0, 0)

    def test_a_flat_snapshot_without_symlinks_also_verifies(
        self, tmp_path: Path
    ) -> None:
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root, symlinks=False)

        result = verify_weights(
            cache_root, manifest_path=_write_manifest(tmp_path, _manifest_document())
        )

        assert result.ok is True

    def test_missing_file_is_refused(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        (snapshot_path(cache_root, _MODEL_ID, _REVISION) / "tokenizer.json").unlink()

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_FILE_MISSING,)
        assert [failure.path for failure in result.failures] == ["tokenizer.json"]

    def test_extra_file_is_refused(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        snapshot = snapshot_path(cache_root, _MODEL_ID, _REVISION)
        (snapshot / "extra_adapter.safetensors").write_bytes(b"surprise")

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_FILE_EXTRA,)
        assert [failure.path for failure in result.failures] == [
            "extra_adapter.safetensors"
        ]

    def test_hash_mismatch_is_refused(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        payload = _FILES["model.safetensors"]
        blob = cache_root / "hub" / repo_dirname(_MODEL_ID) / "blobs" / _sha256(payload)
        blob.write_bytes(b"\x01" * len(payload))

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_HASH_MISMATCH,)
        assert [failure.path for failure in result.failures] == ["model.safetensors"]

    def test_size_mismatch_is_refused(self, tmp_path: Path) -> None:
        """A manifest whose size disagrees with reality is a refusal on its own."""
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root)
        document = _manifest_document()
        entries = cast(list[dict[str, Any]], document["files"])
        for entry in entries:
            if entry["path"] == "config.json":
                entry["size"] = cast(int, entry["size"]) + 1
        manifest = _write_manifest(tmp_path, document)

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_SIZE_MISMATCH,)

    def test_absent_snapshot_is_refused_and_nothing_is_quarantined(
        self, tmp_path: Path
    ) -> None:
        """Cold start: refuse, but there is nothing to move aside."""
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()
        metrics = ModelMetrics()

        result = verify_weights(
            cache_root,
            manifest_path=_write_manifest(tmp_path, _manifest_document()),
            metrics=metrics,
        )

        assert result.ok is False
        assert result.reasons == (REASON_SNAPSHOT_MISSING,)
        assert result.quarantined_to is None
        assert metrics.quarantines == 0

    def test_every_failure_is_reported_not_just_the_first(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        snapshot = snapshot_path(cache_root, _MODEL_ID, _REVISION)
        (snapshot / "tokenizer.json").unlink()
        (snapshot / "extra.json").write_bytes(b"{}")
        payload = _FILES["config.json"]
        blob = cache_root / "hub" / repo_dirname(_MODEL_ID) / "blobs" / _sha256(payload)
        blob.write_bytes(b"tampered")

        result = verify_weights(cache_root, manifest_path=manifest)

        assert set(result.reasons) == {
            REASON_FILE_EXTRA,
            REASON_FILE_MISSING,
            REASON_SIZE_MISMATCH,
            REASON_HASH_MISMATCH,
        }

    def test_unreferenced_blobs_are_not_part_of_the_set(self, tmp_path: Path) -> None:
        """The walk covers ``snapshots/<rev>/``, not the whole cache tree."""
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        repo = cache_root / "hub" / repo_dirname(_MODEL_ID)
        (repo / "blobs" / "orphaned-blob").write_bytes(b"left over")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text(_REVISION, encoding="utf-8")

        assert verify_weights(cache_root, manifest_path=manifest).ok is True

    def test_nested_snapshot_files_are_verified(self, tmp_path: Path) -> None:
        files = dict(_FILES)
        files["nested/extra_config.json"] = b'{"nested": true}'
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root, files)

        assert (
            verify_weights(
                cache_root,
                manifest_path=_write_manifest(tmp_path, _manifest_document(files)),
            ).ok
            is True
        )


# ---------------------------------------------------------------------------
# Safetensors-only allowlist
# ---------------------------------------------------------------------------


class TestFormatAllowlist:
    """The RCE closure: `torch.load` never gets a file to open."""

    @pytest.mark.parametrize(
        "name",
        [
            "model.safetensors",
            "config.json",
            "tokenizer_config.json",
            "vocab.txt",
            "merges.txt",
            "spm.model",
        ],
    )
    def test_allowlisted_names(self, name: str) -> None:
        assert is_allowed_filename(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "pytorch_model.bin",
            "model.pt",
            "weights.pth",
            "checkpoint.ckpt",
            "state.pkl",
            "flax_model.msgpack",
            "tf_model.h5",
            "README.md",
            ".gitattributes",
            "run.sh",
            "malicious",
        ],
    )
    def test_refused_names(self, name: str) -> None:
        assert is_allowed_filename(name) is False

    def test_a_pickle_on_disk_is_refused_by_format_not_merely_as_an_extra(
        self, tmp_path: Path
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        snapshot = snapshot_path(cache_root, _MODEL_ID, _REVISION)
        (snapshot / "pytorch_model.bin").write_bytes(b"\x80\x04pickle")

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_DISALLOWED_FORMAT,)
        assert [failure.path for failure in result.failures] == ["pytorch_model.bin"]

    def test_a_manifest_that_blesses_a_pickle_cannot_be_honoured(
        self, tmp_path: Path
    ) -> None:
        """Even a hash-perfect ``.bin`` fails — the allowlist is upstream of trust."""
        files = dict(_FILES)
        files["pytorch_model.bin"] = b"\x80\x04pickle"
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root, files)

        result = verify_weights(
            cache_root,
            manifest_path=_write_manifest(tmp_path, _manifest_document(files)),
        )

        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_DISALLOWED_FORMAT,)

    def test_allow_patterns_mirror_the_suffix_allowlist(self) -> None:
        """One constant drives both the fetch filter and the verification."""
        assert set(ALLOW_PATTERNS) == {f"*{suffix}" for suffix in ALLOWED_SUFFIXES}
        assert ".bin" not in ALLOWED_SUFFIXES

    def test_the_loader_is_pinned_to_safetensors(self) -> None:
        """`from_pretrained` must never be able to fall back to `torch.load`."""
        with (
            patch("transformers.AutoTokenizer") as tokenizer_class,
            patch("transformers.AutoModelForSequenceClassification") as model_class,
        ):
            assert PromptGuardClassifier().load() is True
            model_call = cast(MagicMock, model_class.from_pretrained).call_args
            tokenizer_call = cast(MagicMock, tokenizer_class.from_pretrained).call_args

        assert cast(dict[str, Any], model_call.kwargs)["use_safetensors"] is True
        assert cast(tuple[Any, ...], model_call.args)[0] == MODEL_ID
        assert cast(tuple[Any, ...], tokenizer_call.args)[0] == MODEL_ID

    def test_the_loader_posture_is_stated_in_the_source(self) -> None:
        """A canary for a future edit that drops the keyword without noticing.

        AST-based on purpose: a plain substring grep was satisfied by the
        *comment* explaining the posture, so removing the real keyword left
        it green (US-002 verification finding). This walks the call nodes.
        """
        source = (_REPO_ROOT / "promptguard" / "classifier.py").read_text()
        tree = ast.parse(source)
        posture_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and any(
                kw.arg == "use_safetensors"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
        ]
        assert posture_calls, (
            "No call in promptguard/classifier.py passes use_safetensors=True "
            "— the loader can fall back to torch.load pickle deserialization"
        )


# ---------------------------------------------------------------------------
# The bytes the loader opens
# ---------------------------------------------------------------------------


class TestSymlinkResolution:
    """HF stores content in ``blobs/`` — a naive flat walk hashes the wrong thing."""

    def test_the_snapshot_is_a_tree_of_symlinks(self, tmp_path: Path) -> None:
        cache_root, _ = _cache_with_snapshot(tmp_path)
        snapshot = snapshot_path(cache_root, _MODEL_ID, _REVISION)

        assert (snapshot / "model.safetensors").is_symlink()

    def test_tampering_with_the_blob_is_detected_through_the_link(
        self, tmp_path: Path
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        payload = _FILES["tokenizer.json"]
        blob = cache_root / "hub" / repo_dirname(_MODEL_ID) / "blobs" / _sha256(payload)
        blob.write_bytes(b'{"version": "9.9"}')

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert REASON_HASH_MISMATCH in result.reasons

    def test_a_symlink_out_of_the_model_directory_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A hostile mirror tarball's shape — never hash or load what it points at."""
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.json"
        secret.write_text('{"stolen": true}', encoding="utf-8")
        link = snapshot_path(cache_root, _MODEL_ID, _REVISION) / "config.json"
        link.unlink()
        link.symlink_to(secret)

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert REASON_SYMLINK_ESCAPE in result.reasons
        assert REASON_FILE_MISSING in result.reasons

    def test_a_broken_symlink_is_a_missing_file(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        snapshot = snapshot_path(cache_root, _MODEL_ID, _REVISION)
        blob = (snapshot / "config.json").resolve()
        blob.unlink()

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_FILE_MISSING,)

    def test_a_directory_symlink_is_refused_rather_than_followed(
        self, tmp_path: Path
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        snapshot = snapshot_path(cache_root, _MODEL_ID, _REVISION)
        (snapshot / "loop").symlink_to(
            os.path.relpath(
                cache_root / "hub" / repo_dirname(_MODEL_ID) / "blobs", snapshot
            )
        )

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert REASON_DISALLOWED_ENTRY in result.reasons


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


class TestQuarantine:
    """Refused weights leave the loader's tree, and never accumulate."""

    def test_a_refused_set_is_moved_out_of_the_scan_tree(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        (snapshot_path(cache_root, _MODEL_ID, _REVISION) / "config.json").unlink()
        metrics = ModelMetrics()

        result = verify_weights(cache_root, manifest_path=manifest, metrics=metrics)

        destination = quarantine_root(cache_root) / repo_dirname(_MODEL_ID)
        assert result.quarantined_to == destination
        assert destination.is_dir()
        assert (cache_root / "hub" / repo_dirname(_MODEL_ID)).exists() is False
        assert metrics.quarantines == 1
        assert metrics.verify_failures == 1

    def test_the_quarantine_is_outside_the_hub_tree(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "model-cache"
        hub = cache_root / "hub"

        assert quarantine_root(cache_root).name == QUARANTINE_DIRNAME
        assert hub not in quarantine_root(cache_root).parents
        assert quarantine_root(cache_root) != hub

    def test_quarantine_is_bounded_to_one_generation(self, tmp_path: Path) -> None:
        """Two ~270 MiB generations would fill the reference container's volume."""
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        metrics = ModelMetrics()

        (snapshot_path(cache_root, _MODEL_ID, _REVISION) / "config.json").unlink()
        verify_weights(cache_root, manifest_path=manifest, metrics=metrics)
        marker = quarantine_root(cache_root) / repo_dirname(_MODEL_ID) / "generation-1"
        marker.write_text("first", encoding="utf-8")

        _materialize(cache_root)
        (snapshot_path(cache_root, _MODEL_ID, _REVISION) / "tokenizer.json").unlink()
        verify_weights(cache_root, manifest_path=manifest, metrics=metrics)

        assert sorted(p.name for p in quarantine_root(cache_root).iterdir()) == [
            repo_dirname(_MODEL_ID)
        ]
        assert marker.exists() is False
        assert metrics.quarantines == 2

    def test_a_quarantine_failure_is_reported_not_swallowed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        (snapshot_path(cache_root, _MODEL_ID, _REVISION) / "config.json").unlink()
        metrics = ModelMetrics()

        with (
            caplog.at_level("ERROR", logger="model_fetcher"),
            patch.object(model_fetcher.shutil, "move", side_effect=OSError("busy")),
        ):
            result = verify_weights(cache_root, manifest_path=manifest, metrics=metrics)

        assert result.ok is False
        assert result.quarantined_to is None
        assert metrics.quarantines == 0
        assert "weights_quarantine_failed" in caplog.text

    def test_a_refusal_is_logged_loudly_with_its_reasons(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)
        (snapshot_path(cache_root, _MODEL_ID, _REVISION) / "config.json").unlink()

        with caplog.at_level("ERROR", logger="model_fetcher"):
            verify_weights(cache_root, manifest_path=manifest)

        assert "weights_verification_failed" in caplog.text
        assert REASON_FILE_MISSING in caplog.text


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


class TestModelMetrics:
    """The three counters `/metrics` exposes under ``model``."""

    def test_counters_start_at_zero(self) -> None:
        metrics = ModelMetrics()

        assert (metrics.fetch_failures, metrics.verify_failures) == (0, 0)
        assert metrics.quarantines == 0
        assert metrics.fetch_in_progress is False

    def test_each_counter_records_independently(self) -> None:
        metrics = ModelMetrics()
        metrics.record_fetch_failure()
        metrics.record_verify_failure()
        metrics.record_verify_failure()
        metrics.record_quarantine()

        assert metrics.fetch_failures == 1
        assert metrics.verify_failures == 2
        assert metrics.quarantines == 1

    def test_verify_weights_without_metrics_still_verifies(
        self, tmp_path: Path
    ) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)

        assert verify_weights(cache_root, manifest_path=manifest).ok is True


# ---------------------------------------------------------------------------
# One entry point, one production path
# ---------------------------------------------------------------------------


class TestSingleEntryPoint:
    """The seam US-001, US-004 and US-005 wire into."""

    def test_the_default_manifest_path_is_the_committed_constant(
        self, tmp_path: Path
    ) -> None:
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()

        with patch.object(model_fetcher, "_load_manifest") as loader:
            loader.return_value = (None, ())
            verify_weights(cache_root)

        assert cast(MagicMock, loader).call_args.args[0] == MANIFEST_PATH

    def test_the_production_manifest_path_is_not_configurable(self) -> None:
        """The injection point is for fixtures; production reads one constant."""
        source = (_REPO_ROOT / "model_fetcher.py").read_text()

        assert "os.environ" not in source
        assert "getenv" not in source
        assert MANIFEST_PATH == _REPO_ROOT / "weights_manifest.json"

    def test_snapshot_path_matches_the_hub_cache_layout(self) -> None:
        """``HF_HUB_CACHE`` is ``$HF_HOME/hub`` — one directory below the volume."""
        path = snapshot_path("/app/model-cache", MODEL_ID, "0" * 40)

        assert path == Path(
            "/app/model-cache/hub/models--meta-llama--Llama-Prompt-Guard-2-22M"
            "/snapshots/" + "0" * 40
        )

    def test_string_and_path_cache_roots_are_equivalent(self, tmp_path: Path) -> None:
        cache_root, manifest = _cache_with_snapshot(tmp_path)

        assert verify_weights(str(cache_root), manifest_path=str(manifest)).ok is True


# ---------------------------------------------------------------------------
# The committed manifest
# ---------------------------------------------------------------------------


class TestCommittedManifest:
    """`weights_manifest.json` at the repo root — shipped in the image."""

    def test_it_is_committed_and_parses(self) -> None:
        document = cast(
            dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )

        assert document["model_id"] == MODEL_ID
        assert isinstance(document["revision"], str)
        assert len(document["revision"]) == 40
        assert isinstance(document["files"], list)

    def test_it_is_still_the_interim_placeholder_and_fails_closed(
        self, tmp_path: Path
    ) -> None:
        """US-003 replaces this file; delete this test in the same commit.

        Until then the honest interim behaviour is a refusal with
        ``manifest_empty`` — degraded, loud, and never "nothing to verify".
        Nothing calls the verifier at boot yet (US-001 wires it), so a stock
        image still reports exactly what it reports today.
        """
        document = cast(
            dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()

        result = verify_weights(cache_root)

        assert document["files"] == []
        assert result.ok is False
        assert result.reasons == (REASON_MANIFEST_EMPTY,)


# ---------------------------------------------------------------------------
# The loadable fixture
# ---------------------------------------------------------------------------


class TestLoadableFixture:
    """A real minimal model, so the loader itself is exercised — not a mock."""

    def test_every_fixture_file_is_allowlisted(self) -> None:
        names = sorted(p.name for p in FIXTURE_MODEL_DIR.iterdir() if p.is_file())

        assert names == [
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
        ]
        assert all(is_allowed_filename(name) for name in names)

    def test_it_loads_with_use_safetensors_and_no_network(self) -> None:
        """The autouse socket guard is the proof that nothing reached the hub."""
        model = AutoModelForSequenceClassification.from_pretrained(
            FIXTURE_MODEL_DIR,
            use_safetensors=True,
            local_files_only=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            FIXTURE_MODEL_DIR, local_files_only=True
        )
        model.eval()
        outputs = model(**tokenizer("tok3 tok4", return_tensors="pt", padding=True))

        assert outputs.logits.shape[-1] == 2

    def test_it_verifies_in_a_real_cache_layout(self, tmp_path: Path) -> None:
        """The US-004 seam: verified bytes, then a load, from one snapshot."""
        files = {
            path.name: path.read_bytes()
            for path in sorted(FIXTURE_MODEL_DIR.iterdir())
            if path.is_file()
        }
        cache_root = tmp_path / "model-cache"
        snapshot = _materialize(cache_root, files)
        manifest = _write_manifest(tmp_path, _manifest_document(files))

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is True
        assert result.snapshot_dir == snapshot
        model = AutoModelForSequenceClassification.from_pretrained(
            snapshot, use_safetensors=True, local_files_only=True
        )

        assert model.eval() is not None

    def test_a_tampered_fixture_weight_is_refused_before_it_can_load(
        self, tmp_path: Path
    ) -> None:
        files = {
            path.name: path.read_bytes()
            for path in sorted(FIXTURE_MODEL_DIR.iterdir())
            if path.is_file()
        }
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root, files)
        manifest = _write_manifest(tmp_path, _manifest_document(files))
        blob = (
            cache_root
            / "hub"
            / repo_dirname(_MODEL_ID)
            / "blobs"
            / _sha256(files["model.safetensors"])
        )
        blob.write_bytes(b"\x00" * len(files["model.safetensors"]))

        result = verify_weights(cache_root, manifest_path=manifest)

        assert result.ok is False
        assert result.reasons == (REASON_HASH_MISMATCH,)
        assert result.quarantined_to is not None
