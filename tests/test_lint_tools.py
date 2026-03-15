"""Tests for mcp_tools/lint_tools.py helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_tools.lint_tools import (
    _format_cmd,
    _linter_available,
    _missing_tool_hints,
    _project_venv_python,
    _tool_package_name,
)

# ---------------------------------------------------------------------------
# _tool_package_name
# ---------------------------------------------------------------------------


class TestToolPackageName:
    def test_pip_audit_maps_to_pip_dash_audit(self):
        assert _tool_package_name("pip-audit") == "pip-audit"

    def test_ruff_maps_to_ruff(self):
        assert _tool_package_name("ruff") == "ruff"

    def test_ty_maps_to_ty(self):
        assert _tool_package_name("ty") == "ty"

    def test_bandit_maps_to_bandit(self):
        assert _tool_package_name("bandit") == "bandit"


# ---------------------------------------------------------------------------
# _project_venv_python
# ---------------------------------------------------------------------------


class TestProjectVenvPython:
    def test_finds_dotenv_python(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text("#!/usr/bin/env python3")
        result = _project_venv_python(str(tmp_path))
        assert result == str(py)

    def test_finds_venv_python(self, tmp_path):
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text("#!/usr/bin/env python3")
        result = _project_venv_python(str(tmp_path))
        assert result == str(py)

    def test_returns_none_when_no_venv(self, tmp_path):
        assert _project_venv_python(str(tmp_path)) is None

    def test_prefers_dotenv_over_venv(self, tmp_path):
        for name in (".venv", "venv"):
            venv_bin = tmp_path / name / "bin"
            venv_bin.mkdir(parents=True)
            py = venv_bin / "python"
            py.write_text("#!/usr/bin/env python3")
        result = _project_venv_python(str(tmp_path))
        assert ".venv" in result


# ---------------------------------------------------------------------------
# _format_cmd
# ---------------------------------------------------------------------------


class TestFormatCmd:
    def test_simple_command(self):
        assert _format_cmd(["pip", "install", "ruff"]) == "pip install ruff"

    def test_quotes_spaces(self):
        result = _format_cmd(["python", "-m", "pip install"])
        assert "'pip install'" in result

    def test_empty_list(self):
        assert _format_cmd([]) == ""


# ---------------------------------------------------------------------------
# _linter_available
# ---------------------------------------------------------------------------


class TestLinterAvailable:
    def test_calls_with_project_root_kwarg(self):
        linter = MagicMock()
        linter.available.return_value = True
        assert _linter_available(linter, "/tmp/proj") is True
        linter.available.assert_called_with(project_root="/tmp/proj")

    def test_falls_back_to_no_args_on_type_error(self):
        linter = MagicMock()
        linter.available.side_effect = [TypeError("bad kwarg"), True]
        assert _linter_available(linter, "/tmp/proj") is True

    def test_returns_false_when_unavailable(self):
        linter = MagicMock()
        linter.available.return_value = False
        assert _linter_available(linter, "/tmp/proj") is False


# ---------------------------------------------------------------------------
# _missing_tool_hints
# ---------------------------------------------------------------------------


class TestMissingToolHints:
    def test_empty_registry(self):
        assert _missing_tool_hints("/tmp/proj", {}) == []

    def test_available_tool_not_in_hints(self):
        linter = MagicMock()
        linter.required_tool = "ruff"
        linter.available.return_value = True
        hints = _missing_tool_hints("/tmp/proj", {"ruff_check": linter})
        assert hints == []

    def test_missing_tool_generates_hint(self, tmp_path):
        linter = MagicMock()
        linter.required_tool = "ty"
        linter.available.return_value = False

        # Create a venv so install commands use it
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text("#!/usr/bin/env python3")

        hints = _missing_tool_hints(str(tmp_path), {"ty_check": linter})
        assert len(hints) == 1
        assert hints[0]["tool"] == "ty"
        assert hints[0]["package"] == "ty"
        assert "ty_check" in hints[0]["required_by"]
        assert hints[0]["auto_installable"] is True
        assert str(py) in hints[0]["install_command"]

    def test_missing_tool_without_venv(self, tmp_path):
        linter = MagicMock()
        linter.required_tool = "ruff"
        linter.available.return_value = False
        hints = _missing_tool_hints(str(tmp_path), {"ruff_lint": linter})
        assert len(hints) == 1
        assert hints[0]["install_command"] == "pip install ruff"
        assert hints[0]["auto_installable"] is False

    def test_multiple_linters_same_tool_grouped(self, tmp_path):
        l1 = MagicMock()
        l1.required_tool = "ruff"
        l1.available.return_value = False
        l2 = MagicMock()
        l2.required_tool = "ruff"
        l2.available.return_value = False
        hints = _missing_tool_hints(str(tmp_path), {"ruff_check": l1, "ruff_format": l2})
        assert len(hints) == 1
        assert sorted(hints[0]["required_by"]) == ["ruff_check", "ruff_format"]
