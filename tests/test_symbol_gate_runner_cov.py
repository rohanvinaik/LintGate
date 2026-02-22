"""Coverage tests for lintgate/symbol_gate_runner.py."""

from __future__ import annotations

from unittest import mock

from lintgate.symbol_gate_runner import (
    _build_arg_parser,
    _run_git_list,
    _to_bool,
    collect_changed_python_files,
    load_symbol_coverage_settings,
    main,
    run_symbol_gate,
)


class TestToBool:
    def test_bool_true(self):
        assert _to_bool(True) is True

    def test_bool_false(self):
        assert _to_bool(False) is False

    def test_str_true_variants(self):
        for v in ("1", "true", "yes", "on", "True", "YES", " On "):
            assert _to_bool(v) is True, f"Failed for {v!r}"

    def test_str_false_variants(self):
        for v in ("0", "false", "no", "off", "False", "NO"):
            assert _to_bool(v) is False, f"Failed for {v!r}"

    def test_none_uses_default(self):
        assert _to_bool(None, default=True) is True
        assert _to_bool(None, default=False) is False

    def test_other_types(self):
        assert _to_bool(1) is True
        assert _to_bool(0) is False
        assert _to_bool([]) is False
        assert _to_bool([1]) is True


class TestRunGitList:
    def test_success(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0, stdout="foo.py\nbar.py\n")
            result = _run_git_list("/tmp", ["diff", "--name-only"])
        assert result == ["foo.py", "bar.py"]

    def test_failure(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=1, stdout="")
            result = _run_git_list("/tmp", ["diff", "--name-only"])
        assert result is None

    def test_timeout(self):
        import subprocess

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            result = _run_git_list("/tmp", ["status"])
        assert result is None


class TestLoadSymbolCoverageSettings:
    def test_no_config_file(self, tmp_path):
        result = load_symbol_coverage_settings(str(tmp_path))
        assert result["enabled"] is True
        assert result["mode"] == "changed"

    def test_valid_config(self, tmp_path):
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "lintgate.yaml").write_text(
            "controlplane:\n  channels:\n    tests:\n      symbol_coverage:\n"
            "        enabled: false\n        mode: explicit\n        diff_base: main\n"
        )
        result = load_symbol_coverage_settings(str(tmp_path))
        assert result["enabled"] is False
        assert result["mode"] == "explicit"
        assert result["diff_base"] == "main"

    def test_malformed_yaml(self, tmp_path):
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "lintgate.yaml").write_text("{{invalid yaml")
        result = load_symbol_coverage_settings(str(tmp_path))
        assert result["enabled"] is True  # defaults preserved


class TestCollectChangedPythonFiles:
    def test_explicit_files(self, tmp_path):
        f = tmp_path / "foo.py"
        f.write_text("pass")
        result = collect_changed_python_files(str(tmp_path), explicit_files=[str(f)])
        assert len(result) == 1
        assert result[0] == str(f.resolve())

    def test_base_and_head(self, tmp_path):
        f = tmp_path / "bar.py"
        f.write_text("pass")
        with mock.patch(
            "lintgate.symbol_gate_runner._run_git_list",
            return_value=["bar.py"],
        ):
            result = collect_changed_python_files(str(tmp_path), base="main", head="feature")
        assert any("bar.py" in r for r in result)

    def test_base_only(self, tmp_path):
        f = tmp_path / "baz.py"
        f.write_text("pass")
        with mock.patch(
            "lintgate.symbol_gate_runner._run_git_list",
            return_value=["baz.py"],
        ):
            result = collect_changed_python_files(str(tmp_path), base="main")
        assert any("baz.py" in r for r in result)

    def test_fallback_to_tracked(self, tmp_path):
        f = tmp_path / "tracked.py"
        f.write_text("pass")
        with mock.patch(
            "lintgate.symbol_gate_runner._run_git_list",
            side_effect=[None, None, ["tracked.py"]],
        ):
            result = collect_changed_python_files(str(tmp_path))
        assert any("tracked.py" in r for r in result)


class TestBuildArgParser:
    def test_defaults(self):
        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.project_root == "."
        assert args.coverage_json == "coverage.json"
        assert args.surface == "ci"
        assert args.base is None
        assert args.head is None


class TestRunSymbolGate:
    def test_missing_coverage_json(self, tmp_path):
        code = run_symbol_gate(
            project_root=str(tmp_path),
            coverage_json="nonexistent.json",
            base=None,
            head=None,
            explicit_files=None,
            surface="ci",
        )
        assert code == 1

    def test_gate_disabled(self, tmp_path):
        cov = tmp_path / "coverage.json"
        cov.write_text("{}")
        with mock.patch(
            "lintgate.symbol_gate_runner.load_symbol_coverage_settings",
            return_value={"enabled": False},
        ):
            code = run_symbol_gate(
                project_root=str(tmp_path),
                coverage_json=str(cov),
                base=None,
                head=None,
                explicit_files=None,
                surface="ci",
            )
        assert code == 0

    def test_gate_passes(self, tmp_path):
        cov = tmp_path / "coverage.json"
        cov.write_text("{}")

        gate_result = mock.MagicMock()
        gate_result.passed = True
        gate_result.symbol_results = []
        gate_result.waivers_applied = []
        gate_result.unresolved_required = []
        gate_result.skipped_reasons = []

        with (
            mock.patch(
                "lintgate.symbol_gate_runner.load_symbol_coverage_settings",
                return_value={"enabled": True, "mode": "changed"},
            ),
            mock.patch(
                "lintgate.symbol_gate_runner.collect_changed_python_files",
                return_value=[],
            ),
            mock.patch(
                "lintgate.symbol_gate_runner.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            code = run_symbol_gate(
                project_root=str(tmp_path),
                coverage_json=str(cov),
                base=None,
                head=None,
                explicit_files=None,
                surface="mcp",
            )
        assert code == 0


class TestMain:
    def test_main_passes(self, tmp_path):
        cov = tmp_path / "coverage.json"
        cov.write_text("{}")

        gate_result = mock.MagicMock()
        gate_result.passed = True
        gate_result.symbol_results = []
        gate_result.waivers_applied = []
        gate_result.unresolved_required = []
        gate_result.skipped_reasons = []

        with (
            mock.patch(
                "lintgate.symbol_gate_runner.load_symbol_coverage_settings",
                return_value={"enabled": True},
            ),
            mock.patch(
                "lintgate.symbol_gate_runner.collect_changed_python_files",
                return_value=[],
            ),
            mock.patch(
                "lintgate.symbol_gate_runner.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            code = main(["--project-root", str(tmp_path), "--coverage-json", str(cov)])
        assert code == 0
