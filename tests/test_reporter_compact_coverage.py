"""Coverage-targeted tests for lintgate/controlplane/reporter_compact.py.

Targets uncovered branches and functions not exercised by existing test files:
- format_mesh_report_compact (the top-level entry point)
- _build_coherence_dict (all conditional branches)
- _build_counts (repairs, channels_run counting)
- _attach_delta_or_blocking (delta path vs blocking path)
- _build_remediation_loop (symbol blocker truncation)
- _format_fail_status (multiple severity mixes, empty parts)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from lintgate.controlplane.reporter_compact import (
    _attach_delta_or_blocking,
    _build_channel_summary,
    _build_coherence_dict,
    _build_counts,
    _build_cp_next_actions,
    _build_remediation_loop,
    _collect_symbol_coverage_blockers,
    _count_findings_by_severity,
    _format_fail_status,
    format_mesh_report_compact,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    ControlPlaneConfig,
    MeshResult,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue


# ── Helpers ────────────────────────────────────────────────────────────


def _make_event(event_id: str = "evt123") -> SupervisionEvent:
    return SupervisionEvent(
        event_id=event_id,
        surface="mcp",
        project_root="/tmp/test",
        tool_name="test",
    )


def _make_mesh(
    channel_results: list[ChannelResult] | None = None,
    coherence: CoherenceResult | None = None,
    event: SupervisionEvent | None = None,
    duration_ms: float = 42.0,
) -> MeshResult:
    return MeshResult(
        channel_results=channel_results or [],
        coherence=coherence or CoherenceResult(state="stable", summary="ok"),
        event=event or _make_event(),
        duration_ms=duration_ms,
    )


# ── format_mesh_report_compact (top-level) ───────────────────────────


class TestFormatMeshReportCompact:
    def test_basic_output_shape(self) -> None:
        """Top-level compact report has all required keys."""
        mesh = _make_mesh()
        result = format_mesh_report_compact(mesh)
        assert "run_id" in result
        assert "duration_ms" in result
        assert "coherence" in result
        assert "counts" in result
        assert "channels" in result
        assert "next_actions" in result
        assert "finding_index" in result

    def test_run_id_uses_event_id(self) -> None:
        """run_id comes from event.event_id when event is present."""
        mesh = _make_mesh(event=_make_event(event_id="my_stable_id"))
        result = format_mesh_report_compact(mesh)
        assert result["run_id"] == "my_stable_id"

    def test_run_id_fallback_when_no_event(self) -> None:
        """When event is None, falls back to generate_run_id."""
        mesh = _make_mesh()
        mesh.event = None  # type: ignore[assignment]
        result = format_mesh_report_compact(mesh)
        assert isinstance(result["run_id"], str)
        assert len(result["run_id"]) > 0

    def test_duration_ms_rounded(self) -> None:
        mesh = _make_mesh(duration_ms=123.456789)
        result = format_mesh_report_compact(mesh)
        assert result["duration_ms"] == 123.5

    def test_config_defaults_when_none(self) -> None:
        """Passing config=None should not crash."""
        mesh = _make_mesh()
        result = format_mesh_report_compact(mesh, config=None)
        assert result["coherence"]["state"] == "stable"

    def test_explicit_config_accepted(self) -> None:
        mesh = _make_mesh()
        config = ControlPlaneConfig(enabled=True)
        result = format_mesh_report_compact(mesh, config=config)
        assert result["coherence"]["state"] == "stable"

    def test_blocking_issues_in_report_when_no_previous_index(self) -> None:
        """When no previous index, blocking findings appear inline."""
        finding = LintIssue(
            linter="ruff", kind="E501", message="line too long",
            severity="blocking", file="/tmp/test/foo.py", line=10,
        )
        cr = ChannelResult(
            channel="lint", status="fail", severity="blocking",
            findings=[finding],
        )
        mesh = _make_mesh(channel_results=[cr])
        result = format_mesh_report_compact(mesh)
        assert "blocking_issues" in result
        assert len(result["blocking_issues"]) >= 1

    def test_delta_in_report_when_previous_index_provided(self) -> None:
        """When previous_finding_index is given, delta section appears."""
        mesh = _make_mesh()
        prev_index: dict[str, dict[str, Any]] = {
            "old_fp": {"severity": "warning", "channel": "lint", "count": 1},
        }
        result = format_mesh_report_compact(mesh, previous_finding_index=prev_index)
        assert "delta" in result
        assert "blocking_issues" not in result

    def test_symbol_blockers_add_remediation_loop(self) -> None:
        """When symbol coverage blockers exist, remediation_loop appears."""
        finding = LintIssue(
            linter="test_channel", kind="symbol_uncovered",
            message="func uncovered", severity="blocking",
            evidence={"symbol_key": "mod::func"},
        )
        cr = ChannelResult(
            channel="tests", status="fail", severity="blocking",
            findings=[finding],
        )
        mesh = _make_mesh(channel_results=[cr])
        result = format_mesh_report_compact(mesh)
        assert "remediation_loop" in result
        assert result["remediation_loop"]["required"] is True

    def test_no_remediation_loop_without_symbol_blockers(self) -> None:
        mesh = _make_mesh()
        result = format_mesh_report_compact(mesh)
        assert "remediation_loop" not in result


# ── _build_coherence_dict ────────────────────────────────────────────


class TestBuildCoherenceDict:
    def test_minimal_stable(self) -> None:
        """Stable with no action, full confidence, no notes."""
        coh = CoherenceResult(state="stable", summary="All clear")
        d = _build_coherence_dict(coh)
        assert d["state"] == "stable"
        assert d["summary"] == "All clear"
        assert "action" not in d
        assert "confidence" not in d
        assert "classification_notes" not in d

    def test_recommended_action_included(self) -> None:
        coh = CoherenceResult(
            state="isolated", summary="lint fails",
            recommended_action="Fix lint errors",
        )
        d = _build_coherence_dict(coh)
        assert d["action"] == "Fix lint errors"

    def test_confidence_below_1_included(self) -> None:
        coh = CoherenceResult(
            state="coupled", summary="multi-fail",
            confidence=0.65,
        )
        d = _build_coherence_dict(coh)
        assert d["confidence"] == 0.65

    def test_confidence_exactly_1_excluded(self) -> None:
        coh = CoherenceResult(state="stable", summary="ok", confidence=1.0)
        d = _build_coherence_dict(coh)
        assert "confidence" not in d

    def test_classification_notes_included(self) -> None:
        coh = CoherenceResult(
            state="systemic", summary="many failures",
            classification_notes=["edge case: boundary coupled/systemic"],
        )
        d = _build_coherence_dict(coh)
        assert d["classification_notes"] == ["edge case: boundary coupled/systemic"]

    def test_empty_classification_notes_excluded(self) -> None:
        coh = CoherenceResult(
            state="stable", summary="ok",
            classification_notes=[],
        )
        d = _build_coherence_dict(coh)
        assert "classification_notes" not in d

    def test_all_optional_fields_present(self) -> None:
        coh = CoherenceResult(
            state="degraded",
            summary="channel errors",
            recommended_action="Investigate errors",
            confidence=0.5,
            classification_notes=["timeout detected"],
        )
        d = _build_coherence_dict(coh)
        assert d["action"] == "Investigate errors"
        assert d["confidence"] == 0.5
        assert d["classification_notes"] == ["timeout detected"]


# ── _build_counts ────────────────────────────────────────────────────


class TestBuildCounts:
    def test_empty_mesh(self) -> None:
        mesh = _make_mesh()
        sev = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, sev, [])
        assert counts["blocking"] == 0
        assert counts["channels_run"] == 0
        assert counts["repairs_available"] == 0
        assert counts["symbol_blocking"] == 0

    def test_channels_run_excludes_skip(self) -> None:
        crs = [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="git", status="skip"),
            ChannelResult(channel="tests", status="fail"),
        ]
        mesh = _make_mesh(channel_results=crs)
        sev = {"blocking": 1, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, sev, [])
        assert counts["channels_run"] == 2

    def test_repairs_available_summed(self) -> None:
        r1 = RepairAction(channel="lint", summary="fix1")
        r2 = RepairAction(channel="lint", summary="fix2")
        r3 = RepairAction(channel="tests", summary="fix3")
        crs = [
            ChannelResult(channel="lint", status="fail", repairs=[r1, r2]),
            ChannelResult(channel="tests", status="fail", repairs=[r3]),
        ]
        mesh = _make_mesh(channel_results=crs)
        sev = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, sev, [])
        assert counts["repairs_available"] == 3

    def test_symbol_blocking_count(self) -> None:
        blockers = [
            {"kind": "symbol_uncovered", "symbol": "a"},
            {"kind": "symbol_uncovered", "symbol": "b"},
        ]
        mesh = _make_mesh()
        sev = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, sev, blockers)
        assert counts["symbol_blocking"] == 2


# ── _format_fail_status ──────────────────────────────────────────────


class TestFormatFailStatus:
    def test_blocking_only(self) -> None:
        finding = LintIssue(
            linter="ruff", kind="E501", message="m", severity="blocking",
        )
        cr = ChannelResult(channel="lint", status="fail", findings=[finding])
        assert _format_fail_status(cr) == "fail(1 blocking)"

    def test_warning_only(self) -> None:
        finding = LintIssue(
            linter="ruff", kind="W001", message="m", severity="warning",
        )
        cr = ChannelResult(channel="lint", status="fail", findings=[finding])
        assert _format_fail_status(cr) == "fail(1 warning)"

    def test_informational_only(self) -> None:
        finding = LintIssue(
            linter="ruff", kind="I001", message="m", severity="informational",
        )
        cr = ChannelResult(channel="lint", status="fail", findings=[finding])
        assert _format_fail_status(cr) == "fail(1 info)"

    def test_mixed_severities(self) -> None:
        findings = [
            LintIssue(linter="ruff", kind="E501", message="m", severity="blocking"),
            LintIssue(linter="ruff", kind="E502", message="m", severity="blocking"),
            LintIssue(linter="ruff", kind="W001", message="m", severity="warning"),
            LintIssue(linter="ruff", kind="I001", message="m", severity="informational"),
        ]
        cr = ChannelResult(channel="lint", status="fail", findings=findings)
        result = _format_fail_status(cr)
        assert result == "fail(2 blocking, 1 warning, 1 info)"

    def test_empty_findings(self) -> None:
        cr = ChannelResult(channel="lint", status="fail", findings=[])
        assert _format_fail_status(cr) == "fail"


# ── _attach_delta_or_blocking ────────────────────────────────────────


class TestAttachDeltaOrBlocking:
    def test_with_previous_index_adds_delta(self) -> None:
        compact: dict[str, Any] = {}
        current = {"fp1": {"severity": "warning", "count": 1}}
        previous = {"fp1": {"severity": "warning", "count": 1}}
        _attach_delta_or_blocking(compact, current, previous)
        assert "delta" in compact
        assert "blocking_issues" not in compact

    def test_without_previous_index_adds_blocking(self) -> None:
        compact: dict[str, Any] = {}
        current = {
            "fp1": {"severity": "blocking", "count": 1, "channel": "lint"},
            "fp2": {"severity": "warning", "count": 1, "channel": "lint"},
        }
        _attach_delta_or_blocking(compact, current, None)
        assert "delta" not in compact
        assert "blocking_issues" in compact
        assert len(compact["blocking_issues"]) == 1
        assert compact["blocking_issues"][0]["fingerprint"] == "fp1"

    def test_without_previous_index_no_blockers(self) -> None:
        compact: dict[str, Any] = {}
        current = {"fp1": {"severity": "warning", "count": 1}}
        _attach_delta_or_blocking(compact, current, None)
        assert "delta" not in compact
        assert "blocking_issues" not in compact

    def test_empty_current_no_blockers(self) -> None:
        compact: dict[str, Any] = {}
        _attach_delta_or_blocking(compact, {}, None)
        assert "blocking_issues" not in compact


# ── _build_remediation_loop ──────────────────────────────────────────


class TestBuildRemediationLoop:
    def test_basic_structure(self) -> None:
        blockers = [{"kind": "symbol_uncovered", "symbol": "foo"}]
        result = _build_remediation_loop(blockers)
        assert result["required"] is True
        assert result["type"] == "symbol_coverage"
        assert len(result["blocking_symbols"]) == 1
        assert "exit_condition" in result
        assert "policy" in result

    def test_truncates_to_25(self) -> None:
        blockers = [{"kind": "symbol_uncovered", "symbol": f"sym_{i}"} for i in range(30)]
        result = _build_remediation_loop(blockers)
        assert len(result["blocking_symbols"]) == 25

    def test_fewer_than_25_not_truncated(self) -> None:
        blockers = [{"kind": "symbol_uncovered", "symbol": f"sym_{i}"} for i in range(5)]
        result = _build_remediation_loop(blockers)
        assert len(result["blocking_symbols"]) == 5


# ── _build_cp_next_actions (additional coverage) ─────────────────────


class TestBuildCpNextActionsCoverage:
    def test_singular_blocking_finding(self) -> None:
        """Single blocking finding uses singular noun."""
        counts = {"blocking": 1, "warning": 0, "repairs_available": 0}
        actions = _build_cp_next_actions("run1", counts)
        assert len(actions) == 1
        assert "1 blocking finding" in actions[0]["reason"]

    def test_singular_warning(self) -> None:
        counts = {"blocking": 0, "warning": 1, "repairs_available": 0}
        actions = _build_cp_next_actions("run1", counts)
        assert len(actions) == 1
        assert "1 warning" in actions[0]["reason"]
        assert "warnings" not in actions[0]["reason"]

    def test_symbol_blocker_singular(self) -> None:
        counts = {"blocking": 0, "warning": 0, "repairs_available": 0}
        blockers = [{"kind": "symbol_uncovered", "symbol": "foo"}]
        actions = _build_cp_next_actions("run1", counts, symbol_blockers=blockers)
        inspect_action = actions[0]
        assert "1 symbol coverage blocker" in inspect_action["reason"]

    def test_symbol_blockers_plural(self) -> None:
        counts = {"blocking": 0, "warning": 0, "repairs_available": 0}
        blockers = [
            {"kind": "symbol_uncovered", "symbol": "foo"},
            {"kind": "symbol_uncovered", "symbol": "bar"},
        ]
        actions = _build_cp_next_actions("run1", counts, symbol_blockers=blockers)
        assert "2 symbol coverage blockers" in actions[0]["reason"]

    def test_priority_adjustment_with_symbol_blockers(self) -> None:
        """When symbol blockers exist, other action priorities shift."""
        counts = {"blocking": 1, "warning": 1, "repairs_available": 1}
        blockers = [{"kind": "symbol_uncovered", "symbol": "foo"}]
        actions = _build_cp_next_actions("run1", counts, symbol_blockers=blockers)
        # 5 actions total: symbol inspect, rerun, blocking detail, repairs, warnings
        assert len(actions) == 5
        # Symbol blocker inspect is priority 1
        assert actions[0]["priority"] == 1
        assert actions[0]["args"].get("channel") == "tests"
        # Rerun is priority 2
        assert actions[1]["priority"] == 2
        assert actions[1]["tool"] == "controlplane_run"
        # General blocking detail (no channel key) is priority 3
        general_blocking = [
            a for a in actions
            if a.get("args", {}).get("severity") == "blocking"
            and "channel" not in a.get("args", {})
        ]
        assert general_blocking[0]["priority"] == 3
        # Repairs is priority 4
        repair_action = [a for a in actions if a["tool"] == "controlplane_apply_repairs"]
        assert repair_action[0]["priority"] == 4
        # Warnings is priority 5
        warning_action = [a for a in actions if a.get("args", {}).get("severity") == "warning"]
        assert warning_action[0]["priority"] == 5
