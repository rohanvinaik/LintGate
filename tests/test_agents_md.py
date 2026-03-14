"""Tests for lintgate/renderers/agents_md.py — AGENTS.md renderer."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers.agents_md import AgentsMdRenderer


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
    r = AgentsMdRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    assert "AGENTS.md" in result


def test_render_contains_mission() -> None:
    r = AgentsMdRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["AGENTS.md"]
    assert "Quality supervision." in content


def test_render_contains_execution_contract() -> None:
    r = AgentsMdRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["AGENTS.md"]
    assert "Execution Contract" in content
    assert "minimal diffs" in content


def test_render_contains_directives() -> None:
    r = AgentsMdRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["AGENTS.md"]
    assert "Prefer deterministic" in content
    assert "Avoid heuristics" in content


def test_render_contains_handoff() -> None:
    r = AgentsMdRenderer()
    result = r.render(_sample_compass(), {"project_name": "test"})
    content = result["AGENTS.md"]
    assert "Handoff Expectations" in content


def test_render_empty_compass() -> None:
    r = AgentsMdRenderer()
    result = r.render(CompassState(), {"project_name": "empty"})
    content = result["AGENTS.md"]
    assert "Write correct code." in content


def test_agents_md_renderer_name() -> None:
    assert AgentsMdRenderer.name == "agents"
