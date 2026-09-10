"""CI guard for mechanically-derived sanitization pipeline revisions."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from model_fetcher import DEFAULT_MODEL_REVISION, MODEL_REVISION_ENV_VAR
from pipeline import sanitizer_revision
from promptguard.classifier import MODEL_ID


@pytest.mark.parametrize(
    "source_name",
    (
        "contract.py",
        "stage2_structural.py",
        "stage3_promptguard.py",
        "stage4_structuring.py",
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

    monkeypatch.setattr(sanitizer_revision, "MODEL_ID", "test/model-revision")

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
    as ``MODEL_ID`` alone, as the revision alone, or with the two run together
    without the separator that makes the pair unambiguous.
    """
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    expected = hashlib.sha256()
    pipeline_dir = Path(sanitizer_revision.__file__).parent
    for source_name in sanitizer_revision._REVISION_SOURCES:
        expected.update((pipeline_dir / source_name).read_bytes())
    expected.update(f"{MODEL_ID}@{DEFAULT_MODEL_REVISION}".encode())
    expected.update(b"0.85")

    assert (
        sanitizer_revision.derive_sanitizer_revision({"promptguard_threshold": 0.85})
        == expected.hexdigest()
    )
