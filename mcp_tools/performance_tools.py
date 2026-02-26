"""Performance tools — inspect_algebra, generate_property_tests."""

from __future__ import annotations

import json
from typing import Any


def _build_manifest_summary(manifest: Any, project_root: str) -> dict[str, Any]:
    """Build a compact summary of the property manifest."""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for name, func in manifest.functions.items():
        source = func.source_file or project_root
        # Use property confidence (mutation-gated) when available, else purity confidence
        effective_conf = func.purity.confidence
        if func.properties:
            effective_conf = func.properties[0].confidence
        entry: dict[str, Any] = {
            "name": name,
            "is_pure": func.purity.is_pure,
            "confidence": effective_conf,
        }
        if func.properties:
            entry["properties"] = [p.kind.value for p in func.properties]
            # Surface mutation gate evidence if present
            for p in func.properties:
                if p.evidence and "[MUTATION" in p.evidence:
                    entry["mutation_gate"] = p.evidence
                    break
        if func.optimization_hints:
            entry["hints"] = list(func.optimization_hints)
        by_file.setdefault(source, []).append(entry)

    return {
        "project": project_root,
        "summary": {
            "total_functions": manifest.pure_count + manifest.impure_count,
            "pure_functions": manifest.pure_count,
            "impure_functions": manifest.impure_count,
            "purity_ratio": round(
                manifest.pure_count / max(manifest.pure_count + manifest.impure_count, 1), 3
            ),
            "property_distribution": {
                k.value: v for k, v in manifest.property_distribution.items()
            },
            "optimization_opportunities": len(manifest.optimization_potential),
        },
        "files": dict(sorted(by_file.items())),
    }


_FILTER_CHECKS: dict[str, Any] = {
    "pure": lambda f: f.get("is_pure"),
    "impure": lambda f: not f.get("is_pure"),
    "cacheable": lambda f: "cacheable" in f.get("hints", []),
    "parallelizable": lambda f: "parallelizable" in f.get("hints", []),
}


def _matches_filter(
    entry: dict[str, Any], filter_type: str | None, func_filter: str | None
) -> bool:
    """Check if a single function entry passes the requested filters."""
    if filter_type and filter_type in _FILTER_CHECKS and not _FILTER_CHECKS[filter_type](entry):
        return False
    return not (func_filter and func_filter.lower() not in entry["name"].lower())


def _filter_manifest(
    manifest_data: dict[str, Any],
    filter_type: str | None,
    func_filter: str | None,
) -> dict[str, Any]:
    """Apply optional filters to the manifest summary."""
    if not filter_type and not func_filter:
        return manifest_data

    filtered_files: dict[str, list[dict[str, Any]]] = {}
    for path, funcs in manifest_data.get("files", {}).items():
        matching = [f for f in funcs if _matches_filter(f, filter_type, func_filter)]
        if matching:
            filtered_files[path] = matching

    manifest_data["files"] = filtered_files
    manifest_data["filter_applied"] = {"type": filter_type, "name": func_filter}
    return manifest_data


def _build_manifest_for_project(path: str, helpers: Any) -> tuple[str, Any, list[str]]:
    """Common setup: validate project, discover files, build manifest."""
    from lintgate.channels.performance_channel import _discover_python_files
    from lintgate.linters.performance_checks.manifest import build_manifest

    project_root = helpers["_validate_project_root"](path)
    py_files = _discover_python_files(project_root)
    manifest = build_manifest(project_root, py_files) if py_files else None
    return project_root, manifest, py_files


def _select_property_candidates(
    manifest: Any,
    function_filter: str | None,
    max_functions: int,
    hotspot_functions: set[str] | None = None,
) -> list[tuple[str, Any]]:
    """Select the top pure functions with interesting algebraic properties."""
    from lintgate.linters.performance_checks.algebra_types import PropertyKind

    candidates: list[tuple[str, Any, int]] = []
    for name, func in manifest.functions.items():
        if not func.purity.is_pure:
            continue
        interesting = sum(1 for p in func.properties if p.kind != PropertyKind.PURE)
        if interesting == 0:
            continue
        if function_filter and function_filter.lower() not in name.lower():
            continue

        # If prefer_mutation_hotspots is on, heavily boost functions that are in the hotspot list
        # Note: manifest name is fully qualified (e.g. "src.file.func"), but hotspots from
        # line-based mutmut exports generally only have the base function name via AST.
        base_name = name.split(".")[-1]
        if hotspot_functions and (name in hotspot_functions or base_name in hotspot_functions):
            interesting += 100

        candidates.append((name, func, interesting))

    candidates.sort(key=lambda x: x[2], reverse=True)
    return [(name, func) for name, func, _ in candidates[:max_functions]]


