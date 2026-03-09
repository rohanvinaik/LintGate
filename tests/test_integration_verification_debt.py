"""Phase 3: Tests for integration_verification_debt behavioral signal.

Verifies that the signal fires when channel/integration code is edited
without running integration verification, and clears correctly.
"""

from __future__ import annotations

import time

from lintgate.channels.behavior_detection import (
    INTEGRATION_PATHS,
    INTEGRATION_VERIFY_BASH_PATTERNS,
    detect_integration_verification_debt,
)
from lintgate.channels.behavior_scoring import IntentBiasScorer, SignalCoordinator
from lintgate.controlplane.behavior_types import BehaviorCompass, new_compass


def _make_compass_with_edits(
    edit_paths: list[str],
    *,
    verify_tool: str | None = None,
    verify_bash: str | None = None,
) -> BehaviorCompass:
    """Create a compass with simulated file edits and optional verification."""
    compass = new_compass()
    now = time.time()

    # Simulate edits
    for path in edit_paths:
        compass.action_history.append(
            {
                "tool": "Edit",
                "ts": now,
                "sig": path,
                "exit": None,
                "err": "",
                "intent": "modify",
            }
        )
        now += 1.0

    # Track integration edits
    for path in edit_paths:
        if any(path.startswith(ip) for ip in INTEGRATION_PATHS):
            compass.integration_edits_since_verify += 1

    # Simulate verification if requested
    if verify_tool:
        compass.action_history.append(
            {
                "tool": verify_tool,
                "ts": now,
                "sig": "",
                "exit": None,
                "err": "",
                "intent": "verify",
            }
        )
        compass.integration_edits_since_verify = 0
        compass.last_integration_verify_ts = now

    if verify_bash:
        compass.action_history.append(
            {
                "tool": "Bash",
                "ts": now,
                "sig": verify_bash,
                "exit": 0,
                "err": "",
                "intent": "execute",
            }
        )
        compass.integration_edits_since_verify = 0
        compass.last_integration_verify_ts = now

    return compass


def _run_detection(compass: BehaviorCompass) -> list:
    """Run the detection and return findings."""
    thresholds = {"integration_verification_debt_edits": 5}
    scorer = IntentBiasScorer(compass, {})
    coord = SignalCoordinator(compass, thresholds)

    detect_integration_verification_debt(compass, thresholds, coord, scorer)

    findings, _, _, _ = coord.finalize()
    return findings


def test_fires_after_5_channel_edits():
    """Signal fires after 5 unverified edits to channel files."""
    compass = _make_compass_with_edits(
        [
            "lintgate/channels/foo.py",
            "lintgate/channels/bar.py",
            "lintgate/controlplane/baz.py",
            "lintgate/convergence/qux.py",
            "mcp_tools/tool.py",
        ]
    )

    findings = _run_detection(compass)
    assert len(findings) >= 1
    assert any(f.kind == "integration_verification_debt" for f in findings)


def test_clears_after_controlplane_run():
    """Counter resets after controlplane_run MCP tool call."""
    compass = _make_compass_with_edits(
        [
            "lintgate/channels/foo.py",
            "lintgate/channels/bar.py",
            "lintgate/controlplane/baz.py",
            "lintgate/convergence/qux.py",
            "mcp_tools/tool.py",
        ],
        verify_tool="controlplane_run",
    )

    findings = _run_detection(compass)
    assert not any(f.kind == "integration_verification_debt" for f in findings)


def test_clears_after_integration_pytest():
    """Counter resets after running pytest on integration tests."""
    compass = _make_compass_with_edits(
        [
            "lintgate/channels/foo.py",
            "lintgate/channels/bar.py",
            "lintgate/controlplane/baz.py",
            "lintgate/convergence/qux.py",
            "mcp_tools/tool.py",
        ],
        verify_bash="pytest tests/test_integration_pipeline.py",
    )

    findings = _run_detection(compass)
    assert not any(f.kind == "integration_verification_debt" for f in findings)


def test_ignores_non_channel_edits():
    """Edits to non-channel files don't trigger the signal."""
    compass = _make_compass_with_edits(
        [
            "lintgate/types.py",
            "lintgate/state.py",
            "scripts/ship_main.py",
            "tests/test_foo.py",
            "README.md",
        ]
    )

    findings = _run_detection(compass)
    assert not any(f.kind == "integration_verification_debt" for f in findings)


