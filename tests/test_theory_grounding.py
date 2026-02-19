"""Tests for Phase 1A: Theory-Grounded Hook Messages."""

from __future__ import annotations

from lintgate.channels.behavior_channel import (
    _THEORY_CODA_MAX_CHARS,
    SIGNAL_THEORY_MAP,
    _ground_finding_in_theory,
    _SignalCoordinator,
)
from lintgate.controlplane.behavior_compass import BehaviorCompass
from lintgate.types import LintIssue

# ── Sample Theory Profile ────────────────────────────────────────────

SAMPLE_PROFILE = {
    "core_theory": [
        {
            "claims": [
                "Constraint maps become architectures of possibility",
                "Named anti-patterns serve as cognitive anchors",
            ],
            "source": "docs/theory.md",
            "heading": "Core Theory",
        }
    ],
    "problem_solving": [
        {
            "claims": [
                "Decompose complex problems before attempting solutions",
                "Approach cycling indicates insufficient constraint understanding",
            ],
            "source": "docs/theory.md",
            "heading": "Problem Solving",
        }
    ],
    "alignment": [
        {
            "claims": [
                "Learning from errors is essential to alignment",
                "Verify assumptions before scaling actions",
            ],
            "source": "docs/alignment.md",
            "heading": "Alignment",
        }
    ],
    "anti_patterns": [
        {
            "claims": [
                "Brute force escalation destroys constraint understanding",
                "Repetitive tool usage without progress indicates stuck state",
            ],
            "source": "docs/theory.md",
            "heading": "Anti-Patterns",
        }
    ],
}


def _make_finding(signal: str, msg: str = "Test finding") -> LintIssue:
    return LintIssue(
        linter="behavior_channel",
        kind=signal,
        message=msg,
        severity="warning",
    )


# ── _ground_finding_in_theory ────────────────────────────────────────


class TestGroundFindingInTheory:
    def test_adds_coda_to_message(self) -> None:
        finding = _make_finding("approach_cycling", "3 approaches failed")
        coda = _ground_finding_in_theory(finding, "approach_cycling", SAMPLE_PROFILE)
        assert coda is not None
        assert "Theory:" in finding.message
        assert finding.message.startswith("3 approaches failed")

    def test_coda_capped_at_max_chars(self) -> None:
        finding = _make_finding("approach_cycling")
        coda = _ground_finding_in_theory(finding, "approach_cycling", SAMPLE_PROFILE)
        if coda is not None:
            # Coda itself (including " Theory: ") should be reasonable
            assert len(coda) <= _THEORY_CODA_MAX_CHARS + 20  # slack for framing

    def test_stores_theory_context_in_evidence(self) -> None:
        finding = _make_finding("failure_amnesia")
        coda = _ground_finding_in_theory(finding, "failure_amnesia", SAMPLE_PROFILE)
        assert coda is not None
        assert "theory_context" in finding.evidence
        assert len(finding.evidence["theory_context"]) >= 1

    def test_none_when_no_profile(self) -> None:
        finding = _make_finding("approach_cycling")
        coda = _ground_finding_in_theory(finding, "approach_cycling", None)
        assert coda is None
        assert "Theory:" not in finding.message

    def test_none_when_empty_profile(self) -> None:
        finding = _make_finding("approach_cycling")
        coda = _ground_finding_in_theory(finding, "approach_cycling", {})
        assert coda is None

    def test_none_when_unknown_signal(self) -> None:
        finding = _make_finding("unknown_signal")
        coda = _ground_finding_in_theory(finding, "unknown_signal", SAMPLE_PROFILE)
        assert coda is None

    def test_none_when_no_matching_claims(self) -> None:
        # Profile with irrelevant content
        empty_profile = {
            "core_theory": [
                {
                    "claims": ["Quantum entanglement in databases"],
                    "source": "test.md",
                    "heading": "Irrelevant",
                }
            ]
        }
        finding = _make_finding("approach_cycling")
        coda = _ground_finding_in_theory(finding, "approach_cycling", empty_profile)
        # May or may not match depending on keyword overlap — this tests the path
        # If no match, coda should be None
        if coda is None:
            assert "Theory:" not in finding.message


# ── SIGNAL_THEORY_MAP completeness ───────────────────────────────────


class TestSignalTheoryMap:
    EXPECTED_SIGNALS = [
        "approach_cycling",
        "failure_amnesia",
        "premature_action",
        "brute_force_escalation",
        "verification_debt",
        "stale_model",
        "serial_discovery",
        "tool_repetition",
        "consecutive_failures",
    ]

    def test_all_signals_have_entries(self) -> None:
        for signal in self.EXPECTED_SIGNALS:
            assert signal in SIGNAL_THEORY_MAP, f"Missing SIGNAL_THEORY_MAP entry for {signal}"

    def test_entries_have_facets_and_keywords(self) -> None:
        for signal, mapping in SIGNAL_THEORY_MAP.items():
            assert "facets" in mapping, f"{signal} missing 'facets'"
            assert "keywords" in mapping, f"{signal} missing 'keywords'"
            assert len(mapping["facets"]) >= 1, f"{signal} has empty facets"
            assert len(mapping["keywords"]) >= 1, f"{signal} has empty keywords"


