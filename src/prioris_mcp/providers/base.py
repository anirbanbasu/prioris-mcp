"""Shared interface every research-publication provider implements.

See docs/requirement-specification/01-architecture.md#researchpublicationprovider. Adding a
source means implementing this interface, not changing it.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from prioris_mcp.errors import InvalidRequestError, NotFoundError
from prioris_mcp.pagination import paginate_text
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.storage.backend import StorageBackend
from prioris_mcp.storage.chunking import detect_chunks
from prioris_mcp.storage.search_index import SearchIndex


class CapabilityNotSupportedError(Exception):
    """Raised by a capability's default implementation for a provider with no equivalent.

    See docs/requirement-specification/index.md#out-of-scope-for-v1 - Europe PMC (no `list_top_n`
    equivalent) and the local filesystem source (no `search`/`fetch_metadata`/`resolve_identifier`
    at all - see
    docs/requirement-specification/01-architecture.md#local-filesystem-source) are the v1
    examples: a provider that can't sensibly support a capability says so via this default,
    rather than faking it.
    """


class ResearchPublicationProvider(ABC):
    """One grouping-level capability set: search, fetch metadata/full text, parse, resolve.

    Only `fetch_full_text` and `parse_full_text` are required of every subclass. The other three
    capabilities default to raising `CapabilityNotSupportedError` (mirroring `list_top_n`) rather
    than being abstract, so a source implementing a genuine subset - the local filesystem source,
    which has no query interface, no structured metadata authority, and no identifier scheme to
    route (see docs/requirement-specification/01-architecture.md#local-filesystem-source) - can
    subclass this ABC without faking capabilities it doesn't have.

    `resolve_identifier` is a provider-native capability, not itself an MCP tool - see
    docs/requirement-specification/01-architecture.md#resolve_identifier. It is declared here
    (rather than left ad hoc per subclass) so grouping-level DOI routing can call it uniformly
    across every provider that supports it.
    """

    async def search(self, query: str, **kwargs: Any) -> BaseModel:
        """Search items by keyword/query. See Functional requirements for the per-provider shape.

        Raises:
            CapabilityNotSupportedError: for a provider with no query interface (e.g. the local
                filesystem source).
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support search")

    async def list_top_n(
        self, include_categories: list[str], n: int, exclude_categories: list[str] | None = None
    ) -> BaseModel:
        """List the top-N items across one or more provider-defined categories.

        Raises:
            CapabilityNotSupportedError: for a provider with no equivalent capability.
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support list_top_n")

    async def fetch_metadata(self, identifiers: list[str]) -> BaseModel:
        """Fetch metadata for one or more identifiers in a single call.

        Raises:
            CapabilityNotSupportedError: for a provider with no structured metadata authority
                (e.g. the local filesystem source).
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support fetch_metadata")

    async def resolve_identifier(self, identifier: str, format: str) -> BaseModel:
        """Resolve `identifier` to a fetchable URL/canonical form in the target `format`.

        Raises:
            CapabilityNotSupportedError: for a provider with no identifier scheme to resolve
                (e.g. the local filesystem source).
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support resolve_identifier")

    @abstractmethod
    async def fetch_full_text(self, identifier: str, format: str) -> BaseModel:
        """Fetch (or return already-persisted) full text for `identifier`/`format`."""

    @abstractmethod
    async def parse_full_text(
        self, identifier: str, format: str, offset: int = 0, limit: int | None = None
    ) -> BaseModel:
        """Convert already-persisted full text for `identifier`/`format` into one page of Markdown."""


async def persist_parsed_markdown(
    *,
    storage: StorageBackend,
    search_index: SearchIndex,
    provider: str,
    canonical_identifier: str,
    external_identifier: str,
    source_format: str,
    backend: ParserBackend,
    offset: int,
    limit: int,
    page: int | None,
    page_aware: bool,
) -> dict:
    """Parse (on cache miss), persist markdown + manifest structure, sync search, paginate.

    Shared by every ResearchPublicationProvider's parse_full_text - see
    docs/requirement-specification/01-architecture.md#parse_full_text. On a fresh parse
    (`served_from_storage=False`), also populates this document's leaf/chunk manifest rows and
    the global search index - a cache hit normally skips that, since it's already correct from
    the original parse, except for a migrated document whose manifest was never populated (see
    `storage/migration.py`), which gets its manifest/search rows rebuilt here on first access.

    Raises:
        InvalidRequestError: `page` was given but `page_aware` is False.
        NotFoundError: the source `document` artefact hasn't been fetched, or `page` doesn't
            exist in this document's manifest.
    """
    if page is not None and not page_aware:
        raise InvalidRequestError(f"page is not supported for format {source_format!r}")

    leaf_spans_holder: list[dict] = []

    async def factory() -> bytes:
        if not await storage.exists(provider, canonical_identifier, source_format, artefact="document"):
            raise NotFoundError(f"{provider}:{canonical_identifier}:{source_format} has not been fetched")
        source_content = await storage.read(provider, canonical_identifier, source_format, artefact="document")
        parsed = await backend.to_markdown(source_content)
        leaf_spans_holder.extend(parsed["leaf_spans"])
        return parsed["markdown"].encode("utf-8")

    markdown_bytes, served_from_storage = await storage.get_or_create(
        provider,
        canonical_identifier,
        source_format,
        factory,
        artefact="markdown",
        public_identifier=external_identifier,
    )
    markdown = markdown_bytes.decode("utf-8")
    manifest = storage.manifest_for(provider, canonical_identifier)

    needs_manifest_rebuild = served_from_storage and await manifest.total_pages(source_format) == 0
    if not served_from_storage or needs_manifest_rebuild:
        if needs_manifest_rebuild:
            if not await storage.exists(provider, canonical_identifier, source_format, artefact="document"):
                raise NotFoundError(
                    f"cannot rebuild manifest for {provider}:{canonical_identifier}:{source_format}: "
                    "source document is no longer available"
                )
            source_content = await storage.read(provider, canonical_identifier, source_format, artefact="document")
            parsed = await backend.to_markdown(source_content)
            leaf_spans_holder.extend(parsed["leaf_spans"])
        await manifest.replace_leaf_rows(source_format, leaf_spans_holder)
        chunks = detect_chunks(markdown)
        await manifest.replace_chunk_rows(source_format, chunks, scheme="heading-bounded-v1")
        search_rows = await manifest.rows_for_search(source_format)
        entries = [
            {
                "key": row["key"],
                "start": row["start"],
                "length": row["length"],
                "text": markdown[row["start"] : row["start"] + row["length"]],
            }
            for row in search_rows
        ]
        await search_index.index_entries(provider, external_identifier, source_format, entries)

    base_offset = offset
    if page is not None:
        leaf = await manifest.leaf_for_page(source_format, page)
        if leaf is None:
            raise NotFoundError(f"page {page} does not exist for {provider}:{canonical_identifier}:{source_format}")
        base_offset = leaf["start"] + offset

    page_result = paginate_text(markdown, base_offset, limit)

    total_pages = None
    page_range = None
    if page_aware:
        total_pages = await manifest.total_pages(source_format)
        page_range = await manifest.page_range_for_span(
            source_format, page_result["offset"], len(page_result["content"])
        )

    return {
        "markdown": page_result["content"],
        "offset": page_result["offset"],
        "limit": page_result["limit"],
        "total_length": page_result["total_length"],
        "has_more": page_result["has_more"],
        "total_pages": total_pages,
        "page_range": page_range,
    }
