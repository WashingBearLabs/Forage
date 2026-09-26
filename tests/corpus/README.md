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
