"""Resolve an OpenAlex work to a known-provider route, OA link, or manual upload.

See docs/requirement-specification/02-discovery.md. This module is pure logic over an already
parsed OpenAlex work object and performs no network I/O.
"""

import re

from prioris_mcp.models.discovery import DiscoveryFetchRoute

_ARXIV_DOI_PATTERN = re.compile(r"10\.48550/arxiv\.(?P<arxiv_id>.+)$", re.IGNORECASE)
_PMCID_PATTERN = re.compile(r"(PMC\d+)", re.IGNORECASE)
_VERSION_PREFERENCE = ("publishedVersion", "acceptedVersion", "submittedVersion")


def _known_provider_route(work: dict) -> DiscoveryFetchRoute | None:
    doi = work.get("doi")
    if isinstance(doi, str):
        match = _ARXIV_DOI_PATTERN.search(doi)
        if match:
            return DiscoveryFetchRoute(kind="known_provider", provider="arxiv", identifier=match.group("arxiv_id"))

    ids = work.get("ids")
    pmcid_url = ids.get("pmcid") if isinstance(ids, dict) else None
    if isinstance(pmcid_url, str):
        match = _PMCID_PATTERN.search(pmcid_url)
        if match:
            return DiscoveryFetchRoute(kind="known_provider", provider="europepmc", identifier=match.group(1).upper())
    return None


def _locations(work: dict) -> list[dict]:
    locations = work.get("locations")
    if isinstance(locations, list) and locations:
        return [location for location in locations if isinstance(location, dict)]
    best_oa_location = work.get("best_oa_location")
    return [best_oa_location] if isinstance(best_oa_location, dict) else []


def _oa_link_route(work: dict) -> DiscoveryFetchRoute | None:
    locations = _locations(work)
    for preferred_version in _VERSION_PREFERENCE:
        for location in locations:
            pdf_url = location.get("pdf_url")
            if location.get("version") == preferred_version and isinstance(pdf_url, str) and pdf_url:
                return DiscoveryFetchRoute(kind="oa_link", pdf_url=pdf_url)
    for location in locations:
        pdf_url = location.get("pdf_url")
        if isinstance(pdf_url, str) and pdf_url:
            return DiscoveryFetchRoute(kind="oa_link", pdf_url=pdf_url)
    return None


def resolve_fetch_route(work: dict) -> DiscoveryFetchRoute:
    """Resolve an OpenAlex work to a supported full-text route or manual-upload fallback."""
    return _known_provider_route(work) or _oa_link_route(work) or DiscoveryFetchRoute(kind="manual_upload")
