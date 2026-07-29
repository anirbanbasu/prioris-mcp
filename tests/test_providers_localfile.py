import asyncio
import re
from pathlib import Path

import pytest

from prioris_mcp.errors import FileTooLargeError, InvalidRequestError, NotFoundError
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend
from prioris_mcp.providers.localfile import LocalFileProvider
from prioris_mcp.storage import FilesystemStorageBackend

PDF_BYTES = b"%PDF-1.4 fake pdf content"


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
