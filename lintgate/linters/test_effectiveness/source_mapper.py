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
from typing import TYPE_CHECKING

from lintgate.keys import canonical_function_key, canonical_relpath

if TYPE_CHECKING:
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics


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

    def _handle_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
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


def map_tests_to_source(
    test_file: str,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None = None,
    diagnostics: MappingDiagnostics | None = None,
) -> dict[str, list[str]]:
    """Map test functions to source functions they likely test.

    Args:
        test_file: Path to the test file.
        source_function_index: Mapping of source function names → path(s).
        project_root: Project root path for unique keys. If None, returns
            legacy keys (`function_name`) for backward compatibility.
        diagnostics: Optional object to track drop reasons and metrics.

    Returns:
        Mapping of:
          - `'relpath::function' -> [test_names]` when `project_root` is provided
          - `'function' -> [test_names]` when `project_root` is None
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
    local_defs = _LocalDefinitionCollector()
    local_defs.visit(tree)

    if project_root is None:
        project_root = os.path.dirname(test_file)
        use_unique_keys = False
    else:
        use_unique_keys = True

    # 2. Walk test functions using scoped visitor
    collector = _TestFunctionCollector()
    collector.visit(tree)
    mapping: dict[str, list[str]] = {}
    for test_qualname, node, class_name in collector.tests:
        matched_keys: set[str] = set()

        _apply_call_graph_strategy(
            node,
            import_collector,
            local_defs,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
        )

        _apply_naming_strategy(
            test_qualname,
            class_name,
            import_collector,
            local_defs,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
        )

        if diagnostics is not None:
            diagnostics.counts.attempted += 1
            diagnostics._test_funcs.add(test_qualname)
            if matched_keys:
                diagnostics.counts.mapped += 1

        # Record mappings
        for key in matched_keys:
            mapping.setdefault(key, []).append(test_qualname)

    if diagnostics is not None:
        # Calculate high-level stats
        diagnostics.symbol_stats.attempted = len(diagnostics._attempted_symbols)
        diagnostics.symbol_stats.mapped = len(diagnostics._mapped_symbols)
        diagnostics.symbol_stats.test_functions_examined = len(diagnostics._test_funcs)

        drops = {
            "ambiguous": diagnostics.counts.dropped_ambiguous,
            "no_candidate": diagnostics.counts.dropped_no_candidate,
            "shadowed": diagnostics.counts.dropped_shadowed,
        }
        total_drops = sum(drops.values())
        if total_drops > 0:
            dominant = max(drops.items(), key=lambda x: x[1])
            diagnostics.drop_analysis.dominant_reason = dominant[0]
            diagnostics.drop_analysis.dominant_pct = round(dominant[1] / total_drops, 2)
        else:
            diagnostics.drop_analysis.dominant_reason = None
            diagnostics.drop_analysis.dominant_pct = None

        # Aggregate drop examples: build frequency map and pick top-N
        # (#66) Rank by frequency with deterministic tie-break (symbol name)
        from collections import Counter

        drop_freq: Counter[tuple[str, str]] = Counter(
            (ex.get("symbol", ""), ex.get("reason", "")) for ex in diagnostics._drop_examples
        )
        # Index first occurrence by (symbol, reason) for O(1) lookup
        first_occurrence: dict[tuple[str, str], dict[str, str]] = {}
        for ex in diagnostics._drop_examples:
            key = (ex.get("symbol", ""), ex.get("reason", ""))
            if key not in first_occurrence:
                first_occurrence[key] = ex
        ranked_examples: list[dict[str, str]] = [
            first_occurrence[key]
            for key, _count in sorted(drop_freq.items(), key=lambda kv: (-kv[1], kv[0]))
            if key in first_occurrence
        ]
        diagnostics.drop_analysis.top_examples = ranked_examples[:5]

    return mapping


def _try_add_match(
    func_name: str,
    module_hint: str | None,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None,
    use_unique_keys: bool,
    matched_keys: set[str],
    diagnostics: MappingDiagnostics | None,
    strategy: str = "unknown",
) -> bool:
    if diagnostics is not None:
        if strategy not in diagnostics.strategy_breakdown:
            from lintgate.linters.test_effectiveness.types import StrategyDiagnostics

            diagnostics.strategy_breakdown[strategy] = StrategyDiagnostics(strategy=strategy)
        sd = diagnostics.strategy_breakdown[strategy]
        sd.attempted += 1
        diagnostics._attempted_symbols.add(func_name)

    candidates = _coerce_candidate_paths(source_function_index.get(func_name))
    if not candidates:
        if diagnostics is not None:
            sd.dropped_no_candidate += 1
            diagnostics.counts.dropped_no_candidate += 1
            diagnostics._drop_examples.append(
                {"symbol": func_name, "reason": "no_candidate", "strategy": strategy}
            )
        return False

    if module_hint and project_root:
        candidates = _filter_candidates_by_module_hint(candidates, module_hint, project_root)

    if len(candidates) != 1:
        if diagnostics is not None:
            sd.dropped_ambiguous += 1
            diagnostics.counts.dropped_ambiguous += 1
            diagnostics._drop_examples.append(
                {"symbol": func_name, "reason": "ambiguous", "strategy": strategy}
            )
        return False

    chosen = candidates[0]
    if use_unique_keys and project_root:
        relpath = canonical_relpath(chosen, project_root)
        matched_keys.add(canonical_function_key(relpath, func_name))
    else:
        matched_keys.add(func_name)

    if diagnostics is not None:
        sd.mapped += 1
        diagnostics._mapped_symbols.add(func_name)
    return True


def _process_test_call(
    call_name: str,
    import_collector: _ImportCollector,
    local_defs: _LocalDefinitionCollector,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None,
    use_unique_keys: bool,
    matched_keys: set[str],
    diagnostics: MappingDiagnostics | None,
) -> bool:
    bare_name = call_name.rsplit(".", 1)[-1] if "." in call_name else call_name
    qualifier = call_name.rsplit(".", 1)[0] if "." in call_name else ""

    if bare_name in local_defs.defined_names and bare_name not in import_collector.imported_names:
        if diagnostics is not None:
            diagnostics.counts.dropped_shadowed += 1
            if "call_graph" not in diagnostics.strategy_breakdown:
                from lintgate.linters.test_effectiveness.types import (
                    StrategyDiagnostics,
                )

                diagnostics.strategy_breakdown["call_graph"] = StrategyDiagnostics(
                    strategy="call_graph"
                )
            diagnostics.strategy_breakdown["call_graph"].dropped_shadowed += 1
            diagnostics._drop_examples.append(
                {"symbol": bare_name, "reason": "shadowed", "strategy": "call_graph"}
            )
        return False

    module_hint: str | None = None
    if call_name in import_collector.imported_names:
        module_hint = _module_hint_from_import(import_collector.imported_names[call_name])
    elif qualifier and qualifier in import_collector.imported_names:
        module_hint = import_collector.imported_names[qualifier]

    # Try the exact call name if it looks like a direct match or is a qualified call
    if call_name in source_function_index or qualifier:
        success = _try_add_match(
            call_name,
            module_hint,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
            strategy="call_graph",
        )
        if success:
            return True
        if call_name != bare_name and call_name in source_function_index:
            # If it was a direct hit on a non-bare name (e.g. self.foo), we don't fallback
            return False

    # Fallback/alternative matching logic for bare name or imports
    module_hint = None
    if bare_name in import_collector.imported_names:
        module_hint = _module_hint_from_import(import_collector.imported_names[bare_name])
    elif qualifier and qualifier in import_collector.imported_names:
        module_hint = import_collector.imported_names[qualifier]

    success = False
    if (
        bare_name in source_function_index
        or bare_name in import_collector.imported_names
        or module_hint
    ) and _try_add_match(
        bare_name,
        module_hint,
        source_function_index,
        project_root,
        use_unique_keys,
        matched_keys,
        diagnostics,
        strategy="call_graph",
    ):
        success = True

    # (#63) alias_import strategy: if the call uses a local alias (e.g. import foo as f),
    # try to resolve the underlying symbol name through the import table.
    if bare_name in import_collector.imported_names:
        imported_qualified = import_collector.imported_names[bare_name]
        imported_symbol = _symbol_name_from_import(imported_qualified)
        if imported_symbol != bare_name:
            # This is an aliased import — record under dedicated strategy bucket
            alias_hint = _module_hint_from_import(imported_qualified)
            if _try_add_match(
                imported_symbol,
                alias_hint,
                source_function_index,
                project_root,
                use_unique_keys,
                matched_keys,
                diagnostics,
                strategy="alias_import",  # explicit strategy bucket for alias resolution
            ):
                success = True

    return success


def _apply_call_graph_strategy(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    import_collector: _ImportCollector,
    local_defs: _LocalDefinitionCollector,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None,
    use_unique_keys: bool,
    matched_keys: set[str],
    diagnostics: MappingDiagnostics | None,
) -> None:
    calls = _extract_test_calls(node)

    for call_name in calls:
        _process_test_call(
            call_name,
            import_collector,
            local_defs,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
        )


def _apply_naming_strategy(
    test_qualname: str,
    class_name: str | None,
    import_collector: _ImportCollector,
    local_defs: _LocalDefinitionCollector,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None,
    use_unique_keys: bool,
    matched_keys: set[str],
    diagnostics: MappingDiagnostics | None,
) -> None:
    guessed_name = _strip_test_prefix(test_qualname)
    if guessed_name not in local_defs.defined_names:
        guessed_hint = None
        if guessed_name in import_collector.imported_names:
            guessed_hint = _module_hint_from_import(import_collector.imported_names[guessed_name])
        _try_add_match(
            guessed_name,
            guessed_hint,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
            "naming",
        )

    if class_name and class_name.startswith("Test"):
        source_cls = class_name[4:]  # TestFoo → Foo
        method_guess = f"{source_cls}.{guessed_name}"
        _try_add_match(
            method_guess,
            None,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
            "naming",
        )
