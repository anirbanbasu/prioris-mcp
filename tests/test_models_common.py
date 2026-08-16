from prioris_mcp.models.common import VectorRebuildMechanismStatus, VectorRebuildStatus


class TestVectorRebuildMechanismStatus:
    """Tests for VectorRebuildMechanismStatus - one mechanism's rebuild progress."""

    def test_shape(self) -> None:
        status = VectorRebuildMechanismStatus(total=5, pending=1, succeeded=2, failed=1, cancelled=1, active=True)
        assert status.total == 5
        assert status.pending == 1
        assert status.succeeded == 2
        assert status.failed == 1
        assert status.cancelled == 1
        assert status.active is True


class TestVectorRebuildStatus:
    """Tests for VectorRebuildStatus - the research://vector-index/rebuild-status resource shape."""

    def test_shape(self) -> None:
        status = VectorRebuildStatus(
            documents=VectorRebuildMechanismStatus(total=5, pending=1, succeeded=2, failed=1, cancelled=1, active=True),
            notes=VectorRebuildMechanismStatus(total=0, pending=0, succeeded=0, failed=0, cancelled=0, active=False),
        )
        assert status.documents.total == 5
        assert status.notes.active is False
