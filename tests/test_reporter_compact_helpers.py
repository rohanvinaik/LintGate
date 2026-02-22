"""Tests for reporter_compact.py helper functions — targeting uncovered branches."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from lintgate.controlplane.reporter_compact import (
    _build_channel_summary,
    _build_cp_next_actions,
    _collect_symbol_coverage_blockers,
    _count_findings_by_severity,
)
from lintgate.controlplane.types import ChannelResult, MeshResult

# ── _count_findings_by_severity ──────────────────────────────────────


class TestCountFindingsBySeverity:
    def test_empty_index(self) -> None:
        result = _count_findings_by_severity({})
        assert result == {"blocking": 0, "warning": 0, "informational": 0}

    def test_mixed_severities(self) -> None:
        """Exercise the loop with findings that have different severities."""
        index = {
            "fp1": {"severity": "blocking", "count": 2},
            "fp2": {"severity": "warning", "count": 1},
            "fp3": {"severity": "informational", "count": 3},
            "fp4": {"severity": "blocking", "count": 1},
        }
        result = _count_findings_by_severity(index)
        assert result["blocking"] == 3
        assert result["warning"] == 1
        assert result["informational"] == 3

    def test_unknown_severity_ignored(self) -> None:
        """Severity not in totals dict is silently skipped (branch 72->69 exit)."""
        index = {
            "fp1": {"severity": "critical", "count": 5},
            "fp2": {"severity": "blocking", "count": 1},
        }
        result = _count_findings_by_severity(index)
        assert result["blocking"] == 1
        assert result["warning"] == 0
        assert result["informational"] == 0

    def test_missing_severity_key(self) -> None:
        """Finding without severity key defaults to empty string, skipped."""
        index = {
            "fp1": {"count": 2},
        }
        result = _count_findings_by_severity(index)
        assert result == {"blocking": 0, "warning": 0, "informational": 0}

    def test_missing_count_defaults_to_one(self) -> None:
        """Finding without count key defaults to 1."""
        index = {
            "fp1": {"severity": "warning"},
        }
        result = _count_findings_by_severity(index)
        assert result["warning"] == 1


# ── _build_channel_summary ───────────────────────────────────────────


class TestBuildChannelSummary:
    def test_skip_channels_excluded(self) -> None:
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="lint", status="skip"),
            ]
        )
        result = _build_channel_summary(mesh)
        assert "lint" not in result

    def test_pass_channel(self) -> None:
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="pass"),
            ]
        )
        result = _build_channel_summary(mesh)
        assert result["tests"] == "pass"

    def test_fail_channel(self) -> None:
        finding = MagicMock()
        finding.severity = "blocking"
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="lint", status="fail", findings=[finding]),
            ]
        )
        result = _build_channel_summary(mesh)
        assert result["lint"].startswith("fail")

    def test_unknown_status_passthrough(self) -> None:
        """Line 121: status is not 'skip', 'fail', or 'pass' — falls to else."""
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="git", status="error"),
            ]
        )
        result = _build_channel_summary(mesh)
        assert result["git"] == "error"

    def test_timeout_status_passthrough(self) -> None:
        """Another non-standard status to confirm the else branch."""
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="structure", status="timeout"),
            ]
        )
        result = _build_channel_summary(mesh)
        assert result["structure"] == "timeout"


# ── _build_cp_next_actions ───────────────────────────────────────────


class TestBuildCpNextActions:
    def test_no_issues_no_actions(self) -> None:
        counts = {
            "blocking": 0,
            "warning": 0,
            "repairs_available": 0,
        }
        actions = _build_cp_next_actions("run123", counts)
        assert actions == []

    def test_blocking_only(self) -> None:
        counts = {"blocking": 2, "warning": 0, "repairs_available": 0}
        actions = _build_cp_next_actions("run123", counts)
        assert len(actions) == 1
        assert actions[0]["tool"] == "controlplane_get_details"
        assert actions[0]["priority"] == 1

    def test_repairs_available_triggers_action(self) -> None:
        """Line 218: repairs_available > 0 triggers the repair action."""
        counts = {"blocking": 0, "warning": 0, "repairs_available": 3}
        actions = _build_cp_next_actions("run123", counts)
        assert len(actions) == 1
        assert actions[0]["tool"] == "controlplane_apply_repairs"
        assert "3 safe repairs available" in actions[0]["reason"]

    def test_repairs_available_singular(self) -> None:
        """Single repair uses singular noun."""
        counts = {"blocking": 0, "warning": 0, "repairs_available": 1}
        actions = _build_cp_next_actions("run123", counts)
        assert len(actions) == 1
        assert "1 safe repair available" in actions[0]["reason"]

    def test_symbol_blockers_with_repairs(self) -> None:
        """Symbol blockers + repairs: both appear with correct priorities."""
        blockers = [{"kind": "symbol_uncovered", "symbol": "foo"}]
        counts = {"blocking": 0, "warning": 0, "repairs_available": 2}
        actions = _build_cp_next_actions("run123", counts, symbol_blockers=blockers)
        tools = [a["tool"] for a in actions]
        assert "controlplane_get_details" in tools
        assert "controlplane_run" in tools
        assert "controlplane_apply_repairs" in tools
        repair_action = next(a for a in actions if a["tool"] == "controlplane_apply_repairs")
        assert repair_action["priority"] == 4

    def test_warnings_action(self) -> None:
        counts = {"blocking": 0, "warning": 5, "repairs_available": 0}
        actions = _build_cp_next_actions("run123", counts)
        assert len(actions) == 1
        assert actions[0]["args"]["severity"] == "warning"

    def test_all_counts_produce_all_actions(self) -> None:
        counts = {"blocking": 1, "warning": 2, "repairs_available": 1}
        actions = _build_cp_next_actions("run123", counts)
        assert len(actions) == 3


# ── _collect_symbol_coverage_blockers ────────────────────────────────


def _make_finding(
    severity: str = "blocking",
    kind: str = "symbol_uncovered",
    evidence: Any = None,
    message: str = "",
    file: str | None = None,
) -> MagicMock:
    """Create a mock LintIssue-like finding."""
    f = MagicMock()
    f.severity = severity
    f.kind = kind
    f.evidence = evidence if evidence is not None else {}
    f.message = message
    f.file = file
    return f


class TestCollectSymbolCoverageBlockers:
    def test_empty_results(self) -> None:
        mesh = MeshResult(channel_results=[])
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_non_tests_channel_skipped(self) -> None:
        """Only 'tests' channel is inspected."""
        finding = _make_finding()
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="lint", status="fail", findings=[finding]),
            ]
        )
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_non_blocking_severity_skipped(self) -> None:
        """Line 249: non-blocking severity triggers continue (branch 248->249)."""
        finding = _make_finding(severity="warning")
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_wrong_kind_skipped(self) -> None:
        """Line 252: kind not in allowed set triggers continue (branch 251->252)."""
        finding = _make_finding(kind="test_failure")
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        assert _collect_symbol_coverage_blockers(mesh) == []

    def test_symbol_from_evidence_key(self) -> None:
        finding = _make_finding(
            evidence={"symbol_key": "lintgate.config.load_config"},
            file="lintgate/config.py",
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["symbol"] == "lintgate.config.load_config"
        assert blockers[0]["file"] == "lintgate/config.py"
        assert blockers[0]["kind"] == "symbol_uncovered"

    def test_symbol_from_evidence_symbol_fallback(self) -> None:
        finding = _make_finding(
            evidence={"symbol": "my_func"},
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["symbol"] == "my_func"

    def test_symbol_fallback_to_message(self) -> None:
        """Line 257: empty symbol_key falls back to finding.message (branch 256->259)."""
        finding = _make_finding(
            evidence={},
            message="Function do_stuff is uncovered",
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["symbol"] == "Function do_stuff is uncovered"

    def test_missing_lines_included(self) -> None:
        """Line 267: missing_lines is a non-empty list (branch present)."""
        finding = _make_finding(
            evidence={
                "symbol_key": "foo.bar",
                "missing_lines": [10, 11, 12, 13, 14],
            },
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["missing_lines"] == [10, 11, 12, 13, 14]

    def test_missing_lines_truncated_to_12(self) -> None:
        lines = list(range(1, 20))
        finding = _make_finding(
            evidence={"symbol_key": "x", "missing_lines": lines},
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers[0]["missing_lines"]) == 12

    def test_missing_lines_empty_list_excluded(self) -> None:
        finding = _make_finding(
            evidence={"symbol_key": "x", "missing_lines": []},
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert "missing_lines" not in blockers[0]

    def test_missing_lines_non_list_excluded(self) -> None:
        finding = _make_finding(
            evidence={"symbol_key": "x", "missing_lines": "not-a-list"},
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert "missing_lines" not in blockers[0]

    def test_no_file_omits_file_key(self) -> None:
        finding = _make_finding(
            evidence={"symbol_key": "x"},
            file=None,
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert "file" not in blockers[0]

    def test_unresolved_required_symbol_kind(self) -> None:
        """The other allowed kind value."""
        finding = _make_finding(
            kind="unresolved_required_symbol",
            evidence={"symbol_key": "abc"},
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["kind"] == "unresolved_required_symbol"

    def test_evidence_not_dict_treated_as_empty(self) -> None:
        """Non-dict evidence falls back to empty dict."""
        finding = _make_finding(
            evidence="not-a-dict",
            message="fallback message",
        )
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="tests", status="fail", findings=[finding]),
            ]
        )
        blockers = _collect_symbol_coverage_blockers(mesh)
        assert len(blockers) == 1
        assert blockers[0]["symbol"] == "fallback message"
