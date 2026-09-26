# `tests/corpus/`

The injection corpus: attack records and a benign counter-corpus, one JSON
object per line. **Payload text is data, never quoted elsewhere** — not in a
commit message, an issue, a log line, a test's assertion message or this README.
Tools and tests refer to a record by its `id` only; the lint reports
`<id>: <rule>` and nothing more. Nothing here is shipped in the image —
`.dockerignore` excludes `tests/` outright.

## Files

| Path | What it is |
|------|------------|
| `attacks/<category>.jsonl` | Attack records, one file per attack category |
| `benign/<genre>.jsonl` | Benign records, one file per genre |

Records are stored **only** as `.jsonl` (finding 16): a renderable `.html` /
`.htm` / `.md` under `attacks/` or `benign/` fails `tests/test_corpus_lint.py`.
The code lives in `scripts/corpus/` — `vocab.py` holds every closed vocabulary,
`records.py` the `CorpusRecord` type, `load_corpus()` and `lint_corpus()`.
`replay.py` is the `ReplayClassifier` (a stage-3 stand-in that answers from recorded
scores), `drivers.py` pushes a record through its route of the real app
(`drive()` / `drive_all()`), and `outcomes.py` turns the wire response into a
`RouteResult`. Drive a record and report it by id only:
`assert result.outcome == "blocked", result.summary()`.
Records are authored by hand (or by the spec 2 / spec 3 generators); there is
no regeneration step. Check a change with:

```bash
uv run pytest tests/test_corpus_lint.py -q
```

## Record shape

Keys appear **in this order** (the `key_order` rule enforces it so diffs stay
readable). `marker`, `pinned`, `pinned_reason`, `params` and `notes` may be
omitted; the rest are required.

| Key | Meaning |
|-----|---------|
| `id` | `atk-NNNN` for attacks, `ben-NNNN` for benign; unique across every file |
| `kind` | `attack` or `benign` |
| `category` | An attack category (16, `vocab.ATTACK_CATEGORIES`) or benign genre (9, `vocab.BENIGN_GENRES`) |
| `surface` | `search` (`/search`), `page` (`/retrieve`) or `text` (`/extract`) |
| `payload` | `search`: `title`, `url`, `content`, optional `engine`, `content_kind` (`snippet` / `chunk`). `page`: `url`, `title`, `head_html` (may be empty — JSON-LD / OG / meta carriers go here), `body_html`. `text`: `filename`, `text`. All values strings |
| `marker` | Attack only: a substring of at least 12 characters, one line, that must not reach the wire |
| `pinned` | `null`/absent, or a non-empty list of acceptable outcomes on **every** applicable route |
| `pinned_reason` | Required exactly when `pinned` is set — e.g. the stage-2 regex a benign record trips |
| `source` | `kind` (`synthetic` / `owned` / `third_party`), `name`, `url`, `licence` (SPDX id or `n/a`), `revision` (required for `third_party`), `record_ref`, `framing` (`indirect` / `rehomed_direct`) |
| `lang` | BCP-47 tag (`en`, `pt-BR`) |
| `params` | Allowed keys per category and genre: `density`, `placement`, `repeat`, `carrier`, `windows_min`, `variant`, `url_exception` |
| `notes` | Free text for reviewers |

`params.variant` is one vocabulary keyed on `kind`: for an attack it names the
obfuscation transform (`vocab.ATTACK_VARIANTS`); for a benign record it names
the `vocab.STAGE2_REGEX_NAMES` member the record deliberately trips.
`params.carrier` names where a `hidden_markup` payload hides and is drawn from
`vocab.ATTACK_CARRIERS`; the lint rejects any other value.

## Outcome vocabulary (ruling 9)

`pinned` draws from `blocked`, `flagged`, `neutralised` and `clean`. A benign
record with no `pinned` is asserted `clean`; one that trips a stage-2 regex
pins `["flagged", "blocked"]` and names the regex in `pinned_reason`. A change
to a pinned record's outcome is a regression to look at.

The marker is compared after `pipeline.stage1_extraction.normalize_text`
(NFC, the invisible-character strip, whitespace collapse) and `casefold()` —
the same helper the leak check uses — against the raw payload fields and, for
`page`, the extracted text. A marker must survive its own variant.

## Seed records

