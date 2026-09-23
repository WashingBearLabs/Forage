"""CI guard for mechanically-derived sanitization pipeline revisions."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import idna
import pytest

import model_fetcher
from cache import cache_policy_fingerprint
from model_fetcher import DEFAULT_MODEL_REVISION, MODEL_REVISION_ENV_VAR
from models import RetrieveRequest, SearchRequest
from pipeline import sanitizer_revision
from promptguard.classifier import DEFAULT_MODEL_ID


def test_manifest_entry_memo_opens_manifest_once_across_revision_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conftest.clear_manifest_entry_cache resets the process-lifetime seam."""
    reads: list[Path] = []
    original = Path.read_text

    def count_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == model_fetcher.MANIFEST_PATH:
            reads.append(path)
        return original(path, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", count_read)
    pin = model_fetcher.read_manifest_pin()
    assert pin is not None
    assert model_fetcher.resolve_revision(DEFAULT_MODEL_ID) == pin.revision
    first = sanitizer_revision.derive_sanitizer_revision({})
    assert sanitizer_revision.derive_sanitizer_revision({}) == first
    assert reads == [model_fetcher.MANIFEST_PATH]
    assert model_fetcher._manifest_entry.cache_info().misses == 1


def test_unreadable_manifest_keeps_default_hash_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    before = sanitizer_revision.derive_sanitizer_revision({})
    model_fetcher._manifest_entry.cache_clear()
    original = Path.read_text
    reads = 0

    def unreadable(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal reads
        if path == model_fetcher.MANIFEST_PATH:
            reads += 1
            raise PermissionError("sensitive-path-must-not-be-logged")
        return original(path, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert model_fetcher.resolve_revision(DEFAULT_MODEL_ID) == DEFAULT_MODEL_REVISION
    assert sanitizer_revision.derive_sanitizer_revision({}) == before
    assert sanitizer_revision.derive_sanitizer_revision({}) == before
    assert reads == 1
    assert [record.getMessage() for record in caplog.records] == [
        "manifest_pin_unavailable — reason=manifest_unreadable"
    ]


@pytest.mark.parametrize("model_id", ["acme/unvendored", "", "invalid/\ninjected"])
def test_an_unpinned_model_still_has_a_total_deterministic_revision(
    monkeypatch: pytest.MonkeyPatch, model_id: str
) -> None:
    monkeypatch.setattr(
        sanitizer_revision, "resolve_model_id", lambda: (model_id, True)
    )
    assert model_fetcher.resolve_revision(model_id) == "unpinned"
    first = sanitizer_revision.derive_sanitizer_revision({})
    assert len(first) == 64
    assert sanitizer_revision.derive_sanitizer_revision({}) == first


@pytest.mark.parametrize(
    "source_name",
    (
        "contract.py",
        "stage2_structural.py",
        "stage3_promptguard.py",
        "stage4_structuring.py",
        # Repo-root, in `_ROOT_REVISION_SOURCES` since
        # `hardening-search-sanitization` US-003: it decides which search
        # results are dropped and which fetches are refused.
        "url_validator.py",
    ),
)
def test_sanitizer_revision_changes_for_security_pipeline_source(
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
) -> None:
    """CI fails if a security-stage source ceases to affect the revision."""
    config = {"promptguard_threshold": 0.85}
    original_revision = sanitizer_revision.derive_sanitizer_revision(config)
    read_bytes = Path.read_bytes

    def changed_read_bytes(path: Path) -> bytes:
        source = read_bytes(path)
        if path.name == source_name:
            return source + b"\n# test revision input\n"
        return source

    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)

    assert sanitizer_revision.derive_sanitizer_revision(config) != original_revision


def test_sanitizer_revision_changes_for_behavior_config() -> None:
    """The PromptGuard threshold is part of the opaque cache-key revision."""
    assert sanitizer_revision.derive_sanitizer_revision(
        {"promptguard_threshold": 0.85}
    ) != sanitizer_revision.derive_sanitizer_revision({"promptguard_threshold": 0.86})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("promptguard_contiguity_windows", 2),
        ("promptguard_contiguity_threshold", 0.6),
    ],
)
def test_contiguity_is_configuration_only_and_keys_the_cache(
    key: str, value: int | float
) -> None:
    assert key not in RetrieveRequest.model_fields
    assert key not in SearchRequest.model_fields
    baseline = sanitizer_revision.derive_sanitizer_revision({})
    changed = sanitizer_revision.derive_sanitizer_revision({key: value})
    assert baseline != changed
    inputs: dict[str, object] = {
        "blocked_domains": [],
        "classifier_loaded": True,
        "promptguard_fail_closed": True,
        "promptguard_threshold": 0.85,
        "sanitizer_revision": baseline,
        "trusted_domains": [],
        "verified_domains": [],
    }
    assert set(inspect.signature(cache_policy_fingerprint).parameters) == set(inputs)
    expected = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    def fingerprint(revision: str) -> str:
        return cache_policy_fingerprint(
            trusted_domains=[],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=True,
            sanitizer_revision=revision,
        )

    assert fingerprint(baseline) == expected
    assert fingerprint(changed) != expected


