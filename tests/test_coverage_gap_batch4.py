"""Tests targeting specific uncovered lines across multiple modules.

Modules covered:
- lintgate/linters/bandit_fast_linter.py (lines 60-61, 108, 138)
- lintgate/linters/bandit_linter.py (lines 47-48)
- mcp_tools/behavior_tools.py (lines 262-269, 272-273)
- mcp_tools/compass_tools.py (lines 455, 466)
- mcp_tools/onboarding_tools.py (lines 95, 111-112, 139-140, 343-344,
  361-362, 1886, 1925)
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

from lintgate.linters.bandit_fast_linter import BanditFastLinter
from lintgate.linters.bandit_fast_linter import (
    _is_test_or_docs_context as fast_is_test_or_docs,
)
from lintgate.linters.bandit_linter import (
    _is_test_or_docs_context as full_is_test_or_docs,
)
from lintgate.types import LinterContext
from mcp_tools.onboarding_tools import (
    _collect_external_tool_gaps,
    _ensure_project_venv,
    _generate_qlty_toml,
    _install_command_for_package,
    _readme_has_quality_badges,
    _scaffold_config_yaml,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bandit_json(results: list[dict]) -> str:
    return json.dumps({"results": results})


def _make_ctx(tmp_path, files=None, config=None) -> LinterContext:
    return LinterContext(
        files=files or [],
        project_root=str(tmp_path),
        strictness="normal",
        config=config or {},
    )


class _FakeMCP:
    """Minimal stand-in for the FastMCP instance used in register()."""

    def __init__(self):
        self._tools: dict[str, Any] = {}

    def tool(self):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn

        return decorator


def _make_helpers(tmp_path):
    def _validate_project_root(path: str) -> str:
        return os.path.abspath(path)

    def _json_dumps(data, **kw):
        return json.dumps(data)

    def _build_onboarding_status(project_root):
        return {"config_state": "config_enabled"}

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
        "_build_onboarding_status": _build_onboarding_status,
    }


# ===================================================================
# bandit_fast_linter: _is_test_or_docs_context ValueError (lines 60-61)
# ===================================================================


def test_fast_is_test_or_docs_relpath_value_error():
    """os.path.relpath raising ValueError returns False (lines 60-61)."""
    with patch("os.path.relpath", side_effect=ValueError("cross-drive")):
        result = fast_is_test_or_docs("/x/tests/foo.py", "/y/project")
    assert result is False


# ===================================================================
# bandit_linter: _is_test_or_docs_context ValueError (lines 47-48)
# ===================================================================


def test_full_is_test_or_docs_relpath_value_error():
    """os.path.relpath raising ValueError returns False (lines 47-48)."""
    with patch("os.path.relpath", side_effect=ValueError("cross-drive")):
        result = full_is_test_or_docs("/x/tests/foo.py", "/y/project")
    assert result is False


# ===================================================================
# bandit_fast_linter: BanditFastLinter.run extra_args (line 108)
# ===================================================================


def test_bandit_fast_linter_extra_args(tmp_path):
    """extra_args from config are appended to the command (line 108)."""
    ctx = _make_ctx(
        tmp_path,
        files=["a.py"],
        config={"extra_args": ["--severity-level", "high"]},
    )
    linter = BanditFastLinter()
    mock_result = MagicMock(stdout=_bandit_json([]))
    with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
        list(linter.run(ctx))
    cmd = mock_cmd.call_args[0][0]
    assert "--severity-level" in cmd
    assert "high" in cmd


# ===================================================================
# bandit_fast_linter: B105 suppressed in test dir (line 138)
# ===================================================================


def test_bandit_fast_linter_b105_suppressed_in_test_dir(tmp_path):
    """B105 in a test directory is suppressed by the fast linter (line 138)."""
    ctx = _make_ctx(tmp_path, files=["tests/test_auth.py"])
    linter = BanditFastLinter()
    data = _bandit_json(
        [
            {
                "test_id": "B105",
                "test_name": "hardcoded_password_string",
                "issue_text": "Possible hardcoded password",
                "issue_severity": "LOW",
                "issue_confidence": "MEDIUM",
                "filename": str(tmp_path / "tests" / "test_auth.py"),
                "line_number": 3,
            }
        ]
    )
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


# ===================================================================
# behavior_tools: constraint_check cold-start theory seeding (lines 262-269)
# ===================================================================


def test_constraint_check_theory_cold_start(tmp_path, monkeypatch):
    """When no relevant hypotheses exist on first check, theory seeds output (lines 262-269)."""
    monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

    config_dir = tmp_path / ".claude"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

    fake_profile = {
        "theory_profile": {
            "anti_patterns": [
                {"claims": ["Avoid global state", "Never use mutable defaults"]}
            ]
        }
    }
    monkeypatch.setattr(
        "lintgate.theory_extractor.extract_theory",
        lambda root: fake_profile,
    )

    from mcp_tools.behavior_tools import register

    mcp = _FakeMCP()
    helpers = _make_helpers(tmp_path)
    tools = register(mcp, helpers)

    result = json.loads(
        tools["constraint_check"](
            path=str(tmp_path),
            planned_action="make build",
        )
    )
    assert "theory_constraints" in result
    assert "Avoid global state" in result["theory_constraints"]
    assert "hint" in result


# ===================================================================
# behavior_tools: constraint_check theory extraction exception (lines 272-273)
# ===================================================================


def test_constraint_check_theory_extraction_exception(tmp_path, monkeypatch):
    """When extract_theory raises, it is silently caught (lines 272-273)."""
    monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

    config_dir = tmp_path / ".claude"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

    def _boom(root):
        raise RuntimeError("theory extraction failed")

    monkeypatch.setattr("lintgate.theory_extractor.extract_theory", _boom)

    from mcp_tools.behavior_tools import register

    mcp = _FakeMCP()
    helpers = _make_helpers(tmp_path)
    tools = register(mcp, helpers)

    result = json.loads(
        tools["constraint_check"](
            path=str(tmp_path),
            planned_action="make build",
        )
    )
    # Should succeed without theory_constraints
    assert "theory_constraints" not in result
    assert "constraint_ledger" in result


# ===================================================================
# compass_tools: compass_interview register wrapper (line 455)
# ===================================================================


def test_compass_interview_register_wrapper():
    """The registered compass_interview tool delegates to _impl_interview (line 455)."""
    from mcp_tools.compass_tools import register

    mcp = _FakeMCP()
    helpers = {
        "_json_dumps": json.dumps,
        "_validate_project_root": lambda p: p,
    }
    tools = register(mcp, helpers)

    with patch(
        "mcp_tools.compass_tools._impl_interview",
        return_value={"status": "skipped"},
    ) as mock_impl:
        raw = tools["compass_interview"](path="/p", answers=None, skip=True)
    mock_impl.assert_called_once_with("/p", "/p", None, True)
    assert json.loads(raw)["status"] == "skipped"


# ===================================================================
# compass_tools: compass_reset register wrapper (line 466)
# ===================================================================


def test_compass_reset_register_wrapper():
    """The registered compass_reset tool delegates to _impl_reset (line 466)."""
    from mcp_tools.compass_tools import register

    mcp = _FakeMCP()
    helpers = {
        "_json_dumps": json.dumps,
        "_validate_project_root": lambda p: p,
    }
    tools = register(mcp, helpers)

    with patch(
        "mcp_tools.compass_tools._impl_reset",
        return_value={"scope": "compass", "dry_run": True},
    ) as mock_impl:
        raw = tools["compass_reset"](path="/p", scope="compass", confirm=False)
    mock_impl.assert_called_once_with("/p", "/p", "compass", False)
    assert json.loads(raw)["scope"] == "compass"


# ===================================================================
# onboarding_tools: _ensure_project_venv — venv_created_but_python_missing (line 95)
# ===================================================================


def test_ensure_venv_created_but_python_missing(tmp_path):
    """After venv creation, if python binary is missing, returns error (line 95)."""
    mock_create = MagicMock(returncode=0, stderr="")
    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            side_effect=[None, None],
        ),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch("subprocess.run", return_value=mock_create),
    ):
        result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "error"
    assert result["reason"] == "venv_created_but_python_missing"


# ===================================================================
# onboarding_tools: _ensure_project_venv — pip check timeout (lines 111-112)
# ===================================================================


def test_ensure_venv_pip_check_timeout(tmp_path):
    """When pip --version times out, returns created with pip_ready=False (lines 111-112)."""
    mock_create = MagicMock(returncode=0, stderr="")
    venv_py = str(tmp_path / ".venv" / "bin" / "python")

    def _run_side_effect(cmd, **kwargs):
        if "-m" in cmd and "pip" in cmd and "--version" in cmd:
            raise subprocess.TimeoutExpired(cmd, 30)
        return mock_create

    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            side_effect=[None, venv_py],
        ),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch("subprocess.run", side_effect=_run_side_effect),
    ):
        result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "created"
    assert result["pip_ready"] is False
    assert result["pip_check"] == "timeout"


# ===================================================================
# onboarding_tools: _ensure_project_venv — ensurepip timeout (lines 139-140)
# ===================================================================


def test_ensure_venv_ensurepip_timeout(tmp_path):
    """When ensurepip times out, returns created with pip_bootstrap=timeout (lines 139-140)."""
    venv_py = str(tmp_path / ".venv" / "bin" / "python")
    mock_create = MagicMock(returncode=0, stderr="")
    mock_pip_fail = MagicMock(returncode=1, stderr="no pip")

    call_count = 0

    def _run_side_effect(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # venv creation succeeds
            return mock_create
        if call_count == 2:
            # pip --version fails
            return mock_pip_fail
        # ensurepip times out
        raise subprocess.TimeoutExpired(cmd, 90)

    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            side_effect=[None, venv_py],
        ),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch("subprocess.run", side_effect=_run_side_effect),
    ):
        result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "created"
    assert result["pip_ready"] is False
    assert result["pip_bootstrap"] == "timeout"


# ===================================================================
# onboarding_tools: _scaffold_config_yaml — OSError on file read (lines 343-344)
# ===================================================================


def test_scaffold_config_yaml_oserror_on_large_file_check(tmp_path):
    """OSError reading a .py file for line count is skipped (lines 343-344)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    py_file = src_dir / "module.py"
    py_file.write_text("x = 1\n")

    helpers = _make_helpers(tmp_path)
    original_open = open

    def _patched_open(path, *args, **kwargs):
        if str(path) == str(py_file):
            raise OSError("permission denied")
        return original_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=_patched_open):
        result = _scaffold_config_yaml(str(tmp_path), helpers)
    # Should complete without raising
    assert "controlplane:" in result


