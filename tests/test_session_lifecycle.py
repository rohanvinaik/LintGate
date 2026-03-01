"""Tests for session lifecycle integration — SessionStart and SessionEnd hooks."""

from __future__ import annotations

from lintgate.runtime_state import RuntimeState, load_runtime_state, save_runtime_state


class TestSessionStartInitialization:
    """Tests for RuntimeState initialization on SessionStart."""

    def test_creates_runtime_state_file(self, tmp_path):
        from lintgate.hooks.session_start import handle

        handle({"cwd": str(tmp_path)})

        state = load_runtime_state(str(tmp_path))
        assert state is not None
        assert state.generation >= 1

    def test_runtime_state_has_default_mode(self, tmp_path):
        from lintgate.hooks.session_start import handle

        handle({"cwd": str(tmp_path)})

        state = load_runtime_state(str(tmp_path))
        assert state.mode == "normal"

    def test_creates_dynamic_files_for_claude(self, tmp_path):
        from lintgate.hooks.session_start import handle

        # Create .claude/rules dir so detect_host returns "claude"
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        handle({"cwd": str(tmp_path)})

        assert (rules_dir / "lg_session.md").exists()
        assert (rules_dir / "lg_focus.md").exists()

    def test_dynamic_files_contain_generation_watermark(self, tmp_path):
        from lintgate.hooks.session_start import handle

        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        handle({"cwd": str(tmp_path)})

        content = (rules_dir / "lg_session.md").read_text()
        assert "LG_GEN:" in content

    def test_creates_dynamic_files_for_multiple_hosts(self, tmp_path):
        from lintgate.hooks.session_start import handle

        claude_rules = tmp_path / ".claude" / "rules"
        cursor_rules = tmp_path / ".cursor" / "rules"
        claude_rules.mkdir(parents=True)
        cursor_rules.mkdir(parents=True)

        handle({"cwd": str(tmp_path)})

        assert (claude_rules / "lg_session.md").exists()
        assert (claude_rules / "lg_focus.md").exists()
        assert (cursor_rules / "lg_session.mdc").exists()
        assert (cursor_rules / "lg_focus.mdc").exists()

    def test_no_dynamic_files_without_host(self, tmp_path):
        from lintgate.hooks.session_start import handle

        handle({"cwd": str(tmp_path)})

        # RuntimeState should exist but no dynamic files
        assert load_runtime_state(str(tmp_path)) is not None
        assert not (tmp_path / ".claude" / "rules" / "lg_session.md").exists()
        assert not (tmp_path / ".cursor" / "rules" / "lg_session.mdc").exists()

    def test_always_returns_continue(self, tmp_path):
        from lintgate.hooks.session_start import handle

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_compass_advisory_still_works(self, tmp_path):
        """Existing compass advisory behavior should be preserved."""
        from lintgate.hooks.session_start import handle

        # No compass => advisory about missing compass
        result = handle({"cwd": str(tmp_path)})
        assert "No compass found" in result.get("systemMessage", "")

    def test_initialization_fail_open(self, tmp_path, monkeypatch):
        """RuntimeState init failure should not break the hook."""

        # Make save_runtime_state raise
        def failing_save(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("lintgate.runtime_state.save_runtime_state", failing_save)

        from lintgate.hooks.session_start import handle

        # Should not raise, and should still return valid response
        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_rehydrates_runtime_state_from_session_sources(self, tmp_path):
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            save_session,
        )
        from lintgate.habit_mode import HabitModeState, save_habit_state
        from lintgate.hooks.session_start import handle
        from lintgate.token_tracker import TokenTrackerState, save_tracker_state

        session = get_or_create_session(str(tmp_path))
        session.behavior_compass["mode_state"] = {"current": "habit"}
        save_habit_state(
            session.behavior_compass,
            HabitModeState(
                active=True,
                habit_score=0.91,
                active_files=["/tmp/proj/a.py", "/tmp/proj/b.py"],
            ),
        )
        save_tracker_state(
            session.behavior_compass,
            TokenTrackerState(
                estimated_tokens_used=80000,
                context_window_size=200000,
                tool_call_count=12,
            ),
        )
        save_session(session)

        handle({"cwd": str(tmp_path)})

        state = load_runtime_state(str(tmp_path))
        assert state is not None
        assert state.mode == "habit"
        assert state.habit_score == 0.91
        assert state.active_files == ["/tmp/proj/a.py", "/tmp/proj/b.py"]
        assert state.tool_calls_total == 12
        assert state.estimated_tokens_pct == 40.0


