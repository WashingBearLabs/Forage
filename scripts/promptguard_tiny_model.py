"""A tiny, seeded, random-init DeBERTa-v2 classifier for weights-free tests.

Pytest-free on purpose: it imports only torch, transformers and the standard
library, so the image's CPU parity step can import it too. The 86M is never
constructed here.

``python -m scripts.promptguard_tiny_model`` prints one ``float.hex()`` score
per sample window (seed 0), in order, one per line, for diffing across builds.
"""

from __future__ import annotations

from typing import Any, cast

import torch
from transformers import DebertaV2Config, DebertaV2ForSequenceClassification

VOCAB_SIZE = 64


def build_model(seed: int = 0) -> DebertaV2ForSequenceClassification:
    """A 2-layer, small-vocab, binary sequence classifier in eval mode."""
    cast(Any, torch).manual_seed(seed)
    config = DebertaV2Config(
        vocab_size=VOCAB_SIZE,
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        max_position_embeddings=128,
        num_labels=2,
    )
    model = DebertaV2ForSequenceClassification(config)
    model.eval()
    return model


def sample_inputs() -> list[list[int]]:
    """A fixed list of token-id windows of varied lengths."""
    lengths = (1, 3, 7, 16, 33, 64, 100)
    return [
        [(i * 7 + position * 3 + 1) % (VOCAB_SIZE - 1) + 1 for position in range(n)]
        for i, n in enumerate(lengths)
    ]


def reference_scores(
    model: DebertaV2ForSequenceClassification, inputs: list[list[int]]
) -> list[float]:
    """Frozen copy of the production batch-1 loop: forward, softmax, index 1."""
    scores: list[float] = []
    for window in inputs:
        input_ids = torch.tensor([window])
        attention_mask = torch.ones_like(input_ids)
        with torch.no_grad():
            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        probs = torch.softmax(outputs.logits, dim=-1)
        scores.append(float(probs[0, 1].item()))
    return scores


if __name__ == "__main__":
    for score in reference_scores(build_model(0), sample_inputs()):
        print(score.hex())
