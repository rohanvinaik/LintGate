"""Factory generation and validation for typed synthesis."""

from __future__ import annotations

import ast
import contextlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import fields as dc_fields

from .typed_synthesis import _default_for_field, _resolve_dataclass

# ── Factory generation ───────────────────────────────────────────


def synthesize_factory(
    type_name: str,
    module_path: str = "",
) -> tuple[str, list[str]] | None:
    """Generate a test factory function for a dataclass.

    Returns (factory_code, imports) or None if the type isn't a known dataclass.

    Example output:
        def _issue(*, linter="ruff", kind="T001", message="test", ...) -> LintIssue:
            return LintIssue(linter=linter, kind=kind, message=message, ...)
    """
    cls = _resolve_dataclass(type_name, module_path)
    if cls is None:
        return None

    fields = dc_fields(cls)
    cls_name = cls.__name__
    module = cls.__module__

    # Build factory signature with all fields as keyword-only with defaults
    sig_parts: list[str] = []
    body_parts: list[str] = []

    for f in fields:
        from dataclasses import MISSING

        default_val = _default_for_field(f.name, f.type)

        if f.default is not MISSING:
            default_val = repr(f.default)
        elif f.default_factory is not MISSING:
            with contextlib.suppress(Exception):
                default_val = repr(f.default_factory())

        sig_parts.append(f"    {f.name}={default_val},")
        body_parts.append(f"{f.name}={f.name}")

    factory_name = f"_make_{cls_name.lower()}"
    if cls_name == "LintIssue":
        factory_name = "_issue"

    lines = [
        f"def {factory_name}(",
        "    *,",
        *sig_parts,
        f") -> {cls_name}:",
        f'    """Build a {cls_name} with sensible defaults for testing."""',
        f"    return {cls_name}(",
        "        " + ", ".join(body_parts) + ",",
        "    )",
        "",
    ]

    imports = [f"from {module} import {cls_name}"]
    return "\n".join(lines), imports


# ── Post-generation validation ───────────────────────────────────


def validate_test_file(
    test_content: str,
    project_root: str | None = None,
    test_file_name: str = "test_generated.py",
    run_pytest: bool = False,
) -> tuple[bool, list[str]]:
    """Validate that generated test code can be parsed and has no obvious issues.

    Returns (valid, errors). Checks:
    1. Syntactically valid Python
    2. All test functions have at least one assert
    3. No bare 'pass' as only body in test functions
    4. No undefined names used in test bodies (AST-level: checks that every
       Name node is either imported, defined locally, or a builtin)
    5. Optional runtime execution via pytest to catch crashing generated tests
    """
    errors: list[str] = []

    try:
        tree = ast.parse(test_content)
    except SyntaxError as e:
        return False, [f"SyntaxError: {e}"]

    # Collect module-level defined names (imports, assignments, functions, classes)
    module_names = _collect_module_names(tree)
    errors.extend(_find_duplicate_test_names(tree))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            errors.extend(_validate_single_test(node, module_names))

    if errors:
        return False, errors

    if run_pytest and project_root:
        errors.extend(_runtime_validate_with_pytest(test_content, project_root, test_file_name))

    return len(errors) == 0, errors


def _validate_single_test(node: ast.FunctionDef, module_names: set[str]) -> list[str]:
    """Validate a single test function for body, assertions, and undefined names."""
    errors: list[str] = []

    # Check for empty/pass-only test body
    body_stmts = [
        s for s in node.body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)
    ]
    if not body_stmts or (len(body_stmts) == 1 and isinstance(body_stmts[0], ast.Pass)):
        errors.append(f"{node.name} (line {node.lineno}): empty body (pass-only)")

    # Check for at least one assert or pytest.raises
    if not _has_assertion(node):
        errors.append(f"{node.name} (line {node.lineno}): no assert statement")

    # Check for undefined names
    for name in _find_undefined_names(node, module_names):
        errors.append(f"{node.name} (line {node.lineno}): undefined name '{name}'")

    return errors


def _has_assertion(node: ast.FunctionDef) -> bool:
    """Check if a test function contains at least one assert or pytest.raises."""
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.With):
            for item in child.items:
                if isinstance(item.context_expr, ast.Call) and _is_pytest_raises(item.context_expr):
                    return True
    return False


