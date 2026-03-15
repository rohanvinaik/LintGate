"""Tests for compact reporter zero-kill functions — closing VALUE/TYPE mutation gaps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lintgate.controlplane.reporter.compact import (
    _build_coherence_dict,
    _build_counts,
    _finding_to_blocker,
    _format_fail_status,
)
from lintgate.controlplane.types import ChannelResult, CoherenceResult, MeshResult

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
            state="isolated", summary="lint fails",
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
            state="systemic", summary="x",
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
        result = _build_counts(mesh, {"blocking": 0, "warning": 0, "informational": 0}, [{"kind": "a"}])
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
            channel="lint", status="fail",
            findings=[FakeFinding(severity="blocking")],
        )
        result = _format_fail_status(cr)
        assert result == "fail(1 blocking)"

    def test_mixed_severities(self):
        cr = ChannelResult(
            channel="lint", status="fail",
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
            severity="blocking", kind="symbol_uncovered",
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
            severity="blocking", kind="symbol_uncovered",
            message="missing test coverage",
            evidence={},
        )
        result = _finding_to_blocker(f)
        assert result is not None
        assert result["symbol"] == "missing test coverage"
