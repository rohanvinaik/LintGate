"""Decomposition heuristic detection based on mutation survival rates and static analysis."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintgate.mutation.state import MutationStateManager
    from lintgate.types import Prescription


@dataclass
class DecompositionCandidate:
    function_id: str
    file_path: str
    survival_rate: float | None  # None for static-only
    surviving_categories: list[str] | None  # None for static-only
    total_mutants: int | None  # None for static-only
    reason: str
    source: str = "dynamic"  # "static" | "dynamic" | "converged"
    confidence: float = 0.60  # static typically lower than dynamic
    evidence: list[str] = field(default_factory=list)
    actionability: str = "investigate"  # "extract" | "split" | "investigate"
    expected_benefit: str = ""  # "lower CC" | "fewer mixed responsibilities"
    prescriptions: list[Prescription] = field(default_factory=list)


class DecompositionDetector:
    """Heuristic detector for functions requiring structural decomposition."""

    DECOMPOSITION_THRESHOLD = 0.50
    MIN_CATEGORIES = 3

    def __init__(self, state_manager: MutationStateManager):
        self.state_manager = state_manager

    def get_candidates(
        self, file_path: str | None = None
    ) -> list[DecompositionCandidate]:
        """Scan all mutation state to find decomposition candidates (dynamic mode)."""
        candidates = []
        for state in self.state_manager.state.values():
            if file_path and not state.file_path.endswith(file_path):
                continue

            if state.total == 0:
                continue

            rate = state.survival_rate
            surviving_cats = [
                c for c, count in state.survived_by_category.items() if count > 0
            ]

            if (
                rate >= self.DECOMPOSITION_THRESHOLD
                and len(surviving_cats) >= self.MIN_CATEGORIES
            ):
                candidates.append(
                    DecompositionCandidate(
                        function_id=f"{state.file_path}::{state.function_name}",
                        file_path=state.file_path,
                        survival_rate=rate,
                        surviving_categories=surviving_cats,
                        total_mutants=state.total,
                        reason=(
                            f"High survival ({rate:.0%}) across {len(surviving_cats)} "
                            f"semantic categories indicates high structural entanglement."
                        ),
                        source="dynamic",
                        confidence=0.75,
                        evidence=["mutation_survival", "category_spread"],
                        actionability="extract",
                        expected_benefit="mutation hotspot isolation",
                    )
                )

        return sorted(candidates, key=lambda c: c.survival_rate or 0, reverse=True)


class DecompositionCoordinator:
    """Merges static (AST-based) and dynamic (mutation-based) decomposition candidates.

    Modes:
    - "auto" (default): return dynamic if data exists, else static. Merge when both.
    - "static": always return AST-based candidates.
    - "dynamic": current behavior (requires mutation data).
    """

    def __init__(
        self,
        state_manager: MutationStateManager,
        project_root: str,
        threshold: float = 0.50,
    ):
        self.state_manager = state_manager
        self.project_root = project_root
        self.threshold = threshold

    def get_candidates(
        self,
        file_path: str | None = None,
        mode: str = "auto",
        channel_results: list | None = None,
    ) -> list[DecompositionCandidate]:
        """Get decomposition candidates using the specified mode.

        Args:
            file_path: Optional file path filter.
            mode: "auto", "static", or "dynamic".

        Returns:
            Merged list of DecompositionCandidate, sorted by confidence.
        """
        if mode == "static":
            return get_static_candidates(self.project_root, file_path=file_path)

        # Dynamic candidates from mutation data
        detector = DecompositionDetector(self.state_manager)
        detector.DECOMPOSITION_THRESHOLD = self.threshold
        dynamic = detector.get_candidates(file_path=file_path)

        if mode == "dynamic":
            return dynamic

        # Auto mode: always get static, merge with dynamic
        static = get_static_candidates(self.project_root, file_path=file_path)
        merged = merge_candidates(dynamic, static)

        if channel_results:
            try:
                from lintgate.convergence.integration import (
                    enrich_decomposition_candidates,
                    extract_all_evidence,
                )

                convergence = extract_all_evidence(channel_results)
                merged = enrich_decomposition_candidates(merged, convergence)
            except Exception:
                pass

        return merged


def merge_candidates(
    dynamic: list[DecompositionCandidate],
    static: list[DecompositionCandidate],
) -> list[DecompositionCandidate]:
    """Merge dynamic (mutation) and static (AST) decomposition candidates.

    Rules:
    - Dynamic takes priority for the same function.
    - If both exist for the same function → source="converged", confidence boosted.
    - Static-only candidates are kept with their original lower confidence.
    """
    by_id: dict[str, DecompositionCandidate] = {}
    for c in dynamic:
        by_id[c.function_id] = c

    for sc in static:
        if sc.function_id in by_id:
            # Convergence: both static and dynamic agree this function needs work
            dc = by_id[sc.function_id]
            dc.source = "converged"
            dc.confidence = min(dc.confidence + 0.10, 0.95)
            dc.evidence = list(set(dc.evidence + sc.evidence))
            dc.expected_benefit = f"{dc.expected_benefit}; {sc.expected_benefit}"
        else:
            by_id[sc.function_id] = sc

    return sorted(by_id.values(), key=lambda c: c.confidence, reverse=True)


# ── Static decomposition ────────────────────────────────────────────────


def get_static_candidates(
    project_root: str,
    file_path: str | None = None,
    cc_threshold: int = 15,
) -> list[DecompositionCandidate]:
    """Find decomposition candidates using static analysis (AST-based).

    Scans Python files for high-CC functions and runs variable dependency
    clustering to propose extractions. No mutation data required.

    Args:
        project_root: Project root path.
        file_path: Optional specific file to analyze.
        cc_threshold: Minimum cognitive complexity to consider.

    Returns:
        List of DecompositionCandidate with source="static".
    """
    from lintgate.linters.cognitive_complexity import compute_cognitive_complexity
    from lintgate.linters.structure_checks.dependency_clustering import (
        find_extraction_candidates,
    )

    candidates: list[DecompositionCandidate] = []
    py_files = _discover_files(project_root, file_path)

    for filepath in py_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (SyntaxError, OSError):
            continue

        relpath = os.path.relpath(filepath, project_root)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cc = compute_cognitive_complexity(node)
            if cc < cc_threshold:
                continue

            prescriptions = find_extraction_candidates(node, filepath)
            if not prescriptions:
                continue

            total_cc_reduction = sum(
                p.expected_delta.get("cc_reduction", 0) for p in prescriptions
            )
            candidates.append(
                DecompositionCandidate(
                    function_id=f"{relpath}::{node.name}",
                    file_path=relpath,
                    survival_rate=None,
                    surviving_categories=None,
                    total_mutants=None,
                    reason=(
                        f"CC={cc}, {len(prescriptions)} extractable block(s) "
                        f"with total CC reduction of {total_cc_reduction}."
                    ),
                    source="static",
                    confidence=max(p.confidence for p in prescriptions),
                    evidence=["variable_clustering", "cognitive_complexity"],
                    actionability="extract",
                    expected_benefit="lower CC",
                )
            )

    return sorted(candidates, key=lambda c: c.confidence, reverse=True)


def _discover_files(project_root: str, file_path: str | None) -> list[str]:
    """Discover Python files to scan."""
    if file_path:
        full = (
            file_path
            if os.path.isabs(file_path)
            else os.path.join(project_root, file_path)
        )
        return [full] if os.path.isfile(full) else []

    py_files: list[str] = []
    for dirpath, _, filenames in os.walk(project_root):
        basename = os.path.basename(dirpath)
        if basename.startswith(".") or basename in (
            "__pycache__",
            "node_modules",
            ".git",
        ):
            continue
        resolved = os.path.realpath(dirpath)
        if "/site-packages/" in resolved or "/dist-packages/" in resolved:
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                py_files.append(os.path.join(dirpath, fn))
    return py_files