class TestSessionEndCleanup:
    """Tests for RuntimeState and dynamic file cleanup on SessionEnd."""

    def test_deletes_runtime_state(self, tmp_path):
        from lintgate.hooks.session_end import handle

        # Create state
        state = RuntimeState(mode="habit")
        save_runtime_state(str(tmp_path), state)
        assert load_runtime_state(str(tmp_path)) is not None

        handle({"cwd": str(tmp_path)})

        assert load_runtime_state(str(tmp_path)) is None

    def test_deletes_dynamic_files_claude(self, tmp_path):
        from lintgate.hooks.session_end import handle

        # Create .claude/rules with dynamic files
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "lg_session.md").write_text("session state")
        (rules_dir / "lg_focus.md").write_text("focus state")

        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)

        handle({"cwd": str(tmp_path)})

        assert not (rules_dir / "lg_session.md").exists()
        assert not (rules_dir / "lg_focus.md").exists()

    def test_preserves_static_rule_files(self, tmp_path):
        from lintgate.hooks.session_end import handle

        # Create .claude/rules with both dynamic and static files
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "lg_session.md").write_text("dynamic")
        (rules_dir / "theory.md").write_text("static — should survive")

        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)

        handle({"cwd": str(tmp_path)})

        # Static file should survive
        assert (rules_dir / "theory.md").exists()
        assert (rules_dir / "theory.md").read_text() == "static — should survive"

    def test_always_returns_continue(self, tmp_path):
        from lintgate.hooks.session_end import handle

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_cleanup_fail_open(self, tmp_path, monkeypatch):
        """Cleanup failure should not break the hook."""
        from lintgate.hooks.session_end import handle

        # Make delete_runtime_state raise
        def failing_delete(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(
            "lintgate.runtime_state.delete_runtime_state", failing_delete
        )

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_no_error_when_no_state_exists(self, tmp_path):
        """SessionEnd with no RuntimeState should not error."""
        from lintgate.hooks.session_end import handle

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_no_error_when_no_dynamic_files(self, tmp_path):
        """SessionEnd with RuntimeState but no dynamic files should not error."""
        from lintgate.hooks.session_end import handle

        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_deletes_runtime_lock_file(self, tmp_path):
        from lintgate.hooks.session_end import handle

        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)
        lock_file = tmp_path / ".lintgate" / "runtime_state.lock"
        lock_file.write_text("")

        handle({"cwd": str(tmp_path)})

        assert not lock_file.exists()


class TestFullLifecycle:
    """Integration tests for the full session start → end lifecycle."""

    def test_start_creates_end_cleans(self, tmp_path):
        from lintgate.hooks.session_end import handle as end_handle
        from lintgate.hooks.session_start import handle as start_handle

        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        # Start creates everything
        start_handle({"cwd": str(tmp_path)})
        assert load_runtime_state(str(tmp_path)) is not None
        assert (rules_dir / "lg_session.md").exists()
        assert (rules_dir / "lg_focus.md").exists()

        # End cleans everything
        end_handle({"cwd": str(tmp_path)})
        assert load_runtime_state(str(tmp_path)) is None
        assert not (rules_dir / "lg_session.md").exists()
        assert not (rules_dir / "lg_focus.md").exists()

    def test_multiple_starts_dont_corrupt(self, tmp_path):
        """Multiple SessionStart calls should produce valid state."""
        from lintgate.hooks.session_start import handle as start_handle

        start_handle({"cwd": str(tmp_path)})
        state1 = load_runtime_state(str(tmp_path))
        assert state1 is not None
        assert state1.generation >= 1

        start_handle({"cwd": str(tmp_path)})
        state2 = load_runtime_state(str(tmp_path))
        assert state2 is not None
        assert state2.generation >= 1
        # Each start creates a fresh state — both are valid
        assert state2.mode == "normal"
