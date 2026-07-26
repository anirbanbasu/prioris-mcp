import importlib
from pathlib import Path

import pytest

import prioris_mcp


@pytest.fixture(autouse=True)
def _restore_module_after_reload():
    """Reload prioris_mcp once more after each test so later test modules see a clean EnvVars."""
    yield
    importlib.reload(prioris_mcp)


class TestStorageDirDefault:
    """EnvVars.PRIORIS_MCP_STORAGE_DIR default computation."""

    def test_defaults_under_xdg_data_home_when_set(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("XDG_DATA_HOME", "/tmp/fake-xdg-data-home")
        monkeypatch.delenv("PRIORIS_MCP_STORAGE_DIR", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_STORAGE_DIR == Path("/tmp/fake-xdg-data-home/prioris-mcp/downloads")

    def test_falls_back_to_local_share_when_xdg_data_home_unset(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.delenv("PRIORIS_MCP_STORAGE_DIR", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert (
            reloaded.EnvVars.PRIORIS_MCP_STORAGE_DIR == Path.home() / ".local" / "share" / "prioris-mcp" / "downloads"
        )

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_STORAGE_DIR", "/tmp/explicit-override")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_STORAGE_DIR == Path("/tmp/explicit-override")


class TestRateLimitBackoffBudgetDefault:
    """EnvVars.PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS default and override."""

    def test_defaults_to_60_seconds(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS == 60.0

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS", "12.5")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS == 12.5
