import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prioris_mcp.errors import FileTooLargeError, InvalidRequestError, NotFoundError
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend
from prioris_mcp.providers.localfile import LocalFileProvider
from prioris_mcp.storage import FilesystemStorageBackend

# A structurally valid minimal PDF, not just a "%PDF-" magic-prefix stub: earlier tasks only
# needed PDF_BYTES to survive fetch_full_text's magic-byte sniff (see
# TestLocalFileProviderContentValidation below), but parse_full_text (this task) feeds it to the
# real, unmocked liteparse backend, which requires an actually-parseable PDF structure.
PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 24 Tf 20 100 Td (Hello World) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF"""


def _provider(tmp_path: Path, root: Path | None = None, max_size_bytes: int = 10_000_000) -> LocalFileProvider:
    storage = FilesystemStorageBackend(tmp_path / "storage")
    return LocalFileProvider(
        storage=storage,
        pdf_backend=LiteParsePdfBackend(),
        root_dir=root if root is not None else tmp_path,
        max_size_bytes=max_size_bytes,
    )


class TestLocalFileProviderPathContainment:
    """Path validation - docs/requirement-specification/05-security.md#local-filesystem-access-is-confined-to-an-operator-configured-root."""

    def test_absolute_path_is_rejected_without_reading_the_target(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        outside = tmp_path.parent / "outside.pdf"
        outside.write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("must not read")))
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text(str(outside)))

    def test_dotdot_traversal_is_rejected_without_reading_the_target(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(PDF_BYTES)
        provider = _provider(tmp_path, root=root)
        monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("must not read")))
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text("../outside.pdf"))

    def test_symlink_escape_is_rejected_without_reading_the_target(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(PDF_BYTES)
        symlink = root / "escape.pdf"
        symlink.symlink_to(outside)
        provider = _provider(tmp_path, root=root)
        monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("must not read")))
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text("escape.pdf"))

    def test_missing_file_is_not_found(self, tmp_path: Path):
        provider = _provider(tmp_path)
        with pytest.raises(NotFoundError):
            asyncio.run(provider.fetch_full_text("does-not-exist.pdf"))

    def test_valid_relative_path_within_root_succeeds(self, tmp_path: Path):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        result = asyncio.run(provider.fetch_full_text("paper.pdf"))
        assert result["format"] == "pdf"
        assert result["served_from_storage"] is False


class TestLocalFileProviderContentValidation:
    """Content sniffing/size limit - docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text."""

    def test_non_pdf_content_is_rejected_regardless_of_extension(self, tmp_path: Path):
        (tmp_path / "fake.pdf").write_bytes(b"not actually a pdf")
        provider = _provider(tmp_path)
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text("fake.pdf"))

    def test_oversized_file_is_rejected_before_reading_content(self, tmp_path: Path):
        big = tmp_path / "big.pdf"
        big.write_bytes(PDF_BYTES)
        provider = _provider(tmp_path, max_size_bytes=len(PDF_BYTES) - 1)
        with pytest.raises(FileTooLargeError):
            asyncio.run(provider.fetch_full_text("big.pdf"))

    def test_unsupported_format_is_rejected(self, tmp_path: Path):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text("paper.pdf", format="html"))


