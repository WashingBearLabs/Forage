# Contract governance

How `contract/openapi.yaml` and `CONTRACT_VERSION` are versioned, what each class of
change costs the consumer on the other end, and the rulings this repository has already
recorded so nobody has to re-derive them. Written by `feature-forage-contract` US-003.

The promise every rule below serves:

> A consumer written against contract `X.Y.Z` keeps working on any later `X.*.*`, and is
> expected to **refuse to activate** on a major mismatch rather than guess.

Classifying a change is therefore always the same question asked three ways: does it move
a wire byte, can a consumer written against today's version still parse and act on every
response, and does every name it already knows still mean what it meant?

---

## What this document governs

| Artifact | Where | Generated? |
|---|---|---|
| `CONTRACT_VERSION` | `pipeline/contract.py` | no — hand-bumped, and the only hand-edit in this list |
| The frozen document | `contract/openapi.yaml` | **yes** — `uv run python -m scripts.export_contract` |
| The trust anchor | `contract/openapi.yaml.sha256` | **yes**, same command |
| The drift check's committed failure case | `tests/fixtures/contract/unregenerated_openapi.yaml` | **yes**, same command |
| Schema fixtures | `tests/golden/contract_X_Y_Z.json` | by hand, one per contract version |
| What the running service serves | `/openapi.json` `info.version`, `/health`'s `contract_version` | from `CONTRACT_VERSION` at import |

The current contract version is **1.1.0**. *(That sentence is checked against
`pipeline/contract.py` by `tests/test_governance_docs.py`; a bump that leaves it stale is
a red test, not a stale doc.)*

**Not** governed here, and deliberately independent:

- the **image tag** — [`docs/releases.md`](../docs/releases.md) owns it (see "Two semvers,"
  below);
- `pyproject.toml`'s `version`, which is packaging metadata nothing in the release path
  reads;
- `sanitizer_revision`, a mechanically-derived hash of pipeline *behaviour*
  (`pipeline/sanitizer_revision.py`). It rotates on its own schedule, it is not a semver,
  and it is not a contract change — `kit_tools/docs/GOTCHAS.md` carries its rotation
  history and the rule for taking one deliberately.

---

## Two semvers, independent — and the mapping is mechanized

Forage publishes two version numbers that move for different reasons:

| | Moves when | Example |
|---|---|---|
| **Image tag** (`ghcr.io/washingbearlabs/forage:X.Y.Z`) | anything ships — a bug fix, a dependency bump, a doc change in the image | `v1.0.0` |
| **`CONTRACT_VERSION`** | the wire shape changes, by the rules below | `1.1.0` |

They are not expected to agree and usually will not. **The first release cut after this
spec is image `v1.0.0` serving contract `1.1.0`** — a 1.0 image because the repository is
publishing a frozen, sha256-anchored contract for the first time, and a contract still at
1.1.0 because freezing a document changes no wire byte. (That tag is cut by this spec's
**US-004**, whose job it is to land the in-image `COPY` and the Release assets first; a
`v1.0.0` tagged before them would ship a contract-less release that nothing can re-cut.)

A human line in a release note claiming "this image serves contract 1.1.0" would be the
last unmechanized integrity claim in the release path, so it is not a human line. The
`publish` job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) does three
things on a `v*` tag:

1. **Read the contract version from the tagged tree** — parses `CONTRACT_VERSION` out of
   `pipeline/contract.py` at the tagged commit. No version literal exists anywhere in that
   job's shell; `tests/test_ci_workflow.py` asserts the absence.
2. **Create the GitHub Release** — whose body carries the line `contract: <version>`,
   interpolated from step 1.
3. **Assert the Release body advertises the tagged tree's contract version** — reads the
   published body back with `gh release view` and fails the run on an anchored regex miss.

Emitting and asserting in the same job is the point: the claim and its check cannot drift
apart, because one value feeds both. `tests/test_ci_workflow.py::TestReleaseContractMapping`
guards the shape of all three steps, including that they fire on the service lane's `v*`
tags only and never on the companion image's `searxng-v*`.

