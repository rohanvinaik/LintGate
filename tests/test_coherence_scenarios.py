"""Scenario-based integration tests for ControlPlane coherence states."""

from __future__ import annotations

from typing import Any

import pytest
from coherence_fixtures import (
    GOLDEN_DIR,
    _build_scenario,
    _generate_golden_for_scenario,
    _save_golden,
)

from lintgate.controlplane.coherence import compute_coherence

# -- Scenario definitions -----------------------------------------------------

SCENARIOS: dict[str, dict[str, Any]] = {
    # -- STABLE ---------------------------------------------------------------
    "stable_all_pass": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "pass"},
            {"name": "deps", "status": "pass"},
            {"name": "git", "status": "pass"},
        ],
        "expected_state": "stable",
        "expected_summary_contains": "clean",
        "expected_loud": [],
        "expected_silent_contains": ["lint", "tests", "deps", "git"],
        "expected_confidence": 1.0,
    },
    "stable_with_skips": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "skip"},
            {"name": "deps", "status": "pass"},
            {"name": "git", "status": "skip"},
        ],
        "expected_state": "stable",
        "expected_summary_contains": "clean",
        "expected_loud": [],
        "expected_silent_contains": ["lint", "deps"],
        "expected_confidence": 1.0,
    },
    "stable_no_channels": {
        "channels": [],
        "expected_state": "stable",
        "expected_summary_contains": "No channels active",
        "expected_loud": [],
        "expected_confidence": 1.0,
    },
    # -- ISOLATED (high confidence: 1 fail, >=2 pass) ------------------------
    "isolated_lint_only": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "blocking",
                "findings": [
                    {
                        "severity": "blocking",
                        "kind": "F821",
                        "message": "undefined name 'foo'",
                        "file": "/src/a.py",
                    },
                ],
            },
            {"name": "tests", "status": "pass"},
            {"name": "deps", "status": "pass"},
            {"name": "git", "status": "pass"},
        ],
        "expected_state": "isolated",
        "expected_summary_contains": "lint",
        "expected_loud": ["lint"],
        "expected_silent_contains": ["tests", "deps", "git"],
        "expected_confidence": 1.0,  # 0.7 + 0.1*3 = 1.0
    },
    "isolated_tests_only": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {
                "name": "tests",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "kind": "test_failure", "message": "test_foo failed"},
                ],
            },
            {"name": "deps", "status": "pass"},
            {"name": "git", "status": "pass"},
        ],
        "expected_state": "isolated",
        "expected_summary_contains": "tests",
        "expected_loud": ["tests"],
        "expected_silent_contains": ["lint", "deps", "git"],
        "expected_confidence": 1.0,  # 0.7 + 0.1*3 = 1.0
    },
    # -- ISOLATED (low confidence: 1 fail, <2 pass) --------------------------
    "isolated_low_confidence_one_pass": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "issue"}],
            },
            {"name": "tests", "status": "pass"},
        ],
        "expected_state": "isolated",
        "expected_summary_contains": "confidence is limited",
        "expected_loud": ["lint"],
        "expected_silent_contains": ["tests"],
        "expected_confidence": 0.6,  # 0.5 + 0.1*1 = 0.6
    },
    "isolated_low_confidence_no_pass": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "issue"}],
            },
            {"name": "tests", "status": "skip"},
        ],
        "expected_state": "isolated",
        "expected_summary_contains": "exclusion confidence is limited",
        "expected_loud": ["lint"],
        "expected_confidence": 0.3,  # no passes at all
    },
    # -- COUPLED (2 fail with shared files) -----------------------------------
    "coupled_shared_files": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "file": "/src/shared.py", "message": "lint issue"},
                ],
            },
            {
                "name": "tests",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "file": "/src/shared.py", "message": "test failure"},
                ],
            },
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "coupled",
        "expected_summary_contains": "shared.py",
        "expected_loud": ["lint", "tests"],
        "expected_silent_contains": ["deps"],
        "expected_confidence": 0.85,
    },
    # -- COUPLED (2 fail, no shared files) ------------------------------------
    "coupled_independent": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "file": "/src/a.py", "message": "lint issue"},
                ],
            },
            {
                "name": "tests",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "file": "/src/b.py", "message": "test failure"},
                ],
            },
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "coupled",
        "expected_summary_contains": "independent",
        "expected_loud": ["lint", "tests"],
        "expected_silent_contains": ["deps"],
        "expected_confidence": 0.7,  # no shared files
    },
    # -- SYSTEMIC (3+ failures) -----------------------------------------------
    "systemic_three_failures": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "issue"}],
            },
            {
                "name": "tests",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "issue"}],
            },
            {
                "name": "deps",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "issue"}],
            },
            {"name": "git", "status": "pass"},
        ],
        "expected_state": "systemic",
        "expected_summary_contains": "structural problem",
        "expected_loud": ["lint", "tests", "deps"],
        "expected_silent_contains": ["git"],
        "expected_confidence": 0.9,  # 3+ failures
    },
    # -- SYSTEMIC (cross-domain: infra + code) --------------------------------
    "systemic_cross_domain": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "file": "/src/a.py", "message": "lint issue"}],
            },
            {
                "name": "deps",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "file": "/pyproject.toml", "message": "dep issue"},
                ],
            },
            {"name": "tests", "status": "pass"},
        ],
        "expected_state": "systemic",
        "expected_summary_contains": "structural problem",
        "expected_loud": ["lint", "deps"],
        "expected_silent_contains": ["tests"],
        "expected_confidence": 0.7,  # cross-domain with only 2 failures
    },
    # -- DEGRADED (error) -----------------------------------------------------
    "degraded_channel_error": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "error", "error_message": "pytest crashed"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "degraded",
        "expected_summary_contains": "tests",
        "expected_loud": [],
        "expected_silent_contains": ["lint", "deps"],
        "expected_confidence": 0.9,
    },
    # -- DEGRADED (timeout) ---------------------------------------------------
    "degraded_channel_timeout": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "timeout", "error_message": "Exceeded budget"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "degraded",
        "expected_summary_contains": "tests",
        "expected_loud": [],
        "expected_silent_contains": ["lint", "deps"],
        "expected_confidence": 0.9,
    },
    # -- DEGRADED takes priority over failures --------------------------------
    "degraded_with_failures": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "issue"}],
            },
            {"name": "tests", "status": "error", "error_message": "crash"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "degraded",
        "expected_summary_contains": "tests",
        "expected_loud": ["lint"],
        "expected_silent_contains": ["deps"],
        "expected_confidence": 0.9,
    },
    # -- Edge: all channels skip ----------------------------------------------
    "all_skipped": {
        "channels": [
            {"name": "lint", "status": "skip"},
            {"name": "tests", "status": "skip"},
            {"name": "deps", "status": "skip"},
        ],
        "expected_state": "stable",
        "expected_summary_contains": "No channels active",
        "expected_loud": [],
        "expected_confidence": 1.0,
    },
    # -- Edge: single channel pass --------------------------------------------
    "single_channel_pass": {
        "channels": [{"name": "lint", "status": "pass"}],
        "expected_state": "stable",
        "expected_summary_contains": "clean",
        "expected_loud": [],
        "expected_silent_contains": ["lint"],
        "expected_confidence": 1.0,
    },
    # -- Edge: behavior channel is the only failure ---------------------------
    "isolated_behavior_only": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "pass"},
            {"name": "deps", "status": "pass"},
            {
                "name": "behavior",
                "status": "fail",
                "severity": "informational",
                "findings": [
                    {
                        "severity": "informational",
                        "kind": "approach_cycling",
                        "message": "3 approaches tried",
                    },
                ],
            },
        ],
        "expected_state": "isolated",
        "expected_summary_contains": "behavior",
        "expected_loud": ["behavior"],
        "expected_silent_contains": ["lint", "tests", "deps"],
        "expected_confidence": 1.0,  # 0.7 + 0.1*3 = 1.0
    },
    # -- Edge: 5 channels, 2 infra fail = systemic ----------------------------
    "systemic_infra_plus_code_five_channels": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "blocking",
                "findings": [{"severity": "blocking", "message": "issue"}],
            },
            {"name": "tests", "status": "pass"},
            {
                "name": "git",
                "status": "fail",
                "severity": "warning",
                "findings": [{"severity": "warning", "message": "dirty tree"}],
            },
            {"name": "deps", "status": "pass"},
            {"name": "behavior", "status": "pass"},
        ],
        "expected_state": "systemic",
        "expected_summary_contains": "structural problem",
        "expected_loud": ["lint", "git"],
        "expected_silent_contains": ["tests", "deps", "behavior"],
        "expected_confidence": 0.7,  # cross-domain with only 2 failures
    },
    # -- Edge: coupled with behavior channel ----------------------------------
    "coupled_lint_and_behavior": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "warning",
                "findings": [
                    {"severity": "warning", "file": "/src/x.py", "message": "lint issue"},
                ],
            },
            {
                "name": "behavior",
                "status": "fail",
                "severity": "informational",
                "findings": [
                    {
                        "severity": "informational",
                        "kind": "failure_amnesia",
                        "message": "repeated error",
                    },
                ],
            },
            {"name": "tests", "status": "pass"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "coupled",
        "expected_summary_contains": "independent",
        "expected_loud": ["lint", "behavior"],
        "expected_silent_contains": ["tests", "deps"],
        "expected_confidence": 0.7,  # no shared files
    },
}


