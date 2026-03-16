"""Utility functions for batch test regeneration.

Prescription extractors, content mergers, skeleton sanitizers,
and factory generators used by BatchRegenerator.
"""

from __future__ import annotations

import ast
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .batch_regenerator import FunctionEnrichment

# ── Prescription extractors ──────────────────────────────────────────


def _prescriptions_from_witnesses(
    survivor_records: list[dict],
    func_key: str,
) -> list[dict]:
    """Extract prescriptions from witness-grounded survivor records."""
    try:
        from lintgate.specification.witness_generation import (
            generate_witness_prescription,
        )
    except ImportError:
        return []

    prescriptions: list[dict] = []
    for sr in survivor_records:
        rx = generate_witness_prescription(sr, func_key)
        prescriptions.append(
            {
                "category": rx.get("category", "UNKNOWN"),
                "assertion_shape": rx.get("assertion_shape", ""),
                "suggested_input": rx.get("suggested_input", ""),
                "confidence": rx.get("confidence", 0.0),
                "source": "witness",
            }
        )
    return prescriptions


def _prescriptions_from_categories(per_category: list[dict]) -> list[dict]:
    """Extract prescriptions from category-level survival data."""
    prescriptions: list[dict] = []
    for cat_data in per_category:
        if cat_data.get("survived", 0) > 0:
            prescriptions.append(
                {
                    "category": cat_data["category"],
                    "assertion_shape": _category_assertion_hint(cat_data["category"]),
                    "suggested_input": "",
                    "confidence": 0.5,
                    "source": "category_generic",
                }
            )
    return prescriptions


def _materialize_value_oracles(enrichments: list[FunctionEnrichment]) -> None:
    """Rewrite needs_oracle=True VALUE properties using corroborated golden captures.

    When a function has both a VALUE survivor (needs_oracle=True) and a
    CORROBORATED golden capture, the capture provides the exact expected
    value, letting us flip the property to needs_oracle=False with a
    concrete assertion.
    """
    try:
        from lintgate.testing.characterization import Provenance
    except ImportError:
        return

    for enr in enrichments:
        if not enr.golden_captures:
            continue

        # Only use CORROBORATED captures — PROVISIONAL may fossilize bugs
        corroborated = [
            cap
            for cap in enr.golden_captures
            if cap.provenance == Provenance.CORROBORATED and cap.deterministic
        ]
        if not corroborated:
            continue

        # Find VALUE properties that need oracles
        for prop in enr.executable_properties:
            if prop.category != "VALUE" or not prop.needs_oracle:
                continue

            # Pick the first corroborated capture with a non-empty output
            cap = next((c for c in corroborated if c.output), None)
            if cap is None:
                continue

            # Build concrete assertion from the capture
            mod, fname = (
                enr.function_key.rsplit("::", 1)
                if "::" in enr.function_key
                else ("", enr.function_key)
            )
            mod_import = mod.replace("/", ".").replace("\\", ".")
            if mod_import.endswith(".py"):
                mod_import = mod_import[:-3]

            args_str = ", ".join(repr(a) for a in cap.inputs)
            if cap.kwargs:
                kw_parts = [f"{k}={v!r}" for k, v in cap.kwargs.items()]
                args_str = ", ".join([args_str, *kw_parts]) if args_str else ", ".join(kw_parts)

            setup = f"from {mod_import} import {fname}" if mod_import else ""
            assertion = f"result = {fname}({args_str})\nassert repr(result) == {cap.output!r}"

            prop.setup_code = setup
            prop.assertion_code = assertion
            prop.needs_oracle = False
            prop.confidence = 0.9
            prop.preconditions = [f"golden capture ({cap.corroborating_lens})"]
            if "golden_capture" not in prop.source_lenses:
                prop.source_lenses.append("golden_capture")


_ASSERTION_HINTS = {
    "VALUE": "assert result == EXPECTED_VALUE",
    "SWAP": "assert func(a, b) != func(b, a)  # or == if commutative",
    "BOUNDARY": "assert func(boundary - 1) == X; assert func(boundary + 1) == Y",
    "STATE": "assert obj.attr == EXPECTED after calling func",
    "TYPE": "assert func(valid) != func(invalid_type)",
}


def _category_assertion_hint(category: str) -> str:
    return _ASSERTION_HINTS.get(category, "assert result == EXPECTED")


# ── Content merger ───────────────────────────────────────────────────


