"""Cold-start bridge tools for test gap coverage.

Provides tools to triage untested functions, infer test inputs from call sites,
and generate characterization tests with golden-value capture.

Tools:
- test_triage: Rank untested functions by specification priority
- test_infer_inputs: Infer candidate inputs from call sites and type hints
- test_characterize: Generate characterization tests with golden assertions
- test_characterize_mark: Mark characterization test maturity lifecycle
"""

from __future__ import annotations

import ast
import json
import os
from typing import Any

from mcp_tools._disk_helpers import _safe_json, tool_response


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register cold-start bridge tools on the shared MCP instance."""

    @mcp.tool()
    def test_triage(
        path: str,
        top_n: int = 20,
        file_filter: str | None = None,
    ) -> str:
        """Rank untested functions by specification priority for cold-start coverage.

        WHEN TO USE: When a project has many untested functions and you need to
        decide what to test first. Ranks by fan-in, sigma, side effects, and coupling.

        Args:
            path: Project root path.
            top_n: Maximum number of functions to return (default 20).
            file_filter: Optional substring filter for source file paths.
        """
        project_root = helpers["_validate_project_root"](path)
        raw = _impl_test_triage(project_root, top_n, file_filter)
        from mcp_tools._disk_helpers import wrap_impl_response
        return wrap_impl_response(raw, "test_triage", project_root)

    @mcp.tool()
    def test_infer_inputs(
        path: str,
        file: str,
        function: str,
        max_examples: int = 5,
    ) -> str:
        """Infer candidate inputs for a function from call sites and type hints.

        WHEN TO USE: Before writing tests for an untested function. Shows how the
        function is actually called in the codebase, what arguments it receives,
        and what outputs are accessed.

        Args:
            path: Project root path.
            file: Relative path to the source file.
            function: Function name to analyze.
            max_examples: Maximum call-site examples to return (default 5).
        """
        project_root = helpers["_validate_project_root"](path)
        result = _impl_test_infer_inputs(project_root, file, function, max_examples)
        if isinstance(result, str):
            return result  # Small error JSON passthrough
        sites = len(result.get("call_sites", []))
        patterns = len(result.get("output_access_patterns", []))
        summary = f"Inferred inputs for {function}: {sites} call sites, {patterns} output patterns."
        return tool_response(
            result, "test_infer_inputs", project_root, summary,
            next_actions=result.get("next_actions"),
        )

    @mcp.tool()
    def test_characterize(
        path: str,
        file: str,
        function: str,
        write: bool = False,
    ) -> str:
        """Generate a characterization test that captures current function behavior.

        WHEN TO USE: For untested functions where you want to lock in current behavior
        before refactoring. Infers inputs from call sites, runs the function to capture
        golden values, and produces a pytest test.

        Maturity lifecycle: unchecked → approved → specified (mutation validated).

        Args:
            path: Project root path.
            file: Relative path to the source file.
            function: Function name to characterize.
            write: If True, write the test to tests/generated/. If False, return as string.
        """
        project_root = helpers["_validate_project_root"](path)
        result = _impl_test_characterize(project_root, file, function, write)
        if isinstance(result, str):
            return result  # Small error JSON passthrough
        golden = "with golden" if result.get("golden_captured") else "no golden"
        written = f", written to {result.get('test_path', '')}" if result.get("written") else ""
        summary = f"Characterized {function} in {file} ({golden}){written}."
        return tool_response(
            result, "test_characterize", project_root, summary,
            next_actions=result.get("next_actions"),
        )

    @mcp.tool()
    def test_characterize_mark(
        path: str,
        test_file: str,
        maturity: str = "approved",
    ) -> str:
        """Mark characterization test maturity: unchecked → approved → specified.

        WHEN TO USE: After reviewing or mutation-validating a characterization test.

        Args:
            path: Project root path.
            test_file: Path to the characterization test file.
            maturity: New maturity level (unchecked, approved, specified).
        """
        project_root = helpers["_validate_project_root"](path)
        result = _impl_test_characterize_mark(project_root, test_file, maturity)
        if isinstance(result, str):
            return result  # Small error JSON passthrough
        status = result.get("status", "unknown")
        summary = f"Maturity mark: {status} -> {maturity} for {test_file}."
        return tool_response(
            result, "test_characterize_mark", project_root, summary,
            next_actions=result.get("next_actions"),
        )

    return {
        "test_triage": test_triage,
        "test_infer_inputs": test_infer_inputs,
        "test_characterize": test_characterize,
        "test_characterize_mark": test_characterize_mark,
    }


# ── test_triage implementation ───────────────────────────────────────


def _impl_test_triage(project_root: str, top_n: int, file_filter: str | None) -> str:
    """Rank untested functions by specification priority."""
    from lintgate.discovery import discover_project_files
    from lintgate.specification.test_impact import build_test_impact_map

    py_files = discover_project_files(project_root)
    source_files = [f for f in py_files if not _is_test_file(f)]
    test_files = [f for f in py_files if _is_test_file(f)]

    if file_filter:
        source_files = [f for f in source_files if file_filter in f]

    # Build test impact map to find which functions have tests
    impact_map = build_test_impact_map(test_files)

    # Scan source files for all functions
    candidates: list[dict[str, Any]] = []
    for filepath in source_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (OSError, SyntaxError):
            continue

        rel_path = os.path.relpath(filepath, project_root)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_name = node.name
            if func_name.startswith("_") and not func_name.startswith("__"):
                continue  # Skip private helpers for triage (focus on public API)

            # Check if function has test coverage — filter by module import
            # to avoid bare-name collisions (e.g. pkg.a.compute vs pkg.b.compute)
            tests = impact_map.tests_for(func_name)
            if tests and _any_test_imports_module(tests, rel_path):
                continue  # Already tested

            # Compute priority signals
            param_count = len(node.args.args)
            branch_count = sum(
                1 for n in ast.walk(node) if isinstance(n, (ast.If, ast.For, ast.While))
            )
            has_return = any(isinstance(n, ast.Return) for n in ast.walk(node))
            line_count = getattr(node, "end_lineno", node.lineno) - node.lineno + 1

            # Simple sigma estimate
            sigma_est = max(1, param_count + branch_count)

            # Priority score: larger + more complex + returns value = higher priority
            score = sigma_est * 2 + (5 if has_return else 0) + min(line_count // 5, 10)

            candidates.append(
                {
                    "function": func_name,
                    "file": rel_path,
                    "line": node.lineno,
                    "sigma_estimate": sigma_est,
                    "param_count": param_count,
                    "branch_count": branch_count,
                    "has_return_value": has_return,
                    "line_count": line_count,
                    "priority_score": score,
                }
            )

    # Sort by priority score descending
    candidates.sort(key=lambda c: c["priority_score"], reverse=True)
    top = candidates[:top_n]

    output: dict[str, Any] = {
        "total_untested": len(candidates),
        "shown": len(top),
        "functions": top,
        "next_actions": [],
    }

    if top:
        output["next_actions"].append(
            {
                "tool": "test_infer_inputs",
                "args": {
                    "path": project_root,
                    "file": top[0]["file"],
                    "function": top[0]["function"],
                },
                "reason": f"Infer inputs for highest-priority untested function: {top[0]['function']}",
                "priority": 1,
            }
        )

    return _safe_json(output)


# ── test_infer_inputs implementation ─────────────────────────────────


def _impl_test_infer_inputs(project_root: str, file: str, function: str, max_examples: int) -> str:
    """Infer candidate inputs from call sites and type hints."""
    from lintgate.discovery import discover_project_files

    abs_file = os.path.join(project_root, file)
    if not os.path.exists(abs_file):
        return json.dumps({"error": f"File not found: {file}"})

    # Extract function signature
    sig_info = _extract_signature(abs_file, function)
    if sig_info is None:
        return json.dumps({"error": f"Function '{function}' not found in {file}"})

    # Scan all source files for call sites
    py_files = discover_project_files(project_root)
    call_sites = _find_call_sites(py_files, function, max_examples)

    # Extract output access patterns from call sites
    output_patterns = _find_output_patterns(py_files, function)

    output: dict[str, Any] = {
        "function": function,
        "file": file,
        "signature": sig_info,
        "call_sites": call_sites,
        "output_access_patterns": output_patterns,
        "next_actions": [
            {
                "tool": "test_characterize",
                "args": {"path": project_root, "file": file, "function": function},
                "reason": "Generate characterization test from inferred inputs",
                "priority": 1,
            }
        ],
    }

    return _safe_json(output)


def _extract_signature(filepath: str, function: str) -> dict[str, Any] | None:
    """Extract function signature info from AST."""
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (OSError, SyntaxError):
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            params = []
            for arg in node.args.args:
                param: dict[str, Any] = {"name": arg.arg}
                if arg.annotation:
                    # Preserve both raw source and ast.dump for typed synthesis
                    try:
                        param["type_hint"] = ast.unparse(arg.annotation)
                    except (AttributeError, ValueError):
                        param["type_hint"] = ast.dump(arg.annotation)
                params.append(param)

            # Check for return type annotation
            return_hint = None
            if node.returns:
                try:
                    return_hint = ast.unparse(node.returns)
                except (AttributeError, ValueError):
                    return_hint = ast.dump(node.returns)

            # Extract defaults
            defaults = []
            for d in node.args.defaults:
                if isinstance(d, ast.Constant):
                    defaults.append(repr(d.value))
                else:
                    defaults.append(ast.dump(d))

            return {
                "params": params,
                "return_type_hint": return_hint,
                "defaults": defaults,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "line": node.lineno,
            }
    return None


def _find_call_sites(py_files: list[str], function: str, max_examples: int) -> list[dict[str, Any]]:
    """Find call sites for a function across the codebase."""
    sites: list[dict[str, Any]] = []

    for filepath in py_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
        except (OSError, SyntaxError):
            continue

        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name) and node.func.id == function:
                name = node.func.id
            elif isinstance(node.func, ast.Attribute) and node.func.attr == function:
                name = node.func.attr
            if name is None:
                continue

            # Extract arguments
            args_info: list[str] = []
            for a in node.args:
                if isinstance(a, ast.Constant):
                    args_info.append(repr(a.value))
                elif isinstance(a, ast.Name):
                    args_info.append(a.id)
                else:
                    args_info.append(ast.dump(a))

            kwargs_info: dict[str, str] = {}
            for kw in node.keywords:
                key = kw.arg or "**"
                if isinstance(kw.value, ast.Constant):
                    kwargs_info[key] = repr(kw.value.value)
                elif isinstance(kw.value, ast.Name):
                    kwargs_info[key] = kw.value.id
                else:
                    kwargs_info[key] = ast.dump(kw.value)

            line_num = node.lineno
            context_line = lines[line_num - 1].strip() if line_num <= len(lines) else ""

            sites.append(
                {
                    "file": filepath,
                    "line": line_num,
                    "context": context_line[:120],
                    "positional_args": args_info,
                    "keyword_args": kwargs_info,
                }
            )

            if len(sites) >= max_examples:
                return sites

    return sites


def _find_output_patterns(py_files: list[str], function: str) -> list[str]:
    """Find how return values of the function are used (attribute access, indexing)."""
    patterns: set[str] = set()

    for filepath in py_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            # Look for result = func(...) followed by result.attr or result[key]
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    call_name = None
                    if isinstance(node.value.func, ast.Name):
                        call_name = node.value.func.id
                    elif isinstance(node.value.func, ast.Attribute):
                        call_name = node.value.func.attr
                    if call_name != function:
                        continue
                    var_name = target.id
                    # Now scan the parent scope for uses of var_name
                    _collect_access_patterns(tree, var_name, patterns)

    return sorted(patterns)[:10]


def _collect_access_patterns(tree: ast.AST, var_name: str, patterns: set[str]) -> None:
    """Collect attribute access and subscript patterns on a variable."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == var_name
        ):
            patterns.add(f".{node.attr}")
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == var_name
        ):
            if isinstance(node.slice, ast.Constant):
                patterns.add(f"[{repr(node.slice.value)}]")
            else:
                patterns.add("[...]")