@pytest.fixture(autouse=True, scope="session")
def ensure_golden_fixtures():
    """Generate golden fixtures if they don't exist."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in SCENARIOS.items():
        path = GOLDEN_DIR / f"{name}.json"
        if not path.exists():
            golden = _generate_golden_for_scenario(name, spec)
            _save_golden(name, golden)


class TestCoherenceScenarios:
    """Test each scenario produces the expected coherence state."""

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_coherence_state(self, scenario_name: str):
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert coherence.state == spec["expected_state"], (
            f"Scenario '{scenario_name}': expected state={spec['expected_state']}, "
            f"got state={coherence.state}. Summary: {coherence.summary}"
        )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_summary_content(self, scenario_name: str):
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        expected_substr = spec["expected_summary_contains"]
        assert expected_substr.lower() in coherence.summary.lower(), (
            f"Scenario '{scenario_name}': expected summary to contain "
            f"'{expected_substr}', got: '{coherence.summary}'"
        )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_loud_channels(self, scenario_name: str):
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert sorted(coherence.loud_channels) == sorted(spec["expected_loud"]), (
            f"Scenario '{scenario_name}': expected loud={spec['expected_loud']}, "
            f"got loud={coherence.loud_channels}"
        )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_confidence_value(self, scenario_name: str):
        """Test that confidence matches expected value when specified."""
        spec = SCENARIOS[scenario_name]
        expected_conf = spec.get("expected_confidence")
        if expected_conf is None:
            pytest.skip("No expected_confidence for this scenario")
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert coherence.confidence == expected_conf, (
            f"Scenario '{scenario_name}': expected confidence={expected_conf}, "
            f"got confidence={coherence.confidence}"
        )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_silent_channels_subset(self, scenario_name: str):
        """If expected_silent_contains is specified, verify those channels are silent."""
        spec = SCENARIOS[scenario_name]
        expected_silent = spec.get("expected_silent_contains")
        if not expected_silent:
            pytest.skip("No expected_silent_contains for this scenario")
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        for ch in expected_silent:
            assert ch in coherence.silent_channels, (
                f"Scenario '{scenario_name}': expected '{ch}' in silent channels, "
                f"got silent={coherence.silent_channels}"
            )


class TestBuildClassificationReasonFallback:
    """Test the defensive fallback in _build_classification_reason (line 883)."""

    def test_unknown_state_produces_fallback_reason(self):
        """When CoherenceResult has an unrecognized state, the fallback branch fires."""
        from lintgate.controlplane.coherence import _build_classification_reason
        from lintgate.controlplane.types import CoherenceResult

        result = CoherenceResult(
            state="unknown_state",  # type: ignore[arg-type]
            loud_channels=["lint", "tests"],
            silent_channels=["deps"],
        )
        reason = _build_classification_reason(result)
        assert reason == "State: unknown_state, 2 loud, 1 silent."
