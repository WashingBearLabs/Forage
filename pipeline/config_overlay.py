"""Read, validate and merge the operator's config overlay (pure, no I/O but one file).

``FORAGE_CONFIG_OVERLAY`` names a YAML file merged over the base config. This
module is the whole contract for that file and imports nothing from
``retrieval_app``: the caller passes the known-key set at call time.

Merge rules: the ``cache``, ``extraction`` and ``retrieve`` blocks merge leaf by
leaf; every other value, lists included, replaces the base value outright.

Every defect raises :class:`ConfigOverlayError` carrying one of six closed
tokens. The message is built only from the token, a fixed phrase and at most one
*validated* key name; it never contains the path, a value or operator text, and
every raise is ``from None`` so no chained ``OSError``/``YAMLError`` (whose text
quotes the path or a YAML snippet) reaches a traceback. Value types and ranges
are not checked here; the typed settings readers own them.
"""

from __future__ import annotations

import copy
import os
import re
import stat
from collections.abc import Collection, Hashable
from typing import Any, Literal, cast

import yaml

ConfigOverlayToken = Literal[
    "overlay_missing",
    "overlay_unreadable",
    "overlay_not_mapping",
    "overlay_unknown_key",
    "overlay_null_value",
    "overlay_shape_mismatch",
]

MAX_OVERLAY_BYTES = 1_048_576

_MERGE_TAG = "tag:yaml.org,2002:merge"
_SAFE_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}(\.[a-z][a-z0-9_]{0,63})?")
_UNPRINTABLE = "<unprintable key>"

_PHRASES: dict[ConfigOverlayToken, str] = {
    "overlay_missing": "overlay file is missing or not a regular file",
    "overlay_unreadable": "overlay file could not be read or parsed",
    "overlay_not_mapping": "overlay document is not a mapping",
    "overlay_unknown_key": "overlay names a key that is not a known config key",
    "overlay_null_value": "overlay sets a null value",
    "overlay_shape_mismatch": "overlay value has the wrong shape for its key",
}


class ConfigOverlayError(ValueError):
    """A refused overlay. ``token`` is the closed, operator-facing reason."""

    token: ConfigOverlayToken

    def __init__(self, token: ConfigOverlayToken, key: str | None = None) -> None:
        message = f"{token}: {_PHRASES[token]}"
        if key is not None:
            message = f"{message} (key={key})"
        super().__init__(message)
        self.token = token
        # Never display chained context, whichever way this is raised.
        self.__suppress_context__ = True


def _refuse(token: ConfigOverlayToken, key: str | None = None) -> ConfigOverlayError:
    return ConfigOverlayError(token, key)


class _OverlayLoader(yaml.SafeLoader):
    """SafeLoader that refuses aliases, duplicate keys and merge keys."""

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("alias")
        node = super().compose_node(parent, index)
        if isinstance(node, yaml.MappingNode):
            seen: set[Hashable] = set()
            for key_node, _ in node.value:
                if key_node.tag == _MERGE_TAG:
                    raise yaml.YAMLError("merge key")
                if isinstance(key_node, yaml.ScalarNode):
                    key = cast(
                        Hashable, cast(Any, self).construct_object(key_node, deep=True)
                    )
                    if key in seen:
                        raise yaml.YAMLError("duplicate key")
                    seen.add(key)
        return node


def read_overlay_file(path: str) -> dict[str, Any]:
    """Return the overlay mapping, or raise :class:`ConfigOverlayError`."""
    try:
        try:
            mode = os.stat(path).st_mode
        except (FileNotFoundError, NotADirectoryError, ValueError):
            raise _refuse("overlay_missing") from None
        except OSError:
            raise _refuse("overlay_unreadable") from None
        if not stat.S_ISREG(mode):
            raise _refuse("overlay_missing")
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_OVERLAY_BYTES + 1)
        except OSError:
            raise _refuse("overlay_unreadable") from None
        if len(raw) > MAX_OVERLAY_BYTES:
            raise _refuse("overlay_unreadable")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _refuse("overlay_unreadable") from None
        try:
            document: Any = yaml.load(text, Loader=_OverlayLoader)
        except (yaml.YAMLError, RecursionError, ValueError, OverflowError):
            raise _refuse("overlay_unreadable") from None
    except ConfigOverlayError as exc:
        # Re-raise with the context stripped: nothing above may chain.
        exc.__cause__ = None
        exc.__suppress_context__ = True
        raise
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise _refuse("overlay_not_mapping") from None
    return cast(dict[str, Any], document)


def _echo(key: object) -> str:
    if isinstance(key, str) and _SAFE_KEY.fullmatch(key):
        return key
    return _UNPRINTABLE


def _has_null(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return any(_has_null(item) for item in cast(list[object], value))
    return False


def _check_value(
    dotted: str, value: object, base_value: object, *, is_block: bool
) -> None:
    """Null, then shape, for one value under a validated (known) key."""
    if _has_null(value):
        raise _refuse("overlay_null_value", dotted)
    if is_block:
        if not isinstance(value, dict):
            raise _refuse("overlay_shape_mismatch", dotted)
        return
    if isinstance(value, dict):
        raise _refuse("overlay_shape_mismatch", dotted)
    if isinstance(base_value, list):
        if not isinstance(value, list):
            raise _refuse("overlay_shape_mismatch", dotted)
    elif isinstance(base_value, (str, int, float, bool)) and isinstance(value, list):
        raise _refuse("overlay_shape_mismatch", dotted)


def merge_config_overlay(
    base: dict[str, Any],
    overlay: dict[str, Any],
    known_keys: Collection[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Merge ``overlay`` over ``base``; return the new mapping and the keys set.

    Neither input is mutated. ``known_keys`` is read at call time.
    """
    top_level = {key for key in known_keys if "." not in key}
    blocks = {key.split(".", 1)[0] for key in known_keys if "." in key}
    leaves = {key for key in known_keys if "." in key}

    raw_keys = list(cast(dict[object, Any], overlay))
    if not all(isinstance(key, str) for key in raw_keys):
        raise _refuse("overlay_unknown_key", _UNPRINTABLE) from None
    overlay_keys = cast(list[str], raw_keys)

    merged = dict(base)
    set_keys: list[str] = []
    for key in sorted(overlay_keys):
        if key not in top_level:
            raise _refuse("overlay_unknown_key", _echo(key)) from None
        value = overlay[key]
        base_value = base.get(key)
        _check_value(key, value, base_value, is_block=key in blocks)
        if key not in blocks:
            merged[key] = value
            set_keys.append(key)
            continue
        leaf_map = cast(dict[object, Any], value)
        for leaf in leaf_map:
            if not isinstance(leaf, str):
                raise _refuse("overlay_unknown_key", _UNPRINTABLE) from None
        block: dict[str, Any] = (
            dict(cast(dict[str, Any], base_value))
            if isinstance(base_value, dict)
            else {}
        )
        for leaf in sorted(cast(list[str], list(leaf_map))):
            dotted = f"{key}.{leaf}"
            if dotted not in leaves:
                raise _refuse("overlay_unknown_key", _echo(dotted)) from None
            leaf_value = leaf_map[leaf]
            _check_value(dotted, leaf_value, block.get(leaf), is_block=False)
            block[leaf] = leaf_value
            set_keys.append(dotted)
        if leaf_map:
            merged[key] = block
    return copy.deepcopy(merged), tuple(sorted(set_keys))
