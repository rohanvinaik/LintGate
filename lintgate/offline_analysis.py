"""Offline analysis engine — comprehensive LLM-free project analysis.

Runs all LintGate analysis channels and produces a single portable JSON
artifact with a prioritized, dependency-ordered action plan. Designed to
run on Google Colab or any Python environment without an LLM.

The output is a complete project diagnosis that an LLM coding agent can
consume to implement fixes systematically.

Usage (standalone):
    from lintgate.offline_analysis import run_full_analysis
    result = run_full_analysis("/path/to/project")
    # result is a dict serializable to JSON

Usage (Colab):
    Generated via colab_sweep_generate(mode="full_analysis")
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from typing import Any

# ── Result types ──────────────────────────────────────────────────────
from .offline_action_plan import (
    ActionItem,  # noqa: E402
    _build_action_plan,
)
from .offline_runners import (
    _analyze_project_structure,
    _analyze_test_coverage,
    _detect_src_dirs,
    _load_mutation_cache,
    _load_prescriptive_state,
    _run_composition_analysis,
    _run_lint_analysis,
    _run_performance_analysis,
    _run_spec_analysis,
)


def run_full_analysis(
    project_root: str,
    *,
    src_dirs: list[str] | None = None,
    include_mutation: bool = True,
    mutation_budget_ms: int = 500,
    max_files: int = 500,
) -> dict[str, Any]:
    """Run comprehensive offline analysis and produce a portable artifact.

    This is the main entry point. Runs all available analysis channels
    and produces a single JSON-serializable dict containing:
    - Project overview (structure, LOC, packages)
    - Lint findings (grouped and prioritized)
    - Specification analysis (per-function sigma, regime, phase)
    - Mutation profiles (per-function kill rates, surviving categories)
    - Composition analysis (cross-module gaps)
    - Performance analysis (purity, algebraic properties)
    - Prescriptive spec state (existing contracts)
    - Action plan (prioritized, dependency-ordered fix list)
    """
    start = time.monotonic()
    project_root = os.path.abspath(project_root)

    if src_dirs is None:
        src_dirs = _detect_src_dirs(project_root)

    result: dict[str, Any] = {
        "schema_version": "1",
        "project": _analyze_project_structure(project_root, src_dirs, max_files),
        "timestamp": time.time(),
        "analysis_config": {
            "src_dirs": src_dirs,
            "include_mutation": include_mutation,
            "mutation_budget_ms": mutation_budget_ms,
        },
    }

    py_files = result["project"]["python_files"]

    # ── Lint analysis ─────────────────────────────────────────────
    result["lint"] = _run_lint_analysis(project_root, py_files)

    # ── Specification analysis ────────────────────────────────────
    result["specification"] = _run_spec_analysis(project_root, py_files)

    # ── Mutation analysis (optional, slow) ────────────────────────
    if include_mutation:
        result["mutation"] = _load_mutation_cache(project_root)
    else:
        result["mutation"] = {"skipped": True, "reason": "include_mutation=False"}

    # ── Composition analysis ──────────────────────────────────────
    result["composition"] = _run_composition_analysis(project_root, py_files)

    # ── Performance analysis ──────────────────────────────────────
    result["performance"] = _run_performance_analysis(project_root, py_files)

    # ── Prescriptive spec state ───────────────────────────────────
    result["prescriptive"] = _load_prescriptive_state(project_root)

    # ── Test coverage mapping ─────────────────────────────────────
    result["test_coverage"] = _analyze_test_coverage(project_root, py_files)

    # ── Action plan (the key output) ──────────────────────────────
    result["action_plan"] = _build_action_plan(result)

    result["elapsed_s"] = round(time.monotonic() - start, 2)

    return result


# ── ControlPlane analysis ──────────────────────────────────────────────


def run_controlplane_analysis(
    project_root: str,
    *,
    src_dirs: list[str] | None = None,
    max_files: int = 500,
) -> dict[str, Any]:
    """Run the full ControlPlane supervision mesh offline.

    Executes all 6 channels (lint, tests, deps, git, behavior, structure)
    and produces a portable artifact with findings, coherence diagnosis,
    and repair suggestions.
    """
    start = time.monotonic()
    project_root = os.path.abspath(project_root)
    if src_dirs is None:
        src_dirs = _detect_src_dirs(project_root)

    result: dict[str, Any] = {
        "schema_version": "1",
        "mode": "controlplane",
        "project": _analyze_project_structure(project_root, src_dirs, max_files),
        "timestamp": time.time(),
    }

    # Run the mesh
    try:
        from lintgate.controlplane.runner import run_mesh
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent

        py_files = [os.path.join(project_root, f) for f in result["project"]["python_files"]]
        event = SupervisionEvent(
            project_root=project_root,
            surface="mcp",
            files_changed=py_files[:10],
        )
        event.context["python_files"] = py_files
        config = ControlPlaneConfig(enabled=True)
        mesh = run_mesh(event, config)

        # Extract per-channel results
        channels: list[dict[str, Any]] = []
        all_findings: list[dict[str, Any]] = []
        for cr in mesh.channel_results:
            channel_data: dict[str, Any] = {
                "channel": cr.channel,
                "status": cr.status,
                "severity": cr.severity,
                "finding_count": len(cr.findings),
                "duration_ms": round(cr.duration_ms, 1),
                "metrics": cr.metrics,
            }
            channels.append(channel_data)
            for f in cr.findings:
                all_findings.append(
                    {
                        "channel": cr.channel,
                        "kind": f.kind,
                        "message": f.message,
                        "file": f.file,
                        "line": f.line,
                        "severity": f.severity,
                        "confidence": f.confidence,
                        "fixable": f.fixable,
                    }
                )

        result["coherence"] = {
            "state": mesh.coherence.state,
            "summary": mesh.coherence.summary,
            "recommended_action": mesh.coherence.recommended_action,
            "loud_channels": mesh.coherence.loud_channels,
            "silent_channels": mesh.coherence.silent_channels,
            "confidence": mesh.coherence.confidence,
        }
        result["channels"] = channels
        result["findings"] = all_findings
        result["summary"] = {
            "total_findings": len(all_findings),
            "blocking": sum(1 for f in all_findings if f["severity"] == "blocking"),
            "warnings": sum(1 for f in all_findings if f["severity"] == "warning"),
            "channels_run": len(channels),
            "channels_failed": sum(1 for c in channels if c["status"] == "fail"),
        }
    except Exception as e:
        result["error"] = str(e)

    # Also run lint analysis (fast, always available)
    result["lint"] = _run_lint_analysis(project_root, result["project"]["python_files"])

    result["action_plan"] = _build_action_plan(result)
    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


# ── Decomposition analysis ────────────────────────────────────────────


def run_decomposition_analysis(
    project_root: str,
    *,
    src_dirs: list[str] | None = None,
    max_files: int = 500,
) -> dict[str, Any]:
    """Run decomposition and extraction guidance offline.

    Analyzes cross-module composition gaps, identifies extraction candidates,
    and produces a dependency-ordered refactoring plan.
    """
    start = time.monotonic()
    project_root = os.path.abspath(project_root)
    if src_dirs is None:
        src_dirs = _detect_src_dirs(project_root)

    result: dict[str, Any] = {
        "schema_version": "1",
        "mode": "decomposition",
        "project": _analyze_project_structure(project_root, src_dirs, max_files),
        "timestamp": time.time(),
    }

    py_files = result["project"]["python_files"]

    # Composition analysis (cross-module gaps)
    result["composition"] = _run_composition_analysis(project_root, py_files)

    # Performance analysis (extraction safety, purity tiers)
    result["performance"] = _run_performance_analysis(project_root, py_files)

    # Specification analysis (sigma hotspots indicate complexity)
    result["specification"] = _run_spec_analysis(project_root, py_files)

    # Mutation cache (surviving categories indicate entangled behavior)
    result["mutation"] = _load_mutation_cache(project_root)

    # Build decomposition-specific action plan
    actions: list[ActionItem] = []
    rank = 0

    # High fan-out functions → decomposition candidates
    composition = result.get("composition", {})
    for entry in composition.get("high_fan_out", [])[:10]:
        rank += 1
        actions.append(
            ActionItem(
                rank=rank,
                priority="P1_critical",
                category="high_fan_out",
                file="",
                function=entry.get("function", ""),
                action=f"Decompose '{entry['function']}' (fan_out={entry['fan_out']}). "
                f"Use `extraction_plan(path)` for dependency-ordered extraction guidance.",
                rationale="High fan-out indicates an orchestrator function that should be split.",
                estimated_effort="large",
                evidence=entry,
            )
        )

    # High fan-in functions → stable API boundaries (don't break)
    for entry in composition.get("high_fan_in", [])[:5]:
        rank += 1
        actions.append(
            ActionItem(
                rank=rank,
                priority="P2_important",
                category="stable_api",
                file="",
                function=entry.get("function", ""),
                action=f"'{entry['function']}' has {entry['fan_in']} callers. "
                f"Preserve this interface during refactoring.",
                rationale="High fan-in means many callers depend on this — changes cascade widely.",
                estimated_effort="small",
                evidence=entry,
            )
        )

    # Under-specified functions with multiple mutation categories → entangled
    mutation = result.get("mutation", {})
    if isinstance(mutation, dict) and mutation.get("cached"):
        for profile in mutation.get("profiles", [])[:10]:
            cats = profile.get("surviving_categories", [])
            if len(cats) >= 2:
                rank += 1
                actions.append(
                    ActionItem(
                        rank=rank,
                        priority="P2_important",
                        category="entangled_behavior",
                        file=profile.get("function_key", "").split("::")[0],
                        function=profile.get("function_key", ""),
                        action=f"'{profile['function_key']}' has {len(cats)} surviving mutation categories "
                        f"({', '.join(cats)}). Decompose before writing more tests.",
                        rationale="Multiple surviving categories indicate entangled responsibilities.",
                        estimated_effort="medium",
                        evidence=profile,
                    )
                )

    result["action_plan"] = [asdict(a) for a in actions]
    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


# ── Platonic convergence analysis ─────────────────────────────────────


def run_platonic_analysis(
    project_root: str,
    *,
    src_dirs: list[str] | None = None,
    include_mutation: bool = True,
    max_files: int = 200,
) -> dict[str, Any]:
    """Run platonic convergence analysis offline.

    For each source file: spec analysis + mutation profiling + prescriptions.
    Identifies which files are closest to/furthest from the platonic ideal
    and produces a convergence roadmap.
    """
    start = time.monotonic()
    project_root = os.path.abspath(project_root)
    if src_dirs is None:
        src_dirs = _detect_src_dirs(project_root)

    result: dict[str, Any] = {
        "schema_version": "1",
        "mode": "platonic",
        "project": _analyze_project_structure(project_root, src_dirs, max_files),
        "timestamp": time.time(),
    }

    py_files = result["project"]["python_files"]

    # Spec analysis (per-function sigma, regime, phase)
    result["specification"] = _run_spec_analysis(project_root, py_files)

    # Mutation profiles
    if include_mutation:
        result["mutation"] = _load_mutation_cache(project_root)
    else:
        result["mutation"] = {"skipped": True}

    # Test coverage
    result["test_coverage"] = _analyze_test_coverage(project_root, py_files)

    # Performance (purity tiers for convergence targeting)
    result["performance"] = _run_performance_analysis(project_root, py_files)

    # Build platonic convergence roadmap
    roadmap: list[dict[str, Any]] = []
    spec = result.get("specification", {})
    mutation = result.get("mutation", {})
    coverage = result.get("test_coverage", {})

    # Rank files by distance from platonic ideal
    file_scores: dict[str, dict[str, Any]] = {}

    # Score from spec: under-specified functions
    for func in spec.get("hotspot_functions", []):
        source = func.get("source_file", "")
        if source not in file_scores:
            file_scores[source] = {
                "file": source,
                "spec_gap": 0,
                "mutation_survival": 0,
                "no_tests": False,
            }
        file_scores[source]["spec_gap"] += func.get("estimated_sigma", 0) - func.get(
            "assertion_count", 0
        )

    # Score from mutation: surviving categories
    if isinstance(mutation, dict) and mutation.get("cached"):
        for profile in mutation.get("profiles", []):
            fk = profile.get("function_key", "")
            source = fk.split("::")[0] if "::" in fk else ""
            if source and source not in file_scores:
                file_scores[source] = {
                    "file": source,
                    "spec_gap": 0,
                    "mutation_survival": 0,
                    "no_tests": False,
                }
            if source:
                file_scores[source]["mutation_survival"] += profile.get("survived", 0)

    # Score from coverage: missing test files
    for entry in coverage.get("no_test_files", []):
        source = entry.get("file", "")
        if source not in file_scores:
            file_scores[source] = {
                "file": source,
                "spec_gap": 0,
                "mutation_survival": 0,
                "no_tests": True,
            }
        file_scores[source]["no_tests"] = True

    # Rank by total distance from ideal
    for fs in file_scores.values():
        fs["distance"] = (
            fs["spec_gap"] * 2 + fs["mutation_survival"] * 3 + (100 if fs["no_tests"] else 0)
        )
    roadmap = sorted(file_scores.values(), key=lambda x: -x["distance"])[:20]

    result["convergence_roadmap"] = roadmap

    # Build action plan
    actions: list[ActionItem] = []
    for i, entry in enumerate(roadmap[:15], 1):
        file = entry["file"]
        if entry["no_tests"]:
            actions.append(
                ActionItem(
                    rank=i,
                    priority="P1_critical",
                    category="no_tests",
                    file=file,
                    action=f"Create tests for {file}. Use `platonic_converge(path, '{file}')` "
                    f"for automated profile → generate → validate cycle.",
                    rationale=f"Distance from ideal: {entry['distance']} (no tests + spec gap {entry['spec_gap']})",
                    estimated_effort="medium",
                    evidence=entry,
                )
            )
        elif entry["mutation_survival"] > 0:
            actions.append(
                ActionItem(
                    rank=i,
                    priority="P2_important",
                    category="mutation_survival",
                    file=file,
                    action=f"Close mutation gaps in {file} ({entry['mutation_survival']} survivors). "
                    f"Use `platonic_converge(path, '{file}')` to converge.",
                    rationale=f"Distance from ideal: {entry['distance']}",
                    estimated_effort="medium",
                    evidence=entry,
                )
            )
        else:
            actions.append(
                ActionItem(
                    rank=i,
                    priority="P2_important",
                    category="spec_gap",
                    file=file,
                    action=f"Close spec gap in {file} (gap={entry['spec_gap']}). "
                    f"Use `spec_file_prescribe(path, '{file}')` for targeted recommendations.",
                    rationale=f"Distance from ideal: {entry['distance']}",
                    estimated_effort="medium",
                    evidence=entry,
                )
            )

    result["action_plan"] = [asdict(a) for a in actions]
    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


# ── Complete suite (everything) ───────────────────────────────────────


def run_complete_analysis(
    project_root: str,
    *,
    src_dirs: list[str] | None = None,
    include_mutation: bool = True,
    mutation_budget_ms: int = 500,
    max_files: int = 500,
) -> dict[str, Any]:
    """Run the COMPLETE LintGate analysis suite — every available analysis.

    Combines: full analysis + controlplane mesh + decomposition guidance +
    platonic convergence roadmap. This is the most comprehensive artifact
    LintGate can produce.
    """
    start = time.monotonic()
    project_root = os.path.abspath(project_root)
    if src_dirs is None:
        src_dirs = _detect_src_dirs(project_root)

    # Start with the standard full analysis
    result = run_full_analysis(
        project_root,
        src_dirs=src_dirs,
        include_mutation=include_mutation,
        mutation_budget_ms=mutation_budget_ms,
        max_files=max_files,
    )
    result["mode"] = "complete"

    # Add controlplane mesh (may fail gracefully on Colab without full deps)
    try:
        cp = run_controlplane_analysis(project_root, src_dirs=src_dirs, max_files=max_files)
        result["controlplane"] = {
            "coherence": cp.get("coherence"),
            "channels": cp.get("channels"),
            "summary": cp.get("summary"),
        }
    except Exception as e:
        result["controlplane"] = {"error": str(e)}

    # Add decomposition guidance
    try:
        decomp = run_decomposition_analysis(project_root, src_dirs=src_dirs, max_files=max_files)
        result["decomposition"] = {
            "high_fan_out": decomp.get("composition", {}).get("high_fan_out", []),
            "high_fan_in": decomp.get("composition", {}).get("high_fan_in", []),
            "entangled": [
                a
                for a in decomp.get("action_plan", [])
                if a.get("category") == "entangled_behavior"
            ],
        }
    except Exception as e:
        result["decomposition"] = {"error": str(e)}

    # Add platonic convergence roadmap
    try:
        platonic = run_platonic_analysis(
            project_root,
            src_dirs=src_dirs,
            include_mutation=include_mutation,
            max_files=max_files,
        )
        result["convergence_roadmap"] = platonic.get("convergence_roadmap", [])
    except Exception as e:
        result["convergence_roadmap"] = {"error": str(e)}

    # Merge all action plans (deduplicate by file+category)
    seen: set[tuple[str, str]] = set()
    merged_plan: list[dict[str, Any]] = []
    for plan_source in [result.get("action_plan", [])]:
        for action in plan_source:
            key = (action.get("file", ""), action.get("category", ""))
            if key not in seen:
                seen.add(key)
                merged_plan.append(action)
    result["action_plan"] = merged_plan
    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result
