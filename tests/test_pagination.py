import pytest

from prioris_mcp.errors import InvalidRequestError
from prioris_mcp.pagination import paginate_text


class TestPaginateText:
    """Tests for `paginate_text`."""

    def test_returns_whole_text_when_shorter_than_limit(self):
        page = paginate_text("hello world", offset=0, limit=100)
        assert page == {
            "content": "hello world",
            "offset": 0,
            "limit": 100,
            "total_length": 11,
            "has_more": False,
        }

    def test_truncates_and_flags_has_more_when_longer_than_limit(self):
        page = paginate_text("hello world", offset=0, limit=5)
        assert page == {
            "content": "hello",
            "offset": 0,
            "limit": 5,
            "total_length": 11,
            "has_more": True,
        }

    def test_offset_starts_mid_string(self):
        page = paginate_text("hello world", offset=6, limit=100)
        assert page["content"] == "world"
        assert page["has_more"] is False

    def test_offset_at_exact_end_of_final_page_has_no_more(self):
        page = paginate_text("hello world", offset=6, limit=5)
        assert page["content"] == "world"
        assert page["has_more"] is False

    def test_offset_beyond_text_length_returns_empty_content(self):
        page = paginate_text("hello world", offset=100, limit=5)
        assert page == {
            "content": "",
            "offset": 100,
            "limit": 5,
            "total_length": 11,
            "has_more": False,
        }

    def test_negative_offset_raises_invalid_request(self):
        with pytest.raises(InvalidRequestError):
            paginate_text("hello world", offset=-1, limit=5)

    def test_zero_limit_raises_invalid_request(self):
        with pytest.raises(InvalidRequestError):
            paginate_text("hello world", offset=0, limit=0)

    def test_negative_limit_raises_invalid_request(self):
        with pytest.raises(InvalidRequestError):
            paginate_text("hello world", offset=0, limit=-5)
