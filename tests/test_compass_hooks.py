"""Tests for compass-aware Claude Code hooks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from lintgate.hooks.pre_compact import handle as pre_compact_handle
from lintgate.hooks.pre_tool import handle as pre_tool_handle
from lintgate.hooks.session_end import handle as session_end_handle
from lintgate.hooks.session_start import handle as session_start_handle
from lintgate.hooks.stop_gate import handle as stop_gate_handle
from lintgate.hooks.user_prompt import handle as user_prompt_handle

# ── All hooks return valid dict with continue: True on empty input ──


def test_session_start_empty_input() -> None:
    result = session_start_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_session_end_empty_input() -> None:
    result = session_end_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_pre_tool_empty_input() -> None:
    result = pre_tool_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_pre_compact_empty_input() -> None:
    result = pre_compact_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_stop_gate_empty_input() -> None:
    result = stop_gate_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_user_prompt_empty_input() -> None:
    result = user_prompt_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


# ── session_start with no compass ───────────────────────────────────


def test_session_start_no_compass_returns_advisory(tmp_path: object) -> None:
    """When no compass file exists, session_start should return an advisory message."""
    result = session_start_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "No compass found" in msg or msg == ""


# ── user_prompt detects theory keywords ─────────────────────────────


def test_user_prompt_theory_keyword_detection() -> None:
    result = user_prompt_handle({"userMessage": "Let me explore the architecture"})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "theory-relevant" in msg


def test_user_prompt_no_theory_keyword() -> None:
    result = user_prompt_handle({"userMessage": "Fix the typo in line 42"})
    assert result["continue"] is True
    # No theory-relevant hint, so systemMessage should be empty
    assert result.get("systemMessage", "") == ""


def test_user_prompt_reports_non_normal_mode_without_keyword(monkeypatch) -> None:
    # Force legacy path by disabling the enhanced primer
    monkeypatch.setattr("lintgate.hooks.user_prompt._build_primer", lambda _root: None)
    monkeypatch.setattr(
        "lintgate.controlplane.session_memory.get_or_create_session",
        lambda _root: SimpleNamespace(behavior_compass={"mode_state": {"current": "habit"}}),
    )
    result = user_prompt_handle({"cwd": "/tmp", "userMessage": "Fix typo"})
    assert result["continue"] is True
    assert "Mode: habit" in result.get("systemMessage", "")


def test_user_prompt_dict_message_format() -> None:
    """user_prompt should handle dict-format messages too."""
    result = user_prompt_handle({"userMessage": {"content": "understand the theory"}})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "theory-relevant" in msg


# ── pre_tool in theory mode ─────────────────────────────────────────


def test_pre_tool_write_tool_no_compass(tmp_path: object) -> None:
    """Without a compass, pre_tool should pass through even for Write."""
    result = pre_tool_handle({"tool_name": "Write", "cwd": str(tmp_path)})
    assert result["continue"] is True


def test_pre_tool_bash_no_compass(tmp_path: object) -> None:
    """Without a compass, pre_tool should pass through for Bash."""
    result = pre_tool_handle(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "cwd": str(tmp_path),
        }
    )
    assert result["continue"] is True


def test_pre_tool_theory_mode_warns_on_write_without_compass(monkeypatch) -> None:
    monkeypatch.setattr(
        "lintgate.controlplane.session_memory.get_or_create_session",
        lambda _root: SimpleNamespace(behavior_compass={"mode_state": {"current": "theory"}}),
    )
    monkeypatch.setattr("lintgate.compass_io.load_compass", lambda _root: None)
    result = pre_tool_handle({"tool_name": "Write", "cwd": "/tmp"})
    assert result["continue"] is True
    assert "Theory mode active" in result.get("systemMessage", "")


# ── stop_gate with no compass ───────────────────────────────────────


def test_stop_gate_no_compass(tmp_path: object) -> None:
    result = stop_gate_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True


# ── pre_compact with no compass ─────────────────────────────────────


def test_pre_compact_no_compass(tmp_path: object) -> None:
    result = pre_compact_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True
    # No capsule data when there is no compass
    assert "lintgate-compact-state" not in result.get("systemMessage", "")


# ── session_end with no compass ─────────────────────────────────────


def test_session_end_no_compass(tmp_path: object) -> None:
    result = session_end_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True


# ── stop_gate mutant-killing tests ────────────────────────────────


class TestStopGateHandleMutantKilling:
    """Exact-value tests that kill VALUE/SWAP/BOUNDARY mutants in stop_gate.handle."""

    def _make_compass(self, axis_depths: dict[str, int]) -> SimpleNamespace:
        """Build a mock CompassState with given axis depths."""
        axes = {}
        for name in ("problem", "solution", "implementation", "world"):
            axes[name] = SimpleNamespace(depth=axis_depths.get(name, 0))
        return SimpleNamespace(axes=axes)

    def test_no_compass_returns_continue_only(self) -> None:
        """When load_compass returns None, result has no systemMessage key."""
        with (
            patch("lintgate.compass_io.load_compass", return_value=None),
            patch("lintgate.compass.compute_staleness"),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert result == {"continue": True}
        assert "systemMessage" not in result

    def test_all_axes_populated_staleness_zero(self) -> None:
        """4/4 axes populated, staleness=0% — pin exact message format."""
        compass = self._make_compass({"problem": 2, "solution": 3, "implementation": 1, "world": 1})
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.0),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert result["continue"] is True
        assert result["systemMessage"] == (
            "[Compass] Session ending — 4/4 axes populated, staleness=0%."
        )

    def test_no_axes_populated_staleness_full(self) -> None:
        """0/4 axes populated, staleness=100%."""
        compass = self._make_compass({})
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=1.0),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert result["systemMessage"] == (
            "[Compass] Session ending — 0/4 axes populated, staleness=100%."
        )

    def test_partial_axes_populated(self) -> None:
        """2/4 axes populated — depth>0 is the condition, not depth>=1."""
        compass = self._make_compass({"problem": 3, "solution": 1})
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.5),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert result["systemMessage"] == (
            "[Compass] Session ending — 2/4 axes populated, staleness=50%."
        )

    def test_staleness_formatting_rounds_correctly(self) -> None:
        """staleness=0.333 formats as '33%' (:.0% format spec)."""
        compass = self._make_compass({"problem": 1})
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.333),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert "staleness=33%." in result["systemMessage"]

    def test_staleness_formatting_rounds_up(self) -> None:
        """staleness=0.255 formats as '26%' (banker's rounding :.0%)."""
        compass = self._make_compass({"problem": 1})
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.255),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert "staleness=26%." in result["systemMessage"]

    def test_depth_zero_not_counted(self) -> None:
        """Axes with depth=0 are NOT counted as populated."""
        compass = self._make_compass({"problem": 0, "solution": 0, "implementation": 0, "world": 0})
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.0),
        ):
            result = stop_gate_handle({"cwd": "/tmp"})
        assert "0/4 axes populated" in result["systemMessage"]

    def test_cwd_defaults_to_dot(self) -> None:
        """When cwd not in data, defaults to '.'."""
        with (
            patch("lintgate.compass_io.load_compass", return_value=None) as mock_load,
            patch("lintgate.compass.compute_staleness"),
        ):
            stop_gate_handle({})
        mock_load.assert_called_once_with(".")

    def test_always_returns_continue_true(self) -> None:
        """Regardless of compass state, continue is always True."""
        result = stop_gate_handle({"cwd": "/nonexistent/path/that/wont/have/compass"})
        assert result["continue"] is True


