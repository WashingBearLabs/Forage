<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: operations, architecture
  required_sections:
    - "Overview"
  skip_if: never
-->
# LOGGING.md

> **TEMPLATE_INTENT:** Document logging patterns, levels, and conventions.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-hostname-and-config US-003)

## Overview

Forage logs through the stdlib `logging` module and nothing else, and three deliberate choices shape every line. First, **closed vocabularies over interpolation**: a failure on a credential-bearing path is reported as one of a fixed set of reason strings, never as `str(exc)` or the value that failed, because `VALKEY_URL` routinely carries a password and `huggingface_hub` / `oras` errors carry request context (`CLAUDE.md` invariant 6; `cache.py`, `model_fetcher.py`). Second, **logging is unconfigured by design**: no `basicConfig`, no `dictConfig`, no uvicorn `--log-level` or `--log-config`, no log-level environment variable. The root logger therefore sits at Python's default, WARNING, and every first-party `logger.info` / `logger.debug` is emitted and dropped inside the container; only WARNING and ERROR lines reach `docker logs` (`kit_tools/docs/GOTCHAS.md`, "Nothing configures logging"). Third, the one long-running background process, weights acquisition, emits **grep-able `weights_*` event markers** with closed outcome and reason codes so that an operator can reconstruct a failed acquisition from `docker logs | grep weights_` without a formatter or a request id.

The consequence to internalise: the *success* narrative of this service is invisible in production and `/health` plus `/metrics` are the observability surface, while every *failure* is loud. This document is the contributor-side pattern; the operator-side view (startup-line tables, marker grep table, retention) is `kit_tools/docs/MONITORING.md` "Logging", and the short standing rules are in `kit_tools/docs/CONVENTIONS.md` "Logging".

---

## Log Levels

| Level | How Forage uses it | Reaches `docker logs`? | Examples (source) |
|-------|--------------------|------------------------|-------------------|
| `DEBUG` | Trace detail for local debugging only. Three sites exist. | No (root at WARNING) | `URL validated: %s → %s (%s)` (`url_validator.py:182`); `Skipping PromptGuard for TRUSTED domain` (`pipeline/stage3_promptguard.py:79`); `PromptGuard loading against torch %s` (`promptguard/classifier.py:82`) |
| `INFO` | The success narrative: config loaded, cache connected, cache hit, search omissions, extraction completed, model loaded, `weights_fetch_attempt` / `weights_fetched` / `weights_verified` / `weights_loaded`. | No (dropped in-container) | `Sidecar config loaded (%d keys); contract_version=%s` (`retrieval_app.py:1072`); `Content cache connected (%s)` (`retrieval_app.py:1101`, `%s` is `valkey` or `memory`, never the URL); `PromptGuard 2 model loaded successfully` (`promptguard/classifier.py:108`) |
| `WARNING` | A dependency-state transition or a degraded-mode decision that changed what a caller received. Also the level chosen when a line *must* be visible. | Yes | `Valkey connection failed for content cache (%s)` (`cache.py:420`); `Content cache not available at startup` (`retrieval_app.py:1103`); `weights_retry_scheduled` (`model_fetcher.py:1864`); `PromptGuard unavailable — fail-closed for %s tier` (`pipeline/stage3_promptguard.py:96`); `Content quarantined for %s — returning content-free response` (`pipeline/orchestrator.py:367`) |
| `ERROR` | A weights-acquisition leg or verification that failed; PromptGuard stays unavailable. Only `model_fetcher.py` uses it. | Yes | `weights_unavailable`, `weights_fetch_failed`, `weights_verification_failed`, `weights_quarantined`, `weights_load_failed`, `weights_pin_unusable`, `weights_mirror_invalid`, `model_revision_invalid` |
| `logger.exception` | ERROR plus traceback; one site. | Yes | `weights_acquisition_crashed — PromptGuard stays unavailable and /health stays degraded` (`model_fetcher.py:1574`) |
| `CRITICAL` | Unused. A boot that cannot continue (bad `extraction:` or `cache:` block in `config.yaml`) raises and uvicorn exits with a traceback; nothing logs at CRITICAL. | n/a | none |

Two WARNING lines are *per-request* and will repeat on every `/retrieve` or `/search` result while PromptGuard is unavailable: the `stage3_promptguard` "PromptGuard unavailable" pair and the orchestrator quarantine line. That noise is intentional (loud degradation, `CLAUDE.md` invariant 5); see `kit_tools/arch/patterns/ERROR_HANDLING.md` "Degradation Matrix" for what the caller receives in each case.

---

## Logger Inventory

