"""Shared error types raised by providers and translated into MCP tool errors.

See docs/requirement-specification/06-interface-specification.md#conventions for the error
codes these map to.
"""


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
