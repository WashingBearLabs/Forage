"""Minimal stubs for the four `transformers` symbols Forage calls.

`transformers` ships `py.typed`, but the auto-class factories are annotated as
returning `Unknown`, which propagates through every use in
`promptguard/classifier.py`: the tokenizer's `encode`/`decode`/`__call__`, the
model's forward pass, and `outputs.logits` all decay to `Unknown` under strict
mode.

This file declares *only* what the classifier calls, with the narrowest true
signature — see `typings/README.md`. A stub package shadows the real one, so
anything used but not declared here is a pyright error rather than a silent
`Unknown`, which is the intended failure mode.
"""

from collections.abc import Iterator, Mapping, Sequence
from os import PathLike

import torch

class BatchEncoding(Mapping[str, torch.Tensor]):
    """Tokenizer output; splatted into the model as `**inputs`."""

    def __getitem__(self, key: str) -> torch.Tensor: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...

class SequenceClassifierOutput:
    """The forward-pass result the classifier reads `logits` off."""

    logits: torch.Tensor

class PreTrainedTokenizerBase:
    def __call__(
        self,
        text: str,
        *,
        return_tensors: str | None = ...,
        truncation: bool = ...,
        max_length: int | None = ...,
        padding: bool = ...,
    ) -> BatchEncoding: ...
    def encode(self, text: str, *, add_special_tokens: bool = ...) -> list[int]: ...
    def decode(
        self, token_ids: Sequence[int], *, skip_special_tokens: bool = ...
    ) -> str: ...

class PreTrainedModel:
    def eval(self) -> PreTrainedModel: ...
    def __call__(self, **kwargs: torch.Tensor) -> SequenceClassifierOutput: ...

class AutoTokenizer:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: str | PathLike[str],
        *,
        local_files_only: bool = ...,
    ) -> PreTrainedTokenizerBase: ...

class AutoModelForSequenceClassification:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: str | PathLike[str],
        *,
        use_safetensors: bool = ...,
        local_files_only: bool = ...,
    ) -> PreTrainedModel: ...
