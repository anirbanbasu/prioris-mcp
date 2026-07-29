import importlib
import os
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


class TestHttpTimeoutSecondsDefault:
    """EnvVars.PRIORIS_MCP_HTTP_TIMEOUT_SECONDS default and override."""

    def test_defaults_to_30_seconds(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_HTTP_TIMEOUT_SECONDS", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_HTTP_TIMEOUT_SECONDS == 30.0

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_HTTP_TIMEOUT_SECONDS", "10")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_HTTP_TIMEOUT_SECONDS == 10.0


class TestMaxInlineCharsDefault:
    """EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS default and override."""

    def test_defaults_to_20000(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_MAX_INLINE_CHARS", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS == 20000

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_MAX_INLINE_CHARS", "5000")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS == 5000


class TestJatsMaxConcurrentTransformsDefault:
    """EnvVars.PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS default, override, and CPU-count clamp."""

    def test_defaults_to_4_when_cpu_count_is_at_least_4(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS", raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS == 4

    def test_defaults_to_cpu_count_when_below_4(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS", raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: 2)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS == 2

    def test_explicit_override_wins_when_within_cpu_count(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS", "3")
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS == 3

    def test_override_is_clamped_to_cpu_count_even_if_higher(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS", "64")
        monkeypatch.setattr(os, "cpu_count", lambda: 4)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS == 4


class TestLocalFileRootDefault:
    """EnvVars.PRIORIS_MCP_LOCAL_FILE_ROOT default and override."""

    def test_defaults_to_current_working_directory(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_LOCAL_FILE_ROOT", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_LOCAL_FILE_ROOT == Path.cwd()

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_LOCAL_FILE_ROOT", "/tmp/explicit-root")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_LOCAL_FILE_ROOT == Path("/tmp/explicit-root")


class TestLocalFileMaxSizeBytesDefault:
    """EnvVars.PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES default and override."""

    def test_defaults_to_10mb(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES == 10 * 1024 * 1024

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES", "1024")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES == 1024
