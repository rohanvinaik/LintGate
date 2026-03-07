"""Phase 5b/5c: Sheaf condition and schema size tests.

Verifies check_sheaf_condition() traces multi-hop publish→consume chains
and check_schema_size() flags channels with too many published keys.
"""

from __future__ import annotations

import pytest

from lintgate.controlplane.metric_schema import (
    ChannelSchema,
    MetricField,
    check_schema_size,
    check_sheaf_condition,
    clear_schemas,
    register_all_schemas,
    register_schema,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure clean schema registry for each test."""
    clear_schemas()
    yield
    clear_schemas()


# ── Sheaf condition tests (5b) ───────────────────────────────────────


def test_sheaf_no_breaks_all_channels():
    """All channels active: no sheaf breaks."""
    register_all_schemas()
    all_channels = [
        "performance",
        "test_effectiveness",
        "structure",
        "specification",
        "coherence",
        "convergence",
    ]
    issues = check_sheaf_condition(all_channels)
    assert issues == [], f"Unexpected sheaf breaks: {issues}"


def test_sheaf_break_on_missing_upstream():
    """Sheaf break when a publisher's upstream dependency is missing."""
    # A → B → C chain where B needs A's data
    register_schema(
        ChannelSchema(
            channel="source",
            publishes=[MetricField("raw_data", "dict", "raw data")],
        )
    )
    register_schema(
        ChannelSchema(
            channel="transformer",
            consumes=[MetricField("raw_data", "dict", "from source")],
            publishes=[MetricField("processed_data", "dict", "processed")],
        )
    )
    register_schema(
        ChannelSchema(
            channel="consumer",
            consumes=[MetricField("processed_data", "dict", "from transformer")],
        )
    )

    # All active: no breaks
    issues = check_sheaf_condition(["source", "transformer", "consumer"])
    assert issues == []

    # Remove source: transformer can't get raw_data, so consumer's chain breaks
    issues = check_sheaf_condition(["transformer", "consumer"])
    sheaf_breaks = [i for i in issues if i.issue_type == "sheaf_break"]
    assert len(sheaf_breaks) >= 1
    assert any("raw_data" in i.missing_publisher for i in sheaf_breaks)


def test_sheaf_optional_upstream_no_break():
    """Optional upstream dependencies don't cause sheaf breaks."""
    register_schema(
        ChannelSchema(
            channel="transformer",
            consumes=[MetricField("optional_data", "dict", "optional", optional=True)],
            publishes=[MetricField("output", "dict", "output")],
        )
    )
    register_schema(
        ChannelSchema(
            channel="consumer",
            consumes=[MetricField("output", "dict", "from transformer")],
        )
    )

    # optional_data has no publisher, but it's optional — no sheaf break
    issues = check_sheaf_condition(["transformer", "consumer"])
    assert issues == []


def test_sheaf_issue_type():
    """Sheaf issues have issue_type='sheaf_break'."""
    register_schema(
        ChannelSchema(
            channel="mid",
            consumes=[MetricField("needed", "dict", "required upstream")],
            publishes=[MetricField("produced", "dict", "output")],
        )
    )
    register_schema(
        ChannelSchema(
            channel="end",
            consumes=[MetricField("produced", "dict", "from mid")],
        )
    )

    issues = check_sheaf_condition(["mid", "end"])
    assert all(i.issue_type == "sheaf_break" for i in issues)


# ── Schema size tests (5c) ──────────────────────────────────────────


def test_schema_size_under_limit():
    """Channels with <= 10 published keys pass."""
    register_all_schemas()
    issues = check_schema_size(max_keys=10)
    assert issues == [], f"Unexpected size issues: {issues}"


def test_schema_size_over_limit():
    """Channel with > max_keys published keys triggers WIRE004."""
    fields = [MetricField(f"key_{i}", "str", f"key {i}") for i in range(12)]
    register_schema(ChannelSchema(channel="bloated", publishes=fields))

    issues = check_schema_size(max_keys=10)
    assert len(issues) == 1
    assert issues[0].consumer == "bloated"
    assert issues[0].issue_type == "schema_growth"
    assert "12" in issues[0].missing_publisher


def test_schema_size_exact_limit():
    """Channel with exactly max_keys published keys passes."""
    fields = [MetricField(f"key_{i}", "str", f"key {i}") for i in range(10)]
    register_schema(ChannelSchema(channel="exact", publishes=fields))

    issues = check_schema_size(max_keys=10)
    assert issues == []


def test_schema_size_custom_threshold():
    """Custom threshold works."""
    fields = [MetricField(f"key_{i}", "str", f"key {i}") for i in range(4)]
    register_schema(ChannelSchema(channel="small", publishes=fields))

    issues = check_schema_size(max_keys=3)
    assert len(issues) == 1
    assert issues[0].consumer == "small"
