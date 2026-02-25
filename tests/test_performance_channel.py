from unittest.mock import MagicMock, patch

import pytest

from lintgate.channels.performance_channel import (
    PerformanceChannel,
    _analyze_optimization_opportunities,
    _analyze_purity_summary,
    _resolve_file,
)
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.linters.performance_checks.manifest import PropertyManifest


@pytest.fixture
def channel():
    return PerformanceChannel()

def test_resolve_file():
    manifest = PropertyManifest()
    # Mock get_source_file
    manifest.functions["func1"] = MagicMock(source_file="file1.py")
    assert _resolve_file(manifest, "func1", "root") == "file1.py"
    assert _resolve_file(manifest, "func2", "root") == "root"

def test_analyze_purity_summary_low_ratio():
    manifest = PropertyManifest(pure_count=1, impure_count=9)
    issues = _analyze_purity_summary(manifest, 10, 0.1, "/root")
    # Should be empty because total_funcs <= 10
    assert issues == []

    manifest = PropertyManifest(pure_count=1, impure_count=10)
    issues = _analyze_purity_summary(manifest, 11, 0.09, "/root")
    assert len(issues) == 1
    assert issues[0].kind == "PERFCH001"

def test_analyze_optimization_opportunities():
    manifest = PropertyManifest()
    manifest.optimization_potential = [
        ("func1", ["parallelizable", "cacheable"]),
        ("func2", ["cache-without-invalidation"]),
        ("func3", ["cacheable"])
    ]
    # Mock _resolve_file
    with patch("lintgate.channels.performance_channel._resolve_file", return_value="file.py"):
        issues = _analyze_optimization_opportunities(manifest, "/root")

    kinds = [i.kind for i in issues]
    assert "PERFCH003" in kinds # parallelizable
    assert "PERFCH004" in kinds # cache-without-invalidation
    assert "PERFCH005" in kinds # summary for cacheable



def test_performance_channel_metadata(channel):
    assert channel.name == "performance"
    assert channel.blocking_capable is True
    assert channel.timeout_ms == 10000


def test_performance_channel_should_run(channel):
    config = ControlPlaneConfig()
    event_no_root = SupervisionEvent(project_root="")
    event_with_root = SupervisionEvent(project_root="/tmp")

    assert channel.should_run(event_no_root, config) is False
    assert channel.should_run(event_with_root, config) is True


def test_performance_channel_execute_no_files(channel, tmp_path):
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path))

    result = channel.execute(event, config)
    assert result.status == "skip"
    assert result.metrics["reason"] == "no_python_files"


def test_performance_channel_execute_with_files(channel, tmp_path):
    # Create a dummy python file
    py_file = tmp_path / "logic.py"
    py_file.write_text("def add(a, b): return a + b\n")

    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path))

    result = channel.execute(event, config)
    # Status is 'fail' because findings are present (optimizations)
    assert result.status == "fail"
    assert result.channel == "performance"
    assert "pure_functions" in result.metrics
    assert result.metrics["pure_functions"] >= 1
    assert result.metrics["properties_detected"]["commutative"] >= 1
    assert result.metrics["properties_detected"]["associative"] >= 1


def test_performance_channel_emits_perfch005(channel, tmp_path):
    # Function that is pure but not idempotent/associative, just cacheable
    py_file = tmp_path / "caching.py"
    py_file.write_text("def get_expensive_val(x): return x * 42\n")

    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path))

    result = channel.execute(event, config)
    perfch005 = [f for f in result.findings if f.kind == "PERFCH005"]
    assert len(perfch005) >= 1
