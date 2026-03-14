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
        result, manual = _merge_enrichments("# skeleton", [])
        assert result.startswith("# skeleton")
        assert result.endswith("\n")
        assert manual == []

    def test_with_enrichments(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            characterization="assert f(1) == 1",
        )
        result, manual = _merge_enrichments("# skeleton", [enr])
        assert "Characterization test for f" in result
        assert manual == []

    def test_strips_placeholder_skeleton_tests(self):
        skeleton = (
            '"""Tests for foo."""\n\n'
            "from __future__ import annotations\n\n"
            "import pytest\n\n"
            "def test_placeholder() -> None:\n"
            "    result = func(...)\n"
            "    assert result == EXPECTED  # TODO: replace\n\n"
            "def test_real_error_path() -> None:\n"
            "    with pytest.raises(ValueError):\n"
            "        raise ValueError()\n"
        )
        result, manual = _merge_enrichments(skeleton, [])
        assert "test_placeholder" not in result
        assert "test_real_error_path" in result
        assert "import pytest" in result
        assert manual == []

    def test_tracks_manual_contract_candidates(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            prescriptions=[{"category": "VALUE", "assertion_shape": "assert result == 1"}],
        )
        result, manual = _merge_enrichments("# skeleton", [enr])
        assert result.startswith("# skeleton")
        assert manual == ["m::f"]


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
        assert section == ""

    def test_prescriptions(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            prescriptions=[
                {"category": "SWAP", "assertion_shape": "a != b", "suggested_input": "x=1"},
            ],
        )
        section = _build_function_section(enr)
        assert section == ""

    def test_characterization_fallback(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            characterization="assert f(1) == 42",
        )
        section = _build_function_section(enr)
        assert "Characterization test for f" in section
        assert "assert f(1) == 42" in section

    def test_characterization_provisional_when_prescriptions_exist(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            prescriptions=[{"category": "VALUE", "assertion_shape": "=="}],
            characterization="assert f(1) == 42",
        )
        section = _build_function_section(enr)
        assert "PROVISIONAL" in section
        assert "assert f(1) == 42" in section

    def test_callsite_assertion_promotion(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            inputs=[
                {"context": "assert f(0, 5) == 1.0"},
                {"context": "assert f(10, 3) == 0.3"},
                {"context": "f(1, 2)"},  # not an assert — stays as comment
            ],
        )
        section = _build_function_section(enr)
        assert "def test_f_callsite():" in section
        assert "assert f(0, 5) == 1.0" in section
        assert "assert f(10, 3) == 0.3" in section
        assert "promoted from call-site" in section
        # Non-assert input still appears as comment
        assert "f(1, 2)" in section

    def test_callsite_promotion_rejects_non_literal(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            inputs=[
                {"context": "assert f(some_var) == 1"},  # non-literal arg
            ],
        )
        section = _build_function_section(enr)
        assert "test_f_callsite" not in section

    def test_callsite_promotion_deduplicates(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            inputs=[
                {"context": "assert f(1) == 2"},
                {"context": "assert f(1) == 2"},  # duplicate
            ],
        )
        section = _build_function_section(enr)
        assert section.count("assert f(1) == 2") == 1

    def test_dot_in_name_replaced(self):
        enr = FunctionEnrichment(
            function_key="m::Cls.method",
            function_name="Cls.method",
            characterization="assert Cls.method(1) == 2",
        )
        section = _build_function_section(enr)
        assert "Characterization test for Cls.method" in section

    def test_inputs_truncated_to_three(self):
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            inputs=[{"context": f"call{i}()"} for i in range(5)],
        )
        section = _build_function_section(enr)
        assert section == ""

    def test_needs_oracle_property_skipped(self):
        from lintgate.testing.oracle_light import ExecutableProperty

        enr = FunctionEnrichment(
            function_key="mod::f",
            function_name="f",
            executable_properties=[
                ExecutableProperty(
                    category="VALUE",
                    inputs={},
                    setup_code="",
                    assertion_code="assert result == ...",
                    preconditions=["needs expected value"],
                    confidence=0.3,
                    source_lenses=["mutation"],
                    needs_oracle=True,
                    function_key="mod::f",
                    mutant_id="value_0",
                )
            ],
        )
        assert _build_function_section(enr) == ""


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
        assert result.manual_contract_candidates == ["m::f"]
        assert "test_f_value_mutation" not in result.content

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


