"""Mutation-killing tests for lintgate.path_filters."""

from __future__ import annotations

import pytest

from lintgate.path_filters import (
    _BACKUP_DIR_EXACT,
    _BACKUP_DIR_PREFIXES,
    _BACKUP_DIR_SUFFIXES,
    is_backup_like_directory,
)

# ---------------------------------------------------------------------------
# Constants: verify exact membership / contents
# ---------------------------------------------------------------------------


class TestConstants:
    """Ensure the sets/tuples contain exactly the expected entries."""

    def test_backup_dir_exact_contents(self):
        expected = frozenset(
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
        assert expected == _BACKUP_DIR_EXACT

    def test_backup_dir_exact_is_frozenset(self):
        assert isinstance(_BACKUP_DIR_EXACT, frozenset)

    def test_backup_dir_exact_length(self):
        assert len(_BACKUP_DIR_EXACT) == 10

    def test_backup_dir_prefixes_contents(self):
        expected = (
            "backup_",
            "backup-",
            "bak_",
            "bak-",
            "snapshot_",
            "snapshot-",
            "archive_",
            "archive-",
        )
        assert expected == _BACKUP_DIR_PREFIXES

    def test_backup_dir_prefixes_length(self):
        assert len(_BACKUP_DIR_PREFIXES) == 8

    def test_backup_dir_suffixes_contents(self):
        expected = (
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
        assert expected == _BACKUP_DIR_SUFFIXES

    def test_backup_dir_suffixes_length(self):
        assert len(_BACKUP_DIR_SUFFIXES) == 15


# ---------------------------------------------------------------------------
# is_backup_like_directory — exact-match branch
# ---------------------------------------------------------------------------


class TestExactMatch:
    """Test the exact-match branch (lowered in _BACKUP_DIR_EXACT)."""

    @pytest.mark.parametrize(
        "dirname",
        [
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
        ],
    )
    def test_exact_match_lowercase(self, dirname: str):
        assert is_backup_like_directory(dirname) is True

    @pytest.mark.parametrize(
        "dirname",
        [
            "BACKUP",
            "Backup",
            "BACKUPS",
            "BAK",
            "Bak",
            "SNAPSHOT",
            "Snapshot",
            "SNAPSHOTS",
            "ARCHIVE",
            "Archive",
            "ARCHIVES",
            "OLD_VERSIONS",
            "Old_Versions",
            "OLD-VERSION",
            "Old-Version",
            "LEGACY_BACKUP",
            "Legacy_Backup",
        ],
    )
    def test_exact_match_case_insensitive(self, dirname: str):
        assert is_backup_like_directory(dirname) is True


# ---------------------------------------------------------------------------
# is_backup_like_directory — prefix branch
# ---------------------------------------------------------------------------


class TestPrefixMatch:
    """Test the prefix-match branch."""

    @pytest.mark.parametrize(
        "dirname",
        [
            "backup_2024",
            "backup-2024",
            "bak_files",
            "bak-files",
            "snapshot_20240101",
            "snapshot-20240101",
            "archive_jan",
            "archive-jan",
        ],
    )
    def test_prefix_match_lowercase(self, dirname: str):
        assert is_backup_like_directory(dirname) is True

    @pytest.mark.parametrize(
        "dirname",
        [
            "BACKUP_2024",
            "Backup_2024",
            "BACKUP-2024",
            "Backup-2024",
            "BAK_files",
            "Bak_files",
            "BAK-files",
            "SNAPSHOT_20240101",
            "SNAPSHOT-20240101",
            "ARCHIVE_jan",
            "ARCHIVE-jan",
        ],
    )
    def test_prefix_match_case_insensitive(self, dirname: str):
        assert is_backup_like_directory(dirname) is True

    def test_prefix_exact_boundary(self):
        # "backup_" alone (prefix with nothing after it) should still match
        assert is_backup_like_directory("backup_") is True

    def test_prefix_not_just_prefix_substring(self):
        # "mybackup_stuff" should NOT match prefix — prefix must start the name
        assert is_backup_like_directory("mybackup_stuff") is False


# ---------------------------------------------------------------------------
# is_backup_like_directory — suffix branch
# ---------------------------------------------------------------------------


class TestSuffixMatch:
    """Test the suffix-match branch."""

    @pytest.mark.parametrize(
        "dirname,expected",
        [
            ("data_backup", True),
            ("data-backup", True),
            ("data.backup", True),
            ("data_bak", True),
            ("data-bak", True),
            ("data.bak", True),
            ("data_snapshot", True),
            ("data-snapshot", True),
            ("data.snapshot", True),
            ("data_archive", True),
            ("data-archive", True),
            ("data.archive", True),
            ("data_old", True),
            ("data-old", True),
            ("data.old", True),
        ],
    )
    def test_suffix_match_lowercase(self, dirname: str, expected: bool):
        assert is_backup_like_directory(dirname) is expected

    @pytest.mark.parametrize(
        "dirname",
        [
            "DATA_BACKUP",
            "Data_Backup",
            "DATA-BACKUP",
            "DATA.BACKUP",
            "DATA_BAK",
            "DATA.BAK",
            "DATA_OLD",
            "DATA-OLD",
            "DATA.OLD",
        ],
    )
    def test_suffix_match_case_insensitive(self, dirname: str):
        assert is_backup_like_directory(dirname) is True


# ---------------------------------------------------------------------------
# is_backup_like_directory — negative cases
# ---------------------------------------------------------------------------


class TestNegativeCases:
    """Inputs that should NOT be classified as backup-like."""

    @pytest.mark.parametrize(
        "dirname",
        [
            "src",
            "lib",
            "tests",
            "docs",
            "utils",
            "config",
            "models",
            "views",
            "controllers",
            "helpers",
            "data",
            "resources",
            "static",
            "templates",
            "migrations",
            "scripts",
        ],
    )
    def test_normal_directories(self, dirname: str):
        assert is_backup_like_directory(dirname) is False

    def test_substring_backup_not_matched(self):
        # "mybackup" has "backup" as substring but not exact, prefix, or suffix
        assert is_backup_like_directory("mybackup") is False

    def test_substring_bak_not_matched(self):
        assert is_backup_like_directory("mybak") is False

    def test_backupx_not_exact_match(self):
        # "backupx" is not in exact set and has no matching prefix/suffix
        assert is_backup_like_directory("backupx") is False

    def test_near_miss_prefix(self):
        # "backupp_test" starts with "backupp_" not "backup_"
        assert is_backup_like_directory("backupp_test") is False

    def test_near_miss_suffix(self):
        # "data_backupp" ends with "_backupp" not "_backup"
        assert is_backup_like_directory("data_backupp") is False

    def test_backup_as_middle_component(self):
        # "data_backup_extra" — has _backup in middle but also suffix match
        # Actually ends with "_extra" not a backup suffix; prefix doesn't match
        # Let's verify it's False
        assert is_backup_like_directory("data_backup_extra") is False


# ---------------------------------------------------------------------------
# is_backup_like_directory — empty / edge-case inputs
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for empty, whitespace, and unusual path inputs."""

    def test_empty_string(self):
        assert is_backup_like_directory("") is False

    def test_single_space(self):
        assert is_backup_like_directory(" ") is False

    def test_dot(self):
        assert is_backup_like_directory(".") is False

    def test_double_dot(self):
        assert is_backup_like_directory("..") is False

    def test_slash_only(self):
        assert is_backup_like_directory("/") is False

    def test_trailing_slash(self):
        # PurePath("/some/backup/").name returns "backup"
        assert is_backup_like_directory("/some/backup/") is True

    def test_nested_path_leaf_is_backup(self):
        # PurePath extracts the leaf "backup"
        assert is_backup_like_directory("/foo/bar/backup") is True

    def test_nested_path_leaf_is_not_backup(self):
        assert is_backup_like_directory("/foo/bar/src") is False

    def test_nested_path_parent_is_backup_leaf_is_not(self):
        # Only the leaf is checked, so parent being "backup" doesn't matter
        assert is_backup_like_directory("/backup/src") is False

    def test_nested_path_with_prefix_match_leaf(self):
        assert is_backup_like_directory("/project/backup_2024") is True

    def test_nested_path_with_suffix_match_leaf(self):
        assert is_backup_like_directory("/project/data_bak") is True

    def test_windows_style_path(self):
        # PurePath handles backslashes on all platforms via PurePath
        # On Unix PurePath treats backslash as part of the name
        # Use forward slashes to be cross-platform
        assert is_backup_like_directory("C:/Users/backup") is True

    def test_relative_path_leaf_match(self):
        assert is_backup_like_directory("../../archive") is True

    def test_relative_path_leaf_no_match(self):
        assert is_backup_like_directory("../../src") is False


# ---------------------------------------------------------------------------
# is_backup_like_directory — return type exactness
# ---------------------------------------------------------------------------


class TestReturnType:
    """Verify exact bool return type (not truthy/falsy ints or strings)."""

    def test_true_return_is_bool(self):
        result = is_backup_like_directory("backup")
        assert result is True
        assert type(result) is bool

    def test_false_return_is_bool(self):
        result = is_backup_like_directory("src")
        assert result is False
        assert type(result) is bool

    def test_empty_return_is_bool(self):
        result = is_backup_like_directory("")
        assert result is False
        assert type(result) is bool


# ---------------------------------------------------------------------------
# is_backup_like_directory — overlap/priority verification
# ---------------------------------------------------------------------------


class TestOverlapPriority:
    """Names that match multiple branches (exact + prefix, exact + suffix)."""

    def test_exact_match_also_has_prefix(self):
        # "legacy_backup" is in exact set AND ends with "_backup" suffix
        # Should return True (exact match hits first)
        assert is_backup_like_directory("legacy_backup") is True

    def test_exact_match_also_has_suffix(self):
        # "backup" is exact AND could be a suffix pattern — still True
        assert is_backup_like_directory("backup") is True

    def test_prefix_and_suffix_overlap(self):
        # "backup_backup" starts with "backup_" AND ends with "_backup"
        assert is_backup_like_directory("backup_backup") is True

    def test_all_three_overlap(self):
        # "backup" is exact, starts with "backup" prefix (only with _/-),
        # and could be suffix. Exact wins.
        assert is_backup_like_directory("backup") is True


# ---------------------------------------------------------------------------
# Exhaustive parametrize: every exact entry
# ---------------------------------------------------------------------------


class TestExhaustiveExact:
    """Ensure every single entry in _BACKUP_DIR_EXACT returns True."""

    @pytest.mark.parametrize("name", sorted(_BACKUP_DIR_EXACT))
    def test_exact_entry(self, name: str):
        assert is_backup_like_directory(name) is True


# ---------------------------------------------------------------------------
# Exhaustive parametrize: every prefix
# ---------------------------------------------------------------------------


class TestExhaustivePrefix:
    """Ensure every prefix in _BACKUP_DIR_PREFIXES matches with a suffix."""

    @pytest.mark.parametrize("prefix", _BACKUP_DIR_PREFIXES)
    def test_prefix_with_content(self, prefix: str):
        assert is_backup_like_directory(prefix + "test") is True

    @pytest.mark.parametrize("prefix", _BACKUP_DIR_PREFIXES)
    def test_prefix_alone(self, prefix: str):
        # e.g. "backup_" with nothing after — still starts with prefix
        assert is_backup_like_directory(prefix) is True


# ---------------------------------------------------------------------------
# Exhaustive parametrize: every suffix
# ---------------------------------------------------------------------------


class TestExhaustiveSuffix:
    """Ensure every suffix in _BACKUP_DIR_SUFFIXES matches with a prefix."""

    @pytest.mark.parametrize("suffix", _BACKUP_DIR_SUFFIXES)
    def test_suffix_with_content(self, suffix: str):
        assert is_backup_like_directory("data" + suffix) is True

    @pytest.mark.parametrize("suffix", _BACKUP_DIR_SUFFIXES)
    def test_suffix_alone(self, suffix: str):
        # Suffix alone might also match exact or prefix — should still be True
        assert is_backup_like_directory(suffix) is True


# ---------------------------------------------------------------------------
# Boundary: names that are close but should NOT match
# ---------------------------------------------------------------------------


class TestBoundaryNearMiss:
    """Names that are near-misses to prevent mutation survival."""

    def test_backup_without_separator_prefix(self):
        # "backupfiles" — no separator after "backup"
        assert is_backup_like_directory("backupfiles") is False

    def test_bak_without_separator_prefix(self):
        assert is_backup_like_directory("bakfiles") is False

    def test_snapshot_without_separator_prefix(self):
        assert is_backup_like_directory("snapshotfiles") is False

    def test_archive_without_separator_prefix(self):
        assert is_backup_like_directory("archivefiles") is False

    def test_no_separator_before_backup_suffix(self):
        # "databackup" — no separator before "backup"
        # Not in exact set. Check prefix: no. Check suffix: needs _/- /. before
        assert is_backup_like_directory("databackup") is False

    def test_no_separator_before_bak_suffix(self):
        assert is_backup_like_directory("databak") is False

    def test_wrong_separator_prefix(self):
        # "backup.test" — dot after backup, not in prefix list
        assert is_backup_like_directory("backup.test") is False

    def test_wrong_separator_suffix(self):
        # Check if there's a suffix that uses dot
        # ".backup" IS a valid suffix, so "data.backup" should match
        assert is_backup_like_directory("data.backup") is True
        # But "data/backup" treated as path — leaf is "backup" which is exact match
        assert is_backup_like_directory("data/backup") is True

    def test_mixed_case_near_miss(self):
        # "BackUpFiles" — case insensitive but still no match
        assert is_backup_like_directory("BackUpFiles") is False

    def test_exact_names_with_extra_char(self):
        # Each exact name + "x" should NOT match (no suffix/prefix applies)
        for name in _BACKUP_DIR_EXACT:
            augmented = name + "x"
            # Some might accidentally match a suffix — verify individually
            if not any(augmented.endswith(s) for s in _BACKUP_DIR_SUFFIXES) and not any(
                augmented.startswith(p) for p in _BACKUP_DIR_PREFIXES
            ):
                assert is_backup_like_directory(augmented) is False, (
                    f"{augmented!r} should not match"
                )
