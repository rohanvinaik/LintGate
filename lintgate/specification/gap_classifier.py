"""Per-function specification gap classification.

Classifies each function's specification gap into one of seven buckets:
- specified: adequately specified (no gap)
- unprofiled: no empirical mutation data — unmeasured, not specified
- real_underspecification: genuine missing tests
- equivalent_or_low_value: mutants are equivalent or low-value (e.g. to_dict serializers)
- budget_instability: measurement failed — profiling hit budget limits
- integration_only: needs integration/scenario tests, not unit tests
- discovery_failure: test linkage or discovery problem, not a real gap

Uses signals from the empirical overlay, mutation truth labels, topology,
and function naming heuristics to route each function to the correct bucket.
"""

from __future__ import annotations

from enum import Enum


class GapClass(str, Enum):
    """Classification of a function's specification gap."""

    SPECIFIED = "specified"
    UNPROFILED = "unprofiled"
    REAL_UNDERSPECIFICATION = "real_underspecification"
    EQUIVALENT_OR_LOW_VALUE = "equivalent_or_low_value"
    BUDGET_INSTABILITY = "budget_instability"
    INTEGRATION_ONLY = "integration_only"
    DISCOVERY_FAILURE = "discovery_failure"


# Function name patterns that indicate serialization/boilerplate
_SERIALIZATION_NAMES = frozenset(
    {
        "to_dict",
        "from_dict",
        "to_json",
        "from_json",
        "serialize",
        "deserialize",
        "as_dict",
        "to_tuple",
    }
)


def classify_gap(
    *,
    spec_level: float,
    reconciled_spec_level: float = 0.0,
    stop_criteria_met: bool = False,
    overlay_status: str = "NO_EMPIRICAL_DATA",
    mutation_truth_label: str = "",
    discovery_state: str = "",
    topology_state: str = "",
    survival_rate: float = 0.0,
    surviving_categories: list[str] | None = None,
    regime: str = "A",
    function_name: str = "",
) -> GapClass:
    """Classify a function's specification gap.

    Args:
        spec_level: Static specification level (0.0-1.0).
        reconciled_spec_level: Reconciled spec level from empirical overlay.
        stop_criteria_met: Whether specification stop criteria are satisfied.
        overlay_status: OverlayStatus value string.
        mutation_truth_label: Truth label from mutation enrichment.
        discovery_state: DiscoveryState value string.
        topology_state: TopologyState value string.
        survival_rate: Mutation survival rate (0.0-1.0).
        surviving_categories: List of surviving mutation category names.
        regime: Static regime classification (A/B).
        function_name: Bare function name (e.g. "to_dict", "run_pipeline").

    Returns:
        GapClass classification.
    """
    cats = surviving_categories or []
    effective_spec = max(spec_level, reconciled_spec_level)

    # ── Bucket 0: already specified ──────────────────────────────
    if stop_criteria_met:
        return GapClass.SPECIFIED
    if effective_spec >= 0.8 and survival_rate <= 0.05:
        return GapClass.SPECIFIED

    # ── Bucket 1: budget instability — measurement failed ────────
    # Separate from equivalent: budget instability means "we don't know",
    # equivalent means "behaviorally uninteresting."
    if mutation_truth_label == "BUDGET_INSTABILITY":
        return GapClass.BUDGET_INSTABILITY

    # ── Bucket 2: discovery/linkage failure ──────────────────────
    if mutation_truth_label == "DISCOVERY_ARTIFACT":
        return GapClass.DISCOVERY_FAILURE
    if overlay_status == "DISCOVERY_FAILURE":
        return GapClass.DISCOVERY_FAILURE
    if discovery_state in (
        "NO_TEST_FILES",
        "TEST_FILES_FOUND_NONE_LINKED",
        "DISCOVERY_IMPORT_FAILED",
    ):
        return GapClass.DISCOVERY_FAILURE

    # ── Bucket 3: equivalent or low-value mutants ────────────────
    if mutation_truth_label == "EQUIVALENT_OR_UNINTERESTING":
        return GapClass.EQUIVALENT_OR_LOW_VALUE

    # Serialization heuristic: VALUE-only survivors on boilerplate methods
    bare_name = function_name.rsplit(".", 1)[-1] if function_name else ""
    if bare_name in _SERIALIZATION_NAMES and cats and all(c == "VALUE" for c in cats):
        return GapClass.EQUIVALENT_OR_LOW_VALUE

    # ── Bucket 4: integration-only ───────────────────────────────
    if mutation_truth_label == "MOCK_BOUNDARY_ARTIFACT":
        return GapClass.INTEGRATION_ONLY
    if topology_state == "MOCK_BOUNDARY_DOMINANT":
        return GapClass.INTEGRATION_ONLY
    # Regime B with SWAP-dominant survivors → orchestration needs scenario tests
    if regime == "B" and cats and _swap_dominant(cats):
        return GapClass.INTEGRATION_ONLY

    # ── Bucket 5: real underspecification ────────────────────────
    if survival_rate > 0.0:
        return GapClass.REAL_UNDERSPECIFICATION

    # ── Bucket 6: unprofiled — no empirical data, can't classify ─
    # survival_rate == 0 here. Distinguish "all mutants killed" (empirical
    # data exists) from "never profiled" (no empirical data at all).
    has_empirical = overlay_status not in ("NO_EMPIRICAL_DATA", "")
    if not has_empirical:
        return GapClass.UNPROFILED

    # ── Fallback: empirical data confirms zero survival ──────────
    return GapClass.SPECIFIED


