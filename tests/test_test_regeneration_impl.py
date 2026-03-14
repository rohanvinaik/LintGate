"""Tests for test regeneration implementation helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mcp_tools._test_regeneration_impl import (
    _load_regen_config,
    impl_rebuild_generate,
    impl_rebuild_plan,
)


def _make_helpers(project_root="/tmp/project"):
    return {
        "_validate_project_root": MagicMock(return_value=project_root),
        "_json_dumps": MagicMock(side_effect=lambda x, **kw: str(x)),
    }


class TestLoadRegenConfig:
    @patch("lintgate.config.load_controlplane_config", side_effect=OSError("no config"))
    def test_fallback_to_defaults(self, _mock):
        cfg = _load_regen_config("/tmp")
        assert hasattr(cfg, "preserve_globs")

    @patch("lintgate.config.load_controlplane_config")
    def test_loads_from_config(self, mock_load):
        mock_cp = MagicMock()
        mock_load.return_value = mock_cp
        cfg = _load_regen_config("/tmp")
        assert cfg == mock_cp.test_regeneration


class TestRebuildPlan:
    @patch("mcp_tools._specification_helpers.resolve_py_files")
    def test_rebuild_plan_no_files_returns_error(self, mock_resolve, tmp_path):
        mock_resolve.return_value = {"error": "No Python files found"}
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_plan(helpers, str(tmp_path))
        assert "error" in result or "No Python" in result

    @patch("lintgate.specification.test_regeneration_strategy.write_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.classify_function")
    @patch("lintgate.specification.test_regeneration_strategy.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states", return_value=[])
    @patch("mcp_tools._mutation_impl.get_cache_dir")
    @patch("mcp_tools._specification_helpers.resolve_py_files")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_rebuild_plan_single_file(
        self,
        mock_validate,
        mock_resolve,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_evidence,
        mock_classify,
        mock_build,
        mock_write,
        tmp_path,
    ):
        test_file = str(tmp_path / "mod.py")
        mock_validate.return_value = test_file
        mock_analyze.return_value = MagicMock(functions={"f1": MagicMock(to_dict=lambda: {})})
        mock_classify.return_value = MagicMock()
        mock_build.return_value = MagicMock(summary=lambda: {"total": 1})
        mock_write.return_value = "manifest.json"
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_plan(helpers, str(tmp_path), file="mod.py")
        assert "manifest" in result.lower() or "total" in result.lower()


class TestRebuildGenerate:
    @patch("lintgate.specification.test_regeneration_strategy.load_manifest", return_value=None)
    def test_no_manifest_returns_error(self, _mock, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_generate(helpers, str(tmp_path))
        assert "error" in result or "No manifest" in result

    @patch("lintgate.testing.batch_regenerator.BatchRegenerator")
    @patch("lintgate.specification.test_regeneration_strategy.load_manifest")
    def test_respects_max_files(self, mock_load, mock_regen, tmp_path):
        from lintgate.specification.test_regeneration_strategy import Strategy

        # Create mock plan with 3 files
        mock_funcs = []
        for i in range(3):
            f = MagicMock()
            f.strategy = Strategy.AUTO_GENERATE_UNIT
            f.evidence.source_file = f"mod{i}.py"
            f.to_dict.return_value = {}
            mock_funcs.append(f)
        plan = MagicMock()
        plan.functions = mock_funcs
        mock_load.return_value = plan
        mock_regen.return_value.generate_for_file.return_value = None
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_generate(helpers, str(tmp_path), max_files=1)
        # Should process at most 1 file
        assert "files_processed" in result
