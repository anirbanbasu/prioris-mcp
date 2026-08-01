import asyncio
import logging

import httpx
import pytest
from fastmcp import Client, FastMCP

from prioris_mcp.middleware import ResponseMetadataMiddleware, StripUnknownArgumentsMiddleware
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
