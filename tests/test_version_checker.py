"""Tests for VersionChecker linter."""

from __future__ import annotations

from unittest import mock

from lintgate.linters.version_checker import VersionChecker
from lintgate.types import LinterContext


def test_version_checker_passes_project_root(tmp_path):
    """Verify that VersionChecker passes project_root to inspect_tool_versions."""
    # Create a dummy pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10"\ndependencies = ["ruff>=0.4"]\n'
    )

    # Mock collect_required_version_specs and inspect_tool_versions
    with (
        mock.patch(
            "lintgate.linters.version_checker.collect_required_version_specs"
        ) as mock_collect,
        mock.patch(
            "lintgate.linters.version_checker.inspect_tool_versions"
        ) as mock_inspect,
    ):
        mock_collect.return_value = {
            "ruff": {"specifiers": [">=0.4"], "sources": ["pyproject.toml"]}
        }
        mock_inspect.return_value = []

        linter = VersionChecker()
        ctx = LinterContext(
            project_root=str(tmp_path), files=[], config=mock.MagicMock()
        )

        # Run linter
        list(linter.run(ctx))

        # Verify inspect_tool_versions was called with project_root
        mock_inspect.assert_called_once()
        args, kwargs = mock_inspect.call_args
        assert kwargs.get("project_root") == str(tmp_path)


def test_version_checker_detects_mismatch(tmp_path):
    """Verify that VersionChecker yields issues for mismatches."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["ruff>=0.4"]\n'
    )

    with (
        mock.patch(
            "lintgate.linters.version_checker.collect_required_version_specs"
        ) as mock_collect,
        mock.patch(
            "lintgate.linters.version_checker.inspect_tool_versions"
        ) as mock_inspect,
    ):
        mock_collect.return_value = {
            "ruff": {"specifiers": [">=0.4"], "sources": ["pyproject.toml"]}
        }
        mock_inspect.return_value = [
            {
                "tool": "ruff",
                "status": "mismatch",
                "message": "ruff version 0.1.0 does not satisfy required specifier >=0.4",
                "installed_version": "0.1.0",
                "required_specifier": ">=0.4",
                "requirement_sources": ["pyproject.toml:project.dependencies"],
                "is_optional": False,
            }
        ]

        linter = VersionChecker()
        ctx = LinterContext(
            project_root=str(tmp_path), files=[], config=mock.MagicMock()
        )

        issues = list(linter.run(ctx))
        assert len(issues) == 1
        assert issues[0].kind == "version-mismatch"
        assert "ruff version 0.1.0" in issues[0].message
        assert issues[0].severity == "blocking"


def test_version_checker_handles_optional(tmp_path):
    """Verify that VersionChecker treats optional tool issues as warnings."""
    with (
        mock.patch(
            "lintgate.linters.version_checker.collect_required_version_specs"
        ) as mock_collect,
        mock.patch(
            "lintgate.linters.version_checker.inspect_tool_versions"
        ) as mock_inspect,
    ):
        mock_collect.return_value = {}
        mock_inspect.return_value = [
            {
                "tool": "pip-audit",
                "status": "missing",
                "message": "pip-audit is not installed but required",
                "installed_version": None,
                "required_specifier": ">=2.0",
                "requirement_sources": [
                    "pyproject.toml:project.optional-dependencies.dev"
                ],
                "is_optional": True,
            }
        ]

        linter = VersionChecker()
        ctx = LinterContext(
            project_root=str(tmp_path), files=[], config=mock.MagicMock()
        )

        issues = list(linter.run(ctx))
        assert len(issues) == 1
        assert issues[0].kind == "version-optional-missing"
        assert issues[0].severity == "warning"
