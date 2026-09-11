"""The typed ``/metrics`` body, and the app metadata the contract is served under.

``feature-forage-contract`` US-005 types ``GET /metrics`` and replaces the
placeholder ``FastAPI(title=..., version="0.1.0")`` — both **without changing a
wire byte**, on the same mirror-and-parity terms US-001 set for the error
surface (``tests/test_contract_errors.py``).

Three claims are checked here, each the way it is stated:

* **Parity.** The handler still builds the body as a dict;
  :class:`~retrieval_app.MetricsResponse` only validates it on the way out. The
  parity test calls the handler directly and asserts the served bytes are that
  same dict, serialized — so a model that reshaped, reordered or dropped a
  counter fails here rather than in a consumer.
* **Failure mode.** ``extra="forbid"`` at every level is the whole reason this
  typing is safe to do: a permissive response model *silently filters* a
  counter it does not carry. Both halves are demonstrated — ours raises, a
  throwaway permissive app drops the field on the floor.
* **Flatness.** The three cgroup keys live flat in ``extraction``, exactly as
  ``**_cgroup_memory_snapshot()`` splats them. Nesting them would be a wire
  change; ``tests/test_app.py``'s ``oom_proximity_ratio in extraction`` is the
  fossil guard and this module says the same thing about the schema.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from pydantic import BaseModel
from starlette.requests import Request
from starlette.routing import Route

import retrieval_app
from cache import CacheMetrics
from model_fetcher import ModelMetrics
from pipeline.contract import CONTRACT_VERSION
from pipeline.extraction_limits import extraction_settings_from_config
from retrieval_app import (
    CacheMetricsResponse,
    ExtractionMetricsResponse,
    MetricsResponse,
    ModelMetricsResponse,
    RetrieveMetricsResponse,
    SearchMetricsResponse,
    app,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIGURATION_DOC = _REPO_ROOT / "docs" / "configuration.md"

_SECTION_MODELS = {
    "extraction": ExtractionMetricsResponse,
    "search": SearchMetricsResponse,
    "retrieve": RetrieveMetricsResponse,
    "cache": CacheMetricsResponse,
    "model": ModelMetricsResponse,
}

# Flat in `extraction`, not nested under a `memory` object. The splat that puts
# them there is `retrieval_app.metrics`'s `**_cgroup_memory_snapshot()`.
_CGROUP_KEYS = (
    "cgroup_memory_current_bytes",
    "cgroup_memory_max_bytes",
    "oom_proximity_ratio",
)


class _PermissiveProbe(BaseModel):
    """Response model for the throwaway app the filtering counterfactual builds.

    Module level rather than nested in the test, on ``test_contract_errors.py``
    's ``_ProbeBody`` precedent: this repo runs
    ``from __future__ import annotations``, and a function-local model can
    leave pydantic with an unresolvable ``ForwardRef`` at schema-generation
    time.
    """

    known: int


@pytest.fixture
def client() -> Iterator[httpx.AsyncClient]:
    """Drive the real app over ASGI with the counters a lifespan would publish.

    ``/metrics`` reads five objects off ``app.state`` and nothing else, so this
    is deliberately smaller than ``tests/test_app.py``'s fixture: the point of
    this module is the shape of the body, not the pipeline behind it.
    """
    app.state.extraction_metrics = retrieval_app.ExtractionMetrics()
    app.state.extraction_admission = retrieval_app.ExtractionAdmissionController(
        extraction_settings_from_config({}),
        app.state.extraction_metrics,
    )
    app.state.search_metrics = retrieval_app.SearchMetrics()
    app.state.retrieve_metrics = retrieval_app.RetrieveMetrics()
    app.state.cache_metrics = CacheMetrics()
    app.state.model_metrics = ModelMetrics()
    transport = httpx.ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")


def _metrics_request() -> Request:
    """Build the minimal ASGI request the handler reads ``app.state`` through."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
            "app": app,
        }
    )


def _openapi() -> dict[str, Any]:
    """Return the app's generated OpenAPI document."""
    return app.openapi()


# ---------------------------------------------------------------------------
# Parity: the typed response is the handler's dict, byte for byte
# ---------------------------------------------------------------------------


