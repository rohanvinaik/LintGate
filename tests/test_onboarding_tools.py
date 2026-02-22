"""Tests for mcp_tools/onboarding_tools.py.

Detailed helper tests live in test_onboarding_helpers.py. This file provides
the standard test_<module>.py entry point for test channel discovery
and covers core utility functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.onboarding_tools import (
    _format_cmd,
    _linter_available,
    _project_venv_python,
    _tool_package_name,
)

# ── _tool_package_name ──────────────────────────────────────────────────


def test_tool_package_name_known_tool() -> None:
    assert _tool_package_name("pip-audit") == "pip-audit"


def test_tool_package_name_unknown_tool() -> None:
    assert _tool_package_name("unknown-tool") == "unknown-tool"


# ── _project_venv_python ────────────────────────────────────────────────


def test_project_venv_python_finds_venv(tmp_path: Path) -> None:
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/usr/bin/env python")
    result = _project_venv_python(str(tmp_path))
    assert result is not None
    assert ".venv" in result


def test_project_venv_python_returns_none_when_missing(tmp_path: Path) -> None:
    result = _project_venv_python(str(tmp_path))
    assert result is None


# ── _format_cmd ─────────────────────────────────────────────────────────


def test_format_cmd_simple() -> None:
    assert _format_cmd(["python", "-m", "pytest"]) == "python -m pytest"


def test_format_cmd_quotes_spaces() -> None:
    result = _format_cmd(["python", "my file.py"])
    assert "my file.py" in result or "'my file.py'" in result


# ── _linter_available ───────────────────────────────────────────────────


def test_linter_available_with_project_root() -> None:
    linter = MagicMock()
    linter.available.return_value = True
    assert _linter_available(linter, "/tmp/test") is True
    linter.available.assert_called_once_with(project_root="/tmp/test")


def test_linter_available_fallback_no_kwargs() -> None:
    """Falls back to no-arg call when project_root kwarg not accepted."""
    linter = MagicMock()
    linter.available.side_effect = [TypeError("unexpected kwarg"), True]
    assert _linter_available(linter, "/tmp/test") is True
