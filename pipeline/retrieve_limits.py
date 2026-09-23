"""Pinned resource limits for the ``/retrieve`` fetch route.

Modelled on ``pipeline/extraction_limits.py``, which does the same job for
``/extract``. The two blocks stay separate because the routes take untrusted
input from different places — ``/extract`` from an authenticated upload,
``/retrieve`` from whatever the fetched origin returns — and an operator
raising one ceiling is not asking to raise the other.

This module is deliberately *not* a ``_REVISION_SOURCES`` member
(``pipeline/sanitizer_revision.py``): it holds no sanitization behaviour, only
the bounds an operator configures, and those already reach the cache key
through ``cache_policy_fingerprint``'s inputs where they change what is
served.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pipeline.config_bounds import bounded_bool, bounded_float, bounded_int
from pipeline.extraction_limits import max_extracted_characters
from pipeline.stage5_url_audit import DEFAULT_MAX_CONTENT_BYTES

# ``0`` means "no pre-check, and no ``max_chunks`` passed to the classifier" —
# exactly the behaviour that shipped before this key existed. It is the
# shipped default for one minor release (``contract/GOVERNANCE.md`` ruling (g),
# worked example 6 step 1); the next MINOR flips it to
# ``COMING_MAX_PROMPTGUARD_CHUNKS`` and ``0`` stays a legal opt-out after that.
RETRIEVE_MAX_PROMPTGUARD_CHUNKS = 0
COMING_MAX_PROMPTGUARD_CHUNKS = 256
_MAX_RETRIEVE_PROMPTGUARD_CHUNKS = 1024

# Pinned at exactly one, the way ``extraction.extraction_concurrency`` is: a
# fetched PDF spawns the same worker ``/extract`` does under the same
# ``child_address_space_bytes`` rlimit, so N fetch slots would put N x 384 MiB
# of worker address space in a 1 GiB container. The constraint is worker
# address space, not fetched-body size.
FETCH_CONCURRENCY = 1

RETRIEVE_ADMISSION_QUEUE_DEPTH = 4
_MAX_RETRIEVE_ADMISSION_QUEUE_DEPTH = 16

# Three fetched bodies at the 10 MB fetch cap. Deliberately *not*
# ``admission_queue_depth x DEFAULT_MAX_CONTENT_BYTES``: at the shipped
# defaults the byte bound binds before the depth bound, so both bounds are
# exercisable rather than one being dead configuration.
MAX_QUEUED_FETCH_BYTES = 3 * DEFAULT_MAX_CONTENT_BYTES
_MIN_QUEUED_FETCH_BYTES = DEFAULT_MAX_CONTENT_BYTES
_MAX_QUEUED_FETCH_BYTES = 16 * DEFAULT_MAX_CONTENT_BYTES

PROMPTGUARD_FAIL_CLOSED_FLOOR = False
PROMPTGUARD_THRESHOLD_CEILING = 1.0

# A float, not an int: ``sanitize_and_structure`` takes
# ``classification_wait_seconds: float | None`` and the tests that exercise the
# timeout need sub-second values inside the range.
PROMPTGUARD_WAIT_SECONDS = 30.0
_MIN_PROMPTGUARD_WAIT_SECONDS = 0.05
_MAX_PROMPTGUARD_WAIT_SECONDS = 300.0


class RetrieveConfigurationError(ValueError):
    """Raised when ``/retrieve`` resource configuration exceeds safe bounds."""


@dataclass(frozen=True, slots=True)
class RetrieveSettings:
    """Validated limits for one fetched page.

    At most one fetched body is held, from fetch through stage 1; none while
    waiting on classification. The PDF branch spawns the bounded pypdf worker
    ``/extract`` uses, which is why ``fetch_concurrency`` is pinned at one; the
    HTML branch spawns no worker and inherits the same slot for its stage-1
    memory peak (body + decoded copy + extraction result), so an HTML-only
    deployment is throttled to single flight by a bound sized for PDFs. That is
    accepted and stated here so a later reader does not re-derive it; spec 6
    widens the range when it sizes the envelope.
    """

    max_promptguard_chunks: int = RETRIEVE_MAX_PROMPTGUARD_CHUNKS
    fetch_concurrency: int = FETCH_CONCURRENCY
    admission_queue_depth: int = RETRIEVE_ADMISSION_QUEUE_DEPTH
    max_queued_fetch_bytes: int = MAX_QUEUED_FETCH_BYTES
    promptguard_fail_closed_floor: bool = PROMPTGUARD_FAIL_CLOSED_FLOOR
    promptguard_threshold_ceiling: float = PROMPTGUARD_THRESHOLD_CEILING
    promptguard_wait_seconds: float = PROMPTGUARD_WAIT_SECONDS

    @property
    def max_extracted_characters(self) -> int | None:
        """Return the character ceiling, or ``None`` when there is no budget.

        Mirrors ``ExtractionSettings.max_extracted_characters`` so the
        pre-check call site reads the same way on both routes, except that this
        one is nullable: ``max_promptguard_chunks: 0`` means no pre-check at
        all rather than a ceiling of zero characters.
        """
        if self.max_promptguard_chunks == 0:
            return None
        return max_extracted_characters(self.max_promptguard_chunks)


def retrieve_settings_from_config(config: dict[str, Any]) -> RetrieveSettings:
    """Build bounded ``/retrieve`` settings from the sidecar configuration.

    Reads the ``retrieve:`` block and the three top-level PromptGuard policy
    keys, because this function owns the boot-validated fetch-route policy: an
    out-of-range value refuses boot here rather than surfacing as a strange
    refusal on the first request.
    """
    raw_retrieve_config = config.get("retrieve", {})
    if not isinstance(raw_retrieve_config, dict):
        raise RetrieveConfigurationError("retrieve must be a mapping")
    retrieve_config = cast(dict[str, Any], raw_retrieve_config)

    return RetrieveSettings(
        max_promptguard_chunks=bounded_int(
            retrieve_config,
            "max_promptguard_chunks",
            RETRIEVE_MAX_PROMPTGUARD_CHUNKS,
            minimum=0,
            maximum=_MAX_RETRIEVE_PROMPTGUARD_CHUNKS,
            error=RetrieveConfigurationError,
        ),
        fetch_concurrency=bounded_int(
            retrieve_config,
            "fetch_concurrency",
            FETCH_CONCURRENCY,
            minimum=1,
            maximum=FETCH_CONCURRENCY,
            error=RetrieveConfigurationError,
        ),
        admission_queue_depth=bounded_int(
            retrieve_config,
            "admission_queue_depth",
            RETRIEVE_ADMISSION_QUEUE_DEPTH,
            minimum=0,
            maximum=_MAX_RETRIEVE_ADMISSION_QUEUE_DEPTH,
            error=RetrieveConfigurationError,
        ),
        max_queued_fetch_bytes=bounded_int(
            retrieve_config,
            "max_queued_fetch_bytes",
            MAX_QUEUED_FETCH_BYTES,
            minimum=_MIN_QUEUED_FETCH_BYTES,
            maximum=_MAX_QUEUED_FETCH_BYTES,
            error=RetrieveConfigurationError,
        ),
        promptguard_fail_closed_floor=bounded_bool(
            config,
            "promptguard_fail_closed_floor",
            PROMPTGUARD_FAIL_CLOSED_FLOOR,
            error=RetrieveConfigurationError,
        ),
        promptguard_threshold_ceiling=bounded_float(
            config,
            "promptguard_threshold_ceiling",
            PROMPTGUARD_THRESHOLD_CEILING,
            minimum=0.0,
            maximum=1.0,
            error=RetrieveConfigurationError,
        ),
        promptguard_wait_seconds=bounded_float(
            config,
            "promptguard_wait_seconds",
            PROMPTGUARD_WAIT_SECONDS,
            minimum=_MIN_PROMPTGUARD_WAIT_SECONDS,
            maximum=_MAX_PROMPTGUARD_WAIT_SECONDS,
            error=RetrieveConfigurationError,
        ),
    )
