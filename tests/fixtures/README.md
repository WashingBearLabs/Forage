# `tests/fixtures/`

Committed fixtures for the test suite. Nothing here is shipped in the image —
`.dockerignore` excludes `tests/` outright.

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