def test_sanitizer_revision_changes_for_promptguard_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PromptGuard model artifact identity is part of the revision."""
    config = {"promptguard_threshold": 0.85}
    monkeypatch.setenv(MODEL_REVISION_ENV_VAR, DEFAULT_MODEL_REVISION)
    original_revision = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setattr(
        model_fetcher,
        "ALLOWED_MODEL_IDS",
        frozenset({DEFAULT_MODEL_ID, "test/model-revision"}),
    )
    monkeypatch.setenv(model_fetcher.MODEL_ID_ENV_VAR, "test/model-revision")

    assert sanitizer_revision.derive_sanitizer_revision(config) != original_revision


def test_sanitizer_revision_changes_for_the_pinned_model_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weights arrive at runtime, so the pin is part of sanitization identity.

    Two containers running byte-identical code can now be scanning with
    different weights (``FORAGE_MODEL_REVISION``). A revision that could not
    tell them apart would key a cache on behaviour it does not describe —
    which is the one thing this value exists to prevent.
    """
    config = {"promptguard_threshold": 0.85}
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    at_the_pin = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setenv(MODEL_REVISION_ENV_VAR, "a" * 40)

    assert sanitizer_revision.derive_sanitizer_revision(config) != at_the_pin


def test_sanitizer_revision_is_stable_at_the_committed_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset override and the committed pin are the same input."""
    config = {"promptguard_threshold": 0.85}
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    unset = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setenv(MODEL_REVISION_ENV_VAR, DEFAULT_MODEL_REVISION)

    assert sanitizer_revision.derive_sanitizer_revision(config) == unset


@pytest.mark.parametrize("model_id", [DEFAULT_MODEL_ID, "acme/second-guard"])
@pytest.mark.parametrize(("windows", "threshold"), [(0, 0.5), (2, 0.6)])
@pytest.mark.parametrize(
    "configured,encoded",
    [
        (0.85, b"0.85"),
        ("\uff10.\uff18\uff15", b"\xef\xbc\x90.\xef\xbc\x98\xef\xbc\x95"),
        ("invalid-\u2603", b"invalid-\xe2\x98\x83"),
        ("invalid-\ud800", b"invalid-\xed\xa0\x80"),
    ],
)
def test_the_hashed_model_identity_is_model_id_at_revision(
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    windows: int,
    threshold: float,
    configured: float | str,
    encoded: bytes,
) -> None:
    """The exact composition, recomputed independently.

    Stronger than "the value moved when the revision moved", which a dozen
    wrong implementations also satisfy: this fails if the identity is hashed
    as ``DEFAULT_MODEL_ID`` alone, as the revision alone, or with the two run together
    without the separator that makes the pair unambiguous.

    Extended by ``hardening-search-sanitization`` US-003 to both new inputs,
    in exactly the order the code feeds them: the root sources after the
    ``pipeline/`` ones, ``idna@<version>`` after the model identity. Extended
    rather than weakened to "the value differs" — an order this test could not
    see is an order a cache key could not rely on.
    """
    monkeypatch.delenv(MODEL_REVISION_ENV_VAR, raising=False)
    monkeypatch.setattr(
        model_fetcher, "ALLOWED_MODEL_IDS", frozenset({DEFAULT_MODEL_ID, model_id})
    )
    monkeypatch.setenv(model_fetcher.MODEL_ID_ENV_VAR, model_id)
    expected = hashlib.sha256()
    pipeline_dir = Path(sanitizer_revision.__file__).parent
    for source_name in sanitizer_revision._REVISION_SOURCES:
        expected.update((pipeline_dir / source_name).read_bytes())
    for root_source_name in sanitizer_revision._ROOT_REVISION_SOURCES:
        expected.update((pipeline_dir.parent / root_source_name).read_bytes())
    revision = DEFAULT_MODEL_REVISION if model_id == DEFAULT_MODEL_ID else "unpinned"
    expected.update(f"{model_id}@{revision}".encode())
    expected.update(f"idna@{idna.__version__}".encode())
    expected.update(encoded)
    expected.update(str(windows).encode("ascii"))
    expected.update(str(threshold).encode("ascii"))

    assert (
        sanitizer_revision.derive_sanitizer_revision(
            {
                "promptguard_threshold": configured,
                "promptguard_contiguity_windows": windows,
                "promptguard_contiguity_threshold": threshold,
            }
        )
        == expected.hexdigest()
    )


@pytest.mark.parametrize("configured", ["", " \t\n", DEFAULT_MODEL_ID, "evil/model"])
def test_default_model_hash_is_unchanged_by_explicit_blank_or_refused_selection(
    monkeypatch: pytest.MonkeyPatch, configured: str
) -> None:
    unset = sanitizer_revision.derive_sanitizer_revision({})
    monkeypatch.setenv(model_fetcher.MODEL_ID_ENV_VAR, configured)
    assert sanitizer_revision.derive_sanitizer_revision({}) == unset


@pytest.mark.parametrize("model_id", sorted(model_fetcher.ALLOWED_MODEL_IDS))
def test_every_allowlisted_model_has_a_total_revision(
    monkeypatch: pytest.MonkeyPatch, model_id: str
) -> None:
    monkeypatch.setenv(model_fetcher.MODEL_ID_ENV_VAR, model_id)
    assert len(sanitizer_revision.derive_sanitizer_revision({})) == 64


def test_the_root_sources_resolve_against_the_repo_root() -> None:
    """A repo-root entry is a path-resolution change, not a tuple entry.

    `_REVISION_SOURCES` names are resolved under `pipeline/`; a name added
    there would be looked for at `pipeline/url_validator.py`, which does not
    exist. The separate tuple is what makes the resolution explicit.
    """
    assert sanitizer_revision._ROOT_REVISION_SOURCES == ("url_validator.py",)
    pipeline_dir = Path(sanitizer_revision.__file__).parent
    for name in sanitizer_revision._ROOT_REVISION_SOURCES:
        assert (pipeline_dir.parent / name).is_file()
        assert not (pipeline_dir / name).exists()
        assert name not in sanitizer_revision._REVISION_SOURCES


def test_sanitizer_revision_changes_for_the_idna_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UTS-46 tables decide which hosts are dropped, so the version is an input.

    A lock bump that moves the tables is a sanitization change with no source
    byte to show for it — the same argument that put `DEFAULT_MODEL_ID@revision` in
    the hash, applied to the table the canonicaliser reads.
    """
    config = {"promptguard_threshold": 0.85}
    original_revision = sanitizer_revision.derive_sanitizer_revision(config)

    monkeypatch.setattr(sanitizer_revision.idna, "__version__", "0.0-test")

    assert sanitizer_revision.derive_sanitizer_revision(config) != original_revision