def _build_test_entry(name: str, func: Any) -> dict[str, Any]:
    """Build a single test-generation result entry."""
    from lintgate.integrations.hypothesis_bridge import generate_hypothesis_template
    from lintgate.integrations.icontract_bridge import generate_icontract_decorators

    entry: dict[str, Any] = {
        "function": name,
        "source_file": func.source_file,
        "properties": [p.kind.value for p in func.properties],
    }

    template = generate_hypothesis_template(name, func)
    if template:
        entry["hypothesis_test"] = template

    decorators = generate_icontract_decorators(func)
    if decorators:
        entry["icontract_decorators"] = decorators

    return entry


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register performance analysis tools on the shared MCP instance."""

    @mcp.tool()
    def inspect_algebra(
        path: str,
        filter_by: str | None = None,
        function: str | None = None,
    ) -> str:
        """Inspect the algebraic property manifest for a project.

        WHEN TO USE: After controlplane_run shows performance findings, or when
        you want to understand which functions are pure, cacheable, parallelizable,
        or have other algebraic properties. This is the drill-down tool for the
        performance channel.

        Example: inspect_algebra(path="/my/project")
        Example: inspect_algebra(path="/my/project", filter_by="cacheable")
        Example: inspect_algebra(path="/my/project", function="compute_hash")

        Args:
            path: Project root path.
            filter_by: Optional filter — "pure", "impure", "cacheable", "parallelizable".
            function: Optional function name substring to search for.
        """
        project_root, manifest, py_files = _build_manifest_for_project(path, helpers)
        if not py_files or manifest is None:
            return json.dumps({"error": "No Python files found in project"})

        summary = _build_manifest_summary(manifest, project_root)
        result = _filter_manifest(summary, filter_by, function)
        return helpers["_json_dumps"](result, output_mode="compact")

    @mcp.tool()
    def generate_property_tests(
        path: str,
        function: str | None = None,
        max_functions: int = 5,
        prefer_mutation_hotspots: bool = False,
    ) -> str:
        """Generate Hypothesis property-based test templates from algebraic properties.

        WHEN TO USE: After inspect_algebra reveals pure functions with algebraic
        properties (commutative, associative, idempotent, bounded). Generates
        ready-to-use pytest + Hypothesis test stubs that verify these properties
        hold under random inputs.

        Also generates icontract decorator suggestions for runtime enforcement.

        Example: generate_property_tests(path="/my/project")
        Example: generate_property_tests(path="/my/project", function="compute_score")

        Args:
            path: Project root path.
            function: Optional function name to generate tests for. If not given,
                generates for the top functions with the most algebraic properties.
            max_functions: Max number of functions to generate tests for (default 5).
            prefer_mutation_hotspots: If True, prioritizes generation for pure functions that have surviving mutations.
        """
        import os

        from lintgate.linters.performance_checks.algebra_types import PropertyKind
        from lintgate.mutation.ci_stats import load_mutation_hotspots

        project_root, manifest, py_files = _build_manifest_for_project(path, helpers)
        if not py_files or manifest is None:
            return json.dumps({"error": "No Python files found in project"})

        hotspot_functions = None
        if prefer_mutation_hotspots:
            survivors_path = os.path.join(project_root, "mutants", "mutmut-survivors.json")
            hotspots = load_mutation_hotspots(survivors_path)
            # `name` from manifest contains the fully-qualified name, we extract baseline function name
            hotspot_functions = {h.get("function") for h in hotspots if h.get("function")}

        candidates = _select_property_candidates(manifest, function, max_functions, hotspot_functions)

        if not candidates:
            note = "No pure functions with algebraic properties found"
            if function:
                note += f" matching '{function}'"
            return json.dumps(
                {"note": note, "suggestion": "Run inspect_algebra to see all functions"}
            )

        results = [_build_test_entry(name, func) for name, func in candidates]

        total_with_props = sum(
            1
            for f in manifest.functions.values()
            if f.purity.is_pure and any(p.kind != PropertyKind.PURE for p in f.properties)
        )

        output: dict[str, Any] = {
            "generated_for": len(results),
            "total_pure_with_properties": total_with_props,
            "functions": results,
            "note": "Review and customize generated code before saving. Use Write tool to create test files.",
        }

        return helpers["_json_dumps"](output)

    return {
        "inspect_algebra": inspect_algebra,
        "generate_property_tests": generate_property_tests,
    }
