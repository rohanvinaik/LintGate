"""Tests for mcp_tools/controlplane_tools.py.

Detailed helper tests live in test_controlplane_tools_helpers*.py. This file
provides the standard test_<module>.py entry point for test channel discovery
and covers core utility functions.
"""

from __future__ import annotations

from mcp_tools.controlplane_tools import (
    _extract_findings,
    _filter_channels,
)

# ── _filter_channels ────────────────────────────────────────────────────


def test_filter_channels_no_filter_yields_all() -> None:
    channels = {"lint": {"status": "pass"}, "tests": {"status": "fail"}}
    result = list(_filter_channels(channels, None))
    assert len(result) == 2


def test_filter_channels_with_filter_yields_matching() -> None:
    channels = {"lint": {"status": "pass"}, "tests": {"status": "fail"}}
    result = list(_filter_channels(channels, "lint"))
    assert len(result) == 1
    assert result[0][0] == "lint"


def test_filter_channels_empty_dict() -> None:
    assert list(_filter_channels({}, None)) == []


# ── _extract_findings ───────────────────────────────────────────────────


def test_extract_findings_returns_all_matching() -> None:
    details = {
        "channels": {
            "lint": {
                "findings": [
                    {"severity": "warning", "message": "warn1"},
                    {"severity": "blocking", "message": "block1"},
                ]
            }
        }
    }
    result = _extract_findings(details, None, None, 10)
    assert result["total_matching"] == 2


def test_extract_findings_filters_by_severity() -> None:
    details = {
        "channels": {
            "lint": {
                "findings": [
                    {"severity": "warning", "message": "warn1"},
                    {"severity": "blocking", "message": "block1"},
                ]
            }
        }
    }
    result = _extract_findings(details, None, "blocking", 10)
    assert result["total_matching"] == 1
    assert result["findings"][0]["message"] == "block1"


def test_extract_findings_truncates_to_max() -> None:
    details = {
        "channels": {
            "lint": {"findings": [{"severity": "warning", "message": f"w{i}"} for i in range(5)]}
        }
    }
    result = _extract_findings(details, None, None, 2)
    assert len(result["findings"]) == 2
    assert result["truncated"] == 3


def test_extract_findings_filters_by_channel() -> None:
    details = {
        "channels": {
            "lint": {"findings": [{"severity": "warning", "message": "lint-warn"}]},
            "tests": {"findings": [{"severity": "warning", "message": "test-warn"}]},
        }
    }
    result = _extract_findings(details, "tests", None, 10)
    assert result["total_matching"] == 1
    assert result["findings"][0]["channel"] == "tests"
