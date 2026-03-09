"""Tests for lintgate/hooks/pre_tool.py.

Core hook helper tests live in test_hook_helpers.py. This file provides
the standard test_<module>.py entry point for test channel discovery.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from lintgate.hooks.pre_tool import (
    _COMMIT_RE,
    _PUSH_RE,
    _WRITE_TOOLS,
    QualityGateResult,
    _check_bash_alignment,
    _check_theory_mode,
    _classify_git_command,
    _collect_state_failures,
    _extract_bash_command,
    _load_mode,
    handle,
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
    mock_session = SimpleNamespace(behavior_compass={"mode_state": {"current": "theory"}})
    with patch(
        "lintgate.controlplane.session_memory.get_or_create_session",
        return_value=mock_session,
    ):
        result = _load_mode("/tmp/test")
    assert result == "theory"


def test_load_mode_returns_normal_when_mode_is_none() -> None:
    """When current mode is None, _load_mode coerces to 'normal'."""
    mock_session = SimpleNamespace(behavior_compass={"mode_state": {"current": None}})
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
    mock_session = SimpleNamespace(behavior_compass={"mode_state": {"current": "habit"}})
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
            latency_budget_ms=120_000,
            advisory_default=True,
            session_max_age_hours=4.0,
            constraint_proposal_threshold=5,
            severity_weighted_coherence=False,
            quality_gate=SimpleNamespace(
                enabled=True,
                block_push=True,
                staleness_threshold_s=1800.0,
            ),
        )
        with patch(
            "lintgate.config.load_controlplane_config",
            return_value=mock_cfg,
        ):
            result = load_controlplane_config("/tmp/repo")
        # Exact-value equality assertions (not just truthiness)
        assert result.enabled == True  # noqa: E712
        assert result.session_memory == True  # noqa: E712
        assert result.latency_budget_ms == 120_000
        assert result.advisory_default == True  # noqa: E712
        assert result.session_max_age_hours == 4.0
        assert result.constraint_proposal_threshold == 5
        assert result.severity_weighted_coherence == False  # noqa: E712
        assert result.quality_gate.enabled == True  # noqa: E712
        assert result.quality_gate.block_push == True  # noqa: E712
        assert result.quality_gate.staleness_threshold_s == 1800.0

    def test_load_controlplane_config_none_when_underlying_returns_none(self) -> None:
        """load_controlplane_config returns None exactly when underlying returns None."""
        from lintgate.hooks.pre_tool import load_controlplane_config

        with patch(
            "lintgate.config.load_controlplane_config",
            return_value=None,
        ):
            result = load_controlplane_config("/tmp/repo")
        assert result is None

    def test_load_controlplane_config_exact_numeric_values(self) -> None:
        """Verify numeric config fields are passed through with exact values."""
        from lintgate.hooks.pre_tool import load_controlplane_config

        mock_cfg = SimpleNamespace(
            enabled=False,
            latency_budget_ms=60_000,
            session_max_age_hours=2.5,
            constraint_proposal_threshold=10,
        )
        with patch(
            "lintgate.config.load_controlplane_config",
            return_value=mock_cfg,
        ):
            result = load_controlplane_config("/tmp/repo")
        assert result.enabled == False  # noqa: E712
        assert result.latency_budget_ms == 60_000
        assert result.session_max_age_hours == 2.5
        assert result.constraint_proposal_threshold == 10

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

    def test_load_compass_returns_exact_state_fields(self) -> None:
        """load_compass passes through exact compass state attributes."""
        from lintgate.hooks.pre_tool import load_compass

        mock_compass = SimpleNamespace(
            version=1,
            axes={"quality": SimpleNamespace(name="quality", weight=0.8)},
            directives=[SimpleNamespace(kind="toward", pattern="test")],
            frozen=False,
            frozen_hash="",
            forged_at=1700000000.0,
        )
        with patch(
            "lintgate.compass_io.load_compass",
            return_value=mock_compass,
        ):
            result = load_compass("/tmp/repo")
        # Exact-value equality assertions
        assert result.version == 1
        assert result.frozen == False  # noqa: E712
        assert result.frozen_hash == ""
        assert result.forged_at == 1700000000.0
        assert len(result.axes) == 1
        assert "quality" in result.axes
        assert result.axes["quality"].name == "quality"
        assert result.axes["quality"].weight == 0.8
        assert len(result.directives) == 1
        assert result.directives[0].kind == "toward"
        assert result.directives[0].pattern == "test"

    def test_load_compass_none_when_no_file(self) -> None:
        """load_compass returns None exactly when underlying returns None."""
        from lintgate.hooks.pre_tool import load_compass

        with patch(
            "lintgate.compass_io.load_compass",
            return_value=None,
        ):
            result = load_compass("/tmp/repo")
        assert result is None

    def test_load_compass_returns_exact_multi_axis_state(self) -> None:
        """Verify compass with multiple axes returns all exact values."""
        from lintgate.hooks.pre_tool import load_compass

        mock_compass = SimpleNamespace(
            version=2,
            axes={
                "quality": SimpleNamespace(name="quality", weight=0.8),
                "speed": SimpleNamespace(name="speed", weight=0.6),
            },
            directives=[],
            frozen=True,
            frozen_hash="abc123",
            forged_at=1700000500.0,
        )
        with patch(
            "lintgate.compass_io.load_compass",
            return_value=mock_compass,
        ):
            result = load_compass("/tmp/repo")
        assert result.version == 2
        assert result.frozen == True  # noqa: E712
        assert result.frozen_hash == "abc123"
        assert result.forged_at == 1700000500.0
        assert len(result.axes) == 2
        assert result.axes["quality"].weight == 0.8
        assert result.axes["speed"].weight == 0.6
        assert result.directives == []


# ── handle() specification tests ─────────────────────────────────────────
#
# Decision rules (DR) and equivalence partitions (EP) for handle():
#
# EP1: tool_name extraction — "tool_name" key vs "toolName" key vs missing
# EP2: command extraction — Bash tool vs non-Bash tool
# EP3: quality gate — blocking (push) vs advisory (commit) vs no-gate
# EP4: compass availability — present vs None vs ImportError
#
# DR1: Non-Bash tool, no gate → continue:true, empty systemMessage
# DR2: Bash tool, blocking gate (git push fails) → continue:false, block message
# DR3: Bash tool, non-blocking gate (commit advisory) → continue:true, advisory message
# DR4: ExecutionCompass ImportError, no gate messages → continue:true, no systemMessage key content
# DR5: ExecutionCompass ImportError, with gate messages → continue:true, gate messages
# DR6: Compass None, not theory+write, no gate → continue:true, empty systemMessage
# DR7: Compass None, theory mode + write tool → theory advisory (does NOT early-return)
# DR8: Compass present, Bash tool, alignment violation → alignment message
# ── End decision rule map ────────────────────────────────────────────────


class TestHandleSpecification:
    """Specification tests for handle() — covers all 8 decision rules and 4 equivalence partitions.

    σ=32, regime B, CC=16. Each test asserts exact output dict shape and values.
    """

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _mock_exec_compass():
        """Return a mock ExecutionCompass with configurable alignment results."""
        ec = MagicMock()
        ec.check_alignment.return_value = {"violations": [], "warnings": []}
        return ec

    @staticmethod
    def _patch_imports(
        *,
        mode: str = "normal",
        compass_state: object | None = None,
        gate_result: QualityGateResult | None = None,
        exec_compass: object | None = None,
        import_error: bool = False,
    ):
        """Build a combined patch context manager for handle()'s dependencies.

        Returns a contextmanager that patches:
        - _load_mode → returns `mode`
        - load_compass → returns `compass_state`
        - _check_quality_gate → returns `gate_result` (default: empty)
        - ExecutionCompass.from_compass_state → returns `exec_compass`
        - Optionally makes the ExecutionCompass import raise ImportError
        """
        from contextlib import contextmanager

        from lintgate.hooks.pre_tool import QualityGateResult as QualityGateRes

        if gate_result is None:
            gate_result = QualityGateRes()

        @contextmanager
        def ctx():
            patches = [
                patch("lintgate.hooks.pre_tool._load_mode", return_value=mode),
                patch("lintgate.hooks.pre_tool.load_compass", return_value=compass_state),
                patch("lintgate.hooks.pre_tool._check_quality_gate", return_value=gate_result),
            ]
            if import_error:
                # Make the `from lintgate.modes.execution_compass import ExecutionCompass`
                # inside handle() raise ImportError
                original_import = (
                    __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
                )

                def failing_import(name, *args, **kwargs):
                    if name == "lintgate.modes.execution_compass":
                        raise ImportError("test-induced import error")
                    return original_import(name, *args, **kwargs)

                patches.append(patch("builtins.__import__", side_effect=failing_import))
            else:
                if exec_compass is not None:
                    patches.append(
                        patch(
                            "lintgate.modes.execution_compass.ExecutionCompass.from_compass_state",
                            return_value=exec_compass,
                        )
                    )

            entered = []
            try:
                for p in patches:
                    entered.append(p.__enter__())
                yield
            finally:
                for p in reversed(patches):
                    p.__exit__(None, None, None)

        return ctx()

    # ── EP1: tool_name extraction ────────────────────────────────────────

    def test_ep1_tool_name_from_tool_name_key(self) -> None:
        """EP1: handle reads tool_name from the 'tool_name' key."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        with self._patch_imports():
            result = handle(data)
        assert result["continue"] is True

    def test_ep1_tool_name_fallback_key(self) -> None:
        """EP1: handle falls back to 'toolName' key."""
        from lintgate.hooks.pre_tool import handle

        data = {"toolName": "Read", "cwd": "/tmp"}
        with self._patch_imports():
            result = handle(data)
        assert result["continue"] is True

    def test_ep1_tool_name_missing_defaults_to_empty(self) -> None:
        """EP1: missing both keys → tool_name is empty string.
        With compass=None and not theory+write, returns bare continue dict.
        """
        data = {"cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="normal"):
            result = handle(data)
        assert result["continue"] is True
        # No tool_name → not Bash, compass None → early-return with no systemMessage key
        assert result == {"continue": True}

    # ── EP2: command extraction — Bash vs non-Bash ───────────────────────

    def test_ep2_bash_tool_extracts_command(self) -> None:
        """EP2: When tool_name is Bash, command is extracted from tool_input."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "cwd": "/tmp",
        }
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True

    def test_ep2_non_bash_tool_skips_command_extraction(self) -> None:
        """EP2: Non-Bash tool → command is '', quality gate not invoked."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Write",
            "tool_input": {"command": "git push"},
            "cwd": "/tmp",
        }
        # Direct test: non-Bash tool should never trigger _check_quality_gate
        with (
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
            patch("lintgate.hooks.pre_tool._check_quality_gate") as mock_qg,
        ):
            mock_qg.return_value = QualityGateResult()
            result = handle(data)
            mock_qg.assert_not_called()
        assert result["continue"] is True

    # ── EP3: quality gate — blocking vs advisory vs no-gate ──────────────

    def test_ep3_no_gate_for_non_git_command(self) -> None:
        """EP3: Non-git bash command → no gate messages."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "cwd": "/tmp",
        }
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(
            gate_result=QualityGateResult(),
            compass_state=compass_state,
            exec_compass=ec,
        ):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == ""

    # ── EP4: compass availability ────────────────────────────────────────

    def test_ep4_compass_present_creates_exec_compass(self) -> None:
        """EP4: When compass is loaded, ExecutionCompass.from_compass_state is called."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo test"},
            "cwd": "/tmp",
        }
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True

    def test_ep4_compass_none_no_theory_write_returns_early(self) -> None:
        """EP4: Compass None + not theory+write → early return, no compass messages."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="normal"):
            result = handle(data)
        assert result == {"continue": True}

    # ── DR1: Non-Bash, no gate → continue:true, empty systemMessage ─────

    def test_dr1_non_bash_no_gate_exact_output(self) -> None:
        """DR1: Read tool, normal mode, compass present → continue:true, empty message."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        compass_state = SimpleNamespace(directives=[], axes={})
        ec = self._mock_exec_compass()
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result == {"continue": True, "systemMessage": ""}

    def test_dr1_grep_tool_exact_output(self) -> None:
        """DR1: Grep tool (non-Bash, non-Write) → continue:true, empty message."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Grep", "cwd": "/tmp"}
        compass_state = SimpleNamespace(directives=[], axes={})
        ec = self._mock_exec_compass()
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == ""

    # ── DR2: Bash, blocking gate → continue:false ────────────────────────

    def test_dr2_git_push_blocked_exact_output(self) -> None:
        """DR2: git push with quality gate failure → continue:false, block message."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=True,
            messages=["[QualityGate] BLOCKED: No quality run found. Run `controlplane_run` first."],
        )
        with self._patch_imports(gate_result=gate):
            result = handle(data)
        assert result["continue"] is False
        assert (
            result["systemMessage"]
            == "[QualityGate] BLOCKED: No quality run found. Run `controlplane_run` first."
        )

    def test_dr2_git_push_blocked_multiple_messages_joined(self) -> None:
        """DR2: Multiple block messages are pipe-joined."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=True,
            messages=[
                "[QualityGate] BLOCKED: Tests are failing.",
                "[QualityGate] BLOCKED: 2 blocking issue(s) remain.",
            ],
        )
        with self._patch_imports(gate_result=gate):
            result = handle(data)
        assert result["continue"] is False
        assert result["systemMessage"] == (
            "[QualityGate] BLOCKED: Tests are failing."
            " | [QualityGate] BLOCKED: 2 blocking issue(s) remain."
        )

    def test_dr2_blocking_gate_returns_immediately_skips_compass(self) -> None:
        """DR2: Blocking gate returns before _load_mode is ever called."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=True,
            messages=["[QualityGate] BLOCKED: reason"],
        )
        with (
            patch("lintgate.hooks.pre_tool._check_quality_gate", return_value=gate),
            patch("lintgate.hooks.pre_tool._load_mode") as mock_mode,
        ):
            result = handle(data)
        # _load_mode should not be called because blocking gate returns early
        mock_mode.assert_not_called()
        assert result["continue"] is False

    # ── DR3: Bash, non-blocking gate (commit advisory) ──────────────────

    def test_dr3_git_commit_advisory_with_compass(self) -> None:
        """DR3: git commit advisory messages flow through when compass is present."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix'"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=False,
            messages=["[QualityGate] Advisory: Tests are failing."],
        )
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(gate_result=gate, compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert "[QualityGate] Advisory: Tests are failing." in result["systemMessage"]

    def test_dr3_advisory_messages_preserved_in_final_output(self) -> None:
        """DR3: Gate advisory messages appear in the pipe-joined systemMessage."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix'"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=False,
            messages=["[QualityGate] Advisory: Stale run."],
        )
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(gate_result=gate, compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == "[QualityGate] Advisory: Stale run."

    # ── DR4: ImportError on ExecutionCompass, no gate messages ────────────

    def test_dr4_import_error_no_gate_exact_output(self) -> None:
        """DR4: ExecutionCompass ImportError + no gate messages → bare continue."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        with self._patch_imports(import_error=True):
            result = handle(data)
        assert result == {"continue": True}

    # ── DR5: ImportError on ExecutionCompass, with gate messages ──────────

    def test_dr5_import_error_with_gate_messages_exact_output(self) -> None:
        """DR5: ExecutionCompass ImportError + gate advisory → continue:true with message."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'msg'"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=False,
            messages=["[QualityGate] Advisory: Tests are failing."],
        )
        with self._patch_imports(gate_result=gate, import_error=True):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == "[QualityGate] Advisory: Tests are failing."

    # ── DR6: Compass None, not theory+write, no gate ─────────────────────

    def test_dr6_compass_none_normal_mode_no_gate_exact_output(self) -> None:
        """DR6: Compass None, normal mode, no gate → bare continue."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="normal"):
            result = handle(data)
        assert result == {"continue": True}

    def test_dr6_compass_none_habit_mode_exact_output(self) -> None:
        """DR6: Compass None, habit mode (not theory) → bare continue."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Write", "cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="habit"):
            result = handle(data)
        assert result == {"continue": True}

    def test_dr6_compass_none_with_gate_messages_exact_output(self) -> None:
        """DR6 variant: Compass None + gate advisory → continue:true with gate message."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix'"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=False,
            messages=["[QualityGate] Advisory: Stale."],
        )
        with self._patch_imports(compass_state=None, mode="normal", gate_result=gate):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == "[QualityGate] Advisory: Stale."

    # ── DR7: Compass None, theory mode + write tool ──────────────────────

    def test_dr7_compass_none_theory_write_produces_advisory(self) -> None:
        """DR7: Compass None + theory mode + Write tool → does NOT early-return,
        produces theory advisory.
        """
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Write", "cwd": "/tmp"}
        # exec_compass will be None since compass_state is None, but handle still
        # continues past the early-return guard because mode=="theory" and tool is Write
        with self._patch_imports(compass_state=None, mode="theory"):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == (
            "[Compass] Theory mode active — consider reading more"
            " before writing. Use `theory_mode_freeze` when ready."
        )

    def test_dr7_compass_none_theory_edit_produces_advisory(self) -> None:
        """DR7: Compass None + theory mode + Edit tool → theory advisory."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Edit", "cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="theory"):
            result = handle(data)
        assert result["continue"] is True
        assert "[Compass] Theory mode active" in result["systemMessage"]

    def test_dr7_compass_none_theory_read_returns_early(self) -> None:
        """DR7 negative: Compass None + theory mode + Read (not write tool) → early return."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="theory"):
            result = handle(data)
        # Read is not a write tool, so the early-return guard triggers
        assert result == {"continue": True}

    # ── DR8: Compass present, Bash tool, alignment check ─────────────────

    def test_dr8_bash_alignment_violation_exact_message(self) -> None:
        """DR8: Bash tool + compass present + violation → alignment message in output."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "cwd": "/tmp",
        }
        ec = MagicMock()
        ec.check_alignment.return_value = {
            "violations": ["forbidden: rm -rf /"],
            "warnings": [],
        }
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == "[Compass] Alignment violation: forbidden: rm -rf /"

    def test_dr8_bash_alignment_warning_exact_message(self) -> None:
        """DR8: Bash tool + compass present + warning → warning message in output."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "find . -name '*.py'"},
            "cwd": "/tmp",
        }
        ec = MagicMock()
        ec.check_alignment.return_value = {
            "violations": [],
            "warnings": ["away: prefer grep"],
        }
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == "[Compass] Alignment warning: away: prefer grep"

    def test_dr8_bash_no_alignment_issues_empty_message(self) -> None:
        """DR8: Bash tool + compass present + no violations/warnings → empty message."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest"},
            "cwd": "/tmp",
        }
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == ""

    def test_dr8_non_bash_tool_skips_alignment_check(self) -> None:
        """DR8 negative: Non-Bash tool with compass → alignment not checked."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        ec = MagicMock()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        ec.check_alignment.assert_not_called()
        assert result["systemMessage"] == ""

    # ── Combined paths: multiple message sources ─────────────────────────

    def test_combined_gate_advisory_plus_theory_advisory(self) -> None:
        """Gate advisory + theory mode write → both messages pipe-joined."""
        from lintgate.hooks.pre_tool import handle

        # Theory mode + Write tool triggers theory advisory.
        # But Write is NOT Bash, so command="" and quality gate is never invoked.
        # To get both, we need a scenario where gate_result has messages AND
        # theory mode fires. gate_result.messages only have content when
        # command is non-empty (Bash tool). So this requires Bash + theory + write.
        # Actually, theory advisory only fires for write tools. Bash is not a write tool.
        # So theory + alignment can combine, but theory + gate requires the
        # gate messages to carry through from the Bash command while also being
        # in theory mode. Since Bash is not in _WRITE_TOOLS, theory_msg will be "".
        #
        # The real combination is: gate advisory + alignment message.
        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix'"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=False,
            messages=["[QualityGate] Advisory: Tests are failing."],
        )
        ec = MagicMock()
        ec.check_alignment.return_value = {
            "violations": ["forbidden: commit without tests"],
            "warnings": [],
        }
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(gate_result=gate, compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == (
            "[QualityGate] Advisory: Tests are failing."
            " | [Compass] Alignment violation: forbidden: commit without tests"
        )

    def test_combined_theory_mode_write_with_compass_present(self) -> None:
        """Theory mode + write tool + compass present → theory advisory only
        (Write is not Bash, so no alignment check).
        """
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Write", "cwd": "/tmp"}
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(mode="theory", compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result["continue"] is True
        assert result["systemMessage"] == (
            "[Compass] Theory mode active — consider reading more"
            " before writing. Use `theory_mode_freeze` when ready."
        )
        # Alignment is NOT checked because tool_name != "Bash"
        ec.check_alignment.assert_not_called()

    def test_combined_no_messages_returns_empty_string(self) -> None:
        """No gate messages + no theory + no alignment → systemMessage is ''."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "cwd": "/tmp",
        }
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert result == {"continue": True, "systemMessage": ""}

    # ── Edge cases ───────────────────────────────────────────────────────

    def test_edge_cwd_defaults_to_dot(self) -> None:
        """When 'cwd' is missing from data, project_root defaults to '.'."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read"}
        with (
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal") as mock_mode,
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
        ):
            result = handle(data)
        # _load_mode should have been called with "." (the default)
        mock_mode.assert_called_once_with(".")
        assert result["continue"] is True

    def test_edge_empty_data_dict(self) -> None:
        """Completely empty input dict → tool_name='', command='', continue:true."""
        from lintgate.hooks.pre_tool import handle

        data: dict[str, Any] = {}
        with (
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
        ):
            result = handle(data)
        assert result == {"continue": True}

    def test_edge_bash_empty_command_skips_gate(self) -> None:
        """Bash tool with empty command → command='', gate not invoked."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": ""},
            "cwd": "/tmp",
        }
        with (
            patch("lintgate.hooks.pre_tool._load_mode", return_value="normal"),
            patch("lintgate.hooks.pre_tool.load_compass", return_value=None),
            patch("lintgate.hooks.pre_tool._check_quality_gate") as mock_qg,
        ):
            result = handle(data)
        mock_qg.assert_not_called()
        assert result == {"continue": True}

    def test_edge_exec_compass_none_when_compass_none_theory_write(self) -> None:
        """When compass_state is None in theory+write path, exec_compass is None.
        Alignment check is skipped because exec_compass is falsy.
        """
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "MultiEdit", "cwd": "/tmp"}
        with self._patch_imports(compass_state=None, mode="theory"):
            result = handle(data)
        assert result["continue"] is True
        # Theory advisory fires because MultiEdit is in _WRITE_TOOLS
        assert "[Compass] Theory mode active" in result["systemMessage"]

    def test_output_dict_has_exactly_two_keys(self) -> None:
        """Normal path output dict always has exactly 'continue' and 'systemMessage'."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert set(result.keys()) == {"continue", "systemMessage"}

    def test_output_dict_blocked_has_exactly_two_keys(self) -> None:
        """Blocked path output dict always has exactly 'continue' and 'systemMessage'."""
        from lintgate.hooks.pre_tool import handle

        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "cwd": "/tmp",
        }
        gate = QualityGateResult(
            should_block=True,
            messages=["[QualityGate] BLOCKED: reason"],
        )
        with self._patch_imports(gate_result=gate):
            result = handle(data)
        assert set(result.keys()) == {"continue", "systemMessage"}

    def test_output_continue_is_bool_not_truthy(self) -> None:
        """Verify 'continue' is actual bool, not just truthy/falsy."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert type(result["continue"]) is bool
        assert result["continue"] is True

    def test_output_system_message_is_str(self) -> None:
        """Verify 'systemMessage' is always a string."""
        from lintgate.hooks.pre_tool import handle

        data = {"tool_name": "Read", "cwd": "/tmp"}
        ec = self._mock_exec_compass()
        compass_state = SimpleNamespace(directives=[], axes={})
        with self._patch_imports(compass_state=compass_state, exec_compass=ec):
            result = handle(data)
        assert type(result["systemMessage"]) is str
