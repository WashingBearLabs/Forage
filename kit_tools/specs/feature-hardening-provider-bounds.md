<!-- Template Version: 2.5.0 -->
---
feature: hardening-provider-bounds
status: active
session_ready: true
depends_on: [hardening-cache-integrity]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 5
epic_final: false
execution_order: [US-001, US-003, US-002, US-004, US-005]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Provider Seam Bounds — Streamed Caps, Wall-Clock Budgets, Policy by Construction

> **Epic 5 of `epic-forage-hardening`.** Close the resource and cost gaps the search epic's
> validation runs found at the provider seam: both providers read their bodies raw and decode them
> themselves under a byte ceiling (never a whole buffer, never an unbounded library decode), every
> provider call has one wall-clock budget, the free provider gets the same outbound query cap the
> paid one has, the per-request policy is cost-monotonic by construction rather than by today's
> one-paid-provider coincidence, and `run_search_pipeline`'s provider loop is refactored once, in one
> recorded rotation, with its wire output *and* its metrics sink pinned. Planning rulings 13, 14 and
> 15 **as corrected in validation round 2 (R13, R14, R15)** are binding; ruling 6 governs every
> rotation; rulings 5 and 36 govern the two window changes; R39 and R40 govern every doc sweep and
> every test-seam change. Source findings: `kit_tools/AUDIT_FINDINGS.md` 2026-09-16-012, -020, -021,
> -044 (bounds), -052, -060 (policy), -054 (recorded as accepted risk, R14), -035, -036, -038, -039,
> -043, -045, -011 (orchestrator cleanup). Validation round 1 split the old US-002 into US-002 and
> US-004 and renumbered the cleanup to US-005; validation round 2 split the old US-001 into US-001
> (the settings seam and the shared test doubles, behaviour-preserving) and US-003 (the streamed,
> self-decoded, wall-clock-bounded read path); validation round 3 corrected the decoder's end-of-stream
> rule (no `flush()`), bounded the *raw* bytes read, made the shared doubles stream-backed, named the
> decode observation seam, made `Accept-Encoding: identity` an admitted new header, and declined every
> further split (R37 corrected). Every id from US-001 to US-005 is now in use.

## Overview

The search epic shipped a provider seam whose *contract* is closed — `ProviderSearchResult` or
`ProviderFailure`, closed `failure_class` and `detail` tokens (`pipeline/search_providers/base.py:
21-76`) — but whose *resource* posture still trusts the peer. `SearxngProvider` buffers the whole
response with `client.get` and only then compares `len(resp.content)` to its 1 MiB bound
(`searxng.py:57-62,162-175`); `BraveApiProvider` counts bytes incrementally (`brave.py:331-346`) but
counts them *after* httpx has transparently decompressed them, and httpx's `GZipDecoder` calls
`zlib.decompressobj().decompress(data)` with no `max_length` (`.venv/.../httpx/_decoders.py:85-97`),
so one ~64 KiB raw read can arrive as one multi-megabyte decoded chunk — the 67 MB first chunk
finding -020 measured from a ~200 KB stream (validation round 1 reproduced it: a 230-byte gzip body
arrives from `aiter_bytes()` as a single 200,000-byte chunk); both clients pass a bare float as
`timeout=` (`searxng.py:160`, `brave.py:317`), which httpx applies per socket operation, so a
trickling peer never trips it (finding -021); and SearXNG receives the caller's `query` verbatim
while Brave truncates its copy to `search_brave_query_max_chars` (`brave.py:310-314`), so a
URI-length failure at SearXNG can buy a paid call (finding -044).

The load-bearing decision is **R13 as corrected in round 2**: both providers read with
`aiter_raw()` and decode compressed bodies **themselves**, under a byte ceiling. `Content-Encoding`
absent or `identity` → raw bytes are counted against the cap; `gzip` / `deflate` → a
`zlib.decompressobj` is fed one raw chunk at a time with `decompress(chunk, max_length=remaining +
1)`, looping on `unconsumed_tail` until it is empty, so the peak decoded bytes held for one response
never exceed the cap plus one byte, plus one raw read buffer — and **`flush()` is never called**:
`zlib.decompressobj.flush(length)` sizes an output buffer and returns every remaining byte regardless
(round 2 measured 4,999,990 bytes back from `flush(10)` after a `max_length=10` decompress), so the
end-of-stream rule is "`eof` set → done; `eof` unset or `unconsumed_tail` non-empty at end of stream →
`malformed_body`". The **raw** bytes read are bounded too, at `4 × max_response_bytes`, because a
deflate stream can consume unbounded input while producing nothing (round 2 measured 1,000,000 raw
bytes → 0 decoded, `eof` unset); any other encoding — `br`, `zstd`, an unknown token, or
a multi-encoding list — is `ProviderFailure("hard_error", "unsupported_encoding")`, because the pinned
httpx 0.28.1 ships no brotli or zstandard decoder (`SUPPORTED_DECODERS` pops both, `_decoders.py:
381-393`; neither package is in `uv.lock`) and silently falls back to identity, which today turns such a
body into `bad_json` and, on a `[searxng, brave]` chain, into a billable call. `Accept-Encoding:
identity` is **now requested — a new outbound header on both clients** (httpx sends `gzip, deflate` by
default; `grep -rni accept-encoding pipeline/` returns nothing today), and because SearXNG's optional
bot limiter treats a request without `gzip`/`deflate` in that header as a bot (`kit_tools/docs/
GOTCHAS.md:370`, `docs/searxng.md:74`), the interaction is written beside the existing limiter caveat
rather than discovered; a compressing proxy in front of an operator's `SEARXNG_URL` is never an
outage — a gzip body is served, bounded and counted; only a body this build *cannot decode* is
refused, and that refusal is a distinct token so the operator sees the proxy setting instead of a
parse error. The new `/metrics` counter, `search.provider_compressed_body`, is incremented **at the
point the header is seen**, so it counts served, over-bound and undecodable compressed bodies alike; a
zero reading genuinely means no compression. The per-provider timeout becomes a wall-clock budget
through one `asyncio.timeout(...)` around the HTTP interaction; the httpx `timeout=` stays as the
inner per-operation guard. SearXNG gains a `SearxngSettings` dataclass mirroring `BraveSettings` so
the budget and, in US-002, the query cap have a real seam. None of this changes a wire byte of
`SearchResponse`; the one contract movement is the additive counter, inside the open 1.3.0 window.

The second decision is **R14**: `apply_request_policy` keeps only a *prefix* of the configured paid
providers, so cost-monotonicity — "no request can cause a paid call the configured chain would not
already have made" (`policy.py:3-6`) — holds for any chain, not just today's single-paid one (finding
-052). The counter-amplification finding (-054) is **not** fixed by changing the counter's unit:
per-entry counting stays (no contract, no `MONITORING.md` change) and the amplification is recorded
as an accepted risk, described honestly. The prefix rule is stated in `SearchRequest.providers`'
description (a window change), together with its one caller-visible consequence: on an all-paid
configured chain a later-paid-only selection now leaves no provider and is the existing
`POLICY_EXCLUDED_ALL_PROVIDERS` 422.

The third is **R15**: the orchestrator's provider loop is cleaned up in *one* story and *one*
rotation — extracted, deduplicated, its legacy `searxng_url=` parameter retired, its omission logs
stripped of result URLs, and its `provider_errors` composition made to enforce the closed vocabulary
at the point it reaches the wire — with the wire output pinned on committed synthetic fixtures
(compared with `request_id` excluded), the metrics sink pinned on the same fixtures, the two
exhaustion outcomes pinned as raised payloads, and the two deliberate deltas stated as their own
criteria.

## Goals

- Peak decoded bytes held for one provider response never exceed `max_response_bytes + 1` plus one
  raw read buffer, **and the total raw bytes read never exceed `4 × max_response_bytes`**, on both
  providers, for a plain body, for a gzip body whose decoded length is ≥ 64× its raw length, and for
  a compressed stream that decodes to nothing; a body of `max_response_bytes + 1` decoded bytes —
  plain or compressed, including one whose overflow arrives only in its final raw chunk — returns
  `ProviderFailure("hard_error", "body_too_large")`; the test asserts the largest value returned by
  **any** zlib call through the recording seam (`tests/fakes.py::RecordingDecompressor`), never a
  chunk count.
- A compressed body is never refused for being compressed: a gzip or deflate body under the bound is
  served; a `br`, `zstd` or unknown encoding is `ProviderFailure("hard_error", "unsupported_encoding")`;
  a truncated or corrupt compressed stream is `ProviderFailure("hard_error", "malformed_body")`; both
  providers send `Accept-Encoding: identity` (a new header); every non-identity `Content-Encoding`
  increments `search.provider_compressed_body` by exactly 1 whatever the outcome — served, over-bound,
  undecodable, malformed, a compressed non-2xx, a timeout after headers, or a transport error mid-body.
- Every provider HTTP interaction completes or fails within its wall-clock budget: a fake peer that
  trickles bytes past a 0.5 s budget yields `ProviderFailure("timeout", "timeout")` after more than
  0.5 s has elapsed, on both providers (lower bound only).
