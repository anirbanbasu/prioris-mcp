import logging
import sqlite3
import sys
from importlib.metadata import version
from typing import Annotated, ClassVar, Literal

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
from prioris_mcp.errors import InvalidRequestError, NotFoundError
from prioris_mcp.middleware import (
    DecodeBinaryResourceContentMiddleware,
    EncodeBinaryResourceContentMiddleware,
    NotesCacheBypassMiddleware,
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
    ParsedFullText,
    ResolvedIdentifierResult,
    SearchFetchedResult,
    SearchMatch,
)
from prioris_mcp.models.europepmc import EuropePmcFetchMetadataResult, EuropePmcSearchResult
from prioris_mcp.models.localfile import LocalFileBeginUploadResult, LocalFileFetchResult, LocalFileUploadChunkResult
from prioris_mcp.models.notes import Anchor, AuthorFilter, Note, PagedNotes
from prioris_mcp.models.vector import VectorSearchMatch
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
from prioris_mcp.vector.embedding import FastEmbedBackend
from prioris_mcp.vector.mechanism import FtsMechanism, SearchMechanism, VectorMechanism
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
        {"fn": "read_notes_export_resource", "uri": "notes://{note_id}/export"},
    ]

    def __init__(self) -> None:
        storage_dir = grouping_dir(EnvVars.PRIORIS_MCP_STORAGE_DIR, DEFAULT_GROUPING)
        self._storage = FilesystemStorageBackend(storage_dir)
        self._search_index = SqliteFts5SearchIndex(storage_dir / "search.sqlite3")
        # Minimal/temporary construction - Task 14 reconciles this into a fuller wiring pass that
        # also covers search-mechanism registries. Kept here (rather than left unwired) so Task 8's
        # background-indexing trigger in the providers below, and this task's note-vector
        # scheduling/cascade, aren't dead code in the meantime. `_search_mechanisms` (Task 12) is
        # also part of this temporary block: it reuses the local `embedding_backend` below rather
        # than the not-yet-promoted `self._embedding_backend` that Task 14 will introduce.
        vector_dir = grouping_dir(EnvVars.PRIORIS_MCP_VECTOR_DIR, DEFAULT_GROUPING)
        embedding_backend = FastEmbedBackend(EnvVars.PRIORIS_MCP_EMBEDDING_MODEL)
        self._document_vector_backend = SqliteVecDocumentBackend(vector_dir / "vectors.sqlite3", embedding_backend)
        self._note_vector_backend = SqliteVecNoteBackend(vector_dir / "notes-vectors.sqlite3", embedding_backend)
        self._embedding_scheduler = EmbeddingScheduler()
        self._search_mechanisms: dict[str, SearchMechanism] = {
            "fts": FtsMechanism(self._search_index),
            "vector": VectorMechanism(self._document_vector_backend, embedding_backend),
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
    ) -> ListFetchedResult:
        """Enumerate persisted (provider, identifier, format) manifest entries; never triggers a fetch."""
        entries = await self._storage.list(provider, format)
        return ListFetchedResult(entries=entries)

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
            str, Field(default="fts", description="A registered mechanism name (e.g. 'fts', 'vector'), or 'hybrid'")
        ] = "fts",
    ) -> SearchFetchedResult:
        """Search previously-persisted chunks; never fetches or parses. See mode for mechanism choice."""
        if identifier is not None and provider is None:
            raise InvalidRequestError("identifier requires provider")
        if mode == "hybrid":
            selected = list(self._search_mechanisms.values())
        else:
            mechanism = self._search_mechanisms.get(mode)
            if mechanism is None:
                raise InvalidRequestError(f"unknown or unavailable mode: {mode!r}")
            selected = [mechanism]

        results: dict[str, list] = {}
        for mechanism in selected:
            try:
                raw = await mechanism.search(query, provider=provider, identifier=identifier, format=format, limit=10)
            except sqlite3.OperationalError as exc:
                raise InvalidRequestError(f"invalid search query: {exc}") from exc
            results[mechanism.name] = raw

        index_status: dict[str, str] = {}
        if identifier is not None and provider is not None:
            for mechanism in self._search_mechanisms.values():
                index_status[mechanism.name] = await mechanism.status(provider, identifier, format or "")

        return SearchFetchedResult(
            fts=[SearchMatch(**m) for m in results["fts"]] if "fts" in results else None,
            vector=[VectorSearchMatch(**m) for m in results["vector"]] if "vector" in results else None,
            index_status=index_status,
        )

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
            await self._note_vector_backend.remove_note(note_id)
        return removed

    async def research_notes_search(
        self,
        ctx: Context,
        provider: Annotated[Literal["arxiv", "europepmc", "localfile"] | None, Field(default=None)] = None,
        canonical_identifier: Annotated[str | None, Field(default=None)] = None,
        format: Annotated[str | None, Field(default=None)] = None,
        date_from: Annotated[str | None, Field(default=None, description="ISO 8601")] = None,
        date_to: Annotated[str | None, Field(default=None, description="ISO 8601")] = None,
        keyword: Annotated[str | None, Field(default=None, description="FTS5 query syntax over note text")] = None,
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
    ) -> PagedNotes:
        """Search/list notes; no filters at all returns everything, paged, newest first."""
        try:
            return await self._notes_backend.search(
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

    async def _delete_fetched(self, entries: list[DeleteEntryRef]) -> DeleteFetchedResult:
        deleted: list[DeleteEntryRef] = []
        not_found: list[DeleteEntryRef] = []
        for entry in entries:
            removed = await self._storage.delete(entry.provider, entry.identifier, entry.format_, entry.artefact)
            if removed and entry.artefact in ("markdown", "all"):
                await self._search_index.remove_document(entry.provider, entry.identifier, entry.format_)
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

    async def read_notes_export_resource(self, note_id: str) -> str:
        """Read one note's file representation, for the caller to write to disk itself.

        Returns the `NoteExport` serialised to JSON - see `read_markdown_resource` for why.
        `frontmatter` is a plain JSON object, not pre-rendered YAML - the caller renders it into
        whatever frontmatter dialect its target tool expects before writing `markdown_body` to a
        file.
        """
        return (await self._notes_backend.export(note_id)).model_dump_json()


def app() -> FastMCP:  # pragma: no cover
    """Create and configure the FastMCP application instance."""
    app = FastMCP(
        name=PACKAGE_NAME,
        version=package_version,
        instructions="A simple MCP server for testing purposes.",
        on_duplicate="error",
    )
    mcp_obj = PriorisMCP()
    app_with_features = mcp_obj.register_features(app)
    app_with_features.add_middleware(StripUnknownArgumentsMiddleware())
    # NotesCacheBypassMiddleware runs before the Encode/Decode sandwich below and fully bypasses
    # it for notes:// URIs, dispatching straight to the resource handler with run_middleware=False
    # so mutable note content is never read from - or written to - the response cache.
    app_with_features.add_middleware(NotesCacheBypassMiddleware())
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
