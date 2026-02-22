"""Contract-invariant and channel-weighting tests for coherence engine.

TestCoherenceContractInvariants verifies invariants that must hold across
ALL scenarios (valid states, loud/silent consistency, confidence range, etc.).

TestCoherenceChannelWeighting verifies the channel_weights feature flag
behavior: None weights are identical to unweighted, low weights demote,
high weights preserve classification.
"""

from __future__ import annotations

import pytest
from coherence_fixtures import (
    GOLDEN_DIR,
    _build_scenario,
    _channel,
    _issue,
    _load_golden,
)
from test_coherence_scenarios import SCENARIOS

from lintgate.controlplane.coherence import compute_coherence


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
            f"Golden regression for '{scenario_name}': silent_channels changed"
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
            assert ch in failed_names, f"Loud channel '{ch}' is not in failed results"

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_silent_channels_are_passing(self, scenario_name: str):
        """Every silent channel must have status=pass in the input."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        coherence = compute_coherence(results)
        passed_names = {r.channel for r in results if r.status == "pass"}
        for ch in coherence.silent_channels:
            assert ch in passed_names, f"Silent channel '{ch}' is not in passed results"

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
                f"stable state but {len(failures)} channels failed: {[r.channel for r in failures]}"
            )


# -- Channel weighting tests --------------------------------------------------


class TestCoherenceChannelWeighting:
    """Verify channel_weights feature flag behavior.

    Key invariants:
    - weights=None produces identical output to unweighted (feature off)
    - Low-weight channels reduce effective failure count
    - High-weight channels preserve classification
    """

    @pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
    def test_none_weights_identical_to_unweighted(self, scenario_name: str):
        """channel_weights=None must produce identical state to no weights."""
        spec = SCENARIOS[scenario_name]
        results = _build_scenario(spec)
        base = compute_coherence(results)
        weighted = compute_coherence(results, channel_weights=None)
        assert base.state == weighted.state, (
            f"Scenario '{scenario_name}': weights=None changed state "
            f"from {base.state} to {weighted.state}"
        )
        assert base.confidence == weighted.confidence
        assert sorted(base.loud_channels) == sorted(weighted.loud_channels)
        assert sorted(base.silent_channels) == sorted(weighted.silent_channels)

    def test_low_weight_demotes_systemic_to_coupled(self):
        """3 low-weight informational failures should demote below systemic.

        Channel weights only apply when severity_weighted=True (the production
        default). With severity weighting, informational-only channels already
        score low; adding low channel weights reduces them further.
        """
        results = [
            _channel(
                "structure",
                "fail",
                "informational",
                [_issue(severity="informational", kind="STRUCT001")],
            ),
            _channel(
                "behavior",
                "fail",
                "informational",
                [_issue(severity="informational", kind="approach_cycling")],
            ),
            _channel(
                "git",
                "fail",
                "informational",
                [_issue(severity="informational", kind="dirty_tree")],
            ),
            _channel("lint", "pass"),
            _channel("tests", "pass"),
        ]
        # With severity_weighted=True but no channel weights:
        # Each informational channel scores ~0.10, total ~0.30
        # Already below systemic (3.0) -- severity weighting alone handles this
        sw_no_weights = compute_coherence(results, severity_weighted=True)
        # With severity_weighted=True AND low channel weights:
        # Each: 0.10 * 0.3 = 0.03, total ~0.09
        sw_with_weights = compute_coherence(
            results,
            severity_weighted=True,
            channel_weights={"structure": 0.3, "behavior": 0.3, "git": 0.3},
        )
        # Both should be demoted from systemic
        assert sw_no_weights.state != "systemic", (
            f"severity_weighted alone should demote informational-only, got {sw_no_weights.state}"
        )
        assert sw_with_weights.state != "systemic", (
            f"severity_weighted + low weights should demote, got {sw_with_weights.state}"
        )

    def test_high_weight_preserves_coupled(self):
        """2 high-weight failures should stay coupled with severity_weighted."""
        results = [
            _channel(
                "lint",
                "fail",
                "blocking",
                [_issue(severity="blocking", kind="F821")],
            ),
            _channel(
                "tests",
                "fail",
                "warning",
                [_issue(severity="warning", kind="test_failure")],
            ),
            _channel("deps", "pass"),
        ]
        weighted = compute_coherence(
            results,
            severity_weighted=True,
            channel_weights={"lint": 1.0, "tests": 1.0},
        )
        assert weighted.state == "coupled", (
            f"High-weight failures should remain coupled, got {weighted.state}"
        )

    def test_mixed_weights_structure_low_lint_high(self):
        """structure(low) + lint(high) failure: lint dominates classification."""
        results = [
            _channel(
                "lint",
                "fail",
                "blocking",
                [_issue(severity="blocking", kind="F821")],
            ),
            _channel(
                "structure",
                "fail",
                "informational",
                [_issue(severity="informational", kind="STRUCT001")],
            ),
            _channel("tests", "pass"),
            _channel("deps", "pass"),
        ]
        weighted = compute_coherence(
            results,
            severity_weighted=True,
            channel_weights={"lint": 1.0, "structure": 0.2},
        )
        # lint(1.0 * 1.0) + structure(0.10 * 0.2 = 0.02) = 1.02
        # Below coupled threshold (1.5) -> should be isolated
        assert weighted.state == "isolated", (
            f"Low-weight structure + high-weight lint should be isolated, got {weighted.state}"
        )

    def test_default_weight_for_unconfigured_channels(self):
        """Channels not in weights dict get default weight of 0.5.

        Channel weights affect the systemic-vs-coupled boundary (effective
        failure count), not the coupled-vs-isolated boundary (raw count >= 2).
        Two actual failures will always be at least coupled.
        """
        results = [
            _channel(
                "lint",
                "fail",
                "blocking",
                [_issue(severity="blocking", kind="F821")],
            ),
            _channel(
                "tests",
                "fail",
                "warning",
                [_issue(severity="warning", kind="test_failure")],
            ),
            _channel("deps", "pass"),
        ]
        # Only configure lint=1.0, tests gets default 0.5
        weighted = compute_coherence(
            results,
            severity_weighted=True,
            channel_weights={"lint": 1.0},
        )
        # lint: 1.0 * 1.0 = 1.0, tests: 0.35 * 0.5 = 0.175 -> total ~1.175
        # Below systemic threshold (3.0) -> coupled (2 raw failures)
        assert weighted.state == "coupled", (
            f"Two failures with weights should be coupled (not systemic), got {weighted.state}"
        )

    def test_weights_prevent_systemic_escalation(self):
        """3 failures with low weights should stay coupled, not escalate."""
        results = [
            _channel(
                "lint",
                "fail",
                "warning",
                [_issue(severity="warning", kind="W001")],
            ),
            _channel(
                "tests",
                "fail",
                "warning",
                [_issue(severity="warning", kind="test_failure")],
            ),
            _channel(
                "behavior",
                "fail",
                "informational",
                [_issue(severity="informational", kind="approach_cycling")],
            ),
            _channel("deps", "pass"),
        ]
        # Without severity weighting: 3 raw failures -> systemic
        unweighted = compute_coherence(results)
        assert unweighted.state == "systemic"
        # With severity weighting + low weights: effective count drops below 3.0
        weighted = compute_coherence(
            results,
            severity_weighted=True,
            channel_weights={"lint": 0.4, "tests": 0.4, "behavior": 0.2},
        )
        # lint: 0.35*0.4=0.14, tests: 0.35*0.4=0.14, behavior: 0.10*0.2=0.02
        # total ~0.30, well below 3.0 -> not systemic, falls to coupled
        assert weighted.state != "systemic", (
            f"Low weights should prevent systemic escalation, got {weighted.state}"
        )
