"""OpenAlex semantic-search client for external research discovery.

See docs/requirement-specification/02-discovery.md.
"""

import httpx

from prioris_mcp.discovery.fetch_ladder import resolve_fetch_route
from prioris_mcp.errors import InvalidRequestError
from prioris_mcp.models.discovery import DiscoveryHit, DiscoveryResult
from prioris_mcp.providers import http as provider_http

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_SEARCH_SEMANTIC_MAX_CHARS = 2000
# OpenAlex's search.semantic caps total matches at 50 per query, not the ordinary /works
# per-page cap of 200 - see https://help.openalex.org/api/semantic-search/.
OPENALEX_MAX_RESULTS = 50


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Rebuild a plain-text abstract from OpenAlex's word-position inverted index."""
    if not inverted_index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        positioned.extend((position, word) for position in positions)
    positioned.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positioned)


def _work_to_hit(work: dict) -> DiscoveryHit:
    """Map one OpenAlex work object to its MCP response representation."""
    openalex_id = work.get("id", "").rsplit("/", maxsplit=1)[-1]
    authors = [
        {"name": authorship["author"]["display_name"]}
        for authorship in work.get("authorships", [])
        if authorship.get("author", {}).get("display_name")
    ]
    return DiscoveryHit(
        openalex_id=openalex_id,
        title=work.get("title"),
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        authors=authors,
        publication_year=work.get("publication_year"),
        doi=work.get("doi"),
        score=work.get("relevance_score"),
        fetch_route=resolve_fetch_route(work),
    )


class OpenAlexClient:
    """Thin wrapper around OpenAlex's ``/works`` ``search.semantic`` filter."""

    def __init__(self, http_client: httpx.AsyncClient, mailto: str | None) -> None:
        self._http_client = http_client
        self._mailto = mailto

    async def search_semantic(self, query: str, *, max_results: int) -> DiscoveryResult:
        """Return embedding-ranked OpenAlex candidates for ``query``.

        Raises:
            InvalidRequestError: The query is too long or max_results is outside OpenAlex's cap.
        """
        if len(query) > OPENALEX_SEARCH_SEMANTIC_MAX_CHARS:
            raise InvalidRequestError(
                f"query exceeds OpenAlex search.semantic's {OPENALEX_SEARCH_SEMANTIC_MAX_CHARS}-character limit"
            )
        if not 1 <= max_results <= OPENALEX_MAX_RESULTS:
            raise InvalidRequestError(f"max_results must be between 1 and {OPENALEX_MAX_RESULTS}, got {max_results}")
        params = {"search.semantic": query, "per-page": str(max_results)}
        if self._mailto:
            params["mailto"] = self._mailto
        response = await provider_http.request(self._http_client, "GET", OPENALEX_WORKS_URL, params=params)
        payload = response.json()
        return DiscoveryResult(hits=[_work_to_hit(work) for work in payload.get("results", [])])
