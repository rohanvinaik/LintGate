"""Phase 2 tests: MUTATION_UNKNOWN + confidence reduction.

Tests cover:
1. classify_properties() with mutation_system_active=True and no mutation data -> confidence 0.4
2. classify_properties() with mutation_system_active=False -> default confidence preserved
3. classify_properties() with mutation data present -> existing gating logic unchanged
4. _check_mutch008() on MutationChannel — various scenarios
"""

from __future__ import annotations

import ast
from unittest.mock import MagicMock

import pytest

from lintgate.channels.mutation_channel import MutationChannel
from lintgate.linters.performance_checks.properties import classify_properties
from lintgate.linters.performance_checks.purity import PurityResult
from lintgate.mutation.state import CoverageDepth, FunctionMutationState

# ── Helpers ──────────────────────────────────────────────────────────


def _make_pure_func_node() -> ast.FunctionDef:
    """Create a simple pure function AST node for testing."""
    code = "def add(a: int, b: int) -> int: return a + b"
    tree = ast.parse(code)
    return tree.body[0]


def _make_purity(is_pure: bool = True, confidence: float = 0.8) -> PurityResult:
    return PurityResult(
        function_name="add",
        qualified_name="test.add",
        line=1,
        is_pure=is_pure,
        confidence=confidence,
        side_effects=(),
        parameter_count=2,
        return_annotation="int",
    )


