import asyncio
import logging
from unittest.mock import MagicMock, patch

import anyio
import pytest

from prioris_mcp.vector.embedding import FastEmbedBackend, _resolve_max_chunk_chars
from prioris_mcp.vector.sqlite_vec_backend import _DEFAULT_MAX_CHUNK_CHARS, SqliteVecDocumentBackend


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

    def test_concurrent_first_calls_construct_text_embedding_exactly_once(self):
        """D9: several embed() calls racing before any has constructed the model must not double-construct it.

        Without the anyio.Lock double-checked-locking guard, two concurrent first calls can both
        observe `self._model is None` and both construct a TextEmbedding - wasteful, and
        non-deterministic about which instance survives.
        """
        with patch("prioris_mcp.vector.embedding.TextEmbedding") as mock_text_embedding:
            mock_text_embedding.list_supported_models = self._mock_list_supported_models
            mock_instance = MagicMock()
            mock_array = MagicMock()
            mock_array.tolist.return_value = [0.1, 0.2, 0.3] * 128
            mock_instance.embed.return_value = [mock_array]
            mock_text_embedding.return_value = mock_instance

            backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")

            async def scenario():
                async with anyio.create_task_group() as tg:
                    for _ in range(10):
                        tg.start_soon(backend.embed, "concurrent call")

            asyncio.run(scenario())

            mock_text_embedding.assert_called_once_with(model_name="BAAI/bge-small-en-v1.5")


class TestResolveMaxChunkChars:
    """Test suite for _resolve_max_chunk_chars (Minor 2: per-model chunk-char default)."""

    def test_known_model_returns_a_value_under_the_unsafeguarded_token_budget(self):
        # BAAI/bge-small-en-v1.5's fastembed description states "512 input tokens truncation" -
        # 512 * 4 chars/token = 2048 unsafeguarded; the safety margin must bring it below that,
        # while staying a sane, non-trivial size.
        result = _resolve_max_chunk_chars("BAAI/bge-small-en-v1.5")
        assert 500 < result < 2048

    def test_unparseable_description_falls_back_and_logs_a_warning(self, caplog: pytest.LogCaptureFixture):
        fixture_models = [{"model": "fake/no-token-count", "dim": 4, "description": "Text embeddings, no count here."}]
        with (
            patch("prioris_mcp.vector.embedding.TextEmbedding.list_supported_models", return_value=fixture_models),
            caplog.at_level(logging.WARNING),
        ):
            result = _resolve_max_chunk_chars("fake/no-token-count")
        assert result == _DEFAULT_MAX_CHUNK_CHARS
        assert any("fake/no-token-count" in record.message for record in caplog.records)

    def test_model_not_in_registry_falls_back_and_logs_a_warning(self, caplog: pytest.LogCaptureFixture):
        with (
            patch("prioris_mcp.vector.embedding.TextEmbedding.list_supported_models", return_value=[]),
            caplog.at_level(logging.WARNING),
        ):
            result = _resolve_max_chunk_chars("not-a-real-model/does-not-exist")
        assert result == _DEFAULT_MAX_CHUNK_CHARS
        assert any("not-a-real-model/does-not-exist" in record.message for record in caplog.records)

    def test_fast_embed_backend_max_chunk_chars_matches_resolve(self):
        backend = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        assert backend.max_chunk_chars == _resolve_max_chunk_chars("BAAI/bge-small-en-v1.5")


class TestSqliteVecDocumentBackendMaxChunkCharsDefault:
    """Test that SqliteVecDocumentBackend defaults max_chunk_chars from the embedding backend."""

    def test_defaults_from_embedding_backend_not_the_bare_literal(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        # Sanity check this test model's resolved value actually differs from the bare-2000
        # fallback, so this test would fail if the default reverted to the literal.
        assert embedding.max_chunk_chars != _DEFAULT_MAX_CHUNK_CHARS

        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        assert backend._max_chunk_chars == embedding.max_chunk_chars

    def test_explicit_override_still_wins(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding, max_chunk_chars=123)
        assert backend._max_chunk_chars == 123
