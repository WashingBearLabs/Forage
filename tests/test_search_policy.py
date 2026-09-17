"""Tests for the per-request policy function (``search-policy-and-health`` US-010).

``apply_request_policy`` is a pure function: given the operator-configured
provider chain and a request's ``providers``/``allow_paid_fallback`` policy,
it returns the effective chain and how many ``providers`` entries were
ignored. It never calls a provider, never mutates its inputs, and never
raises.

test_mapping:
  pipeline/search_providers/policy.py: tests/test_search_policy.py
"""

from __future__ import annotations

import itertools

from models import SearchRequest
from pipeline.search_providers.policy import apply_request_policy
from tests.fakes import FakeSearchProvider


def _is_subsequence(sub: list[str], full: list[str]) -> bool:
    """Whether *sub*'s names appear in *full*, in the same relative order."""
    it = iter(full)
    return all(name in it for name in sub)


def _chain(shape: tuple[bool, ...]) -> list[FakeSearchProvider]:
    """Build a chain from a tuple of ``paid`` flags, uniquely named by position."""
    return [FakeSearchProvider(name=f"p{i}", paid=paid) for i, paid in enumerate(shape)]


# Every chain shape of one to four providers, any mix of free/paid, any order.
_CHAIN_SHAPES: list[tuple[bool, ...]] = [
    shape
    for length in range(1, 5)
    for shape in itertools.product([False, True], repeat=length)
]

# A representative sample of request-side policies: no restriction, a
# restriction naming every provider, one naming none, one naming only the
# paid providers, and one naming only the free providers — crossed with both
# values of `allow_paid_fallback`.
_REQUEST_PROVIDER_SELECTIONS: list[tuple[str, ...]] = [
    (),
    ("nonexistent",),
]


class TestApplyRequestPolicyIsCostMonotonic:
    """The property every configured chain and request policy must satisfy."""

    def test_effective_chain_is_a_bounded_subset_of_the_configured_chain(
        self,
    ) -> None:
        for shape in _CHAIN_SHAPES:
            chain = _chain(shape)
            chain_names = [provider.name for provider in chain]
            free_names = [provider.name for provider in chain if not provider.paid]
            paid_names = [provider.name for provider in chain if provider.paid]

            selections = [
                *_REQUEST_PROVIDER_SELECTIONS,
                tuple(paid_names),
                tuple(free_names),
                tuple(chain_names),
            ]
            for providers in selections:
                for allow_paid_fallback in (True, False):
                    request = SearchRequest(
                        query="q",
                        providers=list(providers),
                        allow_paid_fallback=allow_paid_fallback,
                    )

                    effective, ignored = apply_request_policy(chain, request)
                    effective_names = [provider.name for provider in effective]

                    assert ignored >= 0
                    # A subsequence of the configured chain: never reordered,
                    # never widened, never carrying a name the chain lacks.
                    assert _is_subsequence(effective_names, chain_names)
                    assert set(effective_names) <= set(chain_names)
                    # Every free provider survives, in its configured position.
                    assert [
                        name for name in effective_names if name in free_names
                    ] == free_names
                    if not allow_paid_fallback:
                        assert all(not provider.paid for provider in effective)

    def test_allow_paid_fallback_false_leaves_no_paid_provider(self) -> None:
        for shape in _CHAIN_SHAPES:
            chain = _chain(shape)
            request = SearchRequest(query="q", allow_paid_fallback=False)

            effective, _ = apply_request_policy(chain, request)

            assert all(not provider.paid for provider in effective)


class TestApplyRequestPolicyNormalizesAndCounts:
    """The normalise → match → filter algorithm, ruling 29."""

    def test_empty_providers_runs_the_configured_chain_unrestricted(self) -> None:
        chain = [
            FakeSearchProvider(name="searxng", paid=False),
            FakeSearchProvider(name="brave", paid=True),
        ]
        request = SearchRequest(query="q")

        effective, ignored = apply_request_policy(chain, request)

        assert [provider.name for provider in effective] == ["searxng", "brave"]
        assert ignored == 0

    def test_whitespace_and_case_normalise_with_no_ignored_count(self) -> None:
        chain = [FakeSearchProvider(name="searxng", paid=False)]
        request = SearchRequest(query="q", providers=[" SearXNG "])

        effective, ignored = apply_request_policy(chain, request)

        assert [provider.name for provider in effective] == ["searxng"]
        assert ignored == 0

    def test_a_ninth_entry_is_ignored_and_counted(self) -> None:
        """Eight matching entries cost nothing; the ninth is past the cap."""
        chain = [FakeSearchProvider(name="searxng", paid=False)]
        request = SearchRequest(query="q", providers=["searxng"] * 9)

        _, ignored = apply_request_policy(chain, request)

        assert ignored == 1

    def test_unknown_entries_are_each_ignored_and_counted_including_duplicates(
        self,
    ) -> None:
        chain = [FakeSearchProvider(name="searxng", paid=False)]
        request = SearchRequest(query="q", providers=["tavily", "exa", "tavily"])

        _, ignored = apply_request_policy(chain, request)

        assert ignored == 3

    def test_a_duplicate_of_a_matching_name_counts_nothing(self) -> None:
        chain = [FakeSearchProvider(name="searxng", paid=False)]
        request = SearchRequest(query="q", providers=["searxng", "searxng"])

        _, ignored = apply_request_policy(chain, request)

        assert ignored == 0

    def test_a_hostile_or_oversized_entry_is_ignored_and_counted(self) -> None:
        chain = [FakeSearchProvider(name="searxng", paid=False)]
        request = SearchRequest(
            query="q",
            providers=[
                "x" * 33,
                "has interior\twhitespace",
                "ignore-previous-instructions",
            ],
        )

        _, ignored = apply_request_policy(chain, request)

        assert ignored == 3

    def test_providers_naming_only_paid_never_removes_free_providers(self) -> None:
        chain = [
            FakeSearchProvider(name="searxng", paid=False),
            FakeSearchProvider(name="brave", paid=True),
        ]
        request = SearchRequest(query="q", providers=["brave"])

        effective, ignored = apply_request_policy(chain, request)

        assert [provider.name for provider in effective] == ["searxng", "brave"]
        assert ignored == 0

    def test_providers_naming_only_searxng_excludes_brave(self) -> None:
        chain = [
            FakeSearchProvider(name="searxng", paid=False),
            FakeSearchProvider(name="brave", paid=True),
        ]
        request = SearchRequest(query="q", providers=["searxng"])

        effective, ignored = apply_request_policy(chain, request)

        assert [provider.name for provider in effective] == ["searxng"]
        assert ignored == 0

    def test_a_paid_only_configured_chain_can_become_empty(self) -> None:
        chain = [FakeSearchProvider(name="brave", paid=True)]
        request = SearchRequest(query="q", allow_paid_fallback=False)

        effective, _ = apply_request_policy(chain, request)

        assert effective == []
