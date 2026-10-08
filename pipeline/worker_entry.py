"""Child entry point for the rlimited extraction workers (unhashed).

Launched as ``python -m pipeline.worker_entry <kind> <fd> <cpu> <address-space>
<wall> <max-frame> [kind arguments...]`` by :mod:`pipeline.worker_launch`. Argv
holds a spool path, the pipe fd and numbers — never document content.

Order matters: kernel limits go on **first**, before any parser import, then
logging is disabled so a parser warning can never carry document text anywhere,
and only then are the parser modules imported.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pipeline.worker_launch import FAILED_FRAME_PAYLOAD, encode_frame

_COMMON_ARGS = 5


def _apply_child_limits(
    cpu_seconds: int, address_space_bytes: int, wall_clock_seconds: int
) -> None:
    """Apply child-only kernel limits before any parser is imported."""
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # Docker production runs Linux cgroups where RLIMIT_AS is enforced against
    # the worker. macOS does not reliably account spawned interpreter shared VM
    # mappings under this limit, so local development uses parent supervision.
    if sys.platform.startswith("linux"):
        resource.setrlimit(
            resource.RLIMIT_AS, (address_space_bytes, address_space_bytes)
        )
    signal.setitimer(signal.ITIMER_REAL, wall_clock_seconds)


def _run_pdf(args: Sequence[str]) -> dict[str, object]:
    from pipeline.extraction_limits import ExtractionSettings
    from pipeline.pdf_subprocess import run_pdf_worker

    path, max_pages, max_chunks = args
    settings = ExtractionSettings(
        max_pages=int(max_pages), max_promptguard_chunks=int(max_chunks)
    )
    return run_pdf_worker(Path(path), settings)


def _run_html(args: Sequence[str]) -> dict[str, object]:
    from pipeline.html_subprocess import parse_budget_argument, run_html_worker

    path, budget = args
    return run_html_worker(Path(path), parse_budget_argument(budget))


# kind -> (runner, ensure_ascii for its frame)
_KINDS: dict[str, tuple[Callable[[Sequence[str]], dict[str, object]], bool]] = {
    "pdf": (_run_pdf, True),
    "html": (_run_html, False),
}


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def main(argv: Sequence[str]) -> int:
    kind, fd_text, cpu, address_space, wall, max_frame, *kind_args = argv
    fd = int(fd_text)
    max_bytes = int(max_frame)
    _apply_child_limits(int(cpu), int(address_space), int(wall))
    logging.disable(logging.CRITICAL)
    runner, ensure_ascii = _KINDS[kind]
    try:
        frame = encode_frame(
            runner(kind_args), ensure_ascii=ensure_ascii, max_bytes=max_bytes
        )
    except Exception:
        # Parser errors must not transport arbitrary document-derived messages.
        frame = encode_frame(
            {"status": "failed"}, ensure_ascii=True, max_bytes=len(FAILED_FRAME_PAYLOAD)
        )
    _write_all(fd, frame)
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
