"""Tests for the enhanced UserPromptSubmit pre-generation primer engine."""

from __future__ import annotations

from unittest.mock import patch

from lintgate.hooks.user_prompt import _PRIMER_MAX_CHARS, _build_primer, handle
from lintgate.runtime_state import RuntimeState, save_runtime_state

# ── _build_primer ────────────────────────────────────────────────────


class TestBuildPrimer:
    def test_returns_none_when_no_state(self, tmp_path):
        """No RuntimeState file → returns None (triggers fallback)."""
        assert _build_primer(str(tmp_path)) is None

    def test_mode_line_always_present(self, tmp_path):
        state = RuntimeState(mode="normal")
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Mode: normal" in primer

    def test_habit_mode_includes_score(self, tmp_path):
        state = RuntimeState(mode="habit", habit_score=0.82)
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Mode: habit (82%)" in primer

    def test_focus_files_basenames(self, tmp_path):
        state = RuntimeState(active_files=["/src/main.py", "/src/utils.py", "/tests/test_main.py"])
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Focus: [main.py, utils.py, test_main.py]" in primer

    def test_focus_files_capped_at_3(self, tmp_path):
        state = RuntimeState(active_files=[f"/src/{i}.py" for i in range(10)])
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        # Should only show 3 files
        assert primer.count(".py") <= 3

    def test_blocking_alert(self, tmp_path):
        state = RuntimeState(blocking_issues=5)
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "BLOCKING: 5 issues" in primer

    def test_no_blocking_when_zero(self, tmp_path):
        state = RuntimeState(blocking_issues=0)
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "BLOCKING" not in primer

    def test_approach_failures_warning(self, tmp_path):
        state = RuntimeState(approach_failures=3)
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "WARNING: 3 failed approaches" in primer
        assert "constraint_check" in primer

    def test_low_prediction_accuracy(self, tmp_path):
        state = RuntimeState(prediction_accuracy=0.3, approach_failures=0)
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Prediction accuracy: 30%" in primer

    def test_no_prediction_when_no_data(self, tmp_path):
        state = RuntimeState(prediction_accuracy=-1.0, approach_failures=0)
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Prediction" not in primer

    def test_coherence_alert_systemic(self, tmp_path):
        state = RuntimeState(coherence_state="systemic")
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Coherence: systemic" in primer

    def test_coherence_alert_coupled(self, tmp_path):
        state = RuntimeState(coherence_state="coupled")
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Coherence: coupled" in primer

    def test_no_coherence_when_stable(self, tmp_path):
        state = RuntimeState(coherence_state="stable")
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert "Coherence" not in primer

    def test_max_chars_enforced(self, tmp_path):
        state = RuntimeState(
            mode="habit",
            habit_score=0.95,
            active_files=[f"/very/long/path/to/file_{i}.py" for i in range(3)],
            blocking_issues=99,
            approach_failures=10,
            coherence_state="systemic",
            top_constraint="x" * 200,
        )
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert len(primer) <= _PRIMER_MAX_CHARS

    def test_pipe_separated_parts(self, tmp_path):
        state = RuntimeState(
            mode="habit",
            habit_score=0.8,
            active_files=["/a.py"],
            blocking_issues=2,
        )
        save_runtime_state(str(tmp_path), state)
        primer = _build_primer(str(tmp_path))
        assert primer is not None
        assert " | " in primer


# ── handle() integration ─────────────────────────────────────────────


class TestHandleIntegration:
    def test_enhanced_primer_with_runtime_state(self, tmp_path):
        state = RuntimeState(mode="habit", habit_score=0.75, blocking_issues=2)
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path), "userMessage": "fix the bug"})
        assert result["continue"] is True
        msg = result["systemMessage"]
        assert msg.startswith("[LG]")
        assert "Mode: habit" in msg
        assert "BLOCKING: 2" in msg

    def test_theory_keyword_appended(self, tmp_path):
        state = RuntimeState(mode="normal")
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path), "userMessage": "explain the architecture"})
        msg = result["systemMessage"]
        assert "theory-relevant prompt" in msg

    def test_fallback_to_legacy_when_no_state(self, tmp_path):
        """When RuntimeState doesn't exist, falls back to legacy [Compass] format."""
        # Patch _load_mode to return a non-normal mode so we get output
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="theory"):
            result = handle({"cwd": str(tmp_path), "userMessage": "hello"})
        msg = result["systemMessage"]
        assert "[Compass]" in msg
        assert "Mode: theory" in msg

    def test_fallback_normal_mode_no_output(self, tmp_path):
        """Legacy fallback in normal mode with no theory keywords → empty message."""
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="normal"):
            result = handle({"cwd": str(tmp_path), "userMessage": "hello"})
        assert result["systemMessage"] == ""

    def test_fallback_theory_keyword(self, tmp_path):
        """Legacy fallback with theory keyword → includes mode hint."""
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="normal"):
            result = handle({"cwd": str(tmp_path), "userMessage": "explain the theory"})
        msg = result["systemMessage"]
        assert "[Compass]" in msg
        assert "theory-relevant" in msg

    def test_dict_user_message(self, tmp_path):
        """Handle dict-format user message."""
        state = RuntimeState(mode="normal")
        save_runtime_state(str(tmp_path), state)

        result = handle(
            {
                "cwd": str(tmp_path),
                "userMessage": {"content": "check the architecture"},
            }
        )
        msg = result["systemMessage"]
        assert "theory-relevant prompt" in msg

    def test_always_continues(self, tmp_path):
        result = handle({"cwd": str(tmp_path), "userMessage": "hello"})
        assert result["continue"] is True
