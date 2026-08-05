"""CI guard for mechanically-derived sanitization pipeline revisions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_retrieval_root = str(Path(__file__).resolve().parents[2] / "services" / "retrieval")
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from pipeline import sanitizer_revision  # noqa: E402, I001  # type: ignore[reportMissingImports]


@pytest.mark.parametrize(
    "source_name",
    ("stage2_structural.py", "stage3_promptguard.py", "stage4_structuring.py"),
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
