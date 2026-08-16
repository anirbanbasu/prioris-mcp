import asyncio
import base64
import contextlib
import json
import logging
import ssl
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError
from mcp.types import TextResourceContents

from prioris_mcp import EnvVars
from prioris_mcp.errors import InvalidRequestError
from prioris_mcp.models.arxiv import ArxivCategoriesResult, ArxivCategory
from prioris_mcp.models.common import ArxivResolvedIdentifierResult, MarkdownPage
from prioris_mcp.models.discovery import OpenAlexWorkType, OpenAlexWorkTypesResult
from prioris_mcp.server import PriorisMCP, _vector_reconciliation_lifespan, app
from prioris_mcp.vector.embedding import EmbeddingBackend, FastEmbedBackend
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecDocumentBackend, SqliteVecNoteBackend

logger = logging.getLogger(__name__)


class _RenamedStub(EmbeddingBackend):
    """A same-dimension, differently-named EmbeddingBackend wrapping a real one - for simulating a model rename."""

    def __init__(self, real: EmbeddingBackend, model_name: str) -> None:
        self._real = real
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._real.dimension

    @property
    def max_chunk_chars(self) -> int:
        return self._real.max_chunk_chars

    async def embed(self, text: str) -> list[float]:
        return await self._real.embed(text)


class _DifferentDimensionStub(EmbeddingBackend):
    """A differently-named, differently-dimensioned EmbeddingBackend - for simulating a genuine dimension-changing model swap.

    Deliberately does not wrap a real backend (unlike `_RenamedStub`, which is for the
    same-dimension rename case): `embed()` only needs to produce a fixed-length vector matching
    `self._dim`, mirroring test_vector_sqlite_vec_document_backend.py's `_StubEmbedding` pattern
    used for the same purpose - avoids spinning up two real fastembed models just to exercise a
    dimension change.
    """

    def __init__(self, model_name: str, dimension: int) -> None:
        self._model_name = model_name
        self._dim = dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def max_chunk_chars(self) -> int:
        return 2000

    async def embed(self, text: str) -> list[float]:
        return [0.1] * self._dim


class TestMCPServer:
    """Test suite for the MCP server features."""

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def mcp_server(cls):
        """Fixture to register features in an MCP server."""
        server = FastMCP()
        mcp_obj = PriorisMCP()
        server_with_features = mcp_obj.register_features(server)
        return server_with_features

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def mcp_client(cls, mcp_server):
        """Fixture to create a client for the MCP server."""
        mcp_client = Client(
            transport=mcp_server,
            timeout=60,
        )
        return mcp_client

    async def call_tool(self, tool_name: str, mcp_client: Client, **kwargs):
        """Helper method to call a tool on the MCP server."""
        async with mcp_client:
            result = await mcp_client.call_tool(tool_name, arguments=kwargs)
            await mcp_client.close()
        return result

    async def read_resource(self, resource_name: str, mcp_client: Client):
        """Helper method to load a resource from the MCP server."""
        async with mcp_client:
            result = await mcp_client.read_resource(resource_name)
            await mcp_client.close()
        return result

    async def get_prompt(self, prompt_name: str, mcp_client: Client, **kwargs):
        """Helper method to get a prompt from the MCP server."""
        async with mcp_client:
            result = await mcp_client.get_prompt(prompt_name, arguments=kwargs)
            await mcp_client.close()
        return result

    def test_outbound_http_client_follows_redirects(self):
        """`httpx.AsyncClient()` defaults `follow_redirects` to False.

        Without `follow_redirects=True`, a 3xx from e.g. arXiv's `/pdf/{id}` endpoint would be
        passed through by `providers/http.request` (it only special-cases 429/5xx) and silently
        persisted as if it were the actual document - see
        tests/test_providers_arxiv.py::TestArxivProviderFetchFullText::test_redirect_is_followed_and_final_content_persisted
        for the end-to-end consequence.
        """
        assert PriorisMCP()._http_client.follow_redirects is True

    def test_outbound_http_client_uses_configured_timeout(self, monkeypatch: "pytest.MonkeyPatch"):
        """Verify the client is built with the configured timeout, not httpx's own default.

        httpx.AsyncClient()'s own default (5s) is too tight for arXiv/Europe PMC under load - see
        issue #2 - so the client must be built with PRIORIS_MCP_HTTP_TIMEOUT_SECONDS instead.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_HTTP_TIMEOUT_SECONDS", 45.0)
        assert PriorisMCP()._http_client.timeout == httpx.Timeout(45.0)

    def test_providers_use_configured_max_inline_chars(self, monkeypatch: "pytest.MonkeyPatch"):
        """Verify both providers are built with the configured inline-text limit.

        A large parsed PDF/HTML/XML can otherwise be returned whole in a tool response and exceed
        an MCP client's own max-tokens-per-result ceiling - see issue #1 - so both providers must
        be wired to `PRIORIS_MCP_MAX_INLINE_CHARS`, not an unconfigurable default.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_MAX_INLINE_CHARS", 12345)
        mcp_obj = PriorisMCP()
        assert mcp_obj._arxiv_provider._default_inline_char_limit == 12345
        assert mcp_obj._europepmc_provider._default_inline_char_limit == 12345

    def test_outbound_http_client_trusts_env_for_proxy_and_ca_bundle(self):
        """`trust_env` (on by default) is what makes httpx honour `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and `SSL_CERT_FILE`/`SSL_CERT_DIR`.

        See
        docs/requirement-specification/05-security.md#egress-through-a-organisational-https-inspecting-proxy.
        This guards against a future refactor silently passing `trust_env=False`.
        """
        assert PriorisMCP()._http_client.trust_env is True

    def test_outbound_http_client_verifies_https_by_default(self):
        """Default configuration must verify upstream HTTPS certificates."""
        transport = PriorisMCP()._http_client._transport
        ssl_context = transport._pool._ssl_context  # ty: ignore[unresolved-attribute]
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED

    def test_unverified_https_env_var_disables_certificate_verification(self, monkeypatch: "pytest.MonkeyPatch"):
        """`PRIORIS_MCP_UNVERIFIED_HTTPS=True` must actually disable verification, not just be documented."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_UNVERIFIED_HTTPS", True)
        transport = PriorisMCP()._http_client._transport
        ssl_context = transport._pool._ssl_context  # ty: ignore[unresolved-attribute]
        assert ssl_context.verify_mode == ssl.CERT_NONE

    def test_unverified_https_env_var_logs_warning(self, caplog: "pytest.LogCaptureFixture", monkeypatch):
        """Enabling unverified HTTPS must be loud, not a silent behavioural change."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_UNVERIFIED_HTTPS", True)
        with caplog.at_level(logging.WARNING):
            PriorisMCP()
        assert "HTTPS certificate verification is DISABLED" in caplog.text

    def test_registered_tool_names_include_localfile_and_storage_management(self):
        """docs/requirement-specification/07-test-specification.md's cross-cutting acceptance criteria.

        The registered tool metadata list must include the local-file and storage-management
        tools, and must not include the capabilities this source deliberately lacks (search,
        fetch_metadata, list_top_n - a local file has no catalogue to search or list).
        """
        tool_names = {t["fn"] for t in PriorisMCP.tools}
        assert "research_localfile_fetch_full_text" in tool_names
        assert "research_localfile_parse_full_text" in tool_names
        assert "research_localfile_begin_upload" in tool_names
        assert "research_localfile_upload_chunk" in tool_names
        assert "research_localfile_finalize_upload" in tool_names
        assert "research_list_fetched" in tool_names
        assert "research_delete_fetched" in tool_names
        assert "research_localfile_search" not in tool_names
        assert "research_localfile_fetch_metadata" not in tool_names
        assert "research_localfile_list_top_n" not in tool_names

    def test_localfile_provider_uses_configured_upload_limits(self, monkeypatch: "pytest.MonkeyPatch"):
        """The chunked-upload session manager must be built from EnvVars, not hardcoded defaults."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_LOCAL_FILE_UPLOAD_SESSION_TTL_SECONDS", 42.0)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES", 4096)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CONCURRENT_SESSIONS", 3)
        mcp_obj = PriorisMCP()
        upload_sessions = mcp_obj._localfile_provider._upload_sessions
        assert upload_sessions._ttl_seconds == 42.0
        assert upload_sessions._max_chunk_bytes == 4096
        assert upload_sessions._max_concurrent == 3


class TestNotesBackendWiring:
    """Tests for the NotesBackend/NotesSearchIndex wiring in `PriorisMCP.__init__`."""

    def test_notes_backend_and_search_index_are_constructed(self):
        server = PriorisMCP()
        assert server._notes_backend is not None
        assert server._notes_search_index is not None

    def test_resolve_canonical_identifier_arxiv_pinned_id_needs_no_network_call(self):
        """A version-pinned id short-circuits ArxivProvider.resolve_identifier's own network path.

        The `_is_version_pinned` check means this exercises real resolution without mocking HTTP -
        mirrors tests/test_providers_arxiv.py::TestArxivProviderResolveIdentifier's own convention
        of using a genuinely unmocked call for the version-pinned case.
        """
        server = PriorisMCP()
        canonical = asyncio.run(server._resolve_canonical_identifier_for_notes("arxiv", "2106.09685v2", "pdf"))
        assert canonical == "2106.09685v2"

    def test_resolve_canonical_identifier_europepmc_delegates_to_provider_resolve_identifier(self):
        """The europepmc branch always makes a network call, unlike arXiv's version-pinned shortcut.

        `EuropePmcProvider.resolve_identifier` always calls `fetch_metadata`, so this stubs the
        HTTP client the same way `TestEuropePmcTools._server_and_client` does elsewhere in this
        file, rather than hitting the network.
        """
        import json

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "hitCount": 1,
                        "resultList": {
                            "result": [
                                {
                                    "id": "26551875",
                                    "source": "MED",
                                    "pmid": "26551875",
                                    "pmcid": "PMC4767193",
                                    "title": "A Paper",
                                    "inEPMC": "Y",
                                }
                            ]
                        },
                    }
                ).encode("utf-8"),
            )

        server = PriorisMCP()
        server._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        server._europepmc_provider._http_client = server._http_client
        canonical = asyncio.run(server._resolve_canonical_identifier_for_notes("europepmc", "MED:26551875", "xml"))
        assert canonical == "PMC:4767193"

    def test_resolve_canonical_identifier_localfile_passes_through_unchanged(self):
        server = PriorisMCP()
        canonical = asyncio.run(
            server._resolve_canonical_identifier_for_notes("localfile", "20260729-1430-a3f2", "pdf")
        )
        assert canonical == "20260729-1430-a3f2"

    def test_resolve_canonical_identifier_skips_resolution_when_format_is_none(self):
        server = PriorisMCP()
        canonical = asyncio.run(server._resolve_canonical_identifier_for_notes("arxiv", "2106.09685", None))
        assert canonical == "2106.09685"

    def test_resolve_canonical_identifier_rejects_unknown_provider(self):
        server = PriorisMCP()
        with pytest.raises(InvalidRequestError):
            asyncio.run(server._resolve_canonical_identifier_for_notes("not-a-real-provider", "x", "pdf"))


class TestArxivTools:
    """End-to-end MCP tool tests for the arXiv provider, stubbing arXiv's HTTP API."""

    def _feed(self, arxiv_id: str = "2106.09685v2") -> bytes:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/{arxiv_id}</id>
    <published>2021-06-17T17:59:33Z</published>
    <updated>2021-10-16T13:56:12Z</updated>
    <title>A Paper</title>
    <summary>An abstract.</summary>
    <author><name>Jane Doe</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <link href="http://arxiv.org/pdf/{arxiv_id}" rel="related" type="application/pdf"/>
  </entry>
