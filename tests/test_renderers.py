"""Tests for the renderer registry, auto-detection, and default renderers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.renderers import RendererRegistry, build_default_registry

# ── Stub renderer ───────────────────────────────────────────────────


@dataclass
class StubRenderer:
    name: str = "stub"
    output_paths: list[str] = field(default_factory=lambda: ["stub.md"])

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        summary = compass.axes.get("problem", CompassAxis(name="problem")).summary
        return {"stub.md": f"# Stub\n{summary}"}


# ── RendererRegistry ────────────────────────────────────────────────


def test_register_and_get() -> None:
    reg = RendererRegistry()
    stub = StubRenderer()
    reg.register(stub)
    assert reg.get("stub") is stub


def test_get_missing_returns_none() -> None:
    reg = RendererRegistry()
    assert reg.get("nonexistent") is None


def test_list_available_sorted() -> None:
    reg = RendererRegistry()
    reg.register(StubRenderer(name="zeta"))
    reg.register(StubRenderer(name="alpha"))
    assert reg.list_available() == ["alpha", "zeta"]


# ── detect_tools ────────────────────────────────────────────────────


def test_detect_tools_finds_cursor_dir(tmp_path: object) -> None:
    """detect_tools should find tools based on directory presence."""
    root = str(tmp_path)
    os.makedirs(os.path.join(root, ".cursor"))
    os.makedirs(os.path.join(root, ".github"))

    reg = RendererRegistry()
    found = reg.detect_tools(root)
    assert "cursor" in found
    assert "copilot" in found


def test_detect_tools_empty_project(tmp_path: object) -> None:
    reg = RendererRegistry()
    found = reg.detect_tools(str(tmp_path))
    assert found == []


def test_detect_tools_windsurf_and_cline(tmp_path: object) -> None:
    root = str(tmp_path)
    os.makedirs(os.path.join(root, ".windsurf"))
    os.makedirs(os.path.join(root, ".clinerules"))

    reg = RendererRegistry()
    found = reg.detect_tools(root)
    assert "windsurf" in found
    assert "cline" in found


# ── render_for_targets ──────────────────────────────────────────────


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
        },
        directives=[
            CompassDirective(kind="toward", text="Prefer deterministic checks"),
        ],
    )


def test_render_for_targets_produces_output() -> None:
    reg = RendererRegistry()
    reg.register(StubRenderer())
    compass = _sample_compass()
    results = reg.render_for_targets(["stub"], compass, {"project_name": "test"})
    assert "stub.md" in results
    assert "Quality supervision" in results["stub.md"]


def test_render_for_targets_unknown_target_skipped() -> None:
    reg = RendererRegistry()
    reg.register(StubRenderer())
    compass = _sample_compass()
    results = reg.render_for_targets(["unknown"], compass, {"project_name": "test"})
    assert results == {}


def test_render_for_targets_multiple_targets() -> None:
    reg = RendererRegistry()
    reg.register(StubRenderer(name="a", output_paths=["a.md"]))
    reg.register(StubRenderer(name="b", output_paths=["b.md"]))
    compass = _sample_compass()
    results = reg.render_for_targets(["a", "b"], compass, {})
    # Both renderers produce "stub.md" key (same StubRenderer logic)
    # but both should be called
    assert len(results) >= 1


# ── build_default_registry ──────────────────────────────────────────


def test_build_default_registry_has_all_renderers() -> None:
    reg = build_default_registry()
    available = reg.list_available()
    expected = [
        "agents",
        "aider",
        "claude",
        "cline",
        "copilot",
        "cursor",
        "generic",
        "windsurf",
    ]
    assert available == expected
    assert len(available) == 8


def test_default_registry_renderers_produce_output() -> None:
    """Each default renderer should produce non-empty output for a valid compass."""
    reg = build_default_registry()
    compass = _sample_compass()
    metadata = {"project_name": "testproject"}
    for name in reg.list_available():
        renderer = reg.get(name)
        assert renderer is not None
        result = renderer.render(compass, metadata)
        assert isinstance(result, dict)
        assert len(result) > 0, f"Renderer {name} produced no output"
        for path, content in result.items():
            assert isinstance(content, str)
            assert len(content) > 0, f"Renderer {name} produced empty content for {path}"


def test_renderer_project_name_falls_back_to_project_root(tmp_path: object) -> None:
    reg = build_default_registry()
    compass = _sample_compass()
    renderer = reg.get("generic")
    assert renderer is not None

    metadata = {"project_root": str(tmp_path)}
    result = renderer.render(compass, metadata)
    text = result["CONTEXT.md"]
    assert f"# {os.path.basename(str(tmp_path))} Context" in text


# ── empty compass handling ──────────────────────────────────────────


def test_empty_compass_doesnt_crash_renderers() -> None:
    """Renderers should handle a completely empty compass without errors."""
    reg = build_default_registry()
    empty = CompassState()
    metadata = {"project_name": "empty"}
    for name in reg.list_available():
        renderer = reg.get(name)
        assert renderer is not None
        # Should not raise
        result = renderer.render(empty, metadata)
        assert isinstance(result, dict)
