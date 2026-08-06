import asyncio
import json
from pathlib import Path

import pytest

from prioris_mcp import EnvVars
from prioris_mcp.storage.filesystem import FilesystemStorageBackend


class TestFilesystemStorageBackend:
    """Dedicated test class for FilesystemStorageBackend."""

    def test_exists_is_false_before_write(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.exists("arxiv", "2601.05525v2", "pdf")) is False

    def test_write_then_read_round_trips_content(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2601.05525v2", "pdf", b"%PDF-1.4 fake content")
            exists = await backend.exists("arxiv", "2601.05525v2", "pdf")
            content = await backend.read("arxiv", "2601.05525v2", "pdf")
            return exists, content

        exists, content = asyncio.run(scenario())
        assert exists is True
        assert content == b"%PDF-1.4 fake content"

    def test_write_persists_a_manifest_sidecar(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"content", original_identifier="2601.05525"))
        key = FilesystemStorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        manifest = json.loads((tmp_path / f"{key}.json").read_text())
        assert manifest == {
            "provider": "arxiv",
            "canonical_identifier": "2601.05525v2",
            "original_identifier": "2601.05525",
            "public_identifier": None,
            "format": "pdf",
            "size_bytes": len(b"content"),
            "fetched_at": manifest["fetched_at"],  # asserted separately below
        }
        # fetched_at must be a real, parseable ISO-8601 UTC timestamp
        from datetime import datetime

        datetime.fromisoformat(manifest["fetched_at"])

    def test_write_without_original_identifier_stores_null(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"content"))
        key = FilesystemStorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        manifest = json.loads((tmp_path / f"{key}.json").read_text())
        assert manifest["original_identifier"] is None
        assert manifest["public_identifier"] is None

    def test_write_with_public_identifier_stores_it(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(
            backend.write("localfile", "content-hash-abc", "pdf", b"content", public_identifier="20260729-1430-a3f2")
        )
        key = FilesystemStorageBackend._storage_key("localfile", "content-hash-abc", "pdf")
        manifest = json.loads((tmp_path / f"{key}.json").read_text())
        assert manifest["public_identifier"] == "20260729-1430-a3f2"

    def test_read_missing_content_raises_file_not_found(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        with pytest.raises(FileNotFoundError):
            asyncio.run(backend.read("arxiv", "does-not-exist", "pdf"))

    def test_base_dir_is_created_if_missing(self, tmp_path: Path):
        target = tmp_path / "nested" / "downloads"
        FilesystemStorageBackend(target)
        assert target.is_dir()

    def test_no_leftover_temp_files_after_write(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"content"))
        assert list(tmp_path.glob("*.tmp")) == []

    def test_defaults_base_dir_to_env_var(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "from-env")
        backend = FilesystemStorageBackend()
        assert backend._base_dir == tmp_path / "from-env"
        assert (tmp_path / "from-env").is_dir()

    def test_list_returns_all_entries_with_no_filters(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"aaa"))
        asyncio.run(backend.write("europepmc", "PMC123", "xml", b"bb"))
        entries = asyncio.run(backend.list())
        assert {(e["provider"], e["identifier"], e["format"], e["size_bytes"]) for e in entries} == {
            ("arxiv", "2601.05525v2", "pdf", 3),
            ("europepmc", "PMC123", "xml", 2),
        }

    def test_list_filters_by_provider(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"a"))
        asyncio.run(backend.write("europepmc", "PMC123", "xml", b"b"))
        entries = asyncio.run(backend.list(provider="arxiv"))
        assert [e["identifier"] for e in entries] == ["2601.05525v2"]

    def test_list_filters_by_provider_and_format(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"a"))
        asyncio.run(backend.write("arxiv", "2601.05525v2", "html", b"b"))
        entries = asyncio.run(backend.list(provider="arxiv", format="html"))
        assert [e["format"] for e in entries] == ["html"]

    def test_list_reports_public_identifier_when_set(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("localfile", "content-hash-abc", "pdf", b"a", public_identifier="20260729-1430-a3f2"))
        entries = asyncio.run(backend.list(provider="localfile"))
        assert entries[0]["identifier"] == "20260729-1430-a3f2"

    def test_list_returns_empty_when_nothing_matches(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.list(provider="arxiv")) == []

    def test_list_tolerates_legacy_manifest_missing_size_bytes(self, tmp_path: Path):
        """Regression test for issue #10.

        Manifests written before commit 52ecfc0 lack `size_bytes`/`public_identifier`;
        `list()` must not raise `KeyError` on them.
        """
        backend = FilesystemStorageBackend(tmp_path)
        key = FilesystemStorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        (tmp_path / f"{key}.data").write_bytes(b"legacy content")
        legacy_manifest = {
            "provider": "arxiv",
            "canonical_identifier": "2601.05525v2",
            "original_identifier": None,
            "format": "pdf",
            "fetched_at": "2026-07-01T00:00:00+00:00",
        }
        (tmp_path / f"{key}.json").write_text(json.dumps(legacy_manifest))

        entries = asyncio.run(backend.list())

        assert entries == [
            {
                "provider": "arxiv",
                "identifier": "2601.05525v2",
                "format": "pdf",
                "fetched_at": "2026-07-01T00:00:00+00:00",
                "size_bytes": len(b"legacy content"),
            }
        ]

    def test_list_defaults_size_bytes_to_zero_when_data_file_missing(self, tmp_path: Path):
        """Legacy manifest with no `.data` file falls back to `0` size.

        Covers the case where the `.data` file is also absent (e.g. deleted concurrently);
        `list()` must fall back to `0` rather than raising `FileNotFoundError`.
        """
        backend = FilesystemStorageBackend(tmp_path)
        key = FilesystemStorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        legacy_manifest = {
            "provider": "arxiv",
            "canonical_identifier": "2601.05525v2",
            "original_identifier": None,
            "format": "pdf",
            "fetched_at": "2026-07-01T00:00:00+00:00",
        }
        (tmp_path / f"{key}.json").write_text(json.dumps(legacy_manifest))
        # No corresponding .data file written.

        entries = asyncio.run(backend.list())

        assert entries == [
            {
                "provider": "arxiv",
                "identifier": "2601.05525v2",
                "format": "pdf",
                "fetched_at": "2026-07-01T00:00:00+00:00",
                "size_bytes": 0,
            }
        ]

    def test_delete_removes_content_and_manifest_and_returns_true(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"a"))
        deleted = asyncio.run(backend.delete("arxiv", "2601.05525v2", "pdf"))
        assert deleted is True
        assert asyncio.run(backend.exists("arxiv", "2601.05525v2", "pdf")) is False
        assert list(tmp_path.glob("*.json")) == []
        assert list(tmp_path.glob("*.data")) == []

    def test_delete_by_public_identifier(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("localfile", "content-hash-abc", "pdf", b"a", public_identifier="20260729-1430-a3f2"))
        deleted = asyncio.run(backend.delete("localfile", "20260729-1430-a3f2", "pdf"))
        assert deleted is True
        assert asyncio.run(backend.exists("localfile", "content-hash-abc", "pdf")) is False

    def test_delete_returns_false_when_nothing_matches(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.delete("arxiv", "does-not-exist", "pdf")) is False

    def test_delete_skips_non_matching_entries_when_nothing_matches(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("europepmc", "PMC1", "xml", b"a"))  # provider/format mismatch
        asyncio.run(backend.write("arxiv", "other-id", "pdf", b"b"))  # same provider/format, identifier mismatch
        deleted = asyncio.run(backend.delete("arxiv", "does-not-exist", "pdf"))
        assert deleted is False
        assert asyncio.run(backend.exists("europepmc", "PMC1", "xml")) is True
        assert asyncio.run(backend.exists("arxiv", "other-id", "pdf")) is True

    def test_read_manifest_returns_none_when_absent(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.read_manifest("arxiv", "2601.05525v2", "pdf")) is None

    def test_read_manifest_returns_parsed_manifest(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("localfile", "content-hash-abc", "pdf", b"a", public_identifier="20260729-1430-a3f2"))
        manifest = asyncio.run(backend.read_manifest("localfile", "content-hash-abc", "pdf"))
        assert manifest is not None
        assert manifest["public_identifier"] == "20260729-1430-a3f2"

    def test_find_canonical_identifier_returns_none_when_absent(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.find_canonical_identifier("localfile", "20260729-1430-a3f2", "pdf")) is None

    def test_find_canonical_identifier_skips_non_matching_provider_or_format(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("europepmc", "PMC1", "xml", b"a", public_identifier="other-public-id"))
        result = asyncio.run(backend.find_canonical_identifier("localfile", "some-id", "pdf"))
        assert result is None

    def test_find_canonical_identifier_resolves_public_id_to_storage_key(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("localfile", "content-hash-abc", "pdf", b"a", public_identifier="20260729-1430-a3f2"))
        canonical = asyncio.run(backend.find_canonical_identifier("localfile", "20260729-1430-a3f2", "pdf"))
        assert canonical == "content-hash-abc"

    def test_get_or_create_works_end_to_end_against_the_real_filesystem(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def factory() -> bytes:
            return b"downloaded bytes"

        first = asyncio.run(backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory))
        second = asyncio.run(backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory))
        assert first == (b"downloaded bytes", False)
        assert second == (b"downloaded bytes", True)


