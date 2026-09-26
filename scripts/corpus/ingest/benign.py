"""Benign sampler (spec 3 US-001, US-002): real, permissively licensed text → a surface.

One CLI with a ``--source`` switch and one adapter per source. Each adapter
reads a local download (outside the repository, as spec 2's samplers do),
yields candidates, and the shared path excerpts, re-hosts, renders and
**triages** every accepted candidate before anything is written: the rendered
record is driven through spec 1's ``drive()`` with
``ReplayClassifier(fallback=0.0)`` and the pipeline's outcome is recorded.

A candidate that trips a stage-2 regex is **kept in its genre** — an organic
structural false positive is exactly what the genre's FPR measures — written
with ``pinned: ["flagged", "blocked"]`` and listed as *needs-variant* (ids
only); ``python -m tests.corpus_stage2 name-variants <file>`` then names the
regex. A candidate is rejected only for reasons unrelated to stage 2
(``REJECTION_REASONS``); the per-genre ``examined`` count and rejections go to
``sampler_stats.json`` beside the records. Output is ids and counts only.

Two sources are not core-genre web text (US-002). ``notinject`` re-homes
NotInject's benign user queries (``framing = rehomed_direct``) into
``over_defence_probe`` pages, snippets and uploads, stratified across the
dataset's four slices; ``arxiv`` takes the abstract and one section of a paper
whose arXiv licence is CC-BY-4.0 (any other licence is a ``licence`` rejection)
into ``security_prose``. Both ride the same triage, and their output goes
through the same ``name-variants`` step.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast
from urllib.parse import quote

from scripts.corpus import vocab
from scripts.corpus.drivers import drive
from scripts.corpus.ingest import common, render
from scripts.corpus.outcomes import RouteResult
from scripts.corpus.records import LINT_RULES, CorpusRecord, record_from_mapping
from scripts.corpus.replay import ReplayClassifier

DEFAULT_OUT: Final[Path] = vocab.TESTS_CORPUS_ROOT / "benign"
STATS_FILE: Final[str] = "sampler_stats.json"
DEFAULT_LIMIT: Final[int] = 20
EXCERPT_CAP: Final[int] = 6_000
SEARCH_CONTENT_CAP: Final[int] = 300
MIN_CHARS: Final[int] = 200
MIN_ALPHA_SHARE: Final[float] = 0.55

# Every reason a candidate may be rejected for. None is a stage-2 decision:
# a stage-2 hit keeps the candidate in its genre (module docstring).
REJECTION_REASONS: Final[frozenset[str]] = frozenset(
    {
        "licence",
        "unpinned",
        "too_short",
        "non_prose",
        "duplicate",
        "secret_shape",
        "invalid_url",
        "lint",
    }
)
# The URL-rule omissions a benign ``search`` record must never drive to.
URL_RULE_OMISSIONS: Final[frozenset[str]] = frozenset({"invalid_url", "blocked_url"})
STRUCTURAL_ONLY_MODEL: Final[str] = "structural-only"
NEEDS_VARIANT_REASON: Final[str] = "trips a stage-2 regex; needs-variant"

_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_SLUG_JUNK: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")
_RST_UNDERLINE: Final[re.Pattern[str]] = re.compile(r"^([=\-~*#^\"'`+])\1{2,}\s*$")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One upstream document, as read — never printed."""

    ref: str
    title: str
    text: str
    url: str
    licence: str | None = None
    revision: str | None = None
    source_name: str | None = None
    stratum: str | None = None
    lang: str = "en"


# (input path, --revision) -> candidates in input order.
Reader = Callable[[Path, str], list[Candidate]]
# The rendered records -> one ``RouteResult`` each, in order.
Triage = Callable[[Sequence[CorpusRecord]], list[RouteResult]]


@dataclass(frozen=True, slots=True)
class BenignSource:
    """What every record of a source carries and how it is read and rendered."""

    name: str
    licence: str
    genre: str
    surface: str
    reader: Reader
    framing: str = "indirect"
    surfaces: tuple[str, ...] = ()
    min_chars: int = MIN_CHARS