def _make_manifest_with_pure_funcs(
    func_keys: list[str],
    source_files: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock PropertyManifest with specified pure functions."""
    manifest = MagicMock()
    functions = {}
    for key in func_keys:
        func_props = MagicMock()
        func_props.purity = MagicMock()
        func_props.purity.is_pure = True
        # Default source_file from key
        default_file = key.split("::")[0] if "::" in key else "unknown.py"
        func_props.source_file = (
            source_files.get(key, default_file) if source_files else default_file
        )
        func_props.optimization_hints = ("cacheable",)
        functions[key] = func_props
    manifest.functions = functions
    return manifest


# ── classify_properties: MUTATION_UNKNOWN ────────────────────────────


class TestClassifyPropertiesMutationUnknown:
    """Phase 2: MUTATION_UNKNOWN confidence reduction."""

    def test_mutation_system_active_no_data_reduces_confidence(self):
        """When mutation system is active but no data exists, confidence = 0.4."""
        node = _make_pure_func_node()
        purity = _make_purity(is_pure=True, confidence=0.8)

        result = classify_properties(
            node,
            purity,
            mutation_state=None,
            mutation_system_active=True,
        )

        # The PURE property should have confidence 0.4
        pure_prop = next(
            (p for p in result.properties if p.kind.value == "pure"), None
        )
        assert pure_prop is not None
        assert pure_prop.confidence == pytest.approx(0.4)
        assert "MUTATION_UNKNOWN" in pure_prop.evidence

    def test_mutation_system_inactive_preserves_default_confidence(self):
        """When mutation system is not active, original confidence preserved."""
        node = _make_pure_func_node()
        purity = _make_purity(is_pure=True, confidence=0.8)

        result = classify_properties(
            node,
            purity,
            mutation_state=None,
            mutation_system_active=False,
        )

        pure_prop = next(
            (p for p in result.properties if p.kind.value == "pure"), None
        )
        assert pure_prop is not None
        assert pure_prop.confidence == pytest.approx(0.8)
        assert "MUTATION_UNKNOWN" not in pure_prop.evidence

    def test_mutation_data_present_uses_existing_gating(self):
        """When mutation data exists, existing gating logic takes precedence."""
        node = _make_pure_func_node()
        purity = _make_purity(is_pure=True, confidence=0.8)

        mutation_state = FunctionMutationState(
            function_name="add",
            file_path="test.py",
            code_hash="h",
            test_hash="t",
            total=10,
            killed=9,
            survived=1,  # 10% survival
            depth=CoverageDepth.PROFILED,
        )

        result = classify_properties(
            node,
            purity,
            mutation_state=mutation_state,
            mutation_system_active=True,
        )

        pure_prop = next(
            (p for p in result.properties if p.kind.value == "pure"), None
        )
        assert pure_prop is not None
        # Low survival + profiled = MUTATION VERIFIED -> confidence >= 0.9
        assert pure_prop.confidence >= 0.9
        assert "MUTATION VERIFIED" in pure_prop.evidence

    def test_impure_function_not_affected_by_mutation_unknown(self):
        """MUTATION_UNKNOWN only applies when purity.is_pure is True."""
        code = "def side_effect(): import os; os.remove('x')"
        tree = ast.parse(code)
        node = tree.body[0]
        purity = _make_purity(is_pure=False, confidence=0.8)

        result = classify_properties(
            node,
            purity,
            mutation_state=None,
            mutation_system_active=True,
        )

        # Should have no properties (impure functions don't get classified)
        # or if they do, MUTATION_UNKNOWN should not appear
        for prop in result.properties:
            assert "MUTATION_UNKNOWN" not in (prop.evidence or "")

    def test_mutation_unknown_preserves_optimization_hints(self):
        """MUTATION_UNKNOWN reduces confidence but keeps optimization hints."""
        node = _make_pure_func_node()
        purity = _make_purity(is_pure=True, confidence=0.8)

        result = classify_properties(
            node,
            purity,
            mutation_state=None,
            mutation_system_active=True,
        )

        # Should still have "cacheable" hint
        assert "cacheable" in result.optimization_hints


# ── _check_mutch008: Unit tests ──────────────────────────────────────


class TestCheckMUTCH008:
    """Phase 2: MUTCH008 finding for pure functions with no mutation data."""

    def setup_method(self):
        self.channel = MutationChannel()

    def test_mutch008_emitted_for_unknown_pure_functions(self):
        """Pure functions not in mutation state emit MUTCH008."""
        manifest = _make_manifest_with_pure_funcs(
            ["core.py::compute", "core.py::transform"]
        )
        all_states: dict[str, FunctionMutationState] = {}  # No mutation data

        findings = self.channel._check_mutch008(all_states, manifest, ["core.py"])

        assert len(findings) == 1
        assert findings[0].kind == "MUTCH008"
        assert findings[0].severity == "informational"
        assert findings[0].file == "core.py"
        assert findings[0].evidence["count"] == 2
        assert "compute" in findings[0].evidence["unknown_functions"]
        assert "transform" in findings[0].evidence["unknown_functions"]
        assert "next_action" in findings[0].evidence
        assert findings[0].evidence["next_action"]["tool"] == "mutation_run_sampling"

    def test_mutch008_not_emitted_when_manifest_is_none(self):
        """No findings when manifest build failed."""
        findings = self.channel._check_mutch008({}, None, ["core.py"])
        assert findings == []

    def test_mutch008_not_emitted_when_mutation_data_exists(self):
        """Functions with mutation data don't trigger MUTCH008."""
        manifest = _make_manifest_with_pure_funcs(["core.py::compute"])

        mock_state = FunctionMutationState(
            function_name="compute",
            file_path="core.py",
            code_hash="h",
            test_hash="t",
            total=10,
            killed=8,
            survived=2,
            depth=CoverageDepth.SAMPLED,
        )
        all_states = {"core.py::compute": mock_state}

        findings = self.channel._check_mutch008(all_states, manifest, ["core.py"])
        assert len(findings) == 0

    def test_mutch008_filters_by_relevant_files(self):
        """Only functions in relevant_files are checked."""
        manifest = _make_manifest_with_pure_funcs(
            ["core.py::compute", "utils.py::helper"],
            source_files={
                "core.py::compute": "core.py",
                "utils.py::helper": "utils.py",
            },
        )
        all_states: dict[str, FunctionMutationState] = {}

        # Only core.py is relevant
        findings = self.channel._check_mutch008(all_states, manifest, ["core.py"])

        assert len(findings) == 1
        assert findings[0].file == "core.py"

    def test_mutch008_skips_impure_functions(self):
        """Impure functions don't trigger MUTCH008."""
        manifest = MagicMock()
        func_props = MagicMock()
        func_props.purity.is_pure = False
        func_props.source_file = "core.py"
        manifest.functions = {"core.py::do_io": func_props}

        findings = self.channel._check_mutch008({}, manifest, ["core.py"])
        assert len(findings) == 0

    def test_mutch008_groups_by_file(self):
        """Multiple files produce multiple findings."""
        manifest = _make_manifest_with_pure_funcs(
            ["core.py::compute", "utils.py::helper"],
            source_files={
                "core.py::compute": "core.py",
                "utils.py::helper": "utils.py",
            },
        )
        all_states: dict[str, FunctionMutationState] = {}

        # Both files relevant
        findings = self.channel._check_mutch008(
            all_states, manifest, ["core.py", "utils.py"]
        )

        assert len(findings) == 2
        files = {f.file for f in findings}
        assert files == {"core.py", "utils.py"}

    def test_mutch008_truncates_long_function_lists(self):
        """More than 5 unknown functions in a file -> truncated display."""
        func_keys = [f"big.py::func_{i}" for i in range(8)]
        manifest = _make_manifest_with_pure_funcs(func_keys)
        all_states: dict[str, FunctionMutationState] = {}

        findings = self.channel._check_mutch008(all_states, manifest, ["big.py"])

        assert len(findings) == 1
        assert "+3 more" in findings[0].message
        assert findings[0].evidence["count"] == 8

    def test_mutch008_no_findings_when_all_have_data(self):
        """No MUTCH008 when all pure functions have mutation data."""
        manifest = _make_manifest_with_pure_funcs(["core.py::compute"])

        mock_state = FunctionMutationState(
            function_name="compute",
            file_path="core.py",
            code_hash="h",
            test_hash="t",
            total=5,
            killed=5,
            survived=0,
            depth=CoverageDepth.PROFILED,
        )
        all_states = {"core.py::compute": mock_state}

        findings = self.channel._check_mutch008(all_states, manifest, ["core.py"])
        assert len(findings) == 0

    def test_mutch008_empty_relevant_files_checks_all(self):
        """When relevant_files is empty, all manifest functions are checked."""
        manifest = _make_manifest_with_pure_funcs(
            ["core.py::compute", "utils.py::helper"],
            source_files={
                "core.py::compute": "core.py",
                "utils.py::helper": "utils.py",
            },
        )
        all_states: dict[str, FunctionMutationState] = {}

        findings = self.channel._check_mutch008(all_states, manifest, [])

        assert len(findings) == 2

    def test_mutch008_confidence_is_085(self):
        """MUTCH008 findings have confidence 0.85."""
        manifest = _make_manifest_with_pure_funcs(["core.py::compute"])
        findings = self.channel._check_mutch008({}, manifest, ["core.py"])

        assert len(findings) == 1
        assert findings[0].confidence == pytest.approx(0.85)

    def test_mutch008_metrics_integration(self):
        """mutation_unknown_count in channel metrics reflects MUTCH008 count."""
        # This is tested indirectly through the execute() method
        # but we verify the return count is correct for metrics
        manifest = _make_manifest_with_pure_funcs(
            ["core.py::a", "core.py::b", "core.py::c"]
        )
        findings = self.channel._check_mutch008({}, manifest, ["core.py"])

        # One finding for the file, with 3 unknown functions
        assert len(findings) == 1
        assert findings[0].evidence["count"] == 3
