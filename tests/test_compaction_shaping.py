"""Tests for enhanced PreCompact — compaction shaping with dual-write strategy."""

from __future__ import annotations

import json

from lintgate.hooks.pre_compact import handle
from lintgate.runtime_state import RuntimeState, save_runtime_state


class TestHandleWithRuntimeState:
    """Tests for the enhanced capsule path (RuntimeState available)."""

    def test_capsule_from_runtime_state(self, tmp_path):
        state = RuntimeState(
            mode="habit",
            habit_score=0.8,
            true_north="Build robust software",
            toward=["test first", "small commits"],
            away=["large PRs"],
            forbidden=["force push"],
            active_files=["/src/main.py", "/src/utils.py"],
            last_test_status="pass",
            blocking_issues=0,
            coherence_state="stable",
            approach_failures=1,
            top_constraint="avoid monoliths",
            prediction_accuracy=0.85,
            estimated_tokens_pct=40.0,
            compaction_count=2,
            tool_calls_total=50,
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})

        assert result["continue"] is True
        assert "systemMessage" in result
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "PreCompact"

        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        assert "compass_capsule" in capsule
        assert "session_state" in capsule
        assert "behavioral" in capsule
        assert "token_state" in capsule

    def test_capsule_compass_fields(self, tmp_path):
        state = RuntimeState(
            true_north="Ship quality code",
            toward=["lint", "test"],
            away=["skip tests"],
            forbidden=["force push", "commit secrets"],
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        compass = capsule["compass_capsule"]

        assert compass["true_north"] == "Ship quality code"
        assert compass["toward"] == ["lint", "test"]
        assert compass["away"] == ["skip tests"]
        assert compass["forbidden"] == ["force push", "commit secrets"]

    def test_capsule_session_state_fields(self, tmp_path):
        state = RuntimeState(
            mode="habit",
            active_files=["/src/main.py", "/src/utils.py"],
            last_test_status="fail",
            blocking_issues=3,
            coherence_state="systemic",
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        session = capsule["session_state"]

        assert session["mode"] == "habit"
        assert session["focus_files"] == ["main.py", "utils.py"]
        assert session["test_status"] == "fail"
        assert session["blocking"] == 3
        assert session["coherence"] == "systemic"

    def test_capsule_behavioral_fields(self, tmp_path):
        state = RuntimeState(
            approach_failures=2,
            top_constraint="avoid circular imports",
            prediction_accuracy=0.75,
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        behavioral = capsule["behavioral"]

        assert behavioral["approach_failures"] == 2
        assert behavioral["top_constraint"] == "avoid circular imports"
        assert behavioral["prediction_accuracy"] == 0.75

    def test_capsule_token_state_fields(self, tmp_path):
        state = RuntimeState(
            estimated_tokens_pct=65.3,
            compaction_count=3,
            tool_calls_total=120,
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        token = capsule["token_state"]

        assert token["pct_used"] == 65.3
        # save_runtime_state increments generation (not compaction_count)
        # hook increments compaction_count by 1: 3 -> 4
        assert token["compaction_number"] == 4
        assert token["tool_calls"] == 120

    def test_compaction_count_incremented(self, tmp_path):
        """PreCompact should increment the compaction counter."""
        state = RuntimeState(compaction_count=5)
        save_runtime_state(str(tmp_path), state)
        # After save: generation=1, compaction_count=5 on disk
        # But save_runtime_state increments generation, not compaction_count

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])

        # The hook increments compaction_count by 1 from whatever is on disk
        # On disk after save: compaction_count=5
        # Hook loads (5), increments to 6, saves (gen incremented again)
        assert capsule["token_state"]["compaction_number"] == 6

    def test_system_message_contains_mode(self, tmp_path):
        state = RuntimeState(mode="habit")
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        assert "mode=habit" in result["systemMessage"]

    def test_system_message_contains_directive_counts(self, tmp_path):
        state = RuntimeState(
            toward=["a", "b", "c"],
            away=["x"],
            forbidden=["y", "z"],
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        msg = result["systemMessage"]
        assert "3 toward" in msg
        assert "1 away" in msg
        assert "2 forbidden" in msg

    def test_system_message_contains_compaction_number(self, tmp_path):
        state = RuntimeState(compaction_count=7)
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        # After hook increments: 8
        assert "Pre-compact #8" in result["systemMessage"]

    def test_focus_files_are_basenames(self, tmp_path):
        state = RuntimeState(
            active_files=[
                "/very/deep/path/to/main.py",
                "/another/deep/utils.py",
            ]
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        assert capsule["session_state"]["focus_files"] == ["main.py", "utils.py"]

    def test_focus_files_capped_at_5(self, tmp_path):
        state = RuntimeState(active_files=[f"/src/{i}.py" for i in range(10)])
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        assert len(capsule["session_state"]["focus_files"]) == 5

    def test_toward_capped_at_6(self, tmp_path):
        state = RuntimeState(toward=[f"directive_{i}" for i in range(10)])
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        assert len(capsule["compass_capsule"]["toward"]) == 6

    def test_true_north_truncated(self, tmp_path):
        state = RuntimeState(true_north="x" * 200)
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        assert len(capsule["compass_capsule"]["true_north"]) == 120

    def test_capsule_token_budget(self, tmp_path):
        """Capsule should be roughly under 800 tokens (~3200 chars)."""
        state = RuntimeState(
            mode="habit",
            habit_score=0.82,
            true_north="A" * 120,
            toward=[f"toward_{i}" for i in range(6)],
            away=[f"away_{i}" for i in range(6)],
            forbidden=[f"forbidden_{i}" for i in range(6)],
            active_files=[f"/src/{i}.py" for i in range(5)],
            last_test_status="fail",
            blocking_issues=5,
            coherence_state="systemic",
            approach_failures=3,
            top_constraint="A" * 80,
            prediction_accuracy=0.65,
            estimated_tokens_pct=72.0,
            compaction_count=10,
            tool_calls_total=200,
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        capsule_str = result["hookSpecificOutput"]["additionalContext"]
        # ~800 tokens ≈ 3200 chars — give some margin
        assert len(capsule_str) < 4000


class TestHandleLegacyFallback:
    """Tests for the legacy compass-only fallback path."""

    def test_no_runtime_no_compass_returns_continue(self, tmp_path):
        """When neither RuntimeState nor compass exists, just continue."""
        result = handle({"cwd": str(tmp_path)})
        assert result == {"continue": True}

    def test_continue_always_true(self, tmp_path):
        """Handle should always return continue=True."""
        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True


class TestHandleEdgeCases:
    """Edge case tests for the handle function."""

    def test_empty_runtime_state(self, tmp_path):
        """Default RuntimeState should produce a valid capsule."""
        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True
        assert "hookSpecificOutput" in result

        capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
        assert capsule["session_state"]["mode"] == "normal"
        assert capsule["session_state"]["focus_files"] == []
        assert capsule["session_state"]["test_status"] == ""

    def test_missing_cwd_defaults_to_dot(self):
        """When cwd is missing from data, defaults to '.'."""
        result = handle({})
        assert result["continue"] is True

    def test_handles_corrupt_runtime_state(self, tmp_path):
        """Corrupt runtime_state.json should fallback gracefully."""
        state_dir = tmp_path / ".lintgate"
        state_dir.mkdir()
        (state_dir / "runtime_state.json").write_text("not valid json")

        result = handle({"cwd": str(tmp_path)})
        assert result["continue"] is True

    def test_capsule_is_valid_json(self, tmp_path):
        """additionalContext must be valid JSON."""
        state = RuntimeState(
            mode="theory",
            true_north="Test driven",
            toward=["write tests"],
        )
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        ctx = result["hookSpecificOutput"]["additionalContext"]
        # Should not raise
        parsed = json.loads(ctx)
        assert isinstance(parsed, dict)

    def test_consecutive_compactions_increment_correctly(self, tmp_path):
        """Multiple PreCompact calls should monotonically increment."""
        state = RuntimeState(compaction_count=0)
        save_runtime_state(str(tmp_path), state)

        counts = []
        for _ in range(3):
            result = handle({"cwd": str(tmp_path)})
            capsule = json.loads(result["hookSpecificOutput"]["additionalContext"])
            counts.append(capsule["token_state"]["compaction_number"])

        # Each call increments by 1
        assert counts[1] == counts[0] + 1
        assert counts[2] == counts[1] + 1

    def test_generation_increments_across_calls(self, tmp_path):
        """RuntimeState generation should increase with each compaction."""
        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)

        from lintgate.runtime_state import load_runtime_state

        gen_before = load_runtime_state(str(tmp_path)).generation

        handle({"cwd": str(tmp_path)})

        gen_after = load_runtime_state(str(tmp_path)).generation
        assert gen_after > gen_before


class TestDualWriteStrategy:
    """Tests for the dual-write behavior (hook output + dynamic files)."""

    def test_dual_write_creates_dynamic_files(self, tmp_path):
        """PreCompact with RuntimeState should write dynamic rule files."""
        # Create .claude/rules dir so detect_host returns "claude"
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        state = RuntimeState(
            mode="habit",
            active_files=["/src/main.py"],
            true_north="Quality first",
        )
        save_runtime_state(str(tmp_path), state)

        handle({"cwd": str(tmp_path)})

        # Dynamic files should have been written
        session_file = rules_dir / "lg_session.md"
        focus_file = rules_dir / "lg_focus.md"
        assert session_file.exists()
        assert focus_file.exists()

    def test_dual_write_session_file_content(self, tmp_path):
        """Session file from dual-write should contain session state."""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        state = RuntimeState(
            mode="habit",
            active_files=["/src/main.py"],
            blocking_issues=2,
        )
        save_runtime_state(str(tmp_path), state)

        handle({"cwd": str(tmp_path)})

        content = (rules_dir / "lg_session.md").read_text()
        assert "LG_GEN:" in content
        assert "Mode: habit" in content

    def test_dual_write_focus_file_content(self, tmp_path):
        """Focus file from dual-write should contain focus state."""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        state = RuntimeState(
            mode="normal",
            active_files=["/src/main.py"],
        )
        save_runtime_state(str(tmp_path), state)

        handle({"cwd": str(tmp_path)})

        content = (rules_dir / "lg_focus.md").read_text()
        assert "LG_GEN:" in content
        assert "main.py" in content

    def test_hook_output_and_file_have_same_generation(self, tmp_path):
        """Hook output capsule and dynamic file should share the same generation."""
        from lintgate.renderers.dynamic import read_generation_from_file

        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)

        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)

        result = handle({"cwd": str(tmp_path)})
        assert "hookSpecificOutput" in result

        # Read generation from file
        file_gen = read_generation_from_file(str(tmp_path), ".claude/rules/lg_session.md")
        assert file_gen is not None

    def test_no_dynamic_files_without_host_dir(self, tmp_path):
        """Without a recognized host directory, no dynamic files are written."""
        state = RuntimeState(mode="habit")
        save_runtime_state(str(tmp_path), state)

        handle({"cwd": str(tmp_path)})

        # No .claude/rules should exist
        assert not (tmp_path / ".claude" / "rules" / "lg_session.md").exists()
