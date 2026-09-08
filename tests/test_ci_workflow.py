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
against the recorded one, and :class:`TestImageArtifactHandoff` checks that
every consumer of that contract still refers to the same artifact.

US-005 adds :class:`TestSmokeJob`, whose least obvious assertion is a
*negative* one: the smoke job must not enumerate the ``/health`` contract in
its own shell. The field expectations belong to ``contract_smoke.py``, which
validates against the same ``HealthResponse`` model the golden-schema test
pins and reads every wire value from ``pipeline/contract.py`` at run time. A
hand-written field list in bash would be a second copy of the contract, free
to keep passing after the real one moves — which is precisely the drift the
job exists to catch.

US-007 adds :class:`TestPublishJob`, and with it the only job in the file
allowed to write anything outside the run. Two of its guards are worth knowing
about before reading the rest:

* the tag policy and the job condition are **evaluated**, not substring-matched.
  ``_evaluate`` is a small recursive-descent interpreter for the subset of
  GitHub expressions this workflow uses, so a test can ask "for
  ``refs/tags/v0.9.0-rc``, which tags does this publish?" and get the
  workflow's own answer. An expression it cannot parse raises rather than
  reading as ``False``;
* ``publish`` is an image consumer that cannot push what it consumes. A buildx
  multi-arch push builds a manifest list across platforms and cannot ship an
  image ``docker load`` put in a daemon, so the amd64 leg is rebuilt from the
  cache ``build-amd64`` filled and the published layers are then compared back
  against the gated tarball's. Its tests assert that comparison exists and
  fails the run, because without it the artifact download would be ceremony.

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

import contract_smoke

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


class ExpressionError(AssertionError):
    """A GitHub expression the evaluator below refuses to guess at.

    Raised rather than swallowed on purpose. These tests decide what the
    publish lane does for a given ref by *evaluating* the workflow's own
    conditions, so an expression the evaluator cannot parse must fail loudly:
    a rewritten condition that quietly evaluated to ``False`` would report a
    green "``latest`` does not move" for a policy nobody checked.
    """


