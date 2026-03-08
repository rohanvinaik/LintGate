"""Symmetry regime classification — Theorem 4.1 implementation.

Replaces the heuristic regime classifier (sigma > 20 → B) with
symmetry-group-derived classification using actual mutation data.
Falls back to the symbolic heuristic when mutation data is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mutation_engine import MutationCategory, ProfilingResult


@dataclass
class SymmetryAnalysis:
    """Result of symmetry-based regime classification."""

    function_key: str = ""
    parameter_count: int = 0
    symmetry_group_size: int = 1
    max_possible_symmetry: int = 1
    symmetry_ratio: float = 0.0
    category_independence: float = 0.0
    effective_sigma: int = 0
    regime: str = "A"
    regime_rationale: str = ""
    data_source: str = "symbolic"

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "parameter_count": self.parameter_count,
            "symmetry_group_size": self.symmetry_group_size,
            "symmetry_ratio": round(self.symmetry_ratio, 3),
            "category_independence": round(self.category_independence, 3),
            "effective_sigma": self.effective_sigma,
            "regime": self.regime,
            "regime_rationale": self.regime_rationale,
            "data_source": self.data_source,
        }


def classify_regime_from_mutations(
    profiling_result: ProfilingResult | None,
    sigma: int,
    is_pure: bool,
    parameter_count: int = 0,
) -> SymmetryAnalysis:
    """Regime classification using mutation data (Thm 4.1).

    Falls back to symbolic heuristic when profiling data is unavailable.
    """
    if profiling_result is None or profiling_result.total_mutants == 0:
        return _symbolic_fallback(sigma, is_pure, parameter_count)

    sym_size = _compute_symmetry_group_size(profiling_result)
    cat_independence = _compute_category_independence(profiling_result)

    # Factorial approximation for max symmetry (capped to avoid overflow)
    max_sym = _factorial(min(parameter_count, 10)) if parameter_count > 0 else 1
    sym_ratio = sym_size / max_sym if max_sym > 0 else 0.0

    # Effective sigma accounts for symmetry
    effective = max(1, round(sigma * (1.0 - sym_ratio * 0.5)))

    regime, rationale = _decide_regime(sym_ratio, cat_independence, sigma, is_pure, effective)

    return SymmetryAnalysis(
        function_key=profiling_result.function_key,
        parameter_count=parameter_count,
        symmetry_group_size=sym_size,
        max_possible_symmetry=max_sym,
        symmetry_ratio=sym_ratio,
        category_independence=cat_independence,
        effective_sigma=effective,
        regime=regime,
        regime_rationale=rationale,
        data_source="mutation",
    )


def _compute_symmetry_group_size(profiling_result: ProfilingResult) -> int:
    """Estimate |G(f)| from mutation kill patterns.

    Two mutations are symmetry-equivalent if they produce identical
    kill sets (the same tests kill both). The symmetry group size is
    the number of equivalence classes.

    Conservative: underestimates |G|, so never incorrectly classifies B→A.
    """
    if not profiling_result.kill_matrix:
        return 1

    # Group mutants by their kill set (frozenset of test names)
    kill_set_groups: dict[frozenset[str], int] = {}
    for _mutant_desc, test_names in profiling_result.kill_matrix.items():
        key = frozenset(test_names)
        kill_set_groups[key] = kill_set_groups.get(key, 0) + 1

    # Number of distinct equivalence classes
    return len(kill_set_groups)


def _compute_category_independence(profiling_result: ProfilingResult) -> float:
    """Measure independence between mutation categories.

    For each pair of categories, compute the Jaccard distance of their
    kill sets. Category independence is the mean pairwise Jaccard distance.

    0.0 = all categories killed by the same tests (fully redundant)
    1.0 = each category killed by a unique test set (fully independent)
    """
    # Build per-category kill sets
    cat_kills: dict[MutationCategory, set[str]] = {}
    for mutant_desc, test_names in profiling_result.kill_matrix.items():
        # Extract category from mutant description (format: "CATEGORY_N: desc")
        cat_str = mutant_desc.split("_")[0] if "_" in mutant_desc else ""
        try:
            cat = MutationCategory(cat_str)
        except ValueError:
            continue
        cat_kills.setdefault(cat, set()).update(test_names)

    categories = list(cat_kills.keys())
    if len(categories) < 2:
        return 0.0

    # Mean pairwise Jaccard distance
    total_distance = 0.0
    pair_count = 0
    for i, cat_a in enumerate(categories):
        for cat_b in categories[i + 1 :]:
            set_a = cat_kills[cat_a]
            set_b = cat_kills[cat_b]
            union = set_a | set_b
            if not union:
                continue
            intersection = set_a & set_b
            jaccard_distance = 1.0 - len(intersection) / len(union)
            total_distance += jaccard_distance
            pair_count += 1

    return total_distance / pair_count if pair_count > 0 else 0.0


def _decide_regime(
    symmetry_ratio: float,
    category_independence: float,
    sigma: int,
    is_pure: bool,
    effective_sigma: int,
) -> tuple[str, str]:
    """Decide regime from symmetry analysis."""
    if is_pure:
        return "A", "pure function: specification scales linearly"

    if symmetry_ratio > 0.3:
        return "A", (
            f"high symmetry ratio ({symmetry_ratio:.2f}) reduces "
            f"effective sigma from {sigma} to {effective_sigma}"
        )

    if category_independence < 0.3:
        return "A", (
            f"low category independence ({category_independence:.2f}) "
            f"indicates redundant mutation dimensions"
        )

    if sigma > 12 and category_independence > 0.7:
        return "B", (
            f"sigma={sigma} with high category independence "
            f"({category_independence:.2f}) and low symmetry "
            f"({symmetry_ratio:.2f}): complex specification surface"
        )

    return "A", f"sigma={sigma} within tractable range (symmetry-adjusted)"


def _symbolic_fallback(sigma: int, is_pure: bool, parameter_count: int) -> SymmetryAnalysis:
    """Fallback when no mutation data is available."""
    from .predictor import _classify_regime

    regime, rationale = _classify_regime(
        sigma=sigma,
        is_pure=is_pure,
        semantic_ratio=0.0,
        weakness_taxonomy="",
    )
    return SymmetryAnalysis(
        parameter_count=parameter_count,
        effective_sigma=sigma,
        regime=regime,
        regime_rationale=rationale,
        data_source="symbolic",
    )


def _factorial(n: int) -> int:
    """Compute n! for small n (capped at 10)."""
    result = 1
    for i in range(2, min(n, 10) + 1):
        result *= i
    return result
