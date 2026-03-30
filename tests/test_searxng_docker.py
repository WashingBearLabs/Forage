"""Tests for US-001 — SearXNG Docker Container Setup.

Validates the docker-compose.yml service definition and SearXNG configuration
files without requiring Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestSearxngDockerCompose:
    """Validate poppy-searxng service in docker-compose.yml."""

    @pytest.fixture
    def compose_text(self) -> str:
        path = _REPO_ROOT / "docker-compose.yml"
        assert path.exists()
        return path.read_text()

    @pytest.fixture
    def compose(self) -> dict:
        path = _REPO_ROOT / "docker-compose.yml"
        return yaml.safe_load(path.read_text())

    def test_searxng_service_defined(self, compose: dict) -> None:
        assert "poppy-searxng" in compose["services"]

    def test_searxng_image(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        assert svc["image"] == "searxng/searxng:latest"

    def test_searxng_container_name(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        assert svc["container_name"] == "poppy-searxng"

    def test_searxng_restart_policy(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        assert svc["restart"] == "unless-stopped"

    def test_searxng_on_poppy_net(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        assert "poppy-net" in svc["networks"]

    def test_searxng_config_volume(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        volumes = svc.get("volumes", [])
        assert any("./config/searxng:/etc/searxng" in str(v) for v in volumes)

    def test_searxng_has_healthcheck(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        hc = svc.get("healthcheck", {})
        assert hc, "healthcheck must be defined"
        test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
        assert "healthz" in test_cmd

    def test_searxng_no_host_port_binding(self, compose: dict) -> None:
        """SearXNG is internal only — no host port mapping."""
        svc = compose["services"]["poppy-searxng"]
        assert "ports" not in svc, "SearXNG should not have host port bindings"

    def test_searxng_traefik_disabled(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        labels = svc.get("labels", {})
        assert labels.get("traefik.enable") == "false"

    def test_searxng_poppy_labels(self, compose: dict) -> None:
        svc = compose["services"]["poppy-searxng"]
        labels = svc.get("labels", {})
        assert labels.get("poppy.enabled") == "true"
        assert labels.get("poppy.name") == "poppy-searxng"
        assert labels.get("poppy.tier") == "core"
        assert labels.get("poppy.category") == "retrieval"
        assert labels.get("poppy.health") == "/healthz"
        assert labels.get("poppy.port") == "8080"

    def test_searxng_secret_env_var(self, compose: dict) -> None:
        """Recent SearXNG versions require SEARXNG_SECRET env var."""
        svc = compose["services"]["poppy-searxng"]
        env = svc.get("environment", {})
        assert "SEARXNG_SECRET" in env

    def test_retrieval_depends_on_searxng(self, compose: dict) -> None:
        """poppy-retrieval should depend on poppy-searxng."""
        svc = compose["services"]["poppy-retrieval"]
        deps = svc.get("depends_on", {})
        assert "poppy-searxng" in deps

    def test_no_port_8080_collision(self, compose: dict) -> None:
        """SearXNG uses 8080 internally; verify no host port collision."""
        svc = compose["services"]["poppy-searxng"]
        # SearXNG should have no host ports at all
        assert "ports" not in svc


class TestSearxngSettings:
    """Validate config/searxng/settings.yml."""

    @pytest.fixture
    def settings(self) -> dict:
        path = _REPO_ROOT / "config" / "searxng" / "settings.yml"
        assert path.exists(), "settings.yml must exist at config/searxng/"
        return yaml.safe_load(path.read_text())

    def test_json_format_enabled(self, settings: dict) -> None:
        formats = settings.get("search", {}).get("formats", [])
        assert "json" in formats

    def test_server_port_8080(self, settings: dict) -> None:
        assert settings["server"]["port"] == 8080

    def test_server_binds_all_interfaces(self, settings: dict) -> None:
        assert settings["server"]["bind_address"] == "0.0.0.0"

    def test_secret_key_set(self, settings: dict) -> None:
        assert settings["server"]["secret_key"]

    def test_engines_configured(self, settings: dict) -> None:
        engines = settings.get("engines", [])
        engine_names = {e["name"] for e in engines}
        assert "duckduckgo" in engine_names
        assert "bing" in engine_names
        assert "brave" in engine_names

    def test_request_timeout(self, settings: dict) -> None:
        assert settings["outgoing"]["request_timeout"] == 10

    def test_default_lang_english(self, settings: dict) -> None:
        assert settings["search"]["default_lang"] == "en"


class TestSearxngLimiter:
    """Validate config/searxng/limiter.toml exists."""

    def test_limiter_toml_exists(self) -> None:
        path = _REPO_ROOT / "config" / "searxng" / "limiter.toml"
        assert path.exists(), "limiter.toml must exist at config/searxng/"

    def test_limiter_has_content(self) -> None:
        path = _REPO_ROOT / "config" / "searxng" / "limiter.toml"
        content = path.read_text()
        assert len(content) > 10, "limiter.toml should have meaningful content"