_EXPR_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<comma>,)
      | (?P<and>&&)
      | (?P<or>\|\|)
      | (?P<eq>==)
      | (?P<neq>!=)
      | (?P<bang>!)
      | (?P<string>'[^']*')
      | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
    )""",
    re.VERBOSE,
)

_EXPR_FUNCTIONS = frozenset({"startswith", "endswith", "contains"})


def _tokenize_expression(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(expression):
        if expression[position].isspace():
            position += 1
            continue
        match = _EXPR_TOKEN_RE.match(expression, position)
        if match is None or match.lastgroup is None:
            raise ExpressionError(
                f"Cannot tokenise {expression!r} at offset {position}: "
                f"{expression[position : position + 20]!r}"
            )
        tokens.append((match.lastgroup, match.group().strip()))
        position = match.end()
    return tokens


class _ExpressionParser:
    """A recursive-descent evaluator for the subset of GitHub expressions ci.yml uses.

    Deliberately small and deliberately strict: `&&`, `||`, `!`, `==`, `!=`,
    parentheses, single-quoted strings, `github.*` context lookups and the
    three string predicates. Anything else raises, because the point of
    evaluating rather than substring-matching is that the answer is the
    workflow's answer.
    """

    def __init__(self, tokens: list[tuple[str, str]], context: dict[str, str]) -> None:
        self._tokens = tokens
        self._index = 0
        self._context = context

    def _peek(self) -> str | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index][0]

    def _next(self) -> tuple[str, str]:
        if self._index >= len(self._tokens):
            raise ExpressionError("Expression ended early")
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _accept(self, kind: str) -> bool:
        if self._peek() == kind:
            self._index += 1
            return True
        return False

    def _expect(self, kind: str) -> None:
        if not self._accept(kind):
            raise ExpressionError(f"Expected {kind}, got {self._peek()!r}")

    def parse(self) -> bool:
        value = self._parse_or()
        if self._index != len(self._tokens):
            raise ExpressionError(f"Trailing tokens from index {self._index}")
        if not isinstance(value, bool):
            raise ExpressionError(f"Expression is not a boolean: {value!r}")
        return value

    def _parse_or(self) -> str | bool:
        value = self._parse_and()
        while self._accept("or"):
            right = self._parse_and()
            value = bool(value) or bool(right)
        return value

    def _parse_and(self) -> str | bool:
        value = self._parse_comparison()
        while self._accept("and"):
            right = self._parse_comparison()
            value = bool(value) and bool(right)
        return value

    def _parse_comparison(self) -> str | bool:
        left = self._parse_unary()
        if self._accept("eq"):
            return left == self._parse_unary()
        if self._accept("neq"):
            return left != self._parse_unary()
        return left

    def _parse_unary(self) -> str | bool:
        if self._accept("bang"):
            return not bool(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> str | bool:
        kind, text = self._next()
        if kind == "lparen":
            value = self._parse_or()
            self._expect("rparen")
            return value
        if kind == "string":
            return text[1:-1]
        if kind != "ident":
            raise ExpressionError(f"Unexpected token {text!r}")
        if self._peek() == "lparen":
            return self._parse_call(text)
        if text in {"true", "false"}:
            return text == "true"
        if text not in self._context:
            raise ExpressionError(
                f"Unknown context value {text!r}; the evaluator only knows "
                f"{sorted(self._context)}"
            )
        return self._context[text]

    def _parse_call(self, name: str) -> bool:
        function = name.lower()
        if function not in _EXPR_FUNCTIONS:
            raise ExpressionError(f"Unsupported function {name!r}")
        self._expect("lparen")
        arguments: list[str | bool] = [self._parse_or()]
        while self._accept("comma"):
            arguments.append(self._parse_or())
        self._expect("rparen")
        if len(arguments) != 2 or not all(isinstance(a, str) for a in arguments):
            raise ExpressionError(f"{name}() needs two string arguments")
        haystack, needle = str(arguments[0]), str(arguments[1])
        if function == "startswith":
            return haystack.startswith(needle)
        if function == "endswith":
            return haystack.endswith(needle)
        return needle in haystack


def _evaluate(expression: str, ref: str, event_name: str) -> bool:
    """Evaluate a workflow condition for a hypothetical ref and event."""
    stripped = expression.strip()
    if stripped.startswith("${{") and stripped.endswith("}}"):
        stripped = stripped[3:-2]
    context = {
        "github.ref": ref,
        "github.ref_name": ref.rsplit("/", 1)[-1],
        "github.event_name": event_name,
    }
    return _ExpressionParser(_tokenize_expression(stripped), context).parse()


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
            f"Top-level write permissions on {writable}. The publish job "
            "declares `packages: write` / `contents: write` on itself; nothing "
            "else may inherit them."
        )

    def test_publish_is_the_only_job_that_raises_write_permissions(
        self, jobs: dict[str, Any]
    ) -> None:
        # The general form of the rule, rather than one guard per job. Until
        # US-007 the file's shape was simply "no job block declares
        # permissions", and `build-amd64` and `smoke` each asserted that about
        # themselves. That is no longer true and pretending otherwise would be
        # the dishonest reading: `publish` *must* raise two write scopes to do
        # its job. So the invariant is stated as what it actually is — exactly
        # one job may raise write access, it is `publish`, and the grant is
        # exactly these two scopes. A third scope, or a second job, fails here
        # even if that job never existed when this was written.
        raised: dict[str, list[str]] = {}
        for name, job in jobs.items():
            perms: dict[str, Any] = job.get("permissions") or {}
            writable = sorted(key for key, value in perms.items() if value == "write")
            if writable:
                raised[name] = writable
        assert raised == {"publish": ["contents", "packages"]}, (
            f"Jobs raising write permissions: {raised}. Exactly one job may — "
            "`publish`, with `contents: write` for the Release and "
            "`packages: write` for the registry push, and nothing else."
        )

    def test_a_job_that_scopes_permissions_and_checks_out_keeps_contents(
        self, jobs: dict[str, Any]
    ) -> None:
        # A job-level `permissions:` block REPLACES the top-level grant — it
        # does not merge with it. Learned the expensive way while gathering
        # US-007's evidence: a throwaway job declared `permissions: {packages:
        # read}` to pull the published image, silently lost the `contents: read`
        # it had been inheriting, and `actions/checkout` failed with
        # `fatal: repository 'https://github.com/WashingBearLabs/Forage/' not
        # found` — a 404 for a private repository, which reads like a typo in
        # the repo name rather than like a permissions bug. `publish` is fine
        # today only because `contents: write` happens to imply read.
        #
        # US-004 adds the searxng lane and will write the next permissions
        # block, so this is worth guarding rather than remembering.
        for name, job in jobs.items():
            perms: dict[str, Any] = job.get("permissions") or {}
            if not perms:
                continue
            checks_out = any(
                "checkout" in str(step.get("uses", "")) for step in job.get("steps", [])
            )
            if not checks_out:
                continue
            assert perms.get("contents") in {"read", "write"}, (
                f"Job {name!r} scopes its own permissions to {perms} and also "
                "checks the repository out. A job-level block replaces the "
                "top-level `contents: read` rather than adding to it, so this "
                "job cannot clone a private repository — and the failure is a "
                "404 on the repo URL, which looks like anything but a "
                "permissions problem."
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


# ---------------------------------------------------------------------------
# The published-image contract smoke
# ---------------------------------------------------------------------------

_SMOKE_MODULE_PATH = _REPO_ROOT / "contract_smoke.py"
_SMOKE_SCRIPT_RUN = "uv run python contract_smoke.py"

# Wire values the smoke job must NOT restate in its own shell. Every one of
# them is imported by contract_smoke.py from pipeline/contract.py or
# retrieval_app.py at run time; a copy in bash would be a second contract, and
# a second contract keeps passing after the first one moves.
_CONTRACT_VALUES_THAT_MUST_NOT_BE_IN_BASH = (
    "promptguard_unavailable",
    "search_sanitization",
    "degraded_reasons",
    "contract_version",
    "sanitizer_revision",
)


class TestSmokeJob:
    """US-005's gate: the candidate image really serves the degraded contract.

    ``build-amd64`` proves an image builds and ``secret-grep`` proves it
    carries no baked token. Neither runs it. This job does — with no Hugging
    Face token, which is the only state an image built from this repository can
    be in until the runtime weights fetch lands — and asserts that what comes
    back is honest degradation rather than a crash or a false ``healthy``.
    """

    def test_smoke_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "smoke" in jobs, (
            "Expected a 'smoke' job in ci.yml — without it, nothing in CI ever "
            "runs the image it builds, and the Epic 1 /health handshake is "
            "guarded only by unit tests against an in-process app"
        )

    def test_smoke_needs_the_build(self, jobs: dict[str, Any]) -> None:
        needs = jobs["smoke"].get("needs", [])
        needs_list = [needs] if isinstance(needs, str) else list(needs)
        assert "build-amd64" in needs_list, (
            f"smoke must run after build-amd64; got needs={needs_list!r}"
        )

    def test_smoke_runs_on_github_hosted_ubuntu(self, jobs: dict[str, Any]) -> None:
        runs_on = jobs["smoke"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            f"smoke must run on a GitHub-hosted runner; got {runs_on!r}"
        )

    def test_smoke_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        timeout = jobs["smoke"].get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"smoke needs a timeout-minutes backstop; got {timeout!r}"
        )

    def test_smoke_downloads_and_loads_the_image(self, jobs: dict[str, Any]) -> None:
        _step_using(jobs, "smoke", _DOWNLOAD_ACTION)  # raises if absent
        assert "docker load" in _run_text(jobs, "smoke"), (
            "smoke must `docker load` the downloaded tarball — it runs on a "
            "fresh runner whose image store is empty, so `needs: build-amd64` "
            "alone leaves it with nothing to run"
        )

    def test_smoke_downloads_into_a_subdirectory(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        # Unlike secret-grep, this job checks the repository out. Unpacking a
        # ~375 MB tarball over the working tree would leave the smoke's own
        # source next to build artefacts and make `path:` drift silent.
        with_block: dict[str, Any] = (
            _step_using(jobs, "smoke", _DOWNLOAD_ACTION).get("with") or {}
        )
        path = _resolve_env(workflow, str(with_block.get("path", "")))
        assert path and path != ".", (
            "smoke checks the repository out, so the image artifact must be "
            f"downloaded into its own directory; got path={path!r}"
        )

    def test_smoke_never_rebuilds_the_image(self, jobs: dict[str, Any]) -> None:
        builders = [
            str(step.get("uses"))
            for step in _steps(jobs, "smoke")
            if _BUILD_ACTION in str(step.get("uses", ""))
        ]
        assert builders == [], (
            f"smoke builds its own image ({builders}). Two builds of the same "
            "Dockerfile are not the same image, and the one this job cleared "
            "would not be the one US-007 publishes."
        )
        assert "docker build" not in _run_text(jobs, "smoke")

    def test_smoke_asserts_the_loaded_image_is_the_built_one(
        self, jobs: dict[str, Any]
    ) -> None:
        run_text = _run_text(jobs, "smoke")
        assert "IMAGE_ID_FILE" in run_text and "docker image inspect" in run_text, (
            "smoke must read the recorded image ID and inspect what it actually "
            "loaded — otherwise 'the image we built' is an assumption about a "
            "tag, and a tag is not an identity"
        )

    def test_an_identity_mismatch_fails_the_job(self, jobs: dict[str, Any]) -> None:
        # Narrow on purpose, for the reason recorded on secret-grep's twin:
        # the job has another `exit 1` (the empty-ID guard), so a check for
        # `exit 1` anywhere in the script stays green while the assertion the
        # whole handoff rests on is downgraded to a warning.
        body = _if_block_body(_run_text(jobs, "smoke"), "loaded", "recorded", "!=")
        assert body is not None, (
            "smoke must compare the loaded image ID against the recorded one "
            "in an `if` — without it the handoff is trust in a tag name"
        )
        assert "exit 1" in body, (
            "The image-identity mismatch branch must exit non-zero. A mismatch "
            "that only warns reports a green contract for an image nobody "
            "built. Branch body was:\n" + body
        )

    def test_smoke_runs_the_image_detached_on_the_service_port(
        self, jobs: dict[str, Any]
    ) -> None:
        run_lines = _run_lines(jobs, "smoke")
        run_command = next(
            (line for line in run_lines if line.startswith("docker run")), None
        )
        assert run_command is not None, "smoke must `docker run` the loaded image"
        assert " -d " in f" {run_command} ", (
            f"smoke must run the container detached; got {run_command!r}"
        )
        assert "-p 8020:8020" in run_command, (
            "The container must publish 8020 — the port the Dockerfile EXPOSEs "
            f"and the one the contract script probes. Got {run_command!r}"
        )

    def test_smoke_passes_no_environment_to_the_container(
        self, jobs: dict[str, Any]
    ) -> None:
        run_lines = _run_lines(jobs, "smoke")
        run_command = next(
            (line for line in run_lines if line.startswith("docker run")), None
        )
        assert run_command is not None
        tokens = run_command.split()
        offenders = [
            token
            for token in tokens
            if token in {"-e", "--env"} or token.startswith("--env")
        ]
        assert offenders == [], (
            f"smoke passes environment into the container ({offenders}). The "
            "whole point is the no-token state every image built here is in: a "
            "token, or a flag that fakes readiness, would test a configuration "
            "this repository cannot produce."
        )

    def test_smoke_runs_the_committed_contract_script(
        self, jobs: dict[str, Any]
    ) -> None:
        assert _SMOKE_MODULE_PATH.exists(), (
            "contract_smoke.py must be committed — the smoke job's assertions "
            "live there so they can share the golden schema's field source"
        )
        assert _SMOKE_SCRIPT_RUN in _run_text(jobs, "smoke"), (
            f"smoke must invoke {_SMOKE_SCRIPT_RUN!r}; anything else is a "
            "second implementation of the contract"
        )

    @pytest.mark.parametrize("wire_value", _CONTRACT_VALUES_THAT_MUST_NOT_BE_IN_BASH)
    def test_smoke_does_not_enumerate_the_contract_in_bash(
        self, jobs: dict[str, Any], wire_value: str
    ) -> None:
        assert wire_value not in _run_text(jobs, "smoke"), (
            f"The smoke job's shell mentions {wire_value!r}. Field expectations "
            "must come from contract_smoke.py, which imports them from "
            "pipeline/contract.py and validates against the same HealthResponse "
            "model tests/test_contract_schema.py pins. A copy here is a second "
            "contract that can drift from the first — an AC of US-005."
        )

    def test_smoke_budget_matches_the_scripts_default(self, workflow: Any) -> None:
        env_block: dict[str, Any] = workflow.get("env") or {}
        budget = float(str(env_block.get("SMOKE_TIMEOUT_SECONDS", "0")))
        assert budget == contract_smoke.DEFAULT_TIMEOUT_SECONDS, (
            f"ci.yml budgets {budget:g}s for /health but contract_smoke.py "
            f"defaults to {contract_smoke.DEFAULT_TIMEOUT_SECONDS:g}s. One "
            "number, two places: keep them tied, and keep it at 120 — 30 s "
            "spans a cold torch import too tightly (round-2 finding)."
        )

    def test_smoke_passes_the_budget_to_the_script(self, jobs: dict[str, Any]) -> None:
        run_text = _run_text(jobs, "smoke")
        assert (
            "--timeout-seconds" in run_text and "SMOKE_TIMEOUT_SECONDS" in run_text
        ), (
            "The smoke must hand the workflow's budget to the script, or the "
            "number in ci.yml is decoration and the real budget is whatever the "
            "script defaults to"
        )

    def test_smoke_dumps_the_container_log_on_failure(
        self, jobs: dict[str, Any]
    ) -> None:
        dumps = [
            step
            for step in _steps(jobs, "smoke")
            if "docker logs" in str(step.get("run", ""))
        ]
        assert dumps, (
            "smoke must dump `docker logs` when it fails — an AC. Without it a "
            "red smoke says only that the contract was violated, with no way to "
            "tell a crashed import from a wrong field, and the container is "
            "gone by the time anyone looks."
        )
        conditions = [str(step.get("if", "")).replace(" ", "") for step in dumps]
        assert any("failure()" in condition for condition in conditions), (
            f"The log dump must be conditioned on failure(); got {conditions!r}. "
            "A dump that only runs on success is the one case nobody needs."
        )

    def test_smoke_removes_the_container_afterwards(self, jobs: dict[str, Any]) -> None:
        cleanups = [
            step
            for step in _steps(jobs, "smoke")
            if "docker rm" in str(step.get("run", ""))
        ]
        assert cleanups, "smoke must remove the container it started"
        conditions = [str(step.get("if", "")).replace(" ", "") for step in cleanups]
        assert any("always()" in condition for condition in conditions), (
            f"Cleanup must run on always(); got {conditions!r} — a cleanup that "
            "is skipped on failure is skipped exactly when it matters"
        )

    def test_smoke_uploads_no_artifacts_of_its_own(self, jobs: dict[str, Any]) -> None:
        # Deliberate, and a cost decision as much as a shape one: one run of
        # this workflow already parks ~375 MiB of image tarball against a
        # 500 MB free-tier storage quota. The smoke consumes that artifact and
        # adds nothing to it.
        uploads = [
            str(step.get("uses"))
            for step in _steps(jobs, "smoke")
            if _UPLOAD_ACTION in str(step.get("uses", ""))
        ]
        assert uploads == [], (
            f"smoke uploads artifacts ({uploads}). It has nothing to hand on: "
            "its output is a pass/fail and a job log."
        )

    def test_smoke_grants_itself_no_write_permissions(
        self, jobs: dict[str, Any]
    ) -> None:
        perms: dict[str, Any] = jobs["smoke"].get("permissions") or {}
        writable = sorted(k for k, v in perms.items() if v == "write")
        assert writable == [], (
            f"smoke raises write permissions on {writable}. It runs a container "
            "and reads two endpoints; only the publish job (US-007) may write."
        )

    def test_smoke_syncs_against_the_committed_lock(self, jobs: dict[str, Any]) -> None:
        assert "--locked" in _run_text(jobs, "smoke"), (
            "The smoke's expectations are Python objects imported from this "
            "tree, so they must resolve against the versions the lock pins"
        )

    def test_smoke_uv_setup_enables_caching(self, jobs: dict[str, Any]) -> None:
        setup = next(
            (
                step
                for step in _steps(jobs, "smoke")
                if "setup-uv" in str(step.get("uses", ""))
            ),
            None,
        )
        assert setup is not None, "smoke must install uv via astral-sh/setup-uv"
        with_block: dict[str, Any] = setup.get("with") or {}
        assert with_block.get("enable-cache") is True, (
            "setup-uv must enable caching in every job on the free tier"
        )


# ---------------------------------------------------------------------------
# The publish lane
# ---------------------------------------------------------------------------

_METADATA_ACTION = "docker/metadata-action"
_LOGIN_ACTION = "docker/login-action"
_QEMU_ACTION = "docker/setup-qemu-action"

# Every gate `publish` must wait on. Spelled out rather than derived from the
# job list: the point of the assertion is that adding a seventh gate and
# forgetting to hang the publish off it is a test failure, and a derived list
# would quietly absorb exactly that mistake.
_PUBLISH_GATES = ["lint", "typecheck", "test", "build-amd64", "secret-grep", "smoke"]

# The refs the tag policy is evaluated against below. The last two are the ones
# that matter: a companion-image tag must not reach the service lane, and a
# pre-release must not move `latest`.
_MAIN_REF = "refs/heads/main"
_RELEASE_REF = "refs/tags/v1.0.0"
_PRERELEASE_REF = "refs/tags/v0.9.0-rc"
_SEARXNG_REF = "refs/tags/searxng-v0.1.0"
_PR_REF = "refs/pull/3/merge"


def _job_block(raw_text: str, job_name: str) -> str:
    """The raw lines of one job, comments included, up to the next job key."""
    lines = raw_text.splitlines()
    starts = [index for index, line in enumerate(lines) if line == f"  {job_name}:"]
    assert len(starts) == 1, f"Expected exactly one `{job_name}:` job key"
    start = starts[0]
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
        ):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def _publish_tag_rules(jobs: dict[str, Any]) -> list[str]:
    """Each non-empty line of the metadata-action `tags:` policy."""
    with_block: dict[str, Any] = (
        _step_using(jobs, "publish", _METADATA_ACTION).get("with") or {}
    )
    return [
        line.strip()
        for line in str(with_block.get("tags", "")).splitlines()
        if line.strip()
    ]


def _rule_named(rules: list[str], marker: str) -> str:
    matches = [rule for rule in rules if marker in rule]
    assert len(matches) == 1, (
        f"Expected exactly one tag rule containing {marker!r}; got {matches}"
    )
    return matches[0]


def _enabled_tag_rules(
    jobs: dict[str, Any], ref: str, event_name: str = "push"
) -> list[str]:
    """The tag rules whose `enable=` condition holds for a given ref.

    A rule with no `enable=` is unconditional and always listed. An `enable=`
    the evaluator cannot parse raises rather than being treated as false — see
    :class:`ExpressionError`.
    """
    enabled: list[str] = []
    for rule in _publish_tag_rules(jobs):
        match = re.search(r"enable=(.*)$", rule)
        if match is None or _evaluate(match.group(1), ref, event_name):
            enabled.append(rule)
    return enabled


class TestPublishJob:
    """US-007's publish: gated by everything, and stingy about what it moves."""

    def test_publish_job_exists(self, jobs: dict[str, Any]) -> None:
        assert "publish" in jobs, (
            "Expected a `publish` job — the whole reason this workflow is one "
            "file is so it can `needs:` every gate in it"
        )

    def test_publish_needs_every_gate(self, jobs: dict[str, Any]) -> None:
        needs: Any = jobs["publish"].get("needs", [])
        needs_list = [needs] if isinstance(needs, str) else list(needs)
        assert sorted(needs_list) == sorted(_PUBLISH_GATES), (
            f"publish needs {sorted(needs_list)}, expected "
            f"{sorted(_PUBLISH_GATES)}. A missing edge is a lane that ships an "
            "image over a gate nobody waited for."
        )

    def test_publish_runs_on_github_hosted_ubuntu(self, jobs: dict[str, Any]) -> None:
        assert jobs["publish"]["runs-on"] == "ubuntu-latest"

    def test_publish_has_timeout_minutes(self, jobs: dict[str, Any]) -> None:
        assert isinstance(jobs["publish"].get("timeout-minutes"), int), (
            "publish must carry a timeout — the emulated arm64 leg is the one "
            "job in this file that could plausibly hang for an hour"
        )

    @pytest.mark.parametrize(
        ("ref", "event_name", "expected"),
        [
            (_MAIN_REF, "push", True),
            (_RELEASE_REF, "push", True),
            (_PRERELEASE_REF, "push", True),
            # The cross-fire guard the spec's edge-case list asks for: a
            # companion-image tag runs the gates but must never reach the
            # service publish lane.
            (_SEARXNG_REF, "push", False),
            # A pull request head is not a release candidate, and a fork PR
            # must not reach the registry at all.
            (_PR_REF, "pull_request", False),
            (_MAIN_REF, "pull_request", False),
        ],
    )
    def test_which_refs_reach_the_publish_lane(
        self, jobs: dict[str, Any], ref: str, event_name: str, expected: bool
    ) -> None:
        # Evaluated, not substring-matched. US-003's escaped mutation taught
        # this repo that checking a script's vocabulary is not checking its
        # control flow; the same is true of a job condition. This runs the
        # workflow's own expression against a ref and asserts the answer.
        condition = str(jobs["publish"]["if"])
        assert _evaluate(condition, ref, event_name) is expected, (
            f"publish `if:` evaluates to {not expected} for ref {ref!r} on a "
            f"{event_name} event. Condition was:\n{condition}"
        )

    def test_publish_permissions_are_exactly_the_two_writes_it_needs(
        self, jobs: dict[str, Any]
    ) -> None:
        perms: dict[str, Any] = jobs["publish"].get("permissions") or {}
        assert perms == {"contents": "write", "packages": "write"}, (
            f"publish permissions are {perms}. `packages: write` pushes the "
            "image and `contents: write` creates the Release (which 403s under "
            "the top-level read-only default). Nothing else, and job-scoped so "
            "no other job inherits either."
        )

    def test_publish_targets_the_ghcr_service_repository(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "publish", _METADATA_ACTION).get("with") or {}
        )
        images = _resolve_env(workflow, str(with_block.get("images", "")))
        assert images == "ghcr.io/washingbearlabs/forage", (
            f"publish targets {images!r}. GHCR rejects an upper-case path "
            "component, so the name is spelled out lower-case rather than "
            "derived from `github.repository_owner` (`WashingBearLabs`)."
        )
        assert images == images.lower()

    def test_publish_actually_pushes(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "publish", _BUILD_ACTION).get("with") or {}
        )
        assert with_block.get("push") is True, (
            "publish must set `push: true` — it is the only job in this "
            "workflow permitted to, and the only one that should"
        )

    def test_publish_builds_the_declared_platform_list(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "publish", _BUILD_ACTION).get("with") or {}
        )
        declared = str(with_block.get("platforms", "")).strip()
        # The *expression*, not its value. Comparing `_resolve_env(...)` against
        # the env block would compare a string to itself: a hardcoded
        # `linux/amd64,linux/arm64` resolves to exactly the env value and sails
        # through, and the mutation run proved it does. `_resolve_env` is for
        # comparing two single-sourced references to each other; using it on one
        # side and a literal on the other tests equality of values, never
        # single-sourcing. That distinction matters here because
        # docs/releases.md tells the next maintainer that `PUBLISH_PLATFORMS` is
        # the one lever for the amd64-only fallback — a copy of the string
        # elsewhere makes that documentation false without failing anything.
        assert declared == "${{ env.PUBLISH_PLATFORMS }}", (
            f"publish declares `platforms: {declared}`. It must read "
            "`${{ env.PUBLISH_PLATFORMS }}` — docs/releases.md documents that "
            "variable as the single place a platform trim happens."
        )
        env_block: dict[str, Any] = workflow.get("env") or {}
        platforms = str(env_block.get("PUBLISH_PLATFORMS", ""))
        assert "linux/amd64" in platforms, (
            f"publish builds {platforms!r}. amd64 is the only architecture this "
            "workflow gates, so it can never be the one that gets trimmed."
        )

    def test_emulation_is_set_up_exactly_when_it_is_needed(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        env_block: dict[str, Any] = workflow.get("env") or {}
        platforms = str(env_block.get("PUBLISH_PLATFORMS", ""))
        qemu_steps = [
            step
            for step in _steps(jobs, "publish")
            if _QEMU_ACTION in str(step.get("uses", ""))
        ]
        if "arm64" in platforms:
            assert qemu_steps, (
                "publish builds an arm64 leg but sets up no emulator; buildx "
                "would fail on the first RUN instruction"
            )
        else:
            assert not qemu_steps, (
                "publish sets up qemu for a platform list that needs none — a "
                "step that silently does nothing outlives the reason for it"
            )

    # -- tag policy ---------------------------------------------------------

    def test_the_actions_own_latest_handling_is_turned_off(
        self, jobs: dict[str, Any]
    ) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "publish", _METADATA_ACTION).get("with") or {}
        )
        flavor = str(with_block.get("flavor", ""))
        assert "latest=false" in flavor.replace(" ", ""), (
            "metadata-action must be told `latest=false`. Its `latest=auto` "
            "does roughly the right thing, and `latest` is the one tag whose "
            "accidental movement is a production incident — the rule is "
            "written out below instead."
        )

    def test_the_tag_policy_is_the_four_rules_the_spec_asks_for(
        self, jobs: dict[str, Any]
    ) -> None:
        rules = _publish_tag_rules(jobs)
        assert len(rules) == 4, f"Expected four tag rules, got {rules}"
        assert "type=semver,pattern={{version}}" in _rule_named(rules, "{{version}}")
        assert "{{major}}.{{minor}}" in _rule_named(rules, "{{major}}")
        assert "prefix=sha-" in _rule_named(rules, "type=sha")
        assert "value=latest" in _rule_named(rules, "latest")

    @pytest.mark.parametrize(
        ("ref", "expected_markers"),
        [
            # A push to main gets one moving tag and nothing else: no semver
            # value exists for it, and it must not touch `latest`.
            (_MAIN_REF, {"type=sha"}),
            # A pre-release publishes its exact version and nothing that any
            # consumer follows — no `X.Y` alias, and above all no `latest`.
            (_PRERELEASE_REF, {"{{version}}"}),
            # A real release moves everything.
            (_RELEASE_REF, {"{{version}}", "{{major}}", "latest"}),
        ],
    )
    def test_which_tags_a_ref_publishes(
        self, jobs: dict[str, Any], ref: str, expected_markers: set[str]
    ) -> None:
        enabled = _enabled_tag_rules(jobs, ref)
        markers = {
            marker
            for marker in ("{{version}}", "{{major}}", "type=sha", "latest")
            if any(marker in rule for rule in enabled)
        }
        assert markers == expected_markers, (
            f"For ref {ref!r} the enabled tag rules are {enabled}, which is "
            f"{sorted(markers)} rather than {sorted(expected_markers)}"
        )

    def test_latest_never_moves_for_a_prerelease(self, jobs: dict[str, Any]) -> None:
        # The single most dangerous property in this file, asserted on its own
        # so its failure message says what broke rather than "a set differs".
        latest_rule = _rule_named(_publish_tag_rules(jobs), "value=latest")
        condition = re.search(r"enable=(.*)$", latest_rule)
        assert condition is not None, (
            f"The `latest` rule carries no `enable=`: {latest_rule!r}. An "
            "unconditional `latest` moves on every publish, release candidates "
            "included."
        )
        expression = condition.group(1)
        assert _evaluate(expression, _RELEASE_REF, "push") is True
        assert _evaluate(expression, _PRERELEASE_REF, "push") is False, (
            "A `-rc` tag would move `latest`. This spec's Assumptions put the "
            "first `latest` at v1.0.0; every pre-release before it must leave "
            f"the tag alone. Condition was {expression!r}"
        )
        assert _evaluate(expression, _MAIN_REF, "push") is False
        assert _evaluate(expression, _SEARXNG_REF, "push") is False

    # -- the identity chain -------------------------------------------------

    def test_publish_downloads_and_loads_the_gated_image(
        self, jobs: dict[str, Any]
    ) -> None:
        uses = [str(step.get("uses", "")) for step in _steps(jobs, "publish")]
        assert any(_DOWNLOAD_ACTION in ref for ref in uses), (
            "publish must download build-amd64's artifact. It is the last "
            "consumer, and the only job placed to check that what reaches the "
            "registry is what passed the gates."
        )
        assert "docker load" in _run_text(jobs, "publish")

    def test_publish_asserts_the_loaded_image_is_the_built_one(
        self, jobs: dict[str, Any]
    ) -> None:
        run_text = _run_text(jobs, "publish")
        assert "IMAGE_ID_FILE" in run_text and "docker image inspect" in run_text

    def test_an_identity_mismatch_fails_the_job(self, jobs: dict[str, Any]) -> None:
        # Narrow for the reason US-003 recorded: the job has other `exit 1`s,
        # so a substring check over the whole script would stay green while
        # this branch degraded to a warning.
        body = _if_block_body(_run_text(jobs, "publish"), "loaded", "recorded", "!=")
        assert body is not None, (
            "publish must compare the loaded image ID against the recorded one"
        )
        assert "exit 1" in body, (
            "The image-identity mismatch branch must exit non-zero. Branch "
            "body was:\n" + body
        )

    def test_publish_verifies_the_published_layers_against_the_gated_ones(
        self, jobs: dict[str, Any]
    ) -> None:
        run_text = _run_text(jobs, "publish")
        assert "RootFS.Layers" in run_text, (
            "publish must record the gated image's diff IDs — the sha256 of "
            "each layer's uncompressed tar, which is the filesystem itself and "
            "survives re-compression by a registry"
        )
        assert "rootfs.diff_ids" in run_text and "imagetools inspect" in run_text, (
            "publish must read the *published* image's diff IDs back out of "
            "the registry. A multi-arch push cannot ship the loaded tarball, "
            "so the amd64 leg is rebuilt from the cache build-amd64 wrote — "
            "and 'rebuilt from the same cache' is a claim that has to be "
            "checked, not asserted in a comment."
        )

    def test_a_layer_mismatch_fails_the_job(self, jobs: dict[str, Any]) -> None:
        body = _if_block_body(
            _run_text(jobs, "publish"), "published_layers", "gated_layers", "!="
        )
        assert body is not None, (
            "publish must compare the published diff IDs against the gated "
            "ones inside an `if` — the comparison is the whole linkage between "
            "the smoke-tested image and the registry"
        )
        assert "exit 1" in body, (
            "A published image that is not the gated filesystem must fail the "
            "run, which is also what stops the Release from being created. "
            "Branch body was:\n" + body
        )

    def test_the_layer_comparison_cannot_pass_vacuously(
        self, jobs: dict[str, Any]
    ) -> None:
        body = _if_block_body(_run_text(jobs, "publish"), "gated_count", "-eq 0")
        assert body is not None, (
            "publish must floor the gated layer list before comparing: two "
            "jq misses both yield the string 'null', and 'null' != 'null' is "
            "false — a comparison of nothing against nothing would pass"
        )
        assert "exit 1" in body, (
            "An empty gated layer list must fail the run, not merely skip the "
            "comparison. Branch body was:\n" + body
        )

    def test_publish_greps_the_published_config_for_secrets(
        self, jobs: dict[str, Any]
    ) -> None:
        run_text = _run_text(jobs, "publish")
        body = _if_block_body(run_text, "image_json", "grep", "HF_TOKEN")
        assert body is not None, (
            "publish must grep the published image config for the secret "
            "pattern set. The rootfs parity proves the filesystem; a "
            "build-arg/ENV leak lives in the config with an *empty* layer — "
            "identical diff_ids, leaked history — and secret-grep only ever "
            "reads the local tarball, never the registry copy"
        )
        assert "exit 1" in body, (
            "A forbidden pattern in the published config must fail the run. "
            "Branch body was:\n" + body
        )
        assert "hf_[A-Za-z0-9]{20,}" in run_text, (
            "The published-config grep must use the same pattern set "
            "secret-grep defines — one vocabulary, two vantage points"
        )

    def test_publish_verifies_before_it_releases(self, jobs: dict[str, Any]) -> None:
        names = [str(step.get("name", "")) for step in _steps(jobs, "publish")]
        push_index = next(i for i, name in enumerate(names) if "Build and push" in name)
        verify_index = next(i for i, name in enumerate(names) if "Verify" in name)
        release_index = next(i for i, name in enumerate(names) if "Release" in name)
        assert push_index < verify_index < release_index, (
            f"Step order is {names}. The Release must come after the push and "
            "after the verification: a Release is the artifact humans and "
            "downstream specs read as 'this version shipped', so it may only "
            "exist once a gated image is pullable. Reversing this makes a "
            "Release a promise the registry has not kept."
        )

    def test_the_release_is_created_only_on_a_version_tag(
        self, jobs: dict[str, Any]
    ) -> None:
        release_step = next(
            step
            for step in _steps(jobs, "publish")
            if "Release" in str(step.get("name", ""))
        )
        condition = str(release_step.get("if", ""))
        assert condition, "The Release step must carry its own `if:`"
        assert _evaluate(condition, _RELEASE_REF, "push") is True
        assert _evaluate(condition, _PRERELEASE_REF, "push") is True
        assert _evaluate(condition, _MAIN_REF, "push") is False, (
            "A push to main publishes a `sha-` image and no Release. Minting a "
            "Release per commit would make the word meaningless."
        )
        assert _evaluate(condition, _SEARXNG_REF, "push") is False

    def test_the_release_marks_prereleases_as_such(self, jobs: dict[str, Any]) -> None:
        run_text = _run_text(jobs, "publish")
        assert "--prerelease" in run_text, (
            "A `-rc` tag must produce a Release flagged pre-release, or "
            "GitHub's own 'latest release' pointer moves onto it — the same "
            "mistake as moving the `latest` image tag, in a different registry"
        )

    def test_the_release_uses_the_preinstalled_cli(self, jobs: dict[str, Any]) -> None:
        assert "gh release create" in _run_text(jobs, "publish"), (
            "The Release is created with the preinstalled `gh`, not a "
            "fourth-party action. One fewer pinned dependency for a two-line "
            "API call is the right trade on a repository about to go public."
        )

    # -- posture ------------------------------------------------------------

    def test_publish_logs_in_with_only_the_workflow_token(
        self, jobs: dict[str, Any]
    ) -> None:
        login = _step_using(jobs, "publish", _LOGIN_ACTION)
        with_block: dict[str, Any] = login.get("with") or {}
        assert str(with_block.get("registry")) == "ghcr.io"
        password = str(with_block.get("password", ""))
        assert "secrets.GITHUB_TOKEN" in password, (
            f"The registry login uses {password!r}. GITHUB_TOKEN is minted per "
            "run and dies with it; a stored PAT would be a long-lived registry "
            "credential sitting in a repository that is about to go public."
        )

    def test_publish_ships_no_attestations(self, jobs: dict[str, Any]) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "publish", _BUILD_ACTION).get("with") or {}
        )
        assert with_block.get("provenance") is False, (
            "Image signing and provenance are this spec's declared Out of "
            "Scope. Emitting attestations without the verifying half is "
            "decoration, and it changes the shape of the pushed artifact the "
            "verification step reads."
        )
        assert with_block.get("sbom") is False

    def test_publish_uses_a_separate_cache_scope_for_the_emulated_layers(
        self, jobs: dict[str, Any]
    ) -> None:
        with_block: dict[str, Any] = (
            _step_using(jobs, "publish", _BUILD_ACTION).get("with") or {}
        )
        cache_from = str(with_block.get("cache-from", ""))
        cache_to = str(with_block.get("cache-to", ""))
        assert "type=gha" in cache_from and "scope=publish" in cache_from, (
            "publish must read both the default scope (build-amd64's amd64 "
            f"layers) and its own; got cache-from={cache_from!r}"
        )
        assert "scope=publish" in cache_to and "mode=max" in cache_to, (
            "The arm64 layers must be written to their own scope. Sharing "
            "build-amd64's would evict them on its next amd64-only run and pay "
            f"the cold qemu cost forever; got cache-to={cache_to!r}"
        )

    def test_publish_uploads_no_artifacts_of_its_own(
        self, jobs: dict[str, Any]
    ) -> None:
        uploads = [
            str(step.get("uses"))
            for step in _steps(jobs, "publish")
            if _UPLOAD_ACTION in str(step.get("uses", ""))
        ]
        assert uploads == [], (
            f"publish uploads artifacts ({uploads}). Its output is a registry "
            "push and a Release; there is nothing to hand on."
        )

    def test_publish_documents_its_failure_modes(self, raw: str) -> None:
        prose = _comment_prose(_job_block(raw, "publish"))
        for phrase in (
            "a red publish is not a release",
            "re-running a failed publish is safe",
            "a published release implies a pullable, gated image",
        ):
            assert phrase in prose, (
                f"The publish job must state {phrase!r} beside the step it "
                "describes. These are the questions someone asks at 2am with a "
                "half-finished push, and the answer belongs in the file."
            )

    def test_the_arm64_leg_is_documented_as_ungated(self, raw: str) -> None:
        prose = _comment_prose(_job_block(raw, "publish"))
        assert "there is no arm64 gate" in prose, (
            "The identity chain covers amd64 only — nothing in this workflow "
            "has ever executed the emulated leg. A verification step that does "
            "not say what it excludes reads as covering everything."
        )


