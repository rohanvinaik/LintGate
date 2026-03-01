"""Import tracing resolver — transitive dependency analysis for E402 evidence.

Traces import chains to determine whether a module-level import not at top
of file (E402) has meaningful non-stdlib transitive dependencies, lazy import
patterns, or side-effect-bearing module-level code.

Used for evidence attachment on E402 findings — NOT for severity promotion.
The evidence informs the agent's decision; severity stays as-is.

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field


@dataclass
class LazyImport:
    """An import guarded by a conditional or inside a function."""

    module: str
    guardian: str  # "function" | "if_TYPE_CHECKING" | "try_except" | "conditional"
    line: int = 0


@dataclass
class TransitiveImportResult:
    """Result of tracing a module's transitive import chain."""

    root_module: str
    non_stdlib_deps: set[str] = field(default_factory=set)
    lazy_imports: list[LazyImport] = field(default_factory=list)
    has_module_level_io: bool = False
    total_imports: int = 0
    depth: int = 0  # How many levels deep the trace went


# stdlib module names (Python 3.10+)
_STDLIB_MODULES: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", set()))

# Fallback for Python <3.10
_STDLIB_FALLBACK = frozenset(
    {
        "abc",
        "argparse",
        "ast",
        "asyncio",
        "base64",
        "bisect",
        "calendar",
        "cgi",
        "cmd",
        "codecs",
        "collections",
        "configparser",
        "contextlib",
        "copy",
        "csv",
        "ctypes",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "dis",
        "email",
        "enum",
        "errno",
        "fcntl",
        "fileinput",
        "fnmatch",
        "fractions",
        "ftplib",
        "functools",
        "gc",
        "getpass",
        "gettext",
        "glob",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "importlib",
        "inspect",
        "io",
        "ipaddress",
        "itertools",
        "json",
        "keyword",
        "linecache",
        "locale",
        "logging",
        "lzma",
        "math",
        "mimetypes",
        "multiprocessing",
        "numbers",
        "operator",
        "os",
        "pathlib",
        "pickle",
        "platform",
        "pprint",
        "profile",
        "queue",
        "random",
        "re",
        "readline",
        "reprlib",
        "secrets",
        "select",
        "shelve",
        "shlex",
        "shutil",
        "signal",
        "site",
        "smtplib",
        "socket",
        "sqlite3",
        "ssl",
        "stat",
        "statistics",
        "string",
        "struct",
        "subprocess",
        "sys",
        "syslog",
        "tarfile",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "tkinter",
        "token",
        "tokenize",
        "tomllib",
        "trace",
        "traceback",
        "turtle",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uuid",
        "venv",
        "warnings",
        "wave",
        "weakref",
        "webbrowser",
        "xml",
        "xmlrpc",
        "zipfile",
        "zipimport",
        "zlib",
    }
)

# I/O function names that indicate module-level side effects
_IO_INDICATORS = frozenset(
    {
        "open",
        "connect",
        "listen",
        "bind",
        "run",
        "call",
        "check_output",
        "Popen",
        "makedirs",
        "mkdir",
    }
)


def is_stdlib_module(module_name: str) -> bool:
    """Check if a module name is part of the standard library."""
    top = module_name.split(".")[0]
    stdlib = _STDLIB_MODULES or _STDLIB_FALLBACK
    return top in stdlib


def trace_transitive_imports(
    module_name: str,
    project_root: str,
    *,
    max_depth: int = 3,
) -> TransitiveImportResult:
    """Trace a module's transitive import chain.

    Args:
        module_name: The module to trace (e.g., "requests", "mypackage.utils").
        project_root: Project root for resolving local modules.
        max_depth: Maximum depth of transitive tracing.

    Returns:
        TransitiveImportResult with non-stdlib deps, lazy imports, and I/O flags.
    """
    result = TransitiveImportResult(root_module=module_name)

    # Check the root module itself
    top_module = module_name.split(".")[0]
    if not is_stdlib_module(top_module):
        result.non_stdlib_deps.add(top_module)

    # Try to find and parse the local module file
    module_file = _resolve_module_file(module_name, project_root)
    if module_file:
        _trace_file(
            module_file,
            project_root,
            result,
            visited=set(),
            depth=0,
            max_depth=max_depth,
        )

    return result


