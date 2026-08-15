import asyncio
import os

import httpx
import pytest

from prioris_mcp.discovery.openalex import OpenAlexClient
from prioris_mcp.errors import ConfigurationError, InvalidRequestError

TEST_API_KEY = "test-api-key-123"


def _work(**overrides) -> dict:
    base = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
        "title": "Attention Is All You Need",
        "type": "article",
        "publication_year": 2017,
        "relevance_score": 0.91,
        "authorships": [{"author": {"display_name": "Ashish Vaswani"}}],
        "abstract_inverted_index": {"We": [0], "propose": [1], "a": [2], "new": [3]},
        "ids": {},
        "locations": [],
    }
    base.update(overrides)
    return base


def _run(coro):
    return asyncio.run(coro)


class TestSearchSemantic:
    """OpenAlex semantic-search request and mapping behaviour."""

    def test_builds_request_with_search_semantic_and_per_page(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": [_work()]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = _run(
            OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic(
                "transformer attention mechanisms", max_results=10
            )
        )
        assert len(result.hits) == 1
        assert seen_params[0]["search.semantic"] == "transformer attention mechanisms"
        assert seen_params[0]["per-page"] == "10"
        assert "filter" not in seen_params[0]

    def test_includes_api_key_when_configured(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5))
        assert seen_params[0]["api_key"] == TEST_API_KEY

    def test_raises_configuration_error_when_api_key_missing(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(ConfigurationError, match="PRIORIS_MCP_OPENALEX_API_KEY"):
            _run(OpenAlexClient(client, api_key=None).search_semantic("query", max_results=5))

    def test_rejects_query_over_2000_characters(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(InvalidRequestError, match="2000"):
            _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("x" * 2001, max_results=5))

    @pytest.mark.parametrize("max_results", [0, 51])
    def test_rejects_max_results_outside_openalex_limit(self, max_results: int):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(InvalidRequestError, match="max_results"):
            _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=max_results))

    def test_maps_score_and_authors(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]}))
        )
        hit = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5)).hits[0]
        assert hit.score == 0.91
        assert hit.authors[0].name == "Ashish Vaswani"
        assert hit.openalex_id == "W2741809807"
        assert hit.work_type == "article"

    def test_missing_type_yields_none_work_type(self):
        work = _work()
        del work["type"]
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [work]}))
        )
        hit = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5)).hits[0]
        assert hit.work_type is None

    def test_reconstructs_abstract_from_inverted_index(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]}))
        )
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5))
        assert result.hits[0].abstract == "We propose a new"

    def test_missing_abstract_inverted_index_yields_none(self):
        work = _work()
        del work["abstract_inverted_index"]
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [work]}))
        )
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5))
        assert result.hits[0].abstract is None

    def test_attaches_resolved_fetch_route(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]}))
        )
        hit = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5)).hits[0]
        assert hit.fetch_route.kind == "known_provider"
        assert hit.fetch_route.provider == "arxiv"


class TestSearchSemanticPagingAndFilters:
    """Paging (page=) and the two filters confirmed to actually work with search.semantic."""

    def test_filter_colon_is_sent_literally_not_percent_encoded(self):
        """OpenAlex's servers were observed to 504 when filter's `:` is sent as %3A - see openalex.py."""
        seen_urls = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(
            OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5, open_access_only=True)
        )
        assert "filter=is_oa:true" in seen_urls[0]
        assert "%3A" not in seen_urls[0]

    def test_defaults_page_to_1(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5))
        assert seen_params[0]["page"] == "1"

    def test_passes_through_requested_page(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5, page=3))
        assert seen_params[0]["page"] == "3"

    def test_rejects_page_below_1(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(InvalidRequestError, match="page"):
            _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5, page=0))

    def test_builds_publication_year_and_is_oa_filter(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(
            OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic(
                "query", max_results=5, from_year=2020, to_year=2023, open_access_only=True
            )
        )
        assert seen_params[0]["filter"] == "publication_year:>=2020,publication_year:<=2023,is_oa:true"

    def test_omits_filter_param_when_no_filters_requested(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5))
        assert "filter" not in seen_params[0]

    def test_rejects_from_year_after_to_year(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(InvalidRequestError, match="from_year"):
            _run(
                OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic(
                    "query", max_results=5, from_year=2023, to_year=2020
                )
            )

    def test_maps_pagination_metadata_from_response_meta(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"results": [_work()], "meta": {"count": 50}})
            )
        )
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5, page=2))
        assert result.page == 2
        assert result.per_page == 5
        assert result.total == 50
        assert result.has_more is True

    def test_has_more_false_on_last_page(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"results": [], "meta": {"count": 10}})
            )
        )
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=10, page=1))
        assert result.has_more is False

    def test_missing_meta_defaults_total_to_zero(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).search_semantic("query", max_results=5))
        assert result.total == 0
        assert result.has_more is False


