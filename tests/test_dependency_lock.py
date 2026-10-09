"""The ``cpu`` extra must stay CUDA-free, and torch must stay extras-only.

Resolving ``torch`` from plain PyPI drags in 15 ``nvidia-*`` CUDA packages
(~2.7 GB) that the ``cpu`` lanes (tests, CI, the arm64 image) must not carry.
``pyproject.toml`` keeps them out with explicit ``pytorch-cpu`` / ``pytorch-cu130``
indexes, arch-scoped sources and mutually exclusive ``cpu`` / ``cuda`` extras.

The export check makes the guarantee fail locally, in the same ``uv run pytest``
a developer runs before committing a dependency change.

test_mapping:
  uv.lock: tests/test_dependency_lock.py
  pyproject.toml: tests/test_dependency_lock.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests import conftest

_REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = _REPO_ROOT / "uv.lock"
PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


def test_lock_file_is_committed() -> None:
    assert LOCK_PATH.exists(), (
        "uv.lock must be committed — CI runs `uv sync --locked` against it"
    )


def test_cpu_export_contains_no_cuda_packages() -> None:
    """Temporary guard (US-001); US-002 lifts it into the lock checker.

    The lock now carries the ``cuda`` extra's wheels, so grepping the whole
    file would be red by design. What must stay CUDA-free is what the ``cpu``
    extra installs, and ``uv export`` for exactly that selection is the oracle.
    """
    uv = shutil.which("uv")
    assert uv is not None, "uv must be on PATH to export the lock"
    export = subprocess.run(
        [uv, "export", "--frozen", "--extra", "dev", "--extra", "cpu"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    offenders = [
        line
        for line in export.splitlines()
        if re.match(r"^(nvidia-|cuda-|triton==)", line)
    ]
    assert offenders == [], (
        f"`uv export --extra dev --extra cpu` lists {len(offenders)} CUDA "
        f"package line(s), e.g. {offenders[:3]}. The cpu extra must resolve "
        "torch from the pytorch-cpu index."
    )


def test_pyproject_pins_the_cpu_torch_index() -> None:
    pyproject = PYPROJECT_PATH.read_text()
    assert "https://download.pytorch.org/whl/cpu" in pyproject, (
        "pyproject.toml must declare the pytorch-cpu index — it is the "
        "mechanism that keeps the cpu extra CUDA-free"
    )
    assert "https://download.pytorch.org/whl/cu130" in pyproject, (
        "pyproject.toml must declare the pytorch-cu130 index for the cuda extra"
    )
    assert "sys_platform == 'linux'" in pyproject or (
        'sys_platform == "linux"' in pyproject
    ), (
        "the torch source override must stay marked for linux only — macOS and "
        "Windows wheels on PyPI are already CPU-only"
    )


def _tool_uv() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT_PATH.read_text())["tool"]["uv"]


def test_torch_is_only_in_the_cpu_and_cuda_extras() -> None:
    project = tomllib.loads(PYPROJECT_PATH.read_text())["project"]
    assert not any(d.startswith("torch") for d in project["dependencies"])
    extras = project["optional-dependencies"]
    assert extras["cpu"] == ["torch==2.14.0"]
    assert extras["cuda"] == ["torch==2.14.0"]
    assert not any(d.startswith("torch") for d in extras["dev"])


def test_cpu_and_cuda_extras_conflict() -> None:
    assert _tool_uv()["conflicts"] == [[{"extra": "cpu"}, {"extra": "cuda"}]]


def test_torch_sources_are_arch_scoped_and_both_indexes_explicit() -> None:
    uv = _tool_uv()
    indexes = {i["name"]: i for i in uv["index"]}
    assert set(indexes) == {"pytorch-cpu", "pytorch-cu130"}
    assert all(i["explicit"] is True for i in indexes.values())
    assert uv["sources"]["torch"] == [
        {
            "index": "pytorch-cpu",
            "extra": "cpu",
            "marker": "sys_platform == 'linux'",
        },
        {
            "index": "pytorch-cu130",
            "extra": "cuda",
            "marker": ("sys_platform == 'linux' and platform_machine == 'x86_64'"),
        },
        {
            "index": "pytorch-cpu",
            "extra": "cuda",
            "marker": ("sys_platform == 'linux' and platform_machine != 'x86_64'"),
        },
    ]


def test_a_missing_torch_fails_the_session_with_the_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `None` in sys.modules makes `import torch` raise ImportError.
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(pytest.UsageError) as excinfo:
        conftest.require_torch()
    assert "uv sync --extra dev --extra cpu" in str(excinfo.value)
