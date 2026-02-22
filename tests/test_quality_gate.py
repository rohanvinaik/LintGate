"""Tests for the quality gate in PreToolUse hooks."""

from __future__ import annotations

import time
from unittest.mock import patch

from lintgate.hooks.pre_tool import (
    _check_quality_gate,
    _classify_git_command,
    _extract_bash_command,
    handle,
)
from lintgate.runtime_state import RuntimeState

# ── _classify_git_command ────────────────────────────────────────────


class TestClassifyGitCommand:
    def test_push(self) -> None:
        assert _classify_git_command("git push origin main") == "push"

    def test_push_simple(self) -> None:
        assert _classify_git_command("git push") == "push"

    def test_commit(self) -> None:
        assert _classify_git_command('git commit -m "msg"') == "commit"

    def test_commit_simple(self) -> None:
        assert _classify_git_command("git commit") == "commit"

    def test_non_git(self) -> None:
        assert _classify_git_command("ls -la") is None

    def test_git_status(self) -> None:
        assert _classify_git_command("git status") is None

    def test_chained_commit_then_push(self) -> None:
        """Chained commands with push classify as push (push checked first)."""
        assert _classify_git_command('git commit -m "msg" && git push') == "push"

    def test_git_diff(self) -> None:
        assert _classify_git_command("git diff --cached") is None


# ── _extract_bash_command ────────────────────────────────────────────


class TestExtractBashCommand:
    def test_dict_input(self) -> None:
        data = {"tool_input": {"command": "git push"}}
        assert _extract_bash_command(data) == "git push"

    def test_string_input(self) -> None:
        data = {"tool_input": "git push"}
        assert _extract_bash_command(data) == "git push"

    def test_empty_input(self) -> None:
        assert _extract_bash_command({}) == ""

    def test_fallback_key(self) -> None:
        data = {"input": {"command": "git push"}}
        assert _extract_bash_command(data) == "git push"

    def test_none_tool_input(self) -> None:
        data = {"tool_input": None}
        assert _extract_bash_command(data) == ""


# ── _check_quality_gate — push blocking ──────────────────────────────


def _make_config(enabled: bool = True, **overrides):
    """Build a QualityGateConfig-bearing ControlPlaneConfig."""
    from lintgate.controlplane.types import ControlPlaneConfig, QualityGateConfig

    qg_kwargs = {
        "enabled": enabled,
        "staleness_threshold_s": 1800.0,
        "block_push": True,
        "advise_commit": True,
        "check_secrets": True,
    }
    qg_kwargs.update(overrides)
    cp = ControlPlaneConfig(enabled=True, quality_gate=QualityGateConfig(**qg_kwargs))
    return cp


def _fresh_state(**overrides) -> RuntimeState:
    """Build a RuntimeState that passes all checks."""
    defaults = {
        "timestamp": time.time(),
        "blocking_issues": 0,
        "last_test_status": "",
    }
    defaults.update(overrides)
    return RuntimeState(**defaults)


class TestQualityGatePush:
    """Push should be blocked on known-bad state."""

    def test_blocks_when_no_state(self, tmp_path) -> None:
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=None),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("No quality run" in m for m in result.messages)

    def test_blocks_when_stale(self, tmp_path) -> None:
        old_state = _fresh_state(timestamp=time.time() - 3600)
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=old_state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("old" in m for m in result.messages)

    def test_blocks_when_blocking_issues(self, tmp_path) -> None:
        state = _fresh_state(blocking_issues=3)
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("blocking" in m for m in result.messages)

    def test_blocks_when_tests_failing(self, tmp_path) -> None:
        state = _fresh_state(last_test_status="fail")
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("Tests" in m for m in result.messages)

    def test_blocks_when_secrets_found(self, tmp_path) -> None:
        state = _fresh_state()
        fake_finding = type("LintIssue", (), {"message": "secret found"})()
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[fake_finding]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("secret" in m for m in result.messages)

    def test_allows_when_all_clear(self, tmp_path) -> None:
        state = _fresh_state()
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []

    def test_empty_test_status_is_ok(self, tmp_path) -> None:
        state = _fresh_state(last_test_status="")
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False


# ── _check_quality_gate — commit advisory ────────────────────────────


class TestQualityGateCommit:
    """Commit should advise but never block."""

    def test_advises_when_blocking_issues(self, tmp_path) -> None:
        state = _fresh_state(blocking_issues=2)
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git commit -m 'test'", str(tmp_path))
        assert result.should_block is False
        assert any("Advisory" in m for m in result.messages)

    def test_advises_when_tests_failing(self, tmp_path) -> None:
        state = _fresh_state(last_test_status="fail")
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git commit", str(tmp_path))
        assert result.should_block is False
        assert len(result.messages) > 0

    def test_no_advisory_when_no_state(self, tmp_path) -> None:
        """Missing state is not actionable for commit — skip."""
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=None),
        ):
            result = _check_quality_gate("git commit -m 'test'", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []


# ── Fail-open behavior ───────────────────────────────────────────────


class TestQualityGateFailOpen:
    def test_no_config(self, tmp_path) -> None:
        with patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=None):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False

    def test_gate_disabled(self, tmp_path) -> None:
        with patch(
            "lintgate.hooks.pre_tool.load_controlplane_config",
            return_value=_make_config(enabled=False),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False

    def test_config_crash(self, tmp_path) -> None:
        with patch(
            "lintgate.hooks.pre_tool.load_controlplane_config",
            side_effect=RuntimeError("boom"),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False

    def test_secrets_crash(self, tmp_path) -> None:
        """Secrets check crash should fail-open, not block push."""
        state = _fresh_state()
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", side_effect=RuntimeError("boom")),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False

    def test_non_git_command(self, tmp_path) -> None:
        result = _check_quality_gate("ls -la", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []


# ── Integration: handle() ────────────────────────────────────────────


class TestQualityGateIntegration:
    def test_handle_blocks_push(self, tmp_path) -> None:
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=None),
        ):
            result = handle({
                "tool_name": "Bash",
                "tool_input": {"command": "git push"},
                "cwd": str(tmp_path),
            })
        assert result["continue"] is False
        assert "BLOCKED" in result["systemMessage"]

    def test_handle_advises_commit(self, tmp_path) -> None:
        state = _fresh_state(blocking_issues=1)
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
        ):
            result = handle({
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m 'test'"},
                "cwd": str(tmp_path),
            })
        assert result["continue"] is True
        assert "Advisory" in result["systemMessage"]

    def test_handle_allows_clean_push(self, tmp_path) -> None:
        state = _fresh_state()
        with (
            patch("lintgate.hooks.pre_tool.load_controlplane_config", return_value=_make_config()),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
        ):
            result = handle({
                "tool_name": "Bash",
                "tool_input": {"command": "git push"},
                "cwd": str(tmp_path),
            })
        assert result["continue"] is True

    def test_handle_non_bash_unaffected(self) -> None:
        result = handle({"tool_name": "Read", "cwd": "/tmp"})
        assert result["continue"] is True

    def test_handle_empty_input(self) -> None:
        result = handle({})
        assert result["continue"] is True
