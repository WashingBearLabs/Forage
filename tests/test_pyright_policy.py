"""Guards for the repo's type-checking policy (`[tool.pyright]`).

`uv run pyright` exiting 0 is only meaningful alongside the configuration it
ran under: a `reportUnknownMemberType = "none"` here, or a handful of
type-ignore comments there, would keep the exit code green while emptying it
of content. US-006 burned the backlog to zero under a specific, narrow
policy, and these tests pin that policy so widening it has to be a deliberate,
reviewed act rather than the path of least resistance under a deadline.

The policy, in one line: **strict everywhere, one rule-level relaxation
(`reportPrivateUsage`, `tests/` only), and no inline suppressions at all.**

test_mapping:
  pyproject.toml: tests/test_pyright_policy.py
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"

# Keys allowed in the top-level [tool.pyright] table. Anything else is almost
# certainly a diagnostic-rule override, which is the thing this file exists to
# prevent: strict mode with carve-outs is not strict mode.
_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "pythonVersion",
        "typeCheckingMode",
        "enableTypeIgnoreComments",
        "stubPath",
        "executionEnvironments",
    }
)

# The single sanctioned relaxation, and the only execution environment allowed
# to carry one.
_RELAXED_RULE = "reportPrivateUsage"
_RELAXED_ROOT = "tests"

# Directories skipped when scanning for inline suppressions.
_SKIPPED_DIRS = frozenset(
    {".venv", ".git", "__pycache__", ".ruff_cache", ".pytest_cache"}
)


@pytest.fixture(scope="module")
def pyright_config() -> dict[str, Any]:
    with _PYPROJECT_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    tool: dict[str, Any] = data.get("tool", {})
    config: dict[str, Any] = tool.get("pyright", {})
    assert config, "pyproject.toml must carry a [tool.pyright] table"
    return config


@pytest.fixture(scope="module")
def execution_environments(pyright_config: dict[str, Any]) -> list[dict[str, Any]]:
    envs: list[dict[str, Any]] = list(pyright_config.get("executionEnvironments", []))
    return envs


def _python_sources() -> list[Path]:
    sources: list[Path] = []
    for path in _REPO_ROOT.rglob("*.py"):
        if _SKIPPED_DIRS & set(path.relative_to(_REPO_ROOT).parts):
            continue
        sources.append(path)
    for path in _REPO_ROOT.rglob("*.pyi"):
        if _SKIPPED_DIRS & set(path.relative_to(_REPO_ROOT).parts):
            continue
        sources.append(path)
    return sorted(sources)


class TestStrictMode:
    """Strict is on, everywhere, with no top-level rule overrides."""

    def test_type_checking_mode_is_strict(self, pyright_config: dict[str, Any]) -> None:
        assert pyright_config.get("typeCheckingMode") == "strict"

    def test_python_version_is_pinned(self, pyright_config: dict[str, Any]) -> None:
        assert pyright_config.get("pythonVersion") == "3.12", (
            "pyright must be told which Python it is checking; a drifting "
            "default silently changes what counts as an error"
        )

    def test_no_rule_overrides_at_top_level(
        self, pyright_config: dict[str, Any]
    ) -> None:
        unexpected = sorted(set(pyright_config) - _ALLOWED_TOP_LEVEL_KEYS)
        assert unexpected == [], (
            f"[tool.pyright] carries unexpected keys {unexpected}. A "
            "diagnostic-rule override here applies to the *service* code, "
            "which US-006 made strict with real fixes and no carve-outs. If "
            "one is genuinely needed, change this test and say why in the "
            "same commit."
        )


class TestInlineSuppressions:
    """Type-ignore comments are disabled, and no file relies on one."""

    def test_type_ignore_comments_are_disabled(
        self, pyright_config: dict[str, Any]
    ) -> None:
        assert pyright_config.get("enableTypeIgnoreComments") is False, (
            "pyright honours type-ignore comments by default, with no rule "
            "code required — one comment silences every diagnostic on its "
            "line. US-006's zero was reached with this switch off; turning it "
            "back on would make the exit code stop meaning anything."
        )

    def test_no_inline_suppression_comments_anywhere(self) -> None:
        # Built at runtime so this module's own prose does not match itself.
        needles = ("# type:" + " ignore", "# pyright:" + " ignore")
        offenders: list[str] = []
        for path in _python_sources():
            text = path.read_text(encoding="utf-8")
            if any(needle in text for needle in needles):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert offenders == [], (
            f"Inline type suppressions found in {offenders}. The policy is to "
            "fix the type, or — for a third-party gap — add a minimal stub "
            "under typings/ declaring only the symbols this repo calls. See "
            "typings/README.md."
        )


class TestTestsLaneRelaxation:
    """Exactly one rule-level relaxation, scoped to tests/, and no more."""

    def test_two_execution_environments_declared(
        self, execution_environments: list[dict[str, Any]]
    ) -> None:
        roots = [env.get("root") for env in execution_environments]
        assert roots == [_RELAXED_ROOT, "."], (
            "Expected exactly two execution environments — 'tests' (carrying "
            f"the relaxation) then '.' (everything else); got {roots!r}"
        )

    def test_tests_environment_relaxes_only_private_usage(
        self, execution_environments: list[dict[str, Any]]
    ) -> None:
        tests_env = next(
            env for env in execution_environments if env.get("root") == _RELAXED_ROOT
        )
        # `root` and `extraPaths` are plumbing, not policy; everything else in
        # the table is a diagnostic-rule override.
        overrides = {
            key: value
            for key, value in tests_env.items()
            if key not in {"root", "extraPaths"}
        }
        assert overrides == {_RELAXED_RULE: "none"}, (
            "tests/ may relax exactly one rule, reportPrivateUsage — unit "
            "tests legitimately reach into the private surface of the module "
            "under test. Every other strict rule stays on: an unknown type or "
            "a bad argument is as much a defect in a test as in the service. "
            f"Got {overrides!r}"
        )

    def test_service_environment_carries_no_overrides(
        self, execution_environments: list[dict[str, Any]]
    ) -> None:
        service_env = next(
            env for env in execution_environments if env.get("root") == "."
        )
        assert set(service_env) == {"root"}, (
            "The catch-all execution environment covers the service code and "
            f"must carry no rule overrides at all; got {sorted(service_env)}"
        )

    def test_relaxation_is_documented_in_place(self) -> None:
        text = _PYPROJECT_PATH.read_text(encoding="utf-8")
        assert "reportPrivateUsage" in text
        assert "US-006" in text, (
            "The policy comment above [tool.pyright] is part of the policy: a "
            "future reader has to be able to find out why the one relaxation "
            "exists without excavating git history"
        )


class TestStubPolicy:
    """Local stubs are opt-in, minimal, and never shadow a fully-typed library."""

    def test_stub_path_is_declared(self, pyright_config: dict[str, Any]) -> None:
        assert pyright_config.get("stubPath") == "typings"

    def test_typings_directory_documents_its_rule(self) -> None:
        readme = _REPO_ROOT / "typings" / "README.md"
        assert readme.exists(), (
            "typings/ needs a README: a stub package *shadows* the real one, "
            "so the rule that keeps it safe (declare only what is used) has "
            "to be written down next to the stubs"
        )

    def test_no_torch_stub(self) -> None:
        # torch ships complete types. A hand-written shadow of them would be
        # the largest possible drift hazard, and the round-2 spec review
        # called it out by name.
        assert not (_REPO_ROOT / "typings" / "torch").exists(), (
            "torch ships py.typed — consume its real types rather than "
            "shadowing them with a local stub"
        )
