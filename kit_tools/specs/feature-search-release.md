<!-- Template Version: 2.5.0 -->
---
feature: search-release
status: active
session_ready: true
depends_on: [search-policy-and-health]
vision_ref: "T2.1 — Search-provider abstraction & reliable search"
type: epic-child
size: L
epic: search-providers
epic_seq: 5
epic_final: true
execution_order: [US-001, US-004, US-002, US-003]
created: 2026-09-14
updated: 2026-09-14
---

# Feature Spec: Third-Party Docs + v1.1.0 Release Cut

> **Epic 5 (final) of `epic-search-providers`.** Document the third-party `FORAGE_SEARCH_PROVIDERS`
> / `FORAGE_BRAVE_API_KEY` env contract in README prose, land the release plumbing (epic ruling 20),
> then **cut and publish the `v1.1.0` image** carrying contract 1.2.0 — the artifact Poppy's
> `epic-search-policy` epic pins — and write the handoff record. **US-002 is the owner gate** (the
> tag push, like Epic-2's `v1.0.0`). Revised 2026-09-14 after the `/kit-tools:validate-epic` pass:
> US-004 is new, US-001 is re-scoped to README prose, and every verification step now names the
> command and the test that proves it. Revised again after round 2 (rulings 31–32): the
> announcement mechanism is pinned, the healthy smoke waits, and every fan-out is named line by
> line. Context: Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (row F10) + the cross-repo gate.

## Overview

Specs 1–4 land the engine and freeze the wire at contract 1.2.0; nothing publishes until this spec
cuts `v1.1.0`. Four stories, run in the order `US-001 → US-004 → US-002 → US-003`:

- **US-001 (docs, autonomous)** — README prose for the env contract, credential handling for the new
  key, the per-provider ToS and data-flow posture, the spend consequence in the exposure warning,
  and the stale "`/health` degrades without SearXNG" claim struck everywhere it is recorded.
- **US-004 (release plumbing, autonomous)** — ruling 20 a–d, hardened per ruling 31: the `publish`
  job announces the contract's per-version docstring entry mechanically (POSIX awk inside the
  workflow — never a script from the tagged tree — through a file, a notes file and a `grep -F`),
  `contract_smoke.py` can verify a weights-loaded release image from the tag's checkout by
  waiting for `status: healthy`, the compose fragments move to `1.1.0` with the two new variables
  passed through, and both CI secret-grep pattern lists learn `FORAGE_BRAVE_API_KEY` — with every
  document that restates any of those facts named. All of it must be *in the tagged tree*, so it
  precedes the cut.
- **US-002 (the cut, owner gate)** — pre-flight per the archived `v1.0.0` runbook, now also proving
  by grep that US-001, every part of US-004 and the sibling-owned rows are in the tree and
  rehearsing the announcement extractor on the commit to be tagged; then the tag push, the publish
  steps watched live, and the artifact verified in both smoke modes from the `v1.1.0` checkout.
  `v1.1.0` is the first tag to move `latest` off `1.0.0` and the first to mint `1.1`.
- **US-003 (third-party view + handoff, owner-executed)** — a credential-free pull, `/health` and
  `/search` against the published image on the key-less floor, one run with a placeholder key at
  zero spend to prove the key plumbing is packaged, and the record Poppy pins (tag, index digest,
  contract, anchor sha256, spend posture) in this file's Implementation Notes and
  `docs/releases.md`; the suite-count bookkeeping closes here.

The image tag moves `1.0.0 → 1.1.0` (MINOR feature) while the contract it advertises is `1.2.0` (the
two-semver rule). Publishing runs through Forage's existing gated lane (`publish` needs `lint`,
`typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke`; `.github/workflows/ci.yml` ~687–710);
the `v*` tag is the owner gate.

What this spec does **not** own, because the epic's resolution map put it elsewhere: the
`docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md` rows for `FORAGE_SEARCH_PROVIDERS`
(spec 1 US-003) and `FORAGE_BRAVE_API_KEY` (spec 2 US-002); the contract-version mentions in
`README.md:56`, `CLAUDE.md:82`, `contract/GOVERNANCE.md:29`, `kit_tools/docs/API_GUIDE.md:43`,
`kit_tools/arch/CODE_ARCH.md:108`, `kit_tools/arch/SERVICE_MAP.md:75,193` and the embedded anchor
hashes in API_GUIDE, CI_CD, DEPLOYMENT and `kit_tools/arch/SERVICE_MAP.md:202` (spec 1 US-004,
re-swept by spec 4 US-003); the `/health` field documentation in MONITORING and API_GUIDE (spec 4
US-002). US-002's pre-flight *confirms* the four variable rows, the version sweep and the anchor
sweep by grep — its criteria name each command — rather than redoing them; the `/health` field
docs are proven by spec 4 US-002's own criteria and are not re-checked here.

## Goals

- `README.md` prose documents the third-party env contract — `FORAGE_SEARCH_PROVIDERS` (ordered
  chain), `FORAGE_BRAVE_API_KEY`, the key-less floor, per-provider ToS, the outbound data flow — and
  `docs/configuration.md` gains a credential-handling subsection for the key modelled on the
  `VALKEY_URL` one. The stale SearXNG-degrade sentence is gone from `README.md`,
  `docs/configuration.md`, `kit_tools/docs/MONITORING.md`, `kit_tools/arch/SERVICE_MAP.md` and
  `kit_tools/AGENT_README.md`. The exposure warning names the spend consequence honestly.
- The release plumbing of ruling 20, hardened per ruling 31, is in the tagged tree: mechanical
  announcement that executes nothing from the tagged tree, a smoke that waits for the status it
  expects, compose pins at `1.1.0` with the passthrough, three-pattern secret greps — each pinned
  by a test, and every document that restated the old facts updated.
- A published multi-arch `v1.1.0` image advertising contract 1.2.0 through the gated lane, with
  `latest`, `1.1` and `1.1.0` resolving to one digest, verified from the `v1.1.0` checkout in both
  `degraded` and `healthy` smoke modes.
- The handoff record — tag, OCI index digest, contract version, anchor sha256, tagged commit, run
  URL, and the spend posture the consumer must design around — in this file's Implementation
  Notes and a new `docs/releases.md` "Released versions" section.

## User Stories

### US-001: Third-party env-contract docs (README prose + the stale claim)

**Priority:** P2

**Description:** As a third-party operator, I want the search-provider configuration, the handling
of the paid key, and the consequences of enabling it documented in the README and the configuration
reference, so I can enable a paid backend (or stay on the free floor) without reading the source and
without being surprised by what it costs or where my queries go.

**Independent Test:** `grep -c 'FORAGE_SEARCH_PROVIDERS=searxng,brave' README.md` and
`grep -c 'FORAGE_SEARCH_PROVIDERS=searxng,brave' docs/configuration.md` both report at least 1 —
the enablement recipe, stated because the key alone adds no provider (the default chain is
`searxng`, ruling 9); `grep -c FORAGE_SEARCH_PROVIDERS README.md` and `grep -c FORAGE_BRAVE_API_KEY
README.md` both report at least 1; `grep -n '^### Credential handling for `FORAGE_BRAVE_API_KEY`'
docs/configuration.md` hits once; `grep -n 'starts and reports itself' README.md
docs/configuration.md` returns nothing; `grep -n 'search.paid_calls' README.md
docs/configuration.md` hits both; `uv run pytest` stays green.

**Implementation Hints:**
- **Scope is README prose plus the subsections named here.** The env-var *rows* in
  `docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md` are spec 1 US-003
  (`FORAGE_SEARCH_PROVIDERS`) and spec 2 US-002 (`FORAGE_BRAVE_API_KEY`) — do not add a second row.
  The README "## Configuration" section (`README.md` ~130–149) gets one bullet per variable in the
  existing bullet style: the chain (comma-separated, default `searxng`, read once at start; no
  `config.yaml` key — ruling 9) and the key (per-provider name, no generic alias — ruling 10).
- **The key-less floor, stated plainly:** no key configured means SearXNG-only, fully supported, no
  error, no new required secret. Use the phrase "SearXNG-only" so the statement is grep-anchored.
- **Per-provider ToS, framed as a constraint on consumers, not a description of a cache.** Forage
  caches no search result from any provider (`README.md:116` and `docs/configuration.md` already say
  `/search` has never been cached; ruling 11 and 22 reaffirm it). The ToS note records what a
  *downstream consumer* may persist: SearXNG results are unrestricted; Brave forbids persisting or
  redistributing result payloads, so Forage's telemetry stores metadata only (decision 6). Note
  Brave has no free tier ($5/1,000) and is reached through its LLM-Context (chunks) endpoint. Never
  write "SearXNG results are cacheable" — it reads as "Forage caches them".
- **Credential handling.** Add `### Credential handling for `FORAGE_BRAVE_API_KEY`` to
  `docs/configuration.md` directly after `### Credential handling for `VALKEY_URL`` (~156), copying
  its shape: runtime container environment only — the Dockerfile takes no build arguments at all
  (`CLAUDE.md` invariant 2), so a `--build-arg` attempt is the exact leak shape the repo went
  private over; `compose/.env` (git-ignored) or a secret store, never an inline `-e` flag; Forage
  never logs the value and `/health` exposes presence only (`capabilities.brave_api_key`, ruling
  15); rotation means a restart (the key is read once in the lifespan); a leaked key is a metered
  billing liability with no cap in Forage. Use an obviously synthetic placeholder in every example
  and add no `.gitleaksignore` entry (that file is for triaged false positives). Keep the four
  generic mechanics (env file vs inline `-e`, `docker inspect` exposure, rotation is a restart,
  never logged) to one sentence each with a cross-reference to the `VALKEY_URL` subsection as
  the fuller treatment — the repo's pattern for `HF_TOKEN` and `FORAGE_MIRROR_TOKEN` is a row
  pointer plus a provider-specific section — and spend the subsection on what is
  Brave-specific. The README bullet links here.
