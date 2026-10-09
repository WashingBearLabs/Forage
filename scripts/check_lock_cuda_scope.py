"""Prove the CUDA payload stays inside the ``cuda`` extra on x86_64 Linux.

``feature-unified-image`` US-002. One lock serves two device classes, so a
mistake in it could put ~3 GB of ``nvidia-*`` wheels into the dev and CI
environments, or an unexpected binary into the public image. This checker is the
single oracle for that, called by ``tests/test_dependency_lock.py`` (on the real
lock and on eight planted violations) and by CI's ``lint`` job::

    uv run python -m scripts.check_lock_cuda_scope [--root DIR]

**Behavioural, not textual.** The three install profiles are exported with
``uv export --frozen --no-hashes --format requirements-txt`` and every line's
environment marker is *evaluated* with ``packaging.markers.Marker.evaluate``
under explicit environments (linux/x86_64, linux/aarch64, darwin/arm64,
win32/AMD64, each at two Python versions). Nothing reasons about whether one
marker "implies" another by reading its text.

**Payload** is a package whose PEP 503-normalised name matches
``^(nvidia-|cuda-)`` or is exactly ``triton``. Normalisation is applied to both
sides, the allowlist included.

**Rules**

1. *Payload scope.* ``dev+cpu`` and ``cpu`` export no payload line. In ``cuda``
   every payload line is true only on linux/x86_64.
2. *Torch variant per profile.* ``cpu`` and ``dev+cpu``: ``+cpu`` on every Linux
   environment. ``cuda``: ``+cu130`` on linux/x86_64, ``+cpu`` on linux/aarch64.
   macOS and Windows get plain PyPI torch. A torch line with no local suffix
   must be false on every Linux environment.
3. *Provenance*, read from ``uv.lock`` as TOML and compared as registry **URLs**:
   Linux torch comes from ``https://download.pytorch.org/whl/cpu`` or
   ``/cu130`` and PyPI torch only serves non-Linux; payload packages come from
   ``https://pypi.org/simple``; every wheel (and sdist) carries a non-empty
   ``hash``. **Wheel-URL hosts are deliberately not checked**: the PyTorch
   indexes redirect to ``download-r2.pytorch.org``, which serves the ``+cpu``
   wheels too, so a host check would flag the legitimate lock. The registry
   URL plus the hash is what pins the bytes.
4. *Allowlist.* The payload names in the lock equal the committed
   ``scripts/cuda_payload_allowlist.txt``. A torch bump that adds or drops a
   payload package fails here until a human reviews and edits that file.

Also required: the lock's ``conflicts`` table declares ``cpu`` and ``cuda``
mutually exclusive, because without it uv resolves both extras together.

Output is package names and profile/environment labels only, and the exit code
is non-zero on any violation.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_NAME = "scripts/cuda_payload_allowlist.txt"

PYPI = "https://pypi.org/simple"
TORCH_CPU = "https://download.pytorch.org/whl/cpu"
TORCH_CU130 = "https://download.pytorch.org/whl/cu130"
_TORCH_INDEX_BY_LOCAL = {"cpu": TORCH_CPU, "cu130": TORCH_CU130}

_PAYLOAD = re.compile(r"^(nvidia-|cuda-)")

# Profile label -> the ``uv export`` extras that select it.
PROFILES: dict[str, tuple[str, ...]] = {
    "dev+cpu": ("dev", "cpu"),
    "cpu": ("cpu",),
    "cuda": ("cuda",),
}

_PYTHONS = ("3.12.0", "3.14.0")


@dataclass(frozen=True)
class Platform:
    label: str
    sys_platform: str
    machine: str
    os_name: str
    system: str

    @property
    def linux(self) -> bool:
        return self.sys_platform == "linux"


LINUX_X86 = Platform("linux/x86_64", "linux", "x86_64", "posix", "Linux")
LINUX_ARM = Platform("linux/aarch64", "linux", "aarch64", "posix", "Linux")
PLATFORMS = (
    LINUX_X86,
    LINUX_ARM,
    Platform("darwin/arm64", "darwin", "arm64", "posix", "Darwin"),
    Platform("win32/AMD64", "win32", "AMD64", "nt", "Windows"),
)


def normalise(name: str) -> str:
    """PEP 503 normalisation, applied to every name on both sides of a compare."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_payload(name: str) -> bool:
    n = normalise(name)
    return bool(_PAYLOAD.match(n)) or n == "triton"


def _environment(platform: Platform, python: str) -> dict[str, str]:
    return {
        "implementation_name": "cpython",
        "implementation_version": python,
        "os_name": platform.os_name,
        "platform_machine": platform.machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": platform.system,
        "platform_version": "",
        "python_full_version": python,
        "python_version": ".".join(python.split(".")[:2]),
        "sys_platform": platform.sys_platform,
        "extra": "",
    }


def _envs() -> list[tuple[Platform, dict[str, str]]]:
    return [(p, _environment(p, py)) for p in PLATFORMS for py in _PYTHONS]


def _active(req: Requirement, env: dict[str, str]) -> bool:
    return req.marker is None or req.marker.evaluate(env)


def _local(req: Requirement) -> str:
    for spec in req.specifier:
        if spec.operator == "==":
            return Version(spec.version).local or ""
    return ""


