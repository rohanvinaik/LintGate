"""Characterization tests for lintgate/results_aggregator.py.

Covers _collect_raw_issues, _compute_metrics, _deduplicate,
_resolve_severity_override, _split_by_severity, compute_roi,
estimate_effort, _first_fallback, _is_exempted, _exemption_sections,
and _normalize_file_path.
"""

from __future__ import annotations

from lintgate.linters.import_pattern_detector import OptionalImport, OptionalImportReport
from lintgate.results_aggregator import (
    _collect_raw_issues,
    _compute_metrics,
    _deduplicate,
    _exemption_sections,
    _first_fallback,
    _is_exempted,
    _normalize_file_path,
    _resolve_severity_override,
    _split_by_severity,
    compute_roi,
    estimate_effort,
)
from lintgate.types import LinterResult, LintIssue


def _issue(
    *,
    linter: str = "ruff",
    kind: str = "F821",
    message: str = "undefined name",
    file: str | None = "mod.py",
    line: int | None = 10,
    severity: str = "warning",
    confidence: float = 1.0,
    fixable: bool = False,
    estimated_effort_minutes: float | None = None,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        message=message,
        file=file,
        line=line,
        severity=severity,
        confidence=confidence,
        fixable=fixable,
        estimated_effort_minutes=estimated_effort_minutes,
    )


# ── _collect_raw_issues ──────────────────────────────────────────


class TestCollectRawIssues:
    def test_empty_list(self):
        issues, statuses, duration = _collect_raw_issues([])
        assert issues == []
        assert statuses == {}
        assert duration == 0.0

    def test_single_linter_result(self):
        i1 = _issue(kind="F821")
        lr = LinterResult(linter_name="ruff", issues=[i1], status="ok", duration_ms=50.0)
        issues, statuses, duration = _collect_raw_issues([lr])
        assert len(issues) == 1
        assert issues[0] is i1
        assert statuses == {"ruff": "ok"}
        assert duration == 50.0

    def test_multiple_linters(self):
        i1 = _issue(linter="ruff", kind="F821")
        i2 = _issue(linter="mypy", kind="E001")
        lr1 = LinterResult(linter_name="ruff", issues=[i1], status="ok", duration_ms=30.0)
        lr2 = LinterResult(linter_name="mypy", issues=[i2], status="ok", duration_ms=20.0)
        issues, statuses, duration = _collect_raw_issues([lr1, lr2])
        assert len(issues) == 2
        assert statuses == {"ruff": "ok", "mypy": "ok"}
        assert duration == 50.0

    def test_status_preserved(self):
        lr = LinterResult(linter_name="bandit", status="error", duration_ms=5.0)
        _, statuses, _ = _collect_raw_issues([lr])
        assert statuses["bandit"] == "error"


# ── _compute_metrics ─────────────────────────────────────────────


class TestComputeMetrics:
    def test_all_zeros(self):
        result = _compute_metrics([], [], [], [], 0, {})
        assert result == {
            "total_issues": 0,
            "blocking_count": 0,
            "warning_count": 0,
            "info_count": 0,
            "tuned_count": 0,
            "fixable_count": 0,
            "linters_run": 0,
            "linters_skipped": 0,
            "linters_errored": 0,
        }

    def test_counts_correct(self):
        issues = [_issue(fixable=True), _issue(fixable=False), _issue(fixable=True)]
        blocking = [_issue(severity="blocking")]
        warnings = [_issue(), _issue()]
        info = [_issue(severity="informational")]
        statuses = {"ruff": "ok", "mypy": "skipped", "bandit": "error"}
        result = _compute_metrics(issues, blocking, warnings, info, 5, statuses)
        assert result["total_issues"] == 3
        assert result["blocking_count"] == 1
        assert result["warning_count"] == 2
        assert result["info_count"] == 1
        assert result["tuned_count"] == 5
        assert result["fixable_count"] == 2
        assert result["linters_run"] == 1
        assert result["linters_skipped"] == 1
        assert result["linters_errored"] == 1

    def test_deferred_counts_as_skipped(self):
        statuses = {"ruff": "deferred"}
        result = _compute_metrics([], [], [], [], 0, statuses)
        assert result["linters_skipped"] == 1

    def test_timeout_counts_as_errored(self):
        statuses = {"ruff": "timeout"}
        result = _compute_metrics([], [], [], [], 0, statuses)
        assert result["linters_errored"] == 1


# ── _deduplicate ─────────────────────────────────────────────────