`atk-0001` … `atk-0025` and `ben-0001` … `ben-0010` are the seed (corpus-harness
US-003): one owned attack per category, the six metadata carriers
(`params.carrier`: `jsonld`, `meta_description`, `og_description`,
`css_offscreen`, `hidden_div`, `title_stuffing`), both stage-3 residual shapes
(`boundary_straddle`, `sustained_midband`) and one benign record per genre, two
of them `multilingual`. Growth continues at `atk-0026` and `ben-0011`; an id is
never reused.

Drive them structural-only — a `ReplayClassifier` built with `fallback=0.0`, so
stages 1 and 2 alone decide — with:

```bash
uv run pytest tests/test_corpus_harness.py -k seed -q
```

Under that measurement a BLOCK-category record is `blocked`, a
SUSPICIOUS-category record is `flagged`, the three stripped carriers are
`neutralised`, and everything with no structural marker — `natural_language`,
`authority_seo`, the stage-3 families and the three carriers stage 1 keeps — is
`leaked`. That is the measurement the corpus exists for, not a bug.

### Pinned seed records

The generic test `test_seed_pins_hold_for_every_pinned_record` reads each pin
from its record. A change to any row below is a regression to look at.

| Record | Category | Pin | Basis |
|--------|----------|-----|-------|
| `atk-0008` | `line_anchored_role` | `blocked` | 2026-09-16-016 |
| `atk-0009` | `url_borne_envelope` | `blocked` | 2026-09-16-032 |
| `atk-0010` | `url_borne_envelope` | `blocked` | 2026-09-16-032 |
| `atk-0011` | `url_borne_envelope` | `blocked` | 2026-09-16-032 |
| `atk-0024` | `line_anchored_role` | `blocked` | R26: decode order, double entity |
| `atk-0025` | `instruction_override` | `blocked` | R26: strip order, raw NUL |
| `atk-0014` | `hidden_markup` | `blocked`, `flagged`, `neutralised` | never leaked: stage 1 strips script |
| `atk-0015` | `hidden_markup` | `blocked`, `flagged`, `neutralised` | never leaked: stage 1 strips meta |
| `atk-0016` | `hidden_markup` | `blocked`, `flagged`, `neutralised` | never leaked: stage 1 strips meta |
| `ben-0002` | `code` | `flagged`, `blocked` | trips stage-2 regex base64_run |

The two audit ids are the reproduced bypasses this epic exists to keep closed:
-016 (a role marker after a paragraph break) and -032 (an envelope tag in a URL
path, query or IPv6 zone id). `R26` is the hardening ruling on decode and strip
order.

## Structural families (spec 2 US-001)

`atk-0026` … `atk-0182` are the nine stage-2 families — `instruction_override`,
`authority_impersonation`, `prompt_boundary`, `encoded_payload`,
`suspicious_url`, `exfil_beacon`, `envelope_breakout`, `line_anchored_role` and
`url_borne_envelope` — each authored across the three surfaces and the ten
obfuscation variants below. Every record targets **one** regex: its `notes` starts
`regex: <name>`, a member of `vocab.STAGE2_REGEX_NAMES` (a `url_borne_envelope`
record also names its URL shape and the omission reason or verdict).
`url_borne_envelope` is `search`-only: the category is a property of `/search`
result-URL canonicalisation, and a `page` or `text` record would have no result URL.

### Variant vocabulary

`params.variant` for an attack (`vocab.ATTACK_VARIANTS`) names the obfuscation
transform. The trigger carries the transform; the `marker` sits on the
surrounding sentence, which the transform does not touch.

| Variant | What the record carries |
|---------|-------------------------|
| `plain` | The trigger in the form that reaches stage 2 as text on the record's route: the literal on `text`; on `page` and `search`, a tag-shaped trigger in its entity form |
| `case` | The trigger re-cased (upper or mixed), which only the patterns compiled without `IGNORECASE` can miss |
| `entity` | The trigger's `<`, `[`, backtick, brace or colon as an HTML character reference, or a double-encoded form; the three routes decode to different depths |
| `zwsp` | U+200B or U+200C inside the trigger token; stage 1 deletes both, so the token is rejoined before stage 2 scans it |
| `split_tags` | Inline markup (`<b>`, `<i>`, `<code>`) inside the trigger token; extraction puts a separator at the tag boundary, so the token is broken, not rejoined |
| `confusable` | Cyrillic а / е / о (U+0430 / U+0435 / U+043E) substituted for their Latin twins in a trigger word; nothing in the pipeline folds them |
| `second_paragraph` | A paragraph or line break at or inside the trigger, on any surface; a break inside the trigger adds the note `break-inside-trigger` |
| `url_query` | The trigger carried in the query string of a `/search` result URL |
| `url_path` | The trigger carried in the path of a `/search` result URL |
| `title_field` | The trigger placed in the title — the `search` title field or a page's `<title>` — rather than the body or snippet |

