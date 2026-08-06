import asyncio
import base64
import logging
import ssl
from pathlib import Path

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from prioris_mcp import EnvVars
from prioris_mcp.models.arxiv import ArxivCategoriesResult, ArxivCategory
from prioris_mcp.models.common import ArxivResolvedIdentifierResult, MarkdownPage
from prioris_mcp.server import PriorisMCP

logger = logging.getLogger(__name__)


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

    def test_exactly_two_resource_templates_are_registered(self, tmp_path, monkeypatch: "pytest.MonkeyPatch"):
        """No metadata resource exists.

        Only the fulltext/markdown templates documented in
        docs/requirement-specification/06-interface-specification.md#resources are registered.
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
            "research://{provider}/{identifier}/{format}/markdown{?offset,limit}",
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
