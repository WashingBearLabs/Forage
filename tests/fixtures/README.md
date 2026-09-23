# `tests/fixtures/`

Committed fixtures for the test suite. Nothing here is shipped in the image —
`.dockerignore` excludes `tests/` outright.

## `search/` pipeline pins

`test_search_pipeline_pins.py` captures four complete synthetic search responses
and their five pinned counters, plus two exhaustion payloads and their actual
handler status/counters. Captured before the provider-loop refactor at
`2a275c50165d0538ed07d05a9bb3a9ea2dcbb36d`: clean SearXNG (with an unresponsive
engine), fallback to Brave, every omission reason, honest served-empty, lone
SearXNG exhaustion and mixed-chain exhaustion. Inputs are synthetic SearXNG
dicts and the scrubbed `brave/llm_context_sample.json`, through streaming
doubles; no live fetch or Brave-authored result text is persisted.
`request_id` is removed **before writing**; all other response fields are pinned.

Regenerate, never hand-edit, only for a deliberate wire or pinned-counter change:

```bash
uv run pytest tests/test_search_pipeline_pins.py --regenerate-search-pins -q
```

Commit regenerated pins with that change and explain what moved and why in the
commit message. Without the flag tests only compare. New metrics outside the
five-counter projection do not move these pins. The token guard walks all fixture
directories by default, except exactly `tiny_model/`, `contract/` and this README.
Four long schema key names in these dumps are recognized only in JSON key
positions; their spelling as a payload value remains forbidden.

## `search/baseline_pre_blocked_domains.json`

Captured on 2026-09-22 from commit
`a80b2819f5626b44c161c47fddd72c6982f9149d`, **before** implementing
`hardening-hostname-and-config` US-002 or touching its handler. The real
`run_search_pipeline` ran with the socket guard enabled, empty config, a
`FakeSearchProvider` named `searxng` followed by an uncalled paid `brave`,
and a loaded classifier double returning `(0.1, [])` at threshold **0.85**.
The request was `{"query": "domain policy baseline", "num_results": 3}`,
sending neither `blocked_domains` nor `promptguard_threshold`.

The three fake results, in order, use hosts `a.example`,
`www.blocked.example`, and `blocked.example`; each URL is
`https://<host>/article`. Their titles are `Result 1` through `Result 3`,
their `content` values are `Calm search excerpt 1.` through
`Calm search excerpt 3.`, and each engine is `google`.

Only `results`, `omitted_by_reason`, `fallback_fired`, `provider_used`, and
`provider_errors` are pinned, not whole-response equality: later stories in
the 1.3.0 window add envelope fields. This fixture is deliberately **not
regenerable after this story**. If it goes red later for a request sending
neither new field, the change to these five fields is a behaviour change
that the later story must classify under `contract/GOVERNANCE.md` before
editing this fixture.

## `contract/unregenerated_openapi.yaml`

The contract drift check's own failure case, committed rather than staged by hand.
`feature-forage-contract` US-002 asks for a *deliberate un-regenerated change* that fails
`tests/test_contract_export.py`, and a gate nobody has watched fail is not yet a gate — so
this file is `contract/openapi.yaml` with one property removed,
`Extract422ErrorResponse.sanitizer_revision`: exactly the file you would have on disk if
you had added that field to the model and forgotten to regenerate. The property is not an
arbitrary pick — it is the one Poppy hard-rejects a 422 without.

It is **generated**, by the same command as the contract itself:

```bash
uv run python -m scripts.export_contract
```

so it can never fall behind the document it is a twin of. Two tests use it, and they check
different things: one feeds it to the real `drift_report()` and asserts it is caught, the
other asserts it differs from the live document in that one documented way and no other —
without which a fixture that had rotted into some unrelated file would keep the first test
green for the wrong reason.

## `tiny_model/`

A **real, loadable** DeBERTa-v2 sequence classifier with random weights: 4 files,
~96 KB, `model.safetensors` only. `feature-forage-model-bootstrap` US-002 needs it
because a synthetic byte fixture cannot exercise the loader — the story's whole point
is that `from_pretrained(use_safetensors=True)` opens the verified set and nothing
else — and US-004's mirror-to-load test consumes the same fixture end to end.

It is the same architecture family as the real classifier
(`meta-llama/Llama-Prompt-Guard-2-22M` is a DeBERTa-v3 sequence classifier), two
layers deep, 64-token vocabulary, so it loads in milliseconds and needs no network:
the suite's autouse `pytest-socket` guard would fail any test that reached for one.

**Provenance.** Generated locally, from configuration only — no weights were
downloaded, and no gated repository was touched. To regenerate (any change to the
bytes is a fixture change, not a model change):

