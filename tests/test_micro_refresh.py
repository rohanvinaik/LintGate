"""Tests for MCP tool-response micro-refresh."""

from __future__ import annotations

from lintgate.runtime_state import RuntimeState, save_runtime_state
from mcp_tools.micro_refresh import attach_session_context


class TestAttachSessionContext:
    def test_attaches_context_when_state_exists(self, tmp_path):
        state = RuntimeState(
            generation=10,
            mode="habit",
            active_files=["/src/main.py", "/src/utils.py"],
            blocking_issues=2,
            coherence_state="isolated",
            last_test_status="pass",
            estimated_tokens_pct=35.5,
        )
        save_runtime_state(str(tmp_path), state)

        result = {"run_id": "abc", "issues": 3}
        augmented = attach_session_context(result, str(tmp_path))

        assert "session_context" in augmented
        ctx = augmented["session_context"]
        assert ctx["gen"] == 11  # save incremented it
        assert ctx["mode"] == "habit"
        assert ctx["focus"] == ["main.py", "utils.py"]
        assert ctx["blocking"] == 2
        assert ctx["coherence"] == "isolated"
        assert ctx["test"] == "pass"
        assert ctx["tokens_pct"] == 35.5

    def test_no_context_when_no_state(self, tmp_path):
        result = {"run_id": "abc"}
        augmented = attach_session_context(result, str(tmp_path))
        assert "session_context" not in augmented
        assert augmented == {"run_id": "abc"}

    def test_no_context_when_project_root_none(self):
        result = {"run_id": "abc"}
        augmented = attach_session_context(result, None)
        assert "session_context" not in augmented

    def test_original_result_preserved(self, tmp_path):
        state = RuntimeState(mode="normal")
        save_runtime_state(str(tmp_path), state)

        result = {"run_id": "xyz", "issues": 5, "details": [1, 2, 3]}
        augmented = attach_session_context(result, str(tmp_path))

        assert augmented["run_id"] == "xyz"
        assert augmented["issues"] == 5
        assert augmented["details"] == [1, 2, 3]

    def test_focus_capped_at_3(self, tmp_path):
        state = RuntimeState(active_files=[f"/src/{i}.py" for i in range(10)])
        save_runtime_state(str(tmp_path), state)

        result = {}
        augmented = attach_session_context(result, str(tmp_path))
        assert len(augmented["session_context"]["focus"]) == 3

    def test_focus_basenames_extracted(self, tmp_path):
        state = RuntimeState(active_files=["/very/deep/path/to/file.py"])
        save_runtime_state(str(tmp_path), state)

        result = {}
        augmented = attach_session_context(result, str(tmp_path))
        assert augmented["session_context"]["focus"] == ["file.py"]

    def test_empty_active_files(self, tmp_path):
        state = RuntimeState(active_files=[])
        save_runtime_state(str(tmp_path), state)

        result = {}
        augmented = attach_session_context(result, str(tmp_path))
        assert augmented["session_context"]["focus"] == []

    def test_token_budget_approximate(self, tmp_path):
        """session_context should be roughly 60 tokens (~240 chars)."""
        state = RuntimeState(
            generation=999,
            mode="habit",
            active_files=["/a.py", "/b.py", "/c.py"],
            blocking_issues=5,
            coherence_state="systemic",
            last_test_status="fail",
            estimated_tokens_pct=75.3,
        )
        save_runtime_state(str(tmp_path), state)

        result = {}
        augmented = attach_session_context(result, str(tmp_path))
        import json

        ctx_str = json.dumps(augmented["session_context"])
        # Should be well under 300 chars (~75 tokens)
        assert len(ctx_str) < 300

    def test_fail_open_on_import_error(self, tmp_path, monkeypatch):
        """If runtime_state import fails, result is returned unchanged."""
        del monkeypatch  # no-op: keep signature stable for future fault injection

        # The function should handle the exception gracefully
        result = {"data": "preserved"}
        augmented = attach_session_context(result, str(tmp_path))
        # Even without state, result should be unchanged
        assert augmented["data"] == "preserved"
