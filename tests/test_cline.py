"""Tests for lintgate/renderers/cline.py — Cline renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.cline import ClineRenderer


def _sample_compass() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Quality supervision.",
                claims=[CompassClaim(text="real-time checks")],
                depth=2,
            ),
            "solution": CompassAxis(
                name="solution",
                summary="Lint convergence.",
                claims=[CompassClaim(text="lint + test")],
                depth=2,
            ),
        },
        directives=[
            CompassDirective(kind="toward", text="Prefer deterministic checks"),
            CompassDirective(kind="away", text="Avoid noisy heuristics"),
            CompassDirective(kind="forbidden", text="Never disable lint"),
        ],
    )


def test_render_produces_correct_path() -> None:
    r = ClineRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert ".clinerules/compass.md" in result


def test_render_contains_mission() -> None:
    r = ClineRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".clinerules/compass.md"]
    assert "Quality supervision." in content


def test_render_contains_directives() -> None:
    r = ClineRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".clinerules/compass.md"]
    assert "Prefer deterministic checks" in content
    assert "Avoid noisy heuristics" in content
    assert "Never disable lint" in content


def test_render_includes_claims() -> None:
    r = ClineRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result[".clinerules/compass.md"]
    assert "real-time checks" in content


def test_render_empty_compass() -> None:
    r = ClineRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result[".clinerules/compass.md"]
    assert "Write correct code." in content


def test_cline_renderer_name() -> None:
    assert ClineRenderer.name == "cline"
