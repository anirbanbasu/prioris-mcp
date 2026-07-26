import asyncio
from collections.abc import Callable

import httpx
import pytest

from prioris_mcp.errors import FormatUnavailableError, NotFoundError
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.providers.europepmc import EuropePmcProvider
from prioris_mcp.rate_limit import ProviderRequestQueue
from prioris_mcp.storage import FilesystemStorageBackend


def _record(
    source: str = "MED",
    raw_id: str = "26551875",
    pmid: str | None = "26551875",
    pmcid: str | None = "PMC4767193",
    in_epmc: str = "Y",
) -> dict:
    return {
        "id": raw_id,
        "source": source,
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": "10.1000/example",
        "title": "A Paper",
        "authorList": {"author": [{"fullName": "Jane Doe", "firstName": "Jane", "lastName": "Doe", "initials": "J"}]},
        "abstractText": "An abstract.",
        "journalInfo": {"journal": {"title": "Journal of Examples"}},
        "pubYear": "2016",
        "isOpenAccess": "Y",
        "license": "cc by",
        "inEPMC": in_epmc,
    }


def _search_payload(results: list[dict], hit_count: int | None = None, next_cursor_mark: str | None = None) -> bytes:
    import json

    payload = {"hitCount": hit_count if hit_count is not None else len(results), "resultList": {"result": results}}
    if next_cursor_mark is not None:
        payload["nextCursorMark"] = next_cursor_mark
    return json.dumps(payload).encode("utf-8")