# ── test_characterize implementation ─────────────────────────────────


def _try_golden_capture(
    module_path: str,
    function: str,
    sig_info: dict[str, Any],
    project_root: str | None = None,
) -> dict[str, Any] | None:
    """Try to import, execute, and capture a golden value for a function.

    Returns {"value": repr, "deterministic": bool} on success, None on failure.
    Only attempts capture for functions with no parameters or all-defaulted parameters.
    Runs twice to check determinism.

    When ``project_root`` is set and the module isn't stdlib, capture runs
    in a subprocess from that directory to avoid namespace-package
    shadowing across LintGate's process-wide sys.path.
    """
    import importlib
    import sys

    params = [p for p in sig_info.get("params", []) if p["name"] != "self"]
    if params and not sig_info.get("defaults"):
        return None  # Can't call without arguments

    # Only attempt if zero non-default params
    n_defaults = len(sig_info.get("defaults", []))
    if len(params) > n_defaults:
        return None

    top_level = module_path.split(".", 1)[0]
    if project_root and top_level not in sys.stdlib_module_names:
        from lintgate.testing.characterization import _subprocess_capture

        return _subprocess_capture(project_root, module_path, function, [], {})

    try:
        mod = importlib.import_module(module_path)
        fn = getattr(mod, function, None)
        if fn is None or not callable(fn):
            return None

        result1 = fn()
        result2 = fn()
        deterministic = repr(result1) == repr(result2)
        return {"value": repr(result1), "deterministic": deterministic}
    except Exception:
        return None


