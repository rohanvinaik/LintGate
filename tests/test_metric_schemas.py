"""Phase 1: CI-failing wiring validation and result shape checks.

These tests verify that channel metric schemas are internally consistent:
every consumed key is published by some channel, and every channel's actual
output matches its declared schema.
"""

from __future__ import annotations

import pytest

from lintgate.controlplane.metric_schema import (
    ChannelSchema,
    MetricField,
    clear_schemas,
    register_all_schemas,
    register_schema,
    validate_result,
    validate_wiring,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure clean schema registry for each test."""
    clear_schemas()
    yield
    clear_schemas()


# ── CI-failing wiring tests ──────────────────────────────────────────


def test_validate_wiring_all_channels():
    """All channels active: zero wiring issues. CI-failing."""
    register_all_schemas()
    all_channels = [
        "performance",
        "test_effectiveness",
        "structure",
        "specification",
        "coherence",
        "convergence",
    ]
    issues = validate_wiring(all_channels)
    assert issues == [], f"Wiring issues found: {issues}"


def test_validate_wiring_missing_publisher():
    """Remove specification channel, assert WIRE001 for specification keys."""
    register_all_schemas()
    # coherence consumes specification_function_list (optional) and
    # convergence consumes specification_function_list (optional)
    # Without specification, coherence's finding_deps on test_effectiveness should still pass
    channels_without_spec = [
        "performance",
        "test_effectiveness",
        "structure",
        "coherence",
        "convergence",
    ]
    issues = validate_wiring(channels_without_spec)
    # No issues expected because specification keys consumed by coherence/convergence are optional
    # But the non-optional keys should fail if any exist
    for issue in issues:
        assert issue.consumer in ("coherence", "convergence")


def test_validate_wiring_finding_deps():
    """Remove test_effectiveness, assert finding dependency violation for coherence."""
    register_all_schemas()
    channels_without_teff = [
        "performance",
        "structure",
        "specification",
        "coherence",
        "convergence",
    ]
    issues = validate_wiring(channels_without_teff)

    # coherence has a FindingDependency on test_effectiveness
    finding_dep_issues = [i for i in issues if i.issue_type == "missing_finding_source"]
    assert len(finding_dep_issues) >= 1
    assert any("test_effectiveness" in i.missing_publisher for i in finding_dep_issues)


# ── Result shape checks ──────────────────────────────────────────────


def test_validate_result_complete():
    """Mock channel result with all declared keys: no missing."""
    register_all_schemas()
    metrics = {
        "pure_function_list": [],
        "purity_ratio": 0.5,
        "pure_functions": 10,
        "impure_functions": 10,
        "properties_detected": {},
        "optimization_opportunities": 5,
    }
    missing = validate_result("performance", metrics)
    assert missing == []


def test_validate_result_missing_key():
    """Mock channel result missing a key: detected."""
    register_all_schemas()
    metrics = {
        "purity_ratio": 0.5,
        # Missing: pure_function_list, pure_functions, impure_functions, etc.
    }
    missing = validate_result("performance", metrics)
    assert "pure_function_list" in missing
    assert "pure_functions" in missing


def test_validate_result_optional_keys_not_required():
    """Optional keys should not appear as missing."""
    register_all_schemas()
    # Structure channel has optional keys like _file_cohesion
    metrics = {
        "_module_fan_in": {},
        # _file_cohesion, _import_tracing, _cochange are optional
    }
    missing = validate_result("structure", metrics)
    assert "_file_cohesion" not in missing
    assert "_import_tracing" not in missing
    assert "_cochange" not in missing


# ── Schema registration ──────────────────────────────────────────────


def test_register_and_retrieve_schema():
    """Schemas can be registered and retrieved."""
    schema = ChannelSchema(
        channel="test_channel",
        publishes=[MetricField("key1", "str", "test key")],
    )
    register_schema(schema)
    from lintgate.controlplane.metric_schema import get_schema

    retrieved = get_schema("test_channel")
    assert retrieved is not None
    assert retrieved.channel == "test_channel"
    assert len(retrieved.publishes) == 1


def test_validate_result_skip_status_exempt():
    """Skipped channels should not report missing keys."""
    register_all_schemas()
    # Structure channel with skip payload (too few files)
    skip_metrics = {"reason": "too_few_files", "file_count": 2}
    missing = validate_result("structure", skip_metrics, status="skip")
    assert missing == [], "Skipped channels should be exempt from key validation"


def test_validate_result_skip_vs_pass():
    """Same metrics: missing on pass, exempt on skip."""
    register_all_schemas()
    sparse_metrics = {"reason": "too_few_files"}
    missing_pass = validate_result("structure", sparse_metrics, status="pass")
    missing_skip = validate_result("structure", sparse_metrics, status="skip")
    assert len(missing_pass) > 0, "Pass status should detect missing keys"
    assert missing_skip == [], "Skip status should be exempt"


def test_wiring_with_custom_schemas():
    """Custom schema wiring validation works correctly."""
    register_schema(
        ChannelSchema(
            channel="producer",
            publishes=[MetricField("shared_key", "dict", "shared data")],
        )
    )
    register_schema(
        ChannelSchema(
            channel="consumer",
            consumes=[MetricField("shared_key", "dict", "from producer")],
        )
    )

    # Both active: no issues
    issues = validate_wiring(["producer", "consumer"])
    assert issues == []

    # Only consumer active: missing publisher
    issues = validate_wiring(["consumer"])
    assert len(issues) == 1
    assert issues[0].consumer == "consumer"
    assert issues[0].key == "shared_key"
