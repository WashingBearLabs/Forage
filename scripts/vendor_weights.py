"""Vendor the pinned PromptGuard weights to the private GHCR mirror.

``feature-forage-model-bootstrap`` US-003. Forage fetches its weights at run
time from Hugging Face's *gated* ``meta-llama/Llama-Prompt-Guard-2-22M`` repo.
Gated means one vendor decision away from unavailable, so we keep our own copy:
an OCI artifact at ``ghcr.io/washingbearlabs/forage-weights:<revision>``, which
US-004 falls back to when Hugging Face cannot be reached. This module is how
that copy is made, and how the committed ``weights_manifest.json`` that blesses
it is generated.

**Six phases, each a function, each runnable on its own** (``--step``), because
the run that matters is supervised: it needs the owner's Hugging Face token and
a GHCR credential, so a human drives it and wants to be able to stop, look, and
resume rather than re-run a monolith.

1. ``download``    — ``snapshot_download`` at the pinned revision.
2. ``manifest``    — generate ``weights_manifest.json`` from what landed.
3. ``tar``         — a deterministic, symlink-**dereferenced** tarball.
4. ``selfcheck``   — extract that tarball and run it through the real verifier.
5. ``push``        — ``oras push``, tagged by revision sha.
6. ``visibility``  — the pushed package must be **private**.

``--dry-run`` runs everything that only reads (1-4) and stops before anything
that writes to a network (5-6), printing the exact command and request it would
have made.

**Four properties are load-bearing, and each is a test.**

*The constants come from* :mod:`model_fetcher`. ``MODEL_ID``, the revision pin
and ``ALLOW_PATTERNS`` are imported, never restated. A vendoring run that
downloaded a different file set from the one the service verifies would produce
a manifest nothing could satisfy — the two must be the same rule or they are
not a rule.

*The tar dereferences symlinks.* Every entry in ``snapshots/<revision>/`` is a
symlink into ``blobs/``; ``tar`` without ``-h`` ships the links and ~0 bytes of
content, and the failure only shows up on the far side of a pull. Members are
built from the **resolved** file, and the archive is verified by extracting it
before it is pushed.

*The tar is deterministic.* Same snapshot in, same bytes out: members sorted,
mtimes/uids/gids/mode pinned, gzip header stamped with ``mtime=0``. A byte-wise
reproducible artifact is what lets anyone re-run this script and prove the
published one was not tampered with in between.

*The allowlist is enforced at generation time.* A manifest that blesses a
``pytorch_model.bin`` must be **ungeneratable**, not merely unusable. The
verifier already refuses one; this is the other end of the same closure, so
nobody can hand-wave a pickle into the pin by regenerating the file.

**The token never reaches an argv or a log line.** ``ps`` is world-readable and
CI logs outlive the credentials in them. The Hugging Face token goes to
``snapshot_download(token=...)`` and nowhere else; the registry credential goes
to ``oras login --password-stdin`` over a pipe; the GitHub API token goes into
an ``Authorization`` header. Every subprocess's captured output is redacted
before it is printed.

Usage::

    export HF_TOKEN=hf_...            # gated-repo read access
    export GHCR_USER=<github-login>
    export GHCR_TOKEN=ghp_...         # write:packages, for the push
    export GITHUB_TOKEN=ghp_...       # read:packages, for the visibility check

    uv run python -m scripts.vendor_weights --dry-run     # rehearse
    uv run python -m scripts.vendor_weights               # the real run

``docs/weights.md`` is the procedure this implements, including the rule that
matters most: the revision default, the manifest and the mirror tag move
**together, in one commit**.

test_mapping:
  scripts/vendor_weights.py: tests/test_vendor_weights.py
  scripts/__init__.py: tests/test_vendor_weights.py
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, cast

from model_fetcher import (
    ALLOW_PATTERNS,
    DEFAULT_MODEL_REVISION,
    HF_TOKEN_ENV_VAR,
    MANIFEST_PATH,
    VerificationResult,
    hub_cache_dir,
    is_allowed_filename,
    read_manifest_pin,
    snapshot_path,
    verify_weights,
)
from promptguard.classifier import MODEL_ID

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The mirror, spelled out in lower case. GHCR rejects an upper-case path
# component, and a push is the wrong place to discover that — the same reason
# `.github/workflows/ci.yml` spells `IMAGE_NAME` out rather than deriving it
# from `github.repository_owner` (which renders `WashingBearLabs`).
MIRROR_REPOSITORY: Final = "ghcr.io/washingbearlabs/forage-weights"

# The artifact is one gzipped tar of the snapshot tree. `oras` needs both a
# type for the manifest and a media type for the layer; naming them here keeps
# US-004's puller and this pusher describing the same thing.
ARTIFACT_TYPE: Final = "application/vnd.washingbearlabs.forage-weights.v1+tar"
LAYER_MEDIA_TYPE: Final = "application/vnd.oci.image.layer.v1.tar+gzip"

# Credentials, by environment variable. `HF_TOKEN` is shared with the service
# (imported from `model_fetcher`, not restated); the registry ones are this
# script's own and exist nowhere in the running image.
GHCR_USER_ENV_VAR: Final = "GHCR_USER"
GHCR_TOKEN_ENV_VAR: Final = "GHCR_TOKEN"
# Read-only, and separate on purpose: the visibility check must be able to run
# with a token that could not have pushed anything.
GITHUB_TOKEN_ENV_VAR: Final = "GITHUB_TOKEN"

GITHUB_API_ROOT: Final = "https://api.github.com"
GITHUB_API_VERSION: Final = "2022-11-28"
PACKAGE_TYPE: Final = "container"
REQUIRED_VISIBILITY: Final = "private"

# Where a vendoring run keeps its working files. Outside the repository on
# purpose: a ~270 MiB cache and a tarball inside the tree are one `git add -A`
# away from a very bad commit, and `.gitignore` is a weaker guarantee than
# "not there".
DEFAULT_WORK_DIRNAME: Final = "forage-vendor-weights"

TARBALL_NAME_TEMPLATE: Final = "forage-weights-{revision}.tar.gz"

# Pinned into every archive member. Real ownership and timestamps are
# properties of the machine that ran the download, not of the weights, and
# letting them through is what makes two runs of this script disagree.
_TAR_MTIME: Final = 0
_TAR_MODE: Final = 0o644
_TAR_UID: Final = 0
_TAR_GID: Final = 0
_GZIP_COMPRESSLEVEL: Final = 9

_HASH_CHUNK_BYTES: Final = 1024 * 1024

_STEPS: Final = (
    "download",
    "manifest",
    "tar",
    "selfcheck",
    "push",
    "visibility",
)

_ORAS_INSTALL_GUIDANCE: Final = (
    "`oras` is not on PATH. It is a hard requirement of the push step — "
    "hand-rolling the OCI token exchange was rejected in this spec's "
    "planning, and GHCR needs one even with a PAT. Install it from "
    "https://oras.land/docs/installation (macOS: `brew install oras`), then "
    "re-run with `--step push`. Everything before the push is already done "
    "and on disk; nothing needs repeating."
)


class VendorError(RuntimeError):
    """A vendoring step refused to continue. The message is the diagnosis."""


class ManifestGenerationError(VendorError):
    """The snapshot cannot yield an honest manifest.

    Its own class because it carries the story's sharpest rule: a manifest
    blessing a non-safetensors weight file is not "a manifest we would refuse
    later", it is a file this script will not write.
    """


# ---------------------------------------------------------------------------
# Seams — a subprocess and an HTTP client, injectable
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One completed subprocess: status and captured streams."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """Runs an argv, optionally feeding *stdin*, and reports the result."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        cwd: Path | None = None,
    ) -> CommandResult:
        """Execute *argv* and return its status and captured output."""
        ...


