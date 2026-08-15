from prioris_mcp.models.common import VectorRebuildMechanismStatus, VectorRebuildStatus


class TestVectorRebuildMechanismStatus:
    """Tests for VectorRebuildMechanismStatus - one mechanism's rebuild progress."""

    def test_shape(self) -> None:
        status = VectorRebuildMechanismStatus(total=5, remaining=2)
        assert status.total == 5
        assert status.remaining == 2


class TestVectorRebuildStatus:
    """Tests for VectorRebuildStatus - the research://vector-index/rebuild-status resource shape."""

    def test_shape(self) -> None:
        status = VectorRebuildStatus(
            documents=VectorRebuildMechanismStatus(total=5, remaining=2),
            notes=VectorRebuildMechanismStatus(total=0, remaining=0),
        )
        assert status.documents.total == 5
        assert status.notes.remaining == 0
