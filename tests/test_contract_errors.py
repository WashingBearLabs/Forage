"""The documented error surface, and the parity tests that keep it honest.

``feature-forage-contract`` US-001 documents every error body the service
already emits **without changing one wire byte**. Two halves make that claim
checkable rather than asserted:

* the *vocabulary* half sweeps every raise site in the repo and pins the
  seventeen-code set against Poppy's independently-maintained allowlist;
* the *parity* half drives each emission site through the real routes and
  asserts the mirroring model reproduces the emitted body byte-for-byte —
  same keys, same order, same serialization.

A model that would tolerate a shape change is a failed mirror, so the parity
assertions are deliberately strict: ``extra="forbid"`` on the models plus an
exact serialized-bytes comparison here. If an emission site is edited and its
mirror is not, this module goes red before ``contract/openapi.yaml`` can
document something the service does not do.
"""

from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.types import Message, Receive, Scope, Send

import pipeline.orchestrator
import retrieval_app
from pipeline import contract
from pipeline.contract import (
    DEGRADED_CACHE_UNAVAILABLE,
    DEGRADED_PROMPTGUARD_UNAVAILABLE,
)
from pipeline.extraction_limits import extraction_settings_from_config
from pipeline.orchestrator import DOCUMENT_FAILURE_CODES, PipelineError
from promptguard.classifier import PromptGuardClassifier
from retrieval_app import (
    Admission413Response,
    DetailResponse,
    DocumentSizeLimitMiddleware,
    Extract422ErrorResponse,
    ExtractionAdmissionController,
    ExtractionMetrics,
    HealthResponse,
    HTTPValidationError,
    Pipeline422ErrorResponse,
    RateLimit429Response,
    RetrieveMetrics,
    SearchMetrics,
    app,
)
from tests.fakes import FakeContentCache

_MEBIBYTE = 1024 * 1024

# ---------------------------------------------------------------------------
# Poppy's pinned allowlist, inlined verbatim
# ---------------------------------------------------------------------------

# Copied byte-for-byte from the consuming repository —
# `poppy/core/retrieval/client.py`'s `_SIDECAR_EXTRACT_FAILURE_CODES`, the set
# Poppy validates every /extract 422 against before it will believe one. It is
# inlined rather than imported because that file lives in another repository
# and Forage must never import from Poppy (CONVENTIONS.md, "Imports"). The
# copy is a *test fixture of the consumer's expectation*: if Forage's own
# vocabulary drifts from it, the two repos disagree about what a document
# failure is, and the assertion below says so here rather than in production.
_POPPY_PINNED_EXTRACT_FAILURE_CODES = frozenset(
    {
        "busy",
        "content_too_large",
        "content_too_large_to_classify",
        "extraction_failed",
        "invalid_filename",
        "invalid_mime_hint",
        "invalid_request_id",
        "pdf_encrypted",
        "pdf_no_text",
        "unsupported_format",
    }
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _configure_app(config: dict[str, Any]) -> None:
    """Populate ``app.state`` the way the lifespan would, for one config.

    ``tests/test_app.py``'s ``client`` fixture does the same thing; this
    module needs the settings to vary per test (a small byte cap for the 413
    site, a zero-depth queue for the 429 one), so the wiring is a function
    rather than a fixture constant.
    """
    settings = extraction_settings_from_config(config)
    app.state.classifier = PromptGuardClassifier()
    app.state.cache = FakeContentCache()
    app.state.config = config
    app.state.extraction_settings = settings
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )
    app.state.search_metrics = SearchMetrics()
    app.state.retrieve_metrics = RetrieveMetrics()
    app.state.classification_semaphore = asyncio.Semaphore(
        settings.classification_concurrency
    )
    app.state.sanitizer_revision = "revision-under-test"


@pytest.fixture
def client() -> Iterator[httpx.AsyncClient]:
    """Drive the real app over ASGI with the extract route enabled."""
    _configure_app({"extract_route_enabled": True})
    transport = httpx.ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")


