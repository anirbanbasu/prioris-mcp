"""Local filesystem source output models.

See docs/requirement-specification/06-interface-specification.md#local-filesystem for the
field-by-field source of each field below.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class LocalFileFetchResult(BaseModel):
    """Output of `research_localfile_fetch_full_text` and `research_localfile_finalize_upload`.

    Both call `LocalFileProvider._validate_and_persist`, which is what actually returns this
    shape - see `ArxivResolvedIdentifier`'s docstring in `models/arxiv.py` for the same
    provider-internal-shared-shape pattern.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(..., strict=True, description="The caller-facing identifier for the persisted file.")]
    location: Annotated[str, Field(..., strict=True, description="The storage location of the persisted file.")]
    format_: Annotated[str, Field(..., alias="format", strict=True, description="The format of the persisted file.")]
    size_bytes: Annotated[int, Field(..., strict=True, description="The size of the persisted file in bytes.")]
    served_from_storage: Annotated[
        bool, Field(..., strict=True, description="Whether the file was already persisted from a prior call.")
    ]
    resource_uri: Annotated[str, Field(..., strict=True, description="The URI of the resource.")]


class LocalFileUploadChunkResult(BaseModel):
    """Output of `research_localfile_upload_chunk`."""

    model_config = ConfigDict(extra="forbid")

    received_index: Annotated[int, Field(..., strict=True, description="The chunk index just received.")]
    bytes_so_far: Annotated[
        int, Field(..., strict=True, description="Cumulative bytes received so far for this session.")
    ]


class LocalFileBeginUploadResult(BaseModel):
    """Output of `research_localfile_begin_upload`.

    Assembled by `PriorisMCP._begin_upload` in `server.py`, not by `LocalFileProvider` itself -
    `UploadSessionManager.begin`/`LocalFileProvider.begin_upload` only mint and return the bare
    `session_id`; `max_chunk_bytes` is server configuration, not provider state.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: Annotated[str, Field(..., strict=True, description="The minted upload session id.")]
    max_chunk_bytes: Annotated[
        int, Field(..., strict=True, description="Maximum bytes accepted per chunk for this session.")
    ]
