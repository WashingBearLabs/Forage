<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, tech-stack
  required_sections: []
  skip_if: never
-->
# DECISIONS.md

> **TEMPLATE_INTENT:** Record architectural decisions and their rationale. Explains the 'why' behind technical choices.

> Last updated: 2026-09-23
> Updated by: Copilot (hardening-release US-004)

This file records significant architectural and technical decisions.

**This log was reconstructed on 2026-09-13.** No ADR file existed before it; the decisions below
were scattered across `CLAUDE.md`'s six hard invariants, the Implementation and Refinement Notes of
the four archived specs under `kit_tools/specs/archive/`, `docs/bootstrap-notes.md`,
`contract/GOVERNANCE.md`, `kit_tools/docs/GOTCHAS.md` and code docstrings. Every entry cites the
written source it came from and carries the date the decision was made or first recorded there —
an explicit date in the source where one exists, otherwise the first commit that introduced the
governing text, cited by hash. Where a source records only the chosen path, "Options Considered"
says so rather than inventing alternatives.

`CLAUDE.md` remains the **enforcement point** for the six invariants (marked below); this file
carries the reasoning and the alternatives. Add new decisions here first and promote to
`CLAUDE.md` only if they turn out to be load-bearing. Related: `kit_tools/arch/CODE_ARCH.md`,
`kit_tools/arch/SECURITY.md`, `kit_tools/arch/SERVICE_MAP.md`,
`kit_tools/arch/patterns/ERROR_HANDLING.md`, `kit_tools/arch/patterns/LOGGING.md`,
`kit_tools/docs/CI_CD.md`, `kit_tools/docs/GOTCHAS.md`.

| `CLAUDE.md` invariant | Entry |
|---|---|
| 1 — never depend on Poppy | 2026-09-07: Forage is standalone |
| 2 — no secret enters the image build | 2026-09-07: Zero build arguments; 2026-09-08: Gated public publish |
| 3 — flat module layout | 2026-09-07: Flat layout, `retrieval_app.py` keeps its name |
| 4 — a response-shape change is a contract change | 2026-09-01: Versioned contract; 2026-09-11: Frozen, anchored contract |
| 5 — degradation is loud | 2026-09-01: Honest health |
| 6 — never log a credential | 2026-09-01: Closed log vocabularies |

Entries are chronological. The first two pre-date the extraction; their dates are the Poppy-era
commits `git filter-repo` preserved in this repository's history.

---

## Decision Log

### 2026-03-30: SSRF defence follows redirects manually, revalidating and DNS-pinning every hop

**Status:** Accepted (predates this repository; dated from the rewritten Poppy-era commit)

**Context:**
`/retrieve` fetches arbitrary URLs for an LLM agent. A validated hostname can re-resolve to a
private address between check and connect (DNS rebinding), and any redirect can point somewhere
the first URL could not.

**Options Considered:**

1. **httpx's automatic redirects** (`follow_redirects=True`) — the client re-resolves DNS and no
   hop is revalidated. Rejected.
2. **A manual hop loop with per-hop validation and connection pinning** — chosen.

**Decision:**
`pipeline/stage5_url_audit.py` opens `httpx.AsyncClient(follow_redirects=False)` and follows at
most 5 redirects by hand. Each hop runs `url_validator.validate_url` (http/https only; `localhost`,
`.local` and `.localhost` refused; private and reserved ranges refused for *every* resolved
address, as are the five unwrapped embedded-IPv4 classes — IPv4-mapped, 6to4, Teredo's client
field, NAT64 inside `64:ff9b::/96` and IPv4-compatible inside `::/96`, the two masked unwraps
prefix-guarded so public IPv6 stays fetchable; unparseable addresses treated as private; request blocklist merged with `seed_blocklist`),
then pins the connection to the validated IP by rewriting the netloc and sending the original
`Host` plus `sni_hostname`, so TLS verification stays on. Bodies stream under a 10 MiB cap with a
Content-Length fast reject; timeout 30 s; `domain_changed_on_redirect` costs 0.1 trust score.

**Rationale:**
The module docstring: "DNS rebinding protection via manual redirect following." Pinning means a
second resolution can never occur between check and connect, and every hop gets the audit the
first URL got.

**Consequences:**
Redirect-hop bodies are never buffered; `TooManyRedirectsError` surfaces as the generic
`fetch_error`. The defence is depth behind network placement, not a substitute for it. The
`private_ip` refusal echoes the resolved address — a documented caveat (`contract/GOVERNANCE.md`
ruling (d)).

**Source:** `pipeline/stage5_url_audit.py` docstring and `sni_hostname` pinning, commit `be51560`
(2026-03-30); hardening `01c95e8` (2026-03-31), streaming cap `3ca5fea` (2026-06-24);
`url_validator.py`; tests `tests/test_stage5_url_audit.py`, `tests/test_url_validator.py`.

---

### 2026-08-05: Untrusted PDF parsing runs in a spawned, kernel-limited child; `/extract` ships gated off

**Status:** Accepted (predates this repository; dated from the rewritten Poppy-era commit)

**Context:**
`POST /extract` accepts uploads from callers that cannot be trusted; pypdf on hostile input is a
CPU and memory hazard in a container shared with the API process and the PromptGuard model.

**Options Considered:**
Not recorded; the source gives only the chosen design and its reason.

**Decision:**
`pipeline/pdf_subprocess.py` runs pypdf in a `multiprocessing` spawn-context child under
`RLIMIT_CPU` (20 s), `RLIMIT_AS` (384 MiB, Linux only) and an `ITIMER_REAL` wall clock (90 s).
Results cross a length-framed JSON pipe carrying one of a closed set of status codes; every
abnormal outcome ends in SIGKILL plus reap. The ceilings in `pipeline/extraction_limits.py` are
hard maxima apart from three raisable keys: `classification_concurrency` (1–8 under the
memory rule and boot WARNING), `child_address_space_bytes` (128–512 MiB, widens the
untrusted-PDF child's sandbox) and `admission_queue_depth` (0–4); see the corrected
2026-09-19 sizing decision below. The route is a release gate: `extract_route_enabled:
false` by default answers 404 (invisible, not merely refused); admission is bounded (concurrency 1,
queue depth 1, `429 busy`); uploads always run as `UNTRUSTED` with fail-closed PromptGuard.

**Rationale:**
The docstring: "Killable, spawn-isolated PDF parsing for untrusted uploads." Closed IPC codes mean
a parser error cannot transport document text back to the parent. macOS cannot reliably account a
spawned interpreter's shared mappings under `RLIMIT_AS`, so local development relies on parent
supervision.

**Consequences:**
Enabling `/extract` needs a restart and there is no authentication in front of it. The intended
413 for an oversized upload is shadowed by FastAPI's form parser (see the frozen-contract entry).

**Source:** `pipeline/pdf_subprocess.py`, `pipeline/extraction_limits.py`, `config.yaml`, commit
`1df1988` (2026-08-05); `docs/configuration.md` `extract_route_enabled` row;
`kit_tools/arch/SECURITY.md` "Input validation".

---

### 2026-09-01: Honest health — `/health` is always 200 with the truth in the body; degradation is loud everywhere

**Status:** Accepted — `CLAUDE.md` invariant 5

**Context:**
An earlier version reported `healthy` regardless of whether the PromptGuard model was loaded. A
fail-closed consumer discarded every standard-tier search result, which read to users as "no
matches." It ran unnoticed for **nine days** in production.

**Options Considered:**

1. **A non-200 status code for degraded** — rejected; the consumer's healthcheck is a bare
   `curl -f`, and `CLAUDE.md` forbids regressing the contract "in the name of a cleaner status
   code."
2. **Always 200, machine-readable truth in the body** — chosen.

**Decision:**
`/health` always returns 200; the body carries `status`, a typed `degraded_reasons` vocabulary
(`promptguard_unavailable`, `cache_unavailable`), `promptguard_loaded`, `cache_connected`,
`cache_backend`, `sanitizer_revision`, `contract_version`. Never make a missing model, an
unreachable cache or a refused fetch look healthy. The same rule runs through the pipeline: model
absent with fail-closed yields `unavailable_blocked` and omitted search results; a cache outage
is a miss plus `degraded`; quarantine is a 200 with a content-free body; only
configuration-validation errors refuse to boot. The break-glass variable
`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` (exact value `1`) makes only `capabilities` lie and
logs a WARNING each boot.

**Rationale:**
`kit_tools/arch/CODE_ARCH.md` principles 1 and 2: "Report, don't decide" and "Fail loud, never
silently degrade." The honest-health contract is the fix for the nine-day incident.

**Consequences:**
Read the body, never the status code. CI's `smoke` job runs the token-less image and asserts the
degraded contract through `contract_smoke.py`, so a `/health` that claims `healthy` without
weights is a red gate. An unlisted degraded reason fails response validation with a 500 rather
than leaking through. `/metrics` `model.fetch_in_progress` and `retries_scheduled` separate the
three states that look identical as `promptguard_loaded: false`.

**Source:** `degraded_reasons` introduced in commit `93acb6a` (2026-09-01); `CLAUDE.md`
invariant 5 (recorded 2026-09-07); `kit_tools/docs/GOTCHAS.md` "PromptGuard model absent";
`pipeline/contract.py`; `kit_tools/arch/patterns/ERROR_HANDLING.md`.

---

### 2026-09-01: Closed log vocabularies — never log a credential-bearing value

**Status:** Accepted — `CLAUDE.md` invariant 6

**Context:**
`VALKEY_URL` may carry a password; `HF_TOKEN` and `FORAGE_MIRROR_TOKEN` are secrets; exception
text from redis, `huggingface_hub` or `oras` can embed any of them.

**Options Considered:**

1. **Interpolate the URL or `str(exc)` into failure logs** — rejected.
2. **Map every failure to a fixed reason string** — chosen.

**Decision:**
`cache._closed_vocabulary_reason` maps failures to `timeout`, `connect_failed` or
`operation_failed`; startup logs the backend literal, never the URL; the selection path hands the
URL straight to `ValkeyStorage` without inspecting it. `model_fetcher.py` uses closed reason and
outcome vocabularies, redacts references, never logs `oras` output and passes the mirror token on
stdin. `docker-entrypoint.sh` is `exec "$@"` and prints nothing. Asserted by
`tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret` and
`tests/test_app.py::test_no_selection_path_logs_the_valkey_url`.

**Rationale:**
From `_select_cache_storage`: "the value may carry a password, and a parse attempt at this layer
would be a second place for one to escape into a log line."

**Consequences (recorded, not decided):**
Nothing calls `logging.basicConfig()`, so the root logger stays at WARNING and every
`logger.info` — the whole success narrative of a weights acquisition — is invisible in
`docker logs`; `uvicorn --log-level info` does not change that. `/metrics` is the observability
surface and the retry line was raised to WARNING for that reason. Configuring logging was
deliberately *not* done in passing because it changes output for every lane at once.

**Source:** `cache.py` `_closed_vocabulary_reason`, commit `93acb6a` (2026-09-01); `CLAUDE.md`
invariant 6; `retrieval_app._select_cache_storage` docstring; `docker-entrypoint.sh`;
`kit_tools/docs/GOTCHAS.md` "Nothing configures logging" (2026-09-10);
`kit_tools/arch/patterns/LOGGING.md`; `kit_tools/docs/CONVENTIONS.md`.

---

### 2026-09-01: The response contract is versioned with golden fixtures; consumers refuse on a major mismatch

**Status:** Accepted — `CLAUDE.md` invariant 4

**Context:**
Poppy consumes Forage's wire shapes and hard-validates some (its client rejects an `/extract` 422
body without `sanitizer_revision`). A shape change a consumer cannot detect is worse than a break
it can.

