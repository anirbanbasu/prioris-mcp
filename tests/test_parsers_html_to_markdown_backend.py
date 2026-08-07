import asyncio

import pytest

from prioris_mcp.parsers.base import ParseError
from prioris_mcp.parsers.html_to_markdown_backend import HtmlToMarkdownBackend, convert


class TestHtmlToMarkdownBackend:
    """Tests for `HtmlToMarkdownBackend.to_markdown`."""

    def test_converts_simple_html_to_markdown(self):
        html = b"<h1>Title</h1><p>Hello <strong>world</strong>.</p>"

        async def scenario():
            backend = HtmlToMarkdownBackend()
            return await backend.to_markdown(html)

        result = asyncio.run(scenario())
        assert "# Title" in result["markdown"]
        assert "Hello" in result["markdown"]
        assert "**world**" in result["markdown"]

    def test_slow_parse_raises_parse_error_within_bound(self, monkeypatch: pytest.MonkeyPatch):
        import time

        from prioris_mcp.parsers import html_to_markdown_backend

        monkeypatch.setattr(html_to_markdown_backend, "HTML_PARSE_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(
            html_to_markdown_backend,
            "convert",
            lambda html: (time.sleep(0.3), "never reached")[1],
        )

        async def scenario():
            backend = HtmlToMarkdownBackend()
            await backend.to_markdown(b"<p>slow</p>")

        with pytest.raises(ParseError):
            asyncio.run(scenario())

    def test_decode_error_becomes_parse_error(self, monkeypatch: pytest.MonkeyPatch):
        from prioris_mcp.parsers import html_to_markdown_backend

        def _raise(_html: str) -> str:
            raise RuntimeError("malformed markup")

        monkeypatch.setattr(html_to_markdown_backend, "convert", _raise)

        async def scenario():
            backend = HtmlToMarkdownBackend()
            await backend.to_markdown(b"<p>bad</p>")

        with pytest.raises(ParseError):
            asyncio.run(scenario())


class TestToMarkdownReturnsLeafSpans:
    """Tests for leaf_spans in HtmlToMarkdownBackend.to_markdown result."""

    def test_returns_single_trivial_span_covering_whole_blob(self):
        async def scenario():
            backend = HtmlToMarkdownBackend()
            result = await backend.to_markdown(b"<h1>Title</h1><p>Body text.</p>")
            assert result["markdown"] == convert("<h1>Title</h1><p>Body text.</p>")
            assert result["leaf_spans"] == [{"start": 0, "length": len(result["markdown"])}]

        asyncio.run(scenario())