`plain` is defined per route because `/retrieve` **and** `/search` parse markup
before stage 2: a literal tag-shaped trigger (a system tag, a retrieval-envelope
tag, an `href` in a live anchor) is consumed there and scans clean. That record
is still `plain`, tagged `tag-consumed` in `notes`, and left unpinned; on `text`
nothing parses HTML, so the literal is the plain form. The two patterns compiled
without `re.DOTALL` — `disregard_instructions` and `exfil_image` — carry a
`break-inside-trigger` record on each route: `/search` also scans the collapsed
wire form, `/retrieve` and `/extract` scan one newline-preserving form.

### Pins

Only what the spec's table gives is pinned; every obfuscated variant is a
measurement, reported and never asserted here. The pins were set from a first run
through the real routes with `fallback=0.0`, not from expectation, and every row
measured as the table hypothesised.

| Records | Pin | `pinned_reason` |
|---------|-----|-----------------|
| `plain`, `instruction_override` / `authority_impersonation` / `prompt_boundary` | `blocked` | `stage-2 regex, deterministic` |
| `plain`, `line_anchored_role` | `blocked` | `2026-09-16-016` on `search`, else `stage-2 regex, deterministic` |
| `plain`, `encoded_payload` / `suspicious_url` / `exfil_beacon` / `envelope_breakout` | `flagged` | `stage-2 regex, deterministic` |
| `url_borne_envelope`, shape (i): a BLOCK token, percent- or entity-encoded, in path or query | `blocked` | `2026-09-16-032` |
| `url_borne_envelope`, shape (ii): an encoded retrieval-envelope tag alone | `flagged` | `2026-09-16-032` |
| `url_borne_envelope`, shape (iii): a raw `<` or `>` in the URL | `blocked` | `search-url rule raw_chars (invalid_url), not stage 2` |
| `break-inside-trigger` on `search` | as measured | `stage-2 regex on the collapsed wire form, deterministic` |
| everything else, including `tag-consumed` | none | — |

Shape (ii) is `flagged`, not `blocked`, because `envelope_breakout` is a
SUSPICIOUS category. Shape (iii) never reaches stage 2: the raw-character rule
omits the result as `invalid_url` first, so its pin names that rule.

## Hidden-markup carriers (spec 2 US-002)

`hidden_markup` records (`atk-0014` … `atk-0019` and `atk-0183` … `atk-0214`) are
the places real campaigns hide instructions. They are `page`-only: a carrier is an
HTML placement, so the record is a document and the route is `/retrieve`. The
`search` siblings of `title_stuffing` and `og_description` — the provider surfaces
the meta description as the snippet — belong to `authority_seo`. `params.carrier`
names the placement; `notes` starts `phrasing: <shape>` and, for the
instruction-override shape, continues `; regex: <name>`, so a report can split
every carrier by phrasing. The `marker` sits inside the carrier's own placement, in
prose the placement does not obscure.

### Carrier vocabulary

`params.carrier` (`vocab.ATTACK_CARRIERS`):

| Carrier | Where the payload sits |
|---------|------------------------|
| `jsonld` | A string in a `<script type="application/ld+json">` object — the `description`, `offers`, `publisher` or `applicationCategory` of a `SoftwareApplication` or `Organization` |
| `meta_description` | The `content` of `<meta name="description">` in the head |
| `og_description` | The `content` of `<meta property="og:description">` in the head |
| `css_offscreen` | Text in an element styled off-screen (`position:absolute;left:-9999px`) or to zero size (`font-size:0`) |
| `hidden_div` | Text in an element hidden by the `hidden` attribute or by `display:none` |
| `html_comment` | The text of an HTML comment, in the body or the head |
| `alt_text` | The `alt` attribute of an `<img>` |
| `title_stuffing` | A keyword-stuffed page `<title>` that carries the payload |

