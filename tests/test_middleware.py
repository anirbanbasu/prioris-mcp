import asyncio
import base64
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware.caching import ReadResourceSettings, ResponseCachingMiddleware

from prioris_mcp import EnvVars
from prioris_mcp.middleware import (
    DecodeBinaryResourceContentMiddleware,
    EncodeBinaryResourceContentMiddleware,
    LiveResourceCacheBypassMiddleware,
    ResponseMetadataMiddleware,
    StripUnknownArgumentsMiddleware,
)
from prioris_mcp.models.arxiv import ArxivCategoriesResult, ArxivCategory
from prioris_mcp.server import PriorisMCP

logger = logging.getLogger(__name__)

# A single-result arXiv Atom feed used to stub the arXiv HTTP API for these tests. `greet` used
# to be the vehicle for exercising these generic middlewares; now that it's been removed (see
# CLAUDE.md - it was scaffolding, not a pattern to preserve), `research_arxiv_search` fills the
# same role, with its one outbound HTTP call stubbed so these tests stay hermetic.
_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2106.09685v2</id>
    <published>2021-06-17T17:59:33Z</published>
    <updated>2021-10-16T13:56:12Z</updated>
    <title>A Paper</title>
    <summary>An abstract.</summary>
    <author><name>Jane Doe</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <link href="http://arxiv.org/pdf/2106.09685v2" rel="related" type="application/pdf"/>
  </entry>
</feed>"""


def _stubbed_mcp_obj() -> PriorisMCP:
    """A `PriorisMCP` instance whose arXiv HTTP calls are stubbed, for exercising middleware generically."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_FEED_XML)

    mcp_obj = PriorisMCP()
    mcp_obj._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mcp_obj._arxiv_provider._http_client = mcp_obj._http_client
    return mcp_obj


