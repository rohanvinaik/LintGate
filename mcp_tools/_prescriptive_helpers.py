"""Helper functions for prescriptive spec MCP tools.

Data loaders, target parsing, and prompt rendering.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── Helpers ───────────────────────────────────────────────────────────


def _load_theory_profile(project_root: str) -> dict[str, Any]:
    """Load theory profile — tries cached file, falls back to live extraction."""
    # Try cached file first (fast path)
    theory_path = os.path.join(project_root, ".lintgate", "theory_profile.json")
    if os.path.isfile(theory_path):
        try:
            with open(theory_path, encoding="utf-8") as f:
                result: dict[str, Any] = json.load(f)
                return result
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
                    from lintgate.specification.prescriptive.projection import (
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
                result: dict[str, Any] = data
                return result
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


def _render_repair_prompt(
    spec: Any,
    targets: Any,
    gate_result: Any,
    synthesis_result: Any | None,
) -> str:
    """Render a minimal LLM repair prompt (~200-400 tokens).

    Contains only: signature, generation constraints, semantic hints,
    failing checks, surviving mutations. No source context or test files.
    """
    lines = ["## Repair Prompt", ""]

    # 1. Function signature (from implementation_stub)
    if targets.implementation_stub:
        # Extract just the def line
        for line in targets.implementation_stub.split("\n"):
            if line.startswith("def "):
                lines.append(f"**Signature:** `{line}`")
                break
        lines.append("")

    # 2. Generation constraints (max 8)
    constraints = (
        targets.generation_constraints[:8] if hasattr(targets, "generation_constraints") else []
    )
    if constraints:
        lines.append("### Constraints")
        for c in sorted(constraints, key=lambda x: x.get("priority", 5)):
            desc = c.get("description", "")
            ct = c.get("constraint_type", "")
            prefix = "MUST NOT" if ct == "must_not_use" else "MUST" if ct == "must_use" else "NOTE"
            lines.append(f"- {prefix}: {desc}")
        lines.append("")

    # 3. Semantic hints (CUSTOM predicate descriptions, max 5)
    from lintgate.specification.prescriptive.spec import PredicateOp

    custom_hints = [
        inv.description for inv in spec.invariants if inv.predicate.op == PredicateOp.CUSTOM
    ][:5]
    if custom_hints:
        lines.append("### Semantic Hints")
        for hint in custom_hints:
            lines.append(f"- {hint}")
        lines.append("")

    # 4. Synthesis failure reason
    if synthesis_result and not synthesis_result.success:
        lines.append(f"**Symbolic synthesis failed:** {synthesis_result.failure_reason}")
        if synthesis_result.body:
            lines.append(f"**Partial body:**\n```python\n{synthesis_result.body}\n```")
        lines.append("")

    # 5. Gate reasons (what blocked full synthesis)
    if gate_result and gate_result.reasons:
        lines.append("### Gate Blockers")
        for reason in gate_result.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    return "\n".join(lines)
