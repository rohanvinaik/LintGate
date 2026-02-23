import pytest

from lintgate.channels.performance_channel import PerformanceChannel
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent


@pytest.fixture
def channel():
    return PerformanceChannel()


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
