import importlib
import os
from pathlib import Path

import pytest
from environs import EnvValidationError

import prioris_mcp


@pytest.fixture(autouse=True)
def _restore_module_after_reload():
    """Reload prioris_mcp once more after each test so later test modules see a clean EnvVars."""
    yield
    importlib.reload(prioris_mcp)


def _reload_prioris_mcp():
    """Reload prioris_mcp module and return it."""
    return importlib.reload(prioris_mcp)


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


class TestPriorisMcpNotesDir:
    """EnvVars.PRIORIS_MCP_NOTES_DIR default computation."""

    def test_defaults_under_xdg_data_home_when_set(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("XDG_DATA_HOME", "/tmp/fake-xdg-data-home")
        monkeypatch.delenv("PRIORIS_MCP_NOTES_DIR", raising=False)
        reloaded = _reload_prioris_mcp()
        assert reloaded.EnvVars.PRIORIS_MCP_NOTES_DIR == Path("/tmp/fake-xdg-data-home/prioris-mcp/notes")

    def test_falls_back_to_local_share_when_xdg_data_home_unset(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.delenv("PRIORIS_MCP_NOTES_DIR", raising=False)
        reloaded = _reload_prioris_mcp()
        assert reloaded.EnvVars.PRIORIS_MCP_NOTES_DIR == Path.home() / ".local" / "share" / "prioris-mcp" / "notes"

    def test_explicit_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_NOTES_DIR", "/tmp/explicit-notes-override")
        reloaded = _reload_prioris_mcp()
        assert reloaded.EnvVars.PRIORIS_MCP_NOTES_DIR == Path("/tmp/explicit-notes-override")


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


class TestDiscoveryEnvVars:
    """OpenAlex discovery environment-variable defaults and validation."""

    def test_openalex_api_key_defaults_to_none(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_OPENALEX_API_KEY", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_OPENALEX_API_KEY is None

    def test_openalex_api_key_reads_from_env(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_OPENALEX_API_KEY", "test-api-key-123")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_OPENALEX_API_KEY == "test-api-key-123"

    def test_discovery_max_results_defaults_to_25(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_DISCOVERY_MAX_RESULTS", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_DISCOVERY_MAX_RESULTS == 25

    def test_discovery_max_results_rejects_above_50(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_DISCOVERY_MAX_RESULTS", "51")
        with pytest.raises(EnvValidationError):
            importlib.reload(prioris_mcp)


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


class TestPdfOcrConfigDefaults:
    """EnvVars.PRIORIS_MCP_PDF_OCR_* defaults and overrides."""

    def test_ocr_enabled_defaults_to_true(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_PDF_OCR_ENABLED", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_ENABLED is True

    def test_ocr_enabled_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_PDF_OCR_ENABLED", "false")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_ENABLED is False

    def test_tessdata_path_defaults_to_unset(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_PDF_OCR_TESSDATA_PATH", raising=False)
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_TESSDATA_PATH is None

    def test_tessdata_path_falls_back_to_tessdata_prefix(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_PDF_OCR_TESSDATA_PATH", raising=False)
        monkeypatch.setenv("TESSDATA_PREFIX", "/usr/share/tessdata")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_TESSDATA_PATH == "/usr/share/tessdata"

    def test_tessdata_path_explicit_override_wins_over_tessdata_prefix(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_PDF_OCR_TESSDATA_PATH", "/opt/custom-tessdata")
        monkeypatch.setenv("TESSDATA_PREFIX", "/usr/share/tessdata")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_TESSDATA_PATH == "/opt/custom-tessdata"

    def test_ocr_server_url_defaults_to_unset(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_PDF_OCR_SERVER_URL", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_URL is None

    def test_ocr_server_url_override_wins(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_PDF_OCR_SERVER_URL", "https://ocr.example.internal")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_URL == "https://ocr.example.internal"

    def test_ocr_server_headers_defaults_to_empty_dict(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_PDF_OCR_SERVER_HEADERS", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_HEADERS == {}

    def test_ocr_server_headers_parses_json_with_embedded_commas(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv(
            "PRIORIS_MCP_PDF_OCR_SERVER_HEADERS",
            '{"Authorization": "Bearer secret,with,commas"}',
        )
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_HEADERS == {"Authorization": "Bearer secret,with,commas"}

    def test_ocr_server_headers_rejects_non_object_json(self, monkeypatch: "pytest.MonkeyPatch"):
        from environs import EnvValidationError

        monkeypatch.setenv("PRIORIS_MCP_PDF_OCR_SERVER_HEADERS", "[1, 2, 3]")
        with pytest.raises(EnvValidationError):
            importlib.reload(prioris_mcp)

    def test_ocr_server_headers_rejects_non_string_values(self, monkeypatch: "pytest.MonkeyPatch"):
        from environs import EnvValidationError

        monkeypatch.setenv("PRIORIS_MCP_PDF_OCR_SERVER_HEADERS", '{"X-Retries": 3}')
        with pytest.raises(EnvValidationError):
            importlib.reload(prioris_mcp)


class TestVectorSearchEnvVarsDefaults:
    """EnvVars.PRIORIS_MCP_EMBEDDING_MODEL, PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT, and PRIORIS_MCP_VECTOR_DIR defaults and overrides."""

    def test_embedding_model_defaults_to_bge_small(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_EMBEDDING_MODEL", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"

    def test_embedding_model_reads_from_env(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setenv("PRIORIS_MCP_EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_EMBEDDING_MODEL == "intfloat/multilingual-e5-large"

    def test_vector_search_default_limit_defaults_to_10(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT == 10

    def test_vector_dir_defaults_next_to_storage_dir(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.delenv("PRIORIS_MCP_VECTOR_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        reloaded = importlib.reload(prioris_mcp)
        assert reloaded.EnvVars.PRIORIS_MCP_VECTOR_DIR.name == "vectors"