@dataclass(slots=True)
class BenignReport:
    """Counts and ids only."""

    examined: int = 0
    rejections: Counter[str] = field(default_factory=Counter[str])
    ids: list[str] = field(default_factory=list[str])
    needs_variant: list[str] = field(default_factory=list[str])

    def lines(self, name: str) -> list[str]:
        span = f"{self.ids[0]}..{self.ids[-1]}" if self.ids else "none"
        rejected = ", ".join(f"{k}={v}" for k, v in sorted(self.rejections.items()))
        return [
            f"{name}: examined {self.examined}",
            f"records written: {len(self.ids)} ({span})",
            f"rejections: {rejected or 'none'}",
            f"needs-variant: {', '.join(self.needs_variant) or 'none'}",
        ]


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def read_wikinews(path: Path, revision: str) -> list[Candidate]:
    """A JSONL export of articles: ``{"title", "url", "text"}`` per line."""
    del revision
    candidates: list[Candidate] = []
    for number, line in enumerate(_read_text(path).splitlines()):
        if not line.strip():
            continue
        obj = cast(dict[str, object], json.loads(line))
        title, url, text = obj.get("title"), obj.get("url"), obj.get("text")
        if not (isinstance(title, str) and isinstance(url, str)):
            raise common.IngestError("input_malformed")
        if not isinstance(text, str):
            raise common.IngestError("input_malformed")
        candidates.append(Candidate(f"line {number + 1}", title, text, url))
    return candidates


def _rst_prose(text: str) -> tuple[str, str]:
    """(title, prose) of an ``.rst`` file: directives and underlines dropped."""
    lines = text.splitlines()
    title = ""
    kept: list[str] = []
    in_directive = False
    for index, line in enumerate(lines):
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if _RST_UNDERLINE.match(line):
            continue
        if not title and line.strip() and _RST_UNDERLINE.match(nxt):
            title = line.strip()
            continue
        if line.startswith(".. "):
            in_directive = True
            continue
        if in_directive and (line.startswith(" ") or not line.strip()):
            if not line.strip():
                in_directive = False
                kept.append("")
            continue
        in_directive = False
        kept.append(line)
    return title, "\n".join(kept)


def read_cpython_docs(path: Path, revision: str) -> list[Candidate]:
    """The ``Doc/`` tree at a CPython tag: one candidate per ``.rst`` file."""
    candidates: list[Candidate] = []
    for file in sorted(path.rglob("*.rst")):
        rel = file.relative_to(path).as_posix()
        title, prose = _rst_prose(_read_text(file))
        url = f"https://github.com/python/cpython/blob/{revision}/Doc/{rel}"
        candidates.append(Candidate(rel, title or rel, prose, url))
    return candidates


def _markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return fallback


def read_rust_book(path: Path, revision: str) -> list[Candidate]:
    """*The Rust Programming Language* ``src/`` tree: one candidate per chapter."""
    candidates: list[Candidate] = []
    for file in sorted(path.glob("*.md")):
        if file.name == "SUMMARY.md":
            continue
        text = _read_text(file)
        url = f"https://github.com/rust-lang/book/blob/{revision}/src/{file.name}"
        candidates.append(
            Candidate(file.name, _markdown_title(text, file.stem), text, url)
        )
    return candidates


def _licence_of(project: Path) -> str:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE-MIT", "COPYING"):
        file = project / name
        if file.is_file():
            head = _read_text(file)[:400]
            if "Apache License" in head:
                return "Apache-2.0"
            if "MIT License" in head or "Permission is hereby granted" in head:
                return "MIT"
            return "unrecognised"
    return "missing"


def read_readmes(path: Path, revision: str) -> list[Candidate]:
    """One directory per project, named ``<owner>__<repo>@<sha>``.

    Each holds ``README*.md`` / ``CHANGELOG*.md`` and the project's licence
    file; the licence is resolved there, and the SHA in the directory name is
    the per-record revision (``--revision`` names the collection).
    """
    del revision
    candidates: list[Candidate] = []
    for project in sorted(p for p in path.iterdir() if p.is_dir()):
        slug, _, sha = project.name.partition("@")
        owner, _, repo = slug.partition("__")
        licence = _licence_of(project)
        for file in sorted(project.glob("*.md")):
            if not file.name.upper().startswith(("README", "CHANGELOG")):
                continue
            text = _read_text(file)
            url = f"https://github.com/{owner}/{repo}/blob/{sha}/{file.name}"
            candidates.append(
                Candidate(
                    f"{project.name}/{file.name}",
                    _markdown_title(text, f"{repo} {file.stem}"),
                    text,
                    url,
                    licence=licence,
                    revision=sha or None,
                )
            )
    return candidates


