"""The corpus record type, its JSONL loader and the lint that gates a commit.

A record is data, never quoted: every failure this module reports is a
``CorpusLintError`` carrying the record id and the rule name only, so a lint
run cannot echo a payload, a marker or a secret-shaped value into a log.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit

from pipeline.stage1_extraction import extract_html, normalize_text
from scripts.corpus import vocab

_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"(atk|ben)-[0-9]{4}")
_FAKE_KEY_OVERLONG: Final[re.Pattern[str]] = re.compile(
    re.escape(vocab.FAKE_KEY_PREFIX)
    + rf"[A-Za-z0-9_\-]{{{vocab.FAKE_KEY_MAX_BODY + 1},}}"
)
_HTML_URL_ATTRIBUTE: Final[re.Pattern[str]] = re.compile(
    r"""\b(?:href|src|content)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)
_PRIVATE_NETWORKS: Final[tuple[ipaddress.IPv4Network, ...]] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
_IPV6_ZONE_NETWORKS: Final[tuple[ipaddress.IPv6Network, ...]] = (
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("2001:db8::/32"),
)

ParamValue = str | int | float | bool | None


class CorpusLintError(Exception):
    """One lint failure: the record id and the rule name, never the content."""

    def __init__(self, record_id: str, rule: str) -> None:
        super().__init__(record_id, rule)
        self.record_id = record_id
        self.rule = rule

    def __str__(self) -> str:
        return f"{self.record_id}: {self.rule}"


@dataclass(frozen=True, slots=True)
class CorpusRecord:
    """One corpus record, as read. ``keys`` is the key order of the source line."""

    id: str
    kind: str
    category: str
    surface: str
    payload: Mapping[str, str]
    marker: str | None
    pinned: tuple[str, ...] | None
    pinned_reason: str | None
    source: Mapping[str, str | None]
    lang: str
    params: Mapping[str, ParamValue]
    notes: str
    keys: tuple[str, ...]

    def strings(self) -> Iterator[str]:
        """Every string the record carries — what the secret-shape rule scans."""
        yield from (
            self.id,
            self.kind,
            self.category,
            self.surface,
            self.lang,
            self.notes,
        )
        yield from self.payload.values()
        if self.marker is not None:
            yield self.marker
        if self.pinned_reason is not None:
            yield self.pinned_reason
        yield from (value for value in self.source.values() if value is not None)
        yield from (value for value in self.params.values() if isinstance(value, str))


