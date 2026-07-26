"""HTML-to-Markdown backend using markdownify (MIT).

Reused for JATS XML too: the Europe PMC provider (a follow-up plan) transforms JATS XML to HTML
via an XSLT stylesheet first, then calls this same backend - see
docs/requirement-specification/01-architecture.md#parse_full_text - so there is exactly one
HTML-to-Markdown implementation, not two.
"""

import functools
import logging

import anyio
from anyio import to_thread
from markdownify import ATX, markdownify as _markdownify

from prioris_mcp.parsers.base import ParseError, ParserBackend

logger = logging.getLogger(__name__)

# Bound to ATX (`# Heading`) style rather than markdownify's default underlined/Setext style
# (`Heading\n=======`), which is the more broadly-compatible Markdown heading form. Kept as a
# module-level name (rather than inlined into the `to_markdown` call below) so tests can
# monkeypatch `html_markdownify.markdownify` wholesale with a single-argument replacement.
markdownify = functools.partial(_markdownify, heading_style=ATX)

# Bounded for the same reason as PDF_PARSE_TIMEOUT_SECONDS in pdf_liteparse.py.
HTML_PARSE_TIMEOUT_SECONDS = 30.0


class MarkdownifyHtmlBackend(ParserBackend):
    """Converts HTML bytes to Markdown via markdownify."""

    async def to_markdown(self, content: bytes) -> str:
        def _parse() -> str:
            return markdownify(content.decode("utf-8", errors="replace"))

        try:
            with anyio.fail_after(HTML_PARSE_TIMEOUT_SECONDS):
                # abandon_on_cancel=True is required for fail_after's deadline to have any effect
                # here - without it, anyio.to_thread.run_sync ignores cancellation until the
                # thread finishes, silently defeating the timeout bound entirely.
                return await to_thread.run_sync(_parse, abandon_on_cancel=True)
        except TimeoutError as exc:
            raise ParseError(f"HTML parse exceeded {HTML_PARSE_TIMEOUT_SECONDS}s bound") from exc
        except Exception as exc:
            raise ParseError(f"markdownify failed to parse HTML: {exc}") from exc
