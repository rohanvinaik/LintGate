"""Mutation gap tests for mcp_tools/controlplane_tools.py.

Targets:
- _filter_channels — VALUE survivors (exact return value assertions)
"""

from __future__ import annotations

from mcp_tools.controlplane_tools import _filter_channels

# ── _filter_channels — exact VALUE assertions ────────────────────────────


def test_filter_channels_no_filter_returns_all_pairs() -> None:
    channels = {"lint": {"status": "pass"}, "tests": {"status": "fail"}}
    result = list(_filter_channels(channels, None))
    assert result == [("lint", {"status": "pass"}), ("tests", {"status": "fail"})]


def test_filter_channels_with_filter_returns_exact_match() -> None:
    channels = {"lint": {"status": "pass"}, "tests": {"status": "fail"}}
    result = list(_filter_channels(channels, "lint"))
    assert result == [("lint", {"status": "pass"})]


def test_filter_channels_with_filter_no_match_returns_empty() -> None:
    channels = {"lint": {"status": "pass"}, "tests": {"status": "fail"}}
    result = list(_filter_channels(channels, "deps"))
    assert result == []


def test_filter_channels_empty_dict_returns_empty() -> None:
    result = list(_filter_channels({}, None))
    assert result == []


def test_filter_channels_empty_dict_with_filter_returns_empty() -> None:
    result = list(_filter_channels({}, "lint"))
    assert result == []


def test_filter_channels_single_channel_no_filter() -> None:
    channels = {"behavior": {"findings": []}}
    result = list(_filter_channels(channels, None))
    assert result == [("behavior", {"findings": []})]


def test_filter_channels_single_channel_matching_filter() -> None:
    channels = {"behavior": {"findings": []}}
    result = list(_filter_channels(channels, "behavior"))
    assert result == [("behavior", {"findings": []})]


def test_filter_channels_single_channel_non_matching_filter() -> None:
    channels = {"behavior": {"findings": []}}
    result = list(_filter_channels(channels, "lint"))
    assert result == []


def test_filter_channels_preserves_complex_data() -> None:
    data = {
        "lint": {
            "findings": [{"severity": "warning", "message": "unused import"}],
            "status": "fail",
            "metrics": {"issue_count": 1},
        }
    }
    result = list(_filter_channels(data, "lint"))
    assert len(result) == 1
    name, ch_data = result[0]
    assert name == "lint"
    assert ch_data["findings"][0]["message"] == "unused import"
    assert ch_data["metrics"]["issue_count"] == 1


def test_filter_channels_empty_string_filter_returns_empty() -> None:
    """Empty string is truthy, so it acts as a filter that matches nothing."""
    channels = {"lint": {"status": "pass"}}
    result = list(_filter_channels(channels, ""))
    # "" is falsy in Python, so it behaves like None (no filter)
    assert result == [("lint", {"status": "pass"})]


def test_filter_channels_multiple_channels_filter_second() -> None:
    channels = {
        "lint": {"status": "pass"},
        "tests": {"status": "fail"},
        "deps": {"status": "pass"},
    }
    result = list(_filter_channels(channels, "tests"))
    assert result == [("tests", {"status": "fail"})]


def test_filter_channels_multiple_channels_filter_last() -> None:
    channels = {
        "lint": {"status": "pass"},
        "tests": {"status": "fail"},
        "deps": {"status": "pass"},
    }
    result = list(_filter_channels(channels, "deps"))
    assert result == [("deps", {"status": "pass"})]
