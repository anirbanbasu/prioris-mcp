"""PDF-to-Markdown backend using liteparse (Rust core via PyO3, Apache-2.0).

See docs/requirement-specification/01-architecture.md#parse_full_text and
docs/requirement-specification/04-non-functional-requirements.md#dependency-selection for why
liteparse was chosen: structure-aware PDF parsing, a fast native (Rust) backend, and a
permissive licence.
"""

import logging

import anyio
from anyio import to_thread
from liteparse import LiteParse

from prioris_mcp.parsers.base import ParseError, ParserBackend

logger = logging.getLogger(__name__)

# Bounded per
# docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text -
# a pathological PDF must fail cleanly within a bound rather than hang the calling tool call
# indefinitely. liteparse's `parse()` is synchronous, so it runs in a worker thread; a truly
# pathological document can still occupy that thread past the timeout (Python cannot force-cancel
# a synchronous thread), but the async caller is never blocked past this bound.
PDF_PARSE_TIMEOUT_SECONDS = 60.0


class LiteParsePdfBackend(ParserBackend):
    """Converts PDF bytes to Markdown via liteparse's structure-aware extraction."""

    async def to_markdown(self, content: bytes) -> str:
        def _parse() -> str:
            parser = LiteParse(output_format="markdown")
            result = parser.parse(content)
            return result.text

        try:
            with anyio.fail_after(PDF_PARSE_TIMEOUT_SECONDS):
                # abandon_on_cancel=True is required for fail_after's deadline to have any effect
                # here - without it, anyio.to_thread.run_sync ignores cancellation until the
                # thread finishes, silently defeating the timeout bound entirely.
                return await to_thread.run_sync(_parse, abandon_on_cancel=True)
        except TimeoutError as exc:
            raise ParseError(f"PDF parse exceeded {PDF_PARSE_TIMEOUT_SECONDS}s bound") from exc
        except Exception as exc:
            raise ParseError(f"liteparse failed to parse PDF: {exc}") from exc