def _client() -> httpx.AsyncClient:
    """Return a client for the app as currently configured."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def assert_mirrors(model: type[BaseModel], response: httpx.Response) -> None:
    """Assert ``model`` reproduces ``response``'s body exactly.

    Three assertions, in increasing strictness, because each catches a
    different way a mirror can rot:

    1. ``model_validate`` — with ``extra="forbid"`` this fails if the wire
       grew a field the model lacks, or lost one the model requires.
    2. dict equality plus key order — a renamed or reordered field.
    3. serialized-byte equality against what the service actually sent — the
       claim the spec makes, checked as stated rather than approximated.
    """
    payload: dict[str, Any] = response.json()
    mirrored = model.model_validate(payload)
    dumped = mirrored.model_dump()

    assert dumped == payload
    assert list(dumped) == list(payload)
    assert mirrored.model_dump_json() == response.text


# ---------------------------------------------------------------------------
# The vocabulary: seventeen codes, swept from the raise sites
# ---------------------------------------------------------------------------

# Calls whose error code can be read straight off the call site. The value is
# the argument to read: an index for a positional, or a fixed code for a
# constructor that hardcodes one.
_CODE_FROM_FIRST_POSITIONAL = ("document_failure", "__init__")
_FIXED_CODE_CONSTRUCTORS = {"UnsupportedFormatError": "unsupported_format"}


def _callee_name(node: ast.Call) -> str:
    """Return the bare name of a call's callee, attribute access included."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _string_constant(node: ast.expr | None) -> str | None:
    """Return a string literal's value, or ``None`` for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _swept_error_codes(module_path: Path) -> set[str]:
    """Extract every literal error code a module can put on the wire.

    Four shapes cover the repo's raise sites:

    * ``PipelineError(error="private_ip", ...)`` — the keyword form;
    * ``PipelineError("invalid_filename", ...)`` / ``document_failure("...")``
      / ``super().__init__("unsupported_format", ...)`` — the positional form;
    * ``UnsupportedFormatError(...)``, which hardcodes its code in the class;
    * a dict literal carrying an ``"error"`` key — the two middlewares, which
      build their JSON bodies directly rather than raising.
    """
    tree = ast.parse(module_path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if _string_constant(key) == "error":
                    code = _string_constant(value)
                    if code is not None:
                        found.add(code)
        elif isinstance(node, ast.Call):
            name = _callee_name(node)
            if name in _FIXED_CODE_CONSTRUCTORS:
                found.add(_FIXED_CODE_CONSTRUCTORS[name])
                continue
            keyword = next(
                (
                    _string_constant(kw.value)
                    for kw in node.keywords
                    if kw.arg == "error"
                ),
                None,
            )
            if keyword is not None:
                found.add(keyword)
                continue
            if name in _CODE_FROM_FIRST_POSITIONAL or name == "PipelineError":
                code = _string_constant(node.args[0]) if node.args else None
                if code is not None:
                    found.add(code)
    return found


def test_error_vocabulary_is_the_documented_seventeen() -> None:
    """The complete vocabulary is seventeen codes across the three surfaces."""
    assert len(contract.ERROR_CODES) == 17
    assert contract.ERROR_CODES == (
        contract.EXTRACT_ERROR_CODES
        | contract.RETRIEVE_ERROR_CODES
        | contract.SEARCH_ERROR_CODES
    )
    assert len(contract.EXTRACT_ERROR_CODES) == 10
    assert len(contract.RETRIEVE_ERROR_CODES) == 6
    assert len(contract.SEARCH_ERROR_CODES) == 2
    # `content_too_large` is the one code two surfaces share, and it is why
    # 10 + 6 + 2 documents seventeen codes rather than eighteen.
    assert {
        "content_too_large"
    } == contract.EXTRACT_ERROR_CODES & contract.RETRIEVE_ERROR_CODES


def test_every_raise_site_in_the_repo_is_in_the_vocabulary() -> None:
    """A new raise site with an undocumented code fails here, not in the field.

    The sweep is mechanical on purpose: the round-2 review found a raise site
    a hand-written list had missed, and a hand-written list would miss the
    next one too.
    """
    swept = _swept_error_codes(
        Path(pipeline.orchestrator.__file__)
    ) | _swept_error_codes(Path(retrieval_app.__file__))

    assert swept == set(contract.ERROR_CODES)


def test_extract_surface_matches_the_orchestrator_taxonomy() -> None:
    """The ten /extract codes are the seven document failures plus three."""
    assert DOCUMENT_FAILURE_CODES < contract.EXTRACT_ERROR_CODES
    assert {
        "invalid_filename",
        "invalid_mime_hint",
        "invalid_request_id",
    } == contract.EXTRACT_ERROR_CODES - DOCUMENT_FAILURE_CODES
    # `busy` is the one /extract code the 422 handler never emits: it answers
    # 429 instead.
    assert {"busy"} == contract.EXTRACT_ERROR_CODES - contract.EXTRACT_422_ERROR_CODES


def test_extract_vocabulary_matches_poppys_pinned_allowlist() -> None:
    """Forage's /extract codes are exactly the set Poppy's client accepts."""
    assert contract.EXTRACT_ERROR_CODES == _POPPY_PINNED_EXTRACT_FAILURE_CODES


def test_degraded_reasons_derive_from_one_source() -> None:
    """The reason constants and the Literal cannot drift apart."""
    assert {
        DEGRADED_PROMPTGUARD_UNAVAILABLE,
        DEGRADED_CACHE_UNAVAILABLE,
    } == contract.DEGRADED_REASONS


# ---------------------------------------------------------------------------
# Parity: one test per emission site, driven through the real routes
# ---------------------------------------------------------------------------


async def test_extract_422_handler_body_is_mirrored(
    client: httpx.AsyncClient,
) -> None:
    """`/extract` 422: four fields, `sanitizer_revision` among them."""
    settings = app.state.extraction_settings
    response = await client.post(
        "/extract",
        files={
            "file": (
                "large.txt",
                b"a" * (settings.max_extracted_characters + 1),
                "text/plain",
            )
        },
        data={"filename": "large.txt"},
    )

    assert response.status_code == 422
    payload: dict[str, Any] = response.json()
    assert payload["error"] == "content_too_large_to_classify"
    assert payload["sanitizer_revision"] == "revision-under-test"
    assert_mirrors(Extract422ErrorResponse, response)


async def test_extract_422_metadata_refusal_is_mirrored(
    client: httpx.AsyncClient,
) -> None:
    """The pre-pipeline metadata refusals emit the same four-field shape."""
    response = await client.post(
        "/extract",
        files={"file": ("document.txt", b"safe", "text/plain")},
        data={"filename": "x" * 256},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_filename"
    assert_mirrors(Extract422ErrorResponse, response)


@pytest.mark.parametrize(
    ("route", "body", "patched", "error"),
    [
        (
            "/retrieve",
            {"url": "https://internal.example/secret"},
            "retrieval_app.run_retrieve_pipeline",
            "private_ip",
        ),
        (
            "/search",
            {"query": "test"},
            "retrieval_app.run_search_pipeline",
            "searxng_unavailable",
        ),
    ],
    ids=["retrieve", "search"],
)
async def test_pipeline_422_body_is_mirrored(
    client: httpx.AsyncClient,
    route: str,
    body: dict[str, str],
    patched: str,
    error: str,
) -> None:
    """`/retrieve` and `/search` 422: three fields, no `sanitizer_revision`.

    The handler adds that fourth field on ``/extract`` paths only, and this is
    the assertion that says so from the outside.
    """
    exc = PipelineError(error=error, reason="upstream refused", request_id="r1")
    with patch(patched, new=AsyncMock(side_effect=exc)):
        response = await client.post(route, json=body)

    assert response.status_code == 422
    assert set(response.json()) == {"error", "reason", "request_id"}
    assert_mirrors(Pipeline422ErrorResponse, response)


async def test_extract_413_body_is_mirrored_and_carries_no_request_id() -> None:
    """`/extract` 413: two fields — the streaming refusal predates a request id.

    Driven at the ASGI seam rather than through the route, and that is a
    finding rather than a convenience: on the pinned FastAPI the route's own
    body-parsing guard swallows this refusal and answers 400 instead (see
    :func:`test_extract_oversized_upload_actually_receives_400`). The
    middleware still emits exactly this shape to any downstream that honours
    the raw ASGI contract, so the shape is documented — and this is the only
    place it can be exercised.
    """
    sent: list[Message] = []
    messages = iter([{"type": "http.request", "body": b"x" * 20, "more_body": False}])

    async def receive() -> Message:
        return next(messages)

    async def send(message: Message) -> None:
        sent.append(message)

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, send
        while True:
            message = await receive()
            if not message.get("more_body", False):
                return

    middleware = DocumentSizeLimitMiddleware(downstream, max_bytes=10)
    await middleware({"type": "http", "path": "/extract", "headers": []}, receive, send)

    assert sent[0]["status"] == 413
    body: dict[str, Any] = json.loads(sent[1]["body"])
    assert set(body) == {"error", "reason"}

    mirrored = Admission413Response.model_validate(body)
    assert mirrored.model_dump() == body
    assert list(mirrored.model_dump()) == list(body)
    assert mirrored.model_dump_json().encode() == sent[1]["body"]


async def test_extract_oversized_upload_actually_receives_400() -> None:
    """What an over-sized upload really gets, pinned so a FastAPI bump shows.

    ``fastapi/routing.py`` wraps *any* exception out of ``request.form()`` in
    ``HTTPException(400, "There was an error parsing the body")``, and
    ``DocumentSizeLimitMiddleware`` refuses by raising through ``receive`` —
    so the refusal never reaches the middleware's own handler and the
    documented 413 is shadowed. No wire byte was changed to discover this and
    none is changed to record it: the 400 is declared alongside the 413 so the
    contract describes what a consumer receives as well as what the middleware
    emits.
    """
    _configure_app(
        {
            "extract_route_enabled": True,
            "extraction": {"max_input_bytes": _MEBIBYTE},
        }
    )
    async with _client() as client:
        response = await client.post(
            "/extract",
            files={"file": ("big.txt", b"a" * (2 * _MEBIBYTE), "text/plain")},
            data={"filename": "big.txt"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "There was an error parsing the body"}
    assert_mirrors(DetailResponse, response)


async def test_extract_429_body_is_mirrored() -> None:
    """`/extract` 429: the middleware mints its own id and revision."""
    _configure_app(
        {
            "extract_route_enabled": True,
            "extraction": {"admission_queue_depth": 0},
        }
    )
    controller: ExtractionAdmissionController = app.state.extraction_admission
    assert await controller.acquire() is True

    async with _client() as client:
        response = await client.post(
            "/extract",
            files={"file": ("document.txt", b"safe", "text/plain")},
            data={"filename": "document.txt"},
        )

    assert response.status_code == 429
    assert response.json()["error"] == "busy"
    assert_mirrors(RateLimit429Response, response)
    await controller.release()


async def test_extract_404_body_is_mirrored() -> None:
    """`/extract` 404: the release gate answers the bare detail shape."""
    _configure_app({})
    async with _client() as client:
        response = await client.post(
            "/extract",
            files={"file": ("document.txt", b"safe", "text/plain")},
            data={"filename": "document.txt"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert_mirrors(DetailResponse, response)


async def test_extract_503_body_is_mirrored() -> None:
    """`/extract` 503: no admission controller, so nothing coded can be said."""
    _configure_app({"extract_route_enabled": True})
    app.state.extraction_admission = None

    async with _client() as client:
        response = await client.post(
            "/extract",
            files={"file": ("document.txt", b"safe", "text/plain")},
            data={"filename": "document.txt"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Unavailable"}
    assert_mirrors(DetailResponse, response)


@pytest.mark.parametrize(
    ("route", "body"),
    [("/retrieve", {}), ("/search", {})],
    ids=["retrieve", "search"],
)
async def test_validation_arm_of_the_422_union_is_real(
    client: httpx.AsyncClient,
    route: str,
    body: dict[str, str],
) -> None:
    """A malformed request really does return FastAPI's default 422 body.

    The union declared on each 422 is not hypothetical: the pipeline arm and
    this one are both reachable, which is why the schema documents both.
    ``HTTPValidationError`` mirrors the stable trio and deliberately tolerates
    the extra per-error keys pydantic adds (``input``, sometimes ``ctx``).
    """
    response = await client.post(route, json=body)

    assert response.status_code == 422
    mirrored = HTTPValidationError.model_validate(response.json())
    assert mirrored.detail
    assert mirrored.detail[0].type == "missing"
    assert "error" not in response.json()


async def test_health_degraded_reasons_survive_response_validation(
    client: httpx.AsyncClient,
) -> None:
    """The enum-typed field still serializes the exact strings it always did."""
    response = await client.get("/health")

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["degraded_reasons"] == [
        DEGRADED_PROMPTGUARD_UNAVAILABLE,
        DEGRADED_CACHE_UNAVAILABLE,
    ]
    assert HealthResponse.model_validate(payload).model_dump() == payload


# ---------------------------------------------------------------------------
# The generated schema: exactly the route/status pairs that emit
# ---------------------------------------------------------------------------


def _openapi() -> dict[str, Any]:
    """Return the app's generated OpenAPI document."""
    return app.openapi()


