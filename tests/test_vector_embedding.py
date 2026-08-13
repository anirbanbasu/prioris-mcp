import asyncio

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
        import pytest

        with pytest.raises(ValueError, match="not.*support|not.*recognis|unknown"):
            FastEmbedBackend("not-a-real-model/does-not-exist")
