"""Mutation-killing tests for lintgate/controlplane/reporter/hook.py.

Targets 4 functions with VALUE, SWAP, and BOUNDARY mutant coverage:
- _build_posttooluse_context
- _append_delta_pairs
- _append_status_pairs
- _serialize_pairs
"""

from __future__ import annotations

from typing import Any

from lintgate.controlplane.reporter.hook import (
    PostToolUseInputs,
    _append_delta_pairs,
    _append_status_pairs,
    _build_posttooluse_context,
    _serialize_pairs,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_mesh(
    coherence_state: str = "stable",
    channel_results: list[ChannelResult] | None = None,
    edit_scoped: bool = False,
    edit_related_channels: list[str] | None = None,
    ambient_channels: list[str] | None = None,
) -> MeshResult:
    """Build a MeshResult with minimal boilerplate."""
    coh = CoherenceResult(
        state=coherence_state,
        edit_scoped=edit_scoped,
        edit_related_channels=edit_related_channels or [],
        ambient_channels=ambient_channels or [],
    )
    return MeshResult(
        coherence=coh,
        channel_results=channel_results or [],
    )


def _make_inputs(
    coherence_state: str = "stable",
    blocking_count: int = 0,
    warning_count: int = 0,
    informational_count: int = 0,
    hidden_findings: int = 0,
    channels_run: int = 3,
    delta: dict[str, Any] | None = None,
    baseline_delta: dict[str, Any] | None = None,
    resurfaced_count: int = 0,
    cycle_alerts: list[str] | None = None,
    channel_results: list[ChannelResult] | None = None,
    edit_scoped: bool = False,
    edit_related_channels: list[str] | None = None,
    ambient_channels: list[str] | None = None,
) -> PostToolUseInputs:
    """Build PostToolUseInputs with sensible defaults."""
    mesh = _make_mesh(
        coherence_state=coherence_state,
        channel_results=channel_results or [],
        edit_scoped=edit_scoped,
        edit_related_channels=edit_related_channels,
        ambient_channels=ambient_channels,
    )
    return PostToolUseInputs(
        mesh_result=mesh,
        blocking_count=blocking_count,
        warning_count=warning_count,
        informational_count=informational_count,
        hidden_findings=hidden_findings,
        channels_run=channels_run,
        delta=delta,
        baseline_delta=baseline_delta,
        resurfaced_count=resurfaced_count,
        cycle_alerts=cycle_alerts or [],
    )


# ═══════════════════════════════════════════════════════════════════════
# _serialize_pairs
# ═══════════════════════════════════════════════════════════════════════


class TestSerializePairs:
    """Tests for _serialize_pairs — exact output format and truncation."""

    def test_basic_serialization_exact_format(self) -> None:
        """VALUE: exact '; ' separator and '=' assignment."""
        pairs = [("coherence", "stable"), ("channels_run", "3")]
        result = _serialize_pairs(pairs, max_len=300)
        assert result == "coherence=stable; channels_run=3"

    def test_single_pair(self) -> None:
        """VALUE: single pair produces no separator."""
        pairs = [("coherence", "stable")]
        result = _serialize_pairs(pairs, max_len=300)
        assert result == "coherence=stable"

    def test_empty_pairs(self) -> None:
        """BOUNDARY: empty list produces empty string."""
        result = _serialize_pairs([], max_len=300)
        assert result == ""

    def test_truncation_drops_from_bottom(self) -> None:
        """BOUNDARY: pairs beyond max_len are dropped from the end, preserving first 2."""
        pairs = [
            ("coherence", "stable"),
            ("channels_run", "5"),
            ("blocking", "99"),
            ("extra_long_key", "x" * 300),
        ]
        result = _serialize_pairs(pairs, max_len=60)
        # Must preserve first 2 pairs (coherence + channels_run)
        assert "coherence=stable" in result
        assert "channels_run=5" in result
        assert len(result) <= 60

    def test_truncation_preserves_minimum_two_pairs(self) -> None:
        """BOUNDARY: even if first 2 pairs exceed max_len, they are kept."""
        pairs = [
            ("coherence", "systemic"),
            ("channels_run", "10"),
            ("blocking", "5"),
        ]
        result = _serialize_pairs(pairs, max_len=5)
        # Must keep at least 2 pairs even though they exceed max_len=5
        assert "coherence=systemic" in result
        assert "channels_run=10" in result

    def test_parameter_order_key_before_value(self) -> None:
        """SWAP: key appears before '=' and value appears after."""
        pairs = [("alpha", "beta")]
        result = _serialize_pairs(pairs, max_len=300)
        assert result == "alpha=beta"
        # Verify order: key is left of '=', value is right
        k, v = result.split("=")
        assert k == "alpha"
        assert v == "beta"

    def test_max_len_boundary_exact(self) -> None:
        """BOUNDARY: result at exactly max_len is not truncated."""
        pairs = [("a", "1"), ("b", "2"), ("c", "3")]
        full = "a=1; b=2; c=3"  # len = 14
        result = _serialize_pairs(pairs, max_len=len(full))
        assert result == full

    def test_max_len_one_below_triggers_drop(self) -> None:
        """BOUNDARY: result one char over max_len triggers truncation."""
        pairs = [("a", "1"), ("b", "2"), ("c", "3")]
        full = "a=1; b=2; c=3"  # len = 14
        result = _serialize_pairs(pairs, max_len=len(full) - 1)
        assert "c=3" not in result
        assert result == "a=1; b=2"


# ═══════════════════════════════════════════════════════════════════════
# _append_delta_pairs
# ═══════════════════════════════════════════════════════════════════════


class TestAppendDeltaPairs:
    """Tests for _append_delta_pairs — delta/baseline extraction into pairs."""

    def test_no_delta_no_pairs_added(self) -> None:
        """VALUE: when delta and baseline_delta are None, no pairs appended."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        _append_delta_pairs(pairs, coherence, None, None)
        assert pairs == []

    def test_new_findings_from_delta(self) -> None:
        """VALUE: new_findings counts items in delta['new'], summing 'count' fields."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        delta = {"new": [{"count": 2}, {"count": 3}]}
        _append_delta_pairs(pairs, coherence, delta, None)
        assert ("new_findings", "5") in pairs

    def test_new_findings_default_count_one(self) -> None:
        """VALUE: items without 'count' key default to 1."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        delta = {"new": [{}, {}]}
        _append_delta_pairs(pairs, coherence, delta, None)
        assert ("new_findings", "2") in pairs

    def test_resolved_from_delta(self) -> None:
        """VALUE: resolved_count > 0 produces resolved pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        delta = {"new": [], "resolved_count": 7}
        _append_delta_pairs(pairs, coherence, delta, None)
        assert ("resolved", "7") in pairs

    def test_resolved_zero_omitted(self) -> None:
        """BOUNDARY: resolved_count = 0 produces no pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        delta = {"new": [], "resolved_count": 0}
        _append_delta_pairs(pairs, coherence, delta, None)
        assert all(k != "resolved" for k, _ in pairs)

    def test_known_debt_from_delta(self) -> None:
        """VALUE: still_active_count > 0 produces known_debt pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        delta = {"new": [], "still_active_count": 4}
        _append_delta_pairs(pairs, coherence, delta, None)
        assert ("known_debt", "4") in pairs

    def test_known_debt_zero_omitted(self) -> None:
        """BOUNDARY: still_active_count = 0 produces no pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        delta = {"new": [], "still_active_count": 0}
        _append_delta_pairs(pairs, coherence, delta, None)
        assert all(k != "known_debt" for k, _ in pairs)

    def test_session_regressions_from_baseline_delta(self) -> None:
        """VALUE: baseline_delta new items produce session_regressions."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        baseline = {"new": [{"count": 1}, {"count": 2}]}
        _append_delta_pairs(pairs, coherence, None, baseline)
        assert ("session_regressions", "3") in pairs

    def test_session_regressions_zero_omitted(self) -> None:
        """BOUNDARY: baseline_delta with empty new list produces no pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(state="stable")
        baseline = {"new": []}
        _append_delta_pairs(pairs, coherence, None, baseline)
        assert all(k != "session_regressions" for k, _ in pairs)

    def test_edit_related_channels_appended(self) -> None:
        """VALUE: edit_scoped + edit_related_channels produces edit_related pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(
            state="isolated",
            edit_scoped=True,
            edit_related_channels=["lint", "test"],
        )
        _append_delta_pairs(pairs, coherence, None, None)
        assert ("edit_related", "lint,test") in pairs

    def test_edit_related_not_appended_when_not_scoped(self) -> None:
        """SWAP: edit_scoped=False suppresses edit_related even with channels."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(
            state="isolated",
            edit_scoped=False,
            edit_related_channels=["lint"],
        )
        _append_delta_pairs(pairs, coherence, None, None)
        assert all(k != "edit_related" for k, _ in pairs)

    def test_ambient_debt_appended(self) -> None:
        """VALUE: edit_scoped + ambient_channels produces ambient_debt pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(
            state="isolated",
            edit_scoped=True,
            ambient_channels=["structure"],
        )
        _append_delta_pairs(pairs, coherence, None, None)
        assert ("ambient_debt", "structure") in pairs

    def test_ambient_debt_not_appended_when_not_scoped(self) -> None:
        """SWAP: edit_scoped=False suppresses ambient_debt even with channels."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(
            state="isolated",
            edit_scoped=False,
            ambient_channels=["structure"],
        )
        _append_delta_pairs(pairs, coherence, None, None)
        assert all(k != "ambient_debt" for k, _ in pairs)

    def test_ordering_edit_related_before_ambient(self) -> None:
        """SWAP: edit_related pair appears before ambient_debt pair."""
        pairs: list[tuple[str, str]] = []
        coherence = CoherenceResult(
            state="isolated",
            edit_scoped=True,
            edit_related_channels=["lint"],
            ambient_channels=["structure"],
        )
        _append_delta_pairs(pairs, coherence, None, None)
        keys = [k for k, _ in pairs]
        assert keys.index("edit_related") < keys.index("ambient_debt")


# ═══════════════════════════════════════════════════════════════════════
# _append_status_pairs
# ═══════════════════════════════════════════════════════════════════════


class TestAppendStatusPairs:
    """Tests for _append_status_pairs — loud channels, resurface, cycles."""

    def test_no_loud_channels(self) -> None:
        """VALUE: all passing channels produce no loud pair."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh(channel_results=[
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="test", status="pass"),
        ])
        _append_status_pairs(pairs, mesh, 0, [])
        assert all(k != "loud" for k, _ in pairs)

    def test_loud_channels_fail(self) -> None:
        """VALUE: failing channels appear in loud pair with status."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh(channel_results=[
            ChannelResult(channel="lint", status="fail"),
            ChannelResult(channel="test", status="pass"),
        ])
        _append_status_pairs(pairs, mesh, 0, [])
        assert ("loud", "lint:fail") in pairs

    def test_loud_channels_error_and_timeout(self) -> None:
        """VALUE: error and timeout statuses are included in loud."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh(channel_results=[
            ChannelResult(channel="lint", status="error"),
            ChannelResult(channel="test", status="timeout"),
        ])
        _append_status_pairs(pairs, mesh, 0, [])
        loud_pairs = [v for k, v in pairs if k == "loud"]
        assert len(loud_pairs) == 1
        loud_val = loud_pairs[0]
        assert "lint:error" in loud_val
        assert "test:timeout" in loud_val

    def test_loud_channels_skip_not_included(self) -> None:
        """BOUNDARY: skip status is NOT included in loud."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh(channel_results=[
            ChannelResult(channel="lint", status="skip"),
        ])
        _append_status_pairs(pairs, mesh, 0, [])
        assert all(k != "loud" for k, _ in pairs)

    def test_resurfaced_count_positive(self) -> None:
        """VALUE: resurfaced_count > 0 produces resurface pair."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh()
        _append_status_pairs(pairs, mesh, 3, [])
        assert ("resurface", "3") in pairs

    def test_resurfaced_count_zero_omitted(self) -> None:
        """BOUNDARY: resurfaced_count = 0 produces no resurface pair."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh()
        _append_status_pairs(pairs, mesh, 0, [])
        assert all(k != "resurface" for k, _ in pairs)

    def test_cycle_alerts(self) -> None:
        """VALUE: cycle_alerts list produces comma-joined cycles pair."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh()
        _append_status_pairs(pairs, mesh, 0, ["lint<->test", "git<->structure"])
        assert ("cycles", "lint<->test,git<->structure") in pairs

    def test_cycle_alerts_empty_omitted(self) -> None:
        """BOUNDARY: empty cycle_alerts produces no cycles pair."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh()
        _append_status_pairs(pairs, mesh, 0, [])
        assert all(k != "cycles" for k, _ in pairs)

    def test_ordering_loud_before_resurface_before_cycles(self) -> None:
        """SWAP: loud appears before resurface, which appears before cycles."""
        pairs: list[tuple[str, str]] = []
        mesh = _make_mesh(channel_results=[
            ChannelResult(channel="lint", status="fail"),
        ])
        _append_status_pairs(pairs, mesh, 2, ["a<->b"])
        keys = [k for k, _ in pairs]
        assert keys.index("loud") < keys.index("resurface")
        assert keys.index("resurface") < keys.index("cycles")

    def test_parameter_order_mesh_vs_resurfaced(self) -> None:
        """SWAP: swapping mesh_result and resurfaced_count args would break results."""
        pairs_correct: list[tuple[str, str]] = []
        mesh = _make_mesh(channel_results=[
            ChannelResult(channel="lint", status="fail"),
        ])
        _append_status_pairs(pairs_correct, mesh, 5, [])
        # Verify resurface has correct value from resurfaced_count, not mesh
        assert ("resurface", "5") in pairs_correct
        assert ("loud", "lint:fail") in pairs_correct


# ═══════════════════════════════════════════════════════════════════════
# _build_posttooluse_context
# ═══════════════════════════════════════════════════════════════════════


class TestBuildPostToolUseContext:
    """Tests for _build_posttooluse_context — end-to-end context string."""

    def test_minimal_stable(self) -> None:
        """VALUE: stable state with no findings produces exact baseline."""
        inputs = _make_inputs(coherence_state="stable", channels_run=3)
        result = _build_posttooluse_context(inputs)
        assert result == "coherence=stable; channels_run=3"

    def test_blocking_count_included(self) -> None:
        """VALUE: blocking_count > 0 appears in output."""
        inputs = _make_inputs(blocking_count=2, channels_run=4)
        result = _build_posttooluse_context(inputs)
        assert "blocking=2" in result

    def test_blocking_count_zero_omitted(self) -> None:
        """BOUNDARY: blocking_count = 0 omits the blocking key entirely."""
        inputs = _make_inputs(blocking_count=0, channels_run=4)
        result = _build_posttooluse_context(inputs)
        assert "blocking=" not in result

    def test_warning_count_included(self) -> None:
        """VALUE: warning_count > 0 appears in output."""
        inputs = _make_inputs(warning_count=5, channels_run=4)
        result = _build_posttooluse_context(inputs)
        assert "warnings=5" in result

    def test_warning_count_zero_omitted(self) -> None:
        """BOUNDARY: warning_count = 0 omits the warnings key entirely."""
        inputs = _make_inputs(warning_count=0, channels_run=4)
        result = _build_posttooluse_context(inputs)
        assert "warnings=" not in result

    def test_coherence_always_first(self) -> None:
        """SWAP: coherence key is always the first field."""
        inputs = _make_inputs(
            coherence_state="systemic",
            blocking_count=3,
            warning_count=7,
            channels_run=5,
        )
        result = _build_posttooluse_context(inputs)
        assert result.startswith("coherence=systemic")

    def test_channels_run_always_second(self) -> None:
        """SWAP: channels_run key is always the second field."""
        inputs = _make_inputs(
            coherence_state="stable",
            blocking_count=1,
            channels_run=5,
        )
        result = _build_posttooluse_context(inputs)
        parts = result.split("; ")
        assert parts[0].startswith("coherence=")
        assert parts[1].startswith("channels_run=")

    def test_delta_fields_propagated(self) -> None:
        """VALUE: delta new_findings and resolved propagate through."""
        inputs = _make_inputs(
            channels_run=3,
            delta={"new": [{"count": 2}], "resolved_count": 1, "still_active_count": 3},
        )
        result = _build_posttooluse_context(inputs)
        assert "new_findings=2" in result
        assert "resolved=1" in result
        assert "known_debt=3" in result

    def test_max_300_chars_enforced(self) -> None:
        """BOUNDARY: output never exceeds 300 characters."""
        # Create inputs that would produce a long string
        inputs = _make_inputs(
            coherence_state="systemic",
            blocking_count=100,
            warning_count=200,
            channels_run=10,
            delta={
                "new": [{"count": 999}],
                "resolved_count": 888,
                "still_active_count": 777,
            },
            baseline_delta={"new": [{"count": 666}]},
            resurfaced_count=555,
            cycle_alerts=["a" * 50 + "<->" + "b" * 50],
            channel_results=[
                ChannelResult(channel="lint", status="fail"),
                ChannelResult(channel="test", status="error"),
                ChannelResult(channel="structure", status="timeout"),
            ],
        )
        result = _build_posttooluse_context(inputs)
        assert len(result) <= 300

    def test_edit_scoped_fields_in_output(self) -> None:
        """VALUE: edit-scoped coherence produces edit_related and ambient_debt."""
        inputs = _make_inputs(
            coherence_state="isolated",
            channels_run=4,
            edit_scoped=True,
            edit_related_channels=["lint"],
            ambient_channels=["structure"],
        )
        result = _build_posttooluse_context(inputs)
        assert "edit_related=lint" in result
        assert "ambient_debt=structure" in result

    def test_cycle_alerts_in_output(self) -> None:
        """VALUE: cycle_alerts appear in final output."""
        inputs = _make_inputs(
            channels_run=3,
            cycle_alerts=["lint<->test"],
        )
        result = _build_posttooluse_context(inputs)
        assert "cycles=lint<->test" in result

    def test_resurfaced_in_output(self) -> None:
        """VALUE: resurfaced_count > 0 appears in final output."""
        inputs = _make_inputs(
            channels_run=3,
            resurfaced_count=2,
        )
        result = _build_posttooluse_context(inputs)
        assert "resurface=2" in result
