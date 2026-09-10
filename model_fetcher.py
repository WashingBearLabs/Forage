"""Weight acquisition for the model cache: fetch, verify, load.

Forage's PromptGuard weights used to be baked into the image at build time,
behind a build ARG that leaked the Hugging Face token into the layer history
(`forage-ci-and-image` US-003 deleted that path). They arrive at **runtime**
instead — from Hugging Face, or from our own OCI mirror — and a runtime fetch
opens a supply-chain door the bake never had: whatever lands in the cache
volume is what `from_pretrained` opens, and `from_pretrained` will happily open
a pickle.

This module is the gate every source passes through. It is deliberately the
*whole* answer to "are these the bytes we pinned?", so the HF path
(`feature-forage-model-bootstrap` US-001), the mirror path (US-004) and the
warm-start path (US-005) all call the same function rather than each growing
their own idea of "verified".

**What verification means here**

1. **Fail-closed on the manifest itself.** A missing, empty, unparseable or
   schema-invalid `weights_manifest.json` is a verification FAILURE, never
   "nothing to verify". The one thing worse than an unverified weight set is
   an unverified weight set that reports as verified.
2. **Exact set.** Every file the manifest names must be present with the
   pinned size and sha256, and the snapshot must contain *nothing else*. A
   missing file, an extra file and a hash mismatch are the same class of
   answer: refuse.
3. **Safetensors only.** :data:`ALLOWED_SUFFIXES` is an allowlist, applied to
   the manifest's own entries as well as to what is on disk, so a manifest
   that blesses a `pytorch_model.bin` cannot be honoured even if someone
   generates one. This is the RCE closure — `torch.load` never gets a file to
   open — and it is belt-and-braces with `use_safetensors=True` at the loader
   (`promptguard/classifier.py`).
4. **The bytes the loader opens.** Hugging Face's cache stores real content
   under `blobs/` and fills `snapshots/<revision>/` with symlinks into it, so
   the walk resolves symlinks and hashes the resolved file — and refuses any
   link that resolves outside the model's own cache directory, which is how a
   hostile tarball would try to reach `/etc` on US-004's extraction path.

**Quarantine.** A snapshot that fails verification is moved out of the tree the
loader scans, to `<cache_root>/quarantine/`, and **bounded to one generation**:
the previous quarantine is deleted first. Repeated ~270 MiB quarantines would
fill the reference container's volume, which converts a corrupt download into
an outage. A *manifest* failure never quarantines anything — the weights may be
perfectly good and our manifest broken, and destroying a 270 MiB download over
our own bug is not a trade worth making.

The whole model directory moves, not just `snapshots/<revision>/`: the real
bytes live in `blobs/`, and `huggingface_hub` treats a blob whose filename it
already has as cached without re-hashing it. Leaving the blobs behind would let
the next fetch re-link the same corrupt bytes and quarantine them again,
forever.

**Acquisition** (US-001). :func:`acquire_and_load` is the pipeline the service
starts at boot: verify what the cache already holds → fetch the pinned revision
from Hugging Face if it cannot satisfy the pin → verify the download → load.
Four properties are load-bearing:

1. **It blocks, and it is meant to be called off the event loop.** The caller
   is `retrieval_app.lifespan`, through
   `asyncio.create_task(asyncio.to_thread(...))` — a task, not an `await`,
   because lifespan startup must *yield immediately*: uvicorn serves nothing
   until it returns, and the compose healthcheck (10 s x 5 retries, no
   `start_period`) would restart-loop the container while a ~270 MiB download
   ran.
2. **The revision is pinned**, to :data:`DEFAULT_MODEL_REVISION` unless
   :data:`MODEL_REVISION_ENV_VAR` overrides it. An unpinned `main` turns any
   upstream commit into "corruption" on the next start.
3. **The cache tree is `$HF_HOME/hub`**, never `$HF_HOME` and never a
   `local_dir`. Both the download and the load are handed that directory
   explicitly (:func:`hub_cache_dir`), so the bytes that land are the bytes
   verified and the bytes opened — no reliance on which environment variable
   either library sampled at import time.
4. **The token is optional and never logged.** No token is a supported
   degraded mode, not an error: the fetch is skipped with a warning and
   `/health` keeps saying `promptguard_unavailable`. Failures are reported
   through a closed reason vocabulary (`http_401`, `timeout`, …) because
   `huggingface_hub`'s exceptions carry request context and `HF_TOKEN` must
   never reach a log line.

test_mapping:
  model_fetcher.py: tests/test_model_fetcher.py
  weights_manifest.json: tests/test_model_fetcher.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, cast

from promptguard.classifier import MODEL_ID

logger = logging.getLogger(__name__)

# The production manifest. Injectable at the call below *for fixtures only* —
# every real call site uses this default, which is why it is a constant here
# rather than an environment variable.
MANIFEST_PATH: Final = Path(__file__).resolve().parent / "weights_manifest.json"

# `HF_HUB_CACHE` defaults to `$HF_HOME/hub`, and that — not `$HF_HOME` — is the
# root `from_pretrained` and `snapshot_download` resolve a repo under. The
# Dockerfile sets `HF_HOME=/app/model-cache`, so the canonical snapshot is
# `/app/model-cache/hub/models--meta-llama--Llama-Prompt-Guard-2-22M/snapshots/<rev>/`.
HUB_DIRNAME: Final = "hub"

# Deliberately a sibling of `hub/`, never a child: the loader scans the hub
# tree, and a quarantine inside it is not a quarantine.
QUARANTINE_DIRNAME: Final = "quarantine"

# The pinned upstream revision, measured on the live sidecar. `snapshot_download`
# and `from_pretrained` both take it, the committed manifest repeats it, and
# US-003 vendors the mirror artifact under exactly this tag — one value, three
# places, locked together by test. Bumping it is a deliberate re-vendor:
# revision + manifest + mirror tag move in one commit.
DEFAULT_MODEL_REVISION: Final = "11614a155199674a0a95e6602d6ab0417b790ed0"

# The three environment variables this module reads. Named constants rather
# than inline literals so `tests/test_model_fetcher.py` can assert the whole
# set from the AST — the manifest path, notably, is *not* among them.
MODEL_REVISION_ENV_VAR: Final = "FORAGE_MODEL_REVISION"
CACHE_ROOT_ENV_VAR: Final = "HF_HOME"
HF_TOKEN_ENV_VAR: Final = "HF_TOKEN"

# Where the weights live when nothing says otherwise — the Dockerfile's
# `ENV HF_HOME=/app/model-cache`, restated so a bare `python -c` run outside
# the image resolves the same tree the image does.
DEFAULT_CACHE_ROOT: Final = Path("/app/model-cache")

# Acquisition sources, named once. US-004 adds `mirror`.
SOURCE_HUGGINGFACE: Final = "huggingface"

# The format allowlist. `.safetensors` is the weights format that cannot
# execute code on load; the rest are the inert tokenizer/config files a
# sequence classifier needs (`config.json`, `tokenizer.json`,
# `tokenizer_config.json`, `special_tokens_map.json`, `vocab.txt`,
# `merges.txt`, sentencepiece's `*.model`). Anything else — `pytorch_model.bin`
# and every other pickle carrier, but also `README.md` and `.gitattributes` —
# is refused.
ALLOWED_SUFFIXES: Final = frozenset({".safetensors", ".json", ".txt", ".model"})

# The same allowlist in `huggingface_hub`'s `allow_patterns` shape. US-001 and
# US-003 must pass this to `snapshot_download` — a plain snapshot download also
# pulls `README.md` and `.gitattributes`, which this verifier then refuses as
# extra files. One constant, so the fetch and the verification cannot disagree.
ALLOW_PATTERNS: Final = tuple(sorted(f"*{suffix}" for suffix in ALLOWED_SUFFIXES))

# Closed reason vocabulary. Failures are reported as these codes plus a path,
# never as a formatted exception message — same discipline `cache.py` applies
# to its connection failures, for the same reason: a log line is a wire format.
REASON_MANIFEST_MISSING: Final = "manifest_missing"
REASON_MANIFEST_UNREADABLE: Final = "manifest_unreadable"
REASON_MANIFEST_EMPTY: Final = "manifest_empty"
REASON_MANIFEST_UNPARSEABLE: Final = "manifest_unparseable"
REASON_MANIFEST_INVALID: Final = "manifest_invalid"
REASON_MANIFEST_DISALLOWED_FORMAT: Final = "manifest_disallowed_format"
REASON_SNAPSHOT_MISSING: Final = "snapshot_missing"
REASON_FILE_MISSING: Final = "file_missing"
REASON_FILE_EXTRA: Final = "file_extra"
REASON_DISALLOWED_FORMAT: Final = "disallowed_format"
REASON_SIZE_MISMATCH: Final = "size_mismatch"
REASON_HASH_MISMATCH: Final = "hash_mismatch"
REASON_UNREADABLE_FILE: Final = "unreadable_file"
REASON_SYMLINK_ESCAPE: Final = "symlink_escape"
REASON_DISALLOWED_ENTRY: Final = "disallowed_entry"

# Manifest-level reasons never quarantine: the weights may be fine and the
# manifest broken.
_MANIFEST_REASONS: Final = frozenset(
    {
        REASON_MANIFEST_MISSING,
        REASON_MANIFEST_UNREADABLE,
        REASON_MANIFEST_EMPTY,
        REASON_MANIFEST_UNPARSEABLE,
        REASON_MANIFEST_INVALID,
        REASON_MANIFEST_DISALLOWED_FORMAT,
    }
)

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
# HF revisions are pinned by commit sha, never by a branch name: a movable
# `main` turns any upstream commit into "corruption" on the next start. The
# shape is enforced here so the manifest cannot carry a movable pin, and
# because the value is interpolated into a filesystem path.
_REVISION_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9._-]+)?$")
_RELATIVE_PATH_SEGMENT_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_HASH_CHUNK_BYTES: Final = 1024 * 1024
_MAX_MANIFEST_BYTES: Final = 1024 * 1024


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class ModelMetrics:
    """In-process counters for weight acquisition, exported by ``/metrics``.

    Mirrors :class:`cache.CacheMetrics`. ``fetch_failures`` is owned by the
    fetch paths (US-001/US-004) and stays at zero until they land;
    ``fetch_in_progress`` is what lets an operator — and Poppy's deploy
    readiness wait — tell "downloading 270 MiB" apart from "wedged".
    """

    fetch_failures: int = 0
    verify_failures: int = 0
    quarantines: int = 0
    fetch_in_progress: bool = False

    def record_fetch_failure(self) -> None:
        """Record one failed acquisition attempt against a source."""
        self.fetch_failures += 1

    def record_verify_failure(self) -> None:
        """Record one weight set refused by :func:`verify_weights`."""
        self.verify_failures += 1

    def record_quarantine(self) -> None:
        """Record one weight set moved out of the loader's scan tree."""
        self.quarantines += 1


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One pinned file: its snapshot-relative path, sha256 and size."""

    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class WeightsManifest:
    """The committed pin: which model, which revision, which exact bytes."""

    model_id: str
    revision: str
    entries: tuple[ManifestEntry, ...]

    @property
    def total_bytes(self) -> int:
        """Total pinned size, the basis for US-004's staging-space bound."""
        return sum(entry.size for entry in self.entries)


