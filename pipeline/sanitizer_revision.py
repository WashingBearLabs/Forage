"""Mechanical revisioning for security-sensitive sanitization behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import idna

from model_fetcher import resolve_model_id, resolve_revision

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

# Hashed sources that live at the repo root rather than under ``pipeline/``,
# resolved against ``pipeline_dir.parent`` and hashed in order *after* the
# tuple above. ``url_validator.py`` decides which search results are dropped
# and which fetches are refused -- the canonicaliser, the numeric classifier
# and the embedded-address unwraps all live there -- so leaving it out would
# put the sanitization code the revision exists to describe in the one file
# the revision cannot see.
_ROOT_REVISION_SOURCES = ("url_validator.py",)


def derive_sanitizer_revision(config: dict[str, Any]) -> str:
    """Return an opaque hash of source, model identity, and configured threshold.

    The configured value is hashed unchanged; the active, handler-resolved
    threshold reaches the content cache key through ``cache_policy_fingerprint``.

    Model identity is ``resolved_model_id@revision``, not the model id alone
    (``feature-forage-model-bootstrap`` US-001). Weights arrive at runtime now,
    pinned by commit sha and overridable with ``FORAGE_MODEL_REVISION``, so two
    containers running the same code can be scanning with different weights —
    and a value that could not tell them apart would key a cache on a
    sanitization behaviour it does not actually describe. Adding the revision
    rotated this hash once, deliberately, with the before/after recorded in
    ``docs/bootstrap-notes.md``.

    ``url_validator.py`` is hashed too (``_ROOT_REVISION_SOURCES``,
    ``hardening-search-sanitization`` US-003), after the eight ``pipeline/``
    sources and resolved against the repo root rather than ``pipeline/``. It
    is a sanitization source in every sense that matters: ``canonicalize_host``
    decides which spelling of a host is compared, ``private_address_class``
    decides which addresses are refused, and the search-time audit drops
    results on both. A file whose edit changes what ``/search`` serves belongs
    in a hash that describes what ``/search`` serves.

    ``idna@<version>`` is hashed beside the model identity for the same reason
    the revision is hashed at all: UTS-46 mapping tables change between
    ``idna`` releases, and which host a given spelling canonicalises to is
    decided by those tables, not by this repository. A lock bump that moves
    the tables is a sanitization change with no source byte to show for it,
    and a cache keyed on a value that could not see it would serve decisions
    the running code no longer makes.
    """
    digest = hashlib.sha256()
    pipeline_dir = Path(__file__).parent
    for source_name in _REVISION_SOURCES:
        digest.update((pipeline_dir / source_name).read_bytes())
    for root_source_name in _ROOT_REVISION_SOURCES:
        digest.update((pipeline_dir.parent / root_source_name).read_bytes())
    model_id = resolve_model_id()[0]
    digest.update(f"{model_id}@{resolve_revision(model_id)}".encode())
    digest.update(f"idna@{idna.__version__}".encode())
    digest.update(str(config.get("promptguard_threshold", 0.85)).encode("ascii"))
    return digest.hexdigest()
