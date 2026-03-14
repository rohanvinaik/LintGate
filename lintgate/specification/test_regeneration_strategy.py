"""Test regeneration strategy classifier.

Classifies functions into the platonic terminal strategies:
exclude_mutation, already_good, manual_contract, auto_generate_unit,
and needs_decomposition. Composes evidence from spec analysis,
mutation cache, and AST signals.
"""

from __future__ import annotations

import os
import time

from ._regeneration_types import (
    ClassificationResult,
    ExistingTestAction,
    FunctionEvidence,
    MutationEvidence,
    RebuildManifest,
    SpecEvidence,
    Strategy,
    load_manifest,
    write_manifest,
)
from .decomposition_evidence import (
    DecompositionRecommendation,
    evaluate_decomposition,
)

# Re-export types for backward compatibility
__all__ = [
    "Strategy",
    "ExistingTestAction",
    "SpecEvidence",
    "MutationEvidence",
    "FunctionEvidence",
    "ClassificationResult",
    "RebuildManifest",
    "write_manifest",
    "load_manifest",
    "classify_function",
    "build_evidence",
    "build_manifest",
]

_ENTRYPOINT_NAME_PATTERNS = frozenset(
    {
        "main",
        "__main__",
        "cli",
        "run",
        "entry",
    }
)
_HIGH_RISK_NAME_FRAGMENTS = frozenset(
    {
        "hook",
        "register",
        "bootstrap",
        "posttooluse",
        "setup",
        "teardown",
        "conftest",
    }
)
_SYSTEM_SURFACE_NAME_FRAGMENTS = frozenset(
    {
        "cli",
        "main",
        "hook",
        "register",
        "bootstrap",
        "posttooluse",
        "pretooluse",
        "callback",
    }
)
_ARTIFACT_STATES = frozenset(
    {
        "DISCOVERY_ARTIFACT",
        "TESTS_LINKED_ZERO_KILLS",
        "DISCOVERY_WEAK_LINKAGE",
        "MOCK_BOUNDARY_ARTIFACT",
    }
)
_PHASE_WEIGHTS: dict[str, float] = {
    "bulk": 0.6,
    "transition": 0.8,
    "tail": 0.95,
    "complete": 1.0,
}


def _is_entrypoint_surface(func_key: str) -> bool:
    """Check if the function name suggests an entrypoint/glue surface."""
    parts = func_key.rsplit("::", 1)
    name = parts[-1].lower() if parts else ""
    return name.rsplit(".", 1)[-1] in _ENTRYPOINT_NAME_PATTERNS


def _has_high_risk_name(func_key: str) -> bool:
    """Check if the function name contains high-risk fragments."""
    lower = func_key.lower()
    return any(frag in lower for frag in _HIGH_RISK_NAME_FRAGMENTS)


def _has_system_surface_name(func_key: str) -> bool:
    """Check if the function name suggests system-level surface."""
    lower = func_key.lower()
    return any(frag in lower for frag in _SYSTEM_SURFACE_NAME_FRAGMENTS)


def _has_discovery_artifact(evidence: FunctionEvidence) -> bool:
    """Treat discovery truth labels as authoritative, not just raw state strings."""
    return (
        evidence.discovery_state in _ARTIFACT_STATES
        or evidence.survival_interpretation == "DISCOVERY_ARTIFACT"
        or evidence.mutation_truth_label == "DISCOVERY_ARTIFACT"
    )


def _compute_confidence(evidence: FunctionEvidence) -> float:
    """confidence = min(topology_confidence, 1.0 - survival_rate) * phase_weight"""
    topology_conf = 1.0
    if evidence.topology_state and evidence.topology_state != "NORMAL":
        topology_conf = 0.5
    if _has_discovery_artifact(evidence):
        topology_conf = 0.2

    mutation_conf = 1.0 - evidence.survival_rate
    phase_weight = _PHASE_WEIGHTS.get(evidence.phase, 0.6)

    return min(topology_conf, mutation_conf) * phase_weight


def _try_exclude(evidence: FunctionEvidence) -> ClassificationResult | None:
    """Tier 1: hard exclusions (artifact discovery, entrypoint surfaces)."""
    func_key = evidence.function_key
    if _has_discovery_artifact(evidence):
        return ClassificationResult(
            function_key=func_key,
            strategy=Strategy.EXCLUDE_MUTATION,
            existing_test_action=ExistingTestAction.PRESERVE,
            target_test_file="",
            confidence=0.0,
            reason_codes=["discovery_artifact"],
            evidence=evidence,
        )
    if _is_entrypoint_surface(func_key):
        return ClassificationResult(
            function_key=func_key,
            strategy=Strategy.EXCLUDE_MUTATION,
            existing_test_action=ExistingTestAction.PRESERVE,
            target_test_file="",
            confidence=0.0,
            reason_codes=["entrypoint_surface"],
            evidence=evidence,
        )
    return None


