"""Permanent guards on the text of ``Dockerfile``.

This module is the regression canary for the property that kept this
repository private: **no secret may enter the image build.** The old
``ARG HF_TOKEN`` + ``from_pretrained`` bake block was not a bad habit, it was a
publishable leak — Docker records a build argument in the finished image's
layer history, where ``docker history --no-trunc`` reads it straight back out
of any registry the image reaches, and deleting the downloaded file afterwards
does nothing about it.

CI's ``secret-grep`` job greps the *built image* for the same pattern set these
tests apply to the *source*, and the two are complementary rather than
redundant: the job proves the deletion held for the artifact that was actually
produced, and cannot run until an image exists; these tests run in every
``uv run pytest``, on a developer's machine and in the ``test`` lane, and fail
the moment the path is written back — before anything is built or pushed.

The rest of the module pins the three other properties US-003 established, each
of which an ordinary-looking edit could silently undo:

* the base image is digest-pinned (US-005's smoke and US-007's publish are only
  talking about the same image if it cannot move between their builds);
* dependencies come from the committed ``uv.lock`` rather than a fresh
  resolution of unpinned ranges;
* the build stays single-stage, which is what makes ``docker history`` cover
  the whole build rather than only a final stage.

test_mapping:
  Dockerfile: tests/test_dockerfile.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_PATH = _REPO_ROOT / "Dockerfile"

# Variable names that must never be declared or assigned in a build
# instruction. Deliberately broad: the point is to catch the *next* secret,
# not to re-catch HF_TOKEN. `HF_HOME` and the `UV_*` build settings do not
# match, and neither does any other legitimate name this image needs.
_SECRET_NAME_RE = re.compile(
    r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|_KEY\b",
    re.IGNORECASE,
)

# The same two patterns CI's `secret-grep` job applies to the built image's
# layer history, applied here to the source instead. `HF_TOKEN` is the name the
# deleted path used; the second is the shape of a Hugging Face token itself, so
# a differently-named carrier is caught too. Keep the two sets in step — the
# workflow's copy is asserted by tests/test_ci_workflow.py.
_TOKEN_SHAPE_RE = re.compile(r"hf_[A-Za-z0-9]{20,}")

_BASE_IMAGE_RE = re.compile(r"^python:3\.12-slim@sha256:[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_BASE_VERSION_RE = re.compile(r"\b3\.12\.\d+\b")


def _raw() -> str:
    return DOCKERFILE_PATH.read_text()


def _instruction_lines(text: str) -> list[str]:
    """Logical instructions: comments dropped, line continuations joined.

    Mirrors what the Dockerfile parser itself does, so a check cannot be
    defeated by wrapping an instruction over two lines — and so the header
    comment explaining *why* the build-arg path is gone is not mistaken for
    the path itself.
    """
    instructions: list[str] = []
    buffer = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue
        if not buffer and not stripped:
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        if buffer.strip():
            instructions.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        instructions.append(buffer.strip())
    return instructions


def _instructions_named(lines: list[str], keyword: str) -> list[str]:
    """The argument text of every instruction with the given keyword."""
    prefix = keyword.upper() + " "
    return [
        line[len(prefix) :].strip() for line in lines if line.upper().startswith(prefix)
    ]


def _declared_name(argument_text: str) -> str:
    """The variable name an ARG/ENV instruction declares first."""
    head = argument_text.split()[0] if argument_text.split() else ""
    return head.split("=", 1)[0]


@pytest.fixture(scope="module")
def raw() -> str:
    return _raw()


@pytest.fixture(scope="module")
def instructions() -> list[str]:
    return _instruction_lines(_raw())


# ---------------------------------------------------------------------------
# No secret may enter the build
# ---------------------------------------------------------------------------


class TestNoSecretEntersTheBuild:
    """The permanent canary. A re-added build-arg path fails here first."""

    def test_dockerfile_exists(self) -> None:
        assert DOCKERFILE_PATH.exists(), "Expected a Dockerfile at the repo root"

    def test_no_arg_declares_a_secret_name(self, instructions: list[str]) -> None:
        offenders = [
            text
            for text in _instructions_named(instructions, "ARG")
            if _SECRET_NAME_RE.search(_declared_name(text))
        ]
        assert offenders == [], (
            f"Dockerfile declares secret-shaped build argument(s): {offenders}. "
            "A build ARG is not a secret — Docker records it in the image's "
            "layer history, recoverable via `docker history --no-trunc` from "
            "any registry the image reaches. Pass secrets at *runtime*, or use "
            "a build secret mount; never an ARG."
        )

    def test_no_env_declares_a_secret_name(self, instructions: list[str]) -> None:
        offenders = [
            text
            for text in _instructions_named(instructions, "ENV")
            if _SECRET_NAME_RE.search(_declared_name(text))
        ]
        assert offenders == [], (
            f"Dockerfile bakes secret-shaped environment variable(s): "
            f"{offenders}. An ENV baked at build time ships inside the image "
            "and shows in its layer history; runtime configuration belongs in "
            "the container's environment (docs/configuration.md)."
        )

    def test_no_instruction_assigns_a_secret_valued_variable(
        self, instructions: list[str]
    ) -> None:
        # Catches the RUN-scoped form the ARG check would miss:
        # `RUN HF_TOKEN=... python -c ...` is recorded in the history verbatim.
        offenders: list[str] = []
        for line in instructions:
            for name in re.findall(r"(?:^|[\s\"'])([A-Za-z_][A-Za-z0-9_]*)=", line):
                if _SECRET_NAME_RE.search(name):
                    offenders.append(f"{name} in {line[:80]!r}")
        assert offenders == [], (
            f"Dockerfile assigns secret-shaped variable(s) in a build "
            f"instruction: {offenders}. Every instruction's text is preserved "
            "in the image's layer history."
        )

    def test_no_hf_token_in_any_instruction(self, instructions: list[str]) -> None:
        offenders = [line for line in instructions if "HF_TOKEN" in line.upper()]
        assert offenders == [], (
            f"`HF_TOKEN` appears in build instruction(s): {offenders}. This is "
            "the exact path US-003 deleted; it is a leak the moment the image "
            "is pushed. The weights are fetched at runtime instead."
        )

    def test_no_model_bake_step(self, instructions: list[str]) -> None:
        offenders = [line for line in instructions if "from_pretrained" in line]
        assert offenders == [], (
            f"Dockerfile downloads model weights at build time: {offenders}. "
            "`meta-llama/Llama-Prompt-Guard-2-22M` is a gated repository, so a "
            "build-time download needs a credential in the build — which is "
            "the leak. Weights arrive at runtime "
            "(feature-forage-model-bootstrap); until they do the service "
            "reports `degraded`, honestly."
        )

    def test_no_token_shaped_literal_anywhere(self, raw: str) -> None:
        # Whole file, comments included: a real token pasted into a comment is
        # still a token committed to a repository that is about to go public.
        matches = _TOKEN_SHAPE_RE.findall(raw)
        assert matches == [], (
            f"Dockerfile contains {len(matches)} Hugging-Face-token-shaped "
            "literal(s). Even in a comment, that is a committed credential."
        )


# ---------------------------------------------------------------------------
# The base image is digest-pinned, and the build is single-stage
# ---------------------------------------------------------------------------


class TestBaseImagePin:
    """A floating base breaks the smoke→publish identity argument."""

    def test_single_from_instruction(self, instructions: list[str]) -> None:
        froms = _instructions_named(instructions, "FROM")
        assert len(froms) == 1, (
            f"Expected exactly one FROM; found {len(froms)}: {froms}. The build "
            "is single-stage on purpose: `docker history` reports only the "
            "final stage's layers, so a multi-stage build would quietly narrow "
            "what CI's `secret-grep` job can see to the last stage alone."
        )

    def test_base_image_is_digest_pinned(self, instructions: list[str]) -> None:
        (base,) = _instructions_named(instructions, "FROM")
        assert _BASE_IMAGE_RE.match(base), (
            f"FROM {base!r} must be `python:3.12-slim@sha256:<64 hex>`. A "
            "floating tag can move between the build the `smoke` job proves "
            "and the build the `publish` job ships, which is the whole "
            "argument those two jobs rest on."
        )

    def test_base_version_recorded_in_an_adjacent_comment(self, raw: str) -> None:
        # Dockerfile syntax has no inline comments — a `#` after an
        # instruction's arguments is parsed as another argument and FROM
        # rejects it — so the tag the digest resolved to lives on a comment
        # line just above. Without it the pin is an opaque 64-hex string and
        # nobody can tell which Python it is, let alone when to bump it.
        lines = raw.splitlines()
        from_index = next(
            (i for i, line in enumerate(lines) if line.startswith("FROM ")), None
        )
        assert from_index is not None, "No FROM instruction found"
        preceding = lines[max(0, from_index - 5) : from_index]
        assert any(
            line.lstrip().startswith("#") and _BASE_VERSION_RE.search(line)
            for line in preceding
        ), (
            "The comment line(s) above FROM must record the concrete 3.12.x "
            "version the pinned digest resolved to; a bare digest is "
            f"unmaintainable. Got: {preceding!r}"
        )

    def test_every_external_copy_from_is_digest_pinned(
        self, instructions: list[str]
    ) -> None:
        for text in _instructions_named(instructions, "COPY"):
            match = re.match(r"--from=(\S+)", text)
            if match is None:
                continue
            source = match.group(1)
            if "/" not in source and ":" not in source:
                continue  # a build-stage name, not an image reference
            assert _DIGEST_RE.search(source), (
                f"COPY --from={source} pulls a third-party image on a movable "
                "tag. Every image this build consumes is pinned by digest, for "
                "the same reason every action in ci.yml is pinned by SHA."
            )


# ---------------------------------------------------------------------------
# Dependencies come from the committed lock
# ---------------------------------------------------------------------------


class TestLockDrivenInstall:
    """The image installs what CI tested, not a fresh resolution."""

    def test_uv_sync_locked(self, instructions: list[str]) -> None:
        syncs = [line for line in instructions if "uv sync" in line]
        assert syncs, "The image must install dependencies with `uv sync`"
        for line in syncs:
            assert "--locked" in line, (
                f"`uv sync` without --locked: {line!r}. Without it a stale "
                "uv.lock is silently re-resolved and the image ships versions "
                "nothing has linted, type-checked or tested."
            )

    def test_lock_is_copied_into_the_build(self, instructions: list[str]) -> None:
        assert any(
            "uv.lock" in text for text in _instructions_named(instructions, "COPY")
        ), "uv.lock must be COPYed into the build for `uv sync --locked` to read"

    def test_no_pip_install(self, instructions: list[str]) -> None:
        offenders = [line for line in instructions if "pip install" in line]
        assert offenders == [], (
            f"Dockerfile pip-installs dependencies: {offenders}. The previous "
            "form of this file parsed pyproject.toml with a shell one-liner and "
            "installed the unpinned ranges — a different dependency set from "
            "the one under test. `uv sync --locked` replaced it."
        )

    def test_no_pyproject_parsing_one_liner(self, instructions: list[str]) -> None:
        offenders = [line for line in instructions if "tomllib" in line]
        assert offenders == [], (
            f"Dockerfile re-derives its dependency list from pyproject.toml: "
            f"{offenders}. The lock is the source of truth."
        )


# ---------------------------------------------------------------------------
# What the image still has to be
# ---------------------------------------------------------------------------


class TestRuntimeShapePreserved:
    """US-003 removed a path; it must not have removed anything else."""

    def test_hf_home_points_at_the_model_cache(self, instructions: list[str]) -> None:
        assert "ENV HF_HOME=/app/model-cache" in instructions, (
            "ENV HF_HOME=/app/model-cache must stay: it is where the runtime "
            "weights fetch (feature-forage-model-bootstrap) will write, and the "
            "directory the non-root user owns"
        )

    def test_build_time_import_smoke(self, instructions: list[str]) -> None:
        assert any(
            'python -c "import retrieval_app"' in line for line in instructions
        ), (
            'The `RUN python -c "import retrieval_app"` build smoke must '
            "stay — it is what stops a broken module reaching a published image"
        )

    def test_runs_as_a_non_root_user(self, instructions: list[str]) -> None:
        users = _instructions_named(instructions, "USER")
        assert users and users[-1] == "poppy", (
            f"The image must end up running as the non-root `poppy` user; "
            f"USER instructions were {users!r}"
        )

    def test_exposes_the_service_port(self, instructions: list[str]) -> None:
        assert "8020" in " ".join(_instructions_named(instructions, "EXPOSE"))

    def test_cmd_serves_the_app_on_the_service_port(
        self, instructions: list[str]
    ) -> None:
        (cmd,) = _instructions_named(instructions, "CMD")
        assert "uvicorn" in cmd and "retrieval_app:app" in cmd and "8020" in cmd, (
            f"CMD must serve retrieval_app:app on 8020; got {cmd!r}"
        )
