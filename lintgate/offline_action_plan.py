"""Action plan builder for offline analysis.

Builds prioritized, dependency-ordered fix plans from analysis results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_PROJECT_SENTINEL = "<project>"


@dataclass
class ActionItem:
    """A single prioritized fix for the LLM agent to implement."""

    rank: int
    priority: str
    category: str
    file: str
    function: str = ""
    action: str = ""
    rationale: str = ""
    depends_on: list[int] = field(default_factory=list)
    estimated_effort: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


ActionItem.__test__ = False  # type: ignore[attr-defined]


def _build_action_plan(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a prioritized, dependency-ordered action plan.

    Priority tiers:
    - P0_blocking: Lint errors that prevent code from running
    - P1_critical: Auto-fixable lint issues, high-survival mutations, missing tests for critical functions
    - P2_important: Specification gaps, composition issues, test coverage gaps
    - P3_improve: Performance improvements, prescriptive spec opportunities

    Dependencies:
    - Lint fixes before spec analysis (clean code first)
    - Auto-fixes before manual fixes (quick wins first)
    - Test creation before mutation profiling (need tests to measure)
    - Spec gaps before optimization hints (need spec evidence)
    """
    actions: list[ActionItem] = []
    rank = 0

    lint = analysis.get("lint", {})
    spec = analysis.get("specification", {})
    mutation = analysis.get("mutation", {})
    coverage = analysis.get("test_coverage", {})
    performance = analysis.get("performance", {})

    # ── Phase 1: Auto-fixable lint (P1, no deps) ─────────────────
    auto_fix_rank = None
    if lint.get("auto_fixable", 0) > 0:
        rank += 1
        auto_fix_rank = rank
        actions.append(
            ActionItem(
                rank=rank,
                priority="P1_critical",
                category="lint_auto_fix",
                file=_PROJECT_SENTINEL,
                action=f"Run `lint_fix(path)` to auto-fix {lint['auto_fixable']} issues. This is a zero-effort first step.",
                rationale="Auto-fixable issues are mechanical — fix them immediately to reduce noise.",
                estimated_effort="trivial",
                evidence={"auto_fixable_count": lint["auto_fixable"]},
            )
        )

    # ── Phase 1b: Blocking lint errors (P0, no deps) ─────────────
    for finding in lint.get("findings", [])[:10]:
        if finding.get("severity") != "blocking":
            continue
        rank += 1
        actions.append(
            ActionItem(
                rank=rank,
                priority="P0_blocking",
                category="lint_error",
                file=finding.get("file", ""),
                action=f"Fix {finding.get('kind', '')}: {finding.get('message', '')}",
                rationale="Blocking lint errors prevent code from running correctly.",
                estimated_effort="small",
                evidence={"kind": finding.get("kind"), "line": finding.get("line")},
            )
        )

    # ── Phase 2: Create missing test files (P1, depends on lint fix) ──
    test_creation_ranks: list[int] = []
    for entry in coverage.get("no_test_files", [])[:15]:
        rank += 1
        test_creation_ranks.append(rank)
        deps = [auto_fix_rank] if auto_fix_rank else []
        actions.append(
            ActionItem(
                rank=rank,
                priority="P1_critical",
                category="missing_test_file",
                file=entry["file"],
                action=f"Create test file for {entry['file']} ({entry['src_loc']} LoC, no tests). "
                f"Use `bootstrap_tests(path, file='{entry['file']}')` or write manually.",
                rationale="Functions without any test file cannot be mutation-profiled or spec-verified.",
                depends_on=deps,
                estimated_effort="medium",
                evidence={"src_loc": entry["src_loc"]},
            )
        )

    # ── Phase 3: Close specification gaps (P2, depends on tests) ──
    for func in spec.get("under_specified_top", [])[:15]:
        rank += 1
        func_key = func.get("function_key", "")
        sigma = func.get("estimated_sigma", 0)
        assertions = func.get("assertion_count", 0)
        deps = test_creation_ranks[:3] if test_creation_ranks else []
        actions.append(
            ActionItem(
                rank=rank,
                priority="P2_important",
                category="spec_gap",
                file=func.get("source_file", ""),
                function=func_key,
                action=f"Close specification gap for '{func_key}': sigma={sigma}, assertions={assertions}. "
                f"Add {sigma - assertions} targeted assertions. "
                f"Use `spec_file_prescribe(path, file)` for specific recommendations.",
                rationale=f"Under-specified function: {sigma - assertions} specification points missing.",
                depends_on=deps,
                estimated_effort="medium" if (sigma - assertions) < 5 else "large",
                evidence={
                    "sigma": sigma,
                    "assertions": assertions,
                    "gap": sigma - assertions,
                    "regime": func.get("regime", "unknown"),
                    "phase": func.get("phase", "bulk"),
                },
            )
        )

    # ── Phase 4: Kill surviving mutations (P2, depends on tests) ──
    if isinstance(mutation, dict) and mutation.get("cached"):
        for func_profile in mutation.get("high_survival_functions", [])[:15]:
            rank += 1
            func_key = func_profile.get("function_key", "")
            surviving = func_profile.get("surviving_categories", [])
            actions.append(
                ActionItem(
                    rank=rank,
                    priority="P2_important",
                    category="mutation_survival",
                    file=func_key.split("::")[0] if "::" in func_key else "",
                    function=func_key,
                    action=f"Kill surviving mutations in '{func_key}' "
                    f"(categories: {', '.join(surviving)}). "
                    f"Use `mutation_prescribe(path, file)` for targeted test templates.",
                    rationale=f"Survival rate {func_profile.get('survival_rate', 0):.0%} — "
                    f"tests exist but don't verify key behaviors.",
                    depends_on=[],
                    estimated_effort="medium",
                    evidence={
                        "kill_rate": func_profile.get("kill_rate", 0),
                        "surviving_categories": surviving,
                    },
                )
            )

    # ── Phase 5: Improve low test coverage (P2) ──────────────────
    for entry in coverage.get("low_coverage_files", [])[:10]:
        rank += 1
        actions.append(
            ActionItem(
                rank=rank,
                priority="P2_important",
                category="low_test_coverage",
                file=entry["file"],
                action=f"Improve test coverage for {entry['file']} "
                f"(ratio: {entry['ratio']:.2f}x, {entry['src_loc']} src LoC, "
                f"{entry['test_loc']} test LoC).",
                rationale="Low test-to-source ratio indicates under-tested code.",
                estimated_effort="medium",
                evidence=entry,
            )
        )

    # ── Phase 6: Prescriptive spec opportunities (P3) ─────────────
    prescriptive = analysis.get("prescriptive", {})
    if prescriptive.get("total_specs", 0) == 0 and len(spec.get("hotspot_functions", [])) > 0:
        rank += 1
        top_hotspots = [f.get("function_key", "") for f in spec.get("hotspot_functions", [])[:5]]
        actions.append(
            ActionItem(
                rank=rank,
                priority="P3_improve",
                category="prescriptive_opportunity",
                file=_PROJECT_SENTINEL,
                action=f"Create prescriptive specs for top hotspot functions: "
                f"{', '.join(top_hotspots)}. "
                f"Use `prescriptive_spec_compose(path, target)` to create behavioral contracts "
                f"before writing new code.",
                rationale="Prescriptive specs shift quality left — behavioral contracts before code, not after.",
                estimated_effort="small",
                evidence={"hotspot_count": len(top_hotspots), "hotspots": top_hotspots},
            )
        )

    # ── Phase 7: Pure function optimization (P3) ──────────────────
    pure_count = performance.get("pure_count", 0)
    if pure_count > 0:
        rank += 1
        actions.append(
            ActionItem(
                rank=rank,
                priority="P3_improve",
                category="purity_optimization",
                file=_PROJECT_SENTINEL,
                action=f"{pure_count} pure functions detected. "
                f"Use `inspect_algebra(path)` to extract algebraic properties "
                f"and `generate_property_tests(path)` for Hypothesis-based verification.",
                rationale="Pure functions enable safe caching, parallelization, and property-based testing.",
                estimated_effort="medium",
                evidence={"pure_count": pure_count, "pure_ratio": performance.get("pure_ratio", 0)},
            )
        )

    return [asdict(a) for a in actions]
