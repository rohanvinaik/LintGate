"""Tests for lintgate/renderers/cursor.py — Cursor renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.cursor import CursorRenderer


def _sample_compass() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Quality supervision.",
                claims=[CompassClaim(text="checks")],
                depth=2,
            ),
        },
        directives=[
            CompassDirective(kind="toward", text="Prefer deterministic"),
            CompassDirective(kind="away", text="Avoid heuristics"),
            CompassDirective(kind="forbidden", text="No global state"),
        ],
    )


def test_render_produces_correct_path() -> None:
    r = CursorRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert ".cursor/rules/compass.mdc" in result


def test_render_contains_frontmatter() -> None:
    r = CursorRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".cursor/rules/compass.mdc"]
    assert "description:" in content
    assert "globs:" in content


def test_render_contains_mission() -> None:
    r = CursorRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".cursor/rules/compass.mdc"]
    assert "Quality supervision." in content


def test_render_contains_directives() -> None:
    r = CursorRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".cursor/rules/compass.mdc"]
    assert "Prefer deterministic" in content
    assert "Avoid heuristics" in content
    assert "No global state" in content


def test_render_empty_compass() -> None:
    r = CursorRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result[".cursor/rules/compass.mdc"]
    assert "Write correct code." in content


def test_cursor_renderer_name() -> None:
    assert CursorRenderer.name == "cursor"
