import json
import sqlite3

from prioris_mcp.storage.migration import migrate_if_needed


def _write_old_entry(base_dir, provider, identifier, format, content, **extra):
    payload = json.dumps([provider, identifier, format])
    import hashlib

    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    (base_dir / f"{key}.data").write_bytes(content)
    manifest = {
        "provider": provider,
        "canonical_identifier": identifier,
        "format": format,
        "original_identifier": extra.get("original_identifier"),
        "public_identifier": extra.get("public_identifier"),
        "size_bytes": len(content),
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    (base_dir / f"{key}.json").write_text(json.dumps(manifest))


class TestMigrateFreshStore:
    """Test migration on fresh (empty) storage directory."""

    def test_migrate_on_empty_store_writes_version_marker_and_does_nothing_else(self, tmp_path):
        migrate_if_needed(tmp_path)
        assert (tmp_path / ".storage-version").read_text().strip() == "2"
        assert not (tmp_path / "documents").exists()


class TestMigrateDocumentArtefact:
    """Test migration of document artefacts."""

    def test_migrate_moves_document_artefact_to_new_layout(self, tmp_path):
        _write_old_entry(tmp_path, "arxiv", "2106.09685v2", "pdf", b"%PDF-1.4 raw")
        migrate_if_needed(tmp_path)
        import hashlib

        doc_hash = hashlib.sha256(json.dumps(["arxiv", "2106.09685v2"]).encode("utf-8")).hexdigest()
        new_path = tmp_path / "documents" / doc_hash / "pdf" / "document"
        assert new_path.read_bytes() == b"%PDF-1.4 raw"

    def test_migrate_deletes_old_layout_files(self, tmp_path):
        _write_old_entry(tmp_path, "arxiv", "2106.09685v2", "pdf", b"raw")
        migrate_if_needed(tmp_path)
        assert list(tmp_path.glob("*.data")) == []
        assert list(tmp_path.glob("*.json")) == []

    def test_migrate_populates_catalogue(self, tmp_path):
        _write_old_entry(tmp_path, "arxiv", "2106.09685v2", "pdf", b"raw", original_identifier="2106.09685")
        migrate_if_needed(tmp_path)
        conn = sqlite3.connect(tmp_path / "catalogue.sqlite")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM entries WHERE canonical_identifier = ?", ("2106.09685v2",)).fetchone()
        assert row["format"] == "pdf"
        assert row["artefact"] == "document"
        assert row["original_identifier"] == "2106.09685"


class TestMigrateMarkdownArtefact:
    """Test migration of markdown artefacts and format splitting."""

    def test_migrate_splits_pdf_markdown_format_into_markdown_artefact(self, tmp_path):
        _write_old_entry(tmp_path, "arxiv", "2106.09685v2", "pdf-markdown", b"# Title")
        migrate_if_needed(tmp_path)
        import hashlib

        doc_hash = hashlib.sha256(json.dumps(["arxiv", "2106.09685v2"]).encode("utf-8")).hexdigest()
        new_path = tmp_path / "documents" / doc_hash / "pdf" / "markdown"
        assert new_path.read_bytes() == b"# Title"
        conn = sqlite3.connect(tmp_path / "catalogue.sqlite")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM entries WHERE canonical_identifier = ? AND artefact = 'markdown'", ("2106.09685v2",)
        ).fetchone()
        assert row["format"] == "pdf"


class TestMigrateIdempotency:
    """Test that migration is idempotent."""

    def test_running_migration_twice_is_a_no_op_the_second_time(self, tmp_path):
        _write_old_entry(tmp_path, "arxiv", "2106.09685v2", "pdf", b"raw")
        migrate_if_needed(tmp_path)
        migrate_if_needed(tmp_path)  # must not raise, must not duplicate rows
        conn = sqlite3.connect(tmp_path / "catalogue.sqlite")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM entries WHERE canonical_identifier = ?", ("2106.09685v2",)).fetchall()
        assert len(rows) == 1


class TestMigrateOrphanedManifest:
    """Test handling of orphaned manifests (no matching data file)."""

    def test_manifest_without_matching_data_file_is_skipped(self, tmp_path):
        (tmp_path / "deadbeef.json").write_text(
            json.dumps(
                {
                    "provider": "arxiv",
                    "canonical_identifier": "X",
                    "format": "pdf",
                    "original_identifier": None,
                    "public_identifier": None,
                    "size_bytes": 0,
                    "fetched_at": "2026-01-01T00:00:00+00:00",
                }
            )
        )
        migrate_if_needed(tmp_path)  # must not raise
        assert (tmp_path / ".storage-version").exists()