**A withdrawn image tag does not withdraw a contract version.** When a publish run fails
its own layer-identity gate, the rule (set when `v0.9.2-rc` did exactly that) is to
withdraw the git tag *and* delete the GHCR package version —
[`docs/releases.md`](../docs/releases.md), "Withdrawn tags". Contract versions are not
withdrawn and never reused: the number describes a wire shape, and a shape that was
published once stays described. Re-cut the image, not the contract.

---

## Classifying a change

Work down the table; the first row that matches wins — with one boundary exception: before the freeze boundary (§ The freeze boundary), a documentation-only change that a row would classify as PATCH is **no bump**; the section below owns that call.

| Class | The change | Consumer cost |
|---|---|---|
| **MAJOR** | A response field is removed, renamed, or changes type. A status code a client observes changes. An existing enum member's **meaning** changes. A request field becomes required, or an accepted request value stops being accepted. A route disappears. | Breaks. The consumer is expected to refuse to activate. |
| **MINOR** | A new **optional** request field or parameter. A new response field. A new member of a vocabulary consumers bucket unknowns for (`degraded_reasons`, `omitted_by_reason`, `capabilities`, the error codes). A new route. An expedited security tightening shipped with a compatibility window (below). | None, if the consumer ignores what it does not know. |
| **PATCH** | The **published document** moves but the wire does not: a description fix, a corrected example, a tightened schema annotation, a dependency bump that reflows the render. | None. Regenerate; do not re-read the code. |
| **no bump** | Neither the wire nor the published document moves: tests, comments, internals, a refactor behind an unchanged surface. | None, and nothing to announce. |

Two mechanical helpers decide the top two rows for you, so the judgement call is smaller
than it looks:

- **"Does it move a wire byte?"** — `tests/test_contract_errors.py` and
  `tests/test_contract_metrics.py` compare each emission site's serialized bytes against
  its mirror model. If a change moves a wire byte, one of them goes red and tells you
  which site.
- **"Does it move the document?"** — `tests/test_contract_export.py` regenerates
  `contract/openapi.yaml` in memory and diffs it against the committed file. If it moves
  the document, that test is red until you run the exporter.

A change that makes **neither** test red is a no-bump change, whatever else it touched.

### The freeze boundary, and why "pre-freeze / post-freeze" appears below

Before this spec, `contract/openapi.yaml` did not exist: the document was generated
per-request by FastAPI and no consumer held a copy to diff. A documentation-only change
therefore moved nothing anybody had — hence ruling (a)'s *no bump*.

From the first release that publishes the document as an artifact (image `v1.0.0`, serving
contract `1.1.0` — US-004), the document **is** an artifact, with a committed sha256 anchor
and consumers who vendor it. A documentation-only change now moves bytes those consumers
can see and must re-vendor, which is exactly what PATCH is for.

So: *pre-freeze, a doc-only change is no bump; post-freeze it is a PATCH.* That boundary is
crossed once, by US-004, and this paragraph is the whole of the difference.

---

## The six worked examples

The questions this document must answer without a human in the loop. Every row is decided
by the table above; the "Why" column shows which rule fired.

| # | The change | Class | Why | What you do |
|---|---|---|---|---|
| 1 | **Remove a response field** (say, `sanitizer_revision` off the `/extract` 422 body) | **MAJOR** | A field the consumer reads disappears. Poppy hard-rejects that exact body without it, which is what a major mismatch is *for*. | Bump major, regenerate, add `tests/golden/contract_2_0_0.json`, announce. |
| 2 | **Add an optional request parameter** | **MINOR** | Old callers omit it and get today's behaviour; nothing they send stops being accepted. | Bump minor, regenerate, add the new golden, note it in the Release body. |
| 3 | **Fix a description** (a typo, a clarified sentence) | **PATCH** post-freeze / **no bump** pre-freeze | No wire byte moves; the published document does. | Bump patch (post-freeze), regenerate — the drift check is red until you do. |
| 4 | **Change an enum member's meaning** (e.g. `degraded` stops meaning "PromptGuard absent" and starts meaning "cache absent") | **MAJOR** | The name is unchanged, so no consumer can detect it — the most dangerous change in this table and the reason "meaning" is called out separately from "shape". | Bump major, regenerate, announce loudly. Prefer adding a *new* member (MINOR) over redefining an old one. |
| 5 | **A schema-doc tightening with no wire-byte change** (an enum rendered where a bare `string` was, a field documented as required that always was) | **no bump** pre-freeze / **PATCH** post-freeze | `model_json_schema()` moves, the wire does not — the parity tests prove which. Ruling (a) is this case, decided pre-freeze. | Regenerate the contract **and** the golden fixture in the same commit; bump patch post-freeze. |
| 6 | **An urgent security tightening** (a value that used to be accepted must stop being accepted) | **expedited MINOR, with a compatibility window** — never a silent break | Stopping acceptance of something is a MAJOR-shaped change; shipping it as a silent MINOR is how consumers break in production on a patch upgrade. The window is what makes the MINOR honest. | See the next section. |

