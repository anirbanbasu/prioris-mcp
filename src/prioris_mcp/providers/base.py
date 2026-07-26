"""Shared interface every research-publication provider implements.

See docs/requirement-specification/01-architecture.md#researchpublicationprovider. Adding a
source means implementing this interface, not changing it.
"""

from abc import ABC, abstractmethod
from typing import Any


class CapabilityNotSupportedError(Exception):
    """Raised by the default `list_top_n` for a provider with no equivalent capability.

    See docs/requirement-specification/index.md#out-of-scope-for-v1 - Europe PMC is the v1
    example: it has no single classification field equivalent to arXiv's subject categories.
    """


class ResearchPublicationProvider(ABC):
    """One grouping-level capability set: search, fetch metadata/full text, parse, resolve.

    `resolve_identifier` is a provider-native capability, not itself an MCP tool - see
    docs/requirement-specification/01-architecture.md#resolve_identifier. It is declared here
    (rather than left ad hoc per subclass) so grouping-level DOI routing (added by a later plan)
    can call it uniformly across every provider.
    """

    @abstractmethod
    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Search items by keyword/query. See Functional requirements for the per-provider shape."""

    async def list_top_n(self, category: str, n: int) -> dict[str, Any]:
        """List the top-N items for a provider-defined category.

        Raises:
            CapabilityNotSupportedError: for a provider with no equivalent capability.
        """
        raise CapabilityNotSupportedError(f"{type(self).__name__} does not support list_top_n")

    @abstractmethod
    async def fetch_metadata(self, identifiers: list[str]) -> dict[str, Any]:
        """Fetch metadata for one or more identifiers in a single call."""

    @abstractmethod
    async def resolve_identifier(self, identifier: str, format: str) -> dict[str, Any]:
        """Resolve `identifier` to a fetchable URL/canonical form in the target `format`."""

    @abstractmethod
    async def fetch_full_text(self, identifier: str, format: str) -> dict[str, Any]:
        """Fetch (or return already-persisted) full text for `identifier`/`format`."""

    @abstractmethod
    async def parse_full_text(self, identifier: str, format: str) -> dict[str, Any]:
        """Convert already-persisted full text for `identifier`/`format` into Markdown."""
