"""Phase 2: Lint Channel Parity Gate — HARD GATE.

This test suite verifies that LintChannel produces identical results
to the direct pipeline. All tests must pass 100% before Phase 3.

The parity contract:
- Same tier selected
- Same issue counts per severity bucket (blocking, warning, info)
- Same blocking issues (by linter+kind+file+line)
- Same overall status (pass/fail)

Allowed diffs:
- Timing metadata (duration_ms)
- Exact ordering of issues within the same severity bucket
- Recurrence/pattern data (side effects are non-deterministic)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lintgate.change_classifier import classify_change
from lintgate.channels.lint_channel import LintChannel
from lintgate.config import load_config
from lintgate.controlplane.channel import Channel
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.lint_runner import run_linters
from lintgate.registry import build_registry
from lintgate.results_aggregator import aggregate_results
from lintgate.tier_selector import select_tier
from lintgate.types import AggregatedResult, ChangeClassification, ProjectConfig

GOLDEN_DIR = Path(__file__).parent / "golden"


# ── Debounce clearing ──────────────────────────────────────────────────


def _clear_debounce() -> None:
    """Clear the tier selector's debounce state.

    The debounce mechanism is persisted to disk, so running two
    pipelines on the same files back-to-back would cause the second
    call to be debounced. We must clear it between comparison runs.
    """
    from lintgate.tier_selector import _DEBOUNCE_FILE

    if _DEBOUNCE_FILE.exists():
        _DEBOUNCE_FILE.unlink()


# ── Helpers ─────────────────────────────────────────────────────────────


def _load_golden(name: str) -> dict[str, Any]:
    """Load a golden input JSON file."""
    path = GOLDEN_DIR / name
    with open(path) as f:
        return json.load(f)


def _run_direct_pipeline(
    input_data: dict[str, Any],
) -> tuple[AggregatedResult, str, str]:
    """Run the direct pipeline (same as hook_posttooluse.main phases 1-4).

    Returns:
        (AggregatedResult, tier_name, tier_reason)
    """
    _clear_debounce()  # Prevent debounce from affecting comparison

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")
    cwd = input_data.get("cwd", os.getcwd())

    try:
        config = load_config(cwd)
    except Exception:
        config = ProjectConfig(project_root=cwd)

    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    if classification.risk_level == "none":
        return AggregatedResult(), "skip", "risk_none"

    tier = select_tier(classification, config)
    if tier.skip:
        return AggregatedResult(), tier.name, tier.reason

    registry = build_registry(config)
    linter_results = run_linters(tier, config, registry, timeout_ms=8000)
    aggregated = aggregate_results(
        linter_results,
        config,
        tier_name=tier.name,
        tier_reason=tier.reason,
    )

    return aggregated, tier.name, tier.reason


def _run_lint_channel(input_data: dict[str, Any]) -> tuple[Any, AggregatedResult]:
    """Run the LintChannel and return (ChannelResult, AggregatedResult).

    Uses execute_pure() to get both outputs for comparison.
    """
    _clear_debounce()  # Prevent debounce from affecting comparison

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")
    cwd = input_data.get("cwd", os.getcwd())

    try:
        config = load_config(cwd)
    except Exception:
        config = ProjectConfig(project_root=cwd)

    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    event = SupervisionEvent(
        project_root=cwd,
        tool_name=tool_name,
        files_changed=classification.files_changed,
        change_classification=classification,
        raw_input=tool_input,
    )

    channel = LintChannel()
    cp_config = ControlPlaneConfig(enabled=True)

    channel_result, aggregated = channel.execute_pure(event, cp_config)
    return channel_result, aggregated


def _make_issue_set(issues: list) -> set[tuple]:
    """Create a comparable set from a list of LintIssue objects.

    Uses (linter, kind, file, line) tuples for comparison, ignoring
    ordering within the same severity bucket.
    """
    return {(i.linter, i.kind, i.file, i.line) for i in issues}


# ── Protocol conformance ─────────────────────────────────────────────────


def test_lint_channel_conforms_to_protocol() -> None:
    ch = LintChannel()
    assert isinstance(ch, Channel)


def test_lint_channel_has_correct_name() -> None:
    assert LintChannel.name == "lint"


def test_lint_channel_is_blocking_capable() -> None:
    assert LintChannel.blocking_capable is True


# ── should_run parity ────────────────────────────────────────────────────


def test_should_run_skips_none_risk() -> None:
    """Mirrors hook_posttooluse.py quick exit: risk_level == 'none'."""
    classification = ChangeClassification(
        files_changed=[],
        change_kind="logic",
        risk_level="none",
    )
    event = SupervisionEvent(
        project_root="/tmp/test",
        tool_name="Bash",
        change_classification=classification,
    )
    channel = LintChannel()
    config = ControlPlaneConfig()
    assert channel.should_run(event, config) is False


def test_should_run_skips_no_classification() -> None:
    event = SupervisionEvent(
        project_root="/tmp/test",
        tool_name="Edit",
        change_classification=None,
    )
    channel = LintChannel()
    config = ControlPlaneConfig()
    assert channel.should_run(event, config) is False


def test_should_run_mcp_without_classification() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp/test",
        tool_name="controlplane_run",
        change_classification=None,
    )
    channel = LintChannel()
    config = ControlPlaneConfig()
    assert channel.should_run(event, config) is True


def test_should_run_activates_on_moderate_risk() -> None:
    classification = ChangeClassification(
        files_changed=["/tmp/test/app.py"],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp/test",
        tool_name="Edit",
        change_classification=classification,
    )
    channel = LintChannel()
    config = ControlPlaneConfig()
    assert channel.should_run(event, config) is True


def test_should_run_activates_on_structural_risk() -> None:
    classification = ChangeClassification(
        files_changed=["/tmp/test/app.py"],
        change_kind="structural",
        risk_level="structural",
    )
    event = SupervisionEvent(
        project_root="/tmp/test",
        tool_name="Edit",
        change_classification=classification,
    )
    channel = LintChannel()
    config = ControlPlaneConfig()
    assert channel.should_run(event, config) is True


# ── Tier parity tests ────────────────────────────────────────────────────


def _parity_check_tier(golden_name: str) -> None:
    """Check that both paths select the same tier."""
    input_data = _load_golden(golden_name)

    # Direct pipeline
    direct_agg, direct_tier, direct_reason = _run_direct_pipeline(input_data)

    # LintChannel
    channel_result, channel_agg = _run_lint_channel(input_data)

    # Both should select same tier
    channel_tier = channel_result.metrics.get("tier_used", "")
    if direct_tier == "skip":
        # Both should skip
        assert channel_result.status == "skip" or channel_tier == "" or channel_tier == "skip"
    else:
        assert channel_tier == direct_tier, (
            f"Tier mismatch: direct={direct_tier}, channel={channel_tier}"
        )


def test_tier_parity_logic_edit() -> None:
    _parity_check_tier("logic_edit.json")


def test_tier_parity_config_change() -> None:
    _parity_check_tier("config_change.json")


def test_tier_parity_import_only() -> None:
    _parity_check_tier("import_only.json")


def test_tier_parity_structural_change() -> None:
    _parity_check_tier("structural_change.json")


# ── Issue count parity tests ─────────────────────────────────────────────


def _parity_check_issue_counts(golden_name: str) -> None:
    """Check that both paths produce same issue counts per severity."""
    input_data = _load_golden(golden_name)

    direct_agg, _, _ = _run_direct_pipeline(input_data)
    _, channel_agg = _run_lint_channel(input_data)

    assert len(direct_agg.blocking) == len(channel_agg.blocking), (
        f"Blocking count mismatch: direct={len(direct_agg.blocking)}, "
        f"channel={len(channel_agg.blocking)}"
    )
    assert len(direct_agg.warnings) == len(channel_agg.warnings), (
        f"Warning count mismatch: direct={len(direct_agg.warnings)}, "
        f"channel={len(channel_agg.warnings)}"
    )
    assert len(direct_agg.informational) == len(channel_agg.informational), (
        f"Info count mismatch: direct={len(direct_agg.informational)}, "
        f"channel={len(channel_agg.informational)}"
    )


def test_issue_count_parity_logic_edit() -> None:
    _parity_check_issue_counts("logic_edit.json")


def test_issue_count_parity_config_change() -> None:
    _parity_check_issue_counts("config_change.json")


def test_issue_count_parity_import_only() -> None:
    _parity_check_issue_counts("import_only.json")


def test_issue_count_parity_structural_change() -> None:
    _parity_check_issue_counts("structural_change.json")


# ── Blocking issue parity ────────────────────────────────────────────────


def _parity_check_blocking_issues(golden_name: str) -> None:
    """Check that both paths produce identical blocking issues."""
    input_data = _load_golden(golden_name)

    direct_agg, _, _ = _run_direct_pipeline(input_data)
    _, channel_agg = _run_lint_channel(input_data)

    direct_set = _make_issue_set(direct_agg.blocking)
    channel_set = _make_issue_set(channel_agg.blocking)

    assert direct_set == channel_set, (
        f"Blocking issues differ:\n"
        f"  Only in direct: {direct_set - channel_set}\n"
        f"  Only in channel: {channel_set - direct_set}"
    )


def test_blocking_parity_logic_edit() -> None:
    _parity_check_blocking_issues("logic_edit.json")


def test_blocking_parity_config_change() -> None:
    _parity_check_blocking_issues("config_change.json")


def test_blocking_parity_import_only() -> None:
    _parity_check_blocking_issues("import_only.json")


def test_blocking_parity_structural_change() -> None:
    _parity_check_blocking_issues("structural_change.json")


# ── ChannelResult status/severity correctness ────────────────────────────


def _parity_check_status(golden_name: str) -> None:
    """Check that ChannelResult status/severity correctly reflects AggregatedResult."""
    input_data = _load_golden(golden_name)
    channel_result, channel_agg = _run_lint_channel(input_data)

    if channel_result.status == "skip":
        return  # Skip case, no status to verify

    # Derive expected status from aggregated
    if channel_agg.blocking:
        assert channel_result.status == "fail"
        assert channel_result.severity == "blocking"
    elif channel_agg.warnings:
        assert channel_result.status == "fail"
        assert channel_result.severity == "warning"
    else:
        if channel_agg.metrics.get("total_issues", 0) == 0:
            assert channel_result.status == "pass"
            assert channel_result.severity == "none"


def test_status_parity_logic_edit() -> None:
    _parity_check_status("logic_edit.json")


def test_status_parity_config_change() -> None:
    _parity_check_status("config_change.json")


def test_status_parity_import_only() -> None:
    _parity_check_status("import_only.json")


def test_status_parity_structural_change() -> None:
    _parity_check_status("structural_change.json")


# ── Build command (Bash) parity ──────────────────────────────────────────


def test_build_command_is_skipped_by_channel() -> None:
    """Build commands have risk_level='none' from classify_change, so LintChannel should skip."""
    input_data = _load_golden("build_command.json")

    tool_name = input_data["tool_name"]
    tool_input = input_data["tool_input"]
    tool_output = input_data["tool_output"]
    cwd = input_data["cwd"]

    try:
        config = load_config(cwd)
    except Exception:
        config = ProjectConfig(project_root=cwd)

    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    # Build commands should have none or dependency risk level
    # The channel should handle this appropriately
    event = SupervisionEvent(
        project_root=cwd,
        tool_name=tool_name,
        files_changed=classification.files_changed,
        change_classification=classification,
        raw_input=tool_input,
    )

    channel = LintChannel()
    cp_config = ControlPlaneConfig()

    if classification.risk_level == "none":
        # Channel should not run
        assert channel.should_run(event, cp_config) is False
    else:
        # Channel should produce same result as direct pipeline
        direct_agg, _, _ = _run_direct_pipeline(input_data)
        channel_result = channel.execute(event, cp_config)
        # At minimum, status should agree
        if not direct_agg.blocking and not direct_agg.warnings:
            assert channel_result.status in ("pass", "skip")


# ── Full mesh parity ─────────────────────────────────────────────────────


def test_mesh_with_lint_channel_matches_direct_pipeline() -> None:
    """Run the mesh with only LintChannel and compare to direct pipeline.

    This is the most comprehensive parity test — it exercises the full
    mesh runtime path with a real channel.
    """
    from lintgate.controlplane.runtime import run_mesh

    input_data = _load_golden("logic_edit.json")

    tool_name = input_data["tool_name"]
    tool_input = input_data["tool_input"]
    tool_output = input_data["tool_output"]
    cwd = input_data["cwd"]

    try:
        config = load_config(cwd)
    except Exception:
        config = ProjectConfig(project_root=cwd)

    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    event = SupervisionEvent(
        project_root=cwd,
        tool_name=tool_name,
        files_changed=classification.files_changed,
        change_classification=classification,
        raw_input=tool_input,
    )

    # Run through mesh
    cp_config = ControlPlaneConfig(enabled=True, latency_budget_ms=15000)
    mesh_result = run_mesh(event, cp_config, [LintChannel()])

    # Run direct
    direct_agg, direct_tier, _ = _run_direct_pipeline(input_data)

    # Find the lint channel result in mesh output
    lint_results = [r for r in mesh_result.channel_results if r.channel == "lint"]
    assert len(lint_results) == 1, f"Expected 1 lint result, got {len(lint_results)}"
    lint_result = lint_results[0]

    if lint_result.status == "skip":
        # Both should be empty
        assert len(direct_agg.blocking) == 0
        assert len(direct_agg.warnings) == 0
    else:
        # Issue counts must match
        direct_total = (
            len(direct_agg.blocking) + len(direct_agg.warnings) + len(direct_agg.informational)
        )
        channel_total = len(lint_result.findings)
        assert channel_total == direct_total, (
            f"Total issue count mismatch: direct={direct_total}, channel={channel_total}"
        )

    # Mesh should not be partial (single fast channel)
    assert mesh_result.partial is False
    assert mesh_result.incomplete_channels == []


# ── Edge cases ───────────────────────────────────────────────────────────


def test_lint_channel_handles_missing_project_gracefully() -> None:
    """LintChannel should not crash on a non-existent project root."""
    classification = ChangeClassification(
        files_changed=["/nonexistent/foo.py"],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/nonexistent/project",
        tool_name="Edit",
        files_changed=["/nonexistent/foo.py"],
        change_classification=classification,
    )

    channel = LintChannel()
    cp_config = ControlPlaneConfig()

    # Should not raise — graceful degradation
    result = channel.execute(event, cp_config)
    assert result.channel == "lint"
    # Status can be pass (no issues found for nonexistent files) or skip or error
    assert result.status in ("pass", "skip", "error")


def test_lint_channel_execute_returns_channel_result() -> None:
    """Basic type check on execute return value."""
    from lintgate.controlplane.types import ChannelResult

    classification = ChangeClassification(
        files_changed=["/tmp/test.py"],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        files_changed=["/tmp/test.py"],
        change_classification=classification,
    )

    channel = LintChannel()
    cp_config = ControlPlaneConfig()

    result = channel.execute(event, cp_config)
    assert isinstance(result, ChannelResult)
    assert result.channel == "lint"
    # duration_ms may be 0 if tier is skipped (no linters run)
    assert result.duration_ms >= 0