def _merge_enrichments(
    skeleton: str,
    enrichments: list[FunctionEnrichment],
    project_root: str | None = None,
    target_test_file: str | None = None,
) -> tuple[str, list[str]]:
    """Merge skeleton with enrichment data into final test content."""
    sections: list[str] = []
    manual_contract_candidates: list[str] = []

    # Strip non-executable placeholder tests from the generic skeleton so the
    # autonomous loop only writes runnable tests into tests/generated/.
    sections.append(_sanitize_skeleton(skeleton).rstrip())
    sections.append("")
    sections.append("")

    # File-level pass: detect repeated dataclass needs and emit shared factories
    factory_block, factory_imports = _generate_shared_factories(enrichments)
    if factory_imports:
        sections[0] = _insert_factory_imports(sections[0], factory_imports)
    if factory_block:
        sections.append(factory_block)

    # Enrichment sections per function
    for enr in enrichments:
        func_section = _build_function_section(enr)
        if func_section:
            if _section_is_valid(
                sections[0],
                factory_block,
                func_section,
                project_root,
                target_test_file,
            ):
                sections.append(func_section)
            else:
                manual_contract_candidates.append(enr.function_key)
        else:
            manual_contract_candidates.append(enr.function_key)

    content = "\n".join(sections) + "\n"

    # Post-generation validation: flag broken tests
    content = _validate_and_strip_broken(content, project_root, target_test_file)

    return content, manual_contract_candidates


def _insert_factory_imports(skeleton_text: str, factory_imports: list[str]) -> str:
    """Insert shared-factory imports after the module header and future imports."""
    if not factory_imports:
        return skeleton_text

    lines = skeleton_text.split("\n")
    insert_idx = 0

    try:
        tree = ast.parse(skeleton_text)
        if tree.body and isinstance(tree.body[0], ast.Expr):
            expr_val = tree.body[0].value
            if isinstance(expr_val, ast.Constant) and isinstance(expr_val.value, str):
                insert_idx = getattr(tree.body[0], "end_lineno", tree.body[0].lineno)
    except SyntaxError:
        insert_idx = 0

    while insert_idx < len(lines) and (
        not lines[insert_idx].strip() or lines[insert_idx].strip().startswith("#")
    ):
        insert_idx += 1

    while insert_idx < len(lines) and lines[insert_idx].strip().startswith("from __future__"):
        insert_idx += 1

    import_block = "\n".join(factory_imports)
    lines.insert(insert_idx, import_block)
    return "\n".join(lines)


def _section_is_valid(
    skeleton_text: str,
    factory_block: str,
    func_section: str,
    project_root: str | None,
    target_test_file: str | None,
) -> bool:
    """Validate one generated function section before it lands in the final file."""
    try:
        from lintgate.testing.typed_synthesis import validate_test_file
    except ImportError:
        return True

    parts = [skeleton_text]
    if factory_block:
        parts.append(factory_block)
    parts.append(func_section)
    module_content = "\n\n".join(part for part in parts if part)
    run_runtime = bool(project_root and os.path.isdir(project_root))
    valid, _ = validate_test_file(
        module_content,
        project_root=project_root,
        test_file_name=os.path.basename(target_test_file or "test_generated.py"),
        run_pytest=run_runtime,
    )
    return valid


def _generate_shared_factories(
    enrichments: list[FunctionEnrichment],
) -> tuple[str, list[str]]:
    """Detect repeated dataclass parameter types and emit shared factory functions.

    Scans all function AST nodes for parameter annotations. When a dataclass
    type appears in 3+ functions, emits a factory helper at file level.
    """
    try:
        from lintgate.testing.typed_synthesis import synthesize_factory
    except ImportError:
        return "", []

    # Count how often each type annotation appears across functions
    type_counts: dict[str, int] = {}
    for enr in enrichments:
        if enr.func_node is None:
            continue
        seen_in_func: set[str] = set()
        for arg in enr.func_node.args.args:
            if arg.annotation and arg.arg != "self":
                type_name = _annotation_base_name(arg.annotation)
                if type_name and type_name not in seen_in_func:
                    seen_in_func.add(type_name)
                    type_counts[type_name] = type_counts.get(type_name, 0) + 1

    # Generate factories for types appearing 3+ times
    factory_lines: list[str] = []
    all_imports: list[str] = []
    for type_name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        if count < 3:
            continue
        result = synthesize_factory(type_name)
        if result is not None:
            code, imports = result
            factory_lines.append("")
            factory_lines.append(code)
            all_imports.extend(imports)

    return "\n".join(factory_lines), all_imports


