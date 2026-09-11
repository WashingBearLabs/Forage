"""Minimal stub for the one `huggingface_hub.constants` symbol Forage touches.

`typings/huggingface_hub/__init__.pyi` shadows the real package for pyright, so
every submodule this repository imports needs a declaration here or the import
does not type-check at all. Per `typings/README.md`, that declaration stays as
small as the caller allows: one name.

`HF_HUB_OFFLINE` is the constant the `HF_HUB_OFFLINE` environment variable
ultimately sets. `huggingface_hub` samples the variable **at import time**, so
setting it in a running process does nothing — `model_fetcher._offline_hub()`
assigns the constant directly instead, scoped to a warm-path load, to stop the
library's best-effort harness-registry request from reaching the network.
"""

HF_HUB_OFFLINE: bool
