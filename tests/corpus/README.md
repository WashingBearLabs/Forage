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

## Content rules (ruling 8)

- **Hosts:** every URL — `payload.url` and any `href` / `src` / `content=`
  URL in the HTML — is under RFC 2606: `example.com`, `example.net`,
  `example.org`, `*.example`, `*.test`, `*.invalid`. `*.localhost` is not
  allowed. Two declared exceptions: `params.url_exception: "scheme"` admits
  `data:` / `javascript:` URLs; `params.url_exception: "private_ip"` admits
  RFC 1918 literal hosts. Undeclared, both fail.
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