def _try_preserve(evidence: FunctionEvidence) -> ClassificationResult | None:
    """Tier 2: already-good (system surface or high-confidence local coverage)."""
    func_key = evidence.function_key
    has_integration = len(evidence.covering_tests) >= 3 and _has_system_surface_name(func_key)
    high_local_confidence = (
        evidence.survival_interpretation == "MEANINGFUL"
        and evidence.tests_loaded > 0
        and evidence.survival_rate <= 0.1
        and evidence.specification_level >= 0.7
    )
    if not has_integration and not high_local_confidence:
        return None
    reasons = ["system_surface", "integration_coverage"] if has_integration else ["already_good"]
    return ClassificationResult(
        function_key=func_key,
        strategy=Strategy.ALREADY_GOOD,
        existing_test_action=ExistingTestAction.PRESERVE,
        target_test_file="",
        confidence=0.9,
        reason_codes=reasons,
        evidence=evidence,
    )


def _try_decompose(evidence: FunctionEvidence) -> ClassificationResult | None:
    """Tier 3: needs_decomposition when mutation and structure agree."""
    if _has_discovery_artifact(evidence) or evidence.topology_state != "NORMAL":
        return None
    verdict = evaluate_decomposition(
        function_key=evidence.function_key,
        surviving_categories=evidence.mutation.surviving_categories,
        mutation_cache_entry={
            "survival_rate": evidence.survival_rate,
        },
        spec_data={
            "sigma": evidence.sigma_upper_bound,
            "regime": evidence.regime,
            "specification_level": evidence.specification_level,
        },
        composition_gamma=evidence.spec.composition_gamma,
        topology_state=evidence.topology_state,
    )
    if verdict.recommendation != DecompositionRecommendation.EXTRACT_BOUNDARY:
        return None
    return ClassificationResult(
        function_key=evidence.function_key,
        strategy=Strategy.NEEDS_DECOMPOSITION,
        existing_test_action=ExistingTestAction.QUARANTINE_ONLY,
        target_test_file="",
        confidence=verdict.cross_lens_score,
        reason_codes=["multi_lens_decomposition"],
        evidence=evidence,
        generation_mode="decomposition",
        manual_review_required=True,
    )


def _try_auto_generate(evidence: FunctionEvidence) -> ClassificationResult | None:
    """Tier 4: auto-generate-unit (deterministic, meaningful signal)."""
    topology_ok = evidence.topology_state in ("NORMAL", "")
    mutation_meaningful = evidence.survival_interpretation in ("MEANINGFUL", "")
    not_artifact = not _has_discovery_artifact(evidence)
    has_signal = evidence.sigma_upper_bound > 0 or evidence.is_pure
    is_local = evidence.is_pure or (not evidence.has_side_effects and not evidence.is_stateful)

    if not (topology_ok and mutation_meaningful and not_artifact and has_signal and is_local):
        return None

    reasons = ["pure_or_local"]
    if evidence.survival_interpretation == "MEANINGFUL":
        reasons.append("mutation_meaningful")
    if not_artifact:
        reasons.append("no_topology_artifact")

    confidence = _compute_confidence(evidence)
    target_file = _compute_target_test_file(evidence.source_file)
    return ClassificationResult(
        function_key=evidence.function_key,
        strategy=Strategy.AUTO_GENERATE_UNIT,
        existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
        target_test_file=target_file,
        confidence=confidence,
        reason_codes=reasons,
        evidence=evidence,
        generation_mode="spec+mutation+inputs",
        manual_review_required=confidence < 0.5,
    )


def _fallback_manual(evidence: FunctionEvidence) -> ClassificationResult:
    """Tier 5: manual-contract (meaningful but mutation-resistant)."""
    reasons: list[str] = []
    if evidence.is_stateful or evidence.has_side_effects:
        reasons.append("stateful_or_side_effects")
    if evidence.topology_state and evidence.topology_state != "NORMAL":
        reasons.append("topology_abnormal")
    if _has_high_risk_name(evidence.function_key):
        reasons.append("high_risk_name")
    if not reasons:
        reasons.append("conservative_default")
    return ClassificationResult(
        function_key=evidence.function_key,
        strategy=Strategy.MANUAL_CONTRACT,
        existing_test_action=ExistingTestAction.QUARANTINE_ONLY,
        target_test_file="",
        confidence=0.3,
        reason_codes=reasons,
        evidence=evidence,
    )


