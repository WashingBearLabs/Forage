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

The rest of the module pins the other properties this file has to keep, each of
which an ordinary-looking edit could silently undo:

* the base image is digest-pinned (US-005's smoke and US-007's publish are only
  talking about the same image if it cannot move between their builds);
* dependencies come from the committed ``uv.lock`` rather than a fresh
  resolution of unpinned ranges;
* the build stays single-stage, which is what makes ``docker history`` cover
  the whole build rather than only a final stage;
* the ``oras`` client the runtime weights fetch shells out to is pinned to an
  exact version with a per-architecture sha256, and the build takes **no** build
  arguments at all — not even ``TARGETARCH``
  (``feature-forage-model-bootstrap`` US-004).

test_mapping:
  Dockerfile: tests/test_dockerfile.py
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_PATH = _REPO_ROOT / "Dockerfile"
DOCKERIGNORE_PATH = _REPO_ROOT / ".dockerignore"

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

# The `oras` install (feature-forage-model-bootstrap US-004). The two
# architectures are the two this image is published for — ci.yml's
# `PUBLISH_PLATFORMS` is `linux/amd64,linux/arm64` — and a pin that covered
# only one would leave the mirror path dead on the other, discovered at
# runtime during the outage that made someone need it.
PUBLISHED_ARCHITECTURES = ("amd64", "arm64")
_ORAS_URL_RE = re.compile(
    r"https://github\.com/oras-project/oras/releases/download/"
    r"v(?P<tag>\d+\.\d+\.\d+)/oras_(?P<file>\d+\.\d+\.\d+)_linux_"
)
_ORAS_ARCH_SHA_RE = re.compile(r"(?P<arch>[a-z0-9]+)\)\s*sha256=(?P<sha>[0-9a-f]{64})")
_ORAS_VERSION_COMMENT_RE = re.compile(r"^#\s*oras\s+(?P<version>\d+\.\d+\.\d+)\b")


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


def _oras_instruction(lines: list[str]) -> str:
    """The single build instruction that installs ``oras``."""
    matches = [line for line in lines if _ORAS_URL_RE.search(line)]
    assert len(matches) == 1, (
        f"Expected exactly one instruction downloading oras; found "
        f"{len(matches)}. The install is one RUN on purpose: the download, the "
        "checksum check and the extraction have to live or die together, or a "
        "layer cache can serve an unverified binary."
    )
    return matches[0]


@pytest.fixture(scope="module")
def raw() -> str:
    return _raw()


@pytest.fixture(scope="module")
def instructions() -> list[str]:
    return _instruction_lines(_raw())