def test_declared_error_statuses_match_the_emission_map() -> None:
    """Declarations sit on the pairs that emit, and nowhere else.

    ``/health`` and ``/metrics`` are the load-bearing half of this assertion:
    both only ever answer 200, and an error declaration on either would be
    documentation of something that cannot happen.
    """
    paths = _openapi()["paths"]
    declared = {
        path: sorted(operation["responses"])
        for path, methods in paths.items()
        for operation in methods.values()
    }

    assert declared == {
        "/health": ["200"],
        "/metrics": ["200"],
        "/retrieve": ["200", "422"],
        "/search": ["200", "422"],
        "/extract": ["200", "400", "404", "413", "422", "429", "503"],
    }


def test_each_declaration_points_at_its_mirror_model() -> None:
    """Every declared status references the component that mirrors its site."""
    paths = _openapi()["paths"]

    def schema_for(path: str, status: str) -> dict[str, Any]:
        operation = next(iter(paths[path].values()))
        content: dict[str, Any] = operation["responses"][status]["content"]
        return content["application/json"]["schema"]

    assert schema_for("/extract", "400") == {
        "$ref": "#/components/schemas/DetailResponse"
    }
    assert schema_for("/extract", "404") == {
        "$ref": "#/components/schemas/DetailResponse"
    }
    assert schema_for("/extract", "503") == {
        "$ref": "#/components/schemas/DetailResponse"
    }
    assert schema_for("/extract", "413") == {
        "$ref": "#/components/schemas/Admission413Response"
    }
    assert schema_for("/extract", "429") == {
        "$ref": "#/components/schemas/RateLimit429Response"
    }
    for path, pipeline_model in (
        ("/extract", "Extract422ErrorResponse"),
        ("/retrieve", "Pipeline422ErrorResponse"),
        ("/search", "Pipeline422ErrorResponse"),
    ):
        assert schema_for(path, "422")["anyOf"] == [
            {"$ref": f"#/components/schemas/{pipeline_model}"},
            {"$ref": "#/components/schemas/HTTPValidationError"},
        ]


