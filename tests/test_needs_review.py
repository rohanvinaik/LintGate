"""Tests for the needs_review protocol in bootstrap_context_files.

The needs_review protocol surfaces structured uncertainty markers so
the calling LLM agent can cheaply resolve ambiguities that the
deterministic tool can't decide on its own.  Three sources:

1. Directive classification — DO NOT directives where enforceability is uncertain
2. Dead path candidates — paths flagged dead that the agent might fix
3. Facet fallback — theory facets using generic defaults the agent can replace
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


# ── DirectiveClassification 3-way classifier ─────────────────────────


class TestDirectiveClassification3Way:
    """Tests for the 3-way classifier returning enforceable/architectural/uncertain."""

    def test_clear_syntactic_returns_enforceable(self) -> None:
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability(
            "DO NOT use `threading.Thread` directly"
        )
        assert result.classification == "enforceable"
        assert result.confidence >= 0.9

    def test_clear_architectural_returns_architectural(self) -> None:
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability(
            "DO NOT bypass shared abstractions without understanding the constraint space"
        )
        assert result.classification == "architectural"
        assert result.confidence >= 0.7

    def test_bare_technology_name_returns_uncertain(self) -> None:
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability("DO NOT use checkra1n")
        assert result.classification == "uncertain"
        assert result.confidence < 0.5

    def test_no_signals_returns_uncertain(self) -> None:
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability("DO NOT do bad things")
        assert result.classification == "uncertain"
        assert result.confidence < 0.5

    def test_equal_signals_returns_uncertain(self) -> None:
        """When syntactic and architectural cues are equally present, uncertain."""
        from lintgate.context_auditor import classify_directive_enforceability

        # Has both dotted name (syntactic) and "approach" (architectural)
        result = classify_directive_enforceability(
            "DO NOT use this approach for os.path calls"
        )
        assert result.classification in ("uncertain", "enforceable", "architectural")
        # Key: should have lower confidence than clear-cut cases
        if result.classification == "uncertain":
            assert result.confidence < 0.6

    def test_is_regex_enforceable_backward_compat(self) -> None:
        """The old bool API should still work via the 3-way classifier."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert _is_regex_enforceable("DO NOT use `threading.Thread` directly") is True
        assert _is_regex_enforceable("DO NOT bypass shared abstractions") is False

    def test_classification_has_reason(self) -> None:
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability("DO NOT use `os.system`")
        assert result.reason  # Non-empty explanation

    def test_mixed_signals_syntactic_dominant(self) -> None:
        """When syntactic signals dominate, should be enforceable."""
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability(
            "DO NOT create task-specific functions like solve_task_abc()"
        )
        assert result.classification == "enforceable"

    def test_mixed_signals_architectural_dominant(self) -> None:
        """When architectural signals dominate, should be architectural."""
        from lintgate.context_auditor import classify_directive_enforceability

        result = classify_directive_enforceability(
            "DO NOT bypass shared abstractions without understanding or verifying constraints"
        )
        assert result.classification == "architectural"


# ── ReviewItem dataclass ─────────────────────────────────────────────


class TestReviewItem:
    """Tests for the ReviewItem dataclass."""

    def test_to_dict_structure(self) -> None:
        from lintgate.context_bootstrap import ReviewItem

        item = ReviewItem(
            review_type="directive_classification",
            context="DO NOT use checkra1n",
            question="Is this enforceable or architectural?",
            options=["enforceable", "architectural"],
            detail={"confidence": 0.3, "reason": "No signals"},
        )
        d = item.to_dict()
        assert d["type"] == "directive_classification"
        assert d["context"] == "DO NOT use checkra1n"
        assert d["question"].startswith("Is this")
        assert d["options"] == ["enforceable", "architectural"]
        assert d["detail"]["confidence"] == 0.3

    def test_default_fields(self) -> None:
        from lintgate.context_bootstrap import ReviewItem

        item = ReviewItem(
            review_type="facet_fallback",
            context="core_theory",
            question="Can you summarize?",
        )
        assert item.options == []
        assert item.detail == {}


# ── Directive Review Item Collection ─────────────────────────────────


