import asyncio
from collections.abc import Callable

import httpx
import pytest

from prioris_mcp.errors import FormatUnavailableError, InvalidRequestError, NotFoundError
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.providers.arxiv import ArxivProvider, _bare_id, _is_version_pinned
from prioris_mcp.rate_limit import ProviderRequestQueue
from prioris_mcp.storage import FilesystemStorageBackend

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
    provider = ArxivProvider(
        storage=None,
        queue=queue,
        http_client=client,
        pdf_backend=_StubParserBackend(),
        html_backend=_StubParserBackend(),
    )
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


def _provider_with_storage(
    handler: Callable[[httpx.Request], httpx.Response], tmp_path
) -> tuple[ArxivProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
    storage = FilesystemStorageBackend(base_dir=tmp_path)
    provider = ArxivProvider(
        storage=storage,
        queue=queue,
        http_client=client,
        pdf_backend=_StubParserBackend(),
        html_backend=_StubParserBackend(),
    )
    return provider, client


class TestArxivProviderResolveIdentifier:
    """Tests for `ArxivProvider.resolve_identifier`."""

    def test_versioned_identifier_skips_metadata_call(self):
        calls = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(200, content=_feed([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.resolve_identifier("2106.09685v2", "pdf")

        result = asyncio.run(scenario())
        assert result == {
            "identifier": "2106.09685v2",
            "resolved_url": "https://arxiv.org/pdf/2106.09685v2",
            "format": "pdf",
        }
        assert calls == []

    def test_unversioned_identifier_resolves_current_version_via_metadata(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_feed([_entry("2106.09685v2")]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                return await provider.resolve_identifier("2106.09685", "html")

        result = asyncio.run(scenario())
        assert result["identifier"] == "2106.09685v2"
        assert result["resolved_url"] == "https://arxiv.org/html/2106.09685v2"

    def test_unrecognised_identifier_raises_not_found(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_feed([]))

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                await provider.resolve_identifier("9999.99999", "pdf")

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_unsupported_format_raises_value_error(self):
        # The ABC declares `format: str` (not a `Literal`), so this validates provider-level
        # misuse directly - the MCP tool layer's `Literal["pdf", "html"]` annotation only blocks
        # invalid formats reaching this point via the registered tools, not via direct calls to
        # the provider. Version-pinned so no network call is needed to exercise it.
        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        async def scenario():
            provider, client = _provider_with_handler(handler)
            async with client:
                await provider.resolve_identifier("2106.09685v2", "epub")

        with pytest.raises(ValueError, match="Unsupported format"):
            asyncio.run(scenario())


class TestArxivProviderFetchFullText:
    """Tests for `ArxivProvider.fetch_full_text`."""

    def test_pdf_fetch_persists_and_returns_reference(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes")

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                return await provider.fetch_full_text("2106.09685v2", "pdf")

        result = asyncio.run(scenario())
        assert result["format"] == "pdf"
        assert result["served_from_storage"] is False
        assert result["size_bytes"] == len(b"%PDF-1.4 fake pdf bytes")
        assert result["resource_uri"] == "research://arxiv/2106.09685v2/pdf/fulltext"

    def test_second_call_is_served_from_storage_without_a_second_request(self, tmp_path):
        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes")

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                first = await provider.fetch_full_text("2106.09685v2", "pdf")
                second = await provider.fetch_full_text("2106.09685v2", "pdf")
                return first, second, call_count

        first, second, count = asyncio.run(scenario())
        assert first["served_from_storage"] is False
        assert second["served_from_storage"] is True
        assert count == 1

    def test_html_404_raises_format_unavailable_not_not_found(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                await provider.fetch_full_text("2106.09685v2", "html")

        with pytest.raises(FormatUnavailableError):
            asyncio.run(scenario())

    def test_unversioned_identifier_is_persisted_under_its_canonical_form(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.params.get("id_list"):
                return httpx.Response(200, content=_feed([_entry("2106.09685v2")]))
            return httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes")

        async def scenario():
            provider, client = _provider_with_storage(handler, tmp_path)
            async with client:
                return await provider.fetch_full_text("2106.09685", "pdf")

        result = asyncio.run(scenario())
        assert result["resource_uri"] == "research://arxiv/2106.09685v2/pdf/fulltext"

    def test_redirect_is_followed_and_final_content_persisted(self, tmp_path):
        """Regression test for `PriorisMCP.__init__`'s `httpx.AsyncClient(follow_redirects=True)`.

        Mirrors the real client configuration (see `src/prioris_mcp/server.py`): with
        `follow_redirects=True`, a 302 from the full-text URL is followed transparently and the
        final 200's body - not the redirect stub - is what gets persisted. Constructing the
        client here with `follow_redirects=False` (httpx's default, and what this codebase used
        to construct before the fix) would instead persist the tiny redirect-stub body with
        `served_from_storage=False`, silently masking the failure - this is the exact bug class
        Finding 2 of the final review flagged.
        """
        real_content = b"%PDF-1.4 real pdf bytes after redirect"

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/pdf/2106.09685v2":
                return httpx.Response(302, headers={"Location": "https://arxiv.org/pdf/2106.09685v2-final"})
            return httpx.Response(200, content=real_content)

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
            queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
            storage = FilesystemStorageBackend(base_dir=tmp_path)
            provider = ArxivProvider(
                storage=storage,
                queue=queue,
                http_client=client,
                pdf_backend=_StubParserBackend(),
                html_backend=_StubParserBackend(),
            )
            async with client:
                return await provider.fetch_full_text("2106.09685v2", "pdf")

        result = asyncio.run(scenario())
        assert result["size_bytes"] == len(real_content)


class _StubParserBackend(ParserBackend):
    def __init__(self, markdown: str = "# parsed") -> None:
        self.markdown = markdown
        self.call_count = 0

    async def to_markdown(self, content: bytes) -> str:
        self.call_count += 1
        return self.markdown


def _provider_with_backends(
    handler: Callable[[httpx.Request], httpx.Response], tmp_path, pdf_backend=None, html_backend=None
) -> tuple[ArxivProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    queue = ProviderRequestQueue(base_spacing_seconds=0.0, max_total_backoff_seconds=5.0)
    storage = FilesystemStorageBackend(base_dir=tmp_path)
    provider = ArxivProvider(
        storage=storage,
        queue=queue,
        http_client=client,
        pdf_backend=pdf_backend or _StubParserBackend(),
        html_backend=html_backend or _StubParserBackend(),
    )
    return provider, client


class TestArxivProviderParseFullText:
    """Tests for `ArxivProvider.parse_full_text`."""

    def test_not_found_when_source_never_fetched(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("must not make a network request")

        async def scenario():
            provider, client = _provider_with_backends(handler, tmp_path)
            async with client:
                await provider.parse_full_text("2106.09685v2", "pdf")

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_parses_and_persists_markdown_once_source_is_fetched(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.4 fake bytes")

        pdf_backend = _StubParserBackend(markdown="# Parsed PDF")

        async def scenario():
            provider, client = _provider_with_backends(handler, tmp_path, pdf_backend=pdf_backend)
            async with client:
                await provider.fetch_full_text("2106.09685v2", "pdf")
                first = await provider.parse_full_text("2106.09685v2", "pdf")
                second = await provider.parse_full_text("2106.09685v2", "pdf")
                return first, second

        first, second = asyncio.run(scenario())
        assert first == {"markdown": "# Parsed PDF", "resource_uri": "research://arxiv/2106.09685v2/pdf/markdown"}
        assert second == first
        assert pdf_backend.call_count == 1  # second call served from storage, not re-parsed

    def test_uses_html_backend_for_html_format(self, tmp_path):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>fake</html>")

        html_backend = _StubParserBackend(markdown="# Parsed HTML")

        async def scenario():
            provider, client = _provider_with_backends(handler, tmp_path, html_backend=html_backend)
            async with client:
                await provider.fetch_full_text("2106.09685v2", "html")
                return await provider.parse_full_text("2106.09685v2", "html")

        result = asyncio.run(scenario())
        assert result["markdown"] == "# Parsed HTML"

    def test_unversioned_identifier_finds_content_fetched_under_its_canonical_form(self, tmp_path):
        """Regression test: parse_full_text must canonicalize identifier before touching storage.

        `fetch_full_text` persists content under the canonical, version-pinned id (see
        docs/requirement-specification/02-storage.md#identifier-canonicalisation). A caller who
        fetched with an unversioned id and then parses with that same unversioned id must still
        find the content - parse_full_text must not look it up under the raw, unversioned id.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.params.get("id_list"):
                return httpx.Response(200, content=_feed([_entry("2106.09685v2")]))
            return httpx.Response(200, content=b"%PDF-1.4 fake bytes")

        pdf_backend = _StubParserBackend(markdown="# Parsed PDF")

        async def scenario():
            provider, client = _provider_with_backends(handler, tmp_path, pdf_backend=pdf_backend)
            async with client:
                await provider.fetch_full_text("2106.09685", "pdf")
                return await provider.parse_full_text("2106.09685", "pdf")

        result = asyncio.run(scenario())
        assert result == {"markdown": "# Parsed PDF", "resource_uri": "research://arxiv/2106.09685v2/pdf/markdown"}
