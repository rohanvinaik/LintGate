"""Tests for lintgate/renderers/generic.py — Generic renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.generic import GenericRenderer


def _sample_compass() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Quality supervision.",
                claims=[CompassClaim(text="checks")],
                depth=2,
            ),
            "solution": CompassAxis(
                name="solution",
                summary="Lint convergence.",
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
    r = GenericRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert "CONTEXT.md" in result


def test_render_contains_mission() -> None:
    r = GenericRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["CONTEXT.md"]
    assert "Quality supervision." in content


def test_render_contains_architecture() -> None:
    r = GenericRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["CONTEXT.md"]
    assert "Lint convergence." in content


def test_render_contains_directives() -> None:
    r = GenericRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["CONTEXT.md"]
    assert "Prefer deterministic" in content
    assert "Avoid heuristics" in content
    assert "No global state" in content


def test_render_empty_compass() -> None:
    r = GenericRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result["CONTEXT.md"]
    assert "Write correct code." in content


def test_generic_renderer_name() -> None:
    assert GenericRenderer.name == "generic"