def _export(root: Path, extras: Sequence[str]) -> tuple[list[Requirement], str]:
    uv = shutil.which("uv")
    if uv is None:
        return [], "uv is not on PATH"
    cmd = [uv, "export", "--frozen", "--no-hashes", "--format", "requirements-txt"]
    for extra in extras:
        cmd += ["--extra", extra]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return [], f"`uv export` exited {proc.returncode}"
    reqs: list[Requirement] = []
    for line in proc.stdout.splitlines():
        if not line.strip() or line[0] in "#- \t":
            continue  # comments, annotations, ``-e .``
        reqs.append(Requirement(line.strip()))
    return reqs, ""


def _read_allowlist(root: Path) -> set[str] | None:
    path = root / ALLOWLIST_NAME
    if not path.is_file():
        return None
    names: set[str] = set()
    for raw in path.read_text().splitlines():
        entry = raw.split("#", 1)[0].strip()
        if entry:
            names.add(normalise(entry))
    return names


def _check_exports(root: Path) -> list[str]:
    found: list[str] = []
    envs = _envs()
    for profile, extras in PROFILES.items():
        reqs, error = _export(root, extras)
        if error:
            found.append(f"[{profile}] {error}")
            continue
        for req in reqs:
            if not is_payload(req.name):
                continue
            name = normalise(req.name)
            if profile != "cuda":
                found.append(f"[{profile}] payload package {name} is exported")
                continue
            for platform, env in envs:
                if platform is not LINUX_X86 and _active(req, env):
                    found.append(
                        f"[cuda] payload package {name} is active on {platform.label}"
                    )
        torch = [r for r in reqs if normalise(r.name) == "torch"]
        for platform, env in envs:
            selected = [r for r in torch if _active(r, env)]
            if len(selected) != 1:
                found.append(
                    f"[{profile}] torch selects {len(selected)} lines on "
                    f"{platform.label}, expected exactly 1"
                )
                continue
            local = _local(selected[0])
            if platform.linux and not local:
                found.append(
                    f"[{profile}] torch without a local suffix is active on "
                    f"{platform.label}"
                )
                continue
            if not platform.linux:
                want = ""
            elif profile == "cuda" and platform is LINUX_X86:
                want = "cu130"
            else:
                want = "cpu"
            if local != want:
                found.append(
                    f"[{profile}] torch on {platform.label} is +{local or 'PyPI'}, "
                    f"expected +{want or 'PyPI'}"
                )
    return sorted(set(found))


def _registry(package: dict[str, Any]) -> str:
    source = package.get("source", {})
    return str(source.get("registry", ""))


def _pypi_torch_reaches_linux(package: dict[str, Any]) -> bool:
    """A PyPI torch entry is only legitimate if no resolution marker selects Linux."""
    markers = package.get("resolution-markers", [])
    if not markers:
        return True  # unscoped: applies everywhere
    for text in markers:
        marker = Marker(text)
        for platform in (LINUX_X86, LINUX_ARM):
            for python in _PYTHONS:
                for extra in ("", "extra-6-forage-cpu", "extra-6-forage-cuda"):
                    env = _environment(platform, python) | {"extra": extra}
                    if marker.evaluate(env):
                        return True
    return False


def _check_lock(root: Path) -> list[str]:
    found: list[str] = []
    lock_path = root / "uv.lock"
    if not lock_path.is_file():
        return ["uv.lock is missing"]
    lock = tomllib.loads(lock_path.read_text())

    groups = lock.get("conflicts", [])
    declared = any(
        {item.get("extra") for item in group} >= {"cpu", "cuda"} for group in groups
    )
    if not declared:
        found.append("conflicts table does not declare cpu and cuda exclusive")

    payload: set[str] = set()
    for package in lock.get("package", []):
        name = normalise(str(package.get("name", "")))
        registry = _registry(package)
        version = str(package.get("version", ""))
        if is_payload(name):
            payload.add(name)
            if registry != PYPI:
                found.append(f"payload package {name} is not from {PYPI}")
        if name == "torch":
            local = Version(version).local if version else None
            if not local:
                if registry != PYPI:
                    found.append(f"torch {version} is not from {PYPI}")
                elif _pypi_torch_reaches_linux(package):
                    found.append(f"torch {version} from PyPI can resolve on Linux")
            elif _TORCH_INDEX_BY_LOCAL.get(local) != registry:
                found.append(f"torch {version} is not from its +{local} index")
        wheels: list[dict[str, Any]] = list(package.get("wheels", []))
        if "sdist" in package:
            wheels.append(package["sdist"])
        for artifact in wheels:
            if not str(artifact.get("hash", "")).strip():
                found.append(f"package {name} {version} has an artifact without a hash")

    allowed = _read_allowlist(root)
    if allowed is None:
        found.append(f"{ALLOWLIST_NAME} is missing")
    else:
        for name in sorted(payload - allowed):
            found.append(f"payload package {name} is not in the allowlist")
        for name in sorted(allowed - payload):
            found.append(f"allowlist entry {name} is not in the lock")
    return sorted(set(found))


def check(root: Path = REPO_ROOT) -> list[str]:
    """Return every violation under *root* (empty when the lock is clean)."""
    return sorted(set(_check_lock(root) + _check_exports(root)))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the CUDA scope of uv.lock")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    violations = check(args.root)
    for violation in violations:
        print(violation)
    if violations:
        print(f"{len(violations)} CUDA-scope violation(s).", file=sys.stderr)
        return 1
    print("uv.lock keeps the CUDA payload inside the cuda extra on linux/x86_64.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
