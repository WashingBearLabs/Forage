"""The two cheap guards that catch a stale cassette.

The hard guard is a revision check: a cassette recorded at one weights revision
must not outlive the manifest pin for its model. The soft guard compares the
``torch`` / ``transformers`` versions the cassette was recorded under with the
ones ``uv.lock`` pins; a difference is a note for the report, never a failure,
because a score that moved across a library bump is a measurement, not a bug.

Neither guard reads a text, a token or the network.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

import model_fetcher
from scripts.corpus import vocab

LOCK_PATH: Final[Path] = vocab.TESTS_CORPUS_ROOT.parents[1] / "uv.lock"

VERSIONED_PACKAGES: Final = ("torch", "transformers")
"""The libraries whose version can move a recorded score."""

VERSIONS_DIFFER_TOKEN: Final = "cassette_versions_differ"
"""The report token a differing pair is printed under (rendered by spec 5)."""


def locked_versions(lock_path: Path = LOCK_PATH) -> dict[str, frozenset[str]]:
    """Every locked version of each :data:`VERSIONED_PACKAGES` member.

    A set, because ``torch`` is locked once per platform marker
    (``2.14.0`` and ``2.14.0+cpu``) and the host that recorded may be either.
    """
    loaded = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {name: set() for name in VERSIONED_PACKAGES}
    for entry in cast(list[dict[str, object]], loaded.get("package", [])):
        name = entry.get("name")
        version = entry.get("version")
        if isinstance(name, str) and name in found and isinstance(version, str):
            found[name].add(version)
    return {name: frozenset(versions) for name, versions in found.items()}


def revision_problem(cassette: Mapping[str, object]) -> str | None:
    """Why ``cassette`` is not at the manifest pin for its model, or ``None``.

    Names both revisions; they are public commit hashes, not credentials.
    """
    model_id = str(cassette["model_id"])
    if model_id not in model_fetcher.ALLOWED_MODEL_IDS:
        return f"model id {model_id} is not in ALLOWED_MODEL_IDS"
    pin = model_fetcher.read_manifest_pin(model_id=model_id)
    if pin is None:
        return f"the manifest has no pin for {model_id}"
    if cassette["revision"] != pin.revision:
        return (
            f"cassette revision {cassette['revision']} differs from the manifest "
            f"pin {pin.revision} for {model_id}; re-record"
        )
    return None


def versions_differ(
    cassette: Mapping[str, object],
    locked: Mapping[str, frozenset[str]],
) -> list[str]:
    """One ``cassette_versions_differ`` line per library that differs from the lock."""
    lines: list[str] = []
    for name in VERSIONED_PACKAGES:
        recorded = cassette.get(name)
        pinned = locked.get(name, frozenset())
        if recorded in pinned:
            continue
        lines.append(
            f"WARNING {VERSIONS_DIFFER_TOKEN} {name}: "
            f"cassette={recorded} lock={','.join(sorted(pinned)) or 'absent'}"
        )
    return lines
