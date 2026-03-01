"""Phase 1B: Remediation UX tests.

Tests:
- lint_fixer dry_run returns diff without modifying files
- lint_fixer FixResult serialization
- next_actions schema present in lint responses
- next_actions recommendations are contextually correct
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lintgate.lint_fixer import (
    FixResult,
    _collect_modified_files,
    _parse_ruff_fix_summary,
    _snapshot_mtimes,
    run_safe_fixes,
)

# Ensure mcp_server is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestFixResult:
    def test_to_dict_dry_run(self) -> None:
        result = FixResult(
            dry_run=True,
            diff_preview="--- a.py\n+++ a.py\n-import os",
            changes=[{"action": "preview", "detail": "--- a.py"}],
        )
        d = result.to_dict()
        assert d["dry_run"] is True
        assert d["files_modified"] == 0
        assert "diff_preview" in d
        assert len(d["changes"]) == 1

    def test_to_dict_apply(self) -> None:
        result = FixResult(
            dry_run=False,
            files_modified=["a.py", "b.py"],
            changes=[{"action": "ruff_fix", "detail": "Fixed 2 errors"}],
        )
        d = result.to_dict()
        assert d["dry_run"] is False
        assert d["files_modified"] == 2
        assert "files_modified_list" in d

    def test_to_dict_errors(self) -> None:
        result = FixResult(errors=["ruff not found"])
        d = result.to_dict()
        assert d["errors"] == ["ruff not found"]

    def test_to_dict_no_preview_when_empty(self) -> None:
        result = FixResult()
        d = result.to_dict()
        assert "diff_preview" not in d


class TestRunSafeFixes:
    def test_no_ruff_returns_error(self, tmp_path: Path) -> None:
        with patch("lintgate.lint_fixer._resolve_ruff", return_value=None):
            result = run_safe_fixes(
                files=[str(tmp_path / "test.py")],
                project_root=str(tmp_path),
            )
        assert len(result.errors) > 0
        assert "ruff not found" in result.errors[0]

    def test_no_python_files_returns_empty(self, tmp_path: Path) -> None:
        with patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"):
            result = run_safe_fixes(
                files=[str(tmp_path / "readme.md")],
                project_root=str(tmp_path),
            )
        assert result.files_modified == []
        assert result.changes == []

    def test_dry_run_is_default(self) -> None:
        result = FixResult()
        assert result.dry_run is True


class TestRuffFixSummaryParsing:
    """Test _parse_ruff_fix_summary parses ruff output correctly."""

    def test_parses_found_fixed_remaining(self) -> None:
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary("Found 4 errors (3 fixed, 1 remaining).", result)
        assert len(result.changes) == 1
        assert result.changes[0]["action"] == "ruff_fix"
        assert "3 fixed" in result.changes[0]["detail"]

    def test_parses_fixed_errors(self) -> None:
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary("Fixed 2 errors [F401, I001].", result)
        assert len(result.changes) == 1
        assert result.changes[0]["action"] == "ruff_fix"
        assert "Fixed 2 errors" in result.changes[0]["detail"]

    def test_no_match_on_zero_fixed(self) -> None:
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary("Found 2 errors (0 fixed, 2 remaining).", result)
        assert len(result.changes) == 0

    def test_no_match_on_empty_output(self) -> None:
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary("", result)
        assert len(result.changes) == 0

    def test_custom_action_name(self) -> None:
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary("Fixed 1 error [I001].", result, action="import_sort")
        assert result.changes[0]["action"] == "import_sort"

    def test_multiline_output_finds_summary(self) -> None:
        output = "test.py:1:8: F401 `os` imported but unused\nFound 3 errors (2 fixed, 1 remaining).\n"
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary(output, result)
        assert len(result.changes) == 1
        assert "2 fixed" in result.changes[0]["detail"]

    def test_singular_error(self) -> None:
        result = FixResult(dry_run=False)
        _parse_ruff_fix_summary("Found 1 error (1 fixed, 0 remaining).", result)
        assert len(result.changes) == 1


class TestMtimeTracking:
    """Test file modification tracking via mtime snapshots."""

    def test_snapshot_captures_mtimes(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        mtimes = _snapshot_mtimes([str(f)])
        assert str(f) in mtimes
        assert isinstance(mtimes[str(f)], float)

    def test_snapshot_skips_missing_files(self, tmp_path: Path) -> None:
        mtimes = _snapshot_mtimes([str(tmp_path / "missing.py")])
        assert mtimes == {}

    def test_collect_detects_modification(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        before = _snapshot_mtimes([str(f)])
        # Simulate modification (bump mtime forward)

        time.sleep(0.05)
        f.write_text("x = 2")
        after = _snapshot_mtimes([str(f)])
        result = FixResult(dry_run=False)
        _collect_modified_files(before, after, result)
        assert str(f) in result.files_modified

    def test_collect_ignores_unmodified(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        before = _snapshot_mtimes([str(f)])
        after = dict(before)  # Same mtimes
        result = FixResult(dry_run=False)
        _collect_modified_files(before, after, result)
        assert result.files_modified == []

    def test_collect_no_duplicates(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        before = {str(f): 1000.0}
        after = {str(f): 2000.0}
        result = FixResult(dry_run=False)
        result.files_modified.append(str(f))  # Already tracked
        _collect_modified_files(before, after, result)
        assert result.files_modified.count(str(f)) == 1


class TestRuffFlagCorrectness:
    """Verify that ruff commands use correct flag syntax."""

    def test_safe_only_does_not_pass_unsafe_flag(self, tmp_path: Path) -> None:
        """When safe_only=True, --unsafe-fixes should NOT appear in commands."""
        py_file = tmp_path / "test.py"
        py_file.write_text("import os\nx = 1\n")

        captured_cmds: list[list[str]] = []

        def mock_run(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("subprocess.run", side_effect=mock_run),
        ):
            run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
                safe_only=True,
            )

        for cmd in captured_cmds:
            for arg in cmd:
                assert "--unsafe-fixes" not in arg, (
                    f"safe_only=True should not pass --unsafe-fixes but found in: {cmd}"
                )

    def test_unsafe_mode_passes_flag(self, tmp_path: Path) -> None:
        """When safe_only=False, --unsafe-fixes should appear in commands."""
        py_file = tmp_path / "test.py"
        py_file.write_text("import os\nx = 1\n")

        captured_cmds: list[list[str]] = []

        def mock_run(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("subprocess.run", side_effect=mock_run),
        ):
            run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=True,
                safe_only=False,
            )

        # At least one ruff check cmd should have --unsafe-fixes
        ruff_check_cmds = [c for c in captured_cmds if "check" in c]
        has_unsafe = any("--unsafe-fixes" in c for c in ruff_check_cmds)
        assert has_unsafe, (
            f"safe_only=False should pass --unsafe-fixes. Cmds: {ruff_check_cmds}"
        )

    def test_returncode_1_still_parses(self, tmp_path: Path) -> None:
        """ruff returns 1 when unfixed issues remain — should still parse output."""
        py_file = tmp_path / "test.py"
        py_file.write_text("import os\nx = 1\n")

        def mock_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 1
            mock.stdout = "Found 2 errors (1 fixed, 1 remaining)."
            mock.stderr = ""
            return mock

        with (
            patch("lintgate.lint_fixer._resolve_ruff", return_value="/usr/bin/ruff"),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = run_safe_fixes(
                files=[str(py_file)],
                project_root=str(tmp_path),
                dry_run=False,
                safe_only=True,
            )

        assert any("1 fixed" in c.get("detail", "") for c in result.changes), (
            f"Should parse fix summary even with returncode=1. Changes: {result.changes}"
        )


class TestNextActions:
    """Test the _build_next_actions helper function."""

    def test_fixable_issues_suggest_lint_fix(self) -> None:
        from mcp_server import _build_next_actions

        actions = _build_next_actions(
            {
                "blocking": 2,
                "fixable": 3,
                "warnings": 1,
                "informational": 0,
                "run_id": "abc123",
                "project": "/tmp/proj",
            }
        )
        tool_names = [a["tool"] for a in actions]
        assert "lint_fix" in tool_names

    def test_blocking_issues_suggest_details(self) -> None:
        from mcp_server import _build_next_actions

        actions = _build_next_actions(
            {
                "blocking": 5,
                "fixable": 0,
                "warnings": 0,
                "informational": 0,
                "run_id": "abc123",
                "project": "/tmp/proj",
            }
        )
        tool_names = [a["tool"] for a in actions]
        assert "lint_get_details" in tool_names

    def test_no_issues_no_actions(self) -> None:
        from mcp_server import _build_next_actions

        actions = _build_next_actions(
            {
                "blocking": 0,
                "fixable": 0,
                "warnings": 0,
                "informational": 0,
                "run_id": "abc123",
                "project": "/tmp/proj",
            }
        )
        assert actions == []

    def test_actions_have_required_keys(self) -> None:
        from mcp_server import _build_next_actions

        actions = _build_next_actions(
            {
                "blocking": 1,
                "fixable": 2,
                "warnings": 10,
                "informational": 5,
                "run_id": "test123",
                "project": "/tmp/proj",
            }
        )
        required_keys = {"tool", "args", "safe", "reason", "priority"}
        for action in actions:
            assert required_keys.issubset(action.keys()), f"Missing keys in {action}"

    def test_many_warnings_suggest_details(self) -> None:
        from mcp_server import _build_next_actions

        actions = _build_next_actions(
            {
                "blocking": 0,
                "fixable": 0,
                "warnings": 10,
                "informational": 0,
                "run_id": "test123",
                "project": "/tmp/proj",
            }
        )
        warning_details = [
            a for a in actions if a.get("args", {}).get("severity") == "warning"
        ]
        assert len(warning_details) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
