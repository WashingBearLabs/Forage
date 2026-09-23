"""Bounded resource limits for untrusted document extraction.

Classification concurrency intentionally no longer shares extraction concurrency's
default-is-the-maximum idiom: extraction remains memory-pinned to one worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from pipeline.config_bounds import bounded_int
from promptguard.classifier import CHUNK_OVERLAP, DEFAULT_MODEL_ID, MAX_SEQ_LEN

MEBIBYTE = 1024 * 1024
# Parent with the 22M model resident, but no classification in flight.
PARENT_RESERVATION_BYTES = 512 * MEBIBYTE
CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL: Mapping[str, int] = {DEFAULT_MODEL_ID: 0}
# Provisional, not measured: 1024 - 512 - 384 - 32 = 96 MiB residual;
# reserve 32 MiB of that as margin. Spec 7 replaces this with measured RSS deltas.
PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES = 64 * MEBIBYTE
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
_MAX_CLASSIFICATION_CONCURRENCY = 8


class ExtractionConfigurationError(ValueError):
    """Raised when extraction resource configuration exceeds safe bounds."""


def max_extracted_characters(max_chunks: int) -> int:
    """Derive the classifiable character ceiling from PromptGuard's live budget."""
    return (MAX_SEQ_LEN - CHUNK_OVERLAP) * max_chunks * CHARACTERS_PER_PROMPTGUARD_TOKEN


@dataclass(frozen=True, slots=True)
class ExtractionSettings:
    """Validated limits for one sidecar process.

    The reference 1 GiB envelope reserves 512 MiB for the parent with the
    22M model resident. The pypdf child's default is 384 MiB, configurable
    from 128 to 512 MiB. See docs/configuration.md, "Sizing the container",
    for the model, classification, configured child and cache reservations.
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
        max_input_bytes=bounded_int(
            extraction_config,
            "max_input_bytes",
            MAX_INPUT_BYTES,
            minimum=_MIN_INPUT_BYTES,
            maximum=MAX_INPUT_BYTES,
            error=ExtractionConfigurationError,
        ),
        max_pages=bounded_int(
            extraction_config,
            "max_pages",
            MAX_PDF_PAGES,
            minimum=1,
            maximum=MAX_PDF_PAGES,
            error=ExtractionConfigurationError,
        ),
        child_cpu_seconds=bounded_int(
            extraction_config,
            "child_cpu_seconds",
            MAX_CHILD_CPU_SECONDS,
            minimum=1,
            maximum=MAX_CHILD_CPU_SECONDS,
            error=ExtractionConfigurationError,
        ),
        child_address_space_bytes=bounded_int(
            extraction_config,
            "child_address_space_bytes",
            MAX_CHILD_ADDRESS_SPACE_BYTES,
            minimum=_MIN_CHILD_ADDRESS_SPACE_BYTES,
            maximum=_MAX_CHILD_ADDRESS_SPACE_BYTES,
            error=ExtractionConfigurationError,
        ),
        wall_clock_seconds=bounded_int(
            extraction_config,
            "wall_clock_seconds",
            MAX_EXTRACTION_WALL_SECONDS,
            minimum=1,
            maximum=MAX_EXTRACTION_WALL_SECONDS,
            error=ExtractionConfigurationError,
        ),
        max_promptguard_chunks=bounded_int(
            extraction_config,
            "max_promptguard_chunks",
            MAX_PROMPTGUARD_CHUNKS,
            minimum=1,
            maximum=MAX_PROMPTGUARD_CHUNKS,
            error=ExtractionConfigurationError,
        ),
        extraction_concurrency=bounded_int(
            extraction_config,
            "extraction_concurrency",
            EXTRACTION_CONCURRENCY,
            minimum=1,
            maximum=EXTRACTION_CONCURRENCY,
            error=ExtractionConfigurationError,
        ),
        classification_concurrency=bounded_int(
            extraction_config,
            "classification_concurrency",
            CLASSIFICATION_CONCURRENCY,
            minimum=1,
            maximum=_MAX_CLASSIFICATION_CONCURRENCY,
            error=ExtractionConfigurationError,
        ),
        admission_queue_depth=bounded_int(
            extraction_config,
            "admission_queue_depth",
            ADMISSION_QUEUE_DEPTH,
            minimum=0,
            maximum=_MAX_ADMISSION_QUEUE_DEPTH,
            error=ExtractionConfigurationError,
        ),
        max_queued_upload_bytes=bounded_int(
            extraction_config,
            "max_queued_upload_bytes",
            MAX_QUEUED_UPLOAD_BYTES,
            minimum=0,
            maximum=MAX_QUEUED_UPLOAD_BYTES,
            error=ExtractionConfigurationError,
        ),
    )
