"""Tests for lintgate/lint_runner.py."""

from __future__ import annotations

from unittest.mock import MagicMock

from lintgate.lint_runner import (
    _collect_future_result,
    _execute_parallel,
    _is_external_package,
    run_linters,
)
from lintgate.types import LinterContext, LinterResult, LintIssue, LintTier, ProjectConfig


# ── _is_external_package ────────────────────────────────────────────────


def test_is_external_package_site_packages():
    assert _is_external_package("/home/user/.venv/lib/python3.11/site-packages/foo.py") is True


def test_is_external_package_dist_packages():
    assert _is_external_package("/usr/lib/python3/dist-packages/bar.py") is True


def test_is_external_package_normal_file():
    assert _is_external_package("/project/src/foo.py") is False


# ── _collect_future_result ──────────────────────────────────────────────


def test_collect_future_result_success():
    expected = LinterResult(linter_name="ruff", status="ok")
    fut = MagicMock()
    fut.result.return_value = expected
    linter = MagicMock()
    linter.name = "ruff"
    result = _collect_future_result(fut, linter)
    assert result.linter_name == "ruff"
    assert result.status == "ok"


def test_collect_future_result_exception():
    fut = MagicMock()
    fut.result.side_effect = RuntimeError("thread died")
    linter = MagicMock()
    linter.name = "mypy"
    result = _collect_future_result(fut, linter)
    assert result.status == "error"
    assert "RuntimeError" in result.error


# ── run_linters ─────────────────────────────────────────────────────────


def test_run_linters_skip_tier():
    tier = LintTier(name="skip", linters=[], files=[], reason="no change", skip=True)
    config = ProjectConfig(project_root="/tmp")
    results = run_linters(tier, config, {})
    assert results == []


def test_run_linters_unknown_linter():
    tier = LintTier(
        name="tier_1",
        linters=["nonexistent_linter"],
        files=["foo.py"],
        reason="test",
    )
    config = ProjectConfig(project_root="/tmp")
    results = run_linters(tier, config, {})
    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "Unknown linter" in results[0].error


def test_run_linters_runs_selected(tmp_path):
    # Create a real file so it's not excluded
    src = tmp_path / "foo.py"
    src.write_text("x = 1\n")

    mock_linter = MagicMock()
    mock_linter.name = "test_lint"
    mock_linter.execute.return_value = LinterResult(
        linter_name="test_lint",
        status="ok",
        issues=[LintIssue(linter="test_lint", kind="T001", message="found")],
    )

    tier = LintTier(
        name="tier_1",
        linters=["test_lint"],
        files=[str(src)],
        reason="test",
    )
    config = ProjectConfig(project_root=str(tmp_path))
    results = run_linters(tier, config, {"test_lint": mock_linter})
    assert any(r.status == "ok" for r in results)
    assert any(r.linter_name == "test_lint" for r in results)


def test_run_linters_filters_external_packages():
    tier = LintTier(
        name="tier_1",
        linters=["test_lint"],
        files=["/home/user/.venv/lib/python3.11/site-packages/pkg.py"],
        reason="test",
    )
    mock_linter = MagicMock()
    mock_linter.name = "test_lint"
    config = ProjectConfig(project_root="/project")
    results = run_linters(tier, config, {"test_lint": mock_linter})
    # All files are external, so nothing runs
    assert results == []


# ── _execute_parallel ───────────────────────────────────────────────────


def test_execute_parallel_single_linter():
    mock_linter = MagicMock()
    mock_linter.name = "fast"
    mock_linter.execute.return_value = LinterResult(linter_name="fast", status="ok")

    ctx = LinterContext(files=["a.py"], project_root="/tmp")
    import time

    deadline = time.time() + 10
    results = _execute_parallel([mock_linter], ctx, deadline, 10000)
    assert len(results) == 1
    assert results[0].status == "ok"