class TestStripUnknownArgumentsMiddleware:
    """Dedicated test class for the StripUnknownArgumentsMiddleware."""

    @pytest.fixture(scope="class")
    @classmethod
    def mcp_server(cls):
        """Fixture to create an MCP server instance with the middleware."""
        server = FastMCP()
        mcp_obj = _stubbed_mcp_obj()
        server_with_features = mcp_obj.register_features(server)
        server_with_features.add_middleware(StripUnknownArgumentsMiddleware())
        return server_with_features

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def mcp_client(cls, mcp_server):
        """Fixture to create a client for the MCP server."""
        mcp_client = Client(transport=mcp_server, timeout=60)
        return mcp_client

    async def call_tool(self, tool_name: str, mcp_client: Client, **kwargs):
        """Helper method to call a tool on the MCP server."""
        async with mcp_client:
            result = await mcp_client.call_tool(tool_name, arguments=kwargs)
            await mcp_client.close()
        return result

    def test_strip_unknown_arguments(self, mcp_client: Client, caplog):
        """Test that unknown arguments are stripped from tool calls and logged."""
        tool_name = "research_arxiv_search"
        valid_query = "cat:cs.CL"
        unknown_arg_value = "This should be stripped"

        with caplog.at_level(logging.INFO):
            results = asyncio.run(
                self.call_tool(
                    tool_name,
                    mcp_client,
                    query=valid_query,
                    unknown_argument=unknown_arg_value,
                )
            )

        # Verify the tool call succeeded with the valid argument passed through
        assert hasattr(results, "content"), "Expected results to have 'content' attribute"
        assert hasattr(results, "structured_content"), "Expected results to have 'structured_content' attribute"
        assert results.structured_content["total_results"] == 1, "Expected the stubbed feed's single result"

        # Verify logging occurred for unknown arguments
        assert any(f"Unknown arguments for tool '{tool_name}'" in record.message for record in caplog.records), (
            "Expected logging of unknown arguments"
        )

        # Verify the unknown argument was identified in the logs
        assert any("unknown_argument" in record.message for record in caplog.records), (
            "Expected 'unknown_argument' to be logged as unknown"
        )

    def test_all_arguments_unknown(self, mcp_client: Client, caplog):
        """Test behaviour when every argument besides the required one is unknown."""
        tool_name = "research_arxiv_search"

        with caplog.at_level(logging.INFO):
            results = asyncio.run(
                self.call_tool(
                    tool_name,
                    mcp_client,
                    query="cat:cs.CL",
                    completely_unknown_arg="value1",
                    another_unknown_arg="value2",
                )
            )

        # Verify the tool call still succeeds (unknown args stripped, defaults used for the rest)
        assert hasattr(results, "content"), "Expected results to have 'content' attribute"
        assert hasattr(results, "structured_content"), "Expected results to have 'structured_content' attribute"
        assert results.structured_content["total_results"] == 1

        # Verify logging occurred
        assert any(f"Unknown arguments for tool '{tool_name}'" in record.message for record in caplog.records), (
            "Expected logging of unknown arguments"
        )

    def test_no_arguments_provided(self, mcp_client: Client, caplog):
        """Test that middleware handles a call with only the required argument, and no unknown ones."""
        tool_name = "research_arxiv_search"

        with caplog.at_level(logging.INFO):
            results = asyncio.run(self.call_tool(tool_name, mcp_client, query="cat:cs.CL"))

        # Verify the tool call succeeds
        assert hasattr(results, "content"), "Expected results to have 'content' attribute"
        assert hasattr(results, "structured_content"), "Expected results to have 'structured_content' attribute"

        # Verify no middleware logging for this case (no args to strip)
        middleware_logs = [record for record in caplog.records if "Unknown arguments" in record.message]
        assert len(middleware_logs) == 0, "Expected no middleware logging when no unknown arguments are provided"

    def test_only_valid_arguments(self, mcp_client: Client, caplog):
        """Test that middleware doesn't interfere when only valid arguments are provided."""
        tool_name = "research_arxiv_search"

        with caplog.at_level(logging.INFO):
            results = asyncio.run(self.call_tool(tool_name, mcp_client, query="cat:cs.CL", max_results=5))

        # Verify the tool call succeeds with the valid arguments
        assert hasattr(results, "content"), "Expected results to have 'content' attribute"
        assert results.structured_content["total_results"] == 1

        # Verify no unknown argument logging
        unknown_arg_logs = [record for record in caplog.records if "Unknown arguments" in record.message]
        assert len(unknown_arg_logs) == 0, "Expected no unknown argument logging for valid args only"

    def test_mixed_valid_and_unknown_arguments(self, mcp_client: Client, caplog):
        """Test middleware behavior with a mix of valid and unknown arguments."""
        tool_name = "research_arxiv_search"

        with caplog.at_level(logging.INFO):
            results = asyncio.run(
                self.call_tool(
                    tool_name,
                    mcp_client,
                    query="cat:cs.CL",
                    unknown1="value1",
                    unknown2={"key": "value2"},
                    unknown3=3.14,
                )
            )

        # Verify valid argument was used
        assert results.structured_content["total_results"] == 1

        # Verify multiple unknown arguments are logged
        unknown_logs = [
            record for record in caplog.records if f"Unknown arguments for tool '{tool_name}'" in record.message
        ]
        assert len(unknown_logs) > 0, "Expected logging for unknown arguments"

        # Verify all three unknown arguments are mentioned
        log_messages = " ".join([record.message for record in caplog.records])
        assert "unknown1" in log_messages, "Expected 'unknown1' in logs"
        assert "unknown2" in log_messages, "Expected 'unknown2' in logs"
        assert "unknown3" in log_messages, "Expected 'unknown3' in logs"


