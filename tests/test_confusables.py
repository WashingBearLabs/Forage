"""The generated confusable fold tables, and the generator that keeps them true.

``feature-structural-scan-forms`` US-004. Nothing is wired into stage 2 yet; this
module pins the *tables*:

* **Currency.** ``pipeline/confusables.py`` is byte-for-byte what the generator
  produces from the pinned data, so a hand edit or a data bump without a regen
  fails here.
* **Refusals.** Unpinned data and malformed supplement rows are refused.
* **ASCII never changes** under the full fold, on random and corpus text.
* **An independent oracle.** A hand-written table of Cyrillic and Greek
  look-alikes per Latin letter, deliberately *not* derived from TR39, folds to
  the intended letter through PRE_NFKC_TABLE -> NFKC -> FOLD_TABLE.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module because it reads
corpus records; ``tests/test_corpus_lint.py`` requires the marker in every
corpus test module.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pipeline import confusables
from pipeline.confusables import AMBIGUOUS_IL, FOLD_TABLE, PRE_NFKC_TABLE
from scripts.generate_confusables import (
    CONFUSABLES_PATH,
    OUTPUT_PATH,
    PINNED_SHA256,
    REPO_ROOT,
    SUPPLEMENT_PATH,
    ConfusablesError,
    build_pre_nfkc_table,
    drift_report,
    load_confusables,
    parse_confusables,
    parse_supplement,
    render_module,
)
from tests.corpus_stage2 import stage2_hits

_PRE = str.maketrans(PRE_NFKC_TABLE)
_FOLD = str.maketrans(FOLD_TABLE)
_FOLD_I = str.maketrans({**FOLD_TABLE, **dict.fromkeys(AMBIGUOUS_IL, "i")})


def _u(hexes: str) -> str:
    """Build a string from space-separated hex code points (no literal glyphs)."""
    return "".join(chr(int(part, 16)) for part in hexes.split())


def fold(text: str, *, reading: str = "l") -> str:
    """The documented order: PRE_NFKC_TABLE, NFKC, then the fold table."""
    normalized = unicodedata.normalize("NFKC", text.translate(_PRE))
    return normalized.translate(_FOLD_I if reading == "i" else _FOLD)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in cast("dict[str, Any]", value).values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in cast("list[Any]", value):
            yield from _strings(item)


def _corpus_texts(genre: str) -> list[str]:
    path = REPO_ROOT / "tests" / "corpus" / "benign" / f"{genre}.jsonl"
    texts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        texts.extend(_strings(json.loads(line)["payload"]))
    return texts


# --------------------------------------------------------------------------
# Currency and refusals
# --------------------------------------------------------------------------


def test_committed_module_is_current() -> None:
    assert drift_report(OUTPUT_PATH, render_module()) is None


def test_drift_is_reported_for_a_stale_module(tmp_path: Path) -> None:
    stale = tmp_path / "confusables.py"
    stale.write_text(render_module() + "# hand edit\n", encoding="utf-8")
    report = drift_report(stale, render_module())
    assert report is not None
    assert "uv run python -m scripts.generate_confusables" in report


def test_check_mode_exits_zero_on_the_committed_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.generate_confusables", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_committed_data_matches_the_pin() -> None:
    assert load_confusables().startswith("# confusables.txt")
    assert confusables.CONFUSABLES_SHA256 == PINNED_SHA256


def test_a_data_file_with_the_wrong_sha256_is_refused(tmp_path: Path) -> None:
    tampered = tmp_path / "confusables.txt"
    tampered.write_bytes(CONFUSABLES_PATH.read_bytes() + b"0430 ;\t0061 ;\tMA\n")
    with pytest.raises(ConfusablesError, match="pins"):
        load_confusables(tampered)
    with pytest.raises(ConfusablesError, match="pins"):
        render_module(confusables_path=tampered)


# --------------------------------------------------------------------------
# The supplement
# --------------------------------------------------------------------------


def test_every_committed_supplement_row_has_a_reason() -> None:
    rows = [
        line.split("\t")
        for line in SUPPLEMENT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(rows) >= 15
    assert all(len(row) == 3 and row[2].strip() for row in rows)
    assert len(parse_supplement(SUPPLEMENT_PATH.read_text(encoding="utf-8"))) == len(
        rows
    )


@pytest.mark.parametrize(
    "row",
    [
        "U+0430\ta",  # no reason column
        "U+0430\ta\t   ",  # blank reason
        "U+0430\t\treason",  # empty target
        "U+0430\t" + _u("03B1") + "\treason",  # non-ASCII target
        "U+0430\ta1\treason",  # not letters
        "U+0061\tb\treason",  # ASCII source
        "U+D800\ta\treason",  # surrogate
        "U+110000\ta\treason",  # beyond Unicode
        "0430\ta\treason",  # unprefixed code point
        "U+043a\ta\treason",  # lowercase hex
        "U+0430\ta\treason\textra",  # too many columns
    ],
)
def test_malformed_supplement_rows_are_refused(row: str) -> None:
    with pytest.raises(ConfusablesError):
        parse_supplement(row + "\n")


def test_duplicate_supplement_sources_are_refused() -> None:
    with pytest.raises(ConfusablesError, match="duplicate"):
        parse_supplement("U+0430\ta\tone\nU+0430\to\ttwo\n")


def test_supplement_comments_and_blank_lines_are_ignored() -> None:
    assert parse_supplement("# note\n\nU+043A\tk\treason\n") == {_u("043A"): "k"}


def test_supplement_wins_over_tr39_and_is_merged() -> None:
    # TR39 maps U+043A to a non-ASCII kra; only the supplement gives it a letter.
    assert FOLD_TABLE[_u("043A")] == "k"


def test_tr39_parser_counts_multi_code_point_sources() -> None:
    parsed = parse_confusables("0430 0301 ;\t0061 ;\tMA\n0430 ;\t0061 ;\tMA\n")
    assert parsed.skipped_multi_code_point == 1
    assert parsed.table == {_u("0430"): "a"}


def test_tr39_parser_drops_ascii_sources_and_non_ascii_prototypes() -> None:
    parsed = parse_confusables("0049 ;\t006C ;\tMA\n043A ;\t0138 ;\tMA\n")
    assert parsed.table == {}


# --------------------------------------------------------------------------
# The generated module
# --------------------------------------------------------------------------


def test_module_has_no_literal_non_ascii_and_records_the_skip_count() -> None:
    source = OUTPUT_PATH.read_text(encoding="utf-8")
    assert source.isascii()
    assert "do not hand-edit" in source
    assert "Unicode 18.0.0" in source
    assert PINNED_SHA256 in source
    assert "Multi-code-point sources skipped: 0." in source
    assert confusables.SKIPPED_MULTI_CODE_POINT_SOURCES == 0


def test_tables_hold_only_single_non_ascii_sources_and_ascii_prototypes() -> None:
    for table in (FOLD_TABLE, PRE_NFKC_TABLE):
        for source, prototype in table.items():
            assert len(source) == 1 and not source.isascii()
            assert prototype and prototype.isascii()


def test_pre_nfkc_table_is_generated_from_the_fold_table() -> None:
    assert build_pre_nfkc_table(FOLD_TABLE) == PRE_NFKC_TABLE
    # Lunate sigma maps to c in TR39, but NFKC would first make it a final sigma.
    assert unicodedata.normalize("NFKC", _u("03F2")) == _u("03C2")
    assert PRE_NFKC_TABLE[_u("03F2")] == "c"
    assert PRE_NFKC_TABLE[_u("03F9")] == "C"


def test_long_s_is_not_pre_mapped() -> None:
    # TR39 reads long s as f, but NFKC already makes it the ASCII s. Applying
    # the table before NFKC must not turn it into f.
    assert _u("017F") in FOLD_TABLE
    assert _u("017F") not in PRE_NFKC_TABLE
    assert fold(_u("017F")) == "s"


def test_ambiguous_il_is_the_prototype_l_class() -> None:
    assert {_u("0406"), _u("0399"), _u("04C0")} <= AMBIGUOUS_IL
    assert all(source in FOLD_TABLE for source in AMBIGUOUS_IL)
    assert {s for s, p in FOLD_TABLE.items() if p == "l"} == AMBIGUOUS_IL
    # Neither reading breaks the other.
    assert fold(_u("0406") + "gnore") == "lgnore"
    assert fold(_u("0406") + "gnore", reading="i") == "ignore"


# --------------------------------------------------------------------------
# ASCII never changes
# --------------------------------------------------------------------------


def test_no_table_key_is_ascii() -> None:
    assert not any(key.isascii() for key in (*FOLD_TABLE, *PRE_NFKC_TABLE))


def test_fold_never_changes_random_ascii() -> None:
    rng = random.Random(20261006)
    for _ in range(500):
        text = "".join(chr(rng.randrange(128)) for _ in range(rng.randrange(1, 80)))
        assert fold(text) == text
        assert fold(text, reading="i") == text


def test_fold_never_changes_ascii_text_in_the_benign_corpus() -> None:
    seen = 0
    for path in sorted((REPO_ROOT / "tests" / "corpus" / "benign").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            for text in _strings(json.loads(line)["payload"]):
                if text.isascii():
                    seen += 1
                    assert fold(text) == text
                    assert fold(text, reading="i") == text
    assert seen > 100


# --------------------------------------------------------------------------
# The independent oracle
# --------------------------------------------------------------------------

# Hand-written from how the glyphs look, not from confusables.txt. Each letter
# maps to Cyrillic and Greek characters a reader takes for it, in both cases
# where the look-alike exists. Case is compared insensitively.
_ORACLE: dict[str, str] = {
    "a": _u("0430 0410 03B1 0391"),
    "b": _u("0412 0392 03B2"),
    "c": _u("0441 0421 03F2 03F9"),
    "d": _u("0501"),
    "e": _u("0435 0415 03B5 0395"),
    "g": _u("0434 0414"),
    "h": _u("043D 041D 0397 04BB"),
    "i": _u("0456 0406 0399"),
    "j": _u("0458 0408"),
    "k": _u("043A 041A 03BA 039A"),
    "m": _u("043C 041C 039C"),
    "n": _u("043F 041F 0438 0418 043B 041B 03B7 03C0 03A0"),
    "o": _u("043E 041E 03BF 039F"),
    "p": _u("0440 0420 03C1 03A1"),
    "q": _u("051B"),
    "s": _u("0455 0405"),
    "t": _u("0442 0422 03C4 03A4"),
    "u": _u("03BC 03C5"),
    "v": _u("03BD"),
    "w": _u("051D 0448"),
    "x": _u("0445 0425 03C7 03A7"),
    "y": _u("0443 0423 03A5"),
    "z": _u("0396"),
}

# The oracle letters that read as capital I are ambiguous; they are checked
# under the `i` reading instead of the default `l` one.
_ORACLE_PAIRS = [
    (letter, char)
    for letter, chars in _ORACLE.items()
    for char in chars
    if char not in AMBIGUOUS_IL
]
_ORACLE_AMBIGUOUS = [
    (letter, char)
    for letter, chars in _ORACLE.items()
    for char in chars
    if char in AMBIGUOUS_IL
]


@pytest.mark.parametrize(("letter", "char"), _ORACLE_PAIRS)
def test_oracle_lookalikes_fold_to_their_letter(letter: str, char: str) -> None:
    assert fold(char).lower() == letter, f"U+{ord(char):04X} -> {fold(char)!r}"


@pytest.mark.parametrize(("letter", "char"), _ORACLE_AMBIGUOUS)
def test_oracle_ambiguous_members_fold_under_the_i_reading(
    letter: str, char: str
) -> None:
    assert fold(char, reading="i").lower() == letter


def test_oracle_covers_the_named_probes() -> None:
    # k t p m i n d l (Cyrillic), kappa tau eta mu epsilon beta, lunate sigma.
    probes = _u(
        "043A 0442 043F 043C 0438 043D 0434 043B 03BA 03C4 03B7 03BC 03B5 03B2 03F2"
    )
    covered = "".join(_ORACLE.values())
    for char in probes:
        assert char in covered, f"U+{ord(char):04X}"


def test_a_word_in_mixed_scripts_folds_to_latin() -> None:
    word = _u("0456") + "gn" + _u("043E") + "r" + _u("0435") + " previous"
    assert fold(word) == "ignore previous"
    capital = _u("0406") + "gnore previous"
    assert fold(capital) == "lgnore previous"
    assert fold(capital, reading="i") == "ignore previous"


# --------------------------------------------------------------------------
# Supplement vs the multilingual benign genre
# --------------------------------------------------------------------------


def test_supplement_leaves_multilingual_benign_verdicts() -> None:
    """Folding the multilingual genre moves no stage-2 pattern verdict."""
    texts = _corpus_texts("multilingual")
    assert len(texts) > 10
    moved = [
        text[:40]
        for text in texts
        for reading in ("l", "i")
        if stage2_hits(fold(text, reading=reading)) != stage2_hits(text)
    ]
    assert moved == []
