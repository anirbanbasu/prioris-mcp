import asyncio

import httpx
import pytest

from prioris_mcp.errors import InvalidRequestError, UnsupportedProviderError
from prioris_mcp.providers.identifier_routing import resolve_research_identifier


class _StubResolvingProvider:
    """Minimal stand-in for ArxivProvider/EuropePmcProvider's `resolve_identifier`."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def resolve_identifier(self, identifier: str, format: str) -> dict:
        self.calls.append((identifier, format))
        return self.response


class _UnsupportedFormatProvider:
    """Simulates `ArxivProvider.resolve_identifier` raising a bare `ValueError` for a bad format."""

    async def resolve_identifier(self, identifier: str, format: str) -> dict:
        raise ValueError(f"Unsupported format for arXiv: {format}")


def _no_op_client() -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"must not make an HTTP request for a self-identifying scheme: {req.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestResolveResearchIdentifier:
    """Tests for `resolve_research_identifier`."""

    def test_arxiv_id_routes_directly_with_no_doi_round_trip(self):
        arxiv_stub = _StubResolvingProvider(
            {"identifier": "2106.09685v2", "resolved_url": "https://arxiv.org/pdf/2106.09685v2", "format": "pdf"}
        )
        europepmc_stub = _StubResolvingProvider({})

        async def scenario():
            client = _no_op_client()
            async with client:
                return await resolve_research_identifier("2106.09685v2", "pdf", client, arxiv_stub, europepmc_stub)

        result = asyncio.run(scenario())
        assert result["provider"] == "arxiv"
        assert arxiv_stub.calls == [("2106.09685v2", "pdf")]
        assert europepmc_stub.calls == []

    def test_provider_value_error_for_unsupported_format_becomes_invalid_request_error(self):
        """A provider's own `ValueError` (e.g. `ArxivProvider` on a non-pdf/html `format`) must surface.

        Must surface as `InvalidRequestError`, not the bare `ValueError` - only `InvalidRequestError`
        is mapped in `errors._ERROR_CODES` and so caught by `call_returning_envelope`.
        """
        arxiv_stub = _UnsupportedFormatProvider()
        europepmc_stub = _StubResolvingProvider({})

        async def scenario():
            client = _no_op_client()
            async with client:
                await resolve_research_identifier("2106.09685v2", "xml", client, arxiv_stub, europepmc_stub)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_europepmc_id_routes_directly_with_no_doi_round_trip(self):
        arxiv_stub = _StubResolvingProvider({})
        europepmc_stub = _StubResolvingProvider(
            {
                "identifier": "PMC:PMC4767193",
                "resolved_url": "https://x/fullTextXML",
                "format": "xml",
                "full_text_available": True,
            }
        )

        async def scenario():
            client = _no_op_client()
            async with client:
                return await resolve_research_identifier("MED:26551875", "xml", client, arxiv_stub, europepmc_stub)

        result = asyncio.run(scenario())
        assert result["provider"] == "europepmc"
        assert europepmc_stub.calls == [("MED:26551875", "xml")]
        assert arxiv_stub.calls == []

    def test_bare_pmcid_routes_directly_to_europepmc(self):
        arxiv_stub = _StubResolvingProvider({})
        europepmc_stub = _StubResolvingProvider(
            {
                "identifier": "PMC:PMC4767193",
                "resolved_url": "https://x/fullTextXML",
                "format": "xml",
                "full_text_available": True,
            }
        )

        async def scenario():
            client = _no_op_client()
            async with client:
                return await resolve_research_identifier("PMC4767193", "xml", client, arxiv_stub, europepmc_stub)

        result = asyncio.run(scenario())
        assert result["provider"] == "europepmc"

    def test_doi_resolving_to_arxiv_routes_to_arxiv_provider(self):
        arxiv_stub = _StubResolvingProvider(
            {"identifier": "2106.09685v2", "resolved_url": "https://arxiv.org/pdf/2106.09685v2", "format": "pdf"}
        )
        europepmc_stub = _StubResolvingProvider({})

        def handler(req: httpx.Request) -> httpx.Response:
            assert str(req.url) == "https://doi.org/10.48550/arXiv.2106.09685"
            return httpx.Response(302, headers={"location": "https://arxiv.org/abs/2106.09685v2"})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                return await resolve_research_identifier(
                    "10.48550/arXiv.2106.09685", "pdf", client, arxiv_stub, europepmc_stub
                )

        result = asyncio.run(scenario())
        assert result["provider"] == "arxiv"
        assert arxiv_stub.calls == [("2106.09685v2", "pdf")]

    def test_doi_resolving_to_ncbi_pmc_routes_to_europepmc_provider(self):
        arxiv_stub = _StubResolvingProvider({})
        europepmc_stub = _StubResolvingProvider(
            {
                "identifier": "PMC:PMC4767193",
                "resolved_url": "https://x/fullTextXML",
                "format": "xml",
                "full_text_available": True,
            }
        )

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4767193/"})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                return await resolve_research_identifier("10.1000/example", "xml", client, arxiv_stub, europepmc_stub)

        result = asyncio.run(scenario())
        assert result["provider"] == "europepmc"
        assert europepmc_stub.calls == [("PMC4767193", "xml")]

    def test_doi_resolving_elsewhere_fails_without_a_second_request(self):
        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise AssertionError("must not fetch the landing page")
            return httpx.Response(302, headers={"location": "https://example-publisher.test/articles/123"})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                await resolve_research_identifier(
                    "10.1234/abcde", "pdf", client, _StubResolvingProvider({}), _StubResolvingProvider({})
                )

        with pytest.raises(UnsupportedProviderError):
            asyncio.run(scenario())
        assert call_count == 1

    def test_unrecognised_non_doi_identifier_raises_unsupported_provider(self):
        async def scenario():
            client = _no_op_client()
            async with client:
                await resolve_research_identifier(
                    "not-an-identifier", "pdf", client, _StubResolvingProvider({}), _StubResolvingProvider({})
                )

        with pytest.raises(UnsupportedProviderError):
            asyncio.run(scenario())

    def test_doi_redirect_with_no_location_header_raises_unsupported_provider(self):
        """A 3xx with no `Location` header must fail closed.

        Not be treated as "no redirect happened" and proceed to fetch some derived URL - see
        docs/requirement-specification/05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(302)  # no Location header

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                await resolve_research_identifier(
                    "10.1234/abcde", "pdf", client, _StubResolvingProvider({}), _StubResolvingProvider({})
                )

        with pytest.raises(UnsupportedProviderError):
            asyncio.run(scenario())

    def test_doi_resolving_to_arxiv_with_unrecognised_path_raises_unsupported_provider(self):
        """Covers `_extract_arxiv_id_from_url`'s failure branch.

        The domain is allowlisted for arXiv but the landing URL's path doesn't match arXiv's
        `/abs|pdf|html/{id}` shape.
        """
        arxiv_stub = _StubResolvingProvider({})
        europepmc_stub = _StubResolvingProvider({})

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://arxiv.org/some/other/path"})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                await resolve_research_identifier("10.48550/arXiv.unknown", "pdf", client, arxiv_stub, europepmc_stub)

        with pytest.raises(UnsupportedProviderError):
            asyncio.run(scenario())
        assert arxiv_stub.calls == []
        assert europepmc_stub.calls == []

    def test_doi_resolving_to_europepmc_article_path_routes_to_europepmc_provider(self):
        r"""Covers `_extract_europepmc_id_from_url`'s `/article/{source}/{id}` match branch.

        Distinct from the `/PMC\d+` branch already exercised by
        `test_doi_resolving_to_ncbi_pmc_routes_to_europepmc_provider`.
        """
        arxiv_stub = _StubResolvingProvider({})
        europepmc_stub = _StubResolvingProvider(
            {
                "identifier": "MED:26551875",
                "resolved_url": "https://x/fullTextXML",
                "format": "xml",
                "full_text_available": True,
            }
        )

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://europepmc.org/article/MED/26551875"})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                return await resolve_research_identifier("10.1000/example", "xml", client, arxiv_stub, europepmc_stub)

        result = asyncio.run(scenario())
        assert result["provider"] == "europepmc"
        assert europepmc_stub.calls == [("MED:26551875", "xml")]

    def test_doi_resolving_to_europepmc_with_unrecognised_path_raises_unsupported_provider(self):
        r"""Covers `_extract_europepmc_id_from_url`'s failure branch.

        The domain is allowlisted for Europe PMC but the landing URL's path matches neither
        the `/PMC\d+` nor the `/article/{source}/{id}` pattern.
        """
        arxiv_stub = _StubResolvingProvider({})
        europepmc_stub = _StubResolvingProvider({})

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://europepmc.org/some/other/path"})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with client:
                await resolve_research_identifier("10.1000/example", "xml", client, arxiv_stub, europepmc_stub)

        with pytest.raises(UnsupportedProviderError):
            asyncio.run(scenario())
        assert arxiv_stub.calls == []
        assert europepmc_stub.calls == []
