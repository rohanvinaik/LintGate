"""Phase 1: ControlPlane mesh kernel tests.

Tests the core runtime: channel protocol, parallel execution,
shedding policy, budget enforcement, and coherence engine.
"""

from __future__ import annotations

import time

from lintgate.controlplane.channel import Channel
from lintgate.controlplane.coherence import compute_coherence
from lintgate.controlplane.runtime import run_mesh
from lintgate.controlplane.types import (
    ChannelConfig,
    ChannelResult,
    ControlPlaneConfig,
    MeshResult,
    SupervisionEvent,
)
from lintgate.types import LintIssue

# ── Test fixtures ─────────────────────────────────────────────────────


def _make_event(**kwargs) -> SupervisionEvent:
    defaults = {
        "project_root": "/tmp/test",
        "tool_name": "Edit",
        "files_changed": ["/tmp/test/app.py"],
    }
    defaults.update(kwargs)
    return SupervisionEvent(**defaults)


def _make_config(**kwargs) -> ControlPlaneConfig:
    return ControlPlaneConfig(**kwargs)


class PassChannel:
    """Mock channel that always passes."""

    name = "pass_channel"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        return ChannelResult(channel=self.name, status="pass", severity="none")


class FailChannel:
    """Mock channel that always fails with one finding."""

    name = "fail_channel"
    timeout_ms = 5000
    blocking_capable = True

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        return ChannelResult(
            channel=self.name,
            status="fail",
            severity="warning",
            findings=[
                LintIssue(
                    linter="test",
                    kind="test_issue",
                    message="Test failure",
                    file="/tmp/test/app.py",
                    severity="warning",
                ),
            ],
        )


class SlowChannel:
    """Mock channel that sleeps for a configurable duration."""

    def __init__(self, name: str = "slow_channel", sleep_seconds: float = 2.0):
        self.name = name
        self.timeout_ms = 5000
        self.blocking_capable = False
        self._sleep = sleep_seconds

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        time.sleep(self._sleep)
        return ChannelResult(channel=self.name, status="pass", severity="none")


class SkipChannel:
    """Mock channel that always skips."""

    name = "skip_channel"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        return False

    def execute(self, event, config):
        return ChannelResult(channel=self.name, status="pass", severity="none")


class ErrorChannel:
    """Mock channel that raises during execute."""

    name = "error_channel"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        msg = "Simulated channel crash"
        raise RuntimeError(msg)


# ── Channel protocol tests ────────────────────────────────────────────


def test_pass_channel_conforms_to_protocol() -> None:
    ch = PassChannel()
    assert isinstance(ch, Channel)


def test_fail_channel_conforms_to_protocol() -> None:
    ch = FailChannel()
    assert isinstance(ch, Channel)


# ── Runtime parallel execution tests ──────────────────────────────────


def test_single_passing_channel() -> None:
    result = run_mesh(_make_event(), _make_config(), [PassChannel()])

    assert isinstance(result, MeshResult)
    assert len(result.channel_results) == 1
    assert result.channel_results[0].status == "pass"
    assert result.coherence.state == "stable"
    assert not result.partial


def test_single_failing_channel() -> None:
    result = run_mesh(_make_event(), _make_config(), [FailChannel()])

    assert len(result.channel_results) == 1
    assert result.channel_results[0].status == "fail"
    assert len(result.channel_results[0].findings) == 1


def test_mixed_pass_and_fail() -> None:
    channels = [PassChannel(), FailChannel()]
    result = run_mesh(_make_event(), _make_config(), channels)

    assert len(result.channel_results) == 2
    statuses = {r.channel: r.status for r in result.channel_results}
    assert statuses["pass_channel"] == "pass"
    assert statuses["fail_channel"] == "fail"


def test_skipped_channel_appears_in_results() -> None:
    result = run_mesh(_make_event(), _make_config(), [SkipChannel()])

    assert len(result.channel_results) == 1
    assert result.channel_results[0].status == "skip"


def test_error_channel_reported_not_raised() -> None:
    result = run_mesh(_make_event(), _make_config(), [ErrorChannel()])

    assert len(result.channel_results) == 1
    assert result.channel_results[0].status == "error"
    assert result.channel_results[0].error_message is not None
    assert "RuntimeError" in result.channel_results[0].error_message


def test_disabled_channel_skipped() -> None:
    config = _make_config(channels={"pass_channel": ChannelConfig(enabled=False)})
    result = run_mesh(_make_event(), config, [PassChannel()])

    assert len(result.channel_results) == 1
    assert result.channel_results[0].status == "skip"
    assert result.channel_results[0].metrics.get("reason") == "disabled_in_config"


# ── Shedding / timeout tests ─────────────────────────────────────────


