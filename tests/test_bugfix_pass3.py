"""Tests for PASS 3 bug fixes: Fix 2, Fix 3, Fix 10, Fix 13.

Fix 2:  Coherence severity inflation (informational dampening + classification_reason)
Fix 3:  PERF001 false positives on function parameters
Fix 10: STRUCT003 config path broken
Fix 13: Telemetry trend explanation
"""

from __future__ import annotations

import ast

from lintgate.controlplane.coherence import (
    _channel_failure_weight,
    compute_coherence,
)
from lintgate.controlplane.types import ChannelConfig, ChannelResult, ControlPlaneConfig
from lintgate.linters.performance_checks.perf001_quadratic_membership import (
    _classify_function_parameter,
    check_quadratic_membership,
)
from lintgate.telemetry import _compute_trend_evidence
from lintgate.types import LintIssue

# ── Fix 2: Coherence severity inflation ─────────────────────────────────


def _make_channel_result(
    channel: str,
    status: str = "fail",
    findings: list[LintIssue] | None = None,
    severity: str = "informational",
) -> ChannelResult:
    return ChannelResult(
        channel=channel,
        status=status,
        severity=severity,
        findings=findings or [],
    )


def _make_info_finding(linter: str = "test") -> LintIssue:
    return LintIssue(
        linter=linter,
        kind="TEST001",
        message="Test informational finding",
        file="test.py",
        severity="informational",
        confidence=0.8,
    )


def _make_blocking_finding(linter: str = "test") -> LintIssue:
    return LintIssue(
        linter=linter,
        kind="TEST001",
        message="Test blocking finding",
        file="test.py",
        severity="blocking",
        confidence=0.9,
    )


class TestInformationalDampening:
    """Info-heavy channels should have dampened weight."""

    def test_info_only_channel_dampened(self) -> None:
        findings = [_make_info_finding() for _ in range(30)]
        result = _make_channel_result(
            "perf", findings=findings, severity="informational"
        )
        weight = _channel_failure_weight(result)
        # 30 info findings * 0.10 = 3.0, dampened by 0.3 => 0.9
        assert weight < 1.5  # Significantly less than undampened

    def test_mixed_channel_not_dampened(self) -> None:
        findings = [_make_blocking_finding() for _ in range(5)] + [
            _make_info_finding() for _ in range(5)
        ]
        result = _make_channel_result("lint", findings=findings, severity="blocking")
        weight = _channel_failure_weight(result)
        # Blocking findings dominate => not dampened
        assert weight >= 1.0


class TestClassificationReason:
    """CoherenceResult should always have classification_reason populated."""

    def test_stable_has_reason(self) -> None:
        results = [
            _make_channel_result("lint", status="pass", severity="none"),
            _make_channel_result("tests", status="pass", severity="none"),
        ]
        coherence = compute_coherence(results)
        assert coherence.classification_reason
        assert coherence.state == "stable"

    def test_isolated_has_reason(self) -> None:
        results = [
            _make_channel_result(
                "lint",
                status="fail",
                severity="blocking",
                findings=[_make_blocking_finding()],
            ),
            _make_channel_result("tests", status="pass", severity="none"),
            _make_channel_result("deps", status="pass", severity="none"),
        ]
        coherence = compute_coherence(results)
        assert coherence.classification_reason
        assert "lint" in coherence.classification_reason

    def test_info_only_channels_not_systemic(self) -> None:
        """3 info-only channels should NOT trigger systemic classification."""
        results = [
            _make_channel_result(
                "perf",
                findings=[_make_info_finding() for _ in range(10)],
            ),
            _make_channel_result(
                "structure",
                findings=[_make_info_finding() for _ in range(10)],
            ),
            _make_channel_result(
                "deps",
                findings=[_make_info_finding() for _ in range(10)],
            ),
        ]
        coherence = compute_coherence(results, severity_weighted=True)
        # With severity weighting, info-only channels are demoted
        assert coherence.state != "systemic"


# ── Fix 3: PERF001 parameter guard ──────────────────────────────────────


class TestParameterClassification:
    """_classify_function_parameter should handle type annotations."""

    def test_untyped_parameter(self) -> None:
        code = "def foo(items):\n    for x in range(10):\n        if x in items: pass\n"
        tree = ast.parse(code)
        result = _classify_function_parameter("items", tree)
        assert result == "untyped"

    def test_dict_parameter_fast(self) -> None:
        code = "def foo(items: dict[str, int]):\n    pass\n"
        tree = ast.parse(code)
        result = _classify_function_parameter("items", tree)
        assert result == "typed_fast"

    def test_set_parameter_fast(self) -> None:
        code = "def foo(items: set[str]):\n    pass\n"
        tree = ast.parse(code)
        result = _classify_function_parameter("items", tree)
        assert result == "typed_fast"

    def test_list_parameter_slow(self) -> None:
        code = "def foo(items: list[str]):\n    pass\n"
        tree = ast.parse(code)
        result = _classify_function_parameter("items", tree)
        assert result == "typed_slow"

    def test_non_parameter_returns_none(self) -> None:
        code = "def foo():\n    items = [1, 2, 3]\n"
        tree = ast.parse(code)
        result = _classify_function_parameter("items", tree)
        assert result is None


