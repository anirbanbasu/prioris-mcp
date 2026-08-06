"""PDF-to-Markdown backend using liteparse (Rust core via PyO3, Apache-2.0).

See docs/requirement-specification/01-architecture.md#parse_full_text and
docs/requirement-specification/04-non-functional-requirements.md#dependency-selection for why
liteparse was chosen.
"""

import logging

import anyio
from anyio import to_thread
from liteparse import LiteParse

from prioris_mcp import EnvVars
from prioris_mcp.parsers.base import ParseError, ParserBackend

logger = logging.getLogger(__name__)

PDF_PARSE_TIMEOUT_SECONDS = 60.0

# An ordinary block-separating joiner between pages - not a page-separator marker inserted then
# parsed back out, which would be fragile if a page's own content ever contained one.
_PAGE_JOINER = "\n\n"


class LiteParsePdfBackend(ParserBackend):
    """Converts PDF bytes to Markdown via liteparse's structure-aware, per-page extraction."""

    async def to_markdown(self, content: bytes) -> dict:
        def _parse() -> dict:
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
                return await to_thread.run_sync(_parse, abandon_on_cancel=True)
        except TimeoutError as exc:
            raise ParseError(f"PDF parse exceeded {PDF_PARSE_TIMEOUT_SECONDS}s bound") from exc
        except Exception as exc:
            raise ParseError(f"liteparse failed to parse PDF: {exc}") from exc
