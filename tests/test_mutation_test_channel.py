"""Mutation gap tests for lintgate/channels/test_channel.py.

Targets VALUE survivors in:
- TestChannel.should_run — exact return value assertions for all event variants
- _build_channel_result — exact ChannelResult field assertions
- _build_symbol_suggestions — exact suggestion string assertions
- _emit_symbol_findings — exact LintIssue object assertions
"""

from __future__ import annotations

import time
from typing import Any

from lintgate.channels.test_channel import (
    CoverageEvaluation,
    SymbolGateContext,
    TestChannel,
    TestChannelContext,
    TestRunResult,
    _build_channel_result,
    _build_symbol_suggestions,
    _emit_symbol_findings,
)
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification, LintIssue

# ── Helpers ──────────────────────────────────────────────────────────────


class _MockSymbol:
    def __init__(self, name: str = "foo", file: str = "foo.py", start_line: int = 1):
        self.name = name
        self.file = file
        self.start_line = start_line
        self.symbol_key = f"{file}::{name}"


class _MockSymbolResult:
    def __init__(
        self,
        name: str = "foo",
        covered: bool = False,
        missing_lines: list[int] | None = None,
        missing_branches: list[tuple[int, int]] | None = None,
        total_lines: int = 10,
        executed_lines: int = 5,
    ):
        self.symbol = _MockSymbol(name=name)
        self.covered = covered
        self.missing_lines = missing_lines or []
        self.missing_branches = missing_branches or []
        self.total_lines_in_span = total_lines
        self.executed_lines_in_span = executed_lines


class _MockGateResult:
    def __init__(self, symbol_results: list | None = None):
        self.symbol_results = symbol_results or []
        self.unresolved_required: list[str] = []
        self.waivers_expired: list[Any] = []
        self.waivers_applied: list[Any] = []
        self.skipped_reasons: list[str] = []


# ── TestChannel.should_run — exact VALUE assertions ──────────────────────


def test_should_run_returns_true_for_mcp_surface() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        change_classification=None,
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is True


def test_should_run_returns_false_for_none_classification() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=None,
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is False


