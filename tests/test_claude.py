"""Tests for lintgate/renderers/claude.py — Claude renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.claude import ClaudeRenderer


def _sample_compass() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Quality supervision for AI code.",
                claims=[CompassClaim(text="real-time checks")],
                depth=2,
            ),
            "solution": CompassAxis(
                name="solution",
                summary="Multi-channel lint convergence.",
                claims=[CompassClaim(text="lint + test + git")],
                depth=2,
            ),
            "implementation": CompassAxis(
                name="implementation",
                summary="Python + MCP server.",
                depth=1,
            ),
        },
        directives=[
            CompassDirective(kind="toward", text="Prefer deterministic checks"),
            CompassDirective(kind="away", text="Avoid noisy heuristics"),
            CompassDirective(kind="forbidden", text="Never disable lint"),
        ],
    )


# ── render ────────────────────────────────────────────────────────


def test_render_produces_both_files() -> None:
    r = ClaudeRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert ".claude/CLAUDE.md" in result
    assert ".claude/rules/theory.md" in result


def test_render_claude_md_contains_mission() -> None:
    r = ClaudeRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".claude/CLAUDE.md"]
    assert "Quality supervision" in content


def test_render_claude_md_contains_directives() -> None:
    r = ClaudeRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".claude/CLAUDE.md"]
    assert "Prefer deterministic checks" in content
    assert "Avoid noisy heuristics" in content
    assert "LINTGATE_FORBID" in content


def test_render_theory_md_has_axes() -> None:
    r = ClaudeRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".claude/rules/theory.md"]
    assert "Problem:" in content
    assert "Solution:" in content


def test_render_empty_compass() -> None:
    r = ClaudeRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    assert ".claude/CLAUDE.md" in result
    assert "Write correct, maintainable code." in result[".claude/CLAUDE.md"]


def test_render_includes_implementation_notes() -> None:
    r = ClaudeRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".claude/CLAUDE.md"]
    assert "Implementation Notes" in content
    assert "Python + MCP server." in content


# ── name / output_paths ──────────────────────────────────────────


def test_claude_renderer_name() -> None:
    assert ClaudeRenderer.name == "claude"


def test_claude_renderer_output_paths() -> None:
    assert ".claude/CLAUDE.md" in ClaudeRenderer.output_paths