def test_slow_channel_is_shed_under_tight_budget() -> None:
    config = _make_config(latency_budget_ms=500)  # Very tight
    channels = [SlowChannel(sleep_seconds=5.0)]  # Much longer than budget

    result = run_mesh(_make_event(), config, channels)

    assert len(result.channel_results) == 1
    assert result.channel_results[0].status == "timeout"
    assert result.partial is True
    assert "slow_channel" in result.incomplete_channels


def test_fast_channel_completes_while_slow_shed() -> None:
    config = _make_config(latency_budget_ms=2000)
    channels = [PassChannel(), SlowChannel(sleep_seconds=10.0)]

    result = run_mesh(_make_event(), config, channels)

    statuses = {r.channel: r.status for r in result.channel_results}
    assert statuses["pass_channel"] == "pass"
    assert statuses["slow_channel"] == "timeout"
    assert result.partial is True


# ── Budget enforcement ────────────────────────────────────────────────


def test_global_budget_respected() -> None:
    config = _make_config(latency_budget_ms=3000)
    channels = [
        SlowChannel(name="slow1", sleep_seconds=10.0),
        SlowChannel(name="slow2", sleep_seconds=10.0),
    ]

    start = time.perf_counter()
    result = run_mesh(_make_event(), config, channels)
    elapsed = time.perf_counter() - start

    # Should complete within budget + some buffer (not wait for all 20s)
    assert elapsed < 5.0
    assert result.partial is True


# ── Coherence engine tests ────────────────────────────────────────────


def test_coherence_stable_all_pass() -> None:
    results = [
        ChannelResult(channel="lint", status="pass"),
        ChannelResult(channel="tests", status="pass"),
        ChannelResult(channel="deps", status="pass"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "stable"


def test_coherence_stable_with_skips() -> None:
    results = [
        ChannelResult(channel="lint", status="pass"),
        ChannelResult(channel="tests", status="skip"),
        ChannelResult(channel="deps", status="pass"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "stable"


def test_coherence_isolated_single_failure() -> None:
    results = [
        ChannelResult(channel="lint", status="pass"),
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(linter="tests", kind="failure", message="test failed"),
            ],
        ),
        ChannelResult(channel="deps", status="pass"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "isolated"
    assert "tests" in coherence.loud_channels
    assert "lint" in coherence.silent_channels
    assert "deps" in coherence.silent_channels
    assert "tests" in coherence.summary


def test_coherence_isolated_single_failure_without_pass_signals() -> None:
    results = [
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(linter="tests", kind="failure", message="test failed"),
            ],
        ),
        ChannelResult(channel="lint", status="skip"),
        ChannelResult(channel="deps", status="skip"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "isolated"
    assert "tests" in coherence.loud_channels
    assert "confidence is limited" in coherence.summary


def test_coherence_coupled_two_failures_shared_files() -> None:
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(linter="ruff", kind="E501", message="line too long", file="/app.py"),
            ],
        ),
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(
                    linter="pytest",
                    kind="failure",
                    message="test failed",
                    file="/app.py",
                ),
            ],
        ),
        ChannelResult(channel="deps", status="pass"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "coupled"
    assert "app.py" in coherence.summary


def test_coherence_weighted_coupled_shared_files_three_channels_plural_wording() -> None:
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(
                    linter="ruff",
                    kind="E501",
                    message="line too long",
                    file="/app.py",
                    severity="informational",
                ),
            ],
        ),
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(
                    linter="pytest",
                    kind="failure",
                    message="test failed",
                    file="/app.py",
                    severity="informational",
                ),
            ],
        ),
        ChannelResult(
            channel="structure",
            status="fail",
            findings=[
                LintIssue(
                    linter="structure",
                    kind="STRUCT004",
                    message="low cohesion",
                    file="/app.py",
                    severity="informational",
                ),
            ],
        ),
    ]
    coherence = compute_coherence(results, severity_weighted=True)
    # All three channels have only informational findings — with severity_weighted=True,
    # they are demoted from coherence classification, resulting in "stable"
    assert coherence.state == "stable"
    assert any("demoted" in n for n in coherence.classification_notes)


def test_coherence_weighted_coupled_independent_three_channels_lists_all_actions() -> None:
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(
                    linter="ruff",
                    kind="E501",
                    message="line too long",
                    file="/a.py",
                    severity="informational",
                ),
            ],
        ),
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(
                    linter="pytest",
                    kind="failure",
                    message="test failed",
                    file="/b.py",
                    severity="informational",
                ),
            ],
        ),
        ChannelResult(
            channel="structure",
            status="fail",
            findings=[
                LintIssue(
                    linter="structure",
                    kind="STRUCT002",
                    message="module skew",
                    file="/c.py",
                    severity="informational",
                ),
            ],
        ),
    ]
    coherence = compute_coherence(results, severity_weighted=True)
    # All three channels have only informational findings — demoted to stable
    assert coherence.state == "stable"
    assert any("demoted" in n for n in coherence.classification_notes)