def _collect_module_names(tree: ast.Module) -> set[str]:
    """Collect all names defined at module level (imports, assignments, defs)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
    return names


def _find_duplicate_test_names(tree: ast.Module) -> list[str]:
    """Detect duplicate top-level test names that would shadow each other."""
    seen: dict[str, int] = {}
    errors: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        if node.name in seen:
            errors.append(f"{node.name} (line {node.lineno}): duplicate test name")
        else:
            seen[node.name] = node.lineno
    return errors


# Names that are always available without import
_BUILTINS = {
    "True",
    "False",
    "None",
    "print",
    "len",
    "range",
    "type",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "isinstance",
    "hasattr",
    "getattr",
    "setattr",
    "repr",
    "sorted",
    "enumerate",
    "zip",
    "map",
    "filter",
    "any",
    "all",
    "min",
    "max",
    "sum",
    "abs",
    "round",
    "id",
    "object",
    "super",
    "property",
    "staticmethod",
    "classmethod",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "RuntimeError",
    "Exception",
    "NotImplementedError",
    "OSError",
    "StopIteration",
    "AssertionError",
    "ImportError",
    "FileNotFoundError",
    "frozenset",
    "bytes",
    "bytearray",
    "memoryview",
    "complex",
    "open",
    "input",
    "hash",
    "callable",
    "iter",
    "next",
    "reversed",
    "format",
    "vars",
    "dir",
    "globals",
    "locals",
    "exec",
    "eval",
    "compile",
    "__name__",
    "__file__",
    "__import__",
}


def _collect_local_names(func_node: ast.FunctionDef) -> set[str]:
    """Collect all locally defined names in a function (params, assigns, targets)."""
    local_names: set[str] = {arg.arg for arg in func_node.args.args}
    for child in ast.walk(func_node):
        _collect_name_from_node(child, local_names)
    return local_names


def _collect_name_from_node(child: ast.AST, names: set[str]) -> None:
    """Extract defined names from a single AST node into the set."""
    if isinstance(child, ast.Assign):
        for target in child.targets:
            _collect_names_from_target(target, names)
    elif isinstance(child, (ast.For, ast.comprehension)):
        _collect_names_from_target(child.target, names)
    elif isinstance(child, (ast.With, ast.AsyncWith)):
        for item in child.items:
            if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                names.add(item.optional_vars.id)
    elif isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
        names.add(child.target.id)


def _collect_names_from_target(target: ast.expr, names: set[str]) -> None:
    """Extract names from an assignment target (Name or Tuple)."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                names.add(elt.id)


def _find_undefined_names(func_node: ast.FunctionDef, module_names: set[str]) -> list[str]:
    """Find names used in a test function body that aren't defined anywhere visible."""
    all_defined = module_names | _collect_local_names(func_node) | _BUILTINS

    undefined: list[str] = []
    seen: set[str] = set()
    for child in ast.walk(func_node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            name = child.id
            if name not in all_defined and name not in seen:
                seen.add(name)
                undefined.append(name)

    return undefined


def _is_pytest_raises(call: ast.Call) -> bool:
    """Check if an AST Call node is pytest.raises(...)."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "raises"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pytest"
    )


def _runtime_validate_with_pytest(
    test_content: str,
    project_root: str,
    test_file_name: str,
) -> list[str]:
    """Run generated tests in a temp file to catch runtime failures."""
    errors: list[str] = []
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = project_root if not existing else os.pathsep.join([project_root, existing])

    try:
        with tempfile.TemporaryDirectory(prefix="lintgate-gen-") as tmpdir:
            temp_path = os.path.join(tmpdir, test_file_name)
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(test_content)

            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", temp_path],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"module: pytest execution failed ({exc})"]

    if proc.returncode == 0:
        return []

    output = "\n".join(part for part in [proc.stdout, proc.stderr] if part).strip()
    failed = {match.group(1) for match in re.finditer(r"::(test_[A-Za-z0-9_]+)\b", output)}
    if failed:
        for name in sorted(failed):
            errors.append(f"{name}: runtime failure under pytest")
        return errors

    if output:
        first_line = output.splitlines()[0]
        errors.append(f"module: pytest execution failed ({first_line[:120]})")
    else:
        errors.append("module: pytest execution failed")
    return errors