```python
from pathlib import Path

import torch
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
from transformers import DebertaV2Config, DebertaV2ForSequenceClassification
from transformers import PreTrainedTokenizerFast

out = Path("tests/fixtures/tiny_model")
vocab = ["[PAD]", "[CLS]", "[SEP]", "[UNK]", "[MASK]"] + [f"tok{i}" for i in range(59)]

tokenizer = Tokenizer(
    models.WordPiece({t: i for i, t in enumerate(vocab)}, unk_token="[UNK]")
)
tokenizer.normalizer = normalizers.Sequence(
    [normalizers.NFD(), normalizers.Lowercase()]
)
tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
tokenizer.post_processor = processors.TemplateProcessing(
    single="[CLS] $A [SEP]",
    pair="[CLS] $A [SEP] $B:1 [SEP]:1",
    special_tokens=[("[CLS]", 1), ("[SEP]", 2)],
)
PreTrainedTokenizerFast(
    tokenizer_object=tokenizer,
    unk_token="[UNK]",
    pad_token="[PAD]",
    cls_token="[CLS]",
    sep_token="[SEP]",
    mask_token="[MASK]",
    model_max_length=512,
).save_pretrained(out)

torch.manual_seed(0)
DebertaV2ForSequenceClassification(
    DebertaV2Config(
        vocab_size=len(vocab),
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        max_position_embeddings=64,
        relative_attention=False,
        type_vocab_size=0,
        num_labels=2,
        id2label={0: "BENIGN", 1: "INJECTION"},
        label2id={"BENIGN": 0, "INJECTION": 1},
    )
).eval().save_pretrained(out, safe_serialization=True)
```

`tests/test_model_fetcher.py::TestLoadableFixture` is the guard: it loads the fixture
with `use_safetensors=True`, refuses to accept any non-allowlisted filename in the
directory, and runs it through the real verifier in a real Hugging Face cache layout.

## `brave/llm_context_sample.json`

The Brave LLM-Context response **envelope**, captured once by the owner on 2026-09-16
(`feature-brave-provider` US-001's pre-flight gate, epic ruling 24). The parser in
`pipeline/search_providers/brave.py` is written against this file and only this file —
a fixture typed from memory of the docs is exactly the failure the pinned-sample rule
exists to prevent.

**Endpoint.** `GET https://api.search.brave.com/res/v1/llm/context` with
`q=history+of+the+bicycle&count=3`. Documentation:
<https://api-dashboard.search.brave.com/documentation/services/llm-context>.

**Capture procedure.** One credentialed request from a shell — never a test, never CI.
The auth header line was written into a `curl -K` config file created under `umask 077`
from an environment variable and deleted afterwards, so the key never appeared on a
command line, in shell history, or in the process table. The raw response (30,344 bytes,
3 sources, 84 chunks: 35 / 27 / 22) was read locally for its shape and then deleted.

**Substitution rule.** Every field name, the nesting, every value type and every element
count are exactly as captured. Every chunk body, source snippet, title, URL, hostname and
date was replaced by an obviously synthetic value: chunk and snippet bodies are
`synthetic chunk j of m for source i of 3 …` padded to the captured string's exact
length, so the byte profile is representative; URLs are
`https://synthetic-i.example.invalid/…` (`.invalid` is reserved and can never resolve);
dates are the first three days of January 2026 in the four captured formats. Nothing odd
in the real shape was "fixed" while substituting. No request headers, no key and no
Brave-authored text is committed (owner decision 6: Brave's terms do not license
redistributing response text, and a shape fixture needs none of it).

**Observed shape** — what the parser follows; the spec's pre-capture list was a guess:

- `grounding` carries `generic` (a list of `{url, title, snippets}`, `snippets` a list of
  strings) and `map` (an empty list in this capture). No `poi` key was present.
- `sources` is a map keyed by URL, in the same order as `generic`, and each value is
  `{title, hostname, age, snippet}`. `age` is a **list of four strings** — a long-form
  date, a ten-character ISO date, a relative "N days ago", and an ISO-8601 timestamp —
  not a single string. `snippet` is a short string (about 100–200 characters) that the
  documentation page does not list (it documents an optional `description` instead).

**Guard.** `tests/test_brave_provider.py` (US-001) walks `tests/fixtures/` and asserts no
file carries an auth header name, and walks all payload fixtures (the exact exclusions
above) for any token-shaped literal (24 or more letters, digits, `_` or `-` in a row).
Re-capturing is a fixture
change: repeat the procedure above and update this note's date, byte size and counts.