### Example 6 in full: expedited security changes

An urgent fix does not get to skip the rules; it gets a faster lane through them.

1. **Ship the tightening compatibly where that is possible.** The new, stricter behaviour
   arrives alongside the old one — a new field, a new refusal code, an opt-in flag — so a
   consumer upgrading on a MINOR is not broken by the upgrade itself. That is the
   expedited MINOR.
2. **State the compatibility window in the Release body**: what still works, until when.
   At minimum the window spans one further minor release; a dated deadline is better.
   "It will change soon" is not a window.
3. **The removal of the old behaviour is the MAJOR**, cut when the window closes, and it
   is announced again then.
4. **If the vulnerability cannot be fixed compatibly, it is a MAJOR — cut immediately, not
   an expedited MINOR wearing a disguise.** A break announced as a break is recoverable;
   a break shipped as a MINOR is a consumer refusing traffic at 3am with a version number
   that promised it would not.
5. Either way the reporting channel, the supported versions and the posture are in
   [`SECURITY.md`](../SECURITY.md).

---

## Recorded rulings

Five rulings this epic already made, kept here so the next change re-reads them instead of
re-litigating them. Each cites its source.

### (a) The documentation pass does not bump the contract

**Ruling:** US-001's and US-005's work — the per-status error mirrors, the typed `/metrics`
body, the app metadata — is a **no bump**. The contract stays at `1.1.0`.

**It holds only because the design provably changes zero wire bytes.** The round-1 design
(emit every error through one envelope model) would *not* have qualified: it dropped
`sanitizer_revision`, homogenized four distinct body shapes and omitted five refusal
codes. The corrected design mirrors each emission site and the parity tests hold the
mirror to it, which is what turns "documentation only" from a claim into a measurement.

