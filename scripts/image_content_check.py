"""Build-time proof of what the image's venv holds, run inside the Dockerfile.

Usage: ``python image_content_check.py <dpkg-arch> <allowlist-file>``.

amd64 must carry CUDA torch (``+cu130``) and exactly the allowlisted payload
distributions; arm64 must carry CPU torch (``+cpu``) and none. Anything else
fails the build. Standard library plus torch only, so it runs in the image.
The payload regex and the ``triton`` special case mirror
``scripts/check_lock_cuda_scope.py``; ``tests/test_dockerfile.py`` keeps them
in step.
"""

from __future__ import annotations

import re
import sys
from importlib import metadata

_PAYLOAD = re.compile(r"^(nvidia-|cuda-)")
SUFFIXES = {"amd64": "+cu130", "arm64": "+cpu"}


def normalise(name: str) -> str:
    """PEP 503 normalisation, applied to every name on both sides."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_payload(name: str) -> bool:
    n = normalise(name)
    return bool(_PAYLOAD.match(n)) or n == "triton"


def installed_payload() -> set[str]:
    names = (dist.metadata["Name"] for dist in metadata.distributions())
    return {normalise(n) for n in names if n and is_payload(n)}


def read_allowlist(path: str) -> set[str]:
    with open(path, encoding="utf-8") as handle:
        lines = (line.strip() for line in handle)
        return {normalise(line) for line in lines if line and not line.startswith("#")}


def check(
    arch: str, torch_version: str, payload: set[str], allowed: set[str]
) -> list[str]:
    """Every way the image's content disagrees with its architecture."""
    suffix = SUFFIXES.get(arch)
    if suffix is None:
        return [f"unsupported architecture {arch!r}"]
    problems: list[str] = []
    if not torch_version.endswith(suffix):
        problems.append(f"torch {torch_version!r} does not end {suffix!r} on {arch}")
    expected: set[str] = allowed if arch == "amd64" else set()
    for name in sorted(payload - expected):
        problems.append(f"unexpected payload distribution {name} on {arch}")
    for name in sorted(expected - payload):
        problems.append(f"missing payload distribution {name} on {arch}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: image_content_check.py <arch> <allowlist>", file=sys.stderr)
        return 2
    import torch

    problems = check(
        argv[1], torch.__version__, installed_payload(), read_allowlist(argv[2])
    )
    for problem in problems:
        print(f"image content check: {problem}", file=sys.stderr)
    if not problems:
        print(f"image content check: {argv[1]} ok, torch {torch.__version__}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
