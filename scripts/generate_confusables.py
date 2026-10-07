"""Generate ``pipeline/confusables.py`` from Unicode's own confusables data.

``feature-structural-scan-forms`` US-004. Stage 2 will scan a *folded* form of
the text in which look-alike characters (Cyrillic or Greek letters, say) read
as the Latin letters they imitate. Those tables are far too large and too
security-relevant to hand-write, so they are generated reproducibly from two
committed inputs:

* ``scripts/data/unicode/confusables.txt`` — UTS #39 ``confusables.txt``,
  pinned by sha256 (:data:`PINNED_SHA256`). The generator **refuses** any other
  bytes: a silently-updated data file would change what stage 2 blocks without
  a code change to point at.
* ``scripts/data/unicode/forage_supplement.tsv`` — the owner-reviewed rows TR39
  has no ASCII mapping for. Merged after TR39; malformed rows are refused.

Run it with::

    uv run python -m scripts.generate_confusables           # write the module
    uv run python -m scripts.generate_confusables --check   # verify, write nothing

``tests/test_confusables.py`` calls :func:`drift_report` directly, so the drift
gate runs on every ``uv run pytest``.

**What is kept.** Only entries with a single-code-point, non-ASCII source and an
all-ASCII prototype (``str.maketrans`` keys must be single characters, so the
count of multi-code-point sources skipped is recorded in the output header; it
is 0 for Unicode 18.0.0). ASCII sources are never kept, so ASCII text never
changes.

**The pre-NFKC table.** Stage 2 folds as ``PRE_NFKC_TABLE`` -> NFKC ->
``FOLD_TABLE``. NFKC can destroy a TR39 mapping: Greek lunate sigma maps to
``c`` in TR39, but NFKC first turns it into a plain sigma, which has no mapping.
:func:`build_pre_nfkc_table` computes, from the merged table and *this
interpreter's* ``unicodedata``, every source whose NFKC form differs and still
holds a non-ASCII character with no mapping of its own. An NFKC form that is
already ASCII (long ``s`` -> ``s``) is left alone — mapping it first would turn
it into ``f``, which is why the order is not reversed globally. Because the
result depends on the interpreter's Unicode version (recorded in the header), a
Python upgrade can legitimately fail ``--check``; regenerate and review.

**The I/l ambiguity.** TR39 maps capital-I look-alikes to ``l``, but the same
glyphs read as ``I``. ``FOLD_TABLE`` keeps the TR39 prototype; ``AMBIGUOUS_IL``
lists every source whose merged prototype is ``l`` so a second folded form can
read them as ``i``. Neither reading overrides the other.

test_mapping:
  scripts/generate_confusables.py: tests/test_confusables.py
  pipeline/confusables.py: tests/test_confusables.py
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "scripts" / "data" / "unicode"
CONFUSABLES_PATH = DATA_DIR / "confusables.txt"
SUPPLEMENT_PATH = DATA_DIR / "forage_supplement.tsv"
OUTPUT_PATH = REPO_ROOT / "pipeline" / "confusables.py"

UNICODE_VERSION = "18.0.0"
PINNED_SHA256 = "6ed3ee967c9dfdf6677d563c9985182fbc50a2efb7d6059cd57b2e2ce18f5b92"

REGEN_COMMAND = "uv run python -m scripts.generate_confusables"

_CODEPOINT_RE = re.compile(r"^U\+([0-9A-F]{4,6})$")
_AMBIGUOUS_PROTOTYPE = "l"


class ConfusablesError(RuntimeError):
    """An input to the generator is not acceptable."""


@dataclass(frozen=True)
class Tr39Entries:
    """The kept TR39 entries and what was dropped to get them."""

    table: dict[str, str]
    skipped_multi_code_point: int


def load_confusables(
    path: Path = CONFUSABLES_PATH, expected_sha256: str = PINNED_SHA256
) -> str:
    """Return the text of *path*, refusing bytes that do not match the pin."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ConfusablesError(
            f"{_relative(path)} has sha256 {digest}, but the generator pins "
            f"{expected_sha256}. Refusing to generate from unpinned data; if "
            "the update is intended, review the diff and change PINNED_SHA256, "
            "UNICODE_VERSION and scripts/data/unicode/README together."
        )
    return raw.decode("utf-8-sig")


