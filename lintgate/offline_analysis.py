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

import ast
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# ── Result types ──────────────────────────────────────────────────────


@dataclass
class ActionItem:
    """A single prioritized fix for the LLM agent to implement."""

    rank: int
    priority: str  # "P0_blocking" | "P1_critical" | "P2_important" | "P3_improve"
    category: str  # "lint_fix" | "type_error" | "missing_test" | "spec_gap" | "mutation_survival" | ...
    file: str
    function: str = ""
    action: str = ""  # Human-readable instruction
    rationale: str = ""  # Why this matters
    depends_on: list[int] = field(default_factory=list)  # rank IDs this depends on
    estimated_effort: str = ""  # "trivial" | "small" | "medium" | "large"
    evidence: dict[str, Any] = field(default_factory=dict)


ActionItem.__test__ = False  # type: ignore[attr-defined]


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


# ── Project structure ─────────────────────────────────────────────────


def _detect_src_dirs(project_root: str) -> list[str]:
    """Auto-detect source directories."""
    candidates = ["src", "lib", "app"]
    dirs: list[str] = []
    for entry in sorted(os.listdir(project_root)):
        if entry.startswith(".") or entry.startswith("_"):
            continue
        full = os.path.join(project_root, entry)
        if not os.path.isdir(full):
            continue
        if entry in ("tests", "test", "docs", "scripts", "node_modules", "venv", ".venv"):
            continue
        # Check if it contains Python files
        has_py = any(f.endswith(".py") for f in os.listdir(full) if os.path.isfile(os.path.join(full, f)))
        has_init = os.path.isfile(os.path.join(full, "__init__.py"))
        if has_py or has_init or entry in candidates:
            dirs.append(entry)
    if not dirs:
        # Fallback: use project root
        dirs = ["."]
    return dirs


def _analyze_project_structure(
    project_root: str, src_dirs: list[str], max_files: int
) -> dict[str, Any]:
    """Collect project metadata and file inventory."""
    py_files: list[str] = []
    test_files: list[str] = []
    total_loc = 0

    for src_dir in src_dirs:
        base = os.path.join(project_root, src_dir) if src_dir != "." else project_root
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, filenames in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, project_root)
                loc = _count_lines(full)
                total_loc += loc
                if fn.startswith("test_") or fn.endswith("_test.py"):
                    test_files.append(rel)
                else:
                    py_files.append(rel)

    # Also scan tests/ directory
    test_dir = os.path.join(project_root, "tests")
    if os.path.isdir(test_dir):
        for fn in sorted(os.listdir(test_dir)):
            if fn.endswith(".py") and fn.startswith("test_"):
                rel = os.path.join("tests", fn)
                if rel not in test_files:
                    test_files.append(rel)

    if len(py_files) > max_files:
        py_files = py_files[:max_files]

    return {
        "root": project_root,
        "name": os.path.basename(project_root),
        "src_dirs": src_dirs,
        "python_files": py_files,
        "test_files": test_files,
        "total_source_files": len(py_files),
        "total_test_files": len(test_files),
        "total_loc": total_loc,
    }


# ── Lint analysis ─────────────────────────────────────────────────────


def _run_lint_analysis(project_root: str, py_files: list[str]) -> dict[str, Any]:
    """Run available linters and collect findings."""
    findings: list[dict[str, Any]] = []
    auto_fixable: list[dict[str, Any]] = []

    try:
        from lintgate.linters.ruff_linter import RuffLinter

        ruff = RuffLinter()
        try:
            avail = ruff.available(project_root=project_root)
        except TypeError:
            avail = ruff.available()
        if avail:
            full_paths = [os.path.join(project_root, f) for f in py_files]
            issues = ruff.run(full_paths, project_root=project_root)
            for issue in issues:
                entry = {
                    "linter": "ruff",
                    "kind": issue.kind,
                    "message": issue.message,
                    "file": os.path.relpath(issue.file, project_root) if issue.file else "",
                    "line": issue.line,
                    "severity": issue.severity,
                    "fixable": issue.fixable,
                }
                findings.append(entry)
                if issue.fixable:
                    auto_fixable.append(entry)
    except Exception as e:
        findings.append({"linter": "ruff", "error": str(e)})

    # Group by severity
    blocking = [f for f in findings if f.get("severity") == "blocking"]
    warnings = [f for f in findings if f.get("severity") == "warning"]
    info = [f for f in findings if f.get("severity") not in ("blocking", "warning")]

    return {
        "total_findings": len(findings),
        "blocking": len(blocking),
        "warnings": len(warnings),
        "informational": len(info),
        "auto_fixable": len(auto_fixable),
        "findings": findings[:200],  # Cap for portability
        "auto_fixable_summary": auto_fixable[:50],
    }


