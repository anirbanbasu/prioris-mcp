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

from prioris_mcp import EnvVars
from prioris_mcp.parsers.base import ParseError, ParserBackend

logger = logging.getLogger(__name__)

# Bounded per
# docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text -
# a pathological PDF must fail cleanly within a bound rather than hang the calling tool call
# indefinitely. liteparse's `parse()` is synchronous, so it runs in a worker thread; a truly
# pathological document can still occupy that thread past the timeout (Python cannot force-cancel
# a synchronous thread), but the async caller is never blocked past this bound.
PDF_PARSE_TIMEOUT_SECONDS = 60.0

# An ordinary block-separating joiner between pages - not a page-separator marker inserted then
# parsed back out, which would be fragile if a page's own content ever contained one.
_PAGE_JOINER = "\n\n"


class LiteParsePdfBackend(ParserBackend):
    """Converts PDF bytes to Markdown via liteparse's structure-aware, per-page extraction."""

    async def to_markdown(self, content: bytes) -> dict:
        def _parse() -> dict:
            # Explicit, airgapped-friendly OCR configuration - liteparse's own defaults
            # (ocr_enabled=True, no tessdata_path/ocr_server_url) fall back to lazily downloading
            # Tesseract language data over the network on first use, per
            # docs/requirement-specification/05-security.md#ocr-language-data-is-a-network-dependency-of-parse_full_text.
            # Copied rather than passed by reference - EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_HEADERS is a
            # shared class-level dict, and liteparse mutating it in place would corrupt config for
            # every subsequent parse call.
            headers = dict(EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_HEADERS)
            parser = LiteParse(
                output_format="markdown",
                ocr_enabled=EnvVars.PRIORIS_MCP_PDF_OCR_ENABLED,
                tessdata_path=EnvVars.PRIORIS_MCP_PDF_OCR_TESSDATA_PATH,
                ocr_server_url=EnvVars.PRIORIS_MCP_PDF_OCR_SERVER_URL,
                ocr_server_headers=headers or None,
            )
            result = parser.parse(content)
            markdown_parts: list[str] = []
            leaf_spans: list[dict] = []
            offset = 0
            for index, page in enumerate(result.pages):
                if index > 0:
                    markdown_parts.append(_PAGE_JOINER)
                    offset += len(_PAGE_JOINER)
                page_markdown = page.markdown
                markdown_parts.append(page_markdown)
                leaf_spans.append({"start": offset, "length": len(page_markdown)})
                offset += len(page_markdown)
            return {"markdown": "".join(markdown_parts), "leaf_spans": leaf_spans}

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
