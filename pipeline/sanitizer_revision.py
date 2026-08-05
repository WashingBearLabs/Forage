"""Mechanical revisioning for security-sensitive sanitization behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from promptguard.classifier import MODEL_ID

_REVISION_SOURCES = (
    "stage1_extraction.py",
    "stage1_pdf.py",
    "stage1_upload.py",
    "stage2_structural.py",
    "stage3_promptguard.py",
    "stage4_structuring.py",
    "orchestrator.py",
)


def derive_sanitizer_revision(config: dict[str, Any]) -> str:
    """Return an opaque hash of source, model identity, and active threshold."""
    digest = hashlib.sha256()
    pipeline_dir = Path(__file__).parent
    for source_name in _REVISION_SOURCES:
        digest.update((pipeline_dir / source_name).read_bytes())
    digest.update(MODEL_ID.encode("utf-8"))
    digest.update(str(config.get("promptguard_threshold", 0.85)).encode("ascii"))
    return digest.hexdigest()