class TestBinaryResourceContentCachingMiddleware:
    """Dedicated test class for Encode/DecodeBinaryResourceContentMiddleware.

    Sandwiches a real `ResponseCachingMiddleware` (caching enabled) between the two, matching
    server.py's app() ordering, so these tests exercise the actual bug this pair fixes: fastmcp's
    cache wrapper crashes JSON-serialising raw (non-UTF-8-safe) `bytes` resource content.
    """

    # A minimal, valid, liteparse-parseable PDF - same fixture convention as
    # TestLocalFileTools._PDF_BYTES in test_server.py.
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

    def _server_and_client(self):
        """Build a fresh server+client pair with the caching sandwich, mirroring app()'s ordering."""
        server = FastMCP()
        mcp_obj = _stubbed_mcp_obj()
        server_with_features = mcp_obj.register_features(server)
        server_with_features.add_middleware(DecodeBinaryResourceContentMiddleware())
        server_with_features.add_middleware(
            ResponseCachingMiddleware(read_resource_settings=ReadResourceSettings(ttl=3600, enabled=True))
        )
        server_with_features.add_middleware(EncodeBinaryResourceContentMiddleware())
        return Client(transport=server_with_features, timeout=60)

    def test_binary_fulltext_resource_round_trips_on_cache_miss_and_hit(self):
        """A non-UTF-8-safe fulltext resource must not crash, and must round-trip byte-for-byte.

        The first read is a cache miss (exercises Encode's bytes branch, then Decode's marked
        branch); the second is a cache hit (Encode never runs at all - only Decode's marked
        branch reverses what's already stored encoded).
        """
        client = self._server_and_client()
        payload = b"%PDF-1.4\n" + bytes([0xBF, 0x00, 0x01, 0x02]) + b"trailer garbage"

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": base64.b64encode(payload).decode("ascii")},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                uri = f"research://localfile/{caller_facing_id}/pdf/fulltext"
                first_read = await client.read_resource(uri)
                second_read = await client.read_resource(uri)
                return first_read, second_read

        first_read, second_read = asyncio.run(scenario())
        assert base64.b64decode(first_read[0].blob) == payload
        assert base64.b64decode(second_read[0].blob) == payload
        assert first_read[0].mimeType == "application/octet-stream"

    def test_text_resource_passes_through_untouched_on_cache_miss_and_hit(self):
        """A `str`-content resource (parsed Markdown) is untouched by either middleware.

        Exercises Encode's non-bytes passthrough branch and Decode's unmarked passthrough branch,
        on both a cache miss and a cache hit.
        """
        client = self._server_and_client()

        async def scenario():
            async with client:
                fetch_result = await client.call_tool(
                    "research_localfile_fetch_full_text",
                    arguments={"content_base64": base64.b64encode(self._PDF_BYTES).decode("ascii")},
                )
                caller_facing_id = fetch_result.structured_content["id"]
                await client.call_tool("research_localfile_parse_full_text", arguments={"id": caller_facing_id})
                uri = f"research://localfile/{caller_facing_id}/pdf/markdown"
                first_read = await client.read_resource(uri)
                second_read = await client.read_resource(uri)
                return first_read, second_read

        first_read, second_read = asyncio.run(scenario())
        assert "Hello World" in first_read[0].text
        assert first_read[0].text == second_read[0].text


