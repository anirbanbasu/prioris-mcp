import asyncio

import pytest
from pydantic import RootModel

from prioris_mcp.errors import InvalidRequestError, NotFoundError
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.providers.base import CapabilityNotSupportedError, ResearchPublicationProvider, persist_parsed_markdown
from prioris_mcp.storage.filesystem import FilesystemStorageBackend
from prioris_mcp.storage.search_index import SqliteFts5SearchIndex


class _StubProvider(ResearchPublicationProvider):
    """Minimal concrete subclass exercising the ABC's default behaviour."""

    async def search(self, query: str, **kwargs: object) -> RootModel[dict]:
        return RootModel({"results": [], "total_results": 0})

    async def fetch_metadata(self, identifiers: list[str]) -> RootModel[dict]:
        return RootModel({"results": [], "not_found": identifiers})

    async def resolve_identifier(self, identifier: str, format: str) -> RootModel[dict]:
        return RootModel({"identifier": identifier, "resolved_url": "https://example.test", "format": format})

    async def fetch_full_text(self, identifier: str, format: str) -> RootModel[dict]:
        return RootModel({"location": "x", "format": format, "size_bytes": 0, "served_from_storage": False})

    async def parse_full_text(
        self, identifier: str, format: str, offset: int = 0, limit: int | None = None
    ) -> RootModel[dict]:
        return RootModel({"markdown": "", "resource_uri": "x"})


class _PartialCapabilityProvider(ResearchPublicationProvider):
    """A source implementing only fetch_full_text/parse_full_text - the local filesystem shape."""

    async def fetch_full_text(self, identifier: str, format: str) -> RootModel[dict]:
        return RootModel({"location": "x", "format": format, "size_bytes": 0, "served_from_storage": False})

    async def parse_full_text(
        self, identifier: str, format: str, offset: int = 0, limit: int | None = None
    ) -> RootModel[dict]:
        return RootModel({"markdown": "", "resource_uri": "x"})


class TestResearchPublicationProvider:
    """Shared ABC every research-publication provider implements."""

    def test_cannot_instantiate_without_implementing_required_capabilities(self):
        with pytest.raises(TypeError):
            ResearchPublicationProvider()  # type: ignore[abstract]

    def test_default_list_top_n_raises_capability_not_supported(self):
        async def scenario():
            provider = _StubProvider()
            await provider.list_top_n(["cs.CL"], 5)

        with pytest.raises(CapabilityNotSupportedError):
            asyncio.run(scenario())

    def test_concrete_subclass_implements_required_capabilities(self):
        async def scenario():
            provider = _StubProvider()
            assert (await provider.search("q")).root == {"results": [], "total_results": 0}
            assert (await provider.fetch_metadata(["a"])).root == {"results": [], "not_found": ["a"]}
            resolved = await provider.resolve_identifier("a", "pdf")
            assert resolved.root["identifier"] == "a"
            fetched = await provider.fetch_full_text("a", "pdf")
            assert fetched.root["format"] == "pdf"
            parsed = await provider.parse_full_text("a", "pdf")
            assert parsed.root == {"markdown": "", "resource_uri": "x"}

        asyncio.run(scenario())

    def test_partial_capability_provider_can_be_instantiated(self):
        """A source like the local filesystem one only implements fetch_full_text/parse_full_text."""
        provider = _PartialCapabilityProvider()
        assert isinstance(provider, ResearchPublicationProvider)

    def test_partial_capability_provider_defaults_raise_capability_not_supported(self):
        async def scenario():
            provider = _PartialCapabilityProvider()
            for coro in (
                provider.search("q"),
                provider.fetch_metadata(["a"]),
                provider.resolve_identifier("a", "pdf"),
                provider.list_top_n(["cs.CL"], 5),
            ):
                with pytest.raises(CapabilityNotSupportedError):
                    await coro

        asyncio.run(scenario())


class _CountingParserBackend(ParserBackend):
    def __init__(self, pages: list[str]):
        self.call_count = 0
        self._pages = pages

    async def to_markdown(self, content: bytes) -> dict:
        self.call_count += 1
        markdown_parts, leaf_spans, offset = [], [], 0
        for i, page in enumerate(self._pages):
            if i > 0:
                markdown_parts.append("\n\n")
                offset += 2
            markdown_parts.append(page)
            leaf_spans.append({"start": offset, "length": len(page)})
            offset += len(page)
        return {"markdown": "".join(markdown_parts), "leaf_spans": leaf_spans}


def _env(tmp_path):
    storage = FilesystemStorageBackend(tmp_path / "storage")
    search_index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
    return storage, search_index