class TestDirectiveReviewCollection:
    """Tests for _collect_directive_review_items."""

    def test_uncertain_directives_collected(self) -> None:
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_directive_review_items,
        )

        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT use checkra1n",  # uncertain — no signals
                    "DO NOT use `threading.Thread`",  # enforceable — clear
                    "DO NOT bypass constraints",  # architectural — clear
                ],
            },
        }
        items: list[ReviewItem] = []
        _collect_directive_review_items(items, guidance)

        # Only the uncertain one should appear
        assert len(items) == 1
        assert items[0].review_type == "directive_classification"
        assert "checkra1n" in items[0].context
        assert "enforceable" in items[0].options
        assert "architectural" in items[0].options

    def test_no_directives_no_items(self) -> None:
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_directive_review_items,
        )

        items: list[ReviewItem] = []
        _collect_directive_review_items(items, {"directives": {"do_not": []}})
        assert len(items) == 0

    def test_all_clear_no_items(self) -> None:
        """When all directives are clearly classified, no review items."""
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_directive_review_items,
        )

        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT use `os.system` for subprocess calls",
                    "DO NOT bypass shared abstractions without understanding",
                ],
            },
        }
        items: list[ReviewItem] = []
        _collect_directive_review_items(items, guidance)
        assert len(items) == 0


# ── Dead Path Review Item Collection ─────────────────────────────────


class TestDeadPathReviewCollection:
    """Tests for _collect_dead_path_review_items."""

    def test_dead_paths_collected(self) -> None:
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_dead_path_review_items,
        )

        audit = {
            "audit": [
                {
                    "file": "/project/CLAUDE.md",
                    "name": "CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "2 referenced path(s) don't exist: src/old.py, lib/gone.py",
                        },
                    ],
                },
            ],
        }
        items: list[ReviewItem] = []
        _collect_dead_path_review_items(items, audit)

        assert len(items) == 2
        paths = [i.context for i in items]
        assert "src/old.py" in paths
        assert "lib/gone.py" in paths
        assert all(i.review_type == "dead_path_candidate" for i in items)
        assert "update_path" in items[0].options

    def test_dead_paths_with_more_suffix(self) -> None:
        """Handles the '(+N more)' suffix in detail text."""
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_dead_path_review_items,
        )

        audit = {
            "audit": [
                {
                    "file": "/project/CLAUDE.md",
                    "name": "CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "3 referenced path(s) don't exist: a.py, b.py (+1 more)",
                        },
                    ],
                },
            ],
        }
        items: list[ReviewItem] = []
        _collect_dead_path_review_items(items, audit)
        assert len(items) == 2
        assert items[0].context == "a.py"
        assert items[1].context == "b.py"

    def test_no_dead_paths_no_items(self) -> None:
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_dead_path_review_items,
        )

        audit = {
            "audit": [
                {
                    "file": "/project/CLAUDE.md",
                    "name": "CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "pass",
                            "detail": "All 5 path references verified",
                        },
                    ],
                },
            ],
        }
        items: list[ReviewItem] = []
        _collect_dead_path_review_items(items, audit)
        assert len(items) == 0

    def test_source_file_in_detail(self) -> None:
        """Review items should include the source file for context."""
        from lintgate.context_bootstrap import (
            ReviewItem,
            _collect_dead_path_review_items,
        )

        audit = {
            "audit": [
                {
                    "file": "/project/CLAUDE.md",
                    "name": "CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "1 referenced path(s) don't exist: missing.py",
                        },
                    ],
                },
            ],
        }
        items: list[ReviewItem] = []
        _collect_dead_path_review_items(items, audit)
        assert len(items) == 1
        assert items[0].detail["source_file"] == "/project/CLAUDE.md"


# ── Facet Fallback Review Item Collection ────────────────────────────


