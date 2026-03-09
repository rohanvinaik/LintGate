"""Review item collection for context bootstrap needs_review protocol.

Extracted from context_bootstrap.py for module size compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bootstrap_defaults import ZERO_STATE_FACET_FALLBACKS
from .context_auditor import classify_directive_enforceability

_NO_THEORY = "(no theory content found)"


@dataclass
class ReviewItem:
    """A structured uncertainty marker for the calling agent to resolve.

    Attributes:
        review_type: Category of uncertainty: ``directive_classification``,
            ``dead_path_candidate``, or ``facet_fallback``.
        context: The specific content in question.
        question: A short, answerable question for the agent.
        options: Concrete resolution choices.
        detail: Extra context such as confidence score or candidate paths.
    """

    review_type: str
    context: str
    question: str
    options: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.review_type,
            "context": self.context,
            "question": self.question,
            "options": self.options,
            "detail": self.detail,
        }


def _collect_review_items(
    *,
    guidance: dict[str, Any],
    facet_summaries: dict[str, str],
    audit: dict[str, Any],
    project_root: str,
) -> list[ReviewItem]:
    """Collect structured uncertainty markers for the calling agent."""
    items: list[ReviewItem] = []
    _collect_directive_review_items(items, guidance)
    _collect_dead_path_review_items(items, audit)
    _collect_facet_fallback_items(items, facet_summaries)
    return items


def _collect_directive_review_items(
    items: list[ReviewItem],
    guidance: dict[str, Any],
) -> None:
    """Surface DO NOT directives where enforceability is uncertain."""
    do_not_directives = guidance.get("directives", {}).get("do_not", [])
    for directive in do_not_directives:
        result = classify_directive_enforceability(directive)
        if result.classification == "uncertain":
            items.append(
                ReviewItem(
                    review_type="directive_classification",
                    context=directive,
                    question=(
                        "Is this directive regex-enforceable (references a specific "
                        "API, identifier, or pattern) or architectural (describes a "
                        "process/approach that requires human judgment)?"
                    ),
                    options=["enforceable", "architectural"],
                    detail={
                        "confidence": result.confidence,
                        "reason": result.reason,
                    },
                )
            )


def _extract_dead_paths(detail_text: str) -> list[str]:
    """Parse dead paths from a path_references detail string."""
    if "don't exist" not in detail_text:
        return []
    colon_idx = detail_text.find(": ")
    if colon_idx < 0:
        return []
    paths_part = detail_text[colon_idx + 2 :]
    paren_idx = paths_part.rfind(" (+")
    if paren_idx >= 0:
        paths_part = paths_part[:paren_idx]
    return [p.strip() for p in paths_part.split(",") if p.strip()]


def _collect_dead_path_review_items(
    items: list[ReviewItem],
    audit: dict[str, Any],
) -> None:
    """Surface dead-path warnings so the agent can confirm/fix them."""
    for file_result in audit.get("audit", []):
        for check in file_result.get("health_checks", []):
            if check.get("check") != "path_references" or check.get("status") != "warn":
                continue
            dead_paths = _extract_dead_paths(check.get("detail", ""))
            source_name = file_result.get("name", "?")
            source_file = file_result.get("file", "")
            for dp in dead_paths:
                items.append(
                    ReviewItem(
                        review_type="dead_path_candidate",
                        context=dp,
                        question=(
                            f"The path `{dp}` referenced in "
                            f"`{source_name}` does not exist. "
                            "Should it be updated, removed, or is it correct "
                            "(e.g., it's created at runtime)?"
                        ),
                        options=["update_path", "remove_reference", "keep_as_is"],
                        detail={"source_file": source_file},
                    )
                )


def _collect_facet_fallback_items(
    items: list[ReviewItem],
    facet_summaries: dict[str, str],
) -> None:
    """Surface facets that fell back to zero-state defaults."""
    for key, fallback in ZERO_STATE_FACET_FALLBACKS.items():
        actual = (facet_summaries.get(key) or "").strip()
        if not actual or actual in (_NO_THEORY, fallback):
            items.append(
                ReviewItem(
                    review_type="facet_fallback",
                    context=key,
                    question=(
                        f"The '{key}' theory facet has no project-specific content "
                        "and fell back to a generic default. Can you provide a "
                        "1-2 sentence summary of this project's approach to "
                        f"'{key}'?"
                    ),
                    options=["provide_summary", "keep_default"],
                    detail={"default_used": fallback},
                )
            )
