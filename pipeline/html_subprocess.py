"""Killable, spawn-isolated stage-1 HTML extraction for fetched pages (unhashed).

``extract_html_and_scan`` is the stage-1 work ``/retrieve`` does for an HTML
body: ``extract_html`` plus the inline-form and raw-markup stage-2 scans, in one
call. It lives here rather than in ``stage1_extraction`` because
``stage2_structural`` imports from that module (a cycle). The worker runs it in
a launched child (:mod:`pipeline.worker_launch`, kind ``html``) so the parse
cost of a hostile page is bounded by the OS, and returns one strictly validated
plain-data frame.

How every field crosses (``ensure_ascii=False`` JSON, nothing pickled):

``ExtractionResult`` (``stage1_extraction``)
    ``title``, ``author``, ``date``: sent, ``str | None``.
    ``raw_text``, ``main_content``: sent, ``str``.
    ``word_count``: sent, ``int`` >= 0.
    ``main_content_is_fallback``: sent, ``bool | None``.
    ``scan_text_inline``: **never sent.** The child clears it before the frame
    is built (as the in-thread path does) and the parent rebuilds ``None``.

``StructuralScanResult`` (``stage2_structural``)
    The whole object is sent as ``scan``, or ``null`` when the page was over
    the character budget and both scans were skipped.
    ``verdict``: sent as its string value, checked against ``Stage2Verdict``.
    ``penalty``: sent, ``float`` in [-0.45, 0.0].
    ``flags``: sent as ``[category, line_number]`` pairs.
    ``fold_refused``: sent, ``bool``. Not a field of the dataclass: it is the
    child-side ``ScanForms.expansion_refused``, carried because the child's
    logging is disabled and the closed WARNING token would otherwise be lost.
    The parent re-emits the same ``stage2_fold_expansion_refused`` token.

``FlaggedSpan`` (``stage2_structural``)
    ``category``: sent, checked against the closed category vocabulary.
    ``line_number``: sent, ``int`` >= 0.
    ``matched_text``: **never sent.** It is page text and nothing downstream
    of stage 2 reads it (``stage4_structuring`` uses category and count); the
    parent rebuilds every span with ``matched_text=""``.

Failure vocabulary: the child reports ``ok`` or ``failed`` and the two are
deliberately indistinguishable beyond that. The parent raises one
:class:`HTMLExtractionError` for a spawn ``OSError``, a deadline, a dead or
silent child, an oversize frame, bad JSON and any forged frame. Exception text
never crosses the pipe and nothing here logs it. A spool ``OSError`` is not
mapped: it propagates, as the PDF worker's does.

``MAX_HTML_FRAME_BYTES`` is both the whole-frame cap handed to the launcher and
the bound on every string field. The derivation and measurements are in
``docs/configuration.md`` and ``docs/bootstrap-notes.md``. A page whose frame
exceeds it is refused (the child sends ``failed``), never truncated.

Argv carries a spool path and the budget — never the page. The spool file holds
a small header (the request URL) followed by the body, so a URL with a token in
its query string never reaches ``/proc/<pid>/cmdline``.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from models import Stage2Verdict
from pipeline.extraction_limits import ExtractionSettings
from pipeline.stage1_extraction import ExtractionResult, extract_html
from pipeline.stage2_structural import (
    FlaggedSpan,
    StructuralScanResult,
    combine_scan_results,
    scan_raw_markup,
    scan_structural_forms,
    structural_scan_forms,
)
from pipeline.worker_launch import run_worker, spooled_bytes

_SPOOL_PREFIX = "forage-retrieve-html-"

# The whole-frame cap and the bound on every string field, in UTF-8 bytes.
# Derived in docs/configuration.md ("HTML worker frame cap").
MAX_HTML_FRAME_BYTES = 96 * 1024 * 1024

_FOLD_REFUSED_TOKEN = "stage2_fold_expansion_refused"
_STAGE2_LOGGER = logging.getLogger("pipeline.stage2_structural")

_MIN_PENALTY = -0.45
# The closed category vocabulary stage 2 can emit: its blocking and suspicious
# sets plus the raw-markup categories. Written out because those sets are
# private to ``stage2_structural`` (a hashed file, so it grows no accessor for
# this story); ``tests/test_html_subprocess.py`` pins this set equal to them.
_VALID_CATEGORIES = frozenset(
    {
        "instruction_override",
        "authority_impersonation",
        "prompt_boundary",
        "encoded_payload",
        "suspicious_url",
        "exfil_beacon",
        "envelope_breakout",
    }
)
_FRAME_KEYS = frozenset(
    {
        "status",
        "title",
        "author",
        "date",
        "raw_text",
        "main_content",
        "word_count",
        "main_content_is_fallback",
        "scan",
    }
)
_SCAN_KEYS = frozenset({"verdict", "penalty", "flags", "fold_refused"})
_VERDICTS = frozenset(verdict.value for verdict in Stage2Verdict)

_HEADER = struct.Struct(">BI")
_NO_BUDGET_ARG = "-"


class HTMLExtractionError(Exception):
    """Raised for every HTML worker failure; the message is never document-derived."""


# ---------------------------------------------------------------------------
# The shared stage-1 function
# ---------------------------------------------------------------------------


def _extract_and_scan(
    html: str, url: str | None, budget_characters: int | None
) -> tuple[ExtractionResult, StructuralScanResult | None, bool]:
    extraction = extract_html(html, url, with_inline=True)
    inline = extraction.scan_text_inline
    extraction = replace(extraction, scan_text_inline=None)
    if budget_characters is not None and len(extraction.raw_text) > budget_characters:
        return extraction, None, False
    markup_scan = scan_raw_markup(html)
    if inline is None:
        return extraction, markup_scan, False
    forms = structural_scan_forms(inline, html_parsed=True)
    inline_scan = scan_structural_forms(forms)
    return (
        extraction,
        combine_scan_results(inline_scan, markup_scan),
        forms.expansion_refused,
    )


def extract_html_and_scan(
    html: str, url: str | None, budget_characters: int | None
) -> tuple[ExtractionResult, StructuralScanResult | None]:
    """Stage 1 plus the inline-form and raw-markup stage-2 scans, one call.

    Parses once (``extract_html`` builds the inline text from its own soup),
    scans the inline text and the fetched markup, and returns the extraction
    with ``scan_text_inline`` cleared plus one combined ``StructuralScanResult``.
    An over-budget page is refused by the caller's pre-check, so both scans are
    skipped for it.
    """
    extraction, scan, _ = _extract_and_scan(html, url, budget_characters)
    return extraction, scan


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------


def encode_html_input(body: bytes, url: str | None) -> bytes:
    """Pack the spool file: ``[has_url:1][url_length:4][url][body]``."""
    url_bytes = b"" if url is None else url.encode("utf-8", errors="replace")
    return _HEADER.pack(0 if url is None else 1, len(url_bytes)) + url_bytes + body


def _decode_html_input(data: bytes) -> tuple[bytes, str | None]:
    has_url, url_length = _HEADER.unpack_from(data)
    start = _HEADER.size
    url = data[start : start + url_length].decode("utf-8") if has_url else None
    return data[start + url_length :], url


def build_html_frame(
    html: str, url: str | None, budget_characters: int | None
) -> dict[str, object]:
    """Run the shared function and build the plain-data frame (child side)."""
    extraction, scan, fold_refused = _extract_and_scan(html, url, budget_characters)
    scan_frame: dict[str, object] | None = None
    if scan is not None:
        scan_frame = {
            "verdict": scan.verdict.value,
            "penalty": float(scan.penalty),
            "flags": [[flag.category, flag.line_number] for flag in scan.flags],
            "fold_refused": fold_refused,
        }
    return {
        "status": "ok",
        "title": extraction.title,
        "author": extraction.author,
        "date": extraction.date,
        "raw_text": extraction.raw_text,
        "main_content": extraction.main_content,
        "word_count": extraction.word_count,
        "main_content_is_fallback": extraction.main_content_is_fallback,
        "scan": scan_frame,
    }


def run_html_worker(path: Path, budget_characters: int | None) -> dict[str, object]:
    """Child side: read one spooled page and return its frame."""
    body, url = _decode_html_input(path.read_bytes())
    html = body.decode("utf-8", errors="replace")
    return build_html_frame(html, url, budget_characters)


def parse_budget_argument(text: str) -> int | None:
    return None if text == _NO_BUDGET_ARG else int(text)


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------


def _fail() -> HTMLExtractionError:
    return HTMLExtractionError("HTML extraction worker failed")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_text(value: object, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    # Decoded UTF-8 length: a str's character count can understate its bytes.
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_HTML_FRAME_BYTES:
        raise _fail()
    return value


def _decode_scan(raw: object) -> tuple[StructuralScanResult, bool]:
    if not isinstance(raw, dict):
        raise _fail()
    scan = cast(dict[str, object], raw)
    if frozenset(scan) != _SCAN_KEYS:
        raise _fail()
    verdict = scan["verdict"]
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        raise _fail()
    penalty = scan["penalty"]
    if not isinstance(penalty, float) or not _MIN_PENALTY <= penalty <= 0.0:
        raise _fail()
    fold_refused = scan["fold_refused"]
    if not isinstance(fold_refused, bool):
        raise _fail()
    raw_flags = scan["flags"]
    if not isinstance(raw_flags, list):
        raise _fail()
    flags: list[FlaggedSpan] = []
    for raw_flag in cast(list[object], raw_flags):
        if not isinstance(raw_flag, list):
            raise _fail()
        pair = cast(list[object], raw_flag)
        if len(pair) != 2:
            raise _fail()
        category, line_number = pair
        if (
            not isinstance(category, str)
            or category not in _VALID_CATEGORIES
            or not _is_int(line_number)
            or cast(int, line_number) < 0
        ):
            raise _fail()
        flags.append(
            FlaggedSpan(
                category=category, matched_text="", line_number=cast(int, line_number)
            )
        )
    return (
        StructuralScanResult(
            verdict=Stage2Verdict(verdict), flags=flags, penalty=penalty
        ),
        fold_refused,
    )


def decode_html_frame(
    payload: Mapping[str, object],
) -> tuple[ExtractionResult, StructuralScanResult | None]:
    """Validate a decoded frame and rebuild the result; fail closed.

    Raises :class:`HTMLExtractionError` for any deviation. When the frame
    reports a refused look-alike fold, re-emits the same closed WARNING token
    the in-thread path logs (the child's logging is disabled).
    """
    if frozenset(payload) != _FRAME_KEYS or payload["status"] != "ok":
        raise _fail()
    title = _bounded_text(payload["title"], optional=True)
    author = _bounded_text(payload["author"], optional=True)
    date = _bounded_text(payload["date"], optional=True)
    raw_text = _bounded_text(payload["raw_text"], optional=False)
    main_content = _bounded_text(payload["main_content"], optional=False)
    word_count = payload["word_count"]
    fallback = payload["main_content_is_fallback"]
    if (
        raw_text is None
        or main_content is None
        or not _is_int(word_count)
        or cast(int, word_count) < 0
        or not (fallback is None or isinstance(fallback, bool))
    ):
        raise _fail()
    scan: StructuralScanResult | None = None
    fold_refused = False
    if payload["scan"] is not None:
        scan, fold_refused = _decode_scan(payload["scan"])
    extraction = ExtractionResult(
        title=title,
        author=author,
        date=date,
        raw_text=raw_text,
        main_content=main_content,
        word_count=cast(int, word_count),
        main_content_is_fallback=fallback,
    )
    if fold_refused:
        _STAGE2_LOGGER.warning(_FOLD_REFUSED_TOKEN)
    return extraction, scan


def extract_html_bytes_in_subprocess(
    body: bytes,
    url: str | None,
    budget_characters: int | None,
    settings: ExtractionSettings,
) -> tuple[ExtractionResult, StructuralScanResult | None]:
    """Spool a fetched page and run stage 1 for it in the bounded worker.

    The worker's address-space and CPU limits come from ``settings``; the
    child imports its parsers under ``RLIMIT_CPU``, so import time counts
    against ``child_cpu_seconds``. Like the PDF entry point this is
    synchronous: the caller keeps its thread until the worker is reaped and the
    spool unlinked. A spool ``OSError`` propagates unchanged; every other
    failure is an :class:`HTMLExtractionError`.
    """
    budget_argument = (
        _NO_BUDGET_ARG if budget_characters is None else str(budget_characters)
    )
    with spooled_bytes(encode_html_input(body, url), prefix=_SPOOL_PREFIX) as path:
        payload = run_worker(
            "html",
            [str(path), budget_argument],
            cpu_seconds=settings.child_cpu_seconds,
            address_space_bytes=settings.child_address_space_bytes,
            wall_clock_seconds=settings.wall_clock_seconds,
            max_frame_bytes=MAX_HTML_FRAME_BYTES,
        )
    if payload is None:
        raise _fail()
    return decode_html_frame(payload)
