"""Implementation layer for prescriptive spec MCP tools."""

from __future__ import annotations

import json
import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions


def impl_prescriptive_spec_compose(
    path: str,
    target: str,
    mode: str,
    helpers: dict[str, Any],
) -> dict[str, Any]:
    """Compose a PrescriptiveSpec for a function or module."""
    from lintgate.compass import CompassState
    from lintgate.compass_io import load_compass
    from lintgate.specification.prescriptive_spec import (
        PrescriptiveSpecComposer,
        save_spec,
    )

    project_root = helpers["_validate_project_root"](path)

    # Load compass
    compass = load_compass(project_root)
    if compass is None:
        compass = CompassState()

    # Load theory profile
    theory_profile = _load_theory_profile(project_root)

    # Detect mode
    if mode == "auto":
        func_spec = _try_load_func_spec(project_root, target)
        mode = "retrospective" if func_spec else "prospective"
    else:
        func_spec = _try_load_func_spec(project_root, target) if mode == "retrospective" else None

    composer = PrescriptiveSpecComposer()

    if mode == "retrospective" and func_spec:
        # Load optional enrichments
        algebra = _try_load_algebra(project_root, target)
        mutation_state = _try_load_mutation_state(project_root, target)
        spec = composer.compose_retrospective(
            func_spec=func_spec,
            compass=compass,
            theory_profile=theory_profile,
            algebra=algebra,
            mutation_state=mutation_state,
        )
    else:
        spec = composer.compose_prospective(
            target_key=target,
            compass=compass,
            theory_profile=theory_profile,
        )

    # Save
    save_spec(project_root, spec)

    # Emit living context patch so CLAUDE.md stays current
    try:
        from lintgate.context.bootstrap_patches import generate_context_patch

        inv_descs = [inv.description[:60] for inv in spec.invariants[:2]]
        generate_context_patch(
            project_root,
            trigger="prescriptive_spec_composed",
            evidence={
                "target_key": spec.target_key,
                "problem_class": spec.problem_class,
                "summary": "; ".join(inv_descs) if inv_descs else "behavioral contract",
            },
        )
    except Exception:
        pass

    result = spec.to_dict()
    result["next_actions"] = serialize_next_actions([
        NextAction(
            tool="prescriptive_spec_compile",
            args={"path": path, "target": target},
            reason="Compile spec into test skeletons + generation constraints",
        ),
    ])
    return result


def impl_prescriptive_spec_compile(
    path: str,
    target: str,
    helpers: dict[str, Any],
) -> dict[str, Any]:
    """Compile PrescriptiveSpec into test skeletons + generation constraints."""
    from lintgate.specification.prescriptive_backends import select_backend
    from lintgate.specification.prescriptive_spec import load_spec

    project_root = helpers["_validate_project_root"](path)
    spec = load_spec(project_root, target)

    if spec is None:
        return {
            "error": f"No prescriptive spec found for '{target}'. Run prescriptive_spec_compose first.",
            "next_actions": serialize_next_actions([
                NextAction(
                    tool="prescriptive_spec_compose",
                    args={"path": path, "target": target},
                    reason="Compose a spec before compiling",
                ),
            ]),
        }

    backend = select_backend(spec)
    targets = backend.compile(spec)

    result = targets.to_dict()
    result["spec_id"] = spec.spec_id
    result["target_key"] = spec.target_key
    result["problem_class"] = spec.problem_class

    # Render generation constraints as an LLM-consumable prompt section
    if targets.generation_constraints:
        result["generation_prompt"] = _render_generation_prompt(
            spec.target_key, targets.generation_constraints
        )
    result["next_actions"] = serialize_next_actions([
        NextAction(
            tool="prescriptive_spec_verify",
            args={"path": path, "file": _target_to_file(target), "function": _target_to_func(target)},
            reason="Verify code refinement against prescriptive spec after writing code",
        ),
    ])
    return result


