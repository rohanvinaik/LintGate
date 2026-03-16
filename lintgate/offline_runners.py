"""Individual analysis runners for offline analysis.

Each function runs one dimension of the analysis: lint, spec, mutation,
composition, performance, prescriptive state, test coverage, project structure.
"""

from __future__ import annotations

import ast
import json
import os
from typing import Any

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
        has_py = any(
            f.endswith(".py") for f in os.listdir(full) if os.path.isfile(os.path.join(full, f))
        )
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
        from lintgate.types import LinterContext

        ruff = RuffLinter()
        try:
            avail = ruff.available(project_root=project_root)
        except TypeError:
            avail = ruff.available()
        if avail:
            full_paths = [os.path.join(project_root, f) for f in py_files]
            ctx = LinterContext(files=full_paths, project_root=project_root)
            issues = ruff.run(ctx)
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
        f for f in functions if f.get("estimated_sigma", 0) > f.get("assertion_count", 0)
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

        source_files = [
            os.path.join(project_root, f)
            for f in py_files
            if os.path.isfile(os.path.join(project_root, f))
        ]
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
        "pure_ratio": round(
            len(pure_functions) / max(len(pure_functions) + len(impure_functions), 1), 3
        ),
        "pure_functions": pure_functions[:50],
        "anti_patterns": anti_patterns[:20],
    }


# ── Prescriptive spec state ──────────────────────────────────────────


def _load_prescriptive_state(project_root: str) -> dict[str, Any]:
    """Load existing prescriptive specs."""
    try:
        from lintgate.specification.prescriptive.spec import load_all_specs

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


def _analyze_test_coverage(project_root: str, py_files: list[str]) -> dict[str, Any]:
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
            has_test.append(
                {
                    "file": rel_path,
                    "src_loc": src_loc,
                    "test_file": os.path.relpath(test_path, project_root),
                    "test_loc": test_loc,
                    "ratio": ratio,
                }
            )
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


_PROJECT_SENTINEL = "<project>"


def _count_lines(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0