def test_all_seventeen_codes_render_as_enums_in_the_schema() -> None:
    """Every code reaches the document as an enum member of some component."""
    components: dict[str, Any] = _openapi()["components"]["schemas"]
    rendered: set[str] = set()
    for name in (
        "Extract422ErrorResponse",
        "Pipeline422ErrorResponse",
        "Admission413Response",
        "RateLimit429Response",
    ):
        error_schema: dict[str, Any] = components[name]["properties"]["error"]
        # A single-member Literal renders as `const`, more than one as `enum`.
        members = error_schema.get("enum") or [error_schema["const"]]
        rendered.update(members)

    assert rendered == set(contract.ERROR_CODES)


def test_extract_422_component_documents_sanitizer_revision() -> None:
    """Poppy's hard requirement is visible in the schema, not only in prose."""
    component: dict[str, Any] = _openapi()["components"]["schemas"][
        "Extract422ErrorResponse"
    ]

    assert "sanitizer_revision" in component["required"]
    assert component["properties"]["sanitizer_revision"]["description"]
    assert component["additionalProperties"] is False
    # The sibling shape is the same body minus that field — the difference the
    # two models exist to record.
    pipeline_component: dict[str, Any] = _openapi()["components"]["schemas"][
        "Pipeline422ErrorResponse"
    ]
    assert "sanitizer_revision" not in pipeline_component["properties"]


