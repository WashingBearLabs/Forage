# `typings/` — local stubs for third-party gaps

Pyright runs in **strict** mode over the whole repo (see `[tool.pyright]` in
`pyproject.toml`). Most of Forage's dependencies ship `py.typed` and need
nothing here. This directory exists only for the cases where a dependency's
*shipped* types leave the specific symbols Forage calls typed as `Unknown` —
where the alternative would be an inline suppression, which the type-checking
policy forbids.

## The rule

**A stub in here declares only the symbols this repository actually calls, with
the narrowest signature that is true.**

A stub package shadows the real one for pyright: once `typings/foo/__init__.pyi`
exists, pyright stops reading `foo`'s own types entirely. That makes a
"helpful" broad stub actively dangerous — a hand-written shadow of a large
library's real types drifts silently, and the day it drifts is the day the type
checker starts confidently telling you something false. A small stub cannot
drift far, and if code starts using a symbol the stub does not declare, pyright
says so loudly instead of guessing.

So: no convenience re-exports, no "might need it later" signatures, no
transcribing upstream's full API. Add a symbol when a caller needs it, and
delete it when the last caller goes away.

## What is here, and why

| Stub | Reason |
|---|---|
| `transformers/__init__.pyi` | `AutoTokenizer.from_pretrained` and `AutoModelForSequenceClassification.from_pretrained` are typed as returning `Unknown` upstream, so every downstream use in `promptguard/classifier.py` (tokenising, the forward pass, `.logits`) decays to `Unknown` too. |
| `huggingface_hub/__init__.pyi` | `snapshot_download`'s `user_agent` parameter is annotated as a bare `dict`, which makes its whole overload set partially unknown under strict mode — and the symbol itself unusable at `model_fetcher.py`'s call site. The stub declares the five keywords the fetcher passes and the one overload it uses. |

**`torch` deliberately has no stub here.** It ships real, complete types; the
classifier consumes them directly (`torch.no_grad`, `torch.softmax`,
`torch.Tensor`). Hand-writing a shadow of torch's type surface would be exactly
the drift hazard described above, at the largest possible scale.
