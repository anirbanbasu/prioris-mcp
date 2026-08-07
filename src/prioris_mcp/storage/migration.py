"""One-time migration from the pre-v2 flat layout to documents/<hash>/<format>/{...}.

See docs/requirement-specification/02-storage.md#migration. Gated behind a version marker file
so it runs at most once per storage root, and is safe to call on every FilesystemStorageBackend
construction (a no-op once migrated, or on a store that never had the old layout).
"""

import hashlib
import json
import sqlite3
from pathlib import Path

from prioris_mcp.storage.catalogue import _SCHEMA

_VERSION_MARKER_NAME = ".storage-version"
_CURRENT_VERSION = "2"


def _document_hash(provider: str, identifier: str) -> str:
    payload = json.dumps([provider, identifier])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def migrate_if_needed(base_dir: Path) -> None:
    """Migrate `base_dir` from the flat `{key}.data`/`{key}.json` layout, once.

    Safe to call unconditionally at FilesystemStorageBackend construction time.
    """
    marker_path = base_dir / _VERSION_MARKER_NAME
    if marker_path.exists() and marker_path.read_text().strip() == _CURRENT_VERSION:
        return

    old_manifests = sorted(base_dir.glob("*.json"))
    if old_manifests:
        _migrate_entries(base_dir, old_manifests)

    marker_path.write_text(_CURRENT_VERSION)


def _migrate_entries(base_dir: Path, old_manifests: list[Path]) -> None:
    conn = sqlite3.connect(base_dir / "catalogue.sqlite")
    try:
        conn.executescript(_SCHEMA)
        for manifest_path in old_manifests:
            old_entry = json.loads(manifest_path.read_text())
            data_path = manifest_path.with_suffix(".data")
            if not data_path.exists():
                continue  # orphaned manifest with no content - nothing to migrate

            old_format = old_entry["format"]
            if old_format.endswith("-markdown"):
                new_format, artefact = old_format[: -len("-markdown")], "markdown"
            else:
                new_format, artefact = old_format, "document"

            provider = old_entry["provider"]
            canonical_identifier = old_entry["canonical_identifier"]
            new_dir = base_dir / "documents" / _document_hash(provider, canonical_identifier) / new_format
            new_dir.mkdir(parents=True, exist_ok=True)
            (new_dir / artefact).write_bytes(data_path.read_bytes())

            metadata_entry = {
                "provider": provider,
                "canonical_identifier": canonical_identifier,
                "original_identifier": old_entry.get("original_identifier"),
                "public_identifier": old_entry.get("public_identifier"),
                "format": new_format,
                "artefact": artefact,
                "size_bytes": old_entry.get("size_bytes", data_path.stat().st_size),
                "recorded_at": old_entry.get("fetched_at") or old_entry.get("recorded_at"),
            }
            with (new_dir / "metadata.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(metadata_entry) + "\n")
            conn.execute(
                """
                INSERT INTO entries
                    (provider, canonical_identifier, original_identifier, public_identifier,
                     format, artefact, size_bytes, recorded_at)
                VALUES (:provider, :canonical_identifier, :original_identifier, :public_identifier,
                        :format, :artefact, :size_bytes, :recorded_at)
                ON CONFLICT (provider, canonical_identifier, format, artefact) DO UPDATE SET
                    size_bytes = excluded.size_bytes, recorded_at = excluded.recorded_at
                """,
                metadata_entry,
            )
            data_path.unlink()
            manifest_path.unlink()
        conn.commit()
    finally:
        conn.close()
