"""CI guard for mechanically-derived sanitization pipeline revisions."""

from __future__ import annotations

import hashlib
from pathlib import Path

import idna
import pytest

import model_fetcher
from model_fetcher import DEFAULT_MODEL_REVISION, MODEL_REVISION_ENV_VAR
from pipeline import sanitizer_revision
from promptguard.classifier import DEFAULT_MODEL_ID


def test_manifest_entry_memo_opens_manifest_once_across_revision_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conftest.clear_manifest_entry_cache resets the process-lifetime seam."""
    reads: list[Path] = []
    original = Path.read_text

    def count_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == model_fetcher.MANIFEST_PATH:
            reads.append(path)
        return original(path, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", count_read)
    pin = model_fetcher.read_manifest_pin()
    assert pin is not None
    assert model_fetcher.resolve_revision(DEFAULT_MODEL_ID) == pin.revision
    first = sanitizer_revision.derive_sanitizer_revision({})
    assert sanitizer_revision.derive_sanitizer_revision({}) == first
    assert reads == [model_fetcher.MANIFEST_PATH]
    assert model_fetcher._manifest_entry.cache_info().misses == 1


def test_unreadable_manifest_keeps_default_hash_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    before = sanitizer_revision.derive_sanitizer_revision({})
    model_fetcher._manifest_entry.cache_clear()
    original = Path.read_text
    reads = 0

    def unreadable(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal reads
        if path == model_fetcher.MANIFEST_PATH:
            reads += 1
            raise PermissionError("sensitive-path-must-not-be-logged")
        return original(path, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert model_fetcher.resolve_revision(DEFAULT_MODEL_ID) == DEFAULT_MODEL_REVISION
    assert sanitizer_revision.derive_sanitizer_revision({}) == before
    assert sanitizer_revision.derive_sanitizer_revision({}) == before
    assert reads == 1
    assert [record.getMessage() for record in caplog.records] == [
        "manifest_pin_unavailable — reason=manifest_unreadable"
    ]


@pytest.mark.parametrize("model_id", ["acme/unvendored", "", "invalid/\ninjected"])
def test_an_unpinned_model_still_has_a_total_deterministic_revision(
    monkeypatch: pytest.MonkeyPatch, model_id: str
) -> None:
    monkeypatch.setattr(sanitizer_revision, "DEFAULT_MODEL_ID", model_id)
    assert model_fetcher.resolve_revision(model_id) == "unpinned"
    first = sanitizer_revision.derive_sanitizer_revision({})
    assert len(first) == 64
    assert sanitizer_revision.derive_sanitizer_revision({}) == first


@pytest.mark.parametrize(
    "source_name",
    (
        "contract.py",
        "stage2_structural.py",
        "stage3_promptguard.py",
        "stage4_structuring.py",
        # Repo-root, in `_ROOT_REVISION_SOURCES` since
        # `hardening-search-sanitization` US-003: it decides which search
        # results are dropped and which fetches are refused.
        "url_validator.py",
    ),
)
def test_sanitizer_revision_changes_for_security_pipeline_source(
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
) -> None:
    """CI fails if a security-stage source ceases to affect the revision."""
    config = {"promptguard_threshold": 0.85}
    original_revision = sanitizer_revision.derive_sanitizer_revision(config)
    read_bytes = Path.read_bytes

    def changed_read_bytes(path: Path) -> bytes:
        source = read_bytes(path)
        if path.name == source_name:
            return source + b"\n# test revision input\n"
        return source

    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)

    assert sanitizer_revision.derive_sanitizer_revision(config) != original_revision


def test_sanitizer_revision_changes_for_behavior_config() -> None:
    """The PromptGuard threshold is part of the opaque cache-key revision."""
    assert sanitizer_revision.derive_sanitizer_revision(
        {"promptguard_threshold": 0.85}
    ) != sanitizer_revision.derive_sanitizer_revision({"promptguard_threshold": 0.86})


def test_sanitizer_revision_changes_for_promptguard_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PromptGuard model artifact identity is part of the revision."""
    config = {"promptguard_threshold": 0.85}
    original_revision = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setattr(sanitizer_revision, "DEFAULT_MODEL_ID", "test/model-revision")

    assert sanitizer_revision.derive_sanitizer_revision(config) != original_revision