def _str_mapping(value: object, record_id: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CorpusLintError(record_id, "types")
    items = cast(dict[object, object], value)
    out: dict[str, str] = {}
    for key, item in items.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise CorpusLintError(record_id, "types")
        out[key] = item
    return out


def record_from_mapping(obj: Mapping[str, object]) -> CorpusRecord:
    """Build a record from one decoded JSON object; wrong types raise ``types``."""
    raw_id = obj.get("id")
    record_id = raw_id if isinstance(raw_id, str) else "<no-id>"

    def text(key: str, *, default: str = "") -> str:
        value = obj.get(key, default)
        if not isinstance(value, str):
            raise CorpusLintError(record_id, "types")
        return value

    def optional_text(key: str) -> str | None:
        value = obj.get(key)
        if value is not None and not isinstance(value, str):
            raise CorpusLintError(record_id, "types")
        return value

    raw_pinned = obj.get("pinned")
    pinned: tuple[str, ...] | None = None
    if raw_pinned is not None:
        if not isinstance(raw_pinned, list):
            raise CorpusLintError(record_id, "types")
        entries = cast(list[object], raw_pinned)
        if not all(isinstance(entry, str) for entry in entries):
            raise CorpusLintError(record_id, "types")
        pinned = tuple(cast(list[str], entries))

    raw_source = obj.get("source", {})
    if not isinstance(raw_source, dict):
        raise CorpusLintError(record_id, "types")
    source: dict[str, str | None] = {}
    for key, value in cast(dict[object, object], raw_source).items():
        if not isinstance(key, str) or (
            value is not None and not isinstance(value, str)
        ):
            raise CorpusLintError(record_id, "types")
        source[key] = value

    raw_params = obj.get("params", {})
    if not isinstance(raw_params, dict):
        raise CorpusLintError(record_id, "types")
    params: dict[str, ParamValue] = {}
    for key, value in cast(dict[object, object], raw_params).items():
        if not isinstance(key, str) or not (
            value is None or isinstance(value, str | int | float | bool)
        ):
            raise CorpusLintError(record_id, "types")
        params[key] = value

    return CorpusRecord(
        id=record_id,
        kind=text("kind"),
        category=text("category"),
        surface=text("surface"),
        payload=_str_mapping(obj.get("payload", {}), record_id),
        marker=optional_text("marker"),
        pinned=pinned,
        pinned_reason=optional_text("pinned_reason"),
        source=source,
        lang=text("lang"),
        params=params,
        notes=text("notes"),
        keys=tuple(obj.keys()),
    )


def record_to_mapping(record: CorpusRecord) -> dict[str, object]:
    """The record as a JSON object, keys in the order the source line had them."""
    values: dict[str, object] = {
        "id": record.id,
        "kind": record.kind,
        "category": record.category,
        "surface": record.surface,
        "payload": dict(record.payload),
        "marker": record.marker,
        "pinned": None if record.pinned is None else list(record.pinned),
        "pinned_reason": record.pinned_reason,
        "source": dict(record.source),
        "lang": record.lang,
        "params": dict(record.params),
        "notes": record.notes,
    }
    return {key: values[key] for key in record.keys if key in values}


def _jsonl_files(root: Path) -> list[Path]:
    return [
        path
        for subdir in ("attacks", "benign")
        for path in sorted((root / subdir).glob("*.jsonl"))
    ]


def load_corpus(root: Path = vocab.TESTS_CORPUS_ROOT) -> tuple[CorpusRecord, ...]:
    """Every record under ``root``: attacks then benign, file order then line order.

    A line that is not a JSON object raises ``CorpusLintError`` naming the file
    and line, never the line's text.
    """
    records: list[CorpusRecord] = []
    for path in _jsonl_files(root):
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                where = f"{path.parent.name}/{path.name}:{number}"
                try:
                    decoded: object = json.loads(line)
                except json.JSONDecodeError:
                    raise CorpusLintError(where, "json") from None
                if not isinstance(decoded, dict):
                    raise CorpusLintError(where, "json")
                records.append(record_from_mapping(cast(dict[str, object], decoded)))
    return tuple(records)


def page_document(record: CorpusRecord) -> str:
    """The HTML document a ``page`` record stands for — what ``/retrieve`` serves."""
    payload = record.payload
    return (
        "<!DOCTYPE html><html><head>"
        f"<title>{html.escape(payload.get('title', ''))}</title>"
        f"{payload.get('head_html', '')}</head>"
        f"<body>{payload.get('body_html', '')}</body></html>"
    )


def normalise_for_leak_check(text: str) -> str:
    """The one normalisation both the lint and the leak check apply."""
    return normalize_text(text).casefold()


# ---------------------------------------------------------------------------
# Per-record lint rules. Each returns True when the record passes.
# ---------------------------------------------------------------------------


def rule_key_order(record: CorpusRecord) -> bool:
    """Keys are known, required ones present, and in ``RECORD_KEYS`` order."""
    if not set(record.keys) >= vocab.REQUIRED_RECORD_KEYS:
        return False
    if len(set(record.keys)) != len(record.keys):
        return False
    positions = [
        vocab.RECORD_KEYS.index(key) if key in vocab.RECORD_KEYS else -1
        for key in record.keys
    ]
    return -1 not in positions and positions == sorted(positions)


def rule_id_prefix(record: CorpusRecord) -> bool:
    """``atk-NNNN`` for attacks, ``ben-NNNN`` for benign."""
    prefix = vocab.ID_PREFIXES.get(record.kind)
    return (
        prefix is not None
        and _ID_PATTERN.fullmatch(record.id) is not None
        and record.id.startswith(f"{prefix}-")
    )


def rule_category(record: CorpusRecord) -> bool:
    """The category is in the vocabulary the record's kind selects."""
    return record.category in vocab.CATEGORIES_BY_KIND.get(record.kind, ())


def rule_payload_keys(record: CorpusRecord) -> bool:
    """The surface is known and its payload keys are exactly the allowed ones."""
    spec = vocab.PAYLOAD_KEYS.get(record.surface)
    if spec is None:
        return False
    required, optional = spec
    keys = set(record.payload)
    if not set(required) <= keys <= set(required) | set(optional):
        return False
    kind = record.payload.get("content_kind")
    return kind is None or kind in vocab.CONTENT_KINDS


def rule_marker_iff_attack(record: CorpusRecord) -> bool:
    """Attacks carry a marker (>= 12 chars, one line); benign records carry none."""
    if record.kind != "attack":
        return record.marker is None
    return (
        record.marker is not None
        and len(record.marker) >= vocab.MIN_MARKER_LENGTH
        and "\n" not in record.marker
        and "\r" not in record.marker
    )


def _payload_forms(record: CorpusRecord) -> list[str]:
    forms = [value for value in record.payload.values()]
    forms.extend(html.unescape(value) for value in record.payload.values())
    if record.surface == "page":
        url = record.payload.get("url")
        forms.append(extract_html(page_document(record), url).raw_text)
    return forms


def rule_marker_in_payload(record: CorpusRecord) -> bool:
    """The normalised marker occurs in a normalised raw or post-pipeline form."""
    if record.marker is None:
        return True
    needle = normalise_for_leak_check(record.marker)
    if not needle:
        return False
    return any(
        needle in normalise_for_leak_check(form) for form in _payload_forms(record)
    )


def rule_pinned(record: CorpusRecord) -> bool:
    """``pinned`` is null or a non-empty, duplicate-free list of known outcomes."""
    if record.pinned is None:
        return True
    return (
        len(record.pinned) > 0
        and len(set(record.pinned)) == len(record.pinned)
        and all(outcome in vocab.PINNED_OUTCOMES for outcome in record.pinned)
    )


def rule_pinned_reason(record: CorpusRecord) -> bool:
    """``pinned_reason`` is a non-empty string exactly when ``pinned`` is set."""
    if record.pinned is None:
        return record.pinned_reason is None
    return bool(record.pinned_reason and record.pinned_reason.strip())


def _record_urls(record: CorpusRecord) -> Iterator[str]:
    if "url" in record.payload:
        yield record.payload["url"]
    for field in ("head_html", "body_html"):
        for match in _HTML_URL_ATTRIBUTE.finditer(record.payload.get(field, "")):
            value = html.unescape(
                next(group for group in match.groups() if group is not None)
            )
            value = value.strip()
            lowered = value.lower()
            if (
                "://" in value
                or value.startswith("//")
                or lowered.startswith(
                    tuple(f"{scheme}:" for scheme in vocab.EXCEPTION_SCHEMES)
                )
            ):
                yield value


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in _PRIVATE_NETWORKS
    )


