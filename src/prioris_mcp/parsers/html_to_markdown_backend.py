"""HTML-to-Markdown backend using html-to-markdown (MIT).

Swapped in from markdownify for correct `rowspan`/`colspan` table handling - see
docs/requirement-specification/01-architecture.md#parse_full_text.

Reused for JATS XML too: the Europe PMC provider transforms JATS XML to HTML via an XSLT
stylesheet first, then calls this same backend - see
docs/requirement-specification/01-architecture.md#parse_full_text - so there is exactly one
HTML-to-Markdown implementation, not two.
"""

import logging

import anyio
from anyio import to_thread
from html_to_markdown import ConversionOptions, convert as _convert

from prioris_mcp.parsers.base import ParseError, ParserBackend

logger = logging.getLogger(__name__)

_OPTIONS = ConversionOptions(heading_style="atx")


def convert(html: str) -> str:
    """Convert HTML to Markdown using ATX (`# Heading`) heading style.

    Kept as a module-level name (rather than inlined into the `to_markdown` call below) so tests
    can monkeypatch `html_to_markdown_backend.convert` wholesale with a single-argument replacement.
    """
    text = _convert(html, options=_OPTIONS).content
    if text is None:  # pragma: no cover
        # `content` is typed `str | None` for output formats other than markdown; unreachable
        # here since `_OPTIONS` never sets `output_format` away from its "markdown" default.
        raise ParseError("html-to-markdown returned no content")
    return text


# Bounded for the same reason as PDF_PARSE_TIMEOUT_SECONDS in pdf_liteparse.py.
HTML_PARSE_TIMEOUT_SECONDS = 30.0


class HtmlToMarkdownBackend(ParserBackend):
    """Converts HTML bytes to Markdown via html-to-markdown."""

    async def to_markdown(self, content: bytes) -> dict:
        def _parse() -> str:
            return convert(content.decode("utf-8", errors="replace"))

        try:
            with anyio.fail_after(HTML_PARSE_TIMEOUT_SECONDS):
                # abandon_on_cancel=True is required for fail_after's deadline to have any effect
                # here - without it, anyio.to_thread.run_sync ignores cancellation until the
                # thread finishes, silently defeating the timeout bound entirely.
                text = await to_thread.run_sync(_parse, abandon_on_cancel=True)
        except TimeoutError as exc:
            raise ParseError(f"HTML parse exceeded {HTML_PARSE_TIMEOUT_SECONDS}s bound") from exc
        except Exception as exc:
            raise ParseError(f"html-to-markdown failed to parse HTML: {exc}") from exc
        return {"markdown": text, "leaf_spans": [{"start": 0, "length": len(text)}]}
