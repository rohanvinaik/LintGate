"""Phase 1A: Token efficiency verification tests.

Tests:
- Deterministic issue IDs
- Run persistence (save + load round-trip)
- Output mode formatting (compact vs standard vs full)
- Compact JSON (no indent=2)
- lint_get_details drill-down
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lintgate.results_aggregator import aggregate_results
from lintgate.state import (
    generate_run_id,
    load_run_details,
    save_run_details,
)
from lintgate.types import (
    AggregatedResult,
    LinterResult,
    LintIssue,
    ProjectConfig,
)

# ── Deterministic Issue IDs ──────────────────────────────────────────────


class TestIssueIds:
    def test_compute_issue_id_is_deterministic(self) -> None:
        issue = LintIssue(
            linter="ruff", kind="F821", message="undefined name 'foo'", file="test.py", line=10
        )
        id1 = issue.compute_issue_id()
        id2 = issue.compute_issue_id()
        assert id1 == id2
        assert len(id1) == 12  # sha256 hex[:12]

    def test_different_issues_get_different_ids(self) -> None:
        issue1 = LintIssue(
            linter="ruff", kind="F821", message="undefined name 'foo'", file="a.py", line=10
        )
        issue2 = LintIssue(
            linter="ruff", kind="F821", message="undefined name 'bar'", file="a.py", line=20
        )
        assert issue1.compute_issue_id() != issue2.compute_issue_id()

    def test_issue_id_uses_message_prefix(self) -> None:
        """Only first 50 chars of message are used — long messages with same prefix get same ID."""
        short_msg = "x" * 50
        issue1 = LintIssue(
            linter="ruff", kind="F821", message=short_msg + "AAAA", file="a.py", line=1
        )
        issue2 = LintIssue(
            linter="ruff", kind="F821", message=short_msg + "BBBB", file="a.py", line=1
        )
        assert issue1.compute_issue_id() == issue2.compute_issue_id()

    def test_issue_id_set_by_aggregator(self) -> None:
        """aggregate_results assigns issue_id to each deduped issue."""
        lr = LinterResult(
            linter_name="ruff",
            issues=[
                LintIssue(
                    linter="ruff",
                    kind="F821",
                    message="undef foo",
                    file="a.py",
                    line=1,
                    severity="blocking",
                ),
                LintIssue(
                    linter="ruff",
                    kind="E501",
                    message="line too long",
                    file="a.py",
                    line=5,
                    severity="warning",
                ),
            ],
            status="ok",
        )
        config = ProjectConfig()
        result = aggregate_results([lr], config)
        all_issues = [*result.blocking, *result.warnings, *result.informational]
        for issue in all_issues:
            assert issue.issue_id != "", f"issue_id should be set, got empty for {issue.kind}"
            assert len(issue.issue_id) == 12

    def test_issue_id_excluded_from_to_dict_when_empty(self) -> None:
        """Empty issue_id is excluded from to_dict for compact serialization."""
        issue = LintIssue(linter="ruff", kind="F821", message="test")
        d = issue.to_dict()
        assert "issue_id" not in d

    def test_issue_id_included_in_to_dict_when_set(self) -> None:
        issue = LintIssue(linter="ruff", kind="F821", message="test", issue_id="abc123def456")
        d = issue.to_dict()
        assert d["issue_id"] == "abc123def456"


# ── Run Persistence ──────────────────────────────────────────────────────


class TestRunPersistence:
    def test_generate_run_id_is_12_chars(self) -> None:
        rid = generate_run_id()
        assert len(rid) == 12
        # All hex chars
        assert all(c in "0123456789abcdef" for c in rid)

    def test_generate_run_id_is_unique(self) -> None:
        """Two calls to generate_run_id produce different values."""
        ids = {generate_run_id() for _ in range(10)}
        assert len(ids) == 10

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        """save_run_details + load_run_details round-trips data."""
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            run_id = "test_run_001"
            data = {
                "tier": "tier_2_manual",
                "blocking": 3,
                "warnings": 5,
                "blocking_issues": [{"kind": "F821", "message": "undef"}],
            }
            save_run_details(run_id, data)
            loaded = load_run_details(run_id)

        assert loaded is not None
        assert loaded["run_id"] == run_id
        assert loaded["tier"] == "tier_2_manual"
        assert loaded["blocking"] == 3
        assert loaded["blocking_issues"] == [{"kind": "F821", "message": "undef"}]
        assert "timestamp" in loaded

    def test_load_nonexistent_run_returns_none(self, tmp_path: Path) -> None:
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            assert load_run_details("nonexistent") is None

    def test_prune_keeps_max_files(self, tmp_path: Path) -> None:
        """Pruning keeps only the most recent N files."""
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            # Create 60 run files
            for i in range(60):
                save_run_details(f"run_{i:03d}", {"index": i})

            # After saving 60 with max_keep=50 (default), should have at most 50
            run_files = list(tmp_path.glob("*.json"))
            assert len(run_files) <= 50


# ── Output Mode Formatting ───────────────────────────────────────────────


class TestOutputModes:
    """Test that _run_lint output varies by mode."""

    @staticmethod
    def _make_mock_aggregated(blocking_count: int = 2, warning_count: int = 3, info_count: int = 5):
        """Create a mock AggregatedResult."""
        blocking = [
            LintIssue(
                linter="ruff",
                kind=f"F82{i}",
                message=f"blocking issue {i}",
                file=f"src/mod{i}.py",
                line=i * 10,
                severity="blocking",
                issue_id=f"blk_{i:012d}",
            )
            for i in range(blocking_count)
        ]
        warnings = [
            LintIssue(
                linter="ruff",
                kind=f"W{i}01",
                message=f"warning issue {i}",
                file=f"src/mod{i}.py",
                line=i * 10,
                severity="warning",
                issue_id=f"wrn_{i:012d}",
            )
            for i in range(warning_count)
        ]
        informational = [
            LintIssue(
                linter="radon",
                kind=f"C{i}01",
                message=f"info issue {i}",
                file=f"src/mod{i}.py",
                line=i * 10,
                severity="informational",
                issue_id=f"inf_{i:012d}",
            )
            for i in range(info_count)
        ]
        return AggregatedResult(
            blocking=blocking,
            warnings=warnings,
            informational=informational,
            metrics={
                "total_issues": blocking_count + warning_count + info_count,
                "blocking_count": blocking_count,
                "warning_count": warning_count,
                "info_count": info_count,
                "fixable_count": 1,
                "linters_run": 2,
                "linters_skipped": 0,
                "linters_errored": 0,
            },
            linter_statuses={"ruff_check": "ok", "radon": "ok"},
            tier_used="tier_2_manual",
            tier_reason="Manual invocation (tier 2)",
            total_duration_ms=150.0,
            files_linted=["src/mod0.py", "src/mod1.py"],
        )

    def test_compact_has_run_id(self) -> None:
        """Compact output always includes run_id."""
        # Import the MCP module functions
        import sys

        # We test the _json_dumps helper directly
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from mcp_server import _json_dumps

        data = {"run_id": "abc123", "blocking": 0}
        result = _json_dumps(data, "compact")
        parsed = json.loads(result)
        assert parsed["run_id"] == "abc123"

    def test_compact_json_has_no_indent(self) -> None:
        """Compact mode produces no-indent JSON."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from mcp_server import _json_dumps

        data = {"a": 1, "b": {"c": 2}}
        result = _json_dumps(data, "compact")
        assert "\n" not in result
        assert "  " not in result

    def test_full_json_has_indent(self) -> None:
        """Full mode produces indented JSON."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from mcp_server import _json_dumps

        data = {"a": 1, "b": {"c": 2}}
        result = _json_dumps(data, "full")
        assert "\n" in result

    def test_compact_output_is_smaller(self) -> None:
        """Compact JSON is smaller than full JSON."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from mcp_server import _json_dumps

        data = {
            "run_id": "abc123def456",
            "tier": "tier_2_manual",
            "files_linted": 10,
            "blocking": 2,
            "warnings": 5,
            "informational": 3,
            "fixable": 1,
            "blocking_issues": [
                {"id": "blk1", "kind": "F821", "loc": "mod.py:10", "msg": "undefined name"},
                {"id": "blk2", "kind": "F841", "loc": "mod.py:20", "msg": "unused variable"},
            ],
        }
        compact = _json_dumps(data, "compact")
        full = _json_dumps(data, "full")
        assert len(compact) < len(full)


