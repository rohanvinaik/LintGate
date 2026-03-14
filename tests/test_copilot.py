"""Tests for lintgate/renderers/copilot.py — Copilot renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.copilot import CopilotRenderer


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
        ],
    )


def test_render_produces_correct_path() -> None:
    r = CopilotRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert ".github/copilot-instructions.md" in result


def test_render_contains_mission() -> None:
    r = CopilotRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".github/copilot-instructions.md"]
    assert "Quality supervision." in content


def test_render_contains_architecture() -> None:
    r = CopilotRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".github/copilot-instructions.md"]
    assert "Lint convergence." in content


def test_render_contains_directives() -> None:
    r = CopilotRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".github/copilot-instructions.md"]
    assert "Prefer deterministic" in content
    assert "Avoid heuristics" in content


def test_render_empty_compass() -> None:
    r = CopilotRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result[".github/copilot-instructions.md"]
    assert "Write correct code." in content


def test_copilot_renderer_name() -> None:
    assert CopilotRenderer.name == "copilot"