def _annotation_base_name(node: ast.expr) -> str:
    """Extract the base type name from an AST annotation node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        # For list[X], dict[K,V] — return the inner type if it's a single arg
        if isinstance(node.slice, ast.Name):
            return node.slice.id
        return ""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # X | None — use non-None side
        if isinstance(node.left, ast.Name) and node.left.id != "None":
            return node.left.id
        if isinstance(node.right, ast.Name) and node.right.id != "None":
            return node.right.id
    return ""


def _extract_broken_test_names(errors: list[str]) -> set[str]:
    """Extract test function names from validation error messages."""
    broken: set[str] = set()
    for err in errors:
        if " (line " in err:
            broken.add(err.split(" (line ")[0])
        elif ":" in err:
            name = err.split(":", 1)[0].strip()
            if name.startswith("test_"):
                broken.add(name)
    return broken


def _strip_broken_functions(content: str, broken_names: set[str]) -> str:
    """Remove named test functions from content and collapse blank lines."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content

    lines = content.splitlines()
    keep = [True] * len(lines)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in broken_names:
            start = max(node.lineno - 1, 0)
            end = min(getattr(node, "end_lineno", node.lineno), len(lines))
            for idx in range(start, end):
                keep[idx] = False

    cleaned = [line for idx, line in enumerate(lines) if keep[idx]]
    result: list[str] = []
    last_blank = False
    for line in cleaned:
        is_blank = not line.strip()
        if is_blank and last_blank:
            continue
        result.append(line)
        last_blank = is_blank
    return "\n".join(result)


def _validate_and_strip_broken(
    content: str,
    project_root: str | None = None,
    target_test_file: str | None = None,
) -> str:
    """Validate generated test content and strip tests that would crash."""
    try:
        from lintgate.testing.typed_synthesis import validate_test_file
    except ImportError:
        return content

    valid, errors = validate_test_file(
        content,
        project_root=project_root,
        test_file_name=os.path.basename(target_test_file or "test_generated.py"),
        run_pytest=bool(project_root and os.path.isdir(project_root)),
    )
    if valid:
        return content

    broken_names = _extract_broken_test_names(errors)
    if not broken_names:
        return content

    return _strip_broken_functions(content, broken_names)


