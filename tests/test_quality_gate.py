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
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=None),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("No quality run" in m for m in result.messages)

    def test_blocks_when_stale(self, tmp_path) -> None:
        old_state = _fresh_state(timestamp=time.time() - 3600)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=old_state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("old" in m for m in result.messages)

    def test_blocks_when_blocking_issues(self, tmp_path) -> None:
        state = _fresh_state(blocking_issues=3)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("blocking" in m for m in result.messages)

    def test_blocks_with_symbol_remediation_loop_message(self, tmp_path) -> None:
        state = _fresh_state(blocking_issues=2, symbol_coverage_blockers=2)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("Symbol coverage remediation loop required" in m for m in result.messages)

    def test_blocks_when_tests_failing(self, tmp_path) -> None:
        state = _fresh_state(last_test_status="fail")
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
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
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                return_value=[fake_finding],
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("secret" in m for m in result.messages)

    def test_allows_when_all_clear(self, tmp_path) -> None:
        state = _fresh_state()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []

    def test_empty_test_status_is_ok(self, tmp_path) -> None:
        state = _fresh_state(last_test_status="")
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
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
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git commit -m 'test'", str(tmp_path))
        assert result.should_block is False
        assert any("Advisory" in m for m in result.messages)

    def test_advises_when_tests_failing(self, tmp_path) -> None:
        state = _fresh_state(last_test_status="fail")
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git commit", str(tmp_path))
        assert result.should_block is False
        assert len(result.messages) > 0

    def test_no_advisory_when_no_state(self, tmp_path) -> None:
        """Missing state is not actionable for commit — skip."""
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
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
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False

    def test_non_git_command(self, tmp_path) -> None:
        result = _check_quality_gate("ls -la", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []

    def test_push_disabled(self, tmp_path) -> None:
        """block_push=False → gate passes even with blocking issues."""
        state = _fresh_state(blocking_issues=5)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(block_push=False),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is False

    def test_commit_disabled(self, tmp_path) -> None:
        """advise_commit=False → no advisory on commit."""
        state = _fresh_state(blocking_issues=5)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(advise_commit=False),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
        ):
            result = _check_quality_gate("git commit -m 'test'", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []

    def test_runtime_state_crash(self, tmp_path) -> None:
        """Load state crash → state=None → blocks push (no quality run)."""
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch(
                "lintgate.hooks.pre_tool.load_runtime_state",
                side_effect=RuntimeError("disk error"),
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("No quality run" in m for m in result.messages)


# ── Integration: handle() ────────────────────────────────────────────


class TestQualityGateIntegration:
    def test_handle_blocks_push(self, tmp_path) -> None:
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=None),
        ):
            result = handle(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git push"},
                    "cwd": str(tmp_path),
                }
            )
        assert result["continue"] is False
        assert "BLOCKED" in result["systemMessage"]

    def test_handle_advises_commit(self, tmp_path) -> None:
        state = _fresh_state(blocking_issues=1)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
        ):
            result = handle(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git commit -m 'test'"},
                    "cwd": str(tmp_path),
                }
            )
        assert result["continue"] is True
        assert "Advisory" in result["systemMessage"]

    def test_handle_allows_clean_push(self, tmp_path) -> None:
        state = _fresh_state()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
        ):
            result = handle(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git push"},
                    "cwd": str(tmp_path),
                }
            )
        assert result["continue"] is True

    def test_handle_non_bash_unaffected(self) -> None:
        result = handle({"tool_name": "Read", "cwd": "/tmp"})
        assert result["continue"] is True

    def test_handle_empty_input(self) -> None:
        result = handle({})
        assert result["continue"] is True


# ── _check_quality_gate — missing branch coverage ────────────────────


class TestQualityGateBranchCoverage:
    """Cover remaining branch paths in _check_quality_gate."""

    def test_check_secrets_disabled_skips_scan(self, tmp_path) -> None:
        """check_secrets=False skips the secrets scan entirely (line 197 false branch)."""
        state = _fresh_state(blocking_issues=1)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(check_secrets=False),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            # _check_diff_secrets should NOT be called; if it were, this would fail
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                side_effect=AssertionError("should not be called"),
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        # Should still block due to blocking issues, but no secrets message
        assert result.should_block is True
        assert not any("secret" in m for m in result.messages)
        assert any("blocking" in m for m in result.messages)

    def test_stale_state_commit_advisory(self, tmp_path) -> None:
        """Stale state on commit produces advisory (not block)."""
        old_state = _fresh_state(timestamp=time.time() - 3600)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=old_state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git commit -m 'test'", str(tmp_path))
        assert result.should_block is False
        assert any("Advisory" in m for m in result.messages)
        assert any("old" in m for m in result.messages)

    def test_no_blocking_issues_but_tests_fail_push(self, tmp_path) -> None:
        """blocking_issues=0, tests failing => blocks push (line 178 false, 193 true)."""
        state = _fresh_state(blocking_issues=0, last_test_status="fail")
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("Tests" in m for m in result.messages)
        # No blocking-issue message present
        assert not any("blocking issue" in m for m in result.messages)

    def test_multiple_failures_combined(self, tmp_path) -> None:
        """Multiple failure conditions produce multiple messages."""
        old_state = _fresh_state(
            timestamp=time.time() - 7200,
            blocking_issues=2,
            last_test_status="fail",
        )
        fake_finding = type("LintIssue", (), {"message": "secret found"})()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=old_state),
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                return_value=[fake_finding],
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        # At least 4 messages: stale, blocking issues, resolve blockers, tests, secrets
        assert len(result.messages) >= 4

    def test_blocking_issues_zero_symbol_blockers_resolve_message(self, tmp_path) -> None:
        """blocking_issues > 0, symbol_blockers=0 => 'Resolve blockers' message (line 190)."""
        state = _fresh_state(blocking_issues=3)
        # Ensure symbol_coverage_blockers is explicitly 0
        state.symbol_coverage_blockers = 0
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        assert any("Resolve blockers" in m for m in result.messages)
        assert not any("Symbol coverage remediation" in m for m in result.messages)

    def test_fresh_state_no_failures_commit(self, tmp_path) -> None:
        """Clean state on commit produces no messages (line 207 true branch)."""
        state = _fresh_state()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git commit -m 'clean'", str(tmp_path))
        assert result.should_block is False
        assert result.messages == []

    def test_symbol_blockers_singular_message(self, tmp_path) -> None:
        """symbol_coverage_blockers=1 uses singular form in message."""
        state = _fresh_state(blocking_issues=1, symbol_coverage_blockers=1)
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch("lintgate.hooks.pre_tool.load_runtime_state", return_value=state),
            patch("lintgate.hooks.pre_tool._check_diff_secrets", return_value=[]),
        ):
            result = _check_quality_gate("git push", str(tmp_path))
        assert result.should_block is True
        # Check singular form: "1 uncovered symbol blocker" (no trailing 's')
        remediation_msgs = [m for m in result.messages if "Symbol coverage" in m]
        assert len(remediation_msgs) == 1
        assert "1 uncovered symbol blocker." in remediation_msgs[0]