**Options Considered:**
Not recorded; the source gives only the rule and the promise it serves.

**Decision:**
`pipeline/contract.py` carries a hand-bumped `CONTRACT_VERSION` (1.0.0 froze the Epic 1 surface;
1.1.0 added the additive `/health.cache_backend`, a MINOR, on 2026-09-10; 1.2.0 added the additive
`SearchResult.content_kind` and `SearchResult.date` plus the `search_unavailable` error code, a
MINOR, on 2026-09-15; 1.3.0 opened `hardening-search-sanitization`'s contract window on
2026-09-20, declaring `OMIT_BLOCKED_URL` and bounding `SearchResult.engine` to 64 characters,
also a MINOR), served as
`/health.contract_version` and `/openapi.json` `info.version`. Any change to a response shape
means: classify, bump, add a new golden fixture under `tests/golden/` (older ones retained, never
edited — ruling (c)), and note it for the consuming repo. `contract.py` lives under `pipeline/` on
purpose, so it is a hashed `sanitizer_revision` source.

**Rationale:**
`contract/GOVERNANCE.md`: "A consumer written against contract X.Y.Z keeps working on any later
X.*.*, and is expected to refuse to activate on a major mismatch rather than guess."

**Consequences:**
Two independent semvers — image tag (`v1.2.1`, verified 2026-09-23) and contract
(`1.3.0`, first published by the defective `v1.2.0` on 2026-09-23) move for
different reasons. See `docs/releases.md` for the completed recovery and withdrawal;
`pyproject.toml`'s version is inert. A withdrawn image tag never withdraws a contract version.
`.github/pull_request_template.md` carries the short-form checklist.

**Source:** `pipeline/contract.py` and `tests/golden/contract_1_0_0.json`, commit `93acb6a`
(2026-09-01); `tests/golden/contract_1_1_0.json`, commit `865a586` (2026-09-10); `CLAUDE.md`
invariant 4; `contract/GOVERNANCE.md` "Two semvers".

---

### 2026-09-07: Forage is standalone — never import from or depend on Poppy

**Status:** Accepted — `CLAUDE.md` invariant 1

**Context:**
Forage was split out of the Poppy monorepo on 2026-09-07 with `git filter-repo` (47 commits, Poppy
pin `f73e2091`). The 24 service source files came out clean, but `tests/test_orchestrator.py`
imported `FILE_RECALL_FAILURE_MESSAGES` from `poppy.core.abilities.builtin.canvas` to assert that
Forage's document-failure codes were a subset of Poppy's recall vocabulary — one line that made the
suite unrunnable outside the monorepo.

**Options Considered:**

1. **Vendor Poppy's constant into Forage** so the assertion could stay — rejected.
2. **Recognise the assertion as a Poppy-side property and re-home it there**, deleting the import
   and the assertion here — chosen.

**Decision:**
No `poppy` import, ever, and no path to one. The import and assertion were deleted in
`forage-repo-bootstrap` US-004 and re-homed in Poppy. Legacy `POPPY_*` environment names survive
only as aliases behind a `FORAGE_*` primary; no new name contains "poppy".

**Rationale:**
`CLAUDE.md`: "if an assertion needs to see both sides of the boundary, it belongs on the
consumer's side, not here. Forage may define and publish its own vocabulary; it may never reach
across to check what someone does with it."

**Consequences:**
The suite runs hermetically here. Pre-extraction fossils (`USER poppy`, the `poppy-extract-` spool
prefix, `POPPY_RETRIEVAL_LEGACY_CAPABILITY`) remain; they predate the rule.

**Source:** `CLAUDE.md` invariant 1; `docs/bootstrap-notes.md` pin record (2026-09-07); the
import removal in commit `d2b62bb` (2026-09-07).

---

### 2026-09-07: Coexistence — Poppy's in-tree copy stays the deployed source of truth until it pins a published image

