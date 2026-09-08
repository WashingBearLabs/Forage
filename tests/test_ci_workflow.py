"""Structural guards for ``.github/workflows/ci.yml``.

A trimmed port of Poppy's ``tests/deployment/test_ci_workflow.py``, kept
because ``actionlint`` and these tests answer two different questions:
actionlint checks that the workflow is *valid*, and nothing in it checks that
the workflow is *safe*. Everything asserted below is a supply-chain property
this repository needs to still hold on the day it goes public (US-008):

* every third-party action is pinned to a full 40-hex commit SHA, and carries a
  ``# vX.Y.Z`` comment so a human can read what that SHA is;
* the top-level ``GITHUB_TOKEN`` is read-only — a publish job has to raise its
  own job-scoped permissions rather than inherit write access;
* no checkout persists credentials into the runner's git config;
* ``pull_request_target`` never appears. On GitHub-hosted runners a fork PR
  runs these gates as an ordinary ``pull_request`` event with a read-only token
  and no repository secrets; ``pull_request_target`` would instead run the base
  ref's workflow with a writable token and secrets exposed;
* no repository secret other than ``GITHUB_TOKEN`` is referenced anywhere;
* the trigger set still carries *both* tag patterns — ``v*`` does not match
  ``searxng-v*``, so dropping the second one silently strands the companion
  image's publish lane (US-004);
* no ``needs:`` names a job that does not exist. Jobs land across several
  stories and an undefined ``needs:`` makes GitHub reject the entire file, not
  just the offending job.

Since US-003 it also guards the image handoff. ``needs:`` is an ordering edge,
not a shared Docker daemon: every job gets a fresh runner with an empty image
store, so a downstream job that simply names ``build-amd64`` and then runs
``docker history`` would be inspecting nothing, and one that rebuilds would be
inspecting a *different* image than the one that was built. The workflow
therefore saves the image to an artifact and asserts the loaded image ID
against the recorded one, and :class:`TestImageArtifactHandoff` checks that the
two halves of that contract still refer to the same artifact.

Assertions run against the parsed YAML wherever possible, so reorganising the
file cannot silently void a check.

Since US-002 this module also guards the *hermeticity canary* the ``test`` lane
depends on (:class:`TestHermeticityCanaryIsEnforced`). That check lives here,
in a different module, on purpose: a canary can assert anything it likes about
the socket guard, but it cannot notice its own module being skipped.

test_mapping:
  .github/workflows/ci.yml: tests/test_ci_workflow.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW_PATH = WORKFLOW_DIR / "ci.yml"

# PyYAML (YAML 1.1) parses the bare `on:` key as the boolean True, so the
# trigger block is reached as workflow[True], never workflow["on"].
_ON_KEY = True

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PINNED_USES_RE = re.compile(r"uses:\s*(\S+)@([0-9a-f]{40})\s*#\s*v\d+\.\d+\.\d+")
_SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")

# GITHUB_TOKEN is minted per run and is read-only for fork PRs by GitHub's own
# rules; any *other* secret reference is a repository secret and needs a
# deliberate decision, not a silent addition.
_ALLOWED_SECRETS = frozenset({"GITHUB_TOKEN"})


def _workflow_files() -> list[Path]:
    return sorted(
        p
        for p in WORKFLOW_DIR.iterdir()
        if p.is_file() and p.suffix in {".yml", ".yaml"}
    )


@pytest.fixture(scope="module")
def workflow() -> Any:
    """Parse ci.yml. Note the `on:` key parses as boolean True (YAML 1.1)."""
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text())


@pytest.fixture(scope="module")
def jobs(workflow: Any) -> dict[str, Any]:
    return workflow["jobs"]


@pytest.fixture(scope="module")
def raw() -> str:
    return CI_WORKFLOW_PATH.read_text()


def _steps(jobs: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    return list(jobs[job_name].get("steps", []))


def _run_text(jobs: dict[str, Any], job_name: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(jobs, job_name))


def _all_steps(jobs: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for job in jobs.values():
        collected.extend(job.get("steps", []))
    return collected


def _run_lines(jobs: dict[str, Any], job_name: str) -> list[str]:
    """Every non-empty `run:` line of a job, stripped."""
    return [
        line.strip() for line in _run_text(jobs, job_name).splitlines() if line.strip()
    ]


_ENV_EXPR_RE = re.compile(r"^\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")


def _resolve_env(workflow: Any, value: str) -> str:
    """Resolve a bare `${{ env.NAME }}` against the workflow-level env block.

    The image tag, artifact name and file names are single-sourced in `env:` so
    the producing and consuming jobs cannot drift apart. Resolving them here
    means these tests compare the *values*, not two copies of the same
    expression string.
    """
    match = _ENV_EXPR_RE.match(value.strip())
    if match is None:
        return value
    env_block: dict[str, Any] = workflow.get("env") or {}
    return str(env_block.get(match.group(1), value))


def _comment_prose(raw_text: str) -> str:
    """Every whole-line comment in the file, flowed into one lowercase string.

    The workflow's comments wrap at ~79 columns, so a sentence a human reads as
    one runs across several lines separated by `\\n        # `. Flowing them
    lets a test assert that something is *documented* without also asserting
    where the author happened to wrap it.
    """
    return " ".join(
        line.strip().lstrip("#").strip()
        for line in raw_text.splitlines()
        if line.strip().startswith("#")
    ).lower()


def _step_using(jobs: dict[str, Any], job_name: str, action: str) -> dict[str, Any]:
    """The first step of a job whose `uses:` names the given action."""
    for step in _steps(jobs, job_name):
        if action in str(step.get("uses", "")):
            return step
    raise AssertionError(f"Job {job_name!r} has no step using {action!r}")


def _if_block_body(script: str, *tokens: str) -> str | None:
    """The body of the first `if` whose condition mentions every token.

    Asserting that a *script* contains `exit 1` says nothing about which
    branch exits — a job can keep an unrelated `exit 1` while the check that
    matters degrades to a warning. This finds the specific conditional and
    hands back only what runs inside it. It assumes the block is not nested,
    which is true here and fails safe (a missing body reads as a failure, not
    as a pass).
    """
    lines = script.splitlines()
    for index, line in enumerate(lines):
        condition = line.strip()
        if not condition.startswith("if "):
            continue
        if not all(token in condition for token in tokens):
            continue
        body: list[str] = []
        for follow in lines[index + 1 :]:
            if follow.strip() in {"fi", "fi;"}:
                break
            body.append(follow)
        return "\n".join(body)
    return None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class TestTriggers:
    """The workflow fires on the right events, and on both tag patterns."""

    def test_workflow_file_exists(self) -> None:
        assert CI_WORKFLOW_PATH.exists(), (
            "Expected a single workflow at .github/workflows/ci.yml — "
            "`needs:` cannot span workflow files, so the gate chain lives in one"
        )

    def test_pull_request_trigger_exists(self, workflow: Any) -> None:
        assert "pull_request" in workflow[_ON_KEY]

    @pytest.mark.parametrize("event_type", ("opened", "synchronize", "reopened"))
    def test_pull_request_types(self, workflow: Any, event_type: str) -> None:
        types = workflow[_ON_KEY]["pull_request"]["types"]
        assert event_type in types, (
            f"pull_request must fire on {event_type!r}; got {types!r}"
        )

    def test_no_pull_request_paths_filter(self, workflow: Any) -> None:
        pr_config: dict[str, Any] = workflow[_ON_KEY].get("pull_request") or {}
        assert "paths" not in pr_config, (
            "on.pull_request.paths is forbidden — a workflow-level paths filter "
            "silently skips every gate for a non-matching PR, and a required "
            "status check that never reports blocks the PR forever"
        )

    def test_push_to_main(self, workflow: Any) -> None:
        assert "main" in workflow[_ON_KEY]["push"]["branches"]

    def test_push_tags_include_service_pattern(self, workflow: Any) -> None:
        assert "v*" in workflow[_ON_KEY]["push"]["tags"]

    def test_push_tags_include_searxng_pattern(self, workflow: Any) -> None:
        tags = workflow[_ON_KEY]["push"]["tags"]
        assert "searxng-v*" in tags, (
            "The `v*` glob does NOT match `searxng-v*`. Without its own pattern "
            "the companion image (US-004) has no reachable publish lane at all; "
            f"got {tags!r}"
        )


# ---------------------------------------------------------------------------
# Fork posture
# ---------------------------------------------------------------------------


class TestForkPosture:
    """Fork PRs run the gates; they never get a writable token or secrets."""

    def test_pull_request_target_absent_from_every_workflow(self) -> None:
        # Scan the *parsed* document, re-serialised: comments are dropped by the
        # parser, so the header comment explaining this ban does not trip it,
        # while a real `pull_request_target:` trigger — or the string buried in
        # a job condition — does.
        offenders = [
            path.name
            for path in _workflow_files()
            if "pull_request_target" in yaml.safe_dump(yaml.safe_load(path.read_text()))
        ]
        assert offenders == [], (
            f"pull_request_target found in {offenders}. It runs the base ref's "
            "workflow with a writable token and repository secrets available to "
            "fork-authored code. Fork PRs must use the plain `pull_request` "
            "event, which GitHub already restricts to a read-only token."
        )

    def test_no_repository_secrets_referenced(self, raw: str) -> None:
        referenced = set(_SECRET_REF_RE.findall(raw))
        unexpected = sorted(referenced - _ALLOWED_SECRETS)
        assert unexpected == [], (
            f"ci.yml references repository secrets {unexpected}. Only "
            "GITHUB_TOKEN is allowed: it is minted per run and is read-only on "
            "fork PRs. Any other secret must not be reachable from a "
            "fork-triggered run."
        )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    """Least privilege at the top level; write access is job-scoped only."""

    def test_top_level_permissions_key_exists(self, workflow: Any) -> None:
        assert "permissions" in workflow, (
            "ci.yml must declare top-level permissions — the repository default "
            "would otherwise decide how much power GITHUB_TOKEN has"
        )

    def test_contents_read(self, workflow: Any) -> None:
        assert workflow["permissions"]["contents"] == "read"

    def test_no_top_level_write_permissions(self, workflow: Any) -> None:
        perms: dict[str, Any] = workflow["permissions"]
        writable = sorted(k for k, v in perms.items() if v == "write")
        assert writable == [], (
            f"Top-level write permissions on {writable}. The publish job (US-007) "
            "declares `packages: write` / `contents: write` on itself; nothing "
            "else may inherit them."
        )


# ---------------------------------------------------------------------------
# Action pinning
# ---------------------------------------------------------------------------


class TestActionPinning:
    """Third-party actions are pinned to immutable SHAs, readably."""

    def _uses_refs(self, jobs: dict[str, Any]) -> list[str]:
        return [str(step["uses"]) for step in _all_steps(jobs) if step.get("uses")]

    def test_at_least_one_uses_ref(self, jobs: dict[str, Any]) -> None:
        assert self._uses_refs(jobs), "Expected at least one `uses:` step"

    def test_all_uses_refs_are_sha_pinned(self, jobs: dict[str, Any]) -> None:
        for ref in self._uses_refs(jobs):
            sha_part = ref.split("@", 1)[-1] if "@" in ref else ""
            assert _SHA_RE.match(sha_part), (
                f"uses: {ref!r} is not pinned to a full 40-hex commit SHA. A "
                "movable tag lets an upstream compromise reach this repository "
                "on the next run."
            )

    def test_every_pinned_uses_carries_a_version_comment(
        self, jobs: dict[str, Any], raw: str
    ) -> None:
        annotated = {match.group(1) for match in _PINNED_USES_RE.finditer(raw)}
        for ref in self._uses_refs(jobs):
            action = ref.split("@", 1)[0]
            assert action in annotated, (
                f"uses: {ref!r} has no `# vX.Y.Z` comment. The SHA is the "
                "security control; the comment is what makes it maintainable."
            )

    def test_checkout_action_is_used(self, jobs: dict[str, Any]) -> None:
        assert any("checkout" in ref for ref in self._uses_refs(jobs))


class TestCheckout:
    """Checkout must not leave a usable token in the runner's git config."""

    def test_every_checkout_sets_persist_credentials_false(
        self, jobs: dict[str, Any]
    ) -> None:
        checkouts = [
            step for step in _all_steps(jobs) if "checkout" in str(step.get("uses", ""))
        ]
        assert checkouts, "Expected at least one actions/checkout step"
        for step in checkouts:
            with_block: dict[str, Any] = step.get("with") or {}
            assert with_block.get("persist-credentials") is False, (
                "Every checkout must set `persist-credentials: false` — "
                "otherwise the token stays in .git/config for every later step, "
                "including anything a PR author can influence."
            )


