"""Test symbol resolver — detect stale test references to deleted symbols.

When a test fails because it imports or patches a symbol that no longer exists,
agents instinctively try to satisfy the test specification — even when the
specification itself is outdated.  This module detects that pattern automatically.

Techniques:
1. Parse test file AST for ``from module import name`` statements
2. Parse monkeypatch.setattr("module.name", ...) targets
3. Resolve each imported symbol against the current source tree
4. Return list of unresolvable symbols with confidence and verdict

Finding code: **TEFF009 — Stale test references deleted symbol**
"""

from __future__ import annotations

import ast
import importlib.util
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnresolvedSymbol:
    """A symbol referenced by a test that cannot be found in the source tree."""

    module: str
    symbol: str
    test_file: str
    test_function: str | None = None
    line: int | None = None
    source: str = "import"  # "import" | "monkeypatch" | "sys_argv"
    confidence: float = 0.95


@dataclass
class SymbolResolutionResult:
    """Result of resolving symbols referenced by a failing test."""

    test_file: str
    test_function: str | None = None
    unresolved: list[UnresolvedSymbol] = field(default_factory=list)
    resolved_count: int = 0
    verdict: str = "valid_failure"  # "stale_test" | "potential_regression" | "valid_failure"
    confidence: float = 0.85


def check_test_symbol_resolution(
    test_file: str,
    project_root: str,
    test_function: str | None = None,
) -> SymbolResolutionResult:
    """Check if symbols referenced by a test file still exist in the project.

    Parses the test file AST for:
    1. ``from module import name`` statements
    2. ``monkeypatch.setattr("module.name", ...)`` calls
    3. ``sys.argv = [...]`` assignments with CLI flags

    Resolves each against the current source tree and returns unresolvable
    symbols.
    """
    result = SymbolResolutionResult(test_file=test_file, test_function=test_function)

    try:
        with open(test_file, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=test_file)
    except (OSError, SyntaxError):
        return result

    # Collect symbol references from the test file
    refs = _extract_symbol_references(tree, test_file, project_root)

    # Resolve each reference against the source tree
    for ref in refs:
        if _symbol_exists(ref["module"], ref["symbol"], project_root):
            result.resolved_count += 1
        else:
            result.unresolved.append(
                UnresolvedSymbol(
                    module=ref["module"],
                    symbol=ref["symbol"],
                    test_file=test_file,
                    test_function=test_function,
                    line=ref.get("line"),
                    source=ref.get("source", "import"),
                    confidence=0.95 if ref.get("source") == "import" else 0.90,
                )
            )

    # Determine verdict
    if result.unresolved:
        result.verdict = "stale_test"
        result.confidence = 0.95
    elif result.resolved_count > 0:
        result.verdict = "valid_failure"
        result.confidence = 0.85
    else:
        result.verdict = "valid_failure"
        result.confidence = 0.70

    return result


def _extract_symbol_references(
    tree: ast.AST,
    test_file: str,
    project_root: str,
) -> list[dict[str, Any]]:
    """Extract symbol references from a test file AST.

    Returns list of dicts with keys: module, symbol, line, source.
    Only includes references to project-internal modules (not stdlib/third-party).
    """
    refs: list[dict[str, Any]] = []

    # Determine project package names for filtering
    project_packages = _detect_project_packages(project_root)

    for node in ast.walk(tree):
        # 1. from module import name
        if isinstance(node, ast.ImportFrom) and node.module and node.names:
            if not _is_project_module(node.module, project_packages):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                refs.append(
                    {
                        "module": node.module,
                        "symbol": alias.name,
                        "line": node.lineno,
                        "source": "import",
                    }
                )

        # 2. monkeypatch.setattr("module.symbol", ...) or
        #    monkeypatch.setattr(module, "symbol", ...)
        if isinstance(node, ast.Call):
            mp_ref = _extract_monkeypatch_target(node, project_packages)
            if mp_ref:
                refs.append(mp_ref)

    return refs


def _extract_monkeypatch_target(
    node: ast.Call,
    project_packages: set[str],
) -> dict[str, Any] | None:
    """Extract module.symbol from monkeypatch.setattr() calls."""
    # Check for monkeypatch.setattr or mp.setattr pattern
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "setattr":
        return None

    if not node.args:
        return None

    first_arg = node.args[0]

    # Pattern 1: monkeypatch.setattr("module.symbol", value)
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        dotpath = first_arg.value
        if "." not in dotpath:
            return None
        parts = dotpath.rsplit(".", 1)
        module_path, symbol = parts[0], parts[1]
        if not _is_project_module(module_path, project_packages):
            return None
        return {
            "module": module_path,
            "symbol": symbol,
            "line": node.lineno,
            "source": "monkeypatch",
        }

    # Pattern 2: monkeypatch.setattr(module_ref, "symbol", value)
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        raw_symbol = node.args[1].value
        if not isinstance(raw_symbol, str):
            return None
        symbol: str = raw_symbol
        # Try to get the module name from the first arg
        if isinstance(first_arg, ast.Attribute):
            # e.g., lintgate.lint_runner
            resolved = _reconstruct_dotpath(first_arg)
            if resolved and _is_project_module(resolved, project_packages):
                return {
                    "module": resolved,
                    "symbol": symbol,
                    "line": node.lineno,
                    "source": "monkeypatch",
                }
        elif isinstance(first_arg, ast.Name):
            # e.g., monkeypatch.setattr(my_module, "func", ...)
            # Can't resolve the module name from a variable reference
            pass

    return None


