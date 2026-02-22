"""Tests for dynamic rule file rendering."""

from __future__ import annotations

from lintgate.renderers.dynamic import (
    _CHARS_PER_TOKEN,
    FOCUS_TOKEN_BUDGET,
    SESSION_TOKEN_BUDGET,
    delete_dynamic_file,
    read_generation_from_file,
    render_focus_content,
    render_session_content,
    write_dynamic_file,
)
from lintgate.runtime_state import RuntimeState

# ── Fixtures ─────────────────────────────────────────────────────────


def _make_runtime(**kwargs) -> RuntimeState:
    defaults = {
        "generation": 42,
        "timestamp": 1708567890.0,
        "mode": "habit",
        "habit_score": 0.82,
        "true_north": "Build reliable systems",
        "toward": ["test first", "small functions"],
        "away": ["god objects", "deep nesting"],
        "forbidden": ["eval()"],
        "active_files": ["/src/main.py", "/src/utils.py", "/tests/test_main.py"],
        "last_test_status": "pass",
        "blocking_issues": 0,
        "warning_issues": 3,
        "coherence_state": "stable",
        "prediction_accuracy": 0.75,
        "estimated_tokens_pct": 35.0,
        "tool_calls_total": 25,
        "compaction_count": 1,
        "top_constraint": "no eval",
        "approach_failures": 0,
    }
    defaults.update(kwargs)
    return RuntimeState(**defaults)


# ── render_session_content ───────────────────────────────────────────


class TestRenderSessionContent:
    def test_contains_generation_watermark(self):
        rt = _make_runtime()
        content = render_session_content(rt)
        assert "LG_GEN:42" in content
        assert "LG_TS:1708567890" in content

    def test_contains_mode(self):
        rt = _make_runtime(mode="habit", habit_score=0.82)
        content = render_session_content(rt)
        assert "Mode: habit (score: 0.82)" in content

    def test_normal_mode_no_score(self):
        rt = _make_runtime(mode="normal", habit_score=0.0)
        content = render_session_content(rt)
        assert "Mode: normal" in content
        assert "score" not in content

    def test_contains_active_files(self):
        rt = _make_runtime()
        content = render_session_content(rt)
        assert "main.py" in content
        assert "utils.py" in content

    def test_contains_coherence(self):
        rt = _make_runtime(coherence_state="isolated")
        content = render_session_content(rt)
        assert "Coherence: isolated" in content

    def test_contains_test_status(self):
        rt = _make_runtime(last_test_status="fail")
        content = render_session_content(rt)
        assert "Tests: fail" in content

    def test_contains_blocking_count(self):
        rt = _make_runtime(blocking_issues=3)
        content = render_session_content(rt)
        assert "Blocking: 3" in content

    def test_contains_compass_directives(self):
        rt = _make_runtime()
        content = render_session_content(rt)
        assert "True North: Build reliable systems" in content
        assert "test first" in content
        assert "god objects" in content
        assert "eval()" in content

    def test_contains_behavioral_signals(self):
        rt = _make_runtime(approach_failures=2, prediction_accuracy=0.6)
        content = render_session_content(rt)
        assert "Failed approaches: 2" in content
        assert "Prediction accuracy: 60%" in content

    def test_contains_token_economics(self):
        rt = _make_runtime()
        content = render_session_content(rt)
        assert "35%" in content
        assert "Tools: 25" in content

    def test_token_budget_enforced(self):
        rt = _make_runtime(
            toward=[f"directive_{i}" for i in range(50)],
            away=[f"avoid_{i}" for i in range(50)],
        )
        content = render_session_content(rt)
        assert len(content) <= SESSION_TOKEN_BUDGET * _CHARS_PER_TOKEN

    def test_empty_runtime_renders(self):
        rt = RuntimeState()
        content = render_session_content(rt)
        assert "LG_GEN:0" in content
        assert "Mode: normal" in content


# ── render_focus_content ─────────────────────────────────────────────


class TestRenderFocusContent:
    def test_contains_generation_watermark(self):
        rt = _make_runtime()
        content = render_focus_content(rt)
        assert "LG_GEN:42" in content

    def test_contains_focus_intent(self):
        rt = _make_runtime(focus_intent="Fix the authentication bug")
        content = render_focus_content(rt)
        assert "Fix the authentication bug" in content

    def test_contains_active_files_basenames(self):
        rt = _make_runtime()
        content = render_focus_content(rt)
        assert "main.py" in content

    def test_contains_mode(self):
        rt = _make_runtime()
        content = render_focus_content(rt)
        assert "Mode: habit" in content

    def test_token_budget_enforced(self):
        rt = _make_runtime(focus_intent="x" * 2000)
        content = render_focus_content(rt)
        assert len(content) <= FOCUS_TOKEN_BUDGET * _CHARS_PER_TOKEN

    def test_empty_runtime_renders(self):
        rt = RuntimeState()
        content = render_focus_content(rt)
        assert "LG_GEN:0" in content
        assert "Mode: normal" in content