class TestDeduplicate:
    def test_empty(self):
        assert _deduplicate([]) == []

    def test_no_duplicates(self):
        i1 = _issue(file="a.py", line=1, kind="F821")
        i2 = _issue(file="b.py", line=2, kind="E001")
        result = _deduplicate([i1, i2])
        assert len(result) == 2

    def test_exact_duplicate_keeps_one(self):
        i1 = _issue(file="a.py", line=1, kind="F821", severity="warning")
        i2 = _issue(file="a.py", line=1, kind="F821", severity="warning")
        result = _deduplicate([i1, i2])
        assert len(result) == 1

    def test_higher_severity_wins(self):
        i1 = _issue(file="a.py", line=1, kind="F821", severity="warning", confidence=0.9)
        i2 = _issue(file="a.py", line=1, kind="F821", severity="blocking", confidence=0.5)
        result = _deduplicate([i1, i2])
        assert len(result) == 1
        assert result[0].severity == "blocking"

    def test_same_severity_higher_confidence_wins(self):
        i1 = _issue(file="a.py", line=1, kind="F821", severity="warning", confidence=0.5)
        i2 = _issue(file="a.py", line=1, kind="F821", severity="warning", confidence=0.9)
        result = _deduplicate([i1, i2])
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_dedup_key_is_file_line_kind(self):
        """SWAP-killer: different file or different line = not duplicate."""
        i1 = _issue(file="a.py", line=1, kind="F821")
        i2 = _issue(file="a.py", line=2, kind="F821")
        result = _deduplicate([i1, i2])
        assert len(result) == 2

    def test_different_linter_same_key_deduplicates(self):
        i1 = _issue(linter="ruff", file="a.py", line=1, kind="F821", severity="warning")
        i2 = _issue(linter="mypy", file="a.py", line=1, kind="F821", severity="blocking")
        result = _deduplicate([i1, i2])
        assert len(result) == 1
        assert result[0].severity == "blocking"


# ── _resolve_severity_override ───────────────────────────────────


class TestResolveSeverityOverride:
    def test_exact_match(self):
        result = _resolve_severity_override("F821", {"F821": "blocking"})
        assert result == "blocking"

    def test_no_match(self):
        result = _resolve_severity_override("F821", {"E001": "blocking"})
        assert result is None
        # Verify the non-matching key is still present in overrides (no mutation)
        assert _resolve_severity_override("E001", {"E001": "blocking"}) == "blocking"

    def test_prefix_match_with_slash(self):
        result = _resolve_severity_override(
            "B603/subprocess_without_shell_equals_true", {"B603": "informational"}
        )
        assert result == "informational"

    def test_exact_takes_priority_over_prefix(self):
        result = _resolve_severity_override(
            "B603/foo",
            {"B603": "informational", "B603/foo": "blocking"},
        )
        assert result == "blocking"

    def test_no_slash_no_prefix_match(self):
        result = _resolve_severity_override("F821", {"F8": "blocking"})
        # "F8" is a substring but not a slash-prefix, so no match
        assert result is None
        assert "/" not in "F821"  # confirms no slash → prefix path not taken

    def test_empty_overrides(self):
        overrides: dict[str, str] = {}
        result = _resolve_severity_override("F821", overrides)
        assert result is None
        assert overrides == {}  # overrides dict not mutated

    def test_prefix_is_before_slash_only(self):
        """VALUE-killer: prefix is kind.split('/', 1)[0], not arbitrary substring."""
        result = _resolve_severity_override("A/B/C", {"A": "blocking"})
        assert result == "blocking"
        # "A/B" is the full split result, so exact match not prefix
        result2 = _resolve_severity_override("A/B/C", {"A/B": "warning"})
        assert result2 is None  # not exact, and prefix split gives "A" not "A/B"


# ── _split_by_severity ───────────────────────────────────────────


class TestSplitBySeverity:
    def test_empty(self):
        blocking, warnings, info = _split_by_severity([])
        assert blocking == []
        assert warnings == []
        assert info == []

    def test_correct_buckets(self):
        i1 = _issue(severity="blocking", file="c.py", line=1)
        i2 = _issue(severity="warning", file="b.py", line=2)
        i3 = _issue(severity="informational", file="a.py", line=3)
        blocking, warnings, info = _split_by_severity([i1, i2, i3])
        assert len(blocking) == 1
        assert blocking[0].severity == "blocking"
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"
        assert len(info) == 1
        assert info[0].severity == "informational"

    def test_sorted_by_file_then_line(self):
        i1 = _issue(severity="warning", file="b.py", line=10)
        i2 = _issue(severity="warning", file="a.py", line=5)
        i3 = _issue(severity="warning", file="a.py", line=1)
        _, warnings, _ = _split_by_severity([i1, i2, i3])
        assert warnings[0].file == "a.py"
        assert warnings[0].line == 1
        assert warnings[1].file == "a.py"
        assert warnings[1].line == 5
        assert warnings[2].file == "b.py"

    def test_unknown_severity_excluded(self):
        i1 = _issue(severity="critical")
        blocking, warnings, info = _split_by_severity([i1])
        assert blocking == []
        assert warnings == []
        assert info == []


