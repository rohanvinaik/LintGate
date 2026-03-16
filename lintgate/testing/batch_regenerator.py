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

from .batch_regen_utils import (  # noqa: F401
    _build_function_section,
    _category_assertion_hint,
    _extract_promotable_assertions,
    _generate_shared_factories,
    _insert_factory_imports,
    _materialize_value_oracles,
    _merge_enrichments,
    _prescriptions_from_categories,
    _prescriptions_from_witnesses,
    _section_is_valid,
    _validate_and_strip_broken,
    _walk_qualified_functions,
)


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