@dataclass(frozen=True, slots=True)
class VerificationFailure:
    """One reason a weight set was refused, and the file it applies to.

    ``path`` is snapshot-relative, or empty for a manifest-level failure.
    """

    reason: str
    path: str = ""

    def __str__(self) -> str:
        """Render as ``reason`` or ``reason:path`` for a single log line."""
        return f"{self.reason}:{self.path}" if self.path else self.reason


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """The outcome of one :func:`verify_weights` call.

    ``ok`` is the only thing a caller may act on: a non-empty ``failures``
    with ``ok`` true is not a state this module can produce.
    """

    ok: bool
    failures: tuple[VerificationFailure, ...] = ()
    manifest: WeightsManifest | None = None
    snapshot_dir: Path | None = None
    quarantined_to: Path | None = None

    @property
    def reasons(self) -> tuple[str, ...]:
        """The distinct failure reason codes, in first-seen order."""
        seen: dict[str, None] = {}
        for failure in self.failures:
            seen[failure.reason] = None
        return tuple(seen)

    @property
    def manifest_failure(self) -> bool:
        """Whether the refusal is our manifest's fault, not the weights'.

        The distinction the quarantine rule already makes, exposed for the
        acquisition path: a manifest that cannot bless *any* file set will not
        be fixed by downloading ~270 MiB again, so :func:`acquire_and_load`
        stops rather than fetching bytes it could never accept.
        """
        return _is_manifest_failure(self.failures)


