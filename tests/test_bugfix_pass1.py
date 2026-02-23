"""Tests for PASS 1 bug fixes: Fix 8, Fix 17, Fix 15, Fix 12.

Fix 8:  Code-block state machine in context_guidance directive parser
Fix 17: Theory extractor backtick handling and anti_patterns scoring
Fix 15: ControlPlane file deduplication
Fix 12: Hygiene recommendation double-period fix
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.context_guidance import _parse_context_file
from lintgate.hygiene import classify_and_check
from lintgate.theory_extractor import _extract_claims, _score_claim

# ── Fix 8: Code-block state machine ─────────────────────────────────────


class TestCodeBlockFiltering:
    """Directives inside fenced code blocks must not be extracted."""

    def test_do_not_inside_code_block_is_ignored(self, tmp_path: Path) -> None:
        context_file = tmp_path / "CONTEXT.md"
        context_file.write_text(
            "DO use caching\n```python\n# DO NOT use eval()\nprint('hello')\n```\n"
        )
        result = _parse_context_file(str(context_file))
        directives = result["directives"]
        assert len(directives["do"]) == 1
        assert len(directives["do_not"]) == 0

    def test_critical_inside_code_block_is_ignored(self, tmp_path: Path) -> None:
        context_file = tmp_path / "CONTEXT.md"
        context_file.write_text("```\nCRITICAL: this is code\n```\n")
        result = _parse_context_file(str(context_file))
        assert len(result["directives"]["critical"]) == 0

    def test_directives_before_and_after_code_block(self, tmp_path: Path) -> None:
        context_file = tmp_path / "CONTEXT.md"
        context_file.write_text(
            "CRITICAL: always validate\n"
            "```\n"
            "MUST not be extracted\n"
            "DO NOT extract this\n"
            "```\n"
            "MUST sanitize inputs\n"
        )
        result = _parse_context_file(str(context_file))
        directives = result["directives"]
        assert len(directives["critical"]) == 1
        assert len(directives["must"]) == 1
        assert len(directives["do_not"]) == 0

    def test_inline_backtick_still_extracted(self, tmp_path: Path) -> None:
        """Single backticks (inline code) should NOT trigger block detection."""
        context_file = tmp_path / "CONTEXT.md"
        context_file.write_text("DO NOT use `eval()` in production\n")
        result = _parse_context_file(str(context_file))
        assert len(result["directives"]["do_not"]) == 1


# ── Fix 17: Theory extractor backtick handling ──────────────────────────


class TestTheoryBacktickHandling:
    """Backtick content should be preserved, not replaced with 'CODE'."""

    def _make_section(self, body: str):
        """Helper to create a minimal _Section-like object."""
        from lintgate.theory_extractor import _Section

        return _Section(
            heading="Test",
            heading_level=2,
            body=body,
            source_file="test.md",
            rel_path="test.md",
            line_no=1,
        )

    def test_backtick_content_preserved_in_claims(self) -> None:
        section = self._make_section(
            "Hard-coded `config_path` values will break deployment across environments "
            "because the paths differ between staging and production."
        )
        claims = _extract_claims(section, "anti_patterns")
        # The claim should contain "config_path", not "CODE"
        if claims:
            assert all("CODE" not in c for c in claims)

    def test_real_anti_pattern_scores_positive(self) -> None:
        sentence = (
            "Hard-coded values will break deployment across environments "
            "because the paths differ between staging and production."
        )
        score = _score_claim(sentence, "anti_patterns")
        assert score > 0

    def test_tool_description_single_match_penalized(self) -> None:
        sentence = "The performance channel provides algebraic property analysis for functions."
        score = _score_claim(sentence, "anti_patterns")
        assert score <= 0

    def test_description_verb_penalized(self) -> None:
        sentence = "This module provides utilities for extracting tokens from text."
        score = _score_claim(sentence, "anti_patterns")
        assert score <= 0


# ── Fix 15: ControlPlane file deduplication ──────────────────────────────


class TestControlPlaneDedup:
    """_collect_files_for_event should deduplicate paths."""

    def test_duplicate_absolute_paths_deduped(self) -> None:
        from mcp_tools.controlplane_tools import _collect_files_for_event

        # Mock helpers and git-changed to produce duplicates
        dummy_file = "/tmp/test_dedup_lintgate.py"
        helpers = {
            "_collect_python_files": lambda root: [dummy_file, dummy_file],
        }
        # Patch out the git-changed import to raise (so fallback is used)
        with patch(
            "mcp_tools.controlplane_tools.collect_changed_python_files",
            side_effect=ImportError,
            create=True,
        ):
            result = _collect_files_for_event("/tmp", helpers)
        assert result.count(dummy_file) == 1

    def test_mixed_paths_resolved_to_same_file(self, tmp_path: Path) -> None:
        from mcp_tools.controlplane_tools import _collect_files_for_event

        # Create a real file so Path.resolve() works
        test_file = tmp_path / "example.py"
        test_file.write_text("pass\n")
        abs_path = str(test_file)
        rel_path = os.path.join(str(tmp_path), ".", "example.py")

        helpers = {
            "_collect_python_files": lambda root: [abs_path, rel_path],
        }
        with patch(
            "mcp_tools.controlplane_tools.collect_changed_python_files",
            side_effect=ImportError,
            create=True,
        ):
            result = _collect_files_for_event(str(tmp_path), helpers)
        assert len(result) == 1


# ── Fix 12: Hygiene double-period fix ────────────────────────────────────


class TestHygieneRecommendation:
    """classify_and_check recommendation should never contain '..'."""

    def test_warning_ending_in_period_no_double_period(self, tmp_path: Path) -> None:
        result = classify_and_check("pip install requests", str(tmp_path))
        if result.warnings:
            assert ".." not in result.recommendation

    def test_single_warning_recommendation_format(self, tmp_path: Path) -> None:
        # Force a single warning by checking pip install without venv
        result = classify_and_check("pip install requests", str(tmp_path))
        if result.recommendation:
            # Should end cleanly with "Address before proceeding."
            assert result.recommendation.endswith("Address before proceeding.")
            assert ".." not in result.recommendation

    def test_multiple_warnings_no_double_period(self, tmp_path: Path) -> None:
        # Create a project with pyproject.toml but no lockfile and no venv
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        result = classify_and_check("pip install requests", str(tmp_path))
        if result.warnings and len(result.warnings) > 1:
            assert ".." not in result.recommendation
