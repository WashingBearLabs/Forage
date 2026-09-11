"""Vendoring the pinned weights to the private GHCR mirror.

``feature-forage-model-bootstrap`` US-003, **build half**. The story's other
half is a supervised ops run that holds the owner's Hugging Face token and a
GHCR credential; nothing here has either, and the suite's autouse socket guard
means nothing here *could* reach a registry even by accident. So every test in
this module is fixture-driven or mocked at a seam, and what it proves is the
part that has to be right before a human runs the script for real:

* the **tar dereferences symlinks** — a real hub tree is built with links into
  ``blobs/``, archived, extracted, and the extracted bytes compared; a naive
  ``tar`` here ships dangling links and ~0 bytes and the failure only surfaces
  on the far side of a pull;
* the tar is **deterministic** — same snapshot in, same bytes out, including
  after the source files' mtimes move;
* the allowlist is enforced **at generation time** — a manifest blessing a
  ``.bin`` is not merely refused later, it cannot be produced;
* a generated manifest **round-trips through the real verifier**
  (:func:`model_fetcher.verify_weights`) against an extracted tree;
* **no credential reaches an argv or a printed line**, on the success path or
  the failure path.

test_mapping:
  scripts/vendor_weights.py: tests/test_vendor_weights.py
  scripts/__init__.py: tests/test_vendor_weights.py
"""

from __future__ import annotations

import ast
import io
import json
import os
import tarfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

import model_fetcher
from model_fetcher import (
    ALLOW_PATTERNS,
    DEFAULT_MODEL_REVISION,
    HF_TOKEN_ENV_VAR,
    MANIFEST_PATH,
    REASON_FILE_EXTRA,
    REASON_HASH_MISMATCH,
    hub_cache_dir,
    read_manifest_pin,
    snapshot_path,
    verify_weights,
)
from promptguard.classifier import MODEL_ID
from scripts import vendor_weights
from scripts.vendor_weights import (
    GHCR_TOKEN_ENV_VAR,
    GHCR_USER_ENV_VAR,
    GITHUB_TOKEN_ENV_VAR,
    MIRROR_REPOSITORY,
    CommandResult,
    ManifestGenerationError,
    VendorError,
    build_parser,
    build_plan,
    build_tarball,
    collect_snapshot_files,
    describe,
    download_snapshot,
    enforce_allowlist,
    extract_and_verify,
    fetch_url,
    generate_manifest,
    main,
    manifest_diff,
    mirror_ref,
    package_api_paths,
    push_argv,
    push_artifact,
    read_manifest_document,
    redact,
    require_oras,
    verify_package_is_private,
    write_manifest,
)
from tests.fakes import materialize_hub_snapshot, sha256_hex

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_SOURCE = (_REPO_ROOT / "scripts" / "vendor_weights.py").read_text(
    encoding="utf-8"
)

_REVISION = DEFAULT_MODEL_REVISION
_FILES: Mapping[str, bytes] = {
    "config.json": b'{"model_type": "deberta-v2", "num_labels": 2}',
    "model.safetensors": b"\x00pretend-safetensors-payload\x00" * 8,
    "tokenizer.json": b'{"version": "1.0"}',
}

_FAKE_HF_TOKEN = "hf_" + "x" * 34
_FAKE_GHCR_TOKEN = "ghp_" + "y" * 36


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(
    cache_root: Path,
    files: Mapping[str, bytes] = _FILES,
    *,
    revision: str = _REVISION,
    symlinks: bool = True,
) -> Path:
    """A real hub cache layout under *cache_root*, returning the snapshot dir."""
    return materialize_hub_snapshot(
        cache_root,
        files,
        model_id=MODEL_ID,
        revision=revision,
        symlinks=symlinks,
    )


def _generated(snapshot: Path, *, revision: str = _REVISION) -> dict[str, Any]:
    """The manifest this script would commit for *snapshot*."""
    return generate_manifest(snapshot, model_id=MODEL_ID, revision=revision)


def _vendored(
    tmp_path: Path, files: Mapping[str, bytes] = _FILES
) -> tuple[Path, Path, Path]:
    """Download-equivalent + manifest + tarball. Returns the three paths."""
    cache_root = tmp_path / "model-cache"
    snapshot = _snapshot(cache_root, files)
    manifest_path = tmp_path / "weights_manifest.json"
    write_manifest(_generated(snapshot), manifest_path)
    tarball = build_tarball(snapshot, tmp_path / "weights.tar.gz")
    return cache_root, manifest_path, tarball


class _RecordingRunner:
    """A :class:`CommandRunner` double that records argv, stdin and cwd."""

    def __init__(self, *, failing: str | None = None, stderr: str = "") -> None:
        self.calls: list[tuple[tuple[str, ...], str | None, Path | None]] = []
        self._failing = failing
        self._stderr = stderr

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        cwd: Path | None = None,
    ) -> CommandResult:
        recorded = tuple(argv)
        self.calls.append((recorded, stdin, cwd))
        if self._failing is not None and self._failing in recorded:
            return CommandResult(returncode=1, stderr=self._stderr)
        return CommandResult(returncode=0, stdout="ok")

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        """Just the argv of every call, in order."""
        return [call[0] for call in self.calls]

    @property
    def subcommands(self) -> list[str]:
        """The `oras` subcommand of every call, in order."""
        return [call[0][1] for call in self.calls]


