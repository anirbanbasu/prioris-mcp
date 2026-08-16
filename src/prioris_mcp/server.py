import asyncio
import contextlib
import logging
import re
import sqlite3
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from importlib.metadata import version
from typing import Annotated, ClassVar, Literal, cast

import httpx
import uvicorn
from fastmcp import Context, FastMCP
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    GetPromptSettings,
    ListPromptsSettings,
    ListResourcesSettings,
    ListToolsSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)
from pydantic import Field
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from prioris_mcp import PACKAGE_NAME, EnvVars
from prioris_mcp.discovery.openalex import OPENALEX_MAX_RESULTS, OpenAlexClient
from prioris_mcp.errors import InvalidRequestError, NotFoundError
from prioris_mcp.middleware import (
    DecodeBinaryResourceContentMiddleware,
    EncodeBinaryResourceContentMiddleware,
    LiveResourceCacheBypassMiddleware,
    ResponseMetadataMiddleware,
    StripUnknownArgumentsMiddleware,
)
from prioris_mcp.mixin import MCPMixin
from prioris_mcp.models.arxiv import ArxivFetchMetadataResult, ArxivSearchResult
from prioris_mcp.models.common import (
    DeleteEntryRef,
    DeleteFetchedResult,
    FullTextFetchResult,
    ListFetchedResult,
    MarkdownPage,
    PagedSearchMatches,
    ParsedFullText,
    ResolvedIdentifierResult,
    SearchFetchedResult,
    SearchMatch,
    VectorRebuildMechanismStatus,
    VectorRebuildStatus,
)
from prioris_mcp.models.discovery import DiscoveryHit, DiscoveryResult
from prioris_mcp.models.europepmc import EuropePmcFetchMetadataResult, EuropePmcSearchResult
from prioris_mcp.models.localfile import LocalFileBeginUploadResult, LocalFileFetchResult, LocalFileUploadChunkResult
from prioris_mcp.models.notes import Anchor, AuthorFilter, Note, NotesSearchResult, PagedNotes
from prioris_mcp.models.vector import (
    NoteVectorSearchMatch,
    PagedNoteVectorMatches,
    PagedVectorSearchMatches,
    VectorSearchMatch,
)
from prioris_mcp.notes.backend import NotesBackend
from prioris_mcp.notes.search_index import NotesSearchIndex, SqliteFts5NotesSearchIndex
from prioris_mcp.notes.sqlite_backend import SqliteNotesBackend
from prioris_mcp.pagination import paginate_text
from prioris_mcp.parsers.html_to_markdown_backend import HtmlToMarkdownBackend
from prioris_mcp.parsers.jats_xslt import JatsXsltMarkdownBackend
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend
from prioris_mcp.providers.arxiv import ARXIV_BASE_SPACING_SECONDS, ArxivProvider
from prioris_mcp.providers.europepmc import EUROPEPMC_BASE_SPACING_SECONDS, EuropePmcProvider
from prioris_mcp.providers.grouping import DEFAULT_GROUPING, grouping_dir
from prioris_mcp.providers.identifier_routing import resolve_research_identifier
from prioris_mcp.providers.localfile import LocalFileProvider, UploadSessionManager
from prioris_mcp.rate_limit import ProviderRequestQueue
from prioris_mcp.storage import FilesystemStorageBackend
from prioris_mcp.storage.search_index import SqliteFts5SearchIndex
from prioris_mcp.vector.backend import IndexStatus
from prioris_mcp.vector.embedding import FastEmbedBackend
from prioris_mcp.vector.mechanism import (
    FtsMechanism,
    NotesFtsMechanism,
    NotesVectorMechanism,
    SearchMechanism,
    VectorMechanism,
)
from prioris_mcp.vector.rebuild_progress import VectorRebuildProgress
from prioris_mcp.vector.scheduler import EmbeddingScheduler
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecDocumentBackend, SqliteVecNoteBackend

package_version = version(PACKAGE_NAME)
logger = logging.getLogger(__name__)


