"""Tests for lintgate/renderers/aider.py — Aider renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.aider import AiderRenderer


def _sample_compass() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Quality supervision.",
                claims=[CompassClaim(text="checks")],
                depth=2,
            ),
            "implementation": CompassAxis(
                name="implementation",
                summary="Python codebase.",
                depth=1,
            ),
        },
        directives=[
            CompassDirective(kind="toward", text="Prefer deterministic"),
            CompassDirective(kind="away", text="Avoid heuristics"),
            CompassDirective(kind="forbidden", text="No global state"),
        ],
    )


def test_render_produces_correct_path() -> None:
    r = AiderRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert "CONVENTIONS.md" in result


def test_render_contains_mission() -> None:
    r = AiderRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["CONVENTIONS.md"]
    assert "Quality supervision." in content


def test_render_contains_implementation() -> None:
    r = AiderRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["CONVENTIONS.md"]
    assert "Python codebase." in content


def test_render_contains_directives() -> None:
    r = AiderRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["CONVENTIONS.md"]
    assert "Prefer deterministic" in content
    assert "Avoid heuristics" in content
    assert "No global state" in content


def test_render_empty_compass() -> None:
    r = AiderRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result["CONVENTIONS.md"]
    assert "Write correct code." in content


def test_aider_renderer_name() -> None:
    assert AiderRenderer.name == "aider"