# ── Specification analysis ────────────────────────────────────────────


def _run_spec_analysis(project_root: str, py_files: list[str]) -> dict[str, Any]:
    """Run specification complexity analysis on all source files."""
    functions: list[dict[str, Any]] = []
    regime_dist: dict[str, int] = {"A": 0, "B": 0, "unknown": 0}
    phase_dist: dict[str, int] = {}
    total_sigma = 0

    try:
        from lintgate.specification.file_analyzer import analyze_file

        for rel_path in py_files[:200]:  # Cap for time budget
            full = os.path.join(project_root, rel_path)
            if not os.path.isfile(full):
                continue
            try:
                result = analyze_file(full, project_root, enrich=False)
                if result and hasattr(result, "functions"):
                    for func_key, func_data in result.functions.items():
                        fd = func_data if isinstance(func_data, dict) else {}
                        fd["function_key"] = func_key
                        fd["source_file"] = rel_path
                        functions.append(fd)

                        regime = fd.get("regime", "unknown")
                        regime_dist[regime] = regime_dist.get(regime, 0) + 1
                        phase = fd.get("phase", "bulk")
                        phase_dist[phase] = phase_dist.get(phase, 0) + 1
                        total_sigma += fd.get("estimated_sigma", 0)
            except Exception:
                continue
    except ImportError:
        return {"error": "specification analysis not available"}

    # Sort by sigma descending for hotspot identification
    functions.sort(key=lambda f: f.get("estimated_sigma", 0), reverse=True)

    under_specified = [
        f for f in functions
        if f.get("estimated_sigma", 0) > f.get("assertion_count", 0)
    ]

    return {
        "total_functions": len(functions),
        "total_sigma": total_sigma,
        "regime_distribution": regime_dist,
        "phase_distribution": phase_dist,
        "under_specified_count": len(under_specified),
        "hotspot_functions": functions[:30],
        "under_specified_top": under_specified[:20],
    }


# ── Mutation analysis ─────────────────────────────────────────────────


def _load_mutation_cache(project_root: str) -> dict[str, Any]:
    """Load existing mutation profiles from .lintgate/mutation/."""
    cache_dir = os.path.join(project_root, ".lintgate", "mutation")
    if not os.path.isdir(cache_dir):
        return {"cached": False, "reason": "no mutation cache"}

    profiles: list[dict[str, Any]] = []
    total_killed = 0
    total_survived = 0
    high_survival: list[dict[str, Any]] = []
    category_survival: dict[str, int] = {}

    for fname in sorted(os.listdir(cache_dir)):
        if fname in ("sweep_summary.json", "scheduler_state.json", "coverage_analysis.json"):
            continue
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(cache_dir, fname), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue

        func_key = data.get("function_key", "")
        killed = data.get("total_killed", 0)
        survived = data.get("total_survived", 0)
        total_killed += killed
        total_survived += survived

        profile = {
            "function_key": func_key,
            "killed": killed,
            "survived": survived,
            "kill_rate": round(killed / (killed + survived), 3) if (killed + survived) else 0,
            "discovery_state": data.get("discovery_state", "UNKNOWN"),
            "is_pure": data.get("is_pure", False),
            "surviving_categories": [],
        }

        for cat in data.get("per_category", []):
            cat_name = cat.get("category", "")
            cat_survived = cat.get("survived", 0)
            if cat_survived > 0:
                profile["surviving_categories"].append(cat_name)
                category_survival[cat_name] = category_survival.get(cat_name, 0) + cat_survived

        profiles.append(profile)
        if survived > 0 and (killed + survived) > 0:
            rate = survived / (killed + survived)
            if rate > 0.3:
                high_survival.append({**profile, "survival_rate": round(rate, 3)})

    total = total_killed + total_survived
    high_survival.sort(key=lambda x: x.get("survival_rate", 0), reverse=True)

    return {
        "cached": True,
        "total_profiles": len(profiles),
        "total_killed": total_killed,
        "total_survived": total_survived,
        "kill_rate": round(total_killed / total, 4) if total else 0,
        "category_survival": category_survival,
        "high_survival_functions": high_survival[:30],
        "profiles": profiles,
    }


# ── Composition analysis ─────────────────────────────────────────────


