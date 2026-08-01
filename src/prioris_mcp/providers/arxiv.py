"""arXiv research-publication provider.

See docs/requirement-specification/01-architecture.md and
docs/requirement-specification/06-interface-specification.md#arxiv.
"""

import logging
from urllib.parse import quote

import httpx
from defusedxml import ElementTree as safe_ET

from prioris_mcp.errors import FormatUnavailableError, InvalidRequestError, NotFoundError
from prioris_mcp.models.arxiv import (
    ArxivCategoriesResult,
    ArxivFetchMetadataResult,
    ArxivResolvedIdentifier,
    ArxivSearchResult,
)
from prioris_mcp.models.common import FullTextFetchResult, ParsedFullText
from prioris_mcp.pagination import paginate_text
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.providers import http as provider_http
from prioris_mcp.providers.base import ResearchPublicationProvider
from prioris_mcp.rate_limit import ProviderRequestQueue
from prioris_mcp.storage import StorageBackend

logger = logging.getLogger(__name__)

# https, not http: export.arxiv.org 301-redirects every plain-http request to https, so calling
# https:// directly avoids paying for that redirect round-trip on every arXiv API call (see
# docs/requirement-specification/04-non-functional-requirements.md#provider_unavailable-failures-are-not-retried).
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_BASE_SPACING_SECONDS = 3.0
ARXIV_MAX_RESULTS = 2000
ARXIV_MAX_CUMULATIVE = 30000
# Same avoid-the-redirect rationale as ARXIV_API_URL above: export.arxiv.org/oai2 301-redirects
# here.
ARXIV_OAI_URL = "https://oaipmh.arxiv.org/oai"

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
_NS = {"atom": _ATOM_NS, "arxiv": _ARXIV_NS, "opensearch": _OPENSEARCH_NS}
_OAI_NS = "http://www.openarchives.org/OAI/2.0/"
_OAI_NSMAP = {"oai": _OAI_NS}


def _bare_id(identifier: str) -> str:
    """Strip a trailing arXiv version suffix (e.g. 'v2') from `identifier`, if present."""
    prefix, sep, suffix = identifier.rpartition("v")
    if sep and suffix.isdigit():
        return prefix
    return identifier


def _is_version_pinned(identifier: str) -> bool:
    """Whether `identifier` already names one immutable, version-pinned arXiv record."""
    return _bare_id(identifier) != identifier


def _text(element, path: str, ns: dict[str, str] = _NS) -> str | None:
    found = element.find(path, ns)
    return found.text.strip() if found is not None and found.text else None


def _parse_entry(entry) -> dict:
    entry_id = _text(entry, "atom:id") or ""
    arxiv_id = entry_id.rsplit("/abs/", 1)[-1] if "/abs/" in entry_id else entry_id
    authors = []
    for author in entry.findall("atom:author", _NS):
        authors.append({"name": _text(author, "atom:name") or "", "affiliation": _text(author, "arxiv:affiliation")})
    categories = [c.get("term") for c in entry.findall("atom:category", _NS) if c.get("term")]
    primary_category_el = entry.find("arxiv:primary_category", _NS)
    primary_category = primary_category_el.get("term") if primary_category_el is not None else None
    pdf_url = None
    for link in entry.findall("atom:link", _NS):
        if link.get("rel") == "related" and link.get("type") == "application/pdf":
            pdf_url = link.get("href")
            break
    return {
        "arxiv_id": arxiv_id,
        "title": _text(entry, "atom:title"),
        "authors": authors,
        "abstract": _text(entry, "atom:summary"),
        "categories": categories,
        "primary_category": primary_category,
        "published": _text(entry, "atom:published"),
        "updated": _text(entry, "atom:updated"),
        "pdf_url": pdf_url,
        "doi": _text(entry, "arxiv:doi"),
        "journal_ref": _text(entry, "arxiv:journal_ref"),
        "comment": _text(entry, "arxiv:comment"),
    }


