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
    description: str = "",
    claims: list[str] | None = None,
    interface_hint: str = "",
) -> dict[str, Any]:
    """Compose a PrescriptiveSpec for a function or module."""
    from lintgate.compass import CompassState
    from lintgate.compass_io import load_compass
    from lintgate.specification.prescriptive_spec import (
        Invariant,
        PrescriptiveSpecComposer,
        PrescriptiveWorkflowRecord,
        compile_claim,
        project_claims,
        save_spec,
        save_workflow_record,
    )

    project_root = helpers["_validate_project_root"](path)

    # Load compass
    compass = load_compass(project_root)
    if compass is None:
        compass = CompassState()

    # Load theory profile
    theory_profile = _load_theory_profile(project_root)

    # Parse interface_hint from JSON string
    parsed_hint: dict[str, Any] | None = None
    if interface_hint:
        try:
            parsed_hint = json.loads(interface_hint)
        except (ValueError, TypeError):
            parsed_hint = None

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
            interface_hint=parsed_hint,
        )

    # ── Inject agent-provided claims ──────────────────────────────
    agent_invariants: list[Invariant] = []
    if claims:
        for i, claim_text in enumerate(claims):
            agent_invariants.append(
                Invariant(
                    name=f"agent_claim_{i}",
                    predicate=compile_claim(claim_text),
                    description=claim_text,
                    source=f"agent:claim:{i}",
                    confidence=0.85,
                    kind="safety",
                )
            )
    if description:
        for i, sentence in enumerate(s.strip() for s in description.split(". ") if s.strip()):
            agent_invariants.append(
                Invariant(
                    name=f"agent_desc_{i}",
                    predicate=compile_claim(sentence),
                    description=sentence,
                    source=f"agent:description:{i}",
                    confidence=0.75,
                    kind="alignment",
                )
            )
    if agent_invariants:
        spec.invariants.extend(agent_invariants)
        # Recompute generation constraints and sigma with new invariants
        spec.generation_constraints = composer._build_generation_constraints(spec)
        spec.prescriptive_sigma = composer._compute_prescriptive_sigma(spec)

    # ── Project claims for targeted filtering ─────────────────────
    _projected_invs, _projected_forbidden, projection_log = project_claims(
        target,
        compass,
        theory_profile,
        interface_hint=parsed_hint,
        func_spec=func_spec,
    )

    # Save
    save_spec(project_root, spec)

    # Create workflow record
    workflow = PrescriptiveWorkflowRecord(
        spec_id=spec.spec_id,
        target_key=spec.target_key,
        state="composed",
        projected_claims=projection_log,
        recommended_next_action="prescriptive_spec_compile",
        recommended_next_args={"path": path, "target": target},
    )
    save_workflow_record(project_root, workflow)

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
    result["projection_log"] = projection_log
    result["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="prescriptive_spec_compile",
                args={"path": path, "target": target},
                reason="Compile spec into test skeletons + generation constraints",
            ),
        ]
    )
    return result