def impl_prescriptive_spec_verify(
    path: str,
    file: str,
    function: str | None,
    helpers: dict[str, Any],
) -> dict[str, Any]:
    """Verify code against its PrescriptiveSpec."""
    from lintgate.specification.prescriptive_backends import (
        PrescriptiveAdapter,
        select_backend,
    )
    from lintgate.specification.prescriptive_spec import load_all_specs, load_spec

    project_root = helpers["_validate_project_root"](path)

    # Find matching specs
    if function:
        # Try direct lookup
        for sep in ("::", "."):
            key = f"{file.replace('/', '.').removesuffix('.py')}{sep}{function}"
            spec = load_spec(project_root, key)
            if spec:
                break
        else:
            # Fallback: search all specs for function name match
            all_specs = load_all_specs(project_root)
            spec = None
            for s in all_specs.values():
                if s.target_key.endswith(f"::{function}") or s.target_key.endswith(f".{function}"):
                    spec = s
                    break
    else:
        # Verify all specs for functions in the file
        all_specs = load_all_specs(project_root)
        file_module = file.replace("/", ".").removesuffix(".py")
        matching = [s for s in all_specs.values() if file_module in s.target_key]
        if not matching:
            return {
                "status": "no_specs",
                "message": f"No prescriptive specs found for file '{file}'",
                "next_actions": serialize_next_actions([
                    NextAction(
                        tool="prescriptive_spec_compose",
                        args={"path": path, "target": f"{file_module}::*"},
                        reason="Compose specs for functions in this file",
                    ),
                ]),
            }
        # Verify first match (could extend to multi-verify)
        spec = matching[0]

    if spec is None:
        return {
            "status": "no_spec",
            "message": f"No prescriptive spec found for function '{function}'",
            "next_actions": serialize_next_actions([
                NextAction(
                    tool="prescriptive_spec_compose",
                    args={"path": path, "target": function or ""},
                    reason="Compose a spec first",
                ),
            ]),
        }

    backend = select_backend(spec)
    targets = backend.compile(spec)
    adapter = PrescriptiveAdapter()
    verdict = adapter.verify_refinement(spec, targets, project_root, file, function)

    # Add next actions based on verdict
    next_actions = []
    if verdict["overall"] in ("fail", "partial", "unknown"):
        next_actions.append(NextAction(
            tool="mutation_run_sampling",
            args={"path": path, "file": file},
            reason="Run mutation sampling to get fresh kill data",
        ))
        next_actions.append(NextAction(
            tool="spec_file_analyze",
            args={"path": path, "file": file},
            reason="Analyze specification state of this file",
        ))

    verdict["next_actions"] = serialize_next_actions(next_actions)
    return verdict


def impl_prescriptive_spec_status(
    path: str,
    helpers: dict[str, Any],
) -> dict[str, Any]:
    """Show prescriptive spec coverage and distribution."""
    from lintgate.specification.prescriptive_spec import load_all_specs

    project_root = helpers["_validate_project_root"](path)
    all_specs = load_all_specs(project_root)

    if not all_specs:
        return {
            "total_specs": 0,
            "message": "No prescriptive specs found. Use prescriptive_spec_compose to create specs.",
            "next_actions": serialize_next_actions([
                NextAction(
                    tool="prescriptive_spec_compose",
                    args={"path": path, "target": "<function_key>"},
                    reason="Compose a prescriptive spec",
                ),
            ]),
        }

    # Problem class distribution
    class_dist: dict[str, int] = {"pure": 0, "stateful": 0, "distributed": 0}
    mode_dist: dict[str, int] = {"prospective": 0, "retrospective": 0}
    total_sigma = 0

    for spec in all_specs.values():
        class_dist[spec.problem_class] = class_dist.get(spec.problem_class, 0) + 1
        mode_dist[spec.mode] = mode_dist.get(spec.mode, 0) + 1
        total_sigma += spec.prescriptive_sigma

    n = len(all_specs)
    result = {
        "total_specs": n,
        "problem_classes": class_dist,
        "modes": mode_dist,
        "mean_prescriptive_sigma": round(total_sigma / n, 2) if n else 0.0,
        "specs": [
            {
                "target_key": s.target_key,
                "problem_class": s.problem_class,
                "mode": s.mode,
                "prescriptive_sigma": s.prescriptive_sigma,
                "invariant_count": len(s.invariants),
                "forbidden_count": len(s.forbidden_behaviors),
            }
            for s in all_specs.values()
        ],
        "next_actions": serialize_next_actions([
            NextAction(
                tool="prescriptive_spec_compose",
                args={"path": path, "target": "<uncovered_function>"},
                reason="Add specs for uncovered functions",
            ),
        ]),
    }
    return result


# ── Helpers ───────────────────────────────────────────────────────────


