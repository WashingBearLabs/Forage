"""Golden schema test pinning the retrieval sidecar's wire contract (US-001)."""

from __future__ import annotations

import json
from pathlib import Path

from models import ExtractedContent, RetrievedContent, SearchResponse
from pipeline.contract import CONTRACT_VERSION
from retrieval_app import HealthResponse

_GOLDEN_PATH = (
    Path(__file__).parent
    / "golden"
    / f"contract_{CONTRACT_VERSION.replace('.', '_')}.json"
)

_SCHEMA_MODELS = {
    "HealthResponse": HealthResponse,
    "SearchResponse": SearchResponse,
    "RetrievedContent": RetrievedContent,
    "ExtractedContent": ExtractedContent,
}


def _current_schemas() -> dict[str, object]:
    """Return the live JSON schema for every pinned wire model."""
    return {name: model.model_json_schema() for name, model in _SCHEMA_MODELS.items()}


def test_contract_schema_matches_golden() -> None:
    """Any unrecorded wire-shape change must bump CONTRACT_VERSION.

    This spec *defines* 1.0.0: a change here means either a story forgot to
    update the golden file in the same commit, or the wire shape changed
    without a version bump — both are bugs.
    """
    golden = json.loads(_GOLDEN_PATH.read_text())
    current = _current_schemas()
    assert current == golden, (
        "wire shape changed — bump CONTRACT_VERSION and update "
        f"{_GOLDEN_PATH.name} in the same commit"
    )