def _chars(field: str, *, where: str) -> str:
    """Decode a space-separated hex code point list into a string."""
    try:
        return "".join(chr(int(part, 16)) for part in field.split())
    except (ValueError, OverflowError) as exc:
        raise ConfusablesError(f"{where}: bad code point list {field!r}") from exc


def parse_confusables(text: str) -> Tr39Entries:
    """Keep single-code-point non-ASCII sources with an all-ASCII prototype."""
    table: dict[str, str] = {}
    skipped = 0
    for number, line in enumerate(text.splitlines(), start=1):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        where = f"confusables.txt:{number}"
        fields = [field.strip() for field in body.split(";")]
        if len(fields) < 3:
            raise ConfusablesError(f"{where}: expected 'source ; target ; type'")
        source = _chars(fields[0], where=where)
        prototype = _chars(fields[1], where=where)
        if len(source) != 1:
            skipped += 1
            continue
        if source.isascii() or not prototype or not prototype.isascii():
            continue
        if source in table:
            raise ConfusablesError(f"{where}: duplicate source U+{ord(source):04X}")
        table[source] = prototype
    return Tr39Entries(table=table, skipped_multi_code_point=skipped)


def parse_supplement(text: str) -> dict[str, str]:
    """Parse the reviewed supplement, refusing every malformed row."""
    table: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        where = f"forage_supplement.tsv:{number}"
        fields = line.split("\t")
        if len(fields) != 3:
            raise ConfusablesError(f"{where}: expected codepoint<TAB>latin<TAB>reason")
        codepoint, latin, reason = fields
        match = _CODEPOINT_RE.match(codepoint)
        if match is None:
            raise ConfusablesError(f"{where}: bad code point {codepoint!r}")
        value = int(match.group(1), 16)
        if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            raise ConfusablesError(f"{where}: {codepoint} is not a scalar value")
        source = chr(value)
        if source.isascii():
            raise ConfusablesError(f"{where}: source {codepoint} is ASCII")
        if not latin or not latin.isascii() or not latin.isalpha():
            raise ConfusablesError(
                f"{where}: target {latin!r} must be non-empty ASCII letters"
            )
        if not reason.strip():
            raise ConfusablesError(f"{where}: every row needs a reason")
        if source in table:
            raise ConfusablesError(f"{where}: duplicate source {codepoint}")
        table[source] = latin
    return table


def merge_tables(tr39: dict[str, str], supplement: dict[str, str]) -> dict[str, str]:
    """Merge the supplement after TR39: a supplement row wins on conflict."""
    return {**tr39, **supplement}


def build_pre_nfkc_table(fold: dict[str, str]) -> dict[str, str]:
    """Return the sources NFKC would carry away from their mapping."""
    pre: dict[str, str] = {}
    for source, prototype in fold.items():
        normalized = unicodedata.normalize("NFKC", source)
        if normalized == source:
            continue
        if any(not char.isascii() and char not in fold for char in normalized):
            pre[source] = prototype
    return pre


def build_ambiguous_il(fold: dict[str, str]) -> list[str]:
    """Return every source whose merged prototype is ``l``, in code point order."""
    return sorted(src for src, proto in fold.items() if proto == _AMBIGUOUS_PROTOTYPE)


def _escape(text: str) -> str:
    """Return *text* as the body of an all-ASCII, ruff-format-stable literal."""
    parts: list[str] = []
    for char in text:
        value = ord(char)
        if char.isascii() and char.isalnum():
            parts.append(char)
        elif value <= 0xFFFF:
            parts.append(f"\\u{value:04x}")
        else:
            parts.append(f"\\U{value:08x}")
    return "".join(parts)


def _render_dict(name: str, table: dict[str, str]) -> str:
    rows = "".join(
        f'    "{_escape(src)}": "{_escape(proto)}",\n'
        for src, proto in sorted(table.items())
    )
    return f"{name}: dict[str, str] = {{\n{rows}}}\n"