NOTINJECT_URL: Final[str] = "https://huggingface.co/datasets/leolee99/NotInject"
NOTINJECT_SLICES: Final[tuple[str, ...]] = (
    "Common Queries",
    "Technique Queries",
    "Virtual Creation",
    "Multilingual",
)
# Stop words that separate the Latin-script languages a NotInject query may be in.
_LATIN_STOPWORDS: Final[dict[str, frozenset[str]]] = {
    "es": frozenset(
        ["el", "la", "los", "las", "que", "una", "por", "para", "con", "del", "un"]
    ),
    "fr": frozenset(
        ["le", "les", "des", "une", "est", "pour", "avec", "dans", "que", "qui", "du"]
    ),
    "de": frozenset(
        ["der", "die", "das", "und", "ist", "nicht", "mit", "ein", "eine", "für", "den"]
    ),
    "pt": frozenset(["não", "uma", "para", "com", "os", "as", "dos", "você", "em"]),
    "it": frozenset(["il", "che", "per", "una", "con", "sono", "di", "della", "gli"]),
}
_WORD: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+")


def lang_of(text: str) -> str:
    """A BCP-47 language for ``text`` by script, then by stop words for Latin.

    Kana is Japanese, Hangul Korean, other Han Chinese, and a Cyrillic majority
    Russian; Latin script needs two stop-word hits in one language, else ``en``.
    """
    counts: Counter[str] = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF:
            counts["ja"] += 1
        elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
            counts["ko"] += 1
        elif 0x4E00 <= code <= 0x9FFF:
            counts["zh"] += 1
        elif 0x0400 <= code <= 0x04FF:
            counts["ru"] += 1
        else:
            counts["latin"] += 1
    for script in ("ja", "ko", "zh"):
        if counts[script]:
            return script
    if counts["ru"] > counts["latin"]:
        return "ru"
    words = {word.casefold() for word in _WORD.findall(text)}
    votes = {lang: len(words & stop) for lang, stop in _LATIN_STOPWORDS.items()}
    best = max(votes, key=lambda lang: votes[lang])
    return best if votes[best] >= 2 else "en"


def read_notinject(path: Path, revision: str) -> list[Candidate]:
    """NotInject rows as JSONL: ``{"subset", "index", "category", "prompt"}``.

    The dataset ships as three parquet files (``NotInject_one`` / ``two`` /
    ``three``, 113 rows each). Convert them once on the host, outside the
    repository — one JSON line per row, ``json.dumps(row, ensure_ascii=False,
    sort_keys=True)``, subsets in that order and rows in file order — and pin
    the result with ``--input-sha256``. A row's ``category`` is its slice.
    """
    del revision
    candidates: list[Candidate] = []
    for line in _read_text(path).splitlines():
        if not line.strip():
            continue
        obj = cast(dict[str, object], json.loads(line))
        subset, index = obj.get("subset"), obj.get("index")
        category, prompt = obj.get("category"), obj.get("prompt")
        if not (isinstance(subset, str) and isinstance(index, int)):
            raise common.IngestError("input_malformed")
        if not (isinstance(category, str) and isinstance(prompt, str)):
            raise common.IngestError("input_malformed")
        if category not in NOTINJECT_SLICES:
            raise common.IngestError("input_malformed")
        lang = lang_of(prompt) if category == "Multilingual" else "en"
        candidates.append(
            Candidate(
                ref=f"NotInject_{subset} row {index} ({category})",
                title=f"question {subset} {index}",
                text=prompt,
                url=NOTINJECT_URL,
                stratum=category,
                lang=lang,
            )
        )
    return candidates


ARXIV_LICENCES: Final[dict[str, str]] = {
    "creativecommons.org/licenses/by/4.0/": "CC-BY-4.0",
    "creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0",
}


def arxiv_licence(stated: str) -> str:
    """The SPDX id for an arXiv licence link; anything else stays as stated."""
    key = stated.strip().removeprefix("https://").removeprefix("http://")
    return ARXIV_LICENCES.get(key, stated.strip() or "missing")