- The outbound SearXNG `q` never exceeds `search_searxng_query_max_chars` (default 400, range 50–400,
  the same as Brave's) characters; `SearchRequest.query` keeps its wire shape.
- For every configured chain and every request, the paid providers the effective chain can call are
  a prefix of the configured paid sequence — proven over every chain shape in
  `tests/test_search_policy.py::_CHAIN_SHAPES` crossed with a per-shape later-paid-only selection.
- After the cleanup, `run_search_pipeline` contains no `await provider.search(` call, and the
  committed synthetic fixtures produce identical `SearchResponse.model_dump()` output (with
  `request_id` excluded), identical metrics-sink counters, and identical raised `PipelineError`
  payloads on the two exhaustion paths, before and after; `pipeline/orchestrator.py` no longer
  mentions `searxng_url`.
- No `/search` log record carries a result URL, and no out-of-vocabulary provider class, detail or
  name reaches the wire or the log.

## User Stories

### US-001: The SearXNG settings seam and the shared streaming doubles — behaviour-preserving

**Priority:** P1

**Description:** As a maintainer, I want SearXNG to have the same validated settings object Brave has
and the test suite to own one shared set of streaming HTTP doubles, so the read-path rewrite in
US-003 lands on a seam that already exists and on doubles that are already promoted — with zero
production behaviour change in this story.

**Independent Test:** `searxng_settings_from_config({})` returns `SearxngSettings(timeout_seconds=10.0,
max_response_bytes=1_048_576)`; `{"search_searxng_timeout_seconds": 0.5}` and `61.0` and `"abc"` raise
`SearxngConfigurationError`; a lifespan boot with `search_searxng_timeout_seconds: 30.0` builds the
production chain through `build_provider_chain(searxng_settings=...)` whose `SearxngProvider` carries
`settings.timeout_seconds == 30.0` (asserted on `app.state.search_providers[0].settings`); the
promoted doubles in `tests/fakes.py` are imported by `tests/test_brave_provider.py` and
`tests/test_stage5_url_audit.py`, and `git diff --stat` for this story shows no change under
`pipeline/search_providers/searxng.py`'s `search()` body, `brave.py`, `pipeline/orchestrator.py`,
`pipeline/contract.py`, `models.py` or `contract/`; the full suite passes with every pre-existing
assertion intact.

**Implementation Hints:**
- `SearxngSettings` mirrors `brave.py:193-275` exactly: a frozen dataclass
  `SearxngSettings(timeout_seconds: float = 10.0, max_response_bytes: int = 1_048_576)`, a
  module-owned `SearxngConfigurationError(ValueError)`, module-local `_bounded_float` / `_bounded_int`
  copies (the re-statement is deliberate — `brave.py:231-233`; provider modules import nothing from
  the app's other configuration surfaces; recorded in Decisions Made), and
  `searxng_settings_from_config(config)` reading a new top-level `config.yaml` key
  `search_searxng_timeout_seconds` (float, default `10.0`, range 1–60; out of range or wrong-typed
  refuses boot with `SearxngConfigurationError`, the `search_brave_*` precedent). The shipped
  `config.yaml` gains `search_searxng_timeout_seconds: 10.0` beside `search_brave_timeout_seconds`
  with a one-line comment (both new keys in this spec are shipped, so an operator who bind-mounts
  the shipped file sees them). `SearxngProvider.__init__` (`searxng.py:146`) gains
  `settings: SearxngSettings | None = None` (default = the dataclass defaults, so every direct
  construction keeps working); in this story the value is stored on `self.settings` and **not yet
  read** by `search()` — the module constants `_SEARXNG_TIMEOUT_SECONDS` / `_MAX_SEARXNG_RESPONSE_BYTES`
  still drive the read path until US-003 switches it (say so in a comment US-003 deletes).
- `BraveSettings` (`brave.py:197-203` — today `timeout_seconds`, `chunk_max_chars`, `query_max_chars`
  only) gains `max_response_bytes: int = 1_048_576` with **no** `config.yaml` key (test-only,
  non-configurable, the same shape as SearXNG's field), and `_BRAVE_MAX_RESPONSE_BYTES` (`:150`)
  becomes that field's default; `brave.py:331-346` keeps reading the module constant until US-003
  switches it to `self.settings.max_response_bytes`. Without the field the Brave half of the
  byte-bound test has no seam to shrink the cap and must build 1 MiB-scale fixtures (round-2 salty
  and story-quality reviews).
- Spec 3 US-003's AST code-parity sweep walks a hand-maintained reader list
  (`feature-hardening-hostname-and-config.md`, the `_settings_from_config` readers) that names
  `brave.py` but not `searxng.py`: add `pipeline/search_providers/searxng.py` to that list in this
  story, so `searxng_settings_from_config`'s literal keys are swept like `brave_settings_from_config`'s
  (spec 3 already anticipates the `search_searxng_*` keys in its comment).
- Wiring, exactly as Brave is wired: the lifespan calls the builder unconditionally beside
  `brave_settings_from_config` (`retrieval_app.py:1242`), stores `app.state.searxng_settings`, and
  `build_provider_chain` (`pipeline/search_providers/__init__.py:83-89`) gains
  `searxng_settings: SearxngSettings | None = None` beside `brave_settings=`, used at both construction
  sites (`:114` registry lambda, `:154` all-skipped fallback). **No module-level `app.state`
  default**: `brave_settings` has none (`retrieval_app.py:1378-1402` holds only attributes a
  lifespan-less test reads) and nothing outside the lifespan reads `searxng_settings`; the
  production-unreachable fallback at `retrieval_app.py:310` and the legacy branch at
  `pipeline/orchestrator.py:857` (removed by US-005) keep default construction. Register the key with
  spec 3's `KNOWN_CONFIG_KEYS` (ruling 12) and add its `docs/configuration.md` `### Top-level keys` row
  (`:430`; `search_brave_timeout_seconds` at `:439` is the shape) and its
  `kit_tools/docs/ENV_REFERENCE.md` `### Top-level keys` row (`:81-94`, `:90` is the shape).
- The shared doubles: promote `_ChunkStream` (`tests/test_brave_provider.py:715`), `_make_response`
  (`:230`), `_make_stream_cm` (`:247`) and `_client_patch` (`:259`) into `tests/fakes.py` (the shared
  home: `FakeStorage`, `FakeContentCache`, `FakeSearchProvider`; the module imports neither `httpx` nor
  `unittest.mock` today — add both). They are **parameterised**, not copied: `make_response(*, url,
  content_type="application/json", content=b"{}", headers=None, status_code=200)` builds a
  **stream-backed** `httpx.Response(status_code=..., headers=..., stream=ChunkStream([content]))` —
  never `content=`-backed: `aiter_raw()` on a `content=`-constructed Response raises
  `httpx.StreamConsumed`, a `RuntimeError` outside httpx's `HTTPError` tree, which would fall through
  both providers' httpx arms into the catch-all as `unexpected` (round-2 salty review; 26
  `_make_response(` and 27 `_client_patch(` sites in `tests/test_brave_provider.py` depend on it). Two
  consequences the helper's docstring states: a stream-backed Response synthesises no
  `content-length`, so a test that relies on that header sets it explicitly; and `aiter_bytes()` still
  works on the stream-backed shape, which is what keeps `tests/test_stage5_url_audit.py` green
  (`pipeline/stage5_url_audit.py:212` reads `aiter_bytes()`). Also exported: `make_stream_cm(response)`,
  `client_patch(target, *, response=None, stream_error=None)` taking the patch target as an argument
  (`_BRAVE_CLIENT` at `:255` becomes a call-site constant), `ChunkStream(chunks)` recording every raw
  chunk it yields and exposing `largest_chunk` and `chunks_yielded` (the **raw** side only), and
  `RecordingDecompressor(wbits)` — a thin proxy over `zlib.decompressobj(wbits)` recording `len(out)`
  for every `decompress(...)` return and exposing `largest_output` and `calls`, the seam US-003
  patches over `pipeline.bounded_body.zlib.decompressobj` to prove the decoded-side bound (the
  `patch(..., wraps=...)` spy idiom at `tests/test_brave_provider.py:740-746` is the precedent).
  Rebase `tests/test_brave_provider.py` on them. Rebase `tests/test_stage5_url_audit.py:43-86` —
  which defines `_make_response` (`:43`, 20 call sites), `_make_redirect` (`:60`), `_make_stream_cm`
  (`:69`) and `_stream_side_effect` (`:77`) with different defaults (`text/html; charset=utf-8`,
  `<html>OK</html>`, `https://example.com`) — by keeping its local `_make_response` and
  `_make_stream_cm` as **single-line delegating wrappers** into `tests/fakes.py` carrying the stage-5
  defaults (shape (a) of the round-2 story-quality review: the 20 call sites are untouched and no
  third *implementation* survives); `_make_redirect` and `_stream_side_effect` call those wrappers. The SearXNG transport doubles (`_searxng_client_patch`,
  `_mock_searxng_response`, `tests/test_search_providers.py`'s `_client_patch` / `_SEARXNG_CLIENT` /
  `_response`, `tests/test_app.py:1828`) are **not** touched here: production still calls
  `client.get`, so they stay `get`-shaped until US-003 switches both sides together (R40).
- Tests for the builder live in `tests/test_search_providers.py` (the SearXNG module's own test
  file; `brave_settings_from_config`'s tests in `tests/test_brave_provider.py` are the precedent);
  the lifespan-wiring test follows `tests/test_app.py`'s
  `test_lifespan_wires_the_configured_brave_timeout_into_the_client` (~`:2399`).
- Docs for the new key say today's semantics honestly — "per-socket-operation timeout on the SearXNG
  call (wall-clock budget from US-003 onward)" — and the row is rewritten by US-003.
- No hashed file moves: `pipeline/search_providers/*`, `retrieval_app.py` and `tests/` are outside
  `_REVISION_SOURCES`.

**Acceptance Criteria:**
- [ ] `SearxngSettings`, `SearxngConfigurationError`, `searxng_settings_from_config` and the module-local
      bounded-value helpers exist in `pipeline/search_providers/searxng.py`;
      `search_searxng_timeout_seconds` (default `10.0`, range 1–60) refuses boot out of range or
      wrong-typed with `SearxngConfigurationError`; `config.yaml` ships `search_searxng_timeout_seconds:
      10.0`; the key is in `KNOWN_CONFIG_KEYS` and has `docs/configuration.md` and `ENV_REFERENCE.md`
      top-level rows.
- [ ] `SearxngProvider.__init__` accepts `settings=` (default `SearxngSettings()`);
      `build_provider_chain` takes `searxng_settings=` and both of its construction sites use it; the
      lifespan reads the builder unconditionally and the production chain's `SearxngProvider.settings`
      reflects a configured value; there is no module-level `app.state.searxng_settings` default.
- [ ] `tests/fakes.py` exports the parameterised `ChunkStream` (with `largest_chunk` and
      `chunks_yielded`), the stream-backed `make_response` (a test asserts `aiter_raw()` and
      `aiter_bytes()` both work on it and that no `content-length` is synthesised), `make_stream_cm`,
      `client_patch(target, ...)` and `RecordingDecompressor` (with `largest_output` and `calls`);
      `grep -n 'class _ChunkStream' tests/` returns nothing; `grep -n 'def _make_response\|def
      _make_stream_cm' tests/` returns only `tests/test_stage5_url_audit.py`, whose two bodies are
      single delegating calls into `tests/fakes.py` with the stage-5 defaults; `_make_redirect` and
      `_stream_side_effect` call those wrappers; `tests/test_brave_provider.py` keeps no private copy
      of any of the four; `grep -rn MockTransport tests/` returns nothing.
- [ ] `BraveSettings.max_response_bytes` exists (default `1_048_576`, no config key), is the default
      of `_BRAVE_MAX_RESPONSE_BYTES`, and is not yet read by `search()` (a comment US-003 deletes).
- [ ] `pipeline/search_providers/searxng.py` is in spec 3 US-003's AST code-parity reader list.
- [ ] Behaviour preserved: `git diff --stat` lists no change for `pipeline/search_providers/brave.py`
      (beyond the one dataclass field and its default wiring), `pipeline/orchestrator.py`,
      `pipeline/contract.py`, `models.py` or `contract/`; `git diff pipeline/search_providers/searxng.py`
      shows no hunk inside `search()` (the settings object is stored, not yet read — the comment says
      so); the `get`-shaped SearXNG doubles in `tests/test_orchestrator.py`,
      `tests/test_search_providers.py` and `tests/test_app.py` are unchanged.
- [ ] Every pre-existing test in `tests/test_brave_provider.py` and `tests/test_stage5_url_audit.py`
      keeps its subject and expected value; the only edits are helper-body replacements, explicit
      `content-length` headers where a test relied on the synthesised one, and import lines.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Streamed, self-decoded, wall-clock-bounded bodies on both providers, and the counter

**Priority:** P1

**Description:** As an operator, I want each search provider to bound over-size responses in
*bytes* — plain or compressed — and to fail over-time responses at a wall-clock budget, without ever
refusing a body this build can decode, so a misbehaving or hostile peer degrades into a classified
provider failure instead of a memory spike or a hung `/search`, and a compressing reverse proxy in
front of my SearXNG is a counter I can read and a token I can grep, not an outage or a parse error.

**Independent Test:** Using `tests/fakes.py`'s `ChunkStream` / `make_response` / `make_stream_cm` /
`client_patch` / `RecordingDecompressor` (US-001), with `max_response_bytes` shrunk through
`SearxngSettings(...)` / `BraveSettings(...)` on both providers: each provider called directly returns
`ProviderFailure("hard_error", "body_too_large")` for (a) a plain stream of `max_response_bytes + 1`
bytes with no `Content-Length`, (b) a gzip stream whose decoded length is `max_response_bytes + 1`
(≥ 64× expansion — `gzip.compress(b"A" * 200_000)` is 230 bytes), (c) a gzip stream whose decoded
length crosses the bound **only in its final raw chunk**, and (d) a raw-deflate filler stream of
`5 × max_response_bytes` raw bytes that decodes to nothing (`b"\x00\x00\x00\xff\xff" * n` — round 2
measured 1,000,000 raw bytes → 0 decoded bytes, `eof` unset) — in every case
`RecordingDecompressor.largest_output ≤ max_response_bytes + 1`, `ChunkStream.largest_chunk` is the
raw chunk size the test supplied, and for (d) `sum(len(c) for c in ChunkStream.chunks_yielded) ≤ 4 ×
max_response_bytes` with the outcome `body_too_large`, not `timeout`; a truncated gzip stream and a
corrupt deflate stream return `ProviderFailure("hard_error", "malformed_body")` with `compressed`
set; a gzip stream whose decoded length is under the bound is served (SearXNG: results returned;
Brave: chunks returned); a deflate stream likewise (both zlib-wrapped and raw deflate); a stream with
`Content-Encoding: br`, `zstd`, `x-unknown` or `gzip, br` returns `ProviderFailure("hard_error",
"unsupported_encoding")` before any body byte is read; a stream that yields one byte every 0.2 s
against `SearxngSettings(timeout_seconds=0.5)` / `BraveSettings(timeout_seconds=0.5)` returns
`ProviderFailure("timeout", "timeout")` after more than 0.5 s (`time.perf_counter`, lower bound only);
both clients are constructed with `Accept-Encoding: identity` (asserted on the `AsyncClient` call's
`headers=`). At the `run_search_pipeline` level with a recording `SearchMetricsSink`: a
compressed-under-bound response, a compressed-over-bound response, a `br` response, a compressed
**429**, a compressed stream that **times out after its headers**, a compressed 200-with-zero-
results-plus-`unresponsive_engines` response on a `[searxng, brave]` chain (the re-classification
path, `orchestrator.py:895-919`) **and** the same shape on a configured `[searxng]`-only chain (the
ruling-28 serve-and-break at `:906-916`) each increment `provider_compressed_body` by exactly 1; a
plain response increments it by 0. The SearXNG
transport doubles are migrated to the stream shape in this story's first commit together with the
production switch, and every pre-existing SearXNG assertion keeps its subject and expected value
except the two `_FAILURE_CASES` entries named below.

**Implementation Hints:**
- `SearxngProvider.search` (`searxng.py:154-205`): replace `client.get(...)` + `len(resp.content)` +
  `resp.json()` (`:162-177`) with `async with client.stream("GET", ...)`, reading through the shared
  bounded-body helper below; status mapping (`:182-188`, `HTTP_STATUS_DETAIL_PREFIX`,
  `_RATE_LIMITED_STATUS`) runs before the body is read; parse with `json.loads(body)` inside
  `except ValueError` → `bad_json` (the `brave.py:346-351` arm — `json.JSONDecodeError` and
  `UnicodeDecodeError` are both `ValueError`; today's bare `except Exception` at `:176` narrows, and the
  one `_FAILURE_CASES` entry that only a broader arm could reach is deleted, below). Pin
  `follow_redirects=False` on the SearXNG `AsyncClient` (`brave.py:318` does; `searxng.py:159-161`
  inherits httpx's default today). Read `self.settings.timeout_seconds` / `.max_response_bytes` and
  delete `_SEARXNG_TIMEOUT_SECONDS` / `_MAX_SEARXNG_RESPONSE_BYTES` and the US-001 placeholder comment;
  rewrite the comment at `:57-59` (it describes the buffer-then-check order this story removes).
- One bounded-body helper, shared by both providers: a new module **`pipeline/bounded_body.py`**
  (a `pipeline/` module, not under `pipeline/search_providers/`, so a later fetch-path story can
  import it without a provider package importing outward or stage 5 importing inward; the implementer
  confirms it is absent from `_REVISION_SOURCES`, `pipeline/sanitizer_revision.py:12-21`, so it is not
  hashed) exposing `async def read_bounded_body(response, *, max_bytes) -> bytes` that raises three
  module-owned exceptions: `BodyTooLarge`, `UnsupportedEncoding` and `MalformedBody`. In order it
  (1) dispatches on `Content-Encoding` (lower-cased, whitespace-stripped) **before any body byte is
  read**: absent or `identity` → raw bytes are counted; `gzip` → `zlib.decompressobj(zlib.MAX_WBITS |
  16)`; `deflate` → `zlib.decompressobj(zlib.MAX_WBITS)`, retrying the first chunk with
  `-zlib.MAX_WBITS` on `zlib.error` (the raw-deflate fallback `httpx/_decoders.py:55-83` performs);
  any other token, or a list with more than one token → `UnsupportedEncoding` (this dispatch runs
  first so an undecodable body is refused whatever its `Content-Length` says); (2) fast-rejects on a
  `Content-Length` header longer than 20 characters or parsing above `max_bytes` (defence in depth —
  h11 already enforces `CONTENT_LENGTH_MAX_DIGITS = 20`, `h11/_headers.py:15,178`, and a real peer
  sending more digits surfaces as `httpx.RemoteProtocolError`, classified `connect_error` on SearXNG
  and `transport_error` on Brave; the `body_too_large` classification is the direct-construction
  path's, and the criterion says so); (3) reads `response.aiter_raw()` (never `aiter_bytes()`, whose
  decoder has no output cap — `httpx/_decoders.py:85-97`), counting **raw** bytes against
  `4 × max_bytes` on every path and raising `BodyTooLarge` when that raw budget is exceeded — a
  compressed stream that decodes to nothing must end as `body_too_large`, never as a `timeout` after a
  full budget of bandwidth and zlib CPU; (4) on the compressed path feeds each raw chunk as
  `decompressor.decompress(data, max_length=remaining + 1)` in a loop over `unconsumed_tail` until it
  is empty, `remaining = max_bytes - total`, and the moment `total > max_bytes` raises `BodyTooLarge`
  and stops reading — so no single `decompress()` call can return more than `remaining + 1` bytes and
  the peak held is `max_bytes + 1` plus the raw chunk (the `+ 1` is load-bearing: `zlib` treats
  `max_length=0` as *unlimited*, so `max_length=remaining` would remove the bound at exactly `total ==
  max_bytes` — say so in a comment); (5) **never calls `flush()`** — `zlib.decompressobj.flush(length)`
  sizes an output buffer and returns every remaining byte (round 2: 4,999,990 bytes from `flush(10)`)
  — the end-of-stream rule is: `decompressor.eof` set → done; `eof` unset, or `unconsumed_tail`
  non-empty, when `aiter_raw()` ends → `MalformedBody`; a `zlib.error` at any point → `MalformedBody`.
  Both providers map `BodyTooLarge` → `self._failure("hard_error", "body_too_large")`,
  `UnsupportedEncoding` → `self._failure("hard_error", "unsupported_encoding")` — one **new detail
  token on each provider's closed detail set** (`_SEARXNG_FAILURE_DETAILS` `:69-76`,
  `_BRAVE_FAILURE_DETAILS` `:155-165`; `detail` is log-only — the wire carries `failure_class`, so this
  is not a contract change) — and `MalformedBody` → the existing `malformed_body` on both. The helper
  never logs and never includes the header value or `str(exc)` in an exception message (the
  providers' closed vocabularies are the only thing that reaches a log). `brave.py:329-346` migrates to the helper (its own counter and the `int(content_
  length)` guard at `:331-335` go; the bound is read from `self.settings.max_response_bytes`, US-001),
  and the comment at `brave.py:146-150` is rewritten: decoded bytes are bounded by Forage's own
  decompressor; identity is requested; a compressed reply is served if decodable, refused as
  `unsupported_encoding` if not. `tests/test_brave_provider.py:781-811`
  (`test_compressed_body_whose_decoded_length_exceeds_cap_is_rejected`) already builds its fixture
  with `gzip.compress(raw)` (`:785`) and asserts `failure_class`/`detail` plus `loads_spy.assert_not_
  called()`; it is extended, not rewritten: the fixture is fed through `aiter_raw`, and it gains the
  `RecordingDecompressor.largest_output ≤ max_response_bytes + 1` assertion and the raw-bytes bound.
- The compression signal and the counter (`search.provider_compressed_body`, additive, ruling 5):
  `ProviderSearchResult` **and** `ProviderFailure` (`base.py`, internal dataclasses, not wire models)
  gain `compressed: bool = False`. The provider stamps a local from `Content-Encoding` at the **one
  header-read site**, before status mapping and before the `except TimeoutError` / `except
  httpx.*` / `except Exception` arms, and every `ProviderSearchResult` or `ProviderFailure` built after
  that point — served, over-bound, undecodable, malformed, a compressed non-2xx refused by status
  mapping, a wall-clock timeout after the headers, a transport error mid-body — carries it (a failure
  built *before* the headers arrived — connect error, timeout before headers — is never compressed).
  The orchestrator increments `metrics.provider_compressed_body` **immediately after `call_outcome`
  is produced** (`pipeline/orchestrator.py:882`, and on the orchestrator-side catch-all at `:888`),
  **before** the re-classification block at `:894` — the loop has three exits (the ruling-28
  lone-`searxng` serve-and-break at `:906-916`, the failure `continue` at `:933`, the ordinary
  serve-and-break at `:935-938`) and only that placement covers all three; the
  200-with-zero-results-plus-`unresponsive_engines` re-classification (`:895-919`) still copies the
  flag onto the `ProviderFailure` it builds, for dataclass honesty, not for counting. Five sites for the field:
  `SearchMetricsSink` Protocol (`pipeline/orchestrator.py:747-756`, and its "two counters" docstring
  sentence — three after this story), `_NullSearchMetrics.__init__` (`:759-771`),
  `SearchMetrics.__init__` (`retrieval_app.py:875-886`, a plain class), the `/metrics` handler dict
  (`:1517-1524`, appended after `policy_unknown_provider`), and `SearchMetricsResponse` (`:488-533`,
  appended last; description: "non-identity `Content-Encoding` responses seen from any provider,
  whatever the outcome — served, refused as `body_too_large`, or refused as `unsupported_encoding`; a
  zero count means no compression"). `extra="forbid"` on the metrics models means the model and the
  class move in the same commit (`GOTCHAS.md` "Adding a /metrics counter"); the order guards are
  `tests/test_contract_metrics.py:135` and `:161`; that module hosts the counter's tests; the
  schema-fullness test at `:272-292` requires the description; and `SearchMetricsResponse`'s field set
  is pinned **literally** a second time in `tests/test_contract_schema.py:368-395`
  (`test_search_metrics_response_1_2_0_field_set_is_pinned_exactly`) — extend that literal set with
  the new name and retitle the test or its docstring for a 1.3.0 field set (spec 6 US-004 hits the
  same pin). Update `kit_tools/docs/MONITORING.md:
  433-439` ("Adding a counter to `/metrics`") so the procedure names the `SearchMetricsSink` Protocol
  and `_NullSearchMetrics` sites and says `SearchMetrics` is a plain class (spec 6 US-004 cites the
  same runbook).
- The wall-clock budget: wrap **only the HTTP interaction** — from opening the stream to the last
  body chunk — in `async with asyncio.timeout(self.settings.timeout_seconds)` on both providers
  (`json.loads` and result building sit outside it; `asyncio.timeout` can only fire at an `await`).
  It raises the **builtin `TimeoutError`**, which is not in httpx's hierarchy — the existing arms at
  `searxng.py:179` / `brave.py:352` catch `httpx.TimeoutException` and would not see it — so add
  `except TimeoutError` (or a combined arm) **before** the `except Exception` catch-all (search epic
  ruling 27), mapped to `self._failure("timeout", "timeout")`. Keep the httpx `timeout=` (per
  operation). Nest `asyncio.timeout` inside the `AsyncClient` context so the client's `__aexit__`
  runs on expiry; the hermetic suite (pytest-socket, patched `AsyncClient`) cannot exercise httpx's
  real teardown under cancellation — Technical Considerations records that as accepted untested.
  The defaults do not move (SearXNG 10.0 s, Brave 15.0 s) but their *meaning* tightens from
  per-socket-operation to whole-interaction: a SearXNG that takes 3 s to connect and 8 s to stream
  succeeds today and is `timeout` after this story, and SearXNG fans out to four engines and waits
  for the slowest. No fan-out latency distribution has been measured; the defaults are kept with that
  uncertainty stated, and both timeout rows in `docs/configuration.md` and `ENV_REFERENCE.md` tell
  operators of slow instances to raise `search_searxng_timeout_seconds` (Edge Cases records the
  tightening; on a `[searxng, brave]` chain a new free-path `timeout` buys a paid call, so the
  sentence is not optional).
- `Accept-Encoding: identity` is a **new header** on both clients (`headers={"Accept-Encoding":
  "identity"}` on each `AsyncClient` construction; nothing sends it today — `grep -rni
  accept-encoding pipeline/ tests/` is empty, and httpx's built default is `gzip, deflate`). It buys
  nothing from a proxy that ignores it (Forage decodes anyway) and one thing from a peer that
  honours it (no decode at all), and it collides with SearXNG's optional bot limiter: with
  `server.limiter: true` the `http_accept_encoding` rule refuses requests whose header lacks
  `gzip`/`deflate` with **429 on the first request** (`kit_tools/docs/GOTCHAS.md:370`,
  `docs/searxng.md:74`). The baked image ships `limiter: false`; `docs/searxng.md`'s "Turning it on
  anyway" section and the GOTCHAS entry gain one sentence each naming the header and that a
  limiter-enabled instance answers Forage with 429 → `rate_limited` (the existing classification, and
  on a `[searxng, brave]` chain a paid call) unless the limiter's pass list admits Forage. The
  overruled alternative (round-2 salty review: drop the header entirely) is recorded in Decisions Made.
- The SearXNG test-seam migration (R40) is this story's **first commit, together with the
  production switch**, because production and doubles must change sides at once: rewrite
  `tests/test_orchestrator.py:881-905` (`_mock_searxng_response`, `_searxng_client_patch` — 19 and
  23 uses at planning time; `grep -c` is the authority) so `_mock_searxng_response` returns a **real
  `httpx.Response`** built by the promoted `make_response(...)` with body `json.dumps(data).encode()`,
  wrapped by `make_stream_cm` — today it returns a bare `MagicMock()` (`:887`), and a `MagicMock`
  reaching `read_bounded_body` yields a truthy `Content-Encoding` and lands every SearXNG test on
  `unsupported_encoding` (round-2 codebase-fit review); migrate the direct
  `pipeline.search_providers.searxng.httpx.AsyncClient` patches at `tests/test_orchestrator.py:2417`,
  `:2439` and the `.get.call_count` assertion at `:2505`; in `tests/test_search_providers.py` delete
  the local `_client_patch` (`:311-331`) and **adopt the promoted `client_patch(target=_SEARXNG_CLIENT,
  ...)`** (decided: no thin wrapper, no name collision; its `get_error=` keyword becomes
  `stream_error=`), rebuild `_response` (`:272-300`, today a `MagicMock()` at `:296`) on
  `make_response` (the docstring "the body bound is read off len() before json() runs" is rewritten), the `client.get.call_args` assertions
  at `:378,:388` (now `client.stream.call_args`), and the `_FAILURE_CASES` matrix (`:509-593`,
  consumed at `:611`, `:630`, `:681`): the `json_error=ValueError("not json")` case becomes non-JSON
  body bytes (`b"not json"`), and the `json_error=RuntimeError("decoder exploded")` case is
  **deleted** (with `json.loads(body)` no response double can inject an arbitrary exception, and a
  `RuntimeError` from `json.loads` is not a real input shape — recorded in Decisions Made); migrate
  `tests/test_app.py:1828-1833` (`inner.get.side_effect` → a stream error). Every other pre-existing
  assertion keeps its subject and expected value; the permitted edits are helper bodies, mock-attribute
  renames (`client.get.*` → `client.stream.*`) and the named `_FAILURE_CASES` rows. `raise_for_status()`
  at `searxng.py:171` is inside the replaced range; the status mapping it fed (`:182-188`) survives
  and runs on the streamed response's status before any body byte is read — the replaced lines are
  the read, the `len()` check and the `json()` call, not the status semantics.
- `_FAILURE_CASES` grow with the new peer-reachable paths so the closed-vocabulary and no-leak
  sweeps cover them by construction (round-2 security review): SearXNG's matrix
  (`tests/test_search_providers.py:509-593`, consumed by `test_every_detail_is_in_the_closed_
  vocabulary` `:625-644` and `test_no_log_record_carries_exception_text_or_the_base_url` `:668-703`)
  gains four rows — `unsupported_encoding` (an attacker-controlled `Content-Encoding: br`),
  helper-raised `body_too_large`, `zlib.error` → `malformed_body`, and the builtin `TimeoutError` arm
  — for **fifteen** rows after the one deletion and one rewrite; Brave's `_FAILURE_CASES` gains an
  `unsupported_encoding` row, `test_the_twelve_cases_exercise_every_closed_token`
  (`tests/test_brave_provider.py:1330-1332`, which asserts `{case.detail} == set(_BRAVE_FAILURE_
  DETAILS)` **and** `len(_BRAVE_FAILURE_DETAILS) == 12`) becomes the thirteen-case test, and the three
  prose sites that hard-code the count — `brave.py:152` ("Twelve fixed tokens"), `:290`
  ("twelve-token vocabulary"), `:490` ("not one of the twelve fixed tokens") — are rewritten; `grep
  -rn 'twelve' pipeline/ tests/` is the sweep. One explicit assertion joins the no-leak sweep on both
  providers: no log record carries the raw `Content-Encoding` header value (the one new
  attacker-authored string this story routes into a failure decision).
- Timed tests: the suite's only real sleeps are 0.05 s (`tests/test_app.py:978,1007,1123`) and
  `tests/fakes.py::ManualClock` exists to avoid them; this story knowingly adds two ~0.6 s tests
  (one per provider) asserting `elapsed > budget` **and nothing else** (no upper bound — a contended
  runner cannot flake a one-sided lower bound), and names that departure in the test module docstring.
- Docs (R39: the grep is the criterion): `docs/configuration.md:439` (`search_brave_timeout_seconds`)
  becomes "wall-clock budget for one Brave call — connect, headers and body together; a request on
  an N-provider chain can take the sum of the configured budgets" and its "10 s + this value"
  sentence is rewritten; the `search_searxng_timeout_seconds` row says the same for SearXNG;
  `kit_tools/arch/patterns/ERROR_HANDLING.md:244` (SearXNG query row — becomes the config key,
  wall-clock), `:245` (Brave row), `:246` (chain traversal row — it becomes *true* here);
  `kit_tools/docs/API_GUIDE.md:58` ("10 s per provider call"); `kit_tools/docs/TROUBLESHOOTING.md:178`
  and `:464` ("10 s"); `grep -rn '10 s' kit_tools/arch/patterns/ERROR_HANDLING.md kit_tools/docs/
  API_GUIDE.md kit_tools/docs/TROUBLESHOOTING.md docs/configuration.md` is the sweep (seven hits at
  planning time — `ERROR_HANDLING.md:244` and `:246` are two of them; the implementer re-runs the
  grep at HEAD; `TROUBLESHOOTING.md:702` is Poppy's and stays); `docs/configuration.md`'s "10 s +
  this value" sentence becomes "10 s + this value on a `searxng,brave` chain — 25 s at the shipped
  defaults". A second sweep covers the closed `detail` vocabulary pages: `grep -rn 'malformed_body'
  kit_tools/` finds every page that enumerates the token set (`kit_tools/docs/TROUBLESHOOTING.md:176`
  — the Brave `detail` grep table — and `:178`, the exhaustive SearXNG list; `kit_tools/arch/patterns/
  ERROR_HANDLING.md:164`), and each enumerated set gains `unsupported_encoding`. `kit_tools/docs/
  MONITORING.md` gains the `search.provider_compressed_body` row with the header-seen semantics and
  the `unsupported_encoding` token as the "this build cannot decode it" signal, and both timeout rows
  in `docs/configuration.md` / `ENV_REFERENCE.md` carry the "raise it for a slow instance" sentence.
  `kit_tools/arch/SECURITY.md`'s documented-non-vulnerabilities table (`:336`) gains one row, worded
  exactly: a slow upstream can hold a `/search` for **at least** the sum of the configured
  per-provider budgets — 10 s on the default `[searxng]` chain, 25 s on `searxng,brave` at the
  shipped defaults (`config.yaml:20` Brave 15.0 s; there is no configuration that yields 20 s), up to
  2 × 60 s at the ranges' maxima — plus parse, sanitization and classification time, which sit
  outside the budget; there is no chain-wide deadline and no concurrency ceiling on the
  provider-fetch phase (the one ceiling that exists is spec 2 US-006's classification semaphore, which
  bounds stage 3, not the fetch); network placement and the per-call budget are the controls. `grep
  -n '25 s' kit_tools/arch/SECURITY.md docs/configuration.md` is the criterion for the number.
- Rotation (ruling 6, R32): this story moves **two hashed files** — `pipeline/orchestrator.py` (the
  Protocol, `_NullSearchMetrics`, the loop increment, the re-classification copy) and
  `pipeline/contract.py` (the docstring line). Measure by reverting each in turn with a both-reverted
  control that reproduces the pre-story hash (the search epic's ninth rotation is the precedent) and
  record at the five sites.
- Window mechanics (R36, verbatim): append the docstring line (`* ``1.3.0`` — …` format), run
  `uv run python -m scripts.export_contract`, re-create `tests/golden/contract_1_3_0.json` via
  `_SCHEMA_MODELS`, append the field to `_EXPECTED_ONE_THREE_ZERO_DIFF` in
  `tests/test_contract_schema.py`, refresh the four anchor-quoting pages, and run `--check`. For a
  `/metrics` field the golden and the diff list do not move (`/metrics` models are outside
  `_SCHEMA_MODELS`, `tests/test_contract_schema.py:28-35,81`, and are pinned by
  `tests/test_contract_metrics.py`): the implementer runs every step, confirms the golden is
  byte-identical and the diff list needs no entry, and records both facts in Implementation Notes.

**Acceptance Criteria:**
- [ ] Both providers read through `pipeline/bounded_body.py::read_bounded_body` over `aiter_raw()`,
      each from its own `settings.max_response_bytes`; `grep -n 'aiter_bytes' pipeline/search_providers/
      pipeline/bounded_body.py` returns nothing; `grep -n 'flush(' pipeline/bounded_body.py` returns
      nothing; the four over-bound shapes of the Independent Test — plain, ≥ 64×-expansion gzip,
      overflow-in-the-final-chunk gzip, and the zero-expansion raw-deflate filler — each return
      `ProviderFailure("hard_error", "body_too_large")` with `RecordingDecompressor.largest_output ≤
      max_response_bytes + 1` (the largest value returned by any zlib call), reading stopped at that
      point (`ChunkStream.chunks_yielded` shows no further chunk), and, for the filler, total raw bytes
      read ≤ `4 × max_response_bytes` and the outcome not `timeout`; pinned on both providers.
- [ ] A truncated gzip stream and a corrupt deflate stream return `ProviderFailure("hard_error",
      "malformed_body")` with `compressed` set, on both providers; `eof` unset at end of stream is
      `malformed_body`, never a served partial body; pinned.
- [ ] `gzip`, zlib-wrapped `deflate` and raw `deflate` bodies under the bound are served; `br`, `zstd`,
      an unknown token and `gzip, br` return `ProviderFailure("hard_error", "unsupported_encoding")`
      before any body byte is read, whatever `Content-Length` says; both `AsyncClient` constructions
      pass `Accept-Encoding: identity` explicitly (asserted on the construction call — this is a new
      header); `docs/searxng.md`'s limiter section and `kit_tools/docs/GOTCHAS.md`'s limiter entry
      each name the header and its 429 → `rate_limited` consequence on a limiter-enabled instance;
      pinned on both.
- [ ] A `Content-Length` longer than 20 characters or above the bound is `body_too_large` on both
      providers on the direct-construction path, with the criterion text stating that a real peer
      surfaces as `connect_error` / `transport_error` through h11's own 20-digit bound; pinned.
- [ ] Both providers return `ProviderFailure("timeout", "timeout")` for a trickling body after more
      than the budget has elapsed (lower bound only); the budget covers the HTTP interaction only (a
      slow parse after a fast body is never a timeout); the arm catches the builtin `TimeoutError`
      before the catch-all; the SearXNG client is constructed with `follow_redirects=False`; pinned.
- [ ] `ProviderSearchResult.compressed` and `ProviderFailure.compressed` exist and are stamped from
      the one header-read site before status mapping and before every exception arm; at the
      `run_search_pipeline` level with a recording sink, a served, an over-bound, an undecodable, a
      malformed, a compressed-429, a compressed-timeout-after-headers, a re-classified compressed
      response on `[searxng, brave]` **and** a compressed 200-zero-results response on a configured
      `[searxng]`-only chain each increment `provider_compressed_body` by exactly 1 and a plain
      response by 0; the increment site is immediately after `call_outcome` is produced
      (`orchestrator.py:882` / `:888`), before the re-classification block; pinned.
- [ ] `search.provider_compressed_body` is on `/metrics` (`SearchMetricsSink`, `_NullSearchMetrics`,
      `SearchMetrics`, the handler dict, `SearchMetricsResponse` with the header-seen description); the
      two order guards in `tests/test_contract_metrics.py` pass with the key appended;
      `tests/test_contract_schema.py::test_search_metrics_response_1_2_0_field_set_is_pinned_exactly`
      carries the new name in its literal set; `MONITORING.md:433-439`'s runbook names the Protocol
      and `_NullSearchMetrics` sites.
- [ ] Window mechanics (R36): docstring line appended in the `* ``1.3.0`` — …` format; `uv run
      python -m scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; the field appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` where applicable; the
      four anchor-quoting pages refreshed; `uv run python -m scripts.export_contract --check` green —
      with Implementation Notes recording that the golden is byte-identical and the diff list carries
      no `/metrics` entry, and why.
- [ ] `FAILURE_CLASSES` (`base.py:21,32`) is unchanged; `_SEARXNG_FAILURE_DETAILS` and
      `_BRAVE_FAILURE_DETAILS` each gain exactly `unsupported_encoding`; the only other edit to
      `base.py` is the additive `compressed` field on the two dataclasses; `tests/test_brave_provider.py`'s
      closed-token test asserts thirteen tokens with a new `unsupported_encoding` `_FAILURE_CASES` row,
      and `grep -rn 'twelve' pipeline/ tests/` returns nothing.
- [ ] The SearXNG doubles are stream-shaped and real: `grep -nE '(mock_client|client|inner|
      return_value)\.get\.' tests/test_orchestrator.py tests/test_search_providers.py tests/test_app.py`
      returns nothing (eight sites at planning time — `test_orchestrator.py:903,905,2122,2403,2505`,
      `test_search_providers.py:325,327,378,388`, `test_app.py:1830`; `grep -c` is the authority);
      `grep -n 'MagicMock()' ` over `_mock_searxng_response` and `tests/test_search_providers.py::
      _response` returns nothing (both are built by `make_response`); the local `_client_patch` in
      `tests/test_search_providers.py` is gone and the promoted `client_patch` is used; SearXNG's
      `_FAILURE_CASES` has fifteen rows (the `RuntimeError` row deleted, the `ValueError` row now
      `b"not json"`, four new rows for `unsupported_encoding`, helper `body_too_large`, `zlib.error` →
      `malformed_body` and the builtin `TimeoutError` arm), and the no-leak sweep asserts no record
      carries the raw `Content-Encoding` value on either provider; every other pre-existing SearXNG
      assertion keeps its subject and expected value.
- [ ] Docs: `grep -rn '10 s' kit_tools/arch/patterns/ERROR_HANDLING.md kit_tools/docs/API_GUIDE.md
      kit_tools/docs/TROUBLESHOOTING.md docs/configuration.md` returns only `TROUBLESHOOTING.md:702`
      (Poppy's healthcheck note); both timeout rows in `docs/configuration.md` contain "wall-clock
      budget" and the raise-it-for-a-slow-instance sentence; every page `grep -rn 'malformed_body'
      kit_tools/` returns enumerates `unsupported_encoding` beside it; `MONITORING.md` documents the
      counter; `SECURITY.md` carries the slow-upstream row with `grep -n '25 s' kit_tools/arch/
      SECURITY.md docs/configuration.md` hitting both files and no `20 s` figure anywhere in either.
- [ ] `sanitizer_revision` rotation measured (revert `pipeline/orchestrator.py` and
      `pipeline/contract.py` each in turn, with a both-reverted control reproducing the pre-story
      hash) and recorded at the five sites ruling 6 names — `docs/bootstrap-notes.md`, `CLAUDE.md`,
      `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md` (divergence table) and
      `kit_tools/arch/CODE_ARCH.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Outbound SearXNG query cap — `search_searxng_query_max_chars`

**Priority:** P1

**Description:** As an operator, I want the free provider's outbound query capped exactly like the
paid one, so a caller cannot buy a paid call by making SearXNG fail on request-URI length.

**Independent Test:** Following `tests/test_brave_provider.py:833-857`
(`test_a_five_thousand_character_query_is_truncated_to_the_query_cap`): a 5,000-character query
through `run_search_pipeline` on a `[searxng]` chain built by `build_provider_chain` (not a directly
constructed provider) reaches the transport as `client.stream.call_args.kwargs["params"]["q"]` of
exactly `search_searxng_query_max_chars` characters, the request is accepted end to end, and
`SearchResponse.query` echoes the original; a 400-character CJK query is sent whole (400 characters,
≈ 3.6 KB percent-encoded — under the 8 KB request-line limit the cap is sized against).

**Implementation Hints:**
- `SearxngSettings` (US-001) gains `query_max_chars: int = 400`; `searxng_settings_from_config` reads
  `search_searxng_query_max_chars` with range **50–400** — the same `_MIN_/_MAX_BRAVE_QUERY_MAX_CHARS`
  bounds Brave uses (`brave.py:189-190`), refused at boot with `SearxngConfigurationError`; the
  shipped `config.yaml` gains `search_searxng_query_max_chars: 400`. Rationale for 400 on the free
  floor: 400 characters of 4-byte UTF-8 percent-encode to at most 4,800 bytes, under the common 8 KB
  request-line limit, so the cap is a guardrail, not routine truncation; the alternative (a byte cap
  on the encoded `q`) is rejected as harder to document.
- Truncate the outbound copy only, `outbound_query = query[: self.settings.query_max_chars]` beside
  the `params=` (the `brave.py:310-314` pattern); `SearchRequest.query` keeps its wire shape (search
  epic ruling 30) and `SearchResponse.query` still echoes the caller's string — the caller sees no
  flag; say so in the `docs/configuration.md` row ("results reflect the first N characters; the
  echoed `query` is the caller's").
- Wiring is already through `build_provider_chain(searxng_settings=...)` (US-001); this story only
  adds the field, the key, the `KNOWN_CONFIG_KEYS` entry (ruling 12), the `docs/configuration.md`
  top-level row and the `ENV_REFERENCE.md` `### Top-level keys` row.
- `kit_tools/arch/SECURITY.md:352` (operator-spend row): add the sentence that a URI-length failure at
  SearXNG can no longer buy a paid call (this story), beside the already-closed paths
  (post-sanitization omission — search epic ruling 17; unknown policy names — ignored, never a 422).
- No hashed file, no contract movement (`pipeline/search_providers/*` and `retrieval_app.py` are not
  in `_REVISION_SOURCES`).

**Acceptance Criteria:**
- [ ] `search_searxng_query_max_chars` exists in `config.yaml` (default 400), is bounded 50–400 at
      boot (`SearxngConfigurationError` out of range), has `docs/configuration.md` and
      `ENV_REFERENCE.md` rows and a `KNOWN_CONFIG_KEYS` entry.
- [ ] The outbound SearXNG `q` is truncated to the cap on the chain `build_provider_chain` builds
      (asserted on `client.stream.call_args`); `SearchRequest.query` is unchanged on the wire;
      a query shorter than the cap is sent untouched; a 400-character multi-byte query is sent whole.
- [ ] `SECURITY.md`'s operator-spend row names the SearXNG URI-length path as closed by this story.
- [ ] `git diff --stat` shows no change under `pipeline/orchestrator.py`, `pipeline/contract.py`,
      `models.py` or `contract/`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Cost-monotonic policy by construction — the paid-prefix rule

**Priority:** P1

**Description:** As an operator, I want the per-request policy unable to promote a later configured
paid provider over an earlier one, so cost-monotonicity holds when a second paid backend is
configured, not just today when there is one — and I want the one caller-visible consequence stated,
not discovered.

**Independent Test:** In `tests/test_search_policy.py`, the per-shape `selections` list built inside
`test_effective_chain_is_a_bounded_subset_of_the_configured_chain` (`:62-67`; the module-level
`_REQUEST_PROVIDER_SELECTIONS` at `:44` holds only static names and cannot express "the configured paid
names minus the first") gains a later-paid-only selection, and the loop over every `_CHAIN_SHAPES`
entry and both `allow_paid_fallback` values additionally asserts the effective chain's paid providers
are a prefix of the configured paid sequence; the named
`test_a_second_paid_provider_cannot_be_reached_by_skipping_the_first` shows `providers: ["paidb"]` on
`[free, paidA, paidB]` yields `[free]`, never `[free, paidB]`; and in `tests/test_app.py`,
`test_a_later_paid_only_selection_on_an_all_paid_chain_is_the_policy_422` shows `providers: ["paidb"]`
on a configured `[paidA, paidB]` chain returns 422 `search_unavailable` with reason
`policy_excluded_all_providers` (today it would return `paidB`'s results); every existing case
passes unchanged.

**Implementation Hints:**
- `apply_request_policy` (`pipeline/search_providers/policy.py:35-77`): rewrite step 3 so the kept
  paid providers are the longest prefix of the configured paid sequence whose every member is in the
  named set (a named paid provider after an un-named one is dropped, never promoted); keep steps 1,
  2 and 4 as they are. Write the four steps as four statements each preceded by a `# step N —`
  comment whose text matches the docstring's numbered step (finding -060); a test asserts the
  docstring enumerates exactly the `# step N` markers present in the body (`inspect.getsource`), so
  the mirror is checkable, not a review preference. Update the docstring's algorithm and its
  "output is always *chain* minus a subset of its paid providers" sentence to state the prefix rule.
- The stale comment at `tests/test_search_policy.py:40-43` claims five selections the list never
  held; rewrite it to describe what `:44-47` and the per-shape list at `:62-67` actually build.
- The all-paid-chain consequence: on a configured chain with no free provider, a later-paid-only
  selection leaves the effective chain empty, and `retrieval_app.py:1799-1810` already maps an empty
  effective chain to `PipelineError(search_unavailable, POLICY_EXCLUDED_ALL_PROVIDERS)`. Nothing new
  is built; the behaviour is **named** — in the Edge Cases, in a `tests/test_app.py` test, and in the
  1.3.0 docstring line beside the description change ("on an all-paid configured chain a later-paid-
  only selection is now the policy 422"), so the window record is not read as schema-additive-only.
  **Governance (CLAUDE.md invariant 4):** `contract/GOVERNANCE.md:102` puts "a status code a client
  observes changes" in the MAJOR row, so the story records a ruling in GOVERNANCE's "Recorded
  rulings" (the next free letter) rather than riding a docstring line: the flip is classified an
  **expedited security-tightening MINOR** (row 6, § "Example 6 in full") on the reachability argument
  that today no production chain can reach it — `_KNOWN_PROVIDER_NAMES` holds exactly one paid name
  and `parse_provider_names` collapses duplicates, so a multi-paid configured chain is
  unconstructible (the same unreachability reasoning as the documented-but-unreachable `/extract`
  413, `GOVERNANCE.md:213`) — with the compatibility note that the case becomes reachable the day
  T3.1 registers a second paid backend, and that story inherits this ruling instead of an
  undocumented MAJOR.
- Observability of a prefix drop: **none** (no counter, no log — INFO never renders, GOTCHAS, and a
  WARNING per request is noise). The diagnosis path is written down instead: `SearchRequest.providers`'
  description (`models.py:302-315`) states the prefix rule and that `provider_used` never names a
  dropped provider, and `kit_tools/docs/API_GUIDE.md`'s `providers` sentence adds "if you named a paid
  provider and `provider_used` is not it, check its position in `FORAGE_SEARCH_PROVIDERS`". The
  description change moves `contract/openapi.yaml` and the golden (`SearchRequest` is in
  `_SCHEMA_MODELS`): R36 applies. `pipeline/contract.py` is hashed: measure and record the rotation
  (ruling 6, five sites).
- `policy_unknown_provider` keeps per-entry counting (R14): `retrieval_app.py:1799` and
  `MONITORING.md:150` are **not** edited. Record finding -054 as an accepted risk in
  `kit_tools/arch/SECURITY.md`'s documented-non-vulnerabilities table, worded honestly: the increment
  is one per entry with no cap on entries; `/search` carries no body cap (the size middlewares
  early-return unless `scope["path"] == "/extract"`, `retrieval_app.py:1041,1089`) and
  `SearchRequest.providers` has no `max_length`, so a caller-supplied array is parsed in full before
  the eight-entry slice — both the counter value and the parse cost scale with an uncapped request
  body; the impact is a wrong counter value and CPU, not a resource exhaustion the counter bounds;
  network placement is the control; a body cap is a rejection change reserved for its own GOVERNANCE
  ruling.
- Tests: `tests/test_search_policy.py` (property harness: `_CHAIN_SHAPES` `:34`, `_chain` `:28`,
  `_is_subsequence` `:22`; `hypothesis` is not a dependency), `tests/test_app.py` for the 422 mapping,
  plus the contract regeneration tests.
- Window mechanics (R36, verbatim): append the docstring line (`* ``1.3.0`` — …` format), run
  `uv run python -m scripts.export_contract`, re-create `tests/golden/contract_1_3_0.json` via
  `_SCHEMA_MODELS`, append the field to `_EXPECTED_ONE_THREE_ZERO_DIFF` in
  `tests/test_contract_schema.py` (a description change adds no field — record that the list needs no
  entry), refresh the four anchor-quoting pages, and run `--check`.

**Acceptance Criteria:**
- [ ] `apply_request_policy` keeps only a prefix of the configured paid providers; the extended
      per-shape property loop and `test_a_second_paid_provider_cannot_be_reached_by_skipping_the_first`
      pass; every existing `tests/test_search_policy.py` case passes unchanged; the `:40-43` comment is
      accurate.
- [ ] `test_a_later_paid_only_selection_on_an_all_paid_chain_is_the_policy_422` passes: 422
      `search_unavailable`, reason `policy_excluded_all_providers`, no provider called.
- [ ] The four docstring steps correspond to four `# step N —` marked statements, and a test asserts
      the docstring's numbered steps and the body's markers agree.
- [ ] `SearchRequest.providers`' description states the prefix rule and the `provider_used` diagnosis
      sentence; `API_GUIDE.md` matches; the 1.3.0 docstring line names the all-paid-chain consequence.
- [ ] `contract/GOVERNANCE.md`'s "Recorded rulings" carries the all-paid-chain ruling (expedited
      security-tightening MINOR on the one-paid-name reachability argument, the `/extract` 413
      precedent cited, the T3.1 note), and `tests/test_governance_docs.py`'s ruling-marker test counts
      it.
- [ ] Window mechanics (R36): docstring line appended in the `* ``1.3.0`` — …` format; `uv run
      python -m scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; `_EXPECTED_ONE_THREE_ZERO_DIFF` reviewed (no field added — recorded); the four
      anchor-quoting pages refreshed; `uv run python -m scripts.export_contract --check` green;
      `contract_1_2_0.json` untouched.
- [ ] `policy_unknown_provider`'s unit is unchanged (`retrieval_app.py`'s increment site and
      `MONITORING.md`'s row unedited); `SECURITY.md` carries the accepted-risk row for finding -054 in
      the honest wording above.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce on `pipeline/contract.py`) and
      recorded at the five sites ruling 6 names.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-005: The `run_search_pipeline` cleanup — one rotation, wire output and sink pinned

**Priority:** P2

**Description:** As a maintainer, I want the provider loop in `run_search_pipeline` extracted,
deduplicated and stripped of its legacy parameter and of result-URL logging, in a single recorded
rotation, so the next hardening change to search lands in a function a reviewer can hold in their
head — with the wire output, the metrics sink and the exhaustion payloads pinned so the two deliberate
deltas are the only ones.

**Independent Test:** Before the change, capture for three representative runs built from the
in-repo synthetic fixtures and the stream doubles (a clean SearXNG success, a fallback to Brave with
`provider_errors`, and a run with omissions under every `omitted_by_reason` token — never from a live
provider): `SearchResponse.model_dump()` with `request_id` removed (the one non-deterministic field,
`pipeline/orchestrator.py:848`), **and** the recording `SearchMetricsSink`'s counter dict after the
run; and for the two exhaustion outcomes (a `[searxng]`-only configured chain whose provider fails,
and a `[searxng, brave]` chain where both fail) capture the raised `PipelineError`'s `error`,
`reason` and status. Commit the captures under `tests/fixtures/search/`; after the change the same
inputs produce identical dumps, identical counter dicts and identical raised payloads
(`tests/test_search_pipeline_pins.py`), every pre-existing search test passes with only the
`searxng_url=` → `providers=` migration in the diff, and an omitted result's URL appears in no log
record.

**Implementation Hints:**
- Extract the loop at `pipeline/orchestrator.py:874-946` into a module-level
  `async def _query_provider_chain(...) -> _ServedChain` (a small frozen dataclass carrying
  everything the post-loop code reads today: the serving provider, the raw results, the
  `max_results` slice bound (`serving_max_results`, `:951`), `unresponsive_engines` (`:953-955` →
  `SearchResponse.unresponsive_engines` `:1101`), `content_kind` (`:1056`), `provider_errors` and
  `fallback_fired`) so `run_search_pipeline` reads as query → sanitize → respond; one of the three
  committed fixture runs carries a non-empty `unresponsive_engines` so the pin covers that field. **The sink mutations stay inside the helper**, at the same points relative to
  the provider calls as today (`metrics.fallback_fired += 1` at `:875-877`, `metrics.paid_calls += 1`
  at `:879-880`, US-003's `provider_compressed_body` increment) — the helper takes the sink and
  mutates it; it does **not** return deltas for the caller to apply (a raise mid-chain must leave the
  increments already landed, as today). One `_log_provider_failure(provider, failure_class, detail)`
  replaces the two WARNING statements at `:907-912` and `:925-930` (finding -035), with
  `_UNRESPONSIVE_ENGINES_DETAIL = "unresponsive_engines"` beside it.
- `_legacy_searxng_codes(configured_chain)` (`:670`) is evaluated once before the loop and reused at
  the serve-and-break branch inside the loop (`:906` — the search epic's ruling-28 branch, not a
  raise site) and at the exhaustion raise (`:946-950`, which selects between `_searxng_pipeline_error`
  and `_search_unavailable_error`; finding -038); the two identical `ValueError("...empty provider
  chain...")` sites (`:842-846`, `:941-945`) collapse to the upfront guard; the comment at `:903`
  says "configured chain of exactly one `searxng` (search epic ruling 28)" (finding -039).
- Retire `searxng_url=` from `run_search_pipeline` only (`:777` signature, `:817-824` docstring,
  `:856-858` branch). `build_provider_chain(*, searxng_url=)` (`pipeline/search_providers/__init__.py:
  86`) is **deliberately untouched** — it is the production factory's keyword. Migrate every
  `run_search_pipeline(..., searxng_url=...)` call site (R40): `grep -c 'searxng_url=' tests/
  test_orchestrator.py` is the authority (19 at planning time), plus the multi-line calls in
  `tests/test_search_providers.py` (`grep -n -B6 'searxng_url=' tests/test_search_providers.py | grep
  run_search_pipeline`); the two tests that exist only to exercise the legacy parameter —
  `test_a_malformed_searxng_url_still_yields_a_422` and `test_a_supplied_chain_makes_searxng_url_unused`
  — are removed, since malformed-URL coverage already lives provider-side in `_compute_origin`
  (`searxng.py:93-125`). Migrate to `providers=[SearxngProvider(url)]` through the stream-shaped
  `_searxng_client_patch` US-003 left behind.
- Omission INFO lines (`:999-1003`, `:1023-1027`, `:1031-1035`) log the closed reason token and the
  validated `domain` (`SearchResult.domain`, lower-cased hostname), never the result URL (finding
  -043: on a fallback the URL comes from the Brave body, and telemetry is metadata only — search epic
  owner decision 6). The `invalid_url` omission at `:974-977` already carries no URL and is untouched.
  Extend `tests/test_orchestrator.py::test_provider_failures_leak_no_url_credential_or_exception_text`
  (grep the name; the orchestrator's no-leak family) with the per-record `getMessage()` sweep pattern
  of `tests/test_search_providers.py:676-703`, so an omitted result's sentinel URL is asserted absent
  from every record.
- `provider_errors` composition (`:924`): map a `failure_class` not in `FAILURE_CLASSES` to
  `hard_error`, a `detail` not matching `^[a-z0-9_]{1,32}$` to `unexpected`, **and** a
  `provider.name` not matching the same pattern to `unknown` before the entry is built (finding
  -045; the name half is hardening for a future operator-pluggable provider — say so in
  `_query_provider_chain`'s docstring, and `kit_tools/arch/patterns/ERROR_HANDLING.md:166-172`'s
  "two closed vocabularies" sentence gains the `unknown` provider-name fallback); a test registers a
  fake returning `ProviderFailure("weird", "x y")` and asserts the entry `"<name>: hard_error"` and
  the log token `unexpected`.
- Pins: the three wire captures and their counter dicts are built from
  `tests/fixtures/brave/llm_context_sample.json` and the synthetic SearXNG dicts the tests already use
  — never a live capture (Brave's ToS forbids persisting result payloads); the captured dumps have
  `request_id` removed *before* writing (its 32-hex value is itself token-shaped). The fixture guards:
  `tests/test_brave_provider.py:101-107` (`test_no_auth_header_name_anywhere_in_fixtures`) walks the
  whole `tests/fixtures/` tree; `:109-118` (`test_no_token_shaped_literal_anywhere_in_brave_fixtures`,
  `_TOKEN_SHAPE_RE = [A-Za-z0-9_-]{24,}`) walks only `tests/fixtures/brave/` because the comment at
  `:81-83` keeps it off `tests/fixtures/tiny_model/` — this story **re-roots the token walk at `_FIXTURES_DIR` with an explicit
  `tiny_model/` allowlist** (decided; one walk, not a per-directory sibling), so the captures are
  secret-shape-guarded. Add a `tests/fixtures/README.md` section for `search/` (provenance: synthetic,
  scrubbed, `request_id` removed), a `kit_tools/testing/TESTING_GUIDE.md:153-156` support-files row for
  `tests/fixtures/search/` **and** a per-module row for `tests/test_search_pipeline_pins.py` (the
  guide's module table carries every test file). The comparison lives in a new
  `tests/test_search_pipeline_pins.py`; the closed exclusion list is `{"request_id"}` — the
  implementer sweeps `SearchResponse` for any other non-deterministic field before the capture and
  records the sweep result in Implementation Notes. Captures are taken in the story's **first**
  commit, before any refactor hunk: that commit holds the fixtures, `tests/test_search_pipeline_pins.py`
  green against unchanged code, the re-rooted token walk and the two doc rows, and nothing under
  `pipeline/` (the round-2 proposal to make this its own story is overruled by R37; the ordering
  achieves the same fallback state).
- `pipeline/orchestrator.py` is hashed: this is the one rotation the whole cleanup gets; the
  `provider_errors` and omission-log deltas are deliberately inside it. Record per ruling 6 (five
  sites).

**Acceptance Criteria:**
- [ ] `_query_provider_chain` exists at module level in `pipeline/orchestrator.py` and takes the
      metrics sink; `run_search_pipeline`'s body contains no `await provider.search(` and no
      `for ... in enumerate(chain)`; exactly one `search_provider_failed` WARNING statement remains;
      `_legacy_searxng_codes` is called once per request; one empty-chain `ValueError` site remains.
- [ ] The sink is mutated inside `_query_provider_chain` at the same points as before: the three
      fixture runs produce identical counter dicts before and after; the exhaustion captures show the
      increments already landed when the `PipelineError` is raised.
- [ ] `_ServedChain` carries the serving provider, raw results, `max_results`, `unresponsive_engines`,
      `content_kind`, `provider_errors` and `fallback_fired`, and one committed fixture run has a
      non-empty `unresponsive_engines` that survives the pin; `ERROR_HANDLING.md:166-172` names the
      `unknown` provider-name fallback.
- [ ] `grep -n 'searxng_url' pipeline/orchestrator.py` returns nothing; no `run_search_pipeline(`
      call in `tests/` passes `searxng_url=`; `build_provider_chain`'s `searxng_url=` keyword and its
      callers are unchanged.
- [ ] No omission log line contains a result URL: the extended no-leak test passes; the three
      rewritten lines carry the reason token and `domain=`. **This is a deliberate delta**, pinned by
      that test.
- [ ] A `ProviderFailure` with an out-of-vocabulary class, detail or provider name reaches the wire
      as `"<name>: hard_error"` / `"unknown: …"` with `detail=unexpected` in the log, pinned by a
      test. **This is a deliberate delta** for inputs the pins do not contain.
- [ ] Wire, sink and exhaustion output pinned: for inputs whose provider outcomes are in-vocabulary,
      the three committed captures produce identical `model_dump()` (exclusion list `{"request_id"}`)
      and identical counter dicts, and the two exhaustion inputs raise a `PipelineError` with identical
      `error`, `reason` and status, before and after; `tests/test_search_pipeline_pins.py` stays in the
      suite; the token-shape guard covers `tests/fixtures/search/`; `tests/fixtures/README.md` and
      `TESTING_GUIDE.md` document the directory; all pre-existing search tests in
      `tests/test_orchestrator.py`, `tests/test_search_providers.py` and `tests/test_app.py` pass with
      no assertion changed apart from the two removed legacy-parameter tests.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce on `pipeline/orchestrator.py`) and
      recorded at the five sites ruling 6 names.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A response with `Content-Length` under the bound whose body then exceeds it is still capped by the
  running counter (US-003).
- A `Content-Length` that is not a digit string is ignored and the counter decides; one longer than
  20 characters is `body_too_large` on the direct-construction path (a real peer is `connect_error` /
  `transport_error` via h11) (US-003).
- `Content-Encoding` absent or `identity`: raw bytes counted, not a compressed body. `gzip`, zlib
  `deflate`, raw `deflate`: decoded by Forage under the byte ceiling, served, counted once. `br`,
  `zstd`, an unknown token, or a list such as `gzip, br`: `unsupported_encoding` before any body byte
  is read, counted once (US-003).
- A compressed body whose decoded length exceeds the bound is `body_too_large`, counted once, and no
  zlib call ever returned more than `remaining + 1` bytes — including when the overflow arrives only
  in the final raw chunk, because `flush()` is never called and the loop drains `unconsumed_tail`
  (US-003).
- A compressed stream that decodes to nothing (zero-expansion filler) is `body_too_large` on the raw
  budget (`4 × max_response_bytes`), never a `timeout` after a full budget of bandwidth (US-003).
- `Content-Encoding` dispatch runs before the `Content-Length` fast-reject: an undecodable encoding is
  `unsupported_encoding` whatever `Content-Length` says, and a decodable one with an over-bound
  `Content-Length` is `body_too_large` before any body byte (US-003).
- A limiter-enabled SearXNG (`server.limiter: true`, opt-in; the image ships it off) refuses the new
  `Accept-Encoding: identity` header with 429 on the first request → `rate_limited`, the existing
  classification; documented beside the limiter caveat in `docs/searxng.md` and GOTCHAS (US-003).
- The timeout defaults keep their values (10 s / 15 s) but tighten in meaning to the whole HTTP
  interaction; a slow four-engine fan-out that succeeded under per-operation timeouts may now be
  `timeout` (and buy a paid call on a two-provider chain); the config rows say to raise
  `search_searxng_timeout_seconds` for a slow instance (US-003).
- A gzip or deflate body that is truncated or corrupt raises `zlib.error` mid-stream:
  `read_bounded_body` maps it to `MalformedBody` (a sibling of `BodyTooLarge`) and both providers
  classify it `hard_error` / `malformed_body` — a token both detail sets already carry
  (`searxng.py:69-76`, `brave.py:155-165`), so no second new token is needed (US-003).
- A 200-with-zero-results-plus-`unresponsive_engines` body that arrived compressed is re-classified
  into a `ProviderFailure` that still carries `compressed`, so the counter fires (US-003).
- The wall-clock budget expiring after headers but before the first body byte maps to `timeout`, not
  `body_too_large`, and carries `compressed` if the headers said so; a compressed non-2xx refused by
  status mapping and a transport error mid-body carry it too; a failure before the headers arrive
  (connect error, timeout before headers) never does; a slow `json.loads` after a fast body is never
  a timeout (US-003).
- A query shorter than the cap is sent untouched; a cap equal to the query length sends it whole;
  truncation counts characters, not bytes; the echoed `SearchResponse.query` is the caller's (US-002).
- `providers` naming a paid provider that is configured *first* keeps it; naming only a later paid
  provider drops every paid provider — silently by design, as the field's description now states
  (US-004).
- On an all-paid configured chain (`[paidA, paidB]`), a later-paid-only selection leaves no provider
  and is the existing `POLICY_EXCLUDED_ALL_PROVIDERS` 422 — a caller who got `paidB`'s results
  before gets a 422 after; named in the 1.3.0 docstring line and pinned (US-004).
- A request whose ignored entries are all duplicates of one unknown name counts each entry
  (unchanged, R14) (US-004).
- The cleanup keeps the search epic's ruling 28 predicate on the *configured* chain — a
  policy-filtered `[searxng]` effective chain with a two-provider configured chain still raises
  `search_unavailable`, never `searxng_*` (US-005; pinned by `tests/test_app.py`'s
  `test_exhaustion_code_follows_the_configured_not_the_effective_chain` and by the exhaustion
  captures).
- An `invalid_url` omission (unparseable host) already logs no URL and is untouched by the cleanup
  (US-005).

## Out of Scope

- A spend ceiling or budget breaker (search epic ruling 12 stands; Poppy's P4 is the only cap).
- A body-size cap on `/search` or `/retrieve` request bodies, or a `max_length` on
  `SearchRequest.providers` (recorded as accepted risk in US-004; a rejection change belongs to a
  GOVERNANCE ruling of its own).
- Changing `policy_unknown_provider`'s unit (R14), or adding a counter or log for a prefix drop.
- Adding brotli or zstandard decoders (`httpx[brotli]`, `zstandard`): a new dependency for a case no
  operator has hit; an undecodable encoding is a classified, counted failure instead.
- New providers, LiteLLM adapters, or any change to the `SearchProvider` protocol's members.
- Changing the 1 MiB bounds' defaults or exposing them as knobs beyond `SearxngSettings.
  max_response_bytes` (no config key for it; the field exists for tests and parity with Brave).
- A request-level (chain-wide) deadline — the budget is per provider call; the N-provider sum is
  documented and recorded as a non-vulnerability row.
- Structural or PromptGuard behaviour on search text — spec 1; `SearchResult.engine` bounding — spec 1
  US-004.
- **`pipeline/stage5_url_audit.py:210-219`**, the `/retrieve` fetch path's own running counter over
  `aiter_bytes()` — the identical uncapped-decoder shape finding -020 describes, on a caller-chosen
  URL rather than a fixed host (round-2 salty review). It is deliberately not fixed here: stage 5 is a
  hashed file with its own rotation and belongs to a fetch-path story; `read_bounded_body` is placed
  in `pipeline/bounded_body.py` precisely so that story can adopt it without an import-direction
  change. No sibling spec owns it yet — the epic wrapper's owner files it as a backlog item at the
  close of this planning round, and this spec's Implementation Notes must not claim `/retrieve` is
  covered.

## Assumptions

- Poppy is the only consumer; both window changes (the counter, the `providers` description line) are
  additive and announced in the 1.3.0 docstring entry, the second with its all-paid-chain note.
- `asyncio.timeout` and `zlib` are stdlib (Python ≥ 3.12 per `pyproject.toml:5`); no new dependency.
- The pinned httpx 0.28.1 decodes `gzip`/`deflate` with no output cap and ships no `br`/`zstd`
  decoder (`_decoders.py:381-393`; neither `brotli`, `brotlicffi` nor `zstandard` is in `uv.lock`) —
  which is why Forage decodes for itself and refuses what it cannot decode.
- `aiter_raw()` yields at most one transport read (~64 KiB) per chunk; the raw chunk is the only
  allocation above the cap; the raw budget is `4 × max_response_bytes` (a genuinely compressed body
  under the decoded cap is essentially never larger than the cap raw, so the raw budget costs nothing
  in legitimate traffic).
- `zlib.decompressobj.flush(length)` sizes a buffer and does not cap its return; the bounded reader
  never calls it (measured in round 2).
- `Accept-Encoding: identity` is a new header on both clients; httpx's default is `gzip, deflate`.
- The 1.3.0 contract window is open when this spec executes (spec 1 US-004 opened it) and spec 3
  US-003's `KNOWN_CONFIG_KEYS` registry exists.
- The three wire pins are representative enough; they are kept as regression fixtures for later
  rotations of `orchestrator.py`.

## Technical Considerations

- Rotation ledger (ruling 6, R32): US-001 rotates nothing; US-003 rotates through
  `pipeline/orchestrator.py` **and** `pipeline/contract.py` (one measurement, each reverted in turn
  plus a both-reverted control); US-002 rotates nothing; US-004 rotates through `pipeline/contract.py`
  (the `providers` description line); US-005 rotates through `pipeline/orchestrator.py`. Three
  rotations, each measured and recorded at the five sites.
- Golden movement: US-003 moves `contract/openapi.yaml` and the anchor but **not** the golden or the
  1.3.0 diff list (`/metrics` models are outside `_SCHEMA_MODELS`); US-004 moves the golden
  (`SearchRequest`), adds no field.
- `read_bounded_body` lives in `pipeline/bounded_body.py` — a `pipeline/` module outside
  `pipeline/search_providers/` and outside `_REVISION_SOURCES` — so both providers import inward from
  `pipeline` as today and a later stage-5 story can import it without a provider package importing
  outward.
- `BraveSettings.max_response_bytes` (US-001, no config key) exists so the Brave byte-bound tests can
  shrink the cap the way the SearXNG ones do; `_BRAVE_MAX_RESPONSE_BYTES` is its default.
- The wall-clock budgets keep their default values and tighten in meaning; the tightening is
  documented, the defaults are kept with the stated uncertainty (no fan-out latency distribution has
  been measured), and the config rows tell operators of slow instances what to raise.
- Settings precedent: `brave.py:193-275` (`BraveConfigurationError`, frozen `BraveSettings`,
  module-local `_bounded_int`, `brave_settings_from_config`) — `cache.py:244-267` and
  `pipeline/extraction_limits.py:79-106` are the other two copies; the fourth copy in `searxng.py`
  follows the recorded precedent (Decisions Made).
- `asyncio.timeout` nests inside the `httpx.AsyncClient` context so the client closes on expiry; the
  `except TimeoutError` arm (builtin) precedes the catch-all (search epic ruling 27). httpx's own
  teardown under task cancellation mid-`aiter_raw()` cannot be exercised by the hermetic suite
  (`AsyncClient` is patched; pytest-socket forbids real sockets) and is **accepted untested**.
- The `/metrics` order guards are `tests/test_contract_metrics.py:135` and `:161`; `SearchMetrics`
  (`retrieval_app.py:875-886`) is a plain class, not a dataclass, so
  `test_dataclass_counters_and_their_models_carry_the_same_fields` (`:305`) needs no new parametrisation.
- The timed tests are the suite's first real waits above 0.05 s; they assert the lower bound only.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md) (search pipeline section)
- Security: [SECURITY.md](../arch/SECURITY.md) (cost section, non-vulnerabilities table)
- Patterns: [ERROR_HANDLING.md](../arch/patterns/ERROR_HANDLING.md), [LOGGING.md](../arch/patterns/LOGGING.md)
- Configuration: `docs/configuration.md` (`search_*` rows), [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md)
- Monitoring: [MONITORING.md](../docs/MONITORING.md) (counter rows; "Adding a counter to `/metrics`")
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("Nothing configures logging", "/metrics counter without the model 500s", the divergence table)
- Rotations: `docs/bootstrap-notes.md`, [DECISIONS.md](../arch/DECISIONS.md), [GOTCHAS.md](../docs/GOTCHAS.md), [CODE_ARCH.md](../arch/CODE_ARCH.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Read raw and decode in Forage under a byte ceiling (R13 as corrected).
**Rationale:** httpx 0.28.1's `GZipDecoder` decompresses each raw read with no `max_length`
(`_decoders.py:85-97`), so per-chunk *counting* of decoded bytes bounds accumulation but not
allocation — finding -020's 67 MB chunk was measured against exactly that counter, and round 1
reproduced a 230-byte gzip body arriving as one 200,000-byte chunk. `zlib.decompressobj().decompress
(data, max_length=…)` is the only shape that serves compressed bodies *and* bounds the allocation.
**Alternatives considered:** httpx `max_content_length` — not a client option; per-chunk decoded-byte
counting over `aiter_bytes()` (round-1 plan) — bounds the wrong thing; refuse non-identity (round-0
plan) — a free-floor outage behind any compressing proxy.
**Source:** `.venv/.../httpx/_decoders.py:55-97,381-393`; `brave.py:331-346`; finding 2026-09-16-020.

**Decision:** An encoding this build cannot decode is a distinct, counted failure
(`unsupported_encoding`), not a silent `bad_json`.
**Rationale:** httpx pops `br` and `zstd` from `SUPPORTED_DECODERS` when the optional packages are
absent and falls back to `IdentityDecoder` (`_models.py:699-722`); neither package is in `uv.lock`.
Today such a body fails `json.loads` and, on a `[searxng, brave]` chain, becomes a billable call
with no signal. A named token and a header-seen counter make the proxy setting visible.
**Alternatives considered:** `httpx[brotli]` + `zstandard` — a dependency for a case nobody has hit;
refusing all compression — rejected in round 1.
**Source:** `.venv/.../httpx/_decoders.py:381-393`, `_models.py:699-722`; `uv.lock`.

**Decision:** Count the compression signal at the header, on both outcome types.
**Rationale:** A counter carried only on `ProviderSearchResult` is blind to the over-bound, undecodable
and re-classified cases — the three states an operator reads `/metrics` to explain (round-1 security
and salty reviews). The flag on both internal dataclasses costs nothing on the wire.
**Source:** `base.py:57-76`; `orchestrator.py:895-919`.

**Decision:** `SearxngSettings` mirroring `BraveSettings`, threaded through `build_provider_chain`;
no module-level `app.state` default.
**Rationale:** The timeout needs a per-instance seam for a 0.5 s test; the query cap needs a boot
refusal with a module-owned error class; the production chain is built by `build_provider_chain`
(`retrieval_app.py:1258`), not by the production-unreachable fallback at `:310`; `brave_settings` has
no module-level default and nothing lifespan-less reads the new one.
**Source:** `brave.py:193-275`; `pipeline/search_providers/__init__.py:83-89,114,154`;
`retrieval_app.py:1242,1378-1402`.

**Decision:** Prefix rule for paid providers (R14) rather than scoping the docstring to "one paid
provider"; the all-paid-chain 422 is named, not fixed.
**Rationale:** The guarantee should hold when T3.1 adds a second paid backend; the empty-chain → 422
mapping already exists (`retrieval_app.py:1799-1810`) and is the correct fail-closed shape.
**Source:** `policy.py:35-77`; `tests/test_search_policy.py:22-67`; finding 2026-09-16-052.

**Decision:** One cleanup story, one rotation, with committed wire pins compared modulo `request_id`,
the sink mutated inside the helper, and the exhaustion payloads pinned.
**Rationale:** Seven findings touch the same 70 lines; each on its own would rotate the revision.
`model_dump()` never happens on the raise paths and never shows the sink, so both are pinned
separately (round-1 salty review).
**Source:** findings -035, -036, -038, -039, -043, -045, -011; `orchestrator.py:842-946, 999-1035`.

### Scope Adjustments

- Finding -014 (`engine` bound) moved to spec 1 US-004 because it is wire-adjacent and belongs with
  the contract window opening.
- Validation round 1: the old US-002 (egress bound + policy + counter unit) split into US-002
  (egress bound) and US-004 (policy); the counter-unit change was dropped (R14); the cleanup was
  renumbered US-005.
- Validation round 2 (R37): the old US-001 split into US-001 (settings seam + shared doubles,
  behaviour-preserving) and US-003 (the read-path rewrite, the counter, the budget); the
  compression design corrected to Forage-side bounded decoding with `unsupported_encoding` (R13
  corrected); the counter carried on both outcome types; the timed tests made one-sided; the
  `_FAILURE_CASES` migration, the `_client_patch` collision and the stage-5 helpers named; the
  exhaustion and sink pins added to US-005; the all-paid-chain 422 named in US-004.

### Decisions Made

- No new failure *class*: `body_too_large`, `unsupported_encoding` (the one new detail token, added
  to both providers' closed sets), `malformed_body` (already on both; reused for a corrupt compressed
  stream) and `timeout` cover every new refusal; `detail` is log-only, so the vocabulary growth is not
  a contract change.
- The test-seam split follows the code, not R37's wording: the shared doubles are *promoted* in
  US-001 (pure test refactor), but the SearXNG `get`→`stream` double migration must move with the
  production switch and is US-003's first commit — a `stream`-shaped double against a `get`-shaped
  provider would fail every SearXNG test.
- Overruled (round 1 story-quality, salty): a four-to-six-way split of US-001 — R37's two-way split
  plus the first-commit ordering inside US-003 keeps every criterion and one rotation.
- Overruled (R14): capping `policy_unknown_provider` per request, a `/search` body cap, or a
  `max_length` on `providers` — recorded as accepted risk in honest wording instead.
- Overruled (round 1 second-opinion): an INFO line on a prefix drop — INFO never renders (GOTCHAS);
  the diagnosis path is written into the field description and `API_GUIDE.md` instead.
- Overruled: a `--notes`-style line-count bar on `run_search_pipeline` — replaced by structural
  criteria (no `await provider.search(` in the function; helper at module level).
- The `RuntimeError("decoder exploded")` `_FAILURE_CASES` row is deleted rather than migrated: with
  `json.loads(body)` there is no seam for an arbitrary exception, and the case modelled none.
- The `provider.name` guard at composition is added (security review) even though every name is a
  registry constant today; the docstring says why.
- Overruled (round 1 codebase-fit): promoting the stage-5 helpers as-is — they are rebased on the
  parameterised shared ones instead, so the two `_make_response` copies with different defaults do
  not survive as a third.
- The fourth module-local bounded-value copy follows the recorded precedent (`brave.py:231-233`); a
  shared helper module is noted for a later cleanup, not this spec.
- Round 3 (R13 corrected): the reader never calls `flush()`; `eof` unset or a non-empty
  `unconsumed_tail` at end of stream is `malformed_body`; raw bytes are bounded at 4× the decoded cap;
  the decode observation seam is `tests/fakes.py::RecordingDecompressor` patched over
  `pipeline.bounded_body.zlib.decompressobj` (a test-side wrapper, not a production hook — the
  `wraps=` spy idiom at `tests/test_brave_provider.py:740-746` is the precedent).
- Round 3: `make_response` is stream-backed; the two SearXNG double factories return real
  `httpx.Response` objects; `tests/test_search_providers.py`'s local `_client_patch` is deleted in
  favour of the promoted `client_patch`; the stage-5 helpers survive as single-line delegating
  wrappers (shape (a)); the token walk is re-rooted at `_FIXTURES_DIR` with a `tiny_model/`
  allowlist; `BraveSettings` gains `max_response_bytes`; the helper module is `pipeline/bounded_body.py`.
- Overruled (round-2 salty review): dropping `Accept-Encoding: identity` to avoid SearXNG's
  `http_accept_encoding` limiter rule — R13 (corrected) keeps the header on both clients; the
  limiter is off in the shipped image, the interaction is documented beside the existing limiter
  caveat, and a limiter-enabled instance already refuses curl the same way.
- Overruled (R37 corrected): the round-2 three-way split of US-003 (bodies / wall-clock budget /
  counter) and the pin-corpus split of US-005 into a US-008 — already litigated; single concern each
  at size L; the first-commit ordering inside US-005 gives the same fallback state.
- The all-paid-chain 200→422 is recorded in GOVERNANCE as an expedited security-tightening MINOR on
  the one-paid-name reachability argument (round-2 completionist and salty reviews), not ridden on a
  docstring line.

## Clarifications

### Session 2026-09-19
- Q: Cap provider bodies before or after decode? → A: Decode in Forage under a byte ceiling
  (ruling 13 as corrected in round 2).
- Q: Is the per-provider timeout a per-operation or a wall-clock budget? → A: Wall-clock, via
  `asyncio.timeout` around the HTTP interaction only; the httpx per-operation timeout stays.
- Q: Cost-monotonicity with a second paid provider — restrict by construction or by docstring? → A:
  By construction, paid-prefix rule (ruling 14).
- Q: How many increments may one request add to `policy_unknown_provider`? → A: One per ignored
  entry, as today (R14); the amplification is an accepted, recorded risk.
- Q: How many rotations does the orchestrator cleanup get? → A: One story, one rotation (ruling 15).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R13 (serve-bound-count; `SearxngSettings`; wall-clock scope; no `MockTransport`),
  R14, R15, R31 (split and renumbering), R32, R34, ruling 5.

### Session 2026-09-19 (validation round 2)
- Rulings applied: R13 (corrected — `aiter_raw()` + Forage-side bounded `zlib` decoding;
  `unsupported_encoding`; counter at the header on both outcome types), R32 (US-003 rotates two hashed
  files), R36 (window block verbatim on US-003 and US-004), R37 (US-001/US-003 split;
  `execution_order`), R39 (doc sweeps by grep), R40 (every test seam named with counts), ruling 6
  (five-site rotation record).
- Q: Does a compressed body over the bound count? → A: Yes — the counter fires at the header, so
  served, over-bound and undecodable bodies all count.
- Q: Which exception does the wall-clock budget raise? → A: The builtin `TimeoutError`, caught by its
  own arm before the catch-all; `httpx.TimeoutException` keeps its arm.
- Q: What happens on an all-paid chain with a later-paid-only selection? → A: The existing policy
  422; named in the docstring line and pinned.

### Session 2026-09-19 (validation round 3)
- Rulings applied: R13 (corrected — no `flush()`, `unconsumed_tail` drained, `eof` rule, raw bytes
  bounded at 4×, `Accept-Encoding: identity` as an admitted new header with the SearXNG-limiter
  interaction documented, stream-backed `make_response`, real `httpx.Response` SearXNG doubles,
  `_ServedChain` fields, counter on every outcome, GOVERNANCE classification for the all-paid 422),
  R37 (corrected — no further splits), R39, R40, R41 (every fixture named carries its expected token
  and, where it matters, its arithmetic), ruling 6 (five sites).
- Q: How is "largest decoded output" observed? → A: `RecordingDecompressor` patched over the helper's
  `zlib.decompressobj`; `ChunkStream.largest_chunk` is the raw side only.
- Q: Does the flip on an all-paid chain need a GOVERNANCE ruling? → A: Yes — recorded as an expedited
  security-tightening MINOR on the reachability argument, with the T3.1 note.
- Q: Where does the bounded-body helper live? → A: `pipeline/bounded_body.py`, so stage 5 can adopt it
  in a later story; stage 5's own counter is named in Out of Scope.

## Open Questions

- [ ] Should `SearxngSettings.max_response_bytes` get a `config.yaml` key in a later epic? Non-blocking;
      default no until an operator hits the bound (spec 6's Out of Scope agrees).
- [ ] Whether Brave's LLM-Context endpoint ever answers with a non-identity encoding — informational
      (a decodable reply is served and counted; an undecodable one is a classified failure); confirm
      at the next owner-run capture.
- [ ] Whether the four module-local bounded-value helpers should move to one dependency-free shared
      module — a later cleanup, non-blocking.
