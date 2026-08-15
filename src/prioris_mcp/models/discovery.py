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
    score: Annotated[float, Field(..., strict=True, description="OpenAlex embedding-similarity score.")]
    fetch_route: Annotated[DiscoveryFetchRoute, Field(...)]


class DiscoveryResult(BaseModel):
    """Output of ``research_discovery``."""

    model_config = ConfigDict(extra="forbid")

    hits: Annotated[list[DiscoveryHit], Field(..., strict=True)]
