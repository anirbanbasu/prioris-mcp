"""Output models shared across providers - the fetch/parse result shapes.

See docs/requirement-specification/06-interface-specification.md#conventions for
what these models encode.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FullTextFetchResult(BaseModel):
    """Output of `fetch_full_text`, identical across both v1 providers.

    See docs/requirement-specification/06-interface-specification.md#research_arxiv_fetch_full_text
    and #research_europepmc_fetch_full_text.
    """

    model_config = ConfigDict(extra="forbid")

    location: Annotated[str, Field(..., strict=True, description="The location of the fetched full text.")]
    format_: Annotated[str, Field(..., alias="format", strict=True, description="The format of the fetched full text.")]
    size_bytes: Annotated[int, Field(..., strict=True, description="The size of the fetched full text in bytes.")]
    served_from_storage: Annotated[
        bool, Field(..., strict=True, description="Whether the full text was served from storage.")
    ]
    resource_uri: Annotated[str, Field(..., strict=True, description="The URI of the resource.")]


class MarkdownPage(BaseModel):
    """One bounded page of Markdown - the shape `read_markdown_resource` returns.

    See docs/requirement-specification/04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole.
    """

    model_config = ConfigDict(extra="forbid")

    markdown: Annotated[str, Field(..., strict=True, description="The Markdown content of the page.")]
    offset: Annotated[int, Field(..., strict=True, description="The offset of the page in the full text.")]
    limit: Annotated[int, Field(..., strict=True, description="The limit of the page in the full text.")]
    total_length: Annotated[int, Field(..., strict=True, description="The total length of the full text.")]
    has_more: Annotated[bool, Field(..., strict=True, description="Whether there are more pages of Markdown to fetch.")]
    total_pages: Annotated[
        int | None, Field(default=None, description="Total PDF pages; null for non-page-aware formats")
    ] = None
    page_range: Annotated[
        tuple[int, int] | None,
        Field(
            default=None,
            description="[first_page, last_page] the returned slice spans; null for non-page-aware formats",
        ),
    ] = None


class ParsedFullText(MarkdownPage):
    """`MarkdownPage` plus `resource_uri` - the shape `parse_full_text` tools return.

    See docs/requirement-specification/06-interface-specification.md#research_arxiv_parse_full_text
    and #research_europepmc_parse_full_text.
    """

    resource_uri: Annotated[
        str, Field(..., strict=True, description="The URI of the resource from which the full text was parsed.")
    ]


class ArxivResolvedIdentifierResult(BaseModel):
    """Output of `research_resolve_identifier` when `identifier` routes to arXiv.

    See docs/requirement-specification/01-architecture.md#identifier-routing-grouping-level. Kept
    separate from `EuropePmcResolvedIdentifierResult` (rather than one model with an optional
    `full_text_available`) because arXiv genuinely has no equivalent concept - see
    `providers.identifier_routing.resolve_research_identifier`.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[Literal["arxiv"], Field(description="The owning provider.")] = "arxiv"
    identifier: Annotated[str, Field(..., strict=True, description="The resolved identifier.")]
    resolved_url: Annotated[str, Field(..., strict=True, description="The resolved URL.")]
    format_: Annotated[
        str, Field(..., alias="format", strict=True, description="The format of the resolved identifier.")
    ]


class EuropePmcResolvedIdentifierResult(BaseModel):
    """Output of `research_resolve_identifier` when `identifier` routes to Europe PMC.

    See docs/requirement-specification/01-architecture.md#identifier-routing-grouping-level and
    `ArxivResolvedIdentifierResult`'s docstring for why this isn't merged into one model.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[Literal["europepmc"], Field(description="The owning provider.")] = "europepmc"
    identifier: Annotated[str, Field(..., strict=True, description="The resolved identifier.")]
    resolved_url: Annotated[str, Field(..., strict=True, description="The resolved URL.")]
    format_: Annotated[
        str, Field(..., alias="format", strict=True, description="The format of the resolved identifier.")
    ]
    full_text_available: Annotated[
        bool, Field(..., strict=True, description="Whether Europe PMC hosts full text for the resolved identifier.")
    ]


ResolvedIdentifierResult = ArxivResolvedIdentifierResult | EuropePmcResolvedIdentifierResult


class StorageEntry(BaseModel):
    """One persisted (provider, identifier, format) manifest entry - the shape `research_list_fetched` returns.

    See docs/requirement-specification/06-interface-specification.md#research_list_fetched.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[str, Field(..., strict=True, description="The provider that persisted this entry.")]
    identifier: Annotated[str, Field(..., strict=True, description="The externally-visible identifier.")]
    format_: Annotated[str, Field(..., alias="format", strict=True, description="The format of the persisted entry.")]
    artefact: Annotated[Literal["document", "markdown"], Field(..., description="The type of artefact.")]
    fetched_at_or_parsed_at: Annotated[str, Field(..., strict=True, description="When this entry was fetched.")]
    size_bytes: Annotated[int, Field(..., strict=True, description="The size of the persisted entry in bytes.")]


class ListFetchedResult(BaseModel):
    """Output of `research_list_fetched`."""

    model_config = ConfigDict(extra="forbid")

    entries: Annotated[list[StorageEntry], Field(..., strict=True, description="The matching persisted entries.")]


class DeleteEntryRef(BaseModel):
    """One caller-supplied (provider, identifier, format) entry reference for `research_delete_fetched`."""

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[str, Field(..., strict=True, description="The provider the entry was persisted under.")]
    identifier: Annotated[str, Field(..., strict=True, description="The externally-visible identifier.")]
    format_: Annotated[str, Field(..., alias="format", strict=True, description="The format of the entry.")]
    artefact: Annotated[Literal["document", "markdown", "all"], Field(..., description="The type of artefact.")]


class DeleteFetchedResult(BaseModel):
    """Output of `research_delete_fetched`."""

    model_config = ConfigDict(extra="forbid")

    deleted: Annotated[list[DeleteEntryRef], Field(..., strict=True, description="Entries that were removed.")]
    not_found: Annotated[
        list[DeleteEntryRef], Field(..., strict=True, description="Requested entries that were already absent.")
    ]


class SearchMatch(BaseModel):
    """One match result from a search across the local index."""

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[str, Field(..., strict=True)]
    identifier: Annotated[str, Field(..., strict=True)]
    format_: Annotated[str, Field(..., alias="format", strict=True)]
    snippet: Annotated[str, Field(..., strict=True)]
    offset: Annotated[int, Field(..., strict=True)]
    score: Annotated[float, Field(..., strict=True)]


class SearchFetchedResult(BaseModel):
    """Output of `research_search_fetched`."""

    model_config = ConfigDict(extra="forbid")

    matches: Annotated[list[SearchMatch], Field(..., strict=True)]