def _impl_test_characterize(project_root: str, file: str, function: str, write: bool) -> str:
    """Generate a characterization test for a function."""
    abs_file = os.path.join(project_root, file)
    if not os.path.exists(abs_file):
        return json.dumps({"error": f"File not found: {file}"})

    sig_info = _extract_signature(abs_file, function)
    if sig_info is None:
        return json.dumps({"error": f"Function '{function}' not found in {file}"})

    # Build import path
    module_path = file.replace("/", ".").replace("\\", ".").removesuffix(".py")

    # Attempt golden-value capture for zero-arg / all-defaulted functions
    golden = _try_golden_capture(module_path, function, sig_info, project_root=project_root)

    # Generate test code
    test_code = _generate_characterization_test(module_path, function, sig_info, golden)

    output: dict[str, Any] = {
        "function": function,
        "file": file,
        "test_code": test_code,
        "maturity": "unchecked",
        "written": False,
    }
    if golden:
        output["golden_captured"] = True
        output["golden_deterministic"] = golden["deterministic"]
    else:
        output["golden_captured"] = False

    if write:
        gen_dir = os.path.join(project_root, "tests", "generated")
        os.makedirs(gen_dir, exist_ok=True)
        # Ensure __init__.py exists
        init_file = os.path.join(gen_dir, "__init__.py")
        if not os.path.exists(init_file):
            with open(init_file, "w") as f:
                f.write("")

        test_filename = f"test_char_{function}.py"
        test_path = os.path.join(gen_dir, test_filename)
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_code)
        output["written"] = True
        output["test_path"] = os.path.relpath(test_path, project_root)

    output["next_actions"] = [
        {
            "tool": "mutation_run_sampling",
            "args": {"path": project_root, "file": file},
            "reason": "Validate characterization test kills mutants",
            "priority": 1,
        },
        {
            "tool": "test_characterize_mark",
            "args": {
                "path": project_root,
                "test_file": output.get("test_path", ""),
                "maturity": "approved",
            },
            "reason": "Mark test as reviewed after inspection",
            "priority": 2,
        },
    ]

    return _safe_json(output)


