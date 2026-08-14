import pytest

from prioris_mcp import EnvVars


@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dirs_session(tmp_path_factory):
    """Session-scoped isolation so class-scoped fixtures don't touch real data directories either.

    A few test classes construct PriorisMCP() in a class-scoped fixture, which runs before the
    function-scoped _isolated_data_dirs fixture below ever executes. This session-scoped fixture
    closes that gap by isolating before anything in the session runs. Uses tmp_path_factory (not
    monkeypatch) deliberately: a monkeypatch dependency here previously caused a fixture-teardown-
    ordering conflict with test_env_vars.py's autouse module-reload fixture.
    """
    base = tmp_path_factory.mktemp("session-isolated-data")
    original_storage = EnvVars.PRIORIS_MCP_STORAGE_DIR
    original_notes = EnvVars.PRIORIS_MCP_NOTES_DIR
    original_vector = EnvVars.PRIORIS_MCP_VECTOR_DIR

    EnvVars.PRIORIS_MCP_STORAGE_DIR = base / "downloads"
    EnvVars.PRIORIS_MCP_NOTES_DIR = base / "notes"
    EnvVars.PRIORIS_MCP_VECTOR_DIR = base / "vectors"

    yield

    EnvVars.PRIORIS_MCP_STORAGE_DIR = original_storage
    EnvVars.PRIORIS_MCP_NOTES_DIR = original_notes
    EnvVars.PRIORIS_MCP_VECTOR_DIR = original_vector


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