def test_fires_on_commit_with_unverified_edits():
    """Signal fires when git commit attempted with unverified channel edits."""
    compass = new_compass()
    now = time.time()

    # Single channel edit
    compass.action_history.append(
        {
            "tool": "Edit",
            "ts": now,
            "sig": "lintgate/channels/foo.py",
            "exit": None,
            "err": "",
            "intent": "modify",
        }
    )
    compass.integration_edits_since_verify = 1

    # Git commit attempt
    compass.action_history.append(
        {
            "tool": "Bash",
            "ts": now + 1,
            "sig": "git:commit",
            "exit": 0,
            "err": "",
            "intent": "execute",
        }
    )

    thresholds = {"integration_verification_debt_edits": 5}
    scorer = IntentBiasScorer(compass, {})
    coord = SignalCoordinator(compass, thresholds)

    # The commit-path detection fires even with fewer than 5 edits
    detect_integration_verification_debt(compass, thresholds, coord, scorer)

    findings, _, _, _ = coord.finalize()
    commit_findings = [f for f in findings if f.kind == "integration_verification_debt"]
    assert len(commit_findings) >= 1


def test_bash_pattern_matching():
    """Verify all INTEGRATION_VERIFY_BASH_PATTERNS match expected commands."""
    import re

    test_commands = [
        "pytest tests/test_integration_pipeline.py",
        "pytest tests/test_metric_schemas.py",
        "pytest tests/test_specification_channel.py",
        "pytest -k integration tests/",
    ]

    for cmd in test_commands:
        matched = any(re.search(pat, cmd) for pat in INTEGRATION_VERIFY_BASH_PATTERNS)
        assert matched, f"Pattern should match command: {cmd!r}"

    # Non-matching commands
    non_matching = [
        "pytest tests/test_types.py",
        "ruff check lintgate/",
        "python -c 'print(1)'",
    ]

    for cmd in non_matching:
        matched = any(re.search(pat, cmd) for pat in INTEGRATION_VERIFY_BASH_PATTERNS)
        assert not matched, f"Pattern should NOT match command: {cmd!r}"


# ── record_tool_event integration (verifies P1 fix) ──────────────────


def test_record_tool_event_increments_on_edit():
    """record_tool_event increments integration_edits_since_verify on Edit to channel files."""
    from lintgate.controlplane.behavior_compass import record_tool_event

    compass = new_compass()
    assert compass.integration_edits_since_verify == 0

    record_tool_event(
        compass,
        "Edit",
        {"file_path": "/project/lintgate/channels/foo.py"},
        "ok",
        now=100.0,
    )
    assert compass.integration_edits_since_verify == 1

    record_tool_event(
        compass,
        "Edit",
        {"file_path": "/project/mcp_tools/bar.py"},
        "ok",
        now=101.0,
    )
    assert compass.integration_edits_since_verify == 2


def test_record_tool_event_ignores_non_integration_edit():
    """record_tool_event does NOT increment for edits outside integration paths."""
    from lintgate.controlplane.behavior_compass import record_tool_event

    compass = new_compass()
    record_tool_event(
        compass,
        "Edit",
        {"file_path": "/project/lintgate/types.py"},
        "ok",
        now=100.0,
    )
    assert compass.integration_edits_since_verify == 0


def test_record_tool_event_resets_on_controlplane_run():
    """record_tool_event resets counter when controlplane_run is called."""
    from lintgate.controlplane.behavior_compass import record_tool_event

    compass = new_compass()
    compass.integration_edits_since_verify = 5

    record_tool_event(compass, "controlplane_run", {}, "", now=100.0)
    assert compass.integration_edits_since_verify == 0
    assert compass.last_integration_verify_ts == 100.0


def test_record_tool_event_resets_on_integration_pytest():
    """record_tool_event resets counter on pytest integration bash command."""
    from lintgate.controlplane.behavior_compass import record_tool_event

    compass = new_compass()
    compass.integration_edits_since_verify = 3

    record_tool_event(
        compass,
        "Bash",
        {"command": "pytest tests/test_integration_pipeline.py"},
        "ok",
        now=200.0,
    )
    assert compass.integration_edits_since_verify == 0
    assert compass.last_integration_verify_ts == 200.0
