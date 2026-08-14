"""Pluggable text-to-vector interface, injected into VectorSearchBackend implementations.

See docs/requirement-specification/search/02-vector-search.md#embeddingbackend-is-a-pluggable-interface-injected-into-vectorsearchbackend.
v3 ships one implementation: fastembed (ADR-00023).
"""

from abc import ABC, abstractmethod

import anyio
from anyio import to_thread
from fastembed import TextEmbedding


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

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed `text` into one vector of length `self.dimension`."""


def _resolve_dimension(model_name: str) -> int:
    for supported in TextEmbedding.list_supported_models():
        if supported["model"] == model_name:
            return supported["dim"]
    raise ValueError(f"{model_name!r} is not a fastembed-supported model name")


class FastEmbedBackend(EmbeddingBackend):
    """`fastembed`-backed EmbeddingBackend - local ONNX inference, no PyTorch (ADR-00023)."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._dimension = _resolve_dimension(model_name)
        self._model: TextEmbedding | None = None
        self._model_lock = anyio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

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