def impl_prescriptive_spec_compile(
    path: str,
    target: str,
    helpers: dict[str, Any],
) -> dict[str, Any]:
    """Compile PrescriptiveSpec into test skeletons + generation constraints.

    Enhanced pipeline: compile → synthesis gate → witness generation →
    symbolic synthesis attempt → generation_mode routing.
    """
    from lintgate.specification.prescriptive_backends import (
        PrescriptiveAdapter,
        check_synthesis_gate,
        generate_executable_witnesses,
        select_backend,
    )
    from lintgate.specification.prescriptive_spec import (
        load_spec,
        load_workflow_record,
        save_workflow_record,
    )

    project_root = helpers["_validate_project_root"](path)
    spec = load_spec(project_root, target)

    if spec is None:
        return {
            "error": f"No prescriptive spec found for '{target}'. Run prescriptive_spec_compose first.",
            "next_actions": serialize_next_actions(
                [
                    NextAction(
                        tool="prescriptive_spec_compose",
                        args={"path": path, "target": target},
                        reason="Compose a spec before compiling",
                    ),
                ]
            ),
        }

    backend = select_backend(spec)
    targets = backend.compile(spec)

    # ── Executable witness generation ──────────────────────────────
    witnesses = []
    if spec.problem_class == "pure" and spec.parameters:
        try:
            witnesses = generate_executable_witnesses(spec, project_root)
            if any(w.has_oracle_value for w in witnesses):
                targets.synthesis_profile["has_executable_witnesses"] = True
        except Exception:
            pass

    # ── Synthesis gate + symbolic synthesis ─────────────────────────
    generation_mode = "llm_direct"
    gate_result = check_synthesis_gate(spec, targets)
    synthesis_result = None

    if gate_result.eligible:
        from lintgate.specification.prescriptive_synthesis import synthesize_body

        synthesis_result = synthesize_body(spec, witnesses, project_root)
        if synthesis_result.success:
            generation_mode = "symbolic_only"
        else:
            generation_mode = "symbolic_then_llm_repair"
    elif targets.synthesis_profile.get("gate_eligible", False):
        # Profile says eligible but missing witnesses — could be symbolic after witnesses
        generation_mode = "llm_direct"

    # Persist kill expectations and materialized test file
    adapter = PrescriptiveAdapter()
    adapter.persist_kill_expectations(spec, targets, project_root)

    compiled_targets_path = os.path.join(
        project_root,
        ".lintgate",
        "prescriptive_specs",
        f"{spec.spec_id}_targets.json",
    )
    os.makedirs(os.path.dirname(compiled_targets_path), exist_ok=True)
    with open(compiled_targets_path, "w", encoding="utf-8") as f:
        json.dump(targets.to_dict(), f, indent=2)

    # Materialize test file
    materialized_test_path = ""
    gen_dir = os.path.join(project_root, "tests", "generated")
    func_name = _target_to_func(target) or target.replace("::", "_")
    test_output = os.path.join(gen_dir, f"test_prescriptive_{func_name}.py")
    if targets.property_tests or targets.scenario_tests:
        materialized_test_path = adapter.materialize_test_file(targets, spec, test_output)

    # Materialize implementation stub
    materialized_stub_path = ""
    if targets.implementation_stub:
        stub_dir = os.path.join(project_root, ".lintgate", "generated_stubs")
        os.makedirs(stub_dir, exist_ok=True)
        stub_fname = (func_name or "stub") + "_stub.py"
        stub_path = os.path.join(stub_dir, stub_fname)
        stub_content = targets.implementation_stub
        if synthesis_result and synthesis_result.success:
            # Replace the TODO body with synthesized body
            stub_content = stub_content.replace(
                "    pass  # TODO: implement", synthesis_result.body
            )
        with open(stub_path, "w", encoding="utf-8") as f:
            f.write(stub_content + "\n")
        materialized_stub_path = stub_path

    # Update workflow record
    workflow = load_workflow_record(project_root, target)
    if workflow is not None:
        workflow.state = "compiled"
        workflow.compiled_targets_path = compiled_targets_path
        workflow.materialized_test_path = materialized_test_path
        workflow.expected_kill_set = dict(targets.expected_kill_set)
        workflow.generation_mode = generation_mode
        workflow.recommended_next_action = "prescriptive_spec_verify"
        workflow.recommended_next_args = {
            "path": path,
            "target": target,
        }
        save_workflow_record(project_root, workflow)

    result = targets.to_dict()
    result["spec_id"] = spec.spec_id
    result["target_key"] = spec.target_key
    result["problem_class"] = spec.problem_class
    result["generation_mode"] = generation_mode
    result["synthesis_gate"] = gate_result.to_dict()

    if synthesis_result:
        result["synthesis_result"] = synthesis_result.to_dict()
    if materialized_test_path:
        result["materialized_test_path"] = os.path.relpath(materialized_test_path, project_root)
    if materialized_stub_path:
        result["materialized_stub_path"] = os.path.relpath(materialized_stub_path, project_root)
    if targets.implementation_stub:
        result["implementation_stub"] = targets.implementation_stub

    # ── Generation-mode-aware next_actions ─────────────────────────
    target_file = _target_to_file(target)
    next_actions: list[NextAction] = []

    if generation_mode == "symbolic_only":
        next_actions.append(
            NextAction(
                tool="",
                args={},
                reason="Write synthesized stub to target file, then verify",
            )
        )
        next_actions.append(
            NextAction(
                tool="prescriptive_spec_verify",
                args={"path": path, "target": target, "file": target_file},
                reason="Verify synthesized code against prescriptive spec",
            )
        )
    elif generation_mode == "symbolic_then_llm_repair":
        result["repair_prompt"] = _render_repair_prompt(
            spec, targets, gate_result, synthesis_result
        )
        next_actions.append(
            NextAction(
                tool="",
                args={},
                reason="Use repair_prompt to fill body, then verify",
            )
        )
        next_actions.append(
            NextAction(
                tool="prescriptive_spec_verify",
                args={"path": path, "target": target, "file": target_file},
                reason="Verify repaired code against prescriptive spec",
            )
        )
    else:
        # llm_direct — existing behavior
        if targets.generation_constraints:
            result["generation_prompt"] = _render_generation_prompt(
                spec.target_key, targets.generation_constraints
            )
        next_actions.append(
            NextAction(
                tool="",
                args={},
                reason="Write code guided by the generation_prompt above, then verify",
            )
        )
        next_actions.append(
            NextAction(
                tool="prescriptive_spec_verify",
                args={"path": path, "target": target, "file": target_file},
                reason="Verify code refinement against prescriptive spec after writing code",
            )
        )

    # Witness info for transparency
    if witnesses:
        result["witnesses"] = [w.to_dict() for w in witnesses]

    result["next_actions"] = serialize_next_actions(next_actions)
    return result