def _generate_characterization_test(
    module_path: str,
    function: str,
    sig_info: dict[str, Any],
    golden: dict[str, Any] | None = None,
) -> str:
    """Generate pytest characterization test code."""
    from lintgate.testing.typed_synthesis import synthesize_value

    params = sig_info.get("params", [])
    # Skip 'self' parameter
    params = [p for p in params if p["name"] != "self"]

    # Build parameter values using typed synthesis
    param_lines = []
    extra_imports: list[str] = []
    for p in params:
        hint = p.get("type_hint", "")
        synth = synthesize_value(hint, p["name"], module_path)
        tag = "" if not synth.is_placeholder else "  # TODO: fill from call site"
        param_lines.append(f"    {p['name']} = {synth.code}{tag}")
        extra_imports.extend(synth.imports)

    args_str = ", ".join(p["name"] for p in params)
    param_block = "\n".join(param_lines) if param_lines else "    pass  # No parameters"

    # Build assertion line
    if golden and golden.get("deterministic"):
        assert_line = f"    assert result == {golden['value']}"
        assert_comment = "    # Golden value captured from live execution"
    elif golden and not golden.get("deterministic"):
        assert_line = (
            f"    assert result is not None  # Nondeterministic — golden was {golden['value']}"
        )
        assert_comment = "    # WARNING: function returned different values on repeat calls"
    else:
        assert_line = "    assert result is not None  # TODO: replace with golden assertion"
        assert_comment = "    # No golden capture — fill in expected values manually"

    # Deduplicate extra imports and build import block
    seen_imports: set[str] = set()
    import_lines: list[str] = []
    for imp in extra_imports:
        if imp not in seen_imports:
            seen_imports.add(imp)
            import_lines.append(imp)
    extra_import_block = "\n".join(import_lines) + "\n" if import_lines else ""

    test = f'''"""Characterization test for {function}.

Auto-generated by test_characterize. Maturity: unchecked.
Review and fill in TODO values before marking as approved.
"""

import pytest

from {module_path} import {function}
{extra_import_block}

def test_{function}_characterization():
    """Characterization: capture current behavior of {function}."""
    # Arrange — fill in from call-site inference
{param_block}

    # Act
    result = {function}({args_str})

    # Assert
{assert_comment}
{assert_line}
'''
    return test


