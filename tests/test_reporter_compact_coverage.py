"""Tests for compact reporter zero-kill functions — closing VALUE/TYPE mutation gaps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lintgate.controlplane.reporter.compact import (
    _attach_delta_or_blocking,
    _build_bootstrap_progress,
    _build_coherence_dict,
    _build_counts,
    _build_remediation_loop,
    _extract_import_graph,
    _finding_to_blocker,
    _format_fail_status,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    ControlPlaneConfig,
    MeshResult,
)

# ── _build_coherence_dict ─────────────────────────────────────────────


class TestBuildCoherenceDict:
    def test_stable_minimal(self):
        coherence = CoherenceResult(state="stable", summary="all good")
        result = _build_coherence_dict(coherence)
        assert result["state"] == "stable"
        assert result["summary"] == "all good"
        assert "action" not in result
        assert "confidence" not in result

    def test_with_action(self):
        coherence = CoherenceResult(
            state="isolated",
            summary="lint fails",
            recommended_action="run lint_fix",
        )
        result = _build_coherence_dict(coherence)
        assert result["action"] == "run lint_fix"

    def test_low_confidence_included(self):
        coherence = CoherenceResult(state="coupled", summary="x", confidence=0.6)
        result = _build_coherence_dict(coherence)
        assert result["confidence"] == 0.6

    def test_full_confidence_excluded(self):
        coherence = CoherenceResult(state="stable", summary="x", confidence=1.0)
        result = _build_coherence_dict(coherence)
        assert "confidence" not in result

    def test_classification_notes(self):
        coherence = CoherenceResult(
            state="systemic",
            summary="x",
            classification_notes=["ambiguous boundary"],
        )
        result = _build_coherence_dict(coherence)
        assert result["classification_notes"] == ["ambiguous boundary"]


# ── _build_counts ─────────────────────────────────────────────────────


class TestBuildCounts:
    def test_basic_counts(self):
        mesh = MeshResult(
            channel_results=[
                ChannelResult(channel="lint", status="fail"),
                ChannelResult(channel="tests", status="pass"),
                ChannelResult(channel="structure", status="skip"),
            ],
        )
        severity_counts = {"blocking": 2, "warning": 3, "informational": 1}
        result = _build_counts(mesh, severity_counts, [])

        assert result["blocking"] == 2
        assert result["warning"] == 3
        assert result["informational"] == 1
        assert result["channels_run"] == 2  # skip excluded
        assert result["repairs_available"] == 0
        assert result["symbol_blocking"] == 0

    def test_symbol_blockers_counted(self):
        mesh = MeshResult(channel_results=[])
        result = _build_counts(
            mesh, {"blocking": 0, "warning": 0, "informational": 0}, [{"kind": "a"}]
        )
        assert result["symbol_blocking"] == 1


# ── _format_fail_status ───────────────────────────────────────────────


@dataclass
class FakeFinding:
    severity: str = "warning"
    kind: str = ""
    message: str = ""
    file: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class TestFormatFailStatus:
    def test_single_blocking(self):
        cr = ChannelResult(
            channel="lint",
            status="fail",
            findings=[FakeFinding(severity="blocking")],
        )
        result = _format_fail_status(cr)
        assert result == "fail(1 blocking)"

    def test_mixed_severities(self):
        cr = ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                FakeFinding(severity="blocking"),
                FakeFinding(severity="blocking"),
                FakeFinding(severity="warning"),
            ],
        )
        result = _format_fail_status(cr)
        assert "2 blocking" in result
        assert "1 warning" in result

    def test_no_findings(self):
        cr = ChannelResult(channel="lint", status="fail", findings=[])
        assert _format_fail_status(cr) == "fail"


# ── _finding_to_blocker ──────────────────────────────────────────────


class TestFindingToBlocker:
    def test_non_blocking_returns_none(self):
        f = FakeFinding(severity="warning", kind="symbol_uncovered")
        assert _finding_to_blocker(f) is None

    def test_non_symbol_kind_returns_none(self):
        f = FakeFinding(severity="blocking", kind="RUFF001")
        assert _finding_to_blocker(f) is None

    def test_blocking_symcov_returns_dict(self):
        f = FakeFinding(
            severity="blocking",
            kind="symbol_uncovered",
            message="missing test for func",
            file="src/core.py",
            evidence={"symbol_key": "core::func"},
        )
        result = _finding_to_blocker(f)
        assert result is not None
        assert result["kind"] == "symbol_uncovered"
        assert result["symbol"] == "core::func"
        assert result["file"] == "src/core.py"

    def test_falls_back_to_message_when_no_symbol(self):
        f = FakeFinding(
            severity="blocking",
            kind="symbol_uncovered",
            message="missing test coverage",
            evidence={},
        )
        result = _finding_to_blocker(f)
        assert result is not None
        assert result["symbol"] == "missing test coverage"


# ── _extract_import_graph ────────────────────────────────────────────


class TestExtractImportGraph:
    def test_returns_graph_from_structure_channel(self):
        ig = {"mod_a": ["mod_b"]}
        fm = {"mod_a": "src/a.py"}
        cr = ChannelResult(
            channel="structure",
            status="pass",
            metrics={"_import_graph": ig, "_file_map": fm},
        )
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        assert graph == ig
        assert fmap == fm

    def test_returns_empty_when_no_structure_channel(self):
        cr = ChannelResult(channel="lint", status="pass", metrics={})
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        assert graph == {}
        assert fmap == {}

    def test_returns_empty_when_metrics_is_empty(self):
        cr = ChannelResult(channel="structure", status="pass", metrics={})
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        assert graph == {}
        assert fmap == {}

    def test_returns_empty_when_graph_keys_missing(self):
        cr = ChannelResult(channel="structure", status="pass", metrics={"other": 1})
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        assert graph == {}
        assert fmap == {}

    def test_ignores_non_dict_graph_values(self):
        cr = ChannelResult(
            channel="structure",
            status="pass",
            metrics={"_import_graph": "bad", "_file_map": "bad"},
        )
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        assert graph == {}
        assert fmap == {}

    def test_isinstance_guard_on_import_graph(self):
        """TYPE mutation: isinstance(ig, dict) replaced with True."""
        cr = ChannelResult(
            channel="structure",
            status="pass",
            metrics={"_import_graph": [1, 2], "_file_map": {"a": "b"}},
        )
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        # ig is a list, not dict — should fall through
        assert graph == {}
        assert fmap == {}

    def test_isinstance_guard_on_file_map(self):
        """TYPE mutation: isinstance(fm, dict) replaced with True."""
        cr = ChannelResult(
            channel="structure",
            status="pass",
            metrics={"_import_graph": {"a": ["b"]}, "_file_map": [1, 2]},
        )
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        # fm is a list, not dict — should fall through
        assert graph == {}
        assert fmap == {}

    def test_isinstance_guard_on_metrics(self):
        """TYPE mutation: isinstance(cr.metrics, dict) replaced with True."""
        cr = ChannelResult(channel="structure", status="pass")
        # metrics defaults to {} which IS a dict, but we need to test
        # when the function gets a non-dict. Override via object attribute.
        object.__setattr__(cr, "metrics", "not_a_dict")
        mesh = MeshResult(channel_results=[cr])
        graph, fmap = _extract_import_graph(mesh)
        assert graph == {}
        assert fmap == {}


# ── _attach_delta_or_blocking ────────────────────────────────────────


class TestAttachDeltaOrBlocking:
    def test_with_previous_index_adds_delta(self):
        compact: dict[str, Any] = {}
        current = {"fp1": {"severity": "blocking", "kind": "E501"}}
        previous: dict[str, dict[str, Any]] = {}
        _attach_delta_or_blocking(compact, current, previous)
        assert "delta" in compact

    def test_without_previous_adds_blocking_issues(self):
        compact: dict[str, Any] = {}
        current = {"fp1": {"severity": "blocking", "kind": "E501", "count": 1}}
        _attach_delta_or_blocking(compact, current, None)
        assert "blocking_issues" in compact
        assert len(compact["blocking_issues"]) == 1
        assert compact["blocking_issues"][0]["fingerprint"] == "fp1"

    def test_no_blocking_no_issues_key(self):
        compact: dict[str, Any] = {}
        current = {"fp1": {"severity": "warning", "kind": "W291"}}
        _attach_delta_or_blocking(compact, current, None)
        assert "blocking_issues" not in compact

    def test_truncation_with_many_blockers(self):
        compact: dict[str, Any] = {}
        current = {
            f"fp{i}": {"severity": "blocking", "kind": "E501", "count": 1}
            for i in range(10)
        }
        _attach_delta_or_blocking(compact, current, None)
        # Default max_findings is 5
        assert len(compact["blocking_issues"]) == 5
        assert compact["blocking_truncated"] == 5

    def test_config_max_findings_respected(self):
        compact: dict[str, Any] = {}
        current = {
            f"fp{i}": {"severity": "blocking", "kind": "E501", "count": 1}
            for i in range(10)
        }
        from lintgate.controlplane.types import ChannelConfig

        config = ControlPlaneConfig(
            channels={"lint": ChannelConfig(max_findings_shown=8)}
        )
        _attach_delta_or_blocking(compact, current, None, config)
        assert len(compact["blocking_issues"]) == 8
        assert compact["blocking_truncated"] == 2


# ── _build_remediation_loop ──────────────────────────────────────────


class TestBuildRemediationLoop:
    def test_basic_structure(self):
        blockers = [{"kind": "symbol_uncovered", "symbol": "mod::func"}]
        result = _build_remediation_loop(blockers)
        assert result["required"] is True
        assert result["type"] == "symbol_coverage"
        assert len(result["blocking_symbols"]) == 1
        assert result["blocking_symbols"][0]["symbol"] == "mod::func"

    def test_caps_at_25(self):
        blockers = [{"kind": "x", "symbol": f"s{i}"} for i in range(30)]
        result = _build_remediation_loop(blockers)
        assert len(result["blocking_symbols"]) == 25

    def test_exit_condition_present(self):
        result = _build_remediation_loop([{"kind": "a"}])
        assert "symbol_blocking == 0" in result["exit_condition"]
        assert "blocking == 0" in result["exit_condition"]

    def test_policy_text(self):
        result = _build_remediation_loop([{"kind": "a"}])
        assert "Add tests" in result["policy"]
        assert "rerun" in result["policy"]


# ── _build_bootstrap_progress ────────────────────────────────────────


class TestBuildBootstrapProgress:
    def test_returns_none_when_no_tests_channel(self):
        cr = ChannelResult(channel="lint", status="pass", metrics={})
        mesh = MeshResult(channel_results=[cr])
        assert _build_bootstrap_progress(mesh) is None

    def test_returns_none_when_bootstrap_not_needed(self):
        cr = ChannelResult(
            channel="tests", status="pass", metrics={"bootstrap_needed": False}
        )
        mesh = MeshResult(channel_results=[cr])
        assert _build_bootstrap_progress(mesh) is None

    def test_returns_progress_when_needed(self):
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={"bootstrap_needed": True, "bootstrap_reason": "no_tests"},
        )
        mesh = MeshResult(channel_results=[cr])
        result = _build_bootstrap_progress(mesh)
        assert result is not None
        assert result["needed"] is True
        assert result["reason"] == "no_tests"

    def test_default_reason_when_missing(self):
        cr = ChannelResult(
            channel="tests", status="fail", metrics={"bootstrap_needed": True}
        )
        mesh = MeshResult(channel_results=[cr])
        result = _build_bootstrap_progress(mesh)
        assert result is not None
        assert result["reason"] == "zero_test_files"

    def test_with_bootstrap_state_running(self):
        from unittest.mock import patch

        from lintgate.orchestration.bootstrap_state import BootstrapState

        fake_state = BootstrapState(
            status="running",
            phase="skeletons",
            files_processed={"a.py": "done", "b.py": "done"},
            tests_generated=5,
        )
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={
                "bootstrap_needed": True,
                "project_root": "/tmp/proj",
            },
        )
        mesh = MeshResult(channel_results=[cr])
        with patch.object(BootstrapState, "load", return_value=fake_state):
            result = _build_bootstrap_progress(mesh)

        assert result is not None
        assert result["status"] == "running"
        assert result["phase"] == "skeletons"
        assert result["files_processed"] == 2
        assert result["tests_generated"] == 5
        assert "/" in result["phase_progress"]  # e.g. "3/5"

    def test_with_bootstrap_state_error(self):
        from unittest.mock import patch

        from lintgate.orchestration.bootstrap_state import BootstrapState

        fake_state = BootstrapState(
            status="failed",
            phase="scaffold",
            error="timeout",
        )
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={
                "bootstrap_needed": True,
                "project_root": "/tmp/proj",
            },
        )
        mesh = MeshResult(channel_results=[cr])
        with patch.object(BootstrapState, "load", return_value=fake_state):
            result = _build_bootstrap_progress(mesh)

        assert result is not None
        assert result["status"] == "failed"
        assert result["error"] == "timeout"

    def test_idle_state_no_extra_keys(self):
        from unittest.mock import patch

        from lintgate.orchestration.bootstrap_state import BootstrapState

        fake_state = BootstrapState(status="idle")
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={
                "bootstrap_needed": True,
                "project_root": "/tmp/proj",
            },
        )
        mesh = MeshResult(channel_results=[cr])
        with patch.object(BootstrapState, "load", return_value=fake_state):
            result = _build_bootstrap_progress(mesh)

        assert result is not None
        assert result["needed"] is True
        # idle state should NOT add status/phase keys
        assert "status" not in result

    def test_phase_progress_exact_format(self):
        """Kill VALUE_11: len(PHASES) - 1 → len(PHASES) - 0."""
        from unittest.mock import patch

        from lintgate.orchestration.bootstrap_state import PHASES, BootstrapState

        fake_state = BootstrapState(status="running", phase="skeletons")
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={"bootstrap_needed": True, "project_root": "/tmp/proj"},
        )
        mesh = MeshResult(channel_results=[cr])
        with patch.object(BootstrapState, "load", return_value=fake_state):
            result = _build_bootstrap_progress(mesh)

        assert result is not None
        phase_idx = PHASES.index("skeletons")
        total_phases = len(PHASES) - 1  # exclude "not_started"
        assert result["phase_progress"] == f"{phase_idx}/{total_phases}"

    def test_unknown_phase_fallback(self):
        """Kill VALUE_10: else 0 → else 1 for unknown phase."""
        from unittest.mock import patch

        from lintgate.orchestration.bootstrap_state import PHASES, BootstrapState

        fake_state = BootstrapState(status="running", phase="nonexistent_phase")
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={"bootstrap_needed": True, "project_root": "/tmp/proj"},
        )
        mesh = MeshResult(channel_results=[cr])
        with patch.object(BootstrapState, "load", return_value=fake_state):
            result = _build_bootstrap_progress(mesh)

        assert result is not None
        total_phases = len(PHASES) - 1
        # Unknown phase → phase_idx=0
        assert result["phase_progress"] == f"0/{total_phases}"

    def test_no_project_root_in_metrics(self):
        """Kill VALUE_8: get('project_root', '') → get('project_root', 'mutated')."""
        cr = ChannelResult(
            channel="tests",
            status="fail",
            metrics={"bootstrap_needed": True},
            # No project_root key
        )
        mesh = MeshResult(channel_results=[cr])
        result = _build_bootstrap_progress(mesh)
        assert result is not None
        assert result["needed"] is True
        # Without project_root, BootstrapState.load won't be called,
        # so no status/phase keys
        assert "status" not in result