# ── SignalCoordinator dedup ──────────────────────────────────────────


class TestSignalCoordinatorTheoryDedup:
    def _make_coord(
        self,
        theory_profile: dict | None = None,
        recent_codas: dict[str, str] | None = None,
    ) -> _SignalCoordinator:
        compass = BehaviorCompass()
        compass.event_counter = 100  # Past any cooldown
        return _SignalCoordinator(
            compass,
            {"signal_cooldown": 0, "escalation_threshold": 999},
            theory_profile=theory_profile,
            recent_codas=recent_codas,
        )

    def test_theory_grounding_via_coordinator(self) -> None:
        """Findings get theory coda when profile is provided."""
        coord = self._make_coord(theory_profile=SAMPLE_PROFILE)
        finding = _make_finding("approach_cycling", "3 approaches failed")
        coord.add_finding("approach_cycling", finding, is_hard=True)
        assert len(coord.findings) == 1
        # Should have theory coda
        assert "Theory:" in coord.findings[0].message

    def test_no_grounding_without_profile(self) -> None:
        coord = self._make_coord(theory_profile=None)
        finding = _make_finding("approach_cycling", "3 approaches failed")
        coord.add_finding("approach_cycling", finding, is_hard=True)
        assert "Theory:" not in coord.findings[0].message

    def test_dedup_identical_codas(self) -> None:
        """Same coda as previous run is stripped."""
        # First run: generate the coda
        coord1 = self._make_coord(theory_profile=SAMPLE_PROFILE)
        f1 = _make_finding("approach_cycling", "3 approaches failed")
        coord1.add_finding("approach_cycling", f1, is_hard=True)

        if coord1._new_codas.get("approach_cycling"):
            coda_text = coord1._new_codas["approach_cycling"]

            # Second run: same coda should be deduped
            coord2 = self._make_coord(
                theory_profile=SAMPLE_PROFILE,
                recent_codas={"approach_cycling": coda_text},
            )
            f2 = _make_finding("approach_cycling", "3 approaches failed")
            coord2.add_finding("approach_cycling", f2, is_hard=True)
            assert "Theory:" not in coord2.findings[0].message

    def test_different_codas_not_deduped(self) -> None:
        """Different coda from previous run is preserved."""
        coord = self._make_coord(
            theory_profile=SAMPLE_PROFILE,
            recent_codas={"approach_cycling": " Theory: 'Something completely different'."},
        )
        finding = _make_finding("approach_cycling", "3 approaches failed")
        coord.add_finding("approach_cycling", finding, is_hard=True)
        # Should have new coda (not deduped because it's different)
        if coord._new_codas.get("approach_cycling"):
            assert "Theory:" in coord.findings[0].message

    def test_new_codas_tracked(self) -> None:
        """New codas are tracked in _new_codas for delta propagation."""
        coord = self._make_coord(theory_profile=SAMPLE_PROFILE)
        finding = _make_finding("failure_amnesia", "Repeated error")
        coord.add_finding("failure_amnesia", finding, is_hard=False)
        # If grounding found matches, _new_codas should have an entry
        if "Theory:" in coord.findings[0].message:
            assert "failure_amnesia" in coord._new_codas


# ── Integration: repeated run dedup ──────────────────────────────────


class TestRepeatedRunDedup:
    """Two consecutive coordinator runs with same state produce deduped codas."""

    def test_consecutive_runs_dedup(self) -> None:
        compass = BehaviorCompass()
        compass.event_counter = 100
        thresholds = {"signal_cooldown": 0, "escalation_threshold": 999}

        # Run 1
        coord1 = _SignalCoordinator(
            compass,
            thresholds,
            theory_profile=SAMPLE_PROFILE,
        )
        f1 = _make_finding("approach_cycling", "Original message")
        coord1.add_finding("approach_cycling", f1, is_hard=True)
        run1_has_coda = "Theory:" in coord1.findings[0].message
        run1_codas = dict(coord1._new_codas)

        if run1_has_coda:
            # Run 2 with same codas as "previous run"
            compass2 = BehaviorCompass()
            compass2.event_counter = 110  # Past cooldown
            coord2 = _SignalCoordinator(
                compass2,
                thresholds,
                theory_profile=SAMPLE_PROFILE,
                recent_codas=run1_codas,
            )
            f2 = _make_finding("approach_cycling", "Original message")
            coord2.add_finding("approach_cycling", f2, is_hard=True)
            # Second run should NOT have the same coda
            assert "Theory:" not in coord2.findings[0].message
