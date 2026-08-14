import asyncio
from unittest.mock import MagicMock, patch

import pytest

from prioris_mcp.vector.embedding import FastEmbedBackend


class TestFastEmbedBackend:
    """Test suite for FastEmbedBackend implementation."""

    def test_model_name_is_recorded(self):
        backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        assert backend.model_name == "BAAI/bge-small-en-v1.5"

    def test_dimension_matches_bge_small(self):
        backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        assert backend.dimension == 384

    def test_embed_returns_a_vector_of_the_declared_dimension(self):
        backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        vector = asyncio.run(backend.embed("a transformer architecture for sequence modeling"))
        assert len(vector) == 384
        assert all(isinstance(component, float) for component in vector)

    def test_unrecognised_model_name_raises_at_construction(self):
        with pytest.raises(ValueError, match="not.*support|not.*recognis|unknown"):
            FastEmbedBackend("not-a-real-model/does-not-exist")


class TestLazyInitialization:
    """Test that TextEmbedding is lazily constructed, not eagerly at __init__."""

    @staticmethod
    def _mock_list_supported_models():
        """Mock list_supported_models to return test model."""
        return [{"model": "BAAI/bge-small-en-v1.5", "dim": 384}]

    def test_text_embedding_not_constructed_at_init(self):
        """Verify TextEmbedding is NOT instantiated when FastEmbedBackend is created."""
        with patch("prioris_mcp.vector.embedding.TextEmbedding") as mock_text_embedding:
            mock_text_embedding.list_supported_models = self._mock_list_supported_models
            FastEmbedBackend("BAAI/bge-small-en-v1.5")
            # TextEmbedding should not have been called as a constructor (only list_supported_models)
            mock_text_embedding.assert_not_called()

    def test_model_name_readable_before_embed(self):
        """Verify model_name property is readable before any embed() call."""
        with patch("prioris_mcp.vector.embedding.TextEmbedding") as mock_text_embedding:
            mock_text_embedding.list_supported_models = self._mock_list_supported_models
            backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")
            assert backend.model_name == "BAAI/bge-small-en-v1.5"

    def test_dimension_readable_before_embed(self):
        """Verify dimension property is readable before any embed() call."""
        with patch("prioris_mcp.vector.embedding.TextEmbedding") as mock_text_embedding:
            mock_text_embedding.list_supported_models = self._mock_list_supported_models
            backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")
            assert backend.dimension == 384

    def test_text_embedding_constructed_on_first_embed(self):
        """Verify TextEmbedding is constructed exactly once on first embed() call."""
        with patch("prioris_mcp.vector.embedding.TextEmbedding") as mock_text_embedding:
            mock_text_embedding.list_supported_models = self._mock_list_supported_models
            mock_instance = MagicMock()
            # Return a mock array-like object with tolist() method
            mock_array = MagicMock()
            mock_array.tolist.return_value = [0.1, 0.2, 0.3] * 128
            mock_instance.embed.return_value = [mock_array]
            mock_text_embedding.return_value = mock_instance

            backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")

            # TextEmbedding constructor should not have been called yet
            mock_text_embedding.assert_not_called()

            # Call embed() - TextEmbedding constructor should now be called
            asyncio.run(backend.embed("test text"))

            # TextEmbedding constructor should have been called exactly once
            mock_text_embedding.assert_called_once_with(model_name="BAAI/bge-small-en-v1.5")
            mock_instance.embed.assert_called_once()

    def test_text_embedding_not_reconstructed_on_second_embed(self):
        """Verify TextEmbedding is NOT reconstructed on second embed() call (cached)."""
        with patch("prioris_mcp.vector.embedding.TextEmbedding") as mock_text_embedding:
            mock_text_embedding.list_supported_models = self._mock_list_supported_models
            mock_instance = MagicMock()
            # Return a mock array-like object with tolist() method
            mock_array = MagicMock()
            mock_array.tolist.return_value = [0.1, 0.2, 0.3] * 128
            mock_instance.embed.return_value = [mock_array]
            mock_text_embedding.return_value = mock_instance

            backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")

            # First embed() call
            asyncio.run(backend.embed("first text"))
            mock_text_embedding.assert_called_once()

            # Reset the mock to track subsequent calls
            mock_text_embedding.reset_mock()

            # Second embed() call - TextEmbedding constructor should NOT be called again
            asyncio.run(backend.embed("second text"))

            mock_text_embedding.assert_not_called()
