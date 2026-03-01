"""Additional coverage tests for lint_fixer module.

Covers uncovered lines in:
- FixResult.to_dict (file_diffs branch)
- _find_venv_bin (successful venv discovery)
- _resolve_ruff (venv ruff discovery and PATH fallback)
- run_safe_fixes dry_run path with actual diff parsing
- _preview_fixes (timeout/OSError handling, import sort, format diffs)
- _apply_ruff_fix (timeout and OSError handling, unsafe flag in apply mode)
- _apply_import_sort (timeout/OSError handling)
- _apply_ruff_format (reformatted output parsing)
- _split_diff_by_file (full function)
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.lint_fixer import (
    FixResult,
    _find_venv_bin,
    _resolve_ruff,
    _split_diff_by_file,
    run_safe_fixes,
)

# ── FixResult.to_dict: file_diffs branch (line 40) ──────────────────────


class TestFixResultFileDiffs:
    def test_to_dict_includes_file_diffs_when_present(self) -> None:
        """Line 40: file_diffs should appear in dict when non-empty."""
        result = FixResult(
            dry_run=True,
            diff_preview="some diff",
            file_diffs=[{"file": "a.py", "diff": "--- a.py\n+++ a.py\n-old\n+new"}],
        )
        d = result.to_dict()
        assert "file_diffs" in d
        assert len(d["file_diffs"]) == 1
        assert d["file_diffs"][0]["file"] == "a.py"

    def test_to_dict_omits_file_diffs_when_empty(self) -> None:
        result = FixResult(dry_run=True, diff_preview="some diff", file_diffs=[])
        d = result.to_dict()
        assert "file_diffs" not in d


# ── _find_venv_bin (line 53) ─────────────────────────────────────────────


class TestFindVenvBin:
    def test_finds_dot_venv_bin(self, tmp_path: Path) -> None:
        """Line 53: returns bin dir when .venv/bin exists."""
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(venv_bin)

    def test_finds_venv_bin(self, tmp_path: Path) -> None:
        """Finds 'venv/bin' when .venv doesn't exist."""
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(venv_bin)

    def test_finds_env_bin(self, tmp_path: Path) -> None:
        """Finds 'env/bin' when .venv and venv don't exist."""
        env_bin = tmp_path / "env" / "bin"
        env_bin.mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(env_bin)

    def test_prefers_dot_venv_over_venv(self, tmp_path: Path) -> None:
        """Priority: .venv > venv > env."""
        for name in (".venv", "venv"):
            (tmp_path / name / "bin").mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(tmp_path / ".venv" / "bin")

    def test_returns_none_when_no_venv(self, tmp_path: Path) -> None:
        assert _find_venv_bin(str(tmp_path)) is None


# ── _resolve_ruff (lines 59-64) ─────────────────────────────────────────


class TestResolveRuff:
    def test_finds_ruff_in_venv(self, tmp_path: Path) -> None:
        """Lines 59-63: finds ruff binary inside venv bin."""
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        ruff_bin = venv_bin / "ruff"
        ruff_bin.touch()
        result = _resolve_ruff(str(tmp_path))
        assert result == str(ruff_bin)

    def test_falls_back_to_shutil_which(self, tmp_path: Path) -> None:
        """Line 64: falls back to shutil.which when no venv ruff."""
        with patch("shutil.which", return_value="/usr/local/bin/ruff"):
            result = _resolve_ruff(str(tmp_path))
        assert result == "/usr/local/bin/ruff"

    def test_venv_exists_but_no_ruff_binary(self, tmp_path: Path) -> None:
        """Venv bin exists but ruff is not installed in it."""
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        with patch("shutil.which", return_value="/global/ruff"):
            result = _resolve_ruff(str(tmp_path))
        assert result == "/global/ruff"


# ── run_safe_fixes: dry_run with diff output (lines 102, 111-126) ────────


class TestRunSafeFixesDryRunDiffParsing:
    def _make_diff_output(self) -> str:
        """Build a realistic unified diff for testing."""
        return "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,2 @@\n-import os\n-import sys\n+import sys\n"

    def test_dry_run_parses_diff_into_changes(self, tmp_path: Path) -> None:
        """Lines 111-126: diff is split per-file and additions/deletions counted."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\nimport sys\n")

        diff_text = self._make_diff_output()

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            # Only return diff for the first ruff check --diff call
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

        # Line 102: venv PATH is set (tested implicitly via _find_venv_bin mock)
        # Lines 111-126: diff was parsed into file_diffs and changes
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
        """Line 102: PATH is augmented with venv_bin."""
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

        # Verify PATH was augmented with venv bin
        assert len(captured_envs) > 0
        assert captured_envs[0]["PATH"].startswith(str(venv_bin))


# ── _preview_fixes: timeout/OSError paths (lines 167-171, 179-182, 189-192) ──


class TestPreviewFixesErrorHandling:
    def test_ruff_check_timeout_is_silenced(self, tmp_path: Path) -> None:
        """Lines 167-171: TimeoutExpired during ruff check --diff is caught."""
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
        # Should not crash, just return empty
        assert result.errors == []

    def test_ruff_check_oserror_is_silenced(self, tmp_path: Path) -> None:
        """Lines 167-171: OSError during ruff check --diff is caught."""
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
        """Lines 179-182: import sort diff output is included in preview."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import sys\nimport os\n")

        isort_diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n-import sys\n import os\n+import sys\n"

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            # Import sort diff
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
        """Lines 179-182: TimeoutExpired during import sort --diff is caught."""
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
        """Lines 189-192: ruff format --diff output is included in preview."""
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
        """Lines 189-192: TimeoutExpired during ruff format --diff is caught."""
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


