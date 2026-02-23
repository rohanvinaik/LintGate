from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

import lintgate.versioning as versioning
import mcp_server
from lintgate.agent_reporter import format_report
from lintgate.context_guidance import build_context_guidance
from lintgate.linters.context_rule_checker import ContextRuleChecker
from lintgate.types import AggregatedResult, LinterContext, LintIssue
from lintgate.versioning import collect_required_version_specs, inspect_tool_versions


def test_wheel_packaging_includes_mcp_tools_package() -> None:
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text())
    wheel_cfg = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "mcp_tools" in wheel_cfg.get("packages", [])


def test_mcp_validation_rejects_invalid_tier(tmp_path) -> None:
    file_path = tmp_path / "app.py"
    file_path.write_text("x = 1\n")

    with pytest.raises(ValueError, match="Invalid tier"):
        mcp_server.lint_files(  # type: ignore[attr-defined]
            files=[str(file_path)],
            tier=9,  # type: ignore[arg-type]
        )


def test_mcp_validation_rejects_invalid_strictness(tmp_path) -> None:
    file_path = tmp_path / "app.py"
    file_path.write_text("x = 1\n")

    with pytest.raises(ValueError, match="Invalid strictness"):
        mcp_server.lint_files(  # type: ignore[attr-defined]
            files=[str(file_path)],
            strictness="very_strict",  # type: ignore[arg-type]
        )


def test_context_guidance_infers_solve_task_rule(tmp_path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("- DO NOT add solve_task_ functions to this project.\n")

    guidance = build_context_guidance(str(tmp_path))
    inferred = [
        rule
        for rule in guidance["rules"]
        if rule.get("source") == "inferred:do_not_solve_task_prefix"
    ]

    assert inferred
    assert inferred[0]["kind"] == "forbid_regex"


def test_context_rule_checker_flags_inferred_drift_pattern(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("DO NOT use solve_task_ style helper functions.\n")
    code_file = tmp_path / "solver.py"
    code_file.write_text("def solve_task_alpha(x):\n    return x\n")

    checker = ContextRuleChecker()
    ctx = LinterContext(files=[str(code_file)], project_root=str(tmp_path))

    issues = list(checker.run(ctx))

    assert issues
    assert any(issue.kind == "context-forbid" for issue in issues)
    assert any(issue.severity == "blocking" for issue in issues)


def test_collect_required_version_specs_includes_config_and_pyproject(tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'requires-python = ">=3.10"',
                'dependencies = ["ruff>=0.4", "mypy>=1.8"]',
            ]
        )
        + "\n"
    )

    requirements = collect_required_version_specs(
        str(tmp_path),
        config_requirements={"bandit": ">=1.7"},
    )

    assert requirements["python"]["combined_specifier"] == ">=3.10"
    assert ">=0.4" in requirements["ruff"]["combined_specifier"]
    assert ">=1.8" in requirements["mypy"]["combined_specifier"]
    assert requirements["bandit"]["combined_specifier"] == ">=1.7"


def test_inspect_tool_versions_ignores_unrequired_missing_executables(
    tmp_path,
    monkeypatch,
) -> None:
    requirements = collect_required_version_specs(str(tmp_path), config_requirements=None)

    monkeypatch.setattr(
        versioning,
        "_installed_version",
        lambda spec, project_root=None: "3.11.0" if spec.tool == "python" else None,
    )
    monkeypatch.setattr(versioning, "_which", lambda executable, project_root=None: None)

    observations = inspect_tool_versions(requirements)
    non_python = [item for item in observations if item["tool"] != "python"]

    assert non_python
    assert all(item["status"] == "ok" for item in non_python)


def test_audit_tool_versions_persists_and_returns_summary(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_version_audit(*args, **kwargs):
        captured["audit_args"] = args
        captured["audit_kwargs"] = kwargs
        return {
            "timestamp": 123.0,
            "tools": [{"tool": "ruff", "status": "mismatch"}],
            "issues": [{"tool": "ruff", "status": "mismatch"}],
            "auto_fix_applied": True,
            "post_fix_issues": [],
        }

    def fake_save_version_audit(project: str, audit: dict) -> None:
        captured["saved_project"] = project
        captured["saved_issue_count"] = len(audit.get("issues", []))

    def fake_log_version_event(event: dict) -> None:
        captured["event"] = event

    monkeypatch.setattr("lintgate.versioning.run_version_audit", fake_run_version_audit)
    monkeypatch.setattr("lintgate.state.save_version_audit", fake_save_version_audit)
    monkeypatch.setattr("lintgate.state.log_version_event", fake_log_version_event)

    out = mcp_server.audit_tool_versions(path=str(tmp_path), auto_fix=True)  # type: ignore[attr-defined]
    payload = json.loads(out)

    assert payload["summary"]["issue_count"] == 1
    assert payload["summary"]["post_fix_issue_count"] == 0
    assert captured["saved_project"] == str(tmp_path)
    assert captured["saved_issue_count"] == 1
    assert isinstance(captured.get("event"), dict)


def test_report_includes_recurrence_section() -> None:
    issue = LintIssue(
        linter="ruff",
        kind="F401",
        message="unused import",
        file="/tmp/example.py",
        line=3,
        severity="warning",
    )
    result = AggregatedResult(
        warnings=[issue],
        metrics={
            "total_issues": 1,
            "blocking_count": 0,
            "warning_count": 1,
            "linters_skipped": 0,
            "linters_errored": 0,
            "fixable_count": 0,
        },
        tier_used="tier_2_logic",
        tier_reason="Logic change",
        files_linted=["/tmp/example.py"],
    )

    report = format_report(
        result,
        recurrence_summary={
            "repeated_issue_count": 1,
            "top_repeated": [
                {
                    "linter": "ruff",
                    "kind": "F401",
                    "file": "/tmp/example.py",
                    "line": 3,
                    "count": 2,
                    "message": "unused import",
                }
            ],
        },
    )

    assert "RECURRING" in report["systemMessage"]
    assert "example.py:3" in report["systemMessage"]


def test_line_number_caching() -> None:
    from lintgate.linters.context_rule_checker import _line_number

    test_text = "line1\nline2\nline3"

    # Clear the cache before testing to ensure a fresh start
    _line_number.cache_clear()

    # First call - uncached
    line = _line_number(test_text, 7)
    assert line == 2

    # Second call with same arguments - should be cached
    line_cached = _line_number(test_text, 7)
    assert line_cached == 2

    # Verify cache info (hits and misses)
    cache_info = _line_number.cache_info()
    assert cache_info.hits == 1
    assert cache_info.misses == 1

    # Call with different arguments
    line_new = _line_number(test_text, 0)
    assert line_new == 1

    cache_info_after_new = _line_number.cache_info()
    assert cache_info_after_new.hits == 1
    assert cache_info_after_new.misses == 2