# ---------------------------------------------------------------------------
# The lint job
# ---------------------------------------------------------------------------


class TestLintJob:
    """The gate this story adds: ruff check, ruff format --check, actionlint."""

    def test_lint_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "lint" in jobs, "Expected a 'lint' job in ci.yml"

    def test_lint_runs_on_github_hosted_ubuntu(self, jobs: dict[str, Any]) -> None:
        runs_on = jobs["lint"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            "Forage has no GPU or database needs and must not depend on Poppy's "
            f"self-hosted runner; got {runs_on!r}"
        )

    def test_lint_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        timeout = jobs["lint"].get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"lint needs a timeout-minutes backstop; got {timeout!r}"
        )

    def test_lint_runs_ruff_check(self, jobs: dict[str, Any]) -> None:
        assert "uv run ruff check ." in _run_text(jobs, "lint")

    def test_lint_runs_ruff_format_check(self, jobs: dict[str, Any]) -> None:
        assert "uv run ruff format --check ." in _run_text(jobs, "lint"), (
            "`ruff format --check` is the gate this story adds — the 6-file "
            "backlog was burned to zero so it could become blocking"
        )

    def test_lint_syncs_against_the_committed_lock(self, jobs: dict[str, Any]) -> None:
        assert "--locked" in _run_text(jobs, "lint"), (
            "uv sync must run with --locked so a dependency edit that skipped "
            "re-locking fails CI instead of resolving something else"
        )

    def test_lint_asserts_lock_is_cpu_only(self, jobs: dict[str, Any]) -> None:
        run_text = _run_text(jobs, "lint")
        assert "nvidia-" in run_text and "uv.lock" in run_text, (
            "lint must grep the committed uv.lock for nvidia-* CUDA wheels — "
            "a warm uv cache makes job-log inspection alone vacuous"
        )

    def test_uv_setup_enables_caching(self, jobs: dict[str, Any]) -> None:
        setup = next(
            (
                step
                for step in _steps(jobs, "lint")
                if "setup-uv" in str(step.get("uses", ""))
            ),
            None,
        )
        assert setup is not None, "lint must install uv via astral-sh/setup-uv"
        with_block: dict[str, Any] = setup.get("with") or {}
        assert with_block.get("enable-cache") is True, (
            "setup-uv must enable caching — this repo is on the free Actions "
            "tier and a cold torch download every run is not affordable"
        )

    def test_lint_runs_actionlint(self, jobs: dict[str, Any]) -> None:
        assert "actionlint" in _run_text(jobs, "lint"), (
            "actionlint must run in CI so workflow validity is machine-checked "
            "on every PR rather than verified once by hand"
        )

    def test_actionlint_is_pinned_and_checksum_verified(
        self, workflow: Any, raw: str
    ) -> None:
        env: dict[str, Any] = workflow.get("env") or {}
        version = str(env.get("ACTIONLINT_VERSION", ""))
        digest = str(env.get("ACTIONLINT_SHA256", ""))
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
            f"ACTIONLINT_VERSION must be an exact version; got {version!r}"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", digest), (
            f"ACTIONLINT_SHA256 must be a sha256 digest; got {digest!r}"
        )
        assert "sha256sum -c" in raw, (
            "The pinned actionlint download must be checksum-verified — a "
            "version pin over an unverified download is not a pin"
        )