def test_sanitizer_revision_changes_for_the_pinned_model_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weights arrive at runtime, so the pin is part of sanitization identity.

    Two containers running byte-identical code can now be scanning with
    different weights (``FORAGE_MODEL_REVISION``). A revision that could not
    tell them apart would key a cache on behaviour it does not describe —
    which is the one thing this value exists to prevent.
    """
    config = {"promptguard_threshold": 0.85}
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    at_the_pin = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setenv(MODEL_REVISION_ENV_VAR, "a" * 40)

    assert sanitizer_revision.derive_sanitizer_revision(config) != at_the_pin


def test_sanitizer_revision_is_stable_at_the_committed_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset override and the committed pin are the same input."""
    config = {"promptguard_threshold": 0.85}
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    unset = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setenv(MODEL_REVISION_ENV_VAR, DEFAULT_MODEL_REVISION)

    assert sanitizer_revision.derive_sanitizer_revision(config) == unset


def test_the_hashed_model_identity_is_model_id_at_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact composition, recomputed independently.

    Stronger than "the value moved when the revision moved", which a dozen
    wrong implementations also satisfy: this fails if the identity is hashed
    as ``DEFAULT_MODEL_ID`` alone, as the revision alone, or with the two run together
    without the separator that makes the pair unambiguous.

    Extended by ``hardening-search-sanitization`` US-003 to both new inputs,
    in exactly the order the code feeds them: the root sources after the
    ``pipeline/`` ones, ``idna@<version>`` after the model identity. Extended
    rather than weakened to "the value differs" — an order this test could not
    see is an order a cache key could not rely on.
    """
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    expected = hashlib.sha256()
    pipeline_dir = Path(sanitizer_revision.__file__).parent
    for source_name in sanitizer_revision._REVISION_SOURCES:
        expected.update((pipeline_dir / source_name).read_bytes())
    for root_source_name in sanitizer_revision._ROOT_REVISION_SOURCES:
        expected.update((pipeline_dir.parent / root_source_name).read_bytes())
    expected.update(f"{DEFAULT_MODEL_ID}@{DEFAULT_MODEL_REVISION}".encode())
    expected.update(f"idna@{idna.__version__}".encode())
    expected.update(b"0.85")

    assert (
        sanitizer_revision.derive_sanitizer_revision({"promptguard_threshold": 0.85})
        == expected.hexdigest()
    )


def test_the_root_sources_resolve_against_the_repo_root() -> None:
    """A repo-root entry is a path-resolution change, not a tuple entry.

    `_REVISION_SOURCES` names are resolved under `pipeline/`; a name added
    there would be looked for at `pipeline/url_validator.py`, which does not
    exist. The separate tuple is what makes the resolution explicit.
    """
    assert sanitizer_revision._ROOT_REVISION_SOURCES == ("url_validator.py",)
    pipeline_dir = Path(sanitizer_revision.__file__).parent
    for name in sanitizer_revision._ROOT_REVISION_SOURCES:
        assert (pipeline_dir.parent / name).is_file()
        assert not (pipeline_dir / name).exists()
        assert name not in sanitizer_revision._REVISION_SOURCES


def test_sanitizer_revision_changes_for_the_idna_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UTS-46 tables decide which hosts are dropped, so the version is an input.

    A lock bump that moves the tables is a sanitization change with no source
    byte to show for it — the same argument that put `DEFAULT_MODEL_ID@revision` in
    the hash, applied to the table the canonicaliser reads.
    """
    config = {"promptguard_threshold": 0.85}
    original_revision = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setattr(sanitizer_revision.idna, "__version__", "0.0-test")

    assert sanitizer_revision.derive_sanitizer_revision(config) != original_revision
