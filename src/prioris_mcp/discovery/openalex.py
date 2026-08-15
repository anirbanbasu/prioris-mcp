"""OpenAlex semantic-search client for external research discovery.

See docs/requirement-specification/02-discovery.md.
"""

from urllib.parse import quote

import httpx

from prioris_mcp.discovery.fetch_ladder import resolve_fetch_route
from prioris_mcp.errors import InvalidRequestError
from prioris_mcp.models.discovery import DiscoveryHit, DiscoveryResult, OpenAlexWorkType, OpenAlexWorkTypesResult
from prioris_mcp.providers import http as provider_http

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_WORK_TYPES_URL = "https://api.openalex.org/work-types"
OPENALEX_SEARCH_SEMANTIC_MAX_CHARS = 2000
# OpenAlex's search.semantic caps total matches at 50 per query, not the ordinary /works
# per-page cap of 200 - see https://help.openalex.org/api/semantic-search/.
OPENALEX_MAX_RESULTS = 50
# OpenAlex's own servers have been observed to hang (repeated HTTP 504) when the `filter`
# parameter's `:` separator is percent-encoded (%3A) rather than sent literally - both are RFC
# 3986-legal in a query string, but only the unescaped form works reliably in practice. `,` and
# `=` are left unescaped for the same reason (they combine/assign filter clauses); `>`/`<` still
# get encoded by `quote`, which OpenAlex does accept.
_FILTER_SAFE_CHARS = ":,="


def _build_works_url(query: str, *, per_page: int, page: int, filter_value: str | None, api_key: str | None) -> str:
    """Build the `/works` request URL by hand, keeping `filter`'s `:`/`,`/`=` unescaped.

    httpx's `params=` dict support routes every value through the same encoding regardless of
    key (breaking `filter`, see `_FILTER_SAFE_CHARS`), and passing `params=` alongside a `url`
    that already carries a query string silently discards that existing query instead of merging
    with it - so the full query string is assembled here rather than relying on either.
    """
    parts = [f"search.semantic={quote(query, safe='')}", f"per-page={per_page}", f"page={page}"]
    if filter_value:
        parts.append(f"filter={quote(filter_value, safe=_FILTER_SAFE_CHARS)}")
    if api_key:
        parts.append(f"api_key={quote(api_key, safe='')}")
    return f"{OPENALEX_WORKS_URL}?{'&'.join(parts)}"


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
    """Thin wrapper around OpenAlex's ``/works`` ``search.semantic`` filter and ``/work-types``."""

    def __init__(self, http_client: httpx.AsyncClient, api_key: str | None) -> None:
        self._http_client = http_client
        self._api_key = api_key

    async def search_semantic(
        self,
        query: str,
        *,
        max_results: int,
        page: int = 1,
        from_year: int | None = None,
        to_year: int | None = None,
        open_access_only: bool = False,
    ) -> DiscoveryResult:
        """Return embedding-ranked OpenAlex candidates for ``query``.

        `from_year`/`to_year`/`open_access_only` map to OpenAlex's own `publication_year`/`is_oa`
        filters - the only two filter fields confirmed (by live testing against OpenAlex, not
        just its docs) to actually restrict `search.semantic` results; other filter fields it
        nominally supports were found not to reliably narrow semantic-search results, so aren't
        exposed here.

        Raises:
            InvalidRequestError: `query`/`max_results`/`page` are out of range, or `from_year` is
                after `to_year`.
        """
        if len(query) > OPENALEX_SEARCH_SEMANTIC_MAX_CHARS:
            raise InvalidRequestError(
                f"query exceeds OpenAlex search.semantic's {OPENALEX_SEARCH_SEMANTIC_MAX_CHARS}-character limit"
            )
        if not 1 <= max_results <= OPENALEX_MAX_RESULTS:
            raise InvalidRequestError(f"max_results must be between 1 and {OPENALEX_MAX_RESULTS}, got {max_results}")
        if page < 1:
            raise InvalidRequestError(f"page must be >= 1, got {page}")
        if from_year is not None and to_year is not None and from_year > to_year:
            raise InvalidRequestError(f"from_year must be <= to_year, got from_year={from_year}, to_year={to_year}")

        filters = []
        if from_year is not None:
            filters.append(f"publication_year:>={from_year}")
        if to_year is not None:
            filters.append(f"publication_year:<={to_year}")
        if open_access_only:
            filters.append("is_oa:true")

        url = _build_works_url(
            query,
            per_page=max_results,
            page=page,
            filter_value=",".join(filters) if filters else None,
            api_key=self._api_key,
        )
        response = await provider_http.request(self._http_client, "GET", url)
        payload = response.json()
        meta = payload.get("meta") or {}
        total = meta.get("count", 0)
        return DiscoveryResult(
            hits=[_work_to_hit(work) for work in payload.get("results", [])],
            page=page,
            per_page=max_results,
            total=total,
            has_more=page * max_results < total,
        )

    async def list_work_types(self) -> OpenAlexWorkTypesResult:
        """Return every OpenAlex work `type` value and its description, as reference data.

        See https://help.openalex.org/data/work-types - a static-ish reference list (25 entries
        as of writing), fetched live rather than hardcoded so a new type OpenAlex adds shows up
        without a code change. `research_discovery` itself doesn't filter by `type` (found, via
        live testing, to silently under-filter alongside `search.semantic` - see
        `search_semantic`'s docstring); this is reference data for a caller inspecting a hit's own
        metadata, not a discovery-tool filter parameter.
        """
        params = {"per-page": "100"}
        if self._api_key:
            params["api_key"] = self._api_key
        response = await provider_http.request(self._http_client, "GET", OPENALEX_WORK_TYPES_URL, params=params)
        payload = response.json()
        types = sorted(
            (
                OpenAlexWorkType(
                    code=work_type["id"].rsplit("/", maxsplit=1)[-1],
                    name=work_type["display_name"],
                    description=work_type["description"],
                )
                for work_type in payload.get("results", [])
            ),
            key=lambda work_type: work_type.code,
        )
        return OpenAlexWorkTypesResult(types=types)
