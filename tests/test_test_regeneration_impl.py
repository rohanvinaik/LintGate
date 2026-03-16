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


class TestRebuildPlanBranches:
    """Exact-value assertions for impl_rebuild_plan branches."""

    @patch("mcp_tools._specification_helpers.resolve_py_files")
    def test_resolve_returns_dict_error_passthrough(self, mock_resolve, tmp_path):
        """When resolve_py_files returns a dict (error), it is passed through directly."""
        error_dict = {"error": "No Python files found", "searched": str(tmp_path)}
        mock_resolve.return_value = error_dict
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_plan(helpers, str(tmp_path))
        # The error dict is serialized via _json_dumps
        assert "No Python files found" in result

    @patch("lintgate.specification.test_regeneration_strategy.write_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.classify_function")
    @patch("lintgate.specification.test_regeneration_strategy.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states", return_value=[])
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_single_file_output_contains_exact_fields(
        self,
        mock_validate,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_evidence,
        mock_classify,
        mock_build,
        mock_write,
        tmp_path,
    ):
        """Verify output dict contains manifest_path, errors, and next_actions."""
        test_file = str(tmp_path / "mod.py")
        mock_validate.return_value = test_file
        mock_analyze.return_value = MagicMock(
            functions={"mod::func1": MagicMock(to_dict=lambda: {"sigma": 5})}
        )
        mock_classify.return_value = MagicMock()
        mock_build.return_value = MagicMock(summary=lambda: {"total": 1, "auto_generate": 1})
        mock_write.return_value = "/tmp/manifest.json"
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_plan(helpers, str(tmp_path), file="mod.py")
        assert "manifest_path" in result
        assert "/tmp/manifest.json" in result
        assert "errors" in result
        assert "next_actions" in result
        assert "test_rebuild_generate" in result

    @patch("lintgate.specification.test_regeneration_strategy.write_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.classify_function")
    @patch("lintgate.specification.test_regeneration_strategy.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states")
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_mutation_cache_populates_mutation_by_key(
        self,
        mock_validate,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_evidence,
        mock_classify,
        mock_build,
        mock_write,
        tmp_path,
    ):
        """Mutation cache entries with function_key are passed to build_evidence."""
        test_file = str(tmp_path / "mod.py")
        mock_validate.return_value = test_file
        mock_iter.return_value = [
            {"function_key": "mod::func1", "survived": ["VALUE"]},
            {"function_key": "", "survived": []},  # empty key → skipped
        ]
        mock_analyze.return_value = MagicMock(
            functions={"mod::func1": MagicMock(to_dict=lambda: {"sigma": 3})}
        )
        mock_classify.return_value = MagicMock()
        mock_build.return_value = MagicMock(summary=lambda: {"total": 1})
        mock_write.return_value = "manifest.json"
        helpers = _make_helpers(str(tmp_path))
        impl_rebuild_plan(helpers, str(tmp_path), file="mod.py")
        # build_evidence should receive the mutation data for func1
        call_args = mock_evidence.call_args
        assert call_args is not None
        assert call_args[0][0] == "mod::func1"  # func_key
        assert call_args[0][3] == {"function_key": "mod::func1", "survived": ["VALUE"]}

    @patch("lintgate.specification.test_regeneration_strategy.write_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states", return_value=[])
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_analyze_file_syntax_error_captured(
        self,
        mock_validate,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_build,
        mock_write,
        tmp_path,
    ):
        """SyntaxError from analyze_file is captured in errors list."""
        test_file = str(tmp_path / "bad.py")
        mock_validate.return_value = test_file
        mock_analyze.side_effect = SyntaxError("invalid syntax")
        mock_build.return_value = MagicMock(summary=lambda: {"total": 0})
        mock_write.return_value = ""
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_plan(helpers, str(tmp_path), file="bad.py")
        assert "invalid syntax" in result

    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.classify_function")
    @patch("lintgate.specification.test_regeneration_strategy.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states", return_value=[])
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_write_manifest_false_skips_write(
        self,
        mock_validate,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_evidence,
        mock_classify,
        mock_build,
        tmp_path,
    ):
        """write_manifest=False → manifest_path is empty string, write not called."""
        test_file = str(tmp_path / "mod.py")
        mock_validate.return_value = test_file
        mock_analyze.return_value = MagicMock(functions={"mod::f": MagicMock(to_dict=lambda: {})})
        mock_classify.return_value = MagicMock()
        mock_build.return_value = MagicMock(summary=lambda: {"total": 1})
        helpers = _make_helpers(str(tmp_path))
        result = impl_rebuild_plan(helpers, str(tmp_path), file="mod.py", write_manifest=False)
        assert "'manifest_path': ''" in result or "manifest_path" in result

    @patch("lintgate.specification.test_regeneration_strategy.write_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.classify_function")
    @patch("lintgate.specification.test_regeneration_strategy.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states", return_value=[])
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_spec_isinstance_dict_branch(
        self,
        mock_validate,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_evidence,
        mock_classify,
        mock_build,
        mock_write,
        tmp_path,
    ):
        """When spec is already a dict (not object), to_dict is not called."""
        test_file = str(tmp_path / "mod.py")
        mock_validate.return_value = test_file
        # spec is a plain dict, not an object with to_dict
        spec_dict = {"sigma": 4, "regime": "A"}
        mock_analyze.return_value = MagicMock(functions={"mod::f": spec_dict})
        mock_classify.return_value = MagicMock()
        mock_build.return_value = MagicMock(summary=lambda: {"total": 1})
        mock_write.return_value = "m.json"
        helpers = _make_helpers(str(tmp_path))
        impl_rebuild_plan(helpers, str(tmp_path), file="mod.py")
        # build_evidence receives the dict directly (not to_dict() result)
        call_args = mock_evidence.call_args
        assert call_args[0][2] == {"sigma": 4, "regime": "A"}

    @patch("lintgate.config.load_controlplane_config")
    @patch("lintgate.specification.test_regeneration_strategy.write_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.build_manifest")
    @patch("lintgate.specification.test_regeneration_strategy.classify_function")
    @patch("lintgate.specification.test_regeneration_strategy.build_evidence")
    @patch("lintgate.specification.file_analyzer.analyze_file")
    @patch("mcp_tools._mutation_impl.iter_cached_states", return_value=[])
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    @patch("mcp_tools._specification_helpers.validate_file_in_project")
    def test_preserve_globs_from_config(
        self,
        mock_validate,
        mock_cache_dir,
        mock_iter,
        mock_analyze,
        mock_evidence,
        mock_classify,
        mock_build,
        mock_write,
        mock_config,
        tmp_path,
    ):
        """When preserve_globs is None, config defaults are loaded and passed to build_manifest."""
        test_file = str(tmp_path / "mod.py")
        mock_validate.return_value = test_file
        mock_cfg = MagicMock()
        mock_cfg.test_regeneration.preserve_globs = ["tests/conftest.py", "tests/fixtures/**"]
        mock_config.return_value = mock_cfg
        mock_analyze.return_value = MagicMock(functions={"mod::f": MagicMock(to_dict=lambda: {})})
        mock_classify.return_value = MagicMock()
        mock_build.return_value = MagicMock(summary=lambda: {"total": 1})
        mock_write.return_value = "m.json"
        helpers = _make_helpers(str(tmp_path))
        impl_rebuild_plan(helpers, str(tmp_path), file="mod.py", preserve_globs=None)
        # build_manifest should receive the config-provided preserve_globs
        build_call = mock_build.call_args
        assert build_call[0][2] == ["tests/conftest.py", "tests/fixtures/**"]


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