def _run_composition_analysis(project_root: str, py_files: list[str]) -> dict[str, Any]:
    """Analyze cross-module composition gaps."""
    try:
        from lintgate.specification.call_graph import build_cross_module_call_graph

        source_files = [os.path.join(project_root, f) for f in py_files if os.path.isfile(os.path.join(project_root, f))]
        if not source_files:
            return {"error": "no source files"}

        cg = build_cross_module_call_graph(source_files, project_root)

        # Compute basic graph metrics
        total_edges = sum(len(v) for v in cg.calls.values())
        fan_in_dist = {k: len(v) for k, v in cg.called_by.items()}
        fan_out_dist = {k: len(v) for k, v in cg.calls.items()}

        # High fan-in = widely used, high fan-out = complex orchestrator
        high_fan_in = sorted(fan_in_dist.items(), key=lambda x: -x[1])[:15]
        high_fan_out = sorted(fan_out_dist.items(), key=lambda x: -x[1])[:15]

        return {
            "total_edges": total_edges,
            "total_nodes": len(set(cg.calls.keys()) | set(cg.called_by.keys())),
            "high_fan_in": [{"function": k, "fan_in": v} for k, v in high_fan_in],
            "high_fan_out": [{"function": k, "fan_out": v} for k, v in high_fan_out],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Performance analysis ──────────────────────────────────────────────


def _run_performance_analysis(project_root: str, py_files: list[str]) -> dict[str, Any]:
    """Analyze purity, algebraic properties, and performance anti-patterns."""
    pure_functions: list[str] = []
    impure_functions: list[str] = []
    anti_patterns: list[dict[str, Any]] = []

    try:
        from lintgate.linters.performance_checks.purity_detector import detect_purity

        for rel_path in py_files[:100]:
            full = os.path.join(project_root, rel_path)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    try:
                        result = detect_purity(node, source)
                        func_key = f"{rel_path}::{node.name}"
                        if result.is_pure:
                            pure_functions.append(func_key)
                        else:
                            impure_functions.append(func_key)
                    except Exception:
                        pass
    except ImportError:
        pass

    return {
        "pure_count": len(pure_functions),
        "impure_count": len(impure_functions),
        "pure_ratio": round(len(pure_functions) / max(len(pure_functions) + len(impure_functions), 1), 3),
        "pure_functions": pure_functions[:50],
        "anti_patterns": anti_patterns[:20],
    }


# ── Prescriptive spec state ──────────────────────────────────────────


def _load_prescriptive_state(project_root: str) -> dict[str, Any]:
    """Load existing prescriptive specs."""
    try:
        from lintgate.specification.prescriptive_spec import load_all_specs

        specs = load_all_specs(project_root)
        if not specs:
            return {"total_specs": 0}

        return {
            "total_specs": len(specs),
            "specs": [
                {
                    "target_key": s.target_key,
                    "problem_class": s.problem_class,
                    "mode": s.mode,
                    "prescriptive_sigma": s.prescriptive_sigma,
                    "invariant_count": len(s.invariants),
                    "forbidden_count": len(s.forbidden_behaviors),
                    "generation_constraint_count": len(s.generation_constraints),
                }
                for s in specs.values()
            ],
        }
    except Exception:
        return {"total_specs": 0}


# ── Test coverage ─────────────────────────────────────────────────────


def _analyze_test_coverage(
    project_root: str, py_files: list[str]
) -> dict[str, Any]:
    """Map source files to test files and compute coverage ratios."""
    test_dir = os.path.join(project_root, "tests")
    no_test: list[dict[str, Any]] = []
    has_test: list[dict[str, Any]] = []

    for rel_path in py_files:
        base = os.path.splitext(os.path.basename(rel_path))[0]
        clean = base.lstrip("_")

        # Find matching test file
        test_path = None
        for candidate in [f"test_{base}.py", f"test_{clean}.py"]:
            full_candidate = os.path.join(test_dir, candidate)
            if os.path.isfile(full_candidate):
                test_path = full_candidate
                break

        if test_path is None and os.path.isdir(test_dir):
                for fn in os.listdir(test_dir):
                    if fn.startswith("test_") and clean in fn and fn.endswith(".py"):
                        test_path = os.path.join(test_dir, fn)
                        break

        full_src = os.path.join(project_root, rel_path)
        src_loc = _count_lines(full_src)

        if test_path:
            test_loc = _count_lines(test_path)
            ratio = round(test_loc / max(src_loc, 1), 3)
            has_test.append({
                "file": rel_path,
                "src_loc": src_loc,
                "test_file": os.path.relpath(test_path, project_root),
                "test_loc": test_loc,
                "ratio": ratio,
            })
        else:
            no_test.append({"file": rel_path, "src_loc": src_loc})

    total_src = sum(e["src_loc"] for e in no_test) + sum(e["src_loc"] for e in has_test)
    total_test = sum(e["test_loc"] for e in has_test)

    return {
        "files_with_tests": len(has_test),
        "files_without_tests": len(no_test),
        "total_src_loc": total_src,
        "total_test_loc": total_test,
        "overall_ratio": round(total_test / max(total_src, 1), 3),
        "no_test_files": sorted(no_test, key=lambda x: -x["src_loc"])[:30],
        "low_coverage_files": sorted(
            [e for e in has_test if e["ratio"] < 0.5],
            key=lambda x: x["ratio"],
        )[:20],
    }


# ── Action plan builder ──────────────────────────────────────────────


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
        actions.append(ActionItem(
            rank=rank,
            priority="P1_critical",
            category="lint_auto_fix",
            file="<project>",
            action=f"Run `lint_fix(path)` to auto-fix {lint['auto_fixable']} issues. This is a zero-effort first step.",
            rationale="Auto-fixable issues are mechanical — fix them immediately to reduce noise.",
            estimated_effort="trivial",
            evidence={"auto_fixable_count": lint["auto_fixable"]},
        ))

    # ── Phase 1b: Blocking lint errors (P0, no deps) ─────────────
    for finding in lint.get("findings", [])[:10]:
        if finding.get("severity") != "blocking":
            continue
        rank += 1
        actions.append(ActionItem(
            rank=rank,
            priority="P0_blocking",
            category="lint_error",
            file=finding.get("file", ""),
            action=f"Fix {finding.get('kind', '')}: {finding.get('message', '')}",
            rationale="Blocking lint errors prevent code from running correctly.",
            estimated_effort="small",
            evidence={"kind": finding.get("kind"), "line": finding.get("line")},
        ))

    # ── Phase 2: Create missing test files (P1, depends on lint fix) ──
    test_creation_ranks: list[int] = []
    for entry in coverage.get("no_test_files", [])[:15]:
        rank += 1
        test_creation_ranks.append(rank)
        deps = [auto_fix_rank] if auto_fix_rank else []
        actions.append(ActionItem(
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
        ))

    # ── Phase 3: Close specification gaps (P2, depends on tests) ──
    for func in spec.get("under_specified_top", [])[:15]:
        rank += 1
        func_key = func.get("function_key", "")
        sigma = func.get("estimated_sigma", 0)
        assertions = func.get("assertion_count", 0)
        deps = test_creation_ranks[:3] if test_creation_ranks else []
        actions.append(ActionItem(
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
        ))

    # ── Phase 4: Kill surviving mutations (P2, depends on tests) ──
    if isinstance(mutation, dict) and mutation.get("cached"):
        for func_profile in mutation.get("high_survival_functions", [])[:15]:
            rank += 1
            func_key = func_profile.get("function_key", "")
            surviving = func_profile.get("surviving_categories", [])
            actions.append(ActionItem(
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
            ))

    # ── Phase 5: Improve low test coverage (P2) ──────────────────
    for entry in coverage.get("low_coverage_files", [])[:10]:
        rank += 1
        actions.append(ActionItem(
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
        ))

    # ── Phase 6: Prescriptive spec opportunities (P3) ─────────────
    prescriptive = analysis.get("prescriptive", {})
    if prescriptive.get("total_specs", 0) == 0 and len(spec.get("hotspot_functions", [])) > 0:
        rank += 1
        top_hotspots = [f.get("function_key", "") for f in spec.get("hotspot_functions", [])[:5]]
        actions.append(ActionItem(
            rank=rank,
            priority="P3_improve",
            category="prescriptive_opportunity",
            file="<project>",
            action=f"Create prescriptive specs for top hotspot functions: "
                   f"{', '.join(top_hotspots)}. "
                   f"Use `prescriptive_spec_compose(path, target)` to create behavioral contracts "
                   f"before writing new code.",
            rationale="Prescriptive specs shift quality left — behavioral contracts before code, not after.",
            estimated_effort="small",
            evidence={"hotspot_count": len(top_hotspots), "hotspots": top_hotspots},
        ))

    # ── Phase 7: Pure function optimization (P3) ──────────────────
    pure_count = performance.get("pure_count", 0)
    if pure_count > 0:
        rank += 1
        actions.append(ActionItem(
            rank=rank,
            priority="P3_improve",
            category="purity_optimization",
            file="<project>",
            action=f"{pure_count} pure functions detected. "
                   f"Use `inspect_algebra(path)` to extract algebraic properties "
                   f"and `generate_property_tests(path)` for Hypothesis-based verification.",
            rationale="Pure functions enable safe caching, parallelization, and property-based testing.",
            estimated_effort="medium",
            evidence={"pure_count": pure_count, "pure_ratio": performance.get("pure_ratio", 0)},
        ))

    return [asdict(a) for a in actions]


# ── Helpers ───────────────────────────────────────────────────────────


def _count_lines(path: str) -> int:
    try:
        return sum(1 for _ in open(path, encoding="utf-8", errors="ignore"))
    except OSError:
        return 0
