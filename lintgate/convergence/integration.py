"""Integration helpers: extract evidence from ChannelResults and enrich decomposition candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .aggregator import (
    adapt_cochange_file,
    adapt_cohesion,
    adapt_cohesion_file,
    adapt_cross_channel,
    adapt_fan_in,
    adapt_fan_in_file,
    adapt_import_weight_file,
    adapt_mutation,
    adapt_purity,
    aggregate,
    aggregate_file,
)

if TYPE_CHECKING:
    from lintgate.controlplane.types import ChannelResult
    from lintgate.mutation.decomposition import DecompositionCandidate

    from .evidence import ConvergenceResult, LensEvidence


def extract_all_evidence(
    channel_results: list[ChannelResult],
) -> list[ConvergenceResult]:
    """Extract LensEvidence from channel results and aggregate.

    Pulls available data from channel metrics and findings, adapts through
    the appropriate lens adapters, and runs the convergence aggregator.
    """
    all_evidence: list[LensEvidence] = []

    for cr in channel_results:
        metrics = cr.metrics or {}

        # Structure channel: fan-in data
        fan_in_data = metrics.get("_module_fan_in")
        if fan_in_data:
            all_evidence.extend(adapt_fan_in(fan_in_data))

        # Structure channel: cohesion data
        cohesion_data = metrics.get("cohesion")
        if cohesion_data:
            all_evidence.extend(adapt_cohesion(cohesion_data))

        # Mutation channel: purity data
        purity_data = metrics.get("purity_profile")
        if purity_data:
            all_evidence.extend(adapt_purity(purity_data))

        # Mutation channel: survival data
        mutation_data = metrics.get("mutation_survival")
        if mutation_data:
            all_evidence.extend(adapt_mutation(mutation_data))

        # Cross-channel coherence findings
        if cr.channel == "coherence" and cr.findings:
            coh_findings = [
                f for f in cr.findings if getattr(f, "kind", "").startswith("COH")
            ]
            if coh_findings:
                all_evidence.extend(adapt_cross_channel(coh_findings))

    if not all_evidence:
        return []

    return aggregate(all_evidence)


def convergence_to_metrics(results: list[ConvergenceResult]) -> dict:
    """Convert convergence results to a metrics dict for embedding in ChannelResult."""
    return {
        "total_targets": len(results),
        "actionable_extract": sum(
            1 for r in results if r.actionability.value == "extract"
        ),
        "actionable_split": sum(1 for r in results if r.actionability.value == "split"),
        "top_targets": [r.to_dict() for r in results[:5]],
    }


def enrich_decomposition_candidates(
    candidates: list[DecompositionCandidate],
    convergence: list[ConvergenceResult],
) -> list[DecompositionCandidate]:
    """Enrich decomposition candidates with convergence data.

    If a convergence result matches a candidate's target, boost confidence
    and add convergence evidence.
    """
    conv_by_target: dict[str, ConvergenceResult] = {cr.target: cr for cr in convergence}

    for candidate in candidates:
        cr = conv_by_target.get(candidate.function_id)
        if cr is None:
            continue

        # Boost confidence based on convergence net confidence
        boost = cr.net_confidence * 0.15
        candidate.confidence = min(candidate.confidence + boost, 0.99)

        # Add convergence evidence
        lens_names = [lk.value for lk in cr.supporting_lenses]
        candidate.evidence.append(f"convergence:{','.join(lens_names)}")

        # Upgrade actionability if convergence is stronger
        if cr.actionability.value == "extract" and candidate.actionability != "extract":
            candidate.actionability = "extract"

    return sorted(candidates, key=lambda c: c.confidence, reverse=True)


def extract_file_evidence(
    channel_results: list[ChannelResult],
) -> list[ConvergenceResult]:
    """Extract file-level evidence from channel results and aggregate.

    Pulls _module_fan_in, _file_cohesion from structure channel metrics.
    Falls back to cohesion from lint channel findings evidence if structure
    channel data unavailable. Uses cochange/import_tracing if available.
    """
    all_evidence: list[LensEvidence] = []
    cohesion_map: dict = {}
    split_proposals_map: dict = {}

    file_cohesion_data: dict | None = None
    fan_in_data: dict | None = None
    cochange_data: dict | None = None
    import_tracing_data: dict | None = None

    for cr in channel_results:
        metrics = cr.metrics or {}

        if cr.channel == "structure":
            file_cohesion_data = metrics.get("_file_cohesion")
            fan_in_data = metrics.get("_module_fan_in")
            import_tracing_data = metrics.get("_import_tracing")
            cochange_data = metrics.get("_cochange")

        # Fallback: lint channel may carry cohesion evidence
        if cr.channel == "lint" and file_cohesion_data is None:
            lint_cohesion = metrics.get("_file_cohesion")
            if lint_cohesion:
                file_cohesion_data = lint_cohesion

    # Cohesion lens (file-level)
    if file_cohesion_data:
        all_evidence.extend(adapt_cohesion_file(file_cohesion_data))
        for fp, info in file_cohesion_data.items():
            cohesion_map[fp] = info
            proposals = info.get("split_proposals", [])
            if proposals:
                split_proposals_map[fp] = proposals

    # Fan-in lens (file-level)
    if fan_in_data:
        all_evidence.extend(adapt_fan_in_file(fan_in_data))

    # Co-change lens (file-level, per target)
    if cochange_data and file_cohesion_data:
        for filepath in file_cohesion_data:
            all_evidence.extend(adapt_cochange_file(cochange_data, filepath))

    # Import tracing lens (file-level)
    if import_tracing_data:
        all_evidence.extend(adapt_import_weight_file(import_tracing_data))

    if not all_evidence:
        return []

    return aggregate_file(all_evidence, cohesion_map, split_proposals_map)


def file_convergence_to_metrics(results: list[ConvergenceResult]) -> dict:
    """Convert file convergence results to metrics dict.

    Returns {total_files, actionable_split, actionable_investigate, top_files}.
    """
    return {
        "total_files": len(results),
        "actionable_split": sum(1 for r in results if r.actionability.value == "split"),
        "actionable_investigate": sum(
            1 for r in results if r.actionability.value == "investigate"
        ),
        "top_files": [r.to_dict() for r in results[:5]],
    }