def _is_ipv6_zone_host(host: str) -> bool:
    """A link-local or documentation IPv6 literal, with or without a zone id."""
    try:
        address = ipaddress.ip_address(host.partition("%")[0])
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv6Address) and any(
        address in network for network in _IPV6_ZONE_NETWORKS
    )


def _is_reserved_host(host: str) -> bool:
    host = host.rstrip(".").lower()
    if any(
        host == name or host.endswith(f".{name}") for name in vocab.RESERVED_DOMAINS
    ):
        return True
    return host.rsplit(".", 1)[-1] in vocab.RESERVED_TLDS


def _url_allowed(url: str, exception: ParamValue) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme.lower() in vocab.EXCEPTION_SCHEMES:
        return exception == "scheme"
    if host is None:
        return False
    if _is_private_ip(host):
        return exception == "private_ip"
    if _is_ipv6_zone_host(host):
        return exception == "ipv6_zone"
    return _is_reserved_host(host)


def rule_reserved_urls(record: CorpusRecord) -> bool:
    """Every URL is RFC 2606 reserved, or a declared scheme / private-IP exception."""
    exception = record.params.get("url_exception")
    return all(_url_allowed(url, exception) for url in _record_urls(record))


def rule_secret_shapes(record: CorpusRecord) -> bool:
    """No secret-shaped substring anywhere; fake exfil keys stay short."""
    for value in record.strings():
        if any(pattern.search(value) for pattern in vocab.SECRET_PATTERNS.values()):
            return False
        if _FAKE_KEY_OVERLONG.search(value):
            return False
    return True


