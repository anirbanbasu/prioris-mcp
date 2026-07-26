import asyncio
from collections.abc import Callable

import httpx
import pytest

from prioris_mcp.errors import InvalidRequestError
from prioris_mcp.providers.arxiv import ArxivProvider, _bare_id, _is_version_pinned
from prioris_mcp.rate_limit import ProviderRequestQueue

_FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>{total_results}</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>10</opensearch:itemsPerPage>
  {entries}
</feed>
"""

_ENTRY_TEMPLATE = """
  <entry>
    <id>http://arxiv.org/abs/{arxiv_id}</id>
    <published>2021-06-17T17:59:33Z</published>
    <updated>2021-10-16T13:56:12Z</updated>
    <title>{title}</title>
    <summary>An abstract.</summary>
    <author><name>Jane Doe</name><arxiv:affiliation>Example University</arxiv:affiliation></author>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:primary_category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/{arxiv_id}" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/{arxiv_id}" rel="related" type="application/pdf"/>
    <arxiv:doi>10.48550/arXiv.{arxiv_id}</arxiv:doi>
    <arxiv:comment>10 pages</arxiv:comment>
  </entry>
"""


def _feed(entries: list[str], total_results: int | None = None) -> bytes:
    body = "".join(entries)
    return _FEED_TEMPLATE.format(entries=body, total_results=total_results or len(entries)).encode("utf-8")


def _entry(arxiv_id: str, title: str = "A Paper") -> str:
    return _ENTRY_TEMPLATE.format(arxiv_id=arxiv_id, title=title)


def _provider_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[ArxivProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
    provider = ArxivProvider(storage=None, queue=queue, http_client=client)
    return provider, client


class TestBareId:
    """Tests for `_bare_id`."""

    def test_strips_version_suffix(self):
        assert _bare_id("2106.09685v2") == "2106.09685"

    def test_leaves_unversioned_id_unchanged(self):
        assert _bare_id("2106.09685") == "2106.09685"

    def test_handles_old_style_id_with_slash(self):
        assert _bare_id("hep-th/9901001v1") == "hep-th/9901001"


class TestIsVersionPinned:
    """Tests for `_is_version_pinned`."""

    def test_true_for_versioned_id(self):
        assert _is_version_pinned("2106.09685v2") is True

    def test_false_for_unversioned_id(self):
        assert _is_version_pinned("2106.09685") is False


class TestArxivProviderSearch:
    """Tests for `ArxivProvider.search`."""

    def test_returns_parsed_results_and_total(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.params["search_query"] == "cat:cs.CL"
            return httpx.Response(200, content=_feed([_entry("2106.09685v2")], total_results=1))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.search("cat:cs.CL")

        result = asyncio.run(scenario())
        assert result["total_results"] == 1
        record = result["results"][0]
        assert record["arxiv_id"] == "2106.09685v2"
        assert record["title"] == "A Paper"
        assert record["authors"] == [{"name": "Jane Doe", "affiliation": "Example University"}]
        assert record["primary_category"] == "cs.CL"
        assert record["categories"] == ["cs.CL"]
        assert record["pdf_url"] == "http://arxiv.org/pdf/2106.09685v2"
        assert record["doi"] == "10.48550/arXiv.2106.09685v2"
        assert record["comment"] == "10 pages"

    def test_zero_hit_query_returns_empty_results(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_feed([], total_results=0))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.search("cat:zz.ZZ")

        result = asyncio.run(scenario())
        assert result == {"results": [], "total_results": 0}

    def test_max_results_over_bound_raises_invalid_request_without_network_call(self):
        calls = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(200, content=_feed([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                await provider.search("cat:cs.CL", max_results=2001)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())
        assert calls == []

    def test_cumulative_bound_over_limit_raises_invalid_request(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_feed([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                await provider.search("cat:cs.CL", start=29999, max_results=2)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())


class TestArxivProviderListTopN:
    """Tests for `ArxivProvider.list_top_n`."""

    def test_returns_up_to_n_records_ordered_by_submission(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.params["sortBy"] == "submittedDate"
            assert req.url.params["sortOrder"] == "descending"
            return httpx.Response(200, content=_feed([_entry("2106.09685v2"), _entry("2106.09686v1")]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.list_top_n("cs.CL", 2)

        result = asyncio.run(scenario())
        assert len(result["results"]) == 2
        assert "total_results" not in result


class TestArxivProviderFetchMetadata:
    """Tests for `ArxivProvider.fetch_metadata`."""

    def test_returns_records_for_recognised_ids(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.params["id_list"] == "2106.09685,2106.09686"
            return httpx.Response(200, content=_feed([_entry("2106.09685v2")]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.fetch_metadata(["2106.09685", "2106.09686"])

        result = asyncio.run(scenario())
        assert [r["arxiv_id"] for r in result["results"]] == ["2106.09685v2"]
        assert result["not_found"] == ["2106.09686"]

    def test_all_invalid_batch_returns_empty_results_and_full_not_found(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_feed([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.fetch_metadata(["9999.99999"])

        result = asyncio.run(scenario())
        assert result == {"results": [], "not_found": ["9999.99999"]}

    def test_versioned_request_matches_returned_versioned_id(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_feed([_entry("2106.09685v2")]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.fetch_metadata(["2106.09685v2"])

        result = asyncio.run(scenario())
        assert result["not_found"] == []
