"""Mutation-killing tests for lintgate/lint_fixer.py.

Focuses on pure functions with exact-value assertions to kill VALUE, SWAP,
BOUNDARY, and TYPE mutants. Also tests run_safe_fixes integration paths
with mocked subprocess.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.lint_fixer import (
    FixResult,
    _build_shim_ignores,
    _extract_code_lines,
    _find_venv_bin,
    _is_shim_file,
    _parse_ruff_fix_summary,
    _resolve_ruff,
    _split_diff_by_file,
    run_safe_fixes,
)

# ── _extract_code_lines ─────────────────────────────────────────────────


class TestExtractCodeLines:
    """Kill VALUE/BOUNDARY mutants in _extract_code_lines."""

    def test_empty_input(self) -> None:
        assert _extract_code_lines([]) == []

    def test_only_blank_lines(self) -> None:
        assert _extract_code_lines(["", "  ", "\n", "\t\n"]) == []

    def test_only_comments(self) -> None:
        assert _extract_code_lines(["# comment\n", "  # indented\n"]) == []

    def test_single_code_line(self) -> None:
        result = _extract_code_lines(["x = 1\n"])
        assert result == ["x = 1"]

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = _extract_code_lines(["   x = 1   \n"])
        assert result == ["x = 1"]

    def test_filters_comments_and_blanks_from_mixed(self) -> None:
        lines = [
            "# header comment\n",
            "\n",
            "import os\n",
            "  \n",
            "# another comment\n",
            "x = 1\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["import os", "x = 1"]

    def test_skips_triple_double_quote_docstrings(self) -> None:
        lines = [
            '"""Module docstring."""\n',
            "x = 1\n",
        ]
        result = _extract_code_lines(lines)
        # The docstring line has 2 occurrences of """ (even count), so
        # in_docstring toggles off, and the line is skipped via continue.
        assert result == ["x = 1"]

    def test_skips_multiline_triple_double_quote_docstring(self) -> None:
        lines = [
            '"""Start of docstring\n',
            "middle line\n",
            'end of docstring"""\n',
            "y = 2\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["y = 2"]

    def test_skips_triple_single_quote_docstrings(self) -> None:
        lines = [
            "'''Single-quote docstring.'''\n",
            "z = 3\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["z = 3"]

    def test_skips_multiline_triple_single_quote_docstring(self) -> None:
        lines = [
            "'''Start\n",
            "middle\n",
            "end'''\n",
            "a = 4\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["a = 4"]

    def test_content_between_docstrings_preserved(self) -> None:
        lines = [
            '"""doc1"""\n',
            "x = 1\n",
            '"""doc2"""\n',
            "y = 2\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["x = 1", "y = 2"]

    def test_code_after_multiline_docstring(self) -> None:
        lines = [
            '"""Start\n',
            "inside docstring\n",
            '"""\n',
            "real_code = True\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["real_code = True"]

    def test_interleaved_comments_and_code(self) -> None:
        lines = [
            "from os import path\n",
            "# comment\n",
            "import sys\n",
        ]
        result = _extract_code_lines(lines)
        assert result == ["from os import path", "import sys"]

    def test_returns_list_type(self) -> None:
        result = _extract_code_lines(["x = 1\n"])
        assert isinstance(result, list)

    def test_count_exact_for_mixed_input(self) -> None:
        lines = [
            "# comment\n",
            '"""docstring"""\n',
            "\n",
            "a = 1\n",
            "b = 2\n",
            "c = 3\n",
        ]
        result = _extract_code_lines(lines)
        assert len(result) == 3
        assert result[0] == "a = 1"
        assert result[1] == "b = 2"
        assert result[2] == "c = 3"


# ── _is_shim_file ───────────────────────────────────────────────────────


class TestIsShimFile:
    """Kill VALUE/BOUNDARY mutants in _is_shim_file detection logic."""

    def test_returns_false_for_nonexistent_file(self) -> None:
        assert _is_shim_file("/nonexistent/path/to/module.py") is False

    def test_explicit_marker_in_first_line(self, tmp_path: Path) -> None:
        f = tmp_path / "shim.py"
        f.write_text("# lintgate: shim\nfrom .a import X\n")
        assert _is_shim_file(str(f)) is True

    def test_explicit_marker_at_line_10(self, tmp_path: Path) -> None:
        """Marker at exactly line 10 (index 9) is within the first 10 lines."""
        lines = ["# padding\n"] * 9 + ["# lintgate: shim\n"]
        f = tmp_path / "shim.py"
        f.write_text("".join(lines))
        assert _is_shim_file(str(f)) is True

    def test_explicit_marker_at_line_11_not_detected(self, tmp_path: Path) -> None:
        """Marker at line 11 (index 10) is outside the first 10 lines slice."""
        lines = ["# padding\n"] * 10 + ["# lintgate: shim\n"]
        f = tmp_path / "not_shim.py"
        f.write_text("".join(lines))
        # It might still be detected by other heuristics, so we test the
        # exact boundary: 10 padding + marker = 11 lines. No noqa, no __all__,
        # no reexports => not a shim.
        assert _is_shim_file(str(f)) is False

    def test_noqa_f401_threshold_at_3(self, tmp_path: Path) -> None:
        """Exactly 3 noqa:F401 annotations should be detected as shim."""
        content = (
            "from .a import X  # noqa: F401\n"
            "from .b import Y  # noqa: F401\n"
            "from .c import Z  # noqa: F401\n"
        )
        f = tmp_path / "shim.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is True

    def test_noqa_f401_below_threshold(self, tmp_path: Path) -> None:
        """Only 2 noqa:F401 annotations: not enough for noqa-based detection."""
        content = (
            "from .a import X  # noqa: F401\nfrom .b import Y  # noqa: F401\nx = 1\ny = 2\nz = 3\n"
        )
        f = tmp_path / "shim.py"
        f.write_text(content)
        # 2 noqa (< 3 threshold), no __all__, 2 from-imports out of 5 code
        # lines = 40% (< 50% threshold) => not a shim
        assert _is_shim_file(str(f)) is False

    def test_all_with_two_from_imports(self, tmp_path: Path) -> None:
        """__all__ + >=2 from-imports => shim."""
        content = '__all__ = ["X", "Y"]\nfrom .a import X\nfrom .b import Y\n'
        f = tmp_path / "shim.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is True

    def test_all_with_only_one_from_import(self, tmp_path: Path) -> None:
        """__all__ + only 1 from-import: not enough for __all__-based detection."""
        content = '__all__ = ["X"]\nfrom .a import X\nx = 1\ny = 2\n'
        f = tmp_path / "shim.py"
        f.write_text(content)
        # 1 reexport out of 4 code lines = 25% (< 50%) => not a shim
        assert _is_shim_file(str(f)) is False

    def test_heuristic_over_50_percent_reexports(self, tmp_path: Path) -> None:
        """More than 50% from-import lines => shim by heuristic."""
        content = "from .a import X\nfrom .b import Y\nfrom .c import Z\nx = 1\n"
        f = tmp_path / "shim.py"
        f.write_text(content)
        # 3 from-imports out of 4 code lines = 75% > 50%
        assert _is_shim_file(str(f)) is True

    def test_heuristic_exactly_50_percent_not_shim(self, tmp_path: Path) -> None:
        """Exactly 50% is NOT > 0.5, so not a shim by heuristic."""
        content = "from .a import X\nx = 1\n"
        f = tmp_path / "shim.py"
        f.write_text(content)
        # 1 from-import out of 2 code lines = 50% (not > 50%)
        assert _is_shim_file(str(f)) is False

    def test_less_than_2_code_lines_with_all(self, tmp_path: Path) -> None:
        """<2 code lines + __all__ => shim (returns has_all_definition)."""
        content = '"""Module docstring."""\n__all__ = []\n'
        f = tmp_path / "shim.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is True

    def test_less_than_2_code_lines_without_all(self, tmp_path: Path) -> None:
        """<2 code lines, no __all__ => not a shim."""
        content = '"""Module docstring."""\nx = 1\n'
        f = tmp_path / "not_shim.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is False

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("")
        # 0 code lines < 2, has_all_definition=False => False
        assert _is_shim_file(str(f)) is False

    def test_all_requires_equals_sign(self, tmp_path: Path) -> None:
        """__all__ line without '=' should not count."""
        content = "# __all__ is not assigned here\nfrom .a import X\nfrom .b import Y\nx = 1\n"
        f = tmp_path / "module.py"
        f.write_text(content)
        # No __all__ (comment doesn't count), 2/3 = 66% > 50% => shim by heuristic
        assert _is_shim_file(str(f)) is True

    def test_shim_marker_constant_value(self) -> None:
        """The _SHIM_MARKER must be exactly '# lintgate: shim'."""
        from lintgate.lint_fixer import _SHIM_MARKER

        assert _SHIM_MARKER == "# lintgate: shim"

    def test_return_type_is_bool(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        result = _is_shim_file(str(f))
        assert isinstance(result, bool)


# ── _build_shim_ignores ─────────────────────────────────────────────────


class TestBuildShimIgnores:
    """Kill VALUE mutants in _build_shim_ignores output."""

    def test_empty_files(self) -> None:
        assert _build_shim_ignores([]) == []

    def test_no_shim_files(self, tmp_path: Path) -> None:
        f = tmp_path / "regular.py"
        f.write_text("x = 1\n")
        assert _build_shim_ignores([str(f)]) == []

    def test_shim_file_produces_exact_args(self, tmp_path: Path) -> None:
        f = tmp_path / "shim.py"
        f.write_text("# lintgate: shim\nfrom .a import X\n")
        result = _build_shim_ignores([str(f)])
        assert len(result) == 2
        assert result[0] == "--extend-per-file-ignores"
        assert result[1] == f"{f}:F401"

    def test_multiple_shim_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "shim1.py"
        f1.write_text("# lintgate: shim\nfrom .a import X\n")
        f2 = tmp_path / "shim2.py"
        f2.write_text("# lintgate: shim\nfrom .b import Y\n")
        result = _build_shim_ignores([str(f1), str(f2)])
        assert len(result) == 4
        assert result[0] == "--extend-per-file-ignores"
        assert result[1] == f"{f1}:F401"
        assert result[2] == "--extend-per-file-ignores"
        assert result[3] == f"{f2}:F401"

    def test_mixed_shim_and_regular(self, tmp_path: Path) -> None:
        shim = tmp_path / "shim.py"
        shim.write_text("# lintgate: shim\nfrom .a import X\n")
        regular = tmp_path / "regular.py"
        regular.write_text("x = 1\n")
        result = _build_shim_ignores([str(shim), str(regular)])
        assert len(result) == 2
        assert result[0] == "--extend-per-file-ignores"
        assert ":F401" in result[1]

    def test_ignore_format_uses_colon_f401(self, tmp_path: Path) -> None:
        """The ignore pattern must end with exactly ':F401'."""
        f = tmp_path / "shim.py"
        f.write_text("# lintgate: shim\nfrom .a import X\n")
        result = _build_shim_ignores([str(f)])
        assert result[1].endswith(":F401")


# ── FixResult.to_dict ────────────────────────────────────────────────────


class TestFixResultToDict:
    """Kill VALUE/TYPE mutants in FixResult.to_dict field logic."""

    def test_default_to_dict_keys(self) -> None:
        result = FixResult()
        d = result.to_dict()
        assert set(d.keys()) == {"files_modified", "changes", "dry_run"}

    def test_files_modified_is_count_not_list(self) -> None:
        result = FixResult(files_modified=["a.py", "b.py"])
        d = result.to_dict()
        assert d["files_modified"] == 2
        assert isinstance(d["files_modified"], int)

    def test_changes_passed_through(self) -> None:
        changes = [{"action": "ruff_fix", "detail": "Fixed 1 error"}]
        result = FixResult(changes=changes)
        d = result.to_dict()
        assert d["changes"] is changes

    def test_dry_run_true(self) -> None:
        result = FixResult(dry_run=True)
        d = result.to_dict()
        assert d["dry_run"] is True

    def test_dry_run_false(self) -> None:
        result = FixResult(dry_run=False)
        d = result.to_dict()
        assert d["dry_run"] is False

    def test_diff_preview_included_when_nonempty(self) -> None:
        result = FixResult(diff_preview="some diff")
        d = result.to_dict()
        assert d["diff_preview"] == "some diff"

    def test_diff_preview_excluded_when_empty(self) -> None:
        result = FixResult(diff_preview="")
        d = result.to_dict()
        assert "diff_preview" not in d

    def test_file_diffs_included_when_nonempty(self) -> None:
        diffs = [{"file": "a.py", "diff": "---"}]
        result = FixResult(file_diffs=diffs)
        d = result.to_dict()
        assert d["file_diffs"] is diffs

    def test_file_diffs_excluded_when_empty(self) -> None:
        result = FixResult(file_diffs=[])
        d = result.to_dict()
        assert "file_diffs" not in d

    def test_errors_included_when_nonempty(self) -> None:
        result = FixResult(errors=["ruff not found"])
        d = result.to_dict()
        assert d["errors"] == ["ruff not found"]

    def test_errors_excluded_when_empty(self) -> None:
        result = FixResult(errors=[])
        d = result.to_dict()
        assert "errors" not in d

    def test_files_modified_list_included_when_not_dry_run(self) -> None:
        result = FixResult(dry_run=False, files_modified=["a.py", "b.py"])
        d = result.to_dict()
        assert d["files_modified_list"] == ["a.py", "b.py"]

    def test_files_modified_list_excluded_when_dry_run(self) -> None:
        result = FixResult(dry_run=True, files_modified=["a.py"])
        d = result.to_dict()
        assert "files_modified_list" not in d

    def test_all_keys_present_when_all_fields_populated(self) -> None:
        result = FixResult(
            files_modified=["a.py"],
            changes=[{"action": "fix"}],
            diff_preview="diff here",
            file_diffs=[{"file": "a.py", "diff": "---"}],
            dry_run=False,
            errors=["warning"],
        )
        d = result.to_dict()
        expected_keys = {
            "files_modified",
            "changes",
            "dry_run",
            "diff_preview",
            "file_diffs",
            "errors",
            "files_modified_list",
        }
        assert set(d.keys()) == expected_keys

    def test_files_modified_count_zero(self) -> None:
        result = FixResult(files_modified=[])
        d = result.to_dict()
        assert d["files_modified"] == 0

    def test_return_type_is_dict(self) -> None:
        result = FixResult()
        d = result.to_dict()
        assert isinstance(d, dict)


# ── _find_venv_bin ───────────────────────────────────────────────────────


class TestFindVenvBinMutationKilling:
    """Kill SWAP/VALUE mutants in venv name priority order."""

    def test_returns_none_for_empty_dir(self, tmp_path: Path) -> None:
        assert _find_venv_bin(str(tmp_path)) is None

    def test_dot_venv_preferred_first(self, tmp_path: Path) -> None:
        for name in (".venv", "venv", "env"):
            (tmp_path / name / "bin").mkdir(parents=True)
        result = _find_venv_bin(str(tmp_path))
        assert result == str(tmp_path / ".venv" / "bin")

    def test_venv_preferred_over_env(self, tmp_path: Path) -> None:
        for name in ("venv", "env"):
            (tmp_path / name / "bin").mkdir(parents=True)
        result = _find_venv_bin(str(tmp_path))
        assert result == str(tmp_path / "venv" / "bin")

    def test_env_used_as_last_resort(self, tmp_path: Path) -> None:
        (tmp_path / "env" / "bin").mkdir(parents=True)
        result = _find_venv_bin(str(tmp_path))
        assert result == str(tmp_path / "env" / "bin")

    def test_requires_bin_subdirectory(self, tmp_path: Path) -> None:
        """A venv dir without a 'bin' subdir is not detected."""
        (tmp_path / ".venv").mkdir()
        assert _find_venv_bin(str(tmp_path)) is None

    def test_exact_venv_names_checked(self, tmp_path: Path) -> None:
        """Only .venv, venv, env are checked; other names are ignored."""
        (tmp_path / "myenv" / "bin").mkdir(parents=True)
        (tmp_path / ".env" / "bin").mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) is None

    def test_return_is_string(self, tmp_path: Path) -> None:
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        result = _find_venv_bin(str(tmp_path))
        assert isinstance(result, str)


# ── _resolve_ruff ────────────────────────────────────────────────────────


class TestResolveRuffMutationKilling:
    """Kill SWAP/VALUE mutants in ruff resolution priority."""

    def test_prefers_venv_ruff_over_system(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        ruff_bin = venv_bin / "ruff"
        ruff_bin.touch()

        with patch("shutil.which", return_value="/usr/bin/ruff"):
            result = _resolve_ruff(str(tmp_path))
        assert result == str(ruff_bin)

    def test_falls_back_to_which_when_no_venv(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value="/usr/bin/ruff") as mock_which:
            result = _resolve_ruff(str(tmp_path))
        assert result == "/usr/bin/ruff"
        mock_which.assert_called_once_with("ruff")

    def test_falls_back_to_which_when_venv_has_no_ruff(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        # No ruff binary in venv
        with patch("shutil.which", return_value="/global/ruff"):
            result = _resolve_ruff(str(tmp_path))
        assert result == "/global/ruff"

    def test_returns_none_when_ruff_not_found_anywhere(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            result = _resolve_ruff(str(tmp_path))
        assert result is None

    def test_venv_ruff_path_exact(self, tmp_path: Path) -> None:
        """The returned path must be exactly venv_bin / 'ruff'."""
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        ruff = venv_bin / "ruff"
        ruff.touch()
        result = _resolve_ruff(str(tmp_path))
        assert result == str(venv_bin / "ruff")
        # Not just any file in venv bin
        assert result.endswith("/ruff")


# ── _split_diff_by_file ─────────────────────────────────────────────────


class TestSplitDiffByFileMutationKilling:
    """Kill VALUE/BOUNDARY mutants in diff splitting logic."""

    def test_empty_string_returns_empty_list(self) -> None:
        assert _split_diff_by_file("") == []

    def test_no_markers_returns_empty_list(self) -> None:
        assert _split_diff_by_file("random text\nno diff markers\n") == []

    def test_single_file_exact_keys(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        segments = _split_diff_by_file(diff)
        assert len(segments) == 1
        assert set(segments[0].keys()) == {"file", "diff"}

    def test_single_file_exact_filename(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "foo.py"

    def test_strips_a_slash_prefix(self) -> None:
        diff = "--- a/src/module.py\n+++ b/src/module.py\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "src/module.py"

    def test_no_a_slash_prefix(self) -> None:
        diff = "--- foo.py\n+++ foo.py\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "foo.py"

    def test_strips_tab_timestamp(self) -> None:
        diff = "--- a/foo.py\t2024-01-01 12:00:00\n+++ b/foo.py\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "foo.py"

    def test_diff_content_starts_with_triple_dash(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["diff"].startswith("--- a/foo.py")

    def test_diff_content_includes_hunks(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        segments = _split_diff_by_file(diff)
        assert "@@ -1 +1 @@" in segments[0]["diff"]
        assert "-old" in segments[0]["diff"]
        assert "+new" in segments[0]["diff"]

    def test_two_files_split_correctly(self) -> None:
        diff = (
            "--- a/first.py\n"
            "+++ b/first.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "--- a/second.py\n"
            "+++ b/second.py\n"
            "@@ -1 +1 @@\n"
            "-c\n"
            "+d\n"
        )
        segments = _split_diff_by_file(diff)
        assert len(segments) == 2
        assert segments[0]["file"] == "first.py"
        assert segments[1]["file"] == "second.py"

    def test_first_segment_excludes_second_file_content(self) -> None:
        diff = "--- a/first.py\n+++ b/first.py\n-a\n+b\n--- a/second.py\n+++ b/second.py\n-c\n+d\n"
        segments = _split_diff_by_file(diff)
        assert "-c" not in segments[0]["diff"]
        assert "+d" not in segments[0]["diff"]

    def test_second_segment_excludes_first_file_content(self) -> None:
        diff = "--- a/first.py\n+++ b/first.py\n-a\n+b\n--- a/second.py\n+++ b/second.py\n-c\n+d\n"
        segments = _split_diff_by_file(diff)
        assert "-a" not in segments[1]["diff"]

    def test_three_files(self) -> None:
        diff = (
            "--- a/a.py\n+++ b/a.py\n-1\n--- a/b.py\n+++ b/b.py\n-2\n--- a/c.py\n+++ b/c.py\n-3\n"
        )
        segments = _split_diff_by_file(diff)
        assert len(segments) == 3
        assert [s["file"] for s in segments] == ["a.py", "b.py", "c.py"]

    def test_lines_before_first_marker_in_first_segment(self) -> None:
        diff = "header line\n--- a/foo.py\n+++ b/foo.py\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert len(segments) == 1
        # The header line should NOT be in the diff (it's before the --- marker)
        # Actually, the code captures it in current_lines before a file is set,
        # and then when --- is hit, current_lines is ["header line"], but
        # current_file is None, so it's not appended. Then the --- line starts
        # a new segment.
        assert segments[0]["file"] == "foo.py"

    def test_prefix_stripping_exact_boundary(self) -> None:
        """Exactly 'a/' is stripped, not 'a' alone."""
        diff = "--- a/file.py\n+++ b/file.py\n-x\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "file.py"
        # Verify the 4-char skip: "--- " (line[4:]) gives "a/file.py"
        # Then "a/" (raw[2:]) gives "file.py"

    def test_raw_extraction_starts_at_index_4(self) -> None:
        """The raw filename is extracted starting at index 4 of the --- line."""
        diff = "--- abcdef.py\n+++ b/x.py\n-x\n"
        segments = _split_diff_by_file(diff)
        # line[4:] = "abcdef.py", doesn't start with "a/" so no further strip
        assert segments[0]["file"] == "abcdef.py"


# ── _parse_ruff_fix_summary ─────────────────────────────────────────────


class TestParseRuffFixSummary:
    """Kill VALUE/BOUNDARY mutants in ruff output parsing."""

    def test_found_n_errors_pattern(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Found 4 errors (3 fixed, 1 remaining).\n", result)
        assert len(result.changes) == 1
        assert result.changes[0]["action"] == "ruff_fix"
        assert "Found 4 errors (3 fixed, 1 remaining)." in result.changes[0]["detail"]

    def test_fixed_n_errors_pattern(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Fixed 3 errors [F401, F841, W291].\n", result)
        assert len(result.changes) == 1
        assert result.changes[0]["action"] == "ruff_fix"
        assert "Fixed 3 errors" in result.changes[0]["detail"]

    def test_zero_fixed_is_ignored(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Found 4 errors (0 fixed, 4 remaining).\n", result)
        assert len(result.changes) == 0

    def test_zero_fixed_errors_pattern_ignored(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Fixed 0 errors.\n", result)
        assert len(result.changes) == 0

    def test_custom_action_name(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Fixed 1 error [I001].\n", result, action="import_sort")
        assert len(result.changes) == 1
        assert result.changes[0]["action"] == "import_sort"

    def test_default_action_is_ruff_fix(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Fixed 1 error.\n", result)
        assert result.changes[0]["action"] == "ruff_fix"

    def test_detail_truncated_to_120_chars(self) -> None:
        long_line = "Fixed 1 error " + "x" * 200 + ".\n"
        result = FixResult()
        _parse_ruff_fix_summary(long_line, result)
        assert len(result.changes) == 1
        assert len(result.changes[0]["detail"]) <= 120

    def test_empty_stdout(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("", result)
        assert len(result.changes) == 0

    def test_no_matching_lines(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("All checks passed!\n", result)
        assert len(result.changes) == 0

    def test_multiline_picks_first_match(self) -> None:
        stdout = (
            "Checking 5 files...\n"
            "Found 4 errors (3 fixed, 1 remaining).\n"
            "Fixed 2 errors [F401, F841].\n"
        )
        result = FixResult()
        _parse_ruff_fix_summary(stdout, result)
        # Should return after first match
        assert len(result.changes) == 1
        assert "Found 4 errors" in result.changes[0]["detail"]

    def test_found_1_error_singular(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Found 1 error (1 fixed, 0 remaining).\n", result)
        assert len(result.changes) == 1
        assert "Found 1 error" in result.changes[0]["detail"]

    def test_fixed_1_error_singular(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Fixed 1 error [F401].\n", result)
        assert len(result.changes) == 1

    def test_whitespace_stripped_from_lines(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("  Fixed 2 errors [F401, F841].  \n", result)
        assert len(result.changes) == 1

    def test_dict_keys_exact(self) -> None:
        result = FixResult()
        _parse_ruff_fix_summary("Fixed 1 error.\n", result)
        assert set(result.changes[0].keys()) == {"action", "detail"}

    def test_does_not_mutate_existing_changes(self) -> None:
        existing = {"action": "previous", "detail": "old"}
        result = FixResult(changes=[existing])
        _parse_ruff_fix_summary("Fixed 1 error.\n", result)
        assert len(result.changes) == 2
        assert result.changes[0] is existing


# ── FixResult dataclass defaults ─────────────────────────────────────────


class TestFixResultDefaults:
    """Kill VALUE mutants in FixResult default field values."""

    def test_default_files_modified(self) -> None:
        r = FixResult()
        assert r.files_modified == []

    def test_default_changes(self) -> None:
        r = FixResult()
        assert r.changes == []

    def test_default_diff_preview(self) -> None:
        r = FixResult()
        assert r.diff_preview == ""

    def test_default_file_diffs(self) -> None:
        r = FixResult()
        assert r.file_diffs == []

    def test_default_dry_run(self) -> None:
        r = FixResult()
        assert r.dry_run is True

    def test_default_errors(self) -> None:
        r = FixResult()
        assert r.errors == []

    def test_fields_are_independent_instances(self) -> None:
        """Each FixResult gets its own list instances (factory, not shared)."""
        r1 = FixResult()
        r2 = FixResult()
        r1.files_modified.append("a.py")
        assert r2.files_modified == []
        r1.changes.append({"x": 1})
        assert r2.changes == []
        r1.errors.append("err")
        assert r2.errors == []


# ── run_safe_fixes: dry_run with diff output ─────────────────────────────


class TestRunSafeFixesDryRunDiffParsing:
    def _make_diff_output(self) -> str:
        """Build a realistic unified diff for testing."""
        return "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,2 @@\n-import os\n-import sys\n+import sys\n"

    def test_dry_run_parses_diff_into_changes(self, tmp_path: Path) -> None:
        """Diff is split per-file and additions/deletions counted."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\nimport sys\n")

        diff_text = self._make_diff_output()

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "check" in cmd and "--diff" in cmd and "--select" not in cmd:
                mock.stdout = diff_text
            else:
                mock.stdout = ""
            return mock

        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=str(venv_bin)),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
            )

        assert result.diff_preview  # non-empty
        assert len(result.file_diffs) >= 1
        assert result.file_diffs[0]["file"] == "foo.py"
        assert len(result.changes) >= 1

        change = result.changes[0]
        assert change["action"] == "preview"
        assert change["file"] == "foo.py"
        assert change["additions"] >= 0
        assert change["deletions"] >= 0
        assert isinstance(change["sample"], list)

    def test_dry_run_with_venv_path_augmentation(self, tmp_path: Path) -> None:
        """PATH is augmented with venv_bin."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x = 1\n")

        captured_envs: list[dict] = []

        def mock_run(cmd, *args, **kwargs):
            if "env" in kwargs:
                captured_envs.append(kwargs["env"])
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("subprocess.run", side_effect=mock_run),
        ):
            run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
            )

        assert len(captured_envs) > 0
        assert captured_envs[0]["PATH"].startswith(str(venv_bin))


# ── _preview_fixes: timeout/OSError paths ────────────────────────────────


class TestPreviewFixesErrorHandling:
    def test_ruff_check_timeout_is_silenced(self, tmp_path: Path) -> None:
        """TimeoutExpired during ruff check --diff is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x = 1\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--diff" in cmd and "--select" not in cmd:
                raise subprocess.TimeoutExpired(cmd, 30)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
            )
        assert result.errors == []

    def test_ruff_check_oserror_is_silenced(self, tmp_path: Path) -> None:
        """OSError during ruff check --diff is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x = 1\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--diff" in cmd and "--select" not in cmd:
                raise OSError("binary not found")
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
            )
        assert result.errors == []

    def test_import_sort_diff_is_captured(self, tmp_path: Path) -> None:
        """Import sort diff output is included in preview."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import sys\nimport os\n")

        isort_diff = (
            "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n-import sys\n import os\n+import sys\n"
        )

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "check" in cmd and "--select" in cmd and "I" in cmd and "--diff" in cmd:
                mock.stdout = isort_diff
            else:
                mock.stdout = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
                fix_imports=True,
            )

        assert "import sort diff" in result.diff_preview

    def test_import_sort_timeout_is_silenced(self, tmp_path: Path) -> None:
        """TimeoutExpired during import sort --diff is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x = 1\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--select" in cmd:
                raise subprocess.TimeoutExpired(cmd, 15)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
                fix_imports=True,
            )
        assert result.errors == []

    def test_format_diff_is_captured(self, tmp_path: Path) -> None:
        """Ruff format --diff output is included in preview."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x=1\n")

        format_diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x=1\n+x = 1\n"

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "format" in cmd and "--diff" in cmd:
                mock.stdout = format_diff
            else:
                mock.stdout = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
            )

        assert "ruff format diff" in result.diff_preview

    def test_format_diff_timeout_is_silenced(self, tmp_path: Path) -> None:
        """TimeoutExpired during ruff format --diff is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x = 1\n")

        def mock_run(cmd, *args, **kwargs):
            if "format" in cmd:
                raise subprocess.TimeoutExpired(cmd, 15)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
            )
        assert result.errors == []


# ── _apply_ruff_fix: unsafe flag, timeout, OSError ───────────────────────


class TestApplyRuffFixErrors:
    def test_apply_unsafe_flag_passed(self, tmp_path: Path) -> None:
        """--unsafe-fixes is passed in apply mode when safe_only=False."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\n")

        captured_cmds: list[list[str]] = []

        def mock_run(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
                safe_only=False,
            )

        ruff_check_cmds = [c for c in captured_cmds if "check" in c and "--fix" in c]
        has_unsafe = any("--unsafe-fixes" in c for c in ruff_check_cmds)
        assert has_unsafe, f"--unsafe-fixes not found in apply cmds: {ruff_check_cmds}"

    def test_apply_ruff_fix_timeout(self, tmp_path: Path) -> None:
        """TimeoutExpired during ruff check --fix is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--fix" in cmd and "--select" not in cmd:
                raise subprocess.TimeoutExpired(cmd, 30)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
            )

        assert any("timed out" in e for e in result.errors)

    def test_apply_ruff_fix_oserror(self, tmp_path: Path) -> None:
        """OSError during ruff check --fix is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--fix" in cmd and "--select" not in cmd:
                raise OSError("Permission denied")
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
            )

        assert any("failed" in e for e in result.errors)


# ── _apply_import_sort: timeout/OSError ──────────────────────────────────


class TestApplyImportSortErrors:
    def test_import_sort_timeout_is_silenced(self, tmp_path: Path) -> None:
        """TimeoutExpired during import sort --fix is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--select" in cmd and "--fix" in cmd:
                raise subprocess.TimeoutExpired(cmd, 15)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
                fix_imports=True,
            )

        assert not any("import" in e.lower() for e in result.errors)

    def test_import_sort_oserror_is_silenced(self, tmp_path: Path) -> None:
        """OSError during import sort --fix is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\n")

        def mock_run(cmd, *args, **kwargs):
            if "check" in cmd and "--select" in cmd and "--fix" in cmd:
                raise OSError("bad binary")
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
                fix_imports=True,
            )

        assert not any("import" in e.lower() for e in result.errors)


# ── _apply_ruff_format: reformatted parsing ──────────────────────────────


class TestApplyRuffFormat:
    def test_format_captures_reformatted_stderr(self, tmp_path: Path) -> None:
        """Parses '1 file reformatted' from stderr."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x=1\n")

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            if "format" in cmd and "--diff" not in cmd:
                mock.stderr = "1 file reformatted\n"
            else:
                mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
            )

        format_changes = [c for c in result.changes if c["action"] == "format"]
        assert len(format_changes) >= 1
        assert "reformatted" in format_changes[0]["detail"].lower()

    def test_format_timeout_is_silenced(self, tmp_path: Path) -> None:
        """TimeoutExpired during ruff format is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("x=1\n")

        def mock_run(cmd, *args, **kwargs):
            if "format" in cmd and "--diff" not in cmd:
                raise subprocess.TimeoutExpired(cmd, 15)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
            )

        assert not any("format" in e.lower() for e in result.errors)


# ── Integration: dry_run end-to-end with all diff sources ────────────────


class TestDryRunIntegration:
    def test_all_three_diff_sources(self, tmp_path: Path) -> None:
        """All three diff sources contribute to output."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import sys\nimport os\nx=1\n")

        check_diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,1 @@\n-import os\n import sys\n"
        isort_diff = (
            "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n-import sys\n import os\n+import sys\n"
        )
        format_diff = "--- a/foo.py\n+++ b/foo.py\n@@ -3 +3 @@\n-x=1\n+x = 1\n"

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "check" in cmd and "--diff" in cmd and "--select" not in cmd:
                mock.stdout = check_diff
            elif "check" in cmd and "--select" in cmd and "--diff" in cmd:
                mock.stdout = isort_diff
            elif "format" in cmd and "--diff" in cmd:
                mock.stdout = format_diff
            else:
                mock.stdout = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("lintgate.lint_fixer._find_venv_bin", return_value=None),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
                fix_imports=True,
            )

        assert "ruff check --fix diff" in result.diff_preview
        assert "import sort diff" in result.diff_preview
        assert "ruff format diff" in result.diff_preview
        assert len(result.file_diffs) >= 1
        assert len(result.changes) >= 1
        for change in result.changes:
            assert change["action"] == "preview"
            assert "additions" in change
            assert "deletions" in change
            assert "sample" in change


# ── SecretChecker linter ─────────────────────────────────────────────────


class TestSecretChecker:
    @staticmethod
    def _ctx(tmp_path):
        from lintgate.types import LinterContext

        return LinterContext(
            files=[],
            project_root=str(tmp_path),
            config={},
        )

    def test_metadata(self) -> None:
        from lintgate.linters.secret_checker import SecretChecker

        linter = SecretChecker()
        assert linter.name == "secret_checker"
        assert linter.tier == 2
        assert linter.required_tool is None

    def test_detects_high_confidence_token(self, tmp_path: Path) -> None:
        from lintgate.linters.secret_checker import SecretChecker

        p = tmp_path / "a.py"
        p.write_text('TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"\n')
        ctx = self._ctx(tmp_path)
        ctx.files = [str(p)]

        issues = list(SecretChecker().run(ctx))
        assert len(issues) == 1
        issue = issues[0]
        assert issue.kind == "github_token"
        assert issue.severity == "warning"
        assert issue.file == str(p)
        assert issue.line == 1
        assert "***" in issue.message

    def test_detects_private_key_blocking(self, tmp_path: Path) -> None:
        from lintgate.linters.secret_checker import SecretChecker

        p = tmp_path / "k.py"
        p.write_text("key = '''-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----'''\n")  # noqa: S105  # NOSONAR — test fixture for secret detection
        ctx = self._ctx(tmp_path)
        ctx.files = [str(p)]

        issues = list(SecretChecker().run(ctx))
        assert len(issues) == 1
        assert issues[0].kind == "private_key"
        assert issues[0].severity == "blocking"

    def test_ignores_placeholder_values(self, tmp_path: Path) -> None:
        from lintgate.linters.secret_checker import SecretChecker

        p = tmp_path / "placeholder.py"
        p.write_text('API_KEY = "replace_me_with_real_token"\n')
        ctx = self._ctx(tmp_path)
        ctx.files = [str(p)]

        issues = list(SecretChecker().run(ctx))
        assert issues == []