def rule_sizes(record: CorpusRecord) -> bool:
    """Payload fields fit the pipeline's caps for the surface."""
    payload = record.payload
    if record.surface == "search":
        return all(
            len(payload.get(field, "")) <= cap
            for field, cap in vocab.SEARCH_FIELD_CAPS.items()
        )
    if record.surface == "page":
        size = len(page_document(record).encode("utf-8"))
        return size <= vocab.PAGE_HTML_MAX_BYTES
    if record.surface == "text":
        return len(payload.get("text", "")) <= vocab.TEXT_MAX_CHARS
    return True


def rule_lang(record: CorpusRecord) -> bool:
    """``lang`` has BCP-47 shape."""
    return vocab.BCP47_PATTERN.fullmatch(record.lang) is not None


def rule_source(record: CorpusRecord) -> bool:
    """Known source keys and kind; third-party needs a permitted licence + revision."""
    source = record.source
    if not set(source) <= set(vocab.SOURCE_KEYS):
        return False
    if source.get("kind") not in vocab.SOURCE_KINDS:
        return False
    framing = source.get("framing")
    if framing is not None and framing not in vocab.FRAMINGS:
        return False
    if source.get("kind") == "third_party":
        return source.get("licence") in vocab.THIRD_PARTY_LICENCES and bool(
            source.get("revision")
        )
    return True


def rule_params_keys(record: CorpusRecord) -> bool:
    """``params`` keys are in the allowlist for the record's category or genre."""
    allowed = vocab.PARAMS_ALLOWED.get(record.category, frozenset[str]())
    return set(record.params) <= allowed


def rule_params_values(record: CorpusRecord) -> bool:
    """``variant``, ``carrier`` and ``url_exception`` each take a known value."""
    variant = record.params.get("variant")
    if variant is not None and variant not in vocab.VARIANTS_BY_KIND.get(
        record.kind, ()
    ):
        return False
    carrier = record.params.get("carrier")
    if carrier is not None and carrier not in vocab.ATTACK_CARRIERS:
        return False
    exception = record.params.get("url_exception")
    return exception is None or exception in vocab.URL_EXCEPTIONS


LINT_RULES: Final[Mapping[str, Callable[[CorpusRecord], bool]]] = {
    "key_order": rule_key_order,
    "id_prefix": rule_id_prefix,
    "category": rule_category,
    "payload_keys": rule_payload_keys,
    "marker_iff_attack": rule_marker_iff_attack,
    "marker_in_payload": rule_marker_in_payload,
    "pinned": rule_pinned,
    "pinned_reason": rule_pinned_reason,
    "reserved_urls": rule_reserved_urls,
    "secret_shapes": rule_secret_shapes,
    "sizes": rule_sizes,
    "lang": rule_lang,
    "source": rule_source,
    "params_keys": rule_params_keys,
    "params_values": rule_params_values,
}
"""Every per-record rule by name. ``unique_ids`` is the one corpus-wide rule."""


def lint_corpus(records: Sequence[CorpusRecord]) -> list[CorpusLintError]:
    """Every failure across ``records``: per-record rules, then ``unique_ids``."""
    errors: list[CorpusLintError] = []
    seen: set[str] = set()
    for record in records:
        errors.extend(
            CorpusLintError(record.id, name)
            for name, rule in LINT_RULES.items()
            if not rule(record)
        )
        if record.id in seen:
            errors.append(CorpusLintError(record.id, "unique_ids"))
        seen.add(record.id)
    return errors