def _load_theory_profile(project_root: str) -> dict[str, Any]:
    """Load theory profile — tries cached file, falls back to live extraction."""
    # Try cached file first (fast path)
    theory_path = os.path.join(project_root, ".lintgate", "theory_profile.json")
    if os.path.isfile(theory_path):
        try:
            with open(theory_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass

    # Fall back to live extraction
    try:
        from lintgate.theory_extractor import extract_theory

        result = extract_theory(project_root)
        return result.get("theory_profile", {})
    except Exception:
        return {}


def _try_load_func_spec(project_root: str, target_key: str) -> Any:
    """Try to load a FunctionSpecification from spec cache.

    Hydrates a real FunctionSpecification from cached ledger data so
    retrospective compose has actual sigma, testability, assertions, etc.
    """
    cache_dir = os.path.join(project_root, ".lintgate", "spec_cache")
    if not os.path.isdir(cache_dir):
        return None
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(cache_dir, fname), encoding="utf-8") as f:
                data = json.load(f)
            funcs = data.get("functions", {})
            if target_key in funcs:
                from lintgate.specification.types import (
                    FunctionSpecification,
                    RiskProfile,
                    SpecCore,
                    TestabilityProfile,
                    Traceability,
                )

                fd = funcs[target_key]
                fs = FunctionSpecification(
                    function_key=target_key,
                    source_file=fd.get("source_file", ""),
                    core=SpecCore(
                        estimated_sigma=fd.get("estimated_sigma", 0),
                        specification_level=fd.get("specification_level", 0.0),
                        regime=fd.get("regime", "unknown"),
                        regime_rationale=fd.get("regime_rationale", ""),
                        is_pure=fd.get("is_pure", False),
                        phase=fd.get("phase", "bulk"),
                    ),
                    testability=TestabilityProfile(
                        testability_score=fd.get("testability_score", 1.0),
                        is_stateful=fd.get("is_stateful", False),
                    ),
                    traceability=Traceability(
                        assertion_count=fd.get("assertion_count", 0),
                        covering_tests=fd.get("covering_tests", []),
                        coupling_surface=fd.get("coupling_surface", 0),
                    ),
                    risk=RiskProfile(
                        risk_score=fd.get("risk_score", 0.0),
                        priority_band=fd.get("priority_band", "P2"),
                    ),
                    optimization_hints=fd.get("optimization_hints", []),
                )

                # Enrich from graph projection if available
                try:
                    from lintgate.specification.prescriptive_projection import (
                        load_single_projection,
                    )

                    proj = load_single_projection(project_root, target_key)
                    if proj:
                        fs.traceability.coupling_surface = proj.coupling_surface
                        if proj.covering_tests:
                            fs.traceability.covering_tests = proj.covering_tests
                except Exception:
                    pass

                return fs
        except (OSError, ValueError, KeyError):
            continue
    return None


def _try_load_algebra(project_root: str, target_key: str) -> Any:
    """Try to load algebraic properties for a function from the performance cache."""
    cache_path = os.path.join(project_root, ".lintgate", "algebra_cache.json")
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if target_key in data:
            return data[target_key]  # dict with algebraic_properties key
    except (OSError, ValueError):
        pass
    return None


def _try_load_mutation_state(project_root: str, target_key: str) -> dict[str, Any] | None:
    """Try to load mutation state for a function."""
    cache_dir = os.path.join(project_root, ".lintgate", "mutation")
    if not os.path.isdir(cache_dir):
        return None
    for fname in os.listdir(cache_dir):
        if fname == "scheduler_state.json" or not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(cache_dir, fname), encoding="utf-8") as f:
                data = json.load(f)
            if data.get("function_key") == target_key:
                return data
        except (OSError, ValueError):
            continue
    return None


def _target_to_file(target: str) -> str:
    """Extract file path from target_key (module::func → module.py)."""
    if "::" in target:
        module = target.split("::")[0]
        return module.replace(".", "/") + ".py"
    return target


def _target_to_func(target: str) -> str | None:
    """Extract function name from target_key."""
    if "::" in target:
        return target.split("::")[-1]
    return None


def _render_generation_prompt(target_key: str, constraints: list[dict[str, Any]]) -> str:
    """Render generation constraints as a structured LLM-consumable prompt.

    This is the consumer side of GenerationConstraint — it transforms the
    structured constraint list into natural-language directives sorted by
    priority that an LLM can follow during code generation.
    """
    lines = [f"## Generation Constraints for `{target_key}`", ""]

    must_lines: list[str] = []
    must_not_lines: list[str] = []
    pattern_lines: list[str] = []

    for c in sorted(constraints, key=lambda x: x.get("priority", 5)):
        desc = c.get("description", "")
        ct = c.get("constraint_type", "")
        if ct == "must_not_use":
            must_not_lines.append(f"- MUST NOT: {desc}")
        elif ct == "must_use":
            must_lines.append(f"- MUST: {desc}")
        else:
            pattern_lines.append(f"- {desc}")

    if must_not_lines:
        lines.append("### Forbidden")
        lines.extend(must_not_lines)
        lines.append("")
    if must_lines:
        lines.append("### Required")
        lines.extend(must_lines)
        lines.append("")
    if pattern_lines:
        lines.append("### Patterns")
        lines.extend(pattern_lines)
        lines.append("")

    return "\n".join(lines)
