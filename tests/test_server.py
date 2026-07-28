import asyncio
import base64
import json
import logging
import ssl

import httpx
import pytest
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import McpError

from prioris_mcp import EnvVars
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
                return await client.call_tool("research_arxiv_list_top_n", arguments={"category": "cs.CL", "n": 5})

        result = asyncio.run(scenario())
        assert len(result.structured_content["results"]) == 1

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
        payload = json.loads(resource_result[0].text)
        assert payload["markdown"] == "wor"
        assert payload["has_more"] is True

    def test_research_arxiv_parse_full_text_not_found_returns_error_envelope(
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

        result = asyncio.run(scenario())
        assert result.structured_content["error"] == "not_found"

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
        assert result.structured_content["provider"] == "arxiv"

    def test_research_resolve_identifier_returns_invalid_request_envelope_for_bad_format(
        self, tmp_path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """`format` is intentionally not a `Literal[...]` at the tool schema level.

        Valid values depend on the resolving provider, so an unsupported value (here, `arXiv`'s bare
        `ValueError` for anything other than pdf/html) must still come back as the standard
        `{"error": "invalid_request", ...}` envelope rather than as a raw uncaught exception.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        client = self._server_and_client(handler, tmp_path, monkeypatch)

        async def scenario():
            async with client:
                return await client.call_tool(
                    "research_resolve_identifier", arguments={"identifier": "2106.09685v2", "format": "xml"}
                )

        result = asyncio.run(scenario())
        assert result.structured_content["error"] == "invalid_request"

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
