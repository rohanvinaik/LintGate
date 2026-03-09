"""Tests for lintgate/hooks/pre_tool.py.

Core hook helper tests live in test_hook_helpers.py. This file provides
the standard test_<module>.py entry point for test channel discovery.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lintgate.hooks.pre_tool import (
    _COMMIT_RE,
    _PUSH_RE,
    _WRITE_TOOLS,
    _check_bash_alignment,
    _check_theory_mode,
    _classify_git_command,
    _collect_state_failures,
    _extract_bash_command,
    _load_mode,
)

# ── Constants ────────────────────────────────────────────────────────────


def test_write_tools_contains_expected() -> None:
    assert "Write" in _WRITE_TOOLS
    assert "Edit" in _WRITE_TOOLS
    assert "NotebookEdit" in _WRITE_TOOLS


def test_write_tools_exact_membership() -> None:
    """Pin the full set so additions are caught."""
    assert frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"}) == _WRITE_TOOLS


def test_push_re_matches_git_push() -> None:
    assert _PUSH_RE.search("git push origin main")
    assert not _PUSH_RE.search("git commit -m 'msg'")


def test_commit_re_matches_git_commit() -> None:
    assert _COMMIT_RE.search("git commit -m 'msg'")
    assert not _COMMIT_RE.search("git push origin main")


# ── _load_mode ───────────────────────────────────────────────────────────


def test_load_mode_returns_normal_on_import_error() -> None:
    with patch(
        "lintgate.hooks.pre_tool.get_or_create_session",
        side_effect=ImportError("no module"),
        create=True,
    ):
        # Force re-import path to hit the except branch
        result = _load_mode("/tmp/test")
    assert result == "normal"


def test_load_mode_returns_normal_on_exception() -> None:
    with patch(
        "lintgate.hooks.pre_tool.get_or_create_session",
        side_effect=RuntimeError("broken"),
        create=True,
    ):
        result = _load_mode("/tmp/test")
    assert result == "normal"


def test_load_mode_returns_exact_mode_from_session() -> None:
    """When session memory has a mode, _load_mode returns that exact string."""
    mock_session = SimpleNamespace(
        behavior_compass={"mode_state": {"current": "theory"}}
    )
    with patch(
        "lintgate.controlplane.session_memory.get_or_create_session",
        return_value=mock_session,
    ):
        result = _load_mode("/tmp/test")
    assert result == "theory"


def test_load_mode_returns_normal_when_mode_is_none() -> None:
    """When current mode is None, _load_mode coerces to 'normal'."""
    mock_session = SimpleNamespace(
        behavior_compass={"mode_state": {"current": None}}
    )
    with patch(
        "lintgate.controlplane.session_memory.get_or_create_session",
        return_value=mock_session,
    ):
        result = _load_mode("/tmp/test")
    assert result == "normal"


def test_load_mode_returns_normal_when_mode_state_missing() -> None:
    """When mode_state key is absent, _load_mode falls back to 'normal'."""
    mock_session = SimpleNamespace(behavior_compass={})
    with patch(
        "lintgate.controlplane.session_memory.get_or_create_session",
        return_value=mock_session,
    ):
        result = _load_mode("/tmp/test")
    assert result == "normal"


def test_load_mode_returns_habit_mode() -> None:
    """Verify habit mode string is returned verbatim."""
    mock_session = SimpleNamespace(
        behavior_compass={"mode_state": {"current": "habit"}}
    )
    with patch(
        "lintgate.controlplane.session_memory.get_or_create_session",
        return_value=mock_session,
    ):
        result = _load_mode("/tmp/test")
    assert result == "habit"


# ── _check_theory_mode ──────────────────────────────────────────────────


def test_theory_mode_advisory_on_write_tool_exact_message() -> None:
    """Pin the exact advisory string for theory+Write."""
    result = _check_theory_mode("theory", "Write")
    assert result == (
        "[Compass] Theory mode active — consider reading more"
        " before writing. Use `theory_mode_freeze` when ready."
    )


def test_theory_mode_advisory_on_edit_tool_exact_message() -> None:
    """Edit is also a write tool — same exact message."""
    result = _check_theory_mode("theory", "Edit")
    assert result == (
        "[Compass] Theory mode active — consider reading more"
        " before writing. Use `theory_mode_freeze` when ready."
    )


def test_theory_mode_advisory_on_multiedit_tool() -> None:
    """MultiEdit is a write tool — produces the advisory."""
    result = _check_theory_mode("theory", "MultiEdit")
    assert result == (
        "[Compass] Theory mode active — consider reading more"
        " before writing. Use `theory_mode_freeze` when ready."
    )


def test_theory_mode_advisory_on_notebook_edit_tool() -> None:
    """NotebookEdit is a write tool — produces the advisory."""
    result = _check_theory_mode("theory", "NotebookEdit")
    assert result == (
        "[Compass] Theory mode active — consider reading more"
        " before writing. Use `theory_mode_freeze` when ready."
    )


def test_theory_mode_no_advisory_on_read_tool() -> None:
    result = _check_theory_mode("theory", "Read")
    assert result == ""


def test_theory_mode_no_advisory_on_bash_tool() -> None:
    """Bash is not a write tool — no advisory in theory mode."""
    result = _check_theory_mode("theory", "Bash")
    assert result == ""


def test_normal_mode_no_advisory_on_write_tool() -> None:
    result = _check_theory_mode("normal", "Write")
    assert result == ""


def test_habit_mode_no_advisory_on_write_tool() -> None:
    """Habit mode is not theory — no advisory."""
    result = _check_theory_mode("habit", "Write")
    assert result == ""


# ── _extract_bash_command ────────────────────────────────────────────────


class TestExtractBashCommand:
    """Exact-value tests for _extract_bash_command."""

    def test_dict_tool_input_with_command(self) -> None:
        data = {"tool_input": {"command": "git status"}}
        assert _extract_bash_command(data) == "git status"

    def test_dict_tool_input_empty_command(self) -> None:
        data = {"tool_input": {"command": ""}}
        assert _extract_bash_command(data) == ""

    def test_dict_tool_input_no_command_key(self) -> None:
        data = {"tool_input": {"other_key": "value"}}
        assert _extract_bash_command(data) == ""

    def test_empty_data(self) -> None:
        assert _extract_bash_command({}) == ""

    def test_string_tool_input(self) -> None:
        """When tool_input is a raw string, return it verbatim."""
        data = {"tool_input": "echo hello"}
        assert _extract_bash_command(data) == "echo hello"

    def test_non_dict_non_string_tool_input(self) -> None:
        """When tool_input is neither dict nor string, return empty."""
        data = {"tool_input": 42}
        assert _extract_bash_command(data) == ""

    def test_input_fallback_key(self) -> None:
        """Falls back to 'input' key when 'tool_input' is absent."""
        data = {"input": {"command": "ls -la"}}
        assert _extract_bash_command(data) == "ls -la"

    def test_tool_input_takes_precedence_over_input(self) -> None:
        """'tool_input' is checked first, 'input' is the fallback."""
        data = {
            "tool_input": {"command": "git diff"},
            "input": {"command": "git log"},
        }
        assert _extract_bash_command(data) == "git diff"

    def test_complex_command_preserved(self) -> None:
        """Multi-part commands are returned as-is."""
        cmd = "cd /tmp && git status && echo done"
        data = {"tool_input": {"command": cmd}}
        assert _extract_bash_command(data) == cmd

    def test_none_tool_input(self) -> None:
        """When tool_input is None, falls back to 'input' key then empty dict."""
        data = {"tool_input": None}
        assert _extract_bash_command(data) == ""


# ── _classify_git_command ────────────────────────────────────────────────


class TestClassifyGitCommand:
    """Exact-value tests for _classify_git_command."""

    def test_git_push_returns_push(self) -> None:
        assert _classify_git_command("git push") == "push"

    def test_git_push_with_remote_returns_push(self) -> None:
        assert _classify_git_command("git push origin main") == "push"

    def test_git_push_force_returns_push(self) -> None:
        assert _classify_git_command("git push --force") == "push"

    def test_git_commit_returns_commit(self) -> None:
        assert _classify_git_command("git commit -m 'msg'") == "commit"

    def test_git_commit_amend_returns_commit(self) -> None:
        assert _classify_git_command("git commit --amend") == "commit"

    def test_git_status_returns_none(self) -> None:
        assert _classify_git_command("git status") is None

    def test_git_diff_returns_none(self) -> None:
        assert _classify_git_command("git diff") is None

    def test_non_git_command_returns_none(self) -> None:
        assert _classify_git_command("echo hello") is None

    def test_empty_command_returns_none(self) -> None:
        assert _classify_git_command("") is None

    def test_chained_commit_then_push_returns_push(self) -> None:
        """Push is checked first — chained 'commit && push' classifies as push."""
        assert _classify_git_command("git commit -m 'msg' && git push") == "push"

    def test_push_checked_before_commit(self) -> None:
        """Even with commit first in string, push regex wins."""
        assert _classify_git_command("git commit && git push origin main") == "push"

    def test_git_log_returns_none(self) -> None:
        assert _classify_git_command("git log --oneline") is None

    def test_git_add_returns_none(self) -> None:
        assert _classify_git_command("git add .") is None

    def test_partial_word_push_not_matched(self) -> None:
        r"""'git pushing' should not match \bgit\s+push\b."""
        assert _classify_git_command("git pushing") is None

    def test_partial_word_commit_not_matched(self) -> None:
        r"""'git committing' should not match \bgit\s+commit\b."""
        assert _classify_git_command("git committing") is None


# ── _check_bash_alignment ────────────────────────────────────────────────


class TestCheckBashAlignment:
    """Exact-value tests for _check_bash_alignment with mock compass."""

    def test_empty_command_returns_empty(self) -> None:
        """No command → no alignment check."""
        compass = MagicMock()
        assert _check_bash_alignment({}, compass) == ""
        compass.check_alignment.assert_not_called()

    def test_violation_returns_exact_prefix(self) -> None:
        compass = MagicMock()
        compass.check_alignment.return_value = {
            "violations": ["forbidden: rm -rf /"],
            "warnings": [],
        }
        data = {"tool_input": {"command": "rm -rf /"}}
        result = _check_bash_alignment(data, compass)
        assert result == "[Compass] Alignment violation: forbidden: rm -rf /"

    def test_warning_returns_exact_prefix(self) -> None:
        compass = MagicMock()
        compass.check_alignment.return_value = {
            "violations": [],
            "warnings": ["away: prefer grep over find"],
        }
        data = {"tool_input": {"command": "find . -name '*.py'"}}
        result = _check_bash_alignment(data, compass)
        assert result == "[Compass] Alignment warning: away: prefer grep over find"

    def test_no_violations_no_warnings_returns_empty(self) -> None:
        compass = MagicMock()
        compass.check_alignment.return_value = {
            "violations": [],
            "warnings": [],
        }
        data = {"tool_input": {"command": "git status"}}
        result = _check_bash_alignment(data, compass)
        assert result == ""

    def test_violation_takes_precedence_over_warning(self) -> None:
        """When both violations and warnings exist, violation is returned."""
        compass = MagicMock()
        compass.check_alignment.return_value = {
            "violations": ["forbidden: dangerous"],
            "warnings": ["away: suboptimal"],
        }
        data = {"tool_input": {"command": "dangerous-cmd"}}
        result = _check_bash_alignment(data, compass)
        assert result == "[Compass] Alignment violation: forbidden: dangerous"

    def test_only_first_violation_reported(self) -> None:
        """Multiple violations → only the first is surfaced."""
        compass = MagicMock()
        compass.check_alignment.return_value = {
            "violations": ["first violation", "second violation"],
            "warnings": [],
        }
        data = {"tool_input": {"command": "bad-cmd"}}
        result = _check_bash_alignment(data, compass)
        assert result == "[Compass] Alignment violation: first violation"

    def test_only_first_warning_reported(self) -> None:
        """Multiple warnings → only the first is surfaced."""
        compass = MagicMock()
        compass.check_alignment.return_value = {
            "violations": [],
            "warnings": ["first warning", "second warning"],
        }
        data = {"tool_input": {"command": "suboptimal-cmd"}}
        result = _check_bash_alignment(data, compass)
        assert result == "[Compass] Alignment warning: first warning"

    def test_compass_receives_exact_command(self) -> None:
        """Verify the extracted command is passed to check_alignment."""
        compass = MagicMock()
        compass.check_alignment.return_value = {"violations": [], "warnings": []}
        data = {"tool_input": {"command": "python -m pytest tests/"}}
        _check_bash_alignment(data, compass)
        compass.check_alignment.assert_called_once_with("python -m pytest tests/")


# ── _collect_state_failures ──────────────────────────────────────────────


class TestCollectStateFailures:
    """Exact-value tests for _collect_state_failures."""

    def _make_qg(self, **overrides):
        defaults = {
            "staleness_threshold_s": 1800.0,
            "check_secrets": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_state(self, **overrides):
        defaults = {
            "timestamp": time.time(),
            "blocking_issues": 0,
            "last_test_status": "",
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_fresh_no_issues_returns_empty(self) -> None:
        state = self._make_state()
        qg = self._make_qg()
        result = _collect_state_failures(state, qg, "/tmp")
        assert result == []

    def test_stale_state_reports_exact_message(self) -> None:
        state = self._make_state(timestamp=time.time() - 3600)
        qg = self._make_qg(staleness_threshold_s=1800.0)
        result = _collect_state_failures(state, qg, "/tmp")
        assert len(result) == 1
        assert "60min old" in result[0]
        assert "threshold: 30min" in result[0]

    def test_blocking_issues_reports_count(self) -> None:
        state = self._make_state(blocking_issues=3)
        qg = self._make_qg()
        result = _collect_state_failures(state, qg, "/tmp")
        assert "3 blocking issue(s) remain." in result
        assert "Resolve blockers, then rerun `controlplane_run` before pushing." in result

    def test_symbol_coverage_blockers_exact_message(self) -> None:
        state = self._make_state(blocking_issues=2, symbol_coverage_blockers=2)
        qg = self._make_qg()
        result = _collect_state_failures(state, qg, "/tmp")
        assert any("Symbol coverage remediation loop required" in f for f in result)
        assert any("2 uncovered symbol blockers" in f for f in result)

    def test_single_symbol_blocker_no_plural_s(self) -> None:
        state = self._make_state(blocking_issues=1, symbol_coverage_blockers=1)
        qg = self._make_qg()
        result = _collect_state_failures(state, qg, "/tmp")
        assert any("1 uncovered symbol blocker." in f for f in result)

    def test_failing_tests_reports_exact_message(self) -> None:
        state = self._make_state(last_test_status="fail")
        qg = self._make_qg()
        result = _collect_state_failures(state, qg, "/tmp")
        assert "Tests are failing." in result

    def test_passing_tests_no_failure(self) -> None:
        state = self._make_state(last_test_status="pass")
        qg = self._make_qg()
        result = _collect_state_failures(state, qg, "/tmp")
        assert result == []

    def test_secrets_detected_reports_count(self) -> None:
        state = self._make_state()
        qg = self._make_qg(check_secrets=True)
        with patch(
            "lintgate.hooks.pre_tool._check_diff_secrets",
            return_value=["secret1", "secret2"],
        ):
            result = _collect_state_failures(state, qg, "/tmp")
        assert "2 secret(s) detected in staged diff." in result

    def test_secrets_check_crash_fail_open(self) -> None:
        """Secrets check crash is suppressed — fail-open."""
        state = self._make_state()
        qg = self._make_qg(check_secrets=True)
        with patch(
            "lintgate.hooks.pre_tool._check_diff_secrets",
            side_effect=RuntimeError("crash"),
        ):
            result = _collect_state_failures(state, qg, "/tmp")
        assert result == []

    def test_secrets_disabled_skips_check(self) -> None:
        state = self._make_state()
        qg = self._make_qg(check_secrets=False)
        with patch(
            "lintgate.hooks.pre_tool._check_diff_secrets",
        ) as mock_check:
            result = _collect_state_failures(state, qg, "/tmp")
        mock_check.assert_not_called()
        assert result == []

    def test_multiple_failures_accumulate(self) -> None:
        """Stale + blocking + failing tests all appear."""
        state = self._make_state(
            timestamp=time.time() - 3600,
            blocking_issues=1,
            last_test_status="fail",
        )
        qg = self._make_qg(staleness_threshold_s=1800.0)
        result = _collect_state_failures(state, qg, "/tmp")
        assert len(result) >= 3
        assert any("old" in f for f in result)
        assert any("blocking" in f for f in result)
        assert any("Tests are failing" in f for f in result)


# ── load_controlplane_config / load_runtime_state / load_compass ─────────


class TestLazyWrappers:
    """Verify lazy wrappers delegate to the correct underlying functions."""

    def test_load_controlplane_config_delegates(self) -> None:
        from lintgate.hooks.pre_tool import load_controlplane_config

        mock_result = SimpleNamespace(enabled=True)
        with patch(
            "lintgate.config.load_controlplane_config",
            return_value=mock_result,
        ) as mock_load:
            result = load_controlplane_config("/my/project")
        mock_load.assert_called_once_with("/my/project")
        assert result is mock_result

    def test_load_runtime_state_delegates(self) -> None:
        from lintgate.hooks.pre_tool import load_runtime_state

        mock_result = SimpleNamespace(timestamp=0)
        with patch(
            "lintgate.runtime_state.load_runtime_state",
            return_value=mock_result,
        ) as mock_load:
            result = load_runtime_state("/my/project")
        mock_load.assert_called_once_with("/my/project")
        assert result is mock_result

    def test_load_compass_delegates(self) -> None:
        from lintgate.hooks.pre_tool import load_compass

        mock_result = SimpleNamespace(axes={})
        with patch(
            "lintgate.compass_io.load_compass",
            return_value=mock_result,
        ) as mock_load:
            result = load_compass("/my/project")
        mock_load.assert_called_once_with("/my/project")
        assert result is mock_result

    def test_check_diff_secrets_delegates(self) -> None:
        from lintgate.hooks.pre_tool import _check_diff_secrets

        with patch(
            "lintgate.channels.git_channel._check_diff_secrets",
            return_value=[],
        ) as mock_check:
            result = _check_diff_secrets("/my/project")
        mock_check.assert_called_once_with("/my/project")
        assert result == []


class TestLazyWrapperExactValues:
    """Exact return-value tests for pre_tool lazy wrappers (COH001 remediation)."""

    def test_check_diff_secrets_returns_exact_findings(self) -> None:
        """_check_diff_secrets returns the exact list from the underlying scanner."""
        from lintgate.hooks.pre_tool import _check_diff_secrets

        findings = [
            {"type": "aws_key", "line": 42, "confidence": 0.9},
            {"type": "github_token", "line": 88, "confidence": 0.95},
        ]
        with patch(
            "lintgate.channels.git_channel._check_diff_secrets",
            return_value=findings,
        ):
            result = _check_diff_secrets("/tmp/repo")
        assert result == [
            {"type": "aws_key", "line": 42, "confidence": 0.9},
            {"type": "github_token", "line": 88, "confidence": 0.95},
        ]
        assert len(result) == 2

    def test_load_controlplane_config_returns_exact_fields(self) -> None:
        """load_controlplane_config passes through exact config attributes."""
        from lintgate.hooks.pre_tool import load_controlplane_config

        mock_cfg = SimpleNamespace(
            enabled=True,
            session_memory=True,
            quality_gate=SimpleNamespace(enabled=True, block_push=True),
        )
        with patch(
            "lintgate.config.load_controlplane_config",
            return_value=mock_cfg,
        ):
            result = load_controlplane_config("/tmp/repo")
        assert result.enabled is True
        assert result.session_memory is True
        assert result.quality_gate.enabled is True
        assert result.quality_gate.block_push is True

    def test_load_runtime_state_returns_exact_fields(self) -> None:
        """load_runtime_state passes through exact state attributes."""
        from lintgate.hooks.pre_tool import load_runtime_state

        mock_state = SimpleNamespace(
            timestamp=1700000000.0,
            blocking_issues=3,
            last_test_status="fail",
        )
        with patch(
            "lintgate.runtime_state.load_runtime_state",
            return_value=mock_state,
        ):
            result = load_runtime_state("/tmp/repo")
        assert result.timestamp == 1700000000.0
        assert result.blocking_issues == 3
        assert result.last_test_status == "fail"

    def test_check_diff_secrets_empty_repo_returns_empty(self) -> None:
        """Clean repo => empty list returned verbatim."""
        from lintgate.hooks.pre_tool import _check_diff_secrets

        with patch(
            "lintgate.channels.git_channel._check_diff_secrets",
            return_value=[],
        ):
            result = _check_diff_secrets("/tmp/clean-repo")
        assert result == []
        assert isinstance(result, list)
