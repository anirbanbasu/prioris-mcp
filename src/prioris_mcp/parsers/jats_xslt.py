# src/prioris_mcp/parsers/jats_xslt.py
"""JATS XML -> Markdown backend, via a vendored XSLT stylesheet and an injected HTML backend.

Converts JATS XML to HTML via the vendored XSLT stylesheet, then delegates HTML -> Markdown to
an injected `ParserBackend`.

See docs/requirement-specification/01-architecture.md#parse_full_text - Europe PMC's JATS XML is
converted to HTML first, reusing the same HTML-to-Markdown backend built for arXiv, rather than
maintaining a second bespoke JATS-to-Markdown converter.

The stylesheet (`data/jats-html.xsl`) is vendored from
https://github.com/ncbi/JATSPreviewStylesheets (public domain, US government work - see that
repo's README) - a single, self-contained XSLT 1.0 file with no xsl:import/xsl:include chain,
fully supported by lxml/libxslt (no Saxon/XSLT-2.0 dependency needed).
"""

import logging
import threading
from pathlib import Path

import anyio
from anyio import to_thread

# unresolved-import: lxml ships no inline type stubs (no py.typed marker), and this repo does not
# carry a separate lxml-stubs dependency; ty cannot resolve `lxml.etree`'s members as a result.
from lxml import etree  # ty: ignore[unresolved-import]

from prioris_mcp import EnvVars
from prioris_mcp.parsers.base import ParseError, ParserBackend

logger = logging.getLogger(__name__)

# Bounded per
# docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text -
# a pathological JATS document must fail cleanly within a bound rather than hang the calling tool
# call indefinitely.
JATS_PARSE_TIMEOUT_SECONDS = 60.0

_STYLESHEET_PATH = Path(__file__).parent / "data" / "jats-html.xsl"

# Bounds concurrently-*executing* transforms (not concurrently-awaited ones - see
# docs/requirement-specification/05-security.md#a-bounded-per-call-failure-is-not-sufficient-on-its-own).
# A plain threading.Semaphore, acquired/released around the transform itself rather than passed as
# an anyio CapacityLimiter, is required here: anyio releases a CapacityLimiter as soon as the
# *caller* abandons the call, not when the worker thread actually finishes, which would let
# abandoned-but-still-running transforms escape the cap entirely.
_TRANSFORM_SEMAPHORE = threading.Semaphore(EnvVars.PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS)


class JatsXsltMarkdownBackend(ParserBackend):
    """Converts JATS XML bytes to Markdown via an XSLT-to-HTML transform, then `html_backend`."""

    def __init__(self, html_backend: ParserBackend) -> None:
        self._html_backend = html_backend
        self._transform: etree.XSLT | None = None

    def _get_transform(self) -> etree.XSLT:
        if self._transform is None:
            xslt_root = etree.parse(str(_STYLESHEET_PATH))
            self._transform = etree.XSLT(xslt_root)
        return self._transform

    async def to_markdown(self, content: bytes) -> str:
        def _transform_to_html() -> bytes:
            # Held for the transform's true lifetime (released only once this function returns or
            # raises), not tied to the calling task's own cancellation - see
            # docs/requirement-specification/05-security.md#a-bounded-per-call-failure-is-not-sufficient-on-its-own.
            # A caller beyond the cap blocks here (cheap - no CPU use while waiting), and is still
            # subject to the fail_after bound below if a slot never frees in time.
            with _TRANSFORM_SEMAPHORE:
                # This parses untrusted external content (see
                # docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text):
                # resolve_entities=False stops custom DTD-declared entities (e.g. "billion laughs") from
                # being expanded inline (paired with the explicit rejection below), no_network=True blocks
                # fetching an external DTD/entity over the network, and huge_tree=False (lxml's own
                # default) keeps libxml2's structural safety limits - including its own entity-amplification
                # cap, which independently bounds entities used within attribute values - on.
                parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
                source_doc = etree.fromstring(content, parser=parser)
                # resolve_entities=False stops libxml2 from *expanding* custom DTD-declared
                # entities (so a "billion laughs" document never balloons in memory), but it does
                # not raise on its own - the entity reference is simply left unresolved as an
                # `etree._Entity` node in the tree. Treat any such node as a rejected document:
                # legitimate JATS content does not need custom internal/external general entities,
                # and silently dropping the reference would hide content rather than surface it.
                unresolved_entity_names = [node.name for node in source_doc.iter() if isinstance(node, etree._Entity)]
                if unresolved_entity_names:
                    raise ValueError(
                        "JATS XML contains unresolved custom entity reference(s): "
                        f"{', '.join(f'&{name};' for name in unresolved_entity_names)}; entity expansion is "
                        "disabled to prevent resource-exhaustion attacks, so documents relying on custom "
                        "entities are rejected"
                    )
                result_tree = self._get_transform()(source_doc)
                return etree.tostring(result_tree, encoding="utf-8")

        try:
            with anyio.fail_after(JATS_PARSE_TIMEOUT_SECONDS):
                # abandon_on_cancel=True: anyio's default (False) defers cancellation until the
                # worker thread finishes on its own, so a CPU-bound pathological transform would
                # run past fail_after's deadline instead of surfacing ParseError at the bound. The
                # abandoned thread keeps running in the background after this call returns;
                # _TRANSFORM_SEMAPHORE bounds how many such threads can ever be doing real
                # transform work concurrently, regardless of how many calls have been abandoned.
                html_bytes = await to_thread.run_sync(_transform_to_html, abandon_on_cancel=True)
        except TimeoutError as exc:
            raise ParseError(f"JATS XSLT transform exceeded {JATS_PARSE_TIMEOUT_SECONDS}s bound") from exc
        except etree.XMLSyntaxError as exc:
            raise ParseError(f"Malformed JATS XML: {exc}") from exc
        except Exception as exc:
            raise ParseError(f"JATS XSLT transform failed: {exc}") from exc

        return await self._html_backend.to_markdown(html_bytes)
