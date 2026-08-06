import asyncio
from unittest.mock import MagicMock, patch

import pytest

from prioris_mcp.parsers.base import ParseError
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend


def _mock_page(markdown: str) -> MagicMock:
    return MagicMock(markdown=markdown)


class TestLiteParsePdfBackend:
    """Tests for `LiteParsePdfBackend.to_markdown`."""

    def test_returns_markdown_from_liteparse_result(self):
        mock_result = MagicMock(pages=[_mock_page("# Title\n\nBody text.")])
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.return_value = mock_result

            async def scenario():
                backend = LiteParsePdfBackend()
                return await backend.to_markdown(b"%PDF-1.4 fake bytes")

            result = asyncio.run(scenario())
        assert result == {
            "markdown": "# Title\n\nBody text.",
            "leaf_spans": [{"start": 0, "length": len("# Title\n\nBody text.")}],
        }
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
        mock_result = MagicMock(pages=[_mock_page("# Title")])
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
            return MagicMock(pages=[])

        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.side_effect = _slow_parse

            async def scenario():
                backend = LiteParsePdfBackend()
                await backend.to_markdown(b"slow document")

            with pytest.raises(ParseError):
                asyncio.run(scenario())


class TestToMarkdownReturnsLeafSpans:
    """Tests for leaf_spans tracking in `LiteParsePdfBackend.to_markdown`."""

    def test_single_page_pdf_returns_one_leaf_span_covering_whole_blob(self):
        mock_result = MagicMock(pages=[_mock_page("# Title\n\nBody text.")])
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.return_value = mock_result

            async def scenario():
                backend = LiteParsePdfBackend()
                return await backend.to_markdown(b"%PDF-1.4 fake bytes")

            result = asyncio.run(scenario())
        assert result["markdown"] == "# Title\n\nBody text."
        assert result["leaf_spans"] == [{"start": 0, "length": len("# Title\n\nBody text.")}]

    def test_multi_page_pdf_joins_pages_and_tracks_offsets(self):
        mock_result = MagicMock(pages=[_mock_page("Page one."), _mock_page("Page two."), _mock_page("Page three.")])
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.return_value = mock_result

            async def scenario():
                backend = LiteParsePdfBackend()
                return await backend.to_markdown(b"%PDF-1.4 fake bytes")

            result = asyncio.run(scenario())
        expected_markdown = "Page one.\n\nPage two.\n\nPage three."
        assert result["markdown"] == expected_markdown
        assert len(result["leaf_spans"]) == 3
        for span in result["leaf_spans"]:
            page_text = expected_markdown[span["start"] : span["start"] + span["length"]]
            assert page_text in ("Page one.", "Page two.", "Page three.")
        # spans are contiguous modulo the "\n\n" separators, and recover each page's own text exactly
        assert (
            expected_markdown[
                result["leaf_spans"][0]["start"] : result["leaf_spans"][0]["start"] + result["leaf_spans"][0]["length"]
            ]
            == "Page one."
        )
        assert (
            expected_markdown[
                result["leaf_spans"][1]["start"] : result["leaf_spans"][1]["start"] + result["leaf_spans"][1]["length"]
            ]
            == "Page two."
        )

    def test_zero_page_pdf_returns_empty_markdown_and_no_spans(self):
        mock_result = MagicMock(pages=[])
        with patch("prioris_mcp.parsers.pdf_liteparse.LiteParse") as mock_cls:
            mock_cls.return_value.parse.return_value = mock_result

            async def scenario():
                backend = LiteParsePdfBackend()
                return await backend.to_markdown(b"%PDF-1.4 fake bytes")

            result = asyncio.run(scenario())
        assert result == {"markdown": "", "leaf_spans": []}
