"""Killable, spawn-isolated PDF parsing for untrusted uploads and fetched PDFs."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import stat
import sys
import tempfile
import time
from contextlib import suppress
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

from pipeline.extraction_limits import ExtractionSettings
from pipeline.stage1_extraction import ExtractionResult, normalize_text
from pipeline.stage1_pdf import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFNoTextError,
)


class PDFClassifiableTextLimitError(PDFExtractionError):
    """Raised when extracted PDF text exceeds the PromptGuard budget."""


class PDFPageLimitError(PDFExtractionError):
    """Raised when a PDF exceeds the fixed page-count extraction bound."""


class SpoolDirectoryError(OSError):
    """Raised when the spool directory exists but is not safe to spool into.

    The message is one closed token — ``spool_dir_symlink``,
    ``spool_dir_not_directory``, ``spool_dir_foreign_owner`` or
    ``spool_dir_mode`` — and never the path, so the lifespan can re-raise it
    as a boot refusal and ``/retrieve`` can map it to ``pdf_spool_error``
    without either carrying anything host-derived.
    """


def spool_dir() -> Path:
    """Return the process-private spool directory, creating it on first use.

    ``<tempfile.gettempdir()>/forage-spool-<euid>``, resolved on every call
    rather than at import, so a ``TMPDIR`` set after import still applies.
    Both routes spool here: ``/extract``'s uploads and ``/retrieve``'s fetched
    PDFs. The worker child re-opens a spool file by path, so the file must sit
    in a directory nobody else can enter — that is what closes the re-open
    window without depending on the sticky bit of the parent.

    Created with ``mkdir(mode=0o700)`` and ``exist_ok=False``: umask can only
    clear bits, so the directory is never wider than 0700 at any instant. An
    existing path is verified with ``os.lstat`` (never ``stat``, which would
    follow a planted symlink) on **every** call and refused, never repaired —
    a directory already present with the wrong owner or mode is evidence, not
    a state to fix, and a check that re-runs per call catches one removed and
    re-created by another local user after boot.
    """
    path = Path(tempfile.gettempdir()) / f"forage-spool-{os.geteuid()}"
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise SpoolDirectoryError("spool_dir_symlink") from None
        if not stat.S_ISDIR(st.st_mode):
            raise SpoolDirectoryError("spool_dir_not_directory") from None
        if st.st_uid != os.geteuid():
            raise SpoolDirectoryError("spool_dir_foreign_owner") from None
        if st.st_mode & 0o077:
            raise SpoolDirectoryError("spool_dir_mode") from None
    return path


def _apply_child_limits(settings: ExtractionSettings) -> None:
    """Apply child-only kernel limits before invoking pypdf."""
    import resource

    resource.setrlimit(
        resource.RLIMIT_CPU,
        (settings.child_cpu_seconds, settings.child_cpu_seconds),
    )
    # Docker production runs Linux cgroups where RLIMIT_AS is enforced against
    # the worker. macOS does not reliably account spawned interpreter shared VM
    # mappings under this limit, so local development uses parent supervision.
    if sys.platform.startswith("linux"):
        resource.setrlimit(
            resource.RLIMIT_AS,
            (settings.child_address_space_bytes, settings.child_address_space_bytes),
        )
    signal.setitimer(signal.ITIMER_REAL, settings.wall_clock_seconds)


def _extract_pdf_path(path: Path, settings: ExtractionSettings) -> ExtractionResult:
    """Extract one PDF from a sidecar-owned file without loading input bytes first."""
    from pypdf import PdfReader

    with path.open("rb") as source:
        reader = PdfReader(source)
        if reader.is_encrypted:
            reader.decrypt("")
            raise PDFEncryptedError("PDF is encrypted")
        if len(reader.pages) > settings.max_pages:
            raise PDFPageLimitError("PDF exceeds the page extraction limit")

        page_texts: list[str] = []
        extracted_characters = 0
        extracted_bytes = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            extracted_characters += len(page_text)
            extracted_bytes += len(page_text.encode("utf-8"))
            if (
                extracted_characters > settings.max_extracted_characters
                or extracted_bytes > settings.max_extracted_output_bytes
            ):
                raise PDFClassifiableTextLimitError(
                    "PDF text exceeds the PromptGuard classification budget"
                )
            page_texts.append(page_text)

    raw_text = normalize_text("\n\n".join(page_texts))
    if (
        len(raw_text) > settings.max_extracted_characters
        or len(raw_text.encode("utf-8")) > settings.max_extracted_output_bytes
    ):
        raise PDFClassifiableTextLimitError(
            "PDF text exceeds the PromptGuard classification budget"
        )
    if not raw_text:
        raise PDFNoTextError(
            "PDF contains no extractable text (scanned/image PDF). "
            "OCR is not supported."
        )
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=raw_text,
        main_content=raw_text,
        word_count=len(raw_text.split()),
    )


def _send_child_result(
    connection: Connection,
    payload: dict[str, object],
    max_result_bytes: int,
) -> None:
    """Encode and send one bounded, length-framed result over the IPC pipe."""
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    if len(encoded) > max_result_bytes:
        encoded = b'{"status":"failed"}'
    connection.send_bytes(encoded)


def _pdf_child(
    path: str,
    settings: ExtractionSettings,
    connection: Connection,
) -> None:
    """Spawn target: constrain pypdf and return only a capped extraction result."""
    try:
        _apply_child_limits(settings)
        result = _extract_pdf_path(Path(path), settings)
        _send_child_result(
            connection,
            {
                "status": "ok",
                "raw_text": result.raw_text,
                "word_count": result.word_count,
            },
            settings.max_ipc_result_bytes,
        )
    except PDFClassifiableTextLimitError:
        _send_child_result(
            connection,
            {"status": "too_large_to_classify"},
            settings.max_ipc_result_bytes,
        )
    except PDFEncryptedError:
        _send_child_result(
            connection, {"status": "encrypted"}, settings.max_ipc_result_bytes
        )
    except PDFNoTextError:
        _send_child_result(
            connection, {"status": "no_text"}, settings.max_ipc_result_bytes
        )
    except Exception:
        # Parser errors must not transport arbitrary document-derived messages.
        with suppress(Exception):
            _send_child_result(
                connection, {"status": "failed"}, settings.max_ipc_result_bytes
            )
    finally:
        connection.close()


def extract_pdf_in_subprocess(
    path: Path,
    settings: ExtractionSettings,
) -> ExtractionResult:
    """Run pypdf in a spawned child and kill/reap it on every abnormal outcome."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_child, args=(str(path), settings, sender))
    process.start()
    sender.close()
    deadline = time.monotonic() + settings.wall_clock_seconds
    payload: dict[str, Any] | None = None

    try:
        while time.monotonic() < deadline:
            if receiver.poll(min(0.1, deadline - time.monotonic())):
                try:
                    raw_payload = receiver.recv_bytes(settings.max_ipc_result_bytes)
                    decoded: object = json.loads(raw_payload)
                except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                    break
                if isinstance(decoded, dict):
                    payload = cast(dict[str, object], decoded)
                break
            if not process.is_alive():
                break
    finally:
        # Always SIGKILL and reap unfinished workers; request cancellation cannot
        # leave a parser thread or process consuming the shared sidecar budget.
        if process.is_alive():
            process.kill()
        process.join()
        receiver.close()

    if payload is None or payload.get("status") == "failed":
        raise PDFExtractionError("PDF extraction worker failed")
    if payload.get("status") == "encrypted":
        raise PDFEncryptedError("PDF is encrypted")
    if payload.get("status") == "no_text":
        raise PDFNoTextError("PDF contains no extractable text")
    if payload.get("status") == "too_large_to_classify":
        raise PDFClassifiableTextLimitError(
            "PDF text exceeds the PromptGuard classification budget"
        )
    if payload.get("status") != "ok":
        raise PDFExtractionError("PDF extraction worker returned an invalid result")

    raw_text = payload.get("raw_text")
    word_count = payload.get("word_count")
    if (
        not isinstance(raw_text, str)
        or not isinstance(word_count, int)
        or len(raw_text) > settings.max_extracted_characters
        or len(raw_text.encode("utf-8")) > settings.max_extracted_output_bytes
    ):
        raise PDFExtractionError("PDF extraction worker returned an invalid result")
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=raw_text,
        main_content=raw_text,
        word_count=word_count,
    )


def extract_pdf_bytes_in_subprocess(
    data: bytes,
    settings: ExtractionSettings,
) -> ExtractionResult:
    """Spool fetched PDF bytes and parse them in the same bounded worker.

    ``/retrieve`` already holds the body in memory, so the spool buys reuse of
    ``extract_pdf_in_subprocess``'s entry point and rlimit wiring, not memory
    isolation. The file is created in :func:`spool_dir`, made 0600 before a
    byte is written, and unlinked in ``finally`` on success or any raised
    failure. Cancelling an async caller does not interrupt this synchronous
    function: the caller must retain ownership of its thread until it returns,
    including cleanup, before propagating cancellation or releasing admission.
    An ``OSError`` from the directory check, the create or the write propagates
    unchanged for the caller to map; an already-created file is still unlinked.
    """
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="forage-retrieve-",
            dir=spool_dir(),
            delete=False,
        ) as spool:
            path = Path(spool.name)
            os.fchmod(spool.fileno(), 0o600)
            spool.write(data)
        return extract_pdf_in_subprocess(path, settings)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
