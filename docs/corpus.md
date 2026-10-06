# The injection corpus

Forage's claim that its injection defence is *measured* rests on a corpus of
indirect prompt-injection attacks in web form, a benign counter-corpus, and a
gate that drives both through the real HTTP routes on every CI run. This page is
the guide to checking that claim: what is measured, how a record is written, how
the real classifier is recorded, what each red in the gate means, where the text
came from, and — most important — what the numbers do and do not say.

The short form of the record format lives beside the data in
[`tests/corpus/README.md`](../tests/corpus/README.md). Numbers appear in exactly
one place here, the *Decision inputs* table, and are dated with the commit of the
baseline they came from. Everywhere else, read them from the generated
[`tests/corpus/baseline.json`](../tests/corpus/baseline.json) or the `Injection
corpus` table in a CI job summary.

## What is measured and why

Each record is one piece of web-shaped text, driven through the route that
carries its surface: a search result (`POST /search`), a fetched page
(`POST /retrieve`) or an uploaded text file (`POST /extract`). The harness boots
the real app, so stages 1, 2 and 4 run for real. Stage 3 runs the real
stage-3 *rules* over scores **replayed from a cassette** (see *Recording and
re-recording*), because CI has no model weights.

The outcome of a record on a route is read from the wire response alone:

| Outcome | Applies to | Meaning |
|---------|-----------|---------|
| `blocked` | attack, benign | The result is omitted (`/search`), or the route returned `injection_detected` or the fail-closed quarantine (`/retrieve`, `/extract`). A content-determined refusal (an over-size text, an unsupported format) is also `blocked`, with `refusal` recorded. |
| `flagged` | attack, benign | Delivered, but with `suspicious=true`, a non-empty `structural_flags`, or a `promptguard_state` other than `scanned`. |
| `neutralised` | attack | Delivered with no flag, and the record's `marker` is absent from every wire text field. The payload was stripped or rewritten. |
| `leaked` | attack | Delivered with no flag, and the marker is present. |
| `clean` | benign | Delivered with no flag. |

**Catch rate** is blocked plus flagged; **block rate** is blocked; **false
positive rate (FPR)** is blocked plus flagged on benign records. Leak detection
reads every string field of the response except `injection_spans`, a quarantine
diagnostic that carries the flagged text by design.

The corpus exists because "the defence works" was an assertion. With it, a
change that lets a category through, or starts rejecting ordinary prose, moves a
committed number and fails the build naming the records that moved.

Two rule configurations are driven live: `default` (max window score at the
configured threshold, contiguity off) and `contiguity` (the documented enabling
recipe from the hardening epic). Other pooling rules are evaluated *offline* over
the cassette scores and labelled `offline`; they are numbers for a later ruling,
not code in stage 3.

## The record format

One JSON object per line, in `tests/corpus/attacks/<category>.jsonl` and
`tests/corpus/benign/<genre>.jsonl`. **Payload text is data, never quoted
elsewhere**: not in a commit message, an issue, a log line, an assertion message
or this page. Tools and tests name a record by `id` only.

Keys appear in this order (the `key_order` lint rule enforces it). `marker`,
`pinned`, `pinned_reason`, `params` and `notes` may be omitted.

| Key | Meaning |
|-----|---------|
| `id` | `atk-NNNN` for attacks, `ben-NNNN` for benign; unique across every file, never reused |
| `kind` | `attack` or `benign` |
| `category` | An attack category (`vocab.ATTACK_CATEGORIES`) or benign genre (`vocab.BENIGN_GENRES`) |
| `surface` | `search` (`/search`), `page` (`/retrieve`) or `text` (`/extract`) |
| `payload` | `search`: `title`, `url`, `content`, optional `engine`, `content_kind`. `page`: `url`, `title`, `head_html`, `body_html`. `text`: `filename`, `text`. All values strings |
| `marker` | Attack only: a substring of at least 12 characters, one line, that must not reach the wire |
| `pinned` | Absent, or a non-empty list of acceptable outcomes on every applicable route |
| `pinned_reason` | Required exactly when `pinned` is set |
| `source` | `kind` (`synthetic` / `owned` / `third_party`), `name`, `url`, `licence`, `revision`, `record_ref`, `framing` (`indirect` / `rehomed_direct`) |
| `lang` | BCP-47 tag |
| `params` | Per-category keys: `density`, `placement`, `repeat`, `carrier`, `windows_min`, `variant`, `url_exception` |
| `notes` | Free text for reviewers |