def read_arxiv(path: Path, revision: str) -> list[Candidate]:
    """One paper per JSONL line: the abstract and one section, plus provenance.

    Fields: ``arxiv_id``, ``version`` (``v1``), ``title``, ``licence`` (the link
    the abstract page states), ``abstract``, ``section_heading``,
    ``section_text`` and ``authors``. Fetched by hand on the host; the sampler
    only reads, and refuses any licence but CC-BY-4.0 / CC0.
    """
    del revision
    candidates: list[Candidate] = []
    for line in _read_text(path).splitlines():
        if not line.strip():
            continue
        obj = cast(dict[str, object], json.loads(line))
        fields = {
            key: obj.get(key)
            for key in (
                "arxiv_id",
                "version",
                "title",
                "licence",
                "abstract",
                "section_heading",
                "section_text",
            )
        }
        if not all(isinstance(value, str) for value in fields.values()):
            raise common.IngestError("input_malformed")
        text = cast(dict[str, str], fields)
        paper = f"{text['arxiv_id']}{text['version']}"
        candidates.append(
            Candidate(
                ref=f"arXiv:{paper}",
                title=text["title"],
                text=(
                    f"{text['abstract']}\n\n{text['section_heading']}\n\n"
                    f"{text['section_text']}"
                ),
                url=f"https://arxiv.org/abs/{paper}",
                licence=arxiv_licence(text["licence"]),
                revision=text["version"],
                source_name=f"arXiv:{text['arxiv_id']}",
            )
        )
    return candidates


SOURCES: Final[dict[str, BenignSource]] = {
    "wikinews": BenignSource("wikinews", "CC-BY-2.5", "news", "page", read_wikinews),
    "cpython_docs": BenignSource(
        "cpython_docs", "PSF-2.0", "docs", "page", read_cpython_docs
    ),
    "rust_book": BenignSource("rust_book", "MIT", "code", "text", read_rust_book),
    "readme_changelog": BenignSource(
        "readme_changelog", "MIT", "code", "search", read_readmes
    ),
    "notinject": BenignSource(
        "NotInject",
        "MIT",
        "over_defence_probe",
        "page",
        read_notinject,
        framing="rehomed_direct",
        surfaces=("page", "search", "text"),
        min_chars=8,
    ),
    "arxiv": BenignSource("arXiv", "CC-BY-4.0", "security_prose", "page", read_arxiv),
}


# ---------------------------------------------------------------------------
# Excerpt, render, triage
# ---------------------------------------------------------------------------


def excerpt(text: str, cap: int = EXCERPT_CAP) -> str:
    """Whole paragraphs from the start of ``text`` up to ``cap`` characters."""
    kept: list[str] = []
    size = 0
    for block in (b.strip() for b in re.split(r"\n\s*\n", text)):
        if not block:
            continue
        extra = len(block) + (2 if kept else 0)
        if size + extra > cap:
            if not kept:
                kept.append(block[:cap])
            break
        kept.append(block)
        size += extra
    return "\n\n".join(kept)


def _alpha_share(text: str) -> float:
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return 0.0
    return sum(ch.isalpha() for ch in visible) / len(visible)


def reserved_url(genre: str, title: str) -> str:
    """A reserved-host result URL; raw-reject characters percent-encoded."""
    slug = _SLUG_JUNK.sub("-", title.lower()).strip("-")[:60] or "item"
    host = genre.replace("_", "-")
    return f"https://{host}.example/{quote(slug, safe='/-._~')}"


def _search_content(text: str) -> str:
    flat = _WHITESPACE.sub(" ", text).strip()
    if len(flat) <= SEARCH_CONTENT_CAP:
        return flat
    cut = flat[:SEARCH_CONTENT_CAP]
    space = cut.rfind(" ")
    return cut[:space] if space > 0 else cut


def render_payload(
    source: BenignSource, surface: str, title: str, text: str
) -> dict[str, str]:
    """The surface form of an excerpt (``render.py`` holds the page template).

    A ``rehomed_direct`` source is a user query, so it is placed as a question
    would sit on the web rather than as an article excerpt.
    """
    url = reserved_url(source.genre, title)
    if source.framing == "rehomed_direct":
        if surface == "page":
            return render.question_page(url, text)
        if surface == "search":
            return render.question_search(url, _search_content(text))
        return render.text_upload("question.txt", text)
    if surface == "page":
        return render.benign_page(url, title, text)
    if surface == "search":
        return render.benign_search(url, title, _search_content(text))
    return render.text_upload(f"{source.name}.txt", text)