def _swap_dominant(categories: list[str]) -> bool:
    """Return True if SWAP mutations are the majority of survivors."""
    if not categories:
        return False
    swap_count = sum(1 for c in categories if c == "SWAP")
    return swap_count > len(categories) / 2


def classify_from_func_data(
    func_data: dict,
    mutation_entry: dict | None = None,
) -> GapClass:
    """Classify gap from the per-function dict produced by file_analyzer.

    This is the convenience wrapper for use in rollup aggregation where
    func_data comes from FileSpecResult.functions[key] and mutation_entry
    comes from the mutation cache.

    Args:
        func_data: Per-function dict from FileSpecResult.functions.
        mutation_entry: Optional mutation cache entry for this function.

    Returns:
        GapClass classification.
    """
    overlay = func_data.get("empirical_overlay", {})
    spec_level = float(func_data.get("specification_level", 0.0))
    reconciled = float(func_data.get("reconciled_spec_level", 0.0))

    # Extract mutation signals
    truth_label = ""
    discovery_state = ""
    topology_state = ""
    survival_rate = 0.0
    surviving_cats: list[str] = []

    if mutation_entry:
        truth_label = mutation_entry.get("mutation_truth_label", "")
        discovery_state = mutation_entry.get("discovery_state", "")
        topology_state = mutation_entry.get("topology_state", "")
        survival_rate = float(mutation_entry.get("survival_rate", 0.0))
        # Extract surviving category names from survivor_records
        for rec in mutation_entry.get("survivor_records", []):
            cat = rec.get("category", "")
            if cat:
                surviving_cats.append(cat)
        # Also check per_category for non-zero survival
        if not surviving_cats:
            for pc in mutation_entry.get("per_category", []):
                if pc.get("survived", 0) > 0:
                    cat = pc.get("category", "")
                    if cat:
                        surviving_cats.extend([cat] * pc["survived"])

    # Extract function name from key or func_data
    func_name = func_data.get("function_name", "")

    return classify_gap(
        spec_level=spec_level,
        reconciled_spec_level=reconciled,
        stop_criteria_met=bool(func_data.get("stop_criteria_met", False)),
        overlay_status=overlay.get("status", "NO_EMPIRICAL_DATA"),
        mutation_truth_label=truth_label,
        discovery_state=discovery_state,
        topology_state=topology_state,
        survival_rate=survival_rate,
        surviving_categories=surviving_cats,
        regime=func_data.get("regime", "A"),
        function_name=func_name,
    )
