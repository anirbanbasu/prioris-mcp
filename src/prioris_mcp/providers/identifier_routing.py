"""Grouping-level identifier resolution: `research_resolve_identifier`.

See docs/requirement-specification/01-architecture.md#identifier-routing-grouping-level and
docs/requirement-specification/05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests.
"""

import logging
import re
from typing import Protocol

import httpx

from prioris_mcp.errors import InvalidRequestError, UnsupportedProviderError
from prioris_mcp.providers import http as provider_http

logger = logging.getLogger(__name__)

DOI_RESOLVER_URL = "https://doi.org"

_ARXIV_ID_PATTERN = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$|^[a-zA-Z-]+(\.[A-Za-z]{2})?/\d{7}(v\d+)?$")
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/")

# Checked *before* any request to the resolved landing URL - see
# docs/requirement-specification/05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests.
_ALLOWED_DOMAINS = {
    "arxiv.org": "arxiv",
    "www.arxiv.org": "arxiv",
    "europepmc.org": "europepmc",
    "www.europepmc.org": "europepmc",
    "www.ncbi.nlm.nih.gov": "europepmc",
    "ncbi.nlm.nih.gov": "europepmc",
}


class _ResolvingProvider(Protocol):
    async def resolve_identifier(self, identifier: str, format: str) -> dict: ...


def _is_arxiv_identifier(identifier: str) -> bool:
    return bool(_ARXIV_ID_PATTERN.match(identifier))


def _is_europepmc_identifier(identifier: str) -> bool:
    return not _DOI_PATTERN.match(identifier) and (":" in identifier or identifier.upper().startswith("PMC"))


def _is_doi(identifier: str) -> bool:
    return bool(_DOI_PATTERN.match(identifier))


async def _resolve_identifier(provider: _ResolvingProvider, identifier: str, format: str) -> dict:
    """Call `provider.resolve_identifier`, translating its format validation into `InvalidRequestError`.

    A provider (e.g. `ArxivProvider`) raises a bare `ValueError` for a caller-supplied `format` it
    doesn't support - `format` is deliberately not a `Literal[...]` at the tool schema level since
    valid values depend on the resolving provider, so this is the first point that can validate it.
    A bare `ValueError` isn't one of `errors._ERROR_CODES`' mapped types, so it must be translated
    here rather than left to propagate past `call_returning_envelope` as a raw exception.
    """
    try:
        return await provider.resolve_identifier(identifier, format)
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc


def _extract_arxiv_id_from_url(url: str) -> str:
    match = re.search(r"/(?:abs|pdf|html)/([\w.\-/]+?)(?:\.pdf)?/?$", httpx.URL(url).path)
    if not match:
        raise UnsupportedProviderError(f"Could not extract an arXiv identifier from landing URL: {url}")
    return match.group(1)


def _extract_europepmc_id_from_url(url: str) -> str:
    path = httpx.URL(url).path
    pmc_match = re.search(r"/PMC(\d+)", path, re.IGNORECASE)
    if pmc_match:
        return f"PMC{pmc_match.group(1)}"
    article_match = re.search(r"/article/([A-Za-z]+)/(\w+)", path)
    if article_match:
        return f"{article_match.group(1)}:{article_match.group(2)}"
    raise UnsupportedProviderError(f"Could not extract a Europe PMC identifier from landing URL: {url}")


async def resolve_research_identifier(
    identifier: str,
    format: str,
    http_client: httpx.AsyncClient,
    arxiv_provider: _ResolvingProvider,
    europepmc_provider: _ResolvingProvider,
) -> dict:
    """Route `identifier` to the owning provider's native `resolve_identifier`.

    Self-identifying schemes (an arXiv ID, a Europe PMC identifier) route directly, with no
    network round-trip to determine ownership. A DOI is resolved via doi.org/Crossref first
    (without following the redirect); the `Location` header's domain is checked against an
    explicit allowlist **before** any further request is made - a domain outside that allowlist
    fails with `unsupported_provider` and no second request is ever issued, matching
    docs/requirement-specification/07-test-specification.md#research_resolve_identifier-acceptance-criteria.
    """
    if _is_arxiv_identifier(identifier):
        resolved = await _resolve_identifier(arxiv_provider, identifier, format)
        return {**resolved, "provider": "arxiv"}
    if _is_europepmc_identifier(identifier):
        resolved = await _resolve_identifier(europepmc_provider, identifier, format)
        return {**resolved, "provider": "europepmc"}
    if not _is_doi(identifier):
        raise UnsupportedProviderError(f"Unrecognised identifier scheme: {identifier}")

    redirect_response = await provider_http.request(
        http_client, "GET", f"{DOI_RESOLVER_URL}/{identifier}", follow_redirects=False
    )
    location = redirect_response.headers.get("location")
    if not location:
        raise UnsupportedProviderError(f"DOI {identifier} did not resolve to a landing page")
    domain = httpx.URL(location).host
    provider_name = _ALLOWED_DOMAINS.get(domain)
    if provider_name is None:
        raise UnsupportedProviderError(f"DOI {identifier} resolved to unsupported domain: {domain}")

    if provider_name == "arxiv":
        arxiv_id = _extract_arxiv_id_from_url(location)
        resolved = await _resolve_identifier(arxiv_provider, arxiv_id, format)
        return {**resolved, "provider": "arxiv"}
    europepmc_id = _extract_europepmc_id_from_url(location)
    resolved = await _resolve_identifier(europepmc_provider, europepmc_id, format)
    return {**resolved, "provider": "europepmc"}