class TestLiveResourceCacheBypassMiddleware:
    """Dedicated test class for LiveResourceCacheBypassMiddleware.

    Needs an isolated notes/storage directory (unlike `_stubbed_mcp_obj()`'s bare `PriorisMCP()`,
    which would hit the real default `~/.local/share/prioris-mcp/`), so this mirrors
    `TestResearchNotesCreate._server_and_client` in test_server.py, additionally wiring up the
    middleware chain under test: LiveResourceCacheBypassMiddleware before a real (enabled)
    ResponseCachingMiddleware, matching server.py's app() ordering.
    """

    def _server_and_client(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> tuple[PriorisMCP, Client]:
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "storage")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_NOTES_DIR", tmp_path / "notes")
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_VECTOR_DIR", tmp_path / "vectors")
        mcp_obj = PriorisMCP()
        server = FastMCP()
        server_with_features = mcp_obj.register_features(server)
        server_with_features.add_middleware(LiveResourceCacheBypassMiddleware())
        server_with_features.add_middleware(
            ResponseCachingMiddleware(read_resource_settings=ReadResourceSettings(ttl=3600, enabled=True))
        )
        return mcp_obj, Client(transport=server_with_features, timeout=60)

    def test_notes_export_resource_is_never_served_from_cache(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """Create -> read export -> update -> read export again must reflect the update.

        Without the bypass, ResponseCachingMiddleware would serve the second read from its cache,
        returning the pre-update `markdown_body` instead of the current one.
        """
        _mcp_obj, client = self._server_and_client(tmp_path, monkeypatch)

        async def scenario():
            async with client:
                created = await client.call_tool(
                    "research_notes_create",
                    arguments={
                        "provider": "localfile",
                        "identifier": "id-1",
                        "format": "pdf",
                        "text": "original text",
                    },
                )
                note_id = created.structured_content["id"]  # ty: ignore[not-subscriptable]
                uri = f"notes://{note_id}/export"
                first_read = await client.read_resource(uri)
                await client.call_tool("research_notes_update", arguments={"note_id": note_id, "text": "updated text"})
                second_read = await client.read_resource(uri)
                return first_read, second_read

        first_read, second_read = asyncio.run(scenario())
        first_payload = json.loads(first_read[0].text)
        second_payload = json.loads(second_read[0].text)
        assert first_payload["markdown_body"] == "original text"
        assert second_payload["markdown_body"] == "updated text"

    def test_rebuild_status_resource_is_never_served_from_cache(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        """Read at remaining=1, mutate progress in-memory, read again - must reflect the mutation.

        Without the bypass, ResponseCachingMiddleware would serve the second read from its cache
        for up to the configured TTL (5 minutes by default in production), even though the
        resource is documented as live reconciliation progress a caller polls.
        """
        mcp_obj, client = self._server_and_client(tmp_path, monkeypatch)
        uri = "research://vector-index/rebuild-status"

        async def scenario():
            async with client:
                mcp_obj._rebuild_progress.set_documents_total(1)
                first_read = await client.read_resource(uri)
                mcp_obj._rebuild_progress.document_succeeded()
                second_read = await client.read_resource(uri)
                return first_read, second_read

        first_read, second_read = asyncio.run(scenario())
        first_payload = json.loads(first_read[0].text)
        second_payload = json.loads(second_read[0].text)
        assert first_payload["documents"] == {
            "total": 1,
            "pending": 1,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "active": True,
        }
        assert second_payload["documents"] == {
            "total": 1,
            "pending": 0,
            "succeeded": 1,
            "failed": 0,
            "cancelled": 0,
            "active": False,
        }

    def test_an_ordinary_resource_is_still_served_from_cache(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        """A companion check that the bypass is scoped, not a blanket cache disable.

        Stubs `research://arxiv/categories`'s underlying provider call to return a different
        payload each call, then asserts the second read still equals the first - proving it was
        actually served from the cache (a fresh, non-cached call would observe the changed value).
        """
        mcp_obj, client = self._server_and_client(tmp_path, monkeypatch)
        uri = "research://arxiv/categories"
        responses = iter(
            [
                ArxivCategoriesResult(categories=[ArxivCategory(code="cs.CL", name="first call")]),
                ArxivCategoriesResult(categories=[ArxivCategory(code="cs.CL", name="second call")]),
            ]
        )

        async def _fake_list_categories():
            return next(responses)

        monkeypatch.setattr(mcp_obj._arxiv_provider, "list_categories", _fake_list_categories)

        async def scenario():
            async with client:
                first_read = await client.read_resource(uri)
                second_read = await client.read_resource(uri)
                return first_read, second_read

        first_read, second_read = asyncio.run(scenario())
        assert first_read[0].text == second_read[0].text
        assert "first call" in first_read[0].text

    def test_on_read_resource_passes_non_bypassed_uris_through_to_call_next(self):
        """The bypass is scoped to notes:// and the rebuild-status URI - everything else passes through.

        Inspects the middleware's on_read_resource logic directly, rather than round-tripping
        through a second live resource type, since a non-bypassed resource isn't available in this
        minimal, notes-only harness without overcomplicating the setup.
        """
        middleware = LiveResourceCacheBypassMiddleware()
        calls: list[object] = []

        class _StubMessage:
            uri = "research://arxiv/categories"

        class _StubContext:
            message = _StubMessage()
            fastmcp_context = None

        async def call_next(context):
            calls.append(context)
            return "sentinel"

        result = asyncio.run(middleware.on_read_resource(_StubContext(), call_next))
        assert result == "sentinel"
        assert len(calls) == 1


class TestResponseMetadataMiddleware:
    """Dedicated test class for the ResponseMetadataMiddleware."""

    @pytest.fixture(scope="class")
    @classmethod
    def mcp_server(cls):
        """Fixture to create an MCP server instance with the middleware."""
        server = FastMCP()
        mcp_obj = _stubbed_mcp_obj()
        server_with_features = mcp_obj.register_features(server)
        server_with_features.add_middleware(ResponseMetadataMiddleware())
        return server_with_features

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def mcp_client(cls, mcp_server):
        """Fixture to create a client for the MCP server."""
        mcp_client = Client(transport=mcp_server, timeout=60)
        return mcp_client

    async def call_tool(self, tool_name: str, mcp_client: Client, **kwargs):
        """Helper method to call a tool on the MCP server."""
        async with mcp_client:
            result = await mcp_client.call_tool(tool_name, arguments=kwargs)
            await mcp_client.close()
        return result

    def test_call_for_package_metadata(self, mcp_client: Client, caplog):
        """Test that metadata is added to tool responses and appropriate logging occurs."""
        tool_name = "research_arxiv_search"

        with caplog.at_level(logging.DEBUG):
            results = asyncio.run(self.call_tool(tool_name, mcp_client, query="cat:cs.CL"))

        # Verify the tool call succeeded with the valid argument
        assert hasattr(results, "content"), "Expected results to have 'content' attribute"
        assert hasattr(results, "structured_content"), "Expected results to have 'structured_content' attribute"
        assert results.structured_content["total_results"] == 1

        assert getattr(results, "meta", None) is not None, "Expected results to have a valid 'meta' attribute"
        assert ResponseMetadataMiddleware.PACKAGE_METADATA_KEY in results.meta, (
            f"Expected '{ResponseMetadataMiddleware.PACKAGE_METADATA_KEY}' in meta"
        )
        assert ResponseMetadataMiddleware.TIMING_METADATA_KEY in results.meta, (
            f"Expected '{ResponseMetadataMiddleware.TIMING_METADATA_KEY}' in meta"
        )
        assert "name" in results.meta[ResponseMetadataMiddleware.PACKAGE_METADATA_KEY], (
            "Expected 'name' in package metadata"
        )
        assert "version" in results.meta[ResponseMetadataMiddleware.PACKAGE_METADATA_KEY], (
            "Expected 'version' in package metadata"
        )
        assert results.meta[ResponseMetadataMiddleware.PACKAGE_METADATA_KEY]["name"] == "prioris-mcp"
        assert "tool_response_time_ms" in results.meta[ResponseMetadataMiddleware.TIMING_METADATA_KEY], (
            "Expected 'tool_response_time_ms' in timing metadata"
        )
        assert isinstance(
            results.meta[ResponseMetadataMiddleware.TIMING_METADATA_KEY]["tool_response_time_ms"], float
        ), "Expected 'tool_response_time_ms' to be a float"

        # Verify logging occurred for metadata addition
        assert any("Added package metadata to tool response" in record.message for record in caplog.records), (
            "Expected debug logging of package metadata addition"
        )

    def test_time_operation_reraises_and_logs_on_call_next_failure(self, caplog):
        """`_time_operation`'s exception branch: this is a framework-level failure path.

        A real tool call never reaches here directly via a business-logic error - a tool method
        raising (e.g. `NotFoundError`) is converted to `ToolError` by FastMCP's own dispatch before
        `call_next` returns - so this is unit tested against `_time_operation` itself, standing in
        for `call_next` raising (e.g. a bug elsewhere in the middleware chain or in FastMCP's own
        dispatch), rather than driven through a full `Client`/`FastMCP` round trip.
        """

        async def failing_call_next(context):
            raise ValueError("boom")

        middleware = ResponseMetadataMiddleware()

        async def scenario():
            await middleware._time_operation(None, failing_call_next, "Tool 'unit_test'")

        with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="boom"):
            asyncio.run(scenario())

        assert any("Tool 'unit_test' failed after" in record.message for record in caplog.records), (
            "Expected a warning logged with the elapsed duration before the exception re-raises"
        )
