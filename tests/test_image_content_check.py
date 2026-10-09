"""The Dockerfile's build-time content check (scripts/image_content_check.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_lock_cuda_scope as scope
from scripts import image_content_check as check

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "scripts" / "cuda_payload_allowlist.txt"


def allowed() -> set[str]:
    return check.read_allowlist(str(ALLOWLIST))


def test_payload_predicate_matches_the_lock_checker() -> None:
    names = [
        *allowed(),
        "torch",
        "numpy",
        "Nvidia_Foo",
        "CUDA.bindings",
        "Triton",
        "tritonx",
    ]
    for name in names:
        assert check.is_payload(name) == scope.is_payload(name), name


def test_amd64_with_the_exact_allowlist_passes() -> None:
    assert check.check("amd64", "2.9.0+cu130", allowed(), allowed()) == []


def test_arm64_with_cpu_torch_and_no_payload_passes() -> None:
    assert check.check("arm64", "2.9.0+cpu", set(), allowed()) == []


@pytest.mark.parametrize(
    ("arch", "version"),
    [("amd64", "2.9.0+cpu"), ("arm64", "2.9.0+cu130"), ("amd64", "2.9.0")],
)
def test_wrong_torch_suffix_fails(arch: str, version: str) -> None:
    payload: set[str] = allowed() if arch == "amd64" else set()
    assert check.check(arch, version, payload, allowed())


def test_arm64_with_any_payload_fails() -> None:
    assert check.check("arm64", "2.9.0+cpu", {"triton"}, allowed())


def test_amd64_missing_or_extra_payload_fails() -> None:
    assert check.check("amd64", "2.9.0+cu130", allowed() - {"triton"}, allowed())
    assert check.check("amd64", "2.9.0+cu130", allowed() | {"nvidia-new"}, allowed())


def test_unknown_architecture_fails() -> None:
    assert check.check("s390x", "2.9.0+cpu", set(), allowed())


def test_normalisation_applies_to_the_allowlist(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("# c\nNvidia_Cudnn.cu13\n\n")
    assert check.read_allowlist(str(f)) == {"nvidia-cudnn-cu13"}
