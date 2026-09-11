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
import io
import json
import os
import random
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import model_fetcher
from model_fetcher import (
    ACQUISITION_SOURCES,
    ALLOW_PATTERNS,
    ALLOWED_SUFFIXES,
    CACHE_ROOT_ENV_VAR,
    DEFAULT_CACHE_ROOT,
    DEFAULT_MODEL_REVISION,
    DEFAULT_WEIGHTS_MIRROR,
    HF_TOKEN_ENV_VAR,
    MANIFEST_PATH,
    MIRROR_ENV_VAR,
    MIRROR_TOKEN_ENV_VAR,
    MIRROR_USERNAME,
    MODEL_REVISION_ENV_VAR,
    ORAS_BINARY,
    ORAS_TIMEOUT_S,
    OUTCOME_ARTIFACT_OVERSIZED,
    OUTCOME_EXTRACT_FAILED,
    OUTCOME_INSUFFICIENT_SPACE,
    OUTCOME_MISCONFIGURED,
    OUTCOME_NO_ARTIFACT,
    OUTCOME_PULL_FAILED,
    OUTCOME_REFUSED,
    OUTCOME_SKIPPED_NO_TOKEN,
    OUTCOME_TIMEOUT,
    OUTCOME_TOOL_MISSING,
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
    SOURCE_HUGGINGFACE,
    SOURCE_MIRROR,
    XET_DIRNAME,
    ModelMetrics,
    acquire_and_load,
    hub_cache_dir,
    is_allowed_filename,
    mirror_reference,
    oras_pull_argv,
    quarantine_root,
    read_manifest_pin,
    redact_reference,
    repo_dirname,
    resolve_cache_root,
    resolve_mirror_repository,
    resolve_revision,
    snapshot_path,
    staging_cap_bytes,
    staging_root,
    verify_weights,
)
from promptguard.classifier import MODEL_ID, PromptGuardClassifier
from scripts.vendor_weights import build_tarball
from tests.fakes import (
    hub_download_double,
    materialize_hub_snapshot,
    sha256_hex,
    weights_manifest_document,
)

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


_sha256 = sha256_hex


def _materialize(
    cache_root: Path,
    files: Mapping[str, bytes] = _FILES,
    *,
    model_id: str = _MODEL_ID,
    revision: str = _REVISION,
    symlinks: bool = True,
) -> Path:
    """This module's defaults over the shared hub-layout builder."""
    return materialize_hub_snapshot(
        cache_root,
        files,
        model_id=model_id,
        revision=revision,
        symlinks=symlinks,
    )


def _manifest_document(
    files: Mapping[str, bytes] = _FILES,
    *,
    model_id: str = _MODEL_ID,
    revision: str = _REVISION,
) -> dict[str, Any]:
    """The manifest that exactly describes *files*."""
    return weights_manifest_document(files, model_id=model_id, revision=revision)


