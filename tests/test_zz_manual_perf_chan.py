from lintgate.channels.performance_channel import PerformanceChannel, _discover_python_files
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent


def test_performance_channel_discover():
    files = _discover_python_files(".")
    assert isinstance(files, list)


def test_performance_channel_execute():
    channel = PerformanceChannel()
    config = ControlPlaneConfig()

    # Empty project
    event = SupervisionEvent(project_root="")
    result = channel.execute(event, config)
    assert result.status == "skip"

    # Actual project (should find some files)
    event = SupervisionEvent(project_root=".")
    result = channel.execute(event, config)
    assert result.channel == "performance"
