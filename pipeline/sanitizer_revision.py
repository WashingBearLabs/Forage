"""Mechanical revisioning for security-sensitive sanitization behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from model_fetcher import resolve_revision
from promptguard.classifier import MODEL_ID

_REVISION_SOURCES = (
    "contract.py",
    "stage1_extraction.py",
    "stage1_pdf.py",
    "stage1_upload.py",
    "stage2_structural.py",
    "stage3_promptguard.py",
    "stage4_structuring.py",
    "orchestrator.py",
)


def derive_sanitizer_revision(config: dict[str, Any]) -> str:
    """Return an opaque hash of source, model identity, and active threshold.

    Model identity is ``MODEL_ID@revision``, not ``MODEL_ID`` alone
    (``feature-forage-model-bootstrap`` US-001). Weights arrive at runtime now,
    pinned by commit sha and overridable with ``FORAGE_MODEL_REVISION``, so two
    containers running the same code can be scanning with different weights —
    and a value that could not tell them apart would key a cache on a
    sanitization behaviour it does not actually describe. Adding the revision
    rotated this hash once, deliberately, with the before/after recorded in
    ``docs/bootstrap-notes.md``.
    """
    digest = hashlib.sha256()
    pipeline_dir = Path(__file__).parent
    for source_name in _REVISION_SOURCES:
        digest.update((pipeline_dir / source_name).read_bytes())
    digest.update(f"{MODEL_ID}@{resolve_revision()}".encode())
    digest.update(str(config.get("promptguard_threshold", 0.85)).encode("ascii"))
    return digest.hexdigest()