# ---------------------------------------------------------------------------
# The typecheck job
# ---------------------------------------------------------------------------


class TestTypecheckJob:
    """The gate US-006 adds: pyright strict, against the committed lock."""

    def test_typecheck_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "typecheck" in jobs, "Expected a 'typecheck' job in ci.yml"

    def test_typecheck_runs_on_github_hosted_ubuntu(self, jobs: dict[str, Any]) -> None:
        runs_on = jobs["typecheck"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            f"typecheck must run on a GitHub-hosted runner; got {runs_on!r}"
        )

    def test_typecheck_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        timeout = jobs["typecheck"].get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"typecheck needs a timeout-minutes backstop; got {timeout!r}"
        )

    def test_typecheck_runs_pyright(self, jobs: dict[str, Any]) -> None:
        assert "uv run pyright" in _run_text(jobs, "typecheck"), (
            "typecheck must run pyright — the whole point of the job"
        )

    def test_typecheck_does_not_narrow_pyright_scope(
        self, jobs: dict[str, Any]
    ) -> None:
        # `uv run pyright <path>` would check only that path and let the rest
        # of the repo rot. Pyright's own config decides the scope.
        for line in _run_text(jobs, "typecheck").splitlines():
            stripped = line.strip()
            if stripped.startswith("uv run pyright"):
                assert stripped == "uv run pyright", (
                    "pyright must run over the whole project; arguments would "
                    f"narrow it to a subset. Got: {stripped!r}"
                )

    def test_typecheck_syncs_against_the_committed_lock(
        self, jobs: dict[str, Any]
    ) -> None:
        assert "--locked" in _run_text(jobs, "typecheck"), (
            "pyright's answers depend on the exact dependency versions it "
            "sees, so the sync must come from the committed lock"
        )

    def test_typecheck_uv_setup_enables_caching(self, jobs: dict[str, Any]) -> None:
        setup = next(
            (
                step
                for step in _steps(jobs, "typecheck")
                if "setup-uv" in str(step.get("uses", ""))
            ),
            None,
        )
        assert setup is not None, "typecheck must install uv via astral-sh/setup-uv"
        with_block: dict[str, Any] = setup.get("with") or {}
        assert with_block.get("enable-cache") is True, (
            "setup-uv must enable caching in every job on the free tier"
        )


