"""Pydantic models for OpenAlex-backed discovery and fetch routes.

See docs/requirement-specification/02-discovery.md.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryAuthor(BaseModel):
    """One author on an OpenAlex work."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(..., strict=True)]


class DiscoveryFetchRoute(BaseModel):
    """The fetch-ladder outcome for one discovery hit."""

    model_config = ConfigDict(extra="forbid")

    kind: Annotated[
        Literal["known_provider", "oa_link", "manual_upload"],
        Field(..., description="Which fetch-ladder rung resolved this hit."),
    ]
    provider: Annotated[
        Literal["arxiv", "europepmc"] | None,
        Field(default=None, description="Set only when kind == known_provider."),
    ] = None
    identifier: Annotated[
        str | None,
        Field(default=None, strict=True, description="Provider-native identifier; set only for known_provider."),
    ] = None
    pdf_url: Annotated[
        str | None,
        Field(default=None, strict=True, description="An OA PDF URL; set only when kind == oa_link."),
    ] = None


class DiscoveryHit(BaseModel):
    """One OpenAlex semantic-search result and its resolved fetch route."""

    model_config = ConfigDict(extra="forbid")

    openalex_id: Annotated[str, Field(..., strict=True, description="OpenAlex Work ID, e.g. W2741809807.")]
    title: Annotated[str | None, Field(default=None, strict=True)] = None
    abstract: Annotated[str | None, Field(default=None, strict=True)] = None
    authors: Annotated[list[DiscoveryAuthor], Field(default_factory=list, strict=True)]
    publication_year: Annotated[int | None, Field(default=None, strict=True)] = None
    doi: Annotated[str | None, Field(default=None, strict=True)] = None
    work_type: Annotated[
        str | None,
        Field(
            default=None,
            strict=True,
            description="OpenAlex's own work `type` code for this hit, e.g. `article` - see research://openalex/work-types.",
        ),
    ] = None
    score: Annotated[float, Field(..., strict=True, description="OpenAlex embedding-similarity score.")]
    fetch_route: Annotated[DiscoveryFetchRoute, Field(...)]


class DiscoveryResult(BaseModel):
    """Output of ``research_discovery``.

    `total`/`has_more` reflect OpenAlex's own counts for the underlying `search.semantic` query,
    not the post-local-exclusion `hits` list - a page can come back with fewer than `per_page`
    hits once already-fetched candidates are filtered out, even though more remain to page
    through. `total` itself is capped at 50: that's `search.semantic`'s own hard ceiling on
    matches per query, not a PriorisMCP-imposed limit.
    """

    model_config = ConfigDict(extra="forbid")

    hits: Annotated[list[DiscoveryHit], Field(..., strict=True)]
    page: Annotated[int, Field(..., strict=True, description="The 1-indexed page of results returned.")]
    per_page: Annotated[int, Field(..., strict=True, description="The number of results requested per page.")]
    total: Annotated[
        int, Field(..., strict=True, description="OpenAlex's own total-match count, capped at 50 by search.semantic.")
    ]
    has_more: Annotated[bool, Field(..., strict=True, description="Whether a further page exists.")]


class OpenAlexWorkType(BaseModel):
    """One OpenAlex work `type` value, from the `/work-types` endpoint."""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(..., strict=True, description="OpenAlex's type slug, e.g. 'article'.")]
    name: Annotated[str, Field(..., strict=True, description="The type's display name.")]
    description: Annotated[str, Field(..., strict=True, description="A one-line definition of the type.")]


class OpenAlexWorkTypesResult(BaseModel):
    """Output of the `research://openalex/work-types` resource."""

    model_config = ConfigDict(extra="forbid")

    types: Annotated[list[OpenAlexWorkType], Field(..., strict=True, description="Every OpenAlex work type.")]