Every module obtains its logger with `logger = logging.getLogger(__name__)` at module top; there are no named or hierarchical loggers beyond what the module path gives.

| Logger | Defined at | Levels used | What it emits |
|--------|------------|-------------|---------------|
| `retrieval_app` | `retrieval_app.py` | INFO, WARNING | Startup lines; `config.yaml not found at %s`; `config_unknown_key — key=%s` (WARNING, one per unknown dotted key, never its value, tokens in the message not `extra=`); `config_invalid_value — key=%s dropped=%d entries=%s` (operator domain-list drops); `config_invalid_value — key=promptguard_threshold. /extract reads the raw value through its own guard` (WARNING, never the invalid value); `promptguard_threshold_resolved — value=%s` (once per boot, INFO, validated numeric default only); `break_glass_advertisement_active — %s=1 is forcing /health ...`; `document extraction completed` (INFO, content-free `extra=` dict) |
| `cache` | `cache.py:38` | WARNING | Closed-vocabulary connection, operation and corrupt-entry lines (see below) |
| `model_fetcher` | `model_fetcher.py:141` | INFO, WARNING, ERROR, exception | All `weights_*` markers and `model_revision_invalid` |
| `promptguard.classifier` | `promptguard/classifier.py:21` | DEBUG, INFO, WARNING | Model loaded; `PromptGuard model not available — ML injection detection disabled` (WARNING with `exc_info=True`, so a traceback follows); `classify() called but model not loaded — returning safe fallback` |
| `pipeline.orchestrator` | `pipeline/orchestrator.py:80` | INFO, WARNING | `Cache hit for %s`; search-result omission lines; `search_promptguard_complete`; quarantine WARNING; `search_promptguard_local_latency_target_exceeded` (WARNING, `extra=` only); `search_provider_failed provider=%s failure_class=%s detail=%s` (WARNING, one per failed provider during chain traversal — the closed tokens ride in the message as `key=value`, and the line pairs with the provider's own WARNING: cause at the provider, effect on the chain) |
| `pipeline.stage3_promptguard` | `pipeline/stage3_promptguard.py:22` | DEBUG, WARNING | Trusted-tier skip; `PromptGuard unavailable — fail-closed for %s tier`; `PromptGuard unavailable — %s for %s tier` (`lenient fallback` for verified, `fail-open` otherwise) |
| `url_validator` | `url_validator.py:16` | DEBUG | `URL validated: %s → %s (%s)` only; never visible in a container |
| `pipeline.stage5_url_audit` | `pipeline/stage5_url_audit.py:26` | none | Declared, no emit sites (observation; see Observed Rough Edges) |

---

## What to Log

### Always log

- **Dependency-state transitions**, at WARNING or ERROR so they are visible: the cache connect failure with its closed reason; the cache not being available at startup; every weights-acquisition outcome through a `weights_*` marker; a verified weight set that will not load (`weights_load_failed`, preceded by the classifier's WARNING with traceback).
- **A decision that changed what the caller received** because a dependency was absent: the `stage3_promptguard` fail-closed / fail-open lines and the orchestrator quarantine line. The response body carries the machine-readable version (`promptguard_state`, `degraded_reasons`, `omitted_by_reason`); the log line is the operator's cue to look at `/health`.
- **Operator-facing misconfiguration** at boot: break-glass armed (naming the variable that armed it), `config.yaml` missing, `config_invalid_value` for domain lists (one WARNING per list, key, dropped count and operator entries in the message, not `extra=`; credential/URL-shaped mistakes redacted), or for `promptguard_threshold` (one WARNING, key only, never the value, explicitly noting `/extract`'s separate raw guard; fetch routes fall back to 0.85), `FORAGE_MODEL_REVISION` not a 40-hex sha (`model_revision_invalid`, value not echoed), `FORAGE_WEIGHTS_MIRROR` malformed (`weights_mirror_invalid`, reference redacted).

### Never log

- `VALKEY_URL`, in whole or in part: not the password, not the host, not `str(exc)` from the redis client. Enforced by `tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret` and `tests/test_app.py::test_no_selection_path_logs_the_valkey_url`.
- `HF_TOKEN` and `FORAGE_MIRROR_TOKEN`, `huggingface_hub` exception text, and `oras` stdout/stderr. Enforced by `tests/test_model_fetcher.py::TestHuggingFaceFetch::test_a_failed_download_never_logs_the_token` and `::TestNoSourceProducedWeights::test_neither_token_ever_reaches_a_log_line`.
- A mirror reference before it has passed `redact_reference()` (`model_fetcher.py:957`, userinfo becomes `***@host`). Enforced by `::TestMirrorReferenceResolution::test_a_credential_bearing_reference_is_refused_and_redacted`.
- `SEARXNG_SECRET`. Forage does not read it (it belongs to the SearXNG companion and arrives via the compose env file), so no Forage line can carry it; keep it that way.
- Fetched page content, extracted text, upload bytes, search snippets, or query text. `/extract`'s single INFO line carries only `request_id`, `size`, `content_type`, `verdict`, `reason`, `duration` (`retrieval_app.py:1507`); quarantine returns and logs a content-free response.
- Anything from `docker-entrypoint.sh`. It is `set -euo pipefail; exec "$@"` and prints nothing, by its own comment, because it is the one place a chatty launcher would echo `VALKEY_URL` into the container log.

---

## Closed Vocabularies

Tests are the enforcement mechanism for both vocabularies: each fixed string below has a `caplog` assertion that it appears and that the value it stands in for does not. A new reason or marker without a test is incomplete.

### `cache.py`

`_closed_vocabulary_reason(exc, *, default)` returns `timeout` when `exc` is a
`TimeoutError` and otherwise the caller's `default`: `connect_failed`,
`operation_failed`, `timeout`. It is an exception mapper, not a token registry.
The cache parse guard logs the fixed literal `cache_entry_corrupt` directly.

| Level | Line | Reason values | Site |
|-------|------|---------------|------|
| WARNING | `Valkey connection failed for content cache (%s)` | `connect_failed`, `timeout` | `_attempt_connect`, `cache.py:419-422` |
| WARNING | `Content cache operation failed (%s)` | `operation_failed`, `timeout` | `_mark_disconnected`, `cache.py:482-485` |
| WARNING | `Content cache entry rejected (%s) key=%s` | `cache_entry_corrupt` and the one-way `ret:<sha256>` key digest only | `ContentCache._parse_entry` |

The corrupt-entry line never carries the raw value, URL, exception text or traceback.
`tests/test_cache.py::TestCorruptCacheEntries` asserts the exact record and absence of
sentinels in both the value and URL, including when deletion also fails.

Startup logs only the backend literal (`Content cache connected (valkey|memory)`) or the fixed canary `Content cache not available at startup`. Pinned by `tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret` (drives both `ContentCache.connect()` and the real lifespan with a credentialed URL) and `tests/test_app.py::test_no_selection_path_logs_the_valkey_url` (five start modes).

### `model_fetcher.py`

Every line starts with a snake_case marker followed by ` — ` and a fixed clause; variable parts are closed codes, a revision sha, a byte count, a duration, or a redacted reference.

| Level | Marker | Meaning |
|-------|--------|---------|
| ERROR | `weights_unavailable` | The one terminal line per attempt; ends with `Attempts: huggingface=<code>, mirror=<code>` |
| ERROR | `weights_fetch_failed` | One source leg failed; carries `source=`, `revision=` or `reference=`, `reason=` |
| ERROR | `weights_verification_failed` | Manifest verification refused the set; lists `REASON_*` codes |
| ERROR | `weights_quarantined` / `weights_quarantine_failed` | Refused set moved to `$HF_HOME/quarantine/`, or could not be |
| ERROR | `weights_pin_unusable` | `weights_manifest.json` pins nothing verifiable; no fetch attempted |
| ERROR | `weights_load_failed` | Verified set did not load into the classifier |
| ERROR | `weights_mirror_invalid` / `model_revision_invalid` | Malformed `FORAGE_WEIGHTS_MIRROR` (redacted) / `FORAGE_MODEL_REVISION` (not echoed) |
| ERROR+traceback | `weights_acquisition_crashed` | `logger.exception`; the acquisition thread never raises |
| WARNING | `weights_fetch_skipped` / `weights_mirror_skipped` | No `HF_TOKEN` / no `FORAGE_MIRROR_TOKEN` in the environment |
| WARNING | `weights_retry_scheduled` / `weights_acquisition_in_flight` | Retry armed (`retry %d in %.0fs`); second caller refused |
| INFO (dropped) | `weights_fetch_attempt`, `weights_fetched`, `weights_verified`, `weights_loaded` | The success narrative; invisible in a container |

Closed codes that fill the `reason=` and `Attempts:` slots:

- Hugging Face leg, `_fetch_reason()` (`model_fetcher.py:938`): `http_<status>`, `timeout`, `io_failed`, `fetch_failed`.
- Mirror leg, `OUTCOME_*` (`model_fetcher.py:281-298`): `ok`, `skipped_no_token`, `misconfigured`, `oras_missing`, `insufficient_space`, `pull_failed`, `timeout`, `no_artifact`, `artifact_oversized`, `extract_failed`, `install_failed`, `refused_verification`.
- Verification, `REASON_*` (`model_fetcher.py:260-274`): `manifest_missing`, `manifest_unreadable`, `manifest_empty`, `manifest_unparseable`, `manifest_invalid`, `manifest_disallowed_format`, `snapshot_missing`, `file_missing`, `file_extra`, `disallowed_format`, `size_mismatch`, `hash_mismatch`, `unreadable_file`, `symlink_escape`, `disallowed_entry`.

Pinned by, among others, `tests/test_model_fetcher.py::TestHuggingFaceFetch::test_an_http_status_survives_as_a_closed_reason_code` (`http_401` present, upstream prose absent), `::TestQuarantine::test_a_refusal_is_logged_loudly_with_its_reasons`, `::TestNoSourceProducedWeights::test_the_ending_names_every_source_in_order`, `::TestRevisionPin::test_an_invalid_override_is_reported_without_echoing_it`, and `::TestDegradedRecoveryRetry::test_every_retry_says_so_at_warning`.

---

## Implementation

- **Obtaining a logger.** `logger = logging.getLogger(__name__)` at module top, nothing else. No adapters, no `structlog`, no JSON formatter, no request-id filter.
- **Choosing a level.** Decided at the call site by the rules in Log Levels. One level was chosen for visibility rather than severity: `weights_retry_scheduled` is WARNING so that a container stuck in the retry loop says so every cycle; the comment above the call (`model_fetcher.py:1859-1862`) records the reason, and `::TestDegradedRecoveryRetry::test_every_retry_says_so_at_warning` pins it.
- **No configuration exists.** A repo-wide grep of `*.py`, `*.sh`, `Dockerfile`, `*.yml`, and `*.toml` for `basicConfig`, `dictConfig`, `LOG_LEVEL`, `--log-level`, and `log_config` returns nothing. `docs/configuration.md` lists no log-level variable. The `Dockerfile` `CMD` (line 227) is `uvicorn retrieval_app:app --host 0.0.0.0 --port 8020`.
- **Where a deployment would configure it, if ever.** There is no supported lever today. `uvicorn --log-level info` does *not* raise first-party output (measured at model-bootstrap US-004; uvicorn's config covers only its own loggers). The only levers that would work are `logging.basicConfig(level=...)` from a Python entry point or a `--log-config` file passed to uvicorn, and both are deliberately not done: GOTCHAS records the choice as "recorded, not fixed" because a global config changes every lane at once, and `/metrics.model` is the intended observability answer. Changing this is an owner decision, not a drive-by.
- **What the lines look like** (inferred from stdlib behaviour, medium confidence; only the effective level 30 was measured): with no handler configured, first-party WARNING/ERROR records fall to `logging.lastResort`, a stderr handler whose format is `%(message)s`. Expect the bare message with no timestamp, level, or logger name. `extra={...}` dicts are attached to the `LogRecord` but never rendered.
- **Uvicorn's access log** (inferred from uvicorn defaults): `uvicorn.access` writes `INFO: 127.0.0.1:x - "GET /health HTTP/1.1" 200 OK` style lines to stdout per request. It is the only per-request record; the app's `request_id` is minted per request and returned in every body but never written to a visible log line.
- **Where the bytes go.** stdout/stderr of PID 1, read with `docker logs`; no driver, rotation, or shipping is configured. `kit_tools/docs/TROUBLESHOOTING.md` "Where the Logs Are" has the per-environment table.

---

## Patterns to Follow

- **Do** report failures on a credential-bearing path with a fixed reason string chosen from a closed set. Prose that varies with the failure will eventually vary with the secret.
- **Do** give any new marker the `weights_*` shape, `snake_case_event — fixed clause`, with variable parts drawn from a closed code set, and add a `caplog` test in the same change. A marker without a test is not enforced.
- **Do** keep the startup and entrypoint paths silent about configuration values; log the *choice* (`valkey` / `memory`), never the *input*.
- **Don't** `%s` a URL, token, exception, or subprocess stream on the cache or weights paths. `str(exc)` from redis, `huggingface_hub`, or `oras` is untrusted text that may embed the credential.
- **Don't** add `logging.basicConfig()` or a `dictConfig` in a library module. It would silently reconfigure every importer, and this repo's tests import every module.
- **Don't** raise the root level, pass `--log-level`, or attach a handler as a "fix" for missing INFO lines. It changes what operators see in every deployment; take it to the owner and record it in GOTCHAS if it lands.
- **Don't** put page content, upload bytes, snippets, or query text in any log line. The caller-supplied data that does appear is the requested URL in `Cache hit for %s` (INFO) and `Content quarantined for %s` (WARNING); do not widen that.
- **Don't** rely on `extra=` for anything an operator must see; nothing renders it. Put it in the message or, better, in `/metrics`.
- **Do** prefer `/health` and `/metrics` over a new log line for state an operator polls; logs are for transitions, metrics are for state (`kit_tools/docs/MONITORING.md`).

---

## Testing

Assert log *content*, not just presence, and always include a canary so an absence check cannot pass against an empty capture. The two reference tests are `tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret` and `tests/test_app.py::test_no_selection_path_logs_the_valkey_url`; the shape they share is:

```python
import logging

import pytest


@pytest.mark.asyncio()
async def test_connect_failure_never_logs_url_or_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c = ContentCache(valkey_url="redis://:secret@unreachable:6379/4")

    with caplog.at_level(logging.WARNING, logger="cache"):
        ok = await c.connect()

    assert ok is False
    assert caplog.text.strip()  # canary: something was logged
    assert "secret" not in caplog.text  # the password
    assert "unreachable" not in caplog.text  # the host
```

Notes on the pattern:

- Scope `caplog.at_level` to the logger under test (`logger="cache"`, `logger="retrieval_app"`) when asserting a single module; use the unscoped form at `logging.DEBUG` when driving the real lifespan, because the guarantee covers every logger on the path, including the dropped INFO lines.
- For markers, assert the marker string and the closed code (`"weights_fetch_failed" in caplog.text`, `"http_401" in caplog.text`) and assert the upstream prose is absent (`"gated repo" not in caplog.text`), as `::TestHuggingFaceFetch::test_an_http_status_survives_as_a_closed_reason_code` does.
- The suite is hermetic (`tests/conftest.py` blocks sockets and clears `HF_TOKEN`, `FORAGE_MIRROR_TOKEN`, `VALKEY_URL`, and friends), so a log test never sees a real credential; inject a synthetic one and assert on that.

---

## Observed Rough Edges

Observations, not change proposals; each is either recorded elsewhere as a decision or is harmless.

- `pipeline/stage5_url_audit.py:26` defines `logger` and never calls it. Harmless, but a reader may assume the fetch path logs something; it does not.
- The stderr line format is inferred (`lastResort`, `%(message)s`); a container run has only ever confirmed the effective level. Anyone building log parsing on the format should measure it first.
- `extra=` dicts on `document extraction completed`, `search_promptguard_complete`, and `search_promptguard_local_latency_target_exceeded` are never rendered; the last of these is WARNING and so prints as a bare marker with no duration.
- One *response* body interpolates exception text: `fetch_error` (`Failed to fetch <url>: <exc>`, `pipeline/orchestrator.py`). It is a wire string, not a log line, and that path carries no credential; keep the two channels distinct and do not copy it into a log call. `searxng_unavailable` was the second case until `search-provider-abstraction` US-002 replaced its `str(exc)` with a closed provider `detail` token and its raw-URL echo with `SearxngProvider.origin`.
- Provider failures carry their closed tokens **in the message text**, never only in `extra=` (which nothing renders): `pipeline/search_providers/brave.py` emits `brave_search_failed — <detail> (<failure_class>)`, `pipeline/search_providers/searxng.py` emits one WARNING per failed search, `search_provider_failure`, with `provider`, `failure_class` and `detail`, and `pipeline/orchestrator.py` pairs each with `search_provider_failed provider=… failure_class=… detail=…`. Nothing else is carried — no `exc_info`, no `str(exc)`, no URL, because an httpx message embeds the request URL and with it the query string and any userinfo in `SEARXNG_URL`. `tests/test_search_providers.py` asserts it across every failure mode. Every provider follows the tokens-in-message shape.
- Line style is mixed: newer sites use `snake_case_event — detail` (`weights_*`, `break_glass_advertisement_active`, `search_promptguard_*`); older sites are prose with `%s` arguments. New lines should use the marker style.
- `request_id` is never written to a visible log line, so a log line cannot be joined to a response; the uvicorn access log is the only per-request record.

Related: `../SECURITY.md` "Closed log vocabularies" and "Security-Relevant Logging and Metrics"; `ERROR_HANDLING.md` "Error Categories"; `../../docs/MONITORING.md` "Logging"; `../../docs/CONVENTIONS.md` "Logging"; `../../docs/GOTCHAS.md` "Nothing configures logging"; `../../../docs/configuration.md` "Credentials in logs".