def _provider_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[EuropePmcProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
    provider = EuropePmcProvider(storage=None, queue=queue, http_client=client, xml_backend=None)
    return provider, client


def _provider_with_storage(
    handler: Callable[[httpx.Request], httpx.Response], tmp_path
) -> tuple[EuropePmcProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
    storage = FilesystemStorageBackend(base_dir=tmp_path)
    provider = EuropePmcProvider(storage=storage, queue=queue, http_client=client, xml_backend=None)
    return provider, client


class TestEuropePmcProviderSearch:
    """Tests for EuropePmcProvider.search()."""

    def test_returns_parsed_results_and_hit_count(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.params["query"] == "field:value"
            assert req.url.params["resultType"] == "core"
            return httpx.Response(200, content=_search_payload([_record()], hit_count=1))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.search("field:value")

        result = asyncio.run(scenario())
        assert result["hit_count"] == 1
        assert "next_cursor_mark" not in result
        record = result["results"][0]
        assert record["identifier"] == "MED:26551875"
        assert record["pmid"] == "26551875"
        assert record["pmcid"] == "PMC4767193"
        assert record["authors"] == [
            {"full_name": "Jane Doe", "first_name": "Jane", "last_name": "Doe", "initials": "J"}
        ]
        assert record["is_open_access"] == "Y"
        assert record["full_text_available"] is True

    def test_next_cursor_mark_present_only_when_more_results_exist(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([_record()], next_cursor_mark="AoIIP123"))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.search("field:value")

        result = asyncio.run(scenario())
        assert result["next_cursor_mark"] == "AoIIP123"

    def test_full_text_available_false_when_not_in_epmc(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([_record(in_epmc="N")]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.search("field:value")

        result = asyncio.run(scenario())
        assert result["results"][0]["full_text_available"] is False


class TestEuropePmcProviderFetchMetadata:
    """Tests for EuropePmcProvider.fetch_metadata()."""

    def test_bare_pmcid_and_med_identifier_batch(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert "EXT_ID:26551875 AND SRC:MED" in req.url.params["query"]
            assert "EXT_ID:4767193 AND SRC:PMC" in req.url.params["query"]
            return httpx.Response(200, content=_search_payload([_record()]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.fetch_metadata(["MED:26551875", "PMC4767193"])

        result = asyncio.run(scenario())
        assert result["results"][0]["identifier"] == "MED:26551875"
        assert result["not_found"] == ["PMC4767193"]

    def test_all_invalid_batch_returns_empty_results_and_full_not_found(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.fetch_metadata(["MED:00000000"])

        result = asyncio.run(scenario())
        assert result == {"results": [], "not_found": ["MED:00000000"]}


class TestEuropePmcProviderResolveIdentifier:
    """Tests for EuropePmcProvider.resolve_identifier()."""

    def test_resolves_pmcid_directly_with_full_text_available(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([_record()]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.resolve_identifier("PMC4767193", "xml")

        result = asyncio.run(scenario())
        assert result["identifier"] == "PMC:PMC4767193"
        assert result["resolved_url"] == f"{'https://www.ebi.ac.uk/europepmc/webservices/rest'}/PMC4767193/fullTextXML"
        assert result["full_text_available"] is True

    def test_resolves_med_identifier_to_its_pmcid(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([_record()]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.resolve_identifier("MED:26551875", "xml")

        result = asyncio.run(scenario())
        assert result["identifier"] == "PMC:PMC4767193"

    def test_unrecognised_identifier_raises_not_found(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                await provider.resolve_identifier("MED:00000000", "xml")

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_record_without_pmcid_raises_format_unavailable(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_search_payload([_record(pmcid=None)]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                await provider.resolve_identifier("MED:26551875", "xml")

        with pytest.raises(FormatUnavailableError):
            asyncio.run(scenario())


class TestEuropePmcProviderFetchFullText:
    """Tests for EuropePmcProvider.fetch_full_text()."""

    def test_fetches_and_persists_xml(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            if "fullTextXML" in str(req.url):
                return httpx.Response(200, content=b"<article>fake jats</article>")
            return httpx.Response(200, content=_search_payload([_record()]))

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                return await provider.fetch_full_text("PMC4767193")

        result = asyncio.run(scenario())
        assert result["format"] == "xml"
        assert result["served_from_storage"] is False
        assert result["resource_uri"] == "research://europepmc/PMC:PMC4767193/xml/fulltext"

    def test_second_call_is_served_from_storage(self, tmp_path):
        xml_call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal xml_call_count
            if "fullTextXML" in str(req.url):
                xml_call_count += 1
                return httpx.Response(200, content=b"<article>fake jats</article>")
            return httpx.Response(200, content=_search_payload([_record()]))

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                first = await provider.fetch_full_text("PMC4767193")
                second = await provider.fetch_full_text("PMC4767193")
                return first, second, xml_call_count

        first, second, count = asyncio.run(scenario())
        assert first["served_from_storage"] is False
        assert second["served_from_storage"] is True
        assert count == 1

    def test_not_in_epmc_raises_format_unavailable_without_fulltext_request(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            if "fullTextXML" in str(req.url):
                raise AssertionError("must not attempt fullTextXML when inEPMC is N")
            return httpx.Response(200, content=_search_payload([_record(in_epmc="N")]))

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                await provider.fetch_full_text("PMC4767193")

        with pytest.raises(FormatUnavailableError):
            asyncio.run(scenario())


class _StubXmlBackend(ParserBackend):
    def __init__(self, markdown: str = "# parsed") -> None:
        self.markdown = markdown
        self.call_count = 0

    async def to_markdown(self, content: bytes) -> str:
        self.call_count += 1
        return self.markdown


class TestEuropePmcProviderParseFullText:
    """Tests for EuropePmcProvider.parse_full_text()."""

    def test_not_found_when_source_never_fetched(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            # resolve_identifier calls fetch_metadata first
            if "fullTextXML" not in str(req.url):
                return httpx.Response(200, content=_search_payload([_record()]))
            raise AssertionError("must not attempt fullTextXML fetch when parse_full_text source wasn't fetched")

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
            storage = FilesystemStorageBackend(base_dir=tmp_path)
            provider = EuropePmcProvider(
                storage=storage, queue=queue, http_client=client, xml_backend=_StubXmlBackend()
            )
            async with client:
                await provider.parse_full_text("PMC:PMC4767193")

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_parses_and_persists_markdown_once_source_is_fetched(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            if "fullTextXML" in str(req.url):
                return httpx.Response(200, content=b"<article>fake jats</article>")
            return httpx.Response(200, content=_search_payload([_record()]))

        xml_backend = _StubXmlBackend(markdown="# Parsed JATS")

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
            storage = FilesystemStorageBackend(base_dir=tmp_path)
            provider = EuropePmcProvider(storage=storage, queue=queue, http_client=client, xml_backend=xml_backend)
            async with client:
                fetched = await provider.fetch_full_text("PMC4767193")
                canonical_id = fetched["resource_uri"].split("/")[3]
                first = await provider.parse_full_text(canonical_id)
                second = await provider.parse_full_text(canonical_id)
                return first, second

        first, second = asyncio.run(scenario())
        assert first["markdown"] == "# Parsed JATS"
        assert second == first
        assert xml_backend.call_count == 1

    def test_parses_with_non_canonical_identifier_after_fetch(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            if "fullTextXML" in str(req.url):
                return httpx.Response(200, content=b"<article>fake jats</article>")
            return httpx.Response(200, content=_search_payload([_record()]))

        xml_backend = _StubXmlBackend(markdown="# Parsed JATS")

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
            storage = FilesystemStorageBackend(base_dir=tmp_path)
            provider = EuropePmcProvider(storage=storage, queue=queue, http_client=client, xml_backend=xml_backend)
            async with client:
                # Fetch using bare PMCID
                await provider.fetch_full_text("PMC4767193")
                # Parse using MED identifier (non-canonical form)
                result = await provider.parse_full_text("MED:26551875")
                return result

        result = asyncio.run(scenario())
        assert result["markdown"] == "# Parsed JATS"
        assert "PMC:PMC4767193" in result["resource_uri"]
        assert xml_backend.call_count == 1
