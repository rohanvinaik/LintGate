"""Tests for Phase 1C: Session Readiness Advisory Gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.context_auditor import SessionReadiness, check_session_readiness

# ── Sample Profiles ──────────────────────────────────────────────────

FULL_PROFILE = {
    "core_theory": [
        {
            "claims": ["Constraint maps become architectures of possibility"],
            "source": "docs/theory.md",
            "heading": "Core Theory",
        }
    ],
    "problem_solving": [
        {
            "claims": ["Decompose complex problems before attempting solutions"],
            "source": "docs/theory.md",
            "heading": "Problem Solving",
        }
    ],
    "alignment": [
        {
            "claims": ["Learning from errors is essential to alignment"],
            "source": "docs/alignment.md",
            "heading": "Alignment",
        }
    ],
}

MISSING_FACET_PROFILE = {
    "core_theory": [
        {
            "claims": ["Test-first is core theory"],
            "source": "docs/theory.md",
            "heading": "Core Theory",
        }
    ],
    # Missing problem_solving and alignment
}

EMPTY_CLAIMS_PROFILE = {
    "core_theory": [{"claims": [], "source": "t.md", "heading": "T"}],
    "problem_solving": [{"claims": ["Some claim"], "source": "t.md", "heading": "PS"}],
    "alignment": [{"claims": ["Aligned"], "source": "t.md", "heading": "A"}],
}


# ── SessionReadiness ─────────────────────────────────────────────────


class TestSessionReadiness:
    def test_defaults(self) -> None:
        r = SessionReadiness()
        assert r.ready is False
        assert r.missing == []
        assert r.recommendation == ""


# ── check_session_readiness ──────────────────────────────────────────


class TestCheckSessionReadiness:
    def _setup_with_rules(self, tmp_path: Path) -> str:
        """Create a project with CLAUDE.md containing enforceable rules."""
        (tmp_path / "CLAUDE.md").write_text(
            "# CLAUDE.md\n\n## Rules\n# LINTGATE_FORBID_REGEX: debugger\n"
        )
        return str(tmp_path)

    def _setup_without_rules(self, tmp_path: Path) -> str:
        """Create a project with CLAUDE.md but no enforceable rules."""
        (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n\nGeneral content\n")
        return str(tmp_path)

    def test_full_profile_with_rules_is_ready(self, tmp_path: Path) -> None:
        root = self._setup_with_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=FULL_PROFILE)
        assert result.ready is True
        assert result.missing == []
        assert result.recommendation == ""

    def test_no_profile_not_ready(self, tmp_path: Path) -> None:
        root = self._setup_with_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=None)
        assert result.ready is False
        assert "no_theory_profile" in result.missing

    def test_missing_facets_not_ready(self, tmp_path: Path) -> None:
        root = self._setup_with_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=MISSING_FACET_PROFILE)
        assert result.ready is False
        missing_facets = [m for m in result.missing if m.startswith("missing_facet:")]
        assert len(missing_facets) >= 2
        assert "missing_facet:problem_solving" in result.missing
        assert "missing_facet:alignment" in result.missing

    def test_empty_claims_not_ready(self, tmp_path: Path) -> None:
        root = self._setup_with_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=EMPTY_CLAIMS_PROFILE)
        assert result.ready is False
        assert "missing_facet:core_theory" in result.missing

    def test_no_enforceable_rules_not_ready(self, tmp_path: Path) -> None:
        root = self._setup_without_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=FULL_PROFILE)
        assert result.ready is False
        assert "no_enforceable_rules" in result.missing

    def test_no_claude_md_not_ready(self, tmp_path: Path) -> None:
        result = check_session_readiness(str(tmp_path), theory_profile=FULL_PROFILE)
        assert result.ready is False
        assert "no_enforceable_rules" in result.missing

    def test_recommendation_mentions_bootstrap(self, tmp_path: Path) -> None:
        root = self._setup_without_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=None)
        assert "bootstrap_context_files" in result.recommendation

    def test_recommendation_lists_missing_facets(self, tmp_path: Path) -> None:
        root = self._setup_with_rules(tmp_path)
        result = check_session_readiness(root, theory_profile=MISSING_FACET_PROFILE)
        assert "problem_solving" in result.recommendation
        assert "alignment" in result.recommendation

    def test_require_regex_also_counts_as_rules(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(
            "# Rules\n# LINTGATE_REQUIRE_REGEX: test_\n"
        )
        root = str(tmp_path)
        result = check_session_readiness(root, theory_profile=FULL_PROFILE)
        assert "no_enforceable_rules" not in result.missing


# ── InquiryConfig defaults ───────────────────────────────────────────


class TestSessionGateConfig:
    def test_session_gate_disabled_by_default(self) -> None:
        from lintgate.controlplane.types import InquiryConfig

        cfg = InquiryConfig()
        assert cfg.session_gate is False

    def test_session_gate_enabled(self) -> None:
        from lintgate.controlplane.types import InquiryConfig

        cfg = InquiryConfig(session_gate=True)
        assert cfg.session_gate is True
        assert cfg.any_enabled() is True
