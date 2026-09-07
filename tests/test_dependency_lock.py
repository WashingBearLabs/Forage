"""The committed ``uv.lock`` must stay CPU-only.

Resolving ``torch`` from plain PyPI drags in 15 ``nvidia-*`` CUDA packages
(~2.7 GB) that no Forage lane needs — not the tests, not CI, not the image.
``pyproject.toml`` keeps them out with an explicit ``pytorch-cpu`` index plus a
``sys_platform == 'linux'`` marker, and re-locking without that configuration
silently reintroduces the whole payload.

CI greps the lock too (``.github/workflows/ci.yml``, lint job), but the grep
there only runs on a push. This test makes the same guarantee fail locally, in
the same ``uv run pytest`` a developer runs before committing a dependency
change.

test_mapping:
  uv.lock: tests/test_dependency_lock.py
  pyproject.toml: tests/test_dependency_lock.py
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = _REPO_ROOT / "uv.lock"
PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


def test_lock_file_is_committed() -> None:
    assert LOCK_PATH.exists(), (
        "uv.lock must be committed — CI runs `uv sync --locked` against it"
    )


def test_lock_contains_no_cuda_wheels() -> None:
    offenders = [
        line.strip() for line in LOCK_PATH.read_text().splitlines() if "nvidia-" in line
    ]
    assert offenders == [], (
        f"uv.lock references {len(offenders)} nvidia-* CUDA package line(s), "
        "e.g. "
        f"{offenders[:3]}. Re-lock with the pytorch-cpu index configured in "
        "pyproject.toml ([[tool.uv.index]] + [tool.uv.sources])."
    )


def test_pyproject_pins_the_cpu_torch_index() -> None:
    pyproject = PYPROJECT_PATH.read_text()
    assert "https://download.pytorch.org/whl/cpu" in pyproject, (
        "pyproject.toml must declare the pytorch-cpu index — it is the "
        "mechanism that keeps the lock CUDA-free, and the lock assertion above "
        "is only a symptom check without it"
    )
    assert "sys_platform == 'linux'" in pyproject or (
        'sys_platform == "linux"' in pyproject
    ), (
        "the torch source override must stay marked for linux only — macOS and "
        "Windows wheels on PyPI are already CPU-only"
    )