# ---------------------------------------------------------------------------
# The image artifact handoff, across every consumer
# ---------------------------------------------------------------------------

# Every job that receives the built image. All three read the same artifact and
# assert the same identity against `build-amd64`'s recorded image ID; a fourth
# consumer belongs here too, because the handoff is only sound if none of them
# quietly rebuilds or invents a second copy.
#
# `publish` is a consumer with a twist worth knowing before reading its tests:
# it downloads the tarball but cannot *push* it — a buildx multi-arch push
# builds a manifest list across platforms and cannot ship an image `docker
# load` put in a daemon. What it does with the artifact is compare it: the
# published amd64 layers must equal the gated ones, diff ID for diff ID. So it
# is a consumer of the image's *identity*, not of its bytes-on-the-wire, and
# that is exactly the distinction its own tests assert.
_IMAGE_CONSUMERS = ("secret-grep", "smoke", "publish")


class TestImageArtifactHandoff:
    """The producer and every consumer must name the same artifact."""

    @pytest.mark.parametrize("consumer", _IMAGE_CONSUMERS)
    def test_upload_and_download_agree_on_the_artifact_name(
        self, jobs: dict[str, Any], workflow: Any, consumer: str
    ) -> None:
        upload: dict[str, Any] = (
            _step_using(jobs, "build-amd64", _UPLOAD_ACTION).get("with") or {}
        )
        download: dict[str, Any] = (
            _step_using(jobs, consumer, _DOWNLOAD_ACTION).get("with") or {}
        )
        uploaded = _resolve_env(workflow, str(upload.get("name", "")))
        downloaded = _resolve_env(workflow, str(download.get("name", "")))
        assert uploaded and uploaded == downloaded, (
            f"build-amd64 uploads {uploaded!r} but {consumer} downloads "
            f"{downloaded!r}. A name mismatch fails at download time with a "
            "message about a missing artifact rather than about the handoff."
        )

    @pytest.mark.parametrize("consumer", _IMAGE_CONSUMERS)
    def test_the_consumer_reads_the_files_the_producer_uploads(
        self, jobs: dict[str, Any], workflow: Any, consumer: str
    ) -> None:
        env_block: dict[str, Any] = workflow.get("env") or {}
        consumer_script = _run_text(jobs, consumer)
        for key in ("IMAGE_TARBALL", "IMAGE_ID_FILE"):
            assert key in consumer_script, (
                f"{consumer} never reads ${{{{ env.{key} }}}} "
                f"({env_block.get(key)!r}); both halves of the handoff have to "
                "come from the same single-sourced names or they can drift"
            )

    def test_there_is_exactly_one_image_artifact(
        self, jobs: dict[str, Any], workflow: Any
    ) -> None:
        # One artifact, provably the same bytes, never a rebuild — and never a
        # second copy either: the tarball is ~375 MiB against a 500 MB
        # free-tier quota, so a per-consumer artifact would triple the bill for
        # no added guarantee.
        uploads: set[str] = set()
        for step in _all_steps(jobs):
            if _UPLOAD_ACTION not in str(step.get("uses", "")):
                continue
            with_block: dict[str, Any] = step.get("with") or {}
            uploads.add(_resolve_env(workflow, str(with_block.get("name", ""))))
        assert len(uploads) == 1, (
            f"The workflow uploads {sorted(uploads)}. Consumers share one image "
            "artifact; a second one is storage nobody reads."
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