class HttpFetcher(Protocol):
    """Performs one GET and reports ``(status, body)``."""

    def __call__(self, url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        """Fetch *url* with *headers*, returning the status and raw body."""
        ...


def run_command(
    argv: Sequence[str],
    *,
    stdin: str | None = None,
    cwd: Path | None = None,
) -> CommandResult:
    """Run *argv*, capturing both streams.

    *stdin* is how a credential travels: a pipe is not visible in ``ps`` and
    does not survive in the shell history the way an argument does. There is
    no shell anywhere in this path — the argv is a list, built here.
    """
    completed = subprocess.run(
        list(argv),
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        cwd=None if cwd is None else str(cwd),
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def fetch_url(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    """GET *url*, returning ``(status, body)`` and never raising on 4xx/5xx."""
    if not url.startswith("https://"):
        raise VendorError(f"refusing a non-https API request to {url}")
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = cast(int, response.status)
            return status, cast(bytes, response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise VendorError(f"GitHub API unreachable: {exc.reason}") from None


# ---------------------------------------------------------------------------
# Output — printed, and redacted before it is printed
# ---------------------------------------------------------------------------


def redact(text: str, secrets: Sequence[str | None]) -> str:
    """Replace every non-empty secret in *text* with a fixed marker.

    Belt to the braces of never putting a credential in an argv: an upstream
    tool that decides to echo what it was given must not be the reason a token
    ends up in a terminal scrollback or a session transcript.
    """
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def emit(message: str) -> None:
    """Print one line of progress. The only output channel this module has."""
    print(message)


# ---------------------------------------------------------------------------
# The snapshot: one walk, shared by the manifest and the tar
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    """One file in the snapshot: its relative name and the bytes it resolves to."""

    relative: str
    resolved: Path


def sha256_and_size(path: Path) -> tuple[str, int]:
    """Hash *path* in chunks, returning ``(hexdigest, size)``."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def collect_snapshot_files(snapshot_dir: Path) -> tuple[SnapshotFile, ...]:
    """Every file the snapshot offers, symlinks resolved, sorted by name.

    The single walk behind both the manifest and the tar, so the two cannot
    describe different sets. Directory symlinks and anything that is not a
    regular file are refused rather than followed — the same rule
    :func:`model_fetcher.verify_weights` applies, for the same reason.
    """
    if not snapshot_dir.is_dir():
        raise VendorError(
            f"no snapshot at {snapshot_dir} — run `--step download` first (or "
            f"point `--cache-root` at a volume that already holds one)"
        )

    found: dict[str, Path] = {}
    _scan_into(snapshot_dir, "", found)
    if not found:
        raise VendorError(
            f"the snapshot at {snapshot_dir} is empty; there is nothing to vendor"
        )
    return tuple(
        SnapshotFile(relative=name, resolved=found[name]) for name in sorted(found)
    )


def _scan_into(directory: Path, prefix: str, found: dict[str, Path]) -> None:
    """Recurse one directory level, resolving links, refusing everything else."""
    for entry in sorted(os.scandir(directory), key=lambda item: item.name):
        relative = f"{prefix}{entry.name}"
        path = Path(entry.path)
        if entry.is_symlink():
            resolved = path.resolve()
            if not resolved.is_file():
                raise VendorError(
                    f"{relative} is a symlink to something that is not a regular "
                    f"file ({resolved}); refusing to vendor it"
                )
            found[relative] = resolved
            continue
        if entry.is_dir(follow_symlinks=False):
            _scan_into(path, f"{relative}/", found)
            continue
        if entry.is_file(follow_symlinks=False):
            found[relative] = path
            continue
        raise VendorError(f"{relative} is not a regular file; refusing to vendor it")


def enforce_allowlist(files: Sequence[SnapshotFile]) -> None:
    """Refuse the whole set if any member is a format we will not admit.

    The RCE closure's generation-time half. ``.bin`` and every other pickle
    carrier fails here, so a manifest that blesses one cannot be produced —
    and neither can a tarball that ships one, which matters because US-004
    extracts the tarball into the directory the loader reads.
    """
    offenders = [
        file.relative for file in files if not is_allowed_filename(file.relative)
    ]
    if offenders:
        raise ManifestGenerationError(
            "refusing to vendor a non-allowlisted file: "
            + ", ".join(offenders)
            + ". Only safetensors weights and inert tokenizer/config files may "
            "be pinned — a pickle in the set is the `torch.load` RCE this "
            "manifest exists to close. If the upstream repo genuinely changed "
            "format, that is a spec decision, not a regeneration."
        )


# ---------------------------------------------------------------------------
# Phase 1 — download
# ---------------------------------------------------------------------------


def download_snapshot(*, revision: str, cache_root: Path, token: str | None) -> Path:
    """Download the pinned snapshot into ``<cache_root>/hub`` and return it.

    Identical in every argument that matters to what the service does at boot
    (:func:`model_fetcher.acquire_and_load`): the same ``MODEL_ID``, the same
    ``allow_patterns``, and ``cache_dir=$HF_HOME/hub`` rather than
    ``local_dir=``, which would flatten away the ``snapshots/`` + ``blobs/``
    shape the verifier walks. A vendoring run that fetched a different set
    would mint a manifest no running container could satisfy.
    """
    if not token:
        raise VendorError(
            f"{HF_TOKEN_ENV_VAR} is not set. The PromptGuard repository is "
            f"gated: vendoring needs a token whose account has been granted "
            f"access by Meta. See docs/weights.md."
        )

    from huggingface_hub import snapshot_download

    cache_root.mkdir(parents=True, exist_ok=True)
    emit(f"  downloading {MODEL_ID}@{revision[:12]}… into {cache_root}")
    try:
        snapshot_download(
            MODEL_ID,
            revision=revision,
            cache_dir=hub_cache_dir(cache_root),
            allow_patterns=list(ALLOW_PATTERNS),
            token=token,
        )
    except Exception as exc:
        # Never `str(exc)`: huggingface_hub's errors carry request context, and
        # `HF_TOKEN` must not reach a terminal (CLAUDE.md invariant 6).
        raise VendorError(
            f"the download failed ({type(exc).__name__}). Check that the token "
            f"is valid and that its account has been granted access to "
            f"{MODEL_ID}, and that revision {revision} still exists."
        ) from None

    snapshot = snapshot_path(cache_root, MODEL_ID, revision)
    if not snapshot.is_dir():
        raise VendorError(
            f"the download reported success but {snapshot} does not exist — "
            f"the cache layout is not what this script expects"
        )
    files = collect_snapshot_files(snapshot)
    total = sum(file.resolved.stat().st_size for file in files)
    emit(f"  downloaded {len(files)} file(s), {total:,} bytes")
    return snapshot


# ---------------------------------------------------------------------------
# Phase 2 — the manifest
# ---------------------------------------------------------------------------


def generate_manifest(
    snapshot_dir: Path, *, model_id: str, revision: str
) -> dict[str, Any]:
    """Build the manifest document that pins exactly what is in *snapshot_dir*.

    The allowlist is applied **here**, before a single hash is written, which
    is the difference between a rule and a preference: there is no sequence of
    flags that makes this function emit a manifest blessing a pickle.
    """
    files = collect_snapshot_files(snapshot_dir)
    enforce_allowlist(files)

    entries: list[dict[str, Any]] = []
    for file in files:
        digest, size = sha256_and_size(file.resolved)
        entries.append({"path": file.relative, "sha256": digest, "size": size})

    return {
        "_comment": [
            "Generated by scripts/vendor_weights.py — do not hand-edit.",
            "This is an exact-set allowlist: model_fetcher.verify_weights()",
            "refuses a snapshot that is missing a file, carries an extra one,",
            "or hashes differently. Regenerate it with the vendoring run, and",
            "move it, model_fetcher.DEFAULT_MODEL_REVISION and the mirror tag",
            "ghcr.io/washingbearlabs/forage-weights:<revision> in ONE commit.",
            "See docs/weights.md.",
        ],
        "model_id": model_id,
        "revision": revision,
        "files": entries,
    }


def write_manifest(document: Mapping[str, Any], manifest_path: Path) -> None:
    """Write *document* as the committed manifest, formatted and newline-ended."""
    manifest_path.write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def read_manifest_document(manifest_path: Path) -> dict[str, Any] | None:
    """Read a manifest as a plain document, or ``None`` if it is not one."""
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return cast(dict[str, Any], parsed)


def manifest_diff(
    old: Mapping[str, Any] | None, new: Mapping[str, Any]
) -> tuple[str, ...]:
    """Describe what regenerating the manifest changed, line by line.

    Printed rather than merely computed because this is the moment a re-vendor
    either is or is not what the operator meant: a changed sha on a file whose
    revision did not move is upstream mutating a pin, and it should stop the
    run's author in their tracks.
    """
    lines: list[str] = []
    if old is None:
        lines.append("no readable previous manifest — this is a first generation")
        old_files: dict[str, dict[str, Any]] = {}
    else:
        for key in ("model_id", "revision"):
            before = old.get(key)
            after = new.get(key)
            if before != after:
                lines.append(f"{key}: {before!r} -> {after!r}")
        old_files = _files_by_path(old)

    new_files = _files_by_path(new)
    for path in sorted(set(old_files) - set(new_files)):
        lines.append(f"- {path} (removed)")
    for path in sorted(set(new_files) - set(old_files)):
        entry = new_files[path]
        lines.append(f"+ {path} ({entry.get('size')} bytes, {entry.get('sha256')})")
    for path in sorted(set(old_files) & set(new_files)):
        before_entry = old_files[path]
        after_entry = new_files[path]
        if before_entry.get("sha256") != after_entry.get("sha256"):
            lines.append(
                f"~ {path} sha256 {before_entry.get('sha256')} -> "
                f"{after_entry.get('sha256')}"
            )
        elif before_entry.get("size") != after_entry.get("size"):
            lines.append(
                f"~ {path} size {before_entry.get('size')} -> {after_entry.get('size')}"
            )
    if not lines:
        lines.append("no change")
    return tuple(lines)


def _files_by_path(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index a manifest's ``files`` list by path, tolerating a malformed one."""
    raw = document.get("files")
    if not isinstance(raw, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        path = entry.get("path")
        if isinstance(path, str):
            indexed[path] = entry
    return indexed


# ---------------------------------------------------------------------------
# Phase 3 — the deterministic, dereferenced tarball
# ---------------------------------------------------------------------------


def build_tarball(snapshot_dir: Path, tarball_path: Path) -> Path:
    """Write *snapshot_dir* to *tarball_path* as a reproducible gzipped tar.

    Two properties, both non-negotiable and both tested.

    **Dereferenced.** Each member is built from the file the snapshot entry
    *resolves to*, so the archive carries content rather than links into a
    ``blobs/`` directory that exists only on the machine that ran the
    download. ``dereference=True`` is set as well, but the members are
    resolved explicitly so the guarantee does not rest on a library flag's
    interaction with how the entries were added.

    **Deterministic.** Members are emitted in sorted order with mtime, mode,
    uid/gid and owner names pinned, and the gzip header is stamped
    ``mtime=0`` — otherwise the wrapper alone would make two runs differ. Same
    snapshot in, same bytes out, so a published artifact can be reproduced and
    compared rather than merely trusted.
    """
    files = collect_snapshot_files(snapshot_dir)
    enforce_allowlist(files)

    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    # `filename=""` keeps the source name out of the gzip header and `mtime=0`
    # keeps the clock out of it; `tarfile.open(mode="w:gz")` does neither, which
    # is why the two layers are opened by hand.
    with (
        tarball_path.open("wb") as raw,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            compresslevel=_GZIP_COMPRESSLEVEL,
            mtime=_TAR_MTIME,
        ) as compressed,
        tarfile.TarFile(
            fileobj=compressed,
            mode="w",
            format=tarfile.GNU_FORMAT,
            dereference=True,
        ) as archive,
    ):
        for file in files:
            info = tarfile.TarInfo(name=file.relative)
            info.size = file.resolved.stat().st_size
            info.mtime = _TAR_MTIME
            info.mode = _TAR_MODE
            info.type = tarfile.REGTYPE
            info.uid = _TAR_UID
            info.gid = _TAR_GID
            info.uname = ""
            info.gname = ""
            with file.resolved.open("rb") as handle:
                archive.addfile(info, handle)
    emit(
        f"  wrote {tarball_path} "
        f"({tarball_path.stat().st_size:,} bytes, {len(files)} member(s))"
    )
    return tarball_path


# ---------------------------------------------------------------------------
# Phase 4 — extract what we are about to publish, and verify it
# ---------------------------------------------------------------------------


def extract_and_verify(
    tarball_path: Path, *, manifest_path: Path
) -> VerificationResult:
    """Extract *tarball_path* into a throwaway cache and run the real verifier.

    The proof that the dereferenced tar carries real bytes, and the same
    function the supervised run uses on a **freshly pulled** artifact — which
    is the Independent Test this story is graded on. It calls
    :func:`model_fetcher.verify_weights`, not a re-implementation: if the
    artifact satisfies this, it satisfies the running service.

    ``filter="data"`` explicitly, because Python 3.12 still defaults to the
    permissive filter and this is the extraction shape US-004 inherits.
    """
    pin = read_manifest_pin(manifest_path)
    if pin is None:
        raise VendorError(
            f"{manifest_path} does not pin a verifiable file set, so there is "
            f"nothing to check the tarball against — run `--step manifest` first"
        )

    with tempfile.TemporaryDirectory(prefix="forage-selfcheck-") as workspace:
        cache_root = Path(workspace)
        snapshot = snapshot_path(cache_root, pin.model_id, pin.revision)
        snapshot.mkdir(parents=True)
        with tarfile.open(tarball_path, "r:gz") as archive:
            archive.extractall(snapshot, filter="data")
        return verify_weights(cache_root, manifest_path=manifest_path)


# ---------------------------------------------------------------------------
# Phase 5 — the push
# ---------------------------------------------------------------------------


def mirror_ref(revision: str, *, repository: str = MIRROR_REPOSITORY) -> str:
    """The artifact reference for *revision* — leg three of the triple lock.

    The tag **is** the revision sha. That is what makes
    ``DEFAULT_MODEL_REVISION`` == the committed manifest's revision == the
    mirror tag a single fact rather than three that have to be kept in step by
    hand.
    """
    return f"{repository}:{revision}"


def registry_of(repository: str) -> str:
    """The registry host of an OCI repository reference."""
    return repository.split("/", 1)[0]


def require_oras() -> str:
    """Return the path to the ``oras`` binary, or fail with install guidance.

    Detected rather than assumed: the alternative is a ``FileNotFoundError``
    from deep inside a subprocess call, three phases into a supervised run
    that has already spent a 270 MiB download.
    """
    found = shutil.which("oras")
    if found is None:
        raise VendorError(_ORAS_INSTALL_GUIDANCE)
    return found


def push_argv(
    *, oras: str, ref: str, tarball_name: str, revision: str
) -> tuple[str, ...]:
    """The exact ``oras push`` argv. Contains no credential, by construction."""
    return (
        oras,
        "push",
        ref,
        f"{tarball_name}:{LAYER_MEDIA_TYPE}",
        "--artifact-type",
        ARTIFACT_TYPE,
        "--annotation",
        f"org.opencontainers.image.revision={revision}",
        "--annotation",
        f"org.opencontainers.image.source=https://huggingface.co/{MODEL_ID}",
    )


def push_artifact(
    *,
    tarball_path: Path,
    revision: str,
    repository: str = MIRROR_REPOSITORY,
    username: str | None,
    token: str | None,
    run: CommandRunner = run_command,
) -> str:
    """Log in, push the artifact tagged by revision sha, log out. Returns the ref.

    The credential travels on **stdin** to ``oras login``: an argument would
    be readable from ``ps`` for the life of the process and would land in
    shell history. The logout in the ``finally`` is not tidiness — it keeps
    the credential from outliving the run in the machine's registry
    configuration.
    """
    if not username or not token:
        raise VendorError(
            f"{GHCR_USER_ENV_VAR} and {GHCR_TOKEN_ENV_VAR} must both be set to "
            f"push. The token needs `write:packages`; see docs/weights.md for "
            f"the least-privilege setup."
        )

    oras = require_oras()
    registry = registry_of(repository)
    ref = mirror_ref(revision, repository=repository)

    login = (oras, "login", registry, "--username", username, "--password-stdin")
    _run_checked(run, login, stdin=token, secrets=(token,), what="oras login")
    try:
        argv = push_argv(
            oras=oras,
            ref=ref,
            tarball_name=tarball_path.name,
            revision=revision,
        )
        emit(f"  {' '.join(argv)}")
        # Run from the tarball's directory so the layer's title annotation is
        # the bare filename rather than whatever local path this run used.
        _run_checked(
            run,
            argv,
            stdin=None,
            secrets=(token,),
            what="oras push",
            cwd=tarball_path.parent,
        )
    finally:
        _run_checked(
            run,
            (oras, "logout", registry),
            stdin=None,
            secrets=(token,),
            what="oras logout",
            tolerate_failure=True,
        )
    emit(f"  pushed {ref}")
    return ref


def _run_checked(
    run: CommandRunner,
    argv: Sequence[str],
    *,
    stdin: str | None,
    secrets: Sequence[str | None],
    what: str,
    cwd: Path | None = None,
    tolerate_failure: bool = False,
) -> CommandResult:
    """Run *argv*, redacting every captured stream before it can be printed."""
    result = run(argv, stdin=stdin, cwd=cwd)
    if result.returncode != 0 and not tolerate_failure:
        detail = redact(result.stderr.strip() or result.stdout.strip(), secrets)
        raise VendorError(f"{what} failed (exit {result.returncode}): {detail}")
    return result


# ---------------------------------------------------------------------------
# Phase 6 — the pushed package must be private
# ---------------------------------------------------------------------------


def package_api_paths(repository: str) -> tuple[str, ...]:
    """The GitHub API paths that could describe *repository*'s package.

    Two, because ``ghcr.io/<owner>/<name>`` does not say whether ``<owner>``
    is an organisation or a user, and the two live under different API roots.
    """
    parts = repository.split("/")
    if len(parts) < 3:
        raise VendorError(
            f"{repository} is not a `<registry>/<owner>/<name>` reference"
        )
    owner, package = parts[1], "/".join(parts[2:])
    return (
        f"{GITHUB_API_ROOT}/orgs/{owner}/packages/{PACKAGE_TYPE}/{package}",
        f"{GITHUB_API_ROOT}/users/{owner}/packages/{PACKAGE_TYPE}/{package}",
    )


def verify_package_is_private(
    *,
    repository: str = MIRROR_REPOSITORY,
    token: str | None,
    fetch: HttpFetcher = fetch_url,
) -> str:
    """Assert the pushed package is private, and return its visibility.

    GHCR creates a package on first push with the visibility inherited from
    the account's default, which is not a guarantee — so this is checked after
    every push rather than assumed once. A public weights mirror is not a
    security incident (the Llama licence permits redistribution with
    attribution), but it makes us a weights distributor, which is a role this
    project deliberately declined; see docs/weights.md.
    """
    if not token:
        raise VendorError(
            f"{GITHUB_TOKEN_ENV_VAR} is not set; the visibility of the pushed "
            f"package cannot be confirmed. A read-only token is enough."
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "forage-vendor-weights",
    }
    last_status = 0
    for url in package_api_paths(repository):
        status, body = fetch(url, headers)
        last_status = status
        if status == 404:
            continue
        if status != 200:
            raise VendorError(
                f"the GitHub API answered {status} for {url}; visibility "
                f"unconfirmed. A `read:packages` token is required."
            )
        visibility = _visibility_of(body, url)
        if visibility != REQUIRED_VISIBILITY:
            raise VendorError(
                f"{repository} is {visibility!r}, not {REQUIRED_VISIBILITY!r}. "
                f"Make the package private in its GitHub package settings "
                f"before anything else uses this artifact."
            )
        return visibility

    raise VendorError(
        f"no package found for {repository} (last status {last_status}). If the "
        f"push has not run yet, that is expected; if it has, the token may lack "
        f"`read:packages` for this owner."
    )


def _visibility_of(body: bytes, url: str) -> str:
    """Pull ``visibility`` out of a package response, or fail loudly."""
    try:
        parsed: object = json.loads(body)
    except json.JSONDecodeError:
        raise VendorError(f"the GitHub API returned non-JSON for {url}") from None
    if not isinstance(parsed, dict):
        raise VendorError(f"the GitHub API returned an unexpected shape for {url}")
    visibility = cast(dict[str, Any], parsed).get("visibility")
    if not isinstance(visibility, str):
        raise VendorError(
            f"the GitHub API response for {url} carries no `visibility` field, "
            f"so the package cannot be confirmed private"
        )
    return visibility


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VendorPlan:
    """Everything one invocation was asked to do."""

    steps: tuple[str, ...]
    revision: str
    cache_root: Path
    manifest_path: Path
    tarball_path: Path
    repository: str
    dry_run: bool

    @property
    def snapshot_dir(self) -> Path:
        """Where the pinned snapshot lives under :attr:`cache_root`."""
        return snapshot_path(self.cache_root, MODEL_ID, self.revision)


def default_work_dir() -> Path:
    """The out-of-tree scratch directory a vendoring run uses by default."""
    return Path(tempfile.gettempdir()) / DEFAULT_WORK_DIRNAME


def build_plan(args: argparse.Namespace) -> VendorPlan:
    """Turn parsed arguments into the plan the phases read."""
    revision = cast(str, args.revision)
    work_dir = Path(cast(str, args.work_dir)) if args.work_dir else default_work_dir()
    cache_root = (
        Path(cast(str, args.cache_root))
        if args.cache_root
        else work_dir / "model-cache"
    )
    tarball_path = (
        Path(cast(str, args.tarball))
        if args.tarball
        else work_dir / TARBALL_NAME_TEMPLATE.format(revision=revision)
    )
    step = cast(str, args.step)
    steps = _STEPS if step == "all" else (step,)
    return VendorPlan(
        steps=steps,
        revision=revision,
        cache_root=cache_root,
        manifest_path=Path(cast(str, args.manifest)),
        tarball_path=tarball_path,
        repository=cast(str, args.repository),
        dry_run=bool(args.dry_run),
    )


def describe(plan: VendorPlan) -> tuple[str, ...]:
    """The plan, printed before anything happens. No secret appears here."""
    return (
        f"model      {MODEL_ID}",
        f"revision   {plan.revision}",
        f"mirror     {mirror_ref(plan.revision, repository=plan.repository)}",
        f"cache      {plan.cache_root}",
        f"snapshot   {plan.snapshot_dir}",
        f"manifest   {plan.manifest_path}",
        f"tarball    {plan.tarball_path}",
        f"steps      {', '.join(plan.steps)}",
        "dry run    "
        + (
            "yes — nothing will be written to a network"
            if plan.dry_run
            else "no — the push and the visibility check will run"
        ),
    )


def run_plan(plan: VendorPlan, *, environ: Mapping[str, str] | None = None) -> None:
    """Execute the plan's steps in order. Raises :class:`VendorError` to stop."""
    env = os.environ if environ is None else environ
    for line in describe(plan):
        emit(line)

    if "download" in plan.steps:
        emit("[1/6] download")
        download_snapshot(
            revision=plan.revision,
            cache_root=plan.cache_root,
            token=env.get(HF_TOKEN_ENV_VAR),
        )

    if "manifest" in plan.steps:
        emit("[2/6] manifest")
        document = generate_manifest(
            plan.snapshot_dir, model_id=MODEL_ID, revision=plan.revision
        )
        for line in manifest_diff(read_manifest_document(plan.manifest_path), document):
            emit(f"  {line}")
        write_manifest(document, plan.manifest_path)
        emit(f"  wrote {plan.manifest_path}")

    if "tar" in plan.steps:
        emit("[3/6] tar")
        build_tarball(plan.snapshot_dir, plan.tarball_path)

    if "selfcheck" in plan.steps:
        emit("[4/6] selfcheck")
        result = extract_and_verify(plan.tarball_path, manifest_path=plan.manifest_path)
        if not result.ok:
            raise VendorError(
                "the tarball does not satisfy the committed manifest: "
                + ", ".join(str(failure) for failure in result.failures)
            )
        emit("  the extracted tarball verifies against the committed manifest")

    if "push" in plan.steps:
        emit("[5/6] push")
        if plan.dry_run:
            argv = push_argv(
                oras="oras",
                ref=mirror_ref(plan.revision, repository=plan.repository),
                tarball_name=plan.tarball_path.name,
                revision=plan.revision,
            )
            emit(f"  dry run — would run: {' '.join(argv)}")
        else:
            push_artifact(
                tarball_path=plan.tarball_path,
                revision=plan.revision,
                repository=plan.repository,
                username=env.get(GHCR_USER_ENV_VAR),
                token=env.get(GHCR_TOKEN_ENV_VAR),
            )

    if "visibility" in plan.steps:
        emit("[6/6] visibility")
        if plan.dry_run:
            for url in package_api_paths(plan.repository):
                emit(f"  dry run — would GET {url}")
        else:
            visibility = verify_package_is_private(
                repository=plan.repository,
                token=env.get(GITHUB_TOKEN_ENV_VAR) or env.get(GHCR_TOKEN_ENV_VAR),
            )
            emit(f"  {plan.repository} is {visibility}")


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every credential arrives by environment variable, never a flag."""
    parser = argparse.ArgumentParser(
        prog="vendor_weights",
        description=(
            "Vendor the pinned PromptGuard weights to the private GHCR mirror "
            "and generate the committed weights manifest."
        ),
        epilog=(
            f"Credentials come from the environment: {HF_TOKEN_ENV_VAR} for the "
            f"download, {GHCR_USER_ENV_VAR}/{GHCR_TOKEN_ENV_VAR} for the push, "
            f"{GITHUB_TOKEN_ENV_VAR} for the visibility check. See docs/weights.md."
        ),
    )
    parser.add_argument(
        "--step",
        choices=("all", *_STEPS),
        default="all",
        help="Run one phase instead of the whole sequence (default: all)",
    )
    parser.add_argument(
        "--revision",
        default=DEFAULT_MODEL_REVISION,
        help="The upstream commit sha to vendor (default: the committed pin)",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help=(
            f"Scratch directory for the cache and tarball "
            f"(default: {default_work_dir()})"
        ),
    )
    parser.add_argument(
        "--cache-root",
        default=None,
        help=(
            "Override the $HF_HOME-shaped cache root (default: <work-dir>/model-cache)"
        ),
    )
    parser.add_argument(
        "--tarball",
        default=None,
        help=(
            "Override the tarball path — also how `--step selfcheck` "
            "checks a freshly pulled artifact"
        ),
    )
    parser.add_argument(
        "--manifest",
        default=str(MANIFEST_PATH),
        help=f"The manifest to generate and verify against (default: {MANIFEST_PATH})",
    )
    parser.add_argument(
        "--repository",
        default=MIRROR_REPOSITORY,
        help=f"The OCI repository to push to (default: {MIRROR_REPOSITORY})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything that only reads; stop before the push and the API call",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    args = build_parser().parse_args(argv)
    plan = build_plan(args)
    try:
        run_plan(plan)
    except VendorError as exc:
        emit(f"vendor_weights FAILED: {exc}")
        return 1
    emit("vendor_weights OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
