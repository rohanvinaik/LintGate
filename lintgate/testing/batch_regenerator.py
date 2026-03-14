"""Batch test regenerator composing existing analysis lenses.

Orchestrates skeleton generation, input inference, mutation prescriptions,
and characterization fallback to produce enriched test files for
auto_generate_unit targets.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintgate.testing.characterization import GoldenCapture
    from lintgate.testing.oracle_light import ExecutableProperty


@dataclass
class FunctionEnrichment:
    """Collected enrichment data for one function."""

    function_key: str
    function_name: str
    inputs: list[dict] = field(default_factory=list)
    prescriptions: list[dict] = field(default_factory=list)
    executable_properties: list[ExecutableProperty] = field(default_factory=list)
    characterization: str = ""
    golden_captures: list[GoldenCapture] = field(default_factory=list)
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None


@dataclass
class GeneratedFile:
    """Result of generating tests for one source file."""

    source_file: str
    target_test_file: str
    content: str
    functions_covered: int
    enrichment_sources: list[str] = field(default_factory=list)
    manual_contract_candidates: list[str] = field(default_factory=list)


class BatchRegenerator:
    """Compose existing lenses to generate enriched test files.

    Lenses used:
    1. skeleton_generator — file-level test structure
    2. test_infer_inputs — call-site input examples
    3. mutation_prescribe — survivor category assertions
    4. test_characterize — golden-value fallback
    """

    def __init__(self, project_root: str):
        self.project_root = project_root
        self._mutation_cache: dict[str, dict] | None = None

    def generate_for_file(
        self,
        source_file: str,
        functions: list[dict],
    ) -> GeneratedFile | None:
        """Generate enriched test content for a source file."""
        rel_file = os.path.relpath(source_file, self.project_root)
        target = functions[0].get("target_test_file", "") if functions else ""
        if not target:
            return None

        skeleton = self._generate_skeleton(source_file)
        if not skeleton:
            return None

        enrichments = []
        sources: list[str] = ["skeleton"]
        manual_contract_candidates: list[str] = []

        for func in functions:
            func_key = func.get("function_key", "")
            func_name = func_key.rsplit("::", 1)[-1] if "::" in func_key else func_key
            enrichment = FunctionEnrichment(
                function_key=func_key,
                function_name=func_name,
                func_node=self._resolve_func_node(rel_file, func_name),
            )

            # Lens 2: Input inference from call sites
            inputs = self._infer_inputs(rel_file, func_name)
            if inputs:
                enrichment.inputs = inputs
                if "inputs" not in sources:
                    sources.append("inputs")

            # Lens 3: Mutation prescriptions for surviving categories
            prescriptions = self._get_prescriptions(func_key)
            if prescriptions:
                enrichment.prescriptions = prescriptions
                if "mutation" not in sources:
                    sources.append("mutation")

            # Lens 4: Oracle-light executable properties from survivors
            exec_props = self._get_executable_properties(
                rel_file,
                func_key,
                func_name,
                enrichment.inputs,
            )
            if exec_props:
                enrichment.executable_properties = exec_props
                if "oracle_light" not in sources:
                    sources.append("oracle_light")

            # Lens 5: Parametric golden capture with provenance
            is_pure = self._check_purity(rel_file, func_name)
            capture_inputs = enrichment.inputs if enrichment.inputs else []
            if capture_inputs or is_pure:
                golden_code, golden_caps = self._golden_capture(
                    rel_file,
                    func_key,
                    func_name,
                    capture_inputs,
                )
                if golden_code:
                    enrichment.characterization = golden_code
                    if "golden_capture" not in sources:
                        sources.append("golden_capture")
                if golden_caps:
                    enrichment.golden_captures = golden_caps

            # Lens 6: Raw characterization fallback
            # Runs when: (a) nothing else exists, or (b) pure function with
            # no golden capture yet (supplements prescriptions/exec_props)
            if not enrichment.characterization and (
                (not inputs and not prescriptions and not exec_props) or is_pure
            ):
                char_code = self._characterize(rel_file, func_name)
                if char_code:
                    enrichment.characterization = char_code
                    if "characterize" not in sources:
                        sources.append("characterize")

            enrichments.append(enrichment)

        # Lens 7: Round-trip pair detection for serialize/deserialize pairs
        round_trip_props = self._detect_round_trips(source_file)
        if round_trip_props:
            for prop in round_trip_props:
                # Attach to the serializer function's enrichment if it exists.
                # Enrichment function_name is "ClassName.to_dict", not bare "to_dict".
                attached = False
                for enr in enrichments:
                    if enr.function_name.endswith(".to_dict") or enr.function_name == "to_dict":
                        enr.executable_properties.append(prop)
                        attached = True
                        break
                if not attached:
                    # Extract class name from function_key for unique naming
                    cls_name = ""
                    if prop.function_key and "::" in prop.function_key:
                        func_part = prop.function_key.rsplit("::", 1)[1]
                        if "." in func_part:
                            cls_name = func_part.split(".")[0]
                    standalone_name = f"round_trip_{cls_name}" if cls_name else "round_trip"
                    enr = FunctionEnrichment(
                        function_key=prop.function_key,
                        function_name=standalone_name,
                        executable_properties=[prop],
                    )
                    enrichments.append(enr)
            if "round_trip" not in sources:
                sources.append("round_trip")

        # Lens 8: Materialize VALUE oracles from corroborated golden captures
        _materialize_value_oracles(enrichments)

        content, manual_contract_candidates = _merge_enrichments(
            skeleton,
            enrichments,
            self.project_root,
            target,
        )
        return GeneratedFile(
            source_file=rel_file,
            target_test_file=target,
            content=content,
            functions_covered=len(enrichments),
            enrichment_sources=sources,
            manual_contract_candidates=manual_contract_candidates,
        )

    def _generate_skeleton(self, source_file: str) -> str:
        """Lens 1: File-level test skeleton."""
        try:
            from lintgate.controlplane.skeleton_generator import (
                generate_test_skeleton,
            )

            return generate_test_skeleton(
                source_file,
                archetypes=None,
                project_root=self.project_root,
            )
        except Exception:
            return ""

    def _infer_inputs(self, rel_file: str, function: str) -> list[dict]:
        """Lens 2: Call-site input examples."""
        try:
            from mcp_tools.cold_start_tools import _impl_test_infer_inputs

            raw = _impl_test_infer_inputs(
                self.project_root,
                rel_file,
                function,
                5,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            if "error" in data:
                return []
            return list(data.get("call_sites", []))
        except Exception:
            return []

    def _get_prescriptions(self, func_key: str) -> list[dict]:
        """Lens 3: Mutation prescriptions from cached state."""
        cache = self._load_mutation_cache()
        state = cache.get(func_key)
        if not state:
            return []

        prescriptions: list[dict] = []
        survivor_records = state.get("survivor_records", [])

        if survivor_records:
            prescriptions = _prescriptions_from_witnesses(
                survivor_records,
                func_key,
            )
        else:
            prescriptions = _prescriptions_from_categories(
                state.get("per_category", []),
            )
        return prescriptions

    def _characterize(self, rel_file: str, function: str) -> str:
        """Lens 4: Characterization test as fallback."""
        try:
            from mcp_tools.cold_start_tools import _impl_test_characterize

            raw = _impl_test_characterize(
                self.project_root,
                rel_file,
                function,
                write=False,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            if "error" in data:
                return ""
            return str(data.get("test_code", ""))
        except Exception:
            return ""

    def _golden_capture(
        self,
        rel_file: str,
        func_key: str,
        func_name: str,
        call_site_inputs: list[dict],
    ) -> tuple[str, list]:
        """Lens 5: Parametric golden capture with provenance tracking.

        Returns (rendered_code, structured_captures) so downstream phases
        can use the structured GoldenCapture objects for oracle materialization.
        """
        module_path = func_key.rsplit("::", 1)[0] if "::" in func_key else ""
        if not module_path:
            return "", []
        try:
            from lintgate.testing.characterization import (
                capture_golden,
                corroborate_captures,
                generate_golden_test,
            )

            captures = capture_golden(module_path, func_name, call_site_inputs)
            if not captures:
                return "", []

            mutation_state = self._load_mutation_cache().get(func_key)
            is_pure = self._check_purity(rel_file, func_name)
            captures = corroborate_captures(captures, mutation_state, is_pure)
            rendered = generate_golden_test(func_key, captures)
            return rendered, captures
        except Exception:
            return "", []

    def _check_purity(self, rel_file: str, func_name: str) -> bool:
        """Check if a function is pure via AST analysis."""
        try:
            from mcp_tools._mutation_impl import detect_purity

            abs_path = os.path.join(self.project_root, rel_file)
            return detect_purity(abs_path, func_name)
        except Exception:
            return False

    def _get_executable_properties(
        self,
        rel_file: str,
        func_key: str,
        func_name: str,
        call_site_inputs: list[dict],
    ) -> list[ExecutableProperty]:
        """Lens 4: Oracle-light executable properties from survivor records."""
        cache = self._load_mutation_cache()
        state = cache.get(func_key)
        if not state:
            return []

        survivors = state.get("survivor_records", [])
        if not survivors:
            return []

        func_node = self._resolve_func_node(rel_file, func_name)

        from lintgate.testing.oracle_light import generate_executable_property

        return [
            generate_executable_property(sr, func_key, func_node, call_site_inputs)
            for sr in survivors
        ]

    def _resolve_func_node(
        self,
        rel_file: str,
        func_name: str,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """Resolve a function's AST node from the source file."""
        try:
            abs_path = os.path.join(self.project_root, rel_file)
            with open(abs_path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            requested = func_name
            bare = func_name.split(".")[-1]
            for qualname, node in _walk_qualified_functions(tree):
                if requested == qualname or node.name == requested:
                    return node
                if "." not in requested and (node.name == bare or qualname.endswith(f".{bare}")):
                    return node
        except Exception:
            pass
        return None

    def _load_mutation_cache(self) -> dict[str, dict]:
        """Load mutation cache lazily."""
        if self._mutation_cache is not None:
            return self._mutation_cache
        self._mutation_cache = {}
        try:
            from mcp_tools._mutation_impl import (
                get_cache_dir,
                iter_cached_states,
            )

            cache_dir = get_cache_dir(self.project_root)
            for state in iter_cached_states(cache_dir):
                key = state.get("function_key", "")
                if key:
                    self._mutation_cache[key] = state
        except Exception:
            pass
        return self._mutation_cache

    def _detect_round_trips(self, source_file: str) -> list:
        """Lens 7: Detect serialize/deserialize pairs and generate round-trip tests."""
        try:
            from lintgate.testing.oracle_light import (
                detect_round_trip_pairs,
                generate_round_trip_test,
            )
        except ImportError:
            return []

        pairs = detect_round_trip_pairs(source_file)
        if not pairs:
            return []

        rel_file = os.path.relpath(source_file, self.project_root)
        props = []
        for cls_name, ser_method, deser_func in pairs:
            prop = generate_round_trip_test(cls_name, ser_method, deser_func, rel_file)
            props.append(prop)
        return props


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

    # Extract names of broken test functions
    broken_names: set[str] = set()
    for err in errors:
        # Error format: "test_name (line N): description"
        if " (line " in err:
            name = err.split(" (line ")[0]
            broken_names.add(name)
        elif ":" in err:
            name = err.split(":", 1)[0].strip()
            if name.startswith("test_"):
                broken_names.add(name)

    if not broken_names:
        return content

    # Strip broken test functions from the AST
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
    # Remove consecutive blank lines
    result: list[str] = []
    last_blank = False
    for line in cleaned:
        is_blank = not line.strip()
        if is_blank and last_blank:
            continue
        result.append(line)
        last_blank = is_blank

    return "\n".join(result)


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


def _build_function_section(enr: FunctionEnrichment) -> str:
    """Build enrichment section for one function."""
    lines: list[str] = []
    safe_name = enr.function_name.replace(".", "_")
    has_executable = False

    # Fix 1: Promote assert-shaped call-site inputs into an executable test
    promoted = _extract_promotable_assertions(enr.inputs, enr.function_name) if enr.inputs else []
    if promoted:
        has_executable = True
        lines.append(f"def test_{safe_name}_callsite():")
        lines.append('    """VALUE: call-site golden assertions (promoted)."""')
        lines.extend(promoted)
        lines.append("")

    # Non-promoted input examples as comments
    if enr.inputs:
        non_promoted = [
            site
            for site in enr.inputs[:3]
            if site.get("context", "").strip()
            not in {
                line.strip().removesuffix("# promoted from call-site").strip() for line in promoted
            }
        ]
        if non_promoted:
            lines.append(f"# Input examples for {enr.function_name}:")
            for site in non_promoted:
                ctx = site.get("context", "").strip()
                if ctx:
                    lines.append(f"#   {ctx[:80]}")
            lines.append("")

    # Oracle-light executable properties (preferred over text prescriptions)
    executable_props = [prop for prop in enr.executable_properties if not prop.needs_oracle]
    if executable_props:
        has_executable = True
        for prop in executable_props:
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
    else:
        # Auto lane policy: never emit placeholder-only TODO tests.
        # If no executable witness exists, the caller must route this
        # function to manual-contract review instead of writing dead stubs.
        pass

    # Fix 2: Characterization alongside prescriptions/exec_props, tagged provisional
    if enr.characterization:
        has_executable = True
        if not enr.prescriptions and not enr.executable_properties:
            # Pure fallback — no other evidence
            lines.append(f"# Characterization test for {enr.function_name}")
            lines.append(enr.characterization)
            lines.append("")
        else:
            # Supplementary — tag as provisional
            lines.append(f"# PROVISIONAL: characterization-backed for {enr.function_name}")
            lines.append(enr.characterization)
            lines.append("")

    if not has_executable:
        return ""

    return "\n".join(lines)