async def test_served_metrics_are_the_handlers_dict_serialized(
    client: httpx.AsyncClient,
) -> None:
    """The claim, checked as stated: typing changed no wire byte.

    The handler is called directly for the dict it builds, then the same app is
    driven over ASGI for the bytes it sends. The load-bearing assertion is the
    last one: the handler's dict serialized the way an untyped route would have
    serialized it, compared against what the typed route actually sent. A model
    that renamed, reordered, retyped or dropped a counter — at any depth —
    fails it, because the comparison never passes through the model.
    """
    expected = await retrieval_app.metrics(_metrics_request())
    response = await client.get("/metrics")

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload == expected
    assert list(payload) == list(expected)
    # `JSONResponse.render`'s arguments, which is what produced `response.text`.
    assert response.text == json.dumps(
        expected, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    assert response.text == MetricsResponse.model_validate(expected).model_dump_json()


async def test_metrics_mirror_round_trips_the_served_body(
    client: httpx.AsyncClient,
) -> None:
    """``model_validate`` → ``model_dump`` is the identity on a served body.

    The same three-assertion idiom ``tests/test_contract_errors.py`` uses on the
    error mirrors: validation (which ``extra="forbid"`` makes strict), dict and
    key-order equality, and serialized-byte equality.
    """
    response = await client.get("/metrics")

    payload: dict[str, Any] = response.json()
    mirrored = MetricsResponse.model_validate(payload)

    assert mirrored.model_dump() == payload
    assert list(mirrored.model_dump()) == list(payload)
    for section in _SECTION_MODELS:
        assert list(mirrored.model_dump()[section]) == list(payload[section])
    assert mirrored.model_dump_json() == response.text


async def test_every_section_the_handler_emits_has_a_model(
    client: httpx.AsyncClient,
) -> None:
    """No section reaches the wire as a free-form object."""
    payload: dict[str, Any] = (await client.get("/metrics")).json()

    assert set(payload) == {"contract_version", *_SECTION_MODELS}
    for section, model in _SECTION_MODELS.items():
        assert set(payload[section]) == set(model.model_fields)


async def test_cgroup_keys_stay_flat_in_the_extraction_section(
    client: httpx.AsyncClient,
) -> None:
    """The flatten decision, pinned on the wire and in the schema.

    ``tests/test_app.py::test_metrics_expose_saturation_and_oom_proximity`` is
    the older fossil guard for the same decision and stays untouched; this adds
    the schema half, which is what a generated contract would freeze.
    """
    extraction: dict[str, Any] = (await client.get("/metrics")).json()["extraction"]
    properties: dict[str, Any] = _openapi()["components"]["schemas"][
        "ExtractionMetricsResponse"
    ]["properties"]

    for key in _CGROUP_KEYS:
        assert key in extraction
        assert key in properties
        # A nested section would render as a `$ref`; these are scalars-or-null.
        assert "$ref" not in properties[key]
    # `verdicts` is the section's only object-valued member — no `memory: {...}`.
    assert {key for key, value in extraction.items() if isinstance(value, dict)} == {
        "verdicts"
    }


# ---------------------------------------------------------------------------
# The failure mode ``extra="forbid"`` buys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [*_SECTION_MODELS.values(), MetricsResponse])
def test_every_metrics_model_forbids_extras(model: type[BaseModel]) -> None:
    """Forbidding extras is what makes these models a leash rather than a filter."""
    assert model.model_config.get("extra") == "forbid"


async def test_an_unmodeled_counter_fails_loudly(client: httpx.AsyncClient) -> None:
    """A metric the model does not carry is a 500, not a quiet omission.

    Simulated at the one seam that injects keys into a section —
    ``_cgroup_memory_snapshot``, whose return is splatted into ``extraction`` —
    so the test exercises the real response-validation path rather than a
    hand-built model.
    """
    snapshot = {key: None for key in _CGROUP_KEYS} | {"swap_current_bytes": 1}
    with (
        patch.object(retrieval_app, "_cgroup_memory_snapshot", return_value=snapshot),
        pytest.raises(ResponseValidationError),
    ):
        await client.get("/metrics")


async def test_a_permissive_response_model_would_have_dropped_it_instead() -> None:
    """The counterfactual, measured on the locked FastAPI rather than asserted.

    This is *why* ``extra="forbid"`` is not decoration. With a permissive model
    FastAPI filters the unmodeled key out of the response and answers 200, so a
    counter someone added would be shipped, deployed and invisible — the
    failure mode this design refuses. Nothing here touches the real app.
    """
    probe = FastAPI()

    @probe.get("/probe", response_model=_PermissiveProbe)
    async def probe_route() -> dict[str, int]:
        return {"known": 1, "unmodeled": 2}

    # The decorator is what registers it; the name is never read again, and
    # this repo allows no inline suppression comments (CONVENTIONS.md).
    del probe_route

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe), base_url="http://probe"
    ) as probe_client:
        response = await probe_client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"known": 1}


