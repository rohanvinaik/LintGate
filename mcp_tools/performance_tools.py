"""Performance tools — inspect_algebra, generate_property_tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
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
            "extraction_safety": func.extraction_safety,
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

    # Build pure_functions_by_file from manifest
    pure_by_file = manifest.get_pure_functions_by_file()

    # Compute extraction_safety distribution
    safety_dist: dict[str, int] = {"safe": 0, "needs_module_state": 0, "unsafe": 0}
    for func in manifest.functions.values():
        safety_dist[func.extraction_safety] = safety_dist.get(func.extraction_safety, 0) + 1

    return {
        "project": project_root,
        "summary": {
            "total_functions": manifest.pure_count + manifest.impure_count,
            "pure_functions": manifest.pure_count,
            "impure_functions": manifest.impure_count,
            "purity_ratio": round(
                manifest.pure_count / max(manifest.pure_count + manifest.impure_count, 1),
                3,
            ),
            "property_distribution": {
                k.value: v for k, v in manifest.property_distribution.items()
            },
            "optimization_opportunities": len(manifest.optimization_potential),
            "extraction_safety": safety_dist,
        },
        "pure_functions_by_file": {k: sorted(v) for k, v in sorted(pure_by_file.items())},
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


def _generate_from_prescriptions(
    path: str,
    function: str | None,
    max_functions: int,
    helpers: Any,
) -> str:
    """Redirect to the live mutation prescription tools."""
    from lintgate.next_action import NextAction, serialize_next_actions

    _ = function
    _ = max_functions
    helpers["_validate_project_root"](path)
    next_actions = serialize_next_actions(
        [
            NextAction(
                tool="mutation_run_sampling",
                args={"path": path},
                reason="Run mutation sampling to generate survival profiles",
            ),
            NextAction(
                tool="mutation_prescribe_tests",
                args={"path": path},
                reason="Generate targeted test skeletons from mutation profiles",
                condition="after mutation_run_sampling or mutation_run_full",
            ),
        ]
    )
    return json.dumps(
        {
            "note": "from_prescriptions mode has moved to dedicated mutation tools.",
            "workflow": "mutation_run_sampling → mutation_prescribe → mutation_prescribe_tests",
            "next_actions": next_actions,
        }
    )


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

    _ = hotspot_functions
    candidates: list[tuple[str, Any, int]] = []
    for name, func in manifest.functions.items():
        if not func.purity.is_pure:
            continue
        interesting = sum(1 for p in func.properties if p.kind != PropertyKind.PURE)
        if interesting == 0:
            continue
        if function_filter and function_filter.lower() not in name.lower():
            continue

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


def _prepare_hypothesis_template(
    function: str, func: Any, max_examples: int, deadline_ms: int
) -> str | None:
    """Prepare a Hypothesis test template with correct imports and settings."""
    from lintgate.integrations.hypothesis_bridge import generate_hypothesis_template

    template = generate_hypothesis_template(function.split(".")[-1], func)
    if not template:
        return None

    module_path = function.rsplit(".", 1)[0]
    template = template.replace("# TODO: fix import path", "")
    template = template.replace(
        f"from ... import {function.split('.')[-1]}",
        f"from {module_path} import {function.split('.')[-1]}",
    )

    settings_block = (
        f"\nfrom hypothesis import settings\n"
        f"settings.register_profile('lintgate', max_examples={max_examples}, "
        f"deadline={deadline_ms})\n"
        f"settings.load_profile('lintgate')\n"
    )
    return settings_block + template


def _parse_property_test_output(
    output: str,
) -> tuple[list[str], str | None]:
    """Extract refinement hints and counterexample from pytest output."""
    refinement_hints: list[str] = []
    counterexample: str | None = None

    match = re.search(r"Falsifying example:.*\n(.*)", output)
    if match:
        counterexample = match.group(1).strip()

    hint_patterns = {
        ("Idempotent", "test_is_idempotent"): (
            "Idempotency violated - check if function has hidden state or precision issues."
        ),
        ("Commutative", "test_is_commutative"): (
            "Commutativity violated - check if argument order matters for internal logic."
        ),
        ("Bounded", "test_is_bounded"): (
            "Bounds violated - check for edge cases at extremes (min/max)."
        ),
    }

    for keywords, hint in hint_patterns.items():
        if any(kw in output for kw in keywords):
            refinement_hints.append(hint)

    if not refinement_hints:
        refinement_hints.append(
            "Property violated - consider refining the property hypothesis or fixing the implementation."
        )

    return refinement_hints, counterexample


def _execute_property_tests(template: str, project_root: str, function: str) -> dict[str, Any]:
    """Write template to temp file, execute via pytest, and return structured result."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", dir=project_root, delete=False) as tmp:
        tmp.write(template)
        tmp_path = tmp.name

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

        proc = subprocess.run(
            ["pytest", tmp_path, "-v", "--tb=short"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )

        success = proc.returncode == 0
        output = proc.stdout + proc.stderr

        result: dict[str, Any] = {
            "success": success,
            "function": function,
            "output": output,
            "refinement_hints": [],
        }

        if not success:
            hints, counterexample = _parse_property_test_output(output)
            result["refinement_hints"] = hints
            if counterexample is not None:
                result["counterexample"] = counterexample

        return result

    except subprocess.TimeoutExpired:
        return {
            "error": "Hypothesis execution timed out (15s budget exceeded).",
            "suggestion": "Try reducing max_examples or increasing deadline_ms.",
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _impl_generate_property_tests(
    path: str,
    function: str | None,
    max_functions: int,
    prefer_mutation_hotspots: bool,
    from_prescriptions: bool,
    helpers: Any,
) -> str:
    """Implementation for generate_property_tests."""
    from lintgate.linters.performance_checks.algebra_types import PropertyKind

    if from_prescriptions:
        return _generate_from_prescriptions(path, function, max_functions, helpers)

    project_root, manifest, py_files = _build_manifest_for_project(path, helpers)
    if not py_files or manifest is None:
        return json.dumps({"error": "No Python files found in project"})

    hotspot_functions = None
    if prefer_mutation_hotspots:
        hotspot_functions = set()

    candidates = _select_property_candidates(manifest, function, max_functions, hotspot_functions)

    if not candidates:
        note = "No pure functions with algebraic properties found"
        if function:
            note += f" matching '{function}'"
        return json.dumps({"note": note, "suggestion": "Run inspect_algebra to see all functions"})

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
    if prefer_mutation_hotspots:
        output["note_mutation_hotspots"] = (
            "prefer_mutation_hotspots: use mutation_run_full → mutation_prescribe for hotspot data."
        )

    return helpers["_json_dumps"](output)


# ---------------------------------------------------------------------------
# MCP registration — thin wrappers delegating to impl/helper functions above
# ---------------------------------------------------------------------------


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
        from_prescriptions: bool = False,
    ) -> str:
        """Generate Hypothesis property-based test templates from algebraic properties.

        WHEN TO USE: After inspect_algebra reveals pure functions with algebraic
        properties (commutative, associative, idempotent, bounded). Generates
        ready-to-use pytest + Hypothesis test stubs that verify these properties
        hold under random inputs.

        Also generates icontract decorator suggestions for runtime enforcement.

        `from_prescriptions=True` redirects to the dedicated mutation tools
        (mutation_run_sampling → mutation_prescribe → mutation_prescribe_tests).

        Example: generate_property_tests(path="/my/project")
        Example: generate_property_tests(path="/my/project", function="compute_score")
        Example: generate_property_tests(path="/my/project", from_prescriptions=True)

        Args:
            path: Project root path.
            function: Optional function name to generate tests for. If not given,
                generates for the top functions with the most algebraic properties.
            max_functions: Max number of functions to generate tests for (default 5).
            prefer_mutation_hotspots: Deprecated compatibility flag (ignored).
            from_prescriptions: Deprecated compatibility flag (returns archived notice).
        """
        return _impl_generate_property_tests(
            path,
            function,
            max_functions,
            prefer_mutation_hotspots,
            from_prescriptions,
            helpers,
        )

    @mcp.tool()
    def run_property_tests(
        path: str,
        function: str,
        max_examples: int = 50,
        deadline_ms: int = 500,
    ) -> str:
        """Execute generated property tests for a function and capture counterexamples.

        WHEN TO USE: After generating property tests, use this to validate them
        and find counterexamples. Closes the feedback loop by providing
        refinement hints if tests fail.

        Args:
            path: Project root path.
            function: Fully qualified function name (e.g. "src.module.func").
            max_examples: Hypothesis max_examples setting (budget).
            deadline_ms: Hypothesis deadline setting in milliseconds.
        """
        project_root = helpers["_validate_project_root"](path)
        _, manifest, _py_files = _build_manifest_for_project(path, helpers)

        if not manifest or function not in manifest.functions:
            return json.dumps(
                {"error": f"Function '{function}' not found in performance manifest."}
            )

        func = manifest.functions[function]
        template = _prepare_hypothesis_template(function, func, max_examples, deadline_ms)

        if not template:
            return json.dumps({"error": "Failed to generate Hypothesis template for function."})

        return json.dumps(_execute_property_tests(template, project_root, function))

    return {
        "inspect_algebra": inspect_algebra,
        "generate_property_tests": generate_property_tests,
        "run_property_tests": run_property_tests,
    }