def test_should_run_returns_true_for_logic_kind() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=ChangeClassification(
            files_changed=["/tmp/app.py"],
            change_kind="logic",
            risk_level="moderate",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is True


def test_should_run_returns_true_for_structural_kind() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=ChangeClassification(
            files_changed=["/tmp/app.py"],
            change_kind="structural",
            risk_level="structural",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is True


def test_should_run_returns_true_for_test_kind() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=ChangeClassification(
            files_changed=["/tmp/test_app.py"],
            change_kind="test",
            risk_level="cosmetic",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is True


def test_should_run_returns_false_for_config_kind() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=ChangeClassification(
            files_changed=["/tmp/config.yaml"],
            change_kind="config",
            risk_level="cosmetic",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is False


def test_should_run_returns_false_for_dependency_kind() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Bash",
        change_classification=ChangeClassification(
            files_changed=["/tmp/pyproject.toml"],
            change_kind="dependency",
            risk_level="moderate",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is False


def test_should_run_returns_false_for_cosmetic_kind() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=ChangeClassification(
            files_changed=["/tmp/readme.md"],
            change_kind="cosmetic",
            risk_level="cosmetic",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is False


def test_should_run_mcp_surface_overrides_classification() -> None:
    """MCP surface returns True regardless of classification kind."""
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="Edit",
        change_classification=ChangeClassification(
            files_changed=["/tmp/config.yaml"],
            change_kind="config",
            risk_level="cosmetic",
        ),
    )
    result = TestChannel().should_run(event, ControlPlaneConfig())
    assert result is True


# ── _build_channel_result — exact VALUE assertions ───────────────────────


def test_build_channel_result_no_findings_returns_pass() -> None:
    ctx = TestChannelContext(
        channel_name="tests",
        start=time.perf_counter(),
        findings=[],
        repairs=[],
        impacted_tests=[],
        test_result=None,
        cov_cfg={"measure": False, "threshold": None, "symbol_enabled": False},
        gate_result=None,
    )
    result = _build_channel_result(ctx)
    assert isinstance(result, ChannelResult)
    assert result.channel == "tests"
    assert result.status == "pass"
    assert result.severity == "none"
    assert result.findings == []
    assert result.repairs == []
    assert result.metrics["impacted_tests_found"] == 0
    assert result.metrics["missing_test_count"] == 0
    assert result.metrics["test_failure_count"] == 0


def test_build_channel_result_with_findings_returns_fail() -> None:
    findings = [
        LintIssue(
            linter="test_channel",
            kind="missing_test",
            message="module.py has no test file",
            severity="informational",
        ),
    ]
    ctx = TestChannelContext(
        channel_name="tests",
        start=time.perf_counter(),
        findings=findings,
        repairs=[],
        impacted_tests=["tests/test_a.py"],
        test_result=None,
        cov_cfg={"measure": False, "threshold": None, "symbol_enabled": False},
        gate_result=None,
    )
    result = _build_channel_result(ctx)
    assert result.status == "fail"
    assert result.severity == "informational"
    assert result.metrics["impacted_tests_found"] == 1
    assert result.metrics["missing_test_count"] == 1
    assert result.metrics["test_failure_count"] == 0


def test_build_channel_result_counts_test_failures() -> None:
    findings = [
        LintIssue(
            linter="test_channel",
            kind="test_failure",
            message="test_x failed",
            severity="warning",
        ),
        LintIssue(
            linter="test_channel",
            kind="test_failure",
            message="test_y failed",
            severity="warning",
        ),
    ]
    ctx = TestChannelContext(
        channel_name="tests",
        start=time.perf_counter(),
        findings=findings,
        repairs=[],
        impacted_tests=["tests/test_a.py"],
        test_result=None,
        cov_cfg={"measure": False, "threshold": None, "symbol_enabled": False},
        gate_result=None,
    )
    result = _build_channel_result(ctx)
    assert result.metrics["test_failure_count"] == 2
    assert result.metrics["missing_test_count"] == 0
    assert result.severity == "warning"


def test_build_channel_result_with_coverage() -> None:
    test_result = TestRunResult(passed=5, coverage_pct=85.5)
    ctx = TestChannelContext(
        channel_name="tests",
        start=time.perf_counter(),
        findings=[],
        repairs=[],
        impacted_tests=["tests/test_a.py"],
        test_result=test_result,
        cov_cfg={"measure": True, "threshold": 80.0, "symbol_enabled": False},
        gate_result=None,
    )
    result = _build_channel_result(ctx)
    assert result.metrics["coverage_pct"] == 85.5
    assert result.metrics["coverage_threshold"] == 80.0


def test_build_channel_result_with_gate_result() -> None:
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="foo", covered=True),
            _MockSymbolResult(name="bar", covered=False),
        ]
    )
    gate.waivers_applied = ["some_waiver"]
    ctx = TestChannelContext(
        channel_name="tests",
        start=time.perf_counter(),
        findings=[],
        repairs=[],
        impacted_tests=[],
        test_result=None,
        cov_cfg={"measure": False, "threshold": None, "symbol_enabled": True},
        gate_result=gate,
        cov_eval=CoverageEvaluation(
            targets_mode="impacted",
            is_partial_run=True,
            coverage_ok=False,
            coverage_pct=72.0,
        ),
    )
    result = _build_channel_result(ctx)
    assert result.metrics["symbol_coverage_targets"] == 2
    assert result.metrics["symbol_coverage_passed"] == 1
    assert result.metrics["symbol_coverage_failed"] == 1
    assert result.metrics["symbol_coverage_waivers"] == 1
    assert result.metrics["symbol_gate_context"] == {
        "targets_mode": "impacted",
        "is_partial_run": True,
        "coverage_ok": False,
        "coverage_pct": 72.0,
    }


def test_build_channel_result_bootstrap_needed() -> None:
    ctx = TestChannelContext(
        channel_name="tests",
        start=time.perf_counter(),
        findings=[],
        repairs=[],
        impacted_tests=[],
        test_result=None,
        cov_cfg={"measure": False, "threshold": None, "symbol_enabled": False},
        gate_result=None,
        bootstrap_needed=True,
    )
    result = _build_channel_result(ctx)
    assert result.metrics["bootstrap_needed"] is True
    assert result.metrics["bootstrap_reason"] == "zero_test_files"


def test_build_channel_result_duration_is_positive() -> None:
    start = time.perf_counter()
    ctx = TestChannelContext(
        channel_name="tests",
        start=start,
        findings=[],
        repairs=[],
        impacted_tests=[],
        test_result=None,
        cov_cfg={"measure": False, "threshold": None, "symbol_enabled": False},
        gate_result=None,
    )
    result = _build_channel_result(ctx)
    assert result.duration_ms >= 0


# ── _build_symbol_suggestions — exact VALUE assertions ───────────────────


def test_suggestions_lines_only_exact_output() -> None:
    sr = _MockSymbolResult(name="process", missing_lines=[10, 20, 30])
    result = _build_symbol_suggestions(sr)
    assert result == [
        "Add tests that execute lines 10, 20, 30 in process",
        "Or add a waiver with reason in symbol_coverage.waivers",
    ]


def test_suggestions_branches_only_exact_output() -> None:
    sr = _MockSymbolResult(name="validate", missing_branches=[(5, 6), (7, 8)])
    result = _build_symbol_suggestions(sr)
    assert result == [
        "Add tests that execute branches 5->6, 7->8 in validate",
        "Or add a waiver with reason in symbol_coverage.waivers",
    ]


def test_suggestions_lines_and_branches_exact_output() -> None:
    sr = _MockSymbolResult(
        name="compute",
        missing_lines=[1, 2],
        missing_branches=[(3, 4)],
    )
    result = _build_symbol_suggestions(sr)
    assert result == [
        "Add tests that execute lines 1, 2 and branches 3->4 in compute",
        "Or add a waiver with reason in symbol_coverage.waivers",
    ]


def test_suggestions_no_missing_data_exact_output() -> None:
    sr = _MockSymbolResult(name="helper")
    result = _build_symbol_suggestions(sr)
    assert result == [
        "Add missing tests for helper",
        "Or add a waiver with reason in symbol_coverage.waivers",
    ]


def test_suggestions_truncates_lines_at_10() -> None:
    sr = _MockSymbolResult(name="big_func", missing_lines=list(range(1, 25)))
    result = _build_symbol_suggestions(sr)
    # Only first 10 lines should appear
    assert "1, 2, 3, 4, 5, 6, 7, 8, 9, 10" in result[0]
    assert "11" not in result[0]


def test_suggestions_truncates_branches_at_5() -> None:
    sr = _MockSymbolResult(
        name="branchy",
        missing_branches=[(i, i + 1) for i in range(1, 10)],
    )
    result = _build_symbol_suggestions(sr)
    # Only first 5 branches
    assert "1->2, 2->3, 3->4, 4->5, 5->6" in result[0]
    assert "6->7" not in result[0]


# ── _emit_symbol_findings — exact LintIssue assertions ───────────────────


def test_emit_findings_uncovered_full_run_blocking() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="calc", missing_lines=[5, 6]),
        ]
    )
    ctx = SymbolGateContext(
        surface="hook",
        findings=findings,
        is_partial_run=False,
        coverage_ok=True,
    )
    _emit_symbol_findings(gate, ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.linter == "test_channel"
    assert f.kind == "symbol_uncovered"
    assert f.severity == "blocking"
    assert f.confidence == 1.0
    assert "Symbol calc is not fully covered" in f.message
    assert "(missing lines: 5, 6)" in f.message
    assert "downgraded" not in f.message
    assert f.evidence["symbol_key"] == "foo.py::calc"
    assert f.evidence["symbol"] == "calc"
    assert f.evidence["missing_lines"] == [5, 6]
    assert f.evidence["missing_branches"] == []
    assert f.evidence["is_partial_run"] is False
    assert f.evidence["coverage_ok"] is True


def test_emit_findings_partial_run_coverage_ok_downgrades() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="parse", missing_lines=[1]),
        ]
    )
    ctx = SymbolGateContext(
        surface="hook",
        findings=findings,
        is_partial_run=True,
        coverage_ok=True,
        coverage_pct=90.0,
    )
    _emit_symbol_findings(gate, ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "warning"
    assert f.confidence == 0.6
    assert "downgraded: partial test run with healthy line coverage" in f.message
    assert f.evidence["coverage_pct"] == 90.0


def test_emit_findings_partial_run_coverage_not_ok_keeps_blocking() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="transform", missing_lines=[1]),
        ]
    )
    ctx = SymbolGateContext(
        surface="hook",
        findings=findings,
        is_partial_run=True,
        coverage_ok=False,
    )
    _emit_symbol_findings(gate, ctx)
    f = findings[0]
    assert f.severity == "blocking"
    assert f.confidence == 0.7
    assert "downgraded" not in f.message


