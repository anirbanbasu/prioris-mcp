import pytest

from prioris_mcp import EnvVars


@pytest.fixture(autouse=True)
def _isolated_data_dirs(tmp_path):
    """Every test gets its own isolated storage/notes/vector directories by default.

    Prevents tests that construct PriorisMCP() without explicitly patching these from touching
    the real default data directories on the developer's machine.
    """
    # Save original values
    original_storage = EnvVars.PRIORIS_MCP_STORAGE_DIR
    original_notes = EnvVars.PRIORIS_MCP_NOTES_DIR
    original_vector = EnvVars.PRIORIS_MCP_VECTOR_DIR

    # Set isolated directories
    EnvVars.PRIORIS_MCP_STORAGE_DIR = tmp_path / "downloads"
    EnvVars.PRIORIS_MCP_NOTES_DIR = tmp_path / "notes"
    EnvVars.PRIORIS_MCP_VECTOR_DIR = tmp_path / "vectors"

    yield

    # Restore original values
    EnvVars.PRIORIS_MCP_STORAGE_DIR = original_storage
    EnvVars.PRIORIS_MCP_NOTES_DIR = original_notes
    EnvVars.PRIORIS_MCP_VECTOR_DIR = original_vector
