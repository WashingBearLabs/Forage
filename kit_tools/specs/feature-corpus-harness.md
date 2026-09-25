<!-- Template Version: 2.5.0 -->
---
feature: corpus-harness
status: active
session_ready: true
depends_on: [hardening-release]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 1
epic_final: false
execution_order: [US-001, US-002, US-003]
created: 2026-09-19
updated: 2026-09-24
---

# Feature Spec: Corpus Harness — Record Format, Lint, Replay Classifier, Route Drivers, Outcome Model

> Spec 1 of 6 (spec 0 added 2026-09-24) in `epic-forage-injection-corpus` (wrapper:
> `epic-forage-injection-corpus.md`, owner decisions 1–4 and 17, rulings 5–16 and 6a). Runs after
> spec 0 (`corpus-86m-enablement`), which itself follows the **whole** hardening epic
> (`hardening-release` is its final spec): every seam below is named in its post-hardening form.
> Anchors were first read at `main` = `20ddb2a` and **re-verified at `403e9c5` (post-hardening
> `v1.2.1`, contract `1.3.0`) on 2026-09-24**; they are cited by symbol with the line as a hint
> ("~:N at 403e9c5"). Spec 0 touches none of the files cited here except `model_fetcher.py`
> (`ALLOWED_MODEL_IDS`), `promptguard/classifier.py` (`_PINNED_GENERIC_LABEL_INDICES`) and
> `pipeline/extraction_limits.py` (`CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL`; ruling 6a), so a
> hint may drift by a few lines there; re-grep the symbol, never trust the offset.

## Overview

Nothing in the repo can run an attack record through the service and say what happened to it. The
parity tests call `run_search_pipeline` directly and mock the loop-level `run_promptguard` (audit
2026-09-16-025), so `stage3_promptguard.py` never runs; `/retrieve` tests patch every stage; nothing
measures whether a payload reached the wire. This spec builds the instrument the four corpus specs
after it (2–5) fill and read: a JSONL record format with a closed vocabulary and a lint that keeps the corpus
committable in a public repo; a **replay classifier** that stands in for Prompt Guard at the exact
seam stage 3 calls (`PromptGuardClassifier.classify_windows`) and refuses, loudly, to invent a score;
three **drivers** that push a record through the real app via `POST /search`, `POST /retrieve` and
`POST /extract`; and a four-way **outcome model** (`blocked` / `flagged` / `neutralised` / `leaked`)
computed from the wire response. It lands with a seed of the handoff vectors so the instrument is
proven on the records that motivated the epic.

## Goals

- Every record in `tests/corpus/` loads through one loader, passes one lint, and is driven through
  every route its `surface` targets by one call — with no network, no weights and no `run_promptguard`
  mock (the classifier double sits below stage 3, not above it).
- A cassette miss is a hard error that names the record id, route, rule config, model identity and
  text hash — never a default score **in any gated run** (ruling 10; landscape finding 14). The one
  exception is the explicitly test-only `fallback=` argument, which exists so a test can drive a
  record with no cassette at all (`fallback=0.0` for the structural-only measurement); it is never
  set in the CI gate, and a test asserts the gate's entry point passes `fallback=None`. *(Clarified
  2026-09-19, validation round 1 — this goal read as an absolute that the spec's own escape hatch
  contradicted.)*
- The four outcomes are computed from the wire body alone; a record's payload text never appears in
  a test name, an assertion message, a log line or a report line (ruling 8; finding 16).
- The seed set pins the two audit bypasses (-016, -032) as `blocked` on `/search` and covers every
  stage-2 category, the metadata carriers and both stage-3 residual shapes, so specs 2–5 start from
  a working instrument.
- Zero runtime change (rulings 6, 6a): the `git diff --stat` set, taken against the epic branch's merge base with `main` (`git merge-base main HEAD`, ruling 6a), is empty at the end of the spec.

## User Stories

Execution order: `[US-001, US-002, US-003]` (document order).

### US-001: Record format, loader and lint

**Priority:** P1

**Description:** As a maintainer, I want one documented JSONL record shape with a closed vocabulary,
a typed loader and a lint that rejects anything unsafe to commit — secret-shaped strings, resolving
hostnames, unknown categories, oversized payloads, duplicate ids — so the corpus can grow in a public
repository without a review having to re-derive the rules each time.

**Independent Test:** `uv run pytest tests/test_corpus_lint.py` passes against an empty corpus
directory and against the seed records; the lint function rejects, with the record id and the rule
name only, a record carrying a real-shaped secret, one carrying a resolving hostname, one with an
unknown category, and one whose `marker` is not a substring of its payload — and never echoes the
offending value.

**Implementation Hints:**
- **Layout (ruling 7).** Code: `scripts/corpus/__init__.py`, `scripts/corpus/records.py` (frozen
  dataclasses + loader + lint), `scripts/corpus/vocab.py` (the closed vocabularies). Data:
  `tests/corpus/attacks/<category>.jsonl`, `tests/corpus/benign/<genre>.jsonl`,
  `tests/corpus/README.md` (write it in the register of `tests/fixtures/README.md`: what each file
  is, why it exists, how to regenerate). `scripts/` is on the strict pyright root
  (`pyproject.toml` `[tool.pyright]`, no carve-out — `reportPrivateUsage` is relaxed only for
  `tests/`), so the loader is typed strictly and imports nothing private from the service.
- **Record shape.** One JSON object per line, keys in this order (the lint enforces order so diffs
  stay readable):
  `id` (`atk-NNNN` / `ben-NNNN`, unique across every file), `kind` (`attack` | `benign`),
  `category` (attack vocabulary — 16 values: `instruction_override`, `authority_impersonation`,
  `prompt_boundary`, `encoded_payload`, `suspicious_url`, `exfil_beacon`, `envelope_breakout`,
  `line_anchored_role`, `url_borne_envelope`, `natural_language`, `authority_seo`, `hidden_markup`,
  `boundary_straddle`, `density_thinned`, `repetition_camouflage`, `sustained_midband`; benign
  vocabulary — 9 genres: `news`, `docs`, `forum`, `ecommerce`, `code`, `security_prose`,
  `multilingual`, `long_form`, `over_defence_probe`), `surface` (`search` | `page` | `text`),
  `payload` (an object whose keys depend on `surface`: `search` → `title`, `url`, `content`,
  optional `engine`, `content_kind` (`snippet` | `chunk`); `page` → `url`, `title`, `head_html`
  (may be empty; where JSON-LD / OG / meta carriers go), `body_html`; `text` → `filename`, `text`),
  `marker` (attack only: a verbatim substring, ≥ 12 characters, no newline, that must not reach the
  wire — compared after NFC, case-fold and whitespace-run collapse on both sides), `pinned` (`null`
  or a non-empty list drawn from `blocked` / `flagged` / `neutralised` / `clean`: the outcomes
  acceptable on **every** applicable route), `pinned_reason` (required iff `pinned`).
  **`pinned` is how a benign record declares a non-`clean` expectation, too** — a `news` record the
  triage step tagged with `params.variant` because its text trips a stage-2 regex carries
  `pinned: ["flagged", "blocked"]` with `pinned_reason` naming the regex, and untagged benign records
  carry no `pinned` and are asserted `clean` by a generic test. The semantics are unchanged in both
  cases — "these are the acceptable outcomes; a change here is a regression to look at" — so no new
  field is needed and `clean` joins the vocabulary. *(Added 2026-09-19, validation round 3: spec 3's
  round-2 "every record's outcome equals the outcome its record declares" named no field to hold the
  declaration.)* `source` (`kind`:
  `synthetic` | `owned` | `third_party`; `name`; `url`; `licence` (SPDX id or `n/a`); `revision`
  (commit SHA or dataset revision, required for `third_party`); `record_ref`; `framing`:
  `indirect` | `rehomed_direct` — finding 13), `lang` (BCP-47), `params` (object; allowed keys per
  category: `density`, `placement`, `repeat`, `carrier`, `windows_min`, `variant` — spec 2 defines
  values), `notes`.