def _is_manifest_failure(failures: tuple[VerificationFailure, ...]) -> bool:
    """Whether any failure is about the manifest rather than the weight set."""
    return any(failure.reason in _MANIFEST_REASONS for failure in failures)


def is_allowed_filename(name: str) -> bool:
    """Whether *name* is a format this service will admit into the cache.

    Public because US-003's vendoring script must apply the identical rule at
    manifest-*generation* time: a manifest that blesses a pickle must be
    ungeneratable, not merely unusable.
    """
    return Path(name).suffix in ALLOWED_SUFFIXES


def repo_dirname(model_id: str) -> str:
    """Return Hugging Face's on-disk directory name for *model_id*."""
    return "models--" + model_id.replace("/", "--")


def snapshot_path(cache_root: Path | str, model_id: str, revision: str) -> Path:
    """Locate the snapshot directory ``from_pretrained`` resolves.

    *cache_root* is ``$HF_HOME`` (the Dockerfile's ``/app/model-cache``), not
    ``$HF_HUB_CACHE`` — the ``hub/`` level is added here so no call site has to
    remember it. A locator, not a verifier: nothing here reads file contents.
    """
    return (
        Path(cache_root) / HUB_DIRNAME / repo_dirname(model_id) / "snapshots" / revision
    )


def hub_cache_dir(cache_root: Path | str) -> Path:
    """Return ``$HF_HUB_CACHE`` — the directory both fetch and load are given.

    ``huggingface_hub``'s ``cache_dir`` and ``transformers``' ``cache_dir`` are
    the *hub* cache (``$HF_HOME/hub``), one level below the volume. Passing
    ``$HF_HOME`` writes a repo tree one directory too high, and the loader then
    reads a different one — a failure that presents as "the download worked and
    the model still isn't there". Passing it explicitly, from one helper, also
    removes the dependence on which value either library sampled from the
    environment when it was imported.
    """
    return Path(cache_root) / HUB_DIRNAME