**Status:** Accepted (temporary; closes when Poppy's spec 6 lands)

**Context:**
The extraction copied the service; it did not delete Poppy's `services/retrieval/`. Two copies
exist and only Poppy's is deployed.

**Options Considered:**
Not recorded; the source gives only the rule and its reason.

**Decision:**
Until Poppy pins a published Forage image, any fix to the extracted paths on either side is
replayed onto the other by hand, with the Poppy source commit recorded in
`docs/bootstrap-notes.md`'s pin record. `sanitizer_revision` was allowed to diverge deliberately
(six rotations here; Poppy still derives the split-time value), so cross-repo work must
**compare contracts, not revisions**. Poppy's Dockerfile still carries `ARG HF_TOKEN`; never push
an image built from it.

**Rationale:**
`docs/bootstrap-notes.md`: Poppy's copy "remains the deployed source of truth and the two copies
coexist"; `contract_version` is "the field with cross-repo semantics."

**Consequences:**
Every Poppy hotfix under `services/retrieval/`, `config/searxng/` or `tests/retrieval/` is a
replay obligation here. Poppy's re-extraction on a revision change is owned by spec 6.

**Source:** `CLAUDE.md` "Coexistence with Poppy (temporary)"; `docs/bootstrap-notes.md` (commit
`6aefefc`, 2026-09-07); `contract/GOVERNANCE.md` "Consumers".

---

### 2026-09-07: Spec canonicality — Poppy's copies are canonical, edits flow one way, results are recorded here

**Status:** Accepted

**Context:**
The four feature specs were authored and validated in Poppy, which holds the seven-spec family
context, and copied here at bootstrap. They execute here via `/kit-tools:execute-epic`.

**Options Considered:**
Not recorded.

**Decision:**
Poppy's copies remain the canonical planning record (`status: on-hold` there). Edits flow
Poppy → Forage, one way, at handoff only; re-planning happens in Poppy and is re-copied.
Implementation Notes, ticked criteria and learnings are recorded in this repo's copies, never in
Poppy's originals. `kit_tools/worktree.yaml` is the environment contract the orchestrator reads.

**Rationale:**
One-way handoff avoids two diverging planning records; Poppy has the context re-planning needs.

**Consequences:**
Child specs use Poppy's numbering (its specs 2–5 are `epic_seq` 1–4 here), so "spec 6 owns this"
means the Poppy side owns it. A missing `run_prefix: uv run` in `worktree.yaml` makes the
orchestrator run system Python and report false regressions.

**Source:** `CLAUDE.md` "Spec Execution"; `kit_tools/specs/epic-forage-extraction-forage-side.md`
"Sync rule" (commit `fb9d045`, 2026-09-07).

---

### 2026-09-07: Flat module layout at the repo root; `retrieval_app.py` keeps its name; the `forage/` package rename is deferred

**Status:** Accepted — `CLAUDE.md` invariant 3

**Context:**
`retrieval_app.py`, `models.py`, `cache.py`, `url_validator.py` (later `model_fetcher.py`) sit at
the root; `pipeline/` and `promptguard/` are the only packages — the shape `services/retrieval/`
had in Poppy. The module had already been renamed from `app.py` there to avoid a `sys.modules`
collision with the host application's `app`.

**Options Considered:**

1. **Reorganise into a `forage/` package now** — rejected; three consumers depend on the paths.
2. **Keep the flat layout; defer the rename to a scheduled epic that handles all three together**
   — chosen.

**Decision:**
Do not reorganise. The Dockerfile's `COPY` lines, `pipeline/sanitizer_revision.py`'s
`_REVISION_SOURCES` (hashes by relative path) and the entire test suite (`tests/conftest.py` puts
the root on `sys.path`) depend on the current paths. `retrieval_app.py` keeps its name: the
collision cannot happen here, but the filename is load-bearing for the first two consumers.

**Rationale:**
`kit_tools/arch/CODE_ARCH.md`: "That is a decision, not an accident — it keeps the Dockerfile's
`COPY` lines, `pipeline/sanitizer_revision.py`'s hashed source paths, and the whole moved test
suite working unchanged after the extraction."

**Consequences:**
hatchling needs an explicit module list in `pyproject.toml`; a new top-level module is invisible to
the image until named in a `COPY` line; renaming any `_REVISION_SOURCES` file rotates the revision.

**Source:** `CLAUDE.md` invariant 3 (commit `fb9d045`, 2026-09-07); the rename in commit
`1baa58b` (2026-06-12, rewritten history; `kit_tools/docs/GOTCHAS.md` records it as 2026-08-04);
`pyproject.toml` hatch comment; `tests/conftest.py` docstring.

---

### 2026-09-07: No authentication, by design — network placement is the access control

**Status:** Accepted

**Context:**
All five endpoints, plus FastAPI's `/docs`, `/redoc` and `/openapi.json`, are unauthenticated.
Forage is a natural SSRF target because making outbound requests is its job, and `/docs` is a
working client for it.

**Options Considered:**

1. **An API key, bearer token, allowlist or rate limit** — rejected: "adding a half-auth layer
   would invite exactly the 'it's protected' assumption this section exists to prevent."
2. **Configuration to switch off the docs endpoints** — rejected: "turning the contract off is
   not a substitute for putting the service on a private network."
3. **No auth; private network only; terminate auth in front if needed** — chosen.

**Decision:**
Ship no authentication and no rate limiting beyond `/extract` admission. Run only on a private
network: a Docker bridge network, a loopback-bound host port or a VPN. The compose fragments bind
`127.0.0.1:8020:8020` and publish nothing else, pinned by `tests/test_compose_fragments.py`.
Forage is not a trust boundary; the calling agent's security layer owns every trust decision.

**Rationale:**
`docs/configuration.md`: Forage "defends itself (RFC1918 rejection, DNS-rebinding checks via
manual redirect following, domain blocklists), but those are defence in depth behind your network
boundary, not a substitute for it."

**Consequences:**
Anyone who reaches the port can make Forage fetch arbitrary URLs and read every counter. The
`private_ip` echo makes Forage a DNS oracle for its own network to anyone who can already reach
it — a documented caveat, not a defect (redaction would be a wire change). The companion SearXNG
ships `limiter: false` on the same assumption.

**Source:** `README.md` "Deployment posture" (commit `eecae07`, 2026-09-07);
`docs/configuration.md` "Deployment posture" (commit `381d99a`, 2026-09-07); `SECURITY.md`;
`contract/GOVERNANCE.md` ruling (d); `kit_tools/arch/SECURITY.md`.

---

### 2026-09-07: No secret may enter the image build — the Dockerfile declares zero build arguments, asserted absolutely

**Status:** Accepted — `CLAUDE.md` invariant 2

**Context:**
The pre-extraction Dockerfile carried `ARG HF_TOKEN` and a conditional `from_pretrained` bake. A
build argument is not a secret: BuildKit records it in layer history, `docker history --no-trunc`
reads it back from any registry the image reaches, and deleting the file afterwards does nothing.
That was the blocker on publishing any image.

**Options Considered:**

1. **Guard or default the argument to empty** — rejected: deleted "not guarded, not
   defaulted-to-empty, gone."
2. **A multi-stage build** to keep `uv` out of the image — rejected: `docker history` reports
   only the final stage, silently narrowing what `secret-grep` can see; "trading ~30 MB on a
   348 MB image for a weaker gate is a bad trade."
3. **Single-stage, no `ARG` of any kind, secrets at runtime only** — chosen.
4. Later, for the per-architecture `oras` download: **`ARG TARGETARCH`** (the convention) versus
   **`dpkg --print-architecture` inside the `RUN`** — the latter chosen (2026-09-10).

**Decision:**
The Dockerfile takes no build arguments at all; secrets pass at runtime through the container
environment, and Forage reads no secret store at boot either. Two mechanical guards:
`tests/test_dockerfile.py` on the file's text (`test_the_build_takes_no_arguments_at_all` asserts
zero `ARG` instructions, not just secret-shaped names) and CI's `secret-grep` on the built
image's layer history; `publish` re-greps the published config.

**Rationale:**
The US-004 notes: the absolute "is worth more than the convention precisely because it is
mechanically checkable: once it becomes 'no ARG except the harmless ones', the next argument only
has to clear 'is it as harmless as that one?', which is how an `HF_TOKEN` comes back."
`dpkg --print-architecture` answers the same question from inside the build and also works under
the legacy builder, where `TARGETARCH` would be empty.

**Consequences:**
Every image is weights-free; a token-less container is degraded by design. Poppy's
`services/retrieval/Dockerfile` still carries the argument and must never be pushed.

**Source:** `CLAUDE.md` invariant 2; `Dockerfile` header properties 1–2;
`kit_tools/specs/archive/feature-forage-ci-and-image.md` US-003 notes (commit `32df2d0`,
2026-09-07); `kit_tools/specs/archive/feature-forage-model-bootstrap.md` US-004 "The deliberate
deviation from the hint" (commit `97a3cb5`, 2026-09-10); `kit_tools/docs/GOTCHAS.md`
"Historical / Closed".

---

### 2026-09-07: Hermetic test suite, zero-tolerance ruff and pyright-strict with no baseline, everything through `uv run`

**Status:** Accepted

**Context:**
At the split the backlog was six files `ruff format` would reformat and, with inherited
`# type: ignore` comments disabled, 269 pyright-strict errors. Poppy's socket guard lived in a
`conftest.py` that did not move with the code.

**Options Considered:**

1. **Baseline or exclude the backlog** — rejected: it "was burned to zero rather than baselined."
2. **Honour `# type: ignore`** — rejected: one comment silences every diagnostic on its line; 55
   errors were hiding behind 39 inherited comments.
3. **Per-line suppressions in tests, or widening the public API for the checker** — rejected in
   favour of one documented, rule-level carve-out.

**Decision:**
`uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` are
blocking CI gates, all zero, no baseline, no excludes. `enableTypeIgnoreComments = false`; the only
relaxation is `reportPrivateUsage` for `tests/`, and `tests/test_pyright_policy.py` fails if
another is added without updating the policy comment. Third-party gaps go in `typings/` as minimal
stubs declaring only the symbols this repo calls. `tests/conftest.py` installs an autouse
`disable_socket(allow_unix_socket=True)` and clears the six credential and selection variables
before every test; `tests/test_hermeticity.py` is an executing canary. Always `uv run`: the lock
pins ruff 0.16.6 and pyright 1.1.411, and even `.venv/bin/pyright` reports 34 phantom errors.

**Rationale:**
`pyproject.toml`: turning type-ignore comments off "is what makes 'pyright exits 0' mean
something here." `tests/conftest.py`: the guard "turns a regression that reaches the real network
into a loud `SocketBlockedError` instead of a slow, flaky pass," and clearing credentials stops a
developer who exports `HF_TOKEN` from running "a *different* suite from CI's."

**Consequences:**
Mock at the seam; never relax the guard. The pyright burn-down retyped two hashed files and
rotated the sanitizer revision. `kit_tools/worktree.yaml`'s `run_prefix: uv run` must stay accurate.

