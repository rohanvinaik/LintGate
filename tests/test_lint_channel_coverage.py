"""Tests for lint_channel.py — targeting uncovered symbols and edge cases.

Covers:
- LintChannel class attributes (name, timeout_ms, blocking_capable)
- LintChannel.should_run with various event/classification combos
- LintChannel.execute with mocked pipeline (classification=None, tier skip, full run)
- LintChannel.execute_pure with mocked pipeline (classification=None, tier skip, full run)
- LintChannel._to_channel_result with blocking, warning, informational, and empty findings
- LintChannel._build_project_config success and fallback paths
- _empty_aggregated() returns clean AggregatedResult
- _estimate_scope_chars with real files, missing files, large files, empty list, cap
- _compute_dynamic_timeout_ms with various configs and file counts
- _apply_mcp_strictness_override with mcp/hook surfaces, valid/invalid strictness, same value
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lintgate.channels.lint_channel import (
    LintChannel,
    _apply_mcp_strictness_override,
    _compute_dynamic_timeout_ms,
    _empty_aggregated,
    _estimate_scope_chars,
)
from lintgate.controlplane.types import (
    ChannelConfig,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import (
    AggregatedResult,
    ChangeClassification,
    LintIssue,
    LintTier,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_event(
    surface: str = "hook",
    risk_level: str = "moderate",
    project_root: str = "/tmp",
    raw_input: dict[str, Any] | None = None,
    classification: ChangeClassification | None = "auto",
) -> SupervisionEvent:
    """Build a SupervisionEvent with optional classification."""
    if classification == "auto":
        classification = ChangeClassification(risk_level=risk_level)
    return SupervisionEvent(
        surface=surface,
        project_root=project_root,
        tool_name="Edit",
        raw_input=raw_input or {},
        change_classification=classification,
    )


def _make_tier(
    strictness: str = "normal",
    skip: bool = False,
    files: list[str] | None = None,
) -> LintTier:
    return LintTier(
        name="tier_2_logic",
        linters=["ruff_check"],
        files=files or ["/tmp/example.py"],
        reason="test",
        strictness=strictness,
        skip=skip,
    )


def _make_config(**kwargs: Any) -> ControlPlaneConfig:
    return ControlPlaneConfig(enabled=True, **kwargs)


def _make_issue(severity: str = "warning", linter: str = "ruff") -> LintIssue:
    return LintIssue(
        linter=linter,
        kind="E501",
        message="line too long",
        file="/tmp/example.py",
        line=10,
        severity=severity,
    )


def _make_aggregated(
    blocking: list[LintIssue] | None = None,
    warnings: list[LintIssue] | None = None,
    informational: list[LintIssue] | None = None,
    metrics: dict[str, Any] | None = None,
) -> AggregatedResult:
    return AggregatedResult(
        blocking=blocking or [],
        warnings=warnings or [],
        informational=informational or [],
        metrics=metrics or {},
        linter_statuses={"ruff_check": "ok"},
        tier_used="tier_2_logic",
        tier_reason="test",
        files_linted=["/tmp/example.py"],
    )


# ── LintChannel class attributes ────────────────────────────────────────


class TestLintChannelAttributes:
    """Verify class-level attributes on LintChannel."""

    def test_name_is_lint(self) -> None:
        ch = LintChannel()
        assert ch.name == "lint"

    def test_timeout_ms_default(self) -> None:
        ch = LintChannel()
        assert ch.timeout_ms == 8000

    def test_blocking_capable(self) -> None:
        ch = LintChannel()
        assert ch.blocking_capable is True


# ── should_run ───────────────────────────────────────────────────────────


class TestShouldRun:
    """Verify LintChannel.should_run across event profiles."""

    def test_mcp_surface_always_runs(self) -> None:
        event = _make_event(surface="mcp", classification=None)
        assert LintChannel().should_run(event, _make_config()) is True

    def test_no_classification_returns_false(self) -> None:
        event = _make_event(classification=None)
        assert LintChannel().should_run(event, _make_config()) is False

    def test_risk_none_returns_false(self) -> None:
        event = _make_event(risk_level="none")
        assert LintChannel().should_run(event, _make_config()) is False

    def test_risk_moderate_returns_true(self) -> None:
        event = _make_event(risk_level="moderate")
        assert LintChannel().should_run(event, _make_config()) is True

    def test_risk_cosmetic_returns_true(self) -> None:
        event = _make_event(risk_level="cosmetic")
        assert LintChannel().should_run(event, _make_config()) is True

    def test_risk_structural_returns_true(self) -> None:
        event = _make_event(risk_level="structural")
        assert LintChannel().should_run(event, _make_config()) is True

    def test_risk_architectural_returns_true(self) -> None:
        event = _make_event(risk_level="architectural")
        assert LintChannel().should_run(event, _make_config()) is True


# ── _to_channel_result ───────────────────────────────────────────────────


class TestToChannelResult:
    """Verify the AggregatedResult -> ChannelResult conversion."""

    def test_blocking_issues_produce_fail_blocking(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated(blocking=[_make_issue("blocking")])
        result = ch._to_channel_result(agg, {}, {}, 100.0, "normal", 8000)
        assert result.status == "fail"
        assert result.severity == "blocking"
        assert len(result.findings) == 1

    def test_warning_issues_produce_fail_warning(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated(warnings=[_make_issue("warning")])
        result = ch._to_channel_result(agg, {}, {}, 100.0, "normal", 8000)
        assert result.status == "fail"
        assert result.severity == "warning"

    def test_informational_only_produce_pass_none(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated(informational=[_make_issue("informational")])
        result = ch._to_channel_result(agg, {}, {}, 50.0, "strict", 8000)
        assert result.status == "pass"
        assert result.severity == "none"
        assert len(result.findings) == 1

    def test_no_issues_produce_pass_none(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated()
        result = ch._to_channel_result(agg, {}, {}, 10.0, "relaxed", 8000)
        assert result.status == "pass"
        assert result.severity == "none"
        assert result.findings == []

    def test_blocking_and_warnings_use_blocking_severity(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated(
            blocking=[_make_issue("blocking")],
            warnings=[_make_issue("warning")],
        )
        result = ch._to_channel_result(agg, {}, {}, 200.0, "normal", 8000)
        assert result.severity == "blocking"
        assert len(result.findings) == 2

    def test_metrics_carry_forward(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated(metrics={"custom_key": 42})
        recurrence = {"repeated_issue_count": 3, "unique_signatures_tracked": 5}
        pattern_report = {"alerted_patterns": ["pattern_a"], "top_categories": []}
        result = ch._to_channel_result(
            agg, recurrence, pattern_report, 150.0, "strict", 10000
        )
        assert result.metrics["custom_key"] == 42
        assert result.metrics["recurrence"] == recurrence
        assert result.metrics["pattern_alerts"] == ["pattern_a"]
        assert result.metrics["tier_used"] == "tier_2_logic"
        assert result.metrics["tier_reason"] == "test"
        assert result.metrics["tier_strictness"] == "strict"
        assert result.metrics["lint_timeout_ms"] == 10000
        assert result.metrics["files_linted"] == ["/tmp/example.py"]

    def test_duration_ms_passed_through(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated()
        result = ch._to_channel_result(agg, {}, {}, 42.5, "normal", 8000)
        assert result.duration_ms == 42.5

    def test_channel_name_is_lint(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated()
        result = ch._to_channel_result(agg, {}, {}, 0.0, "normal", 8000)
        assert result.channel == "lint"

    def test_repairs_always_empty(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated(blocking=[_make_issue("blocking")])
        result = ch._to_channel_result(agg, {}, {}, 0.0, "normal", 8000)
        assert result.repairs == []

    def test_pattern_report_missing_key_defaults_empty(self) -> None:
        ch = LintChannel()
        agg = _make_aggregated()
        # pattern_report dict without 'alerted_patterns' key
        result = ch._to_channel_result(agg, {}, {"other_key": 1}, 0.0, "normal", 8000)
        assert result.metrics["pattern_alerts"] == []


# ── _empty_aggregated ────────────────────────────────────────────────────


class TestEmptyAggregated:
    """Verify _empty_aggregated returns a clean AggregatedResult."""

    def test_returns_aggregated_result(self) -> None:
        agg = _empty_aggregated()
        assert isinstance(agg, AggregatedResult)

    def test_all_lists_empty(self) -> None:
        agg = _empty_aggregated()
        assert agg.blocking == []
        assert agg.warnings == []
        assert agg.informational == []
        assert agg.files_linted == []

    def test_metrics_empty(self) -> None:
        agg = _empty_aggregated()
        assert agg.metrics == {}

    def test_tier_fields_empty(self) -> None:
        agg = _empty_aggregated()
        assert agg.tier_used == ""
        assert agg.tier_reason == ""


# ── _estimate_scope_chars ────────────────────────────────────────────────


class TestEstimateScopeChars:
    """Verify file-size estimation for timeout scaling."""

    def test_empty_file_list(self) -> None:
        assert _estimate_scope_chars([]) == 0

    def test_real_files(self, tmp_path: Any) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("x" * 100)
        f2 = tmp_path / "b.py"
        f2.write_text("y" * 200)
        result = _estimate_scope_chars([str(f1), str(f2)])
        assert result == 300

    def test_missing_files_skipped(self, tmp_path: Any) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("x" * 50)
        result = _estimate_scope_chars([str(f1), "/nonexistent/file.py"])
        assert result == 50

    def test_max_files_limit(self, tmp_path: Any) -> None:
        files = []
        for i in range(5):
            f = tmp_path / f"f{i}.py"
            f.write_text("a" * 10)
            files.append(str(f))
        # Only first 3 files should be counted when max_files=3
        result = _estimate_scope_chars(files, max_files=3)
        assert result == 30

    def test_cap_at_max_chars(self, tmp_path: Any) -> None:
        f1 = tmp_path / "big.py"
        f1.write_text("x" * 500)
        # Cap at 100 chars
        result = _estimate_scope_chars([str(f1)], max_chars=100)
        assert result == 100

    def test_cap_exactly_at_boundary(self, tmp_path: Any) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("x" * 50)
        f2 = tmp_path / "b.py"
        f2.write_text("y" * 50)
        # When total reaches max_chars, it returns max_chars immediately
        result = _estimate_scope_chars([str(f1), str(f2)], max_chars=50)
        assert result == 50

    def test_all_files_missing_returns_zero(self) -> None:
        result = _estimate_scope_chars(["/no/such/a.py", "/no/such/b.py"])
        assert result == 0


# ── _compute_dynamic_timeout_ms ──────────────────────────────────────────


class TestComputeDynamicTimeoutMs:
    """Verify dynamic timeout calculation."""

    def test_empty_files_returns_base(self) -> None:
        cp = _make_config()
        timeout = _compute_dynamic_timeout_ms(cp, "lint", [])
        # base = max(2000, 8000) = 8000, dynamic_add = 0, dynamic = 8000
        # cap = max(8000, int(15000*0.9)) = 13500
        # result = max(2000, min(8000, 13500)) = 8000
        assert timeout == 8000

    def test_custom_channel_timeout(self) -> None:
        cp = _make_config(channels={"lint": ChannelConfig(timeout_ms=5000)})
        timeout = _compute_dynamic_timeout_ms(cp, "lint", [])
        # base = max(2000, 5000) = 5000
        assert timeout == 5000

    def test_scales_with_files(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "lintgate.channels.lint_channel.os.path.getsize",
            lambda _: 10000,
        )
        cp = _make_config()
        files = [f"/tmp/f{i}.py" for i in range(10)]
        timeout = _compute_dynamic_timeout_ms(cp, "lint", files)
        # base = 8000
        # scope_chars = 10 * 10000 = 100000
        # dynamic_add = int(100000/1000 * 2.5) + (15 * 10) = 250 + 150 = 400
        # dynamic = 8000 + 400 = 8400
        # cap = max(8000, int(15000*0.9)) = 13500
        # result = max(2000, min(8400, 13500)) = 8400
        assert timeout == 8400

    def test_capped_to_latency_budget(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "lintgate.channels.lint_channel.os.path.getsize",
            lambda _: 1_000_000,
        )
        cp = _make_config(latency_budget_ms=10000)
        files = [f"/tmp/f{i}.py" for i in range(200)]
        timeout = _compute_dynamic_timeout_ms(cp, "lint", files)
        # cap = max(8000, int(10000*0.9)) = 9000
        assert timeout == int(10000 * 0.9)

    def test_floor_at_2000(self) -> None:
        cp = _make_config(
            channels={"lint": ChannelConfig(timeout_ms=100)},
            latency_budget_ms=200,
        )
        timeout = _compute_dynamic_timeout_ms(cp, "lint", [])
        # base = max(2000, 100) = 2000, dynamic = 2000
        # cap = max(2000, int(200*0.9)) = 2000
        # result = max(2000, min(2000, 2000)) = 2000
        assert timeout == 2000

    def test_unknown_channel_uses_default_timeout(self) -> None:
        cp = _make_config()
        timeout = _compute_dynamic_timeout_ms(cp, "nonexistent_channel", [])
        # channel_timeout returns 8000 for unknown channels
        assert timeout == 8000

    def test_files_capped_at_200(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "lintgate.channels.lint_channel.os.path.getsize",
            lambda _: 100,
        )
        cp = _make_config()
        files = [f"/tmp/f{i}.py" for i in range(300)]
        timeout = _compute_dynamic_timeout_ms(cp, "lint", files)
        # scope_chars from _estimate_scope_chars: first 200 files * 100 = 20000
        # dynamic_add = int(20000/1000 * 2.5) + (15 * min(300, 200))
        #             = 50 + 3000 = 3050
        # dynamic = 8000 + 3050 = 11050
        # cap = max(8000, int(15000*0.9)) = 13500
        # result = max(2000, min(11050, 13500)) = 11050
        assert timeout == 11050


# ── _apply_mcp_strictness_override ───────────────────────────────────────


class TestApplyMcpStrictnessOverride:
    """Verify MCP strictness override logic."""

    def test_non_mcp_returns_original(self) -> None:
        event = _make_event(surface="hook", raw_input={"strictness": "strict"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result is tier

    def test_ci_surface_returns_original(self) -> None:
        event = _make_event(surface="ci", raw_input={"strictness": "strict"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result is tier

    def test_mcp_with_valid_strict(self) -> None:
        event = _make_event(surface="mcp", raw_input={"strictness": "strict"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result.strictness == "strict"
        assert result is not tier

    def test_mcp_with_valid_relaxed(self) -> None:
        event = _make_event(surface="mcp", raw_input={"strictness": "relaxed"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result.strictness == "relaxed"

    def test_mcp_same_strictness_returns_original(self) -> None:
        event = _make_event(surface="mcp", raw_input={"strictness": "normal"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result is tier

    def test_mcp_invalid_strictness_returns_original(self) -> None:
        event = _make_event(surface="mcp", raw_input={"strictness": "ultra"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result is tier

    def test_mcp_no_strictness_key_returns_original(self) -> None:
        event = _make_event(surface="mcp", raw_input={})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result is tier

    def test_overridden_tier_preserves_other_fields(self) -> None:
        event = _make_event(surface="mcp", raw_input={"strictness": "strict"})
        tier = _make_tier("normal")
        result = _apply_mcp_strictness_override(event, tier)
        assert result.name == tier.name
        assert result.linters == tier.linters
        assert result.files == tier.files
        assert result.reason == tier.reason
        assert result.skip == tier.skip


# ── _build_project_config ────────────────────────────────────────────────


class TestBuildProjectConfig:
    """Verify project config construction from event data."""

    def test_fallback_when_load_config_fails(self) -> None:
        ch = LintChannel()
        event = _make_event(project_root="/nonexistent/path")
        with patch(
            "lintgate.channels.lint_channel.load_config",
            side_effect=Exception("no config"),
            create=True,
        ):
            config = ch._build_project_config(event)
        assert config.project_root == "/nonexistent/path"

    def test_successful_load_config(self) -> None:
        ch = LintChannel()
        event = _make_event(project_root="/some/project")
        mock_config = MagicMock()
        mock_config.project_root = "/some/project"
        with patch(
            "lintgate.config.load_config",
            return_value=mock_config,
        ):
            config = ch._build_project_config(event)
        assert config.project_root == "/some/project"


# ── execute ──────────────────────────────────────────────────────────────


class TestExecute:
    """Verify LintChannel.execute with mocked pipeline stages."""

    def test_no_classification_returns_skip(self) -> None:
        ch = LintChannel()
        event = _make_event(classification=None)
        result = ch.execute(event, _make_config())
        assert result.status == "skip"
        assert result.severity == "none"
        assert result.metrics["reason"] == "no_classification"

    @patch("lintgate.channels.lint_channel._compute_dynamic_timeout_ms", return_value=8000)
    @patch("lintgate.channels.lint_channel._apply_mcp_strictness_override")
    def test_tier_skip_returns_skip(self, mock_override: Any, mock_timeout: Any) -> None:
        ch = LintChannel()
        event = _make_event(risk_level="moderate")
        skip_tier = _make_tier(skip=True)

        with (
            patch("lintgate.tier_selector.select_tier", return_value=skip_tier),
            patch.object(ch, "_build_project_config", return_value=MagicMock()),
        ):
            result = ch.execute(event, _make_config())

        assert result.status == "skip"
        assert result.metrics["reason"] == "tier_skip"

    @patch("lintgate.channels.lint_channel._compute_dynamic_timeout_ms", return_value=8000)
    @patch("lintgate.channels.lint_channel._apply_mcp_strictness_override")
    def test_full_pipeline_with_blocking_issues(
        self, mock_override: Any, mock_timeout: Any
    ) -> None:
        ch = LintChannel()
        event = _make_event(risk_level="moderate")
        tier = _make_tier()
        mock_override.return_value = tier

        agg = _make_aggregated(blocking=[_make_issue("blocking")])

        with (
            patch("lintgate.tier_selector.select_tier", return_value=tier),
            patch("lintgate.registry.build_registry", return_value=MagicMock()),
            patch("lintgate.lint_runner.run_linters", return_value=[]),
            patch("lintgate.results_aggregator.aggregate_results", return_value=agg),
            patch.object(ch, "_build_project_config", return_value=MagicMock()),
            patch("lintgate.state.update_issue_memory", return_value={}),
            patch("lintgate.pattern_bank.update_pattern_bank", return_value={}),
        ):
            result = ch.execute(event, _make_config())

        assert result.status == "fail"
        assert result.severity == "blocking"

    @patch("lintgate.channels.lint_channel._compute_dynamic_timeout_ms", return_value=8000)
    @patch("lintgate.channels.lint_channel._apply_mcp_strictness_override")
    def test_side_effects_suppressed_on_exception(
        self, mock_override: Any, mock_timeout: Any
    ) -> None:
        """Verify that exceptions in update_issue_memory/update_pattern_bank are suppressed."""
        ch = LintChannel()
        event = _make_event(risk_level="moderate")
        tier = _make_tier()
        mock_override.return_value = tier

        agg = _make_aggregated()

        with (
            patch("lintgate.tier_selector.select_tier", return_value=tier),
            patch("lintgate.registry.build_registry", return_value=MagicMock()),
            patch("lintgate.lint_runner.run_linters", return_value=[]),
            patch("lintgate.results_aggregator.aggregate_results", return_value=agg),
            patch.object(ch, "_build_project_config", return_value=MagicMock()),
            patch(
                "lintgate.state.update_issue_memory",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "lintgate.pattern_bank.update_pattern_bank",
                side_effect=RuntimeError("kaboom"),
            ),
        ):
            result = ch.execute(event, _make_config())

        # Should succeed despite side-effect failures
        assert result.status == "pass"
        assert result.metrics["recurrence"]["repeated_issue_count"] == 0


# ── execute_pure ─────────────────────────────────────────────────────────


class TestExecutePure:
    """Verify LintChannel.execute_pure returns both results and has no side effects."""

    def test_no_classification_returns_skip_and_empty_agg(self) -> None:
        ch = LintChannel()
        event = _make_event(classification=None)
        channel_result, agg = ch.execute_pure(event, _make_config())
        assert channel_result.status == "skip"
        assert channel_result.metrics["reason"] == "no_classification"
        assert isinstance(agg, AggregatedResult)
        assert agg.blocking == []

    @patch("lintgate.channels.lint_channel._apply_mcp_strictness_override")
    def test_tier_skip_returns_skip_and_empty_agg(self, mock_override: Any) -> None:
        ch = LintChannel()
        event = _make_event(risk_level="moderate")
        skip_tier = _make_tier(skip=True)

        with (
            patch("lintgate.tier_selector.select_tier", return_value=skip_tier),
            patch.object(ch, "_build_project_config", return_value=MagicMock()),
        ):
            channel_result, agg = ch.execute_pure(event, _make_config())

        assert channel_result.status == "skip"
        assert agg.blocking == []

    @patch("lintgate.channels.lint_channel._apply_mcp_strictness_override")
    def test_full_pipeline_returns_both_results(self, mock_override: Any) -> None:
        ch = LintChannel()
        event = _make_event(risk_level="moderate")
        tier = _make_tier()
        mock_override.return_value = tier

        agg = _make_aggregated(warnings=[_make_issue("warning")])

        with (
            patch("lintgate.tier_selector.select_tier", return_value=tier),
            patch("lintgate.registry.build_registry", return_value=MagicMock()),
            patch("lintgate.lint_runner.run_linters", return_value=[]),
            patch("lintgate.results_aggregator.aggregate_results", return_value=agg),
            patch.object(ch, "_build_project_config", return_value=MagicMock()),
        ):
            channel_result, returned_agg = ch.execute_pure(event, _make_config())

        assert channel_result.status == "fail"
        assert channel_result.severity == "warning"
        assert returned_agg is agg

    @patch("lintgate.channels.lint_channel._apply_mcp_strictness_override")
    def test_pure_mode_has_zero_recurrence(self, mock_override: Any) -> None:
        """execute_pure should NOT call update_issue_memory or update_pattern_bank."""
        ch = LintChannel()
        event = _make_event(risk_level="moderate")
        tier = _make_tier()
        mock_override.return_value = tier

        agg = _make_aggregated(blocking=[_make_issue("blocking")])

        with (
            patch("lintgate.tier_selector.select_tier", return_value=tier),
            patch("lintgate.registry.build_registry", return_value=MagicMock()),
            patch("lintgate.lint_runner.run_linters", return_value=[]),
            patch("lintgate.results_aggregator.aggregate_results", return_value=agg),
            patch.object(ch, "_build_project_config", return_value=MagicMock()),
        ):
            channel_result, _ = ch.execute_pure(event, _make_config())

        assert channel_result.metrics["recurrence"]["repeated_issue_count"] == 0
        assert channel_result.metrics["pattern_alerts"] == []
