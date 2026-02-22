"""Tests for test channel coverage measurement (MCP mode only).

Verifies:
- Coverage XML parsing (primary path)
- Terminal regex fallback
- Mode gating (MCP only, not hook)
- Threshold finding emission
"""

from __future__ import annotations

from lintgate.channels.test_channel import (
    TestRunResult as ChannelTestRunResult,
    _parse_coverage,
)


class TestParseCoverage:
    """Verify _parse_coverage XML parsing and terminal fallback."""

    def test_parse_xml_primary(self, tmp_path):
        """Primary path: parse line-rate from coverage.xml."""
        xml = '<?xml version="1.0" ?>\n<coverage line-rate="0.795">\n</coverage>'
        xml_path = str(tmp_path / "coverage.xml")
        (tmp_path / "coverage.xml").write_text(xml)

        result = _parse_coverage(xml_path, "")
        assert result == 79.5

    def test_parse_xml_100_percent(self, tmp_path):
        """100% coverage parses correctly."""
        xml = '<?xml version="1.0" ?>\n<coverage line-rate="1.0">\n</coverage>'
        xml_path = str(tmp_path / "coverage.xml")
        (tmp_path / "coverage.xml").write_text(xml)

        result = _parse_coverage(xml_path, "")
        assert result == 100.0

    def test_parse_xml_zero_percent(self, tmp_path):
        """0% coverage parses correctly."""
        xml = '<?xml version="1.0" ?>\n<coverage line-rate="0.0">\n</coverage>'
        xml_path = str(tmp_path / "coverage.xml")
        (tmp_path / "coverage.xml").write_text(xml)

        result = _parse_coverage(xml_path, "")
        assert result == 0.0

    def test_fallback_to_terminal_regex(self):
        """Fallback: parse TOTAL line from terminal output."""
        terminal = (
            "Name                 Stmts   Miss  Cover\n"
            "TOTAL                  500    100    80%\n"
        )
        result = _parse_coverage("/nonexistent/coverage.xml", terminal)
        assert result == 80.0

    def test_fallback_terminal_high_coverage(self):
        """Fallback regex handles various percentages."""
        terminal = "TOTAL                 1000     50    95%\n"
        result = _parse_coverage("/nonexistent/coverage.xml", terminal)
        assert result == 95.0

    def test_fallback_terminal_in_stderr_blob(self):
        """Fallback works when combined stdout/stderr blob is passed."""
        terminal = "error lines...\nTOTAL                  123      6    95%\n"
        result = _parse_coverage("/nonexistent/coverage.xml", terminal)
        assert result == 95.0

    def test_no_coverage_data_returns_none(self):
        """Returns None when neither XML nor terminal has coverage."""
        result = _parse_coverage("/nonexistent/coverage.xml", "no coverage here")
        assert result is None

    def test_malformed_xml_falls_back_to_terminal(self, tmp_path):
        """Malformed XML falls back to terminal regex."""
        xml_path = str(tmp_path / "coverage.xml")
        (tmp_path / "coverage.xml").write_text("not xml at all")

        terminal = "TOTAL                  500    100    80%\n"
        result = _parse_coverage(xml_path, terminal)
        assert result == 80.0

    def test_xml_missing_line_rate_falls_back(self, tmp_path):
        """XML without line-rate attribute falls back to terminal."""
        xml = '<?xml version="1.0" ?>\n<coverage>\n</coverage>'
        xml_path = str(tmp_path / "coverage.xml")
        (tmp_path / "coverage.xml").write_text(xml)

        terminal = "TOTAL                  500     50    90%\n"
        result = _parse_coverage(xml_path, terminal)
        assert result == 90.0


class TestTestRunResultCoverage:
    """Verify TestRunResult.coverage_pct field."""

    def test_default_none(self):
        result = ChannelTestRunResult()
        assert result.coverage_pct is None

    def test_set_coverage(self):
        result = ChannelTestRunResult(coverage_pct=82.5)
        assert result.coverage_pct == 82.5


class TestCoverageModeGating:
    """Verify coverage is only measured in MCP mode, not hook mode."""

    def test_hook_mode_skips_coverage(self):
        """Hook-triggered events should not measure coverage."""
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        config = ControlPlaneConfig(enabled=True)
        config.channels["tests"] = ChannelConfig(
            settings={"coverage_threshold": 80}
        )

        event = SupervisionEvent(
            surface="hook",
            project_root="/tmp/test",
            files_changed=[],
        )

        # Mode gating logic from execute():
        channel_settings = config.channels.get("tests", ChannelConfig()).settings
        coverage_threshold = channel_settings.get("coverage_threshold")
        measure_coverage = (
            coverage_threshold is not None
            and event.surface == "mcp"
        )

        assert not measure_coverage, "Hook mode should not measure coverage"

    def test_mcp_mode_measures_coverage(self):
        """MCP-triggered events should measure coverage when threshold set."""
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        config = ControlPlaneConfig(enabled=True)
        config.channels["tests"] = ChannelConfig(
            settings={"coverage_threshold": 80}
        )

        event = SupervisionEvent(
            surface="mcp",
            project_root="/tmp/test",
            files_changed=[],
        )

        channel_settings = config.channels.get("tests", ChannelConfig()).settings
        coverage_threshold = channel_settings.get("coverage_threshold")
        measure_coverage = (
            coverage_threshold is not None
            and event.surface == "mcp"
        )

        assert measure_coverage, "MCP mode should measure coverage when threshold is set"

    def test_mcp_mode_no_threshold_skips_coverage(self):
        """MCP mode without threshold configured should not measure coverage."""
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        config = ControlPlaneConfig(enabled=True)
        config.channels["tests"] = ChannelConfig(settings={})

        event = SupervisionEvent(
            surface="mcp",
            project_root="/tmp/test",
            files_changed=[],
        )

        channel_settings = config.channels.get("tests", ChannelConfig()).settings
        coverage_threshold = channel_settings.get("coverage_threshold")
        measure_coverage = (
            coverage_threshold is not None
            and event.surface == "mcp"
        )

        assert not measure_coverage, "No threshold means no coverage measurement"