def impl_prescriptive_spec_verify(
    path: str,
    file: str,
    function: str | None,
    helpers: dict[str, Any],
    target: str = "",
) -> dict[str, Any]:
    """Verify code against its PrescriptiveSpec."""
    from lintgate.specification.prescriptive_backends import (
        PrescriptiveAdapter,
        select_backend,
    )
    from lintgate.specification.prescriptive_spec import (
        load_all_specs,
        load_spec,
        load_workflow_record,
        save_workflow_record,
    )

    project_root = helpers["_validate_project_root"](path)

    # When target is provided, try direct spec lookup first
    spec = None
    if target:
        spec = load_spec(project_root, target)
        if spec and not file:
            file = _target_to_file(target)
        if spec and not function:
            function = _target_to_func(target)

    # Find matching specs via file/function if not found by target
    if spec is None and function:
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
    elif spec is None:
        # Verify all specs for functions in the file
        all_specs = load_all_specs(project_root)
        file_module = file.replace("/", ".").removesuffix(".py")
        matching = [s for s in all_specs.values() if file_module in s.target_key]
        if not matching:
            return {
                "status": "no_specs",
                "message": f"No prescriptive specs found for file '{file}'",
                "next_actions": serialize_next_actions(
                    [
                        NextAction(
                            tool="prescriptive_spec_compose",
                            args={"path": path, "target": f"{file_module}::*"},
                            reason="Compose specs for functions in this file",
                        ),
                    ]
                ),
            }
        # Verify first match (could extend to multi-verify)
        spec = matching[0]

    if spec is None:
        return {
            "status": "no_spec",
            "message": f"No prescriptive spec found for function '{function}'",
            "next_actions": serialize_next_actions(
                [
                    NextAction(
                        tool="prescriptive_spec_compose",
                        args={"path": path, "target": target or function or ""},
                        reason="Compose a spec first",
                    ),
                ]
            ),
        }

    backend = select_backend(spec)
    targets = backend.compile(spec)
    adapter = PrescriptiveAdapter()
    verdict = adapter.verify_refinement(spec, targets, project_root, file, function)

    # Update workflow record with evidence
    effective_target = target or spec.target_key
    workflow = load_workflow_record(project_root, effective_target)
    if workflow is not None:
        workflow.state = "verifying"
        workflow.structural_evidence = verdict.get("structural_evidence", [])
        workflow.behavioral_evidence = verdict.get("behavioral_evidence", [])
        workflow.convergence_signal = verdict.get("convergence", {}) or {}

    # Step 6: Tightened next_actions based on verdict
    next_actions = []
    overall = verdict["overall"]
    if overall == "pass":
        next_actions.append(
            NextAction(
                tool="spec_gate_check",
                args={"path": path},
                reason="All checks pass — verify optimization hints are backed by spec",
            )
        )
        if workflow is not None:
            workflow.state = "complete"
            workflow.recommended_next_action = "spec_gate_check"
            workflow.recommended_next_args = {"path": path}
    elif overall == "fail":
        # Structural failures → edit code
        structural_fails = [
            e for e in verdict.get("structural_evidence", []) if e.get("status") == "fail"
        ]
        behavioral_fails = [
            e for e in verdict.get("behavioral_evidence", []) if e.get("status") == "fail"
        ]
        if structural_fails:
            next_actions.append(
                NextAction(
                    tool="",
                    args={},
                    reason=f"Edit code to satisfy {len(structural_fails)} failing structural invariant(s)",
                )
            )
        if behavioral_fails:
            next_actions.append(
                NextAction(
                    tool="platonic_converge",
                    args={"path": path, "file": file},
                    reason="Converge toward prescriptive spec behavioral contract",
                )
            )
        if workflow is not None:
            workflow.recommended_next_action = "platonic_converge" if behavioral_fails else ""
            workflow.recommended_next_args = (
                {"path": path, "file": file} if behavioral_fails else {}
            )
    else:
        # partial or unknown → need more data
        next_actions.append(
            NextAction(
                tool="mutation_run_sampling",
                args={"path": path, "file": file},
                reason="Run mutation sampling to get fresh behavioral data",
            )
        )
        next_actions.append(
            NextAction(
                tool="spec_file_analyze",
                args={"path": path, "file": file},
                reason="Analyze specification state of this file",
            )
        )
        if workflow is not None:
            workflow.recommended_next_action = "mutation_run_sampling"
            workflow.recommended_next_args = {"path": path, "file": file}

    if workflow is not None:
        save_workflow_record(project_root, workflow)

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
            "next_actions": serialize_next_actions(
                [
                    NextAction(
                        tool="prescriptive_spec_compose",
                        args={"path": path, "target": "<function_key>"},
                        reason="Compose a prescriptive spec",
                    ),
                ]
            ),
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
        "next_actions": serialize_next_actions(
            [
                NextAction(
                    tool="prescriptive_spec_compose",
                    args={"path": path, "target": "<uncovered_function>"},
                    reason="Add specs for uncovered functions",
                ),
            ]
        ),
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
    constraints = targets.generation_constraints[:8] if hasattr(targets, "generation_constraints") else []
    if constraints:
        lines.append("### Constraints")
        for c in sorted(constraints, key=lambda x: x.get("priority", 5)):
            desc = c.get("description", "")
            ct = c.get("constraint_type", "")
            prefix = "MUST NOT" if ct == "must_not_use" else "MUST" if ct == "must_use" else "NOTE"
            lines.append(f"- {prefix}: {desc}")
        lines.append("")

    # 3. Semantic hints (CUSTOM predicate descriptions, max 5)
    from lintgate.specification.prescriptive_spec import PredicateOp

    custom_hints = [
        inv.description
        for inv in spec.invariants
        if inv.predicate.op == PredicateOp.CUSTOM
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
