"""Channel metric schema declarations and wiring validation.

Each channel declares what metric keys it publishes and consumes.
validate_wiring() checks that every consumed key is published by some
active channel. CI-failing in tests (hard assertion), runtime advisory
in controlplane_run (WIRE001 finding).

Finding codes:
- WIRE001: Consumed metric key has no publisher among active channels
- WIRE002: Channel published fewer keys than declared schema
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricField:
    """A single metric key published or consumed by a channel."""

    key: str
    type_hint: str
    description: str
    optional: bool = False


@dataclass
class FindingDependency:
    """Declares that a consumer reads findings from another channel."""

    source_channel: str
    finding_kinds: list[str] = field(default_factory=list)
    fields_read: list[str] = field(default_factory=list)


@dataclass
class ChannelSchema:
    """Schema declaration for a single channel."""

    channel: str
    publishes: list[MetricField] = field(default_factory=list)
    consumes: list[MetricField] = field(default_factory=list)
    finding_deps: list[FindingDependency] = field(default_factory=list)


@dataclass
class WiringIssue:
    """A wiring violation: consumed key has no publisher."""

    consumer: str
    key: str
    missing_publisher: str
    issue_type: str = "missing_publisher"  # missing_publisher | missing_finding_source


# ── Registry ──────────────────────────────────────────────────────────

_CHANNEL_SCHEMAS: dict[str, ChannelSchema] = {}


def register_schema(schema: ChannelSchema) -> None:
    """Register a channel's schema declaration."""
    _CHANNEL_SCHEMAS[schema.channel] = schema


def get_schema(channel: str) -> ChannelSchema | None:
    """Get registered schema for a channel."""
    return _CHANNEL_SCHEMAS.get(channel)


def get_all_schemas() -> dict[str, ChannelSchema]:
    """Get all registered schemas."""
    return dict(_CHANNEL_SCHEMAS)


def clear_schemas() -> None:
    """Clear all registered schemas (for testing)."""
    _CHANNEL_SCHEMAS.clear()


# ── Validation ────────────────────────────────────────────────────────


def validate_wiring(active_channels: list[str]) -> list[WiringIssue]:
    """Check that every consumed key is published by some active channel.

    Also checks finding dependencies: source_channel must be in active set.
    Returns list of WiringIssue for each violation.
    """
    issues: list[WiringIssue] = []

    # Build set of all published keys across active channels
    published_keys: dict[str, str] = {}  # key -> publishing channel
    active_set = set(active_channels)

    for channel_name in active_channels:
        schema = _CHANNEL_SCHEMAS.get(channel_name)
        if schema is None:
            continue
        for mf in schema.publishes:
            published_keys[mf.key] = channel_name

    # Check consumed keys
    for channel_name in active_channels:
        schema = _CHANNEL_SCHEMAS.get(channel_name)
        if schema is None:
            continue

        for mf in schema.consumes:
            if mf.key not in published_keys and not mf.optional:
                issues.append(
                    WiringIssue(
                        consumer=channel_name,
                        key=mf.key,
                        missing_publisher=f"no active channel publishes '{mf.key}'",
                    )
                )

        # Check finding dependencies
        for dep in schema.finding_deps:
            if dep.source_channel not in active_set:
                issues.append(
                    WiringIssue(
                        consumer=channel_name,
                        key=f"findings from '{dep.source_channel}'",
                        missing_publisher=f"channel '{dep.source_channel}' not active",
                        issue_type="missing_finding_source",
                    )
                )

    return issues


def validate_result(
    channel: str, metrics: dict[str, Any], *, status: str = "pass"
) -> list[str]:
    """Check that a channel's actual metrics contain all declared published keys.

    Skipped channels (status="skip") are exempt — they legitimately produce
    reduced metric sets (e.g., structure channel with too few files).

    Returns list of missing key names (non-optional only).
    """
    if status == "skip":
        return []

    schema = _CHANNEL_SCHEMAS.get(channel)
    if schema is None:
        return []

    missing: list[str] = []
    for mf in schema.publishes:
        if not mf.optional and mf.key not in metrics:
            missing.append(mf.key)

    return missing


# ── Sheaf Condition (Phase 5b) ───────────────────────────────────────


def check_sheaf_condition(active_channels: list[str]) -> list[WiringIssue]:
    """Trace multi-hop publish→consume chains and verify format compatibility.

    The sheaf condition requires that when channel A publishes key K,
    and channel B consumes key K and publishes key K', and channel C
    consumes key K', the entire chain A→B→C is wired correctly.
    A break at any hop means downstream consumers receive stale/missing data.

    Finding code: WIRE003.
    """
    issues: list[WiringIssue] = []
    active_set = set(active_channels)

    # Build publish map: key → publishing channel
    publish_map: dict[str, str] = {}
    for channel_name in active_channels:
        schema = _CHANNEL_SCHEMAS.get(channel_name)
        if schema is None:
            continue
        for mf in schema.publishes:
            publish_map[mf.key] = channel_name

    # Build consume map: channel → list of consumed keys
    consume_map: dict[str, list[str]] = {}
    for channel_name in active_channels:
        schema = _CHANNEL_SCHEMAS.get(channel_name)
        if schema is None:
            continue
        consume_map[channel_name] = [mf.key for mf in schema.consumes]

    # Trace chains: for each consuming channel, check if its data sources
    # are themselves fed by active publishers
    for consumer, consumed_keys in consume_map.items():
        for key in consumed_keys:
            publisher = publish_map.get(key)
            if publisher is None:
                continue  # Already caught by validate_wiring

            # Check if the publisher itself consumes keys that are missing
            publisher_schema = _CHANNEL_SCHEMAS.get(publisher)
            if publisher_schema is None:
                continue

            for upstream_dep in publisher_schema.consumes:
                if upstream_dep.optional:
                    continue
                upstream_publisher = publish_map.get(upstream_dep.key)
                if upstream_publisher is None or upstream_publisher not in active_set:
                    issues.append(
                        WiringIssue(
                            consumer=consumer,
                            key=key,
                            missing_publisher=(
                                f"chain break: '{consumer}' consumes '{key}' from "
                                f"'{publisher}', which needs '{upstream_dep.key}' "
                                f"but no active channel publishes it"
                            ),
                            issue_type="sheaf_break",
                        )
                    )

    return issues