def quarantine_root(cache_root: Path | str) -> Path:
    """Where refused weight sets are moved. A sibling of ``hub/``, never a child."""
    return Path(cache_root) / QUARANTINE_DIRNAME


# ---------------------------------------------------------------------------
# Manifest loading (fail-closed)
# ---------------------------------------------------------------------------


def _entry_from(raw: object, index: int) -> ManifestEntry | VerificationFailure:
    """Validate one manifest entry, or say why it is not one."""
    invalid = VerificationFailure(REASON_MANIFEST_INVALID, f"files[{index}]")
    if not isinstance(raw, dict):
        return invalid
    entry = cast("dict[str, Any]", raw)
    path = entry.get("path")
    sha256 = entry.get("sha256")
    size = entry.get("size")
    if not isinstance(path, str) or not _is_safe_relative_path(path):
        return invalid
    if not isinstance(sha256, str) or _SHA256_RE.match(sha256) is None:
        return invalid
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return invalid
    if not is_allowed_filename(path):
        return VerificationFailure(REASON_MANIFEST_DISALLOWED_FORMAT, path)
    return ManifestEntry(path=path, sha256=sha256, size=size)


def _is_safe_relative_path(path: str) -> bool:
    """Whether *path* is a plain relative POSIX path inside the snapshot.

    Rejects absolute paths, backslashes, empty segments, ``.`` and ``..`` — a
    manifest is committed data, but it is also the thing that decides which
    filesystem paths get opened.
    """
    if not path or path.startswith("/") or "\\" in path:
        return False
    segments = path.split("/")
    return all(
        _RELATIVE_PATH_SEGMENT_RE.match(segment) is not None for segment in segments
    )


def _load_manifest(
    manifest_path: Path,
) -> tuple[WeightsManifest | None, tuple[VerificationFailure, ...]]:
    """Read and validate the manifest. Every failure mode is a refusal."""
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, (VerificationFailure(REASON_MANIFEST_MISSING),)
    except (OSError, UnicodeDecodeError):
        return None, (VerificationFailure(REASON_MANIFEST_UNREADABLE),)

    if len(raw_text) > _MAX_MANIFEST_BYTES:
        return None, (VerificationFailure(REASON_MANIFEST_INVALID, "size"),)
    if not raw_text.strip():
        return None, (VerificationFailure(REASON_MANIFEST_EMPTY),)

    try:
        parsed: object = json.loads(raw_text)
    except json.JSONDecodeError:
        return None, (VerificationFailure(REASON_MANIFEST_UNPARSEABLE),)

    if not isinstance(parsed, dict):
        return None, (VerificationFailure(REASON_MANIFEST_INVALID, "root"),)
    document = cast("dict[str, Any]", parsed)

    model_id = document.get("model_id")
    revision = document.get("revision")
    files = document.get("files")
    if not isinstance(model_id, str) or _MODEL_ID_RE.match(model_id) is None:
        return None, (VerificationFailure(REASON_MANIFEST_INVALID, "model_id"),)
    if not isinstance(revision, str) or _REVISION_RE.match(revision) is None:
        return None, (VerificationFailure(REASON_MANIFEST_INVALID, "revision"),)
    if not isinstance(files, list):
        return None, (VerificationFailure(REASON_MANIFEST_INVALID, "files"),)
    entries_raw = cast("list[object]", files)
    if not entries_raw:
        # A manifest that pins nothing would verify an empty snapshot — the
        # "nothing to verify" answer this module exists to refuse.
        return None, (VerificationFailure(REASON_MANIFEST_EMPTY, "files"),)

    entries: list[ManifestEntry] = []
    failures: list[VerificationFailure] = []
    for index, raw_entry in enumerate(entries_raw):
        result = _entry_from(raw_entry, index)
        if isinstance(result, VerificationFailure):
            failures.append(result)
        else:
            entries.append(result)
    if failures:
        return None, tuple(failures)

    seen: set[str] = set()
    for entry in entries:
        if entry.path in seen:
            return None, (VerificationFailure(REASON_MANIFEST_INVALID, entry.path),)
        seen.add(entry.path)

    return (
        WeightsManifest(
            model_id=model_id,
            revision=revision,
            entries=tuple(entries),
        ),
        (),
    )