def test_degraded_reasons_and_dict_vocabularies_are_documented() -> None:
    """`degraded_reasons` renders as an enum; the dict fields carry their keys."""
    components: dict[str, Any] = _openapi()["components"]["schemas"]
    health: dict[str, Any] = components["HealthResponse"]["properties"]

    assert health["degraded_reasons"]["items"]["enum"] == [
        DEGRADED_PROMPTGUARD_UNAVAILABLE,
        DEGRADED_CACHE_UNAVAILABLE,
    ]
    assert (
        retrieval_app.CAPABILITY_SEARCH_SANITIZATION
        in health["capabilities"]["description"]
    )

    omitted: dict[str, Any] = components["SearchResponse"]["properties"][
        "omitted_by_reason"
    ]
    for reason in contract.OMISSION_REASONS:
        assert reason in omitted["description"]


# ---------------------------------------------------------------------------
# The FastAPI behaviour the union declarations depend on
# ---------------------------------------------------------------------------


class _ProbeBody(BaseModel):
    """Request body for the throwaway app the FastAPI behaviour pin builds.

    Module level rather than nested in the test: this repo runs
    ``from __future__ import annotations``, and a function-local model leaves
    pydantic with an unresolvable ``ForwardRef`` at schema-generation time.
    """

    value: int