class TestPersistParsedMarkdownNotFound:
    """Test NotFoundError raised when source document missing or page out of range."""

    def test_raises_not_found_when_source_document_missing(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            backend = _CountingParserBackend(["one page"])
            with pytest.raises(NotFoundError):
                await persist_parsed_markdown(
                    storage=storage,
                    search_index=search_index,
                    provider="arxiv",
                    canonical_identifier="2106.09685v2",
                    external_identifier="2106.09685v2",
                    source_format="pdf",
                    backend=backend,
                    offset=0,
                    limit=100,
                    page=None,
                    page_aware=True,
                )

        asyncio.run(scenario())


class TestPersistParsedMarkdownFreshParse:
    """Test parsing, persistence, caching, manifest population, and search index."""

    def test_parses_persists_and_paginates_on_first_call(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"%PDF-1.4 raw")
            backend = _CountingParserBackend(["# Intro\n\nHello world."])
            result = await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=True,
            )
            assert result["markdown"] == "# Intro\n\nHello world."
            assert backend.call_count == 1
            assert result["total_pages"] == 1
            assert result["page_range"] == (1, 1)

        asyncio.run(scenario())

    def test_second_call_is_served_from_cache_not_reparsed(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"%PDF-1.4 raw")
            backend = _CountingParserBackend(["# Intro\n\nHello world."])
            await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=True,
            )
            await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=True,
            )
            assert backend.call_count == 1

        asyncio.run(scenario())

    def test_populates_manifest_leaf_rows(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"raw")
            backend = _CountingParserBackend(["Page one.", "Page two."])
            await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=True,
            )
            manifest = storage.manifest_for("arxiv", "2106.09685v2")
            assert await manifest.total_pages("pdf") == 2

        asyncio.run(scenario())

    def test_populates_search_index(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"raw")
            backend = _CountingParserBackend(["# Introduction\n\nAbout quantum entanglement."])
            await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=True,
            )
            results = await search_index.search("quantum")
            assert len(results) == 1
            assert results[0]["identifier"] == "2106.09685v2"

        asyncio.run(scenario())

    def test_uses_external_identifier_not_canonical_for_search_index(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("localfile", "abc123hash", "pdf", b"raw")
            backend = _CountingParserBackend(["octopus content"])
            await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="localfile",
                canonical_identifier="abc123hash",
                external_identifier="20260729-1430-a3f2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=True,
            )
            results = await search_index.search("octopus")
            assert results[0]["identifier"] == "20260729-1430-a3f2"

        asyncio.run(scenario())


class TestPersistParsedMarkdownMigratedDocument:
    """Test rebuild of manifest/search rows for a migrated document with an empty manifest.

    Simulates what storage/migration.py actually does: copy the document and markdown artefacts
    onto disk directly, bypassing persist_parsed_markdown entirely, so no manifest/search rows
    are ever created. The first parse_full_text call after migration must detect the empty
    manifest (served_from_storage=True but total_pages == 0) and rebuild it by re-parsing the
    source document, without disturbing the already-persisted markdown.
    """

    def test_rebuilds_manifest_and_search_index_for_migrated_document(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"%PDF-1.4 raw", artefact="document")
            await storage.write("arxiv", "2106.09685v2", "pdf", b"Page one.\n\nPage two.", artefact="markdown")
            backend = _CountingParserBackend(["Page one.", "Page two."])
            result = await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=2,
                page_aware=True,
            )
            assert result["markdown"] == "Page two."
            assert backend.call_count == 1

            manifest = storage.manifest_for("arxiv", "2106.09685v2")
            assert await manifest.total_pages("pdf") == 2

            results = await search_index.search("Page")
            assert any(row["identifier"] == "2106.09685v2" for row in results)

        asyncio.run(scenario())

    def test_raises_not_found_when_source_document_missing_for_rebuild(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"Page one.\n\nPage two.", artefact="markdown")
            backend = _CountingParserBackend(["Page one.", "Page two."])
            with pytest.raises(NotFoundError):
                await persist_parsed_markdown(
                    storage=storage,
                    search_index=search_index,
                    provider="arxiv",
                    canonical_identifier="2106.09685v2",
                    external_identifier="2106.09685v2",
                    source_format="pdf",
                    backend=backend,
                    offset=0,
                    limit=1000,
                    page=None,
                    page_aware=True,
                )

        asyncio.run(scenario())


class TestPersistParsedMarkdownPageParam:
    """Test page parameter resolves offset to page start and validates bounds."""

    def test_page_resolves_offset_relative_to_that_pages_start(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"raw")
            backend = _CountingParserBackend(["Page one content.", "Page two content."])
            result = await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="arxiv",
                canonical_identifier="2106.09685v2",
                external_identifier="2106.09685v2",
                source_format="pdf",
                backend=backend,
                offset=0,
                limit=1000,
                page=2,
                page_aware=True,
            )
            assert result["markdown"] == "Page two content."

        asyncio.run(scenario())

    def test_page_out_of_range_raises_not_found(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("arxiv", "2106.09685v2", "pdf", b"raw")
            backend = _CountingParserBackend(["Only page."])
            with pytest.raises(NotFoundError):
                await persist_parsed_markdown(
                    storage=storage,
                    search_index=search_index,
                    provider="arxiv",
                    canonical_identifier="2106.09685v2",
                    external_identifier="2106.09685v2",
                    source_format="pdf",
                    backend=backend,
                    offset=0,
                    limit=1000,
                    page=5,
                    page_aware=True,
                )

        asyncio.run(scenario())


class TestPersistParsedMarkdownPageAwareGuard:
    """Test page_aware guard and page/page_range behavior per format."""

    def test_page_given_when_not_page_aware_raises_invalid_request(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("europepmc", "MED:1", "xml", b"raw")
            backend = _CountingParserBackend(["whole document"])
            with pytest.raises(InvalidRequestError):
                await persist_parsed_markdown(
                    storage=storage,
                    search_index=search_index,
                    provider="europepmc",
                    canonical_identifier="MED:1",
                    external_identifier="MED:1",
                    source_format="xml",
                    backend=backend,
                    offset=0,
                    limit=1000,
                    page=1,
                    page_aware=False,
                )

        asyncio.run(scenario())

    def test_total_pages_and_page_range_are_none_when_not_page_aware(self, tmp_path):
        async def scenario():
            storage, search_index = _env(tmp_path)
            await storage.write("europepmc", "MED:1", "xml", b"raw")
            backend = _CountingParserBackend(["whole document"])
            result = await persist_parsed_markdown(
                storage=storage,
                search_index=search_index,
                provider="europepmc",
                canonical_identifier="MED:1",
                external_identifier="MED:1",
                source_format="xml",
                backend=backend,
                offset=0,
                limit=1000,
                page=None,
                page_aware=False,
            )
            assert result["total_pages"] is None
            assert result["page_range"] is None

        asyncio.run(scenario())
