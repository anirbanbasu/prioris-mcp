import asyncio

import httpx
import pytest

from prioris_mcp.discovery.openalex import OpenAlexClient
from prioris_mcp.errors import InvalidRequestError


def _work(**overrides) -> dict:
    base = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
        "title": "Attention Is All You Need",
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

    def test_builds_request_with_filter_and_per_page(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": [_work()]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = _run(
            OpenAlexClient(client, mailto=None).search_semantic("transformer attention mechanisms", max_results=10)
        )
        assert len(result.hits) == 1
        assert seen_params[0]["filter"] == "search.semantic:transformer attention mechanisms"
        assert seen_params[0]["per-page"] == "10"
        assert "mailto" not in seen_params[0]

    def test_includes_mailto_when_configured(self):
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_params.append(request.url.params)
            return httpx.Response(200, json={"results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _run(OpenAlexClient(client, mailto="researcher@example.com").search_semantic("query", max_results=5))
        assert seen_params[0]["mailto"] == "researcher@example.com"

    def test_rejects_query_over_2000_characters(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(InvalidRequestError, match="2000"):
            _run(OpenAlexClient(client, mailto=None).search_semantic("x" * 2001, max_results=5))

    @pytest.mark.parametrize("max_results", [0, 201])
    def test_rejects_max_results_outside_openalex_limit(self, max_results: int):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(InvalidRequestError, match="max_results"):
            _run(OpenAlexClient(client, mailto=None).search_semantic("query", max_results=max_results))

    def test_maps_score_and_authors(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]}))
        )
        hit = _run(OpenAlexClient(client, mailto=None).search_semantic("query", max_results=5)).hits[0]
        assert hit.score == 0.91
        assert hit.authors[0].name == "Ashish Vaswani"
        assert hit.openalex_id == "W2741809807"

    def test_reconstructs_abstract_from_inverted_index(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]}))
        )
        result = _run(OpenAlexClient(client, mailto=None).search_semantic("query", max_results=5))
        assert result.hits[0].abstract == "We propose a new"

    def test_missing_abstract_inverted_index_yields_none(self):
        work = _work()
        del work["abstract_inverted_index"]
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [work]}))
        )
        result = _run(OpenAlexClient(client, mailto=None).search_semantic("query", max_results=5))
        assert result.hits[0].abstract is None

    def test_attaches_resolved_fetch_route(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]}))
        )
        hit = _run(OpenAlexClient(client, mailto=None).search_semantic("query", max_results=5)).hits[0]
        assert hit.fetch_route.kind == "known_provider"
        assert hit.fetch_route.provider == "arxiv"
