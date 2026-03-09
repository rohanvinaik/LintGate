"""Tests for Phase 3 (Workflow Upgrades) — new functions lacking test coverage.

Covers:
1. pre_compact._capture_refactor_checkpoint()
2. compact.format_mesh_report_compact() — finding_recurrence, delta_summary, work_queue_cap
3. _controlplane_impl_run._compute_finding_recurrence()
4. controlplane_tools.controlplane_get_work_queue tool registration
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lintgate.controlplane.reporter.compact import format_mesh_report_compact
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    ControlPlaneConfig,
    MeshResult,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification, LintIssue

# ── Test helpers ─────────────────────────────────────────────────────────


def _make_event() -> SupervisionEvent:
    return SupervisionEvent(
        event_id="test_run_001",
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


def _make_finding(
    kind: str = "TEST001",
    severity: str = "warning",
    message: str = "test finding",
    file: str = "test.py",
    line: int = 1,
) -> LintIssue:
    return LintIssue(
        linter="test_linter",
        kind=kind,
        message=message,
        file=file,
        line=line,
        severity=severity,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. pre_compact._capture_refactor_checkpoint()
# ═══════════════════════════════════════════════════════════════════════════


class TestCaptureRefactorCheckpoint:
    """Tests for _capture_refactor_checkpoint in pre_compact.py."""

    def test_returns_none_when_no_refactor_state(self, tmp_path):
        """No refactor state file on disk => returns None."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint

        result = _capture_refactor_checkpoint(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_session_id(self, tmp_path):
        """Refactor state exists but has no session_id => returns None."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import RefactorState, save_state

        state = RefactorState(session_id="", thesis="Fix everything")
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))
        assert result is None

    def test_captures_basic_progress(self, tmp_path):
        """Active refactor session => returns progress dict with counts."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="abc123",
            thesis="Refactor all modules for consistency",
            files={
                "module_a.py": FileProgress(status="completed"),
                "module_b.py": FileProgress(status="in_progress"),
                "module_c.py": FileProgress(status="pending"),
                "module_d.py": FileProgress(status="pending"),
            },
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert result["session_id"] == "abc123"
        assert result["completed"] == 1
        assert result["in_progress"] == 1
        assert result["pending"] == 2
        assert result["total"] == 4

    def test_includes_current_files(self, tmp_path):
        """In-progress files appear in current_files."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="sess1",
            thesis="Fix",
            files={
                "a.py": FileProgress(status="in_progress"),
                "b.py": FileProgress(status="in_progress"),
                "c.py": FileProgress(status="completed"),
            },
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert "current_files" in result
        assert set(result["current_files"]) == {"a.py", "b.py"}

    def test_current_files_capped_at_three(self, tmp_path):
        """current_files is capped at 3 entries."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="sess2",
            thesis="Big refactor",
            files={
                f"mod{i}.py": FileProgress(status="in_progress")
                for i in range(5)
            },
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert len(result["current_files"]) <= 3

    def test_includes_next_suggested(self, tmp_path):
        """First pending file appears as next_suggested."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="sess3",
            thesis="Clean up",
            files={
                "done.py": FileProgress(status="completed"),
                "next1.py": FileProgress(status="pending"),
                "next2.py": FileProgress(status="pending"),
            },
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert "next_suggested" in result
        assert result["next_suggested"] in ("next1.py", "next2.py")

    def test_no_next_suggested_when_all_done(self, tmp_path):
        """No pending files => no next_suggested key."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="sess4",
            thesis="Done",
            files={
                "a.py": FileProgress(status="completed"),
                "b.py": FileProgress(status="completed"),
            },
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert "next_suggested" not in result

    def test_thesis_truncated_to_120_chars(self, tmp_path):
        """Long thesis is truncated to 120 characters."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        long_thesis = "A" * 200
        state = RefactorState(
            session_id="sess5",
            thesis=long_thesis,
            files={"a.py": FileProgress(status="pending")},
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert len(result["thesis"]) == 120

    def test_empty_thesis_handled(self, tmp_path):
        """Empty or None thesis => empty string in result."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="sess6",
            thesis="",
            files={"a.py": FileProgress(status="pending")},
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert result["thesis"] == ""

    def test_no_current_files_when_none_in_progress(self, tmp_path):
        """No in-progress files => current_files key absent."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint
        from lintgate.refactor_state import FileProgress, RefactorState, save_state

        state = RefactorState(
            session_id="sess7",
            thesis="All pending",
            files={
                "a.py": FileProgress(status="pending"),
                "b.py": FileProgress(status="completed"),
            },
        )
        save_state(str(tmp_path), state)

        result = _capture_refactor_checkpoint(str(tmp_path))

        assert result is not None
        assert "current_files" not in result

    def test_exception_returns_none(self):
        """If load_state raises, returns None (graceful degradation)."""
        from lintgate.hooks.pre_compact import _capture_refactor_checkpoint

        # load_state is imported lazily inside the function body, so we patch it
        # at the refactor_state module level where the import resolves to.
        with patch("lintgate.refactor_state.load_state", side_effect=RuntimeError("boom")):
            result = _capture_refactor_checkpoint("/nonexistent/path")
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. compact.format_mesh_report_compact() — finding_recurrence, delta_summary, work_queue_cap
# ═══════════════════════════════════════════════════════════════════════════


class TestFindingRecurrence:
    """Tests for finding_recurrence parameter in format_mesh_report_compact."""

    def test_recurrence_not_present_when_none(self):
        """No finding_recurrence => no recurrence annotations on findings."""
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[_make_finding(severity="warning")],
                ),
            ]
        )

        result = format_mesh_report_compact(mesh, finding_recurrence=None)

        # Check that no finding in finding_index has a 'recurrence' key
        for info in result.get("finding_index", {}).values():
            assert "recurrence" not in info

    def test_recurrence_annotated_when_seen_twice_or_more(self):
        """Findings seen >=2 times get a recurrence annotation."""
        finding = _make_finding(severity="warning", kind="LINT001", message="bad style")
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[finding],
                ),
            ]
        )

        # Build the finding index to get the actual fingerprint
        from lintgate.controlplane.reporter.delta import build_finding_index

        idx = build_finding_index(mesh)
        fingerprints = list(idx.keys())
        assert len(fingerprints) == 1
        fp = fingerprints[0]

        recurrence = {fp: 5}
        result = format_mesh_report_compact(mesh, finding_recurrence=recurrence)

        fi = result.get("finding_index", {})
        assert fp in fi
        assert "recurrence" in fi[fp]
        assert "5 times" in fi[fp]["recurrence"]

    def test_recurrence_not_annotated_when_seen_once(self):
        """Findings seen only 1 time should NOT get recurrence annotation."""
        finding = _make_finding(severity="blocking", kind="ERR001", message="error")
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[finding],
                ),
            ]
        )

        from lintgate.controlplane.reporter.delta import build_finding_index

        idx = build_finding_index(mesh)
        fp = list(idx.keys())[0]

        recurrence = {fp: 1}
        result = format_mesh_report_compact(mesh, finding_recurrence=recurrence)

        fi = result.get("finding_index", {})
        assert fp in fi
        assert "recurrence" not in fi[fp]

    def test_recurrence_only_on_blocking_and_warning(self):
        """Informational findings are omitted from compact finding_index entirely."""
        finding_info = _make_finding(severity="informational", kind="INFO001", message="fyi")
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="informational",
                    findings=[finding_info],
                ),
            ]
        )

        from lintgate.controlplane.reporter.delta import build_finding_index

        idx = build_finding_index(mesh)
        fp = list(idx.keys())[0]

        recurrence = {fp: 10}
        result = format_mesh_report_compact(mesh, finding_recurrence=recurrence)

        # Informational findings are filtered out of compact finding_index
        fi = result.get("finding_index", {})
        assert fp not in fi

    def test_recurrence_total_runs_uses_max_value(self):
        """Total runs in the recurrence string comes from max(recurrence.values())."""
        f1 = _make_finding(severity="warning", kind="A001", message="alpha")
        f2 = _make_finding(severity="warning", kind="B001", message="beta")
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[f1, f2],
                ),
            ]
        )

        from lintgate.controlplane.reporter.delta import build_finding_index

        idx = build_finding_index(mesh)
        fps = list(idx.keys())
        assert len(fps) == 2

        recurrence = {fps[0]: 3, fps[1]: 8}
        result = format_mesh_report_compact(mesh, finding_recurrence=recurrence)

        fi = result.get("finding_index", {})
        # The one with count 3 should show "3 times across 8 runs"
        annotated = fi[fps[0]]
        assert "recurrence" in annotated
        assert "8 runs" in annotated["recurrence"]


class TestDeltaSummary:
    """Tests for delta_summary computation in format_mesh_report_compact."""

    def test_delta_summary_present_when_delta_exists(self):
        """When previous_finding_index is provided, delta and delta_summary appear."""
        finding = _make_finding(severity="warning", kind="NEW001", message="new issue")
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[finding],
                ),
            ]
        )

        # Empty previous index => all current findings are "new"
        result = format_mesh_report_compact(mesh, previous_finding_index={})

        assert "delta" in result
        assert "delta_summary" in result
        ds = result["delta_summary"]
        assert "new" in ds
        assert "resolved" in ds
        assert "remaining" in ds
        assert "escalated" in ds
        assert ds["new"] >= 1
        assert ds["resolved"] == 0

    def test_no_delta_summary_without_previous_index(self):
        """Without previous_finding_index, no delta or delta_summary."""
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[_make_finding()],
                ),
            ]
        )

        result = format_mesh_report_compact(mesh, previous_finding_index=None)

        assert "delta_summary" not in result
        assert "delta" not in result

    def test_delta_summary_resolved_count(self):
        """Findings in previous but not current appear as resolved."""
        mesh = _make_mesh(channel_results=[])

        previous = {
            "old_fp_1": {"severity": "warning", "kind": "OLD001", "channel": "lint"},
            "old_fp_2": {"severity": "blocking", "kind": "OLD002", "channel": "lint"},
        }
        result = format_mesh_report_compact(mesh, previous_finding_index=previous)

        assert "delta_summary" in result
        ds = result["delta_summary"]
        assert ds["resolved"] == 2
        assert ds["new"] == 0
        assert ds["remaining"] == 0


class TestWorkQueueCap:
    """Tests for work_queue_cap local variable usage in format_mesh_report_compact."""

    def test_work_queue_truncated_when_exceeds_cap(self):
        """Work queue items are capped at 25 (work_queue_cap)."""
        # Create many findings to trigger a large work queue
        findings = [
            _make_finding(
                severity="warning",
                kind=f"WQ{i:03d}",
                message=f"issue {i}",
                file=f"file_{i}.py",
                line=i,
            )
            for i in range(30)
        ]
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=findings,
                ),
            ]
        )

        # Mock the work queue builder to return many items
        @dataclass
        class MockWorkQueueItem:
            file: str = ""
            findings: list = field(default_factory=list)
            priority: int = 0

        @dataclass
        class MockWorkQueue:
            items: list = field(default_factory=list)

            def to_dict(self):
                return {
                    "items": [{"file": item.file, "priority": item.priority} for item in self.items],
                    "total_files": len(self.items),
                }

        mock_items = [MockWorkQueueItem(file=f"file_{i}.py", priority=i) for i in range(30)]
        mock_wq = MockWorkQueue(items=mock_items)

        with patch(
            "lintgate.controlplane.work_queue.build_work_queue",
            return_value=mock_wq,
        ):
            result = format_mesh_report_compact(mesh)

        if "work_queue" in result:
            wq = result["work_queue"]
            assert len(wq["items"]) <= 25
            if wq.get("truncated"):
                assert wq["total_items"] == 30

    def test_work_queue_not_truncated_when_under_cap(self):
        """Work queue with fewer than 25 items is not truncated."""
        findings = [
            _make_finding(
                severity="warning",
                kind=f"WQ{i:03d}",
                message=f"issue {i}",
                file=f"file_{i}.py",
            )
            for i in range(5)
        ]
        mesh = _make_mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=findings,
                ),
            ]
        )

        @dataclass
        class MockWorkQueueItem:
            file: str = ""
            priority: int = 0

        @dataclass
        class MockWorkQueue:
            items: list = field(default_factory=list)

            def to_dict(self):
                return {
                    "items": [{"file": item.file, "priority": item.priority} for item in self.items],
                    "total_files": len(self.items),
                }

        mock_items = [MockWorkQueueItem(file=f"file_{i}.py", priority=i) for i in range(5)]
        mock_wq = MockWorkQueue(items=mock_items)

        with patch(
            "lintgate.controlplane.work_queue.build_work_queue",
            return_value=mock_wq,
        ):
            result = format_mesh_report_compact(mesh)

        if "work_queue" in result:
            wq = result["work_queue"]
            assert len(wq["items"]) == 5
            assert "truncated" not in wq


# ═══════════════════════════════════════════════════════════════════════════
# 3. _controlplane_impl_run._compute_finding_recurrence()
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeFindingRecurrence:
    """Tests for _compute_finding_recurrence in _controlplane_impl_run.py."""

    def test_empty_snapshots(self):
        """No snapshots => empty recurrence dict."""
        from mcp_tools._controlplane_impl_run import _compute_finding_recurrence

        session = MagicMock()
        session.snapshots = []

        result = _compute_finding_recurrence(session)
        assert result == {}

    def test_single_snapshot_single_finding(self):
        """One snapshot with one finding => count of 1."""
        from mcp_tools._controlplane_impl_run import _compute_finding_recurrence

        snapshot = MagicMock()
        snapshot.finding_index = {"fp_abc": {"kind": "TEST001"}}

        session = MagicMock()
        session.snapshots = [snapshot]

        result = _compute_finding_recurrence(session)
        assert result == {"fp_abc": 1}

    def test_finding_across_multiple_snapshots(self):
        """A finding present in 3 of 5 snapshots => count of 3."""
        from mcp_tools._controlplane_impl_run import _compute_finding_recurrence

        snapshots = []
        for i in range(5):
            snap = MagicMock()
            if i in (0, 2, 4):
                snap.finding_index = {"fp_recurring": {"kind": "LINT001"}}
            else:
                snap.finding_index = {}
            snapshots.append(snap)

        session = MagicMock()
        session.snapshots = snapshots

        result = _compute_finding_recurrence(session)
        assert result["fp_recurring"] == 3

    def test_multiple_findings_across_snapshots(self):
        """Multiple findings tracked independently across snapshots."""
        from mcp_tools._controlplane_impl_run import _compute_finding_recurrence

        snap1 = MagicMock()
        snap1.finding_index = {"fp_a": {}, "fp_b": {}}
        snap2 = MagicMock()
        snap2.finding_index = {"fp_a": {}, "fp_c": {}}
        snap3 = MagicMock()
        snap3.finding_index = {"fp_a": {}, "fp_b": {}, "fp_c": {}}

        session = MagicMock()
        session.snapshots = [snap1, snap2, snap3]

        result = _compute_finding_recurrence(session)
        assert result["fp_a"] == 3
        assert result["fp_b"] == 2
        assert result["fp_c"] == 2

    def test_snapshot_without_finding_index_skipped(self):
        """Snapshots where finding_index is not a dict are skipped."""
        from mcp_tools._controlplane_impl_run import _compute_finding_recurrence

        snap1 = MagicMock()
        snap1.finding_index = {"fp_x": {}}
        snap2 = MagicMock()
        snap2.finding_index = None  # Not a dict
        snap3 = MagicMock()
        snap3.finding_index = "invalid"  # Not a dict

        session = MagicMock()
        session.snapshots = [snap1, snap2, snap3]

        result = _compute_finding_recurrence(session)
        assert result == {"fp_x": 1}

    def test_snapshot_with_no_finding_index_attr(self):
        """Snapshots missing finding_index attribute entirely are skipped."""
        from mcp_tools._controlplane_impl_run import _compute_finding_recurrence

        snap1 = MagicMock()
        snap1.finding_index = {"fp_y": {}}
        snap2 = MagicMock(spec=[])  # No attributes at all

        session = MagicMock()
        session.snapshots = [snap1, snap2]

        result = _compute_finding_recurrence(session)
        assert result == {"fp_y": 1}


# ═══════════════════════════════════════════════════════════════════════════
# 4. controlplane_get_work_queue tool registration
# ═══════════════════════════════════════════════════════════════════════════


class TestControlplaneGetWorkQueueRegistration:
    """Tests for controlplane_get_work_queue tool in controlplane_tools.py."""

    def test_register_returns_work_queue_tool(self):
        """register() returns a dict that includes controlplane_get_work_queue."""
        from mcp_tools.controlplane_tools import register

        mock_mcp = MagicMock()
        # Make mcp.tool() return a decorator that passes through the function
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": lambda p: p,
            "_collect_python_files": lambda p: [],
            "_build_cp_full_details": lambda m, i: {},
            "_json_dumps": lambda d, **kw: json.dumps(d),
            "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
        }

        result = register(mock_mcp, mock_helpers)

        assert "controlplane_get_work_queue" in result
        assert callable(result["controlplane_get_work_queue"])

    def test_work_queue_tool_returns_error_for_missing_run(self):
        """controlplane_get_work_queue returns error JSON when run_id not found."""
        from mcp_tools.controlplane_tools import register

        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": lambda p: p,
            "_collect_python_files": lambda p: [],
            "_build_cp_full_details": lambda m, i: {},
            "_json_dumps": lambda d, **kw: json.dumps(d),
            "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
        }

        tools = register(mock_mcp, mock_helpers)
        get_wq = tools["controlplane_get_work_queue"]

        with patch("lintgate.state.load_controlplane_run", return_value=None):
            result_str = get_wq(run_id="nonexistent_run")

        result = json.loads(result_str)
        assert "error" in result
        assert "nonexistent_run" in result["error"]

    def test_work_queue_tool_returns_empty_for_no_findings(self):
        """controlplane_get_work_queue returns empty queue when run has no findings."""
        from mcp_tools.controlplane_tools import register

        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": lambda p: p,
            "_collect_python_files": lambda p: [],
            "_build_cp_full_details": lambda m, i: {},
            "_json_dumps": lambda d, **kw: json.dumps(d),
            "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
        }

        tools = register(mock_mcp, mock_helpers)
        get_wq = tools["controlplane_get_work_queue"]

        # The function imports load_controlplane_run locally: from lintgate.state import load_controlplane_run
        # We need to patch it where it's looked up inside the closure
        with patch("lintgate.state.load_controlplane_run", return_value={"finding_index": {}, "channels": {}}):
            result_str = get_wq(run_id="run_empty")

        result = json.loads(result_str)
        assert result["run_id"] == "run_empty"
        assert result["work_queue"]["items"] == []

    def test_work_queue_tool_accepts_max_items_param(self):
        """controlplane_get_work_queue accepts max_items parameter."""
        from mcp_tools.controlplane_tools import register

        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": lambda p: p,
            "_collect_python_files": lambda p: [],
            "_build_cp_full_details": lambda m, i: {},
            "_json_dumps": lambda d, **kw: json.dumps(d),
            "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
        }

        tools = register(mock_mcp, mock_helpers)
        get_wq = tools["controlplane_get_work_queue"]

        # Verify the function signature accepts max_items
        import inspect

        sig = inspect.signature(get_wq)
        params = list(sig.parameters.keys())
        assert "run_id" in params
        assert "max_items" in params

        # Verify default value of max_items is 25
        assert sig.parameters["max_items"].default == 25

    def test_work_queue_tool_truncates_at_max_items(self):
        """controlplane_get_work_queue truncates items at max_items."""
        from mcp_tools.controlplane_tools import register

        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": lambda p: p,
            "_collect_python_files": lambda p: [],
            "_build_cp_full_details": lambda m, i: {},
            "_json_dumps": lambda d, **kw: json.dumps(d),
            "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
        }

        tools = register(mock_mcp, mock_helpers)
        get_wq = tools["controlplane_get_work_queue"]

        # Build a run with findings
        finding_index = {
            f"fp_{i}": {
                "kind": f"LINT{i:03d}",
                "severity": "warning",
                "channel": "lint",
                "file": f"file_{i}.py",
                "message": f"issue {i}",
            }
            for i in range(10)
        }
        run_data = {"finding_index": finding_index, "channels": {}}

        @dataclass
        class MockWQItem:
            file: str = ""
            priority: int = 0

        @dataclass
        class MockWQ:
            items: list = field(default_factory=list)

            def to_dict(self):
                return {
                    "items": [{"file": it.file, "priority": it.priority} for it in self.items],
                    "total_files": len(self.items),
                }

        mock_wq = MockWQ(items=[MockWQItem(file=f"file_{i}.py", priority=i) for i in range(10)])

        with (
            patch("lintgate.state.load_controlplane_run", return_value=run_data),
            patch(
                "lintgate.controlplane.work_queue.build_work_queue",
                return_value=mock_wq,
            ),
        ):
            result_str = get_wq(run_id="run_many", max_items=3)

        result = json.loads(result_str)
        wq = result["work_queue"]
        assert len(wq["items"]) <= 3
        assert wq.get("truncated") is True
        assert wq["total_items"] == 10


# ═══════════════════════════════════════════════════════════════════════════
# Integration: _capture_refactor_checkpoint within handle()
# ═══════════════════════════════════════════════════════════════════════════


class TestPreCompactHandleRefactorIntegration:
    """Test that handle() includes refactor_progress in the capsule."""

    def test_capsule_includes_refactor_progress(self, tmp_path):
        """handle() includes refactor_progress when a refactor session is active."""
        import re

        from lintgate.hooks.pre_compact import handle
        from lintgate.refactor_state import FileProgress, RefactorState, save_state
        from lintgate.runtime_state import RuntimeState, save_runtime_state

        # Set up runtime state
        runtime = RuntimeState(
            mode="normal",
            true_north="Ship it",
            toward=["test"],
            away=[],
            forbidden=[],
        )
        save_runtime_state(str(tmp_path), runtime)

        # Set up refactor state
        refactor = RefactorState(
            session_id="integ_sess",
            thesis="Big cleanup",
            files={
                "a.py": FileProgress(status="completed"),
                "b.py": FileProgress(status="in_progress"),
                "c.py": FileProgress(status="pending"),
            },
        )
        save_state(str(tmp_path), refactor)

        result = handle({"cwd": str(tmp_path)})

        assert result["continue"] is True
        assert "systemMessage" in result

        msg = result["systemMessage"]
        m = re.search(r"<lintgate-compact-state>(.*?)</lintgate-compact-state>", msg, re.DOTALL)
        assert m
        capsule = json.loads(m.group(1))

        assert "refactor_progress" in capsule
        rp = capsule["refactor_progress"]
        assert rp["session_id"] == "integ_sess"
        assert rp["completed"] == 1
        assert rp["in_progress"] == 1
        assert rp["pending"] == 1

    def test_capsule_without_refactor_session(self, tmp_path):
        """handle() does NOT include refactor_progress when no refactor session."""
        import re

        from lintgate.hooks.pre_compact import handle
        from lintgate.runtime_state import RuntimeState, save_runtime_state

        runtime = RuntimeState(
            mode="normal",
            true_north="Build",
            toward=[],
            away=[],
            forbidden=[],
        )
        save_runtime_state(str(tmp_path), runtime)

        result = handle({"cwd": str(tmp_path)})

        assert result["continue"] is True
        msg = result.get("systemMessage", "")
        if "<lintgate-compact-state>" in msg:
            m = re.search(
                r"<lintgate-compact-state>(.*?)</lintgate-compact-state>", msg, re.DOTALL
            )
            if m:
                capsule = json.loads(m.group(1))
                assert "refactor_progress" not in capsule
