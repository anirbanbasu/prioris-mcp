"""Pluggable parser-backend interface: converts persisted full text into Markdown + leaf spans.

See docs/requirement-specification/01-architecture.md#parse_full_text - one backend per source
format, swappable by configuration without changing this interface, mirroring
`StorageBackend`'s interface-plus-swappable-implementation shape.
"""

from abc import ABC, abstractmethod


class ParseError(Exception):
    """A source document could not be converted to Markdown.

    Covers both malformed input and a backend-enforced resource bound being exceeded - see
    docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text.
    Callers treat both the same way: a bounded, typed failure, not a crash or a hang.
    """


class ParserBackend(ABC):
    """Converts one source format's raw bytes into a Markdown string plus leaf-span structure."""

    @abstractmethod
    async def to_markdown(self, content: bytes) -> dict:
        """Convert `content` to Markdown, with leaf-level (page-span) structure.

        Returns:
            {"markdown": str, "leaf_spans": list[{"start": int, "length": int}]} - leaf_spans is
            one entry per PDF page (parser-determined), or a single trivial span covering the
            whole blob for whole-document formats with no native page concept (HTML/JATS).

        Raises:
            ParseError: the content is malformed, or parsing exceeded this backend's own
                resource bound (size/time).
        """