# ── test_characterize_mark implementation ────────────────────────────


def _impl_test_characterize_mark(project_root: str, test_file: str, maturity: str) -> str:
    """Update maturity marker in a characterization test file."""
    valid_maturities = {"unchecked", "approved", "specified"}
    if maturity not in valid_maturities:
        return _safe_json(
            {"error": f"Invalid maturity '{maturity}'. Must be one of: {sorted(valid_maturities)}"}
        )

    abs_path = os.path.join(project_root, test_file)
    if not os.path.exists(abs_path):
        return json.dumps({"error": f"File not found: {test_file}"})

    try:
        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return json.dumps({"error": str(e)})

    # Replace maturity marker in docstring
    updated = False
    for old_maturity in valid_maturities:
        marker = f"Maturity: {old_maturity}"
        new_marker = f"Maturity: {maturity}"
        if marker in content and marker != new_marker:
            content = content.replace(marker, new_marker)
            updated = True

    if not updated:
        return _safe_json(
            {
                "status": "no_change",
                "message": f"No maturity marker found or already set to '{maturity}'",
            }
        )

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)

    return _safe_json(
        {
            "status": "updated",
            "file": test_file,
            "maturity": maturity,
            "next_actions": [
                {
                    "tool": "mutation_run_sampling",
                    "args": {"path": project_root, "file": test_file},
                    "reason": "Validate test kills mutants after approval",
                    "priority": 1,
                }
            ]
            if maturity == "approved"
            else [],
        }
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _is_test_file(filepath: str) -> bool:
    base = os.path.basename(filepath)
    return base.startswith("test_") or base.endswith("_test.py")


def _any_test_imports_module(tests: list, source_rel_path: str) -> bool:
    """Check if any test file actually imports from the source module.

    Prevents bare-name collisions: if test_a imports pkg.b.compute,
    it shouldn't count as coverage for pkg.a.compute.
    """
    # Build possible import fragments from the source path
    # e.g. "mylib/core.py" → {"mylib.core", "mylib/core", "core"}
    module_dot = source_rel_path.replace("/", ".").replace("\\", ".").removesuffix(".py")
    fragments = {module_dot}
    # Also the bare module name (last component)
    parts = module_dot.split(".")
    if len(parts) > 1:
        fragments.add(parts[-1])  # Allow bare match as fallback
        # Add parent.module form
        fragments.add(".".join(parts[-2:]))

    for tref in tests:
        test_file = tref.test_file if hasattr(tref, "test_file") else str(tref)
        try:
            with open(test_file, encoding="utf-8") as f:
                source = f.read()
        except OSError:
            continue
        # Check if any import line references the source module
        for line in source.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for frag in fragments:
                if frag in stripped:
                    return True
    return False