def _write_manifest(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _pinless_manifest(tmp_path: Path) -> Path:
    """A manifest that parses and pins the real model but blesses no file.

    The shape of the committed placeholder, as a **fixture**. These tests used
    to drive the manifest-empty branch with ``MANIFEST_PATH`` itself, which
    tied them to an interim state US-003's supervised ops commit replaces — an
    ops commit whose whole job is swapping that one file has no business also
    editing tests. The branch under test is "a manifest that blesses nothing",
    and that is what this builds.
    """
    return _write_manifest(
        tmp_path,
        {"model_id": MODEL_ID, "revision": DEFAULT_MODEL_REVISION, "files": []},
    )


def _cache_with_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    """A cache root holding the canonical good snapshot, and its manifest."""
    cache_root = tmp_path / "model-cache"
    _materialize(cache_root)
    return cache_root, _write_manifest(tmp_path, _manifest_document())


# ---------------------------------------------------------------------------
# Helpers — the acquisition pipeline (US-001)
# ---------------------------------------------------------------------------


def _reads_the_environment(node: ast.Call) -> bool:
    """Whether *node* is ``os.environ.get(...)`` or ``os.getenv(...)``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv":
        return isinstance(func.value, ast.Name) and func.value.id == "os"
    if func.attr != "get":
        return False
    owner = func.value
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "environ"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "os"
    )


def _env_names_read() -> set[str]:
    """The environment-variable names ``model_fetcher.py`` reads.

    Read out of the module's AST and resolved through the module's own
    constants, so the assertion is about what the code *does* rather than
    about which strings happen to appear in it.
    """
    source = (_REPO_ROOT / "model_fetcher.py").read_text()
    return {
        cast(str, getattr(model_fetcher, node.args[0].id))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and _reads_the_environment(node)
        and node.args
        and isinstance(node.args[0], ast.Name)
    }


class _HubHTTPError(RuntimeError):
    """A stand-in for `huggingface_hub`'s HTTP errors.

    Those carry the failing ``response`` (and, in its request, everything that
    was sent — which is why the reason vocabulary is closed). The shape is
    reproduced here rather than imported so the test pins the *contract* the
    fetcher reads off an exception, not one library version's class tree.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status_code)


class _FakeClassifier:
    """A stand-in loader that records exactly what it was asked for."""

    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.loaded = False
        self.calls: list[dict[str, object]] = []

    def load(
        self,
        *,
        revision: str | None = None,
        cache_dir: Path | str | None = None,
        local_files_only: bool = False,
    ) -> bool:
        """Record the call and report the configured outcome."""
        self.calls.append(
            {
                "revision": revision,
                "cache_dir": cache_dir,
                "local_files_only": local_files_only,
            }
        )
        self.loaded = self.succeeds
        return self.succeeds


def _hub_download(
    files: Mapping[str, bytes] = _FILES,
    *,
    on_call: Callable[[], None] | None = None,
) -> Callable[..., str]:
    """This module's default file set over the shared download double."""
    return hub_download_double(files, on_call=on_call)


def _fetchable_cache(
    tmp_path: Path,
    files: Mapping[str, bytes] = _FILES,
) -> tuple[Path, Path]:
    """An empty cache root plus the manifest that will bless *files*."""
    cache_root = tmp_path / "model-cache"
    cache_root.mkdir()
    manifest = _write_manifest(
        tmp_path,
        _manifest_document(files, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION),
    )
    return cache_root, manifest


# ---------------------------------------------------------------------------
# Helpers — the GHCR mirror (US-004)
# ---------------------------------------------------------------------------

_MIRROR_TOKEN = "ghp_" + "m" * 36
_HF_TOKEN = "hf_" + "x" * 34
_ORAS_PATH = "/usr/local/bin/oras"


def _mirror_tarball(
    tmp_path: Path,
    files: Mapping[str, bytes] = _FILES,
    *,
    revision: str = DEFAULT_MODEL_REVISION,
    name: str = "forage-weights.tar.gz",
) -> Path:
    """Build the published artifact with **the producer's own code**.

    ``scripts/vendor_weights.build_tarball`` is what made the artifact sitting
    in GHCR today, so a fixture built any other way would be testing the
    consumer against a guess. Using the real function means the two cannot
    drift: a change to the archive's shape breaks this test rather than the
    fallback, months later, during the outage that made someone reach for it.
    """
    source_root = tmp_path / "vendor-cache"
    snapshot = materialize_hub_snapshot(
        source_root, files, model_id=MODEL_ID, revision=revision
    )
    return build_tarball(snapshot, tmp_path / name)


class _FakeOras:
    """A stand-in for the ``oras`` binary that records how it was invoked.

    It honours ``--output``, which several assertions rest on: a double that
    ignored the flag could not tell a fetcher writing into its staging area
    apart from one writing anywhere else.
    """

    def __init__(
        self,
        *,
        artifacts: Sequence[Path] = (),
        returncode: int = 0,
        stderr: str = "",
        timeout: bool = False,
        raises: OSError | None = None,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.artifacts = tuple(artifacts)
        self.returncode = returncode
        self.stderr = stderr
        self.timeout = timeout
        self.raises = raises
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, argv: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        """Record the invocation, materialise the artifacts, report a status."""
        self.calls.append({"argv": list(argv), **kwargs})
        if self.on_call is not None:
            self.on_call()
        if self.timeout:
            raise subprocess.TimeoutExpired(list(argv), ORAS_TIMEOUT_S)
        if self.raises is not None:
            raise self.raises
        if self.returncode == 0:
            destination = Path(argv[argv.index("--output") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            for artifact in self.artifacts:
                (destination / artifact.name).write_bytes(artifact.read_bytes())
        return subprocess.CompletedProcess(list(argv), self.returncode, "", self.stderr)

    @property
    def argv(self) -> list[str]:
        """The argv of the single invocation, asserting there was exactly one."""
        assert len(self.calls) == 1, f"expected one oras call, got {len(self.calls)}"
        return cast("list[str]", self.calls[0]["argv"])

    @property
    def stdin(self) -> object:
        """What the single invocation was fed on stdin."""
        assert len(self.calls) == 1, f"expected one oras call, got {len(self.calls)}"
        return self.calls[0].get("input")


def _mirror_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the mirror: a token, and the committed default repository."""
    monkeypatch.setenv(MIRROR_TOKEN_ENV_VAR, _MIRROR_TOKEN)


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
        """The injection point is for fixtures; production reads one constant.

        Until US-001 this asserted that ``model_fetcher.py`` read no
        environment variable *at all*, which was a proxy for the real
        property and stopped being true the moment the module grew a
        revision pin and a cache root. The property itself is asserted
        directly below, and the proxy is replaced by an exact set: the module
        reads three named variables, and none of them is the manifest.
        """
        assert MANIFEST_PATH == _REPO_ROOT / "weights_manifest.json"
        assert MANIFEST_PATH.name not in {
            os.environ.get(name, "") for name in _env_names_read()
        }

    def test_the_module_reads_exactly_five_environment_variables(self) -> None:
        """Every ``os.environ`` read in the module, from its own AST.

        The set is closed on purpose. A further read is a new configuration
        surface on the one code path that decides which bytes get loaded, and
        it should have to be argued for here rather than appearing.

        It went from three to five in US-004, deliberately and with the
        argument on the record: the mirror is a second *source* of the bytes
        this module verifies, so it needs a location and a credential, and
        both are per-deployment. Note what is still absent — no override for
        the staging bound, the pull timeout, the registry username or the TLS
        posture. Those are decisions, not configuration, and an operator who
        could move them could move the security properties with them.
        """
        assert _env_names_read() == {
            MODEL_REVISION_ENV_VAR,
            CACHE_ROOT_ENV_VAR,
            HF_TOKEN_ENV_VAR,
            MIRROR_ENV_VAR,
            MIRROR_TOKEN_ENV_VAR,
        }

    def test_no_environment_access_evades_the_exact_set(self) -> None:
        """The exact-set walk only sees ``.get()``/``getenv()`` call nodes.

        A plain ``os.environ["NAME"]`` subscript — or any other mention of
        ``os.environ`` outside those two call shapes — would be an env read
        the closed set above never counts (US-001 verification finding: the
        subscript form escaped all sibling tests). So the module may touch
        ``os.environ`` only inside the recognised call shapes; anything else
        is a refusal here, whatever it does.
        """
        source = (_REPO_ROOT / "model_fetcher.py").read_text()
        tree = ast.parse(source)
        counted = {
            id(node.func.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _reads_the_environment(node)
            and isinstance(node.func, ast.Attribute)
        }
        stray = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and id(node) not in counted
        ]
        assert not stray, (
            f"{len(stray)} os.environ access(es) outside the recognised "
            "`.get()` call shape — a subscript or aliased read the "
            "exact-set test cannot count. Use os.environ.get(<CONSTANT>)."
        )

    def test_environment_variable_names_are_never_inline_literals(self) -> None:
        """Each read goes through a module constant a caller can import."""
        source = (_REPO_ROOT / "model_fetcher.py").read_text()
        literal_reads = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and _reads_the_environment(node)
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]

        assert literal_reads == []

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

    def test_it_fails_closed_in_whichever_state_it_is_in(self, tmp_path: Path) -> None:
        """Placeholder or real pin — never a manifest that blesses nothing.

        This started life as ``test_it_is_still_the_interim_placeholder…``,
        which asserted the empty ``files`` list and told US-003 to delete it.
        US-003's build half rewrote it instead, because the manifest is
        replaced by that story's **supervised ops** commit — a human running
        ``scripts/vendor_weights.py`` with the real token — and an ops commit
        that also has to edit a test is an ops commit that will land with the
        test edited wrongly or not at all.

        So the assertion is the property that survives the swap: an empty file
        list refuses with ``manifest_empty``; a populated one pins a
        de-duplicated, sorted, allowlisted set including real safetensors
        weights at the committed revision. Either way an empty cache verifies
        as **false**, which is the whole point of the file.
        """
        document = cast(
            dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )
        files = cast("list[dict[str, Any]]", document["files"])
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()

        result = verify_weights(cache_root)

        assert result.ok is False
        if not files:
            assert result.reasons == (REASON_MANIFEST_EMPTY,)
            return
        assert result.reasons == (REASON_SNAPSHOT_MISSING,)
        paths = [cast(str, entry["path"]) for entry in files]
        assert paths == sorted(paths)
        assert len(set(paths)) == len(paths)
        assert all(is_allowed_filename(path) for path in paths)
        assert any(path.endswith(".safetensors") for path in paths)
        assert all(cast(int, entry["size"]) > 0 for entry in files)


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


# ---------------------------------------------------------------------------
# The revision pin (US-001)
# ---------------------------------------------------------------------------


class TestRevisionPin:
    """One revision, resolved once, locked to everything that repeats it."""

    def test_the_default_is_the_committed_constant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)

        assert resolve_revision() == DEFAULT_MODEL_REVISION

    def test_the_constant_is_a_commit_sha_not_a_branch(self) -> None:
        """A movable ``main`` makes the next upstream commit look like corruption."""
        assert len(DEFAULT_MODEL_REVISION) == 40
        assert set(DEFAULT_MODEL_REVISION) <= set("0123456789abcdef")

    def test_the_environment_overrides_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MODEL_REVISION_ENV_VAR, "b" * 40)

        assert resolve_revision() == "b" * 40

    @pytest.mark.parametrize(
        "value",
        ["main", "", "   ", "../../etc", "b" * 39, "z" * 40, "B" * 40],
    )
    def test_an_unusable_override_falls_back_to_the_pin(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """The value reaches a filesystem path, so its shape is enforced."""
        monkeypatch.setenv(MODEL_REVISION_ENV_VAR, value)

        assert resolve_revision() == DEFAULT_MODEL_REVISION

    def test_an_invalid_override_is_reported_without_echoing_it(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(MODEL_REVISION_ENV_VAR, "refs/heads/evil\ninjected")

        with caplog.at_level("ERROR", logger="model_fetcher"):
            resolve_revision()

        assert "model_revision_invalid" in caplog.text
        assert "injected" not in caplog.text

    def test_the_pin_is_locked_to_the_committed_manifest(self) -> None:
        """Leg two of the triple lock: constant == manifest revision.

        Leg one is the constant itself. Leg three — the mirror tag
        ``ghcr.io/washingbearlabs/forage-weights:<revision>`` — landed with
        US-003, which *derives* the tag from this constant rather than
        spelling it out; ``tests/test_vendor_weights.py``'s
        ``test_the_mirror_tag_is_the_pinned_revision`` is that leg's lock.
        All three are one fact now.
        """
        document = cast(
            dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )

        assert document["revision"] == DEFAULT_MODEL_REVISION
        assert document["model_id"] == MODEL_ID

    def test_the_manifest_pin_is_readable_without_verifying_anything(self) -> None:
        """``read_manifest_pin`` is a reader, and it reports the real state.

        The placeholder pins no files and therefore reads as ``None`` — which
        is what makes the boot path refuse to spend ~270 MiB it could never
        bless. Once US-003's supervised run commits the generated manifest it
        reads as the pin. Asserting the *equivalence* rather than either side
        keeps that ops commit a pure manifest swap.
        """
        document = cast(
            dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )

        pin = read_manifest_pin(MANIFEST_PATH)

        assert (pin is None) is (document["files"] == [])
        if pin is not None:
            assert (pin.model_id, pin.revision) == (MODEL_ID, DEFAULT_MODEL_REVISION)
            assert pin.total_bytes > 0

    def test_a_real_manifest_yields_its_pin(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, _manifest_document())

        pin = read_manifest_pin(manifest)

        assert pin is not None
        assert (pin.model_id, pin.revision) == (_MODEL_ID, _REVISION)


# ---------------------------------------------------------------------------
# The cache tree both the fetch and the load are handed
# ---------------------------------------------------------------------------


class TestCacheTreeResolution:
    """`$HF_HOME/hub`, explicitly, on both sides of the download."""

    def test_the_default_cache_root_is_the_image_volume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CACHE_ROOT_ENV_VAR, raising=False)

        assert resolve_cache_root() == DEFAULT_CACHE_ROOT == Path("/app/model-cache")

    def test_hf_home_moves_the_cache_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(CACHE_ROOT_ENV_VAR, str(tmp_path))

        assert resolve_cache_root() == tmp_path

    def test_the_hub_directory_is_one_level_below_the_volume(self) -> None:
        """`cache_dir=$HF_HOME` writes a repo tree the loader never reads."""
        assert hub_cache_dir("/app/model-cache") == Path("/app/model-cache/hub")
        assert hub_cache_dir(DEFAULT_CACHE_ROOT) != DEFAULT_CACHE_ROOT

    def test_the_hub_directory_is_the_parent_of_every_snapshot(self) -> None:
        snapshot = snapshot_path("/app/model-cache", MODEL_ID, DEFAULT_MODEL_REVISION)

        assert hub_cache_dir("/app/model-cache") in snapshot.parents


# ---------------------------------------------------------------------------
# The Hugging Face fetch
# ---------------------------------------------------------------------------


class TestHuggingFaceFetch:
    """What reaches ``snapshot_download``, and what reaches the log."""

    def test_the_download_is_pinned_filtered_and_placed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        kwargs = cast(MagicMock, download).call_args.kwargs
        assert cast(MagicMock, download).call_args.args == (MODEL_ID,)
        assert kwargs["revision"] == DEFAULT_MODEL_REVISION
        assert Path(kwargs["cache_dir"]) == hub_cache_dir(cache_root)
        assert set(kwargs["allow_patterns"]) == set(ALLOW_PATTERNS)

    def test_the_download_never_flattens_the_cache_with_local_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`local_dir=` loses the ``snapshots/``+``blobs/`` shape the walk needs."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        kwargs = cast(MagicMock, download).call_args.kwargs
        assert "local_dir" not in kwargs
        assert Path(kwargs["cache_dir"]) != cache_root

    def test_the_allowlist_is_what_makes_the_fetch_verifiable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gotcha, as a test: an unfiltered download fails verification.

        ``README.md`` and ``.gitattributes`` come with a plain snapshot
        download and are not allowlisted formats, so the exact-set verifier
        refuses the whole set. This asserts the *consequence* rather than the
        keyword, so it still bites if the filter is passed but ignored.
        """
        extra = {**_FILES, "README.md": b"# model card\n"}
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download(extra)
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is False
        assert classifier.calls == []

    def test_fetch_in_progress_is_true_only_while_downloading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The metric that tells "downloading ~270 MiB" from "wedged"."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        metrics = ModelMetrics()
        observed: list[bool] = []

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download(
                on_call=lambda: observed.append(metrics.fetch_in_progress)
            )
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert observed == [True]
        assert metrics.fetch_in_progress is False

    def test_fetch_in_progress_is_cleared_when_the_download_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        metrics = ModelMetrics()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = RuntimeError("boom")
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert metrics.fetch_in_progress is False
        assert metrics.fetch_failures == 1

    def test_a_failed_download_never_logs_the_token(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """`HF_TOKEN` must not survive an exception's round trip to the log.

        `huggingface_hub`'s errors carry request context, so the reason
        vocabulary is closed and ``str(exc)`` is never interpolated. The
        canary is a token-shaped literal planted in the exception message
        itself — the worst case, and the one a naive ``%s`` would leak.
        """
        token = "hf_" + "s3cret" * 5
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, token)

        with (
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("DEBUG"),
        ):
            download.side_effect = RuntimeError(f"401 Client Error: token={token}")
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert token not in caplog.text
        assert "s3cret" not in caplog.text
        assert "weights_fetch_failed" in caplog.text
        assert "fetch_failed" in caplog.text

    def test_an_http_status_survives_as_a_closed_reason_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A revoked token reads as ``http_401`` — status only, no credential."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        failure = _HubHTTPError("gated repo", status_code=401)

        with (
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            download.side_effect = failure
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert "http_401" in caplog.text
        assert "gated repo" not in caplog.text

    def test_the_token_is_passed_to_the_hub_and_nowhere_else(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = "hf_" + "y" * 34
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, token)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert cast(MagicMock, download).call_args.kwargs["token"] == token
        assert token not in json.dumps(classifier.calls, default=str)


# ---------------------------------------------------------------------------
# The acquisition pipeline
# ---------------------------------------------------------------------------


class TestAcquireAndLoad:
    """Verify → fetch → verify → load, and every way it stops early."""

    def test_a_verified_fetch_reaches_a_loaded_classifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is True
        assert classifier.loaded is True
        assert classifier.calls == [
            {
                "revision": DEFAULT_MODEL_REVISION,
                "cache_dir": hub_cache_dir(cache_root),
                "local_files_only": True,
            }
        ]

    def test_the_load_reads_only_the_bytes_that_were_just_verified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`local_files_only=True`: the etag round-trip would be a new failure mode."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert classifier.calls[0]["local_files_only"] is True

    def test_an_already_verified_cache_loads_without_downloading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The warm shape US-005 builds on: nothing to fetch, so nothing is."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _materialize(cache_root, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is True
        assert cast(MagicMock, download).call_count == 0

    def test_a_cold_cache_is_not_reported_as_a_verification_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """First boot has nothing to verify — an ERROR there is crying wolf."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.delenv(HF_TOKEN_ENV_VAR, raising=False)
        metrics = ModelMetrics()

        with caplog.at_level("DEBUG"):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert "weights_verification_failed" not in caplog.text
        assert metrics.verify_failures == 0

    def test_without_a_token_the_hugging_face_leg_errors_nothing_of_its_own(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A token-less Hugging Face leg is a skip, not a fault.

        US-001 asserted that a token-less run logged **no** ERROR at all, with
        a note that the combined "every source failed" line was US-004's. It is
        now US-004's, and it fires — so what this test pins is the narrower and
        still-true half of that claim: *this leg* says its piece once, at
        WARNING, and never reports a fetch failure of its own. The terminal
        ERROR is asserted where it belongs, in
        :class:`TestNoSourceProducedWeights`.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        classifier = _FakeClassifier()
        metrics = ModelMetrics()

        with (
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("DEBUG"),
        ):
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert cast(MagicMock, download).call_count == 0
        assert classifier.calls == []
        assert "weights_fetch_skipped" in caplog.text
        assert "weights_fetch_failed" not in caplog.text
        errors = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "ERROR"
        ]
        assert len(errors) == 1
        assert errors[0].startswith("weights_unavailable")

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_token_counts_as_no_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """An env file with a blank line must not become an unauthenticated 401."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, value)

        with patch("huggingface_hub.snapshot_download") as download:
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert cast(MagicMock, download).call_count == 0

    def test_an_unusable_manifest_stops_before_the_download(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A pin that blesses nothing must not cost a download.

        Fetching ~270 MiB that the pin could never accept is not fail-closed,
        it is just slow — so the pipeline refuses before spending the
        bandwidth, and says which file is at fault.
        """
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)

        with (
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=_pinless_manifest(tmp_path),
            )

        assert loaded is False
        assert cast(MagicMock, download).call_count == 0
        assert "weights_pin_unusable" in caplog.text

    def test_a_cached_set_refused_by_our_own_manifest_is_not_re_fetched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A manifest failure is ours; another download cannot fix it."""
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)

        with patch("huggingface_hub.snapshot_download") as download:
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=_pinless_manifest(tmp_path),
            )

        assert loaded is False
        assert cast(MagicMock, download).call_count == 0

    def test_a_manifest_level_refusal_diagnoses_one_cause_not_three(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When our own pin is the problem, that is the whole message.

        Falling through would add "no HF_TOKEN" and "the pin blesses nothing"
        on top of the refusal already logged — three lines, two of them
        misleading, for one cause. An operator reading that log would go
        looking for a token.
        """
        cache_root = tmp_path / "model-cache"
        _materialize(cache_root, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION)
        monkeypatch.delenv(HF_TOKEN_ENV_VAR, raising=False)

        with caplog.at_level("DEBUG"):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=_pinless_manifest(tmp_path),
            )

        assert loaded is False
        assert "weights_verification_failed" in caplog.text
        assert REASON_MANIFEST_EMPTY in caplog.text
        assert "weights_fetch_skipped" not in caplog.text
        assert "weights_pin_unusable" not in caplog.text

    def test_a_corrupt_cached_set_is_quarantined_and_re_fetched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A weight-set failure is theirs — quarantine, then try again."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _materialize(cache_root, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION)
        blob_dir = cache_root / "hub" / repo_dirname(MODEL_ID) / "blobs"
        (blob_dir / _sha256(_FILES["model.safetensors"])).write_bytes(b"\x00" * 8)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        metrics = ModelMetrics()
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert metrics.quarantines == 1
        assert cast(MagicMock, download).call_count == 1
        assert loaded is True
        assert quarantine_root(cache_root).is_dir()

    def test_a_download_that_lands_unverifiable_bytes_never_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        tampered = {**_FILES, "model.safetensors": b"not the pinned bytes"}
        classifier = _FakeClassifier()
        metrics = ModelMetrics()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download(tampered)
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert classifier.calls == []
        assert metrics.verify_failures == 1

    def test_a_loader_that_refuses_verified_bytes_is_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = _FakeClassifier(succeeds=False)

        with (
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            download.side_effect = _hub_download()
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is False
        assert "weights_load_failed" in caplog.text

    def test_it_never_raises_into_the_background_thread(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """It runs detached; an escaping exception would surface as nothing."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        metrics = ModelMetrics()

        with (
            patch.object(model_fetcher, "verify_weights") as verifier,
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            download.side_effect = _hub_download()
            verifier.side_effect = MemoryError("out of memory mid-verify")
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert "weights_acquisition_crashed" in caplog.text
        assert metrics.fetch_failures == 1

    def test_the_environment_supplies_every_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end with no arguments at all — the shape the lifespan calls."""
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()
        manifest = _write_manifest(
            tmp_path,
            _manifest_document(
                _FILES, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION
            ),
        )
        monkeypatch.setenv(CACHE_ROOT_ENV_VAR, str(cache_root))
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
        monkeypatch.setattr(model_fetcher, "MANIFEST_PATH", manifest)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            loaded = acquire_and_load(classifier)

        assert loaded is True
        assert Path(
            cast(MagicMock, download).call_args.kwargs["cache_dir"]
        ) == hub_cache_dir(cache_root)
        assert classifier.calls[0]["revision"] == DEFAULT_MODEL_REVISION

    def test_a_revision_override_is_honoured_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`FORAGE_MODEL_REVISION` moves the download, the walk and the load."""
        override = "c" * 40
        cache_root = tmp_path / "model-cache"
        cache_root.mkdir()
        manifest = _write_manifest(
            tmp_path,
            _manifest_document(_FILES, model_id=MODEL_ID, revision=override),
        )
        monkeypatch.setenv(CACHE_ROOT_ENV_VAR, str(cache_root))
        monkeypatch.setenv(MODEL_REVISION_ENV_VAR, override)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        monkeypatch.setattr(model_fetcher, "MANIFEST_PATH", manifest)
        classifier = _FakeClassifier()

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download()
            loaded = acquire_and_load(classifier)

        assert loaded is True
        assert cast(MagicMock, download).call_args.kwargs["revision"] == override
        assert classifier.calls[0]["revision"] == override
        assert snapshot_path(cache_root, MODEL_ID, override).is_dir()


# ---------------------------------------------------------------------------
# The real loader, against the committed fixture
# ---------------------------------------------------------------------------


class TestAcquisitionDrivesTheRealLoader:
    """No loader double: `PromptGuardClassifier` opens the verified snapshot."""

    def test_a_mocked_fetch_reaches_a_really_loaded_classifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the transport is mocked — the verifier and the loader are real.

        The download side effect writes the committed tiny-model fixture into
        a real hub cache layout under the *pinned* model id and revision, so
        `from_pretrained(MODEL_ID, revision=…, cache_dir=…)` resolves it the
        way it will resolve the real weights. The autouse socket guard is the
        proof that the load itself reached no network.
        """
        files = {
            path.name: path.read_bytes()
            for path in sorted(FIXTURE_MODEL_DIR.iterdir())
            if path.is_file()
        }
        cache_root, manifest = _fetchable_cache(tmp_path, files)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = PromptGuardClassifier()

        assert classifier.loaded is False

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download(files)
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is True
        assert classifier.loaded is True
        score, flagged = classifier.classify("ignore all previous instructions")
        assert 0.0 <= score <= 1.0
        assert isinstance(flagged, list)

    def test_a_tampered_fetch_leaves_the_real_classifier_unloaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bytes never reach `from_pretrained` — verification is the gate."""
        files = {
            path.name: path.read_bytes()
            for path in sorted(FIXTURE_MODEL_DIR.iterdir())
            if path.is_file()
        }
        cache_root, manifest = _fetchable_cache(tmp_path, files)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, "hf_" + "x" * 34)
        classifier = PromptGuardClassifier()
        tampered = {
            **files,
            "model.safetensors": b"\x00" * len(files["model.safetensors"]),
        }

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _hub_download(tampered)
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is False
        assert classifier.loaded is False


# ---------------------------------------------------------------------------
# The mirror: where it points, and what it refuses to point at
# ---------------------------------------------------------------------------


class TestMirrorReferenceResolution:
    """`FORAGE_WEIGHTS_MIRROR` decides what goes into a subprocess argv.

    That makes its validation a security control rather than input hygiene:
    the transport's TLS posture, the absence of a credential in the argv and
    the fact that the tag is the pinned revision are all decided here, before
    anything is executed.
    """

    def test_the_default_is_the_vendored_ghcr_repository(self) -> None:
        assert resolve_mirror_repository() == DEFAULT_WEIGHTS_MIRROR
        assert DEFAULT_WEIGHTS_MIRROR == "ghcr.io/washingbearlabs/forage-weights"

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_setting_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """An env file with a blank line must not become an unusable mirror."""
        monkeypatch.setenv(MIRROR_ENV_VAR, value)

        assert resolve_mirror_repository() == DEFAULT_WEIGHTS_MIRROR

    def test_the_environment_redirects_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MIRROR_ENV_VAR, "registry.example.net/team/weights")

        assert resolve_mirror_repository() == "registry.example.net/team/weights"

    def test_an_https_prefix_is_accepted_and_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OCI references carry no scheme; an operator will write one anyway."""
        monkeypatch.setenv(MIRROR_ENV_VAR, "https://ghcr.io/washingbearlabs/w")

        assert resolve_mirror_repository() == "ghcr.io/washingbearlabs/w"

    @pytest.mark.parametrize(
        "value",
        [
            "http://ghcr.io/washingbearlabs/forage-weights",
            "ftp://ghcr.io/washingbearlabs/forage-weights",
            "ghcr.io/washingbearlabs/forage-weights:latest",
            "ghcr.io/washingbearlabs/forage-weights@sha256:" + "a" * 64,
            "ghcr.io/WashingBearLabs/forage-weights",
            "--plain-http",
            "ghcr.io",
            "ghcr.io/washingbearlabs/forage weights",
            "ghcr.io/washingbearlabs/../../etc/passwd",
        ],
    )
    def test_an_unusable_reference_disables_the_mirror(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Each of these is a distinct way to lose a property we rely on.

        A scheme that is not https downgrades the transport; a tag or digest
        takes the revision pin out of our hands; an upper-case path is one GHCR
        would reject anyway; a flag-shaped value is argv injection; a bare
        registry and a path with a space are simply not references. All of them
        answer the same way — no mirror — because a half-understood reference
        is not something to try anyway and see.
        """
        monkeypatch.setenv(MIRROR_ENV_VAR, value)

        assert resolve_mirror_repository() is None

    def test_a_credential_bearing_reference_is_refused_and_redacted(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Userinfo is both refused *and* redacted — two independent controls.

        Refusing it keeps the credential out of the argv. Redacting it keeps
        the credential out of the log line that reports the refusal, which is
        the one place a naive implementation would print the whole value back.
        """
        secret = "s3cr3t-mirror-pw"
        monkeypatch.setenv(
            MIRROR_ENV_VAR, f"https://ci-bot:{secret}@ghcr.io/washingbearlabs/w"
        )

        with caplog.at_level("DEBUG"):
            assert resolve_mirror_repository() is None

        assert "weights_mirror_invalid" in caplog.text
        assert secret not in caplog.text
        assert "ci-bot" not in caplog.text
        assert "***@ghcr.io/washingbearlabs/w" in caplog.text

    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("ghcr.io/owner/name:rev", "ghcr.io/owner/name:rev"),
            ("https://ghcr.io/owner/name", "https://ghcr.io/owner/name"),
            ("https://user:pw@ghcr.io/owner/name", "https://***@ghcr.io/owner/name"),
            ("user:pw@ghcr.io/owner/name", "***@ghcr.io/owner/name"),
            ("a@b@ghcr.io/owner/name", "***@ghcr.io/owner/name"),
        ],
    )
    def test_redaction_keeps_the_location_and_drops_the_credential(
        self, reference: str, expected: str
    ) -> None:
        assert redact_reference(reference) == expected

    def test_the_tag_is_always_the_pinned_revision(self) -> None:
        """Leg three of the triple lock, from the consumer's side.

        ``scripts/vendor_weights.mirror_ref()`` derives the same string when it
        pushes. One fact — the pinned revision — in the constant, the committed
        manifest and the tag, so the mirror can only serve the revision this
        process is pinned to.
        """
        reference = mirror_reference(DEFAULT_WEIGHTS_MIRROR, DEFAULT_MODEL_REVISION)

        assert reference == f"{DEFAULT_WEIGHTS_MIRROR}:{DEFAULT_MODEL_REVISION}"

    def test_the_repository_setting_cannot_redirect_the_revision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`FORAGE_WEIGHTS_MIRROR` names a repository, never a tag."""
        monkeypatch.setenv(MIRROR_ENV_VAR, "registry.example.net/team/weights")
        repository = resolve_mirror_repository()

        assert repository is not None
        assert mirror_reference(repository, "b" * 40).endswith(":" + "b" * 40)


# ---------------------------------------------------------------------------
# The oras invocation: TLS on, credential off the argv
# ---------------------------------------------------------------------------


class TestOrasInvocation:
    """What the fetcher asks the OCI client to do, exactly."""

    def test_the_argv_carries_the_reference_the_output_and_no_credential(
        self, tmp_path: Path
    ) -> None:
        reference = mirror_reference(DEFAULT_WEIGHTS_MIRROR, DEFAULT_MODEL_REVISION)
        argv = oras_pull_argv(
            oras=_ORAS_PATH, reference=reference, destination=tmp_path
        )

        assert argv[:3] == (_ORAS_PATH, "pull", reference)
        assert "--output" in argv
        assert argv[argv.index("--output") + 1] == str(tmp_path)
        assert "--password-stdin" in argv
        assert argv[argv.index("--username") + 1] == MIRROR_USERNAME

    @pytest.mark.parametrize(
        "flag",
        ["--insecure", "--plain-http", "--allow-http", "-k", "--distribution-spec"],
    )
    def test_no_argument_can_weaken_the_transport(
        self, tmp_path: Path, flag: str
    ) -> None:
        """TLS verification is not a thing this code can be talked out of.

        The argv is a fixed tuple of literals plus a reference that has already
        been reduced to a bare lower-case repository, so there is no value an
        operator can supply that lands here as a flag — and nothing in the
        tuple turns verification off.
        """
        argv = oras_pull_argv(
            oras=_ORAS_PATH,
            reference=mirror_reference(DEFAULT_WEIGHTS_MIRROR, "c" * 40),
            destination=tmp_path,
        )

        assert flag not in argv
        assert not any(flag in element for element in argv)

    def test_the_token_travels_on_stdin_and_never_in_the_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ps` is world-readable; a pipe is not, and leaves no shell history."""
        tarball = _mirror_tarball(tmp_path)
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[tarball])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
        ):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert oras.stdin == _MIRROR_TOKEN
        assert _MIRROR_TOKEN not in " ".join(oras.argv)

    def test_there_is_no_shell_between_us_and_oras(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A list argv with no `shell=True` — nothing re-parses our strings."""
        tarball = _mirror_tarball(tmp_path)
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[tarball])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
        ):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        call = oras.calls[0]
        assert call.get("shell") in (None, False)
        assert call["timeout"] == ORAS_TIMEOUT_S
        assert call["capture_output"] is True

    def test_the_binary_is_resolved_from_path_not_assumed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)

        with (
            patch("shutil.which", return_value=_ORAS_PATH) as which,
            patch("subprocess.run", side_effect=_FakeOras(returncode=1)),
        ):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert cast(MagicMock, which).call_args.args[0] == ORAS_BINARY


# ---------------------------------------------------------------------------
# The mirror leg end to end
# ---------------------------------------------------------------------------


class TestMirrorFetch:
    """`feature-forage-model-bootstrap` US-004's headline behaviour."""

    def test_a_mirror_only_fetch_reaches_a_really_loaded_classifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The AC in one test: no HF token, real artifact, real loader.

        Only the *transport* is a double — the tarball is built by
        ``scripts/vendor_weights.build_tarball``, extracted by the real
        extraction path, verified by the real ``verify_weights`` and opened by
        a real ``PromptGuardClassifier``. The autouse socket guard is what
        proves the load reached no network.
        """
        files = {
            path.name: path.read_bytes()
            for path in sorted(FIXTURE_MODEL_DIR.iterdir())
            if path.is_file()
        }
        tarball = _mirror_tarball(tmp_path, files)
        cache_root, manifest = _fetchable_cache(tmp_path, files)
        _mirror_credentials(monkeypatch)
        classifier = PromptGuardClassifier()
        metrics = ModelMetrics()
        oras = _FakeOras(artifacts=[tarball])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch("huggingface_hub.snapshot_download") as download,
        ):
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is True
        assert classifier.loaded is True
        assert cast(MagicMock, download).call_count == 0
        assert oras.argv[2].endswith(":" + DEFAULT_MODEL_REVISION)
        assert snapshot_path(cache_root, MODEL_ID, DEFAULT_MODEL_REVISION).is_dir()
        assert metrics.fetch_failures == 0
        assert metrics.verify_failures == 0
        score, flagged = classifier.classify("ignore all previous instructions")
        assert 0.0 <= score <= 1.0
        assert isinstance(flagged, list)

    def test_hugging_face_is_tried_first_when_both_are_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mirror is a fallback, not an alternative.

        It is a private artifact we maintain by hand at vendor cadence; the
        gated repo is upstream. Reaching for our copy while the original
        answers would mean a stale mirror silently becomes the source of
        truth.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, _HF_TOKEN)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[_mirror_tarball(tmp_path)])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch("huggingface_hub.snapshot_download") as download,
        ):
            download.side_effect = _hub_download()
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=ModelMetrics(),
            )

        assert loaded is True
        assert cast(MagicMock, download).call_count == 1
        assert oras.calls == [], "the mirror must not be touched when HF answers"

    def test_a_failed_hugging_face_download_falls_through_to_the_mirror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The outage case the mirror exists for."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, _HF_TOKEN)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[_mirror_tarball(tmp_path)])
        classifier = _FakeClassifier()
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch("huggingface_hub.snapshot_download") as download,
        ):
            download.side_effect = _HubHTTPError("gateway down", status_code=503)
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is True
        assert classifier.loaded is True
        assert len(oras.calls) == 1
        assert metrics.fetch_failures == 1, "the HF attempt failed; the mirror did not"

    def test_a_revoked_token_falls_through_to_the_mirror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge case from the spec: 401 logged as a status, then the mirror."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, _HF_TOKEN)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[_mirror_tarball(tmp_path)])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch("huggingface_hub.snapshot_download") as download,
        ):
            download.side_effect = _HubHTTPError("forbidden", status_code=401)
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is True

    def test_a_corrupt_hugging_face_download_falls_through_to_the_mirror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused set is quarantined, and the *next source* is still tried.

        Giving up after one bad download would leave a container degraded with
        a perfectly good mirror one call away — and the quarantine has already
        made room for the retry.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, _HF_TOKEN)
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()
        oras = _FakeOras(artifacts=[_mirror_tarball(tmp_path)])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch("huggingface_hub.snapshot_download") as download,
        ):
            download.side_effect = _hub_download(
                {**_FILES, "model.safetensors": b"not the pinned bytes"}
            )
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is True
        assert metrics.verify_failures == 1
        assert metrics.quarantines == 1
        assert metrics.fetch_failures == 0, "bytes arrived; verify_failures counts it"

    def test_fetch_in_progress_is_true_only_while_the_mirror_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()
        observed: list[bool] = []
        oras = _FakeOras(
            artifacts=[_mirror_tarball(tmp_path)],
            on_call=lambda: observed.append(metrics.fetch_in_progress),
        )

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
        ):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert observed == [True]
        assert metrics.fetch_in_progress is False

    def test_fetch_in_progress_is_cleared_when_the_pull_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wedged flag would make `/metrics` say "downloading" forever."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(returncode=1)),
        ):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert metrics.fetch_in_progress is False

    def test_an_existing_partial_snapshot_is_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reaching the mirror means what is cached did not satisfy the pin."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        partial = snapshot_path(cache_root, MODEL_ID, DEFAULT_MODEL_REVISION)
        partial.mkdir(parents=True)
        (partial / "config.json").write_bytes(b"half a download")
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[_mirror_tarball(tmp_path)])

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is True
        assert (partial / "config.json").read_bytes() == _FILES["config.json"]


# ---------------------------------------------------------------------------
# Safe extraction, and what may reach the loader's tree
# ---------------------------------------------------------------------------


class TestMirrorExtractionIsSafe:
    """A tarball is bytes a remote party chose. Treat it that way."""

    def test_extractall_passes_the_data_filter_literally(self) -> None:
        """The AC names ``filter="data"``; pin the literal, not just outcomes.

        US-004 verification found a ``filter="tar"`` mutation left all
        behavioural tests green — the hostile-tarball suite happens to be
        refused by ``tar`` too, but ``tar`` performs no absolute-path or
        traversal filtering by contract, so the behavioural cover is
        incidental. The call site must say ``data``.
        """
        tree = ast.parse((_REPO_ROOT / "model_fetcher.py").read_text())
        extract_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "extractall"
        ]
        assert extract_calls, "model_fetcher.py no longer calls extractall"
        for call in extract_calls:
            filters = [
                kw.value.value
                for kw in call.keywords
                if kw.arg == "filter" and isinstance(kw.value, ast.Constant)
            ]
            assert filters == ["data"], (
                'every extractall in model_fetcher.py must pass filter="data" '
                f"as a literal; found keywords {[(kw.arg) for kw in call.keywords]}"
            )

    def test_a_traversing_member_never_escapes_the_staging_area(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """`filter="data"` explicitly — Python 3.12 still defaults to unsafe.

        The member name is the attack: a mirror that had been tampered with (or
        a registry serving someone else's artifact) writes `../../escape.json`
        and lands a file wherever the extraction is rooted. The filter refuses
        it; the test proves the file is not there rather than trusting that.
        """
        hostile = tmp_path / "hostile.tar.gz"
        target = tmp_path / "escape.json"
        with tarfile.open(hostile, "w:gz") as archive:
            payload = b'{"owned": true}'
            info = tarfile.TarInfo("../../../escape.json")
            info.size = len(payload)
            archive.addfile(info, __import__("io").BytesIO(payload))
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[hostile])),
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert target.exists() is False
        assert list(tmp_path.rglob("escape.json")) == []
        assert f"{SOURCE_MIRROR}={OUTCOME_EXTRACT_FAILED}" in caplog.text
        assert metrics.fetch_failures == 1

    def test_an_unverifiable_artifact_never_reaches_the_snapshot_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verification happens in staging; only a pass is installed."""
        tampered = _mirror_tarball(
            tmp_path, {**_FILES, "model.safetensors": b"substituted weights"}
        )
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        classifier = _FakeClassifier()
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[tampered])),
        ):
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert classifier.calls == []
        assert snapshot_path(cache_root, MODEL_ID, DEFAULT_MODEL_REVISION).exists() is (
            False
        )
        assert metrics.verify_failures == 1
        # The staged set is quarantined by the shared verifier before the
        # staging tree is swept, so the counter moves for something the
        # operator cannot later go and look at. That is the honest reading of
        # `quarantines` ("a refused set was moved out of the loader's tree")
        # and the alternative — a second verification entry point that does
        # not quarantine — would be a worse trade than a transient count.
        assert metrics.quarantines == 1
        assert quarantine_root(cache_root).exists() is False

    def test_an_artifact_carrying_a_pickle_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The RCE closure, on the path a hostile mirror would use.

        The manifest is an exact set, so an extra `pytorch_model.bin` fails as
        a disallowed format before `from_pretrained` is called at all — and
        `use_safetensors=True` at the loader is the second lock behind it.
        """
        smuggled = tmp_path / "smuggled.tar.gz"
        with tarfile.open(smuggled, "w:gz") as archive:
            for name, payload in {
                **_FILES,
                "pytorch_model.bin": b"\x80\x04pickled",
            }.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, __import__("io").BytesIO(payload))
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        classifier = _FakeClassifier()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[smuggled])),
        ):
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is False
        assert classifier.calls == []

    def test_a_non_archive_is_a_reported_failure_not_a_crash(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        junk = tmp_path / "not-a-tarball.tar.gz"
        junk.write_bytes(b"this is not gzip")
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[junk])),
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_EXTRACT_FAILED}" in caplog.text
        assert metrics.fetch_failures == 1


# ---------------------------------------------------------------------------
# Staging is bounded, and always cleaned up
# ---------------------------------------------------------------------------


class TestMirrorStagingIsBounded:
    """A 1 GB volume, a ~270 MiB weight set, and two copies at peak."""

    def test_the_cap_is_two_copies_plus_ten_percent(self) -> None:
        """The round-2 "total + 10%" figure would abort every real fetch.

        The tarball and its extraction coexist: the artifact has to be on disk
        while it is being unpacked. One copy plus slack is less than what a
        successful fetch actually occupies, so enforcing it would refuse the
        good case.
        """
        pin = read_manifest_pin(MANIFEST_PATH)

        assert pin is not None
        assert staging_cap_bytes(pin) == int(pin.total_bytes * 2 * 1.1)
        assert staging_cap_bytes(pin) > pin.total_bytes * 2

    def test_insufficient_space_refuses_before_the_pull(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Spending the bandwidth to then run out of disk is not fail-closed."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(artifacts=[_mirror_tarball(tmp_path)])
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch(
                "shutil.disk_usage",
                return_value=SimpleNamespace(total=1, used=1, free=1),
            ),
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert oras.calls == []
        assert f"{SOURCE_MIRROR}={OUTCOME_INSUFFICIENT_SPACE}" in caplog.text
        assert metrics.fetch_failures == 1

    def test_an_oversized_artifact_is_refused(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """`filter="data"` says nothing about an archive that is merely huge."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        bomb = _mirror_tarball(tmp_path, {**_FILES, "tokenizer.json": b"x" * 4_000_000})
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[bomb])),
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_ARTIFACT_OVERSIZED}" in caplog.text
        assert metrics.fetch_failures == 1

    def test_a_tarball_larger_than_the_bound_is_refused_before_extraction(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The bound on what arrived, isolated from the bound on what it holds.

        The payload is incompressible, so the artifact is larger on disk than
        the cap while its declared members are comfortably under it — which is
        the only way to prove *this* check fires rather than the one after it.
        Two bounds that cover for each other are one bound with a spare.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        pin = read_manifest_pin(manifest)
        assert pin is not None
        cap = staging_cap_bytes(pin)

        oversized = tmp_path / "oversized.tar.gz"
        payload = random.Random(0).randbytes(cap - 100)
        with tarfile.open(oversized, "w:gz") as archive:
            info = tarfile.TarInfo("model.safetensors")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

        assert len(payload) < cap < oversized.stat().st_size

        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[oversized])),
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_ARTIFACT_OVERSIZED}" in caplog.text
        assert metrics.fetch_failures == 1

    def test_a_decompression_bomb_is_refused_before_a_byte_is_written(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """`filter="data"` has nothing to say about an archive that is huge.

        The mirror image of the test above: 200 KB of zeroes gzips to a few
        hundred bytes, so the artifact sails under the on-disk bound and the
        *declared* member sizes are what refuse it — before `extractall` is
        called at all, which is the point. A bomb caught after extraction has
        already filled the volume it was aimed at.
        """
        bomb = tmp_path / "bomb.tar.gz"
        with tarfile.open(bomb, "w:gz") as archive:
            payload = b"\x00" * 200_000
            info = tarfile.TarInfo("model.safetensors")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        cache_root, manifest = _fetchable_cache(tmp_path)
        pin = read_manifest_pin(manifest)
        assert pin is not None
        assert bomb.stat().st_size < staging_cap_bytes(pin) < len(payload)

        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=_FakeOras(artifacts=[bomb])),
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_ARTIFACT_OVERSIZED}" in caplog.text
        assert [
            path
            for path in tmp_path.rglob("*")
            if path.is_file() and path.stat().st_size >= len(payload)
        ] == []

    def test_the_leg_sweeps_its_own_staging_area(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Asserted at the leg's own boundary, not the pipeline's.

        `acquire_and_load` sweeps staging in a `finally` of its own, which
        makes the leg's cleanup look redundant — and a mutation that deletes it
        passes every test driven through the pipeline. It is not redundant:
        US-005's retry task is a second caller of this seam, and a leg that
        leaks ~230 MiB per attempt into a backoff loop would fill the volume
        long before it converged. So the guarantee is pinned where it is made.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        pin = read_manifest_pin(manifest)
        assert pin is not None
        _mirror_credentials(monkeypatch)

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch(
                "subprocess.run",
                side_effect=_FakeOras(artifacts=[_mirror_tarball(tmp_path)]),
            ),
        ):
            outcome = model_fetcher._fetch_from_mirror(
                revision=DEFAULT_MODEL_REVISION,
                cache_root=cache_root,
                manifest=pin,
                manifest_path=manifest,
                metrics=ModelMetrics(),
            )

        assert outcome == "ok"
        assert staging_root(cache_root).exists() is False

    @pytest.mark.parametrize("succeeds", [True, False])
    def test_the_staging_tree_never_survives_an_acquisition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, succeeds: bool
    ) -> None:
        """Success and failure alike: nothing is left behind to fill the volume."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        oras = _FakeOras(
            artifacts=[_mirror_tarball(tmp_path)] if succeeds else [],
            returncode=0 if succeeds else 1,
        )

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is succeeds
        assert staging_root(cache_root).exists() is False

    def test_the_hub_client_own_staging_tree_is_swept_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`$HF_HOME/xet/` — verified present on the live sidecar.

        It is a chunk-dedup cache for a *future* download of a weight set that
        is fetched once, sitting on a volume the next fetch needs. It is also
        outside the tree the verifier walks, so nothing else would ever notice
        it growing.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        xet = cache_root / XET_DIRNAME
        (xet / "chunks").mkdir(parents=True)
        (xet / "chunks" / "blob").write_bytes(b"\x00" * 1024)

        with patch("huggingface_hub.snapshot_download"):
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert xet.exists() is False

    def test_a_cold_cache_root_that_does_not_exist_is_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        """The cleanup runs on a volume that was never mounted, too."""
        manifest = _write_manifest(
            tmp_path,
            _manifest_document(
                _FILES, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION
            ),
        )

        assert (
            acquire_and_load(
                _FakeClassifier(),
                cache_root=tmp_path / "never-mounted",
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )
            is False
        )


# ---------------------------------------------------------------------------
# Nobody answered: one ERROR, both sources named, the counter moves
# ---------------------------------------------------------------------------


class TestNoSourceProducedWeights:
    """The spec's "zero silent paths" goal, stated as a matrix.

    US-001 deferred this deliberately: its no-token path logged a WARNING and
    left `fetch_failures` at zero, with the note that the combined ERROR
    belonged to the story that would know whether a mirror was available. This
    is that story, and these are its rules:

    * a **skip** (no credential, or an unusable `FORAGE_WEIGHTS_MIRROR`) is not
      a fetch failure — nothing was reached;
    * an **attempt** that produced no bytes is one;
    * an attempt whose bytes were **refused** is a `verify_failures`, never
      also a `fetch_failures` — one event, one counter;
    * and an acquisition where *nothing was attempted at all* still moves
      `fetch_failures` once, because the alternative is a degraded container
      with every counter at zero.
    """

    def _acquire(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        *,
        hf: str | None,
        mirror: str | None,
        hf_effect: object = None,
        oras: _FakeOras | None = None,
    ) -> tuple[bool, ModelMetrics, list[str]]:
        cache_root, manifest = _fetchable_cache(tmp_path)
        if hf is not None:
            monkeypatch.setenv(HF_TOKEN_ENV_VAR, hf)
        if mirror is not None:
            monkeypatch.setenv(MIRROR_TOKEN_ENV_VAR, mirror)
        metrics = ModelMetrics()
        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch(
                "subprocess.run",
                side_effect=oras if oras is not None else _FakeOras(returncode=1),
            ),
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("DEBUG"),
        ):
            if hf_effect is not None:
                download.side_effect = hf_effect
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )
        errors = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "ERROR"
        ]
        return loaded, metrics, errors

    def test_neither_source_configured_logs_one_error_and_moves_the_counter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The stock image's state — degraded, and audibly so.

        This is the container CI smokes: no `HF_TOKEN`, no
        `FORAGE_MIRROR_TOKEN`. It stays up and honest, and it says exactly once
        why it has no classifier.
        """
        loaded, metrics, errors = self._acquire(
            tmp_path, monkeypatch, caplog, hf=None, mirror=None
        )

        assert loaded is False
        assert len(errors) == 1
        terminal = errors[0]
        assert terminal.startswith("weights_unavailable")
        assert f"{SOURCE_HUGGINGFACE}={OUTCOME_SKIPPED_NO_TOKEN}" in terminal
        assert f"{SOURCE_MIRROR}={OUTCOME_SKIPPED_NO_TOKEN}" in terminal
        assert metrics.fetch_failures == 1
        assert metrics.verify_failures == 0

    def test_both_sources_attempted_and_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        loaded, metrics, errors = self._acquire(
            tmp_path,
            monkeypatch,
            caplog,
            hf=_HF_TOKEN,
            mirror=_MIRROR_TOKEN,
            hf_effect=_HubHTTPError("gone", status_code=404),
        )

        terminal = [line for line in errors if line.startswith("weights_unavailable")]
        assert loaded is False
        assert len(terminal) == 1
        assert f"{SOURCE_HUGGINGFACE}=http_404" in terminal[0]
        assert f"{SOURCE_MIRROR}={OUTCOME_PULL_FAILED}" in terminal[0]
        assert metrics.fetch_failures == 2, "one per attempted source"

    def test_a_refused_set_is_counted_once_by_the_verifier_not_twice(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        loaded, metrics, errors = self._acquire(
            tmp_path,
            monkeypatch,
            caplog,
            hf=_HF_TOKEN,
            mirror=None,
            hf_effect=_hub_download(
                {**_FILES, "model.safetensors": b"not the pinned bytes"}
            ),
        )

        terminal = [line for line in errors if line.startswith("weights_unavailable")]
        assert loaded is False
        assert len(terminal) == 1
        assert f"{SOURCE_HUGGINGFACE}={OUTCOME_REFUSED}" in terminal[0]
        assert f"{SOURCE_MIRROR}={OUTCOME_SKIPPED_NO_TOKEN}" in terminal[0]
        assert metrics.verify_failures == 1
        assert metrics.fetch_failures == 0

    def test_a_misconfigured_mirror_is_named_in_the_ending(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(MIRROR_ENV_VAR, "http://ghcr.io/washingbearlabs/w")
        loaded, metrics, errors = self._acquire(
            tmp_path, monkeypatch, caplog, hf=None, mirror=_MIRROR_TOKEN
        )

        terminal = [line for line in errors if line.startswith("weights_unavailable")]
        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_MISCONFIGURED}" in terminal[0]
        assert metrics.fetch_failures == 1, "nothing was attempted"

    def test_a_missing_oras_binary_is_an_attempted_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The image is supposed to ship it; if it does not, say so."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _mirror_credentials(monkeypatch)
        metrics = ModelMetrics()

        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as run,
            caplog.at_level("ERROR", logger="model_fetcher"),
        ):
            loaded = acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
                metrics=metrics,
            )

        assert loaded is False
        assert cast(MagicMock, run).call_count == 0
        assert f"{SOURCE_MIRROR}={OUTCOME_TOOL_MISSING}" in caplog.text
        assert metrics.fetch_failures == 1

    def test_a_pull_timeout_is_reported_as_such(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A bounded subprocess: a wedged pull must not wedge acquisition."""
        loaded, metrics, errors = self._acquire(
            tmp_path,
            monkeypatch,
            caplog,
            hf=None,
            mirror=_MIRROR_TOKEN,
            oras=_FakeOras(timeout=True),
        )

        terminal = [line for line in errors if line.startswith("weights_unavailable")]
        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_TIMEOUT}" in terminal[0]
        assert metrics.fetch_failures == 1

    def test_a_pull_that_leaves_nothing_behind_is_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exit 0 and an empty directory is a lie the fetcher must not believe."""
        loaded, _metrics, errors = self._acquire(
            tmp_path,
            monkeypatch,
            caplog,
            hf=None,
            mirror=_MIRROR_TOKEN,
            oras=_FakeOras(artifacts=[]),
        )

        terminal = [line for line in errors if line.startswith("weights_unavailable")]
        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_NO_ARTIFACT}" in terminal[0]

    def test_a_pull_that_leaves_several_files_is_refused_not_guessed_at(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exactly one file is the contract; two is a question, not an answer.

        Picking the first (or the one that looks like a tarball) would mean the
        fetcher deciding, on its own, which of several remote-chosen files to
        open — on the path whose entire purpose is that remote-chosen bytes are
        not trusted.
        """
        first = _mirror_tarball(tmp_path, name="weights.tar.gz")
        second = tmp_path / "extra.tar.gz"
        second.write_bytes(first.read_bytes())
        loaded, metrics, errors = self._acquire(
            tmp_path,
            monkeypatch,
            caplog,
            hf=None,
            mirror=_MIRROR_TOKEN,
            oras=_FakeOras(artifacts=[first, second]),
        )

        terminal = [line for line in errors if line.startswith("weights_unavailable")]
        assert loaded is False
        assert f"{SOURCE_MIRROR}={OUTCOME_NO_ARTIFACT}" in terminal[0]
        assert metrics.fetch_failures == 1

    def test_the_ending_names_every_source_in_order(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One line, both sources, in the order they were tried.

        An operator reading it should not have to know the pipeline's shape to
        see which source was expected to answer first.
        """
        _loaded, _metrics, errors = self._acquire(
            tmp_path, monkeypatch, caplog, hf=None, mirror=None
        )
        terminal = errors[0]
        positions = [terminal.index(f"{source}=") for source in ACQUISITION_SOURCES]

        assert positions == sorted(positions)
        assert ACQUISITION_SOURCES == (SOURCE_HUGGINGFACE, SOURCE_MIRROR)

    def test_neither_token_ever_reaches_a_log_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Both credentials, every level, including what the tools say back.

        The Hugging Face exception carries a token-shaped literal in its
        message (that is the real shape: `huggingface_hub`'s errors carry
        request context) and `oras` echoes its credential on stderr. Neither
        may survive into the log, which is why the fetcher reports an exit
        status and a reason code rather than a captured stream.
        """
        cache_root, manifest = _fetchable_cache(tmp_path)
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, _HF_TOKEN)
        monkeypatch.setenv(MIRROR_TOKEN_ENV_VAR, _MIRROR_TOKEN)
        oras = _FakeOras(
            returncode=1,
            stderr=f"Error: unauthorized (credential {_MIRROR_TOKEN} rejected)",
        )

        with (
            patch("shutil.which", return_value=_ORAS_PATH),
            patch("subprocess.run", side_effect=oras),
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("DEBUG"),
        ):
            download.side_effect = _HubHTTPError(
                f"401 for https://huggingface.co (Authorization: Bearer {_HF_TOKEN})",
                status_code=401,
            )
            acquire_and_load(
                _FakeClassifier(),
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert _HF_TOKEN not in caplog.text
        assert _MIRROR_TOKEN not in caplog.text
        assert "unauthorized" not in caplog.text
        assert "http_401" in caplog.text
        assert OUTCOME_PULL_FAILED in caplog.text

    def test_a_warm_cache_reaches_no_source_and_logs_no_ending(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The loud ending belongs to failure only — never to a good boot."""
        cache_root, manifest = _fetchable_cache(tmp_path)
        _materialize(cache_root, model_id=MODEL_ID, revision=DEFAULT_MODEL_REVISION)
        classifier = _FakeClassifier()

        with (
            patch("subprocess.run") as run,
            patch("huggingface_hub.snapshot_download") as download,
            caplog.at_level("DEBUG"),
        ):
            loaded = acquire_and_load(
                classifier,
                cache_root=cache_root,
                revision=DEFAULT_MODEL_REVISION,
                manifest_path=manifest,
            )

        assert loaded is True
        assert cast(MagicMock, run).call_count == 0
        assert cast(MagicMock, download).call_count == 0
        assert "weights_unavailable" not in caplog.text