### Phrasing shapes

`notes` names one of three shapes per record, and every carrier carries all three
(the instruction-override shape twice, with two different patterns), so the same
placement is measured against payloads stage 2 can match and payloads only stage 3
could:

| Phrasing | What the record says |
|----------|----------------------|
| `instruction_override` | A stage-2 instruction-override trigger (`ignore_previous`, `disregard_instructions`, `new_directive`, `system_bracket`, `instructions_banner`) followed by the demand — a stage-2 hit *if* the carrier survives extraction |
| `authority_seo` | The Zscaler shape: a publisher's claim to be the verified, authoritative source that deserves first rank — no technique string, nothing for stage 2 to match |
| `natural_language` | A plain-prose imperative to the assistant reading the page — no trigger, no role marker, no bracket |

### Pins

Measured with `fallback=0.0` under both rule configs when the story began, every
phrasing of five carriers came back `neutralised`: `jsonld`, `meta_description`,
`og_description`, `html_comment` and `alt_text`. Stage 1 strips `script`, `meta`
and comments, and takes only text nodes, so an `alt` attribute is never extracted.
Those five carry `pinned: ["blocked", "flagged", "neutralised"]` — never `leaked` —
and a change that lets any of them reach the wire is a regression to look at. The
other three keep their text (`css_offscreen`, `hidden_div`, the page title), so
there the instruction-override phrasing is stage 2's to block and the other two are
left for stage 3; they are unpinned and reported, not asserted.

## Window-shaped families (spec 2 US-003)

