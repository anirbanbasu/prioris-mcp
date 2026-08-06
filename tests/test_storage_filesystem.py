import asyncio
import json
from pathlib import Path

import pytest

from prioris_mcp import EnvVars
from prioris_mcp.storage.filesystem import FilesystemStorageBackend


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


class TestReadManifest:
    """read_manifest() over the catalogue (Task 7c)."""

    def test_read_manifest_returns_none_when_absent(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.read_manifest("arxiv", "2106.09685v2", "pdf")) is None

    def test_read_manifest_returns_catalogue_entry_when_present(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"raw bytes", original_identifier="2106.09685")
            return await backend.read_manifest("arxiv", "2106.09685v2", "pdf")

        entry = asyncio.run(scenario())
        assert entry["size_bytes"] == len(b"raw bytes")
        assert entry["original_identifier"] == "2106.09685"

    def test_read_manifest_defaults_to_document_artefact(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2106.09685v2", "pdf", b"# md", artefact="markdown")
            document_entry = await backend.read_manifest("arxiv", "2106.09685v2", "pdf")
            markdown_entry = await backend.read_manifest("arxiv", "2106.09685v2", "pdf", artefact="markdown")
            return document_entry, markdown_entry

        document_entry, markdown_entry = asyncio.run(scenario())
        assert document_entry is None
        assert markdown_entry is not None


class TestFindCanonicalIdentifier:
    """find_canonical_identifier() over the catalogue (Task 7c)."""

    def test_find_canonical_identifier_returns_none_when_absent(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.find_canonical_identifier("localfile", "20260729-1430-a3f2", "pdf")) is None

    def test_find_canonical_identifier_resolves_public_to_canonical(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("localfile", "abc123hash", "pdf", b"raw", public_identifier="20260729-1430-a3f2")
            return await backend.find_canonical_identifier("localfile", "20260729-1430-a3f2", "pdf")

        assert asyncio.run(scenario()) == "abc123hash"

    def test_find_canonical_identifier_scoped_to_provider_and_format(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("localfile", "abc123hash", "pdf", b"raw", public_identifier="shared-id")
            wrong_provider = await backend.find_canonical_identifier("arxiv", "shared-id", "pdf")
            wrong_format = await backend.find_canonical_identifier("localfile", "shared-id", "html")
            return wrong_provider, wrong_format

        wrong_provider, wrong_format = asyncio.run(scenario())
        assert wrong_provider is None
        assert wrong_format is None


class TestManifestForIntegration:
    """manifest_for() scoping across documents (Task 7c)."""

    def test_manifest_for_is_scoped_per_document_hash(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        manifest_a = backend.manifest_for("arxiv", "A")
        manifest_b = backend.manifest_for("arxiv", "B")

        async def scenario():
            await manifest_a.replace_leaf_rows("pdf", [{"start": 0, "length": 10}])
            pages_a = await manifest_a.total_pages("pdf")
            pages_b = await manifest_b.total_pages("pdf")
            return pages_a, pages_b

        pages_a, pages_b = asyncio.run(scenario())
        assert pages_a == 1
        assert pages_b == 0

    def test_manifest_for_same_document_returns_consistent_data(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.manifest_for("arxiv", "2106.09685v2").replace_leaf_rows("pdf", [{"start": 0, "length": 5}])
            return await backend.manifest_for("arxiv", "2106.09685v2").total_pages("pdf")

        assert asyncio.run(scenario()) == 1
