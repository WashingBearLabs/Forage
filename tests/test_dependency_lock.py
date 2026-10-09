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

import shutil
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts import check_lock_cuda_scope as scope
from tests import conftest

_REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = _REPO_ROOT / "uv.lock"
PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
TORCH_CPU = "https://download.pytorch.org/whl/cpu"


def test_lock_file_is_committed() -> None:
    assert LOCK_PATH.exists(), (
        "uv.lock must be committed — CI runs `uv sync --locked` against it"
    )


def test_the_committed_lock_passes_the_cuda_scope_checker() -> None:
    assert scope.check(_REPO_ROOT) == []


def _plant(tmp_path: Path, edit: Callable[[str], str]) -> list[str]:
    """Copy the lock inputs, apply *edit* to ``uv.lock``, return the violations."""
    for name in ("pyproject.toml", "uv.lock", scope.ALLOWLIST_NAME):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REPO_ROOT / name, target)
    lock = tmp_path / "uv.lock"
    original = lock.read_text()
    edited = edit(original)
    assert edited != original, "the planted edit did not change the lock"
    lock.write_text(edited)
    return scope.check(tmp_path)


def _forage_block(lock: str) -> tuple[int, int]:
    start = lock.index('name = "forage"')
    return start, lock.index("[package.metadata]", start)


def _extra_list(lock: str, extra: str) -> tuple[int, int]:
    """Span of the ``extra = [ ... ]`` list inside the forage package."""
    start, end = _forage_block(lock)
    head = lock.index(f"\n{extra} = [\n", start, end) + 1
    return head, lock.index("\n]\n", head) + 1


def _insert_into_extra(lock: str, extra: str, line: str) -> str:
    _, close = _extra_list(lock, extra)
    return lock[:close] + line + "\n" + lock[close:]


def test_planted_payload_under_a_base_dependency_fails(tmp_path: Path) -> None:
    def edit(lock: str) -> str:
        start, _ = _forage_block(lock)
        anchor = lock.index("dependencies = [\n", start) + len("dependencies = [\n")
        return lock[:anchor] + '    { name = "nvidia-nvtx" },\n' + lock[anchor:]

    violations = _plant(tmp_path, edit)
    assert "[dev+cpu] payload package nvidia-nvtx is exported" in violations
    assert "[cpu] payload package nvidia-nvtx is exported" in violations


def test_planted_payload_under_cpu_fails(tmp_path: Path) -> None:
    violations = _plant(
        tmp_path,
        lambda lock: _insert_into_extra(lock, "cpu", '    { name = "nvidia-nvtx" },'),
    )
    assert "[cpu] payload package nvidia-nvtx is exported" in violations


def test_planted_payload_under_cuda_without_the_x86_marker_fails(
    tmp_path: Path,
) -> None:
    violations = _plant(
        tmp_path,
        lambda lock: _insert_into_extra(lock, "cuda", '    { name = "nvidia-nvtx" },'),
    )
    assert "[cuda] payload package nvidia-nvtx is active on linux/aarch64" in (
        violations
    )


def test_planted_cu130_torch_resolving_on_aarch64_fails(tmp_path: Path) -> None:
    def edit(lock: str) -> str:
        start, end = _extra_list(lock, "cuda")
        block = lock[start:end]
        line = next(ln for ln in block.splitlines() if "+cu130" in ln)
        widened = line.replace(
            "platform_machine == 'x86_64' and sys_platform == 'linux'",
            "sys_platform == 'linux'",
            1,
        )
        assert widened != line
        return lock[:start] + block.replace(line, widened) + lock[end:]

    violations = _plant(tmp_path, edit)
    assert any(
        v.startswith("[cuda] torch") and "linux/aarch64" in v for v in violations
    ), violations


def test_planted_linux_torch_from_pypi_fails(tmp_path: Path) -> None:
    def edit(lock: str) -> str:
        start, end = _extra_list(lock, "cpu")
        block = lock[start:end]
        line = next(ln for ln in block.splitlines() if "+cpu" in ln)
        plain = line.replace("2.14.0+cpu", "2.14.0").replace(TORCH_CPU, scope.PYPI)
        plain = plain.replace("sys_platform == 'linux'", "sys_platform != 'darwin'")
        return lock[:start] + block.replace(line, plain) + lock[end:]

    violations = _plant(tmp_path, edit)
    assert any(
        v.startswith("[cpu] torch without a local suffix is active on linux")
        for v in violations
    ), violations


def test_planted_pypi_registry_on_a_linux_torch_entry_fails(tmp_path: Path) -> None:
    def edit(lock: str) -> str:
        return lock.replace(
            'version = "2.14.0+cpu"\nsource = { registry = "' + TORCH_CPU + '" }',
            'version = "2.14.0+cpu"\nsource = { registry = "' + scope.PYPI + '" }',
            1,
        )

    assert "torch 2.14.0+cpu is not from its +cpu index" in _plant(tmp_path, edit)


def test_planted_wheel_without_a_hash_fails(tmp_path: Path) -> None:
    def edit(lock: str) -> str:
        start = lock.index('[[package]]\nname = "nvidia-nvtx"')
        hash_at = lock.index(', hash = "sha256:', start)
        return lock[:hash_at] + lock[lock.index(", size", hash_at) :]

    assert "package nvidia-nvtx 13.0.85 has an artifact without a hash" in (
        _plant(tmp_path, edit)
    )


def test_planted_unknown_payload_name_fails(tmp_path: Path) -> None:
    violations = _plant(
        tmp_path, lambda lock: lock.replace("nvidia-nvtx", "nvidia-nvtx-extra")
    )
    assert "payload package nvidia-nvtx-extra is not in the allowlist" in violations


def test_planted_lock_resolving_both_extras_together_fails(tmp_path: Path) -> None:
    def edit(lock: str) -> str:
        start = lock.index("conflicts = [[")
        return lock[:start] + lock[lock.index("]]\n", start) + 3 :]

    assert "conflicts table does not declare cpu and cuda exclusive" in (
        _plant(tmp_path, edit)
    )


def test_underscore_and_case_variants_count_as_payload_names() -> None:
    assert scope.is_payload("NVIDIA_cuda.runtime")
    assert scope.is_payload("Triton")
    assert not scope.is_payload("triton-kernels")


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