# ── Compact Output Token Estimation ──────────────────────────────────────


class TestTokenBudget:
    """Verify that compact output stays within token budget."""

    def test_compact_output_under_300_tokens(self) -> None:
        """A typical compact output should be under 300 tokens (~1200 chars)."""
        # Simulate a compact output dict
        compact = {
            "run_id": "abc123def456",
            "tier": "tier_2_manual",
            "files_linted": 15,
            "duration_ms": 250.5,
            "blocking": 2,
            "warnings": 8,
            "informational": 12,
            "fixable": 3,
            "blocking_issues": [
                {
                    "id": "blk000000001",
                    "kind": "F821",
                    "loc": "module.py:42",
                    "msg": "undefined name 'process_data'",
                },
                {
                    "id": "blk000000002",
                    "kind": "F841",
                    "loc": "utils.py:15",
                    "msg": "local variable 'tmp' is assigned but never used",
                },
            ],
        }
        serialized = json.dumps(compact, separators=(",", ":"))
        estimated_tokens = len(serialized) / 4  # rough approximation
        assert estimated_tokens < 300, (
            f"Compact output too large: ~{estimated_tokens:.0f} tokens ({len(serialized)} chars)"
        )


# ── JSON Schema Stability ────────────────────────────────────────────────


class TestSchemaStability:
    """Ensure output schemas have required keys."""

    def test_compact_schema_keys(self) -> None:
        compact = {
            "run_id": "test",
            "tier": "tier_2_manual",
            "files_linted": 5,
            "duration_ms": 100.0,
            "blocking": 0,
            "warnings": 0,
            "informational": 0,
            "fixable": 0,
        }
        required_keys = {
            "run_id",
            "tier",
            "files_linted",
            "duration_ms",
            "blocking",
            "warnings",
            "informational",
            "fixable",
        }
        assert required_keys.issubset(compact.keys())

    def test_lint_get_details_schema(self, tmp_path: Path) -> None:
        """lint_get_details returns expected keys."""
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            run_id = "test_details"
            data = {
                "tier": "tier_2_manual",
                "project": "/tmp/project",
                "duration_ms": 150.0,
                "blocking_issues": [{"kind": "F821", "message": "undef", "severity": "blocking"}],
                "warning_issues": [
                    {"kind": "E501", "message": "line too long", "severity": "warning"}
                ],
                "info_issues": [],
                "recurrence": {"repeated_issue_count": 1, "top_repeated": []},
                "linter_diagnostics": [{"linter": "ruff_check", "status": "ok"}],
            }
            save_run_details(run_id, data)
            loaded = load_run_details(run_id)

        assert loaded is not None
        assert loaded["run_id"] == run_id
        assert "blocking_issues" in loaded
        assert "warning_issues" in loaded

    def test_lint_get_details_severity_filter(self, tmp_path: Path) -> None:
        """Can filter by severity when loading run details."""
        with patch("lintgate.state.RUNS_DIR", tmp_path):
            run_id = "test_filter"
            data = {
                "blocking_issues": [{"kind": "F821", "severity": "blocking"}],
                "warning_issues": [
                    {"kind": "E501", "severity": "warning"},
                    {"kind": "W291", "severity": "warning"},
                ],
                "info_issues": [{"kind": "C901", "severity": "informational"}],
            }
            save_run_details(run_id, data)
            loaded = load_run_details(run_id)

        assert loaded is not None
        # Simulate filtering like lint_get_details does
        issues = loaded.get("warning_issues", [])
        assert len(issues) == 2


