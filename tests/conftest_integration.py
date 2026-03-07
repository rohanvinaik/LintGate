"""Shared fixtures and helpers for integration pipeline tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "integration_projects"


def _copy_fixture(tmp_path: Path, fixture_name: str) -> str:
    """Copy a fixture project to tmp_path and return the project root."""
    src = _FIXTURES_DIR / fixture_name
    dst = tmp_path / fixture_name
    shutil.copytree(src, dst)
    return str(dst)


@pytest.fixture
def pure_calculator(tmp_path):
    """Create pure_calculator fixture project, return path."""
    return _copy_fixture(tmp_path, "pure_calculator")


@pytest.fixture
def stateful_service(tmp_path):
    """Create stateful_service fixture project, return path."""
    return _copy_fixture(tmp_path, "stateful_service")


@pytest.fixture
def cross_module(tmp_path):
    """Create cross_module fixture project, return path."""
    return _copy_fixture(tmp_path, "cross_module")


def run_channel(project_root: str, channel_name: str):
    """Run a single channel on a project and return the ChannelResult."""
    # Build manifests via prepass
    from lintgate.channels.structure_channel import _discover_python_files
    from lintgate.controlplane.types import (
        ControlPlaneConfig,
        SupervisionEvent,
    )

    py_files = _discover_python_files(project_root)
    test_files = [f for f in py_files if os.path.basename(f).startswith("test_")]
    source_files = [f for f in py_files if not os.path.basename(f).startswith("test_")]

    context: dict = {"python_files": py_files}

    # Build property manifest
    if channel_name in ("performance", "specification", "test_effectiveness"):
        from lintgate.linters.performance_checks.manifest import build_manifest

        prop_manifest = build_manifest(project_root, source_files)
        context["property_manifest"] = prop_manifest

    # Build teff manifest
    if channel_name in ("test_effectiveness", "specification"):
        from lintgate.linters.test_effectiveness.manifest import (
            build_test_effectiveness_manifest,
        )

        teff_manifest = build_test_effectiveness_manifest(
            project_root, source_files, test_files
        )
        context["test_effectiveness_manifest"] = teff_manifest

    event = SupervisionEvent(
        surface="mcp",
        tool_name="controlplane_run",
        project_root=project_root,
        files_changed=[],
        context=context,
    )
    config = ControlPlaneConfig()

    # Import and instantiate the channel
    from mcp_tools.controlplane_tools import _build_channel_registry

    registry = _build_channel_registry()
    channel = registry.get(channel_name)
    if channel is None:
        raise ValueError(f"Unknown channel: {channel_name}")

    return channel.execute(event, config)


def run_pipeline(
    project_root: str,
    channels: str = "performance,test_effectiveness,specification",
):
    """Run multiple channels on a project and return dict of ChannelResults."""
    results = {}
    for name in channels.split(","):
        name = name.strip()
        results[name] = run_channel(project_root, name)
    return results
