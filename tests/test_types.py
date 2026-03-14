"""Tests for lintgate/types.py."""

from __future__ import annotations

import hashlib

from lintgate.types import (
    CHANGE_KINDS,
    RISK_LEVELS,
    SKIP_TIER,
    AggregatedResult,
    ChangeClassification,
    CoveragePolicy,
    DiffAnalysis,
    LinterResult,
    LintIssue,
    LintTier,
    Prescription,
    ProjectConfig,
)

# ── LintIssue ───────────────────────────────────────────────────────────


def test_lint_issue_compute_issue_id():
    issue = LintIssue(linter="ruff", kind="F821", message="undefined name 'x'", file="/a.py", line=10)
    issue_id = issue.compute_issue_id()
    raw = "ruff|F821|/a.py|10|undefined name 'x'"
    expected = hashlib.sha256(raw.encode()).hexdigest()[:12]
    assert issue_id == expected
    assert len(issue_id) == 12


def test_lint_issue_compute_issue_id_no_file():
    issue = LintIssue(linter="mypy", kind="E001", message="error")
    issue_id = issue.compute_issue_id()
    raw = "mypy|E001||0|error"
    expected = hashlib.sha256(raw.encode()).hexdigest()[:12]
    assert issue_id == expected


def test_lint_issue_to_dict_omits_empty():
    issue = LintIssue(linter="ruff", kind="F821", message="test")
    d = issue.to_dict()
    assert "linter" in d
    assert "kind" in d
    assert "message" in d
    # These should be omitted because they're None/empty
    assert "file" not in d
    assert "line" not in d
    assert "evidence" not in d
    assert "suggestions" not in d
    assert "issue_id" not in d


def test_lint_issue_to_dict_includes_values():
    issue = LintIssue(
        linter="ruff",
        kind="F821",
        message="test",
        file="/a.py",
        line=5,
        severity="blocking",
        confidence=0.9,
    )
    d = issue.to_dict()
    assert d["file"] == "/a.py"
    assert d["line"] == 5
    assert d["severity"] == "blocking"
    assert d["confidence"] == 0.9


def test_lint_issue_short_location():
    issue = LintIssue(linter="ruff", kind="F821", message="x", file="/path/to/foo.py", line=42)
    assert issue.short_location() == "foo.py:42"


def test_lint_issue_short_location_no_line():
    issue = LintIssue(linter="ruff", kind="F821", message="x", file="/path/to/foo.py")
    assert issue.short_location() == "foo.py"


def test_lint_issue_short_location_no_file():
    issue = LintIssue(linter="ruff", kind="F821", message="x")
    assert issue.short_location() == ""


def test_lint_issue_hash_and_eq():
    a = LintIssue(linter="ruff", kind="F821", message="msg a", file="/a.py", line=10)
    b = LintIssue(linter="ruff", kind="F821", message="msg b", file="/a.py", line=10)
    assert a == b
    assert hash(a) == hash(b)


def test_lint_issue_not_eq_different_line():
    a = LintIssue(linter="ruff", kind="F821", message="x", file="/a.py", line=10)
    b = LintIssue(linter="ruff", kind="F821", message="x", file="/a.py", line=20)
    assert a != b


def test_lint_issue_not_eq_non_issue():
    a = LintIssue(linter="ruff", kind="F821", message="x")
    assert a != "not an issue"


# ── DiffAnalysis ────────────────────────────────────────────────────────


def test_diff_analysis_empty():
    da = DiffAnalysis.empty()
    assert da.lines_added == 0
    assert da.lines_removed == 0
    assert da.import_only is False
    assert da.is_new_file is False
    assert da.is_build_command is False


def test_diff_analysis_defaults():
    da = DiffAnalysis()
    assert da == DiffAnalysis.empty()


# ── ChangeClassification ───────────────────────────────────────────────


def test_change_classification_defaults():
    cc = ChangeClassification()
    assert cc.change_kind == "logic"
    assert cc.risk_level == "moderate"
    assert cc.files_changed == []


# ── Prescription ────────────────────────────────────────────────────────


def test_prescription_to_dict_minimal():
    p = Prescription(
        kind="add_test",
        target="foo.py::bar",
        action="Add test for bar",
        source="lint",
    )
    d = p.to_dict()
    assert d["kind"] == "add_test"
    assert d["target"] == "foo.py::bar"
    assert d["action"] == "Add test for bar"
    assert d["source"] == "lint"
    assert d["confidence"] == 0.5
    assert "lines" not in d
    assert "proposed_name" not in d
    assert "converged" not in d


def test_prescription_to_dict_full():
    p = Prescription(
        kind="extract_function",
        target="big.py::main",
        action="Extract helper",
        source="static",
        confidence=0.9,
        lines=(10, 50),
        proposed_name="helper_fn",
        inputs=["x"],
        outputs=["y"],
        expected_delta={"cc_reduction": 5},
        evidence_sources=["lint", "structure"],
        converged=True,
        lens_contributions={"lint": 0.7, "structure": 0.3},
    )
    d = p.to_dict()
    assert d["lines"] == [10, 50]
    assert d["proposed_name"] == "helper_fn"
    assert d["inputs"] == ["x"]
    assert d["outputs"] == ["y"]
    assert d["expected_delta"] == {"cc_reduction": 5}
    assert d["converged"] is True
    assert d["lens_contributions"] == {"lint": 0.7, "structure": 0.3}


# ── LintTier / SKIP_TIER ───────────────────────────────────────────────


def test_skip_tier():
    assert SKIP_TIER.skip is True
    assert SKIP_TIER.linters == []
    assert SKIP_TIER.files == []


def test_lint_tier_defaults():
    tier = LintTier(name="t1", linters=["ruff"], files=["a.py"], reason="test")
    assert tier.strictness == "normal"
    assert tier.skip is False


# ── Constants ───────────────────────────────────────────────────────────


def test_change_kinds_is_frozenset():
    assert isinstance(CHANGE_KINDS, frozenset)
    assert "logic" in CHANGE_KINDS
    assert "import" in CHANGE_KINDS
    assert len(CHANGE_KINDS) == 8


def test_risk_levels_is_frozenset():
    assert isinstance(RISK_LEVELS, frozenset)
    assert "none" in RISK_LEVELS
    assert "architectural" in RISK_LEVELS
    assert len(RISK_LEVELS) == 5


# ── Config types ────────────────────────────────────────────────────────


def test_coverage_policy_defaults():
    cp = CoveragePolicy()
    assert cp.global_threshold == 80
    assert cp.diff_threshold == 80
    assert cp.source_packages == ["lintgate", "mcp_tools"]


def test_project_config_debounce_defaults():
    pc = ProjectConfig()
    assert pc.debounce["tier_0"] == 0.0
    assert pc.debounce["tier_3"] == 5.0
    assert pc.total_timeout_ms == 8000


def test_linter_result_defaults():
    lr = LinterResult(linter_name="x")
    assert lr.status == "ok"
    assert lr.issues == []
    assert lr.error is None
    assert lr.duration_ms == 0.0


def test_aggregated_result_defaults():
    ar = AggregatedResult()
    assert ar.blocking == []
    assert ar.warnings == []
    assert ar.informational == []
    assert ar.tier_used == ""
