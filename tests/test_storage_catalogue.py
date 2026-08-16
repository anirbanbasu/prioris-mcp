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
        entries, total = asyncio.run(catalogue.list())
        assert entries == []
        assert total == 0

    def test_list_filters_by_provider_and_format(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(provider="arxiv", format="pdf")))
        asyncio.run(catalogue.upsert(_entry(provider="arxiv", format="html", artefact="markdown")))
        asyncio.run(catalogue.upsert(_entry(provider="europepmc", canonical_identifier="MED:1", format="xml")))
        entries, total = asyncio.run(catalogue.list())
        assert len(entries) == 3
        assert total == 3
        arxiv_entries, arxiv_total = asyncio.run(catalogue.list(provider="arxiv"))
        assert len(arxiv_entries) == 2
        assert arxiv_total == 2
        arxiv_pdf_entries, arxiv_pdf_total = asyncio.run(catalogue.list(provider="arxiv", format="pdf"))
        assert len(arxiv_pdf_entries) == 1
        assert arxiv_pdf_total == 1

    def test_list_orders_newest_first_by_recorded_at(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(canonical_identifier="A", recorded_at="2026-08-01T00:00:00+00:00")))
        asyncio.run(catalogue.upsert(_entry(canonical_identifier="B", recorded_at="2026-08-03T00:00:00+00:00")))
        asyncio.run(catalogue.upsert(_entry(canonical_identifier="C", recorded_at="2026-08-02T00:00:00+00:00")))
        entries, total = asyncio.run(catalogue.list())
        assert total == 3
        assert [e["canonical_identifier"] for e in entries] == ["B", "C", "A"]

    def test_list_offset_and_limit_page_through_results(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        for index in range(5):
            asyncio.run(
                catalogue.upsert(
                    _entry(
                        canonical_identifier=f"doc-{index}",
                        recorded_at=f"2026-08-0{index + 1}T00:00:00+00:00",
                    )
                )
            )
        first_page, total = asyncio.run(catalogue.list(offset=0, limit=2))
        assert total == 5
        assert [e["canonical_identifier"] for e in first_page] == ["doc-4", "doc-3"]
        second_page, total2 = asyncio.run(catalogue.list(offset=2, limit=2))
        assert total2 == 5
        assert [e["canonical_identifier"] for e in second_page] == ["doc-2", "doc-1"]
        past_end, total3 = asyncio.run(catalogue.list(offset=5, limit=2))
        assert total3 == 5
        assert past_end == []

    def test_list_filters_by_artefact(self, tmp_path):
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(artefact="document")))
        asyncio.run(catalogue.upsert(_entry(artefact="markdown")))
        markdown_entries, markdown_total = asyncio.run(catalogue.list(artefact="markdown"))
        assert len(markdown_entries) == 1
        assert markdown_total == 1
        assert markdown_entries[0]["artefact"] == "markdown"

    def test_list_with_no_artefact_filter_is_unchanged(self, tmp_path):
        """artefact=None (the default) must preserve list()'s existing unfiltered behaviour."""
        catalogue = Catalogue(tmp_path / "catalogue.sqlite")
        asyncio.run(catalogue.upsert(_entry(artefact="document")))
        asyncio.run(catalogue.upsert(_entry(artefact="markdown")))
        _entries, total = asyncio.run(catalogue.list())
        assert total == 2


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
        result, total = asyncio.run(catalogue.list(provider="arxiv"))
        assert result == []
        assert total == 0

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