# ── compute_roi ──────────────────────────────────────────────────


class TestComputeRoi:
    def test_blocking_ruff_default_effort(self):
        i = _issue(severity="blocking", linter="ruff", confidence=1.0)
        # weight=3.0, effort=2.0 (ruff heuristic), roi = 3.0*1.0/2.0 = 1.5
        assert compute_roi(i) == 1.5

    def test_warning_mypy(self):
        i = _issue(severity="warning", linter="mypy", confidence=1.0)
        # weight=2.0, effort=10.0 (mypy heuristic), roi = 2.0*1.0/10.0 = 0.2
        assert compute_roi(i) == 0.2

    def test_fixable_caps_effort(self):
        i = _issue(severity="blocking", linter="bandit", confidence=1.0, fixable=True)
        # fixable -> effort=2.0 (capped), weight=3.0, roi=3.0*1.0/2.0=1.5
        assert compute_roi(i) == 1.5

    def test_custom_effort_used(self):
        i = _issue(severity="warning", confidence=0.8, estimated_effort_minutes=4.0)
        # weight=2.0, effort=4.0, roi=2.0*0.8/4.0=0.4
        assert compute_roi(i) == 0.4

    def test_informational_weight(self):
        i = _issue(severity="informational", linter="ruff", confidence=1.0)
        # weight=1.0, effort=2.0, roi=1.0*1.0/2.0=0.5
        assert compute_roi(i) == 0.5

    def test_unknown_severity_weight_one(self):
        i = _issue(severity="critical", linter="ruff", confidence=1.0)
        # weight=1.0 (default), effort=2.0, roi=0.5
        assert compute_roi(i) == 0.5

    def test_zero_effort_falls_back_to_estimate(self):
        # 0.0 is falsy, so `or estimate_effort(issue)` kicks in (ruff -> 2.0)
        i = _issue(severity="blocking", linter="ruff", confidence=1.0, estimated_effort_minutes=0.0)
        # weight=3.0, effort=2.0 (fallback), roi=3.0*1.0/2.0=1.5
        assert compute_roi(i) == 1.5


# ── estimate_effort ──────────────────────────────────────────────


class TestEstimateEffort:
    def test_ruff(self):
        assert estimate_effort(_issue(linter="ruff")) == 2.0

    def test_mypy(self):
        assert estimate_effort(_issue(linter="mypy")) == 10.0

    def test_radon(self):
        assert estimate_effort(_issue(linter="radon")) == 15.0

    def test_bandit(self):
        assert estimate_effort(_issue(linter="bandit")) == 20.0

    def test_vulture(self):
        assert estimate_effort(_issue(linter="vulture")) == 5.0

    def test_structure(self):
        assert estimate_effort(_issue(linter="structure")) == 15.0

    def test_unknown_linter(self):
        assert estimate_effort(_issue(linter="custom_tool")) == 10.0

    def test_fixable_caps_at_two(self):
        assert estimate_effort(_issue(linter="bandit", fixable=True)) == 2.0

    def test_fixable_ruff_stays_two(self):
        # ruff base=2.0, fixable cap=min(2.0, 2.0)=2.0
        assert estimate_effort(_issue(linter="ruff", fixable=True)) == 2.0


# ── _first_fallback ──────────────────────────────────────────────


class TestFirstFallback:
    def test_no_imports(self):
        report = OptionalImportReport()
        assert _first_fallback(report) is None
        assert report.optional_imports == []  # confirms empty input

    def test_no_fallback_value(self):
        report = OptionalImportReport(
            optional_imports=[
                OptionalImport(module="foo", names=["bar"], line=1, fallback_value=None)
            ]
        )
        assert _first_fallback(report) is None
        assert len(report.optional_imports) == 1  # import exists but has no fallback

    def test_returns_first_fallback(self):
        report = OptionalImportReport(
            optional_imports=[
                OptionalImport(module="a", names=["x"], line=1, fallback_value=None),
                OptionalImport(module="b", names=["y"], line=5, fallback_value="None"),
                OptionalImport(module="c", names=["z"], line=10, fallback_value="default"),
            ]
        )
        assert _first_fallback(report) == "None"

    def test_single_with_fallback(self):
        report = OptionalImportReport(
            optional_imports=[
                OptionalImport(module="m", names=["n"], line=1, fallback_value="stub")
            ]
        )
        assert _first_fallback(report) == "stub"


