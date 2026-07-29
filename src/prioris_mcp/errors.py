"""Shared error types and the tool-level `{"error": ..., "message": ...}` envelope.

See docs/requirement-specification/06-interface-specification.md#conventions for the envelope
shape and the codes below - every `research_*` tool method returns this envelope on failure
instead of letting an exception cross the MCP protocol boundary as a raw error.
"""

from collections.abc import Awaitable
from typing import Any

from prioris_mcp.rate_limit import ProviderUnavailableError, RateLimitedError


class NotFoundError(Exception):
    """Identifier not recognised by the provider, or requested format not yet persisted.

    Maps to the `not_found` error code.
    """


class FormatUnavailableError(Exception):
    """Identifier is valid but does not offer the requested format.

    Maps to the `format_unavailable` error code.
    """


class UnsupportedProviderError(Exception):
    """A DOI resolved to a domain outside the v1 provider allowlist.

    Maps to the `unsupported_provider` error code.
    """


class InvalidRequestError(Exception):
    """Caller-supplied arguments fail a provider's own validation before any outbound call.

    Maps to the `invalid_request` error code.
    """


class FileTooLargeError(Exception):
    """A local file exceeds PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES.

    Maps to the `file_too_large` error code.
    """


_ERROR_CODES: dict[type[Exception], str] = {
    NotFoundError: "not_found",
    FormatUnavailableError: "format_unavailable",
    UnsupportedProviderError: "unsupported_provider",
    InvalidRequestError: "invalid_request",
    FileTooLargeError: "file_too_large",
    RateLimitedError: "rate_limited",
    ProviderUnavailableError: "provider_unavailable",
}


def to_error_envelope(exc: Exception) -> dict[str, str]:
    """Convert one of the mapped exception types into the tool-level error envelope.

    Raises:
        TypeError: `exc` isn't one of the mapped types - an unmapped exception is a bug in the
            calling tool method (it should not have been caught), not a case to paper over with
            a generic error code.
    """
    for exc_type, code in _ERROR_CODES.items():
        if isinstance(exc, exc_type):
            return {"error": code, "message": str(exc)}
    raise TypeError(f"No error code mapped for exception type {type(exc).__name__}")


async def call_returning_envelope(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Await `coro`, converting any mapped exception into the error envelope instead of raising.

    Every `research_*` tool method on `PriorisMCP` wraps its provider call with this, so the
    try/except-to-envelope translation is written once, not once per tool.
    """
    try:
        return await coro
    except tuple(_ERROR_CODES.keys()) as exc:
        return to_error_envelope(exc)
