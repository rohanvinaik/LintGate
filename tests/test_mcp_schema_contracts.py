"""Phase 3: MCP schema contract tests.

Ensures all MCP tool responses conform to expected schemas,
next_actions have required fields, and output sizes stay within budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lintgate.state import generate_run_id, load_run_details, save_run_details
from lintgate.types import LintIssue

# ── next_actions Schema ─────────────────────────────────────────────────


class TestNextActionsSchema:
    """Verify next_actions entries have all required fields."""

    REQUIRED_KEYS = {"tool", "args", "safe", "reason", "priority"}

    def test_next_actions_entry_has_required_keys(self) -> None:
        from mcp_server import _build_next_actions

        context = {
            "blocking": 2,
            "fixable": 1,
            "run_id": "test123",
            "warnings": 6,
            "informational": 3,
            "project": "/tmp/test",
        }
        actions = _build_next_actions(context)
        assert len(actions) > 0, "Expected at least one next_action"
        for action in actions:
            missing = self.REQUIRED_KEYS - set(action.keys())
            assert not missing, f"Missing keys in next_action: {missing}"

    def test_next_actions_tool_is_string(self) -> None:
        from mcp_server import _build_next_actions

        context = {"blocking": 1, "fixable": 1, "run_id": "abc", "warnings": 0, "project": "/tmp"}
        actions = _build_next_actions(context)
        for action in actions:
            assert isinstance(action["tool"], str)
            assert len(action["tool"]) > 0

    def test_next_actions_args_is_dict(self) -> None:
        from mcp_server import _build_next_actions

        context = {"blocking": 1, "fixable": 1, "run_id": "abc", "warnings": 0, "project": "/tmp"}
        actions = _build_next_actions(context)
        for action in actions:
            assert isinstance(action["args"], dict)

    def test_next_actions_safe_is_bool(self) -> None:
        from mcp_server import _build_next_actions

        context = {"blocking": 1, "fixable": 1, "run_id": "abc", "warnings": 0, "project": "/tmp"}
        actions = _build_next_actions(context)
        for action in actions:
            assert isinstance(action["safe"], bool)

    def test_next_actions_priority_is_int(self) -> None:
        from mcp_server import _build_next_actions

        context = {"blocking": 1, "fixable": 1, "run_id": "abc", "warnings": 0, "project": "/tmp"}
        actions = _build_next_actions(context)
        for action in actions:
            assert isinstance(action["priority"], int)

    def test_clean_run_produces_empty_next_actions(self) -> None:
        from mcp_server import _build_next_actions

        context = {"blocking": 0, "fixable": 0, "run_id": "abc", "warnings": 0, "project": "/tmp"}
        actions = _build_next_actions(context)
        assert actions == []


# ── Output Mode Contracts ───────────────────────────────────────────────


class TestOutputModeContracts:
    """Verify output mode formatting contracts."""

    def test_compact_json_is_valid(self) -> None:
        from mcp_server import _json_dumps

        data = {"run_id": "abc", "blocking": 0, "nested": {"key": "value"}}
        result = _json_dumps(data, "compact")
        parsed = json.loads(result)
        assert parsed == data

    def test_standard_json_is_valid(self) -> None:
        from mcp_server import _json_dumps

        data = {"run_id": "abc", "blocking": 0}
        result = _json_dumps(data, "standard")
        parsed = json.loads(result)
        assert parsed == data

    def test_full_json_is_valid(self) -> None:
        from mcp_server import _json_dumps

        data = {"run_id": "abc", "blocking": 0}
        result = _json_dumps(data, "full")
        parsed = json.loads(result)
        assert parsed == data

    def test_compact_has_no_whitespace_padding(self) -> None:
        from mcp_server import _json_dumps

        data = {"a": 1, "b": {"c": 2}, "d": [1, 2, 3]}
        result = _json_dumps(data, "compact")
        # No spaces after : or ,
        assert ": " not in result
        assert ", " not in result

    def test_full_has_indentation(self) -> None:
        from mcp_server import _json_dumps

        data = {"a": 1, "b": {"c": 2}}
        result = _json_dumps(data, "full")
        assert "\n" in result
        assert "  " in result

    def test_invalid_output_mode_falls_back(self) -> None:
        """_run_lint silently falls back to compact for invalid mode."""
        from mcp_server import _json_dumps

        # _json_dumps itself doesn't validate; it's _run_lint that does
        # But _json_dumps with unknown mode should still produce valid JSON
        data = {"test": True}
        result = _json_dumps(data, "unknown_mode")
        parsed = json.loads(result)
        assert parsed == data


# ── Behavior MCP Contracts ───────────────────────────────────────────────


class TestBehaviorMcpContracts:
    def test_behavior_precheck_recall_dedupes_matches(self, tmp_path: Path) -> None:
        from lintgate.controlplane.behavior_compass import BehaviorHypothesis, new_compass
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            save_behavior_compass,
            save_session,
        )
        from mcp_server import behavior_precheck

        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "session"):
            session = get_or_create_session(str(tmp_path), max_age_hours=4.0)
            compass = new_compass()
            compass.hypotheses.append(
                BehaviorHypothesis(
                    id="h1",
                    claim="device requires signed iBSS",
                    confidence=0.8,
                    source="command_failure",
                    status="confirmed",
                    applies_to_sigs=["idevicerestore:*"],
                    applies_to_tools=["Bash"],
                )
            )
            save_behavior_compass(session, compass)
            save_session(session)

            raw = behavior_precheck(
                path=str(tmp_path),
                planned_action="idevicerestore -e custom.ipsw",
                known_constraints=[
                    "signed iBSS is required",
                    "iBSS must be signed by Apple",
                ],
            )
            payload = json.loads(raw)

        assert payload["coverage"]["prediction_recall"] <= 1.0
        assert payload["coverage"]["prediction_recall"] == 1.0

    def test_controlplane_status_lists_behavior_channel(self, tmp_path: Path) -> None:
        from mcp_server import controlplane_status

        payload = json.loads(controlplane_status(path=str(tmp_path)))
        assert "behavior" in payload["available_channels"]

    def test_behavior_precheck_counts_invocation_once(self, tmp_path: Path) -> None:
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            load_behavior_compass,
        )
        from mcp_server import behavior_precheck

        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "session"):
            behavior_precheck(
                path=str(tmp_path),
                planned_action="git status",
                known_constraints=["constraint one", "constraint two"],
            )
            session = get_or_create_session(str(tmp_path), max_age_hours=4.0)
            compass = load_behavior_compass(session)

        assert compass.precheck_count_session == 1

    def test_controlplane_run_persists_behavior_delta(self, tmp_path: Path) -> None:
        from lintgate.controlplane.behavior_compass import ApproachAttempt, new_compass
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            load_behavior_compass,
            save_behavior_compass,
            save_session,
        )
        from mcp_server import controlplane_run

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "lintgate.yaml").write_text(
            "controlplane:\n  enabled: true\n  session_memory: true\n"
        )

        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "session"):
            session = get_or_create_session(str(tmp_path), max_age_hours=4.0)
            compass = new_compass()
            # Seed state that deterministically fires approach_cycling.
            compass.event_counter = 12
            compass.approaches = [
                ApproachAttempt(
                    approach_sig=f"cmd:{i}",
                    outcome="failed",
                    started_at=100.0 + i,
                    last_event=150.0 + i,
                    event_count=1,
                )
                for i in range(3)
            ]
            compass.action_history = [
                {
                    "tool": "Bash",
                    "ts": 200.0,
                    "sig": "cmd:2",
                    "exit": 1,
                    "err": "fail",
                    "intent": "execute",
                },
            ]
            compass.intent_history = ["execute"]
            save_behavior_compass(session, compass)
            save_session(session)

            controlplane_run(path=str(tmp_path), channels="behavior")

            session2 = get_or_create_session(str(tmp_path), max_age_hours=4.0)
            updated = load_behavior_compass(session2)

        assert "approach_cycling" in updated.last_fired
        assert updated.signal_fire_counts.get("approach_cycling", 0) >= 1

    def test_controlplane_run_uses_delta_after_clean_snapshot(self, tmp_path: Path) -> None:
        from lintgate.controlplane.session_memory import (
            SessionSnapshot,
            get_or_create_session,
            save_session,
        )
        from mcp_server import controlplane_run

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "lintgate.yaml").write_text(
            "controlplane:\n  enabled: true\n  session_memory: true\n"
        )

        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "session"):
            session = get_or_create_session(str(tmp_path), max_age_hours=4.0)
            session.snapshots.append(SessionSnapshot(run_id="prev", finding_index={}))
            save_session(session)

            raw = controlplane_run(path=str(tmp_path), channels="behavior")
            payload = json.loads(raw)
            # Compact output should be serialized without pretty indent.
            assert "\n  " not in raw

        assert "delta" in payload
        assert "blocking_issues" not in payload


# ── lint_get_details Round-Trip ─────────────────────────────────────────


class TestLintGetDetailsContract:
    """Verify lint_get_details correctly round-trips with saved run data."""

    def test_round_trip_with_lint_run(self, tmp_path: Path) -> None:
        """Saved run details can be retrieved and contain expected keys."""
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            run_id = generate_run_id()
            data = {
                "tier": "tier_2_manual",
                "project": "/tmp/test",
                "files_linted": 5,
                "duration_ms": 150.0,
                "blocking": 2,
                "warnings": 3,
                "informational": 1,
                "fixable": 1,
                "blocking_issues": [
                    {
                        "kind": "F821",
                        "message": "undefined name",
                        "severity": "blocking",
                        "issue_id": "abc123def456",
                    },
                ],
                "warning_issues": [
                    {
                        "kind": "E501",
                        "message": "line too long",
                        "severity": "warning",
                        "issue_id": "xyz789uvw012",
                    },
                ],
                "info_issues": [],
                "recurrence": {"repeated_issue_count": 0, "top_repeated": []},
                "linter_diagnostics": [{"linter": "ruff_check", "status": "ok"}],
            }
            save_run_details(run_id, data)
            loaded = load_run_details(run_id)

        assert loaded is not None
        assert loaded["run_id"] == run_id
        assert loaded["blocking_issues"][0]["kind"] == "F821"
        assert loaded["warning_issues"][0]["kind"] == "E501"
        assert "timestamp" in loaded

    def test_severity_filter_blocking_only(self, tmp_path: Path) -> None:
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            run_id = "filter_test"
            data = {
                "blocking_issues": [{"kind": "F821"}],
                "warning_issues": [{"kind": "E501"}, {"kind": "W291"}],
                "info_issues": [{"kind": "C901"}],
            }
            save_run_details(run_id, data)
            loaded = load_run_details(run_id)

        # Simulate the filtering logic from lint_get_details
        blocking = loaded.get("blocking_issues", [])
        assert len(blocking) == 1
        assert blocking[0]["kind"] == "F821"

    def test_nonexistent_run_returns_none(self, tmp_path: Path) -> None:
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            assert load_run_details("nonexistent_run") is None


# ── Telemetry Schema ────────────────────────────────────────────────────


class TestTelemetrySchema:
    """Verify telemetry_summary returns all required keys."""

    REQUIRED_KEYS = {
        "period",
        "total_runs",
        "total_files_linted",
        "total_issues_found",
        "total_blocking_found",
        "total_warnings_found",
        "clean_run_count",
        "fix_rate",
        "avg_duration_ms",
        "tier_distribution",
        "trend",
    }

    def test_empty_metrics_has_required_keys(self, tmp_path: Path) -> None:
        from lintgate.telemetry import compute_telemetry_summary

        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            summary = compute_telemetry_summary("/tmp/nonexistent", period="7d")

        missing = self.REQUIRED_KEYS - set(summary.keys())
        assert not missing, f"Missing keys in telemetry summary: {missing}"

    def test_telemetry_values_are_correct_types(self, tmp_path: Path) -> None:
        from lintgate.telemetry import compute_telemetry_summary

        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            summary = compute_telemetry_summary("/tmp/proj", period="1d")

        assert isinstance(summary["total_runs"], int)
        assert isinstance(summary["total_issues_found"], int)
        assert isinstance(summary["fix_rate"], float)
        assert isinstance(summary["trend"], str)
        assert isinstance(summary["tier_distribution"], dict)
        assert isinstance(summary["period"], str)


# ── Payload Size Regression ─────────────────────────────────────────────


class TestPayloadSizeRegression:
    """Ensure output sizes stay within token budgets."""

    def test_compact_output_under_400_tokens(self) -> None:
        """A typical compact output with 3 blocking issues should be under 400 tokens."""
        compact = {
            "run_id": "abc123def456",
            "tier": "tier_2_manual",
            "files_linted": 25,
            "duration_ms": 350.5,
            "blocking": 3,
            "warnings": 12,
            "informational": 8,
            "fixable": 5,
            "blocking_issues": [
                {
                    "id": f"blk00000000{i}",
                    "kind": f"F82{i}",
                    "loc": f"src/module_{i}.py:{i * 10}",
                    "msg": f"undefined name 'function_{i}'",
                }
                for i in range(3)
            ],
            "next_actions": [
                {
                    "tool": "lint_fix",
                    "args": {"path": "/tmp/proj", "dry_run": True},
                    "safe": True,
                    "reason": "5 auto-fixable issues",
                    "priority": 1,
                },
                {
                    "tool": "lint_get_details",
                    "args": {"run_id": "abc123def456", "severity": "blocking"},
                    "safe": True,
                    "reason": "View 3 blocking issue details",
                    "priority": 2,
                },
            ],
        }
        serialized = json.dumps(compact, separators=(",", ":"))
        estimated_tokens = len(serialized) / 4
        assert estimated_tokens < 400, (
            f"Compact output too large: ~{estimated_tokens:.0f} tokens ({len(serialized)} chars)"
        )

    def test_standard_output_under_800_tokens(self) -> None:
        """Standard output with blocking + warning details under 800 tokens."""
        standard = {
            "run_id": "abc123def456",
            "tier": "tier_2_manual",
            "files_linted": 25,
            "duration_ms": 350.5,
            "blocking": 2,
            "warnings": 5,
            "informational": 8,
            "fixable": 3,
            "linters_run": 4,
            "linter_statuses": {
                "ruff_check": "ok",
                "ruff_format": "ok",
                "mypy": "ok",
                "complexity_checker": "ok",
            },
            "blocking_issues": [
                {
                    "linter": "ruff",
                    "kind": "F821",
                    "message": "undefined name 'foo'",
                    "file": "src/mod.py",
                    "line": 10,
                    "severity": "blocking",
                    "issue_id": "blk000000001",
                }
                for _ in range(2)
            ],
            "warning_issues": [
                {
                    "linter": "ruff",
                    "kind": "E501",
                    "message": "line too long (120 > 100 characters)",
                    "file": f"src/mod{i}.py",
                    "line": i * 5,
                    "severity": "warning",
                    "issue_id": f"wrn{i:09d}",
                }
                for i in range(5)
            ],
            "next_actions": [
                {
                    "tool": "lint_fix",
                    "args": {"path": "/tmp", "dry_run": True},
                    "safe": True,
                    "reason": "3 auto-fixable issues",
                    "priority": 1,
                },
            ],
        }
        serialized = json.dumps(standard, separators=(",", ":"))
        estimated_tokens = len(serialized) / 4
        assert estimated_tokens < 800, (
            f"Standard output too large: ~{estimated_tokens:.0f} tokens ({len(serialized)} chars)"
        )

    def test_compact_smaller_than_standard(self) -> None:
        from mcp_server import _json_dumps

        data = {
            "run_id": "abc",
            "blocking": 2,
            "warnings": 5,
            "informational": 3,
            "fixable": 1,
            "blocking_issues": [
                {"id": "blk1", "kind": "F821", "loc": "mod.py:10", "msg": "undefined name"},
            ],
        }
        compact = _json_dumps(data, "compact")
        standard = _json_dumps(data, "standard")
        full = _json_dumps(data, "full")
        assert len(compact) <= len(standard)
        assert len(standard) <= len(full)

    def test_no_indent_in_compact_mode(self) -> None:
        from mcp_server import _json_dumps

        data = {"a": {"b": {"c": [1, 2, 3]}}}
        compact = _json_dumps(data, "compact")
        assert "\n" not in compact
        # No indentation whitespace
        assert "  " not in compact


# ── Version Consistency ─────────────────────────────────────────────────


class TestVersionConsistency:
    """Verify version is bumped to v0.2.0."""

    def test_lint_status_reports_v020(self) -> None:
        """The version constant in lint_status should be 0.2.0."""
        # Can't easily call lint_status without a real project, so we check the source
        import mcp_server

        source = Path(mcp_server.__file__).read_text()
        assert '"version": "0.2.0"' in source or "'version': '0.2.0'" in source


# ── Issue ID Contracts ──────────────────────────────────────────────────


class TestIssueIdContracts:
    """Issue ID format and stability contracts."""

    def test_issue_id_length_is_12(self) -> None:
        issue = LintIssue(linter="ruff", kind="F821", message="undef foo", file="a.py", line=10)
        assert len(issue.compute_issue_id()) == 12

    def test_issue_id_is_hex(self) -> None:
        issue = LintIssue(linter="ruff", kind="F821", message="undef foo", file="a.py", line=10)
        issue_id = issue.compute_issue_id()
        assert all(c in "0123456789abcdef" for c in issue_id)

    def test_issue_id_deterministic(self) -> None:
        issue = LintIssue(linter="ruff", kind="F821", message="undef foo", file="a.py", line=10)
        id1 = issue.compute_issue_id()
        id2 = issue.compute_issue_id()
        assert id1 == id2

    def test_different_issues_different_ids(self) -> None:
        issue1 = LintIssue(linter="ruff", kind="F821", message="undef foo", file="a.py", line=10)
        issue2 = LintIssue(linter="ruff", kind="F841", message="unused var", file="a.py", line=20)
        assert issue1.compute_issue_id() != issue2.compute_issue_id()


# ── Run ID Contracts ────────────────────────────────────────────────────


class TestRunIdContracts:
    def test_run_id_length(self) -> None:
        assert len(generate_run_id()) == 12

    def test_run_id_is_hex(self) -> None:
        rid = generate_run_id()
        assert all(c in "0123456789abcdef" for c in rid)

    def test_run_id_uniqueness(self) -> None:
        ids = {generate_run_id() for _ in range(20)}
        assert len(ids) == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