# ── session_end mutant-killing tests ──────────────────────────────


class TestSessionEndHandleMutantKilling:
    """Exact-value tests that kill VALUE/STATE mutants in session_end.handle."""

    def test_with_compass_calls_save(self) -> None:
        """When compass exists, save_compass is called with project_root and compass."""
        mock_compass = SimpleNamespace(axes={})
        with (
            patch("lintgate.compass_io.load_compass", return_value=mock_compass),
            patch("lintgate.compass_io.save_compass") as mock_save,
            patch("lintgate.hooks.session_end._cleanup_session_state") as mock_cleanup,
        ):
            result = session_end_handle({"cwd": "/test/project"})
        assert result == {"continue": True}
        mock_save.assert_called_once_with("/test/project", mock_compass)
        mock_cleanup.assert_called_once_with("/test/project")

    def test_no_compass_skips_save(self) -> None:
        """When compass is None, save_compass is NOT called."""
        with (
            patch("lintgate.compass_io.load_compass", return_value=None),
            patch("lintgate.compass_io.save_compass") as mock_save,
            patch("lintgate.hooks.session_end._cleanup_session_state"),
        ):
            result = session_end_handle({"cwd": "/test/project"})
        assert result == {"continue": True}
        mock_save.assert_not_called()

    def test_save_exception_suppressed(self) -> None:
        """save_compass exception is suppressed (contextlib.suppress)."""
        mock_compass = SimpleNamespace(axes={})
        with (
            patch("lintgate.compass_io.load_compass", return_value=mock_compass),
            patch(
                "lintgate.compass_io.save_compass",
                side_effect=OSError("disk full"),
            ),
            patch("lintgate.hooks.session_end._cleanup_session_state"),
        ):
            result = session_end_handle({"cwd": "/test"})
        assert result == {"continue": True}

    def test_cleanup_always_called(self) -> None:
        """_cleanup_session_state is called regardless of compass state."""
        with (
            patch("lintgate.compass_io.load_compass", return_value=None),
            patch("lintgate.compass_io.save_compass"),
            patch("lintgate.hooks.session_end._cleanup_session_state") as mock_cleanup,
        ):
            session_end_handle({"cwd": "/my/project"})
        mock_cleanup.assert_called_once_with("/my/project")

    def test_cwd_defaults_to_dot(self) -> None:
        """When cwd not in data, defaults to '.'."""
        with (
            patch("lintgate.compass_io.load_compass", return_value=None) as mock_load,
            patch("lintgate.compass_io.save_compass"),
            patch("lintgate.hooks.session_end._cleanup_session_state"),
        ):
            session_end_handle({})
        mock_load.assert_called_once_with(".")