def _fetcher(
    responses: Mapping[str, tuple[int, bytes]],
) -> tuple[list[tuple[str, Mapping[str, str]]], vendor_weights.HttpFetcher]:
    """A recording :class:`HttpFetcher` over a URL → response mapping."""
    seen: list[tuple[str, Mapping[str, str]]] = []

    def _fetch(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        seen.append((url, dict(headers)))
        return responses.get(url, (404, b'{"message": "Not Found"}'))

    return seen, _fetch


def _package_body(visibility: str) -> bytes:
    """A GitHub packages API response body."""
    return json.dumps({"name": "forage-weights", "visibility": visibility}).encode()


def _code_string_literals() -> list[str]:
    """Every string constant in the script that is not a docstring."""
    tree = ast.parse(_MODULE_SOURCE)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


# ---------------------------------------------------------------------------
# One rule, one place
# ---------------------------------------------------------------------------


class TestSingleSourceOfTruth:
    """The script reuses the service's constants; it never restates them."""

    def test_the_model_id_comes_from_the_classifier(self) -> None:
        assert vendor_weights.MODEL_ID is MODEL_ID

    def test_the_revision_default_is_the_committed_pin(self) -> None:
        parser = build_parser()

        args = parser.parse_args([])

        assert args.revision == DEFAULT_MODEL_REVISION
        assert vendor_weights.DEFAULT_MODEL_REVISION is DEFAULT_MODEL_REVISION

    def test_the_allowlist_is_the_services_allowlist(self) -> None:
        assert vendor_weights.ALLOW_PATTERNS is ALLOW_PATTERNS
        assert vendor_weights.is_allowed_filename is model_fetcher.is_allowed_filename

    def test_no_constant_is_restated_as_a_literal(self) -> None:
        """A second copy of a rule is a second rule, and it will drift.

        Docstrings are exempt and deliberately so: prose naming the model or
        the format is documentation, not a second source of truth. What must
        not exist is a *code* literal the module could act on.
        """
        for needle in (".safetensors", "meta-llama/", DEFAULT_MODEL_REVISION):
            offenders = [
                literal for literal in _code_string_literals() if needle in literal
            ]
            assert offenders == [], (
                f"{needle!r} is spelled out in scripts/vendor_weights.py code; "
                "import it from model_fetcher / promptguard.classifier instead"
            )

    def test_the_mirror_tag_is_the_pinned_revision(self) -> None:
        """Leg three of US-001's triple lock, closed in the repository.

        The constant, the committed manifest's revision and the mirror tag are
        one fact. US-001 could only lock the first two because no tag existed;
        this story creates the tag, and it creates it *by deriving it from the
        constant* so the third leg cannot drift either. Whether the remote tag
        has actually been pushed is the supervised half's business, not a
        property of this repository.
        """
        document = cast(
            dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )

        ref = mirror_ref(DEFAULT_MODEL_REVISION)

        assert ref == f"{MIRROR_REPOSITORY}:{DEFAULT_MODEL_REVISION}"
        assert ref.endswith(f":{document['revision']}")
        assert MIRROR_REPOSITORY == "ghcr.io/washingbearlabs/forage-weights"

    def test_the_mirror_repository_is_lower_case(self) -> None:
        """GHCR rejects an upper-case path component."""
        assert MIRROR_REPOSITORY.lower() == MIRROR_REPOSITORY

    def test_the_script_is_not_shipped_in_the_image(self) -> None:
        dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        assert "scripts" not in dockerfile, (
            "the Dockerfile COPY list is filename-enumerated and vendoring is "
            "an operator task, not a runtime one — nothing under scripts/ "
            "belongs in the image"
        )


# ---------------------------------------------------------------------------
# The walk both the manifest and the tar are built from
# ---------------------------------------------------------------------------


class TestSnapshotWalk:
    """One walk, symlinks resolved, everything else refused."""

    def test_it_resolves_the_snapshots_symlinks(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        files = collect_snapshot_files(snapshot)

        assert [file.relative for file in files] == sorted(_FILES)
        for file in files:
            assert file.resolved.parent.name == "blobs"
            assert file.resolved.read_bytes() == _FILES[file.relative]

    def test_a_flat_snapshot_is_walked_too(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache", symlinks=False)

        files = collect_snapshot_files(snapshot)

        assert [file.relative for file in files] == sorted(_FILES)

    def test_nested_files_keep_a_relative_posix_path(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache", {"nested/config.json": b"{}"})

        files = collect_snapshot_files(snapshot)

        assert [file.relative for file in files] == ["nested/config.json"]

    def test_a_missing_snapshot_says_what_to_run(self, tmp_path: Path) -> None:
        with pytest.raises(VendorError) as excinfo:
            collect_snapshot_files(tmp_path / "nothing-here")

        assert "--step download" in str(excinfo.value)

    def test_an_empty_snapshot_is_refused(self, tmp_path: Path) -> None:
        empty = tmp_path / "snapshot"
        empty.mkdir()

        with pytest.raises(VendorError, match="nothing to vendor"):
            collect_snapshot_files(empty)

    def test_a_directory_symlink_is_refused_rather_than_followed(
        self, tmp_path: Path
    ) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        (snapshot / "elsewhere").symlink_to(tmp_path, target_is_directory=True)

        with pytest.raises(VendorError, match="not a regular file"):
            collect_snapshot_files(snapshot)

    def test_a_broken_symlink_is_refused(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        (snapshot / "dangling.json").symlink_to(tmp_path / "gone.json")

        with pytest.raises(VendorError, match="not a regular file"):
            collect_snapshot_files(snapshot)


# ---------------------------------------------------------------------------
# Generation-time allowlist — the half of the RCE closure this story owns
# ---------------------------------------------------------------------------


class TestGenerationTimeAllowlist:
    """A manifest that blesses a pickle must be ungeneratable."""

    def test_a_pickle_in_the_snapshot_stops_the_generation(
        self, tmp_path: Path
    ) -> None:
        snapshot = _snapshot(
            tmp_path / "cache", {**_FILES, "pytorch_model.bin": b"\x80\x04pickle"}
        )

        with pytest.raises(ManifestGenerationError) as excinfo:
            _generated(snapshot)

        assert "pytorch_model.bin" in str(excinfo.value)
        assert "RCE" in str(excinfo.value)

    def test_the_refusal_is_a_manifest_generation_error(self, tmp_path: Path) -> None:
        """Its own class, because it is the story's sharpest rule."""
        snapshot = _snapshot(tmp_path / "cache", {"pytorch_model.bin": b"x"})

        with pytest.raises(ManifestGenerationError):
            _generated(snapshot)

        assert issubclass(ManifestGenerationError, VendorError)

    @pytest.mark.parametrize(
        "name",
        ["pytorch_model.bin", "README.md", ".gitattributes", "weights.pt", "run.sh"],
    )
    def test_every_non_allowlisted_name_is_refused(
        self, tmp_path: Path, name: str
    ) -> None:
        snapshot = _snapshot(tmp_path / "cache", {**_FILES, name: b"payload"})

        with pytest.raises(ManifestGenerationError, match="non-allowlisted"):
            _generated(snapshot)

    def test_no_flag_makes_the_generator_emit_one(self) -> None:
        """There is no override: the check is unconditional in the function."""
        with pytest.raises(ManifestGenerationError):
            enforce_allowlist(
                [vendor_weights.SnapshotFile("pytorch_model.bin", Path("/dev/null"))]
            )

    def test_the_tarball_refuses_the_same_set(self, tmp_path: Path) -> None:
        """US-004 extracts the tarball into the directory the loader reads."""
        snapshot = _snapshot(tmp_path / "cache", {**_FILES, "pytorch_model.bin": b"x"})

        with pytest.raises(ManifestGenerationError):
            build_tarball(snapshot, tmp_path / "weights.tar.gz")

    def test_an_allowlisted_set_generates(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        document = _generated(snapshot)

        assert [entry["path"] for entry in document["files"]] == sorted(_FILES)


# ---------------------------------------------------------------------------
# The manifest itself
# ---------------------------------------------------------------------------


class TestManifestGeneration:
    """What the generated document says, and how it is written."""

    def test_every_entry_carries_the_real_sha256_and_size(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        document = _generated(snapshot)

        by_path = {entry["path"]: entry for entry in document["files"]}
        for name, payload in _FILES.items():
            assert by_path[name]["sha256"] == sha256_hex(payload)
            assert by_path[name]["size"] == len(payload)

    def test_it_hashes_the_blob_the_link_points_at(self, tmp_path: Path) -> None:
        """Not the link, and not a stale copy — the bytes a loader opens."""
        cache_root = tmp_path / "cache"
        snapshot = _snapshot(cache_root)
        blob = (snapshot / "config.json").resolve()
        blob.write_bytes(b'{"tampered": true}')

        document = _generated(snapshot)

        by_path = {entry["path"]: entry for entry in document["files"]}
        assert by_path["config.json"]["sha256"] == sha256_hex(b'{"tampered": true}')

    def test_it_pins_the_model_and_the_revision(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        document = _generated(snapshot)

        assert document["model_id"] == MODEL_ID
        assert document["revision"] == _REVISION

    def test_it_carries_the_bump_together_rule_in_the_file(
        self, tmp_path: Path
    ) -> None:
        """The rule has to be readable where the file is, not only in docs."""
        snapshot = _snapshot(tmp_path / "cache")

        comment = " ".join(_generated(snapshot)["_comment"])

        assert "ONE commit" in comment
        assert "docs/weights.md" in comment

    def test_the_entries_are_sorted(self, tmp_path: Path) -> None:
        snapshot = _snapshot(
            tmp_path / "cache",
            {"zeta.json": b"z", "alpha.json": b"a", "model.safetensors": b"m"},
        )

        document = _generated(snapshot)

        paths = [entry["path"] for entry in document["files"]]
        assert paths == sorted(paths)

    def test_it_is_written_as_formatted_json_with_a_trailing_newline(
        self, tmp_path: Path
    ) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        manifest_path = tmp_path / "weights_manifest.json"

        write_manifest(_generated(snapshot), manifest_path)

        raw = manifest_path.read_text(encoding="utf-8")
        assert raw.endswith("\n")
        assert "\n  " in raw
        assert read_manifest_document(manifest_path) is not None

    def test_an_unreadable_document_reads_as_none(self, tmp_path: Path) -> None:
        assert read_manifest_document(tmp_path / "absent.json") is None
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert read_manifest_document(broken) is None
        listy = tmp_path / "list.json"
        listy.write_text("[]", encoding="utf-8")
        assert read_manifest_document(listy) is None


class TestManifestRoundTrip:
    """The generated manifest must satisfy the *real* verifier."""

    def test_a_generated_manifest_verifies_the_snapshot_it_describes(
        self, tmp_path: Path
    ) -> None:
        cache_root = tmp_path / "cache"
        snapshot = _snapshot(cache_root)
        manifest_path = tmp_path / "weights_manifest.json"
        write_manifest(_generated(snapshot), manifest_path)

        result = verify_weights(cache_root, manifest_path=manifest_path)

        assert result.ok is True
        assert result.failures == ()

    def test_it_is_a_readable_pin(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        manifest_path = tmp_path / "weights_manifest.json"
        write_manifest(_generated(snapshot), manifest_path)

        pin = read_manifest_pin(manifest_path)

        assert pin is not None
        assert (pin.model_id, pin.revision) == (MODEL_ID, _REVISION)
        assert pin.total_bytes == sum(len(payload) for payload in _FILES.values())

    def test_tampering_after_generation_is_caught(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "cache"
        snapshot = _snapshot(cache_root)
        manifest_path = tmp_path / "weights_manifest.json"
        write_manifest(_generated(snapshot), manifest_path)
        (snapshot / "model.safetensors").resolve().write_bytes(b"swapped")

        result = verify_weights(cache_root, manifest_path=manifest_path)

        assert result.ok is False
        assert REASON_HASH_MISMATCH in result.reasons

    def test_an_extra_file_after_generation_is_caught(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "cache"
        snapshot = _snapshot(cache_root)
        manifest_path = tmp_path / "weights_manifest.json"
        write_manifest(_generated(snapshot), manifest_path)
        (snapshot / "surprise.json").write_text("{}", encoding="utf-8")

        result = verify_weights(cache_root, manifest_path=manifest_path)

        assert result.ok is False
        assert REASON_FILE_EXTRA in result.reasons


class TestManifestDiff:
    """Regenerating prints what changed, because a re-vendor should be read."""

    def test_a_first_generation_says_so(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        lines = manifest_diff(None, _generated(snapshot))

        assert lines[0] == "no readable previous manifest — this is a first generation"

    def test_an_identical_regeneration_reports_no_change(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        document = _generated(snapshot)

        assert manifest_diff(document, _generated(snapshot)) == ("no change",)

    def test_a_changed_hash_is_reported_as_a_change(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "cache"
        snapshot = _snapshot(cache_root)
        before = _generated(snapshot)
        (snapshot / "config.json").resolve().write_bytes(b'{"changed": true}')

        lines = manifest_diff(before, _generated(snapshot))

        assert any(line.startswith("~ config.json sha256 ") for line in lines)

    def test_a_size_only_change_is_reported(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        before = _generated(snapshot)
        entry = before["files"][0]
        after = json.loads(json.dumps(before))
        after["files"][0]["size"] = cast(int, entry["size"]) + 1

        lines = manifest_diff(before, cast(dict[str, Any], after))

        assert any("size" in line for line in lines)

    def test_added_and_removed_files_are_reported(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        before = _generated(snapshot)
        (snapshot / "tokenizer.json").unlink()
        (snapshot / "vocab.txt").write_text("hello", encoding="utf-8")

        lines = manifest_diff(before, _generated(snapshot))

        assert any(line.startswith("- tokenizer.json") for line in lines)
        assert any(line.startswith("+ vocab.txt") for line in lines)

    def test_a_revision_bump_is_reported(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        before = _generated(snapshot)
        after = dict(before)
        after["revision"] = "b" * 40

        lines = manifest_diff(before, after)

        assert any(line.startswith("revision: ") for line in lines)

    def test_a_malformed_previous_manifest_does_not_crash_the_diff(
        self, tmp_path: Path
    ) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        lines = manifest_diff({"files": "not a list"}, _generated(snapshot))

        assert any(line.startswith("+ ") for line in lines)


# ---------------------------------------------------------------------------
# The tarball
# ---------------------------------------------------------------------------


class TestDereferencedTar:
    """The round-2 critical: a snapshot is a tree of links, not of bytes."""

    def test_the_source_snapshot_really_is_symlinks(self, tmp_path: Path) -> None:
        """Guard on the fixture — without this the test below proves nothing."""
        snapshot = _snapshot(tmp_path / "cache")

        assert all(entry.is_symlink() for entry in snapshot.iterdir())

    def test_the_archive_carries_real_bytes_not_links(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        tarball = build_tarball(snapshot, tmp_path / "weights.tar.gz")

        with tarfile.open(tarball, "r:gz") as archive:
            members = archive.getmembers()
        assert {member.name for member in members} == set(_FILES)
        for member in members:
            assert member.isfile(), f"{member.name} is not a regular file"
            assert member.issym() is False
            assert member.size == len(_FILES[member.name])

    def test_extracting_it_reproduces_the_original_bytes(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        tarball = build_tarball(snapshot, tmp_path / "weights.tar.gz")
        destination = tmp_path / "extracted"
        destination.mkdir()

        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(destination, filter="data")

        for name, payload in _FILES.items():
            extracted = destination / name
            assert extracted.is_symlink() is False
            assert extracted.read_bytes() == payload

    def test_a_naive_archive_would_have_shipped_nothing(self, tmp_path: Path) -> None:
        """The failure mode, demonstrated, so the guard above has meaning."""
        snapshot = _snapshot(tmp_path / "cache")
        naive = tmp_path / "naive.tar.gz"
        with tarfile.open(naive, "w:gz") as archive:
            for entry in sorted(snapshot.iterdir()):
                archive.add(entry, arcname=entry.name, recursive=False)

        with tarfile.open(naive, "r:gz") as archive:
            members = archive.getmembers()

        assert all(member.issym() for member in members)
        assert all(member.size == 0 for member in members)


class TestDeterministicTar:
    """Same snapshot in, same bytes out — otherwise it cannot be re-derived."""

    def test_two_builds_of_the_same_snapshot_are_byte_identical(
        self, tmp_path: Path
    ) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        first = build_tarball(snapshot, tmp_path / "one.tar.gz").read_bytes()
        second = build_tarball(snapshot, tmp_path / "two.tar.gz").read_bytes()

        assert first == second

    def test_moving_the_source_mtimes_changes_nothing(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")
        first = build_tarball(snapshot, tmp_path / "one.tar.gz").read_bytes()
        for file in collect_snapshot_files(snapshot):
            os.utime(file.resolved, (1_000_000, 1_000_000))

        second = build_tarball(snapshot, tmp_path / "two.tar.gz").read_bytes()

        assert first == second

    def test_every_member_has_pinned_metadata(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path / "cache")

        tarball = build_tarball(snapshot, tmp_path / "weights.tar.gz")

        with tarfile.open(tarball, "r:gz") as archive:
            for member in archive.getmembers():
                assert member.mtime == 0
                assert member.mode == 0o644
                assert (member.uid, member.gid) == (0, 0)
                assert (member.uname, member.gname) == ("", "")

    def test_the_members_are_written_in_sorted_order(self, tmp_path: Path) -> None:
        snapshot = _snapshot(
            tmp_path / "cache",
            {"zeta.json": b"z", "alpha.json": b"a", "model.safetensors": b"m"},
        )

        tarball = build_tarball(snapshot, tmp_path / "weights.tar.gz")

        with tarfile.open(tarball, "r:gz") as archive:
            names = archive.getnames()
        assert names == sorted(names)

    def test_the_gzip_wrapper_carries_no_timestamp_or_filename(
        self, tmp_path: Path
    ) -> None:
        """`tarfile.open(mode="w:gz")` stamps the clock; this must not."""
        snapshot = _snapshot(tmp_path / "cache")

        tarball = build_tarball(snapshot, tmp_path / "weights.tar.gz")

        header = tarball.read_bytes()[:10]
        assert header[:2] == b"\x1f\x8b"
        assert header[4:8] == b"\x00\x00\x00\x00", "gzip mtime must be zero"
        assert header[3] & 0x08 == 0, "gzip header must carry no source filename"


class TestExtractAndVerify:
    """The proof the artifact is good, run before it is published."""

    def test_a_freshly_built_tarball_verifies(self, tmp_path: Path) -> None:
        _cache, manifest_path, tarball = _vendored(tmp_path)

        result = extract_and_verify(tarball, manifest_path=manifest_path)

        assert result.ok is True

    def test_it_uses_the_real_verifier(self, tmp_path: Path) -> None:
        """Not a re-implementation — what passes here passes in the service."""
        _cache, manifest_path, tarball = _vendored(tmp_path)

        with (
            patch.object(
                model_fetcher, "verify_weights", wraps=verify_weights
            ) as verifier,
            patch.object(vendor_weights, "verify_weights", verifier),
        ):
            extract_and_verify(tarball, manifest_path=manifest_path)

        assert cast(MagicMock, verifier).call_count == 1

    def test_a_tarball_that_does_not_match_the_manifest_is_refused(
        self, tmp_path: Path
    ) -> None:
        _cache, manifest_path, _tarball = _vendored(tmp_path)
        other = _snapshot(
            tmp_path / "other", {"config.json": b"{}"}, revision=_REVISION
        )
        mismatched = build_tarball(other, tmp_path / "other.tar.gz")

        result = extract_and_verify(mismatched, manifest_path=manifest_path)

        assert result.ok is False

    def test_an_unusable_manifest_stops_the_check(self, tmp_path: Path) -> None:
        _cache, _manifest, tarball = _vendored(tmp_path)
        placeholder = tmp_path / "placeholder.json"
        placeholder.write_text(
            json.dumps({"model_id": MODEL_ID, "revision": _REVISION, "files": []}),
            encoding="utf-8",
        )

        with pytest.raises(VendorError, match="--step manifest"):
            extract_and_verify(tarball, manifest_path=placeholder)

    def test_extraction_refuses_a_path_that_escapes_the_destination(
        self, tmp_path: Path
    ) -> None:
        """`filter="data"` explicitly — 3.12 still defaults to the loose one."""
        _cache, manifest_path, _tarball = _vendored(tmp_path)
        hostile = tmp_path / "hostile.tar.gz"
        payload = b"pwned"
        with tarfile.open(hostile, "w:gz") as archive:
            info = tarfile.TarInfo(name="../escape.json")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

        with pytest.raises(tarfile.TarError):
            extract_and_verify(hostile, manifest_path=manifest_path)

        assert not (tmp_path / "escape.json").exists()


# ---------------------------------------------------------------------------
# The download
# ---------------------------------------------------------------------------


class TestDownload:
    """The same arguments the service's own fetch passes, or it is not a mirror."""

    def test_it_is_pinned_filtered_and_placed(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "cache"

        def _fake_download(repo_id: str, **kwargs: Any) -> str:
            return str(
                materialize_hub_snapshot(
                    Path(str(kwargs["cache_dir"])).parent,
                    _FILES,
                    model_id=repo_id,
                    revision=str(kwargs["revision"]),
                )
            )

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _fake_download
            download_snapshot(
                revision=_REVISION, cache_root=cache_root, token=_FAKE_HF_TOKEN
            )

        call = cast(MagicMock, download).call_args
        assert call.args == (MODEL_ID,)
        assert call.kwargs["revision"] == _REVISION
        assert Path(call.kwargs["cache_dir"]) == hub_cache_dir(cache_root)
        assert set(call.kwargs["allow_patterns"]) == set(ALLOW_PATTERNS)
        assert call.kwargs["token"] == _FAKE_HF_TOKEN
        assert "local_dir" not in call.kwargs

    def test_without_a_token_nothing_is_attempted(self, tmp_path: Path) -> None:
        with (
            patch("huggingface_hub.snapshot_download") as download,
            pytest.raises(VendorError, match=HF_TOKEN_ENV_VAR),
        ):
            download_snapshot(
                revision=_REVISION, cache_root=tmp_path / "cache", token=None
            )

        cast(MagicMock, download).assert_not_called()

    def test_a_failed_download_never_echoes_the_token(self, tmp_path: Path) -> None:
        class _LeakyError(RuntimeError):
            """An exception whose message carries the credential, as HF's do."""

        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = _LeakyError(
                f"401 for https://huggingface.co?token={_FAKE_HF_TOKEN}"
            )
            with pytest.raises(VendorError) as excinfo:
                download_snapshot(
                    revision=_REVISION,
                    cache_root=tmp_path / "cache",
                    token=_FAKE_HF_TOKEN,
                )

        message = str(excinfo.value)
        assert _FAKE_HF_TOKEN not in message
        assert "_LeakyError" in message
        assert MODEL_ID in message

    def test_a_download_that_lands_nothing_is_reported(self, tmp_path: Path) -> None:
        with patch("huggingface_hub.snapshot_download") as download:
            download.return_value = "/nowhere"
            with pytest.raises(VendorError, match="does not exist"):
                download_snapshot(
                    revision=_REVISION,
                    cache_root=tmp_path / "cache",
                    token=_FAKE_HF_TOKEN,
                )


# ---------------------------------------------------------------------------
# The push
# ---------------------------------------------------------------------------


class TestOrasRequirement:
    """Detected and reported, never assumed."""

    def test_it_returns_the_binary_when_present(self) -> None:
        with patch("shutil.which", return_value="/opt/bin/oras"):
            assert require_oras() == "/opt/bin/oras"

    def test_a_missing_binary_fails_loudly_with_install_guidance(self) -> None:
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(VendorError) as excinfo,
        ):
            require_oras()

        message = str(excinfo.value)
        assert "oras.land" in message
        assert "brew install oras" in message
        assert "--step push" in message

    def test_the_push_checks_before_it_does_anything(self, tmp_path: Path) -> None:
        tarball = tmp_path / "weights.tar.gz"
        tarball.write_bytes(b"payload")
        runner = _RecordingRunner()

        with (
            patch("shutil.which", return_value=None),
            pytest.raises(VendorError, match=r"oras\.land"),
        ):
            push_artifact(
                tarball_path=tarball,
                revision=_REVISION,
                username="octocat",
                token=_FAKE_GHCR_TOKEN,
                run=runner,
            )

        assert runner.calls == []


class TestPushCredentialHygiene:
    """`ps` is world-readable; a token may never be an argument."""

    def _push(self, tmp_path: Path, runner: _RecordingRunner, **overrides: Any) -> str:
        tarball = tmp_path / "weights.tar.gz"
        tarball.write_bytes(b"payload")
        kwargs: dict[str, Any] = {
            "tarball_path": tarball,
            "revision": _REVISION,
            "username": "octocat",
            "token": _FAKE_GHCR_TOKEN,
            "run": runner,
        }
        kwargs.update(overrides)
        with patch("shutil.which", return_value="/opt/bin/oras"):
            return push_artifact(**kwargs)

    def test_the_token_never_appears_in_any_argv(self, tmp_path: Path) -> None:
        runner = _RecordingRunner()

        self._push(tmp_path, runner)

        for argv in runner.argvs:
            assert _FAKE_GHCR_TOKEN not in " ".join(argv)

    def test_the_token_travels_on_stdin_to_login(self, tmp_path: Path) -> None:
        runner = _RecordingRunner()

        self._push(tmp_path, runner)

        login = runner.calls[0]
        assert login[0][1] == "login"
        assert "--password-stdin" in login[0]
        assert login[1] == _FAKE_GHCR_TOKEN

    def test_the_sequence_is_login_push_logout(self, tmp_path: Path) -> None:
        runner = _RecordingRunner()

        self._push(tmp_path, runner)

        assert runner.subcommands == ["login", "push", "logout"]

    def test_the_credential_is_dropped_even_when_the_push_fails(
        self, tmp_path: Path
    ) -> None:
        runner = _RecordingRunner(failing="push", stderr="denied")

        with pytest.raises(VendorError, match="oras push failed"):
            self._push(tmp_path, runner)

        assert runner.subcommands == ["login", "push", "logout"]

    def test_a_failure_message_is_redacted(self, tmp_path: Path) -> None:
        runner = _RecordingRunner(
            failing="push", stderr=f"unauthorized: token {_FAKE_GHCR_TOKEN} rejected"
        )

        with pytest.raises(VendorError) as excinfo:
            self._push(tmp_path, runner)

        assert _FAKE_GHCR_TOKEN not in str(excinfo.value)
        assert "***" in str(excinfo.value)

    def test_a_failed_login_stops_before_the_push(self, tmp_path: Path) -> None:
        runner = _RecordingRunner(failing="login", stderr="bad credentials")

        with pytest.raises(VendorError, match="oras login failed"):
            self._push(tmp_path, runner)

        assert runner.subcommands == ["login"]

    def test_a_failed_logout_does_not_fail_the_run(self, tmp_path: Path) -> None:
        runner = _RecordingRunner(failing="logout", stderr="not logged in")

        ref = self._push(tmp_path, runner)

        assert ref == mirror_ref(_REVISION)

    def test_missing_credentials_name_both_variables(self, tmp_path: Path) -> None:
        runner = _RecordingRunner()

        with pytest.raises(VendorError) as excinfo:
            self._push(tmp_path, runner, token=None)

        assert GHCR_USER_ENV_VAR in str(excinfo.value)
        assert GHCR_TOKEN_ENV_VAR in str(excinfo.value)
        assert runner.calls == []

    def test_the_push_runs_from_the_tarballs_directory(self, tmp_path: Path) -> None:
        """So the layer's title is the bare filename, not a local path."""
        runner = _RecordingRunner()

        self._push(tmp_path, runner)

        push = next(call for call in runner.calls if call[0][1] == "push")
        assert push[2] == tmp_path
        assert str(tmp_path) not in " ".join(push[0])

    def test_the_argv_is_tagged_by_revision_sha(self) -> None:
        argv = push_argv(
            oras="oras",
            ref=mirror_ref(_REVISION),
            tarball_name="weights.tar.gz",
            revision=_REVISION,
        )

        assert argv[1] == "push"
        assert argv[2].endswith(f":{_REVISION}")
        assert f"org.opencontainers.image.revision={_REVISION}" in argv
        assert any(part.endswith(vendor_weights.LAYER_MEDIA_TYPE) for part in argv)


class TestRedaction:
    """The last line of defence for anything a tool decides to echo."""

    def test_it_replaces_every_secret(self) -> None:
        assert redact("a=1 b=2", ["1", "2"]) == "a=*** b=***"

    def test_it_ignores_empty_and_missing_secrets(self) -> None:
        assert redact("nothing here", ["", None]) == "nothing here"


# ---------------------------------------------------------------------------
# The visibility check
# ---------------------------------------------------------------------------


class TestVisibilityCheck:
    """A private mirror is a decision, so it is verified rather than assumed."""

    def test_a_private_package_passes(self) -> None:
        org, _user = package_api_paths(MIRROR_REPOSITORY)
        seen, fetch = _fetcher({org: (200, _package_body("private"))})

        visibility = verify_package_is_private(token="ghp_read", fetch=fetch)

        assert visibility == "private"
        assert seen[0][0] == org

    def test_a_public_package_fails_loudly(self) -> None:
        org, _user = package_api_paths(MIRROR_REPOSITORY)
        _seen, fetch = _fetcher({org: (200, _package_body("public"))})

        with pytest.raises(VendorError) as excinfo:
            verify_package_is_private(token="ghp_read", fetch=fetch)

        assert "'public'" in str(excinfo.value)
        assert "package settings" in str(excinfo.value)

    def test_a_user_owned_package_is_found_after_the_org_404s(self) -> None:
        org, user = package_api_paths(MIRROR_REPOSITORY)
        seen, fetch = _fetcher({user: (200, _package_body("private"))})

        assert verify_package_is_private(token="ghp_read", fetch=fetch) == "private"
        assert [url for url, _headers in seen] == [org, user]

    def test_a_package_that_is_nowhere_is_reported(self) -> None:
        _seen, fetch = _fetcher({})

        with pytest.raises(VendorError, match="no package found"):
            verify_package_is_private(token="ghp_read", fetch=fetch)

    def test_a_permission_error_says_which_scope_is_missing(self) -> None:
        org, _user = package_api_paths(MIRROR_REPOSITORY)
        _seen, fetch = _fetcher({org: (403, b'{"message": "Forbidden"}')})

        with pytest.raises(VendorError, match="read:packages"):
            verify_package_is_private(token="ghp_read", fetch=fetch)

    def test_a_response_without_a_visibility_field_is_not_a_pass(self) -> None:
        org, _user = package_api_paths(MIRROR_REPOSITORY)
        _seen, fetch = _fetcher({org: (200, b'{"name": "forage-weights"}')})

        with pytest.raises(VendorError, match="no `visibility` field"):
            verify_package_is_private(token="ghp_read", fetch=fetch)

    def test_a_non_json_response_is_not_a_pass(self) -> None:
        org, _user = package_api_paths(MIRROR_REPOSITORY)
        _seen, fetch = _fetcher({org: (200, b"<html>maintenance</html>")})

        with pytest.raises(VendorError, match="non-JSON"):
            verify_package_is_private(token="ghp_read", fetch=fetch)

    def test_without_a_token_it_refuses_rather_than_assuming(self) -> None:
        _seen, fetch = _fetcher({})

        with pytest.raises(VendorError, match=GITHUB_TOKEN_ENV_VAR):
            verify_package_is_private(token=None, fetch=fetch)

    def test_the_token_travels_in_the_authorization_header(self) -> None:
        org, _user = package_api_paths(MIRROR_REPOSITORY)
        seen, fetch = _fetcher({org: (200, _package_body("private"))})

        verify_package_is_private(token="ghp_read", fetch=fetch)

        _url, headers = seen[0]
        assert headers["Authorization"] == "Bearer ghp_read"
        assert headers["X-GitHub-Api-Version"] == vendor_weights.GITHUB_API_VERSION

    def test_the_api_paths_cover_both_owner_kinds(self) -> None:
        org, user = package_api_paths(MIRROR_REPOSITORY)

        assert org == (
            "https://api.github.com/orgs/washingbearlabs/packages/container/"
            "forage-weights"
        )
        assert user == (
            "https://api.github.com/users/washingbearlabs/packages/container/"
            "forage-weights"
        )

    def test_a_malformed_repository_is_refused(self) -> None:
        with pytest.raises(VendorError) as excinfo:
            package_api_paths("forage-weights")

        assert "`<registry>/<owner>/<name>`" in str(excinfo.value)

    def test_the_api_client_refuses_plain_http(self) -> None:
        with pytest.raises(VendorError, match="non-https"):
            fetch_url("http://api.github.com/orgs/x", {})


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


class TestPlan:
    """What an invocation resolved to, before anything runs."""

    def test_the_work_dir_drives_the_cache_and_the_tarball(
        self, tmp_path: Path
    ) -> None:
        plan = build_plan(build_parser().parse_args(["--work-dir", str(tmp_path)]))

        assert plan.cache_root == tmp_path / "model-cache"
        assert plan.tarball_path.parent == tmp_path
        assert _REVISION in plan.tarball_path.name

    def test_the_snapshot_is_the_hub_layout_the_verifier_walks(
        self, tmp_path: Path
    ) -> None:
        plan = build_plan(build_parser().parse_args(["--work-dir", str(tmp_path)]))

        assert plan.snapshot_dir == snapshot_path(
            plan.cache_root, MODEL_ID, plan.revision
        )
        assert plan.snapshot_dir.is_relative_to(hub_cache_dir(plan.cache_root))

    def test_a_single_step_runs_alone(self) -> None:
        plan = build_plan(build_parser().parse_args(["--step", "tar"]))

        assert plan.steps == ("tar",)

    def test_all_runs_every_phase_in_order(self) -> None:
        plan = build_plan(build_parser().parse_args([]))

        assert plan.steps == (
            "download",
            "manifest",
            "tar",
            "selfcheck",
            "push",
            "visibility",
        )

    def test_the_default_manifest_is_the_committed_one(self) -> None:
        plan = build_plan(build_parser().parse_args([]))

        assert plan.manifest_path == MANIFEST_PATH

    def test_the_work_dir_default_is_outside_the_repository(self) -> None:
        plan = build_plan(build_parser().parse_args([]))

        assert not plan.cache_root.is_relative_to(_REPO_ROOT)
        assert not plan.tarball_path.is_relative_to(_REPO_ROOT)

    def test_the_plan_printout_carries_no_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HF_TOKEN_ENV_VAR, _FAKE_HF_TOKEN)
        monkeypatch.setenv(GHCR_TOKEN_ENV_VAR, _FAKE_GHCR_TOKEN)
        plan = build_plan(build_parser().parse_args([]))

        printed = "\n".join(describe(plan))

        assert _FAKE_HF_TOKEN not in printed
        assert _FAKE_GHCR_TOKEN not in printed
        assert MODEL_ID in printed
        assert mirror_ref(plan.revision) in printed

    def test_no_credential_is_a_command_line_flag(self) -> None:
        """A flag lands in shell history; an environment variable does not."""
        help_text = build_parser().format_help()

        for flag in ("--token", "--password", "--hf-token", "--ghcr-token"):
            assert flag not in help_text


class TestDryRun:
    """Everything that reads; nothing that writes to a network."""

    def test_it_stops_before_the_push_and_the_api_call(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache_root, manifest_path, _tarball = _vendored(tmp_path)

        with (
            patch.object(vendor_weights, "push_artifact") as push,
            patch.object(vendor_weights, "verify_package_is_private") as visibility,
            patch("huggingface_hub.snapshot_download") as download,
        ):
            status = main(
                [
                    "--dry-run",
                    "--cache-root",
                    str(cache_root),
                    "--manifest",
                    str(manifest_path),
                    "--tarball",
                    str(tmp_path / "weights.tar.gz"),
                    "--work-dir",
                    str(tmp_path),
                    "--step",
                    "push",
                ]
            )

        assert status == 0
        cast(MagicMock, push).assert_not_called()
        cast(MagicMock, visibility).assert_not_called()
        cast(MagicMock, download).assert_not_called()
        assert "dry run — would run: " in capsys.readouterr().out

    def test_it_prints_the_visibility_requests_it_would_make(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.object(vendor_weights, "verify_package_is_private") as visibility:
            main(["--dry-run", "--step", "visibility", "--work-dir", str(tmp_path)])

        cast(MagicMock, visibility).assert_not_called()
        out = capsys.readouterr().out
        for url in package_api_paths(MIRROR_REPOSITORY):
            assert url in out

    def test_the_local_phases_still_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache_root = tmp_path / "model-cache"
        _snapshot(cache_root)
        manifest_path = tmp_path / "weights_manifest.json"

        status = main(
            [
                "--dry-run",
                "--step",
                "manifest",
                "--cache-root",
                str(cache_root),
                "--manifest",
                str(manifest_path),
                "--work-dir",
                str(tmp_path),
            ]
        )

        assert status == 0
        assert read_manifest_pin(manifest_path) is not None
        assert "vendor_weights OK" in capsys.readouterr().out


class TestCli:
    """Exit status, and the shape of a failure."""

    def test_a_full_local_sequence_succeeds(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Manifest → tar → selfcheck, driven through the CLI as one run."""
        cache_root = tmp_path / "model-cache"
        _snapshot(cache_root)
        manifest_path = tmp_path / "weights_manifest.json"
        common = [
            "--cache-root",
            str(cache_root),
            "--manifest",
            str(manifest_path),
            "--work-dir",
            str(tmp_path),
        ]

        for step in ("manifest", "tar", "selfcheck"):
            assert main([*common, "--step", step]) == 0

        out = capsys.readouterr().out
        assert "the extracted tarball verifies against the committed manifest" in out

    def test_a_refusal_exits_non_zero_and_says_why(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status = main(
            [
                "--step",
                "manifest",
                "--cache-root",
                str(tmp_path / "empty"),
                "--manifest",
                str(tmp_path / "weights_manifest.json"),
                "--work-dir",
                str(tmp_path),
            ]
        )

        assert status == 1
        assert "vendor_weights FAILED" in capsys.readouterr().out

    def test_a_generation_time_refusal_reaches_the_exit_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache_root = tmp_path / "model-cache"
        _snapshot(cache_root, {**_FILES, "pytorch_model.bin": b"\x80\x04"})

        status = main(
            [
                "--step",
                "manifest",
                "--cache-root",
                str(cache_root),
                "--manifest",
                str(tmp_path / "weights_manifest.json"),
                "--work-dir",
                str(tmp_path),
            ]
        )

        assert status == 1
        assert "pytorch_model.bin" in capsys.readouterr().out

    def test_the_committed_manifest_is_never_touched_by_a_scoped_run(
        self, tmp_path: Path
    ) -> None:
        """A run pointed at a scratch manifest must not rewrite the real one."""
        before = MANIFEST_PATH.read_bytes()
        cache_root = tmp_path / "model-cache"
        _snapshot(cache_root)

        main(
            [
                "--step",
                "manifest",
                "--cache-root",
                str(cache_root),
                "--manifest",
                str(tmp_path / "weights_manifest.json"),
                "--work-dir",
                str(tmp_path),
            ]
        )

        assert MANIFEST_PATH.read_bytes() == before
