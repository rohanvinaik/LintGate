"""Prescriptive spec tools — compose, compile, verify, status.

Implementation functions live in _prescriptive_impl.py.
"""

from __future__ import annotations

from typing import Any

from ._prescriptive_impl import (
    impl_prescriptive_spec_compile,
    impl_prescriptive_spec_compose,
    impl_prescriptive_spec_status,
    impl_prescriptive_spec_verify,
)


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register prescriptive specification tools on the shared MCP instance."""

    @mcp.tool()
    def prescriptive_spec_compose(
        path: str,
        target: str,
        mode: str = "auto",
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
        Example: prescriptive_spec_compose(path="/my/project", target="new:validate_input", mode="prospective")

        Args:
            path: Project root path.
            target: Function key (module::function) or new function name (new:name).
            mode: "prospective" | "retrospective" | "auto" (default).
        """
        result = impl_prescriptive_spec_compose(path, target, mode, helpers)
        return str(helpers["_json_dumps"](result, output_mode="compact"))

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
        result = impl_prescriptive_spec_compile(path, target, helpers)
        return str(helpers["_json_dumps"](result, output_mode="compact"))

    @mcp.tool()
    def prescriptive_spec_verify(
        path: str,
        file: str,
        function: str | None = None,
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

        Example: prescriptive_spec_verify(path="/my/project", file="core/utils.py")
        Example: prescriptive_spec_verify(path="/my/project", file="core/utils.py", function="validate")

        Args:
            path: Project root path.
            file: Relative or absolute path to the Python file.
            function: Optional function name to verify.
        """
        result = impl_prescriptive_spec_verify(path, file, function, helpers)
        return str(helpers["_json_dumps"](result, output_mode="compact"))

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
        result = impl_prescriptive_spec_status(path, helpers)
        return str(helpers["_json_dumps"](result, output_mode="compact"))

    return {
        "prescriptive_spec_compose": prescriptive_spec_compose,
        "prescriptive_spec_compile": prescriptive_spec_compile,
        "prescriptive_spec_verify": prescriptive_spec_verify,
        "prescriptive_spec_status": prescriptive_spec_status,
    }
