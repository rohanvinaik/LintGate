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


@dataclass
class GeneratedFile:
    """Result of generating tests for one source file."""

    source_file: str
    target_test_file: str
    content: str
    functions_covered: int
    enrichment_sources: list[str] = field(default_factory=list)


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

        for func in functions:
            func_key = func.get("function_key", "")
            func_name = func_key.rsplit("::", 1)[-1] if "::" in func_key else func_key
            enrichment = FunctionEnrichment(
                function_key=func_key,
                function_name=func_name,
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
                rel_file, func_key, func_name, enrichment.inputs,
            )
            if exec_props:
                enrichment.executable_properties = exec_props
                if "oracle_light" not in sources:
                    sources.append("oracle_light")

            # Lens 5: Parametric golden capture with provenance
            if enrichment.inputs:
                golden = self._golden_capture(rel_file, func_key, func_name,
                                              enrichment.inputs)
                if golden:
                    enrichment.characterization = golden
                    if "golden_capture" not in sources:
                        sources.append("golden_capture")

            # Lens 6: Raw characterization fallback (only if nothing else)
            if (not inputs and not prescriptions and not exec_props
                    and not enrichment.characterization):
                char_code = self._characterize(rel_file, func_name)
                if char_code:
                    enrichment.characterization = char_code
                    if "characterize" not in sources:
                        sources.append("characterize")

            enrichments.append(enrichment)

        content = _merge_enrichments(skeleton, enrichments)
        return GeneratedFile(
            source_file=rel_file,
            target_test_file=target,
            content=content,
            functions_covered=len(enrichments),
            enrichment_sources=sources,
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
    ) -> str:
        """Lens 5: Parametric golden capture with provenance tracking."""
        module_path = func_key.rsplit("::", 1)[0] if "::" in func_key else ""
        if not module_path:
            return ""
        try:
            from lintgate.testing.characterization import (
                capture_golden,
                corroborate_captures,
                generate_golden_test,
            )

            captures = capture_golden(module_path, func_name, call_site_inputs)
            if not captures:
                return ""

            mutation_state = self._load_mutation_cache().get(func_key)
            is_pure = self._check_purity(rel_file, func_name)
            captures = corroborate_captures(captures, mutation_state, is_pure)
            return generate_golden_test(func_key, captures)
        except Exception:
            return ""

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
        self, rel_file: str, func_name: str,
    ) -> ast.FunctionDef | None:
        """Resolve a function's AST node from the source file."""
        try:
            abs_path = os.path.join(self.project_root, rel_file)
            with open(abs_path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == func_name:
                        return node  # type: ignore[return-value]
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
) -> str:
    """Merge skeleton with enrichment data into final test content."""
    sections: list[str] = []

    # Header: enriched skeleton
    sections.append(skeleton.rstrip())
    sections.append("")
    sections.append("")

    # Enrichment sections per function
    for enr in enrichments:
        func_section = _build_function_section(enr)
        if func_section:
            sections.append(func_section)

    return "\n".join(sections) + "\n"


def _build_function_section(enr: FunctionEnrichment) -> str:
    """Build enrichment section for one function."""
    lines: list[str] = []
    safe_name = enr.function_name.replace(".", "_")

    # Input examples as comments
    if enr.inputs:
        lines.append(f"# Input examples for {enr.function_name}:")
        for site in enr.inputs[:3]:
            ctx = site.get("context", "").strip()
            if ctx:
                lines.append(f"#   {ctx[:80]}")
        lines.append("")

    # Oracle-light executable properties (preferred over text prescriptions)
    if enr.executable_properties:
        for prop in enr.executable_properties:
            mid = prop.mutant_id or prop.category.lower()
            test_name = f"test_{safe_name}_{mid.lower()}"
            tag = "" if not prop.needs_oracle else " [needs oracle]"
            lines.append(f"def {test_name}():")
            lines.append(f'    """{prop.category}: mutation property{tag}"""')
            for pc in prop.preconditions:
                lines.append(f"    # Precondition: {pc}")
            if prop.setup_code:
                for sl in prop.setup_code.split("\n"):
                    lines.append(f"    {sl}")
            for al in prop.assertion_code.split("\n"):
                lines.append(f"    {al}")
            lines.append("")
    else:
        # Fallback: text prescription stubs
        for rx in enr.prescriptions:
            cat = rx["category"]
            test_name = f"test_{safe_name}_{cat.lower()}_mutation"
            shape = rx.get("assertion_shape", "")
            suggested = rx.get("suggested_input", "")
            lines.append(f"def {test_name}():")
            lines.append(f'    """{cat}: {shape}"""')
            if suggested:
                lines.append(f"    # suggested input: {suggested}")
            lines.append("    # TODO: fill in expected values")
            lines.append("    pass")
            lines.append("")

    # Characterization fallback
    if enr.characterization and not enr.prescriptions and not enr.executable_properties:
        lines.append(f"# Characterization test for {enr.function_name}")
        lines.append(enr.characterization)
        lines.append("")

    return "\n".join(lines)