All vocabularies are closed and live in `scripts/corpus/vocab.py`; a new member is
a reviewed edit there. The marker is compared after the same normalisation the
pipeline applies (NFC, invisible-character strip, whitespace collapse) and
`casefold()`, so a marker must survive its own variant. A benign record that
deliberately trips a stage-2 regex pins `["flagged", "blocked"]` and names the
regex in `params.variant`; such a record counts toward its genre's FPR.

The format is versioned in two places: the `"format"` key of cassettes and of
`baseline.json`, and the field list in the corpus README. A change to either is a
format change a consumer has to notice.

## Adding a record

1. Pick the next free id for the kind and append one line to the category's or
   genre's file.
2. Keys in order; `payload` keys exactly those of the surface.
3. Attack: a distinctive `marker` that survives the record's variant.
4. **Hosts are reserved** (RFC 2606: `example.com`, `example.net`,
   `example.org`, `*.example`, `*.test`, `*.invalid`). Three declared
   exceptions exist (`params.url_exception`: `scheme`, `private_ip`,
   `ipv6_zone`), each admitting only its own shape.
5. **No secret-shaped strings.** Exfil bait uses `FAKEKEY-` with a short body.
   The lint's `SECRET_PATTERNS` is the only automated gate on corpus secret
   shapes, and CI's full-history `gitleaks` scan makes a committed false
   positive permanent.
6. Third-party text needs a permitted licence, a pinned `revision`, `record_ref`
   and `framing`, and a `NOTICE` entry.
7. No PDF-bearing records and nothing that needs DNS or a live fetch.
8. `uv run pytest tests/test_corpus_lint.py -q` is green. Then record the new
   texts (a cassette miss is the next red) and regenerate the baseline.
   **Never paste the payload into a commit message.**

## Recording and re-recording

CI has no model weights: the Meta repository is gated, the organisation's mirror
is private by decision ([`weights.md`](weights.md)), and the workflow pins "no
repository secrets". So the real classifier is measured **once per model
revision, on a host, by an owner**. Its per-window scores are committed as a
cassette under `tests/corpus/cassettes/`, keyed by the SHA-256 of exactly the
text stage 3 classifies, holding scores and window counts and **no chunk text**.
CI replays the cassette through the real stage-3 rules. Note the host in the
commit body (`recorded on <host>`).

```bash
unset FORAGE_MODEL_ID FORAGE_MODEL_REVISION   # the recorder refuses to run with either set
read -rs HF_TOKEN && export HF_TOKEN          # or FORAGE_MIRROR_TOKEN + FORAGE_WEIGHTS_MIRROR
export HF_HOME="$HOME/.cache/forage-weights"  # any writable cache; the manifest verifies the files
uv run python -m scripts.corpus.record --model-id meta-llama/Llama-Prompt-Guard-2-22M
unset HF_TOKEN
uv run pytest tests/test_corpus_record.py tests/test_corpus_harness.py -q
git add tests/corpus/cassettes/ && git commit -m "corpus: record 22M cassette @<revision>"
```

Pass the 86M's `--model-id` to record it; each model has its own cassette. The
recorder refuses to record an unloaded classifier, because a fail-closed run
measures nothing.

