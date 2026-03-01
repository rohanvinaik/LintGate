"""Tests for reporter_compact.py extracted helpers."""

from __future__ import annotations

from lintgate.controlplane.reporter_compact import (
    _build_channel_summary,
    _build_cp_next_actions,
    _collect_symbol_coverage_blockers,
    _count_findings_by_severity,
    format_mesh_report_compact,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification, LintIssue


def _make_event() -> SupervisionEvent:
    return SupervisionEvent(
        surface="mcp",
        project_root="/tmp/test",
        tool_name="test",
        files_changed=[],
        change_classification=ChangeClassification(
            files_changed=[],
            files_by_language={},
            change_kind="logic",
            risk_level="moderate",
            tool_name="test",
        ),
        raw_input={},
    )


def _make_mesh(
    channel_results: list[ChannelResult] | None = None,
    coherence_state: str = "stable",
) -> MeshResult:
    return MeshResult(
        channel_results=channel_results or [],
        coherence=CoherenceResult(state=coherence_state, summary="ok"),
        event=_make_event(),
        duration_ms=100.0,
    )


# ── _count_findings_by_severity ────────────────────────────────────────


class TestCountFindingsBySeverity:
    def test_empty_index(self) -> None:
        result = _count_findings_by_severity({})
        assert result == {"blocking": 0, "warning": 0, "informational": 0}

    def test_counts_by_severity(self) -> None:
        index = {
            "fp1": {"severity": "blocking", "count": 2},
            "fp2": {"severity": "warning", "count": 1},
            "fp3": {"severity": "informational", "count": 3},
        }
        result = _count_findings_by_severity(index)
        assert result["blocking"] == 2
        assert result["warning"] == 1
        assert result["informational"] == 3

    def test_unknown_severity_ignored(self) -> None:
        """Severity values not in the totals dict are skipped."""
        index = {
            "fp1": {"severity": "unknown_sev", "count": 5},
            "fp2": {"severity": "blocking", "count": 1},
        }
        result = _count_findings_by_severity(index)
        assert result["blocking"] == 1
        assert result["warning"] == 0


# ── _build_channel_summary ─────────────────────────────────────────────


class TestBuildChannelSummary:
    def test_pass_channel(self) -> None:
        cr = ChannelResult(channel="lint", status="pass", severity="none", findings=[])
        mesh = _make_mesh([cr])
        summary = _build_channel_summary(mesh)
        assert summary["lint"] == "pass"

    def test_skip_channel_excluded(self) -> None:
        cr = ChannelResult(channel="deps", status="skip", severity="none", findings=[])
        mesh = _make_mesh([cr])
        summary = _build_channel_summary(mesh)
        assert "deps" not in summary

    def test_fail_channel(self) -> None:
        finding = LintIssue(linter="test", kind="x", message="m", severity="blocking")
        cr = ChannelResult(
            channel="lint", status="fail", severity="blocking", findings=[finding]
        )
        mesh = _make_mesh([cr])
        summary = _build_channel_summary(mesh)
        assert "fail" in summary["lint"]
        assert "1 blocking" in summary["lint"]

    def test_unknown_status_passthrough(self) -> None:
        """Channel with status not 'fail'/'pass'/'skip' uses raw status string."""
        cr = ChannelResult(
            channel="custom", status="degraded", severity="none", findings=[]
        )
        mesh = _make_mesh([cr])
        summary = _build_channel_summary(mesh)
        assert summary["custom"] == "degraded"


# ── _build_cp_next_actions ─────────────────────────────────────────────


class TestBuildCpNextActions:
    def test_no_issues_empty_actions(self) -> None:
        counts = {"blocking": 0, "warning": 0, "repairs_available": 0}
        actions = _build_cp_next_actions("run1", counts)
        assert actions == []

    def test_blocking_issues_suggest_details(self) -> None:
        counts = {"blocking": 5, "warning": 0, "repairs_available": 0}
        actions = _build_cp_next_actions("run1", counts)
        assert len(actions) == 1
        assert actions[0].tool == "controlplane_get_details"

    def test_repairs_available_suggest_apply(self) -> None:
        """repairs_available > 0 produces apply_repairs action."""
        counts = {"blocking": 0, "warning": 0, "repairs_available": 3}
        actions = _build_cp_next_actions("run1", counts)
        repair_actions = [
            a for a in actions if a.tool == "controlplane_apply_repairs"
        ]
        assert len(repair_actions) == 1
        assert "3" in repair_actions[0].reason

    def test_symbol_blockers_produce_priority_actions(self) -> None:
        counts = {"blocking": 2, "warning": 0, "repairs_available": 0}
        blockers = [{"kind": "symbol_uncovered", "symbol": "foo"}]
        actions = _build_cp_next_actions("run1", counts, symbol_blockers=blockers)
        assert len(actions) >= 2
        assert actions[0].priority == 1

    def test_warnings_suggest_details(self) -> None:
        counts = {"blocking": 0, "warning": 10, "repairs_available": 0}
        actions = _build_cp_next_actions("run1", counts)
        warning_actions = [a for a in actions if "warning" in a.reason.lower()]
        assert len(warning_actions) == 1

    def test_ship_gate_parity_failure_adds_preflight_action(self) -> None:
        counts = {"blocking": 0, "warning": 0, "repairs_available": 0}
        actions = _build_cp_next_actions(
            "run1",
            counts,
            ship_gate_parity={"status": "fail"},
        )
        assert actions[0].tool == "terminal"
        assert (
            actions[0].args["command"] == "python scripts/ship_main.py --preflight"
        )


# ── _collect_symbol_coverage_blockers ──────────────────────────────────


class TestCollectSymbolCoverageBlockers:
    def test_no_tests_channel(self) -> None:
        cr = ChannelResult(
            channel="lint", status="fail", severity="blocking", findings=[]
        )
        mesh = _make_mesh([cr])
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_non_blocking_skipped(self) -> None:
        finding = LintIssue(
            linter="test_channel",
            kind="symbol_uncovered",
            message="msg",
            severity="warning",
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="warning", findings=[finding]
        )
        mesh = _make_mesh([cr])
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_non_symbol_kind_skipped(self) -> None:
        finding = LintIssue(
            linter="test_channel",
            kind="test_failure",
            message="msg",
            severity="blocking",
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="blocking", findings=[finding]
        )
        mesh = _make_mesh([cr])
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_symbol_uncovered_collected(self) -> None:
        finding = LintIssue(
            linter="test_channel",
            kind="symbol_uncovered",
            message="Symbol foo uncovered",
            severity="blocking",
            evidence={"symbol_key": "pkg/mod.py::foo", "missing_lines": [10, 11]},
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="blocking", findings=[finding]
        )
        mesh = _make_mesh([cr])
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["symbol"] == "pkg/mod.py::foo"
        assert blockers[0]["missing_lines"] == [10, 11]

    def test_fallback_to_message_when_no_symbol_key(self) -> None:
        finding = LintIssue(
            linter="test_channel",
            kind="symbol_uncovered",
            message="Symbol bar is not covered",
            severity="blocking",
            evidence={},
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="blocking", findings=[finding]
        )
        mesh = _make_mesh([cr])
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert "bar" in blockers[0]["symbol"]

    def test_unresolved_required_symbol_collected(self) -> None:
        finding = LintIssue(
            linter="test_channel",
            kind="unresolved_required_symbol",
            message="Required symbol missing",
            severity="blocking",
            evidence={"symbol_key": "pkg/mod.py::required_fn"},
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="blocking", findings=[finding]
        )
        mesh = _make_mesh([cr])
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["kind"] == "unresolved_required_symbol"


class TestCompactReport:
    def test_full_mesh_integration(self) -> None:
        cr = ChannelResult(channel="lint", status="pass", severity="none", findings=[])
        mesh = _make_mesh([cr])
        report = format_mesh_report_compact(mesh)
        assert "run_id" in report
        assert "lint" in report["channels"]

    def test_symbol_blockers_add_remediation_loop(self) -> None:
        finding = LintIssue(
            linter="test_channel",
            kind="symbol_uncovered",
            message="Symbol foo uncovered",
            severity="blocking",
            evidence={"symbol_key": "pkg/mod.py::foo", "missing_lines": [10]},
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="blocking", findings=[finding]
        )
        mesh = _make_mesh([cr])
        report = format_mesh_report_compact(mesh)
        assert report["remediation_loop"]["required"] is True
        assert report["remediation_loop"]["policy"].startswith(
            "Add tests for uncovered symbols"
        )
