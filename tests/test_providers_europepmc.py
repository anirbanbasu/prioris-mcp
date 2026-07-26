import asyncio
from collections.abc import Callable

import httpx

from prioris_mcp.providers.europepmc import EuropePmcProvider
from prioris_mcp.rate_limit import ProviderRequestQueue


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
