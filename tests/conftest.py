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

3. **No inherited credentials.** The weight-acquisition environment variables
   and ``VALKEY_URL`` are cleared before every test. A developer who exports
   ``HF_TOKEN`` or ``FORAGE_MIRROR_TOKEN`` in their shell — the people most
   likely to, being the ones who vendor the weights — would otherwise run a
   *different* suite from CI's: paths that assert "this source is skipped"
   would quietly take the configured branch instead. The socket guard would
   catch a real fetch, but only after the branch had already diverged.

   ``VALKEY_URL`` joined them with `feature-forage-cache-fallback` US-002,
   which made an unset one *select a different storage backend*: a developer
   pointing one at their own Valkey would run every lifespan test against the
   Valkey path CI runs against memory, and the credential such a URL routinely
   carries would be in the process the suite's log-leak assertions read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_socket import disable_socket

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Cleared for every test; a test that wants one sets it with `monkeypatch`,
# which runs after this fixture and is undone after the test.
_CLEARED_ENV_VARS = (
    "HF_TOKEN",
    "HF_HOME",
    "FORAGE_MODEL_REVISION",
    "FORAGE_WEIGHTS_MIRROR",
    "FORAGE_MIRROR_TOKEN",
    "VALKEY_URL",
)


@pytest.fixture(autouse=True)
def forbid_network() -> None:
    """Block real network access for every test in the suite."""
    disable_socket(allow_unix_socket=True)


@pytest.fixture(autouse=True)
def forbid_inherited_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test against an environment with no inherited configuration."""
    for name in _CLEARED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
