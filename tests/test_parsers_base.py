import pytest

from prioris_mcp.parsers.base import ParseError, ParserBackend


class TestParserBackend:
    """`ParserBackend` is an ABC, and `ParseError` is a plain exception."""

    def test_cannot_instantiate_without_implementing_to_markdown(self):
        with pytest.raises(TypeError):
            ParserBackend()  # type: ignore[abstract]

    def test_parse_error_is_a_plain_exception(self):
        assert isinstance(ParseError("bad document"), Exception)