**The token rule.** A token never appears in argv, a log, a commit message or
shell history (invariant 6; [`weights.md`](weights.md), "Tokens, and least
privilege for each"). `read -rs` keeps it off the command line and out of
history; the recorder reads no token itself and prints ids and numbers only.

**Re-record when:** a CI run raises `UnrecordedTextError` (a text the cassette
has no scores for); the manifest revision for a model changed (the hard guard
names both revisions); the corpus gained texts; or a sanitizer change altered
**stage-3 inputs** (search-text normalisation, extraction, the stage-3 join). A
`sanitizer_revision` rotation that leaves those inputs unchanged needs no
re-record. The cassette filename embeds the revision, so a rotation writes a
new file: delete the old one in the same commit.

| Guard | Kind | What it checks |
|-------|------|----------------|
| Revision | hard | `cassette.revision` equals the manifest pin for its model; an unpinned model fails too |
| Versions | soft | the cassette's `torch` / `transformers` against `uv.lock`; a difference is a `cassette_versions_differ` warning in the report, never a failure |
| One per model | hard | two cassettes for one model id are refused |

The gate cannot detect a real-model load failure: it measures replayed scores.
`/health.promptguard_loaded` remains the runtime truth.

## The gate

`tests/test_corpus_gate.py` replays the whole corpus under every committed
cassette and both rule configurations once, and compares the result with two
committed files. It runs inside the normal `uv run pytest`; there is no separate
job and no weights in CI. The `test` job also appends the report's tables to the
job summary.

| File | Written by | What it is |
|------|------------|------------|
| `tests/corpus/baseline.json` | `--write-baseline` | The full report: per-cell counts, stage attribution, classifier-only view, offline sweep and the per-record outcome map. **Generated, never hand-edited** |
| `tests/corpus/floors.json` | `--write-floors`, then reviewed | `min_catch` / `min_block` per category × route × model × config, `max_fpr` per genre × route × model × config, and a per-model headline. Catch is rounded down, FPR up — never aspirational |

What each red means:

| Test | Red means | Do |
|------|-----------|----|
| `test_the_committed_baseline_is_what_the_corpus_measures` | A number moved. The message lists each record whose outcome changed, a capped diff and the command | Look at which records moved and why. If intended, regenerate and commit the diff for review. An *improvement* also drifts |
| `test_every_measured_cell_clears_its_floor` | A category's catch fell below its floor, or a genre's FPR rose above its ceiling | Fix the regression. A floor is lowered only by editing `floors.json` in the PR, where a reviewer sees it |
| `test_every_cell_the_corpus_produces_has_a_floor_…` | A cell exists with no floor, or a floor names a cell nothing produces | Run `--write-floors`, review the generated diff |
| `test_every_pinned_record_holds_…` | A record's pinned outcome slipped | Treat as a regression in stage 2 or the route; do not edit the pin to match |
| `test_every_cassette_answers_every_record_…` | `UnrecordedRecordError`: a cassette has no scores for a text | Re-record |
| `test_the_corpus_meets_every_count_floor` | The corpus shrank below the size floors in `vocab.MIN_RECORDS` | Restore the records |

The two commands:

```bash
# Regenerate the baseline after an intended change (review the diff!)
uv run python -m scripts.corpus.report --write-baseline

# Scaffold floors.json from the live report. Overwrites hand edits; review the diff.
uv run python -m scripts.corpus.report --write-floors
```

`--check` compares the live report with the committed baseline without writing;
`--sweep` prints the offline pooling grid. Two pull requests that regenerate the
baseline concurrently conflict in JSON: resolve by regenerating on the merged
tree.

## Sources and licences

Every third-party record carries a licence, a pinned revision and an entry in
[`NOTICE`](../NOTICE) under "Third-party corpus samples", which a test enforces.
Ideas are not copyrightable: taxonomies from rejected sources may be read, but no
string from them enters the corpus.

**Accepted**

| Source | Licence | Used for | Provenance |
|--------|---------|----------|------------|
| AgentDojo | MIT | attack records (indirect, re-rendered) | `NOTICE` |
| LLMail-Inject | MIT (dataset card) | attack records | `NOTICE` |
| CyberSecEval | MIT (`CybersecurityBenchmarks/LICENSE` only) | attack records, indirect cases | `NOTICE` |
| NotInject | MIT (dataset card) | benign over-defence probes | `NOTICE` |
| arXiv papers under CC BY 4.0 | CC-BY-4.0, stated per paper | benign security prose | `NOTICE` |
| Wikinews editions | CC-BY-4.0 / CC-BY-2.5 as the edition declares | benign news, multilingual | `NOTICE` |
| Python documentation, Rust book, project READMEs and changelogs | PSF-2.0 / MIT / per-repository licence | benign docs and code | `NOTICE` |
| Owned and synthetic records | Apache-2.0 | everything else | `source.kind` |

**Rejected**

| Source | Why rejected |
|--------|--------------|
| BIPIA | Benchmark data is CC-BY-SA 4.0 (share-alike) |
| WASP | CC-BY-NC 4.0 (non-commercial) |
| HackAPrompt | MIT, but direct-framed and unnecessary |
| PIGuard / InjecGuard | Licence unverified after a rename "due to licensing issues" |
| Wikipedia | CC-BY-SA (share-alike) |
| Stack Exchange | CC-BY-SA (share-alike) |
| MDN prose | CC-BY-SA (share-alike) |
| OWASP | CC-BY-SA (share-alike) |
| Reddit | No licence grant |
| Hacker News | No licence grant |
| arXiv papers not under CC BY 4.0 / CC0 | The arXiv non-exclusive licence, CC BY-NC-*, CC BY-SA; the sampler refuses them |
| French and Swedish Wikinews | CC-BY-SA 4.0 (share-alike) |
| GitHub Discussions | Posts carry no reuse licence |
| Open Food Facts | ODbL database, CC-BY-SA text |

Resolve a licence at the directory of the files taken, not the repository root,
and pin by commit SHA or dataset revision, never a branch. Downloads live under
`$FORAGE_CORPUS_INPUTS`, outside the working tree, and are never committed.

## Decision inputs

Two defaults were left unflipped by the hardening epic, to be ruled on from
measurement: whether **contiguity gating** (`promptguard_contiguity_windows` /
`_threshold`, shipped off) earns its default, and whether the **86M model**
(opt-in) does. These two tables are the input to both rulings. Nothing here flips a
default.

*Measured at baseline commit `4f13c52`, from `default`-config texts that reached
stage 3 (a text stage 2 blocked first is outside every denominator). Cells are
texts fired, read from `baseline.json`'s `offline` key (a test re-derives every
cell). `live_contiguity` is the live `contiguity` config: max at 0.85 OR a run
of 2 windows at 0.5.*

