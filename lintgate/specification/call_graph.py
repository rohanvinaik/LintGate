"""Cross-module function call graph — lightweight resolution for composition analysis.

Builds a function-level call graph across modules by:
1. Parsing each Python file's AST for function definitions
2. Collecting ast.Call nodes within each function body
3. Resolving call targets against imported names (1-hop only)

No transitive import chasing — unresolvable calls are silently skipped.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lintgate.keys import canonical_function_key, canonical_relpath


@dataclass
class CrossModuleCallGraph:
    """Function-level call graph across modules.

    Keys are qualified function names: "relpath::qualname".
    """

    calls: dict[str, set[str]] = field(default_factory=dict)
    called_by: dict[str, set[str]] = field(default_factory=dict)

    def fan_in(self, func_key: str) -> int:
        """Number of functions that call this function."""
        return len(self.called_by.get(func_key, set()))

    def fan_out(self, func_key: str) -> int:
        """Number of functions this function calls."""
        return len(self.calls.get(func_key, set()))


def build_cross_module_call_graph(py_files: list[str], project_root: str) -> CrossModuleCallGraph:
    """Build a cross-module function call graph from Python source files.

    Args:
        py_files: Absolute paths to Python source files.
        project_root: Project root for computing relative paths.
    """
    graph = CrossModuleCallGraph()
    root = Path(project_root)

    # Phase 1: collect all function definitions and imports per file
    file_data: dict[str, _FileData] = {}
    for filepath in py_files:
        data = _parse_file(filepath, root)
        if data is not None:
            file_data[filepath] = data

    # Phase 2: build a global name → qualified key lookup
    global_names = _build_global_name_map(file_data)

    # Phase 3: resolve calls to qualified keys
    for _filepath, data in file_data.items():
        _resolve_calls(data, global_names, graph)

    return graph


@dataclass
class _FileData:
    """Parsed data for a single source file."""

    relpath: str
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]  # simple_name → node
    imports: dict[str, str]  # imported_name → module_path
    call_sites: dict[str, list[str]]  # func_name → [called_names]


def _parse_file(filepath: str, root: Path) -> _FileData | None:
    """Parse a single Python file for functions, imports, and call sites."""
    try:
        source = Path(filepath).read_text()
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    relpath = _compute_relpath(filepath, root)
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    imports: dict[str, str] = {}
    call_sites: dict[str, list[str]] = {}

    # Collect top-level and class-level function defs
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node

    # Collect imports
    for node in ast.iter_child_nodes(tree):
        _collect_imports(node, imports)

    # Collect call sites per function
    for func_name, func_node in functions.items():
        calls = _collect_call_names(func_node)
        if calls:
            call_sites[func_name] = calls

    return _FileData(
        relpath=relpath,
        functions=functions,
        imports=imports,
        call_sites=call_sites,
    )


def _collect_imports(node: ast.AST, imports: dict[str, str]) -> None:
    """Extract import name → module path mappings from an import node."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[-1]
            imports[name] = alias.name
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            name = alias.asname or alias.name
            imports[name] = f"{module}.{alias.name}" if module else alias.name


def _collect_call_names(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Collect simple call target names within a function body."""
    names: list[str] = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        name = _extract_call_target(node)
        if name:
            names.append(name)
    return names


def _extract_call_target(call_node: ast.Call) -> str | None:
    """Extract the simple name from a Call node."""
    if isinstance(call_node.func, ast.Name):
        return call_node.func.id
    if isinstance(call_node.func, ast.Attribute):
        return call_node.func.attr
    return None


def _build_global_name_map(
    file_data: dict[str, _FileData],
) -> dict[str, str]:
    """Build a mapping from simple function name → qualified key.

    When names collide across files, all mappings are kept (last wins).
    This is acceptable for the 1-hop resolution strategy.
    """
    name_map: dict[str, str] = {}
    for data in file_data.values():
        for func_name in data.functions:
            qualified = canonical_function_key(data.relpath, func_name)
            name_map[func_name] = qualified
    return name_map


def _resolve_calls(
    data: _FileData,
    global_names: dict[str, str],
    graph: CrossModuleCallGraph,
) -> None:
    """Resolve call sites in a file to qualified function keys."""
    for func_name, called_names in data.call_sites.items():
        caller_key = canonical_function_key(data.relpath, func_name)
        for called in called_names:
            callee_key = _resolve_one(called, data, global_names)
            if callee_key is None or callee_key == caller_key:
                continue
            graph.calls.setdefault(caller_key, set()).add(callee_key)
            graph.called_by.setdefault(callee_key, set()).add(caller_key)


def _resolve_one(name: str, data: _FileData, global_names: dict[str, str]) -> str | None:
    """Resolve a single call name to a qualified key.

    Resolution order:
    1. Local function in same file
    2. Imported name → resolve via global name map
    3. Global name map (direct match)
    """
    # Local function
    if name in data.functions:
        return canonical_function_key(data.relpath, name)

    # Imported name — try to find in global names
    if name in data.imports:
        imported_module = data.imports[name]
        # The imported name might be the function itself
        simple = imported_module.split(".")[-1]
        if simple in global_names:
            return global_names[simple]

    # Direct global lookup
    if name in global_names:
        return global_names[name]

    return None


def _compute_relpath(filepath: str, root: Path) -> str:
    """Compute relative path from project root, preserving extension.

    Keys must match PropertyManifest/TestEffectivenessManifest format:
    "relpath.py::qualname".

    Delegates to canonical_relpath for consistent behavior.
    """
    return canonical_relpath(filepath, str(root))