# ---------------------------------------------------------------------------
# The test job
# ---------------------------------------------------------------------------

# Flags that would turn "the suite ran" into "some of the suite ran". A green
# `test` job is the evidence every other guard in this repo rests on, so the
# one thing it must never do is quietly check a subset.
_SUITE_NARROWING_FLAGS = frozenset(
    {
        "-k",
        "-m",
        "-x",
        "--exitfirst",
        "--maxfail",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--lf",
        "--last-failed",
        "--sw",
        "--stepwise",
    }
)

_SANITIZER_STEP_RUN = "uv run pytest -q tests/test_sanitizer_revision.py"
_FULL_SUITE_RUN = "uv run pytest -q"


class TestTestJob:
    """The gate US-002 adds: the whole suite, on GitHub-hosted runners.

    This is the job that makes every other guard in the repo real. Before it
    existed, `test_ci_workflow.py`, `test_pyright_policy.py`,
    `test_dependency_lock.py` and the hermeticity canary all bit only on a
    developer's machine — nothing in CI ran pytest at all.
    """

    def test_test_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "test" in jobs, (
            "Expected a 'test' job in ci.yml — without it the suite, and every "
            "guard inside it, is enforced only at review"
        )

    def test_test_job_runs_on_github_hosted_ubuntu(self, jobs: dict[str, Any]) -> None:
        runs_on = jobs["test"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            "The suite is hermetic and CPU-only; it must not depend on Poppy's "
            f"self-hosted runner. Got {runs_on!r}"
        )

    def test_test_job_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        timeout = jobs["test"].get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"test needs a timeout-minutes backstop; got {timeout!r}"
        )

    def test_test_job_runs_the_whole_suite(self, jobs: dict[str, Any]) -> None:
        assert _FULL_SUITE_RUN in _run_lines(jobs, "test"), (
            f"test must run {_FULL_SUITE_RUN!r} as a bare command. A path "
            "argument would silently reduce the gate to whatever subset the "
            "author last cared about."
        )

    def test_test_job_applies_no_selection_filters(self, jobs: dict[str, Any]) -> None:
        for line in _run_lines(jobs, "test"):
            if not line.startswith("uv run pytest"):
                continue
            flags = {token.split("=", 1)[0] for token in line.split()}
            offenders = sorted(flags & _SUITE_NARROWING_FLAGS)
            assert offenders == [], (
                f"pytest invocation {line!r} carries selection/short-circuit "
                f"flags {offenders}. A filtered run reports green for tests it "
                "never executed."
            )

    def test_sanitizer_revision_step_exists_and_is_named(
        self, jobs: dict[str, Any]
    ) -> None:
        step = next(
            (
                candidate
                for candidate in _steps(jobs, "test")
                if str(candidate.get("run", "")).strip() == _SANITIZER_STEP_RUN
            ),
            None,
        )
        assert step is not None, (
            f"Expected a step running {_SANITIZER_STEP_RUN!r} (mirrors Poppy's "
            "ci.yml). The full suite covers it too; the point of the separate "
            "step is a distinct red line for the file-recall cache contract."
        )
        name = str(step.get("name", ""))
        assert "sanitizer revision" in name.lower(), (
            "The sanitizer-revision step must be *named* — an unnamed step "
            f"renders as its shell command and defeats the purpose. Got {name!r}"
        )

    def test_sanitizer_revision_step_runs_before_the_full_suite(
        self, jobs: dict[str, Any]
    ) -> None:
        runs = [str(step.get("run", "")).strip() for step in _steps(jobs, "test")]
        assert _SANITIZER_STEP_RUN in runs and _FULL_SUITE_RUN in runs
        assert runs.index(_SANITIZER_STEP_RUN) < runs.index(_FULL_SUITE_RUN), (
            "The targeted sanitizer-revision step must run before the full "
            "suite; after it, an unrelated failure anywhere in 607 tests would "
            "stop the contract guard from reporting at all"
        )

    def test_test_job_syncs_against_the_committed_lock(
        self, jobs: dict[str, Any]
    ) -> None:
        assert "--locked" in _run_text(jobs, "test"), (
            "The suite must run against the versions the lock pins, or a green "
            "run says nothing about what a consumer installs"
        )

    def test_test_job_uv_setup_enables_caching(self, jobs: dict[str, Any]) -> None:
        setup = next(
            (
                step
                for step in _steps(jobs, "test")
                if "setup-uv" in str(step.get("uses", ""))
            ),
            None,
        )
        assert setup is not None, "test must install uv via astral-sh/setup-uv"
        with_block: dict[str, Any] = setup.get("with") or {}
        assert with_block.get("enable-cache") is True, (
            "setup-uv must enable caching in every job on the free tier"
        )


