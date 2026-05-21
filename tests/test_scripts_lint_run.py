"""Behavioral tests for scripts/lint_run.py.

These exercise cmd_files / cmd_project / cmd_details / cmd_status /
cmd_audit / cmd_fix directly by passing argparse.Namespace objects
and capturing stdout. The MCP layer in mcp_tools/lint_tools.py just
shells out — its subprocess-boundary tests live in
tests/test_mcp_lint_tools.py.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest import mock

import pytest

from scripts.lint_run import (
    cmd_audit,
    cmd_details,
    cmd_files,
    cmd_fix,
    cmd_project,
    cmd_status,
)

if TYPE_CHECKING:
    from pathlib import Path


def _load_emitted(capsys) -> dict:
    """Read the slim envelope printed to stdout, then load the full file if it exists."""
    captured = capsys.readouterr()
    envelope = json.loads(captured.out.strip().splitlines()[-1])
    if "file" in envelope and "analysis_id" in envelope:
        with open(envelope["file"]) as f:
            return json.loads(f.read())
    return envelope


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _mock_run_lint(**overrides):
    """Patch scripts.lint_run._run_lint with a canned result."""
    defaults = {
        "run_id": "r1",
        "tier": "tier_2_manual",
        "project": "/proj",
        "files_linted": 1,
        "duration_ms": 10.0,
        "blocking_count": 0,
        "warning_count": 0,
        "info_count": 0,
        "issue_count": 0,
        "fixable": 0,
        "linters_run": 2,
        "blocking_issues": [],
        "warning_issues": [],
        "info_issues": [],
    }
    defaults.update(overrides)
    return mock.patch("scripts.lint_run._run_lint", return_value=defaults)


# ── cmd_files ────────────────────────────────────────────────────────────


class TestCmdFiles:
    def test_basic(self, tmp_path: Path, capsys):
        f = tmp_path / "a.py"
        f.touch()
        with _mock_run_lint():
            cmd_files(_ns(
                path=str(tmp_path), files=[str(f)], tier=2, strictness="normal",
                project_root=str(tmp_path), scope="compact",
            ))
        result = _load_emitted(capsys)
        assert result["run_id"] == "r1"

    def test_missing_files_reported(self, tmp_path: Path, capsys):
        f = tmp_path / "exists.py"
        f.touch()
        with _mock_run_lint():
            cmd_files(_ns(
                path=str(tmp_path),
                files=[str(f), str(tmp_path / "missing.py")],
                tier=2, strictness="normal", project_root=str(tmp_path),
                scope="compact",
            ))
        result = _load_emitted(capsys)
        assert "missing_files" in result
        assert str(tmp_path / "missing.py") in result["missing_files"][0] or \
               "missing.py" in result["missing_files"][0]

    def test_surgical_scope(self, tmp_path: Path, capsys):
        f = tmp_path / "a.py"
        f.touch()
        with _mock_run_lint(blocking_count=1, issue_count=2):
            cmd_files(_ns(
                path=str(tmp_path), files=[str(f)], tier=2, strictness="normal",
                project_root=str(tmp_path), scope="surgical",
            ))
        result = _load_emitted(capsys)
        assert "edit_scope" in result
        assert "baseline" in result
        assert result["edit_scope"]["blocking_count"] == 1


# ── cmd_project ──────────────────────────────────────────────────────────


class TestCmdProject:
    def test_includes_total_python_files(self, tmp_path: Path, capsys):
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        with _mock_run_lint(files_linted=2):
            cmd_project(_ns(path=str(tmp_path), tier=2, strictness="normal"))
        result = _load_emitted(capsys)
        assert result["total_python_files"] == 2


# ── cmd_details ──────────────────────────────────────────────────────────


class TestCmdDetails:
    def _details_fixture(self, **overrides):
        base = {
            "tier": 2,
            "project": "/proj",
            "duration_ms": 150,
            "blocking_issues": [{"code": "E1", "severity": "blocking"}],
            "warning_issues": [{"code": "W1", "severity": "warning"}],
            "info_issues": [{"code": "I1", "severity": "informational"}],
            "recurrence": {"E1": 3},
            "linter_diagnostics": {"ruff": "ok"},
        }
        base.update(overrides)
        return base

    def test_all_severities(self, capsys):
        with mock.patch("lintgate.state.load_run_details", return_value=self._details_fixture()):
            cmd_details(_ns(
                path=None, run_id="r1", severity=None, max_issues=10,
                include_recurrence=False,
            ))
        result = _load_emitted(capsys)
        assert result["total_matching"] == 3
        assert len(result["issues"]) == 3

    def test_filter_blocking(self, capsys):
        with mock.patch("lintgate.state.load_run_details", return_value=self._details_fixture()):
            cmd_details(_ns(
                path=None, run_id="r1", severity="blocking", max_issues=10,
                include_recurrence=False,
            ))
        result = _load_emitted(capsys)
        assert result["total_matching"] == 1
        assert result["issues"][0]["severity"] == "blocking"

    def test_truncation(self, capsys):
        many = [{"code": f"E{i}", "severity": "blocking"} for i in range(20)]
        with mock.patch(
            "lintgate.state.load_run_details",
            return_value=self._details_fixture(blocking_issues=many, warning_issues=[], info_issues=[]),
        ):
            cmd_details(_ns(
                path=None, run_id="r1", severity=None, max_issues=5,
                include_recurrence=False,
            ))
        result = _load_emitted(capsys)
        assert result["total_matching"] == 20
        assert len(result["issues"]) == 5
        assert result["truncated"] == 15

    def test_include_recurrence(self, capsys):
        with mock.patch("lintgate.state.load_run_details", return_value=self._details_fixture()):
            cmd_details(_ns(
                path=None, run_id="r1", severity=None, max_issues=10,
                include_recurrence=True,
            ))
        result = _load_emitted(capsys)
        assert result["recurrence"] == {"E1": 3}

    def test_linter_diagnostics_included(self, capsys):
        with mock.patch("lintgate.state.load_run_details", return_value=self._details_fixture()):
            cmd_details(_ns(
                path=None, run_id="r1", severity=None, max_issues=10,
                include_recurrence=False,
            ))
        result = _load_emitted(capsys)
        assert result["linter_diagnostics"] == {"ruff": "ok"}

    def test_run_id_not_found(self, capsys):
        with mock.patch("lintgate.state.load_run_details", return_value=None):
            with pytest.raises(SystemExit):
                cmd_details(_ns(
                    path=None, run_id="missing", severity=None, max_issues=10,
                    include_recurrence=False,
                ))
        captured = capsys.readouterr()
        assert "No lint run found" in captured.out


# ── cmd_status ───────────────────────────────────────────────────────────


class TestCmdStatus:
    def test_basic(self, tmp_path: Path, capsys):
        fake_config = SimpleNamespace(
            languages=["python"],
            pipeline_critical_paths=[],
            severity_overrides={},
            enabled_linters=["ruff"],
            tool_version_requirements={},
        )
        fake_linter = SimpleNamespace(
            tier=1,
            required_tool="ruff",
            available=lambda project_root="": True,
        )
        with (
            mock.patch("lintgate.config.load_config", return_value=fake_config),
            mock.patch("lintgate.registry.build_registry", return_value={"ruff": fake_linter}),
            mock.patch("lintgate.state.load_last_run", return_value=None),
            mock.patch("lintgate.state.load_last_version_audit", return_value=None),
            mock.patch("lintgate.context_guidance.build_context_guidance", return_value={}),
            mock.patch("lintgate.context_guidance.summarize_context_guidance", return_value={"summary": "ok"}),
        ):
            cmd_status(_ns(path=str(tmp_path)))
        result = _load_emitted(capsys)
        assert result["version"] == "0.2.0"
        assert "ruff" in result["linters"]
        assert result["linter_count"] == 1
        assert result["last_run"] is None
        assert result["last_version_audit"] is None


# ── cmd_audit ────────────────────────────────────────────────────────────


class TestCmdAudit:
    def test_basic(self, tmp_path: Path, capsys):
        fake_config = SimpleNamespace(tool_version_requirements={"ruff": ">=0.4"})
        fake_audit = {
            "project": str(tmp_path), "timestamp": 1000.0, "tools": [],
            "issues": [], "issue_count": 0, "auto_fix_applied": False,
        }
        with (
            mock.patch("lintgate.config.load_config", return_value=fake_config),
            mock.patch("lintgate.versioning.run_version_audit", return_value=fake_audit),
            mock.patch(
                "lintgate.versioning.format_version_audit_summary",
                return_value={"issue_count": 0},
            ),
            mock.patch("lintgate.state.save_version_audit"),
            mock.patch("lintgate.state.log_version_event"),
        ):
            cmd_audit(_ns(path=str(tmp_path), auto_fix=False, verify_after_fix=True))
        result = _load_emitted(capsys)
        assert result["summary"]["issue_count"] == 0
        assert result["issue_count"] == 0


# ── cmd_fix ──────────────────────────────────────────────────────────────


class TestCmdFix:
    def test_fix_with_path(self, tmp_path: Path, capsys):
        (tmp_path / "a.py").touch()
        fix_result = SimpleNamespace(
            to_dict=lambda: {"files_modified": 0, "changes": [], "dry_run": True}
        )
        with mock.patch("lintgate.lint_fixer.run_safe_fixes", return_value=fix_result):
            cmd_fix(_ns(
                path=str(tmp_path), files=None, dry_run=True, safe_only=True,
            ))
        result = _load_emitted(capsys)
        assert result["dry_run"] is True

    def test_fix_with_files(self, tmp_path: Path, capsys):
        f = tmp_path / "a.py"
        f.touch()
        fix_result = SimpleNamespace(
            to_dict=lambda: {"files_modified": 1, "changes": [], "dry_run": False}
        )
        with mock.patch("lintgate.lint_fixer.run_safe_fixes", return_value=fix_result):
            cmd_fix(_ns(
                path=None, files=[str(f)], dry_run=False, safe_only=True,
            ))
        result = _load_emitted(capsys)
        assert result["dry_run"] is False

    def test_no_python_files_emits_error(self, tmp_path: Path, capsys):
        cmd_fix(_ns(
            path=str(tmp_path), files=None, dry_run=True, safe_only=True,
        ))
        result = _load_emitted(capsys)
        assert result["error"] == "No Python files found"