# ---------------------------------------------------------------------------
# Snapshot walk (symlink-resolving, containment-checked)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Walk:
    """Files found under a snapshot, plus the structural refusals hit on the way."""

    files: dict[str, Path] = field(default_factory=dict[str, Path])
    failures: list[VerificationFailure] = field(
        default_factory=list[VerificationFailure]
    )


def _is_within(candidate: Path, container: Path) -> bool:
    """Whether *candidate* is *container* or lives beneath it."""
    return candidate == container or container in candidate.parents


def _walk_snapshot(snapshot_dir: Path, containment_root: Path) -> _Walk:
    """Collect snapshot-relative paths → the resolved file each one opens.

    Directory symlinks are refused outright rather than followed: Hugging Face
    never creates one, and following one is how a walk both misses files and
    finds a cycle. File symlinks are resolved and required to land inside
    *containment_root* (the model's own cache directory) — a link out of the
    tree is the shape a hostile mirror tarball takes.
    """
    walk = _Walk()
    # Resolved, because the containment test compares fully-resolved targets
    # and the cache root itself may be reached through a symlinked component
    # (``/tmp`` on macOS is the everyday example).
    _scan_into(snapshot_dir, "", walk, containment_root.resolve())
    return walk


def _scan_into(
    directory: Path,
    prefix: str,
    walk: _Walk,
    containment_root: Path,
) -> None:
    """Recurse one directory level, appending to *walk*."""
    try:
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
    except OSError:
        walk.failures.append(
            VerificationFailure(REASON_UNREADABLE_FILE, prefix.rstrip("/"))
        )
        return

    for entry in children:
        relative = f"{prefix}{entry.name}"
        entry_path = Path(entry.path)
        if entry.is_symlink():
            try:
                resolved = entry_path.resolve(strict=True)
            except OSError:
                walk.failures.append(VerificationFailure(REASON_FILE_MISSING, relative))
                continue
            if not _is_within(resolved, containment_root):
                walk.failures.append(
                    VerificationFailure(REASON_SYMLINK_ESCAPE, relative)
                )
                continue
            if not resolved.is_file():
                walk.failures.append(
                    VerificationFailure(REASON_DISALLOWED_ENTRY, relative)
                )
                continue
            walk.files[relative] = resolved
            continue
        if entry.is_dir(follow_symlinks=False):
            _scan_into(entry_path, f"{relative}/", walk, containment_root)
            continue
        if entry.is_file(follow_symlinks=False):
            walk.files[relative] = entry_path
            continue
        walk.failures.append(VerificationFailure(REASON_DISALLOWED_ENTRY, relative))


def _sha256_and_size(path: Path) -> tuple[str, int] | None:
    """Hash the bytes the loader would open; ``None`` if they cannot be read."""
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                size += len(chunk)
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest(), size


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


def _quarantine(cache_root: Path, repo_dir: Path, metrics: ModelMetrics) -> Path | None:
    """Move *repo_dir* out of the loader's scan tree, one generation only."""
    destination_root = quarantine_root(cache_root)
    try:
        if destination_root.exists():
            # Bounded to one generation: at ~270 MiB a set, keeping the last
            # two would fill the reference container's volume and turn a
            # corrupt download into an outage.
            shutil.rmtree(destination_root)
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / repo_dir.name
        shutil.move(str(repo_dir), str(destination))
    except OSError:
        logger.error(
            "weights_quarantine_failed — could not move the refused weight set "
            "out of %s; it must not be loaded",
            repo_dir,
        )
        return None
    metrics.record_quarantine()
    logger.error("weights_quarantined — refused weight set moved to %s", destination)
    return destination


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