@pytest.fixture(scope="module")
def oras_install(instructions: list[str]) -> str:
    return _oras_instruction(instructions)


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

    def test_the_build_takes_no_arguments_at_all(self, instructions: list[str]) -> None:
        """Not "no secret-shaped ARG" — no ARG, full stop (CLAUDE.md 2).

        The sibling tests above catch a *secret-shaped* name, which is the
        failure everyone pictures. This one catches the way the path actually
        comes back: an innocuous ARG lands for a good reason, "the Dockerfile
        declares no ARG" stops being true, and the next argument only has to
        clear "is it as harmless as that one?" rather than an absolute.

        US-004 is the live example. `ARG TARGETARCH` is the conventional way to
        select a per-architecture download, and it carries no secret — the
        `oras` install uses `dpkg --print-architecture` instead precisely so
        this assertion can stay absolute. `docs/configuration.md`'s
        "Build-time arguments: there are none" is the same claim, made to
        operators.
        """
        declared = _instructions_named(instructions, "ARG")
        assert declared == [], (
            f"Dockerfile declares build argument(s): {declared}. This file "
            "takes none, by design — see CLAUDE.md invariant 2. If a genuinely "
            "unavoidable one arrives, it is a decision to argue for here, in "
            "CLAUDE.md and in docs/configuration.md, not a line to add."
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
# The oras client is version-pinned and checksum-verified, per architecture
# ---------------------------------------------------------------------------


class TestOrasIsPinnedPerArchitecture:
    """`feature-forage-model-bootstrap` US-004.

    The mirror fallback shells out to `oras`, so `oras` is now part of the
    runtime's trusted computing base: whatever it pulls is what gets extracted,
    verified and — if it verifies — loaded. Everything else the image trusts is
    pinned by digest (the base, the uv layer, every GitHub Action), and a
    `curl | tar` of a floating release would make the fetch *tool* the weakest
    link in a chain whose whole point is that the fetched *bytes* are pinned
    file-by-file.

    These are text guards, like the rest of this module: they fail on a
    developer's machine, in every `uv run pytest`, before anything is built.
    """

    def test_oras_is_installed(self, oras_install: str) -> None:
        assert "oras" in oras_install
        assert "/usr/local/bin/oras" in oras_install, (
            "oras must land somewhere on PATH — `model_fetcher` resolves it "
            "with `shutil.which`, and a mirror fetch that cannot find it "
            "reports `oras_missing` and stays degraded"
        )

    def test_the_download_url_pins_an_exact_version(self, oras_install: str) -> None:
        match = _ORAS_URL_RE.search(oras_install)
        assert match is not None
        assert match.group("tag") == match.group("file"), (
            f"The release tag (v{match.group('tag')}) and the asset name "
            f"(oras_{match.group('file')}_…) name different versions — one of "
            "them was bumped and the other was not, and the checksums below "
            "belong to only one of them."
        )
        assert "latest" not in oras_install, (
            "`latest` is not a pin. The `smoke` job proves a specific image and "
            "the `publish` job ships one; a tool that can move between them "
            "breaks that argument exactly as a floating base image would."
        )

    def test_the_version_is_recorded_in_an_adjacent_comment(self, raw: str) -> None:
        """The same courtesy the base-image digest gets, for the same reason.

        A 64-hex digest and a URL buried in a shell `case` are both opaque at a
        glance. The comment is what lets a reader answer "which oras is this,
        and is it current?" without leaving the file.
        """
        lines = raw.splitlines()
        install_index = next(
            (i for i, line in enumerate(lines) if _ORAS_URL_RE.search(line)), None
        )
        assert install_index is not None
        preceding = lines[max(0, install_index - 12) : install_index]
        versions = [
            match.group("version")
            for line in preceding
            if (match := _ORAS_VERSION_COMMENT_RE.match(line.strip())) is not None
        ]
        assert versions, (
            "A comment line above the oras install must record the version, in "
            f"the form `# oras X.Y.Z`. Got: {preceding[-4:]!r}"
        )
        url_match = _ORAS_URL_RE.search(lines[install_index])
        assert url_match is not None
        assert versions[-1] == url_match.group("tag"), (
            f"The comment says oras {versions[-1]} and the URL downloads "
            f"{url_match.group('tag')}. One of them is a lie, and the reader "
            "has no way to tell which."
        )

    @pytest.mark.parametrize("architecture", PUBLISHED_ARCHITECTURES)
    def test_every_published_architecture_has_its_own_checksum(
        self, oras_install: str, architecture: str
    ) -> None:
        pinned = dict(_ORAS_ARCH_SHA_RE.findall(oras_install))
        assert architecture in pinned, (
            f"No pinned oras sha256 for {architecture}. ci.yml publishes "
            f"{', '.join(PUBLISHED_ARCHITECTURES)}, so a missing arm only shows "
            "up as a failed build (if the build fails closed) or a missing "
            "binary at runtime (if it does not)."
        )
        assert len(pinned[architecture]) == 64

    def test_the_checksums_differ_between_architectures(
        self, oras_install: str
    ) -> None:
        """A copy-paste that reuses one digest fails every build but one.

        The realistic mistake in a hand-maintained `case` is not a wrong
        digest, it is the *same* digest in both arms — which looks right, and
        is right for exactly one architecture.
        """
        pinned = dict(_ORAS_ARCH_SHA_RE.findall(oras_install))
        digests = [pinned[arch] for arch in PUBLISHED_ARCHITECTURES if arch in pinned]
        assert len(set(digests)) == len(digests), (
            f"Two architectures share an oras sha256: {pinned}. They cannot "
            "both be correct — the release publishes a different tarball for "
            "each."
        )

    def test_the_download_is_checksum_verified(self, oras_install: str) -> None:
        assert "sha256sum -c" in oras_install, (
            "The downloaded tarball must be checked against the pinned digest "
            "with `sha256sum -c`. Pinning a digest that nothing verifies is "
            "documentation, not a control."
        )

    def test_the_download_is_https(self, oras_install: str) -> None:
        assert "http://" not in oras_install
        assert oras_install.count("https://github.com/oras-project/") >= 1

    def test_an_unpinned_architecture_fails_the_build(self, oras_install: str) -> None:
        """No silent fallback and no silent skip.

        Both alternatives produce an image that builds green and has no `oras`,
        and the mirror path then dies at runtime — on the fallback, which by
        definition is being exercised because the primary source is already
        down.
        """
        assert "*)" in oras_install and "exit 1" in oras_install, (
            "The architecture `case` must have a default arm that exits "
            f"non-zero. Got: {oras_install[:400]!r}"
        )

    def test_the_architecture_is_detected_rather_than_passed_in(
        self, oras_install: str
    ) -> None:
        """`dpkg --print-architecture`, not `ARG TARGETARCH` — deliberately.

        Under buildx the RUN executes in the *target* platform's rootfs, so
        dpkg reports the target rather than the builder, in exactly the names
        oras publishes. It also works under the legacy builder, where
        `TARGETARCH` is simply empty and the `case` would fall through to its
        failing arm. The reason it is written this way rather than the
        conventional way is upstream of both, though: this file takes no build
        arguments at all, and `test_the_build_takes_no_arguments_at_all` is
        what keeps that absolute.
        """
        assert "dpkg --print-architecture" in oras_install
        assert "TARGETARCH" not in oras_install


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


# ---------------------------------------------------------------------------
# Everything the runtime needs is actually copied in
# ---------------------------------------------------------------------------


class TestRuntimeSourceIsCopied:
    """The COPY list is filename-enumerated, so a new module is easy to forget.

    Enumeration is the right choice — it is what keeps `contract_smoke.py`,
    `searxng_smoke.py` and the test suite out of a published image — but its
    failure mode is silent: the image builds, imports, serves, and only the
    path that needed the missing file breaks, at runtime, in production. The
    weights fetcher and the manifest it verifies against are the current
    example (`feature-forage-model-bootstrap` US-002): without them the
    runtime fetch has no pin and no verifier.
    """

    @pytest.mark.parametrize(
        "filename",
        [
            "retrieval_app.py",
            "models.py",
            "cache.py",
            "url_validator.py",
            "config.yaml",
            "model_fetcher.py",
            "weights_manifest.json",
        ],
    )
    def test_runtime_file_is_copied(
        self, instructions: list[str], filename: str
    ) -> None:
        copied = " ".join(_instructions_named(instructions, "COPY"))
        assert filename in copied.split(), (
            f"{filename!r} is not in the Dockerfile's COPY list. The list is "
            "enumerated by filename, so an uncopied module is missing from the "
            "image with no build error — the failure surfaces at runtime."
        )

    def test_the_copied_runtime_files_exist_in_the_repo(
        self, instructions: list[str]
    ) -> None:
        """A COPY of a path that is not committed fails the build outright."""
        missing = [
            token
            for text in _instructions_named(instructions, "COPY")
            if not text.startswith("--from=")
            for token in text.split()[:-1]
            if not (_REPO_ROOT / token).exists()
        ]
        assert missing == [], f"Dockerfile COPYs path(s) that do not exist: {missing}"


# ---------------------------------------------------------------------------
# The frozen contract ships inside the image
# ---------------------------------------------------------------------------

# The in-image destination. It is part of the published interface — the spec's
# own independent test is `docker run --rm --entrypoint cat <image>
# /app/contract/openapi.yaml`, contract/GOVERNANCE.md's Consumers table names
# it, and contract_smoke.py reads it out of the candidate image on every CI
# run. A moved destination breaks all three and breaks nothing the build would
# notice, because no module imports any of it.
IMAGE_CONTRACT_DIR = "/app/contract/"

# What has to arrive there. The document and its anchor are the pair the
# three-way sha256 check is made of; GOVERNANCE.md rides along deliberately
# (the rules travel with the artifact they govern) and is not part of any
# checksum — the anchor covers `openapi.yaml` alone.
CONTRACT_SOURCE_DIR = "contract/"
CONTRACT_SHIPPED_FILES = ("openapi.yaml", "openapi.yaml.sha256")


def _dockerignore_patterns() -> list[str]:
    """The non-comment, non-negated patterns in ``.dockerignore``."""
    return [
        line.strip()
        for line in DOCKERIGNORE_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith(("#", "!"))
    ]


def _would_exclude(pattern: str, path: str) -> bool:
    """Whether *pattern* plausibly removes *path* from the build context.

    A deliberate over-approximation of Docker's matcher: it errs toward
    reporting an exclusion, because the failure this guards against is the
    silent one — an ignored `contract/` produces an image with an empty
    directory and no build error at all, and the first symptom is a consumer
    `cat`-ing a file that is not there.
    """
    cleaned = pattern.rstrip("/")
    if cleaned.startswith("**/"):
        tail = cleaned[3:]
        return fnmatch(path, tail) or fnmatch(PurePosixPath(path).name, tail)
    return fnmatch(path, cleaned) or path.startswith(f"{cleaned}/")


class TestTheContractShipsInTheImage:
    """`feature-forage-contract` US-004: the contract is in the artifact.

    A consumer already pulls the image; making them fetch the wire contract
    from somewhere else — a repository they may not have, a Release page they
    have to trust — is how a vendored contract goes stale without anyone
    noticing. So the image carries it, at a fixed path, byte-identical to the
    committed file its committed `.sha256` anchors.

    These are text guards like the rest of this module. The complementary
    *runtime* check is CI's `smoke` job, which reads the file back out of the
    built image and compares it against both the committed anchor and the
    running container's `/health.contract_version` (`contract_smoke.py`).
    """

    def test_the_contract_directory_is_copied_into_the_image(
        self, instructions: list[str]
    ) -> None:
        copies = [
            text
            for text in _instructions_named(instructions, "COPY")
            if text.split() and text.split()[0] == CONTRACT_SOURCE_DIR
        ]
        assert copies, (
            f"No `COPY {CONTRACT_SOURCE_DIR} {IMAGE_CONTRACT_DIR}` in the "
            "Dockerfile. Without it the image serves a contract it does not "
            "carry, and the in-image route in contract/GOVERNANCE.md's "
            "Consumers table is a promise nothing keeps."
        )

    def test_the_contract_lands_at_the_published_path(
        self, instructions: list[str]
    ) -> None:
        (copy_text,) = [
            text
            for text in _instructions_named(instructions, "COPY")
            if text.split() and text.split()[0] == CONTRACT_SOURCE_DIR
        ]
        destination = copy_text.split()[-1]
        assert destination == IMAGE_CONTRACT_DIR, (
            f"The contract is copied to {destination!r}, not "
            f"{IMAGE_CONTRACT_DIR!r}. Nothing in the image imports it, so a "
            "moved destination builds, boots and serves perfectly — and every "
            "documented way of reading the contract out of the image fails."
        )

    @pytest.mark.parametrize("filename", CONTRACT_SHIPPED_FILES)
    def test_the_shipped_pair_exists_in_the_repo(self, filename: str) -> None:
        """The document and the anchor that verifies it, both committed.

        `COPY contract/` ships whatever is in the directory, so "the anchor is
        in the image" is a property of the repository, not of the Dockerfile.
        Both files are generated together by `scripts/export_contract.py`;
        `tests/test_contract_export.py` is what keeps them current.
        """
        assert (_REPO_ROOT / CONTRACT_SOURCE_DIR / filename).exists(), (
            f"contract/{filename} is missing. The in-image copy is only useful "
            "as a verified pair: the document, and the sha256 anchor a "
            "consumer checks it against."
        )

    @pytest.mark.parametrize(
        "path",
        [
            CONTRACT_SOURCE_DIR.rstrip("/"),
            *(f"{CONTRACT_SOURCE_DIR}{name}" for name in CONTRACT_SHIPPED_FILES),
        ],
    )
    def test_dockerignore_keeps_the_contract_in_the_build_context(
        self, path: str
    ) -> None:
        """The silent failure mode, closed.

        `.dockerignore` excludes `tests/`, `kit_tools/` and `scripts/`; adding
        `contract/` to that list — or a broad pattern that catches it — would
        not fail the build. COPY of a directory whose contents are all excluded
        produces an empty directory, and the image would ship a `contract/`
        with nothing in it.
        """
        offenders = [
            pattern
            for pattern in _dockerignore_patterns()
            if _would_exclude(pattern, path)
        ]
        assert offenders == [], (
            f".dockerignore pattern(s) {offenders} exclude {path!r} from the "
            "build context. The COPY would then ship an empty directory, "
            "silently — no build error, and the failure surfaces when a "
            "consumer reads the contract out of the image."
        )


# ---------------------------------------------------------------------------
# The image contents are reproducible
# ---------------------------------------------------------------------------

# Files whose *content* carries a wall-clock timestamp, so that two builds of
# one commit produce two different layers. Measured at US-004 by diffing the
# layers of two `--no-cache` builds: these four were the entire drift of the
# apt layer.
TIMESTAMPED_APT_ARTEFACTS = (
    "/var/log/apt",
    "/var/log/dpkg.log",
    "/var/cache/ldconfig/aux-cache",
)


class TestReproducibleImageContents:
    """The Dockerfile half of the cold-cache publish fix (US-004).

    `publish` rebuilds the amd64 leg and asserts its layers are the ones
    `smoke` executed. Buildx's `rewrite-timestamp` exporter attribute
    normalizes every timestamp it can reach — but only the ones in the layer
    tar's *headers*. A timestamp written *inside* a file is out of its reach,
    and this image used to produce two kinds:

    * apt and dpkg logs, plus `ldconfig`'s `aux-cache`;
    * the `.pyc` the build-time import check wrote for ~580 dependency
      modules, each embedding its source's mtime.

    Measured: with the exporter attribute alone 16 of 18 layers matched across
    two cold builds from two checkouts; with these two normalizations as well,
    all 19 did. Both halves are load-bearing and neither is cosmetic, which is
    why an "unnecessary `rm -rf`" or a "why is bytecode disabled?" tidy-up
    fails here rather than at a release six weeks later.
    """

    @pytest.mark.parametrize("artefact", TIMESTAMPED_APT_ARTEFACTS)
    def test_the_apt_layer_leaves_no_timestamped_artefact(
        self, instructions: list[str], artefact: str
    ) -> None:
        apt_steps = [line for line in instructions if "apt-get install" in line]
        assert apt_steps, "Expected an apt-get install instruction"
        assert any(artefact in line for line in apt_steps), (
            f"The apt instruction does not remove {artefact}. Its content "
            "carries the build's wall clock, so it differs between two builds "
            "of the same commit and takes the whole layer's digest with it — "
            "which is what failed `publish`'s parity gate on a cold cache. "
            "Nothing in this image reads it: there is exactly one apt "
            "transaction, at build time."
        )

    def test_the_import_check_writes_no_bytecode(self, instructions: list[str]) -> None:
        check = next(
            (line for line in instructions if "import retrieval_app" in line), None
        )
        assert check is not None, "Expected the build-time import check"
        assert "PYTHONDONTWRITEBYTECODE=1" in check, (
            "The import check must not write `.pyc`. A timestamp-based `.pyc` "
            "embeds its source's mtime, which differs between two checkouts of "
            "the same commit — 579 files of drift in one layer, and unreachable "
            "by the exporter's timestamp rewrite because the value is inside "
            "the file. The cache it would leave is void anyway once timestamps "
            "are rewritten: the sources then read SOURCE_DATE_EPOCH while the "
            "`.pyc` still record the build clock, so Python recompiles on "
            f"import regardless. Instruction was: {check[:120]!r}"
        )
