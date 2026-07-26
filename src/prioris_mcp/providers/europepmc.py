"""Europe PMC research-publication provider.

See docs/requirement-specification/01-architecture.md and
docs/requirement-specification/06-interface-specification.md#europe-pmc.
"""

import logging

import httpx

from prioris_mcp.errors import FormatUnavailableError, NotFoundError
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.providers import http as provider_http
from prioris_mcp.providers.base import ResearchPublicationProvider
from prioris_mcp.rate_limit import ProviderRequestQueue
from prioris_mcp.storage import StorageBackend

logger = logging.getLogger(__name__)

EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EUROPEPMC_BASE_SPACING_SECONDS = 3.0


def _split_identifier(identifier: str) -> tuple[str, str]:
    """Split `identifier` into (source, id), defaulting a bare PMCID's source to 'PMC'."""
    if ":" in identifier:
        source, _, raw_id = identifier.partition(":")
        return source, raw_id
    return "PMC", identifier.removeprefix("PMC")


def _parse_record(entry: dict) -> dict:
    source = entry.get("source", "")
    raw_id = entry.get("id", "")
    author_list = (entry.get("authorList") or {}).get("author", [])
    authors = [
        {
            "full_name": a.get("fullName"),
            "first_name": a.get("firstName"),
            "last_name": a.get("lastName"),
            "initials": a.get("initials"),
        }
        for a in author_list
    ]
    return {
        "identifier": f"{source}:{raw_id}",
        "pmid": entry.get("pmid"),
        "pmcid": entry.get("pmcid"),
        "doi": entry.get("doi"),
        "title": entry.get("title"),
        "authors": authors,
        "abstract": entry.get("abstractText"),
        "journal": entry.get("journalInfo"),
        "pub_year": entry.get("pubYear"),
        "is_open_access": entry.get("isOpenAccess"),
        "license": entry.get("license"),
        "full_text_available": entry.get("inEPMC") == "Y",
    }


def _matches(identifier: str, results: list[dict]) -> bool:
    source, raw_id = _split_identifier(identifier)
    return any(
        (source == "MED" and r["pmid"] == raw_id and r["identifier"].startswith("MED:"))
        or (source == "PMC" and r["pmcid"] == f"PMC{raw_id}" and r["identifier"].startswith("PMC:"))
        or r["identifier"] == f"{source}:{raw_id}"
        for r in results
    )


class EuropePmcProvider(ResearchPublicationProvider):
    """Europe PMC implementation of `ResearchPublicationProvider`."""

    def __init__(
        self,
        storage: StorageBackend,
        queue: ProviderRequestQueue,
        http_client: httpx.AsyncClient,
        xml_backend: ParserBackend,
    ) -> None:
        self._storage = storage
        self._queue = queue
        self._http_client = http_client
        self._xml_backend = xml_backend

    async def _get_json(self, path: str, params: dict) -> dict:
        async def op() -> httpx.Response:
            return await provider_http.request(self._http_client, "GET", f"{EUROPEPMC_API_URL}/{path}", params=params)

        response = await self._queue.execute(op)
        return response.json()

    async def search(  # ty: ignore[invalid-method-override]
        self, query: str, page_size: int = 25, cursor_mark: str = "*"
    ) -> dict:
        """See docs/requirement-specification/06-interface-specification.md#research_europepmc_search."""
        payload = await self._get_json(
            "search",
            {"query": query, "format": "json", "resultType": "core", "pageSize": page_size, "cursorMark": cursor_mark},
        )
        results = [_parse_record(e) for e in payload.get("resultList", {}).get("result", [])]
        response: dict = {"results": results, "hit_count": payload.get("hitCount", 0)}
        if "nextCursorMark" in payload:
            response["next_cursor_mark"] = payload["nextCursorMark"]
        return response

    async def fetch_metadata(self, identifiers: list[str]) -> dict:
        """See docs/requirement-specification/06-interface-specification.md#research_europepmc_fetch_metadata."""
        clauses = []
        for identifier in identifiers:
            source, raw_id = _split_identifier(identifier)
            clauses.append(f"(EXT_ID:{raw_id} AND SRC:{source})")
        query = " OR ".join(clauses)
        payload = await self._get_json("search", {"query": query, "format": "json", "resultType": "core"})
        results = [_parse_record(e) for e in payload.get("resultList", {}).get("result", [])]
        not_found = [i for i in identifiers if not _matches(i, results)]
        return {"results": results, "not_found": not_found}

    async def resolve_identifier(self, identifier: str, format: str) -> dict:
        """See docs/requirement-specification/01-architecture.md#resolve_identifier.

        Always resolves via `fetch_metadata`, even for a bare PMCID: Europe PMC's PMCID alone
        doesn't say whether Europe PMC hosts full text for it (`full_text_available`), which
        `fetch_full_text` needs. Returning it here (alongside the `identifier`/`resolved_url`/
        `format` the interface spec requires) lets `fetch_full_text` reuse this one metadata
        call instead of issuing a second, redundant one purely to re-check availability.
        """
        metadata = await self.fetch_metadata([identifier])
        if not metadata["results"]:
            raise NotFoundError(f"Europe PMC identifier not recognised: {identifier}")
        record = metadata["results"][0]
        if not record["pmcid"]:
            raise FormatUnavailableError(f"Europe PMC record {identifier} has no PMCID")
        pmcid = record["pmcid"]
        url = f"{EUROPEPMC_API_URL}/{pmcid}/fullTextXML"
        return {
            "identifier": f"PMC:{pmcid}",
            "resolved_url": url,
            "format": "xml",
            "full_text_available": record["full_text_available"],
        }

    async def fetch_full_text(self, identifier: str, format: str = "xml") -> dict:
        """See docs/requirement-specification/06-interface-specification.md#research_europepmc_fetch_full_text.

        `fullTextUrlList` entries can point off-domain (publisher sites, NCBI Bookshelf), which
        this provider never follows - only the same-domain `fullTextXML` endpoint, and only when
        `full_text_available` is true - see
        docs/requirement-specification/05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests.
        """
        resolved = await self.resolve_identifier(identifier, "xml")
        if not resolved["full_text_available"]:
            raise FormatUnavailableError(f"Europe PMC does not host full text for {identifier}")
        canonical_id = resolved["identifier"]
        url = resolved["resolved_url"]

        async def factory() -> bytes:
            async def op() -> httpx.Response:
                return await provider_http.request(self._http_client, "GET", url)

            response = await self._queue.execute(op)
            return response.content

        content, served_from_storage = await self._storage.get_or_create(
            "europepmc", canonical_id, "xml", factory, original_identifier=identifier
        )
        return {
            "location": f"europepmc:{canonical_id}:xml",
            "format": "xml",
            "size_bytes": len(content),
            "served_from_storage": served_from_storage,
            "resource_uri": f"research://europepmc/{canonical_id}/xml/fulltext",
        }

    async def parse_full_text(self, identifier: str, format: str = "xml") -> dict:
        raise NotImplementedError  # implemented in Task 4
