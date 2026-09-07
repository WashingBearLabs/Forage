"""Tests for the SearXNG configuration Forage ships.

Validates ``searxng/config/`` without requiring Docker. The compose-half of
this module (the ``poppy-searxng`` service definition and its
``compose_helpers.load_compose`` dependency) stayed behind in Poppy at the
repo split — Forage carries no docker-compose.yml of its own, and the example
compose is spec 4's work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SEARXNG_CONFIG = _REPO_ROOT / "searxng" / "config"


class TestSearxngSettings:
    """Validate searxng/config/settings.yml."""

    @pytest.fixture
    def settings(self) -> dict[str, Any]:
        path = _SEARXNG_CONFIG / "settings.yml"
        assert path.exists(), "settings.yml must exist at searxng/config/"
        return yaml.safe_load(path.read_text())

    def test_json_format_enabled(self, settings: dict[str, Any]) -> None:
        formats = settings.get("search", {}).get("formats", [])
        assert "json" in formats

    def test_server_port_8080(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["port"] == 8080

    def test_server_binds_all_interfaces(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["bind_address"] == "0.0.0.0"

    def test_secret_key_set(self, settings: dict[str, Any]) -> None:
        assert settings["server"]["secret_key"]

    def test_engines_configured(self, settings: dict[str, Any]) -> None:
        engines = settings.get("engines", [])
        engine_names = {e["name"] for e in engines}
        assert "duckduckgo" in engine_names
        assert "bing" in engine_names
        assert "brave" in engine_names

    def test_request_timeout(self, settings: dict[str, Any]) -> None:
        assert settings["outgoing"]["request_timeout"] == 10

    def test_default_lang_english(self, settings: dict[str, Any]) -> None:
        assert settings["search"]["default_lang"] == "en"


class TestSearxngLimiter:
    """Validate searxng/config/limiter.toml exists."""

    def test_limiter_toml_exists(self) -> None:
        path = _SEARXNG_CONFIG / "limiter.toml"
        assert path.exists(), "limiter.toml must exist at searxng/config/"

    def test_limiter_has_content(self) -> None:
        path = _SEARXNG_CONFIG / "limiter.toml"
        content = path.read_text()
        assert len(content) > 10, "limiter.toml should have meaningful content"