class TestExistsWriteRead:
    """Directory-layout tests for exists/write/read against the artefact model (Task 7a)."""

    def test_exists_false_before_write(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.exists("arxiv", "2106.09685v2", "pdf")) is False

    def test_write_then_read_round_trips_document_artefact(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"%PDF-1.4 raw bytes")
            exists = await backend.exists("arxiv", "2106.09685v2", "pdf")
            content = await backend.read("arxiv", "2106.09685v2", "pdf")
            return exists, content

        exists, content = asyncio.run(scenario())
        assert exists is True
        assert content == b"%PDF-1.4 raw bytes"

    def test_document_and_markdown_artefacts_are_independent_files(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw", artefact="document")
            await backend.write("arxiv", "2106.09685v2", "pdf", b"# md", artefact="markdown")
            document = await backend.read("arxiv", "2106.09685v2", "pdf", artefact="document")
            markdown = await backend.read("arxiv", "2106.09685v2", "pdf", artefact="markdown")
            return document, markdown

        document, markdown = asyncio.run(scenario())
        assert document == b"raw"
        assert markdown == b"# md"

    def test_read_of_missing_content_raises_file_not_found(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        with pytest.raises(FileNotFoundError):
            asyncio.run(backend.read("arxiv", "nope", "pdf"))

    def test_write_creates_documents_subdirectory_layout(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        location = asyncio.run(backend.write("arxiv", "2106.09685v2", "pdf", b"raw"))
        assert "documents" in location
        assert location.endswith("pdf/document")

    def test_write_populates_catalogue(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw bytes", original_identifier="2106.09685")
            return await backend._catalogue.get("arxiv", "2106.09685v2", "pdf", "document")

        entry = asyncio.run(scenario())
        assert entry["size_bytes"] == len(b"raw bytes")
        assert entry["original_identifier"] == "2106.09685"

    def test_write_appends_metadata_jsonl(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2106.09685v2", "pdf", b"raw bytes"))
        doc_dir = backend._document_dir("arxiv", "2106.09685v2")
        lines = (doc_dir / "pdf" / "metadata.jsonl").read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["provider"] == "arxiv"
        assert record["artefact"] == "document"

    def test_no_leftover_tmp_files_after_write(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2106.09685v2", "pdf", b"raw"))
        doc_dir = backend._document_dir("arxiv", "2106.09685v2")
        assert list((doc_dir / "pdf").glob("*.tmp")) == []

    def test_base_dir_auto_created_if_missing(self, tmp_path: Path):
        target = tmp_path / "nested" / "storage"
        FilesystemStorageBackend(target)
        assert target.exists()
        assert (target / "documents").exists()

    def test_defaults_base_dir_to_env_vars_storage_dir(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "from-env")
        backend = FilesystemStorageBackend()
        assert backend._base_dir == tmp_path / "from-env"


class TestGetOrCreateEndToEnd:
    """End-to-end get_or_create test against the artefact directory layout (Task 7a)."""

    def test_get_or_create_round_trips_against_real_filesystem(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def factory() -> bytes:
            return b"produced content"

        async def scenario():
            content, served = await backend.get_or_create("arxiv", "2106.09685v2", "pdf", factory)
            content2, served2 = await backend.get_or_create("arxiv", "2106.09685v2", "pdf", factory)
            return content, served, content2, served2

        content, served, content2, served2 = asyncio.run(scenario())
        assert content == b"produced content"
        assert served is False
        assert content2 == b"produced content"
        assert served2 is True


class TestList:
    """list() over the catalogue, with provider/format filtering (Task 7b)."""

    def test_list_empty_store(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.list()) == []

    def test_list_reports_public_identifier_when_set(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("localfile", "abc123hash", "pdf", b"raw", public_identifier="20260729-1430-a3f2")
            return await backend.list(provider="localfile")

        entries = asyncio.run(scenario())
        assert entries[0]["identifier"] == "20260729-1430-a3f2"

    def test_list_reports_canonical_identifier_when_no_public_identifier(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw")
            return await backend.list(provider="arxiv")

        entries = asyncio.run(scenario())
        assert entries[0]["identifier"] == "2106.09685v2"

    def test_list_includes_artefact_field(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw", artefact="document")
            await backend.write("arxiv", "2106.09685v2", "pdf", b"# md", artefact="markdown")
            return await backend.list(provider="arxiv")

        entries = asyncio.run(scenario())
        assert {e["artefact"] for e in entries} == {"document", "markdown"}

    def test_list_filters_by_provider_and_format(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "A", "pdf", b"1")
            await backend.write("arxiv", "B", "html", b"2")
            await backend.write("europepmc", "MED:1", "xml", b"3")
            all_entries = await backend.list()
            arxiv_entries = await backend.list(provider="arxiv")
            arxiv_pdf_entries = await backend.list(provider="arxiv", format="pdf")
            return all_entries, arxiv_entries, arxiv_pdf_entries

        all_entries, arxiv_entries, arxiv_pdf_entries = asyncio.run(scenario())
        assert len(all_entries) == 3
        assert len(arxiv_entries) == 2
        assert len(arxiv_pdf_entries) == 1


class TestDeleteSingleArtefact:
    """delete() for a single artefact ("document" or "markdown") (Task 7b)."""

    def test_delete_missing_entry_returns_false(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.delete("arxiv", "nope", "pdf", "document")) is False

    def test_delete_document_leaves_markdown_in_place(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw", artefact="document")
            await backend.write("arxiv", "2106.09685v2", "pdf", b"# md", artefact="markdown")
            deleted = await backend.delete("arxiv", "2106.09685v2", "pdf", "document")
            document_exists = await backend.exists("arxiv", "2106.09685v2", "pdf", artefact="document")
            markdown_exists = await backend.exists("arxiv", "2106.09685v2", "pdf", artefact="markdown")
            return deleted, document_exists, markdown_exists

        deleted, document_exists, markdown_exists = asyncio.run(scenario())
        assert deleted is True
        assert document_exists is False
        assert markdown_exists is True

    def test_delete_resolves_by_public_identifier(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("localfile", "abc123hash", "pdf", b"raw", public_identifier="20260729-1430-a3f2")
            return await backend.delete("localfile", "20260729-1430-a3f2", "pdf", "document")

        assert asyncio.run(scenario()) is True


class TestDeleteAllCascades:
    """delete(artefact="all") cascade: format directory, manifest rows, document-hash directory (Task 7b)."""

    def test_delete_all_removes_every_artefact_and_the_format_directory(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw", artefact="document")
            await backend.write("arxiv", "2106.09685v2", "pdf", b"# md", artefact="markdown")
            deleted = await backend.delete("arxiv", "2106.09685v2", "pdf", "all")
            entries = await backend.list(provider="arxiv")
            return deleted, entries

        deleted, entries = asyncio.run(scenario())
        assert deleted is True
        assert entries == []
        format_dir = backend._document_dir("arxiv", "2106.09685v2") / "pdf"
        assert not format_dir.exists()

    def test_delete_all_removes_document_hash_dir_when_last_format(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        doc_dir = backend._document_dir("arxiv", "2106.09685v2")

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw")
            await backend.delete("arxiv", "2106.09685v2", "pdf", "all")

        asyncio.run(scenario())
        assert not doc_dir.exists()

    def test_delete_all_keeps_document_hash_dir_when_other_formats_remain(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        doc_dir = backend._document_dir("arxiv", "2106.09685v2")

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw")
            await backend.write("arxiv", "2106.09685v2", "html", b"raw html")
            await backend.delete("arxiv", "2106.09685v2", "pdf", "all")
            return await backend.exists("arxiv", "2106.09685v2", "html")

        html_exists = asyncio.run(scenario())
        assert doc_dir.exists()
        assert html_exists is True

    def test_delete_all_removes_manifest_rows_for_that_format(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw")
            await backend.write("arxiv", "2106.09685v2", "html", b"raw html")
            manifest = backend.manifest_for("arxiv", "2106.09685v2")
            await manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 10}])
            await manifest.replace_leaf_rows("html", [{"start": 0, "length": 20}])
            await backend.delete("arxiv", "2106.09685v2", "pdf", "all")
            pdf_pages = await manifest.total_pages("pdf")
            html_pages = await manifest.total_pages("html")
            return pdf_pages, html_pages

        pdf_pages, html_pages = asyncio.run(scenario())
        assert pdf_pages == 0
        assert html_pages == 1

    def test_delete_all_on_missing_entry_returns_false(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.delete("arxiv", "nope", "pdf", "all")) is False