**Source:** `tests/conftest.py` (commit `d2b62bb`, 2026-09-07); `pyproject.toml` `[tool.pyright]`
comment, `tests/test_pyright_policy.py`, `typings/README.md` (commit `f673fab`, 2026-09-07);
`docs/bootstrap-notes.md` "Lint / type backlog"; `CLAUDE.md` "Development";
`kit_tools/docs/GOTCHAS.md` hermeticity and pyright entries.

---

### 2026-09-07: Torch resolves from the CPU index on Linux via `uv.lock`; CI greps the lock for CUDA wheels

**Status:** Accepted

**Context:**
Resolving torch from plain PyPI drags in fifteen `nvidia-*` CUDA packages (about 2.7 GB) that no
Forage lane needs; the image never needs a GPU.

**Options Considered:**
Not recorded; the source gives only the rationale.

**Decision:**
`pyproject.toml` declares an explicit `pytorch-cpu` index and routes `torch` to it with a
`sys_platform == 'linux'` marker; `uv.lock` is committed; `grep nvidia- uv.lock` must be empty.
`tests/test_dependency_lock.py` and the CI `lint` job assert both the lock and the synced
environment are CUDA-free. The image installs with `uv sync --locked --no-dev
--no-install-project` from the same lock, so image, CI and local share one dependency set.

**Rationale:**
`pyproject.toml`: "macOS/Windows wheels are already CPU-only on PyPI, hence the
`sys_platform == 'linux'` marker."

**Consequences:**
Re-locking without the index configuration silently reintroduces the payload; a cold
`uv sync --extra dev` still takes about five minutes.

**Source:** `pyproject.toml` index comment (commit `d2b62bb`, 2026-09-07);
`tests/test_dependency_lock.py` and `.github/workflows/ci.yml` (commit `8c652ad`, 2026-09-07);
`kit_tools/docs/GOTCHAS.md` "`uv.lock` must stay CPU-pinned".

---

### 2026-09-08: The image is public and secret-free, published only behind the six gates plus a layer-identity check

**Status:** Accepted

**Context:**
Secret-free is not the same as public: "no longer a leak", "gated" and "public" are three
different claims. Branch protection was unavailable while the repository was private on the GitHub
Free plan — an admin token got the same 403, so the blocker was the plan, not the actor.

**Options Considered:**

1. **Required status checks as the gate** — insufficient alone; they stop a human merging over
   red, not a publish.
2. **A `needs:` chain inside one workflow file** — chosen; `needs:` cannot span workflow files, and
   a required check with no reporting job deadlocks every PR.

**Decision:**
`publish` runs only on pushes to `main` and `v*` tags and needs `lint`, `typecheck`, `test`,
`build-amd64`, `secret-grep` and `smoke`. It pushes the artifact `build-amd64` produced, never a
rebuild, then verifies the published amd64 layer digests equal the gated tarball layer-for-layer
and greps the published config for secret patterns. `vX.Y.Z` publishes `X.Y.Z`, `X.Y` and `latest`
plus a Release; tags containing `-` publish the exact tag only. The repository and both GHCR
packages have been public since the US-008 flip on 2026-09-10, anonymous pulls verified at the
gate. A publish that fails its own verification withdraws the git tag and deletes the GHCR
package version (the `v0.9.2-rc` precedent).

**Rationale:**
`docs/bootstrap-notes.md`: "the `needs:` chain stops anything *publishing* over red, which is the
property that actually protects consumers."

**Consequences:**
arm64 is built under qemu but never executed by CI; no signing, provenance or SBOM (declared out
of scope). `docs/releases.md` is the reference for what a green publish proves.

**Source:** `CLAUDE.md` invariant 2 "Publishing"; `docs/bootstrap-notes.md` "Required status
checks — attempted and deferred" (US-007, 2026-09-08); `docs/releases.md`;
`kit_tools/specs/archive/feature-forage-ci-and-image.md` Refinement Notes; `kit_tools/docs/CI_CD.md`.

---

### 2026-09-10: `sanitizer_revision` hashes the sanitizer sources plus `MODEL_ID@revision`, and is an input to the content-cache key

**Status:** Accepted; the six earlier hash values below are Superseded

**Context:**
`derive_sanitizer_revision()` sha256s eight `pipeline/` files plus model identity and threshold;
the value rides `/health` and every `/extract` error body, and Poppy keys
`stored_file_extractions` on it. Weights then became a runtime, per-deployment input, and nothing
inside Forage enforced `contract.py`'s own promise that a contract change invalidates cached
extractions — `cache_policy_fingerprint()` mixed in every caller knob but not the revision.

**Options Considered:**

1. **Avoid the rotation** by hashing `MODEL_ID` alone, or by placing the error vocabulary outside
   `pipeline/` — rejected; the latter "would have been the tail wagging the dog."
2. **Take each rotation deliberately, at a boundary, with the before/after measured and
   recorded** — chosen.

**Decision:**
The hashed identity is `MODEL_ID@revision` (`feature-forage-model-bootstrap` US-001) and the
revision is an input to `cache_policy_fingerprint()` (`feature-forage-cache-fallback` US-003), so
a rotation flushes Forage's own cache. Of thirty rotations, twenty-two
changed no sanitization policy or algorithm; the fifteenth, sixteenth, eighteenth,
nineteenth and twenty-seventh through thirtieth change sanitization behaviour.
The last four add opt-in suffix privilege, bound caller domain-list work,
enforce both caller and operator domain policy on `/search`, and default both
fetch thresholds from configured policy before capping (shipped 0.85 unchanged).

