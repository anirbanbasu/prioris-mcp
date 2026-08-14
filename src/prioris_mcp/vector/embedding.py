"""Pluggable text-to-vector interface, injected into VectorSearchBackend implementations.

See docs/requirement-specification/search/02-vector-search.md#embeddingbackend-is-a-pluggable-interface-injected-into-vectorsearchbackend.
v3 ships one implementation: fastembed (ADR-00023).
"""

import logging
import re
from abc import ABC, abstractmethod

import anyio
from anyio import to_thread
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

# Rough English heuristic for converting a model's token budget into a character budget - not
# exact tokenization, just a common rule of thumb.
_APPROX_CHARS_PER_TOKEN = 4

# Applied to a model's token-truncation limit before converting to a character budget, so chunks
# stay comfortably under the model's actual truncation point even though the chars-per-token ratio
# above is approximate, not exact.
_CHUNK_SAFETY_MARGIN = 0.8

# Matches fastembed's free-text model `description` field, e.g. "512 input tokens truncation" or
# "1024 tokens truncation" - fastembed has no structured token-limit field, only this description
# string, and different models phrase it slightly differently.
_TOKEN_COUNT_PATTERN = re.compile(r"\b(\d+)\s*(?:input\s+)?tokens?\b", re.IGNORECASE)


class EmbeddingBackend(ABC):
    """Turns text into a fixed-dimension embedding vector. Async regardless of implementation."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The configured model name - recorded per vector row for index_status comparison."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """The fixed output dimension this model produces - fixes one vec0 table's column width."""

    @property
    @abstractmethod
    def max_chunk_chars(self) -> int:
        """A per-model default character budget for one indexed chunk/window.

        Derived from the model's token-truncation limit (see `_resolve_max_chunk_chars`) - the
        default a `VectorSearchBackend` implementation uses for `max_chunk_chars` unless a caller
        overrides it explicitly.
        """

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed `text` into one vector of length `self.dimension`."""


def _resolve_dimension(model_name: str) -> int:
    for supported in TextEmbedding.list_supported_models():
        if supported["model"] == model_name:
            return supported["dim"]
    raise ValueError(f"{model_name!r} is not a fastembed-supported model name")


def _resolve_max_chunk_chars(model_name: str) -> int:
    """Derive a per-model chunk-char budget from fastembed's free-text model `description`.

    fastembed's model registry has no structured token-limit field, only a free-text
    `description` (e.g. "... 512 input tokens truncation ..."). Unlike `_resolve_dimension`, an
    unrecognised model name or an unparseable description never raises here - chunk size is a
    soft heuristic, not a structural requirement (dimension is; a bad model name is already
    caught by `_resolve_dimension`'s own raise) - so this logs a warning and falls back to
    `sqlite_vec_backend._DEFAULT_MAX_CHUNK_CHARS` instead.
    """
    # Deferred import: sqlite_vec_backend imports EmbeddingBackend from this module at module
    # load time, so importing it back at module scope here would be circular.
    from prioris_mcp.vector.sqlite_vec_backend import _DEFAULT_MAX_CHUNK_CHARS

    description = None
    for supported in TextEmbedding.list_supported_models():
        if supported["model"] == model_name:
            description = supported.get("description")
            break
    match = _TOKEN_COUNT_PATTERN.search(description) if description else None
    if match is None:
        logger.warning(
            "Could not determine a token-truncation limit for embedding model %r "
            "(not found in fastembed's registry, or its description has no parseable token "
            "count); falling back to a %d-character chunk limit.",
            model_name,
            _DEFAULT_MAX_CHUNK_CHARS,
        )
        return _DEFAULT_MAX_CHUNK_CHARS
    token_limit = int(match.group(1))
    return int(token_limit * _APPROX_CHARS_PER_TOKEN * _CHUNK_SAFETY_MARGIN)


class FastEmbedBackend(EmbeddingBackend):
    """`fastembed`-backed EmbeddingBackend - local ONNX inference, no PyTorch (ADR-00023)."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._dimension = _resolve_dimension(model_name)
        self._max_chunk_chars = _resolve_max_chunk_chars(model_name)
        self._model: TextEmbedding | None = None
        self._model_lock = anyio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_chunk_chars(self) -> int:
        return self._max_chunk_chars

    async def embed(self, text: str) -> list[float]:
        # Double-checked locking: the lock is only ever contended on the (rare) first call that
        # races another concurrent first call - every later call sees self._model already set and
        # skips the lock entirely.
        if self._model is None:
            async with self._model_lock:
                if self._model is None:
                    self._model = await to_thread.run_sync(lambda: TextEmbedding(model_name=self._model_name))
        model = self._model
        assert model is not None  # narrows TextEmbedding | None for the type checker; always true here

        def _embed_sync() -> list[float]:
            (embedding,) = model.embed([text])
            return embedding.tolist()

        return await to_thread.run_sync(_embed_sync)