def _parse_feed(xml_bytes: bytes) -> tuple[list[dict], int]:
    root = safe_ET.fromstring(xml_bytes)
    entries = [_parse_entry(e) for e in root.findall("atom:entry", _NS)]
    total_el = root.find("opensearch:totalResults", _NS)
    total_results = int(total_el.text) if total_el is not None and total_el.text else len(entries)
    return entries, total_results


def _parse_list_sets(xml_bytes: bytes) -> list[dict]:
    """Parse an OAI-PMH ListSets response into arXiv's queryable leaf categories.

    A `setSpec` (e.g. "physics:astro-ph:CO") is only a real, queryable `cat:` value if no other
    `setSpec` extends it with one more ":segment" - non-leaf entries like "physics" or
    "physics:physics" are grouping nodes that return 0 hits for `cat:{...}` (verified live
    against export.arxiv.org/api/query while drafting
    docs/superpowers/specs/2026-07-28-arxiv-list-top-n-multi-category-design.md). A leaf's
    actual category code drops the outermost archive segment and joins what remains with ".":
    "physics:astro-ph:CO" -> "astro-ph.CO", "physics:hep-th" -> "hep-th".
    """
    root = safe_ET.fromstring(xml_bytes)
    set_els = root.findall(".//oai:ListSets/oai:set", _OAI_NSMAP)
    entries = [(_text(el, "oai:setSpec", _OAI_NSMAP), _text(el, "oai:setName", _OAI_NSMAP)) for el in set_els]
    specs = {spec for spec, _ in entries if spec}
    leaves = [(spec, name) for spec, name in entries if spec and not any(s.startswith(f"{spec}:") for s in specs)]
    categories: list[dict[str, str]] = []
    for spec, name in leaves:
        parts = spec.split(":")
        code = ".".join(parts[1:]) if len(parts) > 1 else spec
        categories.append({"code": code, "name": name or ""})
    categories.sort(key=lambda c: c["code"])
    return categories