def build_e402_evidence(
    import_module: str,
    filepath: str,
    line: int,
    project_root: str,
) -> dict:
    """Build evidence dict for an E402 finding.

    Attaches transitive import information without changing severity.

    Args:
        import_module: The module being imported (from the E402 finding).
        filepath: The file containing the E402.
        line: Line number of the import.
        project_root: Project root path.

    Returns:
        Evidence dict suitable for LintIssue.evidence.
    """
    evidence: dict = {
        "code": "E402",
        "module": import_module,
    }

    # Check if import is lazy/conditional in the source file
    lazy = _detect_lazy_import_at_line(filepath, import_module, line)
    if lazy:
        evidence["lazy_import"] = {
            "guardian": lazy.guardian,
            "line": lazy.line,
        }

    # Trace transitive imports
    trace = trace_transitive_imports(import_module, project_root, max_depth=2)
    evidence["transitive_imports"] = {
        "non_stdlib": sorted(trace.non_stdlib_deps),
        "has_lazy": bool(trace.lazy_imports),
        "has_module_level_io": trace.has_module_level_io,
        "total_imports": trace.total_imports,
    }

    # Semantic placement hints
    if trace.has_module_level_io:
        evidence["placement_semantic"] = (
            "Import has module-level I/O — placement may affect side effects."
        )
    elif _is_argparse_heavy_import(import_module, filepath, project_root):
        evidence["placement_semantic"] = (
            "Import is heavy and file uses argparse — placement may affect CLI startup cost."
        )

    return evidence


# ── Internal helpers ─────────────────────────────────────────────────────


def _resolve_module_file(module_name: str, project_root: str) -> str | None:
    """Try to resolve a module name to a file path within the project."""
    parts = module_name.split(".")
    # Try as package: module_name/__init__.py
    # Try as module: module_name.py
    for candidate_parts in [parts + ["__init__"], parts]:
        candidate = os.path.join(project_root, *candidate_parts) + ".py"
        if os.path.isfile(candidate):
            return candidate
    # Try in src/ layout
    for candidate_parts in [["src"] + parts + ["__init__"], ["src"] + parts]:
        candidate = os.path.join(project_root, *candidate_parts) + ".py"
        if os.path.isfile(candidate):
            return candidate
    return None


def _trace_file(
    filepath: str,
    project_root: str,
    result: TransitiveImportResult,
    visited: set[str],
    depth: int,
    max_depth: int,
) -> None:
    """Parse a file and trace its imports transitively."""
    if filepath in visited or depth >= max_depth:
        return
    visited.add(filepath)
    result.depth = max(result.depth, depth)

    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result.total_imports += 1
            modules = _extract_import_modules(node)

            for mod in modules:
                top = mod.split(".")[0]
                if not is_stdlib_module(top) and top != "__future__":
                    result.non_stdlib_deps.add(top)

            # Check if it's a lazy import
            lazy = _classify_import_context(node, tree)
            if lazy:
                result.lazy_imports.append(lazy)

        # Check for module-level I/O
        if isinstance(node, ast.Call) and depth == 0:
            call_name = _get_call_name(node)
            if call_name and call_name.split(".")[-1] in _IO_INDICATORS:
                # Only count module-level calls (not inside functions)
                if _is_module_level(node, tree):
                    result.has_module_level_io = True


def _extract_import_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Extract module names from an import statement."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _classify_import_context(
    node: ast.Import | ast.ImportFrom,
    tree: ast.Module,
) -> LazyImport | None:
    """Classify whether an import is lazy (guarded by try/except, if, or function)."""
    module = ""
    if isinstance(node, ast.Import) and node.names:
        module = node.names[0].name
    elif isinstance(node, ast.ImportFrom) and node.module:
        module = node.module

    if not module:
        return None

    # Walk the tree to find the import's parent context
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if child is node:
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return LazyImport(
                        module=module, guardian="function", line=node.lineno
                    )
                if isinstance(parent, ast.If):
                    # Check for TYPE_CHECKING guard
                    test = parent.test
                    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                        return LazyImport(
                            module=module, guardian="if_TYPE_CHECKING", line=node.lineno
                        )
                    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                        return LazyImport(
                            module=module, guardian="if_TYPE_CHECKING", line=node.lineno
                        )
                    return LazyImport(
                        module=module, guardian="conditional", line=node.lineno
                    )
                if isinstance(parent, (ast.ExceptHandler, ast.Try)):
                    return LazyImport(
                        module=module, guardian="try_except", line=node.lineno
                    )

    return None


def _detect_lazy_import_at_line(
    filepath: str,
    module_name: str,
    line: int,
) -> LazyImport | None:
    """Check if the import at a specific line is lazy/conditional."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return None

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if node.lineno != line:
            continue
        return _classify_import_context(node, tree)

    return None


def _is_module_level(node: ast.AST, tree: ast.Module) -> bool:
    """Check if a node is at module level (not inside a function/class)."""
    for top_level in tree.body:
        if top_level is node:
            return True
        # Check if node is a direct child of a module-level expression
        if isinstance(top_level, ast.Expr) and top_level.value is node:
            return True
    return False


def _get_call_name(node: ast.Call) -> str | None:
    """Extract the name of a function call."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        return node.func.attr
    return None


def _is_argparse_heavy_import(
    module_name: str,
    filepath: str,
    project_root: str,
) -> bool:
    """Check if file uses argparse and the import is a heavy non-stdlib dep."""
    if is_stdlib_module(module_name.split(".")[0]):
        return False
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            source = f.read()
        return "argparse" in source
    except OSError:
        return False