def test_metrics_schema_is_fully_rendered() -> None:
    """``/metrics`` documents a real object, not ``additionalProperties: true``."""
    document = _openapi()
    schema: dict[str, Any] = document["paths"]["/metrics"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    components: dict[str, Any] = document["components"]["schemas"]

    assert schema == {"$ref": "#/components/schemas/MetricsResponse"}
    top: dict[str, Any] = components["MetricsResponse"]
    assert top["additionalProperties"] is False
    assert list(top["properties"]) == ["contract_version", *_SECTION_MODELS]
    for section, model in _SECTION_MODELS.items():
        assert top["properties"][section]["$ref"] == (
            f"#/components/schemas/{model.__name__}"
        )
        component: dict[str, Any] = components[model.__name__]
        assert component["additionalProperties"] is False
        assert set(component["required"]) == set(model.model_fields)
        for name in model.model_fields:
            assert component["properties"][name]["description"]


# ---------------------------------------------------------------------------
# The counter objects and their models cannot drift apart
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counters", "model"),
    [(CacheMetrics, CacheMetricsResponse), (ModelMetrics, ModelMetricsResponse)],
    ids=["cache", "model"],
)
def test_dataclass_counters_and_their_models_carry_the_same_fields(
    counters: type[Any],
    model: type[BaseModel],
) -> None:
    """Adding a counter to one of these dataclasses must reach its model.

    Only the two dataclass counter sets can be tied this way — ``extraction``
    mixes controller properties and the cgroup splat with its own counters, and
    that section is covered by the parity test instead.
    """
    assert {field.name for field in dataclasses.fields(counters)} == set(
        model.model_fields
    )


# ---------------------------------------------------------------------------
# App metadata: the document says what this process is and implements
# ---------------------------------------------------------------------------


async def test_openapi_info_reports_the_contract_version(
    client: httpx.AsyncClient,
) -> None:
    """``info.version`` is the wire contract, served live and not a placeholder."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    info: dict[str, Any] = response.json()["info"]
    assert info["title"] == "Forage"
    assert info["version"] == CONTRACT_VERSION
    assert info["version"] != "0.1.0"


async def test_health_and_the_document_report_one_contract_version(
    client: httpx.AsyncClient,
) -> None:
    """The three places this number appears are one constant, checked as three."""
    document = (await client.get("/openapi.json")).json()
    metrics_body = (await client.get("/metrics")).json()

    assert document["info"]["version"] == CONTRACT_VERSION
    assert metrics_body["contract_version"] == CONTRACT_VERSION


async def test_served_description_states_the_deployment_posture(
    client: httpx.AsyncClient,
) -> None:
    """A reader meets the no-auth/private-network precondition before a route."""
    description: str = (await client.get("/openapi.json")).json()["info"]["description"]

    assert "no authentication" in description.lower()
    assert "private network" in description.lower()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert path in description


def test_every_served_path_is_acknowledged_in_the_posture_doc() -> None:
    """Nothing Forage serves is missing from the unauthenticated-surface list.

    The documentation endpoints are the reason this is mechanical: they are
    registered by FastAPI, declared nowhere in this repo, and therefore exactly
    the kind of surface a hand-maintained list forgets.
    """
    section = _posture_section()
    served = {route.path for route in app.routes if isinstance(route, Route)}

    assert served >= {"/health", "/metrics", "/docs", "/redoc", "/openapi.json"}
    for path in sorted(served):
        assert f"`GET {path}`" in section or f"`{path}`" in section, (
            f"{path} is served but not acknowledged in docs/configuration.md's "
            "'Deployment posture' section"
        )


def _posture_section() -> str:
    """Return docs/configuration.md's 'Deployment posture' section."""
    text = _CONFIGURATION_DOC.read_text()
    match = re.search(r"\n## Deployment posture\n(.*?)\n## ", text, flags=re.DOTALL)
    assert match is not None, (
        "docs/configuration.md lost its Deployment posture section"
    )
    return match.group(1)


def test_posture_doc_states_the_docs_endpoints_are_unauthenticated() -> None:
    """The AC in prose: each documentation endpoint is named, with its auth."""
    section = _posture_section()

    for path in ("/openapi.json", "/docs", "/redoc"):
        row = next(
            (
                line
                for line in section.splitlines()
                if line.startswith(f"| `GET {path}`")
            ),
            None,
        )
        assert row is not None, f"{path} has no row in the posture table"
        assert "| none |" in row