# ---------------------------------------------------------------------------
# The image build and the secret-grep gate
# ---------------------------------------------------------------------------

_BUILD_ACTION = "docker/build-push-action"
_UPLOAD_ACTION = "actions/upload-artifact"
_DOWNLOAD_ACTION = "actions/download-artifact"

# The pattern set `secret-grep` applies to the built image's layer history.
# `HF_TOKEN` is the variable name the deleted build-arg path used; the second
# is the shape of a Hugging Face token itself, so a differently-named carrier
# is caught too. tests/test_dockerfile.py applies the same two to the source.
_REQUIRED_GREP_PATTERNS = ("HF_TOKEN", "hf_[A-Za-z0-9]{20,}")


class TestBuildAmd64Job:
    """US-003's build: one amd64 image, loaded, saved, and never pushed."""

    def test_build_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "build-amd64" in jobs, "Expected a 'build-amd64' job in ci.yml"

    def test_build_job_runs_on_github_hosted_ubuntu(self, jobs: dict[str, Any]) -> None:
        runs_on = jobs["build-amd64"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            f"build-amd64 must run on a GitHub-hosted runner; got {runs_on!r}"
        )

    def test_build_job_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        timeout = jobs["build-amd64"].get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"build-amd64 needs a timeout-minutes backstop; got {timeout!r}"
        )

    def test_build_targets_linux_amd64(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _BUILD_ACTION).get("with") or {}
        )
        assert with_block.get("platforms") == "linux/amd64", (
            "The build must name linux/amd64 explicitly. It is the platform the "
            "smoke job runs and the platform Poppy deploys; inheriting the "
            f"runner's would make that implicit. Got {with_block.get('platforms')!r}"
        )

    def test_build_loads_the_image_into_the_daemon(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _BUILD_ACTION).get("with") or {}
        )
        assert with_block.get("load") is True, (
            "`load: true` is what puts the built image in this runner's daemon "
            "so it can be saved and handed on; without it the build produces "
            "nothing any later job can inspect"
        )

    def test_build_never_pushes(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _BUILD_ACTION).get("with") or {}
        )
        assert with_block.get("push") is False, (
            "build-amd64 must set `push: false`. Publishing is US-007's job, "
            "behind the full gate chain — a push from here would ship an image "
            "that `secret-grep` and `smoke` have not seen yet."
        )

    def test_build_job_has_no_registry_login(self, jobs: dict[str, Any]) -> None:
        logins = [
            str(step.get("uses"))
            for step in _steps(jobs, "build-amd64")
            if "login-action" in str(step.get("uses", ""))
        ]
        assert logins == [], (
            f"build-amd64 authenticates to a registry ({logins}). It has no "
            "reason to: it neither pulls a private base nor pushes."
        )

    def test_build_job_grants_itself_no_write_permissions(
        self, jobs: dict[str, Any]
    ) -> None:
        perms: dict[str, Any] = jobs["build-amd64"].get("permissions") or {}
        writable = sorted(k for k, v in perms.items() if v == "write")
        assert writable == [], (
            f"build-amd64 raises write permissions on {writable}. Only the "
            "publish job (US-007) may, and only for the publish."
        )

    def test_build_uses_the_actions_cache(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _BUILD_ACTION).get("with") or {}
        )
        cache_from = str(with_block.get("cache-from", ""))
        cache_to = str(with_block.get("cache-to", ""))
        assert "type=gha" in cache_from and "type=gha" in cache_to, (
            "The build must read and write the GitHub Actions cache — a cold "
            "torch download on every run is not affordable on the free tier. "
            f"Got cache-from={cache_from!r} cache-to={cache_to!r}"
        )

    def test_build_records_the_image_id(self, jobs: dict[str, Any]) -> None:
        run_text = _run_text(jobs, "build-amd64")
        assert "docker image inspect" in run_text, (
            "build-amd64 must read the built image's ID back from the daemon — "
            "that is the value a `docker load` on another runner reproduces, so "
            "it is the only value the downstream equality check can be made of"
        )
        assert "IMAGE_ID_FILE" in run_text, (
            "The recorded image ID must be written to the file the artifact "
            "carries, or nothing downstream can compare against it"
        )

    def test_build_saves_the_image(self, jobs: dict[str, Any]) -> None:
        assert "docker save" in _run_text(jobs, "build-amd64"), (
            "build-amd64 must `docker save` the image: `needs:` gives the next "
            "job ordering, not the image"
        )

    def test_build_uploads_the_image_and_its_id(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _UPLOAD_ACTION).get("with") or {}
        )
        paths = str(with_block.get("path", ""))
        resolved = {
            _resolve_env(workflow, line.strip())
            for line in paths.splitlines()
            if line.strip()
        }
        env_block: dict[str, Any] = workflow.get("env") or {}
        for key in ("IMAGE_TARBALL", "IMAGE_ID_FILE"):
            expected = str(env_block[key])
            assert expected in resolved, (
                f"The uploaded artifact must carry {expected!r} — the image and "
                "the ID that proves which image it is travel together, or the "
                f"assertion downstream has nothing to check. Got {resolved!r}"
            )

    def test_upload_fails_when_there_is_nothing_to_upload(
        self, jobs: dict[str, Any]
    ) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _UPLOAD_ACTION).get("with") or {}
        )
        assert with_block.get("if-no-files-found") == "error", (
            "upload-artifact defaults to a warning when it finds no files, "
            "which would turn a broken save into a green build job and a "
            "confusing failure two jobs later"
        )

    def test_upload_sets_a_short_retention(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _UPLOAD_ACTION).get("with") or {}
        )
        retention = with_block.get("retention-days")
        assert isinstance(retention, int) and 0 < retention <= 7, (
            "The image tarball is several hundred MB against a 500 MB "
            "free-tier storage quota and is consumed minutes later in the same "
            f"run. Keep the retention short; got {retention!r}"
        )


