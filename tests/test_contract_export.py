"""The frozen contract artifact, and the drift check that keeps it true.

``feature-forage-contract`` US-002 writes the served OpenAPI document down as
``contract/openapi.yaml`` plus a committed sha256 anchor, and makes the file's
staleness a **test failure** rather than a release-day surprise. This module is
that gate, and it is deliberately made of four separable claims:

* **Currency.** The committed contract is byte-for-byte what the app generates
  today, and the committed anchor is the sha256 of the committed bytes. A
  response model edited without a regen fails here, in the same PR.
* **The checker works.** A drift gate that cannot fail is worse than none, so
  the failure case is committed too:
  ``tests/fixtures/contract/unregenerated_openapi.yaml`` is the contract with
  ``Extract422ErrorResponse.sanitizer_revision`` removed — an un-regenerated
  response-model change — and it is fed to the *same* checker. A second test
  pins that the twin differs from the live document in exactly that one way, so
  the fixture cannot rot into "some unrelated file" and go on passing for the
  wrong reason.
* **Determinism.** Byte-stable across repeat calls and, because a set rendered
  into a list would not be, across processes with different ``PYTHONHASHSEED``.
  Measured by rendering in subprocesses, not asserted.
* **Completeness.** ``/extract`` is in the document even though
  ``extract_route_enabled`` is ``false`` by default — the route is registered
  regardless and only the handler gates — so the contract describes the service,
  not one deployment's configuration. That is verified against the app rather
  than assumed.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from pipeline.contract import CONTRACT_VERSION
from retrieval_app import app
from scripts.export_contract import (
    ANCHOR_PATH,
    CONTRACT_PATH,
    REGEN_COMMAND,
    REPO_ROOT,
    SELF_TEST_PATH,
    TWIN_COMPONENT,
    TWIN_PROPERTY,
    ContractExportError,
    drift_report,
    openapi_document,
    render_anchor,
    render_contract,
    render_twin,
    unregenerated_twin,
)

_SERVED_PATHS = {"/health", "/metrics", "/retrieve", "/search", "/extract"}

_MISSING = object()


@pytest.fixture
def restore_app_config() -> Iterator[None]:
    """Let a test set ``app.state.config`` without leaking it to the next one."""
    had_config = hasattr(app.state, "config")
    previous = app.state.config if had_config else None
    try:
        yield
    finally:
        if had_config:
            app.state.config = previous
        elif hasattr(app.state, "config"):
            del app.state.config
        app.openapi_schema = None


def _difference_paths(left: object, right: object, prefix: str = "") -> list[str]:
    """Return the dotted paths at which two documents disagree.

    Lists are compared whole — a ``required`` array that lost a member is one
    difference, not one per index.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        left_map = cast(dict[str, Any], left)
        right_map = cast(dict[str, Any], right)
        differences: list[str] = []
        for key in sorted(set(left_map) | set(right_map)):
            child = f"{prefix}.{key}" if prefix else key
            differences.extend(
                _difference_paths(
                    left_map.get(key, _MISSING), right_map.get(key, _MISSING), child
                )
            )
        return differences
    return [] if left == right else [prefix]


