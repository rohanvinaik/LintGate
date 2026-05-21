"""Mutation test generation implementations — skeleton generation, golden captures, enrichment.

Extracted from _mutation_tools_impl.py for file-size compliance.
"""

from __future__ import annotations

import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

from ._mutation_impl import (
    generate_test_skeleton,
    get_cache_dir,
    iter_cached_states,
)


def _load_golden_captures(
    project_root: str,
    file: str | None,
) -> dict[str, dict[str, Any]]:
    """Load golden captures for functions in a file.

    Returns dict mapping func_key -> golden capture data with CORROBORATED
    provenance only.
    """
    if not file:
        return {}
    try:
        from lintgate.specification.file_analyzer import _load_mutation_cache

        abs_path = os.path.join(project_root, file) if not os.path.isabs(file) else file
        mutation_cache = _load_mutation_cache(project_root, abs_path)
        if not mutation_cache:
            return {}

        from lintgate.testing.characterization import (
            Provenance,
            capture_golden,
            corroborate_captures,
        )
        from mcp_tools._mutation_impl import detect_purity

        result: dict[str, dict[str, Any]] = {}
        for func_key, mut_data in mutation_cache.items():
            mod_path = func_key.rsplit("::", 1)[0] if "::" in func_key else ""
            func_name = func_key.rsplit("::", 1)[1] if "::" in func_key else func_key
            if not mod_path:
                continue

            # Get call site inputs from mutation data
            call_site_inputs = mut_data.get("call_site_inputs", [])
            captures = capture_golden(
                mod_path, func_name, call_site_inputs, project_root=project_root,
            )
            if not captures:
                continue

            is_pure = detect_purity(abs_path, func_name)
            captures = corroborate_captures(captures, mut_data, is_pure)

            # Only use CORROBORATED captures
            for cap in captures:
                if cap.provenance == Provenance.CORROBORATED:
                    result[func_key] = {
                        "inputs": cap.inputs,
                        "kwargs": cap.kwargs,
                        "output": cap.output,
                        "provenance": cap.provenance.value,
                        "corroborating_lens": cap.corroborating_lens,
                    }
                    break  # one golden per function is sufficient
    except Exception:
        return {}
    return result


def _render_golden_value_assertion(
    func_key: str,
    golden: dict[str, Any],
) -> str:
    """Render an executable VALUE assertion from a corroborated golden capture."""
    func_expr = func_key.rsplit("::", 1)[1] if "::" in func_key else func_key
    inputs = [repr(v) for v in golden.get("inputs", [])]
    kwargs = [f"{k}={v!r}" for k, v in golden.get("kwargs", {}).items()]
    args = ", ".join(inputs + kwargs)
    golden_output = golden.get("output", "")
    return f"result = {func_expr}({args})\nassert repr(result) == {golden_output!r}"


def _enrich_with_golden_capture(cat, entry, func_key, golden_by_func):
    if cat == "VALUE" and func_key in golden_by_func:
        golden = golden_by_func[func_key]
        if golden.get("provenance") == "corroborated":
            entry["needs_oracle"] = False
            entry["test_code"] = _render_golden_value_assertion(func_key, golden)
            entry["confidence"] = max(entry["confidence"], 0.9)
            entry["golden_value"] = golden.get("output", "")
            entry["golden_inputs"] = golden.get("inputs", [])
            entry["golden_kwargs"] = golden.get("kwargs", {})
            entry["golden_provenance"] = golden.get("corroborating_lens", "")
            entry["source"] = "golden_capture"
            entry["golden_capture_used"] = True
    return entry


def _write_skeleton_file(
    skeletons: list[dict[str, Any]], project_root: str, file: str
) -> str:
    """Write skeleton test code to a .py file under .lintgate/skeletons/."""
    if not skeletons:
        return ""
    skel_dir = os.path.join(project_root, ".lintgate", "skeletons")
    os.makedirs(skel_dir, exist_ok=True)
    skel_file = os.path.join(skel_dir, f"prescribe_{os.path.basename(file or 'all')}")
    if not skel_file.endswith(".py"):
        skel_file += ".py"
    skel_lines = ['"""Auto-generated test skeletons from mutation_prescribe_tests."""\n\n']
    for skel in skeletons:
        func = skel.get("function", "unknown")
        cat = skel.get("category", "unknown")
        skel_lines.append(f"# --- {func} [{cat}] ---\n")
        if skel.get("setup_code"):
            skel_lines.append(f"# Setup: {skel['setup_code']}\n")
        if skel.get("test_code"):
            skel_lines.append(skel["test_code"])
            skel_lines.append("\n\n")
    with open(skel_file, "w", encoding="utf-8") as f:
        f.writelines(skel_lines)
    return os.path.relpath(skel_file, project_root)


