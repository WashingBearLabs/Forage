"""Pinned resource limits for untrusted document extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from promptguard.classifier import CHUNK_OVERLAP, MAX_SEQ_LEN

MEBIBYTE = 1024 * 1024
MAX_INPUT_BYTES = 50 * MEBIBYTE
MAX_EXTRACTED_OUTPUT_BYTES = 2 * MEBIBYTE
MAX_PDF_PAGES = 500
MAX_CHILD_CPU_SECONDS = 20
MAX_EXTRACTION_WALL_SECONDS = 90
MAX_CHILD_ADDRESS_SPACE_BYTES = 384 * MEBIBYTE
MAX_PROMPTGUARD_CHUNKS = 64
EXTRACTION_CONCURRENCY = 1
CLASSIFICATION_CONCURRENCY = 1
ADMISSION_QUEUE_DEPTH = 1
MAX_QUEUED_UPLOAD_BYTES = MAX_INPUT_BYTES
CHARACTERS_PER_PROMPTGUARD_TOKEN = 4

_MIN_INPUT_BYTES = MEBIBYTE
_MIN_CHILD_ADDRESS_SPACE_BYTES = 128 * MEBIBYTE
_MAX_CHILD_ADDRESS_SPACE_BYTES = 512 * MEBIBYTE
_MAX_ADMISSION_QUEUE_DEPTH = 4


class ExtractionConfigurationError(ValueError):
    """Raised when extraction resource configuration exceeds safe bounds."""


def max_extracted_characters(max_chunks: int) -> int:
    """Derive the classifiable character ceiling from PromptGuard's live budget."""
    return (MAX_SEQ_LEN - CHUNK_OVERLAP) * max_chunks * CHARACTERS_PER_PROMPTGUARD_TOKEN


@dataclass(frozen=True, slots=True)
class ExtractionSettings:
    """Validated limits for one sidecar process.

    The 1 GiB container reserves at least 512 MiB for the long-lived FastAPI,
    torch, and PromptGuard process. The spawned pypdf child is capped at
    384 MiB, so parser working memory cannot consume the parent's reservation.
    """

    route_enabled: bool = False
    max_input_bytes: int = MAX_INPUT_BYTES
    max_pages: int = MAX_PDF_PAGES
    child_cpu_seconds: int = MAX_CHILD_CPU_SECONDS
    child_address_space_bytes: int = MAX_CHILD_ADDRESS_SPACE_BYTES
    wall_clock_seconds: int = MAX_EXTRACTION_WALL_SECONDS
    max_promptguard_chunks: int = MAX_PROMPTGUARD_CHUNKS
    extraction_concurrency: int = EXTRACTION_CONCURRENCY
    classification_concurrency: int = CLASSIFICATION_CONCURRENCY
    admission_queue_depth: int = ADMISSION_QUEUE_DEPTH
    max_queued_upload_bytes: int = MAX_QUEUED_UPLOAD_BYTES

    @property
    def max_extracted_characters(self) -> int:
        """Return the character ceiling corresponding to the chunk budget."""
        return max_extracted_characters(self.max_promptguard_chunks)

    @property
    def max_ipc_result_bytes(self) -> int:
        """Return the capped, length-framed child result payload size."""
        return min(
            MAX_EXTRACTED_OUTPUT_BYTES,
            self.max_extracted_characters * 4 + 8 * 1024,
        )

    @property
    def max_extracted_output_bytes(self) -> int:
        """Return the hard UTF-8 output ceiling before classification."""
        return min(MAX_EXTRACTED_OUTPUT_BYTES, self.max_extracted_characters * 4)


def _bounded_int(
    config: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read one bounded integer setting without accepting bool values."""
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExtractionConfigurationError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ExtractionConfigurationError(
            f"{key} must be between {minimum} and {maximum}"
        )
    return value


def extraction_settings_from_config(config: dict[str, Any]) -> ExtractionSettings:
    """Build bounded extraction settings from the sidecar configuration."""
    route_enabled = config.get("extract_route_enabled", False)
    if not isinstance(route_enabled, bool):
        raise ExtractionConfigurationError("extract_route_enabled must be a boolean")

    raw_extraction_config = config.get("extraction", {})
    if not isinstance(raw_extraction_config, dict):
        raise ExtractionConfigurationError("extraction must be a mapping")
    extraction_config = cast(dict[str, Any], raw_extraction_config)

    return ExtractionSettings(
        route_enabled=route_enabled,
        max_input_bytes=_bounded_int(
            extraction_config,
            "max_input_bytes",
            MAX_INPUT_BYTES,
            minimum=_MIN_INPUT_BYTES,
            maximum=MAX_INPUT_BYTES,
        ),
        max_pages=_bounded_int(
            extraction_config,
            "max_pages",
            MAX_PDF_PAGES,
            minimum=1,
            maximum=MAX_PDF_PAGES,
        ),
        child_cpu_seconds=_bounded_int(
            extraction_config,
            "child_cpu_seconds",
            MAX_CHILD_CPU_SECONDS,
            minimum=1,
            maximum=MAX_CHILD_CPU_SECONDS,
        ),
        child_address_space_bytes=_bounded_int(
            extraction_config,
            "child_address_space_bytes",
            MAX_CHILD_ADDRESS_SPACE_BYTES,
            minimum=_MIN_CHILD_ADDRESS_SPACE_BYTES,
            maximum=_MAX_CHILD_ADDRESS_SPACE_BYTES,
        ),
        wall_clock_seconds=_bounded_int(
            extraction_config,
            "wall_clock_seconds",
            MAX_EXTRACTION_WALL_SECONDS,
            minimum=1,
            maximum=MAX_EXTRACTION_WALL_SECONDS,
        ),
        max_promptguard_chunks=_bounded_int(
            extraction_config,
            "max_promptguard_chunks",
            MAX_PROMPTGUARD_CHUNKS,
            minimum=1,
            maximum=MAX_PROMPTGUARD_CHUNKS,
        ),
        extraction_concurrency=_bounded_int(
            extraction_config,
            "extraction_concurrency",
            EXTRACTION_CONCURRENCY,
            minimum=1,
            maximum=EXTRACTION_CONCURRENCY,
        ),
        classification_concurrency=_bounded_int(
            extraction_config,
            "classification_concurrency",
            CLASSIFICATION_CONCURRENCY,
            minimum=1,
            maximum=CLASSIFICATION_CONCURRENCY,
        ),
        admission_queue_depth=_bounded_int(
            extraction_config,
            "admission_queue_depth",
            ADMISSION_QUEUE_DEPTH,
            minimum=0,
            maximum=_MAX_ADMISSION_QUEUE_DEPTH,
        ),
        max_queued_upload_bytes=_bounded_int(
            extraction_config,
            "max_queued_upload_bytes",
            MAX_QUEUED_UPLOAD_BYTES,
            minimum=0,
            maximum=MAX_QUEUED_UPLOAD_BYTES,
        ),
    )
