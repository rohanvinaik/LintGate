"""Typed value synthesis for test generation.

Resolves AST type annotations into constructible test values. Single source
of truth for parameter filling — used by characterization, cold-start, and
batch-regenerator pipelines instead of ad-hoc 0/""/None fallbacks.

Design:
    synthesize_value(annotation_str, param_name, module_path) → SynthesizedValue
    synthesize_factory(dataclass_name, fields, module_path) → str

The module resolves annotations through three layers:
1. Primitives: str, int, float, bool, None → literal defaults
2. Containers: list[T], dict[K,V], set[T], tuple[...] → empty or populated
3. Dataclasses: imported, introspected, minimal construction emitted
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, is_dataclass
from dataclasses import fields as dc_fields
from typing import Any


@dataclass
class SynthesizedValue:
    """A synthesized test input value with metadata."""

    code: str  # Python expression string, e.g. 'LintIssue(linter="ruff", ...)'
    imports: list[str]  # import lines needed, e.g. ['from lintgate.types import LintIssue']
    is_placeholder: bool  # True if we fell back to None/0/""
    type_name: str  # resolved type name for dedup


# ── Primitive resolution ─────────────────────────────────────────


_PRIMITIVE_DEFAULTS: dict[str, str] = {
    "str": '""',
    "int": "0",
    "float": "0.0",
    "bool": "False",
    "bytes": 'b""',
}

_PRIMITIVE_NAMES = set(_PRIMITIVE_DEFAULTS.keys())


def _is_primitive(name: str) -> bool:
    return name in _PRIMITIVE_NAMES


# ── AST annotation parsing ──────────────────────────────────────


def _parse_annotation_str(hint: str) -> tuple[str, list[str]]:
    """Parse an annotation string into (base_type, [type_args]).

    Handles both raw strings like 'list[LintIssue]' and ast.dump output
    like "Name(id='list')" or "Subscript(...)".
    """
    if not hint:
        return ("", [])

    # Detect ast.dump output before trying ast.parse — ast.dump strings
    # like "Name(id='list')" are valid Python (function calls) but should
    # be handled by the heuristic parser, not the annotation parser.
    if "Name(id=" in hint or "Subscript(" in hint or "Constant(value=" in hint:
        return _parse_ast_dump(hint)

    # Try parsing as raw Python type annotation
    try:
        node = ast.parse(hint, mode="eval").body
        return _extract_from_node(node)
    except SyntaxError:
        pass

    return ("", [])


def _extract_from_node(node: ast.expr) -> tuple[str, list[str]]:
    """Extract base type and args from an AST annotation node."""
    if isinstance(node, ast.Constant) and node.value is None:
        return ("None", [])

    if isinstance(node, ast.Name):
        return (node.id, [])

    if isinstance(node, ast.Attribute):
        # e.g. os.PathLike → just use the final name
        return (node.attr, [])

    if isinstance(node, ast.Subscript):
        base = _extract_from_node(node.value)[0]
        slice_node = node.slice

        if isinstance(slice_node, ast.Tuple):
            args = [_extract_from_node(e)[0] for e in slice_node.elts]
        else:
            args = [_extract_from_node(slice_node)[0]]
        return (base, args)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # X | Y union — use the non-None branch
        left = _extract_from_node(node.left)
        right = _extract_from_node(node.right)
        if left[0] == "None":
            return right
        return left

    return ("", [])


def _parse_ast_dump(hint: str) -> tuple[str, list[str]]:
    """Parse ast.dump() output like Name(id='list') or Subscript(...)."""
    # Name(id='Foo')
    if hint.startswith("Name(id='"):
        name = hint.split("'")[1]
        return (name, [])

    # Subscript with Name slice
    if "Subscript" in hint:
        # Extract base name
        import re

        base_match = re.search(r"Name\(id='(\w+)'\)", hint)
        base = base_match.group(1) if base_match else ""
        # Extract all Name ids for type args
        all_names = re.findall(r"Name\(id='(\w+)'\)", hint)
        args = all_names[1:] if len(all_names) > 1 else []
        return (base, args)

    # Constant(value=None)
    if "Constant(value=None)" in hint:
        return ("None", [])

    # BinOp — union type X | None
    if "BinOp" in hint:
        import re

        names = re.findall(r"Name\(id='(\w+)'\)", hint)
        non_none = [n for n in names if n != "None"]
        if non_none:
            return (non_none[0], [])

    return ("", [])


# ── Dataclass introspection ──────────────────────────────────────


def _resolve_dataclass(type_name: str, module_path: str) -> Any:
    """Try to import and resolve a type name to a dataclass.

    module_path is the dotted module path of the source file being tested
    (e.g. 'lintgate.types'). We search the source module's imports first.
    """
    # Common project-internal types we know about
    _KNOWN_MODULES: dict[str, str] = {  # noqa: N806
        "LintIssue": "lintgate.types",
        "LinterResult": "lintgate.types",
        "ProjectConfig": "lintgate.types",
        "AggregatedResult": "lintgate.types",
        "LintSummary": "lintgate.types",
        "SpecCore": "lintgate.specification.types",
        "FunctionSpecification": "lintgate.specification.types",
        "TestDesignSignals": "lintgate.specification.types",
        "RiskProfile": "lintgate.specification.types",
        "Traceability": "lintgate.specification.types",
        "ScheduledItem": "lintgate.specification.scheduler",
        "SchedulerStatus": "lintgate.specification.scheduler",
        "OptionalImportReport": "lintgate.linters.import_pattern_detector",
        "OptionalImport": "lintgate.linters.import_pattern_detector",
    }

    # Try known modules first
    if type_name in _KNOWN_MODULES:
        try:
            mod = importlib.import_module(_KNOWN_MODULES[type_name])
            cls = getattr(mod, type_name, None)
            if cls and is_dataclass(cls):
                return cls
        except (ImportError, ModuleNotFoundError):
            pass

    # Try the source module itself
    if module_path:
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, type_name, None)
            if cls and is_dataclass(cls):
                return cls
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass

    return None


def _dataclass_minimal_construction(cls: type) -> tuple[str, list[str]]:
    """Generate minimal constructor call for a dataclass.

    Returns (code, imports) where code is like:
        LintIssue(linter="ruff", kind="F821", message="test")
    Only includes fields without defaults (required fields).
    """
    if not is_dataclass(cls):
        return ("None", [])

    fields = dc_fields(cls)
    args: list[str] = []

    from dataclasses import MISSING

    for f in fields:
        if f.default is not MISSING or f.default_factory is not MISSING:
            continue  # has a default, skip

        # Required field — synthesize a value
        val = _default_for_field(f.name, f.type)
        args.append(f"{f.name}={val}")

    cls_name = cls.__name__
    module = cls.__module__
    code = f"{cls_name}({', '.join(args)})"
    imports = [f"from {module} import {cls_name}"]

    return (code, imports)


def _default_for_field(name: str, type_hint: Any) -> str:
    """Produce a sensible default value for a dataclass field by name and type."""
    # Resolve type hint to string
    hint_str = ""
    if isinstance(type_hint, str):
        hint_str = type_hint
    elif hasattr(type_hint, "__name__"):
        hint_str = type_hint.__name__
    elif hasattr(type_hint, "__origin__"):
        hint_str = getattr(type_hint.__origin__, "__name__", str(type_hint))

    # Use name-based heuristics for better test values
    name_lower = name.lower()
    if "name" in name_lower or "key" in name_lower:
        return '"test"'
    if "path" in name_lower or "file" in name_lower:
        return '"test.py"'
    if "message" in name_lower or "msg" in name_lower:
        return '"test message"'
    if "linter" in name_lower:
        return '"ruff"'
    if "kind" in name_lower or "code" in name_lower:
        return '"T001"'
    if "severity" in name_lower:
        return '"warning"'
    if "status" in name_lower:
        return '"ok"'

    # Type-based fallbacks
    if hint_str in ("str", "string"):
        return '"test"'
    if hint_str in ("int", "integer"):
        return "1"
    if hint_str in ("float",):
        return "1.0"
    if hint_str in ("bool",):
        return "False"

    return '"test"'


# ── Main synthesis entry point ───────────────────────────────────


def synthesize_value(
    annotation: str,
    param_name: str = "",
    module_path: str = "",
) -> SynthesizedValue:
    """Synthesize a valid test value from a type annotation string.

    Args:
        annotation: Type annotation as string — raw Python ('list[LintIssue]')
                    or ast.dump output ("Name(id='list')").
        param_name: Parameter name for heuristic defaults.
        module_path: Dotted module path of the source being tested.

    Returns:
        SynthesizedValue with code expression and required imports.
    """
    if not annotation:
        return _fallback(param_name)

    base, args = _parse_annotation_str(annotation)

    if not base:
        return _fallback(param_name)

    # None type
    if base == "None" or base == "NoneType":
        return SynthesizedValue(code="None", imports=[], is_placeholder=False, type_name="None")

    # Primitives
    if _is_primitive(base):
        code = _PRIMITIVE_DEFAULTS[base]
        # Use name heuristics for strings
        if base == "str" and param_name:
            code = _default_for_field(param_name, "str")
        return SynthesizedValue(code=code, imports=[], is_placeholder=False, type_name=base)

    # Container types
    if base in ("list", "List"):
        if args:
            inner = synthesize_value(args[0], "", module_path)
            if not inner.is_placeholder and inner.type_name not in _PRIMITIVE_NAMES:
                return SynthesizedValue(
                    code=f"[{inner.code}]",
                    imports=inner.imports,
                    is_placeholder=False,
                    type_name=f"list[{inner.type_name}]",
                )
        return SynthesizedValue(code="[]", imports=[], is_placeholder=False, type_name="list")

    if base in ("dict", "Dict"):
        return SynthesizedValue(code="{}", imports=[], is_placeholder=False, type_name="dict")

    if base in ("set", "Set"):
        return SynthesizedValue(code="set()", imports=[], is_placeholder=False, type_name="set")

    if base in ("tuple", "Tuple"):
        return SynthesizedValue(code="()", imports=[], is_placeholder=False, type_name="tuple")

    if base in ("Optional",):
        if args:
            return synthesize_value(args[0], param_name, module_path)
        return SynthesizedValue(code="None", imports=[], is_placeholder=False, type_name="Optional")

    if base in ("Any",):
        return _fallback(param_name)

    # Try resolving as dataclass
    cls = _resolve_dataclass(base, module_path)
    if cls is not None:
        code, imports = _dataclass_minimal_construction(cls)
        return SynthesizedValue(
            code=code,
            imports=imports,
            is_placeholder=False,
            type_name=base,
        )

    # Unknown type — fall back
    return _fallback(param_name)


def _fallback(param_name: str) -> SynthesizedValue:
    """Name-heuristic fallback when type info is unavailable."""
    name_lower = param_name.lower() if param_name else ""
    if "path" in name_lower or "file" in name_lower or "dir" in name_lower:
        return SynthesizedValue(code='"test.py"', imports=[], is_placeholder=True, type_name="str")
    if "name" in name_lower or "key" in name_lower:
        return SynthesizedValue(code='"test"', imports=[], is_placeholder=True, type_name="str")
    if "count" in name_lower or "num" in name_lower or "size" in name_lower:
        return SynthesizedValue(code="0", imports=[], is_placeholder=True, type_name="int")
    if "flag" in name_lower or "enable" in name_lower or "is_" in name_lower:
        return SynthesizedValue(code="False", imports=[], is_placeholder=True, type_name="bool")
    return SynthesizedValue(code="None", imports=[], is_placeholder=True, type_name="unknown")


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
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue

        # Check for empty/pass-only test body
        body_stmts = [
            s
            for s in node.body
            if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)
        ]
        if not body_stmts or (len(body_stmts) == 1 and isinstance(body_stmts[0], ast.Pass)):
            errors.append(f"{node.name} (line {node.lineno}): empty body (pass-only)")

        # Check for at least one assert or pytest.raises
        has_assert = False
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                has_assert = True
                break
            # pytest.raises(...) counts as an assertion
            if isinstance(child, ast.With):
                for item in child.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Call) and _is_pytest_raises(ctx):
                        has_assert = True
                        break
            if has_assert:
                break
        if not has_assert:
            errors.append(f"{node.name} (line {node.lineno}): no assert statement")

        # Check for undefined names in the test body
        undef = _find_undefined_names(node, module_names)
        for name in undef:
            errors.append(f"{node.name} (line {node.lineno}): undefined name '{name}'")

    if errors:
        return False, errors

    if run_pytest and project_root:
        errors.extend(_runtime_validate_with_pytest(test_content, project_root, test_file_name))

    return len(errors) == 0, errors


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


def _find_undefined_names(func_node: ast.FunctionDef, module_names: set[str]) -> list[str]:
    """Find names used in a test function body that aren't defined anywhere visible.

    Checks load-context Name nodes against: module-level names, function params,
    local assignments, builtins, and comprehension targets.
    """
    # Collect function-local names: params + local assignments + for targets + with targets
    local_names: set[str] = set()
    for arg in func_node.args.args:
        local_names.add(arg.arg)

    for child in ast.walk(func_node):
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            local_names.add(elt.id)
        elif isinstance(child, (ast.For, ast.comprehension)):
            if isinstance(child.target, ast.Name):
                local_names.add(child.target.id)
            elif isinstance(child.target, ast.Tuple):
                for elt in child.target.elts:
                    if isinstance(elt, ast.Name):
                        local_names.add(elt.id)
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    local_names.add(item.optional_vars.id)
        elif isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
            local_names.add(child.target.id)

    all_defined = module_names | local_names | _BUILTINS

    # Find Name nodes in Load context that aren't defined
    undefined: list[str] = []
    seen: set[str] = set()
    for child in ast.walk(func_node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            name = child.id
            if name not in all_defined and name not in seen:
                # Skip names used as attributes (e.g. `x.foo` — we only check `x`)
                # and names that look like they come from a dotted import
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
