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
import importlib
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


from .typed_synthesis_factories import (  # noqa: F401, E402
    synthesize_factory,
    validate_test_file,
)
