import asyncio

import pytest

from prioris_mcp.providers.base import CapabilityNotSupportedError, ResearchPublicationProvider


class _StubProvider(ResearchPublicationProvider):
    """Minimal concrete subclass exercising the ABC's default behaviour."""

    async def search(self, query: str, **kwargs: object) -> dict:
        return {"results": [], "total_results": 0}

    async def fetch_metadata(self, identifiers: list[str]) -> dict:
        return {"results": [], "not_found": identifiers}

    async def resolve_identifier(self, identifier: str, format: str) -> dict:
        return {"identifier": identifier, "resolved_url": "https://example.test", "format": format}

    async def fetch_full_text(self, identifier: str, format: str) -> dict:
        return {"location": "x", "format": format, "size_bytes": 0, "served_from_storage": False}

    async def parse_full_text(self, identifier: str, format: str, offset: int = 0, limit: int | None = None) -> dict:
        return {"markdown": "", "resource_uri": "x"}


class TestResearchPublicationProvider:
    """Shared ABC every research-publication provider implements."""

    def test_cannot_instantiate_without_implementing_abstract_methods(self):
        with pytest.raises(TypeError):
            ResearchPublicationProvider()  # type: ignore[abstract]

    def test_default_list_top_n_raises_capability_not_supported(self):
        async def scenario():
            provider = _StubProvider()
            await provider.list_top_n("cs.CL", 5)

        with pytest.raises(CapabilityNotSupportedError):
            asyncio.run(scenario())

    def test_concrete_subclass_implements_required_capabilities(self):
        async def scenario():
            provider = _StubProvider()
            assert await provider.search("q") == {"results": [], "total_results": 0}
            assert await provider.fetch_metadata(["a"]) == {"results": [], "not_found": ["a"]}
            resolved = await provider.resolve_identifier("a", "pdf")
            assert resolved["identifier"] == "a"
            fetched = await provider.fetch_full_text("a", "pdf")
            assert fetched["format"] == "pdf"
            parsed = await provider.parse_full_text("a", "pdf")
            assert parsed == {"markdown": "", "resource_uri": "x"}

        asyncio.run(scenario())
