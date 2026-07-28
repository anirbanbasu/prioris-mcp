import logging
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
from prioris_mcp.errors import call_returning_envelope
from prioris_mcp.middleware import ResponseMetadataMiddleware, StripUnknownArgumentsMiddleware
from prioris_mcp.mixin import MCPMixin
from prioris_mcp.pagination import paginate_text
from prioris_mcp.parsers.html_markdownify import MarkdownifyHtmlBackend
from prioris_mcp.parsers.jats_xslt import JatsXsltMarkdownBackend
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend
from prioris_mcp.providers.arxiv import ARXIV_BASE_SPACING_SECONDS, ArxivProvider
from prioris_mcp.providers.europepmc import EUROPEPMC_BASE_SPACING_SECONDS, EuropePmcProvider
from prioris_mcp.providers.identifier_routing import resolve_research_identifier
from prioris_mcp.rate_limit import ProviderRequestQueue
from prioris_mcp.storage import FilesystemStorageBackend

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
    ]

    resources: ClassVar[list[dict]] = [
        {"fn": "read_fulltext_resource", "uri": "research://{provider}/{identifier}/{format}/fulltext"},
        {"fn": "read_markdown_resource", "uri": "research://{provider}/{identifier}/{format}/markdown{?offset,limit}"},
        {"fn": "read_arxiv_categories_resource", "uri": "research://arxiv/categories"},
    ]

    def __init__(self) -> None:
        self._storage = FilesystemStorageBackend()
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
        html_backend = MarkdownifyHtmlBackend()
        self._arxiv_provider = ArxivProvider(
            storage=self._storage,
            queue=arxiv_queue,
            http_client=self._http_client,
            pdf_backend=pdf_backend,
            html_backend=html_backend,
            default_inline_char_limit=EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS,
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
            default_inline_char_limit=EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS,
        )

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
    ) -> dict:
        """Search arXiv by keyword/query, returning metadata records."""
        return await call_returning_envelope(
            self._arxiv_provider.search(
                query, max_results=max_results, start=start, sort_by=sort_by, sort_order=sort_order
            )
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
    ) -> dict:
        """List the N most recently submitted arXiv items across one or more subject categories."""
        return await call_returning_envelope(
            self._arxiv_provider.list_top_n(include_categories, n, exclude_categories=exclude_categories)
        )

    async def research_arxiv_fetch_metadata(
        self,
        ctx: Context,
        arxiv_ids: Annotated[list[str], Field(description="One or more arXiv identifiers, version suffix optional")],
    ) -> dict:
        """Fetch metadata for one or more arXiv identifiers in a single call."""
        return await call_returning_envelope(self._arxiv_provider.fetch_metadata(arxiv_ids))

    async def research_arxiv_fetch_full_text(
        self,
        ctx: Context,
        arxiv_id: Annotated[str, Field(description="An arXiv identifier, version suffix optional")],
        format: Annotated[Literal["pdf", "html"], Field(description="Full-text format to fetch")],
    ) -> dict:
        """Fetch (or return the already-persisted) full text for an arXiv item."""
        return await call_returning_envelope(self._arxiv_provider.fetch_full_text(arxiv_id, format))

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
    ) -> dict:
        """Convert already-fetched arXiv full text into one page of Markdown."""
        return await call_returning_envelope(
            self._arxiv_provider.parse_full_text(arxiv_id, format, offset=offset, limit=limit)
        )

    async def research_europepmc_search(
        self,
        ctx: Context,
        query: Annotated[str, Field(description="Europe PMC query syntax, e.g. 'field:value AND field:value'")],
        page_size: Annotated[int, Field(default=25)] = 25,
        cursor_mark: Annotated[str, Field(default="*", description="Europe PMC's opaque pagination cursor")] = "*",
    ) -> dict:
        """Search Europe PMC by keyword/query, returning metadata records."""
        return await call_returning_envelope(
            self._europepmc_provider.search(query, page_size=page_size, cursor_mark=cursor_mark)
        )

    async def research_europepmc_fetch_metadata(
        self,
        ctx: Context,
        identifiers: Annotated[list[str], Field(description="One or more Europe PMC identifiers or bare PMCIDs")],
    ) -> dict:
        """Fetch metadata for one or more Europe PMC identifiers in a single call."""
        return await call_returning_envelope(self._europepmc_provider.fetch_metadata(identifiers))

    async def research_europepmc_fetch_full_text(
        self,
        ctx: Context,
        identifier: Annotated[str, Field(description="A Europe PMC identifier or bare PMCID")],
    ) -> dict:
        """Fetch (or return the already-persisted) JATS XML full text for a Europe PMC item."""
        return await call_returning_envelope(self._europepmc_provider.fetch_full_text(identifier))

    async def research_europepmc_parse_full_text(
        self,
        ctx: Context,
        identifier: Annotated[str, Field(description="A Europe PMC identifier or bare PMCID")],
        offset: Annotated[int, Field(default=0, description="Zero-based character offset into the Markdown")] = 0,
        limit: Annotated[
            int | None,
            Field(default=None, description="Max Markdown characters to return; defaults to a server-side cap"),
        ] = None,
    ) -> dict:
        """Convert already-fetched Europe PMC JATS XML full text into one page of Markdown."""
        return await call_returning_envelope(
            self._europepmc_provider.parse_full_text(identifier, offset=offset, limit=limit)
        )

    async def research_resolve_identifier(
        self,
        ctx: Context,
        identifier: Annotated[str, Field(description="An arXiv ID, a Europe PMC identifier, or a DOI")],
        format: Annotated[
            str, Field(description="Desired target format; valid values depend on the resolving provider")
        ],
    ) -> dict:
        """Resolve an identifier of unknown provider (including DOIs) to its owning provider and URL."""
        return await call_returning_envelope(
            resolve_research_identifier(
                identifier, format, self._http_client, self._arxiv_provider, self._europepmc_provider
            )
        )

    async def read_fulltext_resource(self, provider: str, identifier: str, format: str) -> bytes:
        """Read persisted full text for (provider, identifier, format); a plain not-found if absent."""
        return await self._storage.read(provider, identifier, format)

    async def read_markdown_resource(
        self, provider: str, identifier: str, format: str, offset: int = 0, limit: int | None = None
    ) -> dict:
        """Read one page of persisted parsed Markdown for (provider, identifier, format).

        A plain not-found if absent. Paginated the same way as `parse_full_text` - see
        docs/requirement-specification/04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole
        - `limit` defaults to `PRIORIS_MCP_MAX_INLINE_CHARS` when unset.
        """
        markdown_bytes = await self._storage.read(provider, identifier, f"{format}-markdown")
        page = paginate_text(
            markdown_bytes.decode("utf-8"), offset, limit if limit is not None else EnvVars.PRIORIS_MCP_MAX_INLINE_CHARS
        )
        return {
            "markdown": page["content"],
            "offset": page["offset"],
            "limit": page["limit"],
            "total_length": page["total_length"],
            "has_more": page["has_more"],
        }

    async def read_arxiv_categories_resource(self) -> dict:
        """Read arXiv's queryable category codes and names, for `research_arxiv_list_top_n`/`research_arxiv_search`."""
        return await self._arxiv_provider.list_categories()


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
