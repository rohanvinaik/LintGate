"""Tests for #210: Prescription-to-test-generation pipeline.

Covers:
- Test template generators for each survivor category
- Prescription engine emitting suggested_test_template
- Refactor loop auto-verify suggestions
"""

from __future__ import annotations

from lintgate.mutation.prescriptions import PrescriptionEngine
from lintgate.mutation.state import CoverageDepth, FunctionMutationState
from lintgate.mutation.test_generators import (
    CATEGORY_GENERATORS,
    generate_arithmetic_template,
    generate_boundary_template,
    generate_conditional_template,
    generate_string_template,
    generate_template_for_category,
)

# ── Test template generators ────────────────────────────────────────────


def _make_state(
    func: str = "compute",
    file: str = "src/math.py",
    **kwargs,
) -> FunctionMutationState:
    return FunctionMutationState(
        function_name=func,
        file_path=file,
        code_hash="h",
        test_hash="t",
        **kwargs,
    )


class TestArithmeticTemplate:
    def test_generates_valid_python(self):
        state = _make_state()
        template = generate_arithmetic_template(state)
        assert "def test_known_output" in template
        assert "def test_zero_input" in template
        assert "def test_negative_input" in template

    def test_uses_function_name(self):
        state = _make_state(func="add_values")
        template = generate_arithmetic_template(state)
        assert "add_values" in template

    def test_uses_module_path(self):
        state = _make_state(file="src/math.py")
        template = generate_arithmetic_template(state)
        assert "from src.math import" in template

    def test_compiles(self):
        state = _make_state()
        template = generate_arithmetic_template(state)
        compile(template, "<test>", "exec")  # Should not raise


class TestConditionalTemplate:
    def test_generates_branch_tests(self):
        state = _make_state()
        template = generate_conditional_template(state)
        assert "def test_true_branch" in template
        assert "def test_false_branch" in template
        assert "def test_boundary_condition" in template

    def test_compiles(self):
        state = _make_state()
        template = generate_conditional_template(state)
        compile(template, "<test>", "exec")


class TestBoundaryTemplate:
    def test_generates_boundary_tests(self):
        state = _make_state()
        template = generate_boundary_template(state)
        assert "test_boundary_values" in template
        assert "test_empty_input" in template
        assert "test_single_element" in template
        assert "parametrize" in template

    def test_compiles(self):
        state = _make_state()
        template = generate_boundary_template(state)
        compile(template, "<test>", "exec")


class TestStringTemplate:
    def test_generates_string_tests(self):
        state = _make_state()
        template = generate_string_template(state)
        assert "test_exact_output" in template
        assert "test_empty_string" in template

    def test_compiles(self):
        state = _make_state()
        template = generate_string_template(state)
        compile(template, "<test>", "exec")


class TestGenerateTemplateForCategory:
    def test_arithmetic_returns_template(self):
        state = _make_state()
        result = generate_template_for_category("arithmetic", state)
        assert result is not None
        assert "def test_" in result

    def test_conditional_returns_template(self):
        state = _make_state()
        result = generate_template_for_category("conditional", state)
        assert result is not None

    def test_boundary_returns_template(self):
        state = _make_state()
        result = generate_template_for_category("boundary", state)
        assert result is not None

    def test_string_returns_template(self):
        state = _make_state()
        result = generate_template_for_category("string", state)
        assert result is not None

    def test_unknown_category_returns_none(self):
        state = _make_state()
        result = generate_template_for_category("unknown_cat", state)
        assert result is None

    def test_keyword_returns_none(self):
        """Keyword category has no template generator."""
        state = _make_state()
        result = generate_template_for_category("keyword", state)
        assert result is None

    def test_category_generators_dict(self):
        assert "arithmetic" in CATEGORY_GENERATORS
        assert "conditional" in CATEGORY_GENERATORS
        assert "boundary" in CATEGORY_GENERATORS
        assert "string" in CATEGORY_GENERATORS


