"""Path filtering helpers for noisy/non-source directory names."""

from __future__ import annotations

from pathlib import PurePath

_BACKUP_DIR_EXACT = frozenset(
    {
        "backup",
        "backups",
        "bak",
        "snapshot",
        "snapshots",
        "archive",
        "archives",
        "old_versions",
        "old-version",
        "legacy_backup",
    }
)

_BACKUP_DIR_PREFIXES = (
    "backup_",
    "backup-",
    "bak_",
    "bak-",
    "snapshot_",
    "snapshot-",
    "archive_",
    "archive-",
)

_BACKUP_DIR_SUFFIXES = (
    "_backup",
    "-backup",
    ".backup",
    "_bak",
    "-bak",
    ".bak",
    "_snapshot",
    "-snapshot",
    ".snapshot",
    "_archive",
    "-archive",
    ".archive",
    "_old",
    "-old",
    ".old",
)


def is_backup_like_directory(dirname: str) -> bool:
    """Return True when a directory name looks like backup/archive noise."""
    if not dirname:
        return False

    leaf = PurePath(dirname).name
    if not leaf:
        return False

    lowered = leaf.lower()
    if lowered in _BACKUP_DIR_EXACT:
        return True
    if any(lowered.startswith(prefix) for prefix in _BACKUP_DIR_PREFIXES):
        return True
    return any(lowered.endswith(suffix) for suffix in _BACKUP_DIR_SUFFIXES)
