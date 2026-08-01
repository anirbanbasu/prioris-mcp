"""Europe PMC output models.

See docs/requirement-specification/06-interface-specification.md#europe-pmc-metadata-record-shape
for the field-by-field source of each field below. `journal` is deliberately left as a pass-through
`dict[str, Any]` - the interface spec documents it only as "nested journal title/issue/publication-
date fields" with no exact contract, and pinning down Europe PMC's full `journalInfo` schema is not
part of issue #4.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class EuropePmcAuthor(BaseModel):
    """One `authorList.author` entry from a Europe PMC `resultType=core` record."""

    model_config = ConfigDict(extra="forbid")

    full_name: Annotated[str | None, Field(None, strict=True, description="The full name of the author.")] = None
    first_name: Annotated[str | None, Field(None, strict=True, description="The first name of the author.")] = None
    last_name: Annotated[str | None, Field(None, strict=True, description="The last name of the author.")] = None
    initials: Annotated[str | None, Field(None, strict=True, description="The initials of the author.")] = None


class EuropePmcMetadataRecord(BaseModel):
    """One Europe PMC record, the shared shape `search`/`fetch_metadata` both return."""

    model_config = ConfigDict(extra="forbid")

    identifier: Annotated[str, Field(..., strict=True, description="The identifier of the record.")]
    pmid: Annotated[str | None, Field(None, strict=True, description="The PubMed ID of the record.")] = None
    pmcid: Annotated[str | None, Field(None, strict=True, description="The PubMed Central ID of the record.")] = None
    doi: Annotated[str | None, Field(None, strict=True, description="The DOI of the record.")] = None
    title: Annotated[str | None, Field(None, strict=True, description="The title of the record.")] = None
    authors: Annotated[
        list[EuropePmcAuthor], Field(..., strict=True, description="The list of authors of the record.")
    ] = []
    abstract: Annotated[str | None, Field(None, strict=True, description="The abstract of the record.")] = None
    journal: Annotated[
        dict[str, Any] | None, Field(None, strict=True, description="The journal information of the record.")
    ] = None
    pub_year: Annotated[str | None, Field(None, strict=True, description="The publication year of the record.")] = None
    is_open_access: Annotated[
        str | None, Field(None, strict=True, description="Whether the record is open access.")
    ] = None
    license_: Annotated[
        str | None, Field(None, strict=True, alias="license", description="The license of the record.")
    ] = None
    full_text_available: Annotated[bool, Field(..., strict=True, description="Whether the full text is available.")]


class EuropePmcSearchResult(BaseModel):
    """Output of `research_europepmc_search`."""

    model_config = ConfigDict(extra="forbid")

    results: Annotated[
        list[EuropePmcMetadataRecord], Field(..., strict=True, description="The list of search results.")
    ]
    hit_count: Annotated[int, Field(..., strict=True, description="The total number of hits.")]
    next_cursor_mark: Annotated[
        str | None, Field(None, strict=True, description="The cursor mark for the next page of results.")
    ] = None


class EuropePmcFetchMetadataResult(BaseModel):
    """Output of `research_europepmc_fetch_metadata`."""

    model_config = ConfigDict(extra="forbid")

    results: Annotated[
        list[EuropePmcMetadataRecord], Field(..., strict=True, description="The list of fetched metadata records.")
    ]
    not_found: Annotated[list[str], Field(..., strict=True, description="The list of identifiers that were not found.")]


class EuropePmcResolvedIdentifier(BaseModel):
    """Internal: `EuropePmcProvider.resolve_identifier`'s own return shape.

    Consumed by `EuropePmcProvider.fetch_full_text`/`parse_full_text` and by
    `providers.identifier_routing.resolve_research_identifier` - not itself an MCP tool output.
    """

    model_config = ConfigDict(extra="forbid")

    identifier: Annotated[str, Field(..., strict=True, description="The resolved identifier.")]
    resolved_url: Annotated[str, Field(..., strict=True, description="The resolved URL.")]
    format_: Annotated[
        str, Field(..., strict=True, alias="format", description="The format of the resolved identifier.")
    ]
    full_text_available: Annotated[bool, Field(..., strict=True, description="Whether the full text is available.")]