**Source:** `kit_tools/specs/feature-forage-contract.md`, *Clarifications → Session
2026-09-02* ("does the typing/envelope pass bump the contract? → No — and round 2 tightened
the ruling's premise: it holds only because the design now provably changes zero wire bytes
(parity tests)"), and the US-001 / US-005 *Implementation Notes* in the same file.

### (a2) The documented-but-unreachable `/extract` 413

Added at US-001's verification, 2026-09-11, and **load-bearing for codegen consumers** —
they read statuses, not descriptions.

**What is true:** `DocumentSizeLimitMiddleware` emits
`413 {"error": "content_too_large", "reason": ...}`, and on `/extract` **no HTTP client can
ever observe it**. The middleware refuses by raising out of the ASGI `receive` callable;
FastAPI's form parsing wraps that into `HTTPException(400, "There was an error parsing the
body")` *inside* the middleware's own downstream call, so its `except` never runs. Measured:
a multipart or urlencoded over-sized body gets **400**, a JSON one gets **422**. The byte
cap itself works — nothing over the limit is spooled or extracted.

**Ruling, current state — no bump.** The contract declares **both** statuses on `/extract`:
the 400 that reality sends, and the 413 whose description says it is shadowed. Documenting
a declared-but-unreachable status alongside the reachable one changes no wire byte, so it
carries no bump. A generated client will produce a 413 branch it never enters; that is the
honest shape of the service today, and better than a document that silently promises the
400 does not exist.

**Ruling, the future fix — a wire change, and by this document's table a MAJOR.** Correcting
400 → 413 moves a status code a client observes, which is the MAJOR row. It belongs to
whoever owns the next contract bump, never to a passing refactor. Do not "fix it while
you're in there".

**Source:** `kit_tools/docs/GOTCHAS.md`, *"An over-sized upload gets 400, not the 413 the
size middleware emits"* (the three measurements and the two tests that pin them), and
`kit_tools/specs/feature-forage-contract.md`, US-001 *Implementation Notes* → "⚠ Finding:
the spec's emission map is one shape short, and its 413 is unreachable".

### (b) New omission / degraded enum members are MINOR — with an announcement obligation

**Ruling:** adding a member to `degraded_reasons`, `omitted_by_reason`, `capabilities` or
the error-code vocabulary is a **MINOR**. The first consumer (Poppy) buckets reasons it does
not recognise rather than failing on them, so an added member costs a current consumer
nothing.

**The obligation attached to it:** when the new member is a **security-block reason** — a
reason that means content was withheld or a fetch refused — it must be **announced in the
Release body**, not merely added. A consumer bucketing it as "unknown" is behaving
correctly and silently under-reporting a refusal to its own users; the announcement is what
lets them promote it deliberately.

**Mechanically**, the vocabularies in `pipeline/contract.py` are frozensets derived from
their own `Literal` aliases (`frozenset(get_args(...))`), so a new member updates the type
and the set atomically and cannot half-land. It *will* move `model_json_schema()` and the
document: regenerate the contract and add the golden fixture in the same commit.

**Source:** `kit_tools/specs/feature-forage-contract.md`, US-003 *Implementation Hints*
ruling (b), and US-001 *Implementation Notes* (the derived-Literal composition).

### (c) Golden fixtures are retained, never replaced

**Ruling:** every historical `tests/golden/contract_X_Y_Z.json` stays in the tree.
`contract_1_0_0.json` did not go away when `1.1.0` landed, and `1.1.0` will not go away
when the next version lands.

They are the only artifact that says what an *older* contract looked like in a form a test
can read. A replaced fixture makes "what changed between 1.0.0 and 1.1.0?" a git-archaeology
question instead of a diff of two committed files.

Note what the fixtures pin: `model_json_schema()`, which is strictly more than the wire —
it moves for description and enum-rendering changes too. A documentation-only change
regenerates the *current* fixture (ruling (a)); a wire change adds a **new** file beside it.

**Source:** `kit_tools/specs/feature-forage-contract.md`, US-003 *Implementation Hints*
ruling (c); the fixture semantics are recorded in US-001 *Implementation Notes* and
`kit_tools/testing/TESTING_GUIDE.md`.

### (d) `/retrieve`'s refusal `reason` echoes resolved private IPs — a documented caveat

**Ruling:** the wire is **unchanged** by this epic and this is a documented caveat, not a
defect to fix in passing. Epic 5 owns any change to it.

**What a consumer must know:** a `private_ip` refusal reports
`URL '<url>' resolves to private IP <ip>` (`url_validator.py`), and `pipeline/orchestrator.py`
passes that text through to the 422 body's `reason` field verbatim. Anyone who can call
`/retrieve` can therefore use Forage as a **DNS-resolution oracle for the network Forage
sits on**: ask it to fetch a name, read the internal address out of the refusal.

Under Forage's stated posture — no authentication, private network only — the caller
already reaches that network, so the echo gives them nothing new. **The caveat is for
deployments that break the posture**: anyone exposing Forage beyond a private network is
exposing internal name resolution with it. Redaction is a wire change (a `reason` string
consumers may parse) and belongs to a contract bump, which is why it is not in this
zero-wire-byte epic.

**Source:** `kit_tools/specs/feature-forage-contract.md`, US-003 *Implementation Hints*
ruling (d) (round-2 security note); the emission path is `url_validator.py:174` →
`pipeline/orchestrator.py`'s `private_ip` raise sites. Repeated for reporters in
[`SECURITY.md`](../SECURITY.md).

---

## Bumping the contract: the procedure

1. **Classify** with the table above. If it is a no-bump change, skip to 4.
2. **Bump `CONTRACT_VERSION`** in `pipeline/contract.py`, and extend its docstring's version
   list with one line saying what moved and whether a consumer comparing MAJOR keeps
   working.
3. **Add the golden fixture** `tests/golden/contract_<X>_<Y>_<Z>.json` — a new file; never
   edit an older one (ruling (c)).
4. **Regenerate**, in the same commit:

   ```bash
   uv run python -m scripts.export_contract
   ```

   It writes `contract/openapi.yaml`, `contract/openapi.yaml.sha256` and
   `tests/fixtures/contract/unregenerated_openapi.yaml`. They are consistent only as a set —
   commit all three together, and never hand-edit any of them.
5. **Run the suite.** `tests/test_contract_export.py` is red until step 4 is done, and the
   parity tests are red if you moved a wire byte you thought you hadn't.
6. **Update what states the version in prose**: this document's "current contract version"
   line, `CLAUDE.md`'s invariant 4, `README.md`'s HTTP-surface section.
7. **Announce**: the next Release body carries `contract: <version>` mechanically; a MAJOR,
   or a MINOR under ruling (b)'s announcement obligation, also gets a sentence saying what
   changed and what a consumer must do.

---

## Consumers

**Get the contract from whichever artifact you already consume, and verify it against the
committed anchor — never against another copy.**

`contract/openapi.yaml.sha256` is the trust root: a `sha256sum`-format line naming the
basename, so it verifies identically from a repository checkout, from a directory of
downloaded Release assets, or from inside the image. Registry tags and Release assets are
mutable; a checksum in the git history at a tag is not.

```bash
# From a checkout, or beside downloaded assets — same command either way.
sha256sum -c openapi.yaml.sha256
```

| Route | How you get it |
|---|---|
| The git tag | `contract/openapi.yaml` + `openapi.yaml.sha256` at `v<version>` |
| Release assets | `gh release download v<version> --pattern 'openapi.yaml*'` |
| In-image | `docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml` |

All three are byte-identical by construction, and two of them are checked on every
release rather than promised:

- the **in-image** copy is read out of the candidate image by `contract_smoke.py` in CI's
  `smoke` job, hashed against the committed anchor, and its `info.version` compared with
  the `contract_version` the running container reports — so the document an image ships
  and the contract it serves cannot disagree;
- the **Release assets** are downloaded back out of the API by the `publish` job, their
  anchor compared against the one committed at the tag, and then verified with the same
  `sha256sum -c` you would run.

The in-image `contract/` directory also carries this governance document, deliberately:
the rules travel with the artifact they govern. It is not part of any checksum — the
anchor covers `openapi.yaml` alone.

**Vendoring procedure** (the shape Poppy's own copy follows):

1. **Pick a tag**, not `latest`. Everything below is "from the same tag" — a document
   from one tag verified against another tag's anchor proves nothing.
2. **Fetch the document and the anchor** by any one of the three routes. They may come
   from different routes, as long as both come from the same tag; the anchor from the
   git tag is the strongest choice, because a registry tag and a Release asset are both
   mutable and a commit is not.
3. **Verify before you look at the content**:

   ```bash
   sha256sum -c openapi.yaml.sha256   # from the directory holding both files
   ```

4. **Commit the verified copy and the anchor together.** Two files, one commit: an
   anchor committed without its document, or a document without its anchor, is a
   re-vendoring nobody can check afterwards.
5. **Re-run the verification in your own test suite.** This is the step that matters
   most and the one most often skipped: without it, a future re-vendoring that forgot
   step 3 lands silently, and the copy you are shipping against is whatever someone
   pasted in.
6. **Record the tag** you vendored from beside the files, so "which contract is this?"
   is answerable without a registry lookup.

At activation, compare **contracts, not revisions** (below) — and treat the contract as
the compatibility statement, not the image tag: image `v1.0.0` serves contract `1.1.0`,
and the two move for different reasons.

At activation, compare **contracts, not revisions**: `/health` carries both
`contract_version` and `sanitizer_revision`, and only the first is a compatibility
statement. `sanitizer_revision` has deliberately diverged between Forage and Poppy's
in-tree copy and says nothing about wire compatibility.
