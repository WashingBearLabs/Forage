"""Freeze the served OpenAPI document as a byte-stable, committed artifact.

``feature-forage-contract`` US-002. The app has always been able to *describe*
itself — ``GET /openapi.json`` renders on every request — but a document that
only exists inside a running container is not something a consumer can pin,
diff, or verify. This module writes it down:

* ``contract/openapi.yaml`` — the document, in a canonical form chosen so that
  the same code always produces the same bytes.
* ``contract/openapi.yaml.sha256`` — the **committed anchor**. Release assets
  are mutable and a registry tag can be re-pushed; a checksum that lives in the
  git history under a signed tag is not. Every other copy of the contract (the
  Release asset, the one baked into the image by US-004, the one Poppy vendors)
  is verified against this file, never against another copy.
* ``tests/fixtures/contract/unregenerated_openapi.yaml`` — the self-test twin;
  see "The twin" below.

Run it with::

    uv run python -m scripts.export_contract           # write all three
    uv run python -m scripts.export_contract --check   # verify, write nothing

``--check`` is what a hook or a shell would call; the test suite calls
:func:`drift_report` directly, so the drift gate runs on every ``uv run
pytest`` without anyone remembering to invoke this file.

**Determinism, and what each setting is defending against.** "Deterministic"
here means byte-identical across repeat calls *and across processes* — the
second half matters because a set iterated into a list renders in
``PYTHONHASHSEED`` order, and a document that changes shape between two CI runs
would make the drift check a coin flip. Four choices carry that:

* **JSON round-trip first.** ``json.loads(json.dumps(doc, sort_keys=True))``
  rebuilds the whole structure out of fresh objects. It asserts the document is
  JSON-representable (the wire form is JSON, so anything that is not is a bug
  worth failing on), and it breaks every shared object identity — which is what
  makes the next point unconditional rather than hopeful.
* **No anchors, ever.** PyYAML emits ``&id001``/``*id001`` when one non-scalar
  object is reachable from two places. That is legal YAML and unreadable
  OpenAPI, and whether it happens depends on object identity inside FastAPI
  rather than on the contract. :class:`_CanonicalDumper` refuses to alias at
  all, and the round-trip above independently guarantees there is nothing
  shared to alias. Measured at US-002: with **both** defences removed the
  document still renders anchor-free today, so this is precaution against a
  FastAPI or pydantic release that starts sharing a sub-object, not a fix for
  something currently observed. It is cheap, and the failure it prevents is the
  silent kind.
* **``sort_keys=True``, at every depth.** Key order in the served document
  follows the order routes and fields happen to be declared in. Sorting throws
  that away deliberately: a reordered ``responses=`` dict is not a contract
  change and must not read as one in a diff. Like the anchors it is produced
  twice — ``json.dumps(sort_keys=True)`` above and the dumper here — and either
  alone suffices; removing **both** moves bytes, and fails both the drift test
  and ``test_every_mapping_is_key_sorted_at_every_depth``.
* **``width=88``, ``indent=2``, ``allow_unicode=True``, block style.** The
  width matches the repo's line length (``pyproject.toml``) so the artifact
  reads like the rest of the tree, and ``allow_unicode`` keeps the em dashes in
  the field descriptions as em dashes instead of ``\\u2014`` escapes. Both are
  *rendering* choices with a known cost: editing one word of a long
  description reflows its whole block, and a PyYAML release that changes how it
  folds plain scalars will move bytes that no API change moved. That is the
  intended trade — a dependency bump shows up as a **reviewable regen diff**
  rather than as silence — and it is why these settings are pinned here in one
  place instead of left to library defaults.

**The twin.** US-002's acceptance criterion asks for the drift check's own
failure case to be *committed*, not demonstrated once by hand and thrown away.
So this module also writes a twin of the contract with exactly one property
removed — ``Extract422ErrorResponse.sanitizer_revision``, the field Poppy
hard-rejects a 422 without — which is precisely the file you would have on disk
if you had added that field to the model and forgotten to regenerate.
``tests/test_contract_export.py`` feeds it to the same :func:`drift_report` the
real check uses and asserts it is caught, and separately asserts the twin
differs from the live document in that one documented way and no other, so the
fixture cannot rot into "some other file" and keep passing. Because the twin is
generated alongside the contract, one command keeps all three artifacts in step.

test_mapping:
  scripts/export_contract.py: tests/test_contract_export.py
  contract/openapi.yaml: tests/test_contract_export.py
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import yaml

from retrieval_app import app

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"
CONTRACT_PATH = CONTRACT_DIR / "openapi.yaml"
ANCHOR_PATH = CONTRACT_DIR / "openapi.yaml.sha256"
SELF_TEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "contract" / "unregenerated_openapi.yaml"
)

REGEN_COMMAND = "uv run python -m scripts.export_contract"

# The property the twin drops, and the component it lives on. Named as
# constants because the test asserts the difference against these rather than
# against a hand-copied path.
TWIN_COMPONENT = "Extract422ErrorResponse"
TWIN_PROPERTY = "sanitizer_revision"

_YAML_WIDTH = 88
_YAML_INDENT = 2

_BANNER = (
    "# Generated by scripts/export_contract.py — do not edit by hand.\n"
    f"# Regenerate with: {REGEN_COMMAND}\n"
)

_TWIN_BANNER = (
    "# Generated by scripts/export_contract.py — do not edit by hand.\n"
    "#\n"
    "# The drift check's own failure case, kept committed rather than staged by\n"
    f"# hand: this is contract/openapi.yaml with {TWIN_COMPONENT}'s\n"
    f"# {TWIN_PROPERTY} property removed — an un-regenerated response-model\n"
    "# change. tests/test_contract_export.py asserts the checker catches it.\n"
    f"# Regenerate with: {REGEN_COMMAND}\n"
)


class ContractExportError(RuntimeError):
    """A precondition of the export does not hold."""


class _CanonicalDumper(yaml.SafeDumper):
    """A ``SafeDumper`` that never emits an anchor or an alias.

    See the module docstring: aliasing depends on object identity inside
    FastAPI, not on the contract, so it is refused outright.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def openapi_document() -> dict[str, Any]:
    """Return the served OpenAPI document, freshly built and JSON-normalized.

    FastAPI memoizes the document on ``app.openapi_schema``; the cache is
    cleared first so a caller that changed the app (a test toggling config,
    say) is never handed a stale answer.
    """
    app.openapi_schema = None
    served = app.openapi()
    return cast(dict[str, Any], json.loads(json.dumps(served, sort_keys=True)))


