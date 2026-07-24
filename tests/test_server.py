import asyncio
import logging
import re
from datetime import datetime

import pytest
from fastmcp import Client, FastMCP

from prioris_mcp.server import (
    PriorisMCP,
    package_version,
)

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

    def test_tool_greet(self, mcp_client: Client):
        """Test to call the greet tool on the MCP server."""
        tool_name = "greet"
        name_to_be_greeted = "Sherlock Holmes"
        results = asyncio.run(
            self.call_tool(
                tool_name,
                mcp_client,
                name=name_to_be_greeted,
            )
        )
        assert hasattr(results, "content"), "Expected the results to have a 'content' attribute."
        assert len(results.content) == 1, f"Expected one result for the {tool_name} tool."
        assert getattr(results, "structured_content", None) is not None, (
            "Expected the results to have a 'structured_content' attribute."
        )
        assert "result" in results.structured_content, "Expected the 'structured_content' to have a 'result' key."
        pattern = r"Hello(,?) (.+)! Welcome to the prioris-mcp (\d+\.\d+\.\d+(\.?[a-zA-Z]+\.?\d+)?) server! The current date time in UTC is ([\d\-T:.+]+). This response may be cached."
        result = results.structured_content["result"]
        match = re.match(pattern, result)
        assert match, (
            f"Expected the response to be a greeting in a specific format. The obtained response does not match the expected format: {result}"
        )
        name = match.group(2)  # Extracted name
        assert name == name_to_be_greeted if name_to_be_greeted else "World", (
            f"Expected the name in the greeting to be '{name_to_be_greeted}', but got '{name}'."
        )
        version = match.group(3)  # Extracted version
        assert version == package_version, (
            f"Expected the version in the greeting to be '{package_version}', but got '{version}'."
        )
        datetime_str = match.group(5)  # Extracted date-time
        extracted_datetime = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        assert isinstance(extracted_datetime, datetime), (
            f"Expected the date-time to be a valid datetime object in the format %Y-%m-%dT%H:%M:%S.%f%z but obtained {datetime_str}"
        )

        # Try by explicitly passing name=None
        results = asyncio.run(
            self.call_tool(
                tool_name,
                mcp_client,
                name=None,
            )
        )
        assert hasattr(results, "content"), "Expected the results to have a 'content' attribute."
        assert len(results.content) == 1, f"Expected one result for the {tool_name} tool."
        assert getattr(results, "structured_content", None) is not None, (
            "Expected the results to have a 'structured_content' attribute."
        )
        assert "result" in results.structured_content, "Expected the 'structured_content' to have a 'result' key."
        result = results.structured_content["result"]
        match = re.match(pattern, result)
        assert match, (
            f"Expected the response to be a greeting in a specific format. The obtained response does not match the expected format: {result}"
        )
        name = match.group(2)  # Extracted name
        assert name == "World", f"Expected the name in the greeting to be 'World', but got '{name}'."
        version = match.group(3)  # Extracted version
        assert version == package_version, (
            f"Expected the version in the greeting to be '{package_version}', but got '{version}'."
        )
        datetime_str = match.group(5)  # Extracted date-time
        extracted_datetime = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        assert isinstance(extracted_datetime, datetime), (
            f"Expected the date-time to be a valid datetime object in the format %Y-%m-%dT%H:%M:%S.%f%z but obtained {datetime_str}"
        )