def _mapping(
    source: BenignSource,
    candidate: Candidate,
    record_id: str,
    revision: str,
    surface: str,
) -> dict[str, object]:
    title = render.rewrite_urls(candidate.title).strip()
    text = excerpt(render.rewrite_urls(candidate.text))
    shape = "re-homed" if source.framing == "rehomed_direct" else "excerpt rendered"
    return {
        "id": record_id,
        "kind": "benign",
        "category": source.genre,
        "surface": surface,
        "payload": render_payload(source, surface, title, text),
        "marker": None,
        "pinned": None,
        "pinned_reason": None,
        "source": {
            "kind": "third_party",
            "name": candidate.source_name or source.name,
            "url": candidate.url,
            "licence": candidate.licence or source.licence,
            "revision": candidate.revision or revision,
            "record_ref": candidate.ref,
            "framing": source.framing,
        },
        "lang": candidate.lang,
        "params": {},
        "notes": f"{source.name} {shape} as {surface}",
    }


def screen(
    candidate: Candidate, seen: set[str], min_chars: int = MIN_CHARS
) -> str | None:
    """The rejection reason for ``candidate`` before rendering, or ``None``."""
    if candidate.licence is not None and (
        candidate.licence not in vocab.THIRD_PARTY_LICENCES
    ):
        return "licence"
    if candidate.revision is None and candidate.licence is not None:
        return "unpinned"
    if render.has_secret_shape(f"{candidate.title}\n{candidate.text}"):
        return "secret_shape"
    text = candidate.text.strip()
    if len(text) < min_chars or not candidate.title.strip():
        return "too_short"
    if _alpha_share(text) < MIN_ALPHA_SHARE:
        return "non_prose"
    digest = hashlib.sha256(_WHITESPACE.sub(" ", text).encode("utf-8")).hexdigest()
    if digest in seen:
        return "duplicate"
    seen.add(digest)
    return None


def _structural_only() -> ReplayClassifier:
    """No cassette: every window scores 0.0, so stages 1 and 2 alone decide."""
    return ReplayClassifier(
        {}, model_id=STRUCTURAL_ONLY_MODEL, revision="fallback", fallback=0.0
    )


def drive_structural_only(records: Sequence[CorpusRecord]) -> list[RouteResult]:
    """Spec 1's ``drive()`` per record, structural-only (``fallback=0.0``)."""

    async def run() -> list[RouteResult]:
        return [
            await drive(record, _structural_only(), config="default")
            for record in records
        ]

    return asyncio.run(run())


