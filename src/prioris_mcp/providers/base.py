"""Shared interface every research-publication provider implements.

See docs/requirement-specification/01-architecture.md#researchpublicationprovider. Adding a
source means implementing this interface, not changing it.
"""

from abc import ABC, abstractmethod
from typing import Any


class CapabilityNotSupportedError(Exception):
    """Raised by a capability's default implementation for a provider with no equivalent.

    See docs/requirement-specification/index.md#out-of-scope-for-v1 - Europe PMC (no `list_top_n`
    equivalent) and the local filesystem source (no `search`/`fetch_metadata`/`resolve_identifier`
    at all - see
    docs/requirement-specification/01-architecture.md#local-filesystem-source) are the v1
    examples: a provider that can't sensibly support a capability says so via this default,
    rather than faking it.
    """


class ResearchPublicationProvider(ABC):
    """One grouping-level capability set: search, fetch metadata/full text, parse, resolve.

    Only `fetch_full_text` and `parse_full_text` are required of every subclass. The other three
    capabilities default to raising `CapabilityNotSupportedError` (mirroring `list_top_n`) rather
    than being abstract, so a source implementing a genuine subset - the local filesystem source,
    which has no query interface, no structured metadata authority, and no identifier scheme to
    route (see docs/requirement-specification/01-architecture.md#local-filesystem-source) - can
    subclass this ABC without faking capabilities it doesn't have.

    `resolve_identifier` is a provider-native capability, not itself an MCP tool - see
    docs/requirement-specification/01-architecture.md#resolve_identifier. It is declared here
    (rather than left ad hoc per subclass) so grouping-level DOI routing can call it uniformly
    across every provider that supports it.
    """

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Search items by keyword/query. See Functional requirements for the per-provider shape.

        Raises:
            CapabilityNotSupportedError: for a provider with no query interface (e.g. the local
                filesystem source).
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support search")

    async def list_top_n(
        self, include_categories: list[str], n: int, exclude_categories: list[str] | None = None
    ) -> dict[str, Any]:
        """List the top-N items across one or more provider-defined categories.

        Raises:
            CapabilityNotSupportedError: for a provider with no equivalent capability.
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support list_top_n")

    async def fetch_metadata(self, identifiers: list[str]) -> dict[str, Any]:
        """Fetch metadata for one or more identifiers in a single call.

        Raises:
            CapabilityNotSupportedError: for a provider with no structured metadata authority
                (e.g. the local filesystem source).
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support fetch_metadata")

    async def resolve_identifier(self, identifier: str, format: str) -> dict[str, Any]:
        """Resolve `identifier` to a fetchable URL/canonical form in the target `format`.

        Raises:
            CapabilityNotSupportedError: for a provider with no identifier scheme to resolve
                (e.g. the local filesystem source).
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support resolve_identifier")

    @abstractmethod
    async def fetch_full_text(self, identifier: str, format: str) -> dict[str, Any]:
        """Fetch (or return already-persisted) full text for `identifier`/`format`."""

    @abstractmethod
    async def parse_full_text(
        self, identifier: str, format: str, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        """Convert already-persisted full text for `identifier`/`format` into one page of Markdown."""
