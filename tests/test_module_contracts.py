"""Phase 0: Module interface contract tests.

Verify that public interfaces of core LintGate modules return the expected
types and have the expected fields. These tests act as a regression safety
net during the ControlPlane refactor — if any of these break, the existing
pipeline was accidentally modified.
"""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

import pytest

from lintgate.change_classifier import classify_change
from lintgate.lint_runner import run_linters
from lintgate.linters.base import BaseLinter
from lintgate.results_aggregator import aggregate_results
from lintgate.tier_selector import select_tier
from lintgate.types import (
    AggregatedResult,
    ChangeClassification,
    LinterResult,
    LintIssue,
    LintTier,
    ProjectConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── classify_change contract ───────────────────────────────────────────


def test_classify_change_returns_change_classification() -> None:
    result = classify_change(
        tool_name="Edit",
        tool_input={"file_path": "/tmp/test.py", "old_string": "a", "new_string": "b"},
        tool_output="File edited successfully.",
        cwd="/tmp",
        config=ProjectConfig(project_root="/tmp"),
    )
    assert isinstance(result, ChangeClassification)


def test_classify_change_has_required_fields() -> None:
    result = classify_change(
        tool_name="Edit",
        tool_input={"file_path": "/tmp/test.py", "old_string": "a", "new_string": "b"},
        tool_output="File edited successfully.",
        cwd="/tmp",
        config=ProjectConfig(project_root="/tmp"),
    )
    # These fields must exist for downstream consumers
    assert hasattr(result, "files_changed")
    assert hasattr(result, "change_kind")
    assert hasattr(result, "risk_level")
    assert hasattr(result, "import_only")
    assert hasattr(result, "function_signatures_changed")
    assert hasattr(result, "class_structure_changed")
    assert hasattr(result, "touches_pipeline_critical")
    assert hasattr(result, "touches_test_files")
    assert hasattr(result, "is_new_file")
    assert hasattr(result, "lines_added")
    assert hasattr(result, "lines_removed")
    assert hasattr(result, "tool_name")


def test_classify_change_readonly_bash_returns_none_risk() -> None:
    result = classify_change(
        tool_name="Bash",
        tool_input={"command": "git status"},
        tool_output="On branch main",
        cwd="/tmp",
        config=ProjectConfig(project_root="/tmp"),
    )
    assert result.risk_level == "none"


# ── select_tier contract ──────────────────────────────────────────────


def test_select_tier_returns_lint_tier(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")
    config = ProjectConfig(project_root=str(tmp_path))
    classification = ChangeClassification(
        files_changed=[str(py_file)],
        files_by_language={"python": [str(py_file)]},
        change_kind="logic",
        risk_level="moderate",
    )
    result = select_tier(classification, config)
    assert isinstance(result, LintTier)
    assert hasattr(result, "name")
    assert hasattr(result, "linters")
    assert hasattr(result, "files")
    assert hasattr(result, "reason")
    assert hasattr(result, "strictness")
    assert hasattr(result, "skip")


# ── run_linters contract ──────────────────────────────────────────────


class _NopLinter(BaseLinter):
    name = "nop"
    required_tool = None

    def run(self, ctx):
        return []


def test_run_linters_returns_list_of_linter_results() -> None:
    tier = LintTier(name="test", linters=["nop"], files=["test.py"], reason="contract test")
    config = ProjectConfig(project_root=".")
    registry = {"nop": _NopLinter()}

    results = run_linters(tier, config, registry, timeout_ms=5000)
    assert isinstance(results, list)
    assert all(isinstance(r, LinterResult) for r in results)


def test_linter_result_has_required_fields() -> None:
    tier = LintTier(name="test", linters=["nop"], files=["test.py"], reason="contract test")
    config = ProjectConfig(project_root=".")
    registry = {"nop": _NopLinter()}

    results = run_linters(tier, config, registry, timeout_ms=5000)
    assert len(results) == 1
    r = results[0]
    assert hasattr(r, "linter_name")
    assert hasattr(r, "issues")
    assert hasattr(r, "status")
    assert hasattr(r, "duration_ms")


# ── aggregate_results contract ────────────────────────────────────────


def test_aggregate_results_returns_aggregated_result() -> None:
    issue = LintIssue(linter="test", kind="test", message="test issue", severity="warning")
    linter_result = LinterResult(linter_name="test", issues=[issue], status="ok")
    config = ProjectConfig(project_root=".")

    result = aggregate_results([linter_result], config)
    assert isinstance(result, AggregatedResult)
    assert hasattr(result, "blocking")
    assert hasattr(result, "warnings")
    assert hasattr(result, "informational")
    assert hasattr(result, "metrics")
    assert hasattr(result, "linter_statuses")
    assert hasattr(result, "tier_used")
    assert hasattr(result, "total_duration_ms")
    assert hasattr(result, "files_linted")


# ── format_report contract ────────────────────────────────────────────


def test_format_report_returns_dict_with_system_message() -> None:
    from lintgate.agent_reporter import format_report

    issue = LintIssue(linter="test", kind="test", message="test issue", severity="blocking")
    linter_result = LinterResult(linter_name="test", issues=[issue], status="ok")
    config = ProjectConfig(project_root=".")
    aggregated = aggregate_results([linter_result], config)

    result = format_report(aggregated)
    assert isinstance(result, dict)
    assert "systemMessage" in result


def test_format_report_returns_empty_dict_on_no_issues() -> None:
    from lintgate.agent_reporter import format_report

    config = ProjectConfig(project_root=".")
    aggregated = aggregate_results([], config)

    result = format_report(aggregated)
    assert result == {}
