"""Test effectiveness tools — analyze_test_strength, inspect_test_assertions."""

from __future__ import annotations

import json
from typing import Any


def _build_manifest_for_project(path: str, helpers: Any) -> tuple[str, Any, list[str], list[str]]:
    """Common setup: validate project, discover files, build manifest."""
    from lintgate.channels.structure_channel import _discover_python_files
    from lintgate.linters.test_effectiveness.manifest import build_test_effectiveness_manifest
    from lintgate.linters.test_effectiveness.test_analyzer import (
        _discover_source_files,
        _discover_test_files,
    )

    project_root = helpers["_validate_project_root"](path)
    py_files = _discover_python_files(project_root)
    test_files = _discover_test_files(project_root)
    source_files = _discover_source_files(project_root)

    manifest = (
        build_test_effectiveness_manifest(project_root, source_files, test_files)
        if py_files and test_files
        else None
    )
    return project_root, manifest, py_files, test_files


def _build_summary(manifest: Any, project_root: str) -> dict[str, Any]:
    """Build a compact summary of the effectiveness manifest."""
    vulnerable = sorted(
        (
            (name, fe)
            for name, fe in manifest.functions.items()
            if fe.mutation_vulnerability > 0.5 and fe.test_count > 0
        ),
        key=lambda x: x[1].mutation_vulnerability,
        reverse=True,
    )

    top_vulnerable = [
        {
            "function": name,
            "vulnerability": round(fe.mutation_vulnerability, 3),
            "effectiveness": round(fe.effectiveness_score, 3),
            "semantic_ratio": round(fe.semantic_ratio, 3),
            "test_count": fe.test_count,
            "assertion_count": len(fe.assertions),
        }
        for name, fe in vulnerable[:10]
    ]

    untested = [
        name
        for name, fe in manifest.functions.items()
        if fe.test_count == 0 and not name.startswith("_")
    ]

    return {
        "project": project_root,
        "summary": {
            "effectiveness_score": round(manifest.project_score, 3),
            "functions_analyzed": manifest.functions_analyzed,
            "mutation_vulnerable_count": manifest.mutation_vulnerable_count,
            "untested_count": len(untested),
        },
        "top_vulnerable": top_vulnerable,
        "untested_functions": untested[:20],
    }