`boundary_straddle`, `density_thinned`, `repetition_camouflage` and
`sustained_midband` (`vocab.WINDOW_FAMILIES`; this story's records are `atk-0215`
… `atk-0272`, the seed's `atk-0020` … `atk-0023` sit alongside) are about where a
payload sits across the classifier's windows: 512 tokens with 64 of overlap, so a
448-token step (`promptguard/classifier.py`). They are parameter sweeps, so the
report can say at what density and repetition each pooling rule stops catching.

| Family | Parameters | What the sweep varies |
|--------|------------|-----------------------|
| `boundary_straddle` | `placement` (`split_448` / `split_896` / `split_1344`), `windows_min` (2 / 3 / 4) | The payload's two halves, each carrying the marker, sit either side of the named step boundary, so neither window sees the whole payload. `page` and `text` |
| `density_thinned` | `density` (`1/1` / `1/2` / `1/4` / `1/8` payload sentences per window), `placement` (`head` / `tail` / `interleave`), `windows_min` (4, or 8 for `1/8`) | How thinly one payload sentence is spread through filler: 4, 2, 1 and 1 sentences over 4, 4, 4 and 8 windows, each at the head, tail or middle of its span. Two bases, `natural_language` and `authority_seo`, over the full 4 × 3 grid. `page` and `text` |
| `repetition_camouflage` | `repeat` (`1` / `2` / `3` / `5`), `windows_min` (always 1) | How many times one payload sentence repeats inside a single window. Four bases × four levels: `cookie_banner` and `footer` repeat it as page boilerplate, the two `plain` bases (one `page`, one `text`) as prose. Stage 3 scores `raw_text`, which keeps the boilerplate `div`s, while `main_content`, what the response serves, drops them. So the classifier still scores the camouflaged bases, but their outcome is `neutralised`, not `leaked` |
| `sustained_midband` | `windows_min` (≥ 3) | Nothing is swept. Long review and comment prose that reads as semi-instructional with no trigger string, so the report can measure how often contiguity trips on text that should be benign. Owned prose, `page` only, never pinned |

`notes` on the density and repetition records starts `base: <name>;`. Every
record's `marker` appears in each payload fragment: both straddle halves, every
density sentence and every repetition, so the leak check sees any surviving piece.

**Character-budget rule.** The real tokenizer is not available hermetically, so
records are authored at a planning budget of 4 characters per token: a record
declaring `windows_min: W` carries at least `4 × (448 × (W − 1) + 64)` characters in
its stage-2 form, a straddle boundary `split_N` sits at character `4 × N`, and a
one-window repetition record stays under `4 × 512 / 1.3` characters (30 %
headroom for denser tokenisation). This is a planning budget, not a measured PG2
ratio. Spec 4 verifies the recorded window count against the real tokenizer and
re-authors any record that falls short.

**Stage-2 clean.** Stage 3 never runs on a structural block, and a suspicious span
makes the outcome a constant `flagged`. Either one would flatten the curve. The lint
rule `sweep_stage2_clean` therefore requires `scan_structural(form).flags == []`
over the whole record, filler included, in the form stage 2 receives:
`extract_html(...).raw_text` for `page` and `extract_upload_text(...).raw_text`
for `text`. Stage-2-shaped payloads keep their own pinned `["blocked"]` records in
the regex-floor categories, and those records are the floor the report pairs with
the repetition curve. No window-family record is pinned.

**Filler.** Straddle, density and repetition filler is Project Gutenberg
plain text: `source.kind: third_party`, `licence: LicenseRef-PublicDomain`, `url`
the book's `gutenberg.org/ebooks/<n>` page, `revision` the ebook number, and
`record_ref` listing each excerpt as `offset <o>, <n> chars` into the plain-text
file. No excerpt is longer than 6 000 characters. The payload sentences themselves
are owned.

## Third-party samples (spec 2 US-004)

Three permissively licensed sets are sampled by `scripts/corpus/ingest/`
(`agentdojo.py`, `llmail_inject.py`, `cyberseceval.py`, with the shared
`render.py` and `common.py`). Each sampler reads a local download, orders its
rows under a fixed `--seed`, writes at most `--limit` records and appends them
to the category files here, continuing the id sequence. It prints ids and
counts only, never payload text; a secret-shaped row is skipped with a counted
reason, and every record must pass the lint before it is written.

| Source | Licence (where resolved) | Cap | Shape → surface |
|--------|--------------------------|-----|-----------------|
| AgentDojo | MIT, repository `LICENSE` | 40 | injection `GOAL` in the `important_instructions` tool-output template → article `page` / `search` snippet |
| LLMail-Inject | MIT, dataset card | 60, half caught / half missed by the challenge defence | attacker e-mail → forum-post `page` |
| CyberSecEval | MIT, `CybersecurityBenchmarks/LICENSE` (not the repository root) | 30, indirect cases only | submitted content → `text` upload |

**Category rule (one for all three).** At ingest the re-rendered text is
scanned with `scan_structural`; the first stage-2 category that fires is the
record's category, with `variant = plain`; otherwise it is `natural_language`.
None of the three sources is SEO/authority-framed, so no ingested row takes
`authority_seo`. Ingested records are unpinned and `source.kind = third_party`.

**Where inputs live.** Downloads go under `$FORAGE_CORPUS_INPUTS` (default
`~/.cache/forage-corpus-inputs/`), outside the working tree, and are never
committed — only the sampled, re-rendered, capped records reach
`tests/corpus/`. The samplers refuse an `--input` that resolves (symlinks and
`..` followed) inside the repository, and `.gitignore` carries
`/corpus-inputs/` for the obvious mistake. `--input-sha256` refuses a file
that changed under its pin. A gated fetch reads `HF_TOKEN` from the
environment inside `huggingface_hub`; no sampler takes a token argument, and a
failure prints a closed reason code (`input_inside_repo`, `io_failed`,
`http_<status>`, …), never an exception message or URL.

**Sourcing rule.** Resolve the licence at the directory of the files taken,
not the repository root. Pin by commit SHA or dataset revision, never a
branch (PIGuard's rename "due to licensing issues" is the precedent). Direct
(user-turn) rows are excluded, or re-homed into a web surface with
`framing = rehomed_direct`. Every non-public-domain source has an entry under
"Third-party corpus samples" in `NOTICE`, which a test enforces.

**Rejected on licence.** Ideas are not copyrightable: these taxonomies may be
read, but no string from them enters the corpus.

| Source | Why rejected |
|--------|--------------|
| BIPIA | Benchmark data CC-BY-SA 4.0 (share-alike); its LICENSE disclaims its own accuracy |
| WASP | CC-BY-NC 4.0 (non-commercial) |
| HackAPrompt | MIT, but direct-framed and unnecessary |
| PIGuard / InjecGuard | Licence unverified after the rename |
| Wikipedia | CC-BY-SA (share-alike) |
| Stack Exchange | CC-BY-SA (share-alike) |
| MDN prose | CC-BY-SA (share-alike) |
| OWASP | CC-BY-SA (share-alike) |
| arXiv papers not under CC BY 4.0 / CC0 | The arXiv non-exclusive licence, CC BY-NC-*, CC BY-SA: the abstract page states the licence per paper and the `arxiv` sampler refuses anything else as a `licence` rejection |
| Reddit | No licence grant |
| Hacker News | No licence grant |

## Benign sampler and the core genres (spec 3 US-001)

`scripts/corpus/ingest/benign.py` is one sampler with a `--source` switch
(`wikinews` → `news` page, `cpython_docs` → `docs` page, `rust_book` → `code`
text, `readme_changelog` → `code` search; `notinject` and `arxiv` are spec 3 US-002's; a README directory is named
`<owner>__<repo>@<sha>` and its licence is read from that directory). Same CLI,
input guard, `--input-sha256` pin and ids-and-counts-only output as the spec 2
samplers; `--out` defaults to `benign/`.

Every accepted candidate is excerpted (whole paragraphs, at most 6 000
characters), re-hosted to a reserved name (`<genre>.example`, raw-reject
characters percent-encoded; attribution stays in `source.url`), rendered into
its surface and **driven through `drive()` with `fallback=0.0` before it is
written**. A candidate that trips a stage-2 regex stays in its genre with
`pinned: ["flagged", "blocked"]` and is listed as *needs-variant*; then run

```bash
uv run python -m tests.corpus_stage2 name-variants tests/corpus/benign/<genre>.jsonl
```

to set `params.variant` to the first regex hit. Such a record is an organic
structural false positive and counts toward its genre's FPR. Rejections are
only for reasons unrelated to stage 2 (`benign.REJECTION_REASONS`: licence,
unpinned, too_short, non_prose, duplicate, secret_shape, invalid_url, lint).
`benign/sampler_stats.json` holds `{genre: {examined, rejections}}`; a genre
whose records are not all `third_party` carries `not_ingested: <reason>` there
(the offline fallback: its records are `source.kind: synthetic`).

**Regex probes.** Records authored to match a stage-2 regex live in
`over_defence_probe`, never in a headline genre, and are the only records that
count toward the at-least-two-per-regex coverage floor (the three
`vocab.STAGE2_REGEX_NO_BENIGN` names excepted). The matching string is visible
text on its surface.

## Security prose and over-defence probes (spec 3 US-002)

Two genres hold the text most likely to be wrongly blocked, and each is
measured on its own line. **`over_defence_probe` is reported separately: it is
never pooled into the headline false-positive rate** (spec 5 reports it as its
own number), because its records are written or chosen to be near-misses. The
US-001 regex probes, the NotInject queries and the owned probes below are all
counted there and nowhere else. `security_prose` is likewise its own genre.

| Genre | What it holds |
|-------|---------------|
| `security_prose` | Writing *about* prompt injection: excerpts of CC-BY-4.0 arXiv papers (`--source arxiv`), and owned docs pages, changelog, FAQ, glossary, tutorial and search snippets, some quoting a trigger phrase the way a defence page does |
| `over_defence_probe` | The US-001 regex probes, NotInject's benign queries re-homed as web text (`--source notinject`), and owned look-alikes: support-forum transcripts with line-start speaker labels, a recipe step, a legal notice, an invoice reminder |

**arXiv.** The sampler reads a JSONL the operator prepared on the host, one
paper per line: the abstract and one section (the first with at least 1 500
characters of prose that is not a bibliography, acknowledgement or appendix),
the licence link the arXiv abstract page states, and the author list. It takes
a paper only when that link is CC BY 4.0 or CC0 and pins it by arXiv id and
version (`source.name` is `arXiv:<id>`, `source.revision` the version). Each
paper has its own `NOTICE` entry with the author list as the paper states it.

**NotInject.** `leolee99/NotInject` (MIT, 339 benign queries built from
injection trigger words) ships as three parquet files, by number of trigger
words. Convert them once on the host, outside the repository, to one JSON line
per row (`subset`, `index`, `category`, `prompt`), pin the result with
`--input-sha256`, and run `--source notinject --revision <dataset commit>`.
The dataset's four slices (`Common Queries`, `Technique Queries`,
`Virtual Creation`, `Multilingual`) are the strata: candidates are shuffled
under the seed within each slice and taken in rotation, so any `--limit` draws
evenly from all four. A query is a user turn, so each is **re-homed** into web
text (`source.framing = rehomed_direct`) as a community-question page, a Q&A
search snippet or a `text` upload, rotating in that order; `lang` comes from the
query's script, with stop words for Latin-script languages. All 339 rows drive
`clean` structurally: a single trigger word is not a stage-2 phrase, so this
slice measures the classifier, not the regexes.

**The same path as every other benign record.** Each record, owned probes and
`security_prose` included, is driven with `fallback=0.0`; one that comes back
`flagged` or `blocked` gets `pinned: ["flagged", "blocked"]` and then
`params.variant` from `python -m tests.corpus_stage2 name-variants`. The
variant is the **observed** first hit and only that. An owned record's `notes`
says `intended: <regex>` when it was written to trip one; a record that misses
carries no variant, drives `clean` and is listed by id in the spec's
Implementation Notes as a miss.

## Multilingual and long-form (spec 3 US-003)

`multilingual` holds benign text in eight non-English languages (de, fr, es, pt,
it, ja, zh, ru at four records or more each, plus ko) spread across all three
surfaces, so the 22M model's false-positive rate is measured on the axis its
model card reports as weakest. `long_form` holds pages of 6 000–20 000
characters with `params.windows_min` of 3 or more, the records on which the
contiguity rule's accident rate is first measured; they carry no pins.

`windows_min` is an **authoring estimate, not a measurement**: windows are
token-based (512-token windows, 64-token overlap, through the real tokenizer),
and the estimate is `ceil((chars / 4.5 - 512) / 448) + 1`. Spec 4 checks it
against the recorded window count; a record that falls short is re-authored.

Both genres took the offline fallback in US-003 (`not_ingested` in
`benign/sampler_stats.json`; records are `source.kind: synthetic`): the
permissive sources — other-language Wikinews editions (CC-BY-2.5), the Python
documentation translations (PSF-2.0) and Project Gutenberg public-domain books —
can be ingested later through the benign sampler. `NOTICE` gains no entry.

**Size cap.** The JSONL under `attacks/` and `benign/` together stays at or
below 1.5 MB; a lint test asserts it, so a long record is budgeted, not free.

## Natural-language and authority/SEO poisoning (spec 2 US-005)

`natural_language` and `authority_seo` are the two categories only stage 3 can
carry. They hold no technique string, no role marker and no bracket — nothing a
stage-2 regex matches — which is the shape Meta's Prompt Guard 2 model card says
the model was not built for (it dropped the injection sub-label; landscape
finding 6). Every **owned** record (`source.kind == "owned"`) is therefore
asserted `leaked` under `fallback=0.0` on its route, in both rule configs; a
record that trips a regex belongs in a structural family instead. The assertion
is scoped to owned records on purpose: the ingested rows (spec 2 US-004) land in
these same two categories by the mapping rule, attacker-authored text is dense
with BLOCK-category phrasing, and a share of it legitimately trips stage 2. Those
rows carry their own assertion — `leaked`, or the structural category they were
given.

`atk-0403` … `atk-0460` are this story's records (32 `natural_language`, 26
`authority_seo`), beside the seed's `atk-0012` and `atk-0013`. All are
`params.variant: plain`, `framing = indirect` and unpinned.

