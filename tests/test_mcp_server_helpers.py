"""Tests for pure helper functions in mcp_server.py.

Covers: _collect_python_files, _collect_all_issues, _build_linter_diagnostics,
_normalize_linter_names, _build_cp_full_details, _validate_tier, _validate_strictness,
_validate_project_root, _compute_lint_metadata, _build_next_actions,
_build_onboarding_status.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest import mock

import pytest

from lintgate.types import AggregatedResult, LinterResult, LintIssue, LintTier
from mcp_server import (
    _build_compact_output,
    _build_cp_full_details,
    _build_linter_diagnostics,
    _build_next_actions,
    _build_onboarding_status,
    _build_standard_output,
    _collect_all_issues,
    _collect_python_files,
    _normalize_linter_names,
    _validate_project_root,
    _validate_strictness,
    _validate_tier,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_issue(
    linter: str = "ruff",
    kind: str = "F821",
    message: str = "undefined name",
    severity: str = "warning",
    file: str | None = None,
    line: int | None = None,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        message=message,
        severity=severity,
        file=file,
        line=line,
    )


def _make_linter_result(
    linter_name: str = "ruff_check",
    issues: list[LintIssue] | None = None,
    status: str = "ok",
    error: str | None = None,
    duration_ms: float = 42.0,
) -> LinterResult:
    return LinterResult(
        linter_name=linter_name,
        issues=issues or [],
        status=status,
        error=error,
        duration_ms=duration_ms,
    )


# ── _validate_tier ───────────────────────────────────────────────────────


class TestValidateTier:
    def test_valid_tiers(self):
        for tier in (0, 1, 2, 3):
            assert _validate_tier(tier) == tier

    def test_returns_input_value(self):
        assert _validate_tier(2) == 2

    def test_invalid_tier_raises(self):
        with pytest.raises(ValueError, match="Invalid tier 5"):
            _validate_tier(5)

    def test_negative_tier_raises(self):
        with pytest.raises(ValueError, match="Invalid tier -1"):
            _validate_tier(-1)

    def test_large_tier_raises(self):
        with pytest.raises(ValueError, match="Invalid tier 99"):
            _validate_tier(99)

    def test_error_message_shows_valid_tiers(self):
        with pytest.raises(ValueError, match=r"expected one of \[0, 1, 2, 3\]"):
            _validate_tier(10)


# ── _validate_strictness ─────────────────────────────────────────────────


class TestValidateStrictness:
    def test_relaxed(self):
        assert _validate_strictness("relaxed") == "relaxed"

    def test_normal(self):
        assert _validate_strictness("normal") == "normal"

    def test_strict(self):
        assert _validate_strictness("strict") == "strict"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid strictness 'extra'"):
            _validate_strictness("extra")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid strictness"):
            _validate_strictness("")

    def test_case_sensitive(self):
        with pytest.raises(ValueError, match="Invalid strictness 'Normal'"):
            _validate_strictness("Normal")

    def test_error_lists_valid_options(self):
        with pytest.raises(ValueError, match="normal"):
            _validate_strictness("bogus")


# ── _validate_project_root ───────────────────────────────────────────────


class TestValidateProjectRoot:
    def test_valid_directory(self, tmp_path):
        result = _validate_project_root(str(tmp_path))
        assert result == str(tmp_path)
        assert os.path.isabs(result)

    def test_returns_absolute_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subdir = tmp_path / "sub"
        subdir.mkdir()
        result = _validate_project_root(str(subdir))
        assert os.path.isabs(result)
        assert result == str(subdir)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="must be an existing directory"):
            _validate_project_root("")

    def test_nonexistent_path_raises(self):
        with pytest.raises(ValueError, match="must be an existing directory"):
            _validate_project_root("/nonexistent/path/xyz")

    def test_file_path_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="must be an existing directory"):
            _validate_project_root(str(f))

    def test_custom_arg_name(self):
        with pytest.raises(ValueError, match="project_dir must be"):
            _validate_project_root("/nonexistent", arg_name="project_dir")

    def test_default_arg_name(self):
        with pytest.raises(ValueError, match="path must be"):
            _validate_project_root("/nonexistent")


# ── _collect_python_files ────────────────────────────────────────────────


class TestCollectPythonFiles:
    def test_returns_list(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        with mock.patch(
            "lintgate.discovery.discover_project_files",
            return_value=[str(tmp_path / "a.py")],
        ):
            result = _collect_python_files(str(tmp_path))
        assert isinstance(result, list)
        assert len(result) == 1

    def test_empty_project(self, tmp_path):
        with mock.patch("lintgate.discovery.discover_project_files", return_value=[]):
            result = _collect_python_files(str(tmp_path))
        assert result == []

    def test_delegates_to_discover(self, tmp_path):
        expected = [str(tmp_path / "mod.py"), str(tmp_path / "pkg/init.py")]
        with mock.patch("lintgate.discovery.discover_project_files", return_value=expected) as m:
            result = _collect_python_files(str(tmp_path))
        m.assert_called_once_with(str(tmp_path))
        assert result == expected


# ── _collect_all_issues ──────────────────────────────────────────────────


class TestCollectAllIssues:
    def test_all_categories(self):
        blocking = [_make_issue(severity="blocking")]
        warnings = [_make_issue(kind="W001", severity="warning")]
        info = [_make_issue(kind="I001", severity="informational")]
        agg = AggregatedResult(blocking=blocking, warnings=warnings, informational=info)
        result = _collect_all_issues(agg)
        assert len(result) == 3
        assert result[0] is blocking[0]
        assert result[1] is warnings[0]
        assert result[2] is info[0]

    def test_empty_aggregated(self):
        agg = AggregatedResult()
        result = _collect_all_issues(agg)
        assert result == []

    def test_only_blocking(self):
        blocking = [_make_issue(severity="blocking"), _make_issue(severity="blocking", kind="E501")]
        agg = AggregatedResult(blocking=blocking)
        result = _collect_all_issues(agg)
        assert len(result) == 2

    def test_return_type(self):
        agg = AggregatedResult(warnings=[_make_issue()])
        result = _collect_all_issues(agg)
        assert isinstance(result, list)
        assert all(isinstance(i, LintIssue) for i in result)

    def test_preserves_order(self):
        b = _make_issue(kind="B1", severity="blocking")
        w = _make_issue(kind="W1", severity="warning")
        i = _make_issue(kind="I1", severity="informational")
        agg = AggregatedResult(blocking=[b], warnings=[w], informational=[i])
        result = _collect_all_issues(agg)
        assert [x.kind for x in result] == ["B1", "W1", "I1"]


# ── _build_linter_diagnostics ────────────────────────────────────────────


class TestBuildLinterDiagnostics:
    def test_single_linter(self):
        results = [_make_linter_result()]
        diags = _build_linter_diagnostics(results)
        assert len(diags) == 1
        d = diags[0]
        assert d["linter"] == "ruff_check"
        assert d["status"] == "ok"
        assert d["issue_count"] == 0
        assert d["duration_ms"] == 42.0
        assert d["error"] is None

    def test_multiple_linters_sorted_by_name(self):
        results = [
            _make_linter_result(linter_name="mypy", duration_ms=100.0),
            _make_linter_result(linter_name="bandit", duration_ms=50.0),
            _make_linter_result(linter_name="ruff_check", duration_ms=30.0),
        ]
        diags = _build_linter_diagnostics(results)
        assert [d["linter"] for d in diags] == ["bandit", "mypy", "ruff_check"]

    def test_issue_count_reflects_issues(self):
        issues = [_make_issue(), _make_issue(kind="E501")]
        results = [_make_linter_result(issues=issues)]
        diags = _build_linter_diagnostics(results)
        assert diags[0]["issue_count"] == 2

    def test_error_linter(self):
        results = [
            _make_linter_result(
                linter_name="mypy",
                status="error",
                error="mypy not found",
                duration_ms=0.0,
            )
        ]
        diags = _build_linter_diagnostics(results)
        assert diags[0]["status"] == "error"
        assert diags[0]["error"] == "mypy not found"

    def test_empty_results(self):
        diags = _build_linter_diagnostics([])
        assert diags == []

    def test_duration_rounded(self):
        results = [_make_linter_result(duration_ms=12.345678)]
        diags = _build_linter_diagnostics(results)
        assert diags[0]["duration_ms"] == 12.3

    def test_return_type(self):
        results = [_make_linter_result()]
        diags = _build_linter_diagnostics(results)
        assert isinstance(diags, list)
        assert isinstance(diags[0], dict)


# ── _normalize_linter_names ──────────────────────────────────────────────


class TestNormalizeLinterNames:
    def setup_method(self):
        _normalize_linter_names.cache_clear()

    def test_simple_merge(self):
        result = _normalize_linter_names(("ruff_check",), ("mypy",))
        assert result == ["ruff_check", "mypy"]

    def test_deduplication(self):
        result = _normalize_linter_names(("ruff_check", "mypy"), ("mypy", "bandit"))
        assert result == ["ruff_check", "mypy", "bandit"]

    def test_preserves_order(self):
        result = _normalize_linter_names(("c", "a", "b"), ())
        assert result == ["c", "a", "b"]

    def test_empty_both(self):
        result = _normalize_linter_names((), ())
        assert result == []

    def test_empty_base(self):
        result = _normalize_linter_names((), ("x", "y"))
        assert result == ["x", "y"]

    def test_empty_extra(self):
        result = _normalize_linter_names(("x",), ())
        assert result == ["x"]

    def test_all_duplicates(self):
        result = _normalize_linter_names(("a", "b"), ("a", "b"))
        assert result == ["a", "b"]

    def test_return_type(self):
        result = _normalize_linter_names(("ruff",), ("mypy",))
        assert isinstance(result, list)
        assert all(isinstance(n, str) for n in result)

    def test_caching(self):
        r1 = _normalize_linter_names(("a",), ("b",))
        r2 = _normalize_linter_names(("a",), ("b",))
        assert r1 == r2
        info = _normalize_linter_names.cache_info()
        assert info.hits >= 1


# ── _build_cp_full_details ───────────────────────────────────────────────


class TestBuildCpFullDetails:
    @staticmethod
    def _make_coherence(
        state="stable",
        summary="All good",
        recommended_action="none",
        silent_channels=frozenset(),
        loud_channels=frozenset(),
    ):
        return SimpleNamespace(
            state=state,
            summary=summary,
            recommended_action=recommended_action,
            silent_channels=silent_channels,
            loud_channels=loud_channels,
        )

    @staticmethod
    def _make_channel_result(
        channel="lint",
        status="pass",
        severity="none",
        findings=None,
        repairs=None,
        metrics=None,
        duration_ms=10.0,
        error_message=None,
    ):
        return SimpleNamespace(
            channel=channel,
            status=status,
            severity=severity,
            findings=findings or [],
            repairs=repairs or [],
            metrics=metrics or {},
            duration_ms=duration_ms,
            error_message=error_message,
        )

    @staticmethod
    def _make_mesh_result(
        channel_results=None,
        coherence=None,
        duration_ms=100.0,
        partial=False,
        incomplete_channels=None,
    ):
        return SimpleNamespace(
            channel_results=channel_results or [],
            coherence=coherence or TestBuildCpFullDetails._make_coherence(),
            duration_ms=duration_ms,
            partial=partial,
            incomplete_channels=incomplete_channels or [],
        )

    def test_empty_mesh(self):
        mesh = self._make_mesh_result()
        result = _build_cp_full_details(mesh, {})
        assert result["coherence"]["state"] == "stable"
        assert result["channels"] == {}
        assert result["duration_ms"] == 100.0
        assert result["partial"] is False
        assert result["finding_index"] == {}

    def test_channel_results_included(self):
        finding = SimpleNamespace(to_dict=lambda: {"id": "f1", "msg": "bad"})
        repair = SimpleNamespace(
            action_id="r1",
            kind="command",
            summary="fix it",
            safe=True,
            payload={"cmd": "ruff --fix"},
        )
        cr = self._make_channel_result(
            channel="lint",
            status="fail",
            severity="blocking",
            findings=[finding],
            repairs=[repair],
            metrics={"issue_count": 1},
            duration_ms=55.5,
            error_message=None,
        )
        mesh = self._make_mesh_result(channel_results=[cr])
        result = _build_cp_full_details(mesh, {"f1": {"channel": "lint"}})

        assert "lint" in result["channels"]
        lint_ch = result["channels"]["lint"]
        assert lint_ch["status"] == "fail"
        assert lint_ch["severity"] == "blocking"
        assert lint_ch["duration_ms"] == 55.5
        assert lint_ch["error"] is None
        assert lint_ch["findings"] == [{"id": "f1", "msg": "bad"}]
        assert len(lint_ch["repairs"]) == 1
        assert lint_ch["repairs"][0]["action_id"] == "r1"
        assert lint_ch["repairs"][0]["safe"] is True
        assert lint_ch["metrics"] == {"issue_count": 1}

    def test_skipped_channels_excluded(self):
        cr = self._make_channel_result(channel="tests", status="skip")
        mesh = self._make_mesh_result(channel_results=[cr])
        result = _build_cp_full_details(mesh, {})
        assert "tests" not in result["channels"]

    def test_coherence_fields(self):
        coherence = self._make_coherence(
            state="isolated",
            summary="lint fails",
            recommended_action="fix lint",
            silent_channels=frozenset({"tests", "deps"}),
            loud_channels=frozenset({"lint"}),
        )
        mesh = self._make_mesh_result(coherence=coherence)
        result = _build_cp_full_details(mesh, {})
        coh = result["coherence"]
        assert coh["state"] == "isolated"
        assert coh["summary"] == "lint fails"
        assert coh["recommended_action"] == "fix lint"
        assert sorted(coh["silent_channels"]) == ["deps", "tests"]
        assert coh["loud_channels"] == ["lint"]

    def test_partial_and_incomplete(self):
        mesh = self._make_mesh_result(partial=True, incomplete_channels=["tests", "deps"])
        result = _build_cp_full_details(mesh, {})
        assert result["partial"] is True
        assert result["incomplete_channels"] == ["tests", "deps"]

    def test_multiple_channels(self):
        cr1 = self._make_channel_result(channel="lint", status="pass")
        cr2 = self._make_channel_result(channel="tests", status="fail")
        mesh = self._make_mesh_result(channel_results=[cr1, cr2])
        result = _build_cp_full_details(mesh, {})
        assert set(result["channels"].keys()) == {"lint", "tests"}

    def test_finding_index_passed_through(self):
        index = {"f1": {"channel": "lint"}, "f2": {"channel": "tests"}}
        mesh = self._make_mesh_result()
        result = _build_cp_full_details(mesh, index)
        assert result["finding_index"] == index

    def test_return_type(self):
        mesh = self._make_mesh_result()
        result = _build_cp_full_details(mesh, {})
        assert isinstance(result, dict)
        assert isinstance(result["coherence"], dict)
        assert isinstance(result["channels"], dict)


# ── _compute_lint_metadata ───────────────────────────────────────────────


class TestComputeLintMetadata:
    def test_basic_metadata(self, tmp_path):
        from mcp_server import _compute_lint_metadata

        blocking = [_make_issue(severity="blocking")]
        warnings = [_make_issue(kind="W1", severity="warning")]
        info = [_make_issue(kind="I1", severity="informational")]
        agg = AggregatedResult(
            blocking=blocking,
            warnings=warnings,
            informational=info,
            metrics={"total_issues": 3, "fixable_count": 1, "linters_run": 2},
            linter_statuses={"ruff_check": "ok", "mypy": "ok"},
        )
        linter_results = [
            _make_linter_result(linter_name="ruff_check"),
            _make_linter_result(linter_name="mypy", duration_ms=80.0),
        ]
        lint_tier = LintTier(
            name="tier_1_manual",
            linters=["ruff_check", "mypy"],
            files=[str(tmp_path / "a.py")],
            reason="Manual invocation (tier 1)",
        )
        project_root = str(tmp_path)
        files = [str(tmp_path / "a.py")]

        with (
            mock.patch(
                "mcp_server.update_issue_memory",
                return_value={
                    "repeated_issue_count": 0,
                    "unique_signatures_tracked": 0,
                    "top_repeated": [],
                },
            ),
            mock.patch("mcp_server.load_last_run", return_value=None),
            mock.patch("mcp_server.format_report", return_value=None),
            mock.patch("mcp_server.save_run"),
            mock.patch("mcp_server.save_run_details"),
            mock.patch("mcp_server.log_metric"),
            mock.patch("mcp_server.generate_run_id", return_value="test-run-123"),
        ):
            run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
                agg, linter_results, lint_tier, project_root, files, 150.0, "compact"
            )

        assert run_id == "test-run-123"
        assert full_details["tier"] == "tier_1_manual"
        assert full_details["reason"] == "Manual invocation (tier 1)"
        assert full_details["project"] == project_root
        assert full_details["files_linted"] == 1
        assert full_details["duration_ms"] == 150.0
        assert full_details["blocking"] == 1
        assert full_details["warnings"] == 1
        assert full_details["informational"] == 1
        assert full_details["total_issues"] == 3
        assert full_details["fixable"] == 1
        assert full_details["linters_run"] == 2
        assert lint_delta is None
        assert report is None

    def test_empty_aggregated(self, tmp_path):
        from mcp_server import _compute_lint_metadata

        agg = AggregatedResult(metrics={}, linter_statuses={})
        lint_tier = LintTier(
            name="tier_0_manual",
            linters=["ruff_check"],
            files=[],
            reason="Manual invocation (tier 0)",
        )

        with (
            mock.patch(
                "mcp_server.update_issue_memory",
                return_value={
                    "repeated_issue_count": 0,
                    "unique_signatures_tracked": 0,
                    "top_repeated": [],
                },
            ),
            mock.patch("mcp_server.load_last_run", return_value=None),
            mock.patch("mcp_server.format_report", return_value=None),
            mock.patch("mcp_server.save_run"),
            mock.patch("mcp_server.save_run_details"),
            mock.patch("mcp_server.log_metric"),
            mock.patch("mcp_server.generate_run_id", return_value="empty-run"),
        ):
            run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
                agg, [], lint_tier, str(tmp_path), [], 0.0, "full"
            )

        assert run_id == "empty-run"
        assert full_details["blocking"] == 0
        assert full_details["warnings"] == 0
        assert full_details["informational"] == 0
        assert full_details["total_issues"] == 0
        assert full_details["fixable"] == 0
        assert full_details["files_linted"] == 0

    def test_recurrence_default_on_failure(self, tmp_path):
        from mcp_server import _compute_lint_metadata

        agg = AggregatedResult(metrics={}, linter_statuses={})
        lint_tier = LintTier(
            name="tier_0_manual",
            linters=[],
            files=[],
            reason="test",
        )

        with (
            mock.patch("mcp_server.update_issue_memory", side_effect=RuntimeError("boom")),
            mock.patch("mcp_server.load_last_run", return_value=None),
            mock.patch("mcp_server.format_report", return_value=None),
            mock.patch("mcp_server.save_run"),
            mock.patch("mcp_server.save_run_details"),
            mock.patch("mcp_server.log_metric"),
            mock.patch("mcp_server.generate_run_id", return_value="r-fallback"),
        ):
            run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
                agg, [], lint_tier, str(tmp_path), [], 0.0, "compact"
            )

        assert recurrence["repeated_issue_count"] == 0
        assert recurrence["unique_signatures_tracked"] == 0
        assert recurrence["top_repeated"] == []

    def test_lint_delta_computed_when_previous_exists(self, tmp_path):
        from mcp_server import _compute_lint_metadata

        agg = AggregatedResult(metrics={}, linter_statuses={})
        lint_tier = LintTier(name="tier_0_manual", linters=[], files=[], reason="test")
        prev_run = {"finding_index": {"f1": {"channel": "lint"}}}
        delta_result = {
            "resolved_count": 1,
            "new": [],
            "still_active_count": 0,
            "summary": "1 resolved",
        }

        with (
            mock.patch(
                "mcp_server.update_issue_memory",
                return_value={
                    "repeated_issue_count": 0,
                    "unique_signatures_tracked": 0,
                    "top_repeated": [],
                },
            ),
            mock.patch("mcp_server.load_last_run", return_value=prev_run),
            mock.patch("mcp_server.format_report", return_value=None),
            mock.patch("mcp_server.save_run"),
            mock.patch("mcp_server.save_run_details"),
            mock.patch("mcp_server.log_metric"),
            mock.patch("mcp_server.generate_run_id", return_value="r-delta"),
            mock.patch("lintgate.lint_delta.compute_lint_delta", return_value=delta_result),
        ):
            run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
                agg, [], lint_tier, str(tmp_path), [], 0.0, "compact"
            )

        assert lint_delta == delta_result

    def test_report_included_when_present(self, tmp_path):
        from mcp_server import _compute_lint_metadata

        agg = AggregatedResult(metrics={}, linter_statuses={})
        lint_tier = LintTier(name="tier_0_manual", linters=[], files=[], reason="test")

        with (
            mock.patch(
                "mcp_server.update_issue_memory",
                return_value={
                    "repeated_issue_count": 0,
                    "unique_signatures_tracked": 0,
                    "top_repeated": [],
                },
            ),
            mock.patch("mcp_server.load_last_run", return_value=None),
            mock.patch(
                "mcp_server.format_report",
                return_value={"systemMessage": "2 issues found"},
            ),
            mock.patch("mcp_server.save_run"),
            mock.patch("mcp_server.save_run_details"),
            mock.patch("mcp_server.log_metric"),
            mock.patch("mcp_server.generate_run_id", return_value="r-report"),
        ):
            run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
                agg, [], lint_tier, str(tmp_path), [], 0.0, "full"
            )

        assert full_details["report"] == "2 issues found"

    def test_linter_diagnostics_in_details(self, tmp_path):
        from mcp_server import _compute_lint_metadata

        agg = AggregatedResult(metrics={}, linter_statuses={})
        linter_results = [
            _make_linter_result(linter_name="bandit", duration_ms=10.5),
            _make_linter_result(linter_name="ruff_check", duration_ms=5.3),
        ]
        lint_tier = LintTier(
            name="tier_2_manual",
            linters=["ruff_check", "bandit"],
            files=[],
            reason="test",
        )

        with (
            mock.patch(
                "mcp_server.update_issue_memory",
                return_value={
                    "repeated_issue_count": 0,
                    "unique_signatures_tracked": 0,
                    "top_repeated": [],
                },
            ),
            mock.patch("mcp_server.load_last_run", return_value=None),
            mock.patch("mcp_server.format_report", return_value=None),
            mock.patch("mcp_server.save_run"),
            mock.patch("mcp_server.save_run_details"),
            mock.patch("mcp_server.log_metric"),
            mock.patch("mcp_server.generate_run_id", return_value="r-diag"),
        ):
            run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
                agg, linter_results, lint_tier, str(tmp_path), [], 0.0, "compact"
            )

        diags = full_details["linter_diagnostics"]
        assert len(diags) == 2
        assert diags[0]["linter"] == "bandit"
        assert diags[1]["linter"] == "ruff_check"


# ── _build_next_actions ──────────────────────────────────────────────────


class TestBuildNextActions:
    def test_empty_context_returns_empty(self):
        result = _build_next_actions({})
        assert result == []

    def test_fixable_suggests_lint_fix(self):
        result = _build_next_actions({"fixable": 3, "project": "/tmp/proj"})
        assert len(result) == 1
        action = result[0]
        assert action["tool"] == "lint_fix"
        assert action["args"]["path"] == "/tmp/proj"
        assert action["args"]["dry_run"] is True
        assert action["priority"] == 1

    def test_blocking_with_run_id_suggests_details(self):
        result = _build_next_actions({"blocking": 2, "run_id": "abc-123"})
        assert len(result) == 1
        action = result[0]
        assert action["tool"] == "lint_get_details"
        assert action["args"]["run_id"] == "abc-123"
        assert action["args"]["severity"] == "blocking"
        assert action["priority"] == 2

    def test_blocking_without_run_id_no_action(self):
        result = _build_next_actions({"blocking": 5})
        assert result == []

    def test_warnings_above_5_with_run_id_suggests_details(self):
        result = _build_next_actions({"warnings": 10, "run_id": "run-42"})
        assert len(result) == 1
        action = result[0]
        assert action["tool"] == "lint_get_details"
        assert action["args"]["severity"] == "warning"
        assert action["priority"] == 3
        assert "10" in action["reason"]

    def test_warnings_at_5_no_action(self):
        result = _build_next_actions({"warnings": 5, "run_id": "run-42"})
        assert result == []

    def test_all_conditions_met_returns_multiple_ordered(self):
        result = _build_next_actions(
            {
                "fixable": 2,
                "blocking": 3,
                "warnings": 8,
                "run_id": "run-99",
                "project": "/proj",
            }
        )
        assert len(result) == 3
        priorities = [a["priority"] for a in result]
        assert priorities == [1, 2, 3]
        assert result[0]["tool"] == "lint_fix"
        assert result[1]["tool"] == "lint_get_details"
        assert result[1]["args"]["severity"] == "blocking"
        assert result[2]["tool"] == "lint_get_details"
        assert result[2]["args"]["severity"] == "warning"

    def test_fixable_one_singular_issue(self):
        result = _build_next_actions({"fixable": 1, "project": "/proj"})
        assert len(result) == 1
        assert "issue" in result[0]["reason"]
        assert "issues" not in result[0]["reason"]

    def test_fixable_multiple_plural_issues(self):
        result = _build_next_actions({"fixable": 3, "project": "/proj"})
        assert len(result) == 1
        assert "issues" in result[0]["reason"]

    def test_return_type_is_list_of_dicts(self):
        result = _build_next_actions({"fixable": 1, "project": "."})
        assert isinstance(result, list)
        assert all(isinstance(a, dict) for a in result)

    def test_zero_fixable_no_action(self):
        result = _build_next_actions({"fixable": 0, "project": "/proj"})
        assert result == []

    def test_zero_blocking_no_action(self):
        result = _build_next_actions({"blocking": 0, "run_id": "r1"})
        assert result == []


# ── _build_onboarding_status ─────────────────────────────────────────────


class TestBuildOnboardingStatus:
    def test_no_config_file(self, tmp_path):
        result = _build_onboarding_status(str(tmp_path))
        assert result["config_found"] is False
        assert result["controlplane_enabled"] is False
        assert result["config_state"] == "no_config"
        assert "setup_hint" in result

    def test_config_without_controlplane_section(self, tmp_path):
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("linters:\n  - ruff\n")
        result = _build_onboarding_status(str(tmp_path))
        assert result["config_found"] is True
        assert result["controlplane_enabled"] is False
        assert result["config_state"] == "config_no_controlplane_section"
        assert "setup_hint" in result

    def test_config_with_controlplane_disabled(self, tmp_path):
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: false\n")
        result = _build_onboarding_status(str(tmp_path))
        assert result["config_found"] is True
        assert result["controlplane_enabled"] is False
        assert result["config_state"] == "config_disabled"
        assert "setup_hint" in result

    def test_config_with_controlplane_enabled(self, tmp_path):
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        result = _build_onboarding_status(str(tmp_path))
        assert result["config_found"] is True
        assert result["controlplane_enabled"] is True
        assert result["config_state"] == "config_enabled"
        assert "setup_hint" not in result

    def test_always_has_required_keys(self, tmp_path):
        result = _build_onboarding_status(str(tmp_path))
        required_keys = {
            "config_found",
            "controlplane_enabled",
            "config_state",
            "config_path_checked",
            "automatic_hook_active",
            "using_default_config",
        }
        assert required_keys.issubset(set(result.keys()))

    def test_return_type(self, tmp_path):
        result = _build_onboarding_status(str(tmp_path))
        assert isinstance(result, dict)


# ── Stubs for _build_compact_output / _build_standard_output ──────────


def _make_aggregated(
    blocking: list[LintIssue] | None = None,
    warnings: list[LintIssue] | None = None,
    informational: list[LintIssue] | None = None,
    metrics: dict | None = None,
    linter_statuses: dict | None = None,
) -> AggregatedResult:
    agg = AggregatedResult()
    agg.blocking = blocking or []
    agg.warnings = warnings or []
    agg.informational = informational or []
    agg.metrics = metrics or {"fixable_count": 0, "linters_run": 2}
    agg.linter_statuses = linter_statuses or {}
    return agg


# ── _build_compact_output (sigma=25) ─────────────────────────────────


class TestBuildCompactOutput:
    def test_basic_fields(self):
        agg = _make_aggregated()
        result = _build_compact_output(
            run_id="r1",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=["a.py", "b.py"],
            elapsed_ms=123.456,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert result["run_id"] == "r1"
        assert result["tier"] == "BASIC"
        assert result["files_linted"] == 2
        assert result["duration_ms"] == 123.5
        assert result["blocking"] == 0
        assert result["warnings"] == 0
        assert result["informational"] == 0
        assert result["fixable"] == 0

    def test_blocking_issues_included(self):
        issues = [
            _make_issue(severity="error", file="x.py", line=10),
            _make_issue(severity="error", file="y.py", line=20),
        ]
        agg = _make_aggregated(blocking=issues)
        result = _build_compact_output(
            run_id="r2",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=["x.py"],
            elapsed_ms=50.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert result["blocking"] == 2
        assert len(result["blocking_issues"]) == 2
        assert result["blocking_issues"][0]["kind"] == "F821"

    def test_blocking_truncation(self):
        issues = [_make_issue(severity="error", file=f"f{i}.py") for i in range(10)]
        agg = _make_aggregated(blocking=issues)
        result = _build_compact_output(
            run_id="r3",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=["a.py"],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=3,
        )
        assert len(result["blocking_issues"]) == 3
        assert result["blocking_truncated"] == 7

    def test_no_truncation_key_when_within_limit(self):
        issues = [_make_issue(severity="error", file="a.py")]
        agg = _make_aggregated(blocking=issues)
        result = _build_compact_output(
            run_id="r4",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=["a.py"],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert "blocking_truncated" not in result

    def test_no_blocking_issues_key_when_empty(self):
        agg = _make_aggregated()
        result = _build_compact_output(
            run_id="r5",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert "blocking_issues" not in result

    def test_delta_included(self):
        agg = _make_aggregated()
        delta = {
            "resolved_count": 3,
            "new": [{"id": "W001"}],
            "still_active_count": 5,
            "summary": "improved",
        }
        result = _build_compact_output(
            run_id="r6",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=["a.py"],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=delta,
            max_findings=5,
        )
        assert result["delta"]["resolved"] == 3
        assert result["delta"]["new"] == 1
        assert result["delta"]["remaining"] == 5
        assert result["delta"]["summary"] == "improved"

    def test_no_delta_key_when_none(self):
        agg = _make_aggregated()
        result = _build_compact_output(
            run_id="r7",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert "delta" not in result

    def test_returns_dict(self):
        agg = _make_aggregated()
        result = _build_compact_output(
            run_id="r8",
            lint_tier=LintTier(name="BASIC", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=0.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert isinstance(result, dict)


# ── _build_standard_output (sigma=28) ────────────────────────────────


class TestBuildStandardOutput:
    def test_basic_fields(self):
        agg = _make_aggregated()
        result = _build_standard_output(
            run_id="s1",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=["a.py"],
            elapsed_ms=200.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=10,
        )
        assert result["run_id"] == "s1"
        assert result["tier"] == "STANDARD"
        assert result["files_linted"] == 1
        assert result["linters_run"] == 2
        assert result["linter_statuses"] == {}

    def test_blocking_issues_as_dicts(self):
        issues = [_make_issue(severity="error", file="x.py", line=5)]
        agg = _make_aggregated(blocking=issues)
        result = _build_standard_output(
            run_id="s2",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=["x.py"],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=10,
        )
        assert len(result["blocking_issues"]) == 1
        # to_dict() returns full dict
        assert "kind" in result["blocking_issues"][0]

    def test_warning_issues_fill_remaining_budget(self):
        blocking = [_make_issue(severity="error", file="b.py")]
        warnings = [
            _make_issue(severity="warning", kind="W1", file="w1.py"),
            _make_issue(severity="warning", kind="W2", file="w2.py"),
            _make_issue(severity="warning", kind="W3", file="w3.py"),
        ]
        agg = _make_aggregated(blocking=blocking, warnings=warnings)
        result = _build_standard_output(
            run_id="s3",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=["b.py"],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=3,
        )
        # max_findings=3, 1 blocking → 2 remaining for warnings
        assert len(result["warning_issues"]) == 2
        assert result["warnings_truncated"] == 1

    def test_no_warning_issues_when_budget_zero(self):
        blocking = [_make_issue(severity="error") for _ in range(5)]
        warnings = [_make_issue(severity="warning")]
        agg = _make_aggregated(blocking=blocking, warnings=warnings)
        result = _build_standard_output(
            run_id="s4",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=["a.py"],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert "warning_issues" not in result

    def test_no_warnings_key_when_empty(self):
        agg = _make_aggregated()
        result = _build_standard_output(
            run_id="s5",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=10,
        )
        assert "warning_issues" not in result

    def test_delta_passed_through(self):
        agg = _make_aggregated()
        delta = {"resolved_count": 1, "new": [], "still_active_count": 0}
        result = _build_standard_output(
            run_id="s6",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=delta,
            max_findings=10,
        )
        assert result["delta"] is delta

    def test_no_delta_when_none(self):
        agg = _make_aggregated()
        result = _build_standard_output(
            run_id="s7",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=10.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=10,
        )
        assert "delta" not in result

    def test_returns_dict(self):
        agg = _make_aggregated()
        result = _build_standard_output(
            run_id="s8",
            lint_tier=LintTier(name="STANDARD", linters=[], files=[], reason="test"),
            files=[],
            elapsed_ms=0.0,
            aggregated=agg,
            lint_delta=None,
            max_findings=5,
        )
        assert isinstance(result, dict)


# ── get_workflow_for_intent ──────────────────────────────────────────────


class TestGetWorkflowForIntent:
    def test_valid_intent(self):
        from lintgate.orchestration.workflows import get_workflow_for_intent

        wf = get_workflow_for_intent("implement_issue")
        assert len(wf) > 0
        assert "step" in wf[0]
        assert "tool" in wf[0]
        assert "when" in wf[0]
        assert "args_hint" in wf[0]
        assert "do_not" in wf[0]

    def test_invalid_intent(self):
        from lintgate.orchestration.workflows import get_workflow_for_intent

        wf = get_workflow_for_intent("magic")
        assert wf == []

    def test_none_intent(self):
        from lintgate.orchestration.workflows import get_workflow_for_intent

        wf = get_workflow_for_intent(None)
        assert wf == []


# ── VersionChecker ───────────────────────────────────────────────────────


class TestVersionCheckerPassesProjectRoot:
    def test_passes_project_root(self, tmp_path):
        from lintgate.linters.version_checker import VersionChecker
        from lintgate.types import LinterContext

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.10"\ndependencies = ["ruff>=0.4"]\n'
        )

        with (
            mock.patch(
                "lintgate.linters.version_checker.collect_required_version_specs"
            ) as mock_collect,
            mock.patch("lintgate.linters.version_checker.inspect_tool_versions") as mock_inspect,
        ):
            mock_collect.return_value = {
                "ruff": {"specifiers": [">=0.4"], "sources": ["pyproject.toml"]}
            }
            mock_inspect.return_value = []

            linter = VersionChecker()
            ctx = LinterContext(project_root=str(tmp_path), files=[], config=mock.MagicMock())

            list(linter.run(ctx))

            mock_inspect.assert_called_once()
            args, kwargs = mock_inspect.call_args
            assert kwargs.get("project_root") == str(tmp_path)

    def test_detects_mismatch(self, tmp_path):
        from lintgate.linters.version_checker import VersionChecker
        from lintgate.types import LinterContext

        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["ruff>=0.4"]\n')

        with (
            mock.patch(
                "lintgate.linters.version_checker.collect_required_version_specs"
            ) as mock_collect,
            mock.patch("lintgate.linters.version_checker.inspect_tool_versions") as mock_inspect,
        ):
            mock_collect.return_value = {
                "ruff": {"specifiers": [">=0.4"], "sources": ["pyproject.toml"]}
            }
            mock_inspect.return_value = [
                {
                    "tool": "ruff",
                    "status": "mismatch",
                    "message": "ruff version 0.1.0 does not satisfy required specifier >=0.4",
                    "installed_version": "0.1.0",
                    "required_specifier": ">=0.4",
                    "requirement_sources": ["pyproject.toml:project.dependencies"],
                    "is_optional": False,
                }
            ]

            linter = VersionChecker()
            ctx = LinterContext(project_root=str(tmp_path), files=[], config=mock.MagicMock())

            issues = list(linter.run(ctx))
            assert len(issues) == 1
            assert issues[0].kind == "version-mismatch"
            assert "ruff version 0.1.0" in issues[0].message
            assert issues[0].severity == "blocking"

    def test_handles_optional(self, tmp_path):
        from lintgate.linters.version_checker import VersionChecker
        from lintgate.types import LinterContext

        with (
            mock.patch(
                "lintgate.linters.version_checker.collect_required_version_specs"
            ) as mock_collect,
            mock.patch("lintgate.linters.version_checker.inspect_tool_versions") as mock_inspect,
        ):
            mock_collect.return_value = {}
            mock_inspect.return_value = [
                {
                    "tool": "pip-audit",
                    "status": "missing",
                    "message": "pip-audit is not installed but required",
                    "installed_version": None,
                    "required_specifier": ">=2.0",
                    "requirement_sources": ["pyproject.toml:project.optional-dependencies.dev"],
                    "is_optional": True,
                }
            ]

            linter = VersionChecker()
            ctx = LinterContext(project_root=str(tmp_path), files=[], config=mock.MagicMock())

            issues = list(linter.run(ctx))
            assert len(issues) == 1
            assert issues[0].kind == "version-optional-missing"
            assert issues[0].severity == "warning"


# ── specification_tools register ─────────────────────────────────────────


class TestSpecToolsRegister:
    def test_returns_seven_tools(self):
        from mcp_tools.specification_tools import register

        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        result = register(mcp, helpers)
        assert isinstance(result, dict)
        assert len(result) == 7

    def test_expected_tool_names(self):
        from mcp_tools.specification_tools import register

        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        result = register(mcp, helpers)
        expected_names = {
            "spec_analyze",
            "spec_prescribe",
            "spec_composition",
            "spec_gate_check",
            "spec_file_analyze",
            "spec_file_prescribe",
            "spec_project_rollup",
        }
        assert set(result.keys()) == expected_names

    def test_tools_are_callable(self):
        from mcp_tools.specification_tools import register

        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        result = register(mcp, helpers)
        for name, tool in result.items():
            assert callable(tool), f"{name} is not callable"

    def test_mcp_tool_decorator_called(self):
        from mcp_tools.specification_tools import register

        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        register(mcp, helpers)
        assert mcp.tool.call_count == 7


# ── model_profile MCP ───────────────────────────────────────────────────


class TestModelProfileMcp:
    @staticmethod
    def _load_mcp_funcs():
        import importlib.util
        from pathlib import Path

        mcp_server_path = Path(__file__).resolve().parent.parent / "mcp_server.py"
        spec = importlib.util.spec_from_file_location("lintgate_local_mcp_server", mcp_server_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return (
            mod.model_profile_probe_start,
            mod.model_profile_probe_submit,
            mod.model_profile_status,
        )

    _V2_ANSWERS = {
        "t1_error_reading": {
            "text": "First I would read the source file to understand the bug, "
            "then examine the test output. The real issue is variable shadowing.",
            "tool_calls": ["Read", "Read", "Edit", "Bash"],
            "actions": [
                "Read utils.py to understand function",
                "Read test output carefully",
                "Fix the shadowed variable on line 5",
                "Run pytest to verify",
            ],
            "verify_points": [3],
            "constraint_refs": ["variable shadowing in loop"],
        },
        "t2_retry_behavior": {
            "text": "I would not retry the same command. Instead I would install "
            "the missing dependency first.",
            "tool_calls": ["Read", "Bash", "Bash"],
            "actions": [
                "Read pyproject.toml for dependencies",
                "Install requests into venv",
                "Re-run pytest",
            ],
        },
        "t3_verification_cadence": {
            "text": "Fix each bug one at a time and run tests after each fix.",
            "tool_calls": ["Read", "Edit", "Bash", "Edit", "Bash", "Edit", "Bash"],
            "verify_points": [2, 4, 6],
        },
        "t4_constraint_discovery": {
            "text": "Read CONTRIBUTING.md first, then pyproject.toml, then existing commands.",
            "tool_calls": ["Read", "Read", "Read", "Read", "Grep"],
            "actions": [
                "Read CONTRIBUTING.md for conventions",
                "Read pyproject.toml for entry points",
                "Read existing command in src/cli/commands/list_cmd.py",
                "Read main.py for command registration",
                "Search for patterns in existing commands",
            ],
        },
        "t5_model_updating": {
            "text": "Based on attempt 1 (ALTER TABLE fails in SQLite) and attempt 2 "
            "(foreign key on old table name), I would use a migration approach "
            "that handles both constraints.",
            "constraint_refs": ["SQLite ALTER TABLE", "foreign key constraint"],
        },
    }

    def test_probe_submit_increments_probe_runs(self, monkeypatch, tmp_path):

        probe_start, probe_submit, profile_status = self._load_mcp_funcs()
        lintgate_home = tmp_path / "lintgate_home"
        monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

        first = json.loads(
            probe_submit(
                path=str(tmp_path),
                model_id="claude-opus-4",
                answers=self._V2_ANSWERS,
            )
        )
        second = json.loads(
            probe_submit(
                path=str(tmp_path),
                model_id="claude-opus-4",
                answers=self._V2_ANSWERS,
            )
        )
        status = _load_tool_result(profile_status(path=str(tmp_path), model_id="claude-opus-4"))

        assert first["probe_runs"] == 1
        assert second["probe_runs"] == 2
        assert status["probe_runs"] == 2

    def test_probe_start_returns_tasks(self, monkeypatch, tmp_path):

        probe_start, _, _ = self._load_mcp_funcs()
        lintgate_home = tmp_path / "lintgate_home"
        monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

        result = json.loads(
            probe_start(
                path=str(tmp_path),
                model_id="claude-opus-4",
            )
        )
        assert result["task_count"] == 5
        assert "tasks" in result
        assert "response_schema" in result
        for task in result["tasks"]:
            assert "id" in task
            assert "context" in task
            assert "instruction" in task

    def test_probe_start_rejects_unsupported_probe_set(self, monkeypatch, tmp_path):

        probe_start, _, _ = self._load_mcp_funcs()
        lintgate_home = tmp_path / "lintgate_home"
        monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

        result = json.loads(
            probe_start(
                path=str(tmp_path),
                model_id="claude-opus-4",
                probe_set="full",
            )
        )
        assert "error" in result
        assert "Unsupported probe_set" in result["error"]
        assert result["supported_probe_sets"] == ["quick"]

    def test_probe_submit_v1_compat(self, monkeypatch, tmp_path):

        _, probe_submit, _ = self._load_mcp_funcs()
        lintgate_home = tmp_path / "lintgate_home"
        monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

        v1_style = {
            "t1_error_reading": "I would read the file first then fix the bug",
            "t2_retry_behavior": "Install the missing dependency first",
            "t3_verification_cadence": "Fix each bug and test after each",
        }
        result = json.loads(
            probe_submit(
                path=str(tmp_path),
                model_id="claude-opus-4",
                answers=v1_style,
                probe_version="v2",
            )
        )
        assert "error" not in result
        assert result["tasks_scored"] == 3

    def test_probe_submit_rejects_too_few_answers(self, monkeypatch, tmp_path):

        _, probe_submit, _ = self._load_mcp_funcs()
        lintgate_home = tmp_path / "lintgate_home"
        monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

        result = json.loads(
            probe_submit(
                path=str(tmp_path),
                model_id="claude-opus-4",
                answers={"t1_error_reading": {"text": "fix the bug"}},
            )
        )
        assert "error" in result
        assert "Minimum 3" in result["error"]


# ── Tool applicability guide ──────────────────────────────────────────


class _MockOnboardingMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def test_tool_applicability_guide_schema():
    from mcp_tools.onboarding_tools import register

    mcp = _MockOnboardingMCP()
    helpers = {"_json_dumps": None}
    tools = register(mcp, helpers)
    guide_func = tools["tool_applicability_guide"]

    result_str = guide_func()
    guide = _load_tool_result(result_str)
    core_tools = [
        "controlplane_run",
        "lint_files",
        "lint_project",
        "lint_fix",
        "scaffold_config",
        "getting_started",
    ]

    for tool in core_tools:
        assert tool in guide
        assert "cadence" in guide[tool]
        assert "triggers" in guide[tool]
        assert "anti_patterns" in guide[tool]
        assert "purpose" in guide[tool]
        assert isinstance(guide[tool]["triggers"], list)
        assert isinstance(guide[tool]["anti_patterns"], list)


def test_tool_applicability_guide_content():
    from mcp_tools.onboarding_tools import register

    mcp = _MockOnboardingMCP()
    helpers = {"_json_dumps": None}
    tools = register(mcp, helpers)
    guide_func = tools["tool_applicability_guide"]

    result_str = guide_func()
    guide = _load_tool_result(result_str)
    assert "Every 3-5 tool uses" in guide["controlplane_run"]["cadence"]
    assert "After every edit" in guide["lint_files"]["cadence"]
    assert "Onboarding only" in guide["getting_started"]["cadence"]