| Value | Moved by |
|---|---|
| `e6b2b56d…` | at split, identical to Poppy |
| `2b8d7e9a…` | vault-free hostname defaults in `orchestrator.py` (2026-09-07) |
| `cd00a8b4…` | `ruff format` gate reformatted `stage2_structural.py` (2026-09-07) |
| `0537316d…` | pyright-strict burn-down retyped two hashed files (2026-09-07) |
| `5927038d…` | identity became `MODEL_ID@revision` — no source byte moved (2026-09-10) |
| `fa4691c5…` | contract `1.1.0` and the revision joined the cache key (2026-09-10) |
| `8b1b7f78…` | error vocabulary added to `contract.py`; contract still `1.1.0` (2026-09-11) |
| `ee4450d9…` | the inline SearXNG call extracted from `orchestrator.py` into `SearxngProvider`; wire codes unchanged, `reason` text narrowed (2026-09-15) |
| `e7038672…` | `run_search_pipeline` gained the `providers=` chain seam in `orchestrator.py`; no sanitization behaviour changed (2026-09-15) |
| `b7871b20…` | contract `1.2.0`: `contract.py` gained `ContentKind` and `search_unavailable`, `orchestrator.py` gained the chain-shaped failure predicate and the `content_kind`/`date` copy — both hashed files, measured (2026-09-15) |
| `55e2af1b…` | `run_search_pipeline` gained free-first chain traversal in `orchestrator.py`: calls providers in order, advances on a `ProviderFailure`, stops at the first success, replace-not-merge; a one-provider chain is byte-identical to before (`search-fallback` US-001, 2026-09-16) |
| `5249def6…` | `run_search_pipeline` gained fallback telemetry and per-result provenance in `orchestrator.py`: derives `SearchResult.domain`, populates `provider_used`/`fallback_fired`/`provider_errors`, and increments `paid_calls`/`fallback_fired` through a new `SearchMetricsSink` Protocol; `models.py` gained the matching wire fields but is not a `_REVISION_SOURCES` member, so `orchestrator.py` is the only hashed file that moved — measured, not sanitization behaviour (`search-fallback` US-003, 2026-09-16) |
| `f0b93318…` | `run_search_pipeline` classifies a `ProviderSearchResult` with zero raw results and a non-empty `unresponsive_engines` list as a failure (SearXNG's real production shape, which answers 200 and never raises), advancing the chain exactly as a `ProviderFailure` does; sufficiency stays judged on raw results before sanitization; a configured `[searxng]`-only chain is carved out and still serves that shape as before — `orchestrator.py` is the only hashed file that moved (`search-fallback` US-002, 2026-09-16) |
| `dc3ff92a…` | `contract.py` gained the `POLICY_EXCLUDED_ALL_PROVIDERS` literal for the per-request policy 422 (`retrieval_app.py`, where the raise site lives, is not a hashed file) — `contract.py` is the only hashed file that moved, measured against all eight `_REVISION_SOURCES` files (`search-policy-and-health` US-010, 2026-09-16) |
| `41ac98ca…` | `contract.py`'s `CONTRACT_VERSION` docstring gained the completed 1.2.0 change record — every field, counter and enum member specs 1-4 added, all additive, plus a note that the `/search`/`/retrieve` boundary text rides the same unpublished window (`retrieval_app.py` and `models.py`, where that boundary text lives, are not hashed files) — `contract.py` is the only hashed file that moved, measured by reverting it alone and reproducing `dc3ff92a…` (`search-policy-and-health` US-003, 2026-09-16) |
| `b0ca8d9a…` | **the first rotation that changes sanitization behaviour.** `orchestrator.py` gained `_scan_forms_for_search_text`: `/search` now scans `title` and `snippet` in a newline-preserving form and ships their whitespace collapse, so Stage 2's line-anchored BLOCK patterns fire on any line rather than at character 0 only. Two entity decode levels before the scan, two control strips (one before the parser, one after the decodes), truncation once on the scan form, and a `_SEARCH_PARSER_INPUT_MULTIPLIER * max_length` parser-input bound. `orchestrator.py` is the only hashed file that moved, measured by reverting it alone and reproducing `41ac98ca…` from a clean tree (`hardening-search-sanitization` US-001, 2026-09-20) |
| `42485686…` | **the second rotation that changes sanitization behaviour.** `orchestrator.py`'s `_canonicalize_search_url` became `_SEARCH_URL_RULES`, an ordered registry of named pure rule functions run over the **raw** provider URL, first rejection wins, returning a frozen `SearchUrlOutcome` that carries the omission reason and a closed `SearchUrlRule` log token: presence/length (rejection, never truncation, at 2 048 characters), raw character class (controls, whitespace and RFC 3986 excluded characters rejected, never deleted), parse (`urlsplit` and the `parsed.port` read each in their own `try`), host code points (WHATWG forbidden set, IPv6 colons exempt, `%25` zone id its own token), then a structural scan of **both** `html.unescape(value)` and `unquote(html.unescape(value))`. `_sanitize_search_text` — which routed the URL through `extract_html`, and so ate tag-shaped text before the scan saw it — is deleted. `orchestrator.py` is the only hashed file that moved, measured by reverting it alone and reproducing `b0ca8d9a…` from a clean tree (`hardening-search-sanitization` US-002, 2026-09-20) |
| `05dbbb5c…` | contract `1.3.0`: `contract.py` gained `OMIT_BLOCKED_URL` and the version bump, `orchestrator.py` gained `_MAX_SEARCH_ENGINE_LENGTH = 64` and routed `SearchResult.engine` through the same `_normalize_search_text` call `title`/`snippet` already use — both hashed files, measured (both-reverted control reproduces `42485686…`). Bounds and normalizes a field; does not scan one, so **not** a third rotation that changes sanitization behaviour (`hardening-search-sanitization` US-004, 2026-09-20) |
| `840c78fa…` | the **search-time URL audit**: `orchestrator.py` gained rules (3a)–(3c) of `_SEARCH_URL_RULES`, the `SearchHostClass` vocabulary and `domain` from `CanonicalHost.host`; `contract.py` gained the `1.3.0` continuation line; **and two inputs joined the hash** — repo-root `url_validator.py` as `_ROOT_REVISION_SOURCES` (resolved against `pipeline_dir.parent`) and `idna@<version>` (UTS-46 tables are a sanitization input). All four measured alone; the control reverting both files *and* removing both inputs reproduces `05dbbb5c…`. **Changes sanitization behaviour** (`hardening-search-sanitization` US-003, 2026-09-20) |
| `6f0fa2de…` | **the fourth rotation that changes sanitization behaviour.** The `/search` scan loop scans **both** forms of each text field rather than only the newline-preserving one: two of the twenty-four Stage 2 patterns carry no `re.DOTALL`, so a newline-split payload scanned clean on the form that was scanned and blocked on the form that was served. `orchestrator.py` alone; the revert reproduces `840c78fa…`. |
| `e55b5f06…4d3c0` | **not** a rotation that changes sanitization behaviour. `run_retrieve_pipeline` gained five keyword-only dependencies — `settings: RetrieveSettings`, `retrieve_metrics: RetrieveMetricsSink`, `classification_semaphore`, `extraction_settings` (all required, as `run_extract_pipeline_from_file` takes its limits) and `admission: AdmissionSlot | None = None` (defaulted for exactly one story; US-002 removes the default) — plus the character pre-check that refuses an over-budget fetched page `content_too_large` / `promptguard_budget` and the `PromptGuardBudgetExceededError` backstop around `sanitize_and_structure`. `contract.py` gained `PROMPTGUARD_BUDGET` and the `1.3.0` continuation line. Both hashed files, each reverted in turn; the both-reverted control reproduces `6f0fa2de…`. The shipped default `retrieve.max_promptguard_chunks: 0` runs no pre-check and hands the classifier no `max_chunks`, so nothing served moves; `pipeline/retrieve_limits.py` and `pipeline/config_bounds.py` are not `_REVISION_SOURCES` members (`hardening-retrieve-parity` US-001, 2026-09-20) |
| `d0433876…fc88e` | **not** a rotation that changes sanitization behaviour. `orchestrator.py` gained the `_bounded_permit` async context manager — the only place `asyncio.timeout` and `semaphore.acquire()` appear in the file, with the release in `finally` only when acquired — the two defaulted parameters `classification_semaphore` / `classification_wait_seconds` on both `sanitize_and_structure` and `run_search_pipeline`, `/search`'s one-deadline-per-request budget, the `/extract` file route's acquisition moving inward from the outer `async with` that wrapped stages 2-4, and step 8's refusal to cache an `unavailable_allowed` body while the classifier is loaded; `stage3_promptguard.py` gained the pure `unavailable_result(tier_value, *, fail_closed)` seam so the wait-timeout path and the absent-classifier path cannot drift; `contract.py` gained the `1.3.0` continuation line. Three hashed files, each reverted in turn; the all-reverted control reproduces `e55b5f06…4d3c0`. What moved is *when* stage 3 runs and what happens when the permit wait expires, not how any text is sanitized (`hardening-retrieve-parity` US-006, 2026-09-20) |
| `f654be77…c92fb` | **not** a rotation that changes sanitization behaviour. `orchestrator.py` moved `extract_html`, `scan_structural` and `structure_sanitization_result` onto `asyncio.to_thread` (pure functions, byte-identical output on every route), made `run_retrieve_pipeline`'s `admission` a required `AdmissionSlot` held from after the cache read through stage 1, and deletes the fetched body before the classification wait; `contract.py` gained `busy` in `RetrieveErrorCode`, `RETRIEVE_ADMISSION_QUEUE_FULL` and the `1.3.0` continuation line. Each reverted alone, both-reverted control landing exactly on `d0433876…` (`hardening-retrieve-parity` US-002). |
| `464b6ad5…fead2` | **not** a rotation that changes how text is sanitized, though it moves a served outcome at the shipped defaults. `orchestrator.py` routes a fetched PDF through `asyncio.to_thread(extract_pdf_bytes_in_subprocess, …)` inside the `/retrieve` admission slot — the spawned, rlimited worker `/extract` uses, under `extraction.max_promptguard_chunks` — retaining ownership through worker reaping and spool cleanup even under repeated task cancellation. Its outcomes map most-specific first to 422 `content_too_large` / `promptguard_budget` or `extraction_failed` with reasons `pdf_encrypted`, `pdf_no_text`, `pdf_extraction_error`, `pdf_spool_error`; `contract.py` gained `extraction_failed` in `RetrieveErrorCode`, the four `RETRIEVE_PDF_*` literals and the `1.3.0` continuation line. Each reverted alone, both-reverted control landing exactly on `f654be77…`. Supersedes the unaccepted `6fd320da…` candidate, which released admission while cancelled PDF work remained live. Served text for a PDF within bounds is byte-identical; a PDF over 114,688 characters or the worker's rlimits is now refused where it was served or answered 500 (`hardening-retrieve-parity` US-003, 2026-09-22) |
| `664ee603…c04b` | **not** a sanitization-behaviour change. `contract.py` alone gained the 1.3.0 continuation line announcing `cache.corrupt_entries`; reverting its bytes reproduces `464b6ad5…fead2` exactly. The guarded parse in `cache.py` and metrics mirror/emission in `retrieval_app.py` are not hashed. Invalid cached JSON/schema becomes a counted, logged miss with deletion attempted rather than a 500, without authenticating parseable values (`hardening-retrieve-parity` US-004, 2026-09-22). |
| `d98f7dbe…69359` | **not** a sanitization-behaviour change at shipped defaults. `contract.py` alone gained the 1.3.0 continuation line for the three effective-policy fields; its read-only whole-file revert reproduces `664ee603…c04b` under default and shipped config. `retrieval_app.py` resolves bounds by replacing the request before cache/pipeline reads, then stamps every 200; `models.py` defaults the fields for old entries. Neither file is hashed. The floor and ceiling ship off, `/search` keeps 0.85, `/extract` is unchanged, and trusted-tier / VERIFIED exemptions remain (`hardening-retrieve-parity` US-005, 2026-09-22). |
| `5a470872…bf623` | Twenty-sixth, reconciled from the clean US-001 base: `fe211e3` changed `orchestrator.py` and `stage3_promptguard.py` for cancellation ownership, the absolute fetch deadline and timeout accounting, not the sanitization algorithm. The read-only pre-validation control reproduces `d98f7dbe…`. |
| `328d386c…93286` | Twenty-seventh, **fifth sanitization-behaviour change**: multi-label denylists include subdomains, allowlists opt in with a leading dot, and canonical private names precede caller denylists. Three hashed files (`orchestrator.py`, `contract.py`, and already-hashed root `url_validator.py`), individually reverted and all-reverted to reproduce `5a470872…` under default and shipped config (`hardening-hostname-and-config` US-001). |
| `c8a907cf…546b8` | Twenty-eighth, **sixth policy-driven sanitization-behaviour change**: caller lists normalise once under independent byte budgets; over-budget allowlists lose their tail's trust grants and denylists are refused whole. In-budget matching and text scanning are unchanged. `orchestrator.py` removes its entry pass, merges operator-first and counts wildcard trusted/verified resolutions; `contract.py` announces four counters and the reason; already-hashed `url_validator.py` removes its entry pass and sizes invalid surrogate escapes safely before rejecting them. Each read-only reversal was measured; all-reverted reproduces `328d386c…` under default and shipped config (`hardening-hostname-and-config` US-007; full values in `docs/bootstrap-notes.md`). |
| `de1cea65…6be91` | Twenty-ninth, **seventh policy-driven sanitization-behaviour change**: `/search` honours caller `blocked_domains` and operator `seed_blocklist`, operator-first, after URL audit and before content scanning. Shared blocked outcomes emit `blocked_url` and `host_class=policy_blocklist`; omissions never trigger paid fallback. Two hashed files, `orchestrator.py` and `contract.py`, individually reverted read-only; both-reverted reproduces `c8a907cf…` under default and shipped config. Empty-seed/no-new-field baseline and text scanning are unchanged (`hardening-hostname-and-config` US-002; full values in `docs/bootstrap-notes.md`). |
| `e00049c4…7ed5c` | Thirtieth, **eighth policy-driven sanitization-behaviour change**, for tuned deployments: both fetch routes resolve null/omitted threshold from validated config before the ceiling; search gains a caller threshold. `orchestrator.py` takes one required float for classification and cache fingerprint, never the nullable request field; `contract.py` announces the additions/defaults. Both hashed files individually reverted read-only; both-reverted reproduces `de1cea65…` under default and shipped config. Shipped 0.85 behavior, text-scanning algorithms, raw revision input and `/extract`'s raw guard are unchanged (`hardening-hostname-and-config` US-005; full values in `docs/bootstrap-notes.md`). |
| `aa288bc5…5b39c` | Thirty-first, **not a text-sanitization change**: `contract.py` alone announces `cache.integrity_rejects` and widened `storage_oversize_skips` producers. A read-only reversal against clean `b79504d` reproduces `e00049c4…` under default and shipped config; all other eight hashed sources are unchanged. The envelope and bounds (`cache.py`) and wiring (`retrieval_app.py`) are unhashed. Rotation orphans old cache keys (`hardening-cache-integrity` US-001; full values in `docs/bootstrap-notes.md`). |
| `0866963a…c1e80` | Thirty-second, **not a text-sanitization change**: only `contract.py` moves for `cache_unauthenticated` and the 1.3.0 continuation naming that reason and `cache_hmac_key`. Read-only whole-file reversal against clean `1e467c1` reproduces `aa288bc5…` under default and shipped config; the other eight hashed sources are unchanged. Key resolution and Valkey-only signing wiring are unhashed. Old keys are orphaned (`hardening-cache-integrity` US-002; full values in `docs/bootstrap-notes.md`). |
| `c9bf6e0d…f2f76` | Thirty-third, **not a text-sanitization change**: `orchestrator.py` gains compression/timeout counters before every traversal exit and the re-classification flag; `contract.py` announces both counters and the SearXNG-only `unsupported_encoding` reason token. Whole-file read-only reversals against clean `abf9df6` give `61d54562…` (orchestrator reverted), `e736bb76…` (contract reverted), and exact pre-story `0866963a…` (both), under default and shipped config. Only those two hashed files move; helper/providers remain unhashed. Upstream byte/encoding/time acceptance tightens and may buy a paid call, but text scanning is unchanged (`hardening-provider-bounds` US-003; full values in `docs/bootstrap-notes.md`). |
| `e3b9c138…73d91` | Thirty-fourth, **not a text-sanitization change**: only `contract.py` records the paid-prefix description and all-paid-chain policy 422. Read-only whole-file reversal against clean `0139ad6` reproduces `c9bf6e0d…` under default and shipped config; the other eight hashed sources are unchanged. No currently constructible production chain changes outcome; GOVERNANCE ruling (k) records the one-paid-name/duplicate-collapse basis (`hardening-provider-bounds` US-004; full values in `docs/bootstrap-notes.md`). |
| `d9db7586…1b6e0` | Thirty-fifth, **not a text-sanitization change**: `orchestrator.py` alone extracts `_query_provider_chain` with its original sink timing, retires the pipeline-only URL keyword, closes failure name/class/detail tokens and removes result URLs from omission logs. Four full wire/counter and two exhaustion pins landed first in `8e449fc`, against unchanged code, and remain unchanged. Read-only whole-file reversal against clean `2a275c5` reproduces the pre-story `e3b9c138…` under default and shipped config; all other eight hashed sources and contract artifacts are unchanged (`hardening-provider-bounds` US-005; full values and the direct-caller/log-consumer handoff in `docs/bootstrap-notes.md`). |
| `bf5a1f3e…3e75d` | Thirty-sixth, **not a text-sanitization change** (`hardening-resource-envelope` US-004): `orchestrator.py` takes configurable observational targets and records a strict-overrun count plus an unconditional whole-loop maximum; `contract.py` announces both additive metrics in held 1.3.0. Whole-file read-only reversals against clean `7087c04` yield `66b50985…` (orchestrator only), `3c699860…` (contract only) and exactly `d9db7586…` (both), under default, shipped and maximum-target config. Other seven sources and hash definition unchanged; the new settings module and target values are not hash inputs. Search wire/counter pins and the regenerated schema golden are unchanged; full values and consumer handoff in `docs/bootstrap-notes.md`. |
| `4913fdc1…aa1fb` | Thirty-seventh, **not a sanitization or response-shape change** (`hardening-resource-envelope` US-002): only `contract.py`'s held 1.3.0 continuation moves among nine hashed sources. Correcting the shipped status-only Compose probe's health descriptions regenerates OpenAPI and the held golden, whose sole change is `HealthResponse.description`; `_EXPECTED_ONE_THREE_ZERO_DIFF` gains no entry. Read-only whole-file reversal against clean `2aa6356` reproduces `bf5a1f3e…` under default and shipped config. Other eight sources and hash definition unchanged; historical goldens retained unchanged. Full values and consumer handoff in `docs/bootstrap-notes.md`. |
| `85394a95…3d0c0` | Thirty-eighth, **not a sanitization change at shipped defaults** (`hardening-promptguard-86m` US-006): only `contract.py` changes among nine hashed sources for the additive health model id. Read-only whole-file reversal against clean `06a56b2` reproduces `4913fdc1…` under default and shipped config. The selected `model_id@revision` input remains identical for 22M; only a non-default selection changes that input. Old cache keys invalidate; full measurements and handoff in `docs/bootstrap-notes.md`. |
| `b641e6a5…698f5` | Thirty-ninth (`hardening-promptguard-86m` US-007): stage 3's opt-in contiguity rule, orchestrator wiring/counters and contract continuation move three hashed files; windows then threshold join as ASCII inputs after the max threshold. Every file/input was reversed read-only against clean `967748d`; all-reverted gives `85394a95…` for default and shipped config. Stage 4 and the other five sources are unchanged. Rule ships off, but all old cache keys invalidate. Enabled contiguity changes decisions; the max rule and trusted/absent-model policy remain unchanged. Full measurements: `docs/bootstrap-notes.md`. |
| `bffeb7ba…47fe1` | Fortieth (`hardening-release` US-001): contract-only announcement of redacted request-validation 422s and the one-release placeholders. No text-sanitization change. Whole-file reversal against `7a4819b` reproduces `b641e6a5…`; full measurements and the ruling (l) decision are recorded below and in `docs/bootstrap-notes.md`. |
| `6884dc29…bd7ec` | Forty-first (`hardening-release` US-002): only `contract.py` moves for the final single 1.3.0 announcement and timeless 1.2.0 entry. Read-only whole-file reversal against clean `3ea0b32` reproduces `bffeb7ba…` under default and shipped config; the other eight hashed sources and hash definition are unchanged. No wire or sanitization behavior changes; old cache keys still invalidate. The six-model golden is frozen, cache metrics stay under dedicated coverage, and OpenAPI/anchors are unchanged. Publication state stays in GOVERNANCE and releases docs, not hashed entries. Full measurements: `docs/bootstrap-notes.md`. |
| `021378ef…33900` (current) | Forty-second (whole-epic release gate): `orchestrator.py` pins unavailable readiness before skipping admission; `url_validator.py` compares IPv6 policy identities numerically without changing wire spelling. Both hashed files individually reversed against `84c02af`; both-reverted reproduces `6884dc29…` under default and shipped config. Policy enforcement changes, not scanning or response shape. UTF-8 raw-threshold hashing remains ASCII-compatible; the provider network-read adapter is unhashed. Full controls: `docs/bootstrap-notes.md`. |

**Rationale:**
`pipeline/sanitizer_revision.py`: "two containers running the same code can be scanning with
different weights — and a value that could not tell them apart would key a cache on a
sanitization behaviour it does not actually describe." The fifth rotation is "the first one where
the invalidation is the *objective* rather than the price."

**Model-selection decision (2026-09-22, US-006 / R29):** publish the configured
id unconditionally as `/health.promptguard_model`, read from startup state;
`promptguard_loaded` remains the serving signal. Identity is contract-relevant
and inferable from behaviour, whereas contiguity settings are tuning an
attacker would otherwise have to guess and are not published. This is not a
secrecy promise: US-007's per-rule `promptguard_contiguity_detections` lets a
content/metrics prober infer the settings by bisection. The resolver is total
for revision fallbacks; only the lifespan refuses an unknown id. Blank means
22M, and the allowlist expands only alongside the owner-vendored manifest entry.

**Consequences:**
Any edit to a `_REVISION_SOURCES` file invalidates every cached sanitization; never do it as a
drive-by inside a behavioural change. Poppy re-extracts on next access (spec 6 owns that). The
revision is a drift signal, not a compatibility statement and not tamper-proof: compare contracts,
not revisions. CI runs `tests/test_sanitizer_revision.py` as a named step.

**Source:** `pipeline/sanitizer_revision.py` docstring (commit `c088b04`, 2026-09-10); `cache.py`
`cache_policy_fingerprint` (commit `865a586`, 2026-09-10); `docs/bootstrap-notes.md` rotation
records; `kit_tools/docs/GOTCHAS.md` "`sanitizer_revision` has deliberately diverged from Poppy's".

---

### 2026-09-10: Model weights are a runtime, per-deployment input — pinned, manifest-verified, mirror-backed, retried forever, never blocking startup

**Status:** Accepted; the `HF_HUB_OFFLINE` environment-variable mitigation is Superseded

**Context:**
With the bake gone every image is weights-free and the gated Hugging Face repository needs
`HF_TOKEN`. A runtime fetch opens a supply-chain path the bake never had: `from_pretrained` falls
back to pickle (`torch.load`, remote code execution) when safetensors is absent, and a vendored
manifest would faithfully bless a swapped `.bin`.

**Options Considered (all recorded in the spec):**

1. **Mirror location:** private GHCR OCI artifact — chosen; restic/SFTP rejected (an
   SSH-key-in-container secret shape); both rejected (double maintenance for a yearly artifact).
2. **Mirror client:** a pinned `oras` static binary — chosen; hand-rolled OCI HTTP rejected as "a
   spec-budget blowout for a rarely-exercised fallback."
3. **Startup:** `asyncio.to_thread` inside a task on `app.state` — chosen; awaiting in the
   lifespan rejected (uvicorn serves nothing until the lifespan returns, so a ~270 MiB download
   would restart-loop the consumer's 10 s × 5 healthcheck).
4. **Retry:** single-flight, second caller refused — chosen; queuing rejected because it "means a
   second ~270 MiB download starting the instant the first one finishes."
5. **Warm-start offline pin:** the `HF_HUB_OFFLINE` environment variable at runtime — Superseded
   (sampled once at import, so it does nothing); `huggingface_hub.constants.HF_HUB_OFFLINE` set
   and scoped to the classifier load — chosen (US-005 correction, measured both ways).

**Decision:**
`model_fetcher.py` pins `DEFAULT_MODEL_REVISION` (`11614a15…`, overridable only with a full sha via
`FORAGE_MODEL_REVISION`), verifies an exact-set sha256 manifest (`weights_manifest.json`) with a
safetensors-only allowlist and `use_safetensors=True`, quarantines a failed set for one
generation, tries Hugging Face then the GHCR mirror (token on stdin), and `WeightAcquisition`
retries 30 s doubling to 600 s with ±20 % jitter, forever, single-flight and cancellable. A warm
start from the `forage-model-cache` volume touches no network. A missing token is a supported,
loud degraded mode.

**Rationale:**
`WeightAcquisition` docstring: acquisition "can fail for reasons that heal on their own … and the
alternative to retrying is asking an operator to restart a service that is otherwise running
correctly." A process-wide offline pin "would turn every cold start into a permanent degraded
mode," which is why the pin is scoped.

**Consequences:**
`/health` stays 200 throughout a download. A plain `snapshot_download` without `ALLOW_PATTERNS`
fails verification (`README.md` and `.gitattributes` are `file_extra`). The mirror is private and
useless to third parties. Cancelling mid-acquisition stops the await, not the worker thread.

**Source:** `kit_tools/specs/archive/feature-forage-model-bootstrap.md` Refinement Notes and
Clarifications (session 2026-09-02); commits `c088b04`, `d464bd7`, `97a3cb5`, `1d59438` (all
2026-09-10); `model_fetcher.py` docstrings; `kit_tools/docs/GOTCHAS.md` `ALLOW_PATTERNS` and
`HF_HUB_OFFLINE` entries; `docs/weights.md`.

---

### 2026-09-10: The cache backend is selected by whether `VALKEY_URL` is set, never by whether it is reachable

**Status:** Accepted

**Context:**
Valkey was a hard dependency. In the 2026-09-02 clarification session the owner overrode the
recommendation to defer and chose to make it optional now.

**Options Considered:**

1. **A parallel in-memory twin of `ContentCache`** — rejected: `cache.py` carries five
   security-relevant policy behaviours (tier refusal, freshness revalidation, tz-naive rejection,
   zero-TTL purge, fingerprint keying), and "a twin implementation duplicates enforcement and
   drifts."
2. **One `ContentCache` policy layer over a swappable `CacheStorage` protocol** (`ValkeyStorage`,
   bounded `InMemoryStorage`), parametrized tests over both — chosen.
3. **Fall back to memory when the configured Valkey is unreachable or unparseable** — rejected.

**Decision:**
Only a *fully unset* `VALKEY_URL` selects the in-memory backend; any value, including an empty
string, selects Valkey, and a broken one reports `degraded: cache_unavailable` while requests
proceed uncached. Memory bounds are 256 entries and 32 MiB (from about 128 MiB of real headroom in
a 1 GiB container), storing JSON bytes for exact accounting. The additive `/health.cache_backend`
field bumped the contract to `1.1.0`.

**Rationale:**
`retrieval_app._configured_valkey_url`: "`VALKEY_URL=${VALKEY_URL}` rendered against nothing is a
realistic deployment accident, and reading it as 'unset' would answer a broken configuration by
quietly running an unshared, non-persistent cache in production." A silent fallback "would turn a
typo into a cache that never shares anything with the rest of the deployment."

**Consequences:**
Check `cache_backend`, not `cache_connected`, to know which mode production is in. Memory mode
cannot degrade. `/search` and `/extract` are never cached. `tests/conftest.py` clears `VALKEY_URL`
per test because an exported one would switch backends under the suite.

**Source:** `retrieval_app.py` `_configured_valkey_url` and `_select_cache_storage` docstrings
(commit `921b389`, 2026-09-10); storage abstraction in commit `273753f`;
`kit_tools/specs/archive/feature-forage-cache-fallback.md` Refinement Notes and Clarifications;
`docs/configuration.md` cache selection table.

---

### 2026-09-11: Image builds are byte-reproducible, and every toolchain input is digest- or SHA-pinned

**Status:** Accepted

**Context:**
The `v0.9.2-rc` publish failed its own layer-identity gate twice — correctly. The build was not
byte-reproducible (apt logs, `.pyc` files from the build-time import check, uv-sync timestamps),
so a rebuild only matched the gated tarball when buildx reused cached layers; the failed rebuild
then poisoned the `publish` GHA cache scope.

**Options Considered:**
Not recorded beyond the measurement that decided it: two `--no-cache` builds matched on 7 of 19
layers with neither fix, 16 of 18 with the exporter attribute alone, 19 of 19 with both.

**Decision:**
CI sets `SOURCE_DATE_EPOCH` to the commit's committer date and `rewrite-timestamp=true` on every
building job's exporter; the Dockerfile removes apt and dpkg logs and the ldconfig aux-cache in
the install `RUN` and runs the import check with `PYTHONDONTWRITEBYTECODE=1`. The base image,
`uv` and `oras` are digest- or sha256-pinned; every CI `uses:` is pinned to a 40-hex SHA with
`persist-credentials: false`; actionlint is sha256-pinned. Guards:
`tests/test_ci_workflow.py::TestReproducibleExports`,
`tests/test_dockerfile.py::TestReproducibleImageContents`.

**Rationale:**
`kit_tools/docs/GOTCHAS.md`: exporter rewriting "is necessary and **not sufficient**: it rewrites
the layer tar's *headers*, and this image also wrote timestamps *inside* files." Both halves are
guarded because "both look like tidy-up-able noise to someone who does not know this entry exists."

**Consequences:**
No bytecode cache in the image (import costs about 2.3 s instead of 0.9 s at start).
Reproducibility is bounded by one UTC day (`useradd` stamps a day count into `/etc/shadow`). If a
publish fails its gate again, delete the `index-publish-*` cache entries and re-run.

**Source:** `kit_tools/docs/GOTCHAS.md` "A cold-cache publish fails its own parity gate"
(2026-09-11); `docs/releases.md` "Reproducible builds"; `Dockerfile` comments; `SOURCE_DATE_EPOCH`
in commit `f4c2b16` (2026-09-11); action pinning since commit `8c652ad` (2026-09-07).

---

### 2026-09-11: The frozen contract is a generated file with a committed sha256 anchor; documentation mirrors the wire; five rulings recorded

**Status:** Accepted

**Context:**
Before `feature-forage-contract` the OpenAPI document was generated per request and no consumer
held a copy to diff. The spec had to document the error surface without moving a wire byte, freeze
the document as a verifiable artifact, and write the bump policy down.

**Options Considered (all recorded):**

1. **Emit every error through one envelope model** (round 1) — rejected: it dropped
   `sanitizer_revision` (breaking Poppy's hard 422 validation), omitted five `/retrieve` codes and
   homogenized the 404/413/503 bodies — a MAJOR-class wire change under a no-bump ruling.
2. **Mirror each emission site with a response model and hold parity tests against it** — chosen.
3. **The `/extract` 413 that FastAPI's form parser shadows into a 400:** fix it now (a status a
   client observes moves — MAJOR) versus declare both statuses with the 413 described as shadowed
   — the latter chosen, no bump. The fix belongs to whoever owns the next bump, never to a passing
   refactor.
4. **`/metrics`:** a permissive response model (an unmodeled counter is silently filtered) versus
   `extra="forbid"` (a loud 500) — the latter chosen.

**Decision:**
`contract/openapi.yaml` and `contract/openapi.yaml.sha256` are generated by
`uv run python -m scripts.export_contract`; hand-editing either is always wrong and
`tests/test_contract_export.py` stays red until the exporter runs. The committed anchor is the
trust root, because registry tags and Release assets are mutable and a checksum in git history is
not. The document ships in the git tag, the `v*` Release assets and `/app/contract/openapi.yaml`
in the image; `smoke` verifies the in-image copy and `publish` the Release assets against the
anchor, and `publish` writes and asserts `contract: <version>` in the Release body. Vocabularies in
`pipeline/contract.py` are frozensets derived from their own `Literal` aliases so type and set
cannot half-land. Rulings: (a) the documentation pass is no bump pre-freeze; (a2) the unreachable
413 is documented, fixing it is MAJOR; (b) new vocabulary members are MINOR, security-block reasons
must be announced; (c) golden fixtures are retained, never replaced; (d) the private-IP echo is a
documented caveat.

**Rationale:**
The Refinement Notes: "Documentation must follow the wire, with parity tests as the leash," and "a
human release-notes line was the last unmechanized integrity claim."

**Consequences:**
Anything that moves the document — a response model, a `responses=` declaration, a field
description, a FastAPI bump — is followed by the exporter. Adding a `/metrics` counter without its
model field, in the same position, 500s the endpoint. Consumers vendor per `contract/GOVERNANCE.md`:
verify against the anchor from the same tag, never against another copy. The first release after
the freeze was image `v1.0.0` serving contract `1.1.0`, cut 2026-09-12.

**Source:** `contract/GOVERNANCE.md` (commit `3c116e3`, 2026-09-11);
`kit_tools/specs/archive/feature-forage-contract.md` Refinement Notes and US-001 notes; generated
contract in commit `5985437`, distribution in `f4c2b16` (both 2026-09-11);
`kit_tools/docs/GOTCHAS.md` "An over-sized upload gets 400" and "Adding a `/metrics` counter";
`.github/pull_request_template.md`.

---

### 2026-09-19: Sized to the host, not the deployment

**Status:** Accepted (`hardening-resource-envelope`, R16 corrected through round 5).

**Context:** The Epic-2 cutover finding of 2026-09-12 was a
`search_promptguard_local_latency_target_exceeded` on a 28-core host under a 1-CPU
quota. Torch and the fast tokenizer saw host cores, not the quota. The owner
declined an arbitrary size bump: make the envelope configurable instead.

**Options considered:** fixed larger defaults; runtime environment overrides for
every knob; CPU quota auto-detection; or existing `config.yaml` readers with
Compose-layer container limits. The last is chosen; CPU auto-detection is deferred.

**Decision and rationale:** runtime tunables stay in `config.yaml`, following
`search_brave_*` and `promptguard_threshold`. The vision's "overridable by env"
is met at the Compose layer with `FORAGE_CPUS` / `FORAGE_MEM_LIMIT`, plus the
documented full-file bind mount for runtime settings — **no `FORAGE_*` env override
for `promptguard_threads`**. Unset/zero CPU means Compose omits the cap; memory
remains `1024m`, threads `0`, classification concurrency `1`, and observational
targets 1000/5000 milliseconds. The only new default-visible Compose behavior is
the status-only liveness probe, never an activation or readiness gate.

R16's corrected memory rule counts the named parent reservation, selected model's
shared resident delta, per-classification provisional working set, **configured**
PDF child address space and backend-specific cache term exactly once. The three
raisable extraction keys are `classification_concurrency` (1–8),
`child_address_space_bytes` (128–512 MiB, widening the sandbox) and
`admission_queue_depth` (0–4). The boot memory WARNING is advisory; an OOM kill
cannot report itself through `/health`. The 64 MiB working set is explicitly
unmeasured; spec 7 supplies measurements without silently increasing the shipped
memory default. The cache ceiling remains 128 MiB even on larger hosts.

**Consequences:** under-sizing can turn a bounded classification wait into a
marked fail-open unscanned response, not just slower scanning. `_load_config`
replaces rather than merges, so a short mount can silently undo an operator's
hardening. The explicit security-key partition/default test proves only the
shipped baseline. Effective-value logging was considered and declined: INFO does
not render, and WARNING for normal values is noise. The operator warning is the
only protection for their own stricter baseline.

US-004's `bf5a1f3e…` and US-002's `4913fdc1…` measured revision rotations are
retained in the rotation table above; neither changes text sanitization.
US-003 only changes prose, comments and baseline guards, not hashed sources or
the held contract. See
[`docs/configuration.md` § Sizing the container](../../docs/configuration.md#sizing-the-container)
for the worked rules, delivery recipe and unmeasured latency columns.

**Source:** `feature-hardening-resource-envelope` US-001/US-004/US-002/US-003,
R16 (corrected), Epic-2 cutover finding and the vision's configurable-sizing
constraint; `docs/bootstrap-notes.md` for the measured rotations.

---

### 2026-09-23: Redact validation values now, retain placeholder keys for one MINOR

**Status:** Accepted, `hardening-release` US-001; GOVERNANCE ruling (l).

**Context:** the published validation-error description admitted pydantic's
`input`, `ctx` and `url`, so an immediate key removal with only a release note
would not be a real compatibility window.

**Options considered:** immediate removal, a flag retaining raw values, MAJOR,
or an expedited MINOR retaining key presence with fixed values.

**Decision and rationale:** Example 6 step 1 keeps all three keys with
`"[redacted]"` throughout contract 1.3.0; they disappear at the next MINOR.
No declared property moves at either step; ruling (l) records the explicit
description-admitted-key carve-out for step 3. Preserving actual values behind
a flag would preserve the reflector. The total handler never logs/re-raises
the validation exception; location strings come from owned route fields plus
framework tokens. Malformed entries fail closed and rendering is inside the
same guard. The cap is 100 response entries, not request-body admission.

**Consequences:** consumers reading `detail[].input` must stop. Closed
`validation_422_truncated` and `validation_422_loc_dropped` WARNINGs reveal
only counts and a matched route token. Parse cost and log volume remain the
accepted admitted-caller exhaustion risk; the pipeline DNS-oracle caveat stays.
The fortieth revision rotation is `b641e6a5…` → `bffeb7ba…`: only
`contract.py` moves among the nine hashed sources. Read-only whole-file reversal
against clean `7a4819b` reproduces the former under default and shipped config.
The other eight sources/hash definition are unchanged; no text-sanitization
algorithm changes, but old cache keys invalidate.

**Source:** `retrieval_app.py`, `contract/GOVERNANCE.md`, `docs/releases.md`,
`tests/test_contract_errors.py`, `tests/test_models.py`;
`docs/bootstrap-notes.md` for full measurements and the consumer handoff.

---

<!-- Copy the structure of any entry above for new decisions: the heading is the ISO date and a
     short title, followed by Status, Context, Options Considered (or "not recorded"), Decision,
     Rationale, Consequences and Source. Add new decisions here first; promote to CLAUDE.md only
     if they turn out to be load-bearing. -->
