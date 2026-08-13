"""Split an oversized heading-bounded chunk into embedding-sized windows, tagged with the parent chunk_id.

See docs/requirement-specification/search/02-vector-search.md#chunking-granularity-heading-bounded-split-only-when-oversized.
An overflow valve, not the primary chunking strategy - most chunks pass through as one window.
"""


def split_oversized_chunk(chunk_id: str, text: str, *, max_chars: int) -> list[dict]:
    """Split `text` into `max_chars`-bounded windows, each `{"chunk_id", "text", "sub_index"}`.

    A single window (sub_index 0) when `text` already fits - the common case.
    """
    if len(text) <= max_chars:
        return [{"chunk_id": chunk_id, "text": text, "sub_index": 0}]
    return [
        {"chunk_id": chunk_id, "text": text[start : start + max_chars], "sub_index": sub_index}
        for sub_index, start in enumerate(range(0, len(text), max_chars))
    ]
