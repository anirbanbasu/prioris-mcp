from prioris_mcp.discovery.fetch_ladder import resolve_fetch_route


class TestKnownProviderRoute:
    """Known-provider fetch-ladder routing."""

    def test_arxiv_doi_routes_to_arxiv(self):
        work = {"doi": "https://doi.org/10.48550/arxiv.1706.03762", "ids": {}, "locations": []}
        route = resolve_fetch_route(work)
        assert route.kind == "known_provider"
        assert route.provider == "arxiv"
        assert route.identifier == "1706.03762"

    def test_arxiv_doi_match_is_case_insensitive(self):
        work = {"doi": "https://doi.org/10.48550/ARXIV.1706.03762", "ids": {}, "locations": []}
        assert resolve_fetch_route(work).provider == "arxiv"

    def test_pmcid_routes_to_europepmc(self):
        work = {
            "doi": None,
            "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4767193"},
            "locations": [],
        }
        route = resolve_fetch_route(work)
        assert route.kind == "known_provider"
        assert route.provider == "europepmc"
        assert route.identifier == "PMC4767193"

    def test_known_provider_checked_before_oa_link(self):
        work = {
            "doi": "https://doi.org/10.48550/arxiv.1706.03762",
            "ids": {},
            "locations": [{"version": "publishedVersion", "pdf_url": "https://example.org/paper.pdf"}],
        }
        assert resolve_fetch_route(work).kind == "known_provider"


class TestOaLinkRoute:
    """OA-link fetch-ladder routing."""

    def test_prefers_published_over_accepted_over_submitted(self):
        work = {
            "doi": "https://doi.org/10.1000/unrelated",
            "ids": {},
            "locations": [
                {"version": "submittedVersion", "pdf_url": "https://example.org/submitted.pdf"},
                {"version": "acceptedVersion", "pdf_url": "https://example.org/accepted.pdf"},
                {"version": "publishedVersion", "pdf_url": "https://example.org/published.pdf"},
            ],
        }
        route = resolve_fetch_route(work)
        assert route.kind == "oa_link"
        assert route.pdf_url == "https://example.org/published.pdf"

    def test_skips_locations_with_missing_or_non_string_pdf_url(self):
        work = {
            "doi": None,
            "ids": {},
            "locations": [
                {"version": "publishedVersion", "pdf_url": 3},
                {"version": "acceptedVersion", "pdf_url": "https://example.org/accepted.pdf"},
            ],
        }
        assert resolve_fetch_route(work).pdf_url == "https://example.org/accepted.pdf"

    def test_falls_back_to_best_oa_location_when_locations_absent(self):
        work = {
            "doi": None,
            "ids": {},
            "best_oa_location": {"version": "publishedVersion", "pdf_url": "https://example.org/best.pdf"},
        }
        route = resolve_fetch_route(work)
        assert route.kind == "oa_link"
        assert route.pdf_url == "https://example.org/best.pdf"

    def test_falls_back_to_best_oa_location_when_locations_empty(self):
        work = {
            "doi": None,
            "ids": {},
            "locations": [],
            "best_oa_location": {"version": "publishedVersion", "pdf_url": "https://example.org/best.pdf"},
        }
        assert resolve_fetch_route(work).pdf_url == "https://example.org/best.pdf"

    def test_uses_any_version_when_no_preferred_version_has_a_pdf(self):
        work = {
            "doi": None,
            "ids": {},
            "locations": [{"version": "other", "pdf_url": "https://example.org/other.pdf"}],
        }
        assert resolve_fetch_route(work).pdf_url == "https://example.org/other.pdf"


class TestManualUploadRoute:
    """Manual-upload fallback routing."""

    def test_no_doi_no_pmcid_no_pdf_url_falls_through(self):
        work = {"doi": None, "ids": {}, "locations": [{"version": "publishedVersion", "pdf_url": None}]}
        route = resolve_fetch_route(work)
        assert route.kind == "manual_upload"
        assert route.provider is None
        assert route.pdf_url is None

    def test_missing_locations_and_best_oa_location_falls_through(self):
        assert resolve_fetch_route({"doi": None, "ids": {}}).kind == "manual_upload"