def _build_skeleton_summary(
    skeletons: list[dict[str, Any]], file: str, skeleton_path: str
) -> str:
    """Build a compact summary of generated skeletons."""
    cats_seen: dict[str, int] = {}
    for skel in skeletons:
        cat = skel.get("category", "?")
        cats_seen[cat] = cats_seen.get(cat, 0) + 1
    cat_summary = ", ".join(f"{c}:{n}" for c, n in sorted(cats_seen.items()))
    summary = f"{len(skeletons)} skeleton(s) for {file or 'all'}: {cat_summary}"
    if skeleton_path:
        summary += f"\nSkeletons written to: {skeleton_path}"
    return summary


def _resolve_func_node(project_root: str, func_key: str) -> Any:
    """Resolve a function's AST node from its func_key."""
    import ast as _ast

    mod_path, func_name = func_key.rsplit("::", 1) if "::" in func_key else ("", func_key)
    if not mod_path:
        return None
    abs_path = os.path.join(project_root, mod_path)
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, encoding="utf-8") as f:
            tree = _ast.parse(f.read())
        bare_name = func_name.split(".")[-1]
        for node in _ast.walk(tree):
            if (
                isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                and node.name == bare_name
            ):
                return node
    except Exception:
        pass
    return None


def impl_prescribe_tests(helpers: Any, path: str, file: str, function: str | None) -> str:
    from lintgate.testing.oracle_light import generate_executable_property

    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    if not states:
        from mcp_tools._disk_helpers import tool_response

        return tool_response(
            {"status": "NEEDS_PROFILE", "file": file or "all"},
            "mutation_prescribe_tests",
            project_root,
            summary=f"No mutation data for {file or 'all'}. Run improve_tests(path, file) first.",
            next_actions=[
                {
                    "tool": "improve_tests",
                    "args": {"path": path, "file": file or ""},
                    "reason": "Mutation profile required before generating test skeletons",
                    "priority": "required",
                },
            ],
        )

    # Load golden captures for VALUE skeleton enrichment
    golden_by_func = _load_golden_captures(project_root, file)

    skeletons: list[dict[str, Any]] = []
    for data in states:
        func_key = data.get("function_key", "")
        survivors = data.get("survivor_records", [])

        if survivors:
            # Resolve AST node for richer oracle-light properties
            func_node = _resolve_func_node(project_root, func_key)

            # Deduplicate: one skeleton per category, pick best survivor
            best_by_cat: dict[str, dict[str, Any]] = {}
            for survivor in survivors:
                cat = survivor.get("category", "")
                prop = generate_executable_property(
                    survivor,
                    func_key,
                    func_node=func_node,
                    call_site_inputs=data.get("call_site_inputs", []),
                )
                entry = {
                    "function": func_key,
                    "category": prop.category,
                    "test_code": prop.assertion_code,
                    "setup_code": prop.setup_code,
                    "inputs": prop.inputs,
                    "preconditions": prop.preconditions,
                    "confidence": prop.confidence,
                    "needs_oracle": prop.needs_oracle,
                    "source": "oracle_light",
                    "golden_capture_used": False,
                }
                # Enrich VALUE skeletons with golden captures
                entry = _enrich_with_golden_capture(cat, entry, func_key, golden_by_func)

                existing = best_by_cat.get(cat)
                if existing is None or prop.confidence > existing.get("confidence", 0):
                    best_by_cat[cat] = entry
            skeletons.extend(best_by_cat.values())
            # Fall back to generic skeletons for categories without survivor records
            for cat_data in data.get("per_category", []):
                cat = cat_data["category"]
                if cat_data.get("survived", 0) > 0 and cat not in best_by_cat:
                    skel = generate_test_skeleton(func_key, cat)
                    skel["golden_capture_used"] = False
                    skeletons.append(skel)
        else:
            # No survivor records — use generic skeletons
            for cat_data in data.get("per_category", []):
                if cat_data.get("survived", 0) > 0:
                    skel = generate_test_skeleton(func_key, cat_data["category"])
                    skel["golden_capture_used"] = False
                    skeletons.append(skel)
    next_actions: list[NextAction] = []
    if skeletons:
        args: dict[str, str] = {"path": path}
        if file:
            args["file"] = file
        next_actions = [
            NextAction(
                tool="mutation_validate_tests",
                args=args,
                reason="After writing tests, validate they kill targeted mutants",
                condition="after implementing the test skeletons",
            ),
        ]
    skeleton_path = _write_skeleton_file(skeletons, project_root, file)

    output: dict[str, Any] = {
        "skeletons": skeletons,
        "skeleton_file": skeleton_path,
        "next_actions": serialize_next_actions(next_actions),
    }

    summary = _build_skeleton_summary(skeletons, file, skeleton_path)

    from mcp_tools._disk_helpers import tool_response

    return tool_response(
        output,
        "mutation_prescribe_tests",
        project_root,
        summary,
        next_actions=output.get("next_actions"),
    )
