import asyncio
from unittest.mock import MagicMock, patch

import pytest

from prioris_mcp.parsers.base import ParseError
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend


class TestLiteParsePdfBackend:
    """Tests for `LiteParsePdfBackend.to_markdown`."""

    def test_returns_markdown_from_liteparse_result(self):
        mock_result = MagicMock(text="# Title\n\nBody text.")
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.return_value = mock_result

            async def scenario():
                backend = LiteParsePdfBackend()
                return await backend.to_markdown(b"%PDF-1.4 fake bytes")

            result = asyncio.run(scenario())
        assert result == "# Title\n\nBody text."
        mock_cls.assert_called_once_with(
            output_format="markdown",
            ocr_enabled=True,
            tessdata_path=None,
            ocr_server_url=None,
            ocr_server_headers=None,
        )

    def test_passes_ocr_config_from_env_vars(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("prioris_mcp.parsers.pdf_liteparse.EnvVars.PRIORIS_MCP_PDF_OCR_ENABLED", False)
        monkeypatch.setattr(
            "prioris_mcp.parsers.pdf_liteparse.EnvVars.PRIORIS_MCP_PDF_OCR_TESSDATA_PATH", "/opt/tessdata"
        )
        monkeypatch.setattr(
            "prioris_mcp.parsers.pdf_liteparse.EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_URL", "https://ocr.example.internal"
        )
        monkeypatch.setattr(
            "prioris_mcp.parsers.pdf_liteparse.EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_HEADERS",
            {"Authorization": "Bearer secret,with,commas"},
        )
        mock_result = MagicMock(text="# Title")
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.return_value = mock_result

            async def scenario():
                backend = LiteParsePdfBackend()
                return await backend.to_markdown(b"%PDF-1.4 fake bytes")

            asyncio.run(scenario())
        mock_cls.assert_called_once_with(
            output_format="markdown",
            ocr_enabled=False,
            tessdata_path="/opt/tessdata",
            ocr_server_url="https://ocr.example.internal",
            ocr_server_headers={"Authorization": "Bearer secret,with,commas"},
        )

    def test_liteparse_exception_becomes_parse_error(self):
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.side_effect = RuntimeError("corrupt PDF")

            async def scenario():
                backend = LiteParsePdfBackend()
                await backend.to_markdown(b"not a real pdf")

            with pytest.raises(ParseError):
                asyncio.run(scenario())

    def test_slow_parse_raises_parse_error_within_bound(self, monkeypatch: pytest.MonkeyPatch):
        import time

        monkeypatch.setattr("prioris_mcp.parsers.pdf_liteparse.PDF_PARSE_TIMEOUT_SECONDS", 0.05)

        def _slow_parse(_content):
            time.sleep(0.3)
            return MagicMock(text="never reached")

        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.side_effect = _slow_parse

            async def scenario():
                backend = LiteParsePdfBackend()
                await backend.to_markdown(b"slow document")

            with pytest.raises(ParseError):
                asyncio.run(scenario())
