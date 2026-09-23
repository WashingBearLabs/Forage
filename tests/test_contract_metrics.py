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

import ast
import dataclasses
import gzip
import json
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from pydantic import BaseModel
from starlette.requests import Request
from starlette.routing import Route

import retrieval_app
from cache import CacheMetrics
from model_fetcher import ModelMetrics
from models import SearchRequest
from pipeline.contract import CONTRACT_VERSION
from pipeline.extraction_limits import extraction_settings_from_config
from pipeline.orchestrator import run_search_pipeline
from pipeline.search_providers.base import ProviderFailure, ProviderSearchResult
from pipeline.search_providers.brave import BraveApiProvider, BraveSettings
from pipeline.search_providers.searxng import SearxngProvider, SearxngSettings
from retrieval_app import (
    CacheMetricsResponse,
    ExtractionMetricsResponse,
    MetricsResponse,
    ModelMetricsResponse,
    RetrieveMetricsResponse,
    SearchMetricsResponse,
    app,
)
from tests.fakes import (
    FakeSearchProvider,
    RecordingSearchMetrics,
    client_patch,
    make_response,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIGURATION_DOC = _REPO_ROOT / "docs" / "configuration.md"

_CONFIG_READER_MODULES = (
    "retrieval_app.py",
    "cache.py",
    "pipeline/extraction_limits.py",
    "pipeline/search_providers/brave.py",
    "pipeline/search_providers/searxng.py",
    "pipeline/orchestrator.py",
    "pipeline/sanitizer_revision.py",
    "pipeline/retrieve_limits.py",
    "pipeline/config_bounds.py",
    "promptguard/classifier.py",
)
_BOUNDED_HELPERS = {
    "_bounded_int",
    "_bounded_float",
    "bounded_int",
    "bounded_float",
    "bounded_bool",
}
# The old extraction-local helper migrated to config_bounds; its float/bool
# siblings and lifespan's domain-list loop are now variable-key reads as well.
# These sites never count toward the literal-read floor.
_VARIABLE_KEY_READS = {
    ("cache.py", "_bounded_int", "key"),
    ("pipeline/search_providers/brave.py", "_bounded_int", "key"),
    ("pipeline/search_providers/brave.py", "_bounded_float", "key"),
    ("pipeline/config_bounds.py", "bounded_int", "key"),
    ("pipeline/config_bounds.py", "bounded_float", "key"),
    ("pipeline/config_bounds.py", "bounded_bool", "key"),
    ("retrieval_app.py", "lifespan", "key"),
}

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


async def test_domain_policy_counters_match_classes_models_and_wire(
    client: httpx.AsyncClient,
) -> None:
    payload = (await client.get("/metrics")).json()
    for section in ("retrieve", "search"):
        counters: retrieval_app.RetrieveMetrics | retrieval_app.SearchMetrics = getattr(
            app.state, f"{section}_metrics"
        )
        assert set(vars(counters)) == set(_SECTION_MODELS[section].model_fields)
        for name in ("policy_invalid_domain_entry", "policy_suffix_trusted_skip"):
            assert payload[section][name] == 0
            setattr(counters, name, 7)
    payload = (await client.get("/metrics")).json()
    for section in ("retrieve", "search"):
        for name in ("policy_invalid_domain_entry", "policy_suffix_trusted_skip"):
            assert payload[section][name] == 7


async def test_provider_counters_are_appended_and_emitted(
    client: httpx.AsyncClient,
) -> None:
    counters = app.state.search_metrics
    counters.provider_compressed_body = 3
    counters.provider_timeouts = 2
    payload = (await client.get("/metrics")).json()["search"]
    assert list(payload)[-2:] == ["provider_compressed_body", "provider_timeouts"]
    assert payload["provider_compressed_body"] == 3
    assert payload["provider_timeouts"] == 2


@pytest.mark.parametrize("name", ["searxng", "brave"])
@pytest.mark.parametrize(
    "case",
    [
        "served",
        "oversize",
        "unsupported",
        "malformed",
        "status-429",
        "budget-timeout",
        "operation-timeout",
        "transport-mid-body",
        "unexpected-mid-body",
        "connect-before-headers",
        "timeout-before-headers",
        "plain",
    ],
)
async def test_provider_header_and_timeout_signals_survive_every_outcome(
    name: str,
    case: str,
) -> None:
    provider = (
        SearxngProvider(settings=SearxngSettings(max_response_bytes=1000))
        if name == "searxng"
        else BraveApiProvider("sentinel", BraveSettings(max_response_bytes=1000))
    )
    target = f"pipeline.search_providers.{name}.httpx.AsyncClient"
    compressed = case not in {
        "plain",
        "connect-before-headers",
        "timeout-before-headers",
    }
    body = gzip.compress(b"{}")
    headers = {"content-encoding": "gzip"} if compressed else {}
    if case == "oversize":
        body = gzip.compress(b"x" * 1001)
    elif case == "unsupported":
        headers["content-encoding"] = "br"
    elif case == "malformed":
        body = body[:-1]
    elif case == "plain":
        body = b"{}"
    response = make_response(429 if case == "status-429" else 200, body, headers)
    after_headers: Exception | None = {
        "budget-timeout": TimeoutError("private"),
        "operation-timeout": httpx.ReadTimeout("private"),
        "transport-mid-body": httpx.ReadError("private"),
        "unexpected-mid-body": RuntimeError("private"),
    }.get(case)
    before_headers: Exception | None = {
        "connect-before-headers": httpx.ConnectError("private"),
        "timeout-before-headers": httpx.ConnectTimeout("private"),
    }.get(case)

    async def failing_body() -> AsyncIterator[bytes]:
        yield body[:2]
        assert after_headers is not None
        raise after_headers

    sink = RecordingSearchMetrics()
    with (
        client_patch(target, response=response, stream_error=before_headers),
        patch.object(response, "aiter_raw", side_effect=failing_body)
        if after_headers is not None
        else nullcontext(),
    ):
        outcome = await provider.search("q", 3)
    assert outcome.compressed is compressed
    timeout = case in {"budget-timeout", "operation-timeout", "timeout-before-headers"}
    if timeout:
        assert isinstance(outcome, ProviderFailure)
        assert (outcome.failure_class, outcome.detail) == ("timeout", "timeout")
    # Feed the real provider outcome through traversal, including failure-continue
    # and ordinary serve-and-break, without reusing its already-consumed stream.
    await run_search_pipeline(
        SearchRequest(query="q", promptguard_fail_closed=False),
        providers=[
            FakeSearchProvider(name=name, outcome=outcome),
            FakeSearchProvider(name="backup", paid=True),
        ],
        search_metrics=sink,
        config={},
    )
    assert sink.provider_compressed_body == int(compressed)
    assert sink.provider_timeouts == int(timeout)


@pytest.mark.parametrize("lone", [False, True])
async def test_compressed_zero_results_counts_before_either_reclassification_exit(
    lone: bool,
) -> None:
    sink = RecordingSearchMetrics()
    searxng = SearxngProvider()
    body = gzip.compress(b'{"results":[],"unresponsive_engines":["mojeek"]}')
    chain = (
        [searxng] if lone else [searxng, FakeSearchProvider(name="brave", paid=True)]
    )
    with client_patch(
        "pipeline.search_providers.searxng.httpx.AsyncClient",
        response=make_response(content=body, headers={"content-encoding": "gzip"}),
    ):
        await run_search_pipeline(
            SearchRequest(query="q", promptguard_fail_closed=False),
            providers=chain,
            search_metrics=sink,
            config={},
        )
    assert sink.provider_compressed_body == 1
    assert sink.provider_timeouts == 0
    assert sink.fallback_fired == int(not lone)


def test_provider_compression_defaults_are_false() -> None:
    assert not ProviderSearchResult("searxng", [], []).compressed
    assert not ProviderFailure("searxng", "timeout", "timeout").compressed


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


def test_config_registry_equals_documented_keys() -> None:
    """Borrow the single-source helpers, as test_contract_smoke does for schemas.

    The function-scoped import follows its _SCHEMA_MODELS precedent rather than
    moving helpers out of an existing contract guard just for this consumer.
    Only a key table's first column defines names; prose and request tables do not.
    """
    from tests.test_governance_docs import _cells, _section

    section = _section(_CONFIGURATION_DOC.read_text(), "## `config.yaml`")
    headings = [("### Top-level keys", "")]
    fenced = False
    for line in section.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and (match := re.fullmatch(r"### The `(\w+):` block", line)):
            headings.append((line, f"{match.group(1)}."))
    assert len(headings) > 1
    documented: set[str] = set()
    for heading, prefix in headings:
        body = _section(section, heading)
        tables = re.findall(
            r"^\| Key \|[^\n]*\n((?:\|[^\n]*(?:\n|$))+)", body, flags=re.MULTILINE
        )
        assert len(tables) == 1, f"Expected one key table under {heading}"
        keys = [
            _cells(row)[0].strip("`")
            for row in tables[0].splitlines()
            if _cells(row)[0].startswith("`")
        ]
        assert keys, f"Empty key table under {heading}"
        assert len(keys) == len(set(keys)), f"Duplicate keys under {heading}"
        documented.update(f"{prefix}{key}" for key in keys)
    assert isinstance(retrieval_app.KNOWN_CONFIG_KEYS, frozenset)
    assert documented == retrieval_app.KNOWN_CONFIG_KEYS
    blocks = {key.partition(".")[0] for key in documented if "." in key}
    assert blocks <= documented


def test_config_registry_covers_the_shipped_yaml() -> None:
    """Read the shipped file, on TestCacheSettings' documented-defaults precedent."""
    shipped: dict[str, Any] = yaml.safe_load((_REPO_ROOT / "config.yaml").read_text())
    assert shipped
    keys = set(shipped)
    for block, value in shipped.items():
        if isinstance(value, dict):
            leaves = cast(dict[str, Any], value)
            assert leaves, f"Shipped block {block} is empty"
            keys.update(f"{block}.{leaf}" for leaf in leaves)
    assert keys <= retrieval_app.KNOWN_CONFIG_KEYS


def _literal_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _config_read(node: ast.AST) -> tuple[ast.expr, ast.expr] | None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        return node.func.value, node.args[0]
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        return node.value, node.slice
    return None


class _ConfigKeySweep(ast.NodeVisitor):
    """Follow function-local mapping aliases, including the readers' cast seam."""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.literal_reads = 0
        self.bounded_readers: set[str] = set()
        self.variable_reads: list[tuple[str, str]] = []
        self.shared_calls: dict[str, set[str]] = {}
        self._shared_imports: dict[str, str] = {}
        self._aliases: dict[str, str] = {}
        self._function = "<module>"

    def _prefix(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return "" if node.id == "config" else self._aliases.get(node.id)
        if isinstance(node, ast.Attribute) and node.attr == "config":
            return ""
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "cast"
                and len(node.args) == 2
            ):
                return self._prefix(node.args[1])
            if isinstance(node.func, ast.Attribute) and node.func.attr == "copy":
                return self._prefix(node.func.value)
        if read := _config_read(node):
            receiver, key_node = read
            prefix, key = self._prefix(receiver), _literal_key(key_node)
            if prefix is not None and key is not None:
                return f"{prefix}{key}."
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "pipeline.config_bounds":
            for alias in node.names:
                self._shared_imports[alias.asname or alias.name] = alias.name

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self._function, self._aliases
        self._function, self._aliases = node.name, {}
        self.generic_visit(node)
        self._function, self._aliases = previous

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def _bind(self, target: ast.expr, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            prefix = self._prefix(value) if value is not None else None
            if prefix is None:
                self._aliases.pop(target.id, None)
            else:
                self._aliases[target.id] = prefix

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind(node.target, node.value)
        self.generic_visit(node)

    def _record_read(self, node: ast.AST) -> None:
        if read := _config_read(node):
            receiver, key_node = read
            prefix = self._prefix(receiver)
            if prefix is not None:
                key = _literal_key(key_node)
                if key is None:
                    self.variable_reads.append((self._function, ast.unparse(key_node)))
                else:
                    self.keys.add(f"{prefix}{key}")
                    self.literal_reads += 1

    def visit_Call(self, node: ast.Call) -> None:
        self._record_read(node)
        if isinstance(node.func, ast.Name):
            helper = self._shared_imports.get(node.func.id, node.func.id)
            if helper in _BOUNDED_HELPERS:
                assert len(node.args) >= 2, ast.unparse(node)
                prefix = (
                    ""
                    if isinstance(node.args[0], ast.Dict)
                    else self._prefix(node.args[0])
                )
                key = _literal_key(node.args[1])
                assert prefix is not None and key is not None, ast.unparse(node)
                dotted = f"{prefix}{key}"
                self.keys.add(dotted)
                self.bounded_readers.add(self._function)
                if node.func.id in self._shared_imports:
                    self.shared_calls.setdefault(helper, set()).add(dotted)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record_read(node)
        self.generic_visit(node)


def _swept_config_keys(path: Path) -> _ConfigKeySweep:
    sweep = _ConfigKeySweep()
    sweep.visit(ast.parse(path.read_text()))
    return sweep


def test_config_registry_covers_every_reader() -> None:
    """Adding a config reader means adding its module to _CONFIG_READER_MODULES.

    config_bounds is helper-only: its variable-key reads are resolved from
    callers' literal second arguments, not counted as direct literal sites.
    Check each helper's read and callers so that module cannot silently go dark.
    """
    sweeps = {
        name: _swept_config_keys(_REPO_ROOT / name) for name in _CONFIG_READER_MODULES
    }
    variable_reads = [
        (name, function, key)
        for name, sweep in sweeps.items()
        for function, key in sweep.variable_reads
    ]
    assert set(variable_reads) == _VARIABLE_KEY_READS
    assert len(variable_reads) == len(_VARIABLE_KEY_READS)
    assert sum(sweep.literal_reads for sweep in sweeps.values()) >= 8
    for module, reader in (
        ("cache.py", "cache_settings_from_config"),
        ("pipeline/extraction_limits.py", "extraction_settings_from_config"),
        ("pipeline/retrieve_limits.py", "retrieve_settings_from_config"),
        ("pipeline/search_providers/brave.py", "brave_settings_from_config"),
        ("pipeline/search_providers/searxng.py", "searxng_settings_from_config"),
        ("promptguard/classifier.py", "promptguard_threads_from_config"),
        ("retrieval_app.py", "lifespan"),
        ("retrieval_app.py", "promptguard_threshold_from_config"),
    ):
        assert reader in sweeps[module].bounded_readers, (module, reader)
    shared = sweeps["pipeline/config_bounds.py"]
    resolved: set[str] = set()
    for helper in ("bounded_int", "bounded_float", "bounded_bool"):
        assert (helper, "key") in shared.variable_reads
        call_keys = set[str]().union(
            *(sweep.shared_calls.get(helper, set[str]()) for sweep in sweeps.values())
        )
        assert call_keys, f"No literal callers of config_bounds.{helper}"
        resolved.update(call_keys)
    all_keys: set[str] = set()
    for name, sweep in sweeps.items():
        keys = sweep.keys | (
            resolved if name == "pipeline/config_bounds.py" else set[str]()
        )
        assert keys, f"No literal config keys resolved in {name}"
        assert keys <= retrieval_app.KNOWN_CONFIG_KEYS, (
            name,
            keys - retrieval_app.KNOWN_CONFIG_KEYS,
        )
        all_keys.update(keys)
    blocks = {
        key.partition(".")[0] for key in retrieval_app.KNOWN_CONFIG_KEYS if "." in key
    }
    for block in blocks:
        assert any(key.startswith(f"{block}.") for key in all_keys), block


@pytest.mark.parametrize(
    ("source", "expected", "direct", "bounded"),
    [
        ('config.get("planted_key")', {"planted_key"}, 1, False),
        ('request.app.state.config.get("planted_key", 0)', {"planted_key"}, 1, False),
        ('app.state.config["planted_key"]', {"planted_key"}, 1, False),
        (
            'raw = config.get("cache", {})\n'
            "local = cast(dict[str, object], raw)\n"
            "alias = local\n"
            '_bounded_int(alias, "planted_key", 1)\n'
            'alias.get("other_key")\n'
            'alias["third_key"]',
            {"cache", "cache.planted_key", "cache.other_key", "cache.third_key"},
            3,
            True,
        ),
        ('bounded_float(config, "planted_key", 1.0)', {"planted_key"}, 0, True),
        ('bounded_bool(config, "planted_key", False)', {"planted_key"}, 0, True),
    ],
)
def test_config_sweep_reports_planted_unregistered_keys(
    tmp_path: Path, source: str, expected: set[str], direct: int, bounded: bool
) -> None:
    module = tmp_path / "planted_reader.py"
    module.write_text("def reader(config):\n    " + source.replace("\n", "\n    "))
    sweep = _swept_config_keys(module)
    assert sweep.keys == expected
    assert sweep.keys - retrieval_app.KNOWN_CONFIG_KEYS == expected - {"cache"}
    assert sweep.literal_reads == direct
    assert sweep.bounded_readers == ({"reader"} if bounded else set())


def test_config_sweep_keeps_mapping_aliases_function_local(tmp_path: Path) -> None:
    module = tmp_path / "scoped_readers.py"
    module.write_text(
        "def cache_reader(config):\n"
        '    local = config.get("cache", {})\n'
        '    local.get("max_entries")\n'
        "def retrieve_reader(config):\n"
        '    local = config.get("retrieve", {})\n'
        '    local["max_promptguard_chunks"]\n'
        "def unrelated(local):\n"
        '    local.get("not_config")\n'
    )
    assert _swept_config_keys(module).keys == {
        "cache",
        "cache.max_entries",
        "retrieve",
        "retrieve.max_promptguard_chunks",
    }


def test_config_sweep_resolves_shared_helper_imports(tmp_path: Path) -> None:
    module = tmp_path / "shared_reader.py"
    module.write_text(
        "from pipeline.config_bounds import bounded_float as read_float\n"
        "def reader(config):\n"
        '    read_float({"planted_key": 0.5}, "planted_key", 0.85)\n'
    )
    sweep = _swept_config_keys(module)
    assert sweep.keys == {"planted_key"}
    assert sweep.shared_calls == {"bounded_float": {"planted_key"}}
    assert sweep.literal_reads == 0
    assert sweep.bounded_readers == {"reader"}


def test_config_sweep_refuses_an_unresolved_block_helper(tmp_path: Path) -> None:
    module = tmp_path / "opaque_reader.py"
    module.write_text(
        "def reader(config):\n"
        "    local = opaque_helper(config)\n"
        '    bounded_int(local, "planted_key", 1)\n'
    )
    with pytest.raises(AssertionError, match="planted_key"):
        _swept_config_keys(module)


def test_unknown_config_key_warning_is_documented() -> None:
    from tests.test_governance_docs import _cells, _section

    section = _section(_CONFIGURATION_DOC.read_text(), "## `config.yaml`")
    for phrase in (
        "Unknown keys are ignored",
        "WARNING",
        "config_unknown_key",
        "ExtractionConfigurationError",
        "CacheConfigurationError",
        "promptguard_threshold",
        "policy_domain_entries_max_bytes",
        "seed_blocklist",
        "news_domains",
        "config_invalid_value",
    ):
        assert phrase in section
    monitoring = (_REPO_ROOT / "kit_tools/docs/MONITORING.md").read_text()
    startup = _section(monitoring, "### Startup lines you may see")
    rows = [_cells(line) for line in startup.splitlines() if line.startswith("|")]
    assert any(row[0] == "WARNING" and "config_unknown_key" in row[1] for row in rows)
    logging = (_REPO_ROOT / "kit_tools/arch/patterns/LOGGING.md").read_text()
    assert "config_unknown_key" in _section(logging, "## Logger Inventory")
    troubleshooting = (_REPO_ROOT / "kit_tools/docs/TROUBLESHOOTING.md").read_text()
    assert "restart" in _section(troubleshooting, "### config_unknown_key")
