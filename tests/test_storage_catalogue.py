import asyncio

from prioris_mcp.storage.catalogue import Catalogue


def _entry(**overrides) -> dict:
    base = {
        "provider": "arxiv",
        "canonical_identifier": "2106.09685v2",
        "original_identifier": "2106.09685",
        "public_identifier": None,
        "format": "pdf",
        "artefact": "document",
        "size_bytes": 1234,
        "recorded_at": "2026-08-05T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestCatalogueUpsertAndGet:
    """Test Catalogue upsert and get operations."""

    def test_get_missing_entry_returns_none(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        result = asyncio.run(catalogue.get("arxiv", "2106.09685v2", "pdf", "document"))
        assert result is None

    def test_upsert_then_get_round_trips(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry()))
        result = asyncio.run(catalogue.get("arxiv", "2106.09685v2", "pdf", "document"))
        assert result == _entry()

    def test_upsert_replaces_existing_row_for_same_key(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(size_bytes=100)))
        asyncio.run(catalogue.upsert(_entry(size_bytes=200)))
        result = asyncio.run(catalogue.get("arxiv", "2106.09685v2", "pdf", "document"))
        assert result is not None
        assert result["size_bytes"] == 200

    def test_document_and_markdown_artefacts_are_independent_rows(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(artefact="document", size_bytes=100)))
        asyncio.run(catalogue.upsert(_entry(artefact="markdown", size_bytes=50)))
        result1 = asyncio.run(catalogue.get("arxiv", "2106.09685v2", "pdf", "document"))
        result2 = asyncio.run(catalogue.get("arxiv", "2106.09685v2", "pdf", "markdown"))
        assert result1 is not None
        assert result1["size_bytes"] == 100
        assert result2 is not None
        assert result2["size_bytes"] == 50


class TestCatalogueList:
    """Test Catalogue list operations."""

    def test_list_empty_catalogue(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        result = asyncio.run(catalogue.list())
        assert result == []

    def test_list_filters_by_provider_and_format(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(provider="arxiv", format="pdf")))
        asyncio.run(catalogue.upsert(_entry(provider="arxiv", format="html", artefact="markdown")))
        asyncio.run(catalogue.upsert(_entry(provider="europepmc", canonical_identifier="MED:1", format="xml")))
        assert len(asyncio.run(catalogue.list())) == 3
        assert len(asyncio.run(catalogue.list(provider="arxiv"))) == 2
        assert len(asyncio.run(catalogue.list(provider="arxiv", format="pdf"))) == 1


class TestCatalogueRemove:
    """Test Catalogue remove operations."""

    def test_remove_missing_entry_returns_false(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        result = asyncio.run(catalogue.remove("arxiv", "2106.09685v2", "pdf", "document"))
        assert result is False

    def test_remove_existing_entry_returns_true_and_deletes(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry()))
        result = asyncio.run(catalogue.remove("arxiv", "2106.09685v2", "pdf", "document"))
        assert result is True
        result2 = asyncio.run(catalogue.get("arxiv", "2106.09685v2", "pdf", "document"))
        assert result2 is None

    def test_remove_matches_by_external_identifier_not_canonical(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(
            catalogue.upsert(
                _entry(provider="localfile", canonical_identifier="abc123hash", public_identifier="20260729-1430-a3f2")
            )
        )
        result = asyncio.run(catalogue.remove("localfile", "20260729-1430-a3f2", "pdf", "document"))
        assert result is True

    def test_remove_all_artefacts_removes_every_artefact_for_format(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(artefact="document")))
        asyncio.run(catalogue.upsert(_entry(artefact="markdown")))
        removed = asyncio.run(catalogue.remove_all_artefacts("arxiv", "2106.09685v2", "pdf"))
        assert sorted(removed) == ["document", "markdown"]
        result = asyncio.run(catalogue.list(provider="arxiv"))
        assert result == []

    def test_remove_all_artefacts_on_missing_document_returns_empty_list(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        result = asyncio.run(catalogue.remove_all_artefacts("arxiv", "nope", "pdf"))
        assert result == []


class TestCatalogueFindByExternalIdentifier:
    """Test Catalogue find_by_external_identifier operations."""

    def test_find_returns_none_when_absent(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        result = asyncio.run(catalogue.find_by_external_identifier("localfile", "20260729-1430-a3f2", "pdf"))
        assert result is None

    def test_find_resolves_public_identifier_to_full_entry(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(
            catalogue.upsert(
                _entry(
                    provider="localfile",
                    canonical_identifier="abc123hash",
                    public_identifier="20260729-1430-a3f2",
                    artefact="document",
                )
            )
        )
        result = asyncio.run(catalogue.find_by_external_identifier("localfile", "20260729-1430-a3f2", "pdf"))
        assert result is not None
        assert result["canonical_identifier"] == "abc123hash"


class TestCatalogueCountFormats:
    """Test Catalogue count_formats operations."""

    def test_count_formats_zero_when_no_entries(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        result = asyncio.run(catalogue.count_formats("arxiv", "2106.09685v2"))
        assert result == 0

    def test_count_formats_counts_distinct_formats_not_artefacts(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(format="pdf", artefact="document")))
        asyncio.run(catalogue.upsert(_entry(format="pdf", artefact="markdown")))
        asyncio.run(catalogue.upsert(_entry(format="html", artefact="document")))
        result = asyncio.run(catalogue.count_formats("arxiv", "2106.09685v2"))
        assert result == 2
