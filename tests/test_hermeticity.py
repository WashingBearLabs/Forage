"""Executing canary for the autouse guards in ``tests/conftest.py``.

The suite's hermeticity is an *invariant*, not a habit: every outbound call is
mocked at the seam, and ``conftest.py`` installs an autouse ``pytest-socket``
fixture (``disable_socket(allow_unix_socket=True)``) so a regression that
reaches the real network fails loudly instead of passing slowly and flakily.
It caught three real DNS calls in the cache tests the day it landed.

The problem with an invariant enforced by a fixture is that deleting the
fixture produces no failure — every test simply goes back to being allowed to
touch the network, and nothing says so. These tests are the canary that closes
that hole. They **run and pass** on every invocation of the suite; if the guard
is removed, weakened, or scoped down to opt-in, they are the tests that go red.

What is asserted here, and why each one is a distinct hole:

* TCP, UDP and IPv6 socket construction all raise ``SocketBlockedError`` —
  ``allow_unix_socket=True`` is the *only* exemption, and it is family-scoped,
  not protocol-scoped;
* both DNS entry points ``pytest-socket`` patches (``getaddrinfo`` and
  ``gethostbyname``) are blocked — name resolution is what the cache-test
  regression actually did, and a socket-only guard would have let it through;
* ``socket.create_connection`` — the API real client code calls — is blocked
  too, including to loopback: this suite has no local-service exemption and
  must not grow one silently;
* ``AF_UNIX`` socketpairs still work, and an async test still runs, because
  asyncio's event-loop self-pipe is a Unix socketpair. A guard that blocked it
  would take the whole async half of the suite down with it.

The second autouse fixture — the one that clears inherited environment
settings — has the same hole and the same canary at the bottom of this module.

test_mapping:
  tests/conftest.py: tests/test_hermeticity.py
"""

from __future__ import annotations

import os
import socket

import pytest
from pytest_socket import SocketBlockedError

from tests import conftest


def test_tcp_socket_construction_is_blocked() -> None:
    """A plain test cannot create an AF_INET TCP socket."""
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_udp_socket_construction_is_blocked() -> None:
    """The block is family-scoped, not TCP-scoped: UDP is blocked too."""
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def test_ipv6_socket_construction_is_blocked() -> None:
    """AF_INET6 is not a way around the guard."""
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM)


def test_getaddrinfo_is_blocked() -> None:
    """DNS resolution raises rather than leaving the machine.

    This is the exact shape of the regression the guard caught when it was
    added: a test that never opened a connection, but did resolve a name.
    """
    with pytest.raises(SocketBlockedError):
        socket.getaddrinfo("example.com", 443)


def test_gethostbyname_is_blocked() -> None:
    """The legacy resolver entry point is blocked as well as ``getaddrinfo``."""
    with pytest.raises(SocketBlockedError):
        socket.gethostbyname("example.com")


def test_create_connection_is_blocked_even_to_loopback() -> None:
    """``socket.create_connection`` — what client libraries call — is blocked.

    Loopback deliberately: this suite has no local-service lane and therefore
    no host exemption. A future exemption would make this test fail, which is
    the point — the exemption should be an argued change, not a silent one.
    The error may surface from either patched primitive (``create_connection``
    resolves the host before it builds the socket); both raise
    ``SocketBlockedError``, so the failure stays loud either way.
    """
    with pytest.raises(SocketBlockedError):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)


def test_unix_socketpair_is_allowed() -> None:
    """AF_UNIX stays available — asyncio's event-loop self-pipe needs it."""
    left, right = socket.socketpair()
    left.close()
    right.close()


async def test_async_test_runs_under_the_block() -> None:
    """An async test runs to completion while AF_INET stays blocked.

    If the Unix-socket exemption were lost, the event loop's self-pipe could
    not be created and this test would fail to run at all. Reaching the
    assertion proves the loop is healthy *and* the block is still on.
    """
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


# The environment-clearing half of `conftest.py`. Every name below changes what
# the suite *does*, not merely how fast it does it — a token takes the
# "configured source" branch through the weight acquisition, and `VALKEY_URL`
# selects a different cache storage entirely. A developer who exports one runs
# a different suite from CI's, and the divergence is silent.


def test_the_cleared_environment_is_the_expected_exact_set() -> None:
    """The fixture's list is a gate, not a starting point.

    Exact-set rather than superset: a name dropped from the fixture goes red
    here, in CI, on a runner where nothing was exported and the canary below
    would therefore have passed either way.
    """
    assert set(conftest._CLEARED_ENV_VARS) == {
        "HF_TOKEN",
        "HF_HOME",
        "FORAGE_MODEL_REVISION",
        "FORAGE_WEIGHTS_MIRROR",
        "FORAGE_MIRROR_TOKEN",
        "VALKEY_URL",
    }


def test_no_cleared_variable_survives_into_a_test() -> None:
    """The canary: every listed variable really is gone while a test runs.

    Deleting the fixture is exactly as invisible as deleting the socket guard
    was — every test simply starts inheriting the developer's shell again.
    This is the test that goes red instead.
    """
    inherited = [name for name in conftest._CLEARED_ENV_VARS if name in os.environ]

    assert inherited == []
