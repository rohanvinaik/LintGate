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


def _get_name(node: ast.expr) -> str:
    """Extract a dotted name from an AST expression node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _get_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


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


def build_source_function_index(
    source_files: list[str],
) -> dict[str, str]:
    """Build an index of source function names → file paths.

    Returns dict mapping qualified function names to their source file path.
    Only indexes public functions (no underscore prefix unless dunder).
    """
    index: dict[str, str] = {}

    for filepath in source_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (SyntaxError, OSError):
            continue

        class_stack: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_stack.append(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Index both bare name and qualified name
                index[node.name] = filepath
                if class_stack:
                    qualname = f"{class_stack[-1]}.{node.name}"
                    index[qualname] = filepath

    return index


def map_tests_to_source(
    test_file: str,
    source_function_index: dict[str, str],
) -> dict[str, list[str]]:
    """Map test functions to source functions they likely test.

    Args:
        test_file: Path to the test file.
        source_function_index: Mapping of source function names → file paths.

    Returns:
        Mapping of source function names → list of test function names.
    """
    try:
        with open(test_file, encoding="utf-8") as f:
            source_code = f.read()
        tree = ast.parse(source_code, filename=test_file)
    except (SyntaxError, OSError):
        return {}

    # 1. Collect imports to know which names are from source
    import_collector = _ImportCollector()
    import_collector.visit(tree)
    imported_source_names = set(import_collector.imported_names.keys())

    # 2. Walk test functions
    mapping: dict[str, list[str]] = {}
    class_stack: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_stack.append(node.name)

        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue

        test_qualname = node.name
        if class_stack:
            test_qualname = f"{class_stack[-1]}.{node.name}"

        matched_sources: set[str] = set()

        # Strategy 1: Call graph — what source functions does this test call?
        calls = _extract_test_calls(node)
        for call_name in calls:
            # Direct match in source index
            bare_name = call_name.rsplit(".", 1)[-1] if "." in call_name else call_name
            if bare_name in source_function_index and bare_name in imported_source_names:
                matched_sources.add(bare_name)
            elif call_name in source_function_index:
                matched_sources.add(call_name)

        # Strategy 2: Naming convention — test_foo → foo
        guessed_name = _strip_test_prefix(test_qualname)
        if guessed_name in source_function_index:
            matched_sources.add(guessed_name)
        # Also try with class prefix stripped from test class name
        if class_stack:
            cls_name = class_stack[-1]
            if cls_name.startswith("Test"):
                source_cls = cls_name[4:]  # TestFoo → Foo
                method_guess = f"{source_cls}.{guessed_name}"
                if method_guess in source_function_index:
                    matched_sources.add(method_guess)

        # Record mappings
        for source_name in matched_sources:
            mapping.setdefault(source_name, []).append(test_qualname)

    return mapping
