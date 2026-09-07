"""Shared pytest configuration for the Forage suite.

Two jobs:

1. **Importability.** Forage keeps a flat module layout, so ``retrieval_app``,
   ``models``, ``cache``, ``url_validator``, ``pipeline`` and ``promptguard``
   live at the repo root. Putting that root on ``sys.path`` once here replaces
   the per-module ``sys.path.insert`` dance the suite carried while it lived
   inside Poppy (where the modules sat under ``services/retrieval/``).

2. **Hermeticity.** Every outbound call in this suite is mocked (DNS, httpx,
   transformers/torch). ``pytest-socket`` turns a regression that reaches the
   real network into a loud ``SocketBlockedError`` instead of a slow, flaky
   pass. Unix sockets stay allowed — asyncio's event-loop self-pipe is one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_socket import disable_socket

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def forbid_network() -> None:
    """Block real network access for every test in the suite."""
    disable_socket(allow_unix_socket=True)
