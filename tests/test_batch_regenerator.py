"""Tests for lintgate.testing.batch_regenerator."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintgate.testing.batch_regenerator import (
    BatchRegenerator,
    FunctionEnrichment,
    GeneratedFile,
    _build_function_section,
    _category_assertion_hint,
    _merge_enrichments,
    _prescriptions_from_categories,
)

# ── Dataclass tests ──────────────────────────────────────────────────


class TestFunctionEnrichment:
    def test_defaults(self):
        e = FunctionEnrichment(function_key="mod::func", function_name="func")
        assert e.inputs == []
        assert e.prescriptions == []
        assert e.characterization == ""

    def test_with_data(self):
        e = FunctionEnrichment(
            function_key="mod::func",
            function_name="func",
            inputs=[{"context": "call()"}],
            prescriptions=[{"category": "VALUE"}],
            characterization="assert True",
        )
        assert len(e.inputs) == 1
        assert e.prescriptions[0]["category"] == "VALUE"


class TestGeneratedFile:
    def test_fields(self):
        g = GeneratedFile(
            source_file="src/foo.py",
            target_test_file="tests/generated/test_foo.py",
            content="# test",
            functions_covered=3,
            enrichment_sources=["skeleton", "mutation"],
        )
        assert g.functions_covered == 3
        assert "mutation" in g.enrichment_sources


# ── Merge / section builder tests ────────────────────────────────────


class TestMergeEnrichments:
    def test_skeleton_only(self):
        result = _merge_enrichments("# skeleton", [])
        assert result.startswith("# skeleton")
        assert result.endswith("\n")

    def test_with_enrichments(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            prescriptions=[{"category": "VALUE", "assertion_shape": "x == 1"}],
        )
        result = _merge_enrichments("# skeleton", [enr])
        assert "test_f_value_mutation" in result
        assert "VALUE" in result


class TestBuildFunctionSection:
    def test_empty_enrichment(self):
        enr = FunctionEnrichment(function_key="m::f", function_name="f")
        assert _build_function_section(enr) == ""

    def test_inputs_only(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            inputs=[{"context": "f(1, 2)"}],
        )
        section = _build_function_section(enr)
        assert "Input examples for f" in section
        assert "f(1, 2)" in section

    def test_prescriptions(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            prescriptions=[
                {"category": "SWAP", "assertion_shape": "a != b", "suggested_input": "x=1"},
            ],
        )
        section = _build_function_section(enr)
        assert "def test_f_swap_mutation():" in section
        assert "SWAP" in section
        assert "suggested input: x=1" in section

    def test_characterization_fallback(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            characterization="assert f(1) == 42",
        )
        section = _build_function_section(enr)
        assert "Characterization test for f" in section
        assert "assert f(1) == 42" in section

    def test_characterization_suppressed_when_prescriptions_exist(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            prescriptions=[{"category": "VALUE", "assertion_shape": "=="}],
            characterization="assert f(1) == 42",
        )
        section = _build_function_section(enr)
        assert "Characterization" not in section

    def test_dot_in_name_replaced(self):
        enr = FunctionEnrichment(
            function_key="m::Cls.method",
            function_name="Cls.method",
            prescriptions=[{"category": "STATE", "assertion_shape": "attr==X"}],
        )
        section = _build_function_section(enr)
        assert "test_Cls_method_state_mutation" in section

    def test_inputs_truncated_to_three(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            inputs=[{"context": f"call{i}()"} for i in range(5)],
        )
        section = _build_function_section(enr)
        assert "call0()" in section
        assert "call2()" in section
        assert "call3()" not in section


class TestCategoryAssertionHint:
    @pytest.mark.parametrize("cat", ["VALUE", "SWAP", "BOUNDARY", "STATE", "TYPE"])
    def test_known_categories(self, cat):
        hint = _category_assertion_hint(cat)
        assert "assert" in hint

    def test_unknown_category(self):
        hint = _category_assertion_hint("UNKNOWN")
        assert hint == "assert result == EXPECTED"


class TestPrescriptionsFromCategories:
    def test_filters_zero_survived(self):
        data = [
            {"category": "VALUE", "survived": 3},
            {"category": "SWAP", "survived": 0},
        ]
        result = _prescriptions_from_categories(data)
        assert len(result) == 1
        assert result[0]["category"] == "VALUE"
        assert result[0]["source"] == "category_generic"
        assert result[0]["confidence"] == 0.5


# ── BatchRegenerator tests (mocked lenses) ───────────────────────────


class TestBatchRegeneratorGenerateForFile:
    def test_returns_none_no_target(self):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file("/project/foo.py", [{"function_key": "m::f"}])
        assert result is None

    def test_returns_none_no_functions(self):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file("/project/foo.py", [])
        assert result is None

    @patch.object(BatchRegenerator, "_generate_skeleton", return_value="")
    def test_returns_none_no_skeleton(self, _mock):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file(
            "/project/foo.py",
            [{"function_key": "m::f", "target_test_file": "tests/test_foo.py"}],
        )
        assert result is None

    @patch.object(BatchRegenerator, "_generate_skeleton", return_value="# skeleton\n")
    @patch.object(BatchRegenerator, "_infer_inputs", return_value=[])
    @patch.object(BatchRegenerator, "_get_prescriptions", return_value=[])
    @patch.object(BatchRegenerator, "_characterize", return_value="")
    def test_skeleton_only(self, _char, _rx, _inp, _skel):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file(
            "/project/foo.py",
            [{"function_key": "m::f", "target_test_file": "tests/test_foo.py"}],
        )
        assert result is not None
        assert result.enrichment_sources == ["skeleton"]
        assert result.functions_covered == 1
        assert "# skeleton" in result.content

    @patch.object(BatchRegenerator, "_generate_skeleton", return_value="# skel\n")
    @patch.object(BatchRegenerator, "_infer_inputs", return_value=[{"context": "f(1)"}])
    @patch.object(
        BatchRegenerator,
        "_get_prescriptions",
        return_value=[
            {"category": "VALUE", "assertion_shape": "==", "suggested_input": "1"},
        ],
    )
    @patch.object(BatchRegenerator, "_characterize", return_value="")
    def test_full_enrichment(self, _char, _rx, _inp, _skel):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file(
            "/project/foo.py",
            [{"function_key": "m::f", "target_test_file": "tests/test_foo.py"}],
        )
        assert result is not None
        assert "inputs" in result.enrichment_sources
        assert "mutation" in result.enrichment_sources
        assert "test_f_value_mutation" in result.content

    @patch.object(BatchRegenerator, "_generate_skeleton", return_value="# skel\n")
    @patch.object(BatchRegenerator, "_infer_inputs", return_value=[])
    @patch.object(BatchRegenerator, "_get_prescriptions", return_value=[])
    @patch.object(BatchRegenerator, "_characterize", return_value="assert g() == 7")
    def test_characterize_fallback(self, _char, _rx, _inp, _skel):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file(
            "/project/foo.py",
            [{"function_key": "m::g", "target_test_file": "tests/test_foo.py"}],
        )
        assert result is not None
        assert "characterize" in result.enrichment_sources
        assert "assert g() == 7" in result.content

    @patch.object(BatchRegenerator, "_generate_skeleton", return_value="# skel\n")
    @patch.object(BatchRegenerator, "_infer_inputs", return_value=[{"context": "f(1)"}])
    @patch.object(BatchRegenerator, "_get_prescriptions", return_value=[])
    @patch.object(BatchRegenerator, "_characterize", return_value="")
    def test_characterize_skipped_when_inputs_exist(self, _char, _rx, _inp, _skel):
        """Characterization is only a fallback — skipped when inputs or prescriptions exist."""
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file(
            "/project/foo.py",
            [{"function_key": "m::f", "target_test_file": "tests/test_foo.py"}],
        )
        assert result is not None
        assert "characterize" not in result.enrichment_sources

    @patch.object(BatchRegenerator, "_generate_skeleton", return_value="# skel\n")
    @patch.object(BatchRegenerator, "_infer_inputs", return_value=[])
    @patch.object(BatchRegenerator, "_get_prescriptions", return_value=[])
    @patch.object(BatchRegenerator, "_characterize", return_value="")
    def test_func_key_splitting(self, _char, _rx, _inp, _skel):
        regen = BatchRegenerator("/project")
        result = regen.generate_for_file(
            "/project/foo.py",
            [{"function_key": "mod::Cls::method", "target_test_file": "tests/t.py"}],
        )
        assert result is not None
        assert result.functions_covered == 1


class TestBatchRegeneratorMutationCache:
    @patch("lintgate.testing.batch_regenerator.BatchRegenerator._load_mutation_cache")
    def test_cache_miss(self, mock_cache):
        mock_cache.return_value = {}
        regen = BatchRegenerator("/project")
        assert regen._get_prescriptions("missing::key") == []

    @patch("lintgate.testing.batch_regenerator.BatchRegenerator._load_mutation_cache")
    def test_cache_hit_with_categories(self, mock_cache):
        mock_cache.return_value = {
            "m::f": {
                "function_key": "m::f",
                "survivor_records": [],
                "per_category": [
                    {"category": "VALUE", "survived": 2},
                    {"category": "SWAP", "survived": 0},
                ],
            }
        }
        regen = BatchRegenerator("/project")
        rx = regen._get_prescriptions("m::f")
        assert len(rx) == 1
        assert rx[0]["category"] == "VALUE"
