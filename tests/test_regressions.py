from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lintgate.change_classifier import classify_change
from lintgate.lint_runner import run_linters
from lintgate.linters.base import BaseLinter
from lintgate.results_aggregator import aggregate_results
from lintgate.tier_selector import select_tier
from lintgate.types import (
    ChangeClassification,
    LinterResult,
    LintIssue,
    LintTier,
    ProjectConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


class _SlowLinter(BaseLinter):
    name = "slow"
    required_tool = None

    def run(self, ctx):
        time.sleep(1.0)
        yield LintIssue(linter="slow", kind="slow", message="slow issue")


def test_runner_timeout_is_reported_not_raised() -> None:
    tier = LintTier(
        name="tier_test",
        linters=["slow"],
        files=["example.py"],
        reason="timeout regression test",
    )
    config = ProjectConfig(project_root=".")
    registry = {"slow": _SlowLinter()}

    results = run_linters(tier, config, registry, timeout_ms=100)

    assert len(results) == 1
    assert results[0].linter_name == "slow"
    assert results[0].status == "timeout"


def test_build_bash_command_is_classified_as_build() -> None:
    classification = classify_change(
        tool_name="Bash",
        tool_input={"command": "pip install requests"},
        tool_output="",
        cwd=".",
        config=ProjectConfig(project_root="."),
    )

    assert classification.change_kind == "build"
    assert classification.risk_level == "moderate"


def test_config_change_without_python_path_still_selects_tier(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")

    config = ProjectConfig(project_root=str(tmp_path))
    classification = ChangeClassification(
        files_changed=[str(tmp_path / "pyproject.toml")],
        files_by_language={"other": [str(tmp_path / "pyproject.toml")]},
        change_kind="config",
        risk_level="moderate",
    )

    tier = select_tier(classification, config)

    assert not tier.skip
    assert tier.name == "tier_1_config"
    assert str(py_file) in tier.files


def test_dependency_change_without_python_path_still_selects_tier(tmp_path: Path) -> None:
    py_file = tmp_path / "main.py"
    py_file.write_text("print('ok')\n")

    config = ProjectConfig(project_root=str(tmp_path))
    classification = ChangeClassification(
        files_changed=[str(tmp_path / "requirements.txt")],
        files_by_language={"other": [str(tmp_path / "requirements.txt")]},
        change_kind="dependency",
        risk_level="moderate",
    )

    tier = select_tier(classification, config)

    assert not tier.skip
    assert tier.name == "tier_1_dependency"
    assert str(py_file) in tier.files


def test_build_change_without_files_selects_tier(tmp_path: Path) -> None:
    py_file = tmp_path / "worker.py"
    py_file.write_text("def run():\n    return 1\n")

    config = ProjectConfig(project_root=str(tmp_path))
    classification = ChangeClassification(
        files_changed=[],
        files_by_language={},
        change_kind="build",
        risk_level="moderate",
    )

    tier = select_tier(classification, config)

    assert not tier.skip
    assert tier.name == "tier_1_build"
    assert str(py_file) in tier.files


def test_complexity_exemption_alias_applies_to_radon_issue() -> None:
    issue = LintIssue(
        linter="radon",
        kind="complexity",
        message="complexity too high",
        file="/tmp/legacy.py",
        line=10,
        severity="warning",
    )
    linter_result = LinterResult(linter_name="complexity_checker", issues=[issue], status="ok")
    config = ProjectConfig(
        project_root="/tmp",
        exemptions={
            "complexity": {
                "legacy.py": {
                    "reason": "Known debt",
                }
            }
        },
    )

    aggregated = aggregate_results([linter_result], config)

    assert aggregated.metrics["total_issues"] == 0