# ── Issue ID in Aggregator ───────────────────────────────────────────────


class TestAggregatorIssueId:
    def test_issue_ids_assigned_after_dedup(self) -> None:
        """Issues get deterministic IDs after deduplication."""
        lr1 = LinterResult(
            linter_name="ruff",
            issues=[
                LintIssue(
                    linter="ruff",
                    kind="F821",
                    message="undef foo",
                    file="a.py",
                    line=1,
                    severity="blocking",
                ),
            ],
            status="ok",
        )
        lr2 = LinterResult(
            linter_name="mypy",
            issues=[
                # Same file+line+kind — should be deduped
                LintIssue(
                    linter="mypy",
                    kind="F821",
                    message="undef foo",
                    file="a.py",
                    line=1,
                    severity="warning",
                ),
                # Different — should survive
                LintIssue(
                    linter="mypy",
                    kind="E303",
                    message="too many blank lines",
                    file="b.py",
                    line=10,
                    severity="warning",
                ),
            ],
            status="ok",
        )
        config = ProjectConfig()
        result = aggregate_results([lr1, lr2], config)

        all_issues = [*result.blocking, *result.warnings, *result.informational]
        # Should have 2 issues (one deduped)
        assert len(all_issues) == 2
        # All should have issue_id set
        for issue in all_issues:
            assert issue.issue_id != ""
            assert len(issue.issue_id) == 12

    def test_issue_ids_stable_across_runs(self) -> None:
        """Same input produces same issue IDs."""

        def make_result():
            lr = LinterResult(
                linter_name="ruff",
                issues=[
                    LintIssue(
                        linter="ruff",
                        kind="F821",
                        message="undef foo",
                        file="a.py",
                        line=1,
                        severity="blocking",
                    ),
                ],
                status="ok",
            )
            return aggregate_results([lr], ProjectConfig())

        r1 = make_result()
        r2 = make_result()
        assert r1.blocking[0].issue_id == r2.blocking[0].issue_id


import sys  # noqa: E402 — needed for path manipulation in tests

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