- **Lint rules** (each a named function; the test parametrises over them): unique ids; prefix
  matches `kind`; category in the vocabulary for the kind; surface-specific payload keys exactly;
  `marker` present iff attack, and a substring of at least one payload text field **under the same
  normalisation the leak check uses** (`pipeline.stage1_extraction.normalize_text` + `casefold`, and
  compared against the post-pipeline form — see US-002's leak-check bullet; the lint imports that one
  helper rather than defining its own). *(Corrected 2026-09-19, validation round 3: this rule said
  only "normalised", and a reader would implement the obvious NFC+casefold+`\s+` version — which
  strips no invisibles and would therefore **reject** the very `zwsp` record US-002's acceptance
  criteria require, since its marker matches the payload only after the invisible-strip. One
  normalisation, one helper, both ends.)*
  `pinned` ⇒ `pinned_reason`; every URL (in `payload.url` and any `href` / `src` / `content=`
  attribute value inside `head_html` / `body_html`) has a host under RFC 2606 (`example.com`,
  `example.net`, `example.org`, `*.example`, `*.test`, `*.invalid`, `*.localhost` excluded because
  `url_validator` rejects it), **or** is one of the two declared non-host exceptions below — ruling 8,
  finding 16.
  **The two exceptions are keyed on the record declaring them, not on a category** (corrected
  2026-09-19, validation round 1: both were written as carve-outs for the `suspicious_url`
  *attack* category, which a benign record can never hold — so spec 3's `data:image/png` inline icon,
  its `data:image/svg+xml` favicon, its `javascript:void(0)` accessibility note and both specs'
  private-IP probes were unlintable by construction):
  (a) a `data:` or `javascript:` scheme URL, permitted when the record sets `params.url_exception:
  "scheme"`; (b) an RFC 1918 literal host (`10.*`, `172.16-31.*`, `192.168.*`), permitted when the
  record sets `params.url_exception: "private_ip"` — these are the private-IP probes both the
  `suspicious_url` attack category and the `over_defence_probe` benign genre need, and an RFC 1918
  literal is not an RFC 2606 reserved name so the host rule alone would reject it. Both exceptions
  are in the `params` allowlist for every category and genre, a test covers one record of each kind
  using each exception, and a record that uses a `data:`/`javascript:`/private-IP URL **without**
  declaring the exception still fails the lint; **secret shapes**: no substring matching `hf_[A-Za-z0-9]{20,}`,
  `ghp_[A-Za-z0-9]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`, `sk-[A-Za-z0-9]{20,}`,
  `sk-ant-[A-Za-z0-9_-]{20,}` (Anthropic-style: the hyphens after `sk-ant` end the plain `sk-` run
  short of 20 characters, so the generic shape misses it — added 2026-09-24, validation round 4),
  `AKIA[0-9A-Z]{16}`, `xox[abprs]-[A-Za-z0-9-]{10,}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`, or the
  generic shape `(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['"]?[A-Za-z0-9_\-]{16,}` — and
  exfil bait uses the documented fake prefix `FAKEKEY-` with a body of at most 8 characters
  (finding 12; **this lint is the only automated gate on corpus secret shapes** — CI runs no
  `gitleaks` job, see the corrected Refinement Note below — and `.gitleaksignore` gains no entry);
  sizes: `search` fields within the orchestrator caps (title ≤ 512,
  url ≤ 2 048, content ≤ 2 000 characters — `_MAX_SEARCH_TITLE_LENGTH` / `_MAX_SEARCH_URL_LENGTH` /
  `_MAX_SEARCH_SNIPPET_LENGTH` (`pipeline/orchestrator.py`, ~:911-913 at 403e9c5; values unchanged
  by hardening), `page` HTML ≤ 200 000 bytes, `text` ≤ 114 688 characters (the classifiable
  ceiling, `docs/configuration.md`'s `max_promptguard_chunks` row, ~:920 at 403e9c5); `lang` parses as a BCP-47 tag (`[a-z]{2,3}(-[A-Za-z0-9]{2,8})*`);
  `source.licence` for `third_party` is one of `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`,
  `CC0-1.0`, `CC-BY-2.5`, `CC-BY-3.0`, `CC-BY-4.0`, `PSF-2.0`, `Unlicense`, `LicenseRef-PublicDomain`
  (ruling 3 / owner decision 3; a share-alike or non-commercial licence can never appear — finding
  2, 3); `params` keys are allowed for the category.
- **Negative control** (finding 12): a test feeds each secret regex a real-shaped value and asserts
  the lint rejects it — the lint cannot be silently off.
- **The `params` key allowlist covers all 25 vocabulary members — 16 attack categories and 9 benign
  genres** (corrected 2026-09-19, validation round 1). An earlier draft scoped the allowlist per
  *category* only, and no story extended it to the genres, so every benign record carrying
  `params.variant` or `params.windows_min` would have failed the allowlist rule on day one. Related:
  `params.variant` is **one** closed vocabulary shared by both kinds and its meaning is keyed on
  `kind` — for `kind == "attack"` it names an obfuscation transform (`plain`, `case`, `entity`,
  `zwsp`, `split_tags`, `url_*`, `title_field`, `second_paragraph`, spec 2 US-001); for
  `kind == "benign"` it names the `STAGE2_REGEX_NAMES` member a record deliberately trips (spec 3
  US-001). The lint validates `variant` against the vocabulary its `kind` selects; a test covers one
  record of each kind and one cross-kind value that must be rejected.
- **Count floors** (ruling 14) live in `scripts/corpus/vocab.py` as `MIN_RECORDS` constants; the
  lint test asserting them is written now but **skips with the reason `"asserted from spec 5
  US-002"`**, and **spec 5 US-002 is the one story that removes the skip**. *(Corrected 2026-09-19,
  validation round 1: this bullet said the skip lasts "until spec 3 lands" while the acceptance
  criterion, the Edge Cases entry and spec 5 all say spec 5 US-002. Spec 5 is right — the corpus is
  not complete until spec 3's benign genres are in, and the floors cover both kinds.)*
- **Payload never printed.** Lint failures raise `CorpusLintError(record_id, rule)`; its `__str__`
  is `f"{record_id}: {rule}"` and a test asserts that a failing record's payload substring is absent
  from the message.
- The loader returns `tuple[CorpusRecord, ...]` in file order then line order; `load_corpus(root:
  Path = TESTS_CORPUS_ROOT)` so tests can point it at a temporary directory.

**Acceptance Criteria:**
- [ ] `scripts/corpus/records.py` defines `CorpusRecord` (frozen dataclass, `tests.fakes.assert_frozen`
      pins it), `load_corpus(root)`, `lint_corpus(records) -> list[CorpusLintError]`, and
      `CorpusLintError(record_id, rule)` whose message carries the id and rule only.
- [ ] `scripts/corpus/vocab.py` holds the 16 attack categories, 9 benign genres, 3 surfaces, the
      surface → payload-key map, the `params` key allowlist **per category and per genre** (see the
      note below), the permitted third-party licence ids, the RFC 2606 host rule, the secret-shape
      regexes, the size caps and the `MIN_RECORDS` floors — each as a typed constant with a one-line
      comment naming its ruling.
- [ ] `scripts/corpus/vocab.py` also holds **`STAGE2_REGEX_NAMES`**: the closed, ordered tuple of
      human names for the compiled patterns in `pipeline/stage2_structural.py` (24 at 403e9c5, by
      `_PATTERNS`), plus `STAGE2_REGEX_PROBES` mapping each name to the literal fixture substring
      that provokes it, and **`STAGE2_REGEX_NO_BENIGN`** — the three names exempt from spec 3's
      ≥ 2-benign-records floor because they match Forage/Poppy-internal tokens that cannot occur in
      real external text (`[poppy]`, `^POPPY:`, the `envelope_breakout` pattern). A test asserts
      `len(STAGE2_REGEX_NAMES) == len(_PATTERNS)` so the tuple cannot silently fall behind the
      scanner, and a second asserts `STAGE2_REGEX_NO_BENIGN` is exactly those three so a fourth
      exemption cannot be added quietly. *(Added 2026-09-19, validation round 1: spec 3 US-004
      invented this constant inside a P2 benign story, which made a shared vocabulary a side effect
      of one genre's coverage test. `vocab.py` is spec 1's deliverable and the single source of every
      closed vocabulary — it belongs here, and spec 2's `notes` convention and spec 3's coverage test
      both read it.)*
- [ ] Per-pattern identification is done by **running the patterns**, not by the public `category`
      field and not by probe literal: a helper `stage2_hits(text) -> frozenset[str]` returns
      `frozenset(name for name, (_, pattern) in zip(STAGE2_REGEX_NAMES, _PATTERNS) if
      pattern.search(text))`. It lives **tests-side** (`tests/corpus_stage2.py`), because
      `_PATTERNS` is private and `tests/` is the one pyright execution environment with
      `reportPrivateUsage` relaxed — importing it from `scripts/` would fail strict pyright, and a
      public accessor in `pipeline/stage2_structural.py` would be a runtime change (ruling 6).
      `FlaggedSpan.category` carries only 7 coarse values (`instruction_override`,
      `authority_impersonation`, `encoded_payload`, `prompt_boundary`, `suspicious_url`,
      `exfil_beacon`, `envelope_breakout`), so it cannot distinguish `[SYSTEM]` from `<system>` from
      `---INSTRUCTIONS---`; spec 3's coverage test uses this helper. `STAGE2_REGEX_PROBES` stays in
      `vocab.py` as the per-name fixture, and a test asserts `stage2_hits(probe)` contains its own
      name for every entry, proving each name fires. **Which text it runs on** (round 5): the same
      module holds `stage2_forms(record) -> tuple[str, ...]`, the exact strings stage 2 receives on
      the record's route, built by calling the pipeline's own functions (private ones included —
      this is the tests environment): `search` → **if the URL does not clear the rule chain**
      (`_canonicalize_search_url` omits it, or the effective policy blocklist hits its domain),
      **`()` for the whole record**. `run_search_pipeline` `continue`s before the snippet is
      normalised and before the stage-2 loop (`pipeline/orchestrator.py` ~:1763-1792 vs the loop
      ~:1812-1840 at 403e9c5), so stage 2 scans none of that result's fields: title, URL and
      snippet alike. **Otherwise**, the six forms in the loop's order: both outputs of
      `orchestrator._scan_forms_for_search_text` for `title` (scan, then wire), both
      `_canonicalize_search_url(url).scan_texts`, then both outputs for `content` (scan, then
      wire), with the `_MAX_SEARCH_TITLE_LENGTH` / `_MAX_SEARCH_SNIPPET_LENGTH` caps. The
      blocklist is `effective_blocklist` (~:1694: the config's `seed_blocklist` plus any
      `blocked_domains`), which the reconstruction reads the same way. A test drives one record with an omitted URL and a stage-2-hitting title and
      asserts `stage2_record_hits` is empty. *(Round 6: "an omitted URL contributes nothing" was
      too narrow. The whole result skips stage 2.)* `page` → `extract_html(document, url).raw_text`
      over the document the `/retrieve` driver serves; `text` →
      `stage1_upload.extract_upload_text(text.encode("utf-8")).raw_text`. `stage2_record_hits(record)`
      is the union of `stage2_hits` over those forms — the union matters because the wire form is
      what catches a non-`DOTALL` pattern across a line break and the scan form is what catches a
      line-anchored one. A test asserts, for every seed record, that `stage2_record_hits(record)` is
      non-empty iff the result driven with `fallback=0.0` carries a stage-2 signal (`/search` omit
      `structural_blocked` or `suspicious`; `/retrieve` / `/extract` `structural_flags != []` or
      `promptguard_state == "structural_blocked"`) — the drift guard between this
      reconstruction and the pipeline. **`scripts/` never calls any of this** (no `scripts → tests`
      import, per US-002's direction rule): a `scripts/` tool that needs a stage-2 verdict drives
      the record through the public `drive()`, or — for a lint over `page` / `text` only — calls
      the public `scan_structural` on the public form (`extract_html(...).raw_text`,
      `extract_upload_text(...).raw_text`). Naming *which* regex fired is a tests-side step:
      the module also carries a `name-variants <file.jsonl>` entry point (`uv run python -m
      tests.corpus_stage2 name-variants …`) that fills `params.variant` / `pinned_reason` for
      records a sampler listed as needs-variant, printing ids and names only (spec 3 US-001).
      *(Corrected 2026-09-24, validation round 4:
      the earlier draft matched `FlaggedSpan.matched_text` (`scan_structural` sets it to
      `match.group()`, `pipeline/stage2_structural.py` ~:278 at 403e9c5) against the probe literals;
      for any variable-width pattern — a base64 run, `disregard.*instructions`, the URL patterns — a
      real benign match never equals the probe, so the helper could not name the pattern for exactly
      the benign records spec 3's per-regex coverage test counts.)*
- [ ] `tests/test_corpus_lint.py` parametrises every lint rule with a failing record and asserts the
      rule name in the error, the payload absent from the error text, and the seed corpus (US-003)
      lint-clean; the negative-control test covers every secret regex; the `MIN_RECORDS` test exists
      and skips with reason `"asserted from spec 5 US-002"`.
- [ ] `tests/corpus/README.md` documents the record shape field by field, the outcome vocabulary
      (ruling 9), the content rules (ruling 8), the add-a-record checklist, and states in its first
      paragraph that payload text is data never quoted elsewhere.
- [ ] Records are stored only as `.jsonl` (non-renderable; finding 16) — a test asserts no `.html`
      / `.htm` / `.md` file exists under `tests/corpus/attacks/` or `tests/corpus/benign/`.
- [ ] `kit_tools/testing/TESTING_GUIDE.md` gains a mapping row for `tests/test_corpus_lint.py` and
      a `tests/corpus/` fixtures row; the test count line is updated.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-002: Replay classifier, route drivers and the outcome model

**Priority:** P1

**Description:** As a maintainer, I want to hand a record and a classifier double to one function
and get back, per applicable route and rule configuration, what the service did with it — computed
from the wire body through the real app — so catch rate and false-positive rate are properties of
the service, not of a mocked loop.

**Independent Test:** With `ReplayClassifier` seeded with per-window scores for a `search` record's
stage-3 text, `drive(record, classifier, config="default")` returns `blocked` with
`omit_reason == "injection_detected"` when the max score is 0.9, `flagged` with `suspicious == True`
when it is 0.6, and — for a benign record — `clean` when it is 0.1; a `page` record whose `marker`
survives to `body` with no flag returns `leaked`, and the same record with the marker only inside a
stripped `<script type="application/ld+json">` returns `neutralised`; an unseeded text raises
`UnrecordedRecordError` whose message names the record id, route, config, model id and the sha256
prefix and not the text (the classifier-level `UnrecordedTextError(sha256_hex, chars)` carries only
what the classifier can know — the driver re-raises it with the record context, as Technical
Considerations describes; corrected 2026-09-19, validation round 1); every request goes through `httpx.ASGITransport` and the socket guard stays
green.

**Implementation Hints:**
- **The seam** (post-hardening): stage 3 calls `classifier.classify_windows(text, *, max_chunks)`
  → `tuple[list[float], list[str]]` (hardening spec 7 US-002; `classify()` is retained for the
  loader tests). `scripts/corpus/replay.py::ReplayClassifier(scores: Mapping[str, Sequence[float]],
  *, model_id: str, revision: str, fallback: float | None = None)`: `loaded` is `True`;
  `classify_windows` hashes `text` (`hashlib.sha256(text.encode("utf-8")).hexdigest()`), looks it
  up, raises `PromptGuardBudgetExceededError` when `max_chunks is not None and len(scores) >
  max_chunks` (mirror the raise inside `PromptGuardClassifier.classify_windows`,
  `promptguard/classifier.py` ~:282 at 403e9c5 — same exception class, imported from
  `promptguard.classifier`), and returns `(list(scores), [f"window-{i}" for i in range(n)])`;
  `classify()` re-implements the max-pool over it exactly as the real one does (the real
  `classify()`, ~:237-258 at 403e9c5, now delegates to `classify_windows` and pools with `max`)
  so any retained `classify` caller sees the same contract. `fallback` (a constant single-window score) is
  for spec 1–3 tests only — the gate (spec 5) constructs without it; document that in the docstring.
  A miss raises `UnrecordedTextError(sha256_hex, chars)`; the driver re-raises it as
  `UnrecordedRecordError(record_id, route, config, model_id, sha_prefix)` — neither carries text.
  **`RouteResult.model_id` and the error's `model_id` come from the `ReplayClassifier`'s
  `model_id`, never from `app.state.promptguard_model`**: `corpus_app` pins the boot to the 22M
  default (below), whatever cassette is replayed. Under ruling 6a the 86M is a second cassette
  replayed through the same 22M-booted app, and only if spec 0 enabled it — otherwise every 86M
  field downstream reads `not recorded — 86M not enabled`. *(Added 2026-09-24, validation round 4.)*
- **Booting the app** (ruling 15): `scripts/corpus/drivers.py::corpus_app(*, classifier, config:
  RuleConfig)` is an async context manager, and it is **the public home of a helper that already
  exists privately**. `_running_app` (`tests/test_app.py`, ~:1278 at 403e9c5) is a fixture-free
  async context manager that boots the lifespan and yields a client — the same shape, and the model
  to follow.
  Write `corpus_app` here as the public helper, borrowing that structure; do **not** import
  `_running_app` from `scripts/`.
  **Do not migrate `tests/test_app.py` to it.** `corpus_app` unconditionally stubs
  `model_fetcher.acquire_and_load` (that is its whole point — a drive must never fetch weights), and
  `_running_app`'s ~49 call sites (counted at 403e9c5) include tests that exercise the real
  acquisition path through the real lifespan — `test_the_lifespan_calls_the_fetcher_off_the_event_loop`
  (~:1713), `test_promptguard_loaded_flips_without_a_restart` (~:1567),
  `test_a_credential_less_boot_stays_degraded_and_says_so_once` (~:1598) — which a universal swap
  would silently defeat. `_running_app` stays as it is. The repo already keeps
  `_started_with_valkey_url` (~:3573) as a separate helper for the stub-and-install shape,
  which is the precedent: these are two boot recipes, not one. *(Corrected 2026-09-19, validation
  round 3 — round 2's wording mandated the migration.)* *(Corrected 2026-09-19,
  validation round 2: round 1 said to reuse `_running_app` in place, but it is a leading-underscore
  module-private symbol and `pyproject.toml`'s `[[tool.pyright.executionEnvironments]]` relax
  `reportPrivateUsage` only for the `root = "tests"` execution environment (~:132-139 at 403e9c5) — `scripts/corpus/drivers.py` sits in the default `root = "."`
  environment, so importing it would fail this story's own "`uv run pyright` passes" criterion. The
  dependency direction matters too: tests may import from `scripts/`, not the reverse — **and that
  rule binds this spec's own drivers**, see "Corpus-owned doubles" below.)* It
  (a) patches `retrieval_app._load_config` (~:488 at 403e9c5) to return the real `config.yaml`
  dict plus `{"extract_route_enabled": True}` and, for `config == "contiguity"`,
  `{"promptguard_contiguity_windows": 2, "promptguard_contiguity_threshold": 0.5}` (hardening spec
  7's enabling recipe; `promptguard_settings_from_config` validates the keys at boot, ~:1685);
  (b) **makes the boot independent of the caller's shell** (added 2026-09-24, validation round 4):
  `patch.dict(os.environ, ...)` inside the `ExitStack` removes `VALKEY_URL`,
  `FORAGE_CACHE_HMAC_KEY`, `FORAGE_MODEL_ID`, `FORAGE_MODEL_REVISION`, `FORAGE_SEARCH_PROVIDERS`,
  `FORAGE_BRAVE_API_KEY` and the two break-glass variables (`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION`,
  `POPPY_RETRIEVAL_LEGACY_CAPABILITY`), and `retrieval_app.ContentCache` is patched to return the
  corpus-owned lifespan cache double, exactly as `_running_app` patches it (~:1283). Post-hardening
  the lifespan reads all of these before a driver can touch state: `model_fetcher.resolve_model_id()`
  runs first and refuses boot on a non-allowlisted `FORAGE_MODEL_ID` (~:1644); `_select_cache_storage`
  (~:312) picks Valkey whenever `VALKEY_URL` is set and the lifespan then awaits `cache.connect()`
  (~:1829) — a real connection on an operator host, a socket-guard failure under pytest; the provider
  chain and the Brave key capability are built from env (~:1771-1779). `_started_with_valkey_url`
  delenvs `VALKEY_URL` for the same reason. `SEARXNG_URL` is read at import (~:130) and is inert
  here because the `/search` driver replaces the chain; (c) patches `model_fetcher.acquire_and_load`
  with the `_acquisition_that_never_loads` shape (~:3562) and raises
  `model_fetcher.RETRY_INITIAL_BACKOFF_S` (defined `model_fetcher.py` ~:243; the precedent is
  `_park_the_retry`, `tests/test_app.py` ~:1312, which sets it to `3600.0`) so the retry loop never
  runs during a drive; (d) enters `retrieval_app.lifespan(app)` (the `_running_app` idiom) and then
  sets `app.state.classifier = classifier` (handlers read `request.app.state.classifier` per
  request: `/retrieve` ~:2338, `/extract` ~:2478, `/search` ~:2591; `/health` also reads it,
  ~:2089); (e) yields an `httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
  base_url="http://test")` (idiom at `tests/test_stage3_promptguard.py` ~:1156). Use
  `unittest.mock.patch` / `monkeypatch`-free context managers so the helper is importable from
  `scripts/` (pytest's `monkeypatch` is a fixture; `contextlib.ExitStack` + `patch.object` /
  `patch.dict` is the shape). A test boots `corpus_app` with `VALKEY_URL` set to an unreachable
  value and `FORAGE_MODEL_ID` set to a non-allowlisted id, and still drives a record. **Ruling 15
  wording, reconciled:** the wrapper says `acquire_and_load` is patched "to install the replay
  classifier"; this spec implements that as *patched never to load*, plus the post-entry swap in
  (d). Handlers read the classifier per request, so the swap is what they see; `WeightAcquisition`
  keeps its own reference to the discarded real object, which never loads. Patching the
  `retrieval_app.PromptGuardClassifier` factory instead (the `_started_with_valkey_url` precedent)
  is an acceptable alternative that closes the brief window in which state holds the real unloaded
  classifier, at the cost of a no-op `configure_threads` on `ReplayClassifier`; the implementer
  picks one and records which.
- **Corpus-owned doubles** (added 2026-09-24, validation round 4 — the earlier draft had
  `scripts/corpus/drivers.py` import `FakeSearchProvider` / `FakeContentCache` from `tests.fakes`,
  contradicting the stated direction; nothing in `scripts/` imports `tests` today, and
  `tests/fakes.py` imports `pytest` and the socket-guard helpers at module top, which spec 4's
  host-side recorder would then inherit outside pytest). `scripts/corpus/doubles.py` owns two small
  doubles: `CorpusSearchProvider` (`name`, `paid=False`, `origin=None`, an async `search(query,
  max_results)` returning a fixed `ProviderSearchResult`) and `CorpusContentCache` (async `connect`
  → `True`, `get` → `None`, `put` / `delete` → `False`, `close`, `ping_if_due` → `True`, and a
  `metrics` attribute — the call surface the lifespan and handlers use; the implementer re-greps it
  and records the list). A test in `tests/` pins them against the `SearchProvider` protocol
  (`pipeline/search_providers/base.py`) and against the `ContentCache` methods the service calls, so
  the ~40 duplicated lines cannot drift silently.
- **`/search` driver**: set `app.state.search_providers = [CorpusSearchProvider(name="searxng",
  outcome=ProviderSearchResult(provider_name="searxng", results=[{title, url, content, engine,
  date: None}], unresponsive_engines=[]))]` (the corpus-owned double above, same constructor shape as
  `tests.fakes.FakeSearchProvider`, ~:615 at 403e9c5). **The handler no longer reads
  `app.state.search_providers` directly** (confirmed 2026-09-24, validation round 4): it calls
  `_resolved_search_providers(request.app.state)` (`retrieval_app.py`, ~:387; the configured chain,
  ~:2560) and then `apply_request_policy(configured_chain, body)`
  (`pipeline/search_providers/policy.py`, ~:35-88), which narrows the chain by `body.providers` /
  `body.allow_paid_fallback` before passing `providers=effective_chain,
  configured_chain=configured_chain` into `run_search_pipeline` (~:2587-2588). The lifespan sets
  the attribute once (~:1779) and the helper reads it per request, so overwriting it after boot
  still works — **provided the body carries no `providers` and no `allow_paid_fallback`**: with
  both omitted, a free (`paid=False`) provider passes the policy unfiltered, which is why the body
  below is exactly this. Body: `{"query": "corpus", "num_results": 5, "promptguard_fail_closed":
  true}`. Outcome: `blocked` iff `results == []` and `omitted_results == 1` (record `omit_reason` =
  the single key of `omitted_by_reason`; vocabulary `OMIT_*` in `pipeline/contract.py`, ~:215-235
  at 403e9c5, which already includes `OMIT_BLOCKED_URL = "blocked_url"`); `flagged` iff the one
  result has `suspicious == true`; else marker check. The stage-3 text on this route is
  `_search_result_promptguard_input` (`pipeline/orchestrator.py`, ~:1285-1287 at 403e9c5):
  `"Title: …\nURL: …\nSnippet: …"`, **confirmed unchanged by hardening** (2026-09-24). It is fed
  the canonical URL and the **collapsed wire forms** of title and snippet (call sites ~:1861 /
  ~:1885), not the newline-preserving scan form — the recorder (spec 4) must feed the same; the
  driver never re-derives the text.
- **`/retrieve` driver**: `patch("pipeline.orchestrator.validate_url", new=AsyncMock(return_value=
  ("93.184.215.14", host)))` (`validate_url`, `url_validator.py` ~:391 at 403e9c5, returns
  `(resolved_ip, hostname)`; `pipeline/orchestrator.py` imports it in the `from url_validator
  import (...)` block, ~:100-109, and awaits it in `run_retrieve_pipeline`, ~:432 — the return
  value is now discarded there, so the mock's tuple only has to be well-typed) and
  `patch("pipeline.orchestrator.fetch_url", new=AsyncMock(return_value=FetchResult(final_url=url,
  response_body=html.encode("utf-8"), content_type="text/html; charset=utf-8", status_code=200)))`
  (`FetchResult`, `pipeline/stage5_url_audit.py` ~:56-64; the orchestrator wraps the call in
  `asyncio.wait_for`, ~:489; builder idiom `tests/test_orchestrator.py` ~:177 / ~:443).
  `app.state.cache = CorpusContentCache()` fresh per drive (the corpus-owned double). Body: `{"url": url, "extract_mode": "full", "promptguard_fail_closed":
  true}`. Outcome: `blocked` iff `injection_detected == true` or `promptguard_state` in
  `{"structural_blocked", "unavailable_blocked"}` (`RetrievedContent.promptguard_state`,
  `models.py` ~:110 at 403e9c5; `PromptGuardState` in `pipeline/contract.py`, ~:286, now five members
  — `skipped_trusted` and `unavailable_allowed` joined, both of which fall into `flagged` below);
  `flagged` iff `structural_flags != []` or `promptguard_state not in
  {"scanned", "structural_blocked", "unavailable_blocked"}`; else marker check over `title`, `body`,
  `final_url`, `source_url`, `domain`, `redirect_chain`, `truncation_notice`. Hardening spec 2 adds
  `/retrieve` admission (`app.state.retrieve_admission`, ~:1733; `run_retrieve_pipeline` acquires
  it, ~:478) — the lifespan builds it; nothing to patch. `retrieve.fetch_concurrency: 1` in the
  shipped `config.yaml`, and the slot is released on the way out, so sequential driving never
  contends for it.
- **`/extract` driver**: multipart `files={"file": (filename, text.encode("utf-8"), "text/plain")},
  data={"filename": filename, "extract_mode": "full"}` (shape at `tests/test_orchestrator.py`
  ~:2710 at 403e9c5; the `extract` handler's `File()` / `Form()` parameters, `retrieval_app.py`
  ~:2417); `stage1_upload.detect_upload_content_type` (`pipeline/stage1_upload.py` ~:161) returns
  `text` for anything that is not a PDF, so `text` records are plain text by construction.
  Outcome rules as `/retrieve` over `ExtractedContent` (`models.py` ~:184). **Extraction admission
  gate** (added 2026-09-24, validation round 4): hardening shipped `ExtractionAdmissionMiddleware`
  (`retrieval_app.py` ~:1507), which intercepts every `/extract` request before FastAPI routing —
  404 when `app.state.extraction_settings.route_enabled` is false (the `_load_config` override
  enables it), 503 when `app.state.extraction_admission` (an `ExtractionAdmissionController`, built
  by the lifespan ~:1727) is missing, and 429 `{"error": "busy", …}` when `acquire()` fails. It
  releases the slot in a `finally`, and `extraction.extraction_concurrency: 1` in the shipped
  config, so a sequential `drive_all` never contends for it; nothing to patch. A future parallel
  `drive_all` must route through this gate or bypass it explicitly — and either way a 429 is a
  harness error, never an outcome (see the error-response map below).
- **Route map**: `search` → `/search`; `page` → `/retrieve`; `text` → `/extract`. One record, one
  route. (An `also_text` convenience was considered and rejected: two surfaces are two records.)
- **Error responses are a closed map, not "any non-2xx is `blocked`"** (added 2026-09-24,
  validation round 4; **rule restated round 5 — this table is the one authoritative copy, and
  spec 5's `refused` attribution bucket imports it rather than listing codes of its own**). The
  rule: a refusal the pipeline returns for a given record **every time** — decided by the record's
  own bytes, not by load or by the harness — is an **outcome**; anything else is a **harness
  error**. `scripts/corpus/outcomes.py` holds `BLOCKING_ERRORS`, the (route, status, `error`,
  `reason` constraint) rows below, each driven to `blocked` with `status_code`, `omit_reason =
  error` and `refusal = True` in `signals` (the error body carries no record text, so
  `marker_on_wire` is false by construction). Measured through the real app at 403e9c5 (round 5):

  | Route | Status | `error` | `reason` | Why deterministic |
  |---|---|---|---|---|
  | `/extract` | 422 | `unsupported_format` | any | `stage1_upload` text-validity gate: empty, not UTF-8, NUL, no visible text, > 5% `Cc` (`pipeline/stage1_upload.py` ~:40-62) |
  | `/extract` | 422 | `content_too_large_to_classify` | any | the classifiable ceiling (character pre-check, or the window budget of 64 via `PromptGuardBudgetExceededError`) |
  | `/retrieve` | 422 | `content_too_large` | `promptguard_budget` only | the retrieve classification budget; inert at the shipped `retrieve.max_promptguard_chunks: 0` |

  **Everything else raises a harness error** naming record id, route, status and `error` (never
  text) and stops the drive: `/extract` 429 `busy` and `/retrieve` 422 `busy` /
  `admission_queue_full` (admission is load, not content — the harness pins concurrency, so a busy
  answer means the harness is wrong); the middleware's 404 / 503; a 400 (an oversize `/extract`
  upload is answered 400 by FastAPI's multipart parser on the pinned version — the 413
  `content_too_large` is documented but unreachable, `contract/GOVERNANCE.md`; the lint's size caps
  keep every record far below it); a 422 request-validation `{"detail": …}`; 5xx; `extraction_failed`
  (for a `text` upload only the catch-all `Exception` branch reaches it — a fault, not a verdict;
  PDFs are out of scope, ruling 8); every `/retrieve` URL / fetch refusal (`invalid_url`,
  `private_ip`, `blocked_domain`, `fetch_error`, `fetch_timeout`, `content_too_large` with any
  reason other than `promptguard_budget`) — the driver patches `validate_url` and `fetch_url`, so
  these cannot come from the record; and every `/search` error code — the corpus provider double
  cannot fail. The lint caps keep a lint-clean record under both character ceilings (114 688 for
  `/extract`; `page` HTML ≤ 200 000 bytes against 458 752 at a 256-chunk retrieve budget), so the two
  budget rows fire in practice only through the window budget (a text that tokenises into more than
  64 windows) or an operator-lowered retrieve budget — both genuine, repeatable refusals. The
  implementer re-measures each row at story start (a mini drive per row) and records the result in
  Implementation Notes; a row that no longer reproduces is removed, not kept "in case".
- **`flagged` is route-asymmetric by wire construction — outcomes are never compared across routes**
  (added 2026-09-19, validation round 1). On `/search` the orchestrator sets `suspicious = True`
  whenever `pg_result.score > 0.5` (in `run_search_pipeline`, `pipeline/orchestrator.py` ~:1924-1925
  at 403e9c5, logic unchanged), so a sub-threshold score
  surfaces on the wire. On `/retrieve` and `/extract` there is no sub-threshold signal at all:
  `run_promptguard` returns `Stage3Verdict.SAFE` with `flagged_chunks=[]` at or below 0.85,
  `_derive_promptguard_state` returns `"scanned"`, `structural_flags` is empty and `trust_score`
  takes no stage-3 penalty. The same payload replayed at 0.6 is therefore `flagged` on `/search` and
  `leaked` on `/retrieve` — a difference in what the API exposes, not in what the defence caught.
  Consequences, binding on specs 4 and 5: (a) floors stay **per category × route** (ruling 12) and
  are read within a route only; (b) any cross-route comparison — including spec 5 US-004's
  86M-vs-22M and contiguity-on/off decision tables — is computed on the **recorded window scores**,
  never on the derived outcome. `signals["score"]` (the max) suffices for the 86M-vs-22M comparison
  but **not** for contiguity, which is a predicate over the *sequence* of window scores (N
  consecutive windows above a threshold) and cannot be evaluated from a scalar — so `signals` carries
  `window_scores`, the full replayed list, and the contiguity table is computed from that.
  *(Corrected 2026-09-19, validation round 2: round 1's rule bound the contiguity table to a scalar
  no pooler can use.)* (c) spec 5's report states this asymmetry
  in-line wherever a per-route column appears, so a reader does not mistake it for `/retrieve` being
  the weaker route.
- **Outcome model** (`scripts/corpus/outcomes.py`): `Outcome = Literal["blocked", "flagged",
  "neutralised", "leaked", "clean"]`; `RouteResult(record_id, route, config, model_id, outcome,
  signals)` where `signals` is a frozen mapping of `omit_reason`, `suspicious`, `structural_flags`,
  `injection_detected`, `promptguard_state`, `rule` (`PromptGuardResult.rule` is not on the wire —
  read `injection_spans` count only; the report's `rule` column comes from the replay classifier's
  call log, spec 5), `score` (max of the replayed windows), **`window_scores`** (the full replayed
  list — the contiguity table needs the sequence, not the max), `windows`, `status_code`,
  **`refusal`** (true only for a `BLOCKING_ERRORS` row; spec 5's `refused` bucket keys on it),
  **`marker_on_wire`** (below).
- **Leak check** (corrected 2026-09-19, validation round 1 — three separate defects, all in the
  flattering direction):
  - **It runs on every drive, not only in the `else` branch.** Walk the JSON body, collect every
    string except values under a key named `injection_spans` (ruling 9), normalise (below) and test
    `marker in text`; store the boolean as `signals["marker_on_wire"]` **before** the outcome is
    decided. The four-way outcome is unchanged (floors keep their meaning), but a `blocked` result
    that still put the marker on the wire is now visible. This matters because a blocked response is
    not marker-free: `finalize_quarantine` (`pipeline/stage4_structuring.py` ~:179-211 at 403e9c5) replaces only
    `body` and `injection_spans` — it passes `title=result.title` through verbatim — and
    `build_retrieved_content` adds `source_url`, `final_url`, `domain` and `redirect_chain`
    independently of the sanitization result. A `title_stuffing` record (and `hidden_markup`
    generally) is therefore `blocked` *and* leaking, which the pre-correction model scored as an
    unqualified success. Spec 5's report carries a **blocked-but-leaked** column sourced from this
    signal, and the epic's "any bypass the corpus surfaces is filed" criterion covers it.
  - **The normaliser must match what the pipeline already did to the text — so it calls the
    pipeline's own function rather than re-deriving it.** `pipeline/stage1_extraction.py` exports a
    **public** `normalize_text(text)` (the wrapper over `_normalize_text`): NFC, then delete every
    member of `_INVISIBLE_CHARS`, then collapse whitespace. The leak check applies
    `normalize_text()` and then `casefold()` to **both** the marker and the wire text, and does not
    enumerate codepoints anywhere. *(Corrected 2026-09-19, validation round 2 — round 1's correction
    listed five codepoints where `_INVISIBLE_CHARS` (`stage1_extraction.py` ~:53-67, unchanged at 403e9c5) holds **nine**:
    U+200B, U+200C, U+200D, U+200E (LRM), U+200F (RLM), U+202E (RLO), U+FEFF, U+2060 (word joiner),
    U+00AD. The four omitted include RLO and the word joiner, both standard injection obfuscations,
    so a hand-maintained list was wrong within one round of being written and would drift again.
    Calling the public function makes the match true by construction; a test asserts the corpus
    normaliser and `normalize_text` strip the same set by feeding one string containing all nine.)*
    Python's `\s` matches none of the invisibles, which is why `collapse \s+` alone was never
    sufficient. Without the invisible-strip, every `zwsp` / `entity` /
    `split_tags` variant whose marker overlaps the obfuscated token can never match: BeautifulSoup
    decodes entities during parsing and `get_text` rejoins split tags, so the wire text is already
    de-obfuscated while the authored marker is not. Those records would score `neutralised` while
    the payload reached the consumer intact.
  - **A marker must survive its own variant** — lint rule, spec 2 US-001 authors to it: for every
    record, the normalised marker is a substring of the normalised *post-pipeline* form of its
    payload, not merely of the raw payload. A per-variant test (`plain`, `case`, `entity`, `zwsp`,
    `split_tags`, `url_*`, `title_field`, `second_paragraph`) drives one record and asserts the leak
    check sees the marker when the payload genuinely arrives.
- **Never print payload**: `RouteResult.__repr__` prints ids and signals only; a test drives a record
  whose marker is a unique sentinel and asserts the sentinel is absent from `repr(result)`, from the
  `UnrecordedRecordError` message and from pytest's captured output of a failing assertion written
  the documented way (`assert result.outcome == "blocked", result.summary()`).
- **Hermeticity**: the socket guard (`tests/conftest.py`) stays as is; the `/retrieve` driver's two
  patches are the only network seams; `tests/test_hermeticity.py` is untouched.

**Acceptance Criteria:**
- [ ] `scripts/corpus/replay.py::ReplayClassifier` satisfies the stage-3 seam (`loaded`,
      `classify_windows`, `classify`), applies `max_chunks` with `PromptGuardBudgetExceededError`,
      returns placeholder chunk labels, and raises `UnrecordedTextError` on a miss — each pinned by a
      test; the `fallback` path is documented as test-only.
- [ ] `scripts/corpus/drivers.py` exposes `corpus_app(...)`, `drive(record, classifier, *, config)
      -> RouteResult` and `drive_all(records, classifier, *, configs) -> list[RouteResult]`; every
      request goes through `httpx.ASGITransport` with the lifespan booted; the `contiguity` config
      boots with the two keys set and a test asserts the boot-time validation saw them (a
      `[0.6, 0.6]` replay on a `search` record is `blocked` under `contiguity` and `flagged` under
      `default`).
- [ ] `corpus_app` is hermetic against the caller's shell: a test boots it with `VALKEY_URL` set to
      an unreachable value and `FORAGE_MODEL_ID` set to a non-allowlisted id and still drives a
      record to a `RouteResult`; `RouteResult.model_id` equals the `ReplayClassifier`'s `model_id`.
- [ ] `scripts/corpus/` imports nothing from `tests` (a test greps `scripts/corpus/*.py` for
      `tests` imports); `scripts/corpus/doubles.py`'s `CorpusSearchProvider` and
      `CorpusContentCache` are pinned by a conformance test against the `SearchProvider` protocol
      and the `ContentCache` methods the service calls.
- [ ] Error responses go through the closed `BLOCKING_ERRORS` map: a mapped row is `blocked`
      with `status_code`, `omit_reason` and `refusal = True` in `signals` (test cases: a
      harness-only `text` fixture with no visible text — built directly, deliberately bypassing the
      corpus lint, so it is not a committable record — → `/extract` 422 `unsupported_format`; a synthetic `/retrieve`
      `content_too_large` / `promptguard_budget` body); an unmapped one (a 429 `busy` and a
      `/retrieve` 422 `busy` are the test cases) raises a harness error whose message names record
      id, route, status and `error` and no payload text.
- [ ] The Independent Test's outcome cases pass on all three routes: `blocked`, `flagged`,
      `neutralised`, `leaked`, `clean`, including a `page` record whose marker sits only in a stripped
      tag (`_DANGEROUS_TAGS`, `pipeline/stage1_extraction.py` ~:38-49) → `neutralised`.
- [ ] `UnrecordedRecordError` names record id, route, config, model id and an 8-character sha
      prefix; a test asserts the payload is absent from its message.
- [ ] The leak check ignores `injection_spans` and nothing else; a **unit test of the JSON walker
      over a synthetic body** with the marker present only in `injection_spans` yields `blocked`,
      not `leaked`. It cannot be an end-to-end drive: through the real app `injection_spans` never
      carries record text — every blocked result goes through `finalize_quarantine`, which sets it
      to one diagnostic label (~:204), a SAFE result carries `flagged_chunks == []`
      (`pipeline/stage3_promptguard.py` ~:279), and under replay any chunk entries are `window-<i>`
      placeholders. An end-to-end assertion pins that: on a blocked drive, `injection_spans` is
      exactly one member of the stage-4 diagnostic vocabulary, so the exclusion cannot quietly hide
      a future change that puts text back there. *(Clarified 2026-09-24, validation round 4.)*
- [ ] The leak check runs on **every** drive and `signals["marker_on_wire"]` is set independently of
      the outcome: a `page` record whose payload sits in the document title is driven through
      `/retrieve`, and the test asserts `outcome == "blocked"` **and** `marker_on_wire is True`
      (`finalize_quarantine` passes `title` through verbatim).
- [ ] The leak-check normaliser is `pipeline.stage1_extraction.normalize_text` + `casefold`, applied
      to both marker and wire text, with no codepoint list of its own; a test feeds a string holding
      all nine `_INVISIBLE_CHARS` members and asserts the corpus normaliser and `normalize_text`
      agree. A second test drives a `zwsp`-variant record whose marker spans the obfuscated token and
      asserts the leak is seen (it is `leaked`, not `neutralised`).
- [ ] A per-variant test asserts every obfuscation variant's marker survives its own transform —
      the normalised marker is a substring of the normalised post-pipeline payload.
- [ ] A test asserts the gate's entry point passes `fallback=None` — the promise Goal 2 makes about
      the test-only `fallback` escape hatch, which round 1 stated without giving it a home. It lives
      here because spec 1 owns `ReplayClassifier`; spec 5 US-002's gate is what it reads.
- [ ] A test asserts that with `classifier=None` (the unavailable path) the driver still returns a
      `RouteResult` (`blocked` via `promptguard_unavailable` / `unavailable_blocked` under fail-closed)
      so the structural-only measurement is possible without a cassette.
- [ ] No payload text in any `repr`, error message or captured output — asserted as described.
- [ ] `kit_tools/testing/TESTING_GUIDE.md` gains rows for `tests/test_corpus_harness.py` and
      `scripts/corpus/`; `kit_tools/docs/GOTCHAS.md` gains "A corpus record is data — never quote it"
      (the leak paths: assertion output, docs builds, editors rendering HTML fixtures; finding 16).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-003: Seed records — the handoff vectors, one per category, and a benign handful

**Priority:** P1

**Description:** As the epic owner, I want the instrument proven on the records that motivated it —
the two reproduced audit bypasses, one owned record per attack category, the metadata carriers, both
stage-3 residual shapes, and a handful of benign pages — pinned where the hardening epic made a
promise, so specs 2 and 3 grow a corpus that already runs end to end.

**Independent Test:** `uv run pytest tests/test_corpus_harness.py -k seed` drives every seed record
through its route with `ReplayClassifier(fallback=0.0)` (structural-only) and asserts: the -016 and
-032 records are `blocked` on `/search`; every stage-2 BLOCK-category record is `blocked`; every
SUSPICIOUS-category record is `flagged`; the JSON-LD and `<meta>` carrier records are `neutralised`
(stage 1 strips `script` and `meta`); every benign seed is `clean`; and the seed corpus is
lint-clean.

**Implementation Hints:**
- **Pinned records** (`pinned: ["blocked"]`, `pinned_reason` = the audit id): `line_anchored_role`
  — a `search` record whose `content` is two paragraphs with `System:` / `assistant:` markers after
  `\n\n` (audit 2026-09-16-016; hardening spec 1 US-001 makes this `structural_blocked`);
  `url_borne_envelope` — three `search` records with an envelope tag in the path, the query, and an
  IPv6 zone id (audit -032; hardening spec 1 US-002 yields `invalid_url` or `structural_blocked` —
  the pin is the outcome class, the reason is recorded by the baseline); plus two records for the
  decode/strip order hardening spec 1 fixed (ruling R26): the double-entity `&amp;#83;ystem` title
  (`html.unescape` on the scan form, `_scan_forms_for_search_text` in `pipeline/orchestrator.py`
  ~:979 at 403e9c5, decodes it into a line-anchored role marker, so stage 2 blocks it) pinned
  `["blocked"]`; and a **raw-NUL split-token** title — a new owned vector, *not* a copy of the
  hardening fixture — in which a raw NUL splits a non-line-anchored stage-2 token, so the pre-parser
  control strip (`_CONTROL_CHARS_RE`, ~:927, applied to the raw value ~:976) rejoins it and stage 2
  blocks it. *(Corrected 2026-09-24, validation round 4: the earlier draft pinned "the raw-NUL
  title" fixture `["blocked"]`, but that fixture — `_RAW_NUL_TITLE` in `tests/test_orchestrator.py`
  ~:1576, driven by `test_control_characters_never_reach_the_wire` — is a **benign** title with an
  embedded NUL that hardening promises is **served** with the control stripped
  (`specs/archive/feature-hardening-search-sanitization.md`, the "Control characters never reach
  the wire" criterion), so a record modelled on it fails the seed test on day one; nor can a
  ≥ 12-character marker express "a control character did not reach the wire".)* The split-token
  record's `["blocked"]` pin is **set from measurement**: the implementer drives it, records the
  observed outcome and reason in Implementation Notes, and pins `["blocked"]` only if that is what
  was measured — a served result is a finding against the strip-then-scan order, filed, not pinned
  over. `pinned_reason` for both names R26.
- **One owned record per category** (16), authored to the pattern list in
  `_PATTERNS` (`pipeline/stage2_structural.py` ~:61-248 at 403e9c5, 24 entries) for the seven structural categories (e.g. `[SYSTEM]`,
  `[admin]`, ```` ```system ````, a 44-character base64 run, `javascript:`, a markdown image beacon
  with `{{`, `</retrieved_content>`), and to the definitions in spec 2 for the rest; `natural_language`
  and `authority_seo` carry no structural marker and are expected `leaked` under `fallback=0.0` — the
  seed test asserts that explicitly (it is the measurement the epic exists for, not a bug).
- **Metadata carriers** (`hidden_markup`, `params.carrier`): `jsonld_offers` (payload inside a
  `SoftwareApplication` `offers` object — stage 1 parses JSON-LD only for author/date,
  `_json_ld_documents`, `pipeline/stage1_extraction.py` ~:153, and strips `script`; finding 11), `meta_description`,
  `og_description`, `css_offscreen` (`style="position:absolute;left:-9999px"` — **not** stripped:
  stage 1 keeps the text of styled elements), `hidden_div` (`hidden` attribute / `display:none`),
  `title_stuffing`. The first three are pinned `["blocked", "flagged", "neutralised"]` ("never
  leaked"; a widening of JSON-LD / meta parsing is the regression this pin catches); the other three
  are unpinned — they are what stage 3 must catch.
- **Residual shapes**: `boundary_straddle` (`page`, ≥ 1 200 tokens of benign prose with the payload
  split around the 448-token step: `params.windows_min = 2`) and `sustained_midband` (`page`, long
  benign review text; `params.windows_min = 3`) — unpinned, `fallback=0.0` in the seed test; their
  window counts are asserted only after recording (spec 4 US-002).
- **Benign seeds** (≥ 10): one per genre where cheap (`docs`, `code`, `news`, `forum`,
  `ecommerce`, `security_prose`, `multilingual` ×2, `long_form`, `over_defence_probe`); owned
  text; `code` includes a 40-hex git SHA in a URL to exercise the `encoded_payload`
  `[A-Za-z0-9+/]{40,}` regex — expected `flagged` today and asserted as such (a known structural
  false positive the corpus records; spec 3 US-004 grows the family).
- Every URL under RFC 2606; every marker ≥ 12 characters; ids `atk-0001` … and `ben-0001` …
  (spec 2 / 3 continue the numbering; ids are never reused).
- `tests/corpus/attacks/<category>.jsonl` gets its first line here; the lint's `MIN_RECORDS` floors
  stay skipped (US-001).

**Acceptance Criteria:**
- [ ] Seed corpus: ≥ 22 attack records (the pins above + one per category + the six carriers) and
      ≥ 10 benign records, lint-clean, every file under `tests/corpus/attacks/` and `tests/corpus/benign/`
      named by category / genre.
- [ ] The Independent Test's assertions hold, including the explicit `leaked` expectation for
      `natural_language` / `authority_seo` under `fallback=0.0` and the `flagged` expectation for the
      git-SHA `code` benign.
- [ ] Pinned outcomes are asserted by a generic test (`for record in records if record.pinned`)
      that reads the pin from the record — no per-record test code.
- [ ] `tests/corpus/README.md` lists the seed's pinned records with their audit ids (ids only).
- [ ] Zero runtime change (rulings 6, 6a): `git diff --stat
      "$(git merge-base main HEAD)" -- pipeline/ promptguard/ models.py
      retrieval_app.py cache.py url_validator.py model_fetcher.py contract/ config.yaml
      weights_manifest.json Dockerfile` is empty (spec 0's completion tag, not `main` — spec 0 is
      the one spec allowed to touch `model_fetcher.py`, `promptguard/classifier.py` and
      `weights_manifest.json`); `derive_sanitizer_revision({})`, run with `FORAGE_MODEL_ID` and
      `FORAGE_MODEL_REVISION` unset, equals the value recorded in this spec's Implementation Notes
      at story start (taken on that tag); `uv run python -m scripts.export_contract --check` is
      green.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

## Edge Cases

- A record whose `marker` survives only inside `injection_spans` is `blocked`, not `leaked` (US-002).
- A `search` record omitted for `invalid_url` (post-hardening URL rejection) is `blocked`; the
  baseline records the reason (US-002).
- A `page` record whose HTML has no extractable text (empty body) — **measured round 5**:
  `/retrieve` has no empty-body document failure; it answers 200 with `body == ""` and
  `promptguard_state == "scanned"`, so an attack drives to `neutralised` and a benign record to
  `clean`. That is an honest outcome (the payload never reached the wire), and spec 5's per-record
  map is what makes a corpus edit that moves a record there show in the drift diff — the error map
  plays no part. The
  `/extract` analogue (an empty or invisible-only `text` upload) *is* a refusal: 422
  `unsupported_format`, a `BLOCKING_ERRORS` row (US-002).
- `/extract` disabled in `config.yaml` — the driver's config override enables it; a test asserts a
  404 is never silently counted (US-002). Post-hardening the 404 comes from
  `ExtractionAdmissionMiddleware` before routing, and a 429 `busy` from the same middleware is a
  harness error too (the closed error map, US-002).
- Two records with the same stage-3 text share one cassette entry — allowed; the key is the text
  (US-002).
- An unrecorded text under `fallback` set — the fallback answers; under the gate (no fallback) it is a
  hard error (US-002).
- A `contiguity` config whose keys the post-hardening lifespan refuses (out of range) — the boot fails
  loudly; the driver does not catch it (US-002).
- Lint on an empty corpus directory — passes with zero records (floors skipped until spec 5) (US-001).
- A record with `pinned: []` — lint error (`pinned` is `null` or non-empty) (US-001).
- A `marker` that only matches after normalisation (case / whitespace) — accepted; the normalisation
  is documented and shared by lint and leak check (US-001, US-002).

## Out of Scope

- Any change to the service, its models, the contract, `config.yaml` or the hashed sources (ruling 6).
- The cassette file format and the recorder (spec 4); the report, baseline and floors (spec 5).
- PDF-bearing records (ruling 8; recorded gap).
- Records that need DNS or a live fetch; the `/retrieve` driver patches both seams.
- A driver for `run_search_pipeline` directly — every drive goes through the app.

## Assumptions

- The post-hardening seam is `classify_windows(text, *, max_chunks) -> tuple[list[float], list[str]]`
  and stage 3 no longer calls `classify()` (hardening spec 7 US-002/US-007). If US-007 changed the
  shape, the replay classifier follows the shipped one — the spec names the intent, not the bytes.
- `app.state.classifier` and `app.state.cache` remain plain state attributes read per request after
  hardening; `app.state.search_providers` is read per request **through**
  `_resolved_search_providers` and narrowed by `apply_request_policy` (confirmed at 403e9c5; see the
  `/search` driver bullet), so overwriting it after boot still reaches the handler.
- The contiguity keys are validated **at boot** into `app.state.promptguard_settings`
  (`promptguard_settings_from_config`, called from the lifespan ~:1685; bounds: windows 0 or 2–8,
  threshold 0.0–1.0, `pipeline/stage3_promptguard.py` ~:44-62) and passed per request as
  `promptguard_settings=` — not read from `app.state.config` per request. A patched `_load_config`
  is still enough to select a rule configuration, because the read happens inside the lifespan.
  *(Corrected 2026-09-24, validation round 4 — the assumption was wrong in letter, right in effect.)*
  `derive_sanitizer_revision(config)` hashes both contiguity keys
  (`pipeline/sanitizer_revision.py` ~:82-84), so the `contiguity` boot carries a different
  `app.state.sanitizer_revision` from the `default` boot; spec 5's baseline should expect that.
- `example.com` and its RFC 2606 siblings pass every post-hardening search-time URL audit rule
  (literals, canonical names, blocklist) — they are public names on no blocklist.
- Stage 1 keeps the text of CSS-hidden and `hidden`-attribute elements (only `_DANGEROUS_TAGS` are
  stripped) — verified at `20ddb2a`, `_DANGEROUS_TAGS` unchanged at 403e9c5; the seed test records the fact either way.

## Technical Considerations

- **Pyright strictness on `scripts/`**: the drivers import `retrieval_app`, `pipeline.orchestrator`
  and `pipeline.search_providers.base` — **never `tests`** (corrected 2026-09-24, validation round
  4: the earlier text had the drivers import `tests.fakes`, the first `scripts/` → `tests/` edge in
  the repo and a contradiction of the direction this spec states; the doubles are corpus-owned, see
  US-002). Nothing private (`_load_config`, `ContentCache` and `acquire_and_load` are patched by
  string target, not imported). The helpers that need private names — `stage2_hits`, `stage2_forms`,
  `stage2_record_hits` — live tests-side (US-001), and no `scripts/` module calls them.
- **Boot cost**: one lifespan per (config, cassette) not per record — `drive_all` boots once and
  reuses the client; per-record state (`search_providers`, `cache`) is reset inside the loop.
- **Determinism**: record order is file order then line order; results are returned in that order;
  no `set` iteration reaches output.
- **Hardening anchors — re-verified at 403e9c5 on 2026-09-24** (ruling 5). The drift was hundreds
  of lines, not a handful (`retrieval_app.py` is ~2 600 lines, `pipeline/orchestrator.py` ~2 000,
  `tests/test_app.py` ~6 000), so every citation in this spec is now by symbol with the line as a
  hint: `_load_config` (~:488), `_resolved_search_providers` (~:387) / `apply_request_policy`
  (`pipeline/search_providers/policy.py`) / the lifespan's `app.state.search_providers` (~:1779),
  the classifier reads (~:2338 / ~:2478 / ~:2591), `validate_url`'s call in `run_retrieve_pipeline`
  (~:432), `_MAX_SEARCH_*_LENGTH` (~:911-913), `_search_result_promptguard_input` (~:1285),
  `run_search_pipeline` (~:1604, `omitted_by_reason` from ~:1725). Every *semantic* claim the
  reviewers sampled held; only addresses moved. Re-grep the symbol before writing any hint into a
  code comment — never paste a line number into new code.

### Validation residue — closed at `needs-work` (2026-09-19, `/kit-tools:validate-epic`, 3 rounds)

Thirty reviewers over three rounds took this epic from 19 criticals to 0 open; the items below are
the warnings that remained when validation was deliberately closed rather than chased to zero — the
same call, for the same reason, that `epic-forage-hardening` recorded on the same day: the precision
reviewers surface a new layer every round, and **every code anchor in this spec predates eight
unexecuted hardening specs** (ruling 5), so precision spent now is precision spent twice. Re-verify
against the post-hardening tree at execution time; treat each item as a decision the implementer
makes deliberately, not a defect to discover.

- **US-002 is oversized** — five separable jobs (the `ReplayClassifier` stand-in, the `corpus_app`
  boot harness, three route drivers, the outcome model, the leak check). Flagged every round by two
  reviewers. Splitting was deferred rather than declined: if execution times out, split on those
  seams, and note the drivers share nothing but the boot helper.
- **`RouteResult.signals` is an eleven-key heterogeneous frozen mapping** (`str`, `bool`, `float`,
  `list[float]`, `list[str]`, `int`). Under strict pyright a `TypedDict` types it properly; a bare
  `Mapping[str, object]` will push `cast` calls into every consumer in spec 5.
- **Normalising both sides can create false *positives*.** `normalize_text` + `casefold` is right for
  finding a marker through an obfuscated carrier, but two texts that differ only in invisibles or
  case now compare equal. The per-variant test is the guard; a marker chosen from distinctive prose
  rather than a short common phrase is the other half.
- **`window_scores` duplicates what the cassette already holds.** Spec 5 must read the signal, not
  re-derive the list from the cassette, or the two can disagree silently.
- **The payload-never-printed invariant (ruling 8) rests on targeted unit tests**, not a structural
  lint. As the corpus grows across specs 2-5 nothing mechanically stops a new test from formatting a
  payload into an assertion message.
- All three stories are `P1` with no differentiation, and the lint's key-order rule does not say
  whether it reaches nested objects.

### Validation round 4 — 2026-09-24 (post-hardening re-anchor)

Six reviewers re-read this spec against `403e9c5` (post-hardening `v1.2.1`, contract `1.3.0`) after
owner decision 17 added spec 0. Every seam survived; nearly every address moved.

**Fixed:**
- *Anchors* (all six reviewers): every `file:line` re-verified and re-cited by symbol with a
  "~:N at 403e9c5" hint — `retrieval_app.py`, `pipeline/orchestrator.py`, `promptguard/classifier.py`,
  `url_validator.py`, `pipeline/contract.py`, `tests/test_app.py`, `tests/fakes.py`,
  `tests/test_stage3_promptguard.py`, `tests/test_orchestrator.py`, `docs/configuration.md`,
  `pyproject.toml`, `ci.yml`, `docs/bootstrap-notes.md`; `_running_app` call sites ~24 → ~49; the
  `RETRY_INITIAL_BACKOFF_S` precedent named as `_park_the_retry`.
- *Epic shape*: "Spec 1 of 6"; the ruling-6 `git diff --stat` and the recorded
  `derive_sanitizer_revision({})` now taken against spec 0's completion tag (ruling 6a), with the
  403e9c5 value noted for reference; `RouteResult.model_id` sourced from the replay classifier, and
  86M fields "not recorded — 86M not enabled" if spec 0 stopped short.
- *Behaviour mismatches*: the raw-NUL pin (US-003) replaced by a measured, owned split-token record
  (the shipped fixture is a benign, served title); `scripts/` → `tests.fakes` import replaced by
  corpus-owned doubles with a conformance test; `corpus_app` made shell-independent (`VALKEY_URL`,
  cache HMAC key, `FORAGE_MODEL_ID` / `_REVISION`, provider and Brave env, break-glass env,
  `ContentCache` patched); `stage2_hits` identifies patterns by running them, tests-side; the
  `/extract` driver names `ExtractionAdmissionMiddleware` / `extraction_admission`; the `/search`
  driver names `_resolved_search_providers` + `apply_request_policy` and why the body omits
  `providers` / `allow_paid_fallback`.
- *Info, applied*: the Open Question on the stage-3 join closed (unchanged; wire forms fed); the
  contiguity Assumption corrected (boot-time `promptguard_settings`) and the contiguity boot's
  different `sanitizer_revision` noted for spec 5; the `injection_spans` AC restated as a walker
  unit test plus an end-to-end diagnostic-label pin; a closed error-response map; ruling-15 wording
  reconciled; `sk-ant-` added to the secret shapes.

**Carried as residue:**
- *An `injection_spans` exposure counter* (reviewer 5, warning) — not chosen this round. Its premise
  also does not hold at 403e9c5: every INJECTION_DETECTED result goes through `finalize_quarantine`
  (`injection_spans = [diagnostic]`) and every SAFE result carries `flagged_chunks == []`, so no
  non-blocked response carries chunk text; the new end-to-end diagnostic-label pin is the tripwire
  if that ever changes.
- Round-4 items (b) hermetic-gate real-model-load disclosure, (c) a `rehomed_direct` criterion and
  (d) an LLMail-Inject PII rule belong to specs 5 and 2 and are carried there.
- US-002 is now larger still (doubles, error map, hermetic boot); the round-1–3 split guidance
  stands.

**Round 5 (same day):** re-review found two contradictions the round-4 edits introduced, both
resolved here and mirrored in the dependent specs. (1) *Error map ↔ spec 5's `refused` bucket*:
one rule, stated here only — a refusal the record's own bytes cause every time is an outcome
(`blocked`, `refusal = True`), anything load- or harness-caused is a harness error that stops the
drive. Measured through the real app at 403e9c5: `/retrieve` has **no** empty-body document failure
(200, `body == ""`, `scanned` — the round-4 Edge Case was wrong and is corrected); `/extract`
empty / NUL / invisible-only → 422 `unsupported_format`; over-ceiling text → 422
`content_too_large_to_classify`; an oversize upload → **400** (the 413 is unreachable, as
`contract/GOVERNANCE.md` rules). `BLOCKING_ERRORS` is now a three-row table (those two plus
`/retrieve` `content_too_large` / `promptguard_budget`); `extraction_failed` moved to harness
errors (on a `text` upload only the catch-all fault branch reaches it), as did `/retrieve`'s URL /
fetch refusals (the driver patches both seams). Spec 5 imports the table. (2) *`stage2_hits`
reachability*: `stage2_forms` / `stage2_record_hits` added tests-side with the exact per-surface
text stage 2 receives (both search forms per field, both URL scan texts), a seed-record drift
guard, a `name-variants` entry point for spec 3's tests-side naming step, and the rule that
`scripts/` never calls any of it — specs 2 and 3 now say the same.

**Round 6 (same day):** *`stage2_forms` on `search` with an omitted URL.* Verified at 403e9c5: a URL
that fails `_canonicalize_search_url`, or whose domain the `effective_blocklist` hits, reaches
`continue` in `run_search_pipeline` (~:1763-1792) before the snippet is normalised and before the
stage-2 loop (~:1812-1840). Stage 2 therefore scans none of that result's fields. `stage2_forms`
now returns `()` for the whole record in that case, and otherwise the six forms in loop order
(title scan/wire, both URL `scan_texts`, snippet scan/wire). A test pins the omitted-URL case, so
the seed drift guard cannot go red on a correct pipeline. The `rule` reference in the `signals` list
is unchanged: spec 5 US-001 now defines the `rule` column from the replay call log, as cited. Not
applied this round (info): naming `extract_upload_text_file` for the `text` form, a `model_id`
sentinel for `classifier=None`, and dropping `metrics` from the `CorpusContentCache` surface.
`pipeline/extraction_limits.py` was then added to the spec-0 file list (header and Known risks).

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Security: [SECURITY.md](../arch/SECURITY.md) — "Prompt-Injection Signalling", "Security Testing"
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)
- Conventions: [CONVENTIONS.md](../docs/CONVENTIONS.md)
- Testing: [TESTING_GUIDE.md](../testing/TESTING_GUIDE.md)
- Fixtures register: `tests/fixtures/README.md`

## Implementation Notes

<!-- Populated during implementation. Record here at story start, **on the epic branch's merge base with `main`** (ruling 6a; spec 0
runs in parallel and does not change the default-model value, so re-check it once `main` is merged
in before spec 4): the
`derive_sanitizer_revision({})` value, measured with `FORAGE_MODEL_ID` / `FORAGE_MODEL_REVISION`
unset (for reference, `021378efee6ab43f…` was measured at 403e9c5 on 2026-09-24; spec 0 changes no
`_REVISION_SOURCES` member and not the default model's identity, so the tag should reproduce it — if
it does not, stop and find out why before recording anything); the stage-3 seam's exact signature;
the search-time stage-3 join string (confirmed unchanged at 403e9c5 — record the tag's copy); the
re-grepped anchor set; the `ContentCache` call surface the corpus-owned double covers; the
`BLOCKING_ERRORS` rows, each re-measured by a mini drive (round 5 measured no `/retrieve`
empty-body failure — a 200 with `body == ""`); the ruling-15 boot mechanism chosen; the
raw-NUL split-token record's measured outcome. Also record, once, that the reviewer-sampled
assumptions held at 403e9c5 (stage 3 calls only `classify_windows`; the contiguity keys' defaults
`0` / `0.5`; `PromptGuardResult.rule` exists and is not on the wire). -->

## Refinement Notes

### Research Findings

**Decision:** Drive every record through the app over `httpx.ASGITransport` with the lifespan
booted, and stand in for the classifier *below* stage 3.
**Rationale:** Audit 2026-09-16-025 found the parity tests mock `run_promptguard` and never run
stage 3; the handlers read `request.app.state.classifier` per request (`retrieval_app.py`, the
`retrieve` / `extract` / `search` handlers, ~:2338 / ~:2478 / ~:2591 at 403e9c5), so a replay object on `app.state` exercises stage 3, stage 4 and the wire models.
**Alternatives considered:** Calling `run_search_pipeline` / `run_retrieve_pipeline` directly —
rejected, it skips the handlers, admission and the response models; patching `run_promptguard` —
rejected, it is the gap being closed.
**Source:** `tests/test_brave_provider.py` — the loop-level `run_promptguard` patches ahead of
`TestSanitizationParity` (~:1067 / ~:1096 at 403e9c5); `retrieval_app.py`'s `retrieve` (~:2283) and
`extract` (~:2417) handlers; `tests/test_app.py`'s `_running_app` (~:1278) and
`_acquisition_that_never_loads` (~:3562).

**Decision:** Cassette keys are the sha256 of the exact stage-3 input text; the replay returns
placeholder chunk labels; a miss is a hard error.
**Rationale:** Stage 3 only needs scores for its verdict; chunk text on the wire is a quarantine
diagnostic (`injection_spans`) that the leak check excludes; recording chunk text would duplicate
payloads into cassettes. A silent default score would make the gate measure the default.
**Alternatives considered:** Keying by record id (stale-safe but measures text the pipeline no
longer sends); recording chunk texts (payload duplication); a default score on miss (rejected —
finding 14: every cassette practitioner's lesson is that a miss must be loud).
**Source:** `PromptGuardClassifier.classify` / `classify_windows` (`promptguard/classifier.py`
~:237-306 at 403e9c5); `injection_spans=list(promptguard.flagged_chunks)` in
`pipeline/stage4_structuring.py` (~:165);
landscape finding 14 (https://github.com/cheneeheng/mcp-cassette — `search_snippet`, lead only).

**Decision:** `/extract` receives `text` records as plain text; `page` records go to `/retrieve`.
**Rationale:** `detect_upload_content_type` returns only `pdf` or `text`; HTML uploaded to
`/extract` is not parsed as HTML.
**Source:** `detect_upload_content_type`, `pipeline/stage1_upload.py` ~:161-172 (unchanged at 403e9c5).

**Decision:** Records are `.jsonl`; HTML is materialised at test time; every URL is RFC 2606.
**Rationale:** Editors, docs builds and link checkers render `.html` fixtures and may fetch a real
attacker domain; the socket guard protects pytest only.
**Source:** Landscape finding 16 (Zscaler ThreatLabz, 2026-07-02,
https://www.zscaler.com/blogs/security-research/indirect-prompt-injection-web-content-targets-ai-agents).

**Decision:** Secret-shape lint with a fake exfil prefix and a negative control; no `.gitleaksignore`
entry and no path allowlist, ever. **The corpus lint is the only automated gate** — nothing else in
CI scans corpus files for secrets.
**Rationale (corrected 2026-09-19, validation round 1):** an earlier draft of this note said "CI runs
a full-history `gitleaks` scan" and cited `.github/workflows/ci.yml:474-560`. That is false. That line
range is the `secret-grep` job: it runs `docker history --no-trunc` over the **built image's layer
history** and greps three literals (`HF_TOKEN`, `hf_[A-Za-z0-9]{20,}`, `FORAGE_BRAVE_API_KEY`). It
never reads a repository file, so it cannot see `tests/corpus/*.jsonl` at all. The real full-history
`gitleaks` run was a **one-time, owner-run, local** action at the public-flip gate — `.gitleaksignore`'s
header calls it "the full-history secret scan (US-008 gate b)", `docs/bootstrap-notes.md` records
it (the "Full-history secret scan" row, ~:48, and the `gitleaks` note ~:892 at 403e9c5), and `docs/bootstrap-scan.txt` is dated 2026-09-07. There is one workflow file, no `schedule:`
trigger, and `gitleaks` appears in `ci.yml` only inside a comment. Owner decision (2026-09-19): correct
the claim, do **not** add a gitleaks CI job in this epic — the lint's secret-shape rules stand alone,
and a reviewer must not believe a safety net exists that does not. The `.gitleaksignore`/allowlist
prohibition stands on its own merit: a committed false positive is permanent, and a path allowlist over
`tests/` would switch the scanner off for the next operator who does run it locally.
**Source:** `.github/workflows/ci.yml`'s `secret-grep` job (read 2026-09-19 at ~:474-560; starts
~:485 at 403e9c5 — image layers only); `.gitleaksignore`; `docs/bootstrap-notes.md` (~:48, ~:892); `docs/bootstrap-scan.txt`; landscape finding 12
(https://devopsaitoolkit.com/blog/gitleaks-tuning-precision/ — `search_snippet`, lead only).

**Decision:** Sixteen attack categories and nine benign genres, closed.
**Rationale:** Seven map one-to-one to `stage2_structural.py`'s categories; two are the audit
bypasses; `natural_language` / `authority_seo` are the classifier-only shapes Meta's model card says
PG2 does not target (finding 6); `hidden_markup` carries the five in-the-wild carriers (finding 11);
`boundary_straddle` / `sustained_midband` are hardening spec 7's residuals; `density_thinned` and
`repetition_camouflage` are the two published PG2 evasions (findings 9, 10). `over_defence_probe` is
its own genre so NotInject-style benigns are never pooled into the main FPR (finding 13).
**Source:** `_BLOCKING_CATEGORIES` / `_SUSPICIOUS_CATEGORIES`, `pipeline/stage2_structural.py` ~:42-58 at 403e9c5; https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M
(model card); https://arxiv.org/abs/2605.23196 (Prompt Overflow, 2026-05-22);
https://labs.zenity.io/p/catching-prompt-guard-off-guard-exploiting-overfit-in-training-algorithms
(2026-03-12); https://huggingface.co/datasets/leolee99/NotInject.

### Scope Adjustments

- An `also_text` flag to run a page record through `/extract` as well was dropped: two surfaces are
  two records.
- A `FREEZE_MANIFEST`-style sha256 manifest over corpus files (finding 8) was considered and not
  adopted: git tracks the files and the baseline drifts on any record change, so a second manifest is
  redundant.

### Decisions Made

- Outcome vocabulary is four-way for attacks and three-way for benign (ruling 9); `flagged` counts
  toward catch rate and toward FPR.
- `fallback` exists on the replay classifier for the hermetic seed tests only; the gate never uses it.

## Clarifications

### Session 2026-09-19
- Q: Five specs, four, or Poppy's two? → A: Five, by concern; the owner-gated recording alone.
- Q: How is the real classifier measured with no weights in CI? → A: Recorded-score cassettes,
  owner-recorded per model revision; a miss fails loudly.
- Q: Third-party samples and research? → A: Research first; ingest only permissive-licence sets,
  sampled and re-rendered; owned vectors regardless.
- Q: Gate semantics? → A: Generated baseline, exact match, plus reviewed floors; no default flips in
  this epic.

## Open Questions

- [x] Whether hardening spec 1 changed the `/search` stage-3 join (`Title:/URL:/Snippet:`) —
      **closed 2026-09-24 (validation round 4): it did not.** `_search_result_promptguard_input`
      (`pipeline/orchestrator.py` ~:1285-1287 at 403e9c5) still returns exactly
      `f"Title: {title}\nURL: {url}\nSnippet: {snippet}"`, fed the canonical URL and the collapsed
      wire forms of title and snippet (US-002 `/search` driver bullet).

## Known risks (planning)

- Anchors were **re-verified at 403e9c5 on 2026-09-24** and are cited by symbol with the line as a
  hint (ruling 5); spec 0 may shift lines in `model_fetcher.py`, `promptguard/classifier.py` and
  `pipeline/extraction_limits.py`
  only. Re-grep at story start regardless.
- ~~The `contiguity` boot relies on the config keys being read from `app.state.config`~~ — closed
  favourably at 403e9c5: hardening spec 7 reads them into `app.state.promptguard_settings` at boot,
  inside the lifespan, after `_load_config` — not at import — so the override works (Assumptions).
- `corpus_app` depends on the lifespan's env reads staying the set it neutralises; a new env-driven
  boot input added later would reintroduce shell-dependence. The `VALKEY_URL` /
  non-allowlisted-`FORAGE_MODEL_ID` boot test is the tripwire for the two that matter most.
- Stage 1's treatment of CSS-hidden text is an observation, not a promise; the seed test pins what
  is measured, and a change there is exactly a corpus drift worth seeing.