# ===================================================================
# onboarding_tools: _scaffold_config_yaml — OSError reading content (lines 361-362)
# ===================================================================


def test_scaffold_config_yaml_oserror_on_subprocess_check(tmp_path):
    """OSError reading a .py file for subprocess check is skipped (lines 361-362)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    # Create a file with > 300 lines so it passes the critical-path check,
    # then fails on the subprocess content read
    big_file = src_dir / "big_module.py"
    big_file.write_text("x = 1\n" * 301)

    helpers = _make_helpers(tmp_path)
    original_open = open
    call_count = {"big": 0}

    def _patched_open(path, *args, **kwargs):
        if str(path) == str(big_file):
            call_count["big"] += 1
            if call_count["big"] == 1:
                # First call: line-count check — allow it
                return original_open(path, *args, **kwargs)
            # Second call: subprocess content read — fail
            raise OSError("permission denied")
        return original_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=_patched_open):
        result = _scaffold_config_yaml(str(tmp_path), helpers)
    assert "controlplane:" in result


# ===================================================================
# onboarding_tools: _readme_has_quality_badges — end == -1 (line 1886)
# ===================================================================


def test_readme_has_quality_badges_malformed_block(tmp_path):
    """When BADGE_BLOCK_START present but END not found after START, returns False (line 1886)."""
    readme = tmp_path / "README.md"
    # Put END before START so find(END, start_of_START) returns -1
    content = (
        "<!-- lintgate:quality-badges:end -->\n"
        "some content\n"
        "<!-- lintgate:quality-badges:start -->\n"
    )
    readme.write_text(content)
    assert _readme_has_quality_badges(str(tmp_path)) is False


# ===================================================================
# onboarding_tools: _generate_qlty_toml — empty pattern skipped (line 1925)
# ===================================================================


def test_generate_qlty_toml_empty_exclude_pattern():
    """Empty/whitespace-only exclude patterns are skipped (line 1925)."""
    layout = {
        "test_dirs": ["tests"],
        "exclude_patterns": ["", "  ", "valid_dir"],
    }
    result = _generate_qlty_toml(layout)
    # Empty patterns should not appear; valid_dir should be present
    assert "valid_dir/**" in result
    # The toml output should be well-formed
    assert "[config]" in result or "exclude_patterns" in result


# ===================================================================
# onboarding_tools: _ensure_project_venv — ensurepip completes (149)
# ===================================================================


def test_ensure_venv_ensurepip_completes():
    """Line 149: ensurepip runs and returns (non-timeout)."""
    create_result = subprocess.CompletedProcess([], 0)
    pip_fail = subprocess.CompletedProcess([], 1)
    ensurepip_ok = subprocess.CompletedProcess(
        [], 0, stderr="",
    )

    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            side_effect=[None, "/venv/bin/python"],
        ),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch(
            "subprocess.run",
            side_effect=[create_result, pip_fail, ensurepip_ok],
        ),
    ):
        result = _ensure_project_venv("/tmp/proj")
    assert result["status"] == "created"
    assert result["pip_bootstrap_returncode"] == 0
    assert result["pip_ready"] is True


# ===================================================================
# onboarding_tools: _install_command_for_package (lines 177-178)
# ===================================================================


def test_install_command_for_package_returns_first():
    """Lines 177-178: returns first command from plural helper."""
    with patch(
        "mcp_tools.onboarding_tools._project_venv_python",
        return_value="/venv/bin/python",
    ):
        cmd = _install_command_for_package("/tmp/proj", "ruff")
    assert cmd is not None
    assert "ruff" in cmd


def test_install_command_for_package_no_venv():
    """Lines 177-178: returns None when no venv exists."""
    with patch(
        "mcp_tools.onboarding_tools._project_venv_python",
        return_value=None,
    ):
        cmd = _install_command_for_package("/tmp/proj", "ruff")
    assert cmd is None


# ===================================================================
# onboarding_tools: _collect_external_tool_gaps (lines 211-213)
# ===================================================================


def test_collect_external_tool_gaps_missing_tool():
    """Lines 211-213: missing tool builds install command."""
    mock_linter = MagicMock()
    mock_linter.required_tool = "ruff"

    registry = {"ruff_linter": mock_linter}

    with (
        patch("lintgate.config.load_config"),
        patch(
            "lintgate.registry.build_registry",
            return_value=registry,
        ),
        patch(
            "mcp_tools.onboarding_tools._linter_available",
            return_value=False,
        ),
        patch(
            "mcp_tools.onboarding_tools._install_command_for_package",
            return_value=["/venv/bin/python", "-m", "pip", "install", "ruff"],
        ),
    ):
        result = _collect_external_tool_gaps("/tmp/proj")
    assert len(result["missing_tools"]) == 1
    assert result["missing_tools"][0]["tool"] == "ruff"
    assert "pip install" in result["missing_tools"][0]["install_command"]