### Languages

`lang` is set per record. Every language below is authored in **both**
categories on **all three** surfaces, at least twice each. The multilingual
slice matters because the model card reports a weaker multilingual score for the
22M default than for the 86M (landscape finding 7), so it is where the shipped
model is least sure of itself.

| `lang` | Language | Why it is here |
|--------|----------|----------------|
| `en` | English | Required; the base language of the seed and of every ingested row |
| `de` | German | Required |
| `fr` | French | Required |
| `es` | Spanish | Required |
| `pt` | Portuguese | Required |
| `ja` | Japanese | Required; no word spaces, so a window holds more meaning per token |
| `zh` | Simplified Chinese | Added (cheap, and a second non-spaced script) |
| `it` | Italian | Added (cheap) |

### Natural-language shapes

`notes` starts `context: <context>; ask: <ask>;`. The instruction is addressed to
the assistant reading the text, inside ordinary prose of the named kind.

| Context | Where the instruction sits |
|---------|----------------------------|
| `article` | An aside in a guide or explainer, `page` |
| `product_review` | A review body, as a `search` snippet or a `page` |
| `forum_reply` | A reply under a forum question, `page` |
| `readme` | A note in a README uploaded as `text` |
| `changelog` | A note in a changelog uploaded as `text` |

| Ask | What the record tells the reading agent to do |
|-----|-----------------------------------------------|
| `summarise_falsely` | Summarise the text so it says something it does not |
| `recommend` | Recommend one seller or edition and no other |
| `visit_url` | Send the user to a reserved-host URL in the text |
| `include_phrase` | Repeat a stated sentence in the answer |