class TestSecretGrepJob:
    """US-003's gate: the built image's layer history, mechanically checked."""

    def test_secret_grep_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "secret-grep" in jobs, "Expected a 'secret-grep' job in ci.yml"

    def test_secret_grep_needs_the_build(self, jobs: dict[str, Any]) -> None:
        needs = jobs["secret-grep"].get("needs", [])
        needs_list = [needs] if isinstance(needs, str) else list(needs)
        assert "build-amd64" in needs_list, (
            f"secret-grep must run after build-amd64; got needs={needs_list!r}"
        )

    def test_secret_grep_runs_on_github_hosted_ubuntu(
        self, jobs: dict[str, Any]
    ) -> None:
        runs_on = jobs["secret-grep"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            f"secret-grep must run on a GitHub-hosted runner; got {runs_on!r}"
        )

    def test_secret_grep_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        timeout = jobs["secret-grep"].get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"secret-grep needs a timeout-minutes backstop; got {timeout!r}"
        )

    def test_secret_grep_downloads_and_loads_the_image(
        self, jobs: dict[str, Any]
    ) -> None:
        _step_using(jobs, "secret-grep", _DOWNLOAD_ACTION)  # raises if absent
        assert "docker load" in _run_text(jobs, "secret-grep"), (
            "secret-grep must `docker load` the downloaded tarball — it runs on "
            "a fresh runner whose image store is empty"
        )

    def test_secret_grep_never_rebuilds_the_image(self, jobs: dict[str, Any]) -> None:
        builders = [
            str(step.get("uses"))
            for step in _steps(jobs, "secret-grep")
            if _BUILD_ACTION in str(step.get("uses", ""))
        ]
        assert builders == [], (
            f"secret-grep builds its own image ({builders}). Two builds of the "
            "same Dockerfile are not the same image, and the one this job "
            "cleared would not be the one that gets published."
        )
        assert "docker build" not in _run_text(jobs, "secret-grep"), (
            "secret-grep must inspect the artifact build-amd64 produced, never "
            "a rebuild"
        )

    def test_secret_grep_asserts_the_loaded_image_is_the_built_one(
        self, jobs: dict[str, Any]
    ) -> None:
        run_text = _run_text(jobs, "secret-grep")
        assert "IMAGE_ID_FILE" in run_text and "docker image inspect" in run_text, (
            "secret-grep must read the recorded image ID and inspect what it "
            "actually loaded — otherwise 'the same image' is an assumption "
            "about a tag, and a tag is not an identity"
        )

    def test_an_identity_mismatch_fails_the_job(self, jobs: dict[str, Any]) -> None:
        # Deliberately narrower than "the script contains `exit 1` somewhere":
        # the job has another `exit 1` for an empty ID file, so a coarse check
        # stays green while the check that matters is downgraded to a warning.
        # That mutation was run, escaped the coarse form, and is why this test
        # looks inside the conditional instead.
        body = _if_block_body(
            _run_text(jobs, "secret-grep"), "loaded", "recorded", "!="
        )
        assert body is not None, (
            "secret-grep must compare the loaded image ID against the recorded "
            "one in an `if` — without the comparison, the artifact handoff is "
            "trust in a tag name"
        )
        assert "exit 1" in body, (
            "The image-identity mismatch branch must exit non-zero. A mismatch "
            "that only warns is worse than no check at all: the job reports "
            "green having cleared an image that is not the one built, and not "
            "the one US-007 would publish. Branch body was:\n" + body
        )

    def test_secret_grep_reads_the_layer_history(self, jobs: dict[str, Any]) -> None:
        assert "docker history --no-trunc" in _run_text(jobs, "secret-grep"), (
            "The gate is over `docker history --no-trunc`: a build argument is "
            "recorded there verbatim, which is the leak shape that kept this "
            "repository private"
        )

    @pytest.mark.parametrize("pattern", _REQUIRED_GREP_PATTERNS)
    def test_secret_grep_pattern_set_is_defined_in_the_workflow(
        self, jobs: dict[str, Any], pattern: str
    ) -> None:
        assert pattern in _run_text(jobs, "secret-grep"), (
            f"The pattern {pattern!r} is missing from secret-grep. The set is "
            "deliberately short and lives in the workflow rather than in a "
            "checked-out script, so weakening it is a visible workflow edit."
        )

    def test_secret_grep_does_not_check_out_the_repository(
        self, jobs: dict[str, Any]
    ) -> None:
        checkouts = [
            str(step.get("uses"))
            for step in _steps(jobs, "secret-grep")
            if "checkout" in str(step.get("uses", ""))
        ]
        assert checkouts == [], (
            f"secret-grep checks out the repository ({checkouts}). It needs "
            "nothing from it: the image arrives as an artifact and the pattern "
            "set is in the workflow. Keeping the working directory empty is "
            "what stops the gate being weakened by editing a script it runs."
        )

    def test_metadata_scope_caveat_is_documented_in_the_job(self, raw: str) -> None:
        # An explicit AC: a green tick here must not be read as "no secrets in
        # the image". `docker history` sees layer metadata, not file contents.
        assert "does not read file contents" in _comment_prose(raw), (
            "The secret-grep job must carry a comment stating what the gate "
            "does NOT cover — `docker history` reports the instruction that "
            "created each layer, not the bytes inside it. Without that written "
            "down beside the job, a green tick reads as a filesystem scan."
        )