# ── _apply_ruff_fix: unsafe flag, timeout, OSError (lines 208, 216-219) ──


class TestApplyRuffFixErrors:
    def test_apply_unsafe_flag_passed(self, tmp_path: Path) -> None:
        """Line 208: --unsafe-fixes is passed in apply mode when safe_only=False."""
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
        """Lines 216-217: TimeoutExpired during ruff check --fix is caught."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import os\n")

        call_count = 0

        def mock_run(cmd, *args, **kwargs):
            nonlocal call_count
            call_count += 1
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
        """Lines 218-219: OSError during ruff check --fix is caught."""
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


# ── _apply_import_sort: timeout (lines 235-236) ─────────────────────────


class TestApplyImportSortErrors:
    def test_import_sort_timeout_is_silenced(self, tmp_path: Path) -> None:
        """Lines 235-236: TimeoutExpired during import sort --fix is caught."""
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

        # Should not propagate error (silenced with pass)
        assert not any("import" in e.lower() for e in result.errors)

    def test_import_sort_oserror_is_silenced(self, tmp_path: Path) -> None:
        """Lines 235-236: OSError during import sort --fix is caught."""
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


# ── _apply_ruff_format: reformatted parsing (lines 252-256) ─────────────


class TestApplyRuffFormat:
    def test_format_captures_reformatted_stderr(self, tmp_path: Path) -> None:
        """Lines 252-254: parses '1 file reformatted' from stderr."""
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
        """Lines 255-256: TimeoutExpired during ruff format is caught."""
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

        # Should not crash
        assert not any("format" in e.lower() for e in result.errors)


# ── _split_diff_by_file (lines 284-304) ─────────────────────────────────


class TestSplitDiffByFile:
    def test_single_file_diff(self) -> None:
        """Lines 284-304: splits a single-file diff."""
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,1 @@\n-import os\n x = 1\n"
        segments = _split_diff_by_file(diff)
        assert len(segments) == 1
        assert segments[0]["file"] == "foo.py"
        assert "import os" in segments[0]["diff"]

    def test_multiple_file_diffs(self) -> None:
        """Lines 284-304: splits a multi-file diff."""
        diff = (
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
            "@@ -1 +1 @@\n"
            "-old2\n"
            "+new2\n"
        )
        segments = _split_diff_by_file(diff)
        assert len(segments) == 2
        assert segments[0]["file"] == "foo.py"
        assert segments[1]["file"] == "bar.py"
        assert "-old" in segments[0]["diff"]
        assert "-old2" in segments[1]["diff"]

    def test_strips_a_prefix(self) -> None:
        """Lines 293-295: strips 'a/' prefix from filenames."""
        diff = "--- a/src/module.py\n+++ b/src/module.py\n@@ -1 +1 @@\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "src/module.py"

    def test_strips_timestamp_from_filename(self) -> None:
        """Line 296: strips tab-separated timestamp from filename."""
        diff = "--- a/foo.py\t2024-01-01\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "foo.py"

    def test_no_a_prefix(self) -> None:
        """Line 294: handles diff without a/ prefix."""
        diff = "--- foo.py\n+++ foo.py\n@@ -1 +1 @@\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert segments[0]["file"] == "foo.py"

    def test_empty_diff(self) -> None:
        """Returns empty list for empty input."""
        assert _split_diff_by_file("") == []

    def test_no_diff_markers(self) -> None:
        """Returns empty list when no --- markers are present."""
        assert _split_diff_by_file("just some text\nno diff here\n") == []

    def test_lines_before_first_file(self) -> None:
        """Lines before the first --- are captured in the first segment."""
        diff = "=== header ===\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y\n"
        segments = _split_diff_by_file(diff)
        assert len(segments) == 1
        assert segments[0]["file"] == "foo.py"


# ── Integration: dry_run end-to-end with all diff sources ───────────────


class TestDryRunIntegration:
    def test_all_three_diff_sources(self, tmp_path: Path) -> None:
        """Lines 111-126, 167-192: all three diff sources contribute to output."""
        py_file = tmp_path / "foo.py"
        py_file.write_text("import sys\nimport os\nx=1\n")

        check_diff = (
            "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,1 @@\n-import os\n import sys\n"
        )
        isort_diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n-import sys\n import os\n+import sys\n"
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
        # file_diffs should have segments from the combined diff
        assert len(result.file_diffs) >= 1
        # changes should have preview entries
        assert len(result.changes) >= 1
        for change in result.changes:
            assert change["action"] == "preview"
            assert "additions" in change
            assert "deletions" in change
            assert "sample" in change