class TestListWorkTypes:
    """OpenAlex /work-types request and mapping behaviour."""

    def test_maps_id_display_name_and_description(self):
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/types/article",
                    "display_name": "article",
                    "description": "Original, citable research usually in a journal.",
                }
            ]
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).list_work_types())
        assert len(result.types) == 1
        assert result.types[0].code == "article"
        assert result.types[0].name == "article"
        assert result.types[0].description == "Original, citable research usually in a journal."

    def test_sorts_by_code(self):
        payload = {
            "results": [
                {"id": "https://openalex.org/types/preprint", "display_name": "preprint", "description": "d1"},
                {"id": "https://openalex.org/types/article", "display_name": "article", "description": "d2"},
            ]
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
        result = _run(OpenAlexClient(client, api_key=TEST_API_KEY).list_work_types())
        assert [work_type.code for work_type in result.types] == ["article", "preprint"]

    def test_requests_per_page_100(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, api_key=TEST_API_KEY).list_work_types())
        assert seen_params[0]["per-page"] == "100"

    def test_includes_api_key_when_configured(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, api_key=TEST_API_KEY).list_work_types())
        assert seen_params[0]["api_key"] == TEST_API_KEY

    def test_raises_configuration_error_when_api_key_missing(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(ConfigurationError, match="PRIORIS_MCP_OPENALEX_API_KEY"):
            _run(OpenAlexClient(client, api_key=None).list_work_types())


@pytest.mark.live_openalex
@pytest.mark.skipif(
    not (os.environ.get("PRIORIS_MCP_RUN_LIVE_TESTS") and os.environ.get("PRIORIS_MCP_OPENALEX_API_KEY")),
    reason="Hits the real OpenAlex API - opt in with PRIORIS_MCP_RUN_LIVE_TESTS=1 and a configured "
    "PRIORIS_MCP_OPENALEX_API_KEY (OpenAlex has required a key on every request since 2026-02-13)",
)
class TestSearchSemanticAgainstRealOpenAlex:
    """One real call per run, not per commit: verifies OpenAlex still accepts our request shape.

    Every other test in this file mocks the transport, so a URL/param shape the real API
    rejects can never fail them - only a live call can. Kept to a single request (well under
    OpenAlex's 1 req/sec limit) and off by default so normal test runs and CI never touch the
    network or risk tripping rate limiting.
    """

    def test_search_semantic_returns_a_hit_for_a_well_known_query(self):
        async def _call() -> int:
            async with httpx.AsyncClient() as client:
                result = await OpenAlexClient(
                    client, api_key=os.environ["PRIORIS_MCP_OPENALEX_API_KEY"]
                ).search_semantic("transformer attention mechanisms", max_results=1)
                return len(result.hits)

        assert _run(_call()) >= 1

    def test_search_semantic_with_filter_does_not_504(self):
        """The exact shape that previously hung against the real API: filter= alongside search.semantic."""

        async def _call() -> int:
            async with httpx.AsyncClient() as client:
                result = await OpenAlexClient(
                    client, api_key=os.environ["PRIORIS_MCP_OPENALEX_API_KEY"]
                ).search_semantic("transformer attention mechanisms", max_results=1, open_access_only=True)
                return len(result.hits)

        assert _run(_call()) >= 1

    def test_list_work_types_returns_the_full_openalex_vocabulary(self):
        async def _call() -> list[str]:
            async with httpx.AsyncClient() as client:
                result = await OpenAlexClient(
                    client, api_key=os.environ["PRIORIS_MCP_OPENALEX_API_KEY"]
                ).list_work_types()
                return [work_type.code for work_type in result.types]

        codes = _run(_call())
        assert "article" in codes
        assert "preprint" in codes