def _build_assertion_upgrades(manifest: Any) -> list[dict[str, str]]:
    """Suggest concrete assertion upgrades based on current patterns."""
    from lintgate.linters.test_effectiveness.types import AssertionKind

    upgrades: list[dict[str, str]] = []
    seen: set[str] = set()

    for _name, fe in manifest.functions.items():
        for a in fe.assertions:
            key = f"{a.kind.value}:{a.target_expression}"
            if key in seen:
                continue
            seen.add(key)

            if a.kind == AssertionKind.IS_NOT_NONE:
                upgrades.append(
                    {
                        "current": f"assert {a.target_expression} is not None",
                        "suggested": f"assert {a.target_expression} == expected_value",
                        "reason": "is_not_none (0.3) → equality (0.9): catches value-altering mutants",
                    }
                )
            elif a.kind == AssertionKind.IS_TRUE:
                upgrades.append(
                    {
                        "current": f"assert {a.target_expression}",
                        "suggested": f"assert {a.target_expression} == expected",
                        "reason": "bare assert (0.2) → equality (0.9): catches -1→+1 sentinel mutations",
                    }
                )
            elif a.kind == AssertionKind.ISINSTANCE_CHECK:
                upgrades.append(
                    {
                        "current": f"assert isinstance({a.target_expression}, ...)",
                        "suggested": f"assert {a.target_expression} == expected_value",
                        "reason": "isinstance (0.3) → equality (0.9): type check doesn't verify value",
                    }
                )

            if len(upgrades) >= 10:
                break
        if len(upgrades) >= 10:
            break

    return upgrades


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register test effectiveness tools on the shared MCP instance."""

    @mcp.tool()
    def analyze_test_strength(
        path: str,
        file_filter: str | None = None,
        function_filter: str | None = None,
    ) -> str:
        """Analyze test assertion quality and mutation vulnerability for a project.

        WHEN TO USE: After controlplane_run shows test_effectiveness findings, or
        when you want to understand which functions have weak tests that would let
        mutants survive. This is the drill-down tool for the test_effectiveness channel.

        Returns project summary, top vulnerable functions, and suggested assertion upgrades.

        Example: analyze_test_strength(path="/my/project")
        Example: analyze_test_strength(path="/my/project", function_filter="parse")

        Args:
            path: Project root path.
            file_filter: Optional filename substring to filter by.
            function_filter: Optional function name substring to filter by.
        """
        project_root, manifest, py_files, test_files = _build_manifest_for_project(path, helpers)
        if not py_files or not test_files or manifest is None:
            return json.dumps({"error": "No Python files or test files found in project"})

        if not manifest.functions:
            return json.dumps(
                {"note": "No functions analyzed. Check that test files follow test_ naming."}
            )

        result = _build_summary(manifest, project_root)

        # Apply filters
        if function_filter:
            result["top_vulnerable"] = [
                v
                for v in result["top_vulnerable"]
                if function_filter.lower() in v["function"].lower()
            ]
            result["untested_functions"] = [
                f for f in result["untested_functions"] if function_filter.lower() in f.lower()
            ]
            result["filter_applied"] = {"function": function_filter}

        # Add assertion upgrade suggestions
        result["assertion_upgrades"] = _build_assertion_upgrades(manifest)

        result["next_actions"] = [
            "inspect_test_assertions(path, test_file) — drill into specific test file",
            "controlplane_test_skeleton(source_file) — generate mutation-aware test stubs",
            "generate_property_tests(path) — Hypothesis templates for pure functions",
        ]

        return helpers["_json_dumps"](result, output_mode="compact")

    @mcp.tool()
    def inspect_test_assertions(
        path: str,
        test_file: str,
    ) -> str:
        """Drill down into a single test file showing every assertion classified.

        WHEN TO USE: After analyze_test_strength identifies weak tests, use this
        to see exactly which assertions are structural (weak) vs semantic (strong)
        in a specific test file.

        Example: inspect_test_assertions(path="/my/project", test_file="tests/test_parser.py")

        Args:
            path: Project root path.
            test_file: Path to the test file to inspect (relative or absolute).
        """
        import os

        from lintgate.linters.test_effectiveness.assertion_classifier import (
            classify_test_file_from_path,
        )

        project_root = helpers["_validate_project_root"](path)

        # Resolve test file path
        if not os.path.isabs(test_file):
            test_file = os.path.join(project_root, test_file)

        if not os.path.exists(test_file):
            return json.dumps({"error": f"Test file not found: {test_file}"})

        test_assertions = classify_test_file_from_path(test_file)

        if not test_assertions:
            return json.dumps({"note": f"No test functions found in {test_file}"})

        result: dict[str, Any] = {
            "test_file": test_file,
            "test_functions": {},
            "summary": {
                "total_tests": len(test_assertions),
                "total_assertions": 0,
                "semantic_assertions": 0,
                "structural_assertions": 0,
            },
        }

        from lintgate.linters.test_effectiveness.types import SEMANTIC_STRENGTH_THRESHOLD

        for func_name, assertions in test_assertions.items():
            func_data: dict[str, Any] = {
                "assertions": [a.to_dict() for a in assertions],
                "count": len(assertions),
            }
            semantic = sum(1 for a in assertions if a.strength >= SEMANTIC_STRENGTH_THRESHOLD)
            structural = len(assertions) - semantic
            func_data["semantic_count"] = semantic
            func_data["structural_count"] = structural

            result["test_functions"][func_name] = func_data
            result["summary"]["total_assertions"] += len(assertions)
            result["summary"]["semantic_assertions"] += semantic
            result["summary"]["structural_assertions"] += structural

        total = result["summary"]["total_assertions"]
        if total > 0:
            result["summary"]["semantic_ratio"] = round(
                result["summary"]["semantic_assertions"] / total, 3
            )

        return helpers["_json_dumps"](result)

    return {
        "analyze_test_strength": analyze_test_strength,
        "inspect_test_assertions": inspect_test_assertions,
    }