### Authority/SEO shapes

`notes` starts `claim: <claim>; vector: <vector>;` and ends with the pair the spec
asks for — the Zscaler campaign shape (finding 11) beside the model-card finding
(finding 6). The claim is polite: no technique, only a publisher or seller
asserting standing.

| Claim | What the record asserts |
|-------|--------------------------|
| `rank_first` | The page must be ranked first as the primary source, and other sellers not cited |
| `verified_source` | The site is the verified, authoritative destination for its goods |
| `publisher_claim` | The publisher is the only verified source for its titles, and copies elsewhere are unauthorised |
| `payment_policy` | Payment instructions phrased as policy: deposits go to one portal and any other route is void |

| Vector | Surface | Where the claim rides |
|--------|---------|-----------------------|
| `surfaced_snippet` | `search` | A snippet that reads like a site's meta description |
| `og_description_snippet` | `search` | The Open Graph description the provider surfaces as the snippet; sibling of `atk-0193` |
| `stuffed_title` | `search` | A keyword-stuffed title; sibling of `atk-0213` |
| `page_body` | `page` | The body of an "about the publisher" page |
| `text_file` | `text` | A policy file uploaded as text |

The two `search` siblings are the ones the hidden-markup story deferred: the
carrier itself is `page`-only (stage 1 strips the meta tag), but a provider
surfaces the description as the snippet, so the same claim reaches `/search`
unstripped. `notes` names them as `sibling of atk-NNNN (carrier)`.