</feed>""".encode()

    def _categories_feed(self) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-07-28T00:00:00Z</responseDate>
  <request verb="ListSets">http://oaipmh.arxiv.org/oai</request>
  <ListSets>
    <set><setSpec>physics</setSpec><setName>Physics</setName></set>
    <set><setSpec>physics:hep-th</setSpec><setName>High Energy Physics - Theory</setName></set>
  </ListSets>
</OAI-PMH>"""

    def _server_and_client(self, handler, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        # `monkeypatch.setattr` (not `setenv` + `importlib.reload`) is the pattern already used
        # by tests/test_storage.py for the same problem: `PRIORIS_MCP_STORAGE_DIR` is resolved
        # into an `EnvVars` class attribute once, at process-import time, not re-read from the
        # environment per call - a `setenv` alone would not affect it. Patching the attribute
        # directly avoids that, and avoids `importlib.reload`, which mutates the live
        # `prioris_mcp`/`prioris_mcp.storage` module objects for the rest of the test session
        # and leaks into unrelated test files (confirmed: it broke tests/test_storage.py when
        # tried).
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path)
        mcp_obj = PriorisMCP()
        mcp_obj._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp_obj._arxiv_provider._http_client = mcp_obj._http_client
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_research_arxiv_search_returns_results(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_arxiv_search", arguments={"query": "cat:cs.CL"})

        result = asyncio.run(scenario())
        assert result.structured_content["total_results"] == 1

    def test_research_arxiv_fetch_full_text_then_read_resource(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.params.get("id_list"):
                return httpx.Response(200, content=self._feed())
            return httpx.Response(200, content=b"%PDF-1.4 fake bytes")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "pdf"}
                )
                resource_result = await client.read_resource("research://arxiv/2106.09685v2/pdf/fulltext")
                return fetch_result, resource_result

        fetch_result, resource_result = asyncio.run(scenario())
        assert fetch_result.structured_content["served_from_storage"] is False
        assert len(resource_result) == 1

    def test_fetch_full_text_old_style_slash_id_resource_uri_round_trips(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Regression test for the resource-URI round-trip bug with pre-2007 archive/number ids.

        `hep-th/9901001v1` is version-pinned (see `_is_version_pinned`), so `fetch_full_text`
        never issues a `fetch_metadata` call for it - the handler only ever needs to answer the
        full-text GET. The returned `resource_uri` must contain the identifier's "/" percent-
        encoded (`%2F`) as a single opaque `{identifier}` path segment; if it didn't, a client
        calling `read_resource` with the exact URI string PriorisMCP handed back would fail to
        match the `research://{provider}/{identifier}/{format}/fulltext` template as one segment.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.4 old style fake bytes")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "hep-th/9901001v1", "format": "pdf"}
                )
                resource_uri = fetch_result.structured_content["resource_uri"]
                resource_result = await client.read_resource(resource_uri)
                return resource_uri, resource_result

        resource_uri, resource_result = asyncio.run(scenario())
        assert resource_uri == "research://arxiv/hep-th%2F9901001v1/pdf/fulltext"
        blob = resource_result[0].blob
        assert base64.b64decode(blob) == b"%PDF-1.4 old style fake bytes"

    def test_research_arxiv_list_top_n_returns_results(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_arxiv_list_top_n", arguments={"include_categories": ["cs.CL"], "n": 5}
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["results"]) == 1

    def test_research_arxiv_list_top_n_and_joins_multiple_include_categories(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.params["search_query"] == "cat:cs.CL AND cat:cs.LG"
            return httpx.Response(200, content=self._feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_arxiv_list_top_n", arguments={"include_categories": ["cs.CL", "cs.LG"], "n": 5}
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["results"]) == 1

    def test_research_arxiv_list_top_n_andnot_joins_exclude_categories(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.params["search_query"] == "cat:cs.CL ANDNOT cat:cs.CV"
            return httpx.Response(200, content=self._feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_arxiv_list_top_n",
                    arguments={"include_categories": ["cs.CL"], "n": 5, "exclude_categories": ["cs.CV"]},
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["results"]) == 1

    def test_research_arxiv_list_top_n_empty_include_categories_is_invalid_request(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_arxiv_list_top_n", arguments={"include_categories": [], "n": 5})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_research_arxiv_categories_resource_returns_leaf_categories(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._categories_feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.read_resource("research://arxiv/categories")

        result = asyncio.run(scenario())
        assert ArxivCategoriesResult.model_validate_json(result[0].text) == ArxivCategoriesResult(
            categories=[ArxivCategory(code="hep-th", name="High Energy Physics - Theory")]
        )

    def test_research_arxiv_fetch_metadata_returns_results(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_arxiv_fetch_metadata", arguments={"arxiv_ids": ["2106.09685v2"]}
                )

        result = asyncio.run(scenario())
        assert result.structured_content["not_found"] == []

    def test_research_arxiv_parse_full_text_then_read_markdown_resource(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html><body><p>Hello world</p></body></html>")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "html"}
                )
                parse_result = await client.call_tool(
                    "research_arxiv_parse_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "html"}
                )
                resource_result = await client.read_resource("research://arxiv/2106.09685v2/html/markdown")
                return parse_result, resource_result

        parse_result, resource_result = asyncio.run(scenario())
        assert "Hello world" in parse_result.structured_content["markdown"]
        assert len(resource_result) == 1

    def test_research_arxiv_parse_full_text_honors_offset_and_limit(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html><body><p>Hello world</p></body></html>")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "html"}
                )
                return await client.call_tool(
                    "research_arxiv_parse_full_text",
                    arguments={"arxiv_id": "2106.09685v2", "format": "html", "offset": 6, "limit": 3},
                )

        result = asyncio.run(scenario())
        assert result.structured_content["markdown"] == "wor"
        assert result.structured_content["offset"] == 6
        assert result.structured_content["limit"] == 3
        assert result.structured_content["has_more"] is True

    def test_research_arxiv_markdown_resource_honors_offset_and_limit_query_params(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html><body><p>Hello world</p></body></html>")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "html"}
                )
                await client.call_tool(
                    "research_arxiv_parse_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "html"}
                )
                return await client.read_resource("research://arxiv/2106.09685v2/html/markdown?offset=6&limit=3")

        resource_result = asyncio.run(scenario())
        page = MarkdownPage.model_validate_json(resource_result[0].text)
        assert page.markdown == "wor"
        assert page.has_more is True

    def test_research_arxiv_parse_full_text_not_found_raises_tool_error(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_arxiv_parse_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "pdf"}
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_reading_unfetched_resource_is_a_plain_not_found_with_no_side_effect(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Reading a resource before the corresponding fetch/parse call must be a plain not-found.

        Per docs/requirement-specification/07-test-specification.md's Resources acceptance
        criteria: this must not be a crash, and critically, must never itself trigger a fetch or
        parse as a side effect of the read.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("read_resource must never itself trigger an outbound HTTP request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.read_resource("research://arxiv/2106.09685v2/pdf/fulltext")

        with pytest.raises(McpError):
            asyncio.run(scenario())

    def test_expected_resource_templates_are_registered(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        """Verify that research and notes resource templates are registered.

        Fulltext/markdown for research documents (documented in
        docs/requirement-specification/06-interface-specification.md#resources), and notes
        export.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.list_resource_templates()

        templates = asyncio.run(scenario())
        assert {t.uriTemplate for t in templates} == {
            "research://{provider}/{identifier}/{format}/fulltext",
            "research://{provider}/{identifier}/{format}/markdown{?offset,limit,page}",
            "notes://{note_id}/export",
        }

    def test_greet_tool_no_longer_registered(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._feed())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                tools = await client.list_tools()
                return {t.name for t in tools}

        names = asyncio.run(scenario())
        assert "greet" not in names
        assert "research_arxiv_search" in names


class TestArxivParseFullTextPageParam:
    """End-to-end MCP tool tests for the `page` param on research_arxiv_parse_full_text.

    Uses `format="pdf"` (not "html") because `page` is documented as pdf-only (page_aware=True
    only when format=="pdf" - see ArxivProvider.parse_full_text's docstring), and `format="pdf"`
    invokes the real, unmocked liteparse backend, so this needs a structurally valid single-page
    PDF, not just a magic-byte stub. Same fixture bytes as TestLocalFileTools._PDF_BYTES, copied
    (not cross-referenced) to keep these classes independent.
    """

    _PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 24 Tf 20 100 Td (Hello World) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF"""

    def _server_and_client(self, handler, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        # See TestArxivTools._server_and_client's comment for why monkeypatch.setattr (not
        # setenv) is required for PRIORIS_MCP_STORAGE_DIR.
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path)
        mcp_obj = PriorisMCP()
        mcp_obj._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp_obj._arxiv_provider._http_client = mcp_obj._http_client
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_page_param_returns_that_pages_markdown(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        # "2106.09685v2" is version-pinned, so resolve_identifier never issues a metadata lookup -
        # the handler only ever needs to answer the full-text GET, same as
        # test_fetch_full_text_old_style_slash_id_resource_uri_round_trips above.
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._PDF_BYTES)

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "pdf"}
                )
                return await client.call_tool(
                    "research_arxiv_parse_full_text",
                    arguments={"arxiv_id": "2106.09685v2", "format": "pdf", "page": 1},
                )

        result = asyncio.run(scenario())
        # The fixture PDF has exactly one page, so total_pages/page_range are page-consistent
        # with the requested page=1 - same expectation established for the equivalent provider-
        # level test in tests/test_providers_localfile.py (Task 14).
        assert result.structured_content["total_pages"] == 1
        assert result.structured_content["page_range"] == [1, 1]

    def test_page_with_html_format_returns_invalid_request_tool_error(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html><body><p>Hello world</p></body></html>")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_arxiv_fetch_full_text", arguments={"arxiv_id": "2106.09685v2", "format": "html"}
                )
                return await client.call_tool(
                    "research_arxiv_parse_full_text",
                    arguments={"arxiv_id": "2106.09685v2", "format": "html", "page": 1},
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())


class TestEuropePmcTools:
    """End-to-end MCP tool tests for the Europe PMC provider, stubbing its HTTP API."""

    def _search_payload(self) -> bytes:
        import json

        return json.dumps(
            {
                "hitCount": 1,
                "resultList": {
                    "result": [
                        {
                            "id": "26551875",
                            "source": "MED",
                            "pmid": "26551875",
                            "pmcid": "PMC4767193",
                            "title": "A Paper",
                            "inEPMC": "Y",
                        }
                    ]
                },
            }
        ).encode("utf-8")

    def _server_and_client(self, handler, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        # See TestArxivTools._server_and_client's comment: `monkeypatch.setattr` on the
        # `EnvVars` class attribute (not `setenv` + `importlib.reload`) is required here too -
        # `storage.py` did `from prioris_mcp import EnvVars`, a reference that a reload of the
        # `prioris_mcp` module alone does not update, so a previous test's `setenv`+reload
        # approach silently kept resolving `FilesystemStorageBackend`'s default `base_dir` to a
        # stale directory across tests once any test in this class began writing to storage
        # (`research_europepmc_fetch_full_text`/`research_europepmc_parse_full_text`).
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path)
        mcp_obj = PriorisMCP()
        mcp_obj._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp_obj._arxiv_provider._http_client = mcp_obj._http_client
        mcp_obj._europepmc_provider._http_client = mcp_obj._http_client
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_research_europepmc_search_returns_results(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._search_payload())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_europepmc_search", arguments={"query": "field:value"})

        result = asyncio.run(scenario())
        assert result.structured_content["hit_count"] == 1

    def test_no_list_top_n_tool_registered_for_europepmc(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._search_payload())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                tools = await client.list_tools()
                return {t.name for t in tools}

        names = asyncio.run(scenario())
        assert "research_europepmc_list_top_n" not in names
        assert "research_europepmc_search" in names
        assert "research_resolve_identifier" in names

    def test_research_resolve_identifier_routes_arxiv_id_directly(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._search_payload())

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_resolve_identifier", arguments={"identifier": "2106.09685v2", "format": "pdf"}
                )

        result = asyncio.run(scenario())
        assert (
            ArxivResolvedIdentifierResult.model_validate(result.structured_content["result"]).provider == "arxiv"
        )  # routed directly to arXiv provider, not Europe PMC

    def test_research_resolve_identifier_raises_tool_error_for_bad_format(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """`format` is intentionally not a `Literal[...]` at the tool schema level.

        Valid values depend on the resolving provider, so an unsupported value (here, `arXiv`'s bare
        `ValueError` for anything other than pdf/html, translated to `InvalidRequestError` by
        `providers.identifier_routing._resolve_identifier`) must surface as a `ToolError`, not
        succeed silently.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_resolve_identifier", arguments={"identifier": "2106.09685v2", "format": "xml"}
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_research_resolve_identifier_raises_tool_error_for_bad_format_routed_to_europepmc(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Same contract as the arXiv bad-format case above, exercised for the Europe PMC routing path.

        Europe PMC only ever serves XML full text, so `EuropePmcProvider.resolve_identifier`
        rejects anything else with a bare `ValueError` before any outbound call, translated to
        `InvalidRequestError` by `providers.identifier_routing._resolve_identifier` the same way
        as arXiv's own format check - and must surface as a `ToolError` here too.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_resolve_identifier", arguments={"identifier": "MED:26551875", "format": "pdf"}
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def _jats_feed(self) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.2 20190208//EN"
  "JATS-journalpublishing1.dtd">
<article article-type="research-article">
  <front>
    <article-meta>
      <title-group><article-title>A Test Article</article-title></title-group>
    </article-meta>
  </front>
  <body>
    <p>Hello, JATS world.</p>
  </body>
</article>
"""

    def _europepmc_handler(self, req: httpx.Request) -> httpx.Response:
        """Serves both the `search` JSON endpoint (metadata/resolve) and `fullTextXML`."""
        if "fullTextXML" in str(req.url):
            return httpx.Response(200, content=self._jats_feed())
        return httpx.Response(200, content=self._search_payload())

    def test_research_europepmc_fetch_metadata_returns_results(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(self._europepmc_handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_europepmc_fetch_metadata", arguments={"identifiers": ["MED:26551875"]}
                )

        result = asyncio.run(scenario())
        assert result.structured_content["not_found"] == []
        assert result.structured_content["results"][0]["identifier"] == "MED:26551875"

    def test_research_europepmc_fetch_full_text_then_read_resource(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(self._europepmc_handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_europepmc_fetch_full_text", arguments={"identifier": "PMC4767193"}
                )
                resource_result = await client.read_resource("research://europepmc/PMC:4767193/xml/fulltext")
                return fetch_result, resource_result

        fetch_result, resource_result = asyncio.run(scenario())
        assert fetch_result.structured_content["served_from_storage"] is False
        assert fetch_result.structured_content["resource_uri"] == "research://europepmc/PMC:4767193/xml/fulltext"
        assert len(resource_result) == 1

    def test_research_europepmc_parse_full_text_then_read_markdown_resource(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(self._europepmc_handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool("research_europepmc_fetch_full_text", arguments={"identifier": "PMC4767193"})
                parse_result = await client.call_tool(
                    "research_europepmc_parse_full_text", arguments={"identifier": "PMC4767193"}
                )
                resource_result = await client.read_resource("research://europepmc/PMC:4767193/xml/markdown")
                return parse_result, resource_result

        parse_result, resource_result = asyncio.run(scenario())
        assert "Hello, JATS world." in parse_result.structured_content["markdown"]
        assert len(resource_result) == 1

    def test_research_europepmc_parse_full_text_honors_offset_and_limit(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(self._europepmc_handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool("research_europepmc_fetch_full_text", arguments={"identifier": "PMC4767193"})
                return await client.call_tool(
                    "research_europepmc_parse_full_text",
                    arguments={"identifier": "PMC4767193", "offset": 0, "limit": 5},
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["markdown"]) == 5
        assert result.structured_content["offset"] == 0
        assert result.structured_content["limit"] == 5
        assert result.structured_content["has_more"] is True


class TestLocalFileTools:
    """End-to-end MCP tool tests for the local filesystem provider - no HTTP stubbing needed."""

    # A structurally valid minimal PDF, not just a "%PDF-" magic-prefix stub: unlike the other
    # tests in this module (which only need to survive fetch_full_text's magic-byte sniff),
    # test_fetch_then_parse_then_read_resources below also calls research_localfile_parse_full_text,
    # which feeds the content to the real, unmocked liteparse backend - see the identical fixture
    # and comment in tests/test_providers_localfile.py.
    _PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 24 Tf 20 100 Td (Hello World) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF"""

    _PDF_BASE64 = base64.b64encode(_PDF_BYTES).decode("ascii")

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_fetch_then_parse_then_read_resources(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": self._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                fulltext_resource = await client.read_resource(f"research://localfile/{caller_facing_id}/pdf/fulltext")
                parse_result = await client.call_tool(
                    "research_localfile_parse_full_text", arguments={"id": caller_facing_id}
                )
                markdown_resource = await client.read_resource(f"research://localfile/{caller_facing_id}/pdf/markdown")
                return fetch_result, fulltext_resource, parse_result, markdown_resource

        fetch_result, fulltext_resource, parse_result, markdown_resource = asyncio.run(scenario())
        assert fetch_result.structured_content["served_from_storage"] is False
        assert fulltext_resource[0].blob or fulltext_resource[0].text
        assert "markdown" in parse_result.structured_content
        assert markdown_resource[0].text is not None

    def test_invalid_base64_fails_invalid_request(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_localfile_fetch_full_text", arguments={"content_base64": "not-valid-base64!!!"}
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_reading_resource_with_unrecognised_localfile_id_is_not_found(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.read_resource("research://localfile/20260729-1430-a3f2/pdf/fulltext")

        with pytest.raises(McpError):
            asyncio.run(scenario())

    def _upload_in_chunks(self, client: Client, content: bytes, chunk_size: int, filename: str | None = None):
        async def scenario():
            async with client:
                begin_result = await client.call_tool(
                    "research_localfile_begin_upload", arguments={"filename": filename}
                )
                assert begin_result.structured_content is not None
                session_id = begin_result.structured_content["session_id"]
                for index, offset in enumerate(range(0, len(content), chunk_size)):
                    chunk = content[offset : offset + chunk_size]
                    await client.call_tool(
                        "research_localfile_upload_chunk",
                        arguments={
                            "session_id": session_id,
                            "index": index,
                            "chunk_base64": base64.b64encode(chunk).decode("ascii"),
                        },
                    )
                return await client.call_tool(
                    "research_localfile_finalize_upload", arguments={"session_id": session_id}
                )

        return asyncio.run(scenario())

    def test_begin_upload_returns_max_chunk_bytes(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES", 4096)
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_localfile_begin_upload", arguments={})

        result = asyncio.run(scenario())
        assert result.structured_content["max_chunk_bytes"] == 4096

    def test_chunked_upload_happy_path_matches_single_call_result(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)
        finalize_result = self._upload_in_chunks(client, self._PDF_BYTES, 20, filename="paper.pdf")
        assert finalize_result.structured_content["format"] == "pdf"
        assert finalize_result.structured_content["size_bytes"] == len(self._PDF_BYTES)
        assert finalize_result.structured_content["served_from_storage"] is False

    def test_chunked_upload_parse_full_text_works_on_finalized_content(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)
        finalize_result = self._upload_in_chunks(client, self._PDF_BYTES, 20, filename="paper.pdf")
        caller_facing_id = finalize_result.structured_content["id"]

        async def scenario():
            async with client:
                return await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})

        parse_result = asyncio.run(scenario())
        assert "markdown" in parse_result.structured_content

    def test_upload_chunk_out_of_order_fails_invalid_request(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                begin_result = await client.call_tool("research_localfile_begin_upload", arguments={})
                session_id = begin_result.structured_content["session_id"]
                await client.call_tool(
                    "research_localfile_upload_chunk",
                    arguments={
                        "session_id": session_id,
                        "index": 0,
                        "chunk_base64": base64.b64encode(self._PDF_BYTES[:10]).decode("ascii"),
                    },
                )
                return await client.call_tool(
                    "research_localfile_upload_chunk",
                    arguments={
                        "session_id": session_id,
                        "index": 2,  # skips index 1
                        "chunk_base64": base64.b64encode(self._PDF_BYTES[10:20]).decode("ascii"),
                    },
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_upload_chunk_unknown_session_fails_not_found(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_localfile_upload_chunk",
                    arguments={
                        "session_id": "nonexistent-session",
                        "index": 0,
                        "chunk_base64": base64.b64encode(b"hello").decode("ascii"),
                    },
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_finalize_upload_with_zero_chunks_fails_invalid_request(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                begin_result = await client.call_tool("research_localfile_begin_upload", arguments={})
                session_id = begin_result.structured_content["session_id"]
                return await client.call_tool(
                    "research_localfile_finalize_upload", arguments={"session_id": session_id}
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_fetch_full_text_logs_deprecation_warning(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch", caplog: "pytest.LogCaptureFixture"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": self._PDF_BASE64, "filename": "paper.pdf"},
                )

        with caplog.at_level(logging.WARNING):
            asyncio.run(scenario())
        assert "deprecation candidate" in caplog.text


class TestLocalfileParseFullTextPageParam:
    """End-to-end MCP tool tests for the `page` param on research_localfile_parse_full_text."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_page_param_returns_that_pages_markdown(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                return await client.call_tool(
                    "research_localfile_parse_full_text", arguments={"id": caller_facing_id, "page": 1}
                )

        result = asyncio.run(scenario())
        # TestLocalFileTools._PDF_BASE64/_PDF_BYTES is a real single-page PDF, so total_pages/
        # page_range are page-consistent with the requested page=1 - same expectation as
        # tests/test_providers_localfile.py's page-param test from Task 14.
        assert result.structured_content["total_pages"] == 1
        assert result.structured_content["page_range"] == [1, 1]


class TestReadMarkdownResourcePageParam:
    """End-to-end MCP resource tests for the `page` query param on `read_markdown_resource`.

    The artefact-lookup bug fix itself (`self._storage.read(..., artefact="markdown")` replacing
    the stale pre-Task-6 `f"{format}-markdown"` scheme) is already exercised end-to-end, without
    `page`, by four existing tests elsewhere in this file that read a `research://.../markdown`
    resource after a parse:
    `TestArxivTools::test_research_arxiv_parse_full_text_then_read_markdown_resource`,
    `TestArxivTools::test_research_arxiv_markdown_resource_honors_offset_and_limit_query_params`,
    `TestEuropePmcTools::test_research_europepmc_parse_full_text_then_read_markdown_resource`, and
    `TestLocalFileTools::test_fetch_then_parse_then_read_resources` - all four now pass with the
    fix in place, so this class does not add a near-duplicate artefact-fix test and instead covers
    only the new `page` behaviour.
    """

    def _server_and_client(self, handler, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        # See TestArxivTools._server_and_client's comment for why monkeypatch.setattr (not
        # setenv) is required for PRIORIS_MCP_STORAGE_DIR.
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path)
        mcp_obj = PriorisMCP()
        mcp_obj._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp_obj._arxiv_provider._http_client = mcp_obj._http_client
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def _localfile_server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        # Same as TestLocalfileParseFullTextPageParam._server_and_client - no HTTP stubbing
        # needed for the local filesystem source.
        storage_dir = tmp_path / "storage"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_page_param_resolves_pdf_page_offset(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._localfile_server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})
                return await client.read_resource(f"research://localfile/{caller_facing_id}/pdf/markdown?page=1")

        resource_result = asyncio.run(scenario())
        page = MarkdownPage.model_validate_json(resource_result[0].text)
        # TestLocalFileTools._PDF_BASE64/_PDF_BYTES is a real single-page PDF, so total_pages/
        # page_range are page-consistent with the requested page=1, same expectation established
        # for research_localfile_parse_full_text's own page-param test (Task 14) and this file's
        # TestLocalfileParseFullTextPageParam above.
        assert page.total_pages == 1
        assert page.page_range == (1, 1)

    def test_page_with_non_pdf_format_raises_invalid_request(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("page validation on a non-pdf format must reject before any network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.read_resource("research://arxiv/2106.09685v2/html/markdown?page=1")

        with pytest.raises(McpError):
            asyncio.run(scenario())

    def test_page_before_any_parse_has_happened_is_not_found(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._localfile_server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                # fetch_full_text only - never parse_full_text - so no markdown artefact and no
                # manifest leaf rows exist yet; page=1 must be a not-found, not a silent fallback.
                await client.read_resource(f"research://localfile/{caller_facing_id}/pdf/markdown?page=1")

        with pytest.raises(McpError):
            asyncio.run(scenario())

    def test_page_beyond_total_pages_after_parse_is_not_found(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """Out-of-range `page` after a parse, distinct from the never-parsed case above.

        Here the manifest does have leaf rows (the fixture PDF has exactly one page), but page=2
        has no corresponding row, so `manifest.leaf_for_page` returns `None` and
        `read_markdown_resource` must still raise not-found rather than resolving to some
        fallback offset.
        """
        client = self._localfile_server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})
                await client.read_resource(f"research://localfile/{caller_facing_id}/pdf/markdown?page=2")

        with pytest.raises(McpError):
            asyncio.run(scenario())


class TestStorageManagementTools:
    """End-to-end MCP tool tests for research_list_fetched/research_delete_fetched."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_list_fetched_returns_entries_after_a_fetch(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)
        payload = base64.b64encode(b"%PDF-1.4 fake content").decode("ascii")

        async def scenario():
            async with client:
                await client.call_tool("research_localfile_fetch_full_text", arguments={"content_base64": payload})
                return await client.call_tool("research_list_fetched", arguments={})

        result = asyncio.run(scenario())
        entries = result.structured_content["entries"]
        assert len(entries) == 1
        assert entries[0]["provider"] == "localfile"
        assert entries[0]["format"] == "pdf"

    def test_list_fetched_with_no_matches_returns_empty_list(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_list_fetched", arguments={"provider": "arxiv"})

        result = asyncio.run(scenario())
        assert result.structured_content["entries"] == []

    def test_list_fetched_paging_metadata_with_default_offset_and_limit(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)
        payload = base64.b64encode(b"%PDF-1.4 fake content").decode("ascii")

        async def scenario():
            async with client:
                await client.call_tool("research_localfile_fetch_full_text", arguments={"content_base64": payload})
                return await client.call_tool("research_list_fetched", arguments={})

        result = asyncio.run(scenario())
        assert result.structured_content["offset"] == 0
        assert result.structured_content["limit"] == 50
        assert result.structured_content["total"] == 1
        assert result.structured_content["has_more"] is False

    def test_list_fetched_offset_equal_to_total_returns_empty_page_with_has_more_false(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)
        payload = base64.b64encode(b"%PDF-1.4 fake content").decode("ascii")

        async def scenario():
            async with client:
                await client.call_tool("research_localfile_fetch_full_text", arguments={"content_base64": payload})
                return await client.call_tool("research_list_fetched", arguments={"offset": 1})

        result = asyncio.run(scenario())
        assert result.structured_content["entries"] == []
        assert result.structured_content["total"] == 1
        assert result.structured_content["has_more"] is False

    def test_list_fetched_offset_beyond_total_returns_empty_page(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)
        payload = base64.b64encode(b"%PDF-1.4 fake content").decode("ascii")

        async def scenario():
            async with client:
                await client.call_tool("research_localfile_fetch_full_text", arguments={"content_base64": payload})
                return await client.call_tool("research_list_fetched", arguments={"offset": 100})

        result = asyncio.run(scenario())
        assert result.structured_content["entries"] == []
        assert result.structured_content["total"] == 1
        assert result.structured_content["has_more"] is False

    def test_list_fetched_limit_smaller_than_total_reports_has_more(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                for index in range(3):
                    payload = base64.b64encode(f"%PDF-1.4 fake content {index}".encode()).decode("ascii")
                    await client.call_tool("research_localfile_fetch_full_text", arguments={"content_base64": payload})
                return await client.call_tool("research_list_fetched", arguments={"offset": 0, "limit": 2})

        result = asyncio.run(scenario())
        assert len(result.structured_content["entries"]) == 2
        assert result.structured_content["total"] == 3
        assert result.structured_content["has_more"] is True

    def test_list_fetched_negative_offset_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_list_fetched", arguments={"offset": -1})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_list_fetched_zero_limit_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_list_fetched", arguments={"limit": 0})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_list_fetched_negative_limit_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_list_fetched", arguments={"limit": -5})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_delete_fetched_removes_entry_and_reports_it(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)
        payload = base64.b64encode(b"%PDF-1.4 fake content").decode("ascii")

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text", arguments={"content_base64": payload}
                )
                caller_facing_id = fetch_result.structured_content["id"]
                delete_result = await client.call_tool(
                    "research_delete_fetched",
                    arguments={
                        "entries": [
                            {
                                "provider": "localfile",
                                "identifier": caller_facing_id,
                                "format": "pdf",
                                "artefact": "document",
                            }
                        ]
                    },
                )
                list_after = await client.call_tool("research_list_fetched", arguments={})
                return delete_result, list_after

        delete_result, list_after = asyncio.run(scenario())
        assert len(delete_result.structured_content["deleted"]) == 1
        assert delete_result.structured_content["not_found"] == []
        assert list_after.structured_content["entries"] == []

    def test_delete_fetched_reports_not_found_without_failing_whole_call(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_delete_fetched",
                    arguments={
                        "entries": [
                            {
                                "provider": "arxiv",
                                "identifier": "does-not-exist",
                                "format": "pdf",
                                "artefact": "document",
                            }
                        ]
                    },
                )

        result = asyncio.run(scenario())
        assert result.structured_content["deleted"] == []
        assert len(result.structured_content["not_found"]) == 1

    def test_delete_fetched_reports_invalid_request_for_entry_missing_a_key(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_delete_fetched",
                    arguments={"entries": [{"provider": "arxiv", "identifier": "does-not-exist"}]},
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_delete_fetched_does_not_cascade_to_derived_markdown_entry(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})
                list_after_parse = await client.call_tool("research_list_fetched", arguments={})
                delete_result = await client.call_tool(
                    "research_delete_fetched",
                    arguments={
                        "entries": [
                            {
                                "provider": "localfile",
                                "identifier": caller_facing_id,
                                "format": "pdf",
                                "artefact": "document",
                            }
                        ]
                    },
                )
                list_after_delete = await client.call_tool("research_list_fetched", arguments={})
                return list_after_parse, delete_result, list_after_delete

        list_after_parse, delete_result, list_after_delete = asyncio.run(scenario())
        # Both the source (format="pdf", artefact="document") and derived-markdown
        # (format="pdf", artefact="markdown") entries share the same `format`; only `artefact`
        # distinguishes them - `format` is never synthesized into a "pdf-markdown" display string.
        artefacts_after_parse = {entry["artefact"] for entry in list_after_parse.structured_content["entries"]}
        assert artefacts_after_parse == {"document", "markdown"}

        assert len(delete_result.structured_content["deleted"]) == 1

        entries_after_delete = list_after_delete.structured_content["entries"]
        assert {entry["artefact"] for entry in entries_after_delete} == {"markdown"}
        assert {entry["format"] for entry in entries_after_delete} == {"pdf"}


class TestDeleteFetchedArtefactField:
    """Tests for the required `artefact` field on research_delete_fetched, incl. search cascade."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return mcp_obj, Client(transport=server_with_features, timeout=60)

    def test_entry_missing_artefact_key_raises_invalid_request(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        _, client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_delete_fetched",
                    arguments={"entries": [{"provider": "arxiv", "identifier": "does-not-exist", "format": "pdf"}]},
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_delete_markdown_artefact_removes_it_from_search_index(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        mcp_obj, client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})

                # research_search_fetched (Task 17d) doesn't exist yet, so verify the search-index
                # cascade by poking PriorisMCP's own _search_index directly, matching this file's
                # established pattern of inspecting internal attributes for verification.
                matches_before = await mcp_obj._search_index.search(
                    "Hello", provider="localfile", identifier=caller_facing_id, format="pdf"
                )

                delete_result = await client.call_tool(
                    "research_delete_fetched",
                    arguments={
                        "entries": [
                            {
                                "provider": "localfile",
                                "identifier": caller_facing_id,
                                "format": "pdf",
                                "artefact": "markdown",
                            }
                        ]
                    },
                )

                matches_after = await mcp_obj._search_index.search(
                    "Hello", provider="localfile", identifier=caller_facing_id, format="pdf"
                )
                return delete_result, matches_before, matches_after

        delete_result, matches_before, matches_after = asyncio.run(scenario())
        assert len(delete_result.structured_content["deleted"]) == 1
        assert matches_before != []
        assert matches_after == []


class TestResearchDiscovery:
    """End-to-end MCP tool tests for research_discovery."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch", handler, *, api_key="test-key"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        mock_transport = httpx.MockTransport(handler)
        mcp_obj = PriorisMCP()
        mcp_obj._http_client = httpx.AsyncClient(transport=mock_transport)
        mcp_obj._openalex_client = mcp_obj._openalex_client.__class__(mcp_obj._http_client, api_key=api_key)
        server = FastMCP()
        return mcp_obj, Client(transport=mcp_obj.register_features(server), timeout=60)

    def test_research_discovery_is_registered(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        _, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": []})
        )

        async def scenario():
            async with client:
                tools = await client.list_tools()
                return [tool.name for tool in tools]

        assert "research_discovery" in asyncio.run(scenario())

    def test_research_discovery_returns_hits(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        work = {
            "id": "https://openalex.org/W2741809807",
            "doi": None,
            "title": "A Paper",
            "publication_year": 2020,
            "relevance_score": 0.5,
            "authorships": [],
            "abstract_inverted_index": None,
            "ids": {},
            "locations": [],
        }
        _, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": [work]})
        )

        async def scenario():
            async with client:
                return await client.call_tool("research_discovery", arguments={"query": "graph neural networks"})

        hits = asyncio.run(scenario()).structured_content["hits"]
        assert len(hits) == 1
        assert hits[0]["openalex_id"] == "W2741809807"
        assert hits[0]["fetch_route"]["kind"] == "manual_upload"

    def test_research_discovery_defaults_max_results_from_env_var(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_DISCOVERY_MAX_RESULTS", 7)
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        _, client = self._server_and_client(tmp_path, monkeypatch, handler)

        async def scenario():
            async with client:
                await client.call_tool("research_discovery", arguments={"query": "quantum computing"})

        asyncio.run(scenario())
        assert seen_params[0]["per-page"] == "7"

    def test_research_discovery_rejects_invalid_requests(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        _, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": []})
        )

        async def scenario(arguments: dict):
            async with client:
                return await client.call_tool("research_discovery", arguments=arguments)

        with pytest.raises(ToolError):
            asyncio.run(scenario({"query": "x" * 2001}))
        with pytest.raises(ToolError):
            asyncio.run(scenario({"query": "valid", "max_results": 51}))

    def test_research_discovery_fails_with_actionable_error_when_api_key_missing(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        _, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": []}), api_key=None
        )

        async def scenario():
            async with client:
                return await client.call_tool("research_discovery", arguments={"query": "graph neural networks"})

        with pytest.raises(ToolError, match="PRIORIS_MCP_OPENALEX_API_KEY"):
            asyncio.run(scenario())

    def test_research_discovery_passes_through_paging_and_filters(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": [], "meta": {"count": 0}})

        _, client = self._server_and_client(tmp_path, monkeypatch, handler)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_discovery",
                    arguments={
                        "query": "graph neural networks",
                        "page": 2,
                        "from_year": 2020,
                        "to_year": 2023,
                        "open_access_only": True,
                    },
                )

        result = asyncio.run(scenario())
        assert seen_params[0]["page"] == "2"
        assert seen_params[0]["filter"] == "publication_year:>=2020,publication_year:<=2023,is_oa:true"
        structured = result.structured_content
        assert structured["page"] == 2
        assert structured["total"] == 0
        assert structured["has_more"] is False

    def test_research_discovery_excludes_fetched_known_provider_hits(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        works = [
            {
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.48550/arxiv.1706.03762",
                "title": "Fetched arXiv Paper",
                "publication_year": 2017,
                "relevance_score": 0.9,
                "authorships": [],
                "abstract_inverted_index": None,
                "ids": {},
                "locations": [],
            },
            {
                "id": "https://openalex.org/W2",
                "doi": None,
                "title": "Unfetched Paper",
                "publication_year": 2018,
                "relevance_score": 0.8,
                "authorships": [],
                "abstract_inverted_index": None,
                "ids": {},
                "locations": [],
            },
        ]
        mcp_obj, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": works})
        )

        async def scenario():
            await mcp_obj._storage.write("arxiv", "1706.03762v2", "pdf", b"%PDF")
            async with client:
                return await client.call_tool("research_discovery", arguments={"query": "transformers"})

        hits = asyncio.run(scenario()).structured_content["hits"]
        assert [hit["openalex_id"] for hit in hits] == ["W2"]

    def test_research_discovery_excludes_fetched_europepmc_hit(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        work = {
            "id": "https://openalex.org/W1",
            "doi": None,
            "title": "Fetched Europe PMC Paper",
            "publication_year": 2017,
            "relevance_score": 0.9,
            "authorships": [],
            "abstract_inverted_index": None,
            "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4767193"},
            "locations": [],
        }
        mcp_obj, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": [work]})
        )

        async def scenario():
            await mcp_obj._storage.write("europepmc", "PMC:4767193", "xml", b"<article/>")
            async with client:
                return await client.call_tool("research_discovery", arguments={"query": "biology"})

        assert asyncio.run(scenario()).structured_content["hits"] == []

    def test_fetched_discovery_identifiers_paginates_and_stops_on_empty_page(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        mcp_obj, _ = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": []})
        )
        pages = [
            ([{"identifier": "1706.03762v2"}], 2),
            ([{"identifier": "2401.00001"}], 2),
            ([], 3),
        ]

        async def list_entries(provider: str, *, offset: int, limit: int):
            assert provider == "arxiv"
            assert limit == 200
            return pages.pop(0)

        monkeypatch.setattr(mcp_obj._storage, "list", list_entries)
        assert asyncio.run(mcp_obj._fetched_discovery_identifiers("arxiv")) == {"1706.03762", "2401.00001"}

    def test_normalise_discovery_identifier_leaves_unknown_provider_unchanged(self):
        assert PriorisMCP._normalise_discovery_identifier("localfile", "id-1") == "id-1"

    def test_openalex_work_types_resource_is_registered(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        _, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": []})
        )

        async def scenario():
            async with client:
                resources = await client.list_resources()
                return [str(resource.uri) for resource in resources]

        assert "research://openalex/work-types" in asyncio.run(scenario())

    def test_openalex_work_types_resource_fails_with_actionable_error_when_api_key_missing(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        _, client = self._server_and_client(
            tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"results": []}), api_key=None
        )

        async def scenario():
            async with client:
                return await client.read_resource("research://openalex/work-types")

        with pytest.raises(McpError):
            asyncio.run(scenario())

    def test_openalex_work_types_resource_returns_sorted_types(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        work_types = {
            "results": [
                {
                    "id": "https://openalex.org/types/preprint",
                    "display_name": "preprint",
                    "description": "An article whose primary location is a preprint repository.",
                },
                {
                    "id": "https://openalex.org/types/article",
                    "display_name": "article",
                    "description": "Original, citable research usually in a journal.",
                },
            ]
        }
        _, client = self._server_and_client(tmp_path, monkeypatch, lambda request: httpx.Response(200, json=work_types))

        async def scenario():
            async with client:
                return await client.read_resource("research://openalex/work-types")

        result = asyncio.run(scenario())
        parsed = OpenAlexWorkTypesResult.model_validate_json(result[0].text)
        assert parsed == OpenAlexWorkTypesResult(
            types=[
                OpenAlexWorkType(
                    code="article", name="article", description="Original, citable research usually in a journal."
                ),
                OpenAlexWorkType(
                    code="preprint",
                    name="preprint",
                    description="An article whose primary location is a preprint repository.",
                ),
            ]
        )


class TestResearchSearchFetched:
    """End-to-end MCP tool tests for research_search_fetched."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_search_before_anything_persisted_returns_no_matches(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_search_fetched", arguments={"query": "quantum"})

        result = asyncio.run(scenario())
        assert result.structured_content["fts"]["matches"] == []
        assert result.structured_content["fts"]["total"] == 0
        assert result.structured_content["fts"]["has_more"] is False

    def test_identifier_without_provider_raises_invalid_request(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "quantum", "identifier": "2106.09685v2"},
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_search_finds_previously_parsed_content(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})

                search_result = await client.call_tool("research_search_fetched", arguments={"query": "Hello"})
                return caller_facing_id, search_result

        caller_facing_id, search_result = asyncio.run(scenario())
        matches = search_result.structured_content["fts"]["matches"]
        assert len(matches) >= 1
        assert matches[0]["provider"] == "localfile"
        assert matches[0]["identifier"] == caller_facing_id
        assert matches[0]["format"] == "pdf"

    def test_invalid_fts5_query_syntax_raises_invalid_request(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_search_fetched", arguments={"query": "C++"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_mode_vector_returns_vector_results(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await mcp_obj._document_vector_backend.index_entries(
                    "arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "transformer attention"}]
                )
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector"},
                )

        result = asyncio.run(scenario())
        assert result.structured_content["fts"] is None
        assert len(result.structured_content["vector"]["matches"]) == 1

    def test_mode_hybrid_returns_both_mechanisms(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_search_fetched", arguments={"query": "anything", "mode": "hybrid"}
                )

        result = asyncio.run(scenario())
        assert result.structured_content["fts"]["matches"] == []
        assert result.structured_content["vector"]["matches"] == []

    def test_unrecognised_mode_raises_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                return await client.call_tool("research_search_fetched", arguments={"query": "x", "mode": "graph"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_identifier_and_provider_populates_per_mechanism_index_status(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "quantum", "provider": "arxiv", "identifier": "A", "format": "pdf"},
                )

        result = asyncio.run(scenario())
        assert result.structured_content["index_status"] == {"fts": "not_built", "vector": "not_built"}

    def test_index_status_is_empty_when_format_is_omitted(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "Hello", "provider": "localfile", "identifier": caller_facing_id},
                )

        result = asyncio.run(scenario())
        assert result.structured_content["index_status"] == {}

    def test_index_status_reports_ready_when_format_is_given(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={
                        "query": "Hello",
                        "provider": "localfile",
                        "identifier": caller_facing_id,
                        "format": "pdf",
                    },
                )

        result = asyncio.run(scenario())
        assert result.structured_content["index_status"]["fts"] == "ready"

    def test_index_status_reports_building_while_a_reembed_task_is_in_flight(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """A poll during a live (re-)embed must see "building", not the backend's persisted status.

        See docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                gate = asyncio.Event()
                mcp_obj._embedding_scheduler.schedule(("arxiv", "A", "pdf"), gate.wait)
                try:
                    return await client.call_tool(
                        "research_search_fetched",
                        arguments={"query": "quantum", "provider": "arxiv", "identifier": "A", "format": "pdf"},
                    )
                finally:
                    gate.set()
                    await mcp_obj._embedding_scheduler.wait_all()

        result = asyncio.run(scenario())
        assert result.structured_content["index_status"] == {"fts": "not_built", "vector": "building"}

    def test_limit_defaults_from_env_var(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT", 1)
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await mcp_obj._document_vector_backend.index_entries(
                    "arxiv",
                    "A",
                    "pdf",
                    [
                        {"chunk_id": "c1", "start": 0, "length": 5, "text": "transformer attention mechanism"},
                        {"chunk_id": "c2", "start": 5, "length": 5, "text": "attention mechanism transformer"},
                    ],
                )
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector"},
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 1

    def test_limit_param_overrides_the_default(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT", 1)
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await mcp_obj._document_vector_backend.index_entries(
                    "arxiv",
                    "A",
                    "pdf",
                    [
                        {"chunk_id": "c1", "start": 0, "length": 5, "text": "transformer attention mechanism"},
                        {"chunk_id": "c2", "start": 5, "length": 5, "text": "attention mechanism transformer"},
                    ],
                )
                return await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector", "limit": 2},
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 2

    def test_offset_pages_through_fts_results_without_repeats(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": TestLocalFileTools._PDF_BASE64, "filename": "paper.pdf"},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})

                page1 = await client.call_tool(
                    "research_search_fetched", arguments={"query": "Hello", "limit": 1, "offset": 0}
                )
                page2 = await client.call_tool(
                    "research_search_fetched", arguments={"query": "Hello", "limit": 1, "offset": 1}
                )
                return page1, page2

        page1, page2 = asyncio.run(scenario())
        matches1 = page1.structured_content["fts"]["matches"]
        matches2 = page2.structured_content["fts"]["matches"]
        assert len(matches1) == 1
        if matches2:
            assert matches1[0]["offset"] != matches2[0]["offset"] or matches1[0]["snippet"] != matches2[0]["snippet"]
        assert page1.structured_content["fts"]["offset"] == 0
        assert page2.structured_content["fts"]["offset"] == 1

    def test_offset_pages_through_vector_results_without_repeats(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await mcp_obj._document_vector_backend.index_entries(
                    "arxiv",
                    "A",
                    "pdf",
                    [
                        {"chunk_id": "c1", "start": 0, "length": 5, "text": "transformer attention mechanism"},
                        {"chunk_id": "c2", "start": 5, "length": 5, "text": "attention mechanism transformer"},
                    ],
                )
                page1 = await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector", "limit": 1, "offset": 0},
                )
                page2 = await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector", "limit": 1, "offset": 1},
                )
                return page1, page2

        page1, page2 = asyncio.run(scenario())
        matches1 = page1.structured_content["vector"]["matches"]
        matches2 = page2.structured_content["vector"]["matches"]
        assert len(matches1) == 1
        assert len(matches2) == 1
        assert matches1[0]["chunk_id"] != matches2[0]["chunk_id"]
        assert page1.structured_content["vector"]["total"] == 2
        assert page2.structured_content["vector"]["total"] == 2

    def test_total_and_has_more_boundary_cases(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await mcp_obj._document_vector_backend.index_entries(
                    "arxiv",
                    "A",
                    "pdf",
                    [
                        {"chunk_id": "c1", "start": 0, "length": 5, "text": "transformer attention mechanism"},
                        {"chunk_id": "c2", "start": 5, "length": 5, "text": "attention mechanism transformer"},
                    ],
                )
                not_exhausted = await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector", "limit": 1, "offset": 0},
                )
                exhausted = await client.call_tool(
                    "research_search_fetched",
                    arguments={"query": "attention mechanism", "mode": "vector", "limit": 1, "offset": 1},
                )
                return not_exhausted, exhausted

        not_exhausted, exhausted = asyncio.run(scenario())
        assert not_exhausted.structured_content["vector"]["total"] == 2
        assert not_exhausted.structured_content["vector"]["has_more"] is True
        assert exhausted.structured_content["vector"]["total"] == 2
        assert exhausted.structured_content["vector"]["has_more"] is False

    def test_negative_offset_raises_invalid_request(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_search_fetched", arguments={"query": "quantum", "offset": -1})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_non_positive_limit_raises_invalid_request(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_search_fetched", arguments={"query": "quantum", "limit": 0})

        with pytest.raises(ToolError):
            asyncio.run(scenario())


class TestResearchNotesCreate:
    """End-to-end MCP tool tests for research_notes_create."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        notes_dir = tmp_path / "notes"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", notes_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_creates_note_and_returns_it(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "20260729-1430-a3f2",
                        "format": "pdf",
                        "text": "a note about the ablation study",
                    },
                )

        result = asyncio.run(scenario())
        assert result.structured_content["text"] == "a note about the ablation study"
        assert result.structured_content["provider"] == "localfile"
        assert result.structured_content["canonical_identifier"] == "20260729-1430-a3f2"

    def test_rejects_empty_anchor(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "20260729-1430-a3f2",
                        "format": "pdf",
                        "text": "a note",
                        "anchors": [{}],
                    },
                )

        with pytest.raises(ToolError, match="location or selectors"):
            asyncio.run(scenario())

    def test_notes_create_schedules_vector_indexing(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        client = Client(transport=server_with_features, timeout=60)

        async def scenario():
            async with client:
                result = await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "2106.09685v2", "text": "a note"},
                )
                note_id = result.structured_content["id"]  # ty: ignore[not-subscriptable]
                await mcp_obj._embedding_scheduler.wait_all()
                return await mcp_obj._note_vector_backend.status(note_id)

        assert asyncio.run(scenario()) == "ready"


class TestResearchNotesRead:
    """End-to-end MCP tool tests for research_notes_read."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        notes_dir = tmp_path / "notes"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", notes_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_reads_created_note(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-1",
                        "format": "pdf",
                        "text": "a note",
                    },
                )
                note_id = created.structured_content["id"]
                result = await client.call_tool("research_notes_read", arguments={"note_id": note_id})
                return result

        result = asyncio.run(scenario())
        assert result.structured_content["text"] == "a note"

    def test_read_missing_note_raises(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_read", arguments={"note_id": "does-not-exist"})

        with pytest.raises(ToolError, match="note not found: 'does-not-exist'"):
            asyncio.run(scenario())


class TestResearchNotesUpdate:
    """End-to-end MCP tool tests for research_notes_update."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        notes_dir = tmp_path / "notes"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", notes_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_updates_text(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-1",
                        "format": "pdf",
                        "text": "original",
                    },
                )
                note_id = created.structured_content["id"]
                result = await client.call_tool(
                    "research_notes_update",
                    arguments={"note_id": note_id, "text": "revised"},
                )
                return result

        result = asyncio.run(scenario())
        assert result.structured_content["text"] == "revised"

    def test_update_missing_note_raises(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_notes_update",
                    arguments={"note_id": "does-not-exist", "text": "x"},
                )

        with pytest.raises(ToolError, match="note not found: 'does-not-exist'"):
            asyncio.run(scenario())


class TestResearchNotesDelete:
    """End-to-end MCP tool tests for research_notes_delete."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        notes_dir = tmp_path / "notes"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", notes_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_deletes_existing_note(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-1",
                        "format": "pdf",
                        "text": "a note",
                    },
                )
                note_id = created.structured_content["id"]
                result = await client.call_tool("research_notes_delete", arguments={"note_id": note_id})
                return result

        result = asyncio.run(scenario())
        assert result.structured_content["result"] is True

    def test_delete_missing_note_returns_false(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_delete", arguments={"note_id": "does-not-exist"})

        result = asyncio.run(scenario())
        assert result.structured_content["result"] is False

    def test_notes_delete_removes_vector_entry(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        client = Client(transport=server_with_features, timeout=60)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "2106.09685v2", "text": "a note"},
                )
                note_id = created.structured_content["id"]  # ty: ignore[not-subscriptable]
                await mcp_obj._embedding_scheduler.wait_all()
                await client.call_tool("research_notes_delete", arguments={"note_id": note_id})
                return await mcp_obj._note_vector_backend.status(note_id)

        assert asyncio.run(scenario()) == "not_built"

    def test_delete_racing_in_flight_schedule_does_not_resurrect_vector_row(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        client = Client(transport=server_with_features, timeout=60)

        started = asyncio.Event()
        release = asyncio.Event()
        real_index_note = mcp_obj._note_vector_backend.index_note

        async def slow_index_note(note_id: str, text: str) -> None:
            started.set()
            await release.wait()
            await real_index_note(note_id, text)

        async def scenario():
            async with client:
                monkeypatch.setattr(mcp_obj._note_vector_backend, "index_note", slow_index_note)
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "2106.09685v2", "text": "a note"},
                )
                note_id = created.structured_content["id"]  # ty: ignore[not-subscriptable]
                await started.wait()  # embed is in flight, blocked on release
                await client.call_tool("research_notes_delete", arguments={"note_id": note_id})
                release.set()  # let the (now-cancelled) embed task try to proceed, if it still can
                await mcp_obj._embedding_scheduler.wait_all()
                return await mcp_obj._note_vector_backend.status(note_id)

        assert asyncio.run(scenario()) == "not_built"


class TestResearchNotesSearch:
    """End-to-end MCP tool tests for research_notes_search."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        notes_dir = tmp_path / "notes"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", notes_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_no_filters_lists_everything(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-1",
                        "format": "pdf",
                        "text": "note 1",
                    },
                )
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-2",
                        "format": "pdf",
                        "text": "note 2",
                    },
                )
                return await client.call_tool("research_notes_search", arguments={})

        result = asyncio.run(scenario())
        assert result.structured_content["fts"]["total"] == 2

    def test_keyword_filters_by_text(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-3",
                        "format": "pdf",
                        "text": "mentions latency specifically",
                    },
                )
                return await client.call_tool("research_notes_search", arguments={"keyword": "latency"})

        result = asyncio.run(scenario())
        assert result.structured_content["fts"]["total"] >= 1

    def test_author_filter_named_without_author_name_is_a_tool_error(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"author_filter": "named"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_mode_vector_author_name_without_named_filter_is_a_tool_error(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """D4: author_name misuse must raise consistently in mode='vector' too, not be silently dropped.

        author_filter is left at its default (ANY) while author_name is given - the fts/hybrid
        path already raises for this (see test_author_filter_named_without_author_name_is_a_tool_error
        for the mirror-image misuse), but mode='vector' never called self._notes_backend.search
        (where that validation used to live), so it silently dropped author_name instead.
        """
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "attention", "mode": "vector", "author_name": "Smith"},
                )

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_canonical_identifier_without_provider_is_a_tool_error(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"canonical_identifier": "id-1"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_malformed_fts5_keyword_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"keyword": "AND"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_invalid_date_from_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"date_from": "not-a-date"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_mode_vector_returns_note_vector_results(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()
                return await client.call_tool(
                    "research_notes_search", arguments={"keyword": "attention-based models", "mode": "vector"}
                ), created

        result, created = asyncio.run(scenario())
        assert result.structured_content["fts"] is None
        assert len(result.structured_content["vector"]["matches"]) == 1
        assert result.structured_content["vector"]["matches"][0]["note_id"] == created.structured_content["id"]

    def test_mode_vector_structural_filter_excludes_other_provider_match(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                created_arxiv = await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "notes about feline companions"},
                )
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "europepmc", "identifier": "B", "text": "notes about feline companions"},
                )
                await mcp_obj._embedding_scheduler.wait_all()
                result = await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "feline companions", "mode": "vector", "provider": "arxiv"},
                )
                return result, created_arxiv

        result, created_arxiv = asyncio.run(scenario())
        vector_matches = result.structured_content["vector"]["matches"]
        assert len(vector_matches) == 1
        assert vector_matches[0]["note_id"] == created_arxiv.structured_content["id"]

    def test_mode_vector_offset_pages_without_repeat_or_skip(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "arxiv",
                        "identifier": "A",
                        "text": "feline companions and their behaviour",
                    },
                )
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "arxiv",
                        "identifier": "B",
                        "text": "canine companions and their behaviour",
                    },
                )
                await mcp_obj._embedding_scheduler.wait_all()
                first = await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "pet companion behaviour", "mode": "vector", "offset": 0, "limit": 1},
                )
                second = await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "pet companion behaviour", "mode": "vector", "offset": 1, "limit": 1},
                )
                return first, second

        first, second = asyncio.run(scenario())
        first_ids = [m["note_id"] for m in first.structured_content["vector"]["matches"]]
        second_ids = [m["note_id"] for m in second.structured_content["vector"]["matches"]]
        assert len(first_ids) == 1
        assert len(second_ids) == 1
        assert first_ids != second_ids

    def test_mode_vector_reports_total_and_has_more_across_pages(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """`total`/`has_more` must reflect the corpus-wide match count, not just this page's size.

        Mirrors test_mode_vector_offset_pages_without_repeat_or_skip's two-note setup, but asserts
        the paging metadata itself (introduced by PagedNoteVectorMatches) rather than which note IDs
        land on which page.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "arxiv",
                        "identifier": "A",
                        "text": "feline companions and their behaviour",
                    },
                )
                await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "arxiv",
                        "identifier": "B",
                        "text": "canine companions and their behaviour",
                    },
                )
                await mcp_obj._embedding_scheduler.wait_all()
                first = await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "pet companion behaviour", "mode": "vector", "offset": 0, "limit": 1},
                )
                second = await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "pet companion behaviour", "mode": "vector", "offset": 1, "limit": 1},
                )
                return first, second

        first, second = asyncio.run(scenario())
        first_vector = first.structured_content["vector"]
        second_vector = second.structured_content["vector"]
        assert first_vector["offset"] == 0
        assert first_vector["limit"] == 1
        assert first_vector["total"] == 2
        assert first_vector["has_more"] is True
        assert second_vector["offset"] == 1
        assert second_vector["limit"] == 1
        assert second_vector["total"] == 2
        assert second_vector["has_more"] is False

    def test_mode_fts_leaves_index_status_none(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"mode": "fts"})

        result = asyncio.run(scenario())
        assert result.structured_content["index_status"] is None

    def test_mode_vector_reports_ready_index_status(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()
                return await client.call_tool(
                    "research_notes_search", arguments={"keyword": "attention-based models", "mode": "vector"}
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 1
        assert result.structured_content["index_status"] == {"vector": "ready"}

    def test_mode_vector_reports_building_while_a_reembed_task_is_in_flight(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """A poll during a live re-embed must see "building", not whatever the backend's last completed write left behind.

        Here, that would still be "ready" from the initial embed - see
        docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                create_result = await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                note_id = create_result.structured_content["id"]  # ty: ignore[not-subscriptable]
                await mcp_obj._embedding_scheduler.wait_all()

                gate = asyncio.Event()
                mcp_obj._embedding_scheduler.schedule(("note", note_id), gate.wait)
                try:
                    return await client.call_tool(
                        "research_notes_search", arguments={"keyword": "attention-based models", "mode": "vector"}
                    )
                finally:
                    gate.set()
                    await mcp_obj._embedding_scheduler.wait_all()

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 1
        assert result.structured_content["index_status"] == {"vector": "building"}

    def test_mode_vector_reports_not_built_when_a_matched_note_has_no_status_row(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Covers the non-empty-statuses "not_built" branch (distinct from the D6 empty-page one).

        A match's own per-note status(), not the corpus-wide has_any_indexed() check, drives this
        - simulated here (rather than via a real dangling row, since index_note always writes both
        tables together) by stubbing status() directly, the same technique
        test_mode_vector_reports_not_built_after_a_renamed_model_swap uses to simulate a status
        mismatch without hand-rolling raw SQL against the backend.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()

                async def _always_not_built(note_id: str) -> str:
                    return "not_built"

                monkeypatch.setattr(mcp_obj._note_vector_backend, "status", _always_not_built)
                return await client.call_tool(
                    "research_notes_search", arguments={"keyword": "attention-based models", "mode": "vector"}
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 1
        assert result.structured_content["index_status"] == {"vector": "not_built"}

    def test_mode_vector_reports_not_built_after_a_renamed_model_swap(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """`stale` is not observable through the public API (Fix 1).

        The first status() connect under a renamed model wipes the prior status row in the same
        step that drops the mismatched vec0 table.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()

                # Simulate a model-name change: the mechanism keeps its own reference to the
                # original backend (so search still finds the already-indexed vector), but
                # research_notes_search's own status check goes through mcp_obj._note_vector_backend,
                # which we swap for a same-file, same-dimension, differently-named view.
                real_embedding = mcp_obj._embedding_backend

                class _RenamedStub(EmbeddingBackend):
                    model_name = "a-different-model"
                    dimension = real_embedding.dimension
                    max_chunk_chars = real_embedding.max_chunk_chars

                    async def embed(self, text):
                        return await real_embedding.embed(text)

                mcp_obj._note_vector_backend = SqliteVecNoteBackend(
                    tmp_path / "vectors" / "notes-vectors.sqlite3", _RenamedStub()
                )
                return await client.call_tool(
                    "research_notes_search", arguments={"keyword": "attention-based models", "mode": "vector"}
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 1
        assert result.structured_content["index_status"] == {"vector": "not_built"}

    def test_mode_vector_aggregates_a_stale_status_row_if_one_is_ever_reported(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Exercises the aggregation's `stale` branch directly, by stubbing status().

        The real backend can no longer produce `stale` in practice (Fix 1 deletes a differently-
        named status row rather than letting it survive to be read - see
        test_mode_vector_reports_not_built_after_a_renamed_model_swap), but `stale` remains a
        legal `IndexStatus` value the aggregation must still rank correctly if a future backend
        (or the generation-token design vector-search.md documents as a rejected alternative) ever
        produces it - the same technique
        test_mode_vector_reports_not_built_when_a_matched_note_has_no_status_row uses for
        `not_built`.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()

                async def _always_stale(note_id: str) -> str:
                    return "stale"

                monkeypatch.setattr(mcp_obj._note_vector_backend, "status", _always_stale)
                return await client.call_tool(
                    "research_notes_search", arguments={"keyword": "attention-based models", "mode": "vector"}
                )

        result = asyncio.run(scenario())
        assert len(result.structured_content["vector"]["matches"]) == 1
        assert result.structured_content["index_status"] == {"vector": "stale"}

    def test_mode_vector_with_no_matches_reports_not_built(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_notes_search", arguments={"keyword": "nothing indexed yet", "mode": "vector"}
                )

        result = asyncio.run(scenario())
        assert result.structured_content["vector"]["matches"] == []
        assert result.structured_content["index_status"] == {"vector": "not_built"}

    def test_mode_vector_filtered_empty_scope_reports_not_built_despite_ready_corpus_elsewhere(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """A structural filter matching no notes at all must not borrow an unrelated ready corpus's status.

        Previously (the review's Medium finding) an empty vector.matches page fell back to a
        corpus-wide has_any_indexed() check that ignored the caller's filters entirely - a filter
        matching zero notes reported "ready" merely because *some* unrelated note elsewhere was
        ready. The requested scope here (provider="europepmc") has nothing to build, so it must
        report "not_built", matching test_mode_vector_with_no_matches_reports_not_built's
        genuinely-empty-corpus case rather than the corpus-wide ready note under "arxiv".
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()
                # A structural filter matching no notes (the only note is under "arxiv") narrows
                # the scope to [] before the vector search runs.
                return await client.call_tool(
                    "research_notes_search",
                    arguments={"keyword": "attention-based models", "mode": "vector", "provider": "europepmc"},
                )

        result = asyncio.run(scenario())
        assert result.structured_content["vector"]["matches"] == []
        assert result.structured_content["index_status"] == {"vector": "not_built"}

    def test_mode_vector_empty_page_past_total_still_reports_ready(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """D6: zero matches on this particular *page* must report "ready", not "not_built".

        Distinct from an empty *scope* (see the sibling "not_built" test above): here the note is
        in scope and ready, but `offset` pages past the only match, so `vector.matches` is empty
        for a page-size reason unrelated to build state.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "transformer attention mechanisms"},
                )
                await mcp_obj._embedding_scheduler.wait_all()
                return await client.call_tool(
                    "research_notes_search",
                    arguments={
                        "keyword": "attention-based models",
                        "mode": "vector",
                        "provider": "arxiv",
                        "offset": 5,
                    },
                )

        result = asyncio.run(scenario())
        assert result.structured_content["vector"]["matches"] == []
        assert result.structured_content["index_status"] == {"vector": "ready"}

    def test_mode_vector_reports_building_for_a_filtered_in_scope_note_with_no_vector_row_yet(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Regression for the review's exact repro: an out-of-filter ready note must not mask an in-scope building one.

        Before this fix, an empty vector.matches page for the in-scope note (it has no vector row
        yet, so it can never be a KNN match) fell back to a corpus-wide has_any_indexed() check,
        which only saw the *other*, outside-filter note's ready status and reported "ready" - even
        though the caller's requested scope (provider="europepmc") had nothing searchable yet.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                await client.call_tool(
                    "research_notes_create",
                    arguments={"provider": "arxiv", "identifier": "A", "text": "ready note outside the filter"},
                )
                await mcp_obj._embedding_scheduler.wait_all()

                # Create the in-scope note directly against the notes backend, bypassing
                # research_notes_create's own embedding-scheduler trigger, so it genuinely has no
                # vector row at all - then fake a live building task for it, the same gate
                # technique test_mode_vector_reports_building_while_a_reembed_task_is_in_flight uses.
                in_scope_note = await mcp_obj._notes_backend.create(
                    "europepmc", "B", None, "in-scope note still building", anchors=[], tags=[]
                )
                gate = asyncio.Event()
                mcp_obj._embedding_scheduler.schedule(("note", in_scope_note.id), gate.wait)
                try:
                    return await client.call_tool(
                        "research_notes_search",
                        arguments={"keyword": "attention-based models", "mode": "vector", "provider": "europepmc"},
                    )
                finally:
                    gate.set()
                    await mcp_obj._embedding_scheduler.wait_all()

        result = asyncio.run(scenario())
        assert result.structured_content["vector"]["matches"] == []
        assert result.structured_content["index_status"] == {"vector": "building"}

    def test_negative_offset_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """D7: a negative offset must raise, not silently fall into Python's from-the-end slicing."""
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"offset": -1})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_zero_limit_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """D7: limit=0 must raise, not silently produce a zero-width page."""
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"limit": 0})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_negative_limit_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """D7: a negative limit must raise, not flow into a negative KNN limit downstream."""
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"limit": -5})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_mode_vector_without_keyword_is_a_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"mode": "vector"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())

    def test_unrecognised_mode_raises_tool_error(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool("research_notes_search", arguments={"mode": "graph"})

        with pytest.raises(ToolError):
            asyncio.run(scenario())


class TestNotesExportResource:
    """End-to-end MCP resource tests for notes://{note_id}/export."""

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        storage_dir = tmp_path / "storage"
        notes_dir = tmp_path / "notes"
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", storage_dir)
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", notes_dir)
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        return Client(transport=server_with_features, timeout=60)

    def test_export_resource_returns_note_export_shape(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-1",
                        "format": "pdf",
                        "text": "a note about the ablation study",
                        "tags": ["methodology"],
                    },
                )
                note_id = created.structured_content["id"]
                result = await client.read_resource(f"notes://{note_id}/export")
                import json

                payload = json.loads(result[0].text)
                assert payload["suggested_filename"] == f"{note_id}.md"
                assert payload["markdown_body"] == "a note about the ablation study"
                assert payload["frontmatter"]["tags"] == ["methodology"]
                return True

        assert asyncio.run(scenario())

    def test_export_resource_missing_note_is_not_found(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                await client.read_resource("notes://does-not-exist/export")

        with pytest.raises(McpError):
            asyncio.run(scenario())


class TestVectorSearchWiring:
    """Tests for the EmbeddingBackend/VectorSearchBackend/SearchMechanism wiring in `PriorisMCP.__init__`."""

    def test_embedding_backend_uses_configured_model(self, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        mcp_obj = PriorisMCP()
        assert mcp_obj._embedding_backend.model_name == "BAAI/bge-small-en-v1.5"

    def test_search_mechanisms_registered(self):
        mcp_obj = PriorisMCP()
        assert set(mcp_obj._search_mechanisms.keys()) == {"fts", "vector"}

    def test_vector_dir_respects_grouping_seam(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        assert mcp_obj._document_vector_backend._path.parent == tmp_path / "vectors"


class TestVectorReconciliation:
    """Tests for PriorisMCP's corpus-wide vector-index reconciliation (Task 7)."""

    def test_force_vector_reconnect_does_not_raise_on_a_fresh_corpus(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        asyncio.run(mcp_obj._force_vector_reconnect())  # must not raise

    def test_reconcile_schedules_a_not_built_document(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """A document written+chunked but never embedded.

        Mirrors persist_parsed_markdown's own entries shape, built directly against
        storage/manifest rather than through a provider - matching how
        test_vector_sqlite_vec_document_backend.py builds fixtures directly against backends.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            assert await mcp_obj._document_vector_backend.status("arxiv", "2106.09685v2", "pdf") == "not_built"

            await mcp_obj.reconcile_vector_index()
            await mcp_obj._embedding_scheduler.wait_all()

            return await mcp_obj._document_vector_backend.status("arxiv", "2106.09685v2", "pdf")

        status = asyncio.run(scenario())
        assert status == "ready"

    def test_reconcile_skips_a_document_already_ready_under_the_current_model(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            await mcp_obj._document_vector_backend.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"chunk_id": "c1", "start": 0, "length": 20, "text": "Body text."}]
            )
            await mcp_obj.reconcile_vector_index()
            return mcp_obj._rebuild_progress.documents.total

        total = asyncio.run(scenario())
        assert total == 0  # already ready - nothing scheduled

    def test_reconcile_reindexes_a_document_not_built_under_a_renamed_model(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """The document was indexed under a since-renamed model.

        `stale` isn't observable (Fix 1), so the first connect under the real model already
        reports `not_built`; reconciliation still restores it to `ready`.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            # Index under a stand-in "old" model name directly against the same file, then point
            # the real mcp_obj (a differently-named model) at it - mirrors the existing
            # test_status_not_built_after_a_model_change_wipes_the_prior_status_row pattern.
            old_backend = SqliteVecDocumentBackend(
                tmp_path / "vectors" / "vectors.sqlite3", _RenamedStub(mcp_obj._embedding_backend, "old-model")
            )
            await old_backend.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"chunk_id": "c1", "start": 0, "length": 20, "text": "old text"}]
            )
            assert await mcp_obj._document_vector_backend.status("arxiv", "2106.09685v2", "pdf") == "not_built"

            await mcp_obj.reconcile_vector_index()
            await mcp_obj._embedding_scheduler.wait_all()
            return await mcp_obj._document_vector_backend.status("arxiv", "2106.09685v2", "pdf")

        status = asyncio.run(scenario())
        assert status == "ready"

    def test_reconcile_schedules_a_not_built_note(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()

        async def scenario():
            note = await mcp_obj._notes_backend.create("arxiv", "2106.09685v2", None, "a note about attention")
            # Cancel the create-triggered live embed so this note starts genuinely not_built -
            # isolates reconciliation's own scheduling from the create-time trigger already
            # covered by existing tests.
            await mcp_obj._embedding_scheduler.cancel(("note", note.id))
            assert await mcp_obj._note_vector_backend.status(note.id) == "not_built"

            await mcp_obj.reconcile_vector_index()
            await mcp_obj._embedding_scheduler.wait_all()
            return await mcp_obj._note_vector_backend.status(note.id)

        status = asyncio.run(scenario())
        assert status == "ready"

    def test_reconcile_updates_progress_counters(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            note = await mcp_obj._notes_backend.create("arxiv", "2106.09685v2", None, "a note")
            await mcp_obj._embedding_scheduler.cancel(("note", note.id))

            await mcp_obj.reconcile_vector_index()
            before = (mcp_obj._rebuild_progress.documents.total, mcp_obj._rebuild_progress.notes.total)
            before_pending = (
                mcp_obj._rebuild_progress.documents.pending,
                mcp_obj._rebuild_progress.notes.pending,
            )
            await mcp_obj._embedding_scheduler.wait_all()
            after_pending = (mcp_obj._rebuild_progress.documents.pending, mcp_obj._rebuild_progress.notes.pending)
            after_succeeded = (
                mcp_obj._rebuild_progress.documents.succeeded,
                mcp_obj._rebuild_progress.notes.succeeded,
            )
            return before, before_pending, after_pending, after_succeeded

        before, before_pending, after_pending, after_succeeded = asyncio.run(scenario())
        assert before == (1, 1)
        assert before_pending == (1, 1)
        assert after_pending == (0, 0)
        assert after_succeeded == (1, 1)

    def test_reconcile_document_reembed_failure_reports_failed_not_perpetually_pending(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Regression for ADR-00031: a failed document re-embed must not leave `pending` stuck forever.

        Before this fix, `EmbeddingScheduler` caught and only logged the failure, with no callback
        to `VectorRebuildProgress` - the resource would report `remaining=1` (now `pending=1`)
        forever, indistinguishable from a task genuinely still running.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def _failing_index_entries(*args, **kwargs):
            raise RuntimeError("boom - embedding backend unreachable")

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            monkeypatch.setattr(mcp_obj._document_vector_backend, "index_entries", _failing_index_entries)

            await mcp_obj.reconcile_vector_index()
            await mcp_obj._embedding_scheduler.wait_all()
            async with client:
                result = await client.read_resource("research://vector-index/rebuild-status")
            return json.loads(cast(TextResourceContents, result[0]).text)

        payload = asyncio.run(scenario())
        assert payload["documents"] == {"total": 1, "pending": 0, "succeeded": 0, "failed": 1, "active": False}

    def test_reconcile_note_reembed_failure_reports_failed_not_perpetually_pending(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Mirror of the document failure test, isolating the note re-embed wrapper's except-branch."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def _failing_index_note(*args, **kwargs):
            raise RuntimeError("boom - embedding backend unreachable")

        async def scenario():
            await mcp_obj._notes_backend.create("arxiv", "2106.09685v2", None, "a note")
            monkeypatch.setattr(mcp_obj._note_vector_backend, "index_note", _failing_index_note)

            await mcp_obj.reconcile_vector_index()
            await mcp_obj._embedding_scheduler.wait_all()
            async with client:
                result = await client.read_resource("research://vector-index/rebuild-status")
            return json.loads(cast(TextResourceContents, result[0]).text)

        payload = asyncio.run(scenario())
        assert payload["notes"] == {"total": 1, "pending": 0, "succeeded": 0, "failed": 1, "active": False}

    def test_reconcile_document_reembed_cancellation_counts_as_neither_success_nor_failure(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """A cancelled (e.g. shutdown, or deleted mid-flight) document re-embed is not a failure."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        gate = asyncio.Event()

        async def _hanging_index_entries(*args, **kwargs):
            await gate.wait()

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            monkeypatch.setattr(mcp_obj._document_vector_backend, "index_entries", _hanging_index_entries)

            await mcp_obj.reconcile_vector_index()
            assert mcp_obj._embedding_scheduler.is_building(("arxiv", "2106.09685v2", "pdf"))
            # Let the freshly-created task actually run at least once and reach the `gate.wait()`
            # suspension point before cancelling it - cancelling a task that has never been
            # stepped at all bypasses its own try/except entirely (nothing has executed yet),
            # which would make this test pass for the wrong reason (no code ran, not "cancellation
            # handled correctly").
            for _ in range(10):
                await asyncio.sleep(0)
            # cancel() awaits the task to actually stop before returning - Task.cancel() delivers
            # CancelledError at the suspended `await gate.wait()`, so the gate itself never needs
            # to be set for this to unblock.
            await mcp_obj._embedding_scheduler.cancel(("arxiv", "2106.09685v2", "pdf"))
            return mcp_obj._rebuild_progress.documents

        documents = asyncio.run(scenario())
        assert documents.pending == 0
        assert documents.succeeded == 0
        assert documents.failed == 0

    def test_reconcile_note_reembed_cancellation_counts_as_neither_success_nor_failure(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Mirror of the document cancellation test, isolating the note re-embed wrapper's cancel-branch."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        gate = asyncio.Event()

        async def _hanging_index_note(*args, **kwargs):
            await gate.wait()

        async def scenario():
            note = await mcp_obj._notes_backend.create("arxiv", "2106.09685v2", None, "a note")
            monkeypatch.setattr(mcp_obj._note_vector_backend, "index_note", _hanging_index_note)

            await mcp_obj.reconcile_vector_index()
            assert mcp_obj._embedding_scheduler.is_building(("note", note.id))
            # Let the freshly-created task actually run at least once and reach the `gate.wait()`
            # suspension point before cancelling it - see the matching comment in the document
            # cancellation test above for why.
            for _ in range(10):
                await asyncio.sleep(0)
            # cancel() awaits the task to actually stop before returning - Task.cancel() delivers
            # CancelledError at the suspended `await gate.wait()`, so the gate itself never needs
            # to be set for this to unblock.
            await mcp_obj._embedding_scheduler.cancel(("note", note.id))
            return mcp_obj._rebuild_progress.notes

        notes = asyncio.run(scenario())
        assert notes.pending == 0
        assert notes.succeeded == 0
        assert notes.failed == 0

    def test_reconcile_vector_index_isolates_document_and_note_failures(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """A failure enumerating/scheduling documents must not skip notes, and must not propagate.

        Regression test for the final whole-plan review's Important #1: before the fix, a raise
        from `_reconcile_documents` (e.g. a corrupt catalogue row) skipped `_reconcile_notes`
        entirely and re-raised out of `reconcile_vector_index()` itself.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        calls: list[str] = []

        async def _failing_reconcile_documents():
            calls.append("documents")
            raise RuntimeError("boom - corrupt catalogue row")

        async def _fake_reconcile_notes():
            calls.append("notes")

        monkeypatch.setattr(mcp_obj, "_reconcile_documents", _failing_reconcile_documents)
        monkeypatch.setattr(mcp_obj, "_reconcile_notes", _fake_reconcile_notes)

        asyncio.run(mcp_obj.reconcile_vector_index())  # must not raise

        assert calls == ["documents", "notes"]  # notes still ran despite documents failing

    def test_reconcile_vector_index_a_notes_failure_does_not_raise(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Mirror of the documents-failure test, isolating the notes except-branch specifically."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        calls: list[str] = []

        async def _fake_reconcile_documents():
            calls.append("documents")

        async def _failing_reconcile_notes():
            calls.append("notes")
            raise RuntimeError("boom - corrupt note record")

        monkeypatch.setattr(mcp_obj, "_reconcile_documents", _fake_reconcile_documents)
        monkeypatch.setattr(mcp_obj, "_reconcile_notes", _failing_reconcile_notes)

        asyncio.run(mcp_obj.reconcile_vector_index())  # must not raise

        assert calls == ["documents", "notes"]

    def test_lifespan_shutdown_does_not_crash_when_reconciliation_already_failed(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Reproduces the review's exact crash scenario at shutdown.

        If the background reconciliation task were to complete with a real (non-CancelledError)
        exception before shutdown runs, `task.cancel()` on an already-done task is a no-op and a
        naive `await task` would re-raise that original exception straight through
        `contextlib.suppress(asyncio.CancelledError)`, which doesn't catch it, crashing server
        shutdown. `reconcile_vector_index()`'s own documents/notes isolation must prevent this by
        never letting a sub-call's exception reach the task at all - proven here by monkeypatching
        a sub-call (not `reconcile_vector_index` itself) to raise, so the real, fixed top-level
        method runs.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()

        async def _fake_force_reconnect():
            return None

        async def _failing_reconcile_documents():
            raise RuntimeError("boom - corrupt catalogue row")

        async def _fake_reconcile_notes():
            return None

        monkeypatch.setattr(mcp_obj, "_force_vector_reconnect", _fake_force_reconnect)
        monkeypatch.setattr(mcp_obj, "_reconcile_documents", _failing_reconcile_documents)
        monkeypatch.setattr(mcp_obj, "_reconcile_notes", _fake_reconcile_notes)

        lifespan = _vector_reconciliation_lifespan(mcp_obj)

        async def scenario():
            async with lifespan(None):
                # Wait for the background task to actually finish (with the sub-call's exception
                # already isolated/logged) *before* the `async with` block exits - reproduces the
                # review's exact "already-done, non-cancelled" scenario at shutdown time, not a
                # still-pending task that shutdown's own cancel() would legitimately cancel.
                task = cast("asyncio.Task", mcp_obj._reconciliation_task)
                for _ in range(50):
                    if task.done():
                        break
                    await asyncio.sleep(0.05)
                assert task.done()
                assert task.exception() is None  # isolated internally, never propagated to the task

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))  # must not raise

    def test_rebuild_status_resource_reports_zero_on_a_fresh_corpus(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            async with client:
                return await client.read_resource("research://vector-index/rebuild-status")

        result = asyncio.run(scenario())
        payload = json.loads(cast(TextResourceContents, result[0]).text)
        zero_mechanism = {"total": 0, "pending": 0, "succeeded": 0, "failed": 0, "active": False}
        assert payload == {"documents": zero_mechanism, "notes": zero_mechanism}

    def test_rebuild_status_resource_reflects_progress_mid_reconciliation(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        client = Client(transport=mcp_obj.register_features(FastMCP()), timeout=60)

        async def scenario():
            await mcp_obj._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
            manifest = mcp_obj._storage.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_chunk_rows(
                "pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1"
            )
            await mcp_obj.reconcile_vector_index()
            async with client:
                mid_result = await client.read_resource("research://vector-index/rebuild-status")
                mid_payload = json.loads(cast(TextResourceContents, mid_result[0]).text)
                await mcp_obj._embedding_scheduler.wait_all()
                done_result = await client.read_resource("research://vector-index/rebuild-status")
                done_payload = json.loads(cast(TextResourceContents, done_result[0]).text)
            return mid_payload, done_payload

        mid_payload, done_payload = asyncio.run(scenario())
        assert mid_payload["documents"] == {"total": 1, "pending": 1, "succeeded": 0, "failed": 0, "active": True}
        assert done_payload["documents"] == {"total": 1, "pending": 0, "succeeded": 1, "failed": 0, "active": False}

    def test_lifespan_force_reconnects_before_yielding(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        calls: list[str] = []

        async def _fake_force_reconnect():
            calls.append("force_reconnect")

        async def _fake_reconcile():
            calls.append("reconcile")

        monkeypatch.setattr(mcp_obj, "_force_vector_reconnect", _fake_force_reconnect)
        monkeypatch.setattr(mcp_obj, "reconcile_vector_index", _fake_reconcile)

        lifespan = _vector_reconciliation_lifespan(mcp_obj)

        async def scenario():
            async with lifespan(None):
                pass
            # lifespan's own __aexit__ already cancelled+awaited (and suppressed) this task while
            # exiting the `async with` block above, since the fake reconcile never got a chance to
            # run before shutdown - re-awaiting an already-cancelled task always re-raises
            # CancelledError, so tolerate it here too; this is cleanup, not the assertion.
            with contextlib.suppress(asyncio.CancelledError):
                await cast("asyncio.Task", mcp_obj._reconciliation_task)

        asyncio.run(scenario())
        assert calls[0] == "force_reconnect"

    def test_lifespan_does_not_block_on_reconciliation_completing(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """The core non-blocking guarantee: __aenter__ must return before reconcile_vector_index finishes."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        never_set = asyncio.Event()

        async def _fake_force_reconnect():
            return None

        async def _fake_reconcile():
            await never_set.wait()  # would hang forever if awaited synchronously by the lifespan

        monkeypatch.setattr(mcp_obj, "_force_vector_reconnect", _fake_force_reconnect)
        monkeypatch.setattr(mcp_obj, "reconcile_vector_index", _fake_reconcile)

        lifespan = _vector_reconciliation_lifespan(mcp_obj)

        async def scenario():
            async with lifespan(None):
                still_pending = not cast("asyncio.Task", mcp_obj._reconciliation_task).done()
            # By the time `async with` exits, lifespan's own __aexit__ has already
            # cancelled+awaited (and suppressed) this task, so setting the event here is too late
            # to let it finish normally - re-awaiting an already-cancelled task always re-raises
            # CancelledError, so tolerate it; the guarantee this test proves is `still_pending`
            # above, captured before shutdown ran.
            never_set.set()
            with contextlib.suppress(asyncio.CancelledError):
                await cast("asyncio.Task", mcp_obj._reconciliation_task)
            return still_pending

        still_pending = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        assert still_pending is True

    def test_lifespan_cancels_the_background_task_on_shutdown(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        never_set = asyncio.Event()

        async def _fake_force_reconnect():
            return None

        async def _fake_reconcile():
            await never_set.wait()

        monkeypatch.setattr(mcp_obj, "_force_vector_reconnect", _fake_force_reconnect)
        monkeypatch.setattr(mcp_obj, "reconcile_vector_index", _fake_reconcile)

        lifespan = _vector_reconciliation_lifespan(mcp_obj)

        async def scenario():
            async with lifespan(None):
                pass
            return cast("asyncio.Task", mcp_obj._reconciliation_task).cancelled()

        cancelled = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        assert cancelled is True

    def test_app_wires_the_reconciliation_lifespan(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """Integration check that app() actually passes lifespan= through, not just that the factory works standalone."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_app = app()

        async def scenario():
            async with Client(transport=mcp_app, timeout=60):
                pass

        asyncio.run(scenario())  # must not raise - confirms the lifespan context manager is entered cleanly

    def test_full_server_startup_reconciles_a_dimension_changing_model_swap(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Regression test for the review's exact repro, through the full server + lifespan.

        Original bug: index one document, reopen the same database at a different embedding
        dimension, call search() immediately - count() went from 1 to 0, search() returned [],
        with no path back. This must now self-heal automatically via app()'s lifespan: the
        corpus-wide drop still happens (deliberately, during startup's synchronous force-reconnect
        phase - count genuinely goes to 0), but the background reconciliation then re-embeds it
        back to ready under the real configured model, unlike the original bug where there was no
        path back at all.
        """
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")

        mcp_obj_a = PriorisMCP()
        asyncio.run(
            mcp_obj_a._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
        )
        manifest = mcp_obj_a._storage.manifest_for("arxiv", "2106.09685v2")
        asyncio.run(
            manifest.replace_chunk_rows("pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1")
        )
        # Index directly against the raw db file under a stub with both a different model name AND
        # a different (fake, fixed) dimension from the real configured model used by app() below -
        # genuinely exercises the dimension-changing half of the original repro, not just a
        # same-dimension name change (already covered by the sibling renamed-model test).
        old_backend = SqliteVecDocumentBackend(
            tmp_path / "vectors" / "vectors.sqlite3", _DifferentDimensionStub("old-model", 4)
        )
        asyncio.run(
            old_backend.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"chunk_id": "c1", "start": 0, "length": 20, "text": "old text"}]
            )
        )
        assert asyncio.run(old_backend.count()) == 1

        mcp_app = app()

        async def run_and_check():
            async with Client(transport=mcp_app, timeout=60):
                # lifespan's synchronous force-reconnect phase has fully run by this point - the
                # dimension mismatch is deliberately dropped here, not left to blow up on the next
                # ordinary request the way the original bug did.
                check_backend = SqliteVecDocumentBackend(
                    tmp_path / "vectors" / "vectors.sqlite3", FastEmbedBackend("BAAI/bge-small-en-v1.5")
                )
                count_immediately_after_reconnect = await check_backend.count()

                # Background reconciliation re-embeds it under the real model - poll status until
                # it settles rather than assuming it's already done the instant the force-reconnect
                # phase finished.
                status = None
                for _ in range(50):
                    status = await check_backend.status("arxiv", "2106.09685v2", "pdf")
                    if status == "ready":
                        break
                    await asyncio.sleep(0.05)
                final_count = await check_backend.count()
            return count_immediately_after_reconnect, status, final_count

        count_immediately_after_reconnect, final_status, final_count = asyncio.run(run_and_check())
        assert count_immediately_after_reconnect == 0  # deliberately dropped by the dimension mismatch
        assert final_status == "ready"  # self-healed by background reconciliation
        assert final_count == 1

    def test_full_server_startup_reconciles_a_same_dimension_renamed_model(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """The Low-finding half of the repro: same dimension, renamed model, must not go undetected."""
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")

        mcp_obj_a = PriorisMCP()
        asyncio.run(
            mcp_obj_a._storage.write("arxiv", "2106.09685v2", "pdf", b"# Title\n\nBody text.", artefact="markdown")
        )
        manifest = mcp_obj_a._storage.manifest_for("arxiv", "2106.09685v2")
        asyncio.run(
            manifest.replace_chunk_rows("pdf", [{"key": "c1", "start": 0, "length": 20}], scheme="heading-bounded-v1")
        )
        old_backend = SqliteVecDocumentBackend(
            tmp_path / "vectors" / "vectors.sqlite3", _RenamedStub(mcp_obj_a._embedding_backend, "old-model")
        )
        asyncio.run(
            old_backend.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"chunk_id": "c1", "start": 0, "length": 20, "text": "old text"}]
            )
        )

        mcp_app = app()

        async def run_and_wait():
            async with Client(transport=mcp_app, timeout=60):
                check_backend = SqliteVecDocumentBackend(
                    tmp_path / "vectors" / "vectors.sqlite3", FastEmbedBackend("BAAI/bge-small-en-v1.5")
                )
                # Reconciliation's own re-embed runs in the background - poll status until it settles
                # rather than assuming it's already done the instant the client connection closes.
                for _ in range(50):
                    status = await check_backend.status("arxiv", "2106.09685v2", "pdf")
                    if status == "ready":
                        return status
                    await asyncio.sleep(0.05)
                return status

        final_status = asyncio.run(run_and_wait())
        assert final_status == "ready"
