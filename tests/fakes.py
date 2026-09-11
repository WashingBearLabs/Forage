"""Shared test doubles and helpers for the Forage suite."""

from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

import pytest

from cache import CacheMetrics
from model_fetcher import repo_dirname

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from models import RetrievedContent


def assert_frozen(instance: object, field: str, value: object) -> None:
    """Assert *instance* refuses assignment to *field* — the frozen guarantee.

    The write goes through ``setattr`` with a name the type checker cannot see
    through, and that indirection is the point: written plainly,
    ``instance.field = value`` is a *static* error precisely because the
    dataclass is frozen, so under strict type checking the runtime behaviour
    these tests exist to pin could not be expressed at all. The suite still
    asserts the real thing — that the assignment raises at run time.

    ``AttributeError`` rather than ``dataclasses.FrozenInstanceError`` because
    Forage's result types are ``slots=True`` as well as ``frozen=True``, and
    the two paths raise different subclasses of the same base.
    """
    with pytest.raises(AttributeError):
        setattr(instance, field, value)


def sha256_hex(payload: bytes) -> str:
    """The digest the weights manifest pins, for a fixture's bytes."""
    return hashlib.sha256(payload).hexdigest()


def materialize_hub_snapshot(
    cache_root: Path,
    files: Mapping[str, bytes],
    *,
    model_id: str,
    revision: str,
    symlinks: bool = True,
) -> Path:
    """Build ``hub/models--…/{blobs,snapshots/<rev>}`` the way the hub does.

    ``symlinks=True`` is the real shape — content under ``blobs/``, the
    snapshot a directory of links into it — which is what makes a walk that
    hashes the *link* rather than its target, or one that walks the whole
    model directory, produce the wrong answer. The flat form exists so a test
    can prove the walk handles both.

    Shared because two suites need the same layout for different reasons:
    ``test_model_fetcher.py`` verifies against it, and ``test_app.py``'s
    lifespan tests have ``snapshot_download`` write it.
    """
    repo = cache_root / "hub" / repo_dirname(model_id)
    blobs = repo / "blobs"
    snapshot = repo / "snapshots" / revision
    blobs.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        link = snapshot / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if symlinks:
            blob = blobs / sha256_hex(payload)
            blob.write_bytes(payload)
            link.symlink_to(os.path.relpath(blob, link.parent))
        else:
            link.write_bytes(payload)
    return snapshot


def hub_download_double(
    files: Mapping[str, bytes],
    *,
    on_call: Callable[[], None] | None = None,
) -> Callable[..., str]:
    """A ``snapshot_download`` stand-in that writes a real cache tree.

    It honours the ``cache_dir`` it is handed, which several assertions rest
    on: a fetcher that wrote somewhere else would materialise a snapshot the
    verifier never looks at, and a double that ignored the argument could not
    tell the two apart.
    """

    def _download(repo_id: str, **kwargs: Any) -> str:
        if on_call is not None:
            on_call()
        cache_dir = Path(str(kwargs["cache_dir"]))
        snapshot = materialize_hub_snapshot(
            cache_dir.parent,
            files,
            model_id=repo_id,
            revision=str(kwargs["revision"]),
        )
        return str(snapshot)

    return _download


def record_network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every outbound attempt made while the returned list is watched.

    The autouse ``pytest-socket`` guard makes a real connection *fail*, which
    is not the same thing as making it *not happen*: ``huggingface_hub``
    swallows every error from its best-effort harness-registry request, so a
    blocked attempt and no attempt at all look identical to a test that only
    asserts the call succeeded. US-005's "zero network on a warm start" is a
    claim about attempts, so the attempts have to be counted.

    This records and then refuses, exactly as the guard does — the recording is
    the only addition. ``AF_UNIX`` sockets are delegated to whatever is already
    installed (asyncio's event-loop self-pipe is one, and the suite allows it).

    Assignment goes through ``monkeypatch`` rather than a bare ``socket.socket
    = …`` for two reasons: restoration is guaranteed even when an assertion
    raises, and re-binding a module's *class* attribute to a function is
    something pyright rightly refuses to type.
    """
    attempts: list[str] = []
    real_socket = socket.socket

    def _refuse(label: str) -> NoReturn:
        attempts.append(label)
        raise OSError(f"network attempt refused by the test recorder: {label}")

    def _socket(
        family: int = socket.AF_INET, *args: Any, **kwargs: Any
    ) -> socket.socket:
        if family in (socket.AF_INET, socket.AF_INET6):
            _refuse(f"socket(family={family})")
        return real_socket(family, *args, **kwargs)

    def _getaddrinfo(host: object, port: object, *args: Any, **kwargs: Any) -> NoReturn:
        _refuse(f"getaddrinfo({host!r}, {port!r})")

    def _create_connection(address: object, *args: Any, **kwargs: Any) -> NoReturn:
        _refuse(f"create_connection({address!r})")

    monkeypatch.setattr(socket, "socket", _socket)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    return attempts


def weights_manifest_document(
    files: Mapping[str, bytes],
    *,
    model_id: str,
    revision: str,
) -> dict[str, Any]:
    """The manifest that exactly describes *files* — nothing more, nothing less."""
    return {
        "model_id": model_id,
        "revision": revision,
        "files": [
            {"path": name, "sha256": sha256_hex(payload), "size": len(payload)}
            for name, payload in sorted(files.items())
        ],
    }


class FakeContentCache:
    """Settable-``connected`` double for ``ContentCache`` used by app-level tests.

    ``get``/``put``/``delete`` are no-ops (always miss / never write) so
    fixtures that don't care about cache content can drop this in without
    exercising real Valkey I/O.
    """

    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected
        self.metrics = CacheMetrics()

    async def ping_if_due(self) -> bool:
        """Report the settable ``connected`` state, mirroring ``ContentCache``."""
        return self.connected

    async def get(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        news_domains: list[str] | None = None,
    ) -> RetrievedContent | None:
        """Always miss."""
        return None

    async def put(
        self,
        url: str,
        content: RetrievedContent,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        domain: str = "",
        news_domains: list[str] | None = None,
    ) -> bool:
        """Never write."""
        return False

    async def delete(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
    ) -> bool:
        """Never write."""
        return False

    async def close(self) -> None:
        """No-op; the fake owns no real connection."""
        return None