def _walk_qualified_functions(
    tree: ast.AST,
    prefix: str = "",
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Walk functions in definition order, preserving class-qualified names."""
    results: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_body(body: list[ast.stmt], parent: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{parent}.{node.name}" if parent else node.name
                results.append((qualname, node))
            elif isinstance(node, ast.ClassDef):
                class_prefix = f"{parent}.{node.name}" if parent else node.name
                visit_body(node.body, class_prefix)

    if isinstance(tree, ast.Module):
        visit_body(tree.body, prefix)
    return results


def _sanitize_skeleton(skeleton: str) -> str:
    """Drop placeholder-only skeleton blocks while preserving imports/helpers."""
    if not skeleton.strip():
        return skeleton

    try:
        tree = ast.parse(skeleton)
    except SyntaxError:
        return skeleton

    lines = skeleton.splitlines()
    keep = [True] * len(lines)
    removed = False

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not _is_placeholder_block(node, lines):
            continue
        removed = True
        start = max(node.lineno - 1, 0)
        end = min(getattr(node, "end_lineno", node.lineno), len(lines))
        for idx in range(start, end):
            keep[idx] = False

    if not removed:
        return skeleton

    cleaned: list[str] = []
    last_blank = False
    for idx, line in enumerate(lines):
        if not keep[idx]:
            continue
        is_blank = not line.strip()
        if is_blank and last_blank:
            continue
        cleaned.append(line)
        last_blank = is_blank

    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


def _is_placeholder_block(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    lines: list[str],
) -> bool:
    """Detect generic skeleton blocks that are not safe to execute."""
    start = max(node.lineno - 1, 0)
    end = min(getattr(node, "end_lineno", node.lineno), len(lines))
    segment = "\n".join(lines[start:end])

    if "TODO" in segment or "EXPECTED" in segment:
        return True

    for child in ast.walk(node):
        if isinstance(child, ast.Pass):
            return True
        if isinstance(child, ast.Constant) and child.value is Ellipsis:
            return True

    return False


def _extract_promotable_assertions(
    inputs: list[dict],
    function_name: str,
) -> list[str]:
    """Extract call-site contexts that are direct assert statements with literals.

    Only promotes patterns like ``assert func(literal, ...) == literal``
    where all arguments and the expected value pass ``ast.literal_eval``.
    """
    promoted: list[str] = []
    seen: set[str] = set()
    for site in inputs:
        ctx = site.get("context", "").strip()
        if not ctx:
            continue
        # Must start with 'assert ' and reference the target function
        if not ctx.startswith("assert ") or function_name not in ctx:
            continue
        try:
            tree = ast.parse(ctx, mode="exec")
        except SyntaxError:
            continue
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert):
            continue
        test_node = tree.body[0].test
        # Must be a comparison: func(...) == value
        if not isinstance(test_node, ast.Compare):
            continue
        if len(test_node.ops) != 1 or not isinstance(
            test_node.ops[0], (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)
        ):
            continue
        # Left side must be a call to the target function
        left = test_node.left
        if not isinstance(left, ast.Call):
            continue
        call_name = ast.unparse(left.func) if hasattr(left, "func") else ""
        if call_name != function_name and not call_name.endswith(f".{function_name}"):
            continue
        # All args must be literal-evaluable
        try:
            for arg in left.args:
                ast.literal_eval(arg)
            for kw in left.keywords:
                ast.literal_eval(kw.value)
            for comp in test_node.comparators:
                ast.literal_eval(comp)
        except (ValueError, TypeError):
            continue
        # Deduplicate
        if ctx in seen:
            continue
        seen.add(ctx)
        promoted.append(f"    {ctx}  # promoted from call-site")
    return promoted


def _build_promoted_tests(enr: FunctionEnrichment, safe_name: str) -> tuple[list[str], list[str]]:
    """Build promoted call-site assertions and non-promoted input comments."""
    lines: list[str] = []
    promoted = _extract_promotable_assertions(enr.inputs, enr.function_name) if enr.inputs else []
    if promoted:
        lines.append(f"def test_{safe_name}_callsite():")
        lines.append('    """VALUE: call-site golden assertions (promoted)."""')
        lines.extend(promoted)
        lines.append("")

    if enr.inputs:
        promoted_set = {
            line.strip().removesuffix("# promoted from call-site").strip() for line in promoted
        }
        non_promoted = [
            site for site in enr.inputs[:3] if site.get("context", "").strip() not in promoted_set
        ]
        if non_promoted:
            lines.append(f"# Input examples for {enr.function_name}:")
            for site in non_promoted:
                ctx = site.get("context", "").strip()
                if ctx:
                    lines.append(f"#   {ctx[:80]}")
            lines.append("")

    return lines, promoted


def _build_executable_prop_tests(enr: FunctionEnrichment, safe_name: str) -> list[str]:
    """Build oracle-light executable property tests."""
    lines: list[str] = []
    for prop in enr.executable_properties:
        if prop.needs_oracle:
            continue
        mid = prop.mutant_id or prop.category.lower()
        test_name = f"test_{safe_name}_{mid.lower()}"
        lines.append(f"def {test_name}():")
        lines.append(f'    """{prop.category}: mutation property"""')
        for pc in prop.preconditions:
            lines.append(f"    # Precondition: {pc}")
        if prop.setup_code:
            for sl in prop.setup_code.split("\n"):
                lines.append(f"    {sl}")
        for al in prop.assertion_code.split("\n"):
            lines.append(f"    {al}")
        lines.append("")
    return lines


def _build_function_section(enr: FunctionEnrichment) -> str:
    """Build enrichment section for one function."""
    lines: list[str] = []
    safe_name = enr.function_name.replace(".", "_")
    has_executable = False

    promoted_lines, promoted = _build_promoted_tests(enr, safe_name)
    if promoted_lines:
        lines.extend(promoted_lines)
    if promoted:
        has_executable = True

    exec_lines = _build_executable_prop_tests(enr, safe_name)
    if exec_lines:
        has_executable = True
        lines.extend(exec_lines)

    if enr.characterization:
        has_executable = True
        if not enr.prescriptions and not enr.executable_properties:
            lines.append(f"# Characterization test for {enr.function_name}")
        else:
            lines.append(f"# PROVISIONAL: characterization-backed for {enr.function_name}")
        lines.append(enr.characterization)
        lines.append("")

    return "\n".join(lines) if has_executable else ""