def classify_function(evidence: FunctionEvidence) -> ClassificationResult:
    """Classify a single function into a regeneration strategy."""
    return (
        _try_exclude(evidence)
        or _try_preserve(evidence)
        or _try_decompose(evidence)
        or _try_auto_generate(evidence)
        or _fallback_manual(evidence)
    )


def _compute_target_test_file(source_file: str) -> str:
    """lintgate/foo/bar.py -> tests/generated/test_lintgate_foo_bar.py

    Uses the full relative path (underscored) to avoid collisions when
    different source dirs contain files with the same basename.
    """
    stem = source_file.replace(os.sep, "/")
    if stem.endswith(".py"):
        stem = stem[:-3]
    # lintgate/foo/bar -> lintgate_foo_bar
    safe = stem.replace("/", "_").replace(".", "_")
    return f"tests/generated/test_{safe}.py"


def build_evidence(
    func_key: str,
    source_file: str,
    spec_data: dict | None = None,
    mutation_data: dict | None = None,
) -> FunctionEvidence:
    """Build FunctionEvidence from spec analysis and mutation cache data."""
    spec_ev = SpecEvidence()
    if spec_data:
        g = spec_data.get
        spec_ev = SpecEvidence(
            specification_level=g("specification_level", 0.0),
            # file_analyzer emits "sigma", FunctionSpec.to_dict() emits "estimated_sigma"
            sigma_upper_bound=g("estimated_sigma", g("sigma", 0)),
            regime=g("regime", "unknown"),
            phase=g("phase", "bulk"),
            is_pure=g("is_pure", False),
            is_stateful=g("is_stateful", False),
            has_side_effects=g("has_side_effects", False),
            testability_score=g("testability_score", 1.0),
            composition_gamma=g("composition_gamma", 0.0),
        )
    mut_ev = MutationEvidence()
    if mutation_data:
        g = mutation_data.get
        mut_ev = MutationEvidence(
            discovery_state=g("discovery_state", ""),
            topology_state=g("topology_state", ""),
            survival_interpretation=g("survival_interpretation", ""),
            survival_rate=g("survival_rate", 1.0),
            tests_loaded=g("tests_loaded", 0),
            surviving_categories=[
                c["category"]
                for c in g("per_category", [])
                if isinstance(c, dict) and c.get("survived", 0) > 0 and c.get("category")
            ],
            mutation_truth_label=g("mutation_truth_label", ""),
        )
    return FunctionEvidence(
        function_key=func_key,
        source_file=source_file,
        spec=spec_ev,
        mutation=mut_ev,
        covering_tests=spec_data.get("covering_tests", []) if spec_data else [],
        assertion_count=spec_data.get("assertion_count", 0) if spec_data else 0,
    )


def _collect_file_sets(
    classifications: list[ClassificationResult],
) -> tuple[set[str], set[str]]:
    """Partition covering test files into preserve and quarantine sets."""
    preserve: set[str] = set()
    quarantine: set[str] = set()
    for c in classifications:
        py_tests = [t for t in c.evidence.covering_tests if t.endswith(".py")]
        if c.existing_test_action == ExistingTestAction.PRESERVE:
            preserve.update(py_tests)
        elif c.existing_test_action in (
            ExistingTestAction.QUARANTINE_REPLACE,
            ExistingTestAction.QUARANTINE_ONLY,
        ):
            quarantine.update(py_tests)
    return preserve, quarantine


def _apply_preserve_globs(
    preserve_files: set[str],
    project_root: str,
    globs: list[str],
) -> None:
    """Add test files matching preserve globs to the preserve set."""
    import fnmatch

    test_dir = os.path.join(project_root, "tests")
    if not os.path.isdir(test_dir):
        return
    for f in os.listdir(test_dir):
        for pattern in globs:
            if fnmatch.fnmatch(f, pattern):
                preserve_files.add(f"tests/{f}")


def build_manifest(
    project_root: str,
    classifications: list[ClassificationResult],
    preserve_globs: list[str] | None = None,
) -> RebuildManifest:
    """Build a rebuild manifest from classification results."""
    preserve_files, quarantine_files = _collect_file_sets(classifications)

    if preserve_globs:
        _apply_preserve_globs(preserve_files, project_root, preserve_globs)

    quarantine_files -= preserve_files

    return RebuildManifest(
        version=1,
        project_root=project_root,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        functions=classifications,
        preserve_test_files=sorted(preserve_files),
        quarantine_test_files=sorted(quarantine_files),
    )
