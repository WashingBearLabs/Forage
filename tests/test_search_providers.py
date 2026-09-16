"""Tests for the ``SearchProvider`` seam (US-001).

No pipeline change is exercised here — ``run_search_pipeline`` still calls
SearXNG inline; extracting it behind the protocol is US-002. These tests only
pin the protocol shape, the closed failure vocabulary, and the boundary rule
that provider code never imports a sanitization stage or the cache.

test_mapping:
  pipeline/search_providers/__init__.py: tests/test_search_providers.py
  pipeline/search_providers/base.py: tests/test_search_providers.py
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

import pipeline.search_providers
from pipeline.search_providers.base import (
    FAILURE_CLASSES,
    FailureClass,
    ProviderFailure,
    ProviderSearchResult,
    SearchProvider,
)
from tests.fakes import FakeSearchProvider, assert_frozen

# ---------------------------------------------------------------------------
# Closed failure vocabulary
# ---------------------------------------------------------------------------


def test_failure_classes_is_the_closed_five() -> None:
    assert {
        "rate_limited",
        "timeout",
        "hard_error",
        "auth",
        "quota",
    } == FAILURE_CLASSES


# ---------------------------------------------------------------------------
# Field exactness — the internal, never-wire shapes
# ---------------------------------------------------------------------------


def test_provider_search_result_fields_are_exact() -> None:
    names = {f.name for f in fields(ProviderSearchResult)}
    assert names == {"provider_name", "results", "unresponsive_engines"}


def test_provider_failure_fields_are_exact() -> None:
    names = {f.name for f in fields(ProviderFailure)}
    assert names == {"provider_name", "failure_class", "detail"}


def test_provider_search_result_is_frozen() -> None:
    result = ProviderSearchResult(
        provider_name="fake", results=[], unresponsive_engines=[]
    )
    assert_frozen(result, "provider_name", "other")


def test_provider_failure_is_frozen() -> None:
    failure = ProviderFailure(
        provider_name="fake", failure_class="timeout", detail="timeout"
    )
    assert_frozen(failure, "detail", "other")


# ---------------------------------------------------------------------------
# A fake driven through every path a real provider can take
# ---------------------------------------------------------------------------


class TestFakeProviderConformance:
    """``FakeSearchProvider`` satisfies ``SearchProvider`` structurally.

    Assigning it to a ``SearchProvider``-typed variable is the assertion that
    matters here: ``uv run pyright`` (strict) fails this module if the fake's
    shape ever drifts from the protocol.
    """

    @pytest.mark.asyncio()
    async def test_populated_result_is_a_success(self) -> None:
        expected = ProviderSearchResult(
            provider_name="fake",
            results=[
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "content": "snippet text",
                    "engine": "duckduckgo",
                    "date": None,
                }
            ],
            unresponsive_engines=[],
        )
        provider: SearchProvider = FakeSearchProvider(outcome=expected)
        outcome = await provider.search("query", 5)
        assert outcome is expected
        assert isinstance(outcome, ProviderSearchResult)

    @pytest.mark.asyncio()
    async def test_empty_result_list_is_a_success_never_a_failure(self) -> None:
        provider: SearchProvider = FakeSearchProvider(
            outcome=ProviderSearchResult(
                provider_name="fake", results=[], unresponsive_engines=[]
            )
        )
        outcome = await provider.search("query", 5)
        assert isinstance(outcome, ProviderSearchResult)
        assert outcome.results == []

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("failure_class", sorted(FAILURE_CLASSES))
    async def test_each_failure_class_is_driven(
        self, failure_class: FailureClass
    ) -> None:
        provider: SearchProvider = FakeSearchProvider(
            outcome=ProviderFailure(
                provider_name="fake",
                failure_class=failure_class,
                detail="fixed_token",
            )
        )
        outcome = await provider.search("query", 5)
        assert isinstance(outcome, ProviderFailure)
        assert outcome.failure_class == failure_class
        assert outcome.detail == "fixed_token"

    @pytest.mark.asyncio()
    async def test_calls_are_recorded_with_query_and_max_results(self) -> None:
        provider = FakeSearchProvider()
        await provider.search("weather in boston", 7)
        assert provider.calls == [("weather in boston", 7)]


# ---------------------------------------------------------------------------
# Protocol docstring — the contract lives in the text, so pin the text
# ---------------------------------------------------------------------------


class TestProtocolDocstring:
    """The six-point provider contract, stated where implementers read it."""

    def test_states_name_is_the_chain_token(self) -> None:
        assert "chain token" in (SearchProvider.__doc__ or "")

    def test_states_engine_is_provenance(self) -> None:
        assert "provenance" in (SearchProvider.__doc__ or "")

    def test_states_the_six_point_contract(self) -> None:
        doc = SearchProvider.__doc__ or ""
        for phrase in (
            "trust_env=False",
            "hard_error",
            "malformed_body",
            "unexpected",
            "never raise",
            "body_too_large",
        ):
            assert phrase in doc, f"protocol docstring is missing {phrase!r}"


# ---------------------------------------------------------------------------
# Boundary rule: provider code never imports a sanitization stage or the cache
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = frozenset(
    {
        "pipeline.stage1_extraction",
        "pipeline.stage2_structural",
        "pipeline.stage3_promptguard",
        "promptguard",
        "cache",
        "models",
    }
)


def _imported_modules(module_path: Path) -> set[str]:
    """Every module name a file imports, read out of its own AST."""
    tree = ast.parse(module_path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_no_search_provider_module_imports_a_sanitization_stage_or_the_cache() -> None:
    """Ruling 11: provider code changes what is fetched, not how it is sanitized.

    Swept mechanically, the ``_swept_error_codes`` idiom
    (``tests/test_contract_errors.py``): a hand-written list would miss the
    next provider module the way a hand-written error-code list once missed a
    raise site.
    """
    package_dir = Path(pipeline.search_providers.__file__).parent
    for path in sorted(package_dir.glob("*.py")):
        offending = _imported_modules(path) & _FORBIDDEN_IMPORTS
        assert not offending, f"{path} imports forbidden modules: {offending}"
