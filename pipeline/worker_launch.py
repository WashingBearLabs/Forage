"""Shared launcher for the rlimited extraction workers (unhashed).

A worker is a fresh ``python -m pipeline.worker_entry <kind>`` interpreter, not a
``multiprocessing`` child. ``multiprocessing`` inherits ``os.environ`` and takes no
per-process ``env=``: clearing the environment in the child would leave every
credential in its ``/proc/self/environ`` and its memory, and swapping
``os.environ`` around ``start()`` mutates process-global state while request
threads run. ``subprocess.Popen(env=...)`` hands the child only what is built here.

The result comes back as one length-prefixed JSON frame on a pipe fd that
``pass_fds`` hands to the child alone. Argv carries the spool path, the fd number
and numeric limits — never document content.
"""

from __future__ import annotations

import json
import os
import select
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pipeline

# Authoritative: the only parent variables a worker is launched with, and only
# when the parent has them. Nothing here is a credential. ``HF_HOME`` is
# deliberately absent — the parse path never touches the model cache. Not listed
# either: macOS's ``__CF_USER_TEXT_ENCODING`` is injected by the OS, and
# PEP 538 may add ``LC_CTYPE`` on its own, so neither is ours to grant.
WORKER_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE",
)

_FRAME_HEADER = struct.Struct(">I")
FAILED_FRAME_PAYLOAD = b'{"status":"failed"}'
_POLL_SECONDS = 0.1


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
    documents. The worker child re-opens a spool file by path, so the file must
    sit in a directory nobody else can enter — that is what closes the re-open
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


@contextmanager
def spooled_bytes(data: bytes, *, prefix: str) -> Generator[Path]:
    """Write ``data`` to a 0600 file in :func:`spool_dir`; unlink on every exit.

    The file is made 0600 before a byte is written. An ``OSError`` from the
    directory check, the create or the write propagates unchanged for the
    caller to map; an already-created file is still unlinked.
    """
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=prefix,
            dir=spool_dir(),
            delete=False,
        ) as spool:
            path = Path(spool.name)
            os.fchmod(spool.fileno(), 0o600)
            spool.write(data)
        yield path
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def encode_frame(
    payload: Mapping[str, object], *, ensure_ascii: bool, max_bytes: int
) -> bytes:
    """Encode ``payload`` as one length-prefixed frame, capped at ``max_bytes``.

    An oversize body is replaced by ``{"status":"failed"}`` rather than sent.
    """
    body = json.dumps(
        payload, ensure_ascii=ensure_ascii, separators=(",", ":")
    ).encode()
    if len(body) > max_bytes:
        body = FAILED_FRAME_PAYLOAD
    return _FRAME_HEADER.pack(len(body)) + body


def worker_environment(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the allowlisted subset of ``parent`` (default ``os.environ``)."""
    source = os.environ if parent is None else parent
    return {name: source[name] for name in WORKER_ENV_ALLOWLIST if name in source}


_PR_SET_DUMPABLE = 4


def make_process_non_dumpable() -> bool:
    """Mark this process non-dumpable on Linux so a same-uid child cannot read it.

    ``prctl(PR_SET_DUMPABLE, 0)`` makes ``/proc/<pid>/*`` root-owned, so a
    compromised worker child gets ``PermissionError`` on ``/proc/<ppid>/environ``.
    The flag is inherited across fork and reset on exec, so workers are
    unaffected. Returns whether the flag was set; elsewhere it is a no-op.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def project_root() -> Path:
    """Return the directory holding the ``pipeline`` package.

    The image installs with ``--no-install-project``, so ``-m pipeline.worker_entry``
    resolves only through the working directory or ``PYTHONPATH``. The launcher
    pins ``cwd`` here instead of inheriting whatever the parent happens to have.
    """
    return Path(pipeline.__file__).resolve().parent.parent


def _read_exactly(fd: int, count: int, deadline: float) -> bytes | None:
    """Read ``count`` bytes from ``fd`` before ``deadline``; ``None`` on EOF/timeout."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        wait = min(_POLL_SECONDS, deadline - time.monotonic())
        if wait <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            continue
        chunk = os.read(fd, min(remaining, 65536))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(fd: int, max_bytes: int, deadline: float) -> dict[str, object] | None:
    header = _read_exactly(fd, _FRAME_HEADER.size, deadline)
    if header is None:
        return None
    (length,) = _FRAME_HEADER.unpack(header)
    if length > max_bytes:
        return None
    body = _read_exactly(fd, length, deadline)
    if body is None:
        return None
    try:
        decoded: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(dict[str, object], decoded)


def run_worker(
    kind: str,
    worker_args: Sequence[str],
    *,
    cpu_seconds: int,
    address_space_bytes: int,
    wall_clock_seconds: int,
    max_frame_bytes: int,
) -> dict[str, object] | None:
    """Launch one worker and return its decoded frame, or ``None`` on any failure.

    ``None`` covers a spawn failure, a deadline, a dead or silent child, an
    oversize or malformed frame and a non-object body. The child is killed and
    reaped on **every** path, including cancellation of an async caller's
    thread-await, so no worker outlives the call holding the shared budget.
    """
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        argv = [
            sys.executable,
            "-m",
            "pipeline.worker_entry",
            kind,
            str(write_fd),
            str(cpu_seconds),
            str(address_space_bytes),
            str(wall_clock_seconds),
            str(max_frame_bytes),
            *worker_args,
        ]
        deadline = time.monotonic() + wall_clock_seconds
        try:
            process = subprocess.Popen(
                argv,
                env=worker_environment(),
                cwd=project_root(),
                pass_fds=(write_fd,),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None
        finally:
            # The parent must not hold the write end, or EOF never arrives.
            os.close(write_fd)
        try:
            return _read_frame(read_fd, max_frame_bytes, deadline)
        except OSError:
            return None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        os.close(read_fd)