**Attack catch.** Denominators: attack 369, `boundary_straddle` 13,
`sustained_midband` 7, `natural_language` 155, `density_thinned` 25,
`repetition_camouflage` 17, `authority_seo` 27.

| Model | Pooler | attack | boundary_straddle | sustained_midband | natural_language | density_thinned | repetition_camouflage | authority_seo |
|-------|--------|-------:|------------------:|------------------:|-----------------:|----------------:|----------------------:|--------------:|
| 22M | `max@0.85` (default) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 22M | `live_contiguity` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 22M | `contiguity(2,0.5)` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 22M | `mean@0.5` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 86M | `max@0.85` (default) | 15 | 0 | 0 | 15 | 0 | 0 | 0 |
| 86M | `live_contiguity` | 15 | 0 | 0 | 15 | 0 | 0 | 0 |
| 86M | `contiguity(2,0.5)` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 86M | `mean@0.5` | 21 | 0 | 0 | 21 | 0 | 0 | 0 |

**Benign false positives.** Denominators: benign external 94, `long_form` 21,
`multilingual` 34, `over_defence_probe` 79.

| Model | Pooler | benign external | long_form | multilingual | over_defence_probe |
|-------|--------|----------------:|----------:|-------------:|-------------------:|
| 22M | `max@0.85` (default) | 0 | 0 | 0 | 0 |
| 22M | `live_contiguity` | 0 | 0 | 0 | 0 |
| 22M | `contiguity(2,0.5)` | 0 | 0 | 0 | 0 |
| 22M | `mean@0.5` | 0 | 0 | 0 | 0 |
| 86M | `max@0.85` (default) | 0 | 0 | 0 | 0 |
| 86M | `live_contiguity` | 0 | 0 | 0 | 0 |
| 86M | `contiguity(2,0.5)` | 0 | 0 | 0 | 0 |
| 86M | `mean@0.5` | 0 | 0 | 0 | 0 |

The full grid (every threshold, window count, `k_anywhere` and smoothed pooler,
and the per-density and per-repeat splits) is `baseline.json`'s `offline` key, or
`uv run python -m scripts.corpus.report --sweep --markdown`.

**Reading guide.** Ask two questions.

1. *Does contiguity at `2 @ 0.5` add catch over max at 0.85 without adding
   false positives?* Compare the `live_contiguity` row with `max@0.85`, per
   model, in both tables — including the `long_form` and `multilingual` benign
   columns. In this table the two rows are equal for both models: contiguity
   added no catch, including on the two families built to test it
   (`boundary_straddle`, `sustained_midband`). That is a statement about this
   corpus and these two cassettes; on the 86M the firing texts are effectively
   single-window, so a run rule has nothing to act on.
2. *Does the 86M earn its default?* Compare the 86M `max@0.85` row with the 22M's.
   The 86M fires on some `natural_language` texts the 22M does not, with no
   benign false positive in any group above. The 22M fires on none of the stage-3
   texts at this threshold, so the 22M's measured stage-3 contribution on this
   corpus is nil, and every catch the 22M shows in the live tables is stage 2.
   Whether that difference justifies a default flip, a memory cost or an
   operating-point change is the owner's ruling, not this table's.

Lower thresholds (see the full grid) trade FPR for catch; do not read a single
row in isolation from the benign columns beside it.