def _render_set(name: str, members: list[str]) -> str:
    rows = "".join(f'        "{_escape(src)}",\n' for src in members)
    return f"{name}: frozenset[str] = frozenset(\n    {{\n{rows}    }}\n)\n"


def render_module(
    confusables_path: Path = CONFUSABLES_PATH,
    supplement_path: Path = SUPPLEMENT_PATH,
    expected_sha256: str = PINNED_SHA256,
) -> str:
    """Return the text ``pipeline/confusables.py`` must contain."""
    tr39 = parse_confusables(load_confusables(confusables_path, expected_sha256))
    supplement = parse_supplement(supplement_path.read_text(encoding="utf-8"))
    fold = merge_tables(tr39.table, supplement)
    pre_nfkc = build_pre_nfkc_table(fold)
    ambiguous = build_ambiguous_il(fold)
    nfkc_version = unicodedata.unidata_version
    header = (
        '"""Unicode confusable fold tables. GENERATED - do not hand-edit.\n'
        "\n"
        f"Regenerate with: {REGEN_COMMAND}\n"
        "\n"
        "Source: UTS #39 confusables.txt, Unicode "
        f"{UNICODE_VERSION}, sha256\n"
        f"{expected_sha256},\n"
        "merged with scripts/data/unicode/forage_supplement.tsv.\n"
        f"Multi-code-point sources skipped: {tr39.skipped_multi_code_point}.\n"
        f"Supplement rows merged: {len(supplement)}.\n"
        f"NFKC Unicode version at generation: {nfkc_version}.\n"
        "\n"
        "Fold order: PRE_NFKC_TABLE, then NFKC, then FOLD_TABLE. FOLD_TABLE maps\n"
        "capital-I look-alikes to l (the TR39 prototype); AMBIGUOUS_IL lists every\n"
        "source in that class so a second form can read them as i.\n"
        '"""\n'
        "\n"
        f'UNICODE_VERSION: str = "{UNICODE_VERSION}"\n'
        "CONFUSABLES_SHA256: str = (\n"
        f'    "{expected_sha256}"\n'
        ")\n"
        f"SKIPPED_MULTI_CODE_POINT_SOURCES: int = {tr39.skipped_multi_code_point}\n"
        f'NFKC_UNICODE_VERSION: str = "{nfkc_version}"\n'
        "\n"
    )
    return (
        header
        + _render_dict("FOLD_TABLE", fold)
        + "\n"
        + _render_dict("PRE_NFKC_TABLE", pre_nfkc)
        + "\n"
        + _render_set("AMBIGUOUS_IL", ambiguous)
    )


def drift_report(path: Path, rendered: str) -> str | None:
    """Return a regeneration message if *path* is not *rendered*, else ``None``."""
    if not path.exists():
        return (
            f"{_relative(path)} is missing — generate it with:\n    {REGEN_COMMAND}\n"
        )
    current = path.read_text(encoding="utf-8")
    if current == rendered:
        return None
    diff = list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{_relative(path)} (committed)",
            tofile=f"{_relative(path)} (regenerated)",
            n=1,
        )
    )
    excerpt = "".join(diff[:40])
    elided = "" if len(diff) <= 40 else f"\n... {len(diff) - 40} more diff lines\n"
    return (
        f"{_relative(path)} is out of date — the generator produces something "
        f"else.\nRegenerate it with:\n    {REGEN_COMMAND}\n{excerpt}{elided}"
    )


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.generate_confusables",
        description="Generate pipeline/confusables.py from pinned Unicode data.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed module is current; write nothing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    args = build_parser().parse_args(argv)
    try:
        rendered = render_module()
    except ConfusablesError as exc:
        print(f"generate_confusables: {exc}")
        return 2
    if args.check:
        problem = drift_report(OUTPUT_PATH, rendered)
        if problem is not None:
            print(problem)
            return 1
        print("generate_confusables OK — committed module is current")
        return 0
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {_relative(OUTPUT_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
