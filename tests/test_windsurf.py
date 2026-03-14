"""Tests for lintgate/renderers/windsurf.py — Windsurf renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.windsurf import WindsurfRenderer


def _sample_compass() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Quality supervision.",
                claims=[CompassClaim(text="real-time checks")],
                depth=2,
            ),
            "implementation": CompassAxis(
                name="implementation",
                summary="Python + MCP.",
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
    r = WindsurfRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert ".windsurf/rules/compass.md" in result


def test_render_contains_mission() -> None:
    r = WindsurfRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".windsurf/rules/compass.md"]
    assert "Quality supervision." in content


def test_render_contains_implementation() -> None:
    r = WindsurfRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".windsurf/rules/compass.md"]
    assert "Python + MCP." in content


def test_render_contains_directives() -> None:
    r = WindsurfRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".windsurf/rules/compass.md"]
    assert "Prefer deterministic" in content
    assert "Avoid heuristics" in content
    assert "No global state" in content


def test_render_includes_axis_claims() -> None:
    r = WindsurfRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".windsurf/rules/compass.md"]
    assert "real-time checks" in content


def test_render_empty_compass() -> None:
    r = WindsurfRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result[".windsurf/rules/compass.md"]
    assert "Write correct code." in content


def test_windsurf_renderer_name() -> None:
    assert WindsurfRenderer.name == "windsurf"
