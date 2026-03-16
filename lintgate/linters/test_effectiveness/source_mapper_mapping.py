"""Test-to-source mapping logic and matching strategies.

Split from source_mapper.py — contains map_tests_to_source and all
supporting match/resolve/strategy functions.
"""

from __future__ import annotations

import ast
import os
from typing import TYPE_CHECKING

from lintgate.keys import canonical_function_key, canonical_relpath

if TYPE_CHECKING:
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics

from .source_mapper import (
    _coerce_candidate_paths,
    _extract_test_calls,
    _filter_candidates_by_module_hint,
    _ImportCollector,
    _LocalDefinitionCollector,
    _module_hint_from_import,
    _strip_test_prefix,
    _symbol_name_from_import,
    _TestFunctionCollector,
)

# ── Test-to-source mapping ────────────────────────────────────────


def map_tests_to_source(
    test_file: str,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None = None,
    diagnostics: MappingDiagnostics | None = None,
) -> dict[str, list[str]]:
    """Map test functions to source functions they likely test."""
    try:
        with open(test_file, encoding="utf-8") as f:
            source_code = f.read()
        tree = ast.parse(source_code, filename=test_file)
    except (SyntaxError, OSError):
        return {}

    import_collector = _ImportCollector()
    import_collector.visit(tree)
    local_defs = _LocalDefinitionCollector()
    local_defs.visit(tree)

    if project_root is None:
        project_root = os.path.dirname(test_file)
        use_unique_keys = False
    else:
        use_unique_keys = True

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

        for key in matched_keys:
            mapping.setdefault(key, []).append(test_qualname)

    if diagnostics is not None:
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

        from collections import Counter

        drop_freq: Counter[tuple[str, str]] = Counter(
            (ex.get("symbol", ""), ex.get("reason", "")) for ex in diagnostics._drop_examples
        )
        first_occurrence: dict[tuple[str, str], dict[str, str]] = {}
        for ex in diagnostics._drop_examples:
            drop_key: tuple[str, str] = (ex.get("symbol", ""), ex.get("reason", ""))
            if drop_key not in first_occurrence:
                first_occurrence[drop_key] = ex
        ranked_examples: list[dict[str, str]] = [
            first_occurrence[rk]
            for rk, _count in sorted(drop_freq.items(), key=lambda kv: (-kv[1], kv[0]))
            if rk in first_occurrence
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
    prefer_qualified: bool = False,
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
        symbol_name = _resolve_symbol_name_for_match(
            func_name,
            chosen,
            source_function_index,
            prefer_qualified=prefer_qualified,
        )
        relpath = canonical_relpath(chosen, project_root)
        matched_keys.add(canonical_function_key(relpath, symbol_name))
    else:
        matched_keys.add(func_name)

    if diagnostics is not None:
        sd.mapped += 1
        diagnostics._mapped_symbols.add(func_name)
    return True


def _resolve_symbol_name_for_match(
    func_name: str,
    chosen_path: str,
    source_function_index: dict[str, str | list[str]],
    prefer_qualified: bool,
) -> str:
    """Resolve the best symbol name to use for a matched source file path."""
    if not prefer_qualified or "." in func_name:
        return func_name

    suffix = f".{func_name}"
    qualified: list[str] = []
    for key, raw_paths in source_function_index.items():
        if not isinstance(key, str) or "." not in key:
            continue
        if not key.endswith(suffix):
            continue
        if chosen_path in _coerce_candidate_paths(raw_paths):
            qualified.append(key)

    if len(qualified) == 1:
        return qualified[0]
    return func_name


def _record_shadowed_drop(
    diagnostics: MappingDiagnostics | None,
    bare_name: str,
) -> None:
    """Record a shadowed-symbol drop in diagnostics."""
    if diagnostics is None:
        return
    diagnostics.counts.dropped_shadowed += 1
    if "call_graph" not in diagnostics.strategy_breakdown:
        from lintgate.linters.test_effectiveness.types import StrategyDiagnostics

        diagnostics.strategy_breakdown["call_graph"] = StrategyDiagnostics(strategy="call_graph")
    diagnostics.strategy_breakdown["call_graph"].dropped_shadowed += 1
    diagnostics._drop_examples.append(
        {"symbol": bare_name, "reason": "shadowed", "strategy": "call_graph"}
    )


def _resolve_module_hint(
    name: str,
    qualifier: str,
    import_collector: _ImportCollector,
) -> str | None:
    """Resolve a module hint from the import table for a name or its qualifier."""
    if name in import_collector.imported_names:
        return _module_hint_from_import(import_collector.imported_names[name])
    if qualifier and qualifier in import_collector.imported_names:
        return _module_hint_from_import(import_collector.imported_names[qualifier])
    return None


def _try_alias_import(
    bare_name: str,
    import_collector: _ImportCollector,
    source_function_index: dict[str, str | list[str]],
    project_root: str | None,
    use_unique_keys: bool,
    matched_keys: set[str],
    diagnostics: MappingDiagnostics | None,
) -> bool:
    """Try resolving an aliased import (e.g. import foo as f)."""
    if bare_name not in import_collector.imported_names:
        return False
    imported_qualified = import_collector.imported_names[bare_name]
    imported_symbol = _symbol_name_from_import(imported_qualified)
    if imported_symbol == bare_name:
        return False
    alias_hint = _module_hint_from_import(imported_qualified)
    return _try_add_match(
        imported_symbol,
        alias_hint,
        source_function_index,
        project_root,
        use_unique_keys,
        matched_keys,
        diagnostics,
        strategy="alias_import",
    )


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
        _record_shadowed_drop(diagnostics, bare_name)
        return False

    module_hint = _resolve_module_hint(call_name, qualifier, import_collector)
    if call_name in source_function_index or qualifier:
        if _try_add_match(
            call_name,
            module_hint,
            source_function_index,
            project_root,
            use_unique_keys,
            matched_keys,
            diagnostics,
            strategy="call_graph",
        ):
            return True
        if call_name != bare_name and call_name in source_function_index:
            return False

    bare_hint = _resolve_module_hint(bare_name, qualifier, import_collector)
    prefer_qualified = bool(qualifier and qualifier not in import_collector.imported_names)
    success = False
    if (
        bare_name in source_function_index
        or bare_name in import_collector.imported_names
        or bare_hint
    ) and _try_add_match(
        bare_name,
        bare_hint,
        source_function_index,
        project_root,
        use_unique_keys,
        matched_keys,
        diagnostics,
        strategy="call_graph",
        prefer_qualified=prefer_qualified,
    ):
        success = True

    if _try_alias_import(
        bare_name,
        import_collector,
        source_function_index,
        project_root,
        use_unique_keys,
        matched_keys,
        diagnostics,
    ):
        success = True

    return success


# ── Matching strategies ───────────────────────────────────────────


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
        source_cls = class_name[4:]
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