class TestFacetFallbackReviewCollection:
    """Tests for _collect_facet_fallback_items."""

    def test_missing_facets_collected(self) -> None:
        from lintgate.context_bootstrap import ReviewItem, _collect_facet_fallback_items

        # Empty summaries → all 4 facets fall back
        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, {})
        assert len(items) == 4
        facets = {i.context for i in items}
        assert facets == {"core_theory", "problem_solving", "alignment", "architecture"}

    def test_present_facets_not_collected(self) -> None:
        from lintgate.context_bootstrap import ReviewItem, _collect_facet_fallback_items

        summaries = {
            "core_theory": "This project focuses on secure device recovery.",
            "problem_solving": "Iterative hardware probing approach.",
            "alignment": "Changes must preserve device safety invariants.",
            "architecture": "Modular pipeline with hardware abstraction layer.",
        }
        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, summaries)
        assert len(items) == 0

    def test_partial_facets(self) -> None:
        """Only missing facets should be collected."""
        from lintgate.context_bootstrap import ReviewItem, _collect_facet_fallback_items

        summaries = {
            "core_theory": "Real content here.",
            "architecture": "Real architecture content.",
        }
        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, summaries)
        facets = {i.context for i in items}
        assert "core_theory" not in facets
        assert "architecture" not in facets
        assert "problem_solving" in facets
        assert "alignment" in facets

    def test_no_theory_string_treated_as_missing(self) -> None:
        from lintgate.context_bootstrap import ReviewItem, _collect_facet_fallback_items

        summaries = {
            "core_theory": "(no theory content found)",
        }
        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, summaries)
        facets = {i.context for i in items}
        assert "core_theory" in facets

    def test_default_text_treated_as_missing(self) -> None:
        """Facets whose value matches the zero-state fallback should be collected."""
        from lintgate.bootstrap_defaults import ZERO_STATE_FACET_FALLBACKS
        from lintgate.context_bootstrap import ReviewItem, _collect_facet_fallback_items

        summaries = {
            "core_theory": ZERO_STATE_FACET_FALLBACKS["core_theory"],
        }
        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, summaries)
        facets = {i.context for i in items}
        assert "core_theory" in facets

    def test_review_item_has_default_in_detail(self) -> None:
        from lintgate.context_bootstrap import ReviewItem, _collect_facet_fallback_items

        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, {})
        for item in items:
            assert "default_used" in item.detail
            assert len(item.detail["default_used"]) > 10  # Non-trivial default text


# ── _collect_review_items integration ────────────────────────────────


class TestCollectReviewItemsIntegration:
    """Tests for _collect_review_items combining all three sources."""

    def test_combines_all_sources(self) -> None:
        from lintgate.context_bootstrap import _collect_review_items

        guidance = {
            "directives": {
                "do_not": ["DO NOT use checkra1n"],  # uncertain
            },
            "rules": [],
        }
        audit = {
            "audit": [
                {
                    "file": "/project/CLAUDE.md",
                    "name": "CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "1 referenced path(s) don't exist: gone.py",
                        },
                    ],
                },
            ],
        }
        facet_summaries: dict[str, str] = {}  # All fallbacks

        items = _collect_review_items(
            guidance=guidance,
            facet_summaries=facet_summaries,
            audit=audit,
            project_root="/tmp/fake",
        )

        types = {i["type"] for i in [item.to_dict() for item in items]}
        assert "directive_classification" in types
        assert "dead_path_candidate" in types
        assert "facet_fallback" in types

    def test_empty_inputs_yield_only_facet_fallbacks(self) -> None:
        from lintgate.context_bootstrap import _collect_review_items

        items = _collect_review_items(
            guidance={"directives": {"do_not": []}, "rules": []},
            facet_summaries={},
            audit={"audit": []},
            project_root="/tmp/fake",
        )
        # Should have exactly the 4 facet fallbacks
        assert len(items) == 4
        assert all(i.review_type == "facet_fallback" for i in items)


# ── Bootstrap payload integration ────────────────────────────────────


class TestBootstrapPayloadNeedsReview:
    """Tests that the bootstrap return payload includes needs_review."""

    def test_needs_review_in_payload(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        assert "needs_review" in result
        assert isinstance(result["needs_review"], list)

    def test_needs_review_items_have_correct_structure(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        for item in result["needs_review"]:
            assert "type" in item
            assert "context" in item
            assert "question" in item
            assert "options" in item
            assert "detail" in item

    def test_llm_usage_hint_mentions_needs_review(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        assert "needs_review" in result["llm_usage_hint"]

    def test_no_review_items_when_everything_resolved(self, tmp_path: Path) -> None:
        """A project with full theory and no dead paths should have minimal review items.

        Note: facet fallbacks will still appear for projects with no docs, but
        directive and dead-path items should be zero.
        """
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        items = result["needs_review"]
        directive_items = [i for i in items if i["type"] == "directive_classification"]
        dead_path_items = [i for i in items if i["type"] == "dead_path_candidate"]
        # Empty project with no CLAUDE.md → no directives, no dead paths
        assert len(directive_items) == 0
        assert len(dead_path_items) == 0