class TestLocalFileProviderFetchFullText:
    """Content-hash canonicalisation and caller-facing ID - docs/requirement-specification/02-storage.md#content-hash-canonicalisation-for-the-local-filesystem-source."""

    def test_first_fetch_mints_a_caller_facing_id(self, tmp_path: Path):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        result = asyncio.run(provider.fetch_full_text("paper.pdf"))
        assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-z]{4}", result["id"])
        assert result["served_from_storage"] is False
        assert result["resource_uri"] == f"research://localfile/{result['id']}/pdf/fulltext"
        assert result["size_bytes"] == len(PDF_BYTES)

    def test_second_fetch_of_unchanged_content_reuses_the_same_id(self, tmp_path: Path):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        first = asyncio.run(provider.fetch_full_text("paper.pdf"))
        second = asyncio.run(provider.fetch_full_text("paper.pdf"))
        assert second["id"] == first["id"]
        assert second["served_from_storage"] is True

    def test_changed_content_gets_a_new_id_and_old_id_remains_valid(self, tmp_path: Path):
        path = tmp_path / "paper.pdf"
        path.write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        first = asyncio.run(provider.fetch_full_text("paper.pdf"))

        path.write_bytes(PDF_BYTES + b" more content")
        second = asyncio.run(provider.fetch_full_text("paper.pdf"))

        assert second["id"] != first["id"]
        assert second["served_from_storage"] is False
        # The first id's content is still independently readable.
        first_manifest_identifier = asyncio.run(
            provider._storage.find_canonical_identifier("localfile", first["id"], "pdf")
        )
        assert asyncio.run(provider._storage.read("localfile", first_manifest_identifier, "pdf")) == PDF_BYTES

    def test_concurrent_fetches_of_same_unchanged_content_write_once_and_share_an_id(self, tmp_path: Path):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)

        async def scenario():
            return await asyncio.gather(
                provider.fetch_full_text("paper.pdf"),
                provider.fetch_full_text("paper.pdf"),
            )

        first, second = asyncio.run(scenario())
        assert first["id"] == second["id"]
        assert {first["served_from_storage"], second["served_from_storage"]} == {True, False}


class TestLocalFileProviderParseFullText:
    """docs/requirement-specification/06-interface-specification.md#research_localfile_parse_full_text."""

    def test_unrecognised_id_fails_not_found_without_touching_disk(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        provider = _provider(tmp_path)
        read_bytes_spy = AsyncMock(side_effect=AssertionError("must not read any path from disk"))
        monkeypatch.setattr(Path, "read_bytes", lambda self: read_bytes_spy())
        with pytest.raises(NotFoundError):
            asyncio.run(provider.parse_full_text("20260729-1430-a3f2"))
        read_bytes_spy.assert_not_called()

    def test_parses_already_fetched_content_and_returns_resource_uri(self, tmp_path: Path):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text("paper.pdf"))
        parsed = asyncio.run(provider.parse_full_text(fetched["id"]))
        assert parsed["resource_uri"] == f"research://localfile/{fetched['id']}/pdf/markdown"
        assert isinstance(parsed["markdown"], str)
        assert parsed["offset"] == 0
        assert parsed["has_more"] is False

    def test_second_parse_of_same_id_does_not_reparse(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        (tmp_path / "paper.pdf").write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text("paper.pdf"))
        asyncio.run(provider.parse_full_text(fetched["id"]))

        original_to_markdown = provider._pdf_backend.to_markdown

        async def _fail_if_called(content: bytes) -> str:
            raise AssertionError("must not re-parse an already-parsed identifier")

        monkeypatch.setattr(provider._pdf_backend, "to_markdown", _fail_if_called)
        asyncio.run(provider.parse_full_text(fetched["id"]))
        monkeypatch.setattr(provider._pdf_backend, "to_markdown", original_to_markdown)

    def test_never_triggers_a_fetch(self, tmp_path: Path):
        provider = _provider(tmp_path)
        # No file was ever fetched; parse must fail rather than reach into the filesystem itself.
        with pytest.raises(NotFoundError):
            asyncio.run(provider.parse_full_text("20260729-1430-a3f2"))


class TestLocalFileProviderDeleteDoesNotTouchOriginal:
    """docs/requirement-specification/07-test-specification.md#storage-management-acceptance-criteria."""

    def test_delete_via_storage_backend_leaves_original_file_untouched(self, tmp_path: Path):
        original = tmp_path / "paper.pdf"
        original.write_bytes(PDF_BYTES)
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text("paper.pdf"))
        asyncio.run(provider._storage.delete("localfile", fetched["id"], "pdf"))
        assert original.read_bytes() == PDF_BYTES