class TestImageArtifactHandoff:
    """The producer and the consumer must name the same artifact."""

    def test_upload_and_download_agree_on_the_artifact_name(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        upload: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _UPLOAD_ACTION).get("with") or {}
        )
        download: dict[str, Any] = (
            _step_using(jobs, "secret-grep", _DOWNLOAD_ACTION).get("with") or {}
        )
        uploaded = _resolve_env(workflow, str(upload.get("name", "")))
        downloaded = _resolve_env(workflow, str(download.get("name", "")))
        assert uploaded and uploaded == downloaded, (
            f"build-amd64 uploads {uploaded!r} but secret-grep downloads "
            f"{downloaded!r}. A name mismatch fails at download time with a "
            "message about a missing artifact rather than about the handoff."
        )

    def test_the_consumer_reads_the_files_the_producer_uploads(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        env_block: dict[str, Any] = workflow.get("env") or {}
        consumer = _run_text(jobs, "secret-grep")
        for key in ("IMAGE_TARBALL", "IMAGE_ID_FILE"):
            assert key in consumer, (
                f"secret-grep never reads ${{{{ env.{key} }}}} "
                f"({env_block.get(key)!r}); both halves of the handoff have to "
                "come from the same single-sourced names or they can drift"
            )


# ---------------------------------------------------------------------------
# The hermeticity canary the test lane depends on
# ---------------------------------------------------------------------------

_CANARY_PATH = _REPO_ROOT / "tests" / "test_hermeticity.py"
_CONFTEST_PATH = _REPO_ROOT / "tests" / "conftest.py"
_SKIP_MARK_RE = re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b")
_DISABLE_SOCKET_RE = re.compile(r"disable_socket\(\s*allow_unix_socket\s*=\s*True\s*\)")


class TestHermeticityCanaryIsEnforced:
    """The canary must be committed, executing, and backed by a live guard.

    These assertions deliberately live in *this* module rather than in
    ``tests/test_hermeticity.py``: a canary can assert anything it likes about
    the socket guard, but it cannot notice its own module being skipped. Run
    from here, a `pytestmark = pytest.mark.skip` on the canary still turns the
    suite red.
    """

    def test_canary_module_is_committed(self) -> None:
        assert _CANARY_PATH.exists(), (
            "tests/test_hermeticity.py must exist — the suite's hermeticity is "
            "enforced by an autouse fixture, and deleting that fixture produces "
            "no failure unless something asserts on it"
        )

    def test_canary_asserts_the_block_is_active(self) -> None:
        source = _CANARY_PATH.read_text()
        assert "SocketBlockedError" in source and "pytest.raises" in source, (
            "The canary must assert that a socket attempt raises "
            "SocketBlockedError; anything weaker is documentation"
        )

    def test_canary_is_not_skipped(self) -> None:
        source = _CANARY_PATH.read_text()
        marks = sorted(set(_SKIP_MARK_RE.findall(source)))
        assert marks == [], (
            f"The canary carries {marks} markers. A skipped canary is worse "
            "than no canary: the suite stays green while the hermeticity "
            "invariant is unguarded."
        )

    def test_conftest_still_installs_the_autouse_socket_guard(self) -> None:
        source = _CONFTEST_PATH.read_text()
        assert "autouse=True" in source, (
            "The socket guard must stay autouse — an opt-in guard protects only "
            "the tests that remember to ask for it"
        )
        assert _DISABLE_SOCKET_RE.search(source), (
            "tests/conftest.py must call disable_socket(allow_unix_socket=True): "
            "blocked network, Unix sockets kept for asyncio's self-pipe"
        )


# ---------------------------------------------------------------------------
# Job graph
# ---------------------------------------------------------------------------


class TestJobGraph:
    """`needs:` edges must be real — an undefined one invalidates the file."""

    def test_no_needs_references_an_undefined_job(self, jobs: dict[str, Any]) -> None:
        defined = set(jobs)
        for name, job in jobs.items():
            needs = job.get("needs", [])
            needs_list = [needs] if isinstance(needs, str) else list(needs)
            missing = sorted(set(needs_list) - defined)
            assert missing == [], (
                f"Job {name!r} needs undefined job(s) {missing}. Jobs arrive "
                "across several stories; a forward reference makes GitHub "
                "reject the whole workflow, not just that job."
            )
