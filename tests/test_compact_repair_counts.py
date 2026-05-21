"""Tests for typed repair count breakdown in compact reporter.

Verifies that _build_counts() and _build_cp_next_actions() distinguish
executable repairs (command with payload, safe_delete) from advisory-only
repairs (create_test_skeleton, config_patch, command without payload).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import pytest

from lintgate.controlplane.reporter.compact import (
    _build_counts,
    _build_cp_next_actions,
    _classify_repair,
)


# ── Lightweight stubs ────────────────────────────────────────────────


@dataclass
class StubRepairAction:
    action_id: str = "r1"
    channel: str = "lint"
    kind: str = "command"
    summary: str = "fix it"
    payload: dict[str, Any] = field(default_factory=dict)
    safe: bool = True


@dataclass
class StubChannelResult:
    channel: str = "lint"
    status: str = "pass"
    severity: str = "none"
    findings: list = field(default_factory=list)
    repairs: list = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 10.0
    error_message: str | None = None


@dataclass
class StubCoherence:
    state: str = "stable"
    summary: str = ""
    recommended_action: str = ""
    confidence: float = 1.0
    classification_notes: list[str] = field(default_factory=list)


@dataclass
class StubEvent:
    event_id: str = "test-run-001"


@dataclass
class StubMeshResult:
    event: StubEvent = field(default_factory=StubEvent)
    channel_results: list = field(default_factory=list)
    coherence: StubCoherence = field(default_factory=StubCoherence)
    duration_ms: float = 100.0


# ── _classify_repair unit tests ──────────────────────────────────────


class TestClassifyRepair:
    def test_command_with_payload_safe(self):
        r = StubRepairAction(kind="command", payload={"command": "ruff check --fix"}, safe=True)
        assert _classify_repair(r) == "safe_executable"

    def test_command_with_payload_unsafe(self):
        r = StubRepairAction(kind="command", payload={"command": "rm -rf /"}, safe=False)
        assert _classify_repair(r) == "unsafe_executable"

    def test_command_empty_payload(self):
        r = StubRepairAction(kind="command", payload={}, safe=True)
        assert _classify_repair(r) == "advisory_only"

    def test_command_empty_command_string(self):
        r = StubRepairAction(kind="command", payload={"command": ""}, safe=True)
        assert _classify_repair(r) == "advisory_only"

    def test_safe_delete_safe(self):
        r = StubRepairAction(kind="safe_delete", payload={"target_path": "tests/old.py"}, safe=True)
        assert _classify_repair(r) == "safe_executable"

    def test_safe_delete_unsafe(self):
        r = StubRepairAction(kind="safe_delete", payload={"target_path": "tests/old.py"}, safe=False)
        assert _classify_repair(r) == "unsafe_executable"

    def test_create_test_skeleton(self):
        r = StubRepairAction(kind="create_test_skeleton", payload={"file": "test_x.py"})
        assert _classify_repair(r) == "advisory_only"

    def test_config_patch(self):
        r = StubRepairAction(kind="config_patch", payload={"key": "val"})
        assert _classify_repair(r) == "advisory_only"


# ── _build_counts tests ─────────────────────────────────────────────


class TestBuildCountsTypedRepairBreakdown:
    def test_mixed_repair_kinds(self):
        """Mixed repairs are bucketed correctly into sub-counts."""
        repairs = [
            StubRepairAction(action_id="r1", kind="command", payload={"command": "ruff --fix"}, safe=True),
            StubRepairAction(action_id="r2", kind="command", payload={}, safe=True),
            StubRepairAction(action_id="r3", kind="create_test_skeleton", payload={"file": "t.py"}),
            StubRepairAction(action_id="r4", kind="safe_delete", payload={"target_path": "tests/x.py"}, safe=True),
            StubRepairAction(action_id="r5", kind="command", payload={"command": "dangerous"}, safe=False),
        ]
        mesh = StubMeshResult(
            channel_results=[StubChannelResult(repairs=repairs, status="fail")],
        )
        severity_counts = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, severity_counts, [])

        rc = counts["repair_counts"]
        assert rc["safe_executable"] == 2  # r1 (command+payload+safe) + r4 (safe_delete+safe)
        assert rc["unsafe_executable"] == 1  # r5 (command+payload+unsafe)
        assert rc["advisory_only"] == 2  # r2 (command, no payload) + r3 (create_test_skeleton)

    def test_backward_compat_repairs_available_key(self):
        """repairs_available equals safe_executable + unsafe_executable."""
        repairs = [
            StubRepairAction(kind="command", payload={"command": "fix"}, safe=True),
            StubRepairAction(kind="command", payload={"command": "risky"}, safe=False),
            StubRepairAction(kind="create_test_skeleton", payload={"file": "t.py"}),
        ]
        mesh = StubMeshResult(
            channel_results=[StubChannelResult(repairs=repairs, status="fail")],
        )
        severity_counts = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, severity_counts, [])

        assert "repairs_available" in counts
        rc = counts["repair_counts"]
        assert counts["repairs_available"] == rc["safe_executable"] + rc["unsafe_executable"]
        assert counts["repairs_available"] == 2  # excludes advisory_only

    def test_all_advisory_repairs_available_zero(self):
        """When all repairs are advisory, repairs_available is 0."""
        repairs = [
            StubRepairAction(kind="create_test_skeleton", payload={"file": "t.py"}),
            StubRepairAction(kind="config_patch", payload={"key": "val"}),
        ]
        mesh = StubMeshResult(
            channel_results=[StubChannelResult(repairs=repairs, status="fail")],
        )
        severity_counts = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, severity_counts, [])

        assert counts["repairs_available"] == 0
        assert counts["repair_counts"]["advisory_only"] == 2

    def test_no_repairs(self):
        """Zero repairs produce all-zero sub-counts."""
        mesh = StubMeshResult(
            channel_results=[StubChannelResult(repairs=[], status="pass")],
        )
        severity_counts = {"blocking": 0, "warning": 0, "informational": 0}
        counts = _build_counts(mesh, severity_counts, [])

        assert counts["repairs_available"] == 0
        rc = counts["repair_counts"]
        assert rc == {"safe_executable": 0, "unsafe_executable": 0, "advisory_only": 0}


# ── _build_cp_next_actions tests ─────────────────────────────────────


class TestNextActionsRepairGating:
    def test_omits_repair_when_no_executable(self):
        """All advisory repairs -> no controlplane_apply_repairs in next_actions."""
        counts = {
            "blocking": 0,
            "warning": 0,
            "informational": 0,
            "channels_run": 1,
            "repairs_available": 0,
            "repair_counts": {"safe_executable": 0, "unsafe_executable": 0, "advisory_only": 3},
            "symbol_blocking": 0,
        }
        actions = _build_cp_next_actions("run-1", counts)
        tool_names = [a.tool for a in actions]
        assert "controlplane_apply_repairs" not in tool_names

    def test_includes_repair_when_safe_executable(self):
        """One safe command repair -> controlplane_apply_repairs appears."""
        counts = {
            "blocking": 0,
            "warning": 0,
            "informational": 0,
            "channels_run": 1,
            "repairs_available": 1,
            "repair_counts": {"safe_executable": 1, "unsafe_executable": 0, "advisory_only": 2},
            "symbol_blocking": 0,
        }
        actions = _build_cp_next_actions("run-1", counts)
        tool_names = [a.tool for a in actions]
        assert "controlplane_apply_repairs" in tool_names

    def test_repair_reason_mentions_safe_executable(self):
        """Reason string should say 'safe executable repair(s)'."""
        counts = {
            "blocking": 0,
            "warning": 0,
            "informational": 0,
            "channels_run": 1,
            "repairs_available": 3,
            "repair_counts": {"safe_executable": 3, "unsafe_executable": 0, "advisory_only": 0},
            "symbol_blocking": 0,
        }
        actions = _build_cp_next_actions("run-1", counts)
        repair_actions = [a for a in actions if a.tool == "controlplane_apply_repairs"]
        assert len(repair_actions) == 1
        assert "safe executable" in repair_actions[0].reason
        assert "3" in repair_actions[0].reason

    def test_no_repair_counts_key_backward_compat(self):
        """If repair_counts is missing (old data), no crash and no repair action."""
        counts = {
            "blocking": 0,
            "warning": 0,
            "informational": 0,
            "channels_run": 1,
            "repairs_available": 5,
            "symbol_blocking": 0,
        }
        actions = _build_cp_next_actions("run-1", counts)
        tool_names = [a.tool for a in actions]
        assert "controlplane_apply_repairs" not in tool_names


# ── _collect_pending_repairs tests ───────────────────────────────────


class TestCollectPendingRepairsFiltersAdvisory:
    def test_advisory_only_repairs_yield_empty_pending(self):
        """When all repairs are advisory-only, pending list is empty."""
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        # Build a mock session with a snapshot that has advisory-only repairs
        advisory_repairs = [
            {
                "action_id": "a1",
                "kind": "create_test_skeleton",
                "summary": "gen test",
                "safe": True,
                "channel": "tests",
                "payload": {"file": "test_x.py"},
            },
            {
                "action_id": "a2",
                "kind": "config_patch",
                "summary": "patch config",
                "safe": True,
                "channel": "lint",
                "payload": {"key": "val"},
            },
            {
                "action_id": "a3",
                "kind": "command",
                "summary": "empty cmd",
                "safe": True,
                "channel": "lint",
                "payload": {},  # no command string
            },
        ]

        @dataclass
        class MockSnapshot:
            run_id: str = "run-advisory"
            repairs_proposed: list[str] = field(default_factory=lambda: ["a1", "a2", "a3"])

        @dataclass
        class MockSession:
            snapshots: list = field(default_factory=list)
            repair_outcomes: dict = field(default_factory=dict)

        session = MockSession(snapshots=[MockSnapshot()])

        # Mock _select_repair_source to return our snapshot + run_details with advisory repairs
        run_details = {
            "channels": {
                "tests": {"repairs": advisory_repairs[:1]},
                "lint": {"repairs": advisory_repairs[1:]},
            }
        }

        with mock.patch(
            "mcp_tools._controlplane_impl_feedback._select_repair_source",
            return_value=(MockSnapshot(), run_details, []),
        ):
            pending, skipped = _collect_pending_repairs(session, [], False)

        assert pending == []
        assert any(d.get("reason") == "no_executable_repairs" for d in skipped)

    def test_mixed_repairs_only_executable_collected(self):
        """Mixed repairs: only executable ones end up in pending."""
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        mixed_repairs = [
            {
                "action_id": "e1",
                "kind": "command",
                "summary": "ruff fix",
                "safe": True,
                "channel": "lint",
                "payload": {"command": "ruff check --fix"},
            },
            {
                "action_id": "e2",
                "kind": "safe_delete",
                "summary": "delete stale",
                "safe": True,
                "channel": "tests",
                "payload": {"target_path": "tests/old.py"},
            },
            {
                "action_id": "a1",
                "kind": "create_test_skeleton",
                "summary": "gen test",
                "safe": True,
                "channel": "tests",
                "payload": {"file": "test_x.py"},
            },
        ]

        @dataclass
        class MockSnapshot:
            run_id: str = "run-mixed"
            repairs_proposed: list[str] = field(
                default_factory=lambda: ["e1", "e2", "a1"]
            )

        @dataclass
        class MockSession:
            snapshots: list = field(default_factory=list)
            repair_outcomes: dict = field(default_factory=dict)

        session = MockSession(snapshots=[MockSnapshot()])
        run_details = {"channels": {"lint": {"repairs": mixed_repairs}}}

        with mock.patch(
            "mcp_tools._controlplane_impl_feedback._select_repair_source",
            return_value=(MockSnapshot(), run_details, []),
        ):
            pending, skipped = _collect_pending_repairs(session, [], False)

        pending_ids = {r["action_id"] for r in pending}
        assert pending_ids == {"e1", "e2"}
        assert "a1" not in pending_ids