def test_declaring_422_suppresses_fastapis_automatic_one() -> None:
    """Pinned against the locked FastAPI, because the whole design rests on it.

    Declaring a 422 response stops FastAPI adding its own
    ``HTTPValidationError`` one — which is why each declaration has to carry
    the validation arm itself. A FastAPI bump that changed this would silently
    either drop the validation arm from the document or duplicate it; this
    test turns that into a red gate.
    """
    probe = FastAPI()

    @probe.post("/declared", responses={422: {"model": DetailResponse}})
    async def declared(body: _ProbeBody) -> dict[str, int]:
        return {"value": body.value}

    @probe.post("/undeclared")
    async def undeclared(body: _ProbeBody) -> dict[str, int]:
        return {"value": body.value}

    # The decorators are what register these; the names themselves are never
    # read again, and this repo allows no inline suppression comments
    # (CONVENTIONS.md, "Type-checking policy").
    del declared, undeclared

    schema: dict[str, Any] = probe.openapi()
    responses: dict[str, Any] = schema["paths"]["/declared"]["post"]["responses"]
    auto: dict[str, Any] = schema["paths"]["/undeclared"]["post"]["responses"]

    assert responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DetailResponse"
    }
    assert auto["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }


def test_our_validation_mirror_matches_fastapis_own_definition() -> None:
    """The mirrored ``HTTPValidationError`` documents FastAPI's real shape.

    Because every body-taking route declares its 422, FastAPI never injects
    its own component and ours is the one the document carries. That makes
    this comparison the only thing standing between a FastAPI change to the
    validation body and a contract that quietly describes the old one.
    """
    from fastapi.openapi.utils import validation_error_definition

    definition: dict[str, Any] = dict(validation_error_definition)
    required: list[str] = definition["required"]
    mirrored: dict[str, Any] = HTTPValidationError.model_json_schema()
    detail_properties: dict[str, Any] = mirrored["$defs"]["ValidationErrorDetail"][
        "properties"
    ]

    assert set(required) == set(detail_properties)
    assert set(mirrored["$defs"]["ValidationErrorDetail"]["required"]) == set(required)


async def test_no_error_declaration_changed_a_success_body() -> None:
    """The declarations are documentation: 200 bodies are untouched."""
    _configure_app({"extract_route_enabled": True})
    async with _client() as client:
        health = await client.get("/health")
        metrics = await client.get("/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
    assert json.loads(health.text)["contract_version"] == contract.CONTRACT_VERSION
    assert json.loads(metrics.text)["contract_version"] == contract.CONTRACT_VERSION