# ── Prescription engine template integration ────────────────────────────


class TestPrescriptionEngineTemplates:
    def test_arithmetic_prescription_has_template(self):
        state = _make_state(
            total=10,
            killed=7,
            survived=3,
            survived_by_category={"arithmetic": 3},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        arith_p = [p for p in diag.prescriptions if p.survivor_category == "arithmetic"]
        assert len(arith_p) == 1
        assert arith_p[0].suggested_test_template is not None
        assert "def test_" in arith_p[0].suggested_test_template

    def test_conditional_prescription_has_template(self):
        state = _make_state(
            total=10,
            killed=7,
            survived=3,
            survived_by_category={"conditional": 3},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        cond_p = [p for p in diag.prescriptions if p.survivor_category == "conditional"]
        assert len(cond_p) == 1
        assert cond_p[0].suggested_test_template is not None

    def test_string_prescription_has_template(self):
        state = _make_state(
            total=10,
            killed=7,
            survived=3,
            survived_by_category={"string": 3},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        str_p = [p for p in diag.prescriptions if p.survivor_category == "string"]
        assert len(str_p) == 1
        assert str_p[0].suggested_test_template is not None

    def test_keyword_prescription_has_no_template(self):
        state = _make_state(
            total=10,
            killed=7,
            survived=3,
            survived_by_category={"keyword": 3},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        kw_p = [p for p in diag.prescriptions if p.survivor_category == "keyword"]
        assert len(kw_p) == 1
        assert kw_p[0].suggested_test_template is None

    def test_decomposition_has_no_template(self):
        """Decomposition prescriptions are architectural — no test template."""
        state = _make_state(
            total=10,
            killed=3,
            survived=7,
            survived_by_category={"arithmetic": 3, "conditional": 2, "string": 2},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        decomp_p = [
            p for p in diag.prescriptions if p.category.value == "decompose_function"
        ]
        assert len(decomp_p) == 1
        assert decomp_p[0].suggested_test_template is None

    def test_low_survival_no_template(self):
        """Low survival (<= 10%) → no action required, no template."""
        state = _make_state(
            total=100,
            killed=95,
            survived=5,
            survived_by_category={"arithmetic": 5},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        assert diag.gate_status == "PASS"
        for p in diag.prescriptions:
            assert p.suggested_test_template is None

    def test_prescription_has_survivor_category(self):
        state = _make_state(
            total=10,
            killed=7,
            survived=3,
            survived_by_category={"arithmetic": 2, "conditional": 1},
            depth=CoverageDepth.PROFILED,
        )
        engine = PrescriptionEngine()
        diag = engine.diagnose(state)

        cats = {p.survivor_category for p in diag.prescriptions if p.survivor_category}
        assert "arithmetic" in cats
        assert "conditional" in cats


# ── Refactor loop auto-verify suggestion ────────────────────────────────


class TestRefactorLoopSuggestion:
    """Tests for the suggestion logic (unit-testing the helper data, not the MCP tool)."""

    def test_suggestion_when_still_above_threshold(self):
        """When survival improved but still > 20%, suggest mutation_prescribe."""
        from mcp_tools.mutation_tools import _profile_survival_rate

        before = {"survived": 7, "total": 10, "survival_rate": 0.7}
        after = {"survived": 4, "total": 10, "survival_rate": 0.4}

        rate_change = _profile_survival_rate(after) - _profile_survival_rate(before)
        after_rate = _profile_survival_rate(after)

        assert rate_change < 0  # Improved
        assert after_rate > 0.2  # Still above threshold

    def test_suggestion_when_below_threshold(self):
        """When survival is <= 20%, indicate strong specification."""
        from mcp_tools.mutation_tools import _profile_survival_rate

        after = {"survived": 1, "total": 10, "survival_rate": 0.1}
        assert _profile_survival_rate(after) <= 0.2
