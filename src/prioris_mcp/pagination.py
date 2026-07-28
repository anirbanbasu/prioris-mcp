"""Shared offset/limit slicing for text returned inline in a tool response or resource read.

See docs/requirement-specification/04-non-functional-requirements.md - `parse_full_text` and the
markdown resource both return one bounded page of text instead of the whole string, since an MCP
client's own max-tokens-per-result ceiling can be smaller than a parsed PDF/HTML source.
"""

from prioris_mcp.errors import InvalidRequestError


def paginate_text(text: str, offset: int, limit: int) -> dict:
    """Slice one page of `text`, up to `limit` characters starting at `offset`.

    Raises:
        InvalidRequestError: `offset` is negative, or `limit` is not positive.
    """
    if offset < 0:
        raise InvalidRequestError(f"offset must be >= 0, got {offset}")
    if limit <= 0:
        raise InvalidRequestError(f"limit must be > 0, got {limit}")
    total_length = len(text)
    content = text[offset : offset + limit]
    return {
        "content": content,
        "offset": offset,
        "limit": limit,
        "total_length": total_length,
        "has_more": offset + len(content) < total_length,
    }
