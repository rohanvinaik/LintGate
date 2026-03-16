"""Test-to-source mapping heuristics.

Resolves which source functions each test function exercises using three
strategies (in priority order):
1. Import analysis: parse test file imports → resolve to source modules
2. Naming convention: test_foo_bar → foo_bar
3. Call graph: AST scan for function calls in test bodies → match to source

No coverage.py dependency — pure AST heuristic.
"""

from __future__ import annotations

import ast
import os

# ── Name extraction + utilities ───────────────────────────────────


def _get_name(node: ast.expr) -> str:
    """Extract a dotted name from an AST expression node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _get_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


# ── AST visitors for test/source analysis ─────────────────────────


class _ImportCollector(ast.NodeVisitor):
    """Collects imported names from a test file."""

    def __init__(self) -> None:
        self.imported_modules: list[str] = []  # e.g., ["lintgate.types"]
        self.imported_names: dict[str, str] = {}  # local_name → qualified_name

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imported_modules.append(alias.name)
            local = alias.asname or alias.name
            self.imported_names[local] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module:
            self.imported_modules.append(module)
        for alias in node.names:
            local = alias.asname or alias.name
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.imported_names[local] = qualified


class _CallCollector(ast.NodeVisitor):
    """Collects function call names within a test function body."""

    def __init__(self) -> None:
        self.called_names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        name = _get_name(node.func)
        if name:
            self.called_names.add(name)
        self.generic_visit(node)


def _extract_test_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Extract all function/method calls from a test function body."""
    collector = _CallCollector()
    for child in ast.iter_child_nodes(node):
        collector.visit(child)
    return collector.called_names


class _TestFunctionCollector(ast.NodeVisitor):
    """Collect test functions with accurate lexical class scope."""

    def __init__(self) -> None:
        self.tests: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = []
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_func(node)

    def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._maybe_record(node)
        self.generic_visit(node)

    def _maybe_record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.name.startswith("test_"):
            return
        class_name = self._class_stack[-1] if self._class_stack else None
        qualname = node.name if class_name is None else f"{class_name}.{node.name}"
        self.tests.append((qualname, node, class_name))


class _LocalDefinitionCollector(ast.NodeVisitor):
    """Collect local names defined in the test module that can shadow imports."""

    def __init__(self) -> None:
        self.defined_names: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_name(node.name, kind="func")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_name(node.name, kind="async_func")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_name(node.name, kind="class")
        self.generic_visit(node)

    def _record_name(self, name: str, kind: str) -> None:
        self.defined_names.add(name)


def _strip_test_prefix(test_name: str) -> str:
    """Strip test_ prefix and common suffixes to guess source function name.

    test_foo_bar → foo_bar
    test_foo_returns_expected → foo
    test_foo_raises_error → foo
    TestFoo.test_bar → bar
    """
    # Handle class-qualified names
    if "." in test_name:
        test_name = test_name.rsplit(".", 1)[-1]

    if not test_name.startswith("test_"):
        return test_name

    stripped = test_name[5:]  # Remove 'test_'

    # Try to remove common suffixes that describe test intent, not function name
    suffixes = [
        "_returns_expected_output",
        "_returns_expected",
        "_raises_error",
        "_raises_exception",
        "_handles_errors_gracefully",
        "_handles_errors",
        "_with_valid_input",
        "_with_invalid_input",
        "_on_invalid_input",
        "_with_defaults",
        "_boundary_values",
        "_edge_cases",
        "_modifies_state",
        "_is_correct",
        "_works",
    ]
    for suffix in suffixes:
        if stripped.endswith(suffix):
            candidate = stripped[: -len(suffix)]
            if candidate:
                return candidate

    return stripped


class _SourceFunctionVisitor(ast.NodeVisitor):
    """Visitor to collect function names with correct class scope."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.index: dict[str, list[str]] = {}
        self.class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_def(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_def(node, is_async=True)

    def _handle_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:  # noqa: ARG002
        self._record_function(node.name)
        self.generic_visit(node)

    def _record_function(self, name: str) -> None:
        self.index.setdefault(name, []).append(self.filepath)
        if self.class_stack:
            qualname = f"{self.class_stack[-1]}.{name}"
            self.index.setdefault(qualname, []).append(self.filepath)


def _merge_index_entry(
    index: dict[str, str | list[str]],
    key: str,
    filepath: str,
) -> None:
    """Insert filepath under key while preserving backward-compatible shape."""
    existing = index.get(key)
    if existing is None:
        index[key] = filepath
        return
    if isinstance(existing, list):
        if filepath not in existing:
            existing.append(filepath)
        return
    if existing != filepath:
        index[key] = [existing, filepath]


def _coerce_candidate_paths(value: str | list[str] | None) -> list[str]:
    """Normalize index entry into a candidate path list."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(dict.fromkeys(value))
    return [value]


def _path_to_module(path: str, project_root: str) -> str:
    """Best-effort module name from file path."""
    try:
        rel = os.path.relpath(path, project_root)
    except ValueError:
        rel = os.path.basename(path)
    rel = rel.replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def _module_hint_from_import(qualified: str) -> str:
    """Extract module hint from an import reference."""
    if "." not in qualified:
        return qualified
    return qualified.rsplit(".", 1)[0]


def _symbol_name_from_import(qualified: str) -> str:
    """Extract symbol name from an import reference."""
    if "." not in qualified:
        return qualified
    return qualified.rsplit(".", 1)[-1]


def _filter_candidates_by_module_hint(
    candidates: list[str],
    module_hint: str,
    project_root: str,
) -> list[str]:
    """Keep candidates whose module appears to match an import/module hint."""
    if not module_hint:
        return candidates
    filtered: list[str] = []
    for path in candidates:
        module = _path_to_module(path, project_root)
        if (
            module == module_hint
            or module.endswith(f".{module_hint}")
            or module.split(".")[-1] == module_hint
        ):
            filtered.append(path)
    # Keep unique candidates even when module inference is noisy/missing.
    if not filtered and len(candidates) == 1:
        return candidates
    return filtered


# ── Source function index building ─────────────────────────────────


def build_source_function_index(
    source_files: list[str],
) -> dict[str, str | list[str]]:
    """Build an index of source function names → source path(s).

    Returns:
      - `name -> filepath` when unique
      - `name -> [filepath, ...]` when ambiguous across multiple files

    This preserves backward compatibility for single-path lookups while
    still retaining ambiguity information for mapping disambiguation.

    Only indexes public functions (no underscore prefix unless dunder).
    """
    index: dict[str, str | list[str]] = {}

    for filepath in source_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (SyntaxError, OSError):
            continue

        visitor = _SourceFunctionVisitor(filepath)
        visitor.visit(tree)
        for key in visitor.index:
            _merge_index_entry(index, key, filepath)

    return index


from .source_mapper_mapping import (  # noqa: F401, E402
    _apply_call_graph_strategy,
    _apply_naming_strategy,
    _process_test_call,
    _record_shadowed_drop,
    _resolve_module_hint,
    _resolve_symbol_name_for_match,
    _try_add_match,
    _try_alias_import,
    map_tests_to_source,
)