def test_emit_findings_covered_symbols_skipped() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="covered_fn", covered=True),
        ]
    )
    ctx = SymbolGateContext(surface="hook", findings=findings)
    _emit_symbol_findings(gate, ctx)
    assert findings == []


def test_emit_findings_branch_only_message() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="branch_fn", missing_branches=[(10, 20), (30, 40)]),
        ]
    )
    ctx = SymbolGateContext(surface="hook", findings=findings)
    _emit_symbol_findings(gate, ctx)
    assert len(findings) == 1
    assert "(missing 2 branches)" in findings[0].message
    assert "missing lines" not in findings[0].message


def test_emit_findings_lines_and_branches_message() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(
                name="mixed_fn",
                missing_lines=[1, 2, 3],
                missing_branches=[(4, 5)],
            ),
        ]
    )
    ctx = SymbolGateContext(surface="hook", findings=findings)
    _emit_symbol_findings(gate, ctx)
    assert "(missing lines: 1, 2, 3, and 1 branches)" in findings[0].message


def test_emit_findings_unresolved_required() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult()
    gate.unresolved_required = ["missing_module::critical_fn"]
    ctx = SymbolGateContext(surface="hook", findings=findings)
    _emit_symbol_findings(gate, ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "unresolved_required_symbol"
    assert f.severity == "blocking"
    assert f.confidence == 1.0
    assert f.message == "Required symbol not found: missing_module::critical_fn"
    assert f.evidence == {"symbol": "missing_module::critical_fn"}
    assert f.suggestions == [
        "Check that the file and symbol exist",
        "Update required_symbols in symbol_coverage config",
    ]


def test_emit_findings_waiver_expired() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult()

    class _Waiver:
        symbol = "old_fn"
        expires = "2025-01-01"

    gate.waivers_expired = [_Waiver()]
    ctx = SymbolGateContext(surface="hook", findings=findings)
    _emit_symbol_findings(gate, ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "waiver_expired"
    assert f.severity == "informational"
    assert f.message == "Symbol coverage waiver expired: old_fn (expired 2025-01-01)"
    assert f.evidence == {"symbol": "old_fn", "expires": "2025-01-01"}


def test_emit_findings_multiple_uncovered_produces_multiple_issues() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="fn_a", missing_lines=[1]),
            _MockSymbolResult(name="fn_b", missing_lines=[2]),
            _MockSymbolResult(name="fn_c", covered=True),
        ]
    )
    ctx = SymbolGateContext(surface="hook", findings=findings)
    _emit_symbol_findings(gate, ctx)
    assert len(findings) == 2
    names = [f.evidence["symbol"] for f in findings]
    assert names == ["fn_a", "fn_b"]


def test_emit_findings_evidence_targets_mode() -> None:
    findings: list[LintIssue] = []
    gate = _MockGateResult(
        [
            _MockSymbolResult(name="fn", missing_lines=[1]),
        ]
    )
    ctx = SymbolGateContext(
        surface="hook",
        findings=findings,
        targets_mode="fallback",
        coverage_pct=75.0,
    )
    _emit_symbol_findings(gate, ctx)
    assert findings[0].evidence["targets_mode"] == "fallback"
    assert findings[0].evidence["coverage_pct"] == 75.0