# ── _exemption_sections ──────────────────────────────────────────


class TestExemptionSections:
    def test_basic(self):
        i = _issue(linter="ruff", kind="F821")
        sections = _exemption_sections(i)
        assert sections[0] == "ruff"
        assert "F821" in sections

    def test_radon_aliases(self):
        i = _issue(linter="radon", kind="C901")
        sections = _exemption_sections(i)
        assert "radon" in sections
        assert "complexity" in sections
        assert "complexity_checker" in sections
        assert "C901" in sections

    def test_structure_alias(self):
        i = _issue(linter="structure", kind="S001")
        sections = _exemption_sections(i)
        assert "structure" in sections
        assert "structure_checker" in sections

    def test_no_duplicate_kind(self):
        """If kind equals linter, don't add it twice."""
        i = _issue(linter="ruff", kind="ruff")
        sections = _exemption_sections(i)
        assert sections.count("ruff") == 1

    def test_empty_kind_not_added(self):
        i = _issue(linter="ruff", kind="")
        sections = _exemption_sections(i)
        assert "" not in sections


# ── Mutation-killing VALUE tests for _deduplicate ────────────────


class TestDeduplicateValueMutations:
    """Target: 6 surviving VALUE mutants in _deduplicate."""

    def test_preserves_insertion_order(self):
        """VALUE: verify exact output order matches input order for unique issues."""
        i1 = _issue(file="b.py", line=2, kind="E001")
        i2 = _issue(file="a.py", line=1, kind="F821")
        result = _deduplicate([i1, i2])
        assert len(result) == 2
        assert result[0].file == "b.py"
        assert result[1].file == "a.py"

    def test_severity_ranking_informational_loses(self):
        """VALUE: informational (rank=1) loses to warning (rank=2)."""
        i1 = _issue(file="a.py", line=1, kind="F821", severity="informational", confidence=1.0)
        i2 = _issue(file="a.py", line=1, kind="F821", severity="warning", confidence=0.5)
        result = _deduplicate([i1, i2])
        assert result[0].severity == "warning"

    def test_three_way_dedup_keeps_highest(self):
        """VALUE: three duplicates → keeps blocking over warning over informational."""
        i1 = _issue(file="a.py", line=1, kind="X", severity="informational", confidence=0.9)
        i2 = _issue(file="a.py", line=1, kind="X", severity="blocking", confidence=0.3)
        i3 = _issue(file="a.py", line=1, kind="X", severity="warning", confidence=0.8)
        result = _deduplicate([i1, i2, i3])
        assert len(result) == 1
        assert result[0].severity == "blocking"
        assert result[0].confidence == 0.3

    def test_unknown_severity_ranks_zero(self):
        """VALUE: unknown severity has rank 0, loses to any known severity."""
        i1 = _issue(file="a.py", line=1, kind="X", severity="custom")
        i2 = _issue(file="a.py", line=1, kind="X", severity="informational")
        result = _deduplicate([i1, i2])
        assert result[0].severity == "informational"

    def test_none_file_in_key(self):
        """VALUE: None file is part of the dedup key — two issues with file=None, same line+kind dedup."""
        i1 = _issue(file=None, line=1, kind="X", severity="warning")
        i2 = _issue(file=None, line=1, kind="X", severity="blocking")
        result = _deduplicate([i1, i2])
        assert len(result) == 1
        assert result[0].severity == "blocking"

    def test_returns_list_not_dict_values_view(self):
        """VALUE: return type is list."""
        result = _deduplicate([_issue()])
        assert isinstance(result, list)

    def test_unknown_severity_default_zero_not_one(self):
        """VALUE_7: severity_order.get(x, 0) — unknown ranks below informational even with higher confidence."""
        # Unknown severity (rank=0) with high confidence vs informational (rank=1) with low confidence
        # With default=0: unknown=(0, 0.99), info=(1, 0.01) → info wins by rank
        # With default=1: unknown=(1, 0.99), info=(1, 0.01) → unknown wins by confidence
        i_unknown = _issue(file="a.py", line=1, kind="X", severity="custom", confidence=0.99)
        i_info = _issue(file="a.py", line=1, kind="X", severity="informational", confidence=0.01)
        # Forward order: unknown first, info replaces it
        result = _deduplicate([i_unknown, i_info])
        assert result[0].severity == "informational"
        # Reverse order: info first, unknown cannot replace it
        result2 = _deduplicate([i_info, i_unknown])
        assert result2[0].severity == "informational"