def verify_weights(
    cache_root: Path | str,
    *,
    manifest_path: Path | str = MANIFEST_PATH,
    metrics: ModelMetrics | None = None,
) -> VerificationResult:
    """Verify the cached weight set against the committed manifest.

    This is the **single** verification entry point. Every acquisition path
    calls it with the same arguments: the HF path after a download (US-001),
    the mirror path after a safe extraction (US-004), and the warm start
    before ``local_files_only`` load (US-005). *manifest_path* is injectable
    for fixtures; production always takes the :data:`MANIFEST_PATH` default.

    *cache_root* is ``$HF_HOME`` — the directory holding ``hub/``, not
    ``hub/`` itself.

    On success the returned :class:`VerificationResult` has ``ok`` true and no
    failures. On failure it carries every reason found (not just the first),
    and — for failures of the *snapshot* rather than of the manifest — the
    offending model directory has been moved to ``<cache_root>/quarantine/``
    and ``quarantined_to`` records where.
    """
    metrics = metrics if metrics is not None else ModelMetrics()
    root = Path(cache_root)

    manifest, manifest_failures = _load_manifest(Path(manifest_path))
    if manifest is None:
        return _refuse(manifest_failures, metrics, manifest=None)

    repo_dir = root / HUB_DIRNAME / repo_dirname(manifest.model_id)
    snapshot_dir = snapshot_path(root, manifest.model_id, manifest.revision)
    if not snapshot_dir.is_dir():
        return _refuse(
            (VerificationFailure(REASON_SNAPSHOT_MISSING, manifest.revision),),
            metrics,
            manifest=manifest,
            snapshot_dir=snapshot_dir,
        )

    walk = _walk_snapshot(snapshot_dir, repo_dir)
    failures = list(walk.failures)
    expected = {entry.path: entry for entry in manifest.entries}

    for relative in sorted(set(walk.files) - set(expected)):
        reason = (
            REASON_FILE_EXTRA
            if is_allowed_filename(relative)
            else REASON_DISALLOWED_FORMAT
        )
        failures.append(VerificationFailure(reason, relative))

    for relative in sorted(expected):
        found = walk.files.get(relative)
        if found is None:
            failures.append(VerificationFailure(REASON_FILE_MISSING, relative))
            continue
        hashed = _sha256_and_size(found)
        if hashed is None:
            failures.append(VerificationFailure(REASON_UNREADABLE_FILE, relative))
            continue
        digest, size = hashed
        entry = expected[relative]
        if size != entry.size:
            failures.append(VerificationFailure(REASON_SIZE_MISMATCH, relative))
        if digest != entry.sha256:
            failures.append(VerificationFailure(REASON_HASH_MISMATCH, relative))

    if failures:
        return _refuse(
            tuple(failures),
            metrics,
            manifest=manifest,
            snapshot_dir=snapshot_dir,
            quarantine_from=(root, repo_dir),
        )

    logger.info(
        "weights_verified — %d file(s), %d byte(s), revision %s",
        len(manifest.entries),
        manifest.total_bytes,
        manifest.revision,
    )
    return VerificationResult(ok=True, manifest=manifest, snapshot_dir=snapshot_dir)


def _refuse(
    failures: tuple[VerificationFailure, ...],
    metrics: ModelMetrics,
    *,
    manifest: WeightsManifest | None,
    snapshot_dir: Path | None = None,
    quarantine_from: tuple[Path, Path] | None = None,
) -> VerificationResult:
    """Count, log loudly, quarantine where appropriate, and return the refusal."""
    metrics.record_verify_failure()
    logger.error(
        "weights_verification_failed — refusing to load; reasons: %s",
        ", ".join(str(failure) for failure in failures),
    )
    quarantined_to: Path | None = None
    if quarantine_from is not None and not _is_manifest_failure(failures):
        cache_root, repo_dir = quarantine_from
        quarantined_to = _quarantine(cache_root, repo_dir, metrics)
    return VerificationResult(
        ok=False,
        failures=failures,
        manifest=manifest,
        snapshot_dir=snapshot_dir,
        quarantined_to=quarantined_to,
    )


# ---------------------------------------------------------------------------
# Acquisition: the pinned revision, the environment, the token
# ---------------------------------------------------------------------------


def resolve_revision() -> str:
    """Return the revision to fetch, verify and load.

    :data:`DEFAULT_MODEL_REVISION` unless :data:`MODEL_REVISION_ENV_VAR` names
    another commit sha. The shape is validated because the value is
    interpolated into a filesystem path and because a branch name is not a pin:
    a movable ``main`` turns the next upstream commit into "corruption" on the
    following start. An unusable override falls back to the committed pin
    loudly — and without echoing the value, which is operator-supplied text
    heading for a log line.
    """
    configured = os.environ.get(MODEL_REVISION_ENV_VAR, "").strip()
    if not configured:
        return DEFAULT_MODEL_REVISION
    if _REVISION_RE.match(configured) is None:
        logger.error(
            "model_revision_invalid — %s must be a 40-character commit sha; "
            "falling back to the committed pin",
            MODEL_REVISION_ENV_VAR,
        )
        return DEFAULT_MODEL_REVISION
    return configured