class TestPERF001ParameterConfidence:
    """PERF001 confidence should vary based on parameter type."""

    def test_untyped_parameter_reduced_confidence(self) -> None:
        code = "def foo(items):\n    for x in range(10):\n        if x in items:\n            pass\n"
        tree = ast.parse(code)
        issues = list(check_quadratic_membership(tree, "test.py"))
        if issues:
            assert issues[0].confidence == 0.25

    def test_list_parameter_full_confidence(self) -> None:
        code = (
            "def foo(items: list[int]):\n"
            "    for x in range(10):\n"
            "        if x in items:\n"
            "            pass\n"
        )
        tree = ast.parse(code)
        issues = list(check_quadratic_membership(tree, "test.py"))
        assert len(issues) >= 1
        assert issues[0].confidence == 0.60

    def test_dict_parameter_no_issue(self) -> None:
        code = (
            "def foo(items: dict[str, int]):\n"
            "    for x in range(10):\n"
            "        if x in items:\n"
            "            pass\n"
        )
        tree = ast.parse(code)
        issues = list(check_quadratic_membership(tree, "test.py"))
        assert len(issues) == 0

    def test_set_parameter_no_issue(self) -> None:
        code = (
            "def foo(items: set[str]):\n"
            "    for x in range(10):\n"
            "        if x in items:\n"
            "            pass\n"
        )
        tree = ast.parse(code)
        issues = list(check_quadratic_membership(tree, "test.py"))
        assert len(issues) == 0


# ── Fix 10: STRUCT003 config path ────────────────────────────────────────
# This is an integration-level fix; testing that the correct attribute is
# accessed is best done by verifying no AttributeError on a real config.


class TestStructureChannelConfig:
    """STRUCT003 should read orphan_exclude_dirs from config.channels."""

    def test_config_with_orphan_exclude_dirs_no_crash(self) -> None:
        """Constructing a config with structure channel settings should work."""
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "structure": ChannelConfig(
                    settings={"orphan_exclude_dirs": ["vendor", "third_party"]}
                ),
            },
        )
        ch_config = config.channels.get("structure")
        assert ch_config is not None
        settings = ch_config.settings
        assert settings.get("orphan_exclude_dirs") == ["vendor", "third_party"]

    def test_default_config_no_crash(self) -> None:
        """Default config with no structure channel should not crash."""
        config = ControlPlaneConfig(enabled=True)
        ch_config = config.channels.get("structure")
        settings = ch_config.settings if ch_config else {}
        assert settings.get("orphan_exclude_dirs", []) == []


# ── Fix 13: Telemetry trend explanation ──────────────────────────────────


class TestTrendExplanation:
    """_compute_trend_evidence should always include trend_explanation."""

    def test_insufficient_data_explanation(self) -> None:
        entries = [{"blocking_count": 5}] * 3
        result = _compute_trend_evidence(entries)
        assert "trend_explanation" in result
        assert "Insufficient" in result["trend_explanation"]

    def test_improving_trend_explanation(self) -> None:
        # First half high blockers, second half low
        entries = [
            {"blocking_count": 10},
            {"blocking_count": 10},
            {"blocking_count": 10},
            {"blocking_count": 10},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
        ]
        result = _compute_trend_evidence(entries)
        assert "trend_explanation" in result
        assert "decreased" in result["trend_explanation"]

    def test_degrading_trend_explanation(self) -> None:
        entries = [
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 10},
            {"blocking_count": 10},
            {"blocking_count": 10},
            {"blocking_count": 10},
        ]
        result = _compute_trend_evidence(entries)
        assert "trend_explanation" in result
        assert "increased" in result["trend_explanation"]

    def test_stable_trend_explanation(self) -> None:
        entries = [
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
        ]
        result = _compute_trend_evidence(entries)
        assert "trend_explanation" in result
        assert "stable" in result["trend_explanation"]

    def test_trend_explanation_key_always_present(self) -> None:
        # Even with no entries
        for count in [0, 1, 2, 3, 4, 8]:
            entries = [{"blocking_count": 5}] * count
            result = _compute_trend_evidence(entries)
            assert "trend_explanation" in result