# ── Private method tests (coverage) ──────────────────────────────────

# Late imports inside methods — patch at source modules
_SKEL_SRC = "lintgate.controlplane.skeleton_generator.generate_test_skeleton"
_INFER_SRC = "mcp_tools.cold_start_tools._impl_test_infer_inputs"
_CHAR_SRC = "mcp_tools.cold_start_tools._impl_test_characterize"
_PURITY_SRC = "mcp_tools._mutation_impl.detect_purity"
_CACHE_DIR_SRC = "mcp_tools._mutation_impl.get_cache_dir"
_ITER_CACHE_SRC = "mcp_tools._mutation_impl.iter_cached_states"
_CAPTURE_SRC = "lintgate.testing.characterization.capture_golden"
_CORROB_SRC = "lintgate.testing.characterization.corroborate_captures"
_GOLDEN_SRC = "lintgate.testing.characterization.generate_golden_test"


class TestBatchRegeneratorSkeleton:
    @patch(_SKEL_SRC, return_value="# skel\n")
    def test_success(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._generate_skeleton("/project/foo.py") == "# skel\n"

    @patch(_SKEL_SRC, side_effect=ImportError("no module"))
    def test_exception_returns_empty(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._generate_skeleton("/project/foo.py") == ""


class TestBatchRegeneratorInferInputs:
    @patch(_INFER_SRC)
    def test_success(self, mock_infer):
        mock_infer.return_value = '{"call_sites": [{"context": "f(1)"}]}'
        regen = BatchRegenerator("/project")
        result = regen._infer_inputs("src/mod.py", "func")
        assert len(result) == 1
        assert result[0]["context"] == "f(1)"

    @patch(_INFER_SRC)
    def test_error_response(self, mock_infer):
        mock_infer.return_value = '{"error": "not found"}'
        regen = BatchRegenerator("/project")
        assert regen._infer_inputs("src/mod.py", "func") == []

    @patch(_INFER_SRC, side_effect=Exception("fail"))
    def test_exception_returns_empty(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._infer_inputs("src/mod.py", "func") == []


class TestBatchRegeneratorCharacterize:
    @patch(_CHAR_SRC)
    def test_success(self, mock_char):
        mock_char.return_value = '{"test_code": "assert f(1) == 42"}'
        regen = BatchRegenerator("/project")
        assert regen._characterize("src/mod.py", "f") == "assert f(1) == 42"

    @patch(_CHAR_SRC)
    def test_error_response(self, mock_char):
        mock_char.return_value = '{"error": "fail"}'
        regen = BatchRegenerator("/project")
        assert regen._characterize("src/mod.py", "f") == ""

    @patch(_CHAR_SRC, side_effect=Exception("boom"))
    def test_exception_returns_empty(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._characterize("src/mod.py", "f") == ""


class TestBatchRegeneratorCheckPurity:
    @patch(_PURITY_SRC, return_value=True)
    def test_pure(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._check_purity("src/mod.py", "f") is True

    @patch(_PURITY_SRC, return_value=False)
    def test_impure(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._check_purity("src/mod.py", "f") is False

    @patch(_PURITY_SRC, side_effect=Exception("no"))
    def test_exception_returns_false(self, mock_purity):
        regen = BatchRegenerator("/project")
        assert regen._check_purity("src/mod.py", "f") is False
        mock_purity.assert_called_once()  # confirms exception path was hit


class TestBatchRegeneratorResolveFuncNode:
    def test_finds_function(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def helper():\n    return 1\n\ndef target():\n    return 2\n")
        regen = BatchRegenerator(str(tmp_path))
        node = regen._resolve_func_node("mod.py", "target")
        assert node is not None
        assert node.name == "target"

    def test_missing_function_returns_none(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def other(): pass\n")
        regen = BatchRegenerator(str(tmp_path))
        assert regen._resolve_func_node("mod.py", "missing") is None

    def test_missing_file_returns_none(self):
        regen = BatchRegenerator("/nonexistent")
        assert regen._resolve_func_node("no.py", "f") is None

    def test_finds_qualified_method(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("class Item:\n    def to_dict(self):\n        return {'x': 1}\n")
        regen = BatchRegenerator(str(tmp_path))
        node = regen._resolve_func_node("mod.py", "Item.to_dict")
        assert node is not None
        assert node.name == "to_dict"


class TestBatchRegeneratorLoadMutationCache:
    @patch(_CACHE_DIR_SRC, return_value="/tmp/cache")
    @patch(_ITER_CACHE_SRC, return_value=[{"function_key": "m::f", "data": 1}])
    def test_loads_and_caches(self, _iter, _dir):
        regen = BatchRegenerator("/project")
        cache = regen._load_mutation_cache()
        assert "m::f" in cache
        # Second call returns cached result (no re-import)
        cache2 = regen._load_mutation_cache()
        assert cache2 is cache

    @patch(_CACHE_DIR_SRC, side_effect=ImportError("no"))
    def test_import_error_returns_empty(self, _mock):
        regen = BatchRegenerator("/project")
        assert regen._load_mutation_cache() == {}


class TestBatchRegeneratorGoldenCapture:
    @patch.object(BatchRegenerator, "_load_mutation_cache", return_value={})
    @patch.object(BatchRegenerator, "_check_purity", return_value=True)
    @patch(_CAPTURE_SRC, return_value=[{"val": 1}])
    @patch(_CORROB_SRC, return_value=[{"val": 1}])
    @patch(_GOLDEN_SRC, return_value="assert f() == 1")
    def test_success(self, _gen, _corr, _cap, _pur, _cache):
        regen = BatchRegenerator("/project")
        rendered, captures = regen._golden_capture("src/mod.py", "mod::f", "f", [{"args": [1]}])
        assert rendered == "assert f() == 1"
        assert captures == [{"val": 1}]

    def test_no_module_path_returns_empty(self):
        regen = BatchRegenerator("/project")
        rendered, captures = regen._golden_capture("src/mod.py", "f", "f", [])
        assert rendered == ""
        assert captures == []

    @patch.object(BatchRegenerator, "_load_mutation_cache", return_value={})
    @patch.object(BatchRegenerator, "_check_purity", return_value=True)
    @patch(_CAPTURE_SRC, return_value=[])
    def test_no_captures_returns_empty(self, _cap, _pur, _cache):
        regen = BatchRegenerator("/project")
        rendered, captures = regen._golden_capture("src/mod.py", "mod::f", "f", [{"args": [1]}])
        assert rendered == ""
        assert captures == []

    @patch.object(BatchRegenerator, "_load_mutation_cache", return_value={})
    @patch.object(BatchRegenerator, "_check_purity", return_value=True)
    @patch(_CAPTURE_SRC, side_effect=Exception("boom"))
    def test_exception_returns_empty(self, mock_cap, _pur, _cache):
        regen = BatchRegenerator("/project")
        rendered, captures = regen._golden_capture("src/mod.py", "mod::f", "f", [{"args": [1]}])
        assert rendered == ""
        assert captures == []
        mock_cap.assert_called_once()  # confirms exception path was hit


class TestBatchRegeneratorExecutableProperties:
    @patch.object(BatchRegenerator, "_load_mutation_cache")
    def test_no_state_returns_empty(self, mock_cache):
        mock_cache.return_value = {}
        regen = BatchRegenerator("/project")
        assert regen._get_executable_properties("src/mod.py", "m::f", "f", []) == []

    @patch.object(BatchRegenerator, "_load_mutation_cache")
    def test_no_survivors_returns_empty(self, mock_cache):
        mock_cache.return_value = {"m::f": {"survivor_records": []}}
        regen = BatchRegenerator("/project")
        assert regen._get_executable_properties("src/mod.py", "m::f", "f", []) == []


# ── Integration: factory import ordering (P0-2) ─────────────────────


class TestFactoryImportOrdering:
    def test_factory_imports_after_docstring_and_future_annotations(self, monkeypatch):
        """Factory imports must not precede from __future__ import annotations."""
        skeleton = '"""Tests."""\n\nfrom __future__ import annotations\n\nimport pytest\n\n'
        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            executable_properties=[],
        )
        monkeypatch.setattr(
            "lintgate.testing.batch_regenerator._generate_shared_factories",
            lambda _enrichments: ("", ["from lintgate.types import LintIssue"]),
        )
        content, _ = _merge_enrichments(skeleton, [enr])

        # Verify: from __future__ must be the first non-comment, non-blank import
        lines = content.split("\n")
        first_import_idx = None
        future_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("from ") or stripped.startswith("import "):
                if first_import_idx is None:
                    first_import_idx = i
                if "from __future__" in stripped:
                    future_idx = i
                    break
        if future_idx is not None:
            assert future_idx == first_import_idx, (
                f"__future__ import at line {future_idx} but first import at {first_import_idx}"
            )


class TestSectionValidation:
    def test_invalid_section_routes_to_manual_contract(self):
        from lintgate.testing.oracle_light import ExecutableProperty

        enr = FunctionEnrichment(
            function_key="m::f",
            function_name="f",
            executable_properties=[
                ExecutableProperty(
                    category="VALUE",
                    inputs={},
                    setup_code="",
                    assertion_code="result = missing_name\nassert result == 1",
                    preconditions=[],
                    confidence=0.8,
                    source_lenses=["test"],
                    needs_oracle=False,
                )
            ],
        )
        content, manual = _merge_enrichments("import pytest\n", [enr])
        assert manual == ["m::f"]
        assert "test_f_value" not in content


# ── Integration: round-trip naming uniqueness (P1-3) ────────────────


class TestRoundTripNaming:
    def test_distinct_names_for_multiple_pairs(self):
        """Multiple round-trip pairs must produce distinct test function names."""
        from lintgate.testing.oracle_light import ExecutableProperty

        prop_a = ExecutableProperty(
            category="ROUND_TRIP",
            inputs={},
            setup_code="from m import A",
            assertion_code="assert True",
            preconditions=[],
            confidence=0.9,
            source_lenses=["pair_detection"],
            needs_oracle=False,
            function_key="m.py::Alpha.to_dict",
            mutant_id="",
        )
        prop_b = ExecutableProperty(
            category="ROUND_TRIP",
            inputs={},
            setup_code="from m import B",
            assertion_code="assert True",
            preconditions=[],
            confidence=0.9,
            source_lenses=["pair_detection"],
            needs_oracle=False,
            function_key="m.py::Beta.to_dict",
            mutant_id="",
        )
        enr_a = FunctionEnrichment(
            function_key="m.py::Alpha.to_dict",
            function_name="round_trip_Alpha",
            executable_properties=[prop_a],
        )
        enr_b = FunctionEnrichment(
            function_key="m.py::Beta.to_dict",
            function_name="round_trip_Beta",
            executable_properties=[prop_b],
        )
        content, _ = _merge_enrichments("# skel\n", [enr_a, enr_b])

        # Extract test function names
        import re

        test_names = re.findall(r"def (test_\w+)\(", content)
        assert len(test_names) >= 2, f"Expected >=2 test functions, got {test_names}"
        assert len(test_names) == len(set(test_names)), f"Duplicate test names: {test_names}"
