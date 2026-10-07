"""Re-render an upstream payload into a corpus surface (spec 2 US-004).

No third-party row is committed as it came: an email body becomes a forum-post
``page``, a tool-output string an article ``page`` or a ``search`` snippet, a
short instruction a ``text`` upload. Every host the text names is rewritten to
an RFC 2606 reserved one first, so a record can never point a reader at a real
site. The category is assigned by one rule for every source: the text is
scanned over the builder's forms (``structural_scan_forms``) and takes the
first matching structural category (``variant = plain``), otherwise
``natural_language``.
"""

from __future__ import annotations

import html
import re
from typing import Final

from pipeline.stage2_structural import scan_structural_forms, structural_scan_forms
from scripts.corpus import vocab

# A URL with a scheme: the host part is rewritten, the rest kept.
_SCHEME_URL: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s?#'\"<>`)\]]+)"
)
# A bare host (``www.x.com``, an e-mail domain) under a common public TLD.
_BARE_HOST: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b((?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+"
    r"(?:com|net|org|io|co|ai|info|biz|xyz|me|app|dev|ru|cn|de|uk|us|fr|in|"
    r"site|online|top|ly|gl|edu|gov|tv|cc|eu))\b"
)
_LABEL_JUNK: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9\-]+")
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")

MIN_MARKER: Final[int] = vocab.MIN_MARKER_LENGTH
MAX_MARKER: Final[int] = 60


def _is_reserved(host: str) -> bool:
    host = host.rstrip(".").lower()
    if any(
        host == name or host.endswith(f".{name}") for name in vocab.RESERVED_DOMAINS
    ):
        return True
    return host.rsplit(".", 1)[-1] in vocab.RESERVED_TLDS


def reserved_host(host: str) -> str:
    """``www.secure-systems-252.com`` -> ``secure-systems-252.example``."""
    labels = [label for label in host.lower().rstrip(".").split(".") if label]
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) > 1:
        labels = labels[:-1]
    joined = _LABEL_JUNK.sub("-", "-".join(labels)).strip("-") or "host"
    return f"{joined[:48]}.example"


def _rewrite_authority(match: re.Match[str]) -> str:
    scheme, authority = match.group(1), match.group(2)
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host.split("]", 1)[0].lstrip("[")
    else:
        host = host.split(":", 1)[0]
    if _is_reserved(host):
        return f"{scheme}{host}"
    return f"{scheme}{reserved_host(host)}"


def _rewrite_bare(match: re.Match[str]) -> str:
    host = match.group(1)
    return host if _is_reserved(host) else reserved_host(host)


def rewrite_urls(text: str) -> str:
    """Every host ``text`` names, rewritten to a reserved one (userinfo dropped)."""
    return _BARE_HOST.sub(_rewrite_bare, _SCHEME_URL.sub(_rewrite_authority, text))


def unreserved_hosts(text: str) -> list[str]:
    """Hosts in ``text`` that are not reserved — empty after ``rewrite_urls``."""
    hosts = [
        match.group(2).rsplit("@", 1)[-1].split(":", 1)[0]
        for match in _SCHEME_URL.finditer(text)
    ]
    hosts.extend(match.group(1) for match in _BARE_HOST.finditer(text))
    return [host for host in hosts if not _is_reserved(host)]


def has_secret_shape(text: str) -> bool:
    """True when ``text`` carries any ``vocab.SECRET_PATTERNS`` shape."""
    return any(pattern.search(text) for pattern in vocab.SECRET_PATTERNS.values())


def assign_category(text: str) -> str:
    """The one category rule: the first stage-2 category that fires, else NL."""
    flags = scan_structural_forms(structural_scan_forms(text, html_parsed=False)).flags
    if flags:
        return flags[0].category
    return "natural_language"


def choose_marker(text: str) -> str | None:
    """A one-line phrase of 12-60 characters from ``text``'s longest line."""
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) >= MIN_MARKER]
    if not lines:
        return None
    line = max(lines, key=len)
    if len(line) <= MAX_MARKER:
        return line
    cut = line[:MAX_MARKER]
    space = cut.rfind(" ")
    marker = cut[:space] if space >= MIN_MARKER else cut
    return marker.strip() if len(marker.strip()) >= MIN_MARKER else None


def _paragraphs(text: str) -> str:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in block.splitlines()) + "</p>"
        for block in blocks
    )


def forum_post(url: str, subject: str, body: str) -> dict[str, str]:
    """An e-mail body re-rendered as a forum post ``page`` payload."""
    title = "Community forum thread"
    heading = f"<h2>{html.escape(subject)}</h2>" if subject.strip() else ""
    return {
        "url": url,
        "title": title,
        "head_html": "",
        "body_html": (
            f'<h1>{title}</h1><article class="post">'
            f"<p>Posted by a community member.</p>{heading}{_paragraphs(body)}"
            "</article>"
        ),
    }


def article_page(url: str, text: str) -> dict[str, str]:
    """A tool-output string re-rendered as an article ``page`` payload."""
    title = "Travel and account notes"
    return {
        "url": url,
        "title": title,
        "head_html": "",
        "body_html": f"<h1>{title}</h1><article>{_paragraphs(text)}</article>",
    }


def search_snippet(url: str, text: str) -> dict[str, str]:
    """A tool-output string re-rendered as a ``search`` snippet payload."""
    return {
        "title": "Customer notes and reviews",
        "url": url,
        "content": text,
        "engine": "example",
        "content_kind": "snippet",
    }


def benign_page(url: str, title: str, text: str) -> dict[str, str]:
    """A benign excerpt as a ``page`` payload (spec 3 US-001).

    The record schema keeps ``head_html`` apart from ``body_html``;
    ``records.page_document`` assembles the full document —
    ``<html><head><title>…</title>{head_html}</head><body>{body_html}</body>`` —
    so stage 1 extracts the declared title. The attack corpus shares it.
    """
    return {
        "url": url,
        "title": title,
        "head_html": "",
        "body_html": (
            f"<article><h1>{html.escape(title)}</h1>{_paragraphs(text)}</article>"
        ),
    }


def benign_search(url: str, title: str, content: str) -> dict[str, str]:
    """A benign excerpt as a ``search`` result: title, reserved URL, snippet."""
    return {"title": title, "url": url, "content": content}


def question_page(url: str, question: str) -> dict[str, str]:
    """A re-homed user query as a community-question ``page`` (spec 3 US-002)."""
    title = "Community question"
    return {
        "url": url,
        "title": title,
        "head_html": "",
        "body_html": (
            f'<article class="question"><h1>{title}</h1>'
            f"<p>Asked by a community member.</p>{_paragraphs(question)}</article>"
        ),
    }


def question_search(url: str, question: str) -> dict[str, str]:
    """A re-homed user query as a Q&A ``search`` result: the question is the snippet."""
    return {
        "title": "Community question and answers",
        "url": url,
        "content": question,
        "engine": "example",
        "content_kind": "snippet",
    }


def text_upload(filename: str, text: str) -> dict[str, str]:
    """A short instruction re-rendered as a ``text`` upload payload."""
    return {"filename": filename, "text": text}
