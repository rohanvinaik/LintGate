"""Tests for E402 conditional severity escalation (Deliverable B, Gap 6).

Verifies that _maybe_escalate_e402 boosts confidence when both
non_stdlib_deps and has_lazy conditions are met, and does nothing
when either condition is absent.
"""

from __future__ import annotations

from lintgate.linters.ruff_linter import _maybe_escalate_e402


class TestE402EscalationBothConditions:
    """non_stdlib + has_lazy → confidence boosted to 0.85."""

    def test_both_conditions_met(self) -> None:
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["requests", "boto3"],
                "has_lazy": True,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 0.85
        assert escalation is not None
        assert "cross-environment risk" in escalation["reason"]
        assert "non_stdlib_deps" in escalation["conditions_met"]
        assert "lazy_imports" in escalation["conditions_met"]
        assert escalation["non_stdlib"] == ["requests", "boto3"]


class TestE402NoEscalationStdlibOnly:
    """Empty non_stdlib → no escalation."""

    def test_empty_non_stdlib(self) -> None:
        evidence = {
            "transitive_imports": {
                "non_stdlib": [],
                "has_lazy": True,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 1.0
        assert escalation is None

    def test_missing_non_stdlib(self) -> None:
        evidence = {
            "transitive_imports": {
                "has_lazy": True,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 1.0
        assert escalation is None


class TestE402NoEscalationNoLazy:
    """non_stdlib but !has_lazy → no escalation."""

    def test_has_lazy_false(self) -> None:
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["requests"],
                "has_lazy": False,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 1.0
        assert escalation is None

    def test_has_lazy_missing(self) -> None:
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["requests"],
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 1.0
        assert escalation is None


class TestE402EscalationEdgeCases:
    """Edge case tests for E402 escalation."""

    def test_empty_evidence(self) -> None:
        """Empty evidence dict → no escalation."""
        new_conf, escalation = _maybe_escalate_e402({}, 1.0)
        assert new_conf == 1.0
        assert escalation is None

    def test_no_transitive_imports(self) -> None:
        """Missing transitive_imports key → no escalation."""
        evidence = {"code": "E402"}
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 1.0
        assert escalation is None

    def test_confidence_value_preserved(self) -> None:
        """When no escalation, original confidence is returned unchanged."""
        evidence = {
            "transitive_imports": {
                "non_stdlib": [],
                "has_lazy": False,
            },
        }
        for conf in [0.5, 0.7, 1.0]:
            new_conf, escalation = _maybe_escalate_e402(evidence, conf)
            assert new_conf == conf
            assert escalation is None

    def test_escalation_is_fixed_085(self) -> None:
        """Escalation always sets confidence to 0.85 regardless of input."""
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["pkg"],
                "has_lazy": True,
            },
        }
        for conf in [0.5, 0.7, 1.0]:
            new_conf, _ = _maybe_escalate_e402(evidence, conf)
            assert new_conf == 0.85
