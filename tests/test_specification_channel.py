"""Tests for the specification channel — protocol compliance, finding emission."""

from __future__ import annotations

from lintgate.channels.specification_channel import SpecificationChannel
from lintgate.controlplane.types import ChannelResult, ControlPlaneConfig, SupervisionEvent


class TestChannelProtocol:
    def test_name(self):
        ch = SpecificationChannel()
        assert ch.name == "specification"

    def test_not_blocking(self):
        ch = SpecificationChannel()
        assert ch.blocking_capable is False

    def test_should_run_with_project_root(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(project_root="/some/path")
        config = ControlPlaneConfig()
        assert ch.should_run(event, config) is True

    def test_should_not_run_without_project_root(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(project_root="")
        config = ControlPlaneConfig()
        assert ch.should_run(event, config) is False


class TestMissingManifests:
    def test_skip_when_no_manifests(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(
            project_root="/some/path",
            context={},
        )
        config = ControlPlaneConfig()
        result = ch.execute(event, config)
        assert result.status == "skip"
        assert result.channel == "specification"

    def test_skip_when_partial_manifests(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(
            project_root="/some/path",
            context={"property_manifest": object()},
        )
        config = ControlPlaneConfig()
        result = ch.execute(event, config)
        assert result.status == "skip"


class TestChannelResult:
    def test_result_has_correct_channel(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(
            project_root="/some/path",
            context={},
        )
        config = ControlPlaneConfig()
        result = ch.execute(event, config)
        assert isinstance(result, ChannelResult)
        assert result.channel == "specification"
        assert result.duration_ms >= 0