def _existing(out: Path) -> tuple[int, set[str]]:
    """The highest ``ben`` number under ``out`` and the source names there."""
    highest = 0
    names: set[str] = set()
    for path in sorted(out.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = cast(dict[str, object], json.loads(line))
            record_id = obj.get("id")
            if isinstance(record_id, str) and record_id.startswith("ben-"):
                highest = max(highest, int(record_id.removeprefix("ben-")))
            source = obj.get("source")
            if isinstance(source, dict):
                name = cast(dict[str, object], source).get("name")
                if isinstance(name, str):
                    names.add(name)
    return highest, names


def write_stats(out: Path, genre: str, report: BenignReport) -> None:
    """Add this run's counts to ``sampler_stats.json`` (sorted, stable)."""
    path = out / STATS_FILE
    stats: dict[str, dict[str, object]] = (
        cast(dict[str, dict[str, object]], json.loads(path.read_text("utf-8")))
        if path.exists()
        else {}
    )
    entry = stats.setdefault(genre, {"examined": 0, "rejections": {}})
    rejections = Counter(cast(dict[str, int], entry.get("rejections", {})))
    rejections.update(report.rejections)
    entry["examined"] = cast(int, entry.get("examined", 0)) + report.examined
    entry["rejections"] = dict(sorted(rejections.items()))
    path.write_text(
        json.dumps(dict(sorted(stats.items())), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _in_sample_order(candidates: Sequence[Candidate], seed: int) -> list[Candidate]:
    """Seed order within each stratum, interleaved one per stratum in turn.

    Candidates without a stratum form one, so the order is the plain seeded
    shuffle; with strata, any ``limit`` draws from every slice in rotation.
    """
    by_ref = {c.ref: c for c in candidates}
    strata: dict[str, list[Candidate]] = {}
    for candidate in by_ref.values():
        strata.setdefault(candidate.stratum or "", []).append(candidate)
    queues = [
        [
            by_ref[row.ref]
            for row in common.shuffled(
                [common.Row(ref=c.ref, text=c.text) for c in members], seed
            )
        ]
        for _, members in sorted(strata.items())
    ]
    ordered: list[Candidate] = []
    for rank in range(max(map(len, queues), default=0)):
        ordered.extend(queue[rank] for queue in queues if rank < len(queue))
    return ordered


def sample(
    source: BenignSource,
    candidates: Sequence[Candidate],
    *,
    revision: str,
    seed: int,
    limit: int,
    out: Path,
    triage: Triage = drive_structural_only,
) -> BenignReport:
    """Screen, render and triage candidates in seed order; append to ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    highest, names = _existing(out)
    ordered = _in_sample_order(candidates, seed)
    if source.name in names or any(
        (c.source_name or source.name) in names for c in ordered
    ):
        raise common.IngestError("already_ingested")
    surfaces = source.surfaces or (source.surface,)
    report = BenignReport()
    seen: set[str] = set()
    written: list[dict[str, object]] = []
    for candidate in ordered:
        if len(written) >= limit:
            break
        report.examined += 1
        reason = screen(candidate, seen, source.min_chars)
        record_id = f"ben-{highest + len(written) + 1:04d}"
        surface = surfaces[len(written) % len(surfaces)]
        mapping = _mapping(source, candidate, record_id, revision, surface)
        if reason is None:
            record = record_from_mapping(mapping)
            if not all(rule(record) for rule in LINT_RULES.values()):
                reason = "lint"
            else:
                (result,) = triage([record])
                if result.signals.omit_reason in URL_RULE_OMISSIONS:
                    reason = "invalid_url"
                elif result.outcome in ("flagged", "blocked"):
                    mapping["pinned"] = ["flagged", "blocked"]
                    mapping["pinned_reason"] = NEEDS_VARIANT_REASON
                    report.needs_variant.append(record_id)
                elif result.outcome != "clean":
                    mapping["pinned"] = [result.outcome]
                    mapping["pinned_reason"] = "measured at ingest, fallback=0.0"
        if reason is not None:
            reject(report, reason)
            continue
        written.append(mapping)
        report.ids.append(cast(str, mapping["id"]))
    if written:
        with (out / f"{source.genre}.jsonl").open("a", encoding="utf-8") as handle:
            for mapping in written:
                handle.write(json.dumps(mapping, ensure_ascii=False) + "\n")
    write_stats(out, source.genre, report)
    return report


def reject(report: BenignReport, reason: str) -> None:
    """Count a rejection; a stage-2 reason is refused outright."""
    if reason not in REJECTION_REASONS:
        raise ValueError("unknown rejection reason")
    report.rejections[reason] += 1


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, guard, read, sample, triage, write; print ids and counts only."""
    parser = common.build_parser(
        "python -m scripts.corpus.ingest.benign",
        "Sample permissively licensed benign text into tests/corpus/benign/.",
        DEFAULT_LIMIT,
    )
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    parser.set_defaults(out=DEFAULT_OUT)
    args = parser.parse_args(argv)
    source = SOURCES[cast(str, args.source)]
    try:
        path = common.check_input_path(cast(Path, args.input))
        if path.is_file():
            common.check_sha256(path, cast("str | None", args.input_sha256))
        revision = cast(str, args.revision)
        report = sample(
            source,
            source.reader(path, revision),
            revision=revision,
            seed=cast(int, args.seed),
            limit=cast(int, args.limit),
            out=cast(Path, args.out),
        )
    except Exception as exc:  # every failure leaves as a closed reason code
        print(f"{source.name}: refused ({common.failure_reason(exc)})", file=sys.stderr)
        return 2
    for line in report.lines(source.name):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
