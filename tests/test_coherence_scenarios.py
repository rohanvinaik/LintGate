"""Scenario-based integration tests for ControlPlane coherence states.

Tests compute_coherence() against golden fixtures covering all 5 coherence
states (stable, isolated, coupled, systemic, degraded) plus edge cases
where classification is ambiguous.

Each scenario builds realistic ChannelResult objects with findings, then
asserts the coherence engine produces the expected state, summary shape,
and recommended action shape.  Golden fixtures live in tests/golden/coherence/
as JSON files — update them deliberately when coherence logic changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lintgate.controlplane.coherence import compute_coherence
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue

GOLDEN_DIR = Path(__file__).parent / "golden" / "coherence"

# ── Fixture builders ─────────────────────────────────────────────────


def _issue(
    linter: str = "ruff_check",
    kind: str = "E001",
    severity: str = "warning",
    message: str = "test issue",
    file: str | None = "/src/foo.py",
    line: int | None = 1,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        severity=severity,
        message=message,
        file=file,
        line=line,
    )


def _channel(
    name: str,
    status: str = "pass",
    severity: str = "none",
    findings: list[LintIssue] | None = None,
    error_message: str | None = None,
    duration_ms: float = 100.0,
) -> ChannelResult:
    return ChannelResult(
        channel=name,
        status=status,
        severity=severity,
        findings=findings or [],
        error_message=error_message,
        duration_ms=duration_ms,
    )


def _build_scenario(spec: dict[str, Any]) -> list[ChannelResult]:
    """Build ChannelResult list from a scenario spec dict."""
    results = []
    for ch_spec in spec["channels"]:
        findings = []
        for f in ch_spec.get("findings", []):
            findings.append(
                _issue(
                    linter=f.get("linter", ch_spec["name"]),
                    kind=f.get("kind", "E001"),
                    severity=f.get("severity", "warning"),
                    message=f.get("message", "issue"),
                    file=f.get("file"),
                    line=f.get("line"),
                )
            )
        results.append(
            _channel(
                name=ch_spec["name"],
                status=ch_spec.get("status", "pass"),
                severity=ch_spec.get("severity", "none"),
                findings=findings,
                error_message=ch_spec.get("error_message"),
            )
        )
    return results


def _load_golden(name: str) -> dict[str, Any]:
    path = GOLDEN_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def _save_golden(name: str, data: dict[str, Any]) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDEN_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# ── Scenario definitions ─────────────────────────────────────────────

SCENARIOS: dict[str, dict[str, Any]] = {
    # ── STABLE ────────────────────────────────────────────────────
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
        "expected_confidence": 1.0,
    },
    "stable_no_channels": {
        "channels": [],
        "expected_state": "stable",
        "expected_summary_contains": "No channels active",
        "expected_loud": [],
        "expected_confidence": 1.0,
    },
    # ── ISOLATED (high confidence: 1 fail, >=2 pass) ─────────────
    "isolated_lint_only": {
        "channels": [
            {
                "name": "lint",
                "status": "fail",
                "severity": "blocking",
                "findings": [
                    {"severity": "blocking", "kind": "F821", "message": "undefined name 'foo'", "file": "/src/a.py"},
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
        "expected_confidence": 1.0,  # 0.7 + 0.1*3 = 1.0
    },
    # ── ISOLATED (low confidence: 1 fail, <2 pass) ──────────────
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
    # ── COUPLED (2 fail with shared files) ───────────────────────
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
        "expected_confidence": 0.85,
    },
    # ── COUPLED (2 fail, no shared files) ────────────────────────
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
        "expected_confidence": 0.7,  # no shared files
    },
    # ── SYSTEMIC (3+ failures) ───────────────────────────────────
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
        "expected_confidence": 0.9,  # 3+ failures
    },
    # ── SYSTEMIC (cross-domain: infra + code) ────────────────────
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
                "findings": [{"severity": "warning", "file": "/pyproject.toml", "message": "dep issue"}],
            },
            {"name": "tests", "status": "pass"},
        ],
        "expected_state": "systemic",
        "expected_summary_contains": "structural problem",
        "expected_loud": ["lint", "deps"],
        "expected_confidence": 0.7,  # cross-domain with only 2 failures
    },
    # ── DEGRADED (error) ─────────────────────────────────────────
    "degraded_channel_error": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "error", "error_message": "pytest crashed"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "degraded",
        "expected_summary_contains": "tests",
        "expected_loud": [],
        "expected_confidence": 0.9,
    },
    # ── DEGRADED (timeout) ───────────────────────────────────────
    "degraded_channel_timeout": {
        "channels": [
            {"name": "lint", "status": "pass"},
            {"name": "tests", "status": "timeout", "error_message": "Exceeded budget"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "degraded",
        "expected_summary_contains": "tests",
        "expected_loud": [],
        "expected_confidence": 0.9,
    },
    # ── DEGRADED takes priority over failures ────────────────────
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
        "expected_confidence": 0.9,
    },
    # ── Edge: all channels skip ──────────────────────────────────
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
    # ── Edge: single channel pass ────────────────────────────────
    "single_channel_pass": {
        "channels": [
            {"name": "lint", "status": "pass"},
        ],
        "expected_state": "stable",
        "expected_summary_contains": "clean",
        "expected_loud": [],
        "expected_confidence": 1.0,
    },
    # ── Edge: behavior channel is the only failure ───────────────
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
                    {"severity": "informational", "kind": "approach_cycling", "message": "3 approaches tried"},
                ],
            },
        ],
        "expected_state": "isolated",
        "expected_summary_contains": "behavior",
        "expected_loud": ["behavior"],
        "expected_confidence": 1.0,  # 0.7 + 0.1*3 = 1.0
    },
    # ── Edge: 5 channels, 2 infra fail = systemic ────────────────
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
        "expected_confidence": 0.7,  # cross-domain with only 2 failures
    },
    # ── Edge: coupled with behavior channel ──────────────────────
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
                    {"severity": "informational", "kind": "failure_amnesia", "message": "repeated error"},
                ],
            },
            {"name": "tests", "status": "pass"},
            {"name": "deps", "status": "pass"},
        ],
        "expected_state": "coupled",
        "expected_summary_contains": "independent",
        "expected_loud": ["lint", "behavior"],
        "expected_confidence": 0.7,  # no shared files
    },
}


# ── Golden fixture generation (run with --update-golden) ─────────────


def _generate_golden_for_scenario(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Run coherence engine and capture output as golden fixture."""
    results = _build_scenario(spec)
    coherence = compute_coherence(results)
    return {
        "scenario": name,
        "input": {
            "channels": [
                {
                    "name": cr.channel,
                    "status": cr.status,
                    "finding_count": len(cr.findings),
                }
                for cr in results
            ]
        },
        "expected": {
            "state": coherence.state,
            "summary": coherence.summary,
            "recommended_action": coherence.recommended_action,
            "silent_channels": coherence.silent_channels,
            "loud_channels": coherence.loud_channels,
            "confidence": coherence.confidence,
            "classification_notes": coherence.classification_notes,
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


# ── Parametrized test: scenario → coherence state ────────────────────


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


class TestCoherenceGoldenRegression:
    """Verify current output matches golden fixtures (regression guard)."""

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_matches_golden(self, scenario_name: str):
        golden_path = GOLDEN_DIR / f"{scenario_name}.json"
        if not golden_path.exists():
            pytest.skip(f"Golden fixture not yet generated for {scenario_name}")

        golden = _load_golden(scenario_name)
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)

        expected = golden["expected"]
        assert coherence.state == expected["state"], (
            f"Golden regression for '{scenario_name}': "
            f"state changed from {expected['state']} to {coherence.state}"
        )
        assert sorted(coherence.loud_channels) == sorted(expected["loud_channels"]), (
            f"Golden regression for '{scenario_name}': "
            f"loud_channels changed from {expected['loud_channels']} "
            f"to {coherence.loud_channels}"
        )
        assert sorted(coherence.silent_channels) == sorted(expected["silent_channels"]), (
            f"Golden regression for '{scenario_name}': "
            f"silent_channels changed"
        )


class TestCoherenceContractInvariants:
    """Invariants that must hold across ALL scenarios."""

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_state_is_valid(self, scenario_name: str):
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert coherence.state in {"stable", "isolated", "coupled", "systemic", "degraded"}

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_loud_channels_are_failing(self, scenario_name: str):
        """Every loud channel must have status=fail in the input."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)

        failed_names = {r.channel for r in results if r.status == "fail"}
        for ch in coherence.loud_channels:
            assert ch in failed_names, (
                f"Loud channel '{ch}' is not in failed results"
            )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_silent_channels_are_passing(self, scenario_name: str):
        """Every silent channel must have status=pass in the input."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)

        passed_names = {r.channel for r in results if r.status == "pass"}
        for ch in coherence.silent_channels:
            assert ch in passed_names, (
                f"Silent channel '{ch}' is not in passed results"
            )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_summary_is_nonempty(self, scenario_name: str):
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert coherence.summary, "Summary must not be empty"

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_recommended_action_is_nonempty(self, scenario_name: str):
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert coherence.recommended_action, "Recommended action must not be empty"

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_degraded_requires_error_or_timeout(self, scenario_name: str):
        """degraded state should only occur when there's an error/timeout."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)

        if coherence.state == "degraded":
            has_error = any(r.status in ("error", "timeout") for r in results)
            assert has_error, "degraded state without any error/timeout channels"

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_confidence_in_valid_range(self, scenario_name: str):
        """Confidence must be between 0.0 and 1.0."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert 0.0 <= coherence.confidence <= 1.0, (
            f"Confidence {coherence.confidence} out of range [0.0, 1.0]"
        )

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_classification_notes_is_list(self, scenario_name: str):
        """classification_notes must always be a list (possibly empty)."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        assert isinstance(coherence.classification_notes, list)

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_stable_means_no_failures(self, scenario_name: str):
        """stable state should only occur when no channels failed."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)

        if coherence.state == "stable":
            enabled = [r for r in results if r.status != "skip"]
            failures = [r for r in enabled if r.status == "fail"]
            assert len(failures) == 0, (
                f"stable state but {len(failures)} channels failed: "
                f"{[r.channel for r in failures]}"
            )