class PriorisMCP(MCPMixin):
    """PriorisMCP: MCP tools/resources for looking up prior art."""

    tools: ClassVar[list[dict]] = [
        {"fn": "research_arxiv_search", "tags": ["research", "arxiv"], "annotations": {"readOnlyHint": True}},
        {"fn": "research_arxiv_list_top_n", "tags": ["research", "arxiv"], "annotations": {"readOnlyHint": True}},
        {
            "fn": "research_arxiv_fetch_metadata",
            "tags": ["research", "arxiv"],
            "annotations": {"readOnlyHint": True},
        },
        {
            "fn": "research_arxiv_fetch_full_text",
            "tags": ["research", "arxiv"],
            "annotations": {"readOnlyHint": True},
        },
        {
            "fn": "research_arxiv_parse_full_text",
            "tags": ["research", "arxiv"],
            "annotations": {"readOnlyHint": True},
        },
        {"fn": "research_europepmc_search", "tags": ["research", "europepmc"], "annotations": {"readOnlyHint": True}},
        {
            "fn": "research_europepmc_fetch_metadata",
            "tags": ["research", "europepmc"],
            "annotations": {"readOnlyHint": True},
        },
        {
            "fn": "research_europepmc_fetch_full_text",
            "tags": ["research", "europepmc"],
            "annotations": {"readOnlyHint": True},
        },
        {
            "fn": "research_europepmc_parse_full_text",
            "tags": ["research", "europepmc"],
            "annotations": {"readOnlyHint": True},
        },
        {"fn": "research_resolve_identifier", "tags": ["research"], "annotations": {"readOnlyHint": True}},
        {
            "fn": "research_localfile_fetch_full_text",
            "tags": ["research", "localfile"],
            "annotations": {"readOnlyHint": True},
        },
        {
            "fn": "research_localfile_parse_full_text",
            "tags": ["research", "localfile"],
            "annotations": {"readOnlyHint": True},
        },
        {
            "fn": "research_localfile_begin_upload",
            "tags": ["research", "localfile"],
            "annotations": {"readOnlyHint": False},
        },
        {
            "fn": "research_localfile_upload_chunk",
            "tags": ["research", "localfile"],
            "annotations": {"readOnlyHint": False},
        },
        {
            "fn": "research_localfile_finalize_upload",
            "tags": ["research", "localfile"],
            "annotations": {"readOnlyHint": False},
        },
        {"fn": "research_list_fetched", "tags": ["research", "storage"], "annotations": {"readOnlyHint": True}},
        {
            "fn": "research_delete_fetched",
            "tags": ["research", "storage"],
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {"fn": "research_search_fetched", "tags": ["research", "storage"], "annotations": {"readOnlyHint": True}},
        {"fn": "research_discovery", "tags": ["research", "discovery"], "annotations": {"readOnlyHint": True}},
        {"fn": "research_notes_create", "tags": ["research", "notes"], "annotations": {"readOnlyHint": False}},
        {"fn": "research_notes_read", "tags": ["research", "notes"], "annotations": {"readOnlyHint": True}},
        {"fn": "research_notes_update", "tags": ["research", "notes"], "annotations": {"readOnlyHint": False}},
        {
            "fn": "research_notes_delete",
            "tags": ["research", "notes"],
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {"fn": "research_notes_search", "tags": ["research", "notes"], "annotations": {"readOnlyHint": True}},
    ]

    resources: ClassVar[list[dict]] = [
        {"fn": "read_fulltext_resource", "uri": "research://{provider}/{identifier}/{format}/fulltext"},
        {
            "fn": "read_markdown_resource",
            "uri": "research://{provider}/{identifier}/{format}/markdown{?offset,limit,page}",
        },
        {"fn": "read_arxiv_categories_resource", "uri": "research://arxiv/categories"},
        {"fn": "read_openalex_work_types_resource", "uri": "research://openalex/work-types"},
        {"fn": "read_notes_export_resource", "uri": "notes://{note_id}/export"},
        {"fn": "read_vector_rebuild_status_resource", "uri": "research://vector-index/rebuild-status"},
    ]

    def __init__(self) -> None:
        storage_dir = grouping_dir(EnvVars.PRIORIS_MCP_STORAGE_DIR, DEFAULT_GROUPING)
        self._storage = FilesystemStorageBackend(storage_dir)
        self._search_index = SqliteFts5SearchIndex(storage_dir / "search.sqlite3")
        # Embedding backend and both vector-search stores (documents, notes) share one
        # EmbeddingBackend instance; the scheduler below drives background (re-)indexing
        # triggered by the providers/notes methods further down.
        vector_dir = grouping_dir(EnvVars.PRIORIS_MCP_VECTOR_DIR, DEFAULT_GROUPING)
        self._embedding_backend = FastEmbedBackend(EnvVars.PRIORIS_MCP_EMBEDDING_MODEL)
        self._document_vector_backend = SqliteVecDocumentBackend(
            vector_dir / "vectors.sqlite3", self._embedding_backend
        )
        self._note_vector_backend = SqliteVecNoteBackend(vector_dir / "notes-vectors.sqlite3", self._embedding_backend)
        self._embedding_scheduler = EmbeddingScheduler(max_concurrent=EnvVars.PRIORIS_MCP_EMBEDDING_MAX_CONCURRENCY)
        self._search_mechanisms: dict[str, SearchMechanism] = {
            "fts": FtsMechanism(self._search_index),
            "vector": VectorMechanism(
                self._document_vector_backend, self._embedding_backend, self._embedding_scheduler
            ),
        }
        if EnvVars.PRIORIS_MCP_UNVERIFIED_HTTPS:
            logger.warning(
                "HTTPS certificate verification is DISABLED (PRIORIS_MCP_UNVERIFIED_HTTPS=True) - "
                "do not use in production"
            )
        self._http_client = httpx.AsyncClient(
            follow_redirects=True,
            verify=not EnvVars.PRIORIS_MCP_UNVERIFIED_HTTPS,
            timeout=EnvVars.PRIORIS_MCP_HTTP_TIMEOUT_SECONDS,
        )
        self._openalex_client = OpenAlexClient(self._http_client, api_key=EnvVars.PRIORIS_MCP_OPENALEX_API_KEY)
        arxiv_queue = ProviderRequestQueue(
            base_spacing_seconds=ARXIV_BASE_SPACING_SECONDS,
            max_total_backoff_seconds=EnvVars.PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS,
        )
        pdf_backend = LiteParsePdfBackend()
        html_backend = HtmlToMarkdownBackend()
        self._arxiv_provider = ArxivProvider(
            storage=self._storage,
            queue=arxiv_queue,
            http_client=self._http_client,
            pdf_backend=pdf_backend,
            html_backend=html_backend,
            search_index=self._search_index,
            default_inline_char_limit=EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS,
            vector_backend=self._document_vector_backend,
            embedding_scheduler=self._embedding_scheduler,
        )
        europepmc_queue = ProviderRequestQueue(
            base_spacing_seconds=EUROPEPMC_BASE_SPACING_SECONDS,
            max_total_backoff_seconds=EnvVars.PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS,
        )
        jats_backend = JatsXsltMarkdownBackend(html_backend)
        self._europepmc_provider = EuropePmcProvider(
            storage=self._storage,
            queue=europepmc_queue,
            http_client=self._http_client,
            xml_backend=jats_backend,
            search_index=self._search_index,
            default_inline_char_limit=EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS,
            vector_backend=self._document_vector_backend,
            embedding_scheduler=self._embedding_scheduler,
        )
        self._localfile_provider = LocalFileProvider(
            storage=self._storage,
            pdf_backend=pdf_backend,
            max_size_bytes=EnvVars.PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES,
            default_inline_char_limit=EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS,
            search_index=self._search_index,
            upload_session_manager=UploadSessionManager(
                ttl_seconds=EnvVars.PRIORIS_MCP_LOCAL_FILE_UPLOAD_SESSION_TTL_SECONDS,
                max_chunk_bytes=EnvVars.PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES,
                max_total_bytes=EnvVars.PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES,
                max_concurrent=EnvVars.PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CONCURRENT_SESSIONS,
            ),
            vector_backend=self._document_vector_backend,
            embedding_scheduler=self._embedding_scheduler,
        )
        notes_dir = grouping_dir(EnvVars.PRIORIS_MCP_NOTES_DIR, DEFAULT_GROUPING)
        self._notes_search_index: NotesSearchIndex = SqliteFts5NotesSearchIndex(notes_dir / "notes-search.sqlite3")
        self._notes_backend: NotesBackend = SqliteNotesBackend(notes_dir / "notes.sqlite", self._notes_search_index)
        self._notes_search_mechanisms = {
            "fts": NotesFtsMechanism(self._notes_search_index),
            "vector": NotesVectorMechanism(self._note_vector_backend, self._embedding_backend),
        }
        self._rebuild_progress = VectorRebuildProgress()
        self._reconciliation_task: asyncio.Task | None = None

    async def research_arxiv_search(
        self,
        ctx: Context,
        query: Annotated[str, Field(description="arXiv search_query syntax, e.g. 'cat:cs.CL AND ti:transformers'")],
        max_results: Annotated[
            int, Field(default=10, description="Maximum results to return (arXiv caps at 2000)")
        ] = 10,
        start: Annotated[int, Field(default=0, description="Zero-based offset into the result set")] = 0,
        sort_by: Annotated[
            Literal["relevance", "lastUpdatedDate", "submittedDate"], Field(default="relevance")
        ] = "relevance",
        sort_order: Annotated[Literal["ascending", "descending"], Field(default="descending")] = "descending",
    ) -> ArxivSearchResult:
        """Search arXiv by keyword/query, returning metadata records."""
        return await self._arxiv_provider.search(
            query, max_results=max_results, start=start, sort_by=sort_by, sort_order=sort_order
        )

    async def research_arxiv_list_top_n(
        self,
        ctx: Context,
        include_categories: Annotated[
            list[str],
            Field(
                description="One or more arXiv subject classes to include, e.g. ['cs.CL', 'cs.LG']; combined with AND"
            ),
        ],
        n: Annotated[int, Field(description="Number of most-recently-submitted items to return")],
        exclude_categories: Annotated[
            list[str] | None,
            Field(default=None, description="Optional arXiv subject classes to exclude, combined with ANDNOT"),
        ] = None,
    ) -> ArxivSearchResult:
        """List the N most recently submitted arXiv items across one or more subject categories."""
        return await self._arxiv_provider.list_top_n(include_categories, n, exclude_categories=exclude_categories)

    async def research_arxiv_fetch_metadata(
        self,
        ctx: Context,
        arxiv_ids: Annotated[list[str], Field(description="One or more arXiv identifiers, version suffix optional")],
    ) -> ArxivFetchMetadataResult:
        """Fetch metadata for one or more arXiv identifiers in a single call."""
        return await self._arxiv_provider.fetch_metadata(arxiv_ids)

    async def research_arxiv_fetch_full_text(
        self,
        ctx: Context,
        arxiv_id: Annotated[str, Field(description="An arXiv identifier, version suffix optional")],
        format: Annotated[Literal["pdf", "html"], Field(description="Full-text format to fetch")],
    ) -> FullTextFetchResult:
        """Fetch (or return the already-persisted) full text for an arXiv item."""
        return await self._arxiv_provider.fetch_full_text(arxiv_id, format)

    async def research_arxiv_parse_full_text(
        self,
        ctx: Context,
        arxiv_id: Annotated[str, Field(description="An arXiv identifier, version suffix optional")],
        format: Annotated[Literal["pdf", "html"], Field(description="Already-persisted source format to parse")],
        offset: Annotated[int, Field(default=0, description="Zero-based character offset into the Markdown")] = 0,
        limit: Annotated[
            int | None,
            Field(default=None, description="Max Markdown characters to return; defaults to a server-side cap"),
        ] = None,
        page: Annotated[
            int | None, Field(default=None, description="1-indexed PDF page to page by; pdf format only")
        ] = None,
    ) -> ParsedFullText:
        """Convert already-fetched arXiv full text into one page of Markdown."""
        return await self._arxiv_provider.parse_full_text(arxiv_id, format, offset=offset, limit=limit, page=page)

    async def research_europepmc_search(
        self,
        ctx: Context,
        query: Annotated[str, Field(description="Europe PMC query syntax, e.g. 'field:value AND field:value'")],
        page_size: Annotated[int, Field(default=25)] = 25,
        cursor_mark: Annotated[str, Field(default="*", description="Europe PMC's opaque pagination cursor")] = "*",
    ) -> EuropePmcSearchResult:
        """Search Europe PMC by keyword/query, returning metadata records."""
        return await self._europepmc_provider.search(query, page_size=page_size, cursor_mark=cursor_mark)

    async def research_europepmc_fetch_metadata(
        self,
        ctx: Context,
        identifiers: Annotated[list[str], Field(description="One or more Europe PMC identifiers or bare PMCIDs")],
    ) -> EuropePmcFetchMetadataResult:
        """Fetch metadata for one or more Europe PMC identifiers in a single call."""
        return await self._europepmc_provider.fetch_metadata(identifiers)

    async def research_europepmc_fetch_full_text(
        self,
        ctx: Context,
        identifier: Annotated[str, Field(description="A Europe PMC identifier or bare PMCID")],
    ) -> FullTextFetchResult:
        """Fetch (or return the already-persisted) JATS XML full text for a Europe PMC item."""
        return await self._europepmc_provider.fetch_full_text(identifier)

    async def research_europepmc_parse_full_text(
        self,
        ctx: Context,
        identifier: Annotated[str, Field(description="A Europe PMC identifier or bare PMCID")],
        offset: Annotated[int, Field(default=0, description="Zero-based character offset into the Markdown")] = 0,
        limit: Annotated[
            int | None,
            Field(default=None, description="Max Markdown characters to return; defaults to a server-side cap"),
        ] = None,
    ) -> ParsedFullText:
        """Convert already-fetched Europe PMC JATS XML full text into one page of Markdown."""
        return await self._europepmc_provider.parse_full_text(identifier, offset=offset, limit=limit)

    async def research_resolve_identifier(
        self,
        ctx: Context,
        identifier: Annotated[str, Field(description="An arXiv ID, a Europe PMC identifier, or a DOI")],
        format: Annotated[
            str, Field(description="Desired target format; valid values depend on the resolving provider")
        ],
    ) -> ResolvedIdentifierResult:
        """Resolve an identifier of unknown provider (including DOIs) to its owning provider and URL."""
        return await resolve_research_identifier(
            identifier, format, self._http_client, self._arxiv_provider, self._europepmc_provider
        )

    @staticmethod
    def _normalise_discovery_identifier(provider: str, identifier: str) -> str:
        """Normalise a provider identifier to the form discovery can compare with local storage."""
        if provider == "arxiv":
            return re.sub(r"v\d+$", "", identifier, flags=re.IGNORECASE)
        if provider == "europepmc":
            return identifier.upper().removeprefix("PMC:").removeprefix("PMC")
        return identifier

    async def _fetched_discovery_identifiers(self, provider: str) -> set[str]:
        """Return every locally fetched identifier for a provider, normalised for discovery."""
        identifiers: set[str] = set()
        offset = 0
        limit = 200
        while True:
            entries, total = await self._storage.list(provider, offset=offset, limit=limit)
            identifiers.update(self._normalise_discovery_identifier(provider, entry["identifier"]) for entry in entries)
            offset += len(entries)
            if not entries or offset >= total:
                return identifiers

    async def _exclude_local_discovery_hits(self, result: DiscoveryResult) -> DiscoveryResult:
        """Remove hits already fetched through a route whose provider-native identity is known."""
        routed_providers = {
            hit.fetch_route.provider
            for hit in result.hits
            if hit.fetch_route.kind == "known_provider" and hit.fetch_route.provider is not None
        }
        fetched_identifiers = {
            provider: await self._fetched_discovery_identifiers(provider) for provider in routed_providers
        }
        return DiscoveryResult(
            hits=[hit for hit in result.hits if not self._is_locally_fetched_discovery_hit(hit, fetched_identifiers)],
            page=result.page,
            per_page=result.per_page,
            total=result.total,
            has_more=result.has_more,
        )

    def _is_locally_fetched_discovery_hit(self, hit: DiscoveryHit, fetched_identifiers: dict[str, set[str]]) -> bool:
        """Whether a discovery hit has a provider-native identifier already in local storage."""
        route = hit.fetch_route
        if route.kind != "known_provider" or route.provider is None or route.identifier is None:
            return False
        return self._normalise_discovery_identifier(route.provider, route.identifier) in fetched_identifiers.get(
            route.provider, set()
        )

    async def research_discovery(
        self,
        ctx: Context,
        query: Annotated[
            str,
            Field(
                description=(
                    "Free-text query (title, abstract, grant summary, or similar - up to 2000 characters), "
                    "embedded and ranked by similarity via OpenAlex search.semantic"
                )
            ),
        ],
        max_results: Annotated[
            int | None,
            Field(
                default=None, ge=1, le=OPENALEX_MAX_RESULTS, description="Defaults to PRIORIS_MCP_DISCOVERY_MAX_RESULTS"
            ),
        ] = None,
        page: Annotated[
            int, Field(default=1, ge=1, description="1-indexed page of results, within search.semantic's 50-match cap")
        ] = 1,
        from_year: Annotated[
            int | None, Field(default=None, description="Only works published in or after this year")
        ] = None,
        to_year: Annotated[
            int | None, Field(default=None, description="Only works published in or before this year")
        ] = None,
        open_access_only: Annotated[
            bool, Field(default=False, description="Restrict to works OpenAlex marks as open access")
        ] = False,
    ) -> DiscoveryResult:
        """Discover external research candidates that are not already in the local corpus."""
        result = await self._openalex_client.search_semantic(
            query,
            max_results=max_results if max_results is not None else EnvVars.PRIORIS_MCP_DISCOVERY_MAX_RESULTS,
            page=page,
            from_year=from_year,
            to_year=to_year,
            open_access_only=open_access_only,
        )
        return await self._exclude_local_discovery_hits(result)

    async def research_localfile_fetch_full_text(
        self,
        ctx: Context,
        content_base64: Annotated[str, Field(description="Base64-encoded bytes of a PDF the caller already has")],
        filename: Annotated[
            str | None, Field(default=None, description="Optional caller-supplied filename, stored for reference only")
        ] = None,
    ) -> LocalFileFetchResult:
        """Validate and persist caller-sent PDF bytes, returning a server-assigned caller-facing ID.

        Small-file path only - the whole payload must fit in one call. For files at risk of
        hitting transport/relay size limits, use research_localfile_begin_upload +
        research_localfile_upload_chunk + research_localfile_finalize_upload instead. This tool
        is a deprecation candidate once the chunked flow is established, though it is not being
        removed now.
        """
        if not getattr(self, "_warned_localfile_fetch_full_text_deprecation", False):
            logger.warning(
                "research_localfile_fetch_full_text is a deprecation candidate - see "
                "research_localfile_begin_upload/research_localfile_upload_chunk/research_localfile_finalize_upload "
                "for the chunked-upload alternative"
            )
            self._warned_localfile_fetch_full_text_deprecation = True
        return await self._localfile_provider.fetch_full_text(content_base64, filename)

    async def research_localfile_parse_full_text(
        self,
        ctx: Context,
        id: Annotated[
            str, Field(description="The caller-facing identifier returned by research_localfile_fetch_full_text")
        ],
        offset: Annotated[int, Field(default=0, description="Zero-based character offset into the Markdown")] = 0,
        limit: Annotated[
            int | None,
            Field(default=None, description="Max Markdown characters to return; defaults to a server-side cap"),
        ] = None,
        page: Annotated[int | None, Field(default=None, description="1-indexed PDF page to page by")] = None,
    ) -> ParsedFullText:
        """Convert an already-fetched local PDF's full text into one page of Markdown."""
        return await self._localfile_provider.parse_full_text(id, offset=offset, limit=limit, page=page)

    async def research_localfile_begin_upload(
        self,
        ctx: Context,
        filename: Annotated[
            str | None, Field(default=None, description="Optional caller-supplied filename, stored for reference only")
        ] = None,
    ) -> LocalFileBeginUploadResult:
        """Start a new chunked upload session for a large local PDF; returns a session_id."""
        return await self._begin_upload(filename)

    async def _begin_upload(self, filename: str | None) -> LocalFileBeginUploadResult:
        session_id = await self._localfile_provider.begin_upload(filename)
        return LocalFileBeginUploadResult(
            session_id=session_id, max_chunk_bytes=EnvVars.PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES
        )

    async def research_localfile_upload_chunk(
        self,
        ctx: Context,
        session_id: Annotated[str, Field(description="The session_id returned by research_localfile_begin_upload")],
        index: Annotated[int, Field(description="Zero-based chunk index; chunks must arrive in strict order")],
        chunk_base64: Annotated[str, Field(description="Base64-encoded bytes of this chunk")],
    ) -> LocalFileUploadChunkResult:
        """Upload one chunk of a large local PDF to an in-progress upload session."""
        return await self._localfile_provider.upload_chunk(session_id, index, chunk_base64)

    async def research_localfile_finalize_upload(
        self,
        ctx: Context,
        session_id: Annotated[str, Field(description="The session_id returned by research_localfile_begin_upload")],
    ) -> LocalFileFetchResult:
        """Validate and persist a chunked upload session's reassembled content, same as fetch_full_text."""
        return await self._localfile_provider.finalize_upload(session_id)

    async def research_list_fetched(
        self,
        ctx: Context,
        provider: Annotated[
            Literal["arxiv", "europepmc", "localfile"] | None,
            Field(default=None, description="Restrict to one provider; omit to list all providers"),
        ] = None,
        format: Annotated[str | None, Field(default=None, description="Further restrict to one format")] = None,
        offset: Annotated[int, Field(default=0)] = 0,
        limit: Annotated[int, Field(default=50)] = 50,
    ) -> ListFetchedResult:
        """Enumerate persisted (provider, identifier, format) manifest entries, newest first; never triggers a fetch."""
        if offset < 0:
            raise InvalidRequestError(f"offset must be >= 0, got {offset}")
        if limit <= 0:
            raise InvalidRequestError(f"limit must be > 0, got {limit}")
        entries, total = await self._storage.list(provider, format, offset=offset, limit=limit)
        return ListFetchedResult(
            entries=entries, offset=offset, limit=limit, total=total, has_more=offset + len(entries) < total
        )

    async def research_delete_fetched(
        self,
        ctx: Context,
        entries: Annotated[
            list[DeleteEntryRef],
            Field(description="One or more {provider, identifier, format, artefact} entries to remove"),
        ],
    ) -> DeleteFetchedResult:
        """Remove one or more persisted artefacts, tolerating entries no longer present.

        `identifier` must match the canonical form actually in storage (e.g. a version-pinned
        arXiv id like "2403.10131v2", not an unversioned id that may resolve to a newer version
        by the time this call runs) - use `research_list_fetched` first to find it if unsure.

        `artefact` is "document", "markdown", or "all" - see
        docs/requirement-specification/02-storage.md#deletion-is-per-artefact-not-per-format.
        Does not cascade between artefacts: deleting "document" leaves "markdown" in place and
        vice versa. Deleting "markdown" or "all" also removes that document from the search
        index.
        """
        return await self._delete_fetched(entries)

    async def research_search_fetched(
        self,
        ctx: Context,
        query: Annotated[str, Field(description="FTS5 query syntax, or free text for vector/hybrid mode")],
        provider: Annotated[Literal["arxiv", "europepmc", "localfile"] | None, Field(default=None)] = None,
        identifier: Annotated[
            str | None, Field(default=None, description="Scopes to one document; requires provider")
        ] = None,
        format: Annotated[str | None, Field(default=None)] = None,
        mode: Annotated[
            str,
            Field(
                default="fts",
                description=(
                    "A registered mechanism name (e.g. 'fts', 'vector'), or 'hybrid'. Vector/KNN "
                    "search is unthresholded: it always returns up to limit nearest matches "
                    "regardless of how dissimilar they are, so treat score as a relevance signal "
                    "to filter on client-side rather than assuming every match is relevant."
                ),
            ),
        ] = "fts",
        offset: Annotated[int, Field(default=0, description="Number of matches to skip, per mechanism")] = 0,
        limit: Annotated[
            int | None,
            Field(
                default=None,
                description="Max results per mechanism; defaults to PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT",
            ),
        ] = None,
    ) -> SearchFetchedResult:
        """Search previously-persisted chunks; never fetches or parses. See mode for mechanism choice."""
        if identifier is not None and provider is None:
            raise InvalidRequestError("identifier requires provider")
        if offset < 0:
            raise InvalidRequestError(f"offset must be >= 0, got {offset}")
        if limit is not None and limit <= 0:
            raise InvalidRequestError(f"limit must be > 0, got {limit}")
        if mode == "hybrid":
            selected = list(self._search_mechanisms.values())
        else:
            mechanism = self._search_mechanisms.get(mode)
            if mechanism is None:
                raise InvalidRequestError(f"unknown or unavailable mode: {mode!r}")
            selected = [mechanism]

        effective_limit = limit if limit is not None else EnvVars.PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT
        results: dict[str, tuple[list, int]] = {}
        for mechanism in selected:
            try:
                raw = await mechanism.search(
                    query,
                    provider=provider,
                    identifier=identifier,
                    format=format,
                    offset=offset,
                    limit=effective_limit,
                )
                total = await mechanism.count(query, provider=provider, identifier=identifier, format=format)
            except sqlite3.OperationalError as exc:
                raise InvalidRequestError(f"invalid search query: {exc}") from exc
            results[mechanism.name] = (raw, total)

        index_status: dict[str, IndexStatus] = {}
        if identifier is not None and provider is not None and format is not None:
            for mechanism in self._search_mechanisms.values():
                index_status[mechanism.name] = await mechanism.status(provider, identifier, format)

        fts_paged = None
        if "fts" in results:
            raw, total = results["fts"]
            matches = [SearchMatch(**m) for m in raw]
            fts_paged = PagedSearchMatches(
                matches=matches,
                offset=offset,
                limit=effective_limit,
                total=total,
                has_more=offset + len(matches) < total,
            )
        vector_paged = None
        if "vector" in results:
            raw, total = results["vector"]
            matches = [VectorSearchMatch(**m) for m in raw]
            vector_paged = PagedVectorSearchMatches(
                matches=matches,
                offset=offset,
                limit=effective_limit,
                total=total,
                has_more=offset + len(matches) < total,
            )

        return SearchFetchedResult(fts=fts_paged, vector=vector_paged, index_status=index_status)

    async def research_notes_create(
        self,
        ctx: Context,
        provider: Annotated[Literal["arxiv", "europepmc", "localfile"], Field(description="Owning provider")],
        identifier: Annotated[str, Field(description="Provider-native identifier; canonicalised before storing")],
        format: Annotated[str | None, Field(default=None, description="Omit for a note predating any fetch")] = None,
        author_name: Annotated[str | None, Field(default=None, description="Null means self")] = None,
        metadata: Annotated[dict[str, str] | None, Field(default=None, description="Caller-owned, opaque")] = None,
        *,
        anchors: Annotated[list[Anchor], Field(default_factory=list, description="Unresolved positional hints")],
        tags: Annotated[list[str], Field(default_factory=list)],
        text: Annotated[str, Field(description="Free-form Markdown - the note itself")],
    ) -> Note:
        """Create a new user-authored note against a document, or against a bare identifier."""
        canonical_identifier = await self._resolve_canonical_identifier_for_notes(provider, identifier, format)
        note = await self._notes_backend.create(
            provider,
            canonical_identifier,
            format,
            text,
            anchors=anchors,
            author_name=author_name,
            tags=tags,
            metadata=metadata,
        )
        self._embedding_scheduler.schedule(
            ("note", note.id), lambda: self._note_vector_backend.index_note(note.id, note.text)
        )
        return note

    async def research_notes_read(
        self, ctx: Context, note_id: Annotated[str, Field(description="A note id returned by research_notes_create")]
    ) -> Note:
        """Read a single note by id."""
        try:
            return await self._notes_backend.read(note_id)
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc

    async def research_notes_update(
        self,
        ctx: Context,
        note_id: Annotated[str, Field(description="A note id returned by research_notes_create")],
        text: Annotated[str | None, Field(default=None)] = None,
        anchors: Annotated[list[Anchor] | None, Field(default=None)] = None,
        tags: Annotated[list[str] | None, Field(default=None)] = None,
        metadata: Annotated[dict[str, str] | None, Field(default=None)] = None,
    ) -> Note:
        """Partially edit an existing note; fields left as None are unchanged."""
        try:
            updated = await self._notes_backend.update(
                note_id, text=text, anchors=anchors, tags=tags, metadata=metadata
            )
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        if text is not None:
            self._embedding_scheduler.schedule(
                ("note", note_id), lambda: self._note_vector_backend.index_note(note_id, updated.text)
            )
        return updated

    async def research_notes_delete(
        self, ctx: Context, note_id: Annotated[str, Field(description="A note id returned by research_notes_create")]
    ) -> bool:
        """Delete a note by id. Returns False, not an error, if it's already absent."""
        removed = await self._notes_backend.delete(note_id)
        if removed:
            await self._embedding_scheduler.cancel(("note", note_id))
            await self._note_vector_backend.remove_note(note_id)
        return removed

    @staticmethod
    def _validate_notes_search_params(
        author_filter: AuthorFilter, author_name: str | None, offset: int, limit: int
    ) -> None:
        """Validate research_notes_search's own params, ahead of any mode branching or backend call.

        Split out of research_notes_search itself to keep that method's cyclomatic complexity down
        - these checks are unconditional (independent of `mode`), so mode='vector' (which never
        calls self._notes_backend.search/matching_ids, where the author_filter/author_name pairing
        check also lives) doesn't silently drop a misused author_name instead of erroring like
        fts/hybrid do, and a negative offset/non-positive limit is rejected before it can flow into
        slicing or the underlying KNN query's limit.

        Raises:
            InvalidRequestError: author_filter/author_name misuse, or offset/limit out of range.
        """
        if author_filter == AuthorFilter.NAMED and author_name is None:
            raise InvalidRequestError("author_filter=NAMED requires author_name")
        if author_filter != AuthorFilter.NAMED and author_name is not None:
            raise InvalidRequestError("author_name is only used when author_filter=NAMED")
        if offset < 0:
            raise InvalidRequestError(f"offset must be >= 0, got {offset}")
        if limit <= 0:
            raise InvalidRequestError(f"limit must be > 0, got {limit}")

    async def research_notes_search(
        self,
        ctx: Context,
        provider: Annotated[Literal["arxiv", "europepmc", "localfile"] | None, Field(default=None)] = None,
        canonical_identifier: Annotated[str | None, Field(default=None)] = None,
        format: Annotated[str | None, Field(default=None)] = None,
        date_from: Annotated[str | None, Field(default=None, description="ISO 8601")] = None,
        date_to: Annotated[str | None, Field(default=None, description="ISO 8601")] = None,
        keyword: Annotated[
            str | None, Field(default=None, description="FTS5 query syntax, or free text for vector/hybrid mode")
        ] = None,
        author_filter: Annotated[AuthorFilter, Field(default=AuthorFilter.ANY)] = AuthorFilter.ANY,
        author_name: Annotated[
            str | None, Field(default=None, description="Only used when author_filter=named")
        ] = None,
        *,
        tags_all: Annotated[list[str], Field(default_factory=list)],
        tags_any: Annotated[list[str], Field(default_factory=list)],
        tags_exclude: Annotated[list[str], Field(default_factory=list)],
        offset: Annotated[int, Field(default=0)] = 0,
        limit: Annotated[int, Field(default=50)] = 50,
        mode: Annotated[
            str,
            Field(
                default="fts",
                description=(
                    "A registered mechanism name (e.g. 'fts', 'vector'), or 'hybrid'. Vector/KNN "
                    "search is unthresholded: it always returns up to limit nearest matches "
                    "regardless of how dissimilar they are, so treat score as a relevance signal "
                    "to filter on client-side rather than assuming every match is relevant."
                ),
            ),
        ] = "fts",
    ) -> NotesSearchResult:
        """Search/list notes; no filters at all returns everything, paged, newest first. See mode for mechanism choice."""
        if mode != "hybrid" and mode not in self._notes_search_mechanisms:
            raise InvalidRequestError(f"unknown or unavailable mode: {mode!r}")
        if mode == "vector" and keyword is None:
            raise InvalidRequestError("mode='vector' requires keyword: nothing to embed")
        self._validate_notes_search_params(author_filter, author_name, offset, limit)

        fts_result: PagedNotes | None = None
        if mode in ("fts", "hybrid"):
            try:
                fts_result = await self._notes_backend.search(
                    provider=provider,
                    canonical_identifier=canonical_identifier,
                    format=format,
                    date_from=date_from,
                    date_to=date_to,
                    keyword=keyword,
                    author_filter=author_filter,
                    author_name=author_name,
                    tags_all=tags_all,
                    tags_any=tags_any,
                    tags_exclude=tags_exclude,
                    offset=offset,
                    limit=limit,
                )
            except ValueError as exc:
                raise InvalidRequestError(str(exc)) from exc
            except sqlite3.OperationalError as exc:
                raise InvalidRequestError(f"invalid search query: {exc}") from exc

        vector_result: PagedNoteVectorMatches | None = None
        status_scope_note_ids: list[str] | None = None
        if mode in ("vector", "hybrid") and keyword is not None:
            structural_filters_given = any(
                [
                    provider is not None,
                    canonical_identifier is not None,
                    format is not None,
                    date_from is not None,
                    date_to is not None,
                    author_filter != AuthorFilter.ANY,
                    tags_all,
                    tags_any,
                    tags_exclude,
                ]
            )
            note_ids = None
            if structural_filters_given:
                try:
                    note_ids = await self._notes_backend.matching_ids(
                        provider=provider,
                        canonical_identifier=canonical_identifier,
                        format=format,
                        date_from=date_from,
                        date_to=date_to,
                        author_filter=author_filter,
                        author_name=author_name,
                        tags_all=tags_all,
                        tags_any=tags_any,
                        tags_exclude=tags_exclude,
                    )
                except ValueError as exc:
                    raise InvalidRequestError(str(exc)) from exc
            # The status aggregate below must reflect every note the caller's structural filters
            # select, not just this page's KNN matches - an in-scope note that hasn't been
            # embedded yet has no vector row, so it can never appear in `matches`, but the caller
            # still needs to see "building"/"not_built" for it rather than a stale/unrelated
            # corpus-wide "ready". `note_ids` is reused when given (already the structural scope);
            # an unfiltered query fetches the same full-corpus id set matching_ids() would return,
            # purely for this status computation - `mechanism.search`/`count` still receive `None`
            # so the unfiltered KNN query itself stays unscoped.
            status_scope_note_ids = note_ids if note_ids is not None else await self._notes_backend.matching_ids()
            mechanism = cast(NotesVectorMechanism, self._notes_search_mechanisms["vector"])
            raw = await mechanism.search(keyword, note_ids=note_ids, offset=offset, limit=limit)
            total = await mechanism.count(note_ids=note_ids)
            matches = [NoteVectorSearchMatch(**m) for m in raw]
            vector_result = PagedNoteVectorMatches(
                matches=matches, offset=offset, limit=limit, total=total, has_more=offset + len(matches) < total
            )

        index_status: dict[str, IndexStatus] | None = None
        if vector_result is not None:
            if not status_scope_note_ids:
                # No note matches the caller's structural filters at all (or the corpus is
                # genuinely empty) - nothing to build, so there's no in-scope work in flight.
                index_status = {"vector": "not_built"}
            else:
                # One batched query for every in-scope note's persisted status, rather than N
                # serial SqliteVecNoteBackend.status() connect+query round-trips - the
                # "building" override still has to be per-note against the in-memory scheduler,
                # same semantics `_note_index_status` (now folded in here) had.
                db_statuses = await self._note_vector_backend.statuses_for(status_scope_note_ids)
                statuses = [
                    "building"
                    if self._embedding_scheduler.is_building(("note", note_id))
                    else db_statuses.get(note_id, "not_built")
                    for note_id in status_scope_note_ids
                ]
                if "building" in statuses:
                    index_status = {"vector": "building"}
                elif "not_built" in statuses:
                    index_status = {"vector": "not_built"}
                elif "stale" in statuses:
                    index_status = {"vector": "stale"}
                else:
                    index_status = {"vector": "ready"}

        return NotesSearchResult(fts=fts_result, vector=vector_result, index_status=index_status)

    async def _force_vector_reconnect(self) -> None:
        """Force both vector backends' `_connect()` to run now, so any model-mismatch drop happens here.

        `count()` with no filters is the cheapest existing call that reaches `_connect()`. Must run
        before anything else touches either vector backend for this process, so a model-name change
        is always deliberately reconciled rather than dropped as a side effect of an ordinary
        client request - see
        docs/superpowers/specs/2026-08-15-vector-index-reconciliation-design.md.
        """
        await self._document_vector_backend.count()
        await self._note_vector_backend.count()

    async def reconcile_vector_index(self) -> None:
        """Background corpus-wide re-embed of every document/note not yet ready under the configured model.

        Fired as a background task from `_vector_reconciliation_lifespan`'s startup phase, after
        `_force_vector_reconnect` has already made any destructive model-mismatch drop deliberate.
        Reuses the same per-item `EmbeddingScheduler` trigger every other indexing path already
        uses, so a bulk-scheduled item is visible as "building" the same way a freshly-fetched
        document already is - see
        docs/superpowers/specs/2026-08-15-vector-index-reconciliation-design.md.

        Document and note reconciliation are isolated from each other - a failure enumerating or
        scheduling one mechanism's corpus is logged and does not prevent the other mechanism's
        reconciliation from running, and never propagates out of this background task.
        """
        try:
            await self._reconcile_documents()
        except Exception:
            logger.exception("Document vector-index reconciliation failed")
        try:
            await self._reconcile_notes()
        except Exception:
            logger.exception("Note vector-index reconciliation failed")

    async def _reconcile_documents(self) -> None:
        model_name = self._embedding_backend.model_name
        already_ready = await self._document_vector_backend.indexed_under(model_name)
        to_rebuild: list[dict] = []
        offset = 0
        limit = 200
        while True:
            entries, total = await self._storage.list_markdown_entries(offset=offset, limit=limit)
            to_rebuild.extend(
                entry
                for entry in entries
                if (entry["provider"], entry["identifier"], entry["format"]) not in already_ready
            )
            offset += len(entries)
            if not entries or offset >= total:
                break
        self._rebuild_progress.set_documents_total(len(to_rebuild))
        for entry in to_rebuild:
            self._schedule_document_reembed(entry)

    def _schedule_document_reembed(self, entry: dict) -> None:
        provider = entry["provider"]
        canonical_identifier = entry["canonical_identifier"]
        identifier = entry["identifier"]
        format_ = entry["format"]

        async def _reembed_and_mark_done() -> None:
            # try/except/else, not a bare call: EmbeddingScheduler already catches and logs any
            # non-cancellation exception, but it has no callback to this progress counter - a
            # failure here would otherwise leave `pending` stuck forever with no evidence of what
            # happened. Re-raising in both non-success branches preserves the scheduler's own
            # single log site and cancellation handling unchanged; this only adds bookkeeping.
            try:
                markdown_bytes = await self._storage.read(provider, canonical_identifier, format_, artefact="markdown")
                markdown = markdown_bytes.decode("utf-8")
                manifest = self._storage.manifest_for(provider, canonical_identifier)
                search_rows = await manifest.rows_for_search(format_)
                reindex_entries = [
                    {
                        "chunk_id": str(uuid.uuid4()),
                        "start": row["start"],
                        "length": row["length"],
                        "text": markdown[row["start"] : row["start"] + row["length"]],
                    }
                    for row in search_rows
                ]
                await self._document_vector_backend.index_entries(provider, identifier, format_, reindex_entries)
            except asyncio.CancelledError:
                self._rebuild_progress.document_cancelled()
                raise
            except Exception:
                self._rebuild_progress.document_failed()
                raise
            else:
                self._rebuild_progress.document_succeeded()

        self._embedding_scheduler.schedule(
            (provider, identifier, format_),
            _reembed_and_mark_done,
            on_discarded=self._rebuild_progress.document_cancelled,
        )

    async def _reconcile_notes(self) -> None:
        model_name = self._embedding_backend.model_name
        already_ready = await self._note_vector_backend.indexed_under(model_name)
        all_note_ids = await self._notes_backend.matching_ids()
        to_rebuild = [note_id for note_id in all_note_ids if note_id not in already_ready]
        self._rebuild_progress.set_notes_total(len(to_rebuild))
        for note_id in to_rebuild:
            self._schedule_note_reembed(note_id)

    def _schedule_note_reembed(self, note_id: str) -> None:
        async def _reembed_and_mark_done() -> None:
            # See the matching comment in _schedule_document_reembed for why this is
            # try/except/else rather than a bare call.
            try:
                note = await self._notes_backend.read(note_id)
                await self._note_vector_backend.index_note(note_id, note.text)
            except asyncio.CancelledError:
                self._rebuild_progress.note_cancelled()
                raise
            except Exception:
                self._rebuild_progress.note_failed()
                raise
            else:
                self._rebuild_progress.note_succeeded()

        self._embedding_scheduler.schedule(
            ("note", note_id), _reembed_and_mark_done, on_discarded=self._rebuild_progress.note_cancelled
        )

    async def _delete_fetched(self, entries: list[DeleteEntryRef]) -> DeleteFetchedResult:
        deleted: list[DeleteEntryRef] = []
        not_found: list[DeleteEntryRef] = []
        for entry in entries:
            removed = await self._storage.delete(entry.provider, entry.identifier, entry.format_, entry.artefact)
            if removed and entry.artefact in ("markdown", "all"):
                await self._search_index.remove_document(entry.provider, entry.identifier, entry.format_)
                await self._embedding_scheduler.cancel((entry.provider, entry.identifier, entry.format_))
                await self._document_vector_backend.remove_document(entry.provider, entry.identifier, entry.format_)
            (deleted if removed else not_found).append(entry)
        return DeleteFetchedResult(deleted=deleted, not_found=not_found)

    async def _resolve_storage_identifier(self, provider: str, identifier: str, format: str) -> str:
        """Translate a caller-facing identifier into its storage-key identifier.

        Only the local filesystem source's caller-facing ID differs from its storage key (a
        content hash) - see
        docs/requirement-specification/02-storage.md#caller-facing-identifiers-for-sources-without-one;
        every other provider's identifier already *is* its storage key.
        """
        if provider != "localfile":
            return identifier
        canonical = await self._storage.find_canonical_identifier("localfile", identifier, format)
        if canonical is None:
            raise FileNotFoundError(identifier)
        return canonical

    async def _resolve_canonical_identifier_for_notes(self, provider: str, identifier: str, format: str | None) -> str:
        """Pin `identifier` to its canonical form for a new/updated note.

        When `format` is given, delegates to the owning provider's own `resolve_identifier`
        (arXiv/Europe PMC) and takes its `.identifier`, mirroring `StorageBackend`'s own
        canonicalisation - this keeps a note pointed at the same storage-key identifier a fetched
        artefact would end up under. `localfile` notes use the given identifier as-is: it's
        already a stable, server-assigned caller-facing ID, not something `LocalFileProvider` can
        re-resolve (it doesn't implement `resolve_identifier` at all). When `format` is `None` -
        a note that predates any fetch - resolution is skipped entirely and the given identifier
        is stored as-is, since there is no fetched artefact whose storage-key stability this needs
        to protect yet.
        """
        if format is None:
            return identifier
        if provider == "arxiv":
            resolved = await self._arxiv_provider.resolve_identifier(identifier, format)
            return resolved.identifier
        if provider == "europepmc":
            resolved = await self._europepmc_provider.resolve_identifier(identifier, format)
            return resolved.identifier
        if provider == "localfile":
            return identifier
        raise InvalidRequestError(f"unrecognised provider: {provider!r}")

    async def read_fulltext_resource(self, provider: str, identifier: str, format: str) -> bytes:
        """Read persisted full text for (provider, identifier, format); a plain not-found if absent."""
        storage_identifier = await self._resolve_storage_identifier(provider, identifier, format)
        return await self._storage.read(provider, storage_identifier, format)

    async def read_markdown_resource(
        self,
        provider: str,
        identifier: str,
        format: str,
        offset: int = 0,
        limit: int | None = None,
        page: int | None = None,
    ) -> str:
        """Read one page of persisted parsed Markdown for (provider, identifier, format).

        A plain not-found if absent. `page` (PDF-only, 1-indexed) resolves directly against
        this document's manifest.sqlite via StorageBackend - never through a provider/parser,
        since this endpoint is documented as never triggering a fetch/parse. Requesting `page`
        before parse_full_text has ever populated the manifest is a not-found, not a silent
        fallback to character offset 0.

        Paginated the same way as `parse_full_text` - see
        docs/requirement-specification/04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole
        - `limit` defaults to `PRIORIS_MCP_MAX_INLINE_CHARS` when unset.

        Returns the `MarkdownPage` serialised to JSON - FastMCP's resource templates, unlike its
        tools, don't auto-serialise a returned Pydantic model into resource content.
        """
        if page is not None and format != "pdf":
            raise InvalidRequestError(f"page is not supported for format {format!r}")
        storage_identifier = await self._resolve_storage_identifier(provider, identifier, format)
        markdown_bytes = await self._storage.read(provider, storage_identifier, format, artefact="markdown")
        markdown = markdown_bytes.decode("utf-8")
        manifest = self._storage.manifest_for(provider, storage_identifier)

        base_offset = offset
        if page is not None:
            leaf = await manifest.leaf_for_page(format, page)
            if leaf is None:
                raise FileNotFoundError(f"page {page} does not exist for {provider}:{storage_identifier}:{format}")
            base_offset = leaf["start"] + offset

        page_data = paginate_text(
            markdown, base_offset, limit if limit is not None else EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS
        )
        total_pages = None
        page_range = None
        if format == "pdf":
            total_pages = await manifest.total_pages(format)
            page_range = await manifest.page_range_for_span(format, page_data["offset"], len(page_data["content"]))

        return MarkdownPage(
            markdown=page_data["content"],
            offset=page_data["offset"],
            limit=page_data["limit"],
            total_length=page_data["total_length"],
            has_more=page_data["has_more"],
            total_pages=total_pages,
            page_range=page_range,
        ).model_dump_json()

    async def read_arxiv_categories_resource(self) -> str:
        """Read arXiv's queryable category codes and names, for `research_arxiv_list_top_n`/`research_arxiv_search`.

        Returns the `ArxivCategoriesResult` serialised to JSON - see `read_markdown_resource` for why.
        """
        return (await self._arxiv_provider.list_categories()).model_dump_json()

    async def read_openalex_work_types_resource(self) -> str:
        """Read OpenAlex's work `type` vocabulary, for interpreting a `research_discovery` hit's own metadata.

        Returns the `OpenAlexWorkTypesResult` serialised to JSON - see `read_markdown_resource` for why.
        """
        return (await self._openalex_client.list_work_types()).model_dump_json()

    async def read_notes_export_resource(self, note_id: str) -> str:
        """Read one note's file representation, for the caller to write to disk itself.

        Returns the `NoteExport` serialised to JSON - see `read_markdown_resource` for why.
        `frontmatter` is a plain JSON object, not pre-rendered YAML - the caller renders it into
        whatever frontmatter dialect its target tool expects before writing `markdown_body` to a
        file.
        """
        return (await self._notes_backend.export(note_id)).model_dump_json()

    async def read_vector_rebuild_status_resource(self) -> str:
        """Read corpus-wide vector-index rebuild progress for the reconciliation run started at server startup.

        `total`/`pending`/`succeeded`/`failed` count only items this run decided needed rebuilding
        - a corpus already fully ready under the configured model reports all zeros. A nonzero
        `failed` with `active=False` is a terminal, not-currently-recoverable-without-a-restart
        state - see docs/requirement-specification/ADR/00031-rebuild-progress-failure-visibility.md.
        Process-local, in-memory, not persisted - see
        docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
        Returns the `VectorRebuildStatus` serialised to JSON - see `read_markdown_resource` for why.
        """
        progress = self._rebuild_progress
        return VectorRebuildStatus(
            documents=VectorRebuildMechanismStatus(
                total=progress.documents.total,
                pending=progress.documents.pending,
                succeeded=progress.documents.succeeded,
                failed=progress.documents.failed,
                active=progress.documents.active,
            ),
            notes=VectorRebuildMechanismStatus(
                total=progress.notes.total,
                pending=progress.notes.pending,
                succeeded=progress.notes.succeeded,
                failed=progress.notes.failed,
                active=progress.notes.active,
            ),
        ).model_dump_json()


def _vector_reconciliation_lifespan(
    mcp_obj: "PriorisMCP",
) -> Callable[[FastMCP], AbstractAsyncContextManager[None]]:
    """Build the lifespan callable that runs vector-index reconciliation before serving requests.

    Split out of app() so it's directly testable without going through FastMCP's own lifespan
    machinery, since app()/main() stay '# pragma: no cover' by existing convention. Only
    mcp_obj._force_vector_reconnect() blocks this context manager's __aenter__; corpus
    enumeration/scheduling (reconcile_vector_index()) runs as a background task so server startup
    never waits on corpus size - see
    docs/superpowers/specs/2026-08-15-vector-index-reconciliation-design.md.
    """

    @asynccontextmanager
    async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
        await mcp_obj._force_vector_reconnect()
        mcp_obj._reconciliation_task = asyncio.create_task(mcp_obj.reconcile_vector_index())
        try:
            yield
        finally:
            task = mcp_obj._reconciliation_task
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return _lifespan


def app() -> FastMCP:  # pragma: no cover
    """Create and configure the FastMCP application instance."""
    mcp_obj = PriorisMCP()
    app = FastMCP(
        name=PACKAGE_NAME,
        version=package_version,
        instructions="A simple MCP server for testing purposes.",
        on_duplicate="error",
        lifespan=_vector_reconciliation_lifespan(mcp_obj),
    )
    app_with_features = mcp_obj.register_features(app)
    app_with_features.add_middleware(StripUnknownArgumentsMiddleware())
    # LiveResourceCacheBypassMiddleware runs before the Encode/Decode sandwich below and fully
    # bypasses it for notes:// URIs and the rebuild-status resource, dispatching straight to the
    # resource handler with run_middleware=False so mutable note content and live reconciliation
    # progress are never read from - or written to - the response cache.
    app_with_features.add_middleware(LiveResourceCacheBypassMiddleware())
    # Encode/DecodeBinaryResourceContentMiddleware sandwich ResponseCachingMiddleware: fastmcp's
    # cache wrapper JSON-serialises via Pydantic, whose default bytes encoding is a UTF-8 decode -
    # it crashes on non-UTF-8-safe resource content (e.g. a fetched PDF's fulltext resource).
    # FastMCP's middleware chain runs first-added-outermost (see _run_middleware in
    # fastmcp/server/server.py), so Decode (must see every read, hit or miss) is added before
    # ResponseCachingMiddleware, and Encode (must only run next to the real resource handler, on
    # a cache miss) is added after it - see middleware.py for the full rationale.
    app_with_features.add_middleware(DecodeBinaryResourceContentMiddleware())
    app_with_features.add_middleware(
        ResponseCachingMiddleware(
            list_tools_settings=ListToolsSettings(
                ttl=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL,
                enabled=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL > 0,
            ),
            list_prompts_settings=ListPromptsSettings(
                ttl=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL,
                enabled=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL > 0,
            ),
            list_resources_settings=ListResourcesSettings(
                ttl=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL,
                enabled=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL > 0,
            ),
            call_tool_settings=CallToolSettings(
                # research_localfile_* and research_list_fetched/research_delete_fetched are
                # deliberately excluded: the local file source re-hashes file content on every
                # call by design (a file can change on disk without notice - see
                # docs/requirement-specification/01-architecture.md#local-filesystem-source), and
                # list/delete must reflect live storage state, not a cached snapshot.
                included_tools=[
                    "research_arxiv_search",
                    "research_arxiv_list_top_n",
                    "research_arxiv_fetch_metadata",
                    "research_arxiv_fetch_full_text",
                    "research_arxiv_parse_full_text",
                    "research_europepmc_search",
                    "research_europepmc_fetch_metadata",
                    "research_europepmc_fetch_full_text",
                    "research_europepmc_parse_full_text",
                    "research_resolve_identifier",
                    "research_discovery",
                ],
                ttl=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL,
                enabled=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL > 0,
            ),
            get_prompt_settings=GetPromptSettings(
                ttl=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL,
                enabled=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL > 0,
            ),
            read_resource_settings=ReadResourceSettings(
                ttl=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL,
                enabled=EnvVars.PRIORIS_MCP_RESPONSE_CACHE_TTL > 0,
            ),
        )
    )
    app_with_features.add_middleware(EncodeBinaryResourceContentMiddleware())
    # The last middleware must be the one to attach response metadata
    app_with_features.add_middleware(ResponseMetadataMiddleware())
    return app_with_features


def main():  # pragma: no cover
    """Main entry point to run the FastMCP server."""
    try:
        # Run the FastMCP server using stdio by default.
        # Other transports can be configured as needed using the MCP_SERVER_TRANSPORT environment variable.
        mcp_app = app()
        transport_type = EnvVars.PRIORIS_MCP_TRANSPORT
        if transport_type != "stdio":
            # Configure CORS for browser-based clients, see: https://gofastmcp.com/deployment/http#cors-for-browser-based-clients
            middleware = [
                Middleware(
                    CORSMiddleware,
                    allow_origins=EnvVars.PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS,
                    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                    allow_headers=[
                        "mcp-protocol-version",
                        "mcp-session-id",
                        "Authorization",
                        "Content-Type",
                    ],
                    expose_headers=["mcp-session-id"],
                ),
            ]

            asgi_app = mcp_app.http_app(middleware=middleware, transport=transport_type)
            uvicorn.run(
                asgi_app,
                host=EnvVars.PRIORIS_MCP_HOST,
                port=EnvVars.PRIORIS_MCP_PORT,
                timeout_graceful_shutdown=5,  # seconds
            )
        else:
            mcp_app.run(transport=transport_type)
    except KeyboardInterrupt:
        sys.exit(0)
    finally:
        # Cleanup if necessary
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
