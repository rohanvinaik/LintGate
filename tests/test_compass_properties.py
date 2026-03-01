from hypothesis import given
from hypothesis import strategies as st

from lintgate.compass import (
    CompassAxis,
    CompassClaim,
    CompassDirective,
    CompassState,
    GapReport,
)


# Strategies for Compass types
def compass_claims():
    return st.builds(
        CompassClaim,
        text=st.text(min_size=1),
        source=st.text(),
        heading=st.text(),
        confidence=st.floats(min_value=0.0, max_value=1.0),
        provenance=st.sampled_from(["parsed", "inferred", "interviewed"]),
        origin_facet=st.text(),
    )


def compass_axes():
    return st.builds(
        CompassAxis,
        name=st.sampled_from(["problem", "solution", "implementation", "world"]),
        claims=st.lists(compass_claims()),
        summary=st.text(),
        depth=st.integers(min_value=0, max_value=3),
    )


def compass_directives():
    return st.builds(
        CompassDirective,
        kind=st.sampled_from(["toward", "away", "forbidden"]),
        text=st.text(min_size=1),
        source=st.text(),
    )


def gap_reports():
    return st.builds(
        GapReport,
        axis_depths=st.dictionaries(st.text(), st.integers(min_value=0, max_value=3)),
        # Round input spikiness to avoid precision errors, as to_dict rounds to 4 decimals
        spikiness=st.floats(min_value=0.0, max_value=1.0).map(lambda x: round(x, 4)),
        sparse_axes=st.lists(st.text()),
        interview_recommended=st.booleans(),
    )


def compass_states():
    return st.builds(
        CompassState,
        version=st.integers(min_value=1),
        axes=st.dictionaries(st.text(), compass_axes()),
        directives=st.lists(compass_directives()),
        gap_report=gap_reports(),
        forged_at=st.floats(min_value=0.0),
        frozen=st.booleans(),
        frozen_hash=st.text(),
    )


# Property Tests


@given(claim=compass_claims())
def test_compass_claim_roundtrip(claim):
    """Verify CompassClaim can be serialized and deserialized."""
    data = claim.to_dict()
    restored = CompassClaim.from_dict(data)
    assert restored == claim


@given(axis=compass_axes())
def test_compass_axis_roundtrip(axis):
    """Verify CompassAxis can be serialized and deserialized."""
    data = axis.to_dict()
    restored = CompassAxis.from_dict(data)
    assert restored == axis


@given(directive=compass_directives())
def test_compass_directive_roundtrip(directive):
    """Verify CompassDirective can be serialized and deserialized."""
    data = directive.to_dict()
    restored = CompassDirective.from_dict(data)
    assert restored == directive


@given(report=gap_reports())
def test_gap_report_roundtrip(report):
    """Verify GapReport can be serialized and deserialized."""
    data = report.to_dict()
    restored = GapReport.from_dict(data)
    # Compare dictionary forms to handle float rounding effectively
    assert restored.to_dict() == data
    # Also verify object equality since we clamped input precision
    assert restored == report


@given(state=compass_states())
def test_compass_state_roundtrip(state):
    """Verify CompassState can be serialized and deserialized."""
    data = state.to_dict()
    restored = CompassState.from_dict(data)
    assert restored.to_dict() == data
    assert restored == state