def check_schema_size(max_keys: int = 10) -> list[WiringIssue]:
    """Emit WIRE004 when a single channel publishes > max_keys metric keys.

    A channel with too many published keys is a signal to consider splitting
    the channel into more focused units.
    """
    issues: list[WiringIssue] = []
    for channel_name, schema in _CHANNEL_SCHEMAS.items():
        count = len(schema.publishes)
        if count > max_keys:
            issues.append(
                WiringIssue(
                    consumer=channel_name,
                    key=f"{count} published keys",
                    missing_publisher=(
                        f"channel publishes {count} keys (>{max_keys}), "
                        f"consider splitting"
                    ),
                    issue_type="schema_growth",
                )
            )
    return issues


# ── Schema Registration ──────────────────────────────────────────────
# Each channel's schema is registered at import time.


def register_all_schemas() -> None:
    """Register schemas for all known channels.

    Called once during controlplane startup. Schemas are also importable
    from each channel module as SCHEMA constants.
    """
    register_schema(PERFORMANCE_SCHEMA)
    register_schema(TEST_EFFECTIVENESS_SCHEMA)
    register_schema(STRUCTURE_SCHEMA)
    register_schema(SPECIFICATION_SCHEMA)
    register_schema(CROSS_CHANNEL_SCHEMA)
    register_schema(CONVERGENCE_SCHEMA)


# ── Schema Declarations ──────────────────────────────────────────────

PERFORMANCE_SCHEMA = ChannelSchema(
    channel="performance",
    publishes=[
        MetricField("pure_function_list", "list[dict]", "List of pure function info dicts"),
        MetricField("purity_ratio", "float", "Ratio of pure to total functions"),
        MetricField("pure_functions", "int", "Count of pure functions"),
        MetricField("impure_functions", "int", "Count of impure functions"),
        MetricField("properties_detected", "dict[str, int]", "Property kind distribution"),
        MetricField("optimization_opportunities", "int", "Count of optimization opportunities"),
    ],
)

TEST_EFFECTIVENESS_SCHEMA = ChannelSchema(
    channel="test_effectiveness",
    publishes=[
        MetricField(
            "project_effectiveness_score", "float", "Overall project effectiveness score"
        ),
        MetricField("semantic_ratio", "float", "Ratio of semantic assertions"),
        MetricField("functions_analyzed", "int", "Count of functions analyzed"),
        MetricField(
            "mutation_vulnerable_count", "int", "Count of mutation-vulnerable functions"
        ),
    ],
)

STRUCTURE_SCHEMA = ChannelSchema(
    channel="structure",
    publishes=[
        MetricField("_module_fan_in", "dict[str, int]", "Per-module fan-in counts"),
        MetricField(
            "_file_cohesion",
            "dict[str, dict]",
            "Per-file cohesion analysis",
            optional=True,
        ),
        MetricField(
            "_import_tracing",
            "dict",
            "Import tracing data",
            optional=True,
        ),
        MetricField(
            "_cochange",
            "dict",
            "Co-change frequency data",
            optional=True,
        ),
    ],
)

SPECIFICATION_SCHEMA = ChannelSchema(
    channel="specification",
    publishes=[
        MetricField(
            "specification_function_list",
            "dict[str, dict]",
            "Per-function specification data",
        ),
        MetricField(
            "composition_gaps",
            "dict[str, dict] | None",
            "Composition gap analysis results",
            optional=True,
        ),
        MetricField(
            "specification_coverage",
            "float",
            "Overall specification coverage ratio",
        ),
        MetricField(
            "sheaf_obstruction",
            "float | None",
            "Total gamma from composition analysis",
            optional=True,
        ),
    ],
)

CROSS_CHANNEL_SCHEMA = ChannelSchema(
    channel="coherence",
    consumes=[
        MetricField("pure_function_list", "list[dict]", "From performance channel"),
        MetricField(
            "specification_function_list",
            "dict[str, dict]",
            "From specification channel",
            optional=True,
        ),
        MetricField(
            "composition_gaps",
            "dict[str, dict] | None",
            "From specification channel",
            optional=True,
        ),
    ],
    finding_deps=[
        FindingDependency(
            source_channel="test_effectiveness",
            finding_kinds=["*"],
            fields_read=[
                "evidence.value_ratio",
                "evidence.branch_ratio",
                "evidence.semantic_ratio",
            ],
        ),
    ],
)

CONVERGENCE_SCHEMA = ChannelSchema(
    channel="convergence",
    consumes=[
        MetricField("_module_fan_in", "dict[str, int]", "From structure channel"),
        MetricField(
            "purity_profile",
            "dict",
            "From performance channel (via purity adapter)",
            optional=True,
        ),
        MetricField(
            "specification_function_list",
            "dict[str, dict]",
            "From specification channel",
            optional=True,
        ),
        MetricField(
            "composition_gaps",
            "dict[str, dict] | None",
            "From specification channel",
            optional=True,
        ),
        MetricField(
            "cohesion",
            "dict",
            "From lint/structure channel",
            optional=True,
        ),
    ],
)
