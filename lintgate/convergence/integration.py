"""Integration helpers: extract evidence from ChannelResults and enrich decomposition candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .aggregator import (
    adapt_cochange_file,
    adapt_cohesion,
    adapt_cohesion_file,
    adapt_composition_gap,
    adapt_contract_coverage,
    adapt_cross_channel,
    adapt_fan_in,
    adapt_fan_in_file,
    adapt_import_weight_file,
    adapt_mutation,
    adapt_purity,
    adapt_specification,
    aggregate,
    aggregate_file,
)

if TYPE_CHECKING:
    from lintgate.controlplane.types import ChannelResult

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
        _extract_channel_evidence(cr, all_evidence)

    # Contract coverage: cross-channel publish/consume gap analysis
    contract_evidence = _extract_contract_coverage(channel_results)
    all_evidence.extend(contract_evidence)

    if not all_evidence:
        return []

    return aggregate(all_evidence)


# Metric key → adapter function mapping for extract_all_evidence
_METRIC_ADAPTERS: list[tuple[str, Any]] = []


def _init_metric_adapters() -> list[tuple[str, Any]]:
    """Build the metric key → adapter list (lazy init)."""
    return [
        ("_module_fan_in", adapt_fan_in),
        ("cohesion", adapt_cohesion),
        ("purity_profile", adapt_purity),
        ("mutation_survival", adapt_mutation),
        ("specification_function_list", adapt_specification),
        ("composition_gaps", adapt_composition_gap),
    ]


def _extract_channel_evidence(cr: ChannelResult, out: list[LensEvidence]) -> None:
    """Extract evidence from a single channel result."""
    global _METRIC_ADAPTERS  # noqa: PLW0603
    if not _METRIC_ADAPTERS:
        _METRIC_ADAPTERS = _init_metric_adapters()

    metrics = cr.metrics or {}
    for key, adapter in _METRIC_ADAPTERS:
        data = metrics.get(key)
        if data:
            out.extend(adapter(data))

    # Cross-channel coherence findings
    if cr.channel == "coherence" and cr.findings:
        coh_findings = [f for f in cr.findings if getattr(f, "kind", "").startswith("COH")]
        if coh_findings:
            out.extend(adapt_cross_channel(coh_findings))


def _extract_contract_coverage(
    channel_results: list[ChannelResult],
) -> list[LensEvidence]:
    """Extract contract coverage evidence from cross-channel metric gaps.

    Identifies functions that appear in one channel's published metrics but
    NOT in another channel's published metrics, indicating a contract boundary
    where one channel sees the function but another doesn't.

    Uses schema-aware publish/consume: a function is "consumed" if it appears
    in ANY other channel's function-level metrics. This avoids false positives
    when coherence emits no findings.
    """
    # Collect per-channel function-level targets
    # channel_name -> set of func_keys
    channel_funcs: dict[str, set[str]] = {}

    # Keys that contain per-function dicts
    func_metric_keys = {
        "specification_function_list": "specification",
        "pure_function_list": "performance",
    }

    for cr in channel_results:
        metrics = cr.metrics or {}
        for metric_key, channel_name in func_metric_keys.items():
            data = metrics.get(metric_key)
            if not data:
                continue
            funcs = channel_funcs.setdefault(channel_name, set())
            if isinstance(data, list):
                for item in data:
                    func_key = item.get("function", item.get("name", ""))
                    if func_key:
                        funcs.add(func_key)
            elif isinstance(data, dict):
                funcs.update(data.keys())

    # Need at least 2 channels with function data to detect gaps
    if len(channel_funcs) < 2:
        return []

    # Build published/consumed maps: a function is "published" if it appears
    # in only one channel, "consumed" if it appears in 2+ channels
    all_funcs: dict[str, set[str]] = {}  # func_key -> set of channels
    for channel_name, funcs in channel_funcs.items():
        for func_key in funcs:
            all_funcs.setdefault(func_key, set()).add(channel_name)

    published: dict[str, dict] = {}
    consumed: dict[str, dict] = {}
    for func_key, channels in all_funcs.items():
        if len(channels) == 1:
            ch = next(iter(channels))
            published[func_key] = {"channel": ch, "metric_key": "function_level"}
        else:
            consumed[func_key] = {"channel": ",".join(sorted(channels))}

    if not published:
        return []

    return adapt_contract_coverage(published, consumed)


def convergence_to_metrics(results: list[ConvergenceResult]) -> dict:
    """Convert convergence results to a metrics dict for embedding in ChannelResult."""
    return {
        "total_targets": len(results),
        "actionable_extract": sum(1 for r in results if r.actionability.value == "extract"),
        "actionable_split": sum(1 for r in results if r.actionability.value == "split"),
        "top_targets": [r.to_dict() for r in results[:5]],
    }


def enrich_decomposition_candidates(
    candidates: list[Any],
    convergence: list[ConvergenceResult],
) -> list[Any]:
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
        "actionable_investigate": sum(1 for r in results if r.actionability.value == "investigate"),
        "top_files": [r.to_dict() for r in results[:5]],
    }