def test_coherence_weighted_cross_domain_low_signal_is_not_systemic() -> None:
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(
                    linter="ruff",
                    kind="E501",
                    message="line too long",
                    severity="warning",
                    file="/a.py",
                ),
            ],
        ),
        ChannelResult(
            channel="deps",
            status="fail",
            findings=[
                LintIssue(
                    linter="deps",
                    kind="lockfile",
                    message="lock drift",
                    severity="informational",
                    file="/pyproject.toml",
                ),
            ],
        ),
        ChannelResult(channel="tests", status="pass"),
    ]
    coherence = compute_coherence(results, severity_weighted=True)
    # deps has only informational findings → demoted, leaving only lint failing → isolated
    assert coherence.state == "isolated"
    assert any("deps demoted" in n for n in coherence.classification_notes)


def test_coherence_weighted_orders_channels_by_failure_volume() -> None:
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(
                    linter="ruff",
                    kind="E501",
                    message=f"line too long {i}",
                    severity="warning",
                    file="/a.py",
                )
                for i in range(6)
            ],
        ),
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(
                    linter="pytest",
                    kind="failure",
                    message="single failure",
                    severity="warning",
                    file="/b.py",
                ),
            ],
        ),
        ChannelResult(channel="deps", status="pass"),
    ]
    coherence = compute_coherence(results, severity_weighted=True)
    assert coherence.state == "coupled"
    assert coherence.recommended_action.startswith("Address lint first")


def test_coherence_systemic_three_failures() -> None:
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(linter="ruff", kind="E501", message="issue"),
            ],
        ),
        ChannelResult(
            channel="tests",
            status="fail",
            findings=[
                LintIssue(linter="pytest", kind="failure", message="issue"),
            ],
        ),
        ChannelResult(
            channel="deps",
            status="fail",
            findings=[
                LintIssue(linter="deps", kind="lockfile", message="issue"),
            ],
        ),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "systemic"


def test_coherence_systemic_cross_domain() -> None:
    """Infra (deps) + code (lint) failure = cross-domain = systemic."""
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(linter="ruff", kind="E501", message="issue"),
            ],
        ),
        ChannelResult(
            channel="deps",
            status="fail",
            findings=[
                LintIssue(linter="deps", kind="lockfile", message="issue"),
            ],
        ),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "systemic"


def test_coherence_degraded_on_error() -> None:
    results = [
        ChannelResult(channel="lint", status="pass"),
        ChannelResult(channel="tests", status="error", error_message="crash"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "degraded"


def test_coherence_degraded_on_timeout() -> None:
    results = [
        ChannelResult(channel="lint", status="pass"),
        ChannelResult(channel="tests", status="timeout"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "degraded"


def test_coherence_empty_channels() -> None:
    coherence = compute_coherence([])
    assert coherence.state == "stable"


def test_coherence_all_skipped() -> None:
    results = [
        ChannelResult(channel="lint", status="skip"),
        ChannelResult(channel="tests", status="skip"),
    ]
    coherence = compute_coherence(results)
    assert coherence.state == "stable"


def test_edit_scope_stable_downgrade_clears_loud_channels() -> None:
    """Ambient-only failures can downgrade to stable without loud channels."""
    results = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(
                    linter="ruff",
                    kind="E501",
                    message="line too long",
                    severity="warning",
                    file="/tmp/test/legacy.py",
                ),
            ],
        ),
        ChannelResult(channel="tests", status="pass"),
    ]
    coherence = compute_coherence(results, files_changed=["/tmp/test/app.py"])
    assert coherence.state == "stable"
    assert coherence.edit_scoped is True
    assert coherence.ambient_channels == ["lint"]
    assert coherence.edit_related_channels == []
    assert coherence.loud_channels == []


# ── Mesh result assembly ──────────────────────────────────────────────


def test_mesh_result_has_duration() -> None:
    result = run_mesh(_make_event(), _make_config(), [PassChannel()])
    assert result.duration_ms > 0


def test_mesh_result_has_coherence() -> None:
    result = run_mesh(_make_event(), _make_config(), [PassChannel()])
    assert result.coherence.state == "stable"


def test_mesh_result_incomplete_channels_empty_when_all_complete() -> None:
    result = run_mesh(_make_event(), _make_config(), [PassChannel()])
    assert result.incomplete_channels == []
    assert result.partial is False
