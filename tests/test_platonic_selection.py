"""Tests for platonic selection helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.testing.platonic_selection import (
    _has_automation_headroom,
    assess_file,
    select_project_target,
)


def _mock_spec_result(functions=None, error=None):
    result = MagicMock()
    result.error = error
    result.functions = functions or {}
    return result


def _mock_classification(
    strategy_value="auto_generate_unit", func_key="mod.py::func", confidence=0.9
):
    from lintgate.specification.test_regeneration_strategy import Strategy

    cls = MagicMock()
    cls.strategy = Strategy(strategy_value)
    cls.function_key = func_key
    cls.confidence = confidence
    cls.evidence = MagicMock()
    cls.evidence.function_key = func_key
    cls.evidence.discovery_state = ""
    cls.evidence.survival_interpretation = ""
    cls.evidence.mutation_truth_label = ""
    cls.evidence.topology_state = ""
    cls.target_test_file = "tests/test_mod.py"
    return cls


class TestAssessFile:
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("lintgate.specification.file_analyzer._load_mutation_cache", return_value={})
    def test_assess_file_spec_error_returns_error_dict(self, _mock_cache, mock_analyze, tmp_path):
        mock_analyze.return_value = _mock_spec_result(error="parse error")
        result = assess_file(str(tmp_path), "mod.py")
        assert result["error"] == "parse error"
        assert result["classifications"] == []

    @patch("lintgate.testing.platonic_selection.classify_function")
    @patch("lintgate.testing.platonic_selection.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("lintgate.specification.file_analyzer._load_mutation_cache", return_value={})
    def test_assess_file_classifies_all_functions(
        self, _mc, mock_analyze, mock_evidence, mock_classify, tmp_path
    ):
        mock_analyze.return_value = _mock_spec_result(functions={"f1": {}, "f2": {}})
        mock_classify.return_value = _mock_classification()
        result = assess_file(str(tmp_path), "mod.py")
        assert len(result["classifications"]) == 2

    @patch("lintgate.testing.platonic_selection.classify_function")
    @patch("lintgate.testing.platonic_selection.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("lintgate.specification.file_analyzer._load_mutation_cache", return_value={})
    def test_assess_file_majority_veto_threshold(
        self, _mc, mock_analyze, mock_evidence, mock_classify, tmp_path
    ):
        mock_analyze.return_value = _mock_spec_result(functions={"f1": {}, "f2": {}, "f3": {}})
        # 2 out of 3 are EXCLUDE_MUTATION -> majority veto
        exclude_cls = _mock_classification(strategy_value="exclude_mutation")
        auto_cls = _mock_classification()
        mock_classify.side_effect = [exclude_cls, exclude_cls, auto_cls]
        result = assess_file(str(tmp_path), "mod.py")
        assert result["majority_hard_veto"] is True

    @patch("lintgate.testing.platonic_selection.classify_function")
    @patch("lintgate.testing.platonic_selection.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("lintgate.specification.file_analyzer._load_mutation_cache", return_value={})
    def test_assess_file_strategy_distribution_counts(
        self, _mc, mock_analyze, mock_evidence, mock_classify, tmp_path
    ):
        mock_analyze.return_value = _mock_spec_result(functions={"f1": {}, "f2": {}})
        mock_classify.return_value = _mock_classification(strategy_value="auto_generate_unit")
        result = assess_file(str(tmp_path), "mod.py")
        assert result["summary"]["strategy_distribution"]["auto_generate_unit"] == 2

    @patch("lintgate.testing.platonic_selection.classify_function")
    @patch("lintgate.testing.platonic_selection.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("lintgate.specification.file_analyzer._load_mutation_cache", return_value={})
    def test_assess_file_artifact_states_cause_veto(
        self, _mc, mock_analyze, mock_evidence, mock_classify, tmp_path
    ):
        mock_analyze.return_value = _mock_spec_result(functions={"f1": {}})
        cls = _mock_classification()
        cls.evidence.discovery_state = "DISCOVERY_ARTIFACT"
        mock_classify.return_value = cls
        result = assess_file(str(tmp_path), "mod.py")
        # Single function with artifact = majority veto
        assert result["majority_hard_veto"] is True


class TestSelectProjectTarget:
    @patch("lintgate.specification.project_rollup.rollup_project")
    @patch("lintgate.testing.platonic_selection.assess_file")
    def test_select_picks_first_eligible_file(self, mock_assess, mock_rollup, tmp_path):
        mock_rollup.return_value = MagicMock(hotspot_files=[{"file": "a.py"}, {"file": "b.py"}])
        mock_assess.return_value = {
            "error": None,
            "majority_hard_veto": False,
            "auto_targets": [MagicMock()],
            "decompose_targets": [],
        }
        result = select_project_target(str(tmp_path))
        assert result["selected_file"] == "a.py"

    @patch("lintgate.specification.project_rollup.rollup_project")
    @patch("lintgate.testing.platonic_selection.assess_file")
    def test_select_skips_vetoed_files(self, mock_assess, mock_rollup, tmp_path):
        mock_rollup.return_value = MagicMock(hotspot_files=[{"file": "a.py"}, {"file": "b.py"}])

        def side_effect(root, f, **kw):
            if f == "a.py":
                return {
                    "error": None,
                    "majority_hard_veto": True,
                    "auto_targets": [],
                    "decompose_targets": [],
                }
            return {
                "error": None,
                "majority_hard_veto": False,
                "auto_targets": [MagicMock()],
                "decompose_targets": [],
            }

        mock_assess.side_effect = side_effect
        result = select_project_target(str(tmp_path))
        assert result["selected_file"] == "b.py"

    @patch("lintgate.specification.project_rollup.rollup_project")
    @patch("lintgate.testing.platonic_selection.assess_file")
    def test_select_skips_error_files(self, mock_assess, mock_rollup, tmp_path):
        mock_rollup.return_value = MagicMock(hotspot_files=[{"file": "a.py"}])
        mock_assess.return_value = {
            "error": "parse failed",
            "majority_hard_veto": False,
            "auto_targets": [],
            "decompose_targets": [],
        }
        result = select_project_target(str(tmp_path))
        assert result["selected_file"] == ""

    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_select_no_eligible_returns_empty(self, mock_rollup, tmp_path):
        mock_rollup.return_value = MagicMock(hotspot_files=[])
        result = select_project_target(str(tmp_path))
        assert result["selected_file"] == ""
        assert result["assessment"] is None

    @patch("lintgate.specification.project_rollup.rollup_project")
    @patch("lintgate.testing.platonic_selection.assess_file")
    def test_select_respects_max_files(self, mock_assess, mock_rollup, tmp_path):
        mock_rollup.return_value = MagicMock(hotspot_files=[{"file": f"{i}.py"} for i in range(10)])
        mock_assess.return_value = {
            "error": None,
            "majority_hard_veto": False,
            "auto_targets": [],
            "decompose_targets": [],
        }
        result = select_project_target(str(tmp_path), max_files=2)
        assert result["files_inspected"] <= 2

    @patch("lintgate.specification.project_rollup.rollup_project")
    @patch("lintgate.testing.platonic_selection.assess_file")
    def test_select_skips_files_without_headroom(self, mock_assess, mock_rollup, tmp_path):
        """Files where all auto_targets are tail-phase + high spec_level are skipped."""
        mock_rollup.return_value = MagicMock(hotspot_files=[{"file": "a.py"}, {"file": "b.py"}])

        # a.py: auto_targets but no headroom (tail phase, high spec_level)
        func_data_a = MagicMock()
        func_data_a.phase = "tail"
        func_data_a.spec_level = 0.8
        spec_result_a = MagicMock()
        spec_result_a.functions = {"a.py::func": func_data_a}
        target_a = MagicMock()
        target_a.function_key = "a.py::func"

        # b.py: auto_targets with headroom (bulk phase)
        func_data_b = MagicMock()
        func_data_b.phase = "bulk"
        func_data_b.spec_level = 0.2
        spec_result_b = MagicMock()
        spec_result_b.functions = {"b.py::func": func_data_b}
        target_b = MagicMock()
        target_b.function_key = "b.py::func"

        def side_effect(root, f, **kw):
            if f == "a.py":
                return {
                    "error": None,
                    "majority_hard_veto": False,
                    "auto_targets": [target_a],
                    "decompose_targets": [],
                    "spec_result": spec_result_a,
                }
            return {
                "error": None,
                "majority_hard_veto": False,
                "auto_targets": [target_b],
                "decompose_targets": [],
                "spec_result": spec_result_b,
            }

        mock_assess.side_effect = side_effect
        result = select_project_target(str(tmp_path))
        assert result["selected_file"] == "b.py"


class TestHasAutomationHeadroom:
    def test_no_auto_targets_returns_true(self):
        assert _has_automation_headroom({"auto_targets": [], "spec_result": MagicMock()}) is True

    def test_no_spec_result_returns_true(self):
        assert (
            _has_automation_headroom({"auto_targets": [MagicMock()], "spec_result": None}) is True
        )

    def test_all_tail_high_spec_returns_false(self):
        func_data = MagicMock()
        func_data.phase = "tail"
        func_data.spec_level = 0.8
        spec_result = MagicMock()
        target = MagicMock()
        target.function_key = "mod.py::func"
        spec_result.functions = {"mod.py::func": func_data}
        assert (
            _has_automation_headroom(
                {
                    "auto_targets": [target],
                    "spec_result": spec_result,
                }
            )
            is False
        )

    def test_bulk_phase_returns_true(self):
        func_data = MagicMock()
        func_data.phase = "bulk"
        func_data.spec_level = 0.2
        spec_result = MagicMock()
        target = MagicMock()
        target.function_key = "mod.py::func"
        spec_result.functions = {"mod.py::func": func_data}
        assert (
            _has_automation_headroom(
                {
                    "auto_targets": [target],
                    "spec_result": spec_result,
                }
            )
            is True
        )

    def test_mixed_targets_returns_true(self):
        """If at least one target has headroom, returns True."""
        t1_data = MagicMock()
        t1_data.phase = "tail"
        t1_data.spec_level = 0.9
        t2_data = MagicMock()
        t2_data.phase = "bulk"
        t2_data.spec_level = 0.1
        spec_result = MagicMock()
        t1 = MagicMock()
        t1.function_key = "mod.py::f1"
        t2 = MagicMock()
        t2.function_key = "mod.py::f2"
        spec_result.functions = {"mod.py::f1": t1_data, "mod.py::f2": t2_data}
        assert (
            _has_automation_headroom(
                {
                    "auto_targets": [t1, t2],
                    "spec_result": spec_result,
                }
            )
            is True
        )

    def test_complete_phase_blocks(self):
        func_data = MagicMock()
        func_data.phase = "complete"
        func_data.spec_level = 0.7
        spec_result = MagicMock()
        target = MagicMock()
        target.function_key = "mod.py::func"
        spec_result.functions = {"mod.py::func": func_data}
        assert (
            _has_automation_headroom(
                {
                    "auto_targets": [target],
                    "spec_result": spec_result,
                }
            )
            is False
        )