def render_contract(document: dict[str, Any] | None = None) -> str:
    """Render *document* (default: the served one) as the canonical YAML text."""
    body = yaml.dump(
        openapi_document() if document is None else document,
        Dumper=_CanonicalDumper,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=_YAML_WIDTH,
        indent=_YAML_INDENT,
    )
    return _BANNER + body


def render_anchor(contract_text: str) -> str:
    """Return the ``sha256sum``-format anchor for *contract_text*.

    The name in the anchor is the contract's **basename**, not its repo path,
    so ``sha256sum -c openapi.yaml.sha256`` verifies unchanged from the repo's
    ``contract/``, from a directory holding the downloaded Release assets, and
    from ``/app/contract/`` inside the image — the three-way check US-004 runs.
    """
    digest = hashlib.sha256(contract_text.encode("utf-8")).hexdigest()
    return f"{digest}  {CONTRACT_PATH.name}\n"


def unregenerated_twin(document: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return *document* with one property dropped — the self-test case.

    Raises if the property is not there to drop. A twin that silently came back
    identical to the contract would turn the self-test green while proving
    nothing, so a rename of the component or the field has to fail here, loudly,
    rather than quietly hollow out the check.
    """
    twin = json.loads(json.dumps(openapi_document() if document is None else document))
    twin = cast(dict[str, Any], twin)
    schemas = cast(dict[str, Any], twin["components"]["schemas"])
    if TWIN_COMPONENT not in schemas:
        raise ContractExportError(
            f"the self-test twin needs component {TWIN_COMPONENT!r}, which the "
            "contract no longer carries — pick another un-regenerated change "
            "and update scripts/export_contract.py's TWIN_* constants"
        )
    component = cast(dict[str, Any], schemas[TWIN_COMPONENT])
    properties = cast(dict[str, Any], component["properties"])
    if TWIN_PROPERTY not in properties:
        raise ContractExportError(
            f"the self-test twin needs {TWIN_COMPONENT}.{TWIN_PROPERTY}, which "
            "the contract no longer carries — pick another un-regenerated "
            "change and update scripts/export_contract.py's TWIN_* constants"
        )
    del properties[TWIN_PROPERTY]
    required = cast("list[str]", component["required"])
    component["required"] = [name for name in required if name != TWIN_PROPERTY]
    return twin


def render_twin(document: dict[str, Any] | None = None) -> str:
    """Render the self-test twin as canonical YAML, with its own banner."""
    rendered = render_contract(unregenerated_twin(document))
    return _TWIN_BANNER + rendered.removeprefix(_BANNER)


def drift_report(path: Path, rendered: str) -> str | None:
    """Return a regeneration message if *path* is not *rendered*, else ``None``.

    This is the drift gate. ``tests/test_contract_export.py`` calls it on the
    committed contract, which is why the check runs in CI's ``test`` job, under
    the orchestrator's regression gate, and on every local ``uv run pytest``
    without a separate lane to remember.
    """
    if not path.exists():
        return (
            f"{_relative(path)} is missing — generate it with:\n"
            f"    {REGEN_COMMAND}\n"
            "(scripts/export_contract.py is what generates it.)"
        )
    current = path.read_text(encoding="utf-8")
    if current == rendered:
        return None
    diff = list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{_relative(path)} (committed)",
            tofile=f"{_relative(path)} (regenerated)",
            n=1,
        )
    )
    excerpt = "".join(diff[:40])
    elided = "" if len(diff) <= 40 else f"\n... {len(diff) - 40} more diff lines\n"
    return (
        f"{_relative(path)} is out of date — the app generates something else.\n"
        f"Regenerate it with:\n    {REGEN_COMMAND}\n"
        "(scripts/export_contract.py is what generates it; commit every file it "
        "writes together.)\n"
        f"{excerpt}{elided}"
    )


def write_artifacts() -> list[Path]:
    """Write the contract, its anchor and the self-test twin. Returns the paths."""
    document = openapi_document()
    contract_text = render_contract(document)
    twin_text = render_twin(document)

    CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    SELF_TEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(contract_text, encoding="utf-8")
    ANCHOR_PATH.write_text(render_anchor(contract_text), encoding="utf-8")
    SELF_TEST_PATH.write_text(twin_text, encoding="utf-8")
    return [CONTRACT_PATH, ANCHOR_PATH, SELF_TEST_PATH]


def check_artifacts() -> list[str]:
    """Return one message per artifact that is missing or stale (empty = clean)."""
    document = openapi_document()
    contract_text = render_contract(document)
    reports = [
        drift_report(CONTRACT_PATH, contract_text),
        drift_report(ANCHOR_PATH, render_anchor(contract_text)),
        drift_report(SELF_TEST_PATH, render_twin(document)),
    ]
    return [report for report in reports if report is not None]


def _relative(path: Path) -> str:
    """Return *path* relative to the repo root when it is inside it."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.export_contract",
        description=(
            "Export the served OpenAPI document to contract/openapi.yaml, with "
            "its committed sha256 anchor and the drift check's self-test twin."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed artifacts are current; write nothing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    args = build_parser().parse_args(argv)
    if args.check:
        problems = check_artifacts()
        for problem in problems:
            print(problem)
        if problems:
            return 1
        print("export_contract OK — committed artifacts are current")
        return 0
    for path in write_artifacts():
        print(f"wrote {_relative(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
