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
(`params.carrier`: `jsonld_offers`, `meta_description`, `og_description`,
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
