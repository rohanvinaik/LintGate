"""Tests for lintgate/controlplane/delegation.py — sub-agent suitability scoring (#195)."""

from __future__ import annotations

from lintgate.controlplane.delegation import (
    DelegationSuitability,
    annotate_findings_with_suitability,
    compute_delegation_suitability,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _finding(
    kind: str = "E501",
    severity: str = "warning",
    file: str = "mod.py",
    channel: str = "lint",
) -> dict:
    return {"kind": kind, "severity": severity, "file": file, "channel": channel}


# ── compute_delegation_suitability ───────────────────────────────────


class TestComputeDelegationSuitability:
    def test_mechanical_fix_scores_high(self):
        result = compute_delegation_suitability(_finding(kind="F401"))
        assert result.category == "high"
        assert result.score >= 0.7
        assert "mechanical" in result.reason

    def test_unused_import_high(self):
        result = compute_delegation_suitability(_finding(kind="F401"))
        assert result.category == "high"

    def test_perf_fix_high(self):
        result = compute_delegation_suitability(_finding(kind="PERF001"))
        assert result.category == "high"

    def test_convention_dependent_low(self):
        result = compute_delegation_suitability(_finding(kind="N802"))
        assert result.category == "low"
        assert "convention" in result.reason
        assert "naming conventions" in " ".join(result.requires_context)

    def test_cognitive_complexity_low(self):
        result = compute_delegation_suitability(_finding(kind="C901", severity="blocking"))
        # CC + blocking → score drops significantly
        assert result.score < 0.5
        assert "structural decomposition" in result.reason

    def test_behavior_finding_zero(self):
        result = compute_delegation_suitability(
            _finding(kind="approach_cycling", channel="behavior")
        )
        assert result.score == 0.0
        assert result.category == "low"
        assert "behavioral" in result.reason

    def test_default_medium(self):
        result = compute_delegation_suitability(_finding(kind="SomeUnknownRule"))
        assert result.category == "medium"
        assert 0.4 <= result.score <= 0.6

    def test_blocking_severity_penalty(self):
        warning_result = compute_delegation_suitability(
            _finding(kind="SomeRule", severity="warning")
        )
        blocking_result = compute_delegation_suitability(
            _finding(kind="SomeRule", severity="blocking")
        )
        assert blocking_result.score < warning_result.score

    def test_downstream_dependents_penalty(self):
        graph: dict[str, list[str] | set[str]] = {"app": ["utils"], "main": ["utils"], "utils": []}
        file_map = {"app": "app.py", "main": "main.py", "utils": "utils.py"}
        # utils.py has 2 downstream dependents
        result = compute_delegation_suitability(_finding(file="utils.py"), graph, file_map)
        assert "downstream" in result.reason
        assert result.score < 0.5

    def test_no_dependents_no_penalty(self):
        graph: dict[str, list[str] | set[str]] = {"app": ["utils"], "utils": []}
        file_map = {"app": "app.py", "utils": "utils.py"}
        # app.py has 0 downstream dependents
        result = compute_delegation_suitability(_finding(file="app.py"), graph, file_map)
        assert "downstream" not in result.reason

    def test_c4_prefix_match(self):
        # C4xx family should match C4 prefix in MECHANICAL_FIXES
        result = compute_delegation_suitability(_finding(kind="C400"))
        assert result.category == "high"
        assert "mechanical" in result.reason


# ── annotate_findings_with_suitability ───────────────────────────────


class TestAnnotateFindings:
    def test_annotates_in_place(self):
        findings = [_finding(kind="F401"), _finding(kind="C901")]
        details: dict[str, dict[str, object]] = {"channels": {}}
        annotate_findings_with_suitability(findings, details)
        assert "delegation_suitability" in findings[0]
        assert "delegation_suitability" in findings[1]
        assert findings[0]["delegation_suitability"]["category"] == "high"

    def test_uses_import_graph_from_details(self):
        findings = [_finding(kind="E501", file="utils.py")]
        details = {
            "channels": {
                "structure": {
                    "metrics": {
                        "_import_graph": {"app": ["utils"], "utils": []},
                        "_file_map": {"app": "app.py", "utils": "utils.py"},
                    }
                }
            }
        }
        annotate_findings_with_suitability(findings, details)
        ds = findings[0]["delegation_suitability"]
        assert "downstream" in ds["reason"]

    def test_empty_details_still_works(self):
        findings = [_finding(kind="F401")]
        annotate_findings_with_suitability(findings, {})
        assert findings[0]["delegation_suitability"]["category"] == "high"


# ── DelegationSuitability serialization ──────────────────────────────


class TestDelegationSuitabilitySerialization:
    def test_to_dict(self):
        ds = DelegationSuitability(
            score=0.85,
            category="high",
            reason="mechanical fix",
            requires_context=[],
        )
        d = ds.to_dict()
        assert d["score"] == 0.85
        assert d["category"] == "high"
        assert d["reason"] == "mechanical fix"
        assert d["requires_context"] == []

    def test_score_clamped(self):
        result = compute_delegation_suitability(_finding(kind="F401"))
        assert 0.0 <= result.score <= 1.0
