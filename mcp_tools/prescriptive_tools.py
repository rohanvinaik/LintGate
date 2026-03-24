"""Prescriptive spec tools — compose, compile, verify, status.

Implementation functions live in _prescriptive_impl.py.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ._prescriptive_impl import (
    impl_prescriptive_spec_compile,
    impl_prescriptive_spec_compose,
    impl_prescriptive_spec_status,
    impl_prescriptive_spec_verify,
)


from mcp_tools._disk_helpers import tool_response

def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register prescriptive specification tools on the shared MCP instance."""

    @mcp.tool()
    def prescriptive_spec_compose(
        path: str,
        target: str,
        mode: str = "auto",
        description: str = "",
        claims: list[str] | None = None,
        interface_hint: str = "",
    ) -> str:
        """Compose a PrescriptiveSpec for a function or module.

        WHEN TO USE: Before writing new code, compose a behavioral contract
        from theory + compass. For existing code, enrich with prescriptive
        obligations. This is the entry point for specification-first development.

        CHAIN: compose → prescriptive_spec_compile → [write code] → prescriptive_spec_verify
        REQUIRES: compass_update (or compass loaded). Theory profile recommended.
        COST: Fast (<1s). Reads cached theory/compass, no computation.

        mode:
        - "prospective": Build spec from theory + compass alone (no existing code)
        - "retrospective": Enrich existing FunctionSpecification with prescriptive contract
        - "auto": Detect from existing code (default)

        Example: prescriptive_spec_compose(path="/my/project", target="module::function")
        Example: prescriptive_spec_compose(path="/my/project", target="new:validate_input", mode="prospective",
            description="Validate user input against schema",
            claims=["must be pure", "must return bool"])

        Args:
            path: Project root path.
            target: Function key (module::function) or new function name (new:name).
            mode: "prospective" | "retrospective" | "auto" (default).
            description: NL description of the function — sentences become invariants.
            claims: Explicit NL claims (e.g. ["must be pure", "at most 2 parameters"]).
            interface_hint: JSON string of {parameters, return_type, problem_class, ...}.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_prescriptive_spec_compose(
            path, target, mode, helpers,
            description=description, claims=claims, interface_hint=interface_hint,
        )
        summary = f"Spec composed for {target}. Mode: {mode}."
        return tool_response(result, "prescriptive_spec_compose", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def prescriptive_spec_compile(
        path: str,
        target: str,
    ) -> str:
        """Compile PrescriptiveSpec into test skeletons + generation constraints.

        WHEN TO USE: After composing a spec, compile it into actionable artifacts:
        property test skeletons, scenario test skeletons, expected mutation kill set,
        and structured generation constraints for LLM code generation. The output
        includes a generation_prompt section with MUST/MUST NOT directives.

        CHAIN: prescriptive_spec_compose → compile → [write code using generation_prompt] → prescriptive_spec_verify
        REQUIRES: A spec must exist for the target (call compose first).
        COST: Fast (<1s). Pure computation on the spec IR.

        Example: prescriptive_spec_compile(path="/my/project", target="module::function")

        Args:
            path: Project root path.
            target: Function key matching a previously composed spec.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_prescriptive_spec_compile(path, target, helpers)
        summary = f"Spec compiled for {target}. Test skeletons + generation constraints ready."
        return tool_response(result, "prescriptive_spec_compile", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def prescriptive_spec_verify(
        path: str,
        file: str = "",
        function: str | None = None,
        target: str = "",
    ) -> str:
        """Verify code against its PrescriptiveSpec (refinement check).

        WHEN TO USE: After writing or editing code that has a prescriptive spec.
        Returns two evidence classes:
        - structural_evidence: AST checks (return type, purity, param count, etc.)
        - behavioral_evidence: mutation kill expectations vs cached mutation state
        Does NOT re-run mutations — reads cached results.

        CHAIN: prescriptive_spec_compose → prescriptive_spec_compile → [write code] → verify
        REQUIRES: Spec must exist for at least one function in the file.
        COST: Fast (<1s). AST parse + cached state reads.
        FOLLOW-UP: If verdict is fail/partial, use mutation_run_sampling for fresh behavioral data.

        Example: prescriptive_spec_verify(path="/my/project", target="core.utils::validate")
        Example: prescriptive_spec_verify(path="/my/project", file="core/utils.py", function="validate")

        Args:
            path: Project root path.
            file: Relative or absolute path to the Python file.
            function: Optional function name to verify.
            target: Preferred — module::function key. Derives file/function automatically.
        """
        # When target is provided, derive file/function from it
        if target and not file:
            from mcp_tools._prescriptive_impl import _target_to_file, _target_to_func

            file = _target_to_file(target)
            function = function or _target_to_func(target)
        project_root = helpers["_validate_project_root"](path)
        result = impl_prescriptive_spec_verify(path, file, function, helpers, target=target)
        verdict = result.get("verdict", "unknown")
        summary = f"Spec verify for {target or file}: {verdict}."
        return tool_response(result, "prescriptive_spec_verify", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def prescriptive_spec_status(
        path: str,
    ) -> str:
        """Show prescriptive coverage, sigma convergence, problem class distribution.

        WHEN TO USE: To get an overview of prescriptive spec coverage across the
        project. Shows how many functions have specs, problem class distribution,
        and mean prescriptive sigma. Suggests uncovered functions for compose.

        CHAIN: status → prescriptive_spec_compose (for uncovered functions)
        REQUIRES: Nothing — works on empty state (reports zero specs).
        COST: Fast (<1s). Reads index file only.

        Example: prescriptive_spec_status(path="/my/project")

        Args:
            path: Project root path.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_prescriptive_spec_status(path, helpers)
        total = result.get("total_specs", 0)
        summary = f"Prescriptive status: {total} specs composed."
        return tool_response(result, "prescriptive_spec_status", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def prescriptive_code_scaffold(
        path: str,
        target: str,
    ) -> str:
        """Generate an implementation skeleton — you fill in only the computation.

        Returns a function body where the structure is already written:
        - Function signature with typed parameters
        - Guard clauses from invariants (if X < 0: raise ValueError)
        - Forbidden-behavior assertions
        - if/elif/else branching from specification complexity
        - Return statement shape

        You fill in the «PLACEHOLDER» markers — typically 3-8 expressions
        like «CONDITION_0» or «BRANCH_1_RESULT». Each has fill_instructions
        explaining exactly what to write.

        Call this after prescriptive_spec_compile. The compile step gives you
        test skeletons. This step gives you the implementation skeleton.
        Fill both, then prescriptive_spec_verify checks everything.

        Args:
            path: Project root path.
            target: Target key (module::function or module.function).
        """
        from lintgate.specification.prescriptive.code_scaffold import generate_code_scaffold
        from lintgate.specification.prescriptive.spec import load_spec

        project_root = helpers["_validate_project_root"](path)
        spec = load_spec(project_root, target)
        if spec is None:
            return json.dumps({"error": f"No spec found for '{target}'. Run prescriptive_spec_compose first."})

        scaffold = generate_code_scaffold(spec)
        result = scaffold.to_dict()

        # Write scaffold code to a file the agent can reference
        stub_dir = os.path.join(project_root, ".lintgate", "generated_stubs")
        os.makedirs(stub_dir, exist_ok=True)
        stub_name = target.replace("::", "_").replace(".", "_") + "_stub.py"
        stub_path = os.path.join(stub_dir, stub_name)
        with open(stub_path, "w", encoding="utf-8") as sf:
            sf.write(scaffold.code if hasattr(scaffold, "code") else result.get("code", ""))

        from lintgate.next_action import NextAction, serialize_next_actions
        result["stub_file"] = stub_path
        result["next_actions"] = serialize_next_actions([
            NextAction(
                tool="prescriptive_spec_verify",
                args={"path": path, "target": target},
                reason="Verify filled implementation against the prescriptive spec",
            ),
        ])

        # Rich summary: placeholder markers + fill instructions
        lines = [f"Scaffold written to: {stub_path}", ""]
        placeholders = result.get("placeholders", [])
        if placeholders:
            lines.append(f"Fill {len(placeholders)} placeholder(s):")
            for ph in placeholders[:5]:
                marker = ph.get("marker", "«?»")
                instr = ph.get("fill_instructions", "")[:80]
                lines.append(f"  {marker} — {instr}")
        else:
            lines.append(f"Fill {scaffold.placeholder_count} «PLACEHOLDER» markers in the stub file.")

        params = result.get("parameters", [])
        ret = result.get("return_type", "")
        if params or ret:
            sig_parts = [f"{p.get('name', '?')}: {p.get('type', '?')}" for p in params[:5]] if isinstance(params, list) else []
            lines.append(f"\n  Signature: ({', '.join(sig_parts)}) -> {ret}")

        lines.append("\nNext: fill placeholders, then prescriptive_spec_verify")
        summary = "\n".join(lines)
        return tool_response(result, "prescriptive_code_scaffold", project_root, summary, next_actions=result.get("next_actions"))

    return {
        "prescriptive_spec_compose": prescriptive_spec_compose,
        "prescriptive_spec_compile": prescriptive_spec_compile,
        "prescriptive_spec_verify": prescriptive_spec_verify,
        "prescriptive_spec_status": prescriptive_spec_status,
    }