## Content rules (ruling 8)

- **Hosts:** every URL — `payload.url` and any `href` / `src` / `content=`
  URL in the HTML — is under RFC 2606: `example.com`, `example.net`,
  `example.org`, `*.example`, `*.test`, `*.invalid`. `*.localhost` is not
  allowed. Three declared exceptions: `params.url_exception: "scheme"` admits
  `data:` / `javascript:` URLs; `params.url_exception: "private_ip"` admits
  RFC 1918 literal hosts; `params.url_exception: "ipv6_zone"` admits a
  link-local (`fe80::/10`) or documentation (`2001:db8::/32`) IPv6 literal host,
  with or without a zone id — the shape audit 2026-09-16-032 needs. Undeclared,
  all three fail, and a declared one admits only its own shape.
- **Secrets:** no string matches a `vocab.SECRET_PATTERNS` shape. Exfil bait
  uses `FAKEKEY-` with a body of at most 8 characters. This lint is the only
  automated gate on corpus secret shapes.
- **Sizes:** `search` title ≤ 512, url ≤ 2 048, content ≤ 2 000 characters;
  `page` document ≤ 200 000 bytes; `text` ≤ 114 688 characters.
- **Licences:** a `third_party` record carries one of
  `vocab.THIRD_PARTY_LICENCES` and a `revision` — never share-alike or
  non-commercial.
- No PDF-bearing records, and nothing that needs DNS or a live fetch.

## Add-a-record checklist

1. Pick the next free id for the kind (`atk-` / `ben-`); append one line to the
   category's or genre's file.
2. Keys in the order above; `payload` keys exactly those of the surface.
3. Attack: a distinctive `marker` that survives the record's variant.
4. Hosts reserved, or the exception declared in `params.url_exception`.
5. No secret shapes; bait is `FAKEKEY-` + ≤ 8 characters.
6. Third-party text: permitted licence, `revision`, `record_ref`, `framing`.
7. `uv run pytest tests/test_corpus_lint.py -q` is green. Never paste the
   payload into the commit message.