- **Data flow.** In the same subsection (and one README sentence): what is sent to Brave is the
  verbatim query text under the operator's account, to the Brave endpoint host spec 2 pins as its
  constant endpoint (name the host so egress-allowlist operators can act on it); what comes back is
  chunks that enter the unchanged sanitization pipeline; nothing paid is retained. The key-less
  floor adds no outbound destination beyond the self-hosted SearXNG and its configured engines.
- **The bad-key path.** State that `capabilities.brave_api_key` reports *presence, not validity*: a
  rejected, expired or unentitled key surfaces per request as a provider failure (`auth`, `quota` —
  ruling 13's closed classes — in `provider_errors`), never on `/health`. Cross-reference
  `kit_tools/docs/TROUBLESHOOTING.md` rather than duplicating its rows.
- **Strike the stale claim** that `/health` reports `degraded` when SearXNG is unreachable. The code
  has never probed SearXNG (`DegradedReason` in `pipeline/contract.py` has two members). Sites:
  `README.md:97–98` ("**SearXNG** backs `/search`: without it Forage starts and reports itself
  `degraded`") and `docs/configuration.md:93–96` ("If SearXNG is not reachable, Forage still starts
  and reports itself `degraded` rather than refusing to boot"). Replace with the truth: an
  unreachable SearXNG surfaces per request as a `/search` 422 (`searxng_unavailable`), never as a
  `degraded_reasons` value, and provider *status* is `search_providers` on `/health` (spec 4). Do
  not reuse the phrase "starts and reports itself" in the replacement — it is the grep anchor.
- **Close out the documents that track the discrepancy as open**, keeping their factual statements:
  `kit_tools/docs/MONITORING.md:101` (drop "that sentence is flagged for correction"),
  `kit_tools/arch/SERVICE_MAP.md:112–116` (replace the "Documented discrepancy (awaiting an owner
  decision)" block with one sentence recording that the docs were corrected here),
  `kit_tools/AGENT_README.md:162–165` (remove the open-ruling clause from the contract bullet), and
  tick item 1 of the 2026-09-13 "Open / Next" list in `kit_tools/SESSION_LOG.md`.
  `kit_tools/docs/ENV_REFERENCE.md` is already correct on this point and needs no edit here.
- **The exposure warning gains the spend consequence.** Add one bullet to the README posture
  blockquote (~12–22) and one to the "Consequences you must design around" list in
  `docs/configuration.md` § "Deployment posture": with a paid key set, anyone who can reach port
  8020 can spend the operator's money; Forage enforces no budget cap (ruling 12 — say so, do not
  promise one); network placement and a front-side proxy or rate limit are the operator's controls;
  `/health` discloses key presence to anyone who can reach it; `/metrics` `search.paid_calls` and
  `search.fallback_fired` (spec 3 US-003) are how spend is seen. **Do not edit the two posture
  tables** — `tests/test_contract_metrics.py:361–400` parses that section row by row.
- **Two more README sentences this epic falsifies:** `README.md:52` (`POST /search` row says "Search
  via SearXNG" — make it name the configured provider chain, SearXNG by default) and `README.md:238`
  ("The first non-pre-release tag, `v1.0.0`, is the next step" — `v1.0.0` shipped 2026-09-12; say so
  and point at `docs/releases.md` for the current release, so US-003 does not have to reopen
  README).
- **Preserve the literals tests pin:** `compose/minimal.yml`, `compose/full.yml`, `` `"memory"` ``
  and `` `"valkey"` `` in README (`tests/test_compose_fragments.py:708–725`), and every row of the
  posture tables in `docs/configuration.md`.
- Doc-only story: no code. `uv run pytest` is the regression gate, not the proof — the proof is the
  greps in the criteria.

**Acceptance Criteria:**
- [ ] `README.md` and `docs/configuration.md` each state the two-variable enablement recipe
      verbatim — `FORAGE_SEARCH_PROVIDERS=searxng,brave` **and** `FORAGE_BRAVE_API_KEY` — and say
      that the key alone changes nothing (grep-verifiable, see the Independent Test).
- [ ] `README.md` "## Configuration" carries one bullet for `FORAGE_SEARCH_PROVIDERS` and one for
      `FORAGE_BRAVE_API_KEY`; `grep -n 'SearXNG-only' README.md` hits, and the sentence states that
      a deployment with no key is fully supported.
- [ ] `grep -n '^### Credential handling for `FORAGE_BRAVE_API_KEY`' docs/configuration.md` hits
      once; the subsection states, each in its own sentence: runtime env only; never a build
      argument; `compose/.env` rather than an inline `-e` flag; never logged and `/health` shows
      presence only; rotation is a restart; a leaked key is metered spend with no cap in Forage.
      Every example uses a synthetic placeholder; `git diff --stat` for the story touches no
      `.gitleaksignore`.
- [ ] The README `FORAGE_BRAVE_API_KEY` bullet links to that subsection (`grep -n 'Credential
      handling for `FORAGE_BRAVE_API_KEY`' README.md` hits).
- [ ] Per-provider ToS: `grep -n -i 'persist' README.md docs/configuration.md` hits both, in a
      sentence stating Brave forbids persisting or redistributing result payloads and Forage stores
      metadata only; `grep -n 'has never been cached' README.md` still hits.
- [ ] Data flow: `grep -n 'brave.com' README.md docs/configuration.md` hits both, in a sentence
      naming the outbound host and stating the query text is what is sent.
- [ ] Bad key: `grep -n 'presence, not validity' docs/configuration.md` hits; that paragraph names
      `auth` and `quota` and links `kit_tools/docs/TROUBLESHOOTING.md`.
- [ ] Stale claim: `grep -n 'starts and reports itself' README.md docs/configuration.md` returns
      nothing; `grep -n 'searxng_unavailable' README.md docs/configuration.md` hits both.
- [ ] Close-out: `grep -n 'flagged for correction' kit_tools/docs/MONITORING.md`, `grep -n 'awaiting
      an owner decision' kit_tools/arch/SERVICE_MAP.md` and `grep -n 'open ruling'
      kit_tools/AGENT_README.md` each return nothing; `grep -n 'never probes SearXNG'
      kit_tools/docs/MONITORING.md kit_tools/arch/SERVICE_MAP.md` still hits both.
- [ ] Exposure: `grep -n 'search.paid_calls' README.md docs/configuration.md` hits both, inside the
      posture blockquote and the "Consequences" list respectively; each sentence says Forage
      enforces no budget cap and that `/health` discloses key presence. The posture tables in
      `docs/configuration.md` are byte-identical before and after.
- [ ] `grep -n 'Search via SearXNG' README.md` and `grep -n 'is the next step' README.md` return
      nothing; the `POST /search` row names the provider chain; the release sentence points at
      `docs/releases.md`.
- [ ] `uv run pytest` passes — specifically
      `tests/test_compose_fragments.py::TestTheFragmentsAreDocumented` (README literals) and
      `tests/test_contract_metrics.py::test_every_served_path_is_acknowledged_in_the_posture_doc`
      plus `::test_posture_doc_states_the_docs_endpoints_are_unauthenticated` (posture section), the
      two modules that bind the files this story edits.

### US-004: Release plumbing (ruling 20 a–d, hardened per ruling 31)

**Priority:** P2

**Description:** As the owner cutting `v1.1.0`, I want the ruling-(b) announcement, the smoke flags,
the compose pins and the secret-grep patterns landed in the tree before the tag, so the cut is
mechanical, the published image can be verified as an operator runs it, and the example a third
party copies actually pulls the release this epic ships — without the `publish` job ever running
code from the tagged tree or trusting free text it carried through an output.

**Independent Test:** `tests/test_ci_workflow.py::TestReleaseContractMapping` (extended, including
the `subprocess`-driven extractor tests), `tests/test_contract_smoke.py` (extended:
`evaluate_health` under both statuses and the status-aware `wait_for_health`),
`tests/test_compose_fragments.py` (extended) and `tests/test_ci_workflow.py::TestSecretGrepJob` +
`::TestPublishJob::test_publish_greps_the_published_config_for_secrets` all pass, and each part's
tests fail if that part alone is reverted.

**Implementation Hints:**
- **(a) Mechanical announcement — the mechanism is pinned (ruling 31a), not left to choice.** The
  per-version entries live in the docstring under `pipeline/contract.py:CONTRACT_VERSION`: one
  bullet `* ``X.Y.Z`` — …` at column 0 with continuation lines indented two spaces (the `1.1.0`
  entry at ~29–32 is the shape); the `1.2.0` entry is written by spec 1 US-004, completed by spec
  4 US-003, and names `search_unavailable`. Today the `publish` job's step "Read the contract
  version from the tagged tree" (`ci.yml` ~1000–1033) greps the version out of
  `pipeline/contract.py` in bash — no `uv`, no Python — and writes one single-line `version=` to
  `$GITHUB_OUTPUT` (~1032); "Create the GitHub Release" (~1035–1107) builds the body from an
  **unquoted** heredoc (`notes="$(cat <<EOF`, its own backticks escaped because the text is
  subject to command substitution) and passes it as `--notes "${notes}"`; "Assert the Release body
  advertises the tagged tree's contract version" (~1109–1154) reads the body back and greps
  `^contract: X\.Y\.Z$` after `tr -d '\r'`.
- **(a) Extraction: POSIX awk inside the read step's `run:`, nothing else.** `awk -v v="${version}"`
  builds the prefix `* ``v`` ` in `BEGIN`, matches the bullet with `index($0, prefix) == 1` (a
  string comparison, not a regex — the dots stay literal, and the closing backticks plus the
  space in the prefix keep `1.2.0` from matching `1.2.01`), prints that line and every immediately
  following line whose first two characters are spaces, and `exit`s at the first line that is
  not (the next bullet, a blank line, the closing `"""`). Only `BEGIN`, `index`, `substr`, `print`
  and `exit`: the runner's `mawk`, macOS `awk` and `gawk` all run it unchanged, which is what
  lets the unit test and US-002's pre-flight run the very program the job runs. Output goes to a
  file, `${RUNNER_TEMP}/contract-entry.md`, referenced by that literal in every step that touches
  it (the static test asserts the literal; no step-level `env:` alias, so the workflow text and
  the test agree);
  `[ -s "${CONTRACT_ENTRY_FILE}" ]` fails the step with `::error::` + `exit 1` when the version
  has no entry — a release with an unannounced contract is the failure this exists to catch.
  `$GITHUB_OUTPUT` keeps its one `version=` line and never carries the entry: a multi-line output
  needs a delimiter form, and a delimiter line inside tagged-tree prose would terminate the value
  early and append a forged `version=` that the two later steps interpolate through
  `steps.contract.outputs.version` *after* the semver check already ran.
- **(a) Why not a script: `publish` never executes code from the tagged tree.** The job reads
  checked-out files (`pipeline/contract.py`, the contract and its anchor) but runs none of them,
  and `secret-grep` goes further and refuses a checkout at all (`ci.yml` ~486–488: the pattern
  set is defined in the workflow "so the gate cannot be weakened by editing a script the job
  fetches"; `test_secret_grep_does_not_check_out_the_repository`). `publish` is the one job
  holding `contents: write`, `packages: write` and a `GH_TOKEN`, and it pushes the public image; a
  `scripts/` helper run with the runner's `python3` would let a merged docstring PR plus a tag run
  arbitrary code there. So: no `python3`, no `uv`, no `scripts/` anywhere in the read step, and a
  static test says so.
- **(a) Assembly: a notes file, the entry copied byte-for-byte, never interpolated.** Introduce
  `notes_file="${RUNNER_TEMP}/release-notes.md"`. The existing heredoc text stays byte-identical —
  its `contract: ${CONTRACT_VERSION}` line is what `test_the_release_body_emits_the_contract_line`
  and `test_the_emitted_line_and_the_asserted_pattern_use_one_token` read — but it is redirected
  into the file instead of captured into `notes`; then `printf '\nWhat changed in contract %s:\n\n'
  "${CONTRACT_VERSION}" >> "${notes_file}"` and `cat "${CONTRACT_ENTRY_FILE}" >> "${notes_file}"`.
  The entry is never placed inside the heredoc (unquoted: a backtick or `$(` in the entry would be
  evaluated in the release job), never passed through `eval`, never used as a `printf` format
  string. `gh release create … --notes-file "${notes_file}"` replaces `--notes "${notes}"`; the
  two asset arguments are unchanged.
- **(a) Read-back with a fixed-string grep.** Keep the existing `grep -qE "^contract: ${escaped}$"`
  first and unchanged (the one-token test reads the *first* `grep -qE`), then loop over the entry
  file's lines and assert each is present in the body with `grep -qF -- "${line}"`: `-F` because
  the first line starts with `*`, an invalid ERE, and the text carries `(`, `)` and `|`. The
  failure prints the body exactly as the existing branch does.
- **(a) Tests in `tests/test_ci_workflow.py`.** The module is 271 static YAML/string assertions
  and imports no `subprocess`; this story adds its first executing tests on purpose, because a
  workflow-embedded program can be proven no other way. A helper reads the read step's `run:` and
  returns the single-quoted awk program text (the text between `awk -v v="${version}" '` and the
  closing `'`); tests run it with `subprocess.run(["awk", "-v", f"v={version}", program, path],
  capture_output=True, text=True, check=True)` — no network, so the `pytest-socket` guard is
  untouched. Cases: `test_the_current_contract_version_has_a_docstring_entry` runs it on the real
  `pipeline/contract.py` for `CONTRACT_VERSION` and asserts the result is byte-identical to the entry as it appears in
  `pipeline/contract.py` (sliced in Python by the same prefix rule, so a mid-entry blank line —
  which ends the awk read — fails the test instead of truncating the announcement) and that its
  first line begins `* ``<version>`` `, with a failure message written for the contract author (the entry
  is missing; `contract/GOVERNANCE.md` step 7 says where it goes);
  `test_the_extractor_is_exact_against_a_hostile_module` writes a synthetic module to `tmp_path`
  whose target entry contains a single-backtick span, `$(id)`, a mid-line `*`, an indented line
  beginning `* ``9.9.8`` ` (a bullet look-alike that must be kept), a line reading `EOF`, a line
  reading `version=forged` and a mid-line `"""` (a fake terminator), followed by a sibling bullet
  and the real closing `"""`, and asserts the output equals exactly the expected lines;
  `test_the_extractor_matches_the_whole_version` asks for `9.9.1` where `9.9.1` and `9.9.10`
  both exist and gets only `9.9.1`'s entry;
  `test_the_extractor_returns_nothing_for_an_unannounced_version` asserts empty output. Static
  tests: `test_the_read_step_extracts_the_entry_with_posix_awk` (the read step's `run:` contains
  `awk`, no `python3`, `uv` or `scripts/`, writes under `${RUNNER_TEMP}`, has the `-s` guard with
  `::error::` and `exit 1`, and its only `$GITHUB_OUTPUT` write is `version=`);
  `test_the_release_step_appends_the_entry_from_a_notes_file` (the create step uses
  `--notes-file`, `cat`s `CONTRACT_ENTRY_FILE`, and contains no `--notes "${notes}"` and no
  `eval`); `test_the_read_back_checks_the_entry_with_a_fixed_string_grep` (a `grep -qF` that
  references the entry file follows the `-E` grep). Every pre-existing test in the class passes
  unmodified.
- **(a) Documentation.** `docs/releases.md` § "The body says which contract the image serves": the
  `contract: 1.1.0` example at :152 becomes version-agnostic (`contract: <version>`, with a
  sentence that the value is the tagged tree's `CONTRACT_VERSION`) so the section cannot go stale
  on a bump, and a paragraph describes the "per-version entry", the notes file and the rule that
  `publish` executes nothing from the tagged tree; `kit_tools/docs/CI_CD.md` publish step 8 uses
  the phrase "per-version entry". `contract/GOVERNANCE.md` step 7 gets a prose amendment inside
  the existing step — the announcement sentence *is* the `CONTRACT_VERSION` docstring entry (one
  bullet at column 0, continuation lines indented two spaces, no blank line inside); `publish`
  extracts it, appends it to the Release body and fails the tag without it, and
  `test_the_current_contract_version_has_a_docstring_entry` fails the PR first — no new ruling
  section, no table row, so `tests/test_governance_docs.py::test_there_are_exactly_six` and
  `_RULING_MARKERS` are untouched. One sentence appended to the "Which class is it?" bullet of
  `.github/pull_request_template.md` (no new checkbox; `test_it_asks_the_four_bump_questions`
  stays green unmodified).
- **(b) `contract_smoke.py` flags, and a wait that knows what it is waiting for (ruling 31b).**
  `EXPECTED_STATUS = "degraded"` (~97) and `evaluate_health` (~284–300) hard-wire the weights-free
  contract; `wait_for_health` (~186–218) returns on the *first* 200; `run_smoke` compares the
  in-image anchor against `ANCHOR_PATH.read_text()` (~524), the local checkout's anchor. Add
  `--expect-status` with choices `healthy` and `degraded`, default `degraded`, so CI's smoke
  invocation (`ci.yml` ~645–666) and `tests/test_ci_workflow.py::TestSmokeJob` are untouched.
  Under `healthy` the three PromptGuard-coupled checks invert: `status` is `healthy`,
  `promptguard_unavailable` is absent from `degraded_reasons`, `search_sanitization` is present in
  `capabilities`; every other check (contract version, sanitizer revision, `/metrics`, in-image
  contract and anchor) is identical. Thread the expected status into `wait_for_health`: poll
  until `/health` answers 200 *and* its parsed body's `status` equals the expected one, or the
  deadline passes, returning the last response either way (the one-failure-path shape the module
  already has; a body that does not parse counts as "not yet"). This matters because `/health`
  answers 200 the moment uvicorn binds while PromptGuard loads in the background and
  `promptguard_loaded` flips in place (`retrieval_app.py` ~1114): "first 200" under `healthy`
  goes red on all three checks against a container thirty seconds from healthy. Under the
  default against a weights-free image the first 200 already reports `degraded`, so CI's path is
  byte-for-byte what it was. `DEFAULT_TIMEOUT_SECONDS` (120) was sized for a token-less start;
  the docstring and `--help` say to raise `--timeout-seconds` for a cold weights fetch.
- **(b) `--anchor`, with the rule that makes it mean something.** Add `--anchor <path>`, default
  the committed `contract/openapi.yaml.sha256`, so a release image can be verified from any
  checkout by passing the file `git show v1.1.0:contract/openapi.yaml.sha256` prints. The flag's
  help text and the module docstring state where the value must come from: the committed anchor
  at the tag (a `git show` or a clean checkout of it), never from the Release assets and never
  from the image — both are mutable copies, and a tampered document-plus-anchor pair verifies
  against itself (`ci.yml` ~1064–1070; `contract/GOVERNANCE.md` "Consumers"). Spell the phrase
  "never from the Release assets"; it is the grep anchor. Keep
  `TestSingleSourceOfTruth::test_no_wire_value_is_restated_as_a_code_literal` green — import the
  capability and reason names as today; the two status choices are the assertion, spelled out
  like `EXPECTED_STATUS`.
- **(b) Tests and fan-out (ruling 31d).** `tests/test_contract_smoke.py` gains: a healthy body
  passes under `healthy`; a degraded body fails under `healthy`; a healthy body fails under the
  default; `wait_for_health` under `healthy` with an injected fetch that answers `degraded` twice
  and then `healthy` returns the healthy body; the same fetch never turning `healthy` returns the
  last body once the injected clock passes the deadline, and `evaluate_health` then fails on
  `status`; the in-image anchor is compared against the `--anchor` file. Update the module
  docstring, the operator invocations in `kit_tools/docs/DEPLOYMENT.md` and
  `kit_tools/docs/CI_CD.md`, and every committed sentence that asserts the limitation this part
  removes: `kit_tools/docs/MONITORING.md:352` (the synopsis gains both flags) and `:370` (the
  caveat paragraph: the healthy mode exists, the wait is status-aware, and the "do not wire it
  into a production deploy gate" sentence is replaced by the rule of which flag matches which
  container); `kit_tools/docs/TROUBLESHOOTING.md:122` (drop "fails by design against a container
  that has loaded weights"); `kit_tools/docs/LOCAL_DEV.md:303` and
  `kit_tools/testing/TESTING_GUIDE.md:58` ("token-less, degraded" becomes both modes);
  `kit_tools/arch/CODE_ARCH.md:109` (the module-map description);
  `kit_tools/docs/DEPLOYMENT.md:215` (the PASSED line names the mode). Resolve item 3 of the
  2026-09-13 "Open / Next" list in `kit_tools/SESSION_LOG.md` (the script's fitness as a
  weights-loaded probe).
- **(c) Compose pins and passthrough.** `compose/minimal.yml:70` and `compose/full.yml:44` pin
  `ghcr.io/washingbearlabs/forage:0.9.3-rc`; move both to `ghcr.io/washingbearlabs/forage:1.1.0`.
  The `forage-searxng` pin stays `0.1.1-rc`: no non-pre-release `searxng-v*` tag exists (`git tag`
  lists only `searxng-v0.1.0-rc` and `searxng-v0.1.1-rc`) and the fragments' comments say only that
  the searxng tag is published. Refresh the IMAGE PINS block (`minimal.yml:52–59`) and the
  per-service comments (`minimal.yml:66–69`, `full.yml:40–43`): the pin resolves once `v1.1.0`
  publishes (US-002), and a `docker compose up` before that fails with `manifest unknown` — that is
  sequencing, not breakage (the sentence the file already used for `0.9.3-rc`). Add `-
  FORAGE_SEARCH_PROVIDERS` and `- FORAGE_BRAVE_API_KEY` to the `forage` service's `environment:`
  list in both files as **bare names**, exactly like `- HF_TOKEN`: passed through when set in
  `compose/.env`, genuinely unset otherwise (unset is the default chain; the key is a credential and
  belongs in `compose/.env`, never inline — say so in the comment). In
  `tests/test_compose_fragments.py`: a module constant for the current forage release tag (one edit
  at the next release), a test that both fragments' `forage` image is `<IMAGE_NAME>:<that tag>`, a
  test that both new names are present as bare passthrough (`_environment(...)[name] is None`) on
  the `forage` service in both files, and drop the "does not exist for either published image yet"
  wording from `test_every_image_is_pinned`'s message.
- **(c) The pin-drift sweep is anchored on the literal, not on a phrase (ruling 31c).**
  `grep -rn 'forage:0.9.3-rc' compose kit_tools/docs kit_tools/arch` lists every live site today
  and is the list to clear: `compose/minimal.yml:70`, `compose/full.yml:44`,
  `kit_tools/docs/DEPLOYMENT.md:138`, `kit_tools/arch/INFRA_ARCH.md:146` (the fragment table row —
  its "Image" cell, and its "bare `HF_TOKEN` pass-through" cell gains the two new names) and
  `:159`, `kit_tools/arch/SERVICE_MAP.md:180` ("Current state"), `:365` (fragment table "Image
  pins" row) and `:367`, `kit_tools/docs/TROUBLESHOOTING.md:679`, `kit_tools/docs/LOCAL_DEV.md:229`
  ("At the time of this seed both pin"). One site carries `0.9.3-rc` without the `forage:` prefix,
  `kit_tools/docs/CI_CD.md:503`; `grep -n '0\.9\.3-rc' kit_tools/docs/CI_CD.md` hits only there
  today and must hit nothing after. `kit_tools/SESSION_LOG.md:156` keeps the literal as the
  history of item 2 of the 2026-09-13 "Open / Next" list, which this part ticks, and
  `kit_tools/specs/archive/` is history; neither is in the grep. Keep each document's factual
  content (the fragments exist, they pull published images) and change only the pin it names.
- **(d) Secret-grep patterns.** The `secret-grep` heredoc (`ci.yml` ~550–553) and the `publish`
  config grep (~991, `grep -E -q 'HF_TOKEN|hf_[A-Za-z0-9]{20,}'`) are deliberately duplicated; add
  `FORAGE_BRAVE_API_KEY` to both. In `tests/test_ci_workflow.py`, `_REQUIRED_GREP_PATTERNS` (~1068)
  becomes the three-tuple and `test_publish_greps_the_published_config_for_secrets` (~2026) iterates
  it instead of asserting only the `hf_` shape, so the two copies are pinned by one constant. Only
  the ruling's name — no bare `BRAVE_API_KEY`, no key-shape regex (ruling 20d). The documents that
  enumerate the set are the fan-out (ruling 31d): the operator's `docker history` grep at
  `kit_tools/docs/DEPLOYMENT.md:110`, the "same two patterns" wording at
  `kit_tools/docs/CI_CD.md:294` and the `secret-grep` description at `:217–218` ("two patterns"
  becomes three, naming the third), the guard-table row at `kit_tools/docs/GOTCHAS.md:531`, the
  "Secret-free check" Quick-commands row at `kit_tools/docs/TROUBLESHOOTING.md:121` (an operator
  following it must grep the three the gate greps), and `kit_tools/arch/SECURITY.md:230` (states
  the set and names the two tests that pin it).
- Land the four parts in one PR so `main` carries all of them before US-002's pre-flight; the
  compose pin is unresolvable until US-002 publishes, and the fragments must say so.

**Acceptance Criteria:**
- [ ] (a) In `.github/workflows/ci.yml`, the publish read step extracts the docstring entry for the
      version it read with POSIX awk into a file under `${RUNNER_TEMP}` and exits 1 with an
      `::error::` line when the file is empty; the step's `run:` contains no `python3`, `uv` or
      `scripts/`; its only `$GITHUB_OUTPUT` write is the existing single-line `version=`.
- [ ] (a) The create step writes the unchanged heredoc text to a notes file, appends a `What
      changed in contract ${CONTRACT_VERSION}:` heading and then the entry file with `cat`, and
      calls `gh release create … --notes-file`; the `publish` job's shell contains no
      `--notes "${notes}"` and no `eval`; the `contract: ${CONTRACT_VERSION}` line is still on its
      own line. `grep -c '1\.2\.0' .github/workflows/ci.yml` reports 0 and `publish` gains no
      toolchain-install step.
- [ ] (a) The read-back step keeps `grep -qE "^contract: ${escaped}$"` as its first grep and then
      asserts every line of the entry file is present in the published body with `grep -qF`.
- [ ] (a) `tests/test_ci_workflow.py::TestReleaseContractMapping` gains
      `test_the_current_contract_version_has_a_docstring_entry`,
      `test_the_extractor_is_exact_against_a_hostile_module`,
      `test_the_extractor_matches_the_whole_version` and
      `test_the_extractor_returns_nothing_for_an_unannounced_version` (all four run the awk
      program extracted from `ci.yml` through `subprocess`), plus the static
      `test_the_read_step_extracts_the_entry_with_posix_awk`,
      `test_the_release_step_appends_the_entry_from_a_notes_file` and
      `test_the_read_back_checks_the_entry_with_a_fixed_string_grep`; the hostile module carries a
      backtick span, `$(id)`, a mid-line `*`, an indented bullet look-alike, an `EOF` line, a
      `version=forged` line and a mid-line `"""`, and the asserted output is exact. Every
      pre-existing test in the class passes unmodified.
- [ ] (a) `grep -n 'per-version entry' docs/releases.md kit_tools/docs/CI_CD.md` hits both;
      `grep -n 'contract: <version>' docs/releases.md` hits; `grep -n 'docstring entry'
      contract/GOVERNANCE.md` hits inside step 7 of "Bumping the contract"; `grep -n 'docstring
      entry' .github/pull_request_template.md` hits; `tests/test_governance_docs.py` passes
      unmodified.
- [ ] (b) `uv run python contract_smoke.py --help` lists `--expect-status` with choices `healthy`
      and `degraded` and default `degraded`, and `--anchor` with the committed
      `contract/openapi.yaml.sha256` as its default; `grep -n 'never from the Release assets'
      contract_smoke.py` hits in the `--anchor` help text and in the module docstring; CI's smoke
      invocation is byte-identical and `tests/test_ci_workflow.py::TestSmokeJob` passes unmodified.
- [ ] (b) `tests/test_contract_smoke.py` covers: a healthy body passes under `healthy`; a degraded
      body fails under `healthy`; a healthy body fails under the default; `wait_for_health` under
      `healthy` keeps polling past a 200 `degraded` body and returns the first `healthy` one; it
      returns the last body when the deadline passes without one; the in-image anchor is compared
      against the `--anchor` file. `TestSingleSourceOfTruth` passes unmodified.
- [ ] (b) `grep -n -i 'fails by design' kit_tools/docs/TROUBLESHOOTING.md` and `grep -n 'deploy
      gate as-is' kit_tools/docs/MONITORING.md` return nothing; `grep -n -- '--expect-status'
      kit_tools/docs/MONITORING.md kit_tools/docs/TROUBLESHOOTING.md kit_tools/docs/LOCAL_DEV.md
      kit_tools/testing/TESTING_GUIDE.md kit_tools/arch/CODE_ARCH.md kit_tools/docs/DEPLOYMENT.md
      kit_tools/docs/CI_CD.md` hits every file.
- [ ] (c) `grep -c 'image: ghcr.io/washingbearlabs/forage:1.1.0' compose/minimal.yml
      compose/full.yml` reports 1 for each file; `grep -c 'forage-searxng:0.1.1-rc'
      compose/minimal.yml compose/full.yml` reports 1 for each file; `grep -c 'manifest unknown'
      compose/minimal.yml compose/full.yml` reports at least 1 for each file.
- [ ] (c) `tests/test_compose_fragments.py` pins the forage tag literal `1.1.0` in both fragments
      and asserts `FORAGE_SEARCH_PROVIDERS` and `FORAGE_BRAVE_API_KEY` are bare-name entries of the
      `forage` service in both.
- [ ] (c) `grep -rn 'forage:0.9.3-rc' compose kit_tools/docs kit_tools/arch` returns nothing;
      `grep -n '0\.9\.3-rc' kit_tools/docs/CI_CD.md` returns nothing; `grep -c 'forage:1.1.0'
      kit_tools/arch/INFRA_ARCH.md kit_tools/arch/SERVICE_MAP.md kit_tools/docs/LOCAL_DEV.md
      kit_tools/docs/DEPLOYMENT.md` reports at least 1 for each file, and the INFRA_ARCH fragment
      table row names `FORAGE_SEARCH_PROVIDERS` and `FORAGE_BRAVE_API_KEY` beside `HF_TOKEN`.
- [ ] (d) `grep -c 'FORAGE_BRAVE_API_KEY' .github/workflows/ci.yml` reports at least 2;
      `tests/test_ci_workflow.py::_REQUIRED_GREP_PATTERNS` is `("HF_TOKEN", "hf_[A-Za-z0-9]{20,}",
      "FORAGE_BRAVE_API_KEY")`, and both `test_secret_grep_pattern_set_is_defined_in_the_workflow`
      and `test_publish_greps_the_published_config_for_secrets` iterate it.
- [ ] (d) `grep -c 'FORAGE_BRAVE_API_KEY' kit_tools/docs/DEPLOYMENT.md kit_tools/docs/CI_CD.md
      kit_tools/docs/GOTCHAS.md kit_tools/docs/TROUBLESHOOTING.md kit_tools/arch/SECURITY.md`
      reports at least 1 for each file; `grep -n 'two patterns' kit_tools/docs/CI_CD.md` returns
      nothing; the operator grep in DEPLOYMENT and the TROUBLESHOOTING Quick-commands row carry the
      same three patterns as `ci.yml`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-002: Cut + publish the v1.1.0 image (owner gate)

**Priority:** P2

**Description:** As the owner, I want a `v1.1.0` image published through the gated lane and verified
from the tagged checkout in both smoke modes, so Poppy can pin a digest carrying the search-provider
capability at contract 1.2.0. **Execution halts here for the owner:** the tag push is the human
gate, and every step in this story needs a Docker daemon, network egress and GHCR access that the
autonomous environment does not have.

**Independent Test:** The `v1.1.0` tag triggers `publish` (which `needs:` the six gates) and it goes
green; `gh release view v1.1.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.2\.0$`
and contains every line of the `1.2.0` docstring entry; `latest`, `1.1` and `1.1.0` resolve to one
index digest; from the `v1.1.0` checkout `contract_smoke.py --image <ref> --anchor
contract/openapi.yaml.sha256` exits 0 with `--expect-status degraded` against a no-env container
and with `--expect-status healthy` against a weights-loaded one started with `--env-file`.

**Implementation Hints:**
- **The runbook is `kit_tools/specs/archive/feature-forage-contract.md:975–1070`** — the executed
  `v1.0.0` cut: the four-item pre-flight, the cut, the table of `publish` steps to watch with what
  each failure means, the four-way sha256 equality, the `latest` digest check, and the "what to
  record" list. Follow it with `v1.1.0` substituted; the hints below list only what differs.
- **Pre-flight, on `main` at the commit to be tagged:** all six required checks green; `git switch
  main && git pull` and confirm `git log -1`; `uv run python -m scripts.export_contract --check`
  clean; `grep CONTRACT_VERSION pipeline/contract.py` prints `1.2.0`; the `1.2.0` docstring entry
  in `pipeline/contract.py` names `search_unavailable`. Cut clear of the ten minutes around 00:00
  UTC (the passwd-layer bound in `docs/releases.md` § "Reproducible builds").
- **Pre-flight, US-001 is in the tree** (doc-only, so the gates cannot prove it): `grep -c
  FORAGE_SEARCH_PROVIDERS README.md` and `grep -c FORAGE_BRAVE_API_KEY README.md` both report at
  least 1; `grep -c 'Credential handling for .FORAGE_BRAVE_API_KEY.' docs/configuration.md` reports
  at least 1 (the `.` stands for the backtick); `grep -n 'starts and reports itself' README.md
  docs/configuration.md` returns nothing.
- **Pre-flight, US-004 is in the tree, part by part.** (a) `uv run pytest tests/test_ci_workflow.py
  -k 'extractor or docstring_entry'` collects at least four tests and passes — that run executes
  the awk program extracted from `ci.yml` against this very tree's `pipeline/contract.py`. Then
  rehearse it by hand: copy the program verbatim out of the read step and run `awk -v v=1.2.0
  '<program>' pipeline/contract.py`; confirm the output is the `1.2.0` bullet with its
  continuation lines, names `search_unavailable` and contains no line of the `1.1.0` entry; then
  assemble the notes the way the step does (the header, `What changed in contract 1.2.0:`, the
  entry) into a scratch file and read it once. The tag's run is then the extractor's second
  execution, not its first, and the point of no return sits behind a rehearsal. Record the
  extraction output in the Implementation Notes. (b) `grep -c -- '--expect-status'
  contract_smoke.py` reports at least 1 and `grep -n 'never from the Release assets'
  contract_smoke.py` hits; (c) `grep -c 'forage:1.1.0' compose/minimal.yml compose/full.yml`
  reports 1 each; (d) `grep -c FORAGE_BRAVE_API_KEY .github/workflows/ci.yml` reports at least 2.
- **Pre-flight, the sibling-owned rows and sweeps landed** (the Overview's promise, made good
  here). The four variable rows: `grep -cE '^\| .FORAGE_SEARCH_PROVIDERS. \|'
  docs/configuration.md kit_tools/docs/ENV_REFERENCE.md` and the same for `FORAGE_BRAVE_API_KEY`
  report at least 1 for each file (two variables × two documents). The version sweep: `grep -n
  '\*\*1\.1\.0\*\*' README.md CLAUDE.md contract/GOVERNANCE.md kit_tools/docs/API_GUIDE.md
  kit_tools/arch/CODE_ARCH.md kit_tools/arch/SERVICE_MAP.md` returns nothing (CODE_ARCH:108 and
  SERVICE_MAP:75,193 are spec 1 US-004's per ruling 32). The anchor sweep: with
  `anchor=$(cut -d' ' -f1 contract/openapi.yaml.sha256)`, `grep -c "$anchor"
  kit_tools/docs/API_GUIDE.md kit_tools/docs/CI_CD.md kit_tools/docs/DEPLOYMENT.md
  kit_tools/arch/SERVICE_MAP.md` reports 1 for each file (SERVICE_MAP:202 is the paragraph that
  tells Poppy which hash to verify against).
- **The cut:** `git tag v1.1.0 && git push origin v1.1.0`, then `gh run watch`. Do **not** cut a
  `searxng-v*` tag — the companion image is unchanged this epic.
- **Watch these `publish` steps live**, in order: "Verify the published amd64 image is the gated
  filesystem" (the parity gate — a failure here is a finding, see Edge Cases); "Read the contract
  version from the tagged tree" (its awk extraction runs for the second time, after the rehearsal
  — US-004a; a red here after the push is the "Release missing" edge case); the published-config
  secret grep (three patterns, first live run — US-004d); "Create the GitHub Release"
  (`--notes-file`); "Assert the Release body advertises the tagged tree's contract version" (the
  `contract:` line, then every entry line by `grep -F`); "Assert the Release assets verify against
  the committed anchor". Record whether the publish rebuild was cold or warm.
- **After the run:** the four-way sha256 equality from the runbook with `v1.1.0` (committed anchor =
  `git show v1.1.0:contract/openapi.yaml` = Release asset via `sha256sum -c` = the in-image
  `/app/contract/openapi.yaml`); the Release body check above; `docker buildx imagetools inspect`
  (the `--format` from the runbook) on `latest`, `1.1` and `1.1.0` — `latest` moves off the `1.0.0`
  image for the first time and `1.1` is minted for the first time, so a pointer landing anywhere
  else is the one outcome to stop for.
- **Smoke, twice, from the tagged checkout** (`git switch --detach v1.1.0`, then `uv sync --extra
  dev`) so `CONTRACT_VERSION` and the default anchor are the tag's, with the pulled index digest as
  `<ref>` (`ghcr.io/washingbearlabs/forage@sha256:…`). `--anchor contract/openapi.yaml.sha256` is
  the committed file of that checkout every time — never the copy `gh release download` writes,
  never the in-image file (GOVERNANCE "Consumers": verify against the anchor from the same tag,
  never against another copy). (1) `docker run --rm -d -p 127.0.0.1:8020:8020 --name forage-rel
  <ref>` with no `--env-file` and no volume, then `uv run python contract_smoke.py --image <ref>
  --expect-status degraded --anchor contract/openapi.yaml.sha256`. (2) The same image started with
  `--env-file "$TMPDIR/hf.env"` — a temporary file holding only the `HF_TOKEN` line copied out of
  `compose/.env`, never the whole file (a `VALKEY_URL` pointing at an unreachable Valkey would pin
  `/health` at `cache_unavailable` and turn the healthy wait into a 600 s hang); it is never
  printed and is deleted afterwards plus the warm `forage-model-cache` volume mounted at `/app/model-cache` when it exists,
  then the same command with `--expect-status healthy --timeout-seconds 600`: the script itself
  now waits for `status: healthy` (US-004b), and 600 s covers a cold weights fetch. Never pass the
  token inline (`-e HF_TOKEN=…`): the recorded command is what the next release copies, and this
  file is public. A red under the wrong flag is a misconfigured verification, not a release
  failure (Edge Cases).
- Read the `secret-grep` job log and the publish config-grep output for "No forbidden pattern"; they
  now cover `FORAGE_BRAVE_API_KEY`.
- **Record** in a `## Implementation Notes` section appended to this file (US-003 completes it): run
  URL, tagged commit sha, index digest, the four sha256 values, the `contract:` line and the entry
  as published, the three-tag digest equality, cold/warm, the rehearsal extraction output, and
  both smoke commands exactly as run (with `--env-file "$TMPDIR/hf.env"`, never a value) with their
  exit codes.

**Acceptance Criteria:**
- [ ] Pre-flight recorded in the Implementation Notes: six checks green on the tagged commit;
      `export_contract --check` clean; `CONTRACT_VERSION` is `1.2.0`; the `1.2.0` docstring entry
      names `search_unavailable`; the four US-001 greps pass; the US-004 greps (b), (c) and (d)
      pass.
- [ ] Pre-flight, US-004(a): `uv run pytest tests/test_ci_workflow.py -k 'extractor or
      docstring_entry'` collected at least four tests and passed on the tagged commit, and the
      by-hand `awk` run on that commit's `pipeline/contract.py` printed the `1.2.0` entry — first
      line beginning `* ``1.2.0`` `, naming `search_unavailable`, no line of the `1.1.0` entry —
      recorded verbatim.
- [ ] Pre-flight, sibling-owned work: the four variable-row greps report at least 1 for each file
      and variable; no bold `**1.1.0**` in `README.md`, `CLAUDE.md`, `contract/GOVERNANCE.md`,
      `kit_tools/docs/API_GUIDE.md`, `kit_tools/arch/CODE_ARCH.md`, `kit_tools/arch/SERVICE_MAP.md`;
      the four embedded anchors (API_GUIDE, CI_CD, DEPLOYMENT, SERVICE_MAP) equal
      `contract/openapi.yaml.sha256`.
- [ ] `v1.1.0` is cut by the owner and no `searxng-v*` tag is pushed; `publish` is green; the
      multi-arch `1.1.0` image is on GHCR; run URL, tagged commit sha and OCI index digest recorded.
- [ ] Four-way sha256 equality at `v1.1.0` (committed anchor, repository file, Release asset via
      `sha256sum -c`, in-image `/app/contract/openapi.yaml`) verified and the values recorded.
- [ ] `gh release view v1.1.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.2\.0$`
      and contains every line of the rehearsal extraction output (`grep -F` per line); the publish
      step "Assert the Release body advertises the tagged tree's contract version" is green.
- [ ] `docker buildx imagetools inspect` prints one identical index digest for `latest`, `1.1` and
      `1.1.0`; recorded.
- [ ] From the `v1.1.0` checkout, `uv run python contract_smoke.py --image <ref> --anchor
      contract/openapi.yaml.sha256` exits 0 with `--expect-status degraded` against the container
      started with no `--env-file` and no volume, and exits 0 with `--expect-status healthy`
      against the container started with `--env-file "$TMPDIR/hf.env"`; both commands and exit codes
      are recorded; the recorded commands carry `--env-file "$TMPDIR/hf.env"` and no token value, and
      `grep -nE 'hf_[A-Za-z0-9]{20,}' kit_tools/specs/feature-search-release.md` returns nothing.
- [ ] Every `--anchor` value recorded in the Implementation Notes is `contract/openapi.yaml.sha256`
      of the `v1.1.0` checkout — never a file written by `gh release download` and never the
      in-image copy.
- [ ] `secret-grep` and the publish config grep are green on the tag's run with the three-pattern
      set; the two "No forbidden pattern" log lines recorded.

### US-003: Post-release verification (third-party view) + Poppy handoff record

**Priority:** P2

**Description:** As the owner, I want the published image verified the way a stranger reaches it —
credential-free, key-less, one real `/search`, and once with a placeholder key to prove the key
plumbing is packaged — and the digest, contract, anchor and spend posture recorded where the Poppy
`epic-search-policy` session reads them, so the cross-repo gate opens on evidence rather than
memory. Owner-executed: needs a Docker daemon and registry access.

**Independent Test:** `docker logout ghcr.io` followed by `docker pull
ghcr.io/washingbearlabs/forage@sha256:<index-digest>` succeeds; `GET /health` on that image reports
`contract_version: "1.2.0"`, `search_providers: ["searxng"]` and no `brave_api_key` key in
`capabilities`; `POST /search` returns 200 with `provider_used: "searxng"`; the same image started
with a placeholder `FORAGE_BRAVE_API_KEY` reports `capabilities.brave_api_key: 1` and the
placeholder appears nowhere; this file's Implementation Notes and `docs/releases.md` § "Released
versions" both carry tag, index digest, contract version, anchor sha256 and the spend-posture
sentence.

**Implementation Hints:**
- **Boundary with US-002.** US-002 verified the artifact the lane produced, from an authenticated
  workstation and the tagged checkout. This story verifies only the third-party view and owns the
  record — nothing here repeats a US-002 check.
- **Credential-free pull.** The owner's Docker keychain holds a GHCR credential (the release work
  needs one), so a plain `docker pull` authenticates silently and proves nothing. Run `docker logout
  ghcr.io` immediately before `docker pull ghcr.io/washingbearlabs/forage@sha256:<digest>` and
  capture both outputs. The daemon-independent equivalent is `skopeo inspect --no-creds
  docker://ghcr.io/washingbearlabs/forage@sha256:<digest>`; use it as a second witness when `skopeo`
  is installed.
- **Key-less floor on the real artifact.** Bring the image up with `compose/minimal.yml` (pinned at
  `1.1.0` by US-004, now resolvable) with `SEARXNG_SECRET` in `compose/.env` and no
  `FORAGE_SEARCH_PROVIDERS` and no `FORAGE_BRAVE_API_KEY` set. Then `curl -s localhost:8020/health |
  jq '{contract_version, search_providers, capabilities}'` — expect `"1.2.0"`, `["searxng"]`, and a
  `capabilities` object with no `brave_api_key` key (ruling 15). Then one `POST /search` with a
  `SearchRequest` body (`{"query": "…"}`): HTTP 200, `provider_used: "searxng"`, `fallback_fired:
  false`, `provider_errors: []` (spec 3 US-003 fields). Record the telemetry fields only — never
  result bodies. A 422 `searxng_error` or `searxng_unavailable` here is an upstream-engine condition
  (`kit_tools/docs/GOTCHAS.md` "SearXNG :latest rots"); retry with another query — the packaging
  evidence is `search_providers`, the round-trip is the traffic proof.
- **Key plumbing on the real artifact, at zero spend.** Stop that container and bring the same
  image up once more with `FORAGE_BRAVE_API_KEY` set to an obviously synthetic placeholder,
  `placeholder-not-a-key`, and `FORAGE_SEARCH_PROVIDERS` still unset. Supply it through a
  throwaway env file (`printf 'FORAGE_BRAVE_API_KEY=placeholder-not-a-key\n' >
  "$TMPDIR/keyed.env"`, then `--env-file "$TMPDIR/keyed.env"`, deleted afterwards) rather than
  `-e`: the placeholder is not a secret, but the recorded command is the template the next person
  copies for a real one. Expect `GET /health` to report `capabilities.brave_api_key: 1` (presence,
  ruling 15) and `search_providers: ["searxng"]` (the default chain; the key alone adds no
  provider). Then `grep -c 'placeholder-not-a-key'` over the `/health` body, the `/metrics` body
  and `docker logs <name> 2>&1` reports 0 each — spec 2 US-003's never-leaks guarantee, now on the
  published artifact. Issue **no** `/search` in this run: no request is ever made with the
  placeholder, so it never reaches Brave and the run costs nothing; what it proves is that the
  variable reaches the lifespan inside the published image and is disclosed as presence only. The
  paid path itself is verified against the committed envelope sample (spec 2, ruling 24); no live
  Brave request is made through the image in this epic, and the record says so.
- **The record.** Complete the `## Implementation Notes` section US-002 started with a field/value
  table in the shape of `docs/bootstrap-notes.md` § "Pin record": image tag `1.1.0`; OCI index
  digest; contract version `1.2.0`; anchor sha256 (`git show v1.1.0:contract/openapi.yaml.sha256`);
  tagged commit sha; publish run URL; the anonymous-pull command and output; the `/health` excerpt;
  the `/search` telemetry; the placeholder-key `/health` excerpt and the three zero counts. Two
  rows carry posture rather than provenance, because the consumer reads this table and not the
  README: a `Spend posture` row — with `FORAGE_BRAVE_API_KEY` set, anyone who can reach port 8020
  can spend the operator's money; Forage enforces no budget cap by decision (ruling 12); the
  observability floor is `search.paid_calls` and `search.fallback_fired`; the budget breaker is
  the consumer's (`epic-search-policy`) — linking the README posture blockquote rather than
  restating it; and a `Paid-path evidence` row — verified against the committed envelope sample
  and by the placeholder-key packaging check; no live Brave request through the image. Say in the
  section that **the Poppy session reads this table** and that recording into Poppy's pin record
  happens there (one-way sync; nothing here pushes). Poppy's re-vendor fetches `openapi.yaml` +
  `openapi.yaml.sha256` from the `v1.1.0` Release assets (`gh release download v1.1.0 --pattern
  'openapi.yaml*'`) and verifies against the anchor in this table, never against another copy
  (`contract/GOVERNANCE.md` "Consumers").
- **`docs/releases.md` gets a "Released versions" section** — the document has none (its headings
  are Withdrawn tags through Required status checks) and no `v1.0.0` entry. Add `## Released
  versions` between the intro and "## Withdrawn tags". Format, one `### v<X.Y.Z> — <cut date>`
  subsection per release, newest first: a `contract: <version>` line (verbatim the Release-body
  line), an `anchor: <sha256>` line, an `index digest: sha256:<…>` line, a `tagged commit: <sha>`
  line, then `What shipped:` with **one bullet per capability**. Give `v1.0.0` a retroactive entry
  from the archived runbook record (`feature-forage-contract.md:951–969`: run `34667821482`, commit
  `f4c2b16`, contract `1.1.0`, anchor `00b1dbaa…`) and `gh release view v1.0.0` for its digest. The
  `v1.1.0` bullets: the `SearchProvider` seam and `FORAGE_SEARCH_PROVIDERS`; the Brave LLM-Context
  paid provider behind `FORAGE_BRAVE_API_KEY`, carrying the spend-posture sentence in the same
  words as the table row; free-first/paid-on-failure fallback with the `search_unavailable` code;
  per-request policy and `/health` provider status; the two `search.*` `/metrics` counters. In the
  same edit, correct `docs/releases.md:58–60` ("`latest` therefore does not exist yet") to the
  shipped state: `latest` has existed since `v1.0.0` and `v1.1.0` moved it.
- **Release-state docs the session-end table names:** `kit_tools/SYNOPSIS.md` rows "Maturity" and
  "Published image"; `kit_tools/docs/DEPLOYMENT.md:87–91` (tag inventory, `TAG=1.0.0` example) and
  `:111–117` (the mapping sentence becomes image `1.1.0` serves contract `1.2.0`);
  `kit_tools/docs/CI_CD.md:297` ("currently `1.1.0`" — the one stale contract-version claim no
  other spec owns — becomes `1.2.0`) and `:315–316`; `kit_tools/arch/INFRA_ARCH.md:188–192`. Then
  the closeout the previous epic did in `a92d373`: tick the epic wrapper's Completion Criteria, and
  follow `kit_tools/AGENT_README.md`'s session-end table for `SESSION_LOG.md`,
  `roadmap/MILESTONES.md` and the vision's T2.1 status.
- **Suite-count bookkeeping is this story's close-out (ruling 32).** US-004 grew three test modules
  and nothing enforces the numbers the docs state. Run `uv run pytest` on the tagged commit and
  write its count into `kit_tools/testing/TESTING_GUIDE.md:98` (the total) and the per-module rows
  at `:120–122` (`test_ci_workflow.py`, `test_compose_fragments.py`, `test_contract_smoke.py`, with
  each row's prose extended for the extractor tests, the passthrough tests and the status-aware
  wait), `kit_tools/SYNOPSIS.md:32` and `kit_tools/AGENT_README.md:72`; the parenthetical at
  `CLAUDE.md:131` follows the same number.

**Acceptance Criteria:**
- [ ] `docker logout ghcr.io` followed by `docker pull
      ghcr.io/washingbearlabs/forage@sha256:<index-digest>` succeeds; both commands and their output
      are recorded in the Implementation Notes.
- [ ] Against the pulled image with no `FORAGE_SEARCH_PROVIDERS` and no `FORAGE_BRAVE_API_KEY` set,
      `GET /health` reports `contract_version: "1.2.0"`, `search_providers: ["searxng"]`, and a
      `capabilities` object with no `brave_api_key` key; the excerpt is recorded.
- [ ] One `POST /search` against that container (brought up from `compose/minimal.yml` at `1.1.0`)
      returns HTTP 200 with `provider_used: "searxng"` and `fallback_fired: false`; the telemetry
      fields are recorded and no result body is.
- [ ] Against the same image started with an `--env-file` carrying
      `FORAGE_BRAVE_API_KEY=placeholder-not-a-key` and no `FORAGE_SEARCH_PROVIDERS`, `GET /health`
      reports `capabilities.brave_api_key: 1` and `search_providers: ["searxng"]`; `grep -c
      'placeholder-not-a-key'` over the `/health` body, the `/metrics` body and `docker logs`
      reports 0 for each; no `/search` was issued in that run; the excerpt and the three counts
      are recorded.
- [ ] `## Implementation Notes` in this file carries a field/value table with image tag `1.1.0`, OCI
      index digest, contract version `1.2.0`, the anchor sha256 from `git show
      v1.1.0:contract/openapi.yaml.sha256`, the tagged commit sha and the publish run URL,
      introduced as the table the Poppy session reads; its `Spend posture` row states that Forage
      enforces no budget cap (ruling 12), names `search.paid_calls` and `search.fallback_fired`,
      and says the budget breaker is the consumer's; its `Paid-path evidence` row states that no
      live Brave request was made through the image.
- [ ] `docs/releases.md` has a `## Released versions` section with `### v1.0.0` and `### v1.1.0`
      entries in the defined format (`contract:`, `anchor:`, `index digest:`, `tagged commit:` lines
      and one "What shipped" bullet per capability, the Brave bullet carrying the spend-posture
      sentence); `grep -n 'does not exist yet' docs/releases.md` returns nothing.
- [ ] `grep -n 'v1.1.0' kit_tools/SYNOPSIS.md kit_tools/docs/DEPLOYMENT.md kit_tools/docs/CI_CD.md
      kit_tools/arch/INFRA_ARCH.md` hits every file; the DEPLOYMENT mapping sentence reads image
      `1.1.0` serves contract `1.2.0`; `grep -nE 'currently .1\.1\.0.' kit_tools/docs/CI_CD.md`
      returns nothing.
- [ ] `grep -n '1610 tests' kit_tools/testing/TESTING_GUIDE.md`, `grep -n '1610 collected'
      kit_tools/SYNOPSIS.md` and `grep -n '1610 as of' kit_tools/AGENT_README.md` return nothing,
      and the three state the one count `uv run pytest` reports on the tagged commit.
- [ ] Every Completion Criterion in `kit_tools/specs/epic-search-providers.md` is ticked.
- [ ] `uv run pytest` passes (doc-only edits; the release-doc guards stay green).

## Edge Cases

- **A gate fails before the push** → nothing was pushed, `publish` never started. Fix on `main` and
  push a *new* tag; never move `v1.1.0` (`docs/releases.md` § "The git tag is the version"). US-002.
- **The push itself is interrupted** → orphan blobs, no half-formed tag; re-running the same tag is
  safe (`docs/releases.md` § "When something goes wrong"). US-004 amends that section
  (`docs/releases.md:284–287`, the sentence saying a failed Release step is fixed by re-running)
  with the two announcement cases: an extractor fault against a correct docstring entry is fixed
  by `gh release edit --notes-file` from the locally extracted entry and the tags stay; a wrong or
  missing docstring entry cannot be fixed on an immutable tagged tree and is
  withdraw-the-tag-and-cut-`v1.1.1`. US-004 carries a criterion that the section states both.
  US-002.
- **The layer-parity gate fails after the push** → the tags exist but point at an ungated image and
  `latest` has already moved. This is a finding, not a flake: withdraw the git tag **and** delete
  the GHCR package version (`docs/releases.md` § "Withdrawn tags"; `delete:packages` obtained ad
  hoc, dropped after), confirm where `latest` and `1.1` point afterwards, apply the recovery in
  `kit_tools/docs/GOTCHAS.md` § "A cold-cache publish fails its own parity gate" (delete the
  `index-publish-*` GHA cache entries), and cut `v1.1.1`. The one exception: a run within ~10
  minutes of 00:00 UTC can trip the gate on the passwd-layer day count — check those timestamps
  first and re-run; only a non-midnight failure is a finding. US-002.
- **The read step's entry extraction fails after the push** → the image is live and gated, and the
  Release is *missing*, not wrong. Re-running does not help: the step reads the immutable tagged
  tree, so it fails identically every time. Do not hand-create the Release. Add or fix the entry on
  `main` and cut `v1.1.1`; the `1.1.0` tags stay (the image passed every gate) and Poppy pins the
  version that has a Release. US-002's rehearsal — the `-k 'extractor or docstring_entry'` run and
  the by-hand `awk` on the commit to be tagged — exists so this never happens live. US-002.
- **The Release-body assertion fails after a successful push** → the image is live and fine; the
  Release is wrong rather than missing. `gh release edit v1.1.0 --notes-file` with a body carrying
  the `contract: 1.2.0` line and the entry verbatim, or delete the Release and re-run the job (`gh
  release create` refuses a tag that already has one). US-002.
- **The published image advertises the wrong contract version, or `/health` lacks
  `search_providers`** → a release failure: do not hand the digest to Poppy, and because `1.1.0`,
  `1.1` and `latest` are pointers anonymous third parties follow, withdraw the tag and delete the
  package version under the same rule, then cut `v1.1.1` from a fixed tree. US-002/US-003.
- **`contract_smoke.py --expect-status healthy` goes red on `status`, `degraded_reasons` and
  `capabilities` together** → the deadline passed before `status: healthy`. The script waits for
  that status (US-004b), so this is a wait that expired, not a flag that mismatched: check
  `promptguard_loaded` and the container log, raise `--timeout-seconds` for a cold weights fetch or
  mount the warm volume, and re-run under the **same** flag. A container that still cannot reach
  `healthy` is a release failure to stop for. Never re-run a weights-loaded container under
  `degraded` and record the healthy mode as verified — a `degraded` pass proves the token-less
  floor and nothing else. US-002.
- **`contract_smoke.py --expect-status degraded` goes red on the same three checks** → the container
  has weights and had loaded them (an `--env-file` or a volume was passed): a misconfigured
  verification, not a release failure. The `degraded` run's target is the container started with
  no `--env-file` and no volume; the weights-loaded container is verified under `healthy`. US-002.
- **`contract_smoke.py` goes red on the anchor** from a `main` checkout after later merges → the
  wrong anchor was compared; run from the `v1.1.0` checkout. A red after passing `--anchor` a file
  from `gh release download` or from the image is the rule being enforced, not a bug: the anchor
  is the committed file at the tag. US-002.
- **`docker compose up` fails with `manifest unknown` between US-004 merging and US-002 publishing**
  → sequencing, not breakage; the fragments say so. US-004/US-002.
- **The `/search` round-trip returns 422 `searxng_*`** → the upstream engines, not the image; retry
  with another query. `/health` `search_providers` is the packaging evidence. US-003.
- **The placeholder-key run reports no `brave_api_key` in `capabilities`** → the published image is
  not reading the variable from its environment: a packaging failure, handled under the same rule
  as a wrong contract version — withdraw, delete the package version, cut `v1.1.1`. US-003.
- **The anonymous pull "succeeds" with a credential still in the keychain** → it proved nothing;
  `docker logout ghcr.io` first, or use `skopeo inspect --no-creds`. US-003.
- **Poppy attempts to pin before this ships** → the cross-repo gate is closed by design until the
  Implementation Notes table exists. Documented, not a bug. US-003.

## Out of Scope

- Poppy's re-vendor, pin bump, settings UI, badge and vault→env plumbing — the Poppy
  `epic-search-policy` epic. Nothing here edits Poppy.
- The `forage-searxng` companion image and its `searxng-v*` lane: unchanged this epic; its compose
  pin stays `0.1.1-rc` because no non-pre-release `searxng-v*` tag exists.
- A spend ceiling or budget breaker (ruling 12): the docs say there is none; none is built. The
  handoff record states the consequence once so the consumer builds its own.
- The `docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md` rows for the two variables
  (specs 1 and 2); the contract-version mentions and embedded anchors in README, CLAUDE.md,
  GOVERNANCE, API_GUIDE, CODE_ARCH, CI_CD, DEPLOYMENT, SERVICE_MAP (spec 1 US-004, spec 4 US-003);
  the `/health` field documentation in MONITORING and API_GUIDE (spec 4 US-002). US-002's
  pre-flight confirms the rows, the version sweep and the anchor sweep by grep — its criteria name
  each command; the `/health` field docs are proven by spec 4 US-002's own criteria.
- Deleting `0.9.2-rc`'s GHCR package version: recorded as pending in `docs/releases.md` § "Withdrawn
  tags" since 2026-09-11; it stays tracked there, not here.
- Mechanising the index digest into the Release body: the manual transcription mirrors the `v1.0.0`
  precedent and is cross-checked by US-003's anonymous pull; a future release-tooling pass.
- Additional secret-grep patterns beyond `FORAGE_BRAVE_API_KEY` (a bare `BRAVE_API_KEY`, a key-shape
  regex) — ruling 20d names exactly one addition.
- A live Brave request through the published image: the paid path is verified against the
  committed envelope sample (ruling 24) and the key plumbing by a placeholder at zero spend; the
  handoff record says exactly that.
- Lifting the `v1.0.0` runbook out of the archived spec into `docs/releases.md` § "Cutting a
  release" as a version-agnostic checklist — a release-tooling pass after this cut; US-002 cites
  the archived record once more.

## Assumptions

- Forage's gated publish lane and `v*` tag scheme are intact: `publish` `needs:` the six gates,
  fires on `refs/tags/v*`, and a non-pre-release tag moves `X.Y.Z`, `X.Y` and `latest`
  (`tests/test_ci_workflow.py::TestPublishJob` evaluates those rules).
- Specs 1–4 are merged: `CONTRACT_VERSION` is `1.2.0`, `search_unavailable` exists, the `1.2.0`
  docstring entry names it, `/health` carries `search_providers` and the `capabilities` map entry,
  `SearchResponse` carries `provider_used` / `fallback_fired` / `provider_errors`.
- Poppy consumes by digest, never a floating tag, and re-vendors the contract from the `v1.1.0`
  Release assets verified against the anchor recorded here.
- The owner has PromptGuard weights available for the `healthy` smoke — an `HF_TOKEN` line in
  `compose/.env` (passed with `--env-file`, never inline) or the warm `forage-model-cache` volume
  — and gives a cold fetch `--timeout-seconds` above the 120 s default.
- The owner's workstation `awk` is POSIX (macOS `awk`, `mawk` and `gawk` all qualify), so the
  extractor rehearsal runs the workflow's program unchanged.
- The owner's workstation has Docker, `gh`, `docker buildx` and network access to GHCR; US-002 and
  US-003 are not executable by the autonomous orchestrator.

## Technical Considerations

- **Two semvers.** Image `1.1.0` serves contract `1.2.0`; keep them distinct in every doc and in the
  release record. `tests/test_governance_docs.py::TestTheTwoSemverRule` requires GOVERNANCE's "Two
  semvers" section to name both `v1.0.0` and the current `CONTRACT_VERSION` — spec 4 US-003's sweep
  satisfies it and US-002's pre-flight `uv run pytest` proves it.
- **Gating per story.** US-001 and US-004 are autonomous (docs, code, CI, compose, tests — PRs to
  `main`). US-002 is the owner gate: execution stops before the tag push. US-003 is owner-executed
  (Docker daemon, registry, `gh`). The epic's decomposition table names US-002 as the human gate.
- **Verification is split, not repeated.** US-002 verifies the artifact the lane produced —
  authenticated, from the tagged checkout, both smoke modes, four-way sha256, alias digests. US-003
  verifies the third-party view — credential-free pull, key-less `/health`, one `/search`, one
  placeholder-key run — and owns the record.
- **`--expect-status` and `--anchor` change nothing for CI.** Both default to today's behaviour:
  under `degraded` against a weights-free image the status-aware wait returns on the first 200
  exactly as before, and the `smoke` job's invocation and its tests are untouched. Under `healthy`
  the wait is the difference between a verification and a race against the weights load.
- **The compose pin is intentionally unresolvable for a window.** US-004 sets `1.1.0`; US-002
  publishes it. The fragments state the sequencing, and `tests/test_compose_fragments.py` pins the
  literal so the pin cannot silently lag the next release either.
- **The announcement is mechanical because a human line cannot be asserted — and the mechanism is
  narrow because `publish` is the highest-privilege job in the file.** Emitting the docstring
  entry from the tagged tree and reading it back in the same job is the discipline the `contract:`
  line already follows. Ruling 31a adds the constraints that keep tagged-tree prose inert on the
  way through: POSIX awk in the workflow (no tagged-tree code runs — the `secret-grep` principle
  applied to the release job), a file under `${RUNNER_TEMP}` rather than a multi-line
  `$GITHUB_OUTPUT` (no forged `version=`), `cat` into a notes file rather than a heredoc (no
  command substitution), `--notes-file` rather than `--notes` (no argv surprise), and `grep -F` on
  the way back (no ERE). `gh release edit` after the fact would rewrite a body whose assertion had
  already run.

## Related Documentation

- `kit_tools/specs/archive/feature-forage-contract.md:975–1070` — the executed `v1.0.0` runbook
  (pre-flight, publish steps to watch, four-way sha256, alias digest check, what to record).
- `docs/releases.md` — tag scheme, "What a GitHub Release means", "Withdrawn tags", "When something
  goes wrong"; `kit_tools/docs/GOTCHAS.md` § "A cold-cache publish fails its own parity gate".
- `contract/GOVERNANCE.md` — bump procedure step 7 (the announcement obligation, now discharged by
  the docstring entry), "Two semvers", "Consumers" (the vendoring procedure Poppy follows).
- `docs/configuration.md` § "Credential handling for `VALKEY_URL`" (the template) and § "Deployment
  posture"; `kit_tools/docs/DEPLOYMENT.md`, `kit_tools/docs/CI_CD.md`, `kit_tools/docs/API_GUIDE.md`
  (release-state and anchor statements).
- `docs/bootstrap-notes.md` § "Pin record" (the table shape the handoff record copies).
- Poppy `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (F10 + the cross-repo gate); the epic wrapper's rulings
  12, 15, 20, 24, 31, 32 and its resolution map.

## Refinement Notes

**Priority.** All four stories are P2 because the epic ranks spec 5 as P2: the P1 MVP is specs 1–4
(reliable search plus telemetry, autonomously executable). Within this spec the ordering is
`execution_order`, not priority — docs and plumbing precede the cut because everything the cut
depends on must be in the tagged tree, and the record follows the cut because it needs the digest.

**What the validate-epic pass changed (2026-09-14).** US-004 is new (ruling 20): the announcement
obligation the epic assigned to the `v1.1.0` Release body had no mechanism, `contract_smoke.py`
could not verify a weights-loaded release image, the compose fragments were still at `0.9.3-rc`, and
both secret greps were blind to the one new credential. US-001 was re-scoped: the variable rows
moved to specs 1 and 2, the `tests/test_governance_docs.py` claim (which binds none of the files
this story edits) was replaced by the two modules that do, and credential handling, data flow, the
bad-key path and the spend consequence were added. US-002 and US-003 now name every command, the
tagged checkout, both smoke modes, the credential-free pull, and the anchor in the handoff record.

**What round 2 changed (2026-09-14, rulings 31–32).** US-004(a) no longer offers a choice of
mechanism: the reviewers who read the `publish` job found that a `scripts/` helper would execute
tagged-tree code in the one job holding write tokens, that a multi-line `$GITHUB_OUTPUT` is an
output-forgery vector against the semver-validated `version`, that the unquoted heredoc would
evaluate backticks and `$(` inside the entry, and that a `grep -E` on a line starting with `*` is
malformed — so the mechanism is POSIX awk, a `${RUNNER_TEMP}` file, `--notes-file` and `grep -F`,
and both the unit tests and US-002's pre-flight run the real program before the tag. US-004(b)'s
healthy mode now waits for `status: healthy` instead of the first 200, and the edge case that would
have let a wrong-flag re-run count as verification is gone. The fan-outs reviewers found unowned
are named line by line (the smoke caveats, the secret-pattern copies, the compose literal,
GOVERNANCE step 7, the stale `contract: 1.1.0` example). US-002's pre-flight now proves US-001 and
every part of US-004 landed and makes the Overview's "confirms by grep" claim true for the four
sibling-owned rows. US-003 runs the image once with a placeholder key at zero spend, carries the
spend posture across the repo boundary in the handoff table, and owns the suite-count bookkeeping.

**Why US-004 stays one story.** Two reviewers proposed splitting it three or four ways. The split
would cost a re-cut of `execution_order`, the epic's decomposition table and its resolution map for
no gain in verification: each part already has its own labelled hints, its own criteria and its
own test module, so a red in (a) is visible as (a) and hides nothing at the criterion level; the
four parts share one constraint (all in the tagged tree before US-002) and land in one PR because
the compose pin is unresolvable until the cut and should not sit on `main` alone. The story is one
deliverable — "the tree is ready to tag" — verified four ways.

**Why the compose pin moves before the tag.** A pin that lands after the release is the state the
repo was in for `v1.0.0` — recorded as debt in four documents and never done. Pinning `1.1.0` in the
tagged tree, with the fragments stating the sequencing and a test pinning the literal, makes the
next release's bump a one-line, test-enforced edit.

The handoff record (US-003) is what opens the cross-repo gate for Poppy — the epic is not done until
Poppy can pin.

## Clarifications

_None outstanding._

## Open Questions

_None (session_ready)._
