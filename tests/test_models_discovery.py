import pytest
from pydantic import ValidationError

from prioris_mcp.models.discovery import DiscoveryFetchRoute, DiscoveryHit, DiscoveryResult


class TestDiscoveryFetchRoute:
    """Validation tests for fetch-ladder routes."""

    def test_known_provider_route_accepts_provider_and_identifier(self):
        route = DiscoveryFetchRoute(kind="known_provider", provider="arxiv", identifier="2106.09685v2")
        assert route.provider == "arxiv"

    def test_oa_link_route_accepts_pdf_url(self):
        route = DiscoveryFetchRoute(kind="oa_link", pdf_url="https://example.org/paper.pdf")
        assert route.pdf_url == "https://example.org/paper.pdf"

    def test_manual_upload_route_needs_no_extra_fields(self):
        route = DiscoveryFetchRoute(kind="manual_upload")
        assert route.provider is None
        assert route.pdf_url is None

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValidationError):
            DiscoveryFetchRoute(kind="not_a_real_kind")

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            DiscoveryFetchRoute.model_validate({"kind": "manual_upload", "extra_field": "nope"})


class TestDiscoveryHit:
    """Validation tests for discovery hits."""

    def test_minimal_hit_defaults_optional_fields(self):
        hit = DiscoveryHit(
            openalex_id="W2741809807",
            score=0.87,
            fetch_route=DiscoveryFetchRoute(kind="manual_upload"),
        )
        assert hit.title is None
        assert hit.authors == []

    def test_hit_with_full_metadata(self):
        hit = DiscoveryHit(
            openalex_id="W2741809807",
            title="Attention Is All You Need",
            abstract="We propose the Transformer...",
            authors=[{"name": "Ashish Vaswani"}],
            publication_year=2017,
            doi="10.48550/arxiv.1706.03762",
            score=0.95,
            fetch_route=DiscoveryFetchRoute(kind="known_provider", provider="arxiv", identifier="1706.03762"),
        )
        assert hit.authors[0].name == "Ashish Vaswani"


class TestDiscoveryResult:
    """Validation tests for discovery result envelopes."""

    def test_empty_hits_is_valid(self):
        result = DiscoveryResult(hits=[], page=1, per_page=25, total=0, has_more=False)
        assert result.hits == []
        assert result.has_more is False

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            DiscoveryResult.model_validate(
                {"hits": [], "page": 1, "per_page": 25, "total": 0, "has_more": False, "extra_field": "nope"}
            )