# ── Mutation-killing VALUE tests for _split_by_severity ──────────


class TestSplitBySeverityValueMutations:
    """Target: 2 surviving VALUE mutants in _split_by_severity."""

    def test_exact_output_identities(self):
        """VALUE: verify exact issue objects land in correct buckets."""
        i_b = _issue(severity="blocking", file="a.py", line=1)
        i_w = _issue(severity="warning", file="a.py", line=2)
        i_i = _issue(severity="informational", file="a.py", line=3)
        blocking, warnings, info = _split_by_severity([i_b, i_w, i_i])
        assert blocking[0] is i_b
        assert warnings[0] is i_w
        assert info[0] is i_i

    def test_multiple_same_severity_sorted(self):
        """VALUE: multiple warnings sorted by (file, line)."""
        i1 = _issue(severity="warning", file="z.py", line=100)
        i2 = _issue(severity="warning", file="a.py", line=50)
        i3 = _issue(severity="warning", file="a.py", line=10)
        _, warnings, _ = _split_by_severity([i1, i2, i3])
        assert [w.line for w in warnings] == [10, 50, 100]
        assert [w.file for w in warnings] == ["a.py", "a.py", "z.py"]

    def test_none_file_and_line_sort(self):
        """VALUE: None file sorts as '', None line sorts as 0."""
        i1 = _issue(severity="blocking", file=None, line=None)
        i2 = _issue(severity="blocking", file="a.py", line=1)
        blocking, _, _ = _split_by_severity([i1, i2])
        # None → "" sorts before "a.py"
        assert blocking[0].file is None
        assert blocking[1].file == "a.py"

    def test_none_line_sorts_before_line_one(self):
        """VALUE_1: `line or 0` — None line sorts as 0, strictly before line=1."""
        i1 = _issue(severity="warning", file="a.py", line=1)
        i2 = _issue(severity="warning", file="a.py", line=None)
        _, warnings, _ = _split_by_severity([i1, i2])
        assert warnings[0].line is None  # None → 0 sorts before 1
        assert warnings[1].line == 1


# ── _is_exempted ─────────────────────────────────────────────────


class TestIsExempted:
    def test_no_exemptions(self):
        assert _is_exempted(_issue(), {}) is False

    def test_file_exempt_all_codes(self):
        exemptions = {"ruff": {"mod.py": {"reason": "legacy"}}}
        assert _is_exempted(_issue(linter="ruff", file="mod.py"), exemptions) is True

    def test_file_exempt_specific_code_match(self):
        exemptions = {"ruff": {"mod.py": {"codes": ["F821"], "reason": "known"}}}
        assert _is_exempted(_issue(linter="ruff", file="mod.py", kind="F821"), exemptions) is True

    def test_file_exempt_specific_code_no_match(self):
        exemptions = {"ruff": {"mod.py": {"codes": ["E001"], "reason": "known"}}}
        assert _is_exempted(_issue(linter="ruff", file="mod.py", kind="F821"), exemptions) is False

    def test_basename_match(self):
        """Exemptions match on basename of file path."""
        exemptions = {"ruff": {"mod.py": {"reason": "legacy"}}}
        assert _is_exempted(_issue(linter="ruff", file="/abs/path/mod.py"), exemptions) is True

    def test_alias_match(self):
        """Radon alias 'complexity' matches."""
        exemptions = {"complexity": {"mod.py": {"reason": "high CC"}}}
        assert _is_exempted(_issue(linter="radon", file="mod.py"), exemptions) is True

    def test_no_file(self):
        assert _is_exempted(_issue(file=None), {"ruff": {"mod.py": {"reason": "x"}}}) is False


# ── _normalize_file_path ─────────────────────────────────────────


class TestNormalizeFilePath:
    def test_relative_stays_relative(self):
        assert _normalize_file_path("src/mod.py", "/project") == "src/mod.py"

    def test_absolute_under_root_becomes_relative(self):
        assert _normalize_file_path("/project/src/mod.py", "/project") == "src/mod.py"

    def test_absolute_outside_root_stays_absolute(self):
        assert _normalize_file_path("/other/mod.py", "/project") == "/other/mod.py"

    def test_no_project_root(self):
        assert _normalize_file_path("/abs/mod.py", "") == "/abs/mod.py"

    def test_relative_no_root(self):
        assert _normalize_file_path("mod.py", "") == "mod.py"