def _reconstruct_dotpath(node: ast.Attribute) -> str | None:
    """Reconstruct a dotted path from nested ast.Attribute nodes."""
    parts: list[str] = [node.attr]
    current = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _detect_project_packages(project_root: str) -> set[str]:
    """Detect top-level package names in the project."""
    packages: set[str] = set()
    try:
        for entry in os.listdir(project_root):
            full = os.path.join(project_root, entry)
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                packages.add(entry)
    except OSError:
        pass
    return packages


def _is_project_module(module_path: str, project_packages: set[str]) -> bool:
    """Check if a module path belongs to the project (not stdlib/third-party)."""
    top_level = module_path.split(".")[0]
    return top_level in project_packages


def _symbol_exists(module_path: str, symbol_name: str, project_root: str) -> bool:
    """Check if a symbol exists in the given module within the project.

    Uses AST parsing to check for:
    - Top-level function/class definitions
    - Module-level variable assignments
    - Re-exports via __all__ or import chains
    """
    # Convert module path to file path
    rel_path = module_path.replace(".", os.sep)
    candidates = [
        os.path.join(project_root, rel_path + ".py"),
        os.path.join(project_root, rel_path, "__init__.py"),
    ]

    for filepath in candidates:
        if not os.path.isfile(filepath):
            continue

        try:
            with open(filepath, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
        except (OSError, SyntaxError):
            continue

        if _find_symbol_in_ast(tree, symbol_name):
            return True

        # Check re-exports: if __init__.py imports from submodules
        if filepath.endswith("__init__.py") and _find_reexport(
            tree, symbol_name, module_path, project_root
        ):
            return True

    # Last resort: try importlib spec resolution (catches installed packages)
    return _try_importlib_resolve(module_path, symbol_name)


def _node_defines_symbol(node: ast.AST, symbol_name: str) -> bool:
    """Check if a single AST node defines the given symbol name."""
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == symbol_name
    ):
        return True
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == symbol_name for t in node.targets)
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == symbol_name
    ):
        return True
    if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names:
        return any((a.asname or a.name) == symbol_name for a in node.names)
    return False


def _find_symbol_in_ast(tree: ast.AST, symbol_name: str) -> bool:
    """Check if a symbol is defined at the top level of an AST."""
    return any(_node_defines_symbol(node, symbol_name) for node in ast.iter_child_nodes(tree))


def _find_reexport(
    tree: ast.AST,
    symbol_name: str,
    parent_module: str,
    project_root: str,
) -> bool:
    """Check if a symbol is re-exported from a submodule via __init__.py."""
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.names:
            continue
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            if name != symbol_name:
                continue
            # Found a re-export candidate — resolve the source module
            if node.module:
                source_module = f"{parent_module}.{node.module}" if node.level > 0 else node.module
                return _symbol_exists(source_module, alias.name, project_root)
    return False


def _try_importlib_resolve(module_path: str, symbol_name: str) -> bool:
    """Try to resolve a symbol using importlib.util.find_spec.

    This is a fallback for installed packages. Returns False for safety
    if the module can't be found — we'd rather report a false positive
    (stale reference to a moved symbol) than miss a genuinely deleted symbol.
    """
    try:
        spec = importlib.util.find_spec(module_path)
        # Module exists — we assume the symbol is there if the module resolves.
        # This is conservative: we only flag symbols where even the module is gone.
        return spec is not None
    except (ModuleNotFoundError, ValueError, AttributeError):
        return False


def build_stale_test_findings(
    test_file: str,
    project_root: str,
    test_function: str | None = None,
) -> list[dict[str, Any]]:
    """Build TEFF009 finding dicts for stale test references.

    Returns a list of finding dicts ready for conversion to LintIssue.
    Each dict has: module, symbol, test_file, line, source, confidence, verdict.
    """
    result = check_test_symbol_resolution(test_file, project_root, test_function)

    if result.verdict != "stale_test":
        return []

    findings: list[dict[str, Any]] = []
    for sym in result.unresolved:
        findings.append(
            {
                "module": sym.module,
                "symbol": sym.symbol,
                "test_file": sym.test_file,
                "test_function": sym.test_function,
                "line": sym.line,
                "source": sym.source,
                "confidence": sym.confidence,
                "verdict": "stale_test",
            }
        )

    return findings
