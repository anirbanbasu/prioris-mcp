"""arXiv output models.

See docs/requirement-specification/06-interface-specification.md#arxiv-metadata-record-shape
for the field-by-field source of each field below.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ArxivAuthor(BaseModel):
    """One `<author>` entry from an arXiv Atom feed."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(..., strict=True, description="The name of the author.")]
    affiliation: Annotated[str | None, Field(None, strict=True, description="The affiliation of the author.")] = None


class ArxivMetadataRecord(BaseModel):
    """One arXiv `<entry>`, the shared shape `search`/`list_top_n`/`fetch_metadata` all return."""

    model_config = ConfigDict(extra="forbid")

    arxiv_id: Annotated[str, Field(..., strict=True, description="The arXiv ID of the record.")]
    title: Annotated[str | None, Field(None, strict=True, description="The title of the record.")] = None
    authors: Annotated[list[ArxivAuthor], Field(..., strict=True, description="The authors of the record.")] = []
    abstract: Annotated[str | None, Field(None, strict=True, description="The abstract of the record.")] = None
    categories: Annotated[list[str], Field(..., strict=True, description="The categories of the record.")] = []
    primary_category: Annotated[
        str | None, Field(None, strict=True, description="The primary category of the record.")
    ] = None
    published: Annotated[str | None, Field(None, strict=True, description="The publication date of the record.")] = None
    updated: Annotated[str | None, Field(None, strict=True, description="The last updated date of the record.")] = None
    pdf_url: Annotated[
        str | None, Field(None, strict=True, description="The URL of the PDF version of the record.")
    ] = None
    doi: Annotated[str | None, Field(None, strict=True, description="The DOI of the record.")] = None
    journal_ref: Annotated[str | None, Field(None, strict=True, description="The journal reference of the record.")] = (
        None
    )
    comment: Annotated[str | None, Field(None, strict=True, description="A comment about the record.")] = None


class ArxivSearchResult(BaseModel):
    """Output of `research_arxiv_search`."""

    model_config = ConfigDict(extra="forbid")

    results: Annotated[list[ArxivMetadataRecord], Field(..., strict=True, description="The search results.")]
    total_results: Annotated[int, Field(..., strict=True, description="The total number of search results.")]


class ArxivListTopNResult(BaseModel):
    """Output of `research_arxiv_list_top_n`."""

    model_config = ConfigDict(extra="forbid")

    results: Annotated[list[ArxivMetadataRecord], Field(..., strict=True, description="The list of top N records.")]


class ArxivFetchMetadataResult(BaseModel):
    """Output of `research_arxiv_fetch_metadata`."""

    model_config = ConfigDict(extra="forbid")

    results: Annotated[list[ArxivMetadataRecord], Field(..., strict=True, description="The fetched metadata records.")]
    not_found: Annotated[list[str], Field(..., strict=True, description="The identifiers that were not found.")]


class ArxivCategory(BaseModel):
    """One leaf arXiv subject category, from the OAI-PMH `ListSets` response."""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(..., strict=True, description="The category code.")]
    name: Annotated[str, Field(..., strict=True, description="The category name.")]


class ArxivCategoriesResult(BaseModel):
    """Output of the `research://arxiv/categories` resource."""

    model_config = ConfigDict(extra="forbid")

    categories: Annotated[list[ArxivCategory], Field(..., strict=True, description="The list of arXiv categories.")]


class ArxivResolvedIdentifier(BaseModel):
    """Internal: `ArxivProvider.resolve_identifier`'s own return shape.

    Consumed by `ArxivProvider.fetch_full_text`/`parse_full_text` and by
    `providers.identifier_routing.resolve_research_identifier` - not itself an MCP tool output.
    """

    model_config = ConfigDict(extra="forbid")

    identifier: Annotated[str, Field(..., strict=True, description="The resolved identifier.")]
    resolved_url: Annotated[str, Field(..., strict=True, description="The resolved URL.")]
    format_: Annotated[
        str, Field(..., strict=True, alias="format", description="The format of the resolved identifier.")
    ]