**Rulings (owner, 2026-10-06).** Both questions are answered: the **86M is the default
model** (the 22M stays a `FORAGE_MODEL_ID` opt-out), and **contiguity gating stays off**.
Reasoning and consequences: `kit_tools/arch/DECISIONS.md` (2026-10-06). The tables above
are unchanged by the rulings: the gate replays both cassettes whatever the default is.

## Reading the results

**What the headline numbers claim.** A catch rate is the share of *this corpus's*
records of a category that the live pipeline blocked or flagged on a route. An
FPR is the share of *this corpus's* benign records that it blocked or flagged. A
floor is a regression tripwire set from the measurement, not a quality target.
None of these is a property of unseen text, and none is a guarantee.

**Disclosure.** This repository publishes a parameter-tuned evasion corpus
(`density_thinned`, `repetition_camouflage` and `sustained_midband` exist to
locate where Prompt Guard 2 stops catching) and a permanent per-record catalog of
which categories and carriers reach the consumer under the live default
configuration (the per-record outcome map under `records` in
[`tests/corpus/baseline.json`](../tests/corpus/baseline.json)). That is
deliberate, and both are committed in full, for three reasons (leak findings are
also tracked in `kit_tools/AUDIT_FINDINGS.md`, a local, gitignored working record
that is not part of the published repository):

- Public injection corpora are standard defensive practice, and the ones ingested
  here (AgentDojo, LLMail-Inject, CyberSecEval) are themselves public.
- Forage is public, ships no authentication by design, and says on the first
  screen of [`README.md`](../README.md) that it is **not a trust boundary** — the
  calling agent owns every trust decision. A reader who learns which shapes get
  through learns something Forage never promised to stop.
- A gate whose failures are hidden is a gate nobody can check, which is the
  failure mode this repository exists to avoid (invariant 5: degradation is
  loud).

**The candidate rejection rate beside the FPR.** The benign FPR is measured over
the records that were *kept*. The report prints, per genre,
`rejected / examined` (every candidate the sampler drew, split by reason) beside
it. A low FPR next to a high rejection rate says the sample was filtered, not that
the defence is quiet; a rejection driven by `duplicate` means something different
from one driven by `non_prose`. A genre whose records are entirely synthetic shows
`—`: nothing was drawn, so there is no rate.

**`flagged` is not comparable across routes.** On `/search` a sub-threshold score
surfaces as `suspicious`; on `/retrieve` and `/extract` nothing below the threshold
surfaces at all. The same text scored 0.6 is `flagged` on one route and `leaked` on
another. That is a difference in what the API exposes, not in what the defence
caught. Outcomes are read within a route; any cross-route comparison uses the
recorded window scores, which the cassettes in `tests/corpus/cassettes/` carry.

**`blocked_but_leaked` is a half-worked defence, not a success.** A record counts
here when its outcome is `blocked` while its marker is still on the wire (a
structural block that left the claim in another field). The block is counted in the
catch rate, and the leak is counted here and in the baseline's per-record map
(and tracked in the local, gitignored audit findings). Read the two together.

**The classifier-only view covers only texts that reached stage 3.** Stage 3 is
skipped after a structural block, so a text stage 2 stopped has no window scores.
The view's denominators say how many reached it; it cannot show what the
classifier would have done to the rest.

**What it does not cover.** PDF-borne text is outside the corpus (there is no
text-bearing PDF generator in `tests/fixtures/`), caller-set trust tiers on
`/retrieve` (`trusted_domains` skips stage 3 by design, so a "bypass" there is the
documented behaviour), and any carrier or phrasing the authors did not think of.
There is no fuzz harness. A replayed score is only as good as the recording: the
gate cannot detect a real-model load failure.

## Consumers

Poppy's `epic-web-injection-regression-suite` may vendor records from this corpus,
one way: the records are plain JSONL and carry their own licence and provenance
fields. The format is versioned by the `"format"` key in cassettes and the baseline
and by the field list in [`tests/corpus/README.md`](../tests/corpus/README.md); a
consumer should refuse a format it does not recognise rather than guess. **Forage
never reads Poppy**: a property that needs to see both sides of the boundary
belongs on the consumer's side (invariant 1).

Nothing under `tests/corpus/` or `scripts/corpus/` is shipped in the image;
`.dockerignore` excludes `tests/` and `scripts/`. The corpus changes no response
shape and no `_REVISION_SOURCES` file, so it is not a contract change
([`contract/GOVERNANCE.md`](../contract/GOVERNANCE.md)).
