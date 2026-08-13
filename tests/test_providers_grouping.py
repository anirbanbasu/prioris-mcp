from pathlib import Path

from prioris_mcp.providers.grouping import DEFAULT_GROUPING, grouping_dir


def test_grouping_dir_default_grouping_returns_base_unchanged():
    """DEFAULT_GROUPING must not alter today's on-disk layout."""
    base = Path("/tmp/prioris-mcp")
    assert grouping_dir(base, DEFAULT_GROUPING) is base


def test_grouping_dir_other_grouping_returns_subdirectory():
    """A non-default grouping gets its own base/<grouping> subtree."""
    base = Path("/tmp/prioris-mcp")
    assert grouping_dir(base, "patents") == base / "patents"