def resolve_cache_root() -> Path:
    """Return ``$HF_HOME`` — the volume holding ``hub/``, not ``hub/`` itself."""
    configured = os.environ.get(CACHE_ROOT_ENV_VAR, "").strip()
    return Path(configured) if configured else DEFAULT_CACHE_ROOT


def _resolve_token() -> str | None:
    """Return the Hugging Face token, or ``None`` when there is none.

    Absence is a supported mode, not an error: the gated repo is simply
    unreachable and ``/health`` says so. The value is never logged, never put
    in an exception message, and never passed anywhere but
    ``snapshot_download``'s ``token=`` keyword.
    """
    token = os.environ.get(HF_TOKEN_ENV_VAR, "").strip()
    return token or None


def read_manifest_pin(
    manifest_path: Path | str = MANIFEST_PATH,
) -> WeightsManifest | None:
    """Return the committed pin, or ``None`` if the manifest cannot supply one.

    A reader, not a verifier — it opens no weight file and logs nothing, so a
    caller can ask "could this manifest bless anything?" before spending a
    ~270 MiB download to find out. :func:`verify_weights` remains the single
    entry point for deciding whether a *weight set* is acceptable.
    """
    manifest, _failures = _load_manifest(Path(manifest_path))
    return manifest


def _fetch_reason(exc: BaseException) -> str:
    """Map a download failure to a closed, credential-free reason code.

    Never ``str(exc)``: ``huggingface_hub``'s errors carry request URLs,
    response bodies and request context, and ``HF_TOKEN`` must never reach a
    log line (``CLAUDE.md`` invariant 6 — the same discipline ``cache.py``
    applies to ``VALKEY_URL``). An HTTP status is the one detail worth keeping:
    401/403 means the token is missing rights, 404 means the revision is gone.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "io_failed"
    return "fetch_failed"


class SupportsWeightLoad(Protocol):
    """The one classifier method this module drives.

    A protocol rather than :class:`promptguard.classifier.PromptGuardClassifier`
    so the acquisition pipeline can be exercised against a double without the
    test suite having to build a real transformer — and so this module keeps
    its stdlib-only import weight.
    """

    def load(
        self,
        *,
        revision: str | None = None,
        cache_dir: Path | str | None = None,
        local_files_only: bool = False,
    ) -> bool:
        """Load the classifier from *cache_dir* at *revision*."""
        ...


def _download_from_hub(
    *,
    revision: str,
    cache_root: Path,
    token: str,
    metrics: ModelMetrics,
) -> bool:
    """Download the pinned snapshot from Hugging Face into the hub cache.

    ``allow_patterns`` is :data:`ALLOW_PATTERNS` and not optional: a plain
    snapshot download also pulls ``README.md`` and ``.gitattributes``, which
    the exact-set verifier then refuses as extra files — a download that
    "succeeds" straight into a quarantine.
    """
    # Imported here rather than at module scope: verification is stdlib-only
    # and is exercised in contexts (US-003's generator, the fixture tests) that
    # have no business paying for the hub client.
    from huggingface_hub import snapshot_download

    metrics.fetch_in_progress = True
    started_at = time.monotonic()
    try:
        snapshot_download(
            MODEL_ID,
            revision=revision,
            cache_dir=hub_cache_dir(cache_root),
            allow_patterns=list(ALLOW_PATTERNS),
            token=token,
        )
    except Exception as exc:
        metrics.record_fetch_failure()
        logger.error(
            "weights_fetch_failed — source=%s revision=%s reason=%s duration=%.1fs",
            SOURCE_HUGGINGFACE,
            revision,
            _fetch_reason(exc),
            time.monotonic() - started_at,
        )
        return False
    finally:
        metrics.fetch_in_progress = False
    logger.info(
        "weights_fetched — source=%s revision=%s duration=%.1fs",
        SOURCE_HUGGINGFACE,
        revision,
        time.monotonic() - started_at,
    )
    return True


def _load_verified(
    classifier: SupportsWeightLoad,
    *,
    cache_root: Path,
    revision: str,
) -> bool:
    """Load the just-verified snapshot, from disk only.

    ``local_files_only=True`` because the exact file set was verified a moment
    ago: there is nothing left for the loader to go and look for, and an etag
    round-trip would make a loaded classifier depend on the hub still being
    reachable.
    """
    started_at = time.monotonic()
    loaded = classifier.load(
        revision=revision,
        cache_dir=hub_cache_dir(cache_root),
        local_files_only=True,
    )
    if loaded:
        logger.info(
            "weights_loaded — revision=%s duration=%.1fs",
            revision,
            time.monotonic() - started_at,
        )
    else:
        logger.error(
            "weights_load_failed — the verified set at revision=%s did not load; "
            "PromptGuard stays unavailable",
            revision,
        )
    return loaded


def _verify_cached(
    *,
    cache_root: Path,
    revision: str,
    manifest_path: Path,
    metrics: ModelMetrics,
) -> VerificationResult | None:
    """Verify what the cache already holds, or ``None`` when it is cold.

    A cold cache is not a verification failure — there is nothing to verify
    yet — and treating it as one would log ``weights_verification_failed`` at
    ERROR on every first boot, which is exactly the cry-wolf that makes a real
    refusal unreadable.
    """
    if not snapshot_path(cache_root, MODEL_ID, revision).is_dir():
        return None
    return verify_weights(cache_root, manifest_path=manifest_path, metrics=metrics)


def acquire_and_load(
    classifier: SupportsWeightLoad,
    *,
    cache_root: Path | str | None = None,
    revision: str | None = None,
    manifest_path: Path | str | None = None,
    metrics: ModelMetrics | None = None,
) -> bool:
    """Bring *classifier* to loaded, fetching the pinned weights if needed.

    The boot pipeline, in order: verify what is cached (skipping the fetch
    entirely when it already satisfies the pin) → fetch the pinned revision
    from Hugging Face → verify the download → load. Returns whether the
    classifier ended up loaded.

    **Blocking on purpose, and not to be awaited from the lifespan.**
    ``snapshot_download`` and ``from_pretrained`` are synchronous network and
    torch work; the caller is
    ``asyncio.create_task(asyncio.to_thread(acquire_and_load, ...))`` so that
    lifespan startup yields immediately and ``/health`` answers throughout.

    **It does not raise.** It runs detached in a worker thread, where an
    exception would surface only as a stray "Task exception was never
    retrieved" at interpreter shutdown. Every failure is a logged ``False``.

    *cache_root*, *revision* and *manifest_path* default to the environment and
    the committed constants; they are parameters so a test can drive the whole
    pipeline without one, not a configuration surface.
    """
    metrics = metrics if metrics is not None else ModelMetrics()
    try:
        return _acquire_and_load(
            classifier,
            cache_root=(
                Path(cache_root) if cache_root is not None else resolve_cache_root()
            ),
            revision=revision if revision is not None else resolve_revision(),
            manifest_path=(
                MANIFEST_PATH if manifest_path is None else Path(manifest_path)
            ),
            metrics=metrics,
        )
    except Exception:
        metrics.record_fetch_failure()
        logger.exception(
            "weights_acquisition_crashed — PromptGuard stays unavailable and "
            "/health stays degraded"
        )
        return False


def _acquire_and_load(
    classifier: SupportsWeightLoad,
    *,
    cache_root: Path,
    revision: str,
    manifest_path: Path,
    metrics: ModelMetrics,
) -> bool:
    """The acquisition pipeline proper. See :func:`acquire_and_load`."""
    cached = _verify_cached(
        cache_root=cache_root,
        revision=revision,
        manifest_path=manifest_path,
        metrics=metrics,
    )
    if cached is not None:
        if cached.ok:
            return _load_verified(classifier, cache_root=cache_root, revision=revision)
        if cached.manifest_failure:
            # The refusal is ours, not the weight set's. Another download
            # cannot fix a manifest that blesses nothing.
            return False

    token = _resolve_token()
    if token is None:
        # Not an error: a token-less deployment is a supported degraded mode,
        # and the loud combined "every source failed" ERROR belongs to US-004,
        # which is the story that knows whether a mirror was available.
        logger.warning(
            "weights_fetch_skipped — no %s in the environment, so the gated "
            "repo cannot be reached; PromptGuard stays unavailable and /health "
            "stays degraded",
            HF_TOKEN_ENV_VAR,
        )
        return False

    if read_manifest_pin(manifest_path) is None:
        logger.error(
            "weights_pin_unusable — %s pins no verifiable file set, so a "
            "download could never be blessed; refusing to fetch",
            manifest_path,
        )
        return False

    if not _download_from_hub(
        revision=revision,
        cache_root=cache_root,
        token=token,
        metrics=metrics,
    ):
        return False

    verified = verify_weights(cache_root, manifest_path=manifest_path, metrics=metrics)
    if not verified.ok:
        return False
    return _load_verified(classifier, cache_root=cache_root, revision=revision)
