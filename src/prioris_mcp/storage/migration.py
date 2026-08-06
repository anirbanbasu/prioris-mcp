"""Migration of pre-existing storage directories to the current on-disk layout.

Task 8 will replace this stub's body with a real migration; the call site in
`FilesystemStorageBackend.__init__` is stable now so Task 8 only needs to change this file.
"""

from pathlib import Path


def migrate_if_needed(base_dir: Path) -> None:
    """No-op placeholder; Task 8 implements the actual migration logic here."""
    return
