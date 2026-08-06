import asyncio

import pytest

from prioris_mcp.parsers.base import ParseError, ParserBackend


class TestParserBackend:
    """`ParserBackend` is an ABC, and `ParseError` is a plain exception."""

    def test_cannot_instantiate_without_implementing_to_markdown(self):
        with pytest.raises(TypeError):
            ParserBackend()  # type: ignore[abstract]

    def test_parse_error_is_a_plain_exception(self):
        assert isinstance(ParseError("bad document"), Exception)


class _ConcreteBackend(ParserBackend):
    async def to_markdown(self, content: bytes) -> dict:
        text = content.decode("utf-8")
        return {"markdown": text, "leaf_spans": [{"start": 0, "length": len(text)}]}


class TestParserBackendReturnShape:
    """Test that ParserBackend.to_markdown returns the expected dict structure."""

    def test_to_markdown_returns_dict_with_markdown_and_leaf_spans(self):
        async def scenario():
            backend = _ConcreteBackend()
            result = await backend.to_markdown(b"hello")
            return result

        result = asyncio.run(scenario())
        assert result == {"markdown": "hello", "leaf_spans": [{"start": 0, "length": 5}]}