class ArxivProvider(ResearchPublicationProvider):
    """arXiv implementation of `ResearchPublicationProvider`."""

    def __init__(
        self,
        storage: StorageBackend,
        queue: ProviderRequestQueue,
        http_client: httpx.AsyncClient,
        pdf_backend: ParserBackend,
        html_backend: ParserBackend,
        default_inline_char_limit: int = 20000,
    ) -> None:
        self._storage = storage
        self._queue = queue
        self._http_client = http_client
        self._pdf_backend = pdf_backend
        self._html_backend = html_backend
        self._default_inline_char_limit = default_inline_char_limit

    async def _get(self, params: dict) -> bytes:
        async def op() -> httpx.Response:
            return await provider_http.request(self._http_client, "GET", ARXIV_API_URL, params=params)

        response = await self._queue.execute(op)
        return response.content

    async def _get_oai_sets(self) -> bytes:
        async def op() -> httpx.Response:
            return await provider_http.request(self._http_client, "GET", ARXIV_OAI_URL, params={"verb": "ListSets"})

        response = await self._queue.execute(op)
        return response.content

    # invalid-method-override: base.py's search(**kwargs: Any) is intentionally generic so each
    # provider can define its own concrete parameter shape (see providers/base.py's docstring).
    async def search(  # ty: ignore[invalid-method-override]
        self,
        query: str,
        max_results: int = 10,
        start: int = 0,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> ArxivSearchResult:
        """See docs/requirement-specification/06-interface-specification.md#research_arxiv_search."""
        if max_results > ARXIV_MAX_RESULTS:
            raise InvalidRequestError(f"max_results must be <= {ARXIV_MAX_RESULTS}, got {max_results}")
        if start + max_results > ARXIV_MAX_CUMULATIVE:
            raise InvalidRequestError(f"start + max_results must be <= {ARXIV_MAX_CUMULATIVE}")
        params = {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        entries, total_results = _parse_feed(await self._get(params))
        return ArxivSearchResult(results=entries, total_results=total_results)

    async def list_top_n(
        self, include_categories: list[str], n: int, exclude_categories: list[str] | None = None
    ) -> ArxivSearchResult:
        """See docs/requirement-specification/06-interface-specification.md#research_arxiv_list_top_n."""
        if not include_categories:
            raise InvalidRequestError("include_categories must contain at least one category")
        included = list(dict.fromkeys(include_categories))  # preserve first-seen order
        excluded = list(dict.fromkeys(exclude_categories or []))
        query_parts = [" AND ".join(f"cat:{c}" for c in included)]
        query_parts += [f"ANDNOT cat:{c}" for c in excluded]
        params = {
            "search_query": " ".join(query_parts),
            "start": 0,
            "max_results": n,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        entries, _ = _parse_feed(await self._get(params))
        return ArxivSearchResult(results=entries, total_results=len(entries))

    async def list_categories(self) -> ArxivCategoriesResult:
        """See docs/requirement-specification/06-interface-specification.md#arxiv-category-list-resource."""
        return ArxivCategoriesResult(categories=_parse_list_sets(await self._get_oai_sets()))

    async def fetch_metadata(self, identifiers: list[str]) -> ArxivFetchMetadataResult:
        """See docs/requirement-specification/06-interface-specification.md#research_arxiv_fetch_metadata."""
        params = {"id_list": ",".join(identifiers)}
        entries, _ = _parse_feed(await self._get(params))
        found_bare_ids = {_bare_id(e["arxiv_id"]) for e in entries}
        not_found = [i for i in identifiers if _bare_id(i) not in found_bare_ids]
        return ArxivFetchMetadataResult(results=entries, not_found=not_found)

    async def resolve_identifier(self, identifier: str, format: str) -> ArxivResolvedIdentifier:
        """See docs/requirement-specification/01-architecture.md#resolve_identifier.

        A version-pinned identifier is already immutable (see
        docs/requirement-specification/02-storage.md#identifier-canonicalisation) and needs no
        network round-trip; only an unversioned identifier triggers a `fetch_metadata` call to
        find the current version.
        """
        if _is_version_pinned(identifier):
            canonical_id = identifier
        else:
            metadata = await self.fetch_metadata([identifier])
            if not metadata.results:
                raise NotFoundError(f"arXiv identifier not recognised: {identifier}")
            canonical_id = metadata.results[0].arxiv_id

        if format == "pdf":
            url = f"https://arxiv.org/pdf/{canonical_id}"
        elif format == "html":
            url = f"https://arxiv.org/html/{canonical_id}"
        else:
            raise ValueError(f"Unsupported format for arXiv: {format}")

        return ArxivResolvedIdentifier(identifier=canonical_id, resolved_url=url, format=format)

    async def fetch_full_text(self, identifier: str, format: str) -> FullTextFetchResult:
        """See docs/requirement-specification/06-interface-specification.md#research_arxiv_fetch_full_text."""
        resolved = await self.resolve_identifier(identifier, format)
        canonical_id = resolved.identifier
        url = resolved.resolved_url

        async def factory() -> bytes:
            async def op() -> httpx.Response:
                return await provider_http.request(self._http_client, "GET", url)

            response = await self._queue.execute(op)
            if response.status_code == 404:
                if format == "pdf":
                    # A PDF is always available for any submission that exists (see
                    # docs/requirement-specification/06-interface-specification.md#research_arxiv_fetch_full_text),
                    # so a 404 here can only mean the identifier itself doesn't exist - unlike
                    # html below, there is no legitimate "exists but unavailable in this format"
                    # case to distinguish it from.
                    raise NotFoundError(f"arXiv identifier not recognised: {canonical_id}")
                # html: resolve_identifier skips the fetch_metadata existence check for an
                # already-version-pinned identifier (see
                # docs/requirement-specification/02-storage.md#identifier-canonicalisation), so a
                # 404 here is ambiguous on its own for one - it could mean "no html rendering for
                # a real item" or "this identifier doesn't exist at all". Disambiguate now, but
                # only in that case: an unversioned identifier was already existence-checked by
                # resolve_identifier's own fetch_metadata call, so paying for a second one here
                # would be pure waste on the common path.
                if _is_version_pinned(identifier):
                    metadata = await self.fetch_metadata([canonical_id])
                    if not metadata.results:
                        raise NotFoundError(f"arXiv identifier not recognised: {canonical_id}")
                raise FormatUnavailableError(f"No HTML rendering available for arXiv {canonical_id}")
            return response.content

        content, served_from_storage = await self._storage.get_or_create(
            "arxiv", canonical_id, format, factory, original_identifier=identifier
        )
        return FullTextFetchResult(
            location=f"arxiv:{canonical_id}:{format}",
            format=format,
            size_bytes=len(content),
            served_from_storage=served_from_storage,
            # canonical_id is percent-encoded (safe="" so even old-style slash-bearing ids like
            # "hep-th/9901001v1" collapse to one opaque path segment): the resource template in
            # server.py is `research://{provider}/{identifier}/{format}/fulltext`, and a bare "/"
            # in {identifier} would otherwise split across what FastMCP's matcher treats as two
            # path segments. FastMCP's match_uri_template() unquotes each captured segment before
            # calling the handler, so read_fulltext_resource still receives the identifier in its
            # original, storage-key-compatible form.
            resource_uri=f"research://arxiv/{quote(canonical_id, safe='')}/{format}/fulltext",
        )

    async def parse_full_text(
        self, identifier: str, format: str, offset: int = 0, limit: int | None = None
    ) -> ParsedFullText:
        """See docs/requirement-specification/06-interface-specification.md#research_arxiv_parse_full_text.

        Storage is always keyed on the canonical, version-pinned identifier - see
        docs/requirement-specification/02-storage.md#identifier-canonicalisation - so this
        resolves `identifier` first, mirroring `fetch_full_text`'s pattern, before touching
        storage. A version-pinned identifier short-circuits inside `resolve_identifier` with no
        network call; an unversioned one costs a `fetch_metadata` lookup only, never a
        `fetch_full_text` call.

        Uses `StorageBackend.get_or_create` (keyed on the derived markdown format) so that two
        concurrent parses of the same (identifier, format) never both invoke the parser backend -
        see docs/requirement-specification/04-non-functional-requirements.md#storage-must-de-duplicate-in-flight-work-not-just-completed-work.

        Returns one paginated page of the Markdown, not the whole string - see
        docs/requirement-specification/04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole.
        `limit` defaults to `default_inline_char_limit` when unset.
        """
        canonical_id = (await self.resolve_identifier(identifier, format)).identifier
        markdown_format = f"{format}-markdown"
        backend = self._pdf_backend if format == "pdf" else self._html_backend

        async def factory() -> bytes:
            if not await self._storage.exists("arxiv", canonical_id, format):
                raise NotFoundError(f"arXiv full text not fetched yet: identifier={canonical_id}, format={format}")
            source_content = await self._storage.read("arxiv", canonical_id, format)
            markdown = await backend.to_markdown(source_content)
            return markdown.encode("utf-8")

        markdown_bytes, _ = await self._storage.get_or_create("arxiv", canonical_id, markdown_format, factory)
        page = paginate_text(
            markdown_bytes.decode("utf-8"), offset, limit if limit is not None else self._default_inline_char_limit
        )
        return ParsedFullText(
            markdown=page["content"],
            offset=page["offset"],
            limit=page["limit"],
            total_length=page["total_length"],
            has_more=page["has_more"],
            # See the matching comment in fetch_full_text: canonical_id is percent-encoded so an
            # old-style slash-bearing identifier still resolves as a single {identifier} segment.
            resource_uri=f"research://arxiv/{quote(canonical_id, safe='')}/{format}/markdown",
        )
