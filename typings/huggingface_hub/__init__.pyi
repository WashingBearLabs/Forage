"""Minimal stub for the one `huggingface_hub` symbol Forage calls.

`huggingface_hub` ships `py.typed`, and almost all of it types cleanly. The
gap is `snapshot_download`: its `user_agent` parameter is annotated as a bare
`dict`, which under strict mode makes the whole overload set — and therefore
the symbol itself — partially unknown at the call site in `model_fetcher.py`.

This declares only the keywords the fetcher actually passes, in the shape it
passes them, and only the non-`dry_run` overload it uses (which is the one
that returns the snapshot path as a `str`). Per `typings/README.md`: a stub
shadows the real package, so it stays as small as the caller allows.
"""

from os import PathLike

def snapshot_download(
    repo_id: str,
    *,
    revision: str | None = ...,
    cache_dir: str | PathLike[str] | None = ...,
    allow_patterns: list[str] | str | None = ...,
    token: bool | str | None = ...,
    local_files_only: bool = ...,
) -> str: ...
