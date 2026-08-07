import base64
import logging
import time
from importlib.metadata import PackageMetadata, metadata as importlib_metadata
from typing import ClassVar

from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.middleware import Middleware

from prioris_mcp import PACKAGE_NAME

logger = logging.getLogger(__name__)


class StripUnknownArgumentsMiddleware(Middleware):
    """Middleware to strip unknown arguments from MCP feature invocations."""

    async def on_call_tool(self, context, call_next):
        """Filter out unknown arguments from tool calls."""
        try:
            # Only proceed if this is a tool call with non-zero arguments
            if context.fastmcp_context and context.message.arguments and len(context.message.arguments) > 0:
                tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
                tool_args = tool.parameters.get("properties", None)
                expected_args_names = set(tool_args.keys()) if tool_args else set()
                filtered_args = {k: v for k, v in context.message.arguments.items() if k in expected_args_names}
                unknown_args = set(context.message.arguments.keys()).difference(expected_args_names)
                if unknown_args:
                    logger.info(f"Unknown arguments for tool '{context.message.name}': {list(unknown_args)}")
                context.message.arguments = filtered_args  # modify in place
        except Exception:  # pragma: no cover
            logger.exception(
                f"Error in {StripUnknownArgumentsMiddleware.__name__}.on_call_tool",
                stack_info=True,
            )
        return await call_next(context)


class EncodeBinaryResourceContentMiddleware(Middleware):
    """Base64-encodes binary resource content before it can reach `ResponseCachingMiddleware`.

    `ResponseCachingMiddleware` JSON-serialises cached values via Pydantic, whose default `bytes`
    encoding is a UTF-8 decode - fine for text resources, but it crashes on any resource whose raw
    bytes aren't valid UTF-8 (e.g. a fetched PDF's `fulltext` resource). Must be registered
    *before* `ResponseCachingMiddleware` in `server.py`'s `app()` chain, so this only runs on a
    cache miss - a cache hit already holds the already-encoded form. Paired with
    `DecodeBinaryResourceContentMiddleware`, which reverses this on every read regardless of hit
    or miss.
    """

    META_MARKER: ClassVar[str] = "_prioris_mcp_base64_encoded"

    async def on_read_resource(self, context, call_next):
        """Base64-encode any `bytes` content items so caching never sees raw binary."""
        result = await call_next(context)
        contents = [
            ResourceContent(
                base64.b64encode(item.content).decode("ascii"),
                mime_type=item.mime_type,
                meta={**(item.meta or {}), self.META_MARKER: True},
            )
            if isinstance(item.content, bytes)
            else item
            for item in result.contents
        ]
        return ResourceResult(contents=contents, meta=result.meta)


class DecodeBinaryResourceContentMiddleware(Middleware):
    """Reverses `EncodeBinaryResourceContentMiddleware`'s base64 encoding on every read.

    Must be registered *after* `ResponseCachingMiddleware` in `server.py`'s `app()` chain, so it
    runs on both a fresh read and a cache hit - a cache hit never reaches
    `EncodeBinaryResourceContentMiddleware`, so only a middleware outside the cache boundary can
    restore the original bytes for the caller.
    """

    async def on_read_resource(self, context, call_next):
        """Base64-decode any content item `EncodeBinaryResourceContentMiddleware` marked."""
        result = await call_next(context)
        contents = []
        for item in result.contents:
            if item.meta and item.meta.get(EncodeBinaryResourceContentMiddleware.META_MARKER):
                meta = {k: v for k, v in item.meta.items() if k != EncodeBinaryResourceContentMiddleware.META_MARKER}
                contents.append(
                    ResourceContent(base64.b64decode(item.content), mime_type=item.mime_type, meta=meta or None)
                )
            else:
                contents.append(item)
        return ResourceResult(contents=contents, meta=result.meta)


class ResponseMetadataMiddleware(Middleware):
    """Middleware to add metadata to MCP responses."""

    _package_metadata: ClassVar[PackageMetadata] = importlib_metadata(PACKAGE_NAME)
    PACKAGE_METADATA_KEY: ClassVar[str] = "_package_metadata"
    TIMING_METADATA_KEY: ClassVar[str] = "_timing_metadata"

    async def _time_operation(self, context, call_next, operation_name: str):
        """Helper method to time any operation."""
        # Based on: https://github.com/jlowin/fastmcp/blob/ee6340526216c7796000c69ef3b9001a1a6f31a3/src/fastmcp/server/middleware/timing.py#L91C5-L109C18
        start_time = time.perf_counter()
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(f"{operation_name} completed in {duration_ms:.2f}ms")
            return result, duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.warning(
                f"{operation_name} failed after {duration_ms:.2f}ms: {e}",
            )
            raise

    async def on_call_tool(self, context, call_next):
        """Add metadata to tool responses."""
        # There is no support for top-level metadata for prompts or resources yet.
        # Do not encapsulate this in a try-except; let errors propagate
        feature_name = getattr(context.message, "name", "unknown")
        result, duration_ms = await self._time_operation(context, call_next, f"Tool '{feature_name}'")

        if result is None:  # pragma: no cover
            # Isn't this an impossible scenario?
            return result
        if getattr(result, "meta", None) is None:
            result.meta = {}  # pragma: no cover
        result.meta[ResponseMetadataMiddleware.PACKAGE_METADATA_KEY] = {
            "name": ResponseMetadataMiddleware._package_metadata["name"],
            "version": ResponseMetadataMiddleware._package_metadata["version"],
        }
        result.meta[ResponseMetadataMiddleware.TIMING_METADATA_KEY] = {
            "tool_response_time_ms": duration_ms,
        }
        logger.debug(
            f"Added package metadata to tool response: {result.meta[ResponseMetadataMiddleware.PACKAGE_METADATA_KEY]}"
        )
        return result
