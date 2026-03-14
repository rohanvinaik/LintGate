"""Target selection and file assessment for the platonic workflow."""

from __future__ import annotations

import os
from typing import Any

from lintgate.specification.test_regeneration_strategy import (
    Strategy,
    build_evidence,
    build_manifest,
    classify_function,
    write_manifest,
)

_HEADROOM_SPEC_LEVEL_CEILING = 0.65
_HEADROOM_TAIL_PHASES = frozenset({"tail", "complete"})


def _has_automation_headroom(assessment: dict[str, Any]) -> bool:
    """Check whether a file has enough specification headroom for auto-generation.

    Returns False when ALL auto_targets are in tail/complete phase with
    spec_level above the ceiling — meaning existing tests already cover
    most of the specification space and auto-generation will just produce
    ``existing_tests_adequate`` skips.
    """
    auto_targets = assessment.get("auto_targets", [])
    if not auto_targets:
        return True  # no targets = let other filters decide

    spec_result = assessment.get("spec_result")
    if spec_result is None:
        return True  # can't assess without spec data

    blocked = 0
    for target in auto_targets:
        func_key = target.function_key
        func_data = spec_result.functions.get(func_key)
        if func_data is None:
            continue
        phase = getattr(func_data, "phase", "") or ""
        spec_level = getattr(func_data, "spec_level", 0.0)
        if phase in _HEADROOM_TAIL_PHASES and spec_level >= _HEADROOM_SPEC_LEVEL_CEILING:
            blocked += 1

    return blocked < len(auto_targets)


_ARTIFACT_DISCOVERY_STATES = frozenset(
    {
        "DISCOVERY_ARTIFACT",
        "TESTS_LINKED_ZERO_KILLS",
        "DISCOVERY_WEAK_LINKAGE",
        "MOCK_BOUNDARY_ARTIFACT",
        "NO_TEST_FILES",
        "TEST_FILES_FOUND_NONE_LINKED",
        "DISCOVERY_IMPORT_FAILED",
    }
)


def assess_file(
    project_root: str,
    rel_file: str,
    *,
    preserve_globs: list[str] | None = None,
    write_file_manifest: bool = False,
) -> dict[str, Any]:
    """Assess one file for platonic workflow routing."""
    from lintgate.specification.file_analyzer import (
        _load_mutation_cache as load_file_mutation_cache,
    )
    from lintgate.specification.file_analyzer import analyze_file

    full_path = rel_file if os.path.isabs(rel_file) else os.path.join(project_root, rel_file)
    spec_result = analyze_file(full_path, project_root, enrich=True)
    if spec_result.error:
        return {
            "file": rel_file,
            "error": spec_result.error,
            "classifications": [],
            "auto_targets": [],
            "decompose_targets": [],
            "majority_hard_veto": False,
            "manifest_path": "",
            "summary": {"total_functions": 0},
        }

    mutation_cache = load_file_mutation_cache(project_root, full_path) or {}
    classifications = []
    for func_key, func_data in spec_result.functions.items():
        mut_data = mutation_cache.get(func_key)
        evidence = build_evidence(func_key, rel_file, func_data, mut_data)
        classifications.append(classify_function(evidence))

    classifications.sort(key=lambda c: (-c.confidence, c.function_key))
    auto_targets = [c for c in classifications if c.strategy == Strategy.AUTO_GENERATE_UNIT]
    decompose_targets = [c for c in classifications if c.strategy == Strategy.NEEDS_DECOMPOSITION]
    vetoed = [
        c
        for c in classifications
        if c.strategy == Strategy.EXCLUDE_MUTATION
        or c.evidence.discovery_state in _ARTIFACT_DISCOVERY_STATES
        or c.evidence.survival_interpretation == "DISCOVERY_ARTIFACT"
        or c.evidence.mutation_truth_label == "DISCOVERY_ARTIFACT"
        or c.evidence.topology_state == "MOCK_BOUNDARY_DOMINANT"
    ]
    majority_hard_veto = bool(classifications) and len(vetoed) * 2 > len(classifications)

    manifest_path = ""
    if write_file_manifest:
        manifest = build_manifest(project_root, classifications, preserve_globs)
        manifest_path = write_manifest(manifest, project_root)

    strategy_counts: dict[str, int] = {}
    for c in classifications:
        key = c.strategy.value
        strategy_counts[key] = strategy_counts.get(key, 0) + 1

    return {
        "file": rel_file,
        "spec_result": spec_result,
        "classifications": classifications,
        "auto_targets": auto_targets,
        "decompose_targets": decompose_targets,
        "majority_hard_veto": majority_hard_veto,
        "manifest_path": manifest_path,
        "primary_target": (
            auto_targets[0].function_key
            if auto_targets
            else (decompose_targets[0].function_key if decompose_targets else "")
        ),
        "summary": {
            "total_functions": len(classifications),
            "strategy_distribution": strategy_counts,
            "auto_generate_unit": len(auto_targets),
            "needs_decomposition": len(decompose_targets),
            "majority_hard_veto": majority_hard_veto,
        },
    }


def select_project_target(
    project_root: str,
    *,
    max_files: int = 5,
    preserve_globs: list[str] | None = None,
) -> dict[str, Any]:
    """Pick the deterministic first platonic target for a project."""
    from lintgate.specification.project_rollup import rollup_project

    rollup = rollup_project(
        project_root,
        use_cache=True,
        analyze_uncached=True,
        include_tests=False,
    )

    inspected = 0
    for hotspot in rollup.hotspot_files[:max_files]:
        rel_file = hotspot.get("file", "")
        if not rel_file:
            continue
        inspected += 1
        assessment = assess_file(
            project_root,
            rel_file,
            preserve_globs=preserve_globs,
            write_file_manifest=False,
        )
        if assessment.get("error"):
            continue
        if assessment["majority_hard_veto"]:
            continue
        if not (assessment["auto_targets"] or assessment["decompose_targets"]):
            continue
        if assessment["auto_targets"] and not _has_automation_headroom(assessment):
            continue
        return {
            "rollup": rollup,
            "selected_file": rel_file,
            "assessment": assessment,
            "files_inspected": inspected,
        }

    return {
        "rollup": rollup,
        "selected_file": "",
        "assessment": None,
        "files_inspected": inspected,
    }