def _render_in_subprocess(hash_seed: str) -> bytes:
    """Render the contract in a fresh interpreter with ``PYTHONHASHSEED`` set."""
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = hash_seed
    # The suite's socket guard does not reach a child process; this keeps the
    # child from making the one hub call kit_tools/docs/GOTCHAS.md describes.
    environment["HF_HUB_OFFLINE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from scripts.export_contract import render_contract\n"
            "sys.stdout.buffer.write(render_contract().encode('utf-8'))\n",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return completed.stdout


class TestTheCommittedArtifacts:
    """What is on disk must be what the app says, or the suite is red."""

    def test_the_committed_contract_is_what_the_app_generates(self) -> None:
        # Asserted with the report as the message, not just as a repr: this is
        # the failure a contributor meets, and it is only useful unelided.
        report = drift_report(CONTRACT_PATH, render_contract())
        assert report is None, report

    def test_the_committed_anchor_is_the_sha256_of_the_committed_contract(
        self,
    ) -> None:
        """The anchor is the trust root, so it is checked against the file itself.

        Not against a freshly rendered string — a rendered-vs-rendered check
        would pass on a tree where both files were stale together.
        """
        contract_bytes = CONTRACT_PATH.read_bytes()
        digest = hashlib.sha256(contract_bytes).hexdigest()
        assert ANCHOR_PATH.read_text(encoding="utf-8") == f"{digest}  openapi.yaml\n"

    def test_the_anchor_is_sha256sum_verifiable_as_committed(self) -> None:
        """``sha256sum -c openapi.yaml.sha256`` must work from ``contract/``.

        Hence the bare basename: the same file verifies the Release asset and
        the copy US-004 bakes into the image, where the path differs and the
        bytes do not.
        """
        anchor = ANCHOR_PATH.read_text(encoding="utf-8")
        assert re.fullmatch(r"[0-9a-f]{64}  openapi\.yaml\n", anchor), anchor
        assert render_anchor(CONTRACT_PATH.read_text(encoding="utf-8")) == anchor

    def test_the_committed_contract_carries_the_live_contract_version(self) -> None:
        """The file, ``CONTRACT_VERSION`` and the served ``info.version`` agree.

        US-005 tied the *served* document to ``pipeline/contract.py``; this is
        the third copy, and the one a consumer reads offline.
        """
        document = cast(
            dict[str, Any], yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        )
        assert document["info"]["version"] == CONTRACT_VERSION

    def test_the_committed_contract_documents_every_served_route(self) -> None:
        document = cast(
            dict[str, Any], yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        )
        assert set(cast(dict[str, Any], document["paths"])) == _SERVED_PATHS

    def test_the_self_test_twin_is_current(self) -> None:
        """The committed failure case is regenerated alongside the contract."""
        report = drift_report(SELF_TEST_PATH, render_twin())
        assert report is None, report


class TestTheDriftCheckCatchesDrift:
    """A gate nobody has seen fail is not yet a gate."""

    def test_an_unregenerated_response_model_change_is_caught(self) -> None:
        """The committed twin, fed to the real checker, must be refused."""
        report = drift_report(SELF_TEST_PATH, render_contract())
        assert report is not None
        assert "scripts/export_contract.py" in report
        assert REGEN_COMMAND in report
        assert TWIN_PROPERTY in report

    def test_the_twin_differs_from_the_contract_in_one_documented_way(self) -> None:
        """Exactly the dropped property and the ``required`` list it left.

        Computed structurally, not by re-running the mutation, so a twin that
        drifted into being a different document altogether fails here instead
        of passing the test above for the wrong reason.
        """
        live = openapi_document()
        twin = cast(
            dict[str, Any], yaml.safe_load(SELF_TEST_PATH.read_text(encoding="utf-8"))
        )
        component = f"components.schemas.{TWIN_COMPONENT}"
        assert _difference_paths(live, twin) == [
            f"{component}.properties.{TWIN_PROPERTY}",
            f"{component}.required",
        ]

    def test_a_missing_contract_is_caught_and_says_how_to_make_one(
        self, tmp_path: Path
    ) -> None:
        report = drift_report(tmp_path / "openapi.yaml", render_contract())
        assert report is not None
        assert "scripts/export_contract.py" in report
        assert REGEN_COMMAND in report

    def test_the_twin_generator_refuses_when_its_target_is_gone(self) -> None:
        """A hollow self-test is the failure mode worth being loud about.

        If the component or the property is renamed, the twin would come back
        identical to the contract and every assertion above would pass while
        checking nothing.
        """
        document = openapi_document()
        schemas = cast(dict[str, Any], document["components"]["schemas"])
        component = cast(dict[str, Any], schemas[TWIN_COMPONENT])
        del cast(dict[str, Any], component["properties"])[TWIN_PROPERTY]
        with pytest.raises(ContractExportError, match=TWIN_PROPERTY):
            unregenerated_twin(document)

        del schemas[TWIN_COMPONENT]
        with pytest.raises(ContractExportError, match=TWIN_COMPONENT):
            unregenerated_twin(document)


class TestTheRenderIsDeterministic:
    """Same code in, same bytes out — including in a different process."""

    def test_repeat_renders_are_byte_identical(self) -> None:
        assert render_contract() == render_contract()

    def test_the_render_survives_a_different_hash_seed(self) -> None:
        """The one that would catch a set iterated into a list.

        Two interpreters, two seeds, and the in-process render as a third
        witness. ``PYTHONHASHSEED`` can only be set before start-up, so this
        costs two subprocesses and is worth them.
        """
        first = _render_in_subprocess("0")
        second = _render_in_subprocess("1")
        assert first == second
        assert first == render_contract().encode("utf-8")

    def test_the_canonical_form_round_trips_to_the_document(self) -> None:
        document = openapi_document()
        rendered = render_contract(document)
        assert yaml.safe_load(rendered) == document

    def test_the_render_carries_no_anchors_or_aliases(self) -> None:
        """PyYAML's identity-driven ``&id001``/``*id001`` must never appear.

        They are legal YAML and unreadable OpenAPI, and whether they appear
        depends on object sharing inside FastAPI rather than on the contract.
        """
        rendered = render_contract()
        assert not re.search(r"(?m):\s+[&*]id\d+", rendered)
        assert "&id" not in rendered and "*id" not in rendered

    def test_every_mapping_is_key_sorted_at_every_depth(self) -> None:
        """Read from the *render*, deliberately, not from the committed file.

        Sorting is what stops a reordered ``responses=`` dict from reading as a
        contract change, and it is produced twice over — by the JSON
        normalization and again by the dumper. Asserting it on the committed
        bytes would only re-check a file the drift test already covers; against
        the render it pins the renderer, so losing both mechanisms fails here
        as well as there.
        """
        document = cast(dict[str, Any], yaml.safe_load(render_contract()))
        assert list(_unsorted_mappings(document)) == []

    def test_the_contract_ends_with_exactly_one_newline(self) -> None:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        assert text.endswith("\n") and not text.endswith("\n\n")


class TestTheDocumentIsConfigurationIndependent:
    """The contract describes the service, not one deployment's config."""

    def test_extract_is_documented_while_the_route_is_disabled(
        self, restore_app_config: None
    ) -> None:
        """``extract_route_enabled: false`` is the shipped default.

        The route is registered at import; only the handler gates, returning
        404. Verified here rather than assumed, because a contract that
        appeared and disappeared with a config file would be worthless to pin.
        """
        app.state.config = {"extract_route_enabled": False}
        document = openapi_document()
        assert "/extract" in cast(dict[str, Any], document["paths"])
        assert set(cast(dict[str, Any], document["paths"])) == _SERVED_PATHS

    def test_toggling_the_extract_gate_moves_no_byte_of_the_document(
        self, restore_app_config: None
    ) -> None:
        app.state.config = {"extract_route_enabled": False}
        disabled = render_contract()
        app.state.config = {"extract_route_enabled": True}
        enabled = render_contract()
        assert disabled == enabled


def _unsorted_mappings(node: object, prefix: str = "") -> Iterator[str]:
    """Yield the path of every mapping whose keys are not in sorted order."""
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        keys = list(mapping)
        if keys != sorted(keys):
            yield prefix or "<root>"
        for key, value in mapping.items():
            yield from _unsorted_mappings(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for index, value in enumerate(cast("list[Any]", node)):
            yield from _unsorted_mappings(value, f"{prefix}[{index}]")