# ── File operations ──────────────────────────────────────────────────


class TestFileOperations:
    def test_write_and_read_generation(self, tmp_path):
        content = "<!-- LG_GEN:42 LG_TS:1234 -->\n# Test"
        assert write_dynamic_file(str(tmp_path), "rules/test.md", content) is True
        assert read_generation_from_file(str(tmp_path), "rules/test.md") == 42

    def test_write_creates_directories(self, tmp_path):
        content = "test"
        assert write_dynamic_file(str(tmp_path), "deep/nested/file.md", content) is True
        assert (tmp_path / "deep" / "nested" / "file.md").exists()

    def test_delete_existing(self, tmp_path):
        (tmp_path / "test.md").write_text("content")
        assert delete_dynamic_file(str(tmp_path), "test.md") is True
        assert not (tmp_path / "test.md").exists()

    def test_delete_missing(self, tmp_path):
        assert delete_dynamic_file(str(tmp_path), "missing.md") is False

    def test_read_generation_missing_file(self, tmp_path):
        assert read_generation_from_file(str(tmp_path), "missing.md") is None

    def test_read_generation_no_watermark(self, tmp_path):
        (tmp_path / "test.md").write_text("# No watermark here")
        assert read_generation_from_file(str(tmp_path), "test.md") is None


# ── Per-host renderer integration ────────────────────────────────────


class TestClaudeRendererDynamic:
    def test_render_session(self):
        from lintgate.renderers.claude import ClaudeRenderer

        renderer = ClaudeRenderer()
        rt = _make_runtime()
        result = renderer.render_session(rt)
        assert ".claude/rules/lg_session.md" in result
        content = result[".claude/rules/lg_session.md"]
        # Claude gets frontmatter
        assert "---" in content
        assert "**/*.py" in content
        assert "LG_GEN:42" in content

    def test_render_focus(self):
        from lintgate.renderers.claude import ClaudeRenderer

        renderer = ClaudeRenderer()
        rt = _make_runtime()
        result = renderer.render_focus(rt)
        assert ".claude/rules/lg_focus.md" in result
        content = result[".claude/rules/lg_focus.md"]
        assert "---" in content
        assert "LG_GEN:42" in content

    def test_cleanup_dynamic(self, tmp_path):
        from lintgate.renderers.claude import ClaudeRenderer

        renderer = ClaudeRenderer()
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "lg_session.md").write_text("session")
        (rules_dir / "lg_focus.md").write_text("focus")

        deleted = renderer.cleanup_dynamic(str(tmp_path))
        assert len(deleted) == 2
        assert not (rules_dir / "lg_session.md").exists()
        assert not (rules_dir / "lg_focus.md").exists()


class TestCursorRendererDynamic:
    def test_render_session(self):
        from lintgate.renderers.cursor import CursorRenderer

        renderer = CursorRenderer()
        rt = _make_runtime()
        result = renderer.render_session(rt)
        assert ".cursor/rules/lg_session.mdc" in result
        content = result[".cursor/rules/lg_session.mdc"]
        assert "LG_GEN:42" in content
        # Cursor gets .mdc frontmatter
        assert "description:" in content

    def test_render_focus(self):
        from lintgate.renderers.cursor import CursorRenderer

        renderer = CursorRenderer()
        rt = _make_runtime()
        result = renderer.render_focus(rt)
        assert ".cursor/rules/lg_focus.mdc" in result


class TestWindsurfRendererDynamic:
    def test_render_session(self):
        from lintgate.renderers.windsurf import WindsurfRenderer

        renderer = WindsurfRenderer()
        rt = _make_runtime()
        result = renderer.render_session(rt)
        assert ".windsurf/rules/lg_session.md" in result
        content = result[".windsurf/rules/lg_session.md"]
        assert "LG_GEN:42" in content
        # Windsurf gets no frontmatter
        assert "---" not in content


class TestClineRendererDynamic:
    def test_render_session(self):
        from lintgate.renderers.cline import ClineRenderer

        renderer = ClineRenderer()
        rt = _make_runtime()
        result = renderer.render_session(rt)
        assert ".clinerules/lg_session.md" in result

    def test_cleanup_dynamic(self, tmp_path):
        from lintgate.renderers.cline import ClineRenderer

        renderer = ClineRenderer()
        rules_dir = tmp_path / ".clinerules"
        rules_dir.mkdir()
        (rules_dir / "lg_session.md").write_text("session")

        deleted = renderer.cleanup_dynamic(str(tmp_path))
        assert ".clinerules/lg_session.md" in deleted
