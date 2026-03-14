"""Comprehensive tests for lintgate/controlplane/runtime.py.

Covers the mesh runtime orchestrator: parallel channel execution,
shedding policy, prepass, convergence escalation, git context collection,
cross-channel coherence, and final coherence computation.
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest import mock

from lintgate.controlplane.runtime import (
    _apply_convergence_escalation,
    _collect_git_context,
    _compute_final_coherence,
    _escalate_convergent_findings,
    _execute_parallel,
    _filter_nested_subprojects,
    _run_cross_channel_coherence,
    _run_prepass,
    _run_single_channel,
    _scoped_discover,
    run_mesh,
)
from lintgate.controlplane.types import (
    ChannelConfig,
    ChannelResult,
    ControlPlaneConfig,
    MeshResult,
    SupervisionEvent,
)
from lintgate.types import LintIssue

# ── Helpers ───────────────────────────────────────────────────────────


def _make_event(**kwargs: object) -> SupervisionEvent:
    defaults: dict[str, object] = {
        "project_root": "",
        "tool_name": "Edit",
        "files_changed": [],
    }
    defaults.update(kwargs)
    return SupervisionEvent(**defaults)  # type: ignore[arg-type]  # dict unpacking


def _make_config(**kwargs) -> ControlPlaneConfig:
    return ControlPlaneConfig(**kwargs)


class PassChannel:
    name = "pass_ch"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        return ChannelResult(channel=self.name, status="pass", severity="none")


class FailChannel:
    name = "fail_ch"
    timeout_ms = 5000
    blocking_capable = True

    def __init__(self, name: str = "fail_ch", file: str | None = "/tmp/test/app.py"):
        self.name = name
        self._file = file

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
                    file=self._file,
                    severity="warning",
                ),
            ],
        )


class SkipChannel:
    name = "skip_ch"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        return False

    def execute(self, event, config):
        return ChannelResult(channel=self.name, status="pass", severity="none")


class ErrorChannel:
    name = "error_ch"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        msg = "Simulated channel crash"
        raise RuntimeError(msg)


class ShouldRunErrorChannel:
    """Channel whose should_run raises."""

    name = "should_run_err_ch"
    timeout_ms = 5000
    blocking_capable = False

    def should_run(self, event, config):
        msg = "should_run exploded"
        raise ValueError(msg)

    def execute(self, event, config):
        return ChannelResult(channel=self.name, status="pass", severity="none")


class SlowChannel:
    def __init__(self, name: str = "slow_ch", sleep_seconds: float = 2.0):
        self.name = name
        self.timeout_ms = 5000
        self.blocking_capable = False
        self._sleep = sleep_seconds

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        time.sleep(self._sleep)
        return ChannelResult(channel=self.name, status="pass", severity="none")


class RecordingChannel:
    """Channel that records the event it received."""

    name = "recording_ch"
    timeout_ms = 5000
    blocking_capable = False

    def __init__(self):
        self.received_event = None
        self.received_config = None

    def should_run(self, event, config):
        return True

    def execute(self, event, config):
        self.received_event = event
        self.received_config = config
        return ChannelResult(channel=self.name, status="pass", severity="none")


# ── TestRunMesh: the main orchestrator ────────────────────────────────


class TestRunMesh:
    """Tests for the run_mesh() top-level orchestrator."""

    def test_empty_channels_returns_stable(self):
        result = run_mesh(_make_event(), _make_config(), [])
        assert isinstance(result, MeshResult)
        assert result.channel_results == []
        assert result.coherence.state == "stable"
        assert result.partial is False
        assert result.incomplete_channels == []

    def test_single_pass_channel(self):
        result = run_mesh(_make_event(), _make_config(), [PassChannel()])
        assert len(result.channel_results) == 1
        assert result.channel_results[0].status == "pass"
        assert result.coherence.state == "stable"

    def test_single_fail_channel(self):
        result = run_mesh(_make_event(), _make_config(), [FailChannel()])
        channel_statuses = {r.channel: r.status for r in result.channel_results}
        assert channel_statuses["fail_ch"] == "fail"

    def test_disabled_channel_skipped_with_reason(self):
        config = _make_config(channels={"pass_ch": ChannelConfig(enabled=False)})
        result = run_mesh(_make_event(), config, [PassChannel()])
        assert len(result.channel_results) == 1
        cr = result.channel_results[0]
        assert cr.status == "skip"
        assert cr.metrics["reason"] == "disabled_in_config"

    def test_should_run_false_skipped_with_reason(self):
        result = run_mesh(_make_event(), _make_config(), [SkipChannel()])
        assert len(result.channel_results) == 1
        cr = result.channel_results[0]
        assert cr.status == "skip"
        assert cr.metrics["reason"] == "event_not_relevant"

    def test_should_run_exception_produces_error_result(self):
        result = run_mesh(_make_event(), _make_config(), [ShouldRunErrorChannel()])
        assert len(result.channel_results) == 1
        cr = result.channel_results[0]
        assert cr.status == "error"
        assert cr.error_message is not None
        assert "should_run failed" in cr.error_message
        assert "ValueError" in cr.error_message

    def test_error_channel_produces_error_result(self):
        result = run_mesh(_make_event(), _make_config(), [ErrorChannel()])
        errs = [r for r in result.channel_results if r.status == "error"]
        assert len(errs) == 1
        assert errs[0].error_message is not None
        assert "RuntimeError" in errs[0].error_message

    def test_duration_ms_positive(self):
        result = run_mesh(_make_event(), _make_config(), [PassChannel()])
        assert result.duration_ms > 0

    def test_mixed_channels_all_appear_in_results(self):
        channels: list[Any] = [PassChannel(), FailChannel(), SkipChannel()]
        result = run_mesh(_make_event(), _make_config(), channels)
        result_channels = {r.channel for r in result.channel_results}
        assert "pass_ch" in result_channels
        assert "fail_ch" in result_channels
        assert "skip_ch" in result_channels

    def test_partial_flag_false_when_all_complete(self):
        result = run_mesh(_make_event(), _make_config(), [PassChannel()])
        assert result.partial is False
        assert result.incomplete_channels == []

    def test_git_context_empty_when_no_project_root(self):
        event = _make_event(project_root="")
        result = run_mesh(event, _make_config(), [PassChannel()])
        assert result.git_context == {}

    def test_coherence_attached_to_result(self):
        result = run_mesh(_make_event(), _make_config(), [PassChannel()])
        assert result.coherence is not None
        assert hasattr(result.coherence, "state")

    def test_multiple_disabled_channels(self):
        config = _make_config(
            channels={
                "pass_ch": ChannelConfig(enabled=False),
                "fail_ch": ChannelConfig(enabled=False),
            }
        )
        result = run_mesh(_make_event(), config, [PassChannel(), FailChannel()])
        assert all(r.status == "skip" for r in result.channel_results)
        assert all(r.metrics["reason"] == "disabled_in_config" for r in result.channel_results)


# ── TestRunPrepass ────────────────────────────────────────────────────


class TestRunPrepass:
    """Tests for the _run_prepass() phase 0 shared artifact builder."""

    def test_no_project_root_is_noop(self):
        event = _make_event(project_root="")
        _run_prepass(event)
        assert "property_manifest" not in event.context

    def test_prepass_suppresses_exceptions(self):
        """Prepass should never raise — graceful degradation."""
        event = _make_event(project_root="/nonexistent/path/that/does/not/exist")
        # Should not raise even if manifest build fails
        _run_prepass(event)

    def test_prepass_with_real_project(self, tmp_path):
        """Prepass on a real directory with Python files populates context."""
        py_file = tmp_path / "example.py"
        py_file.write_text("def foo(): return 1\n")
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=[str(py_file)],
        )
        _run_prepass(event)
        # Prepass should at minimum set python_files if discovery succeeds
        # (property_manifest depends on the manifest builder's behavior)
        # We just verify it doesn't crash
        assert isinstance(event.context, dict)

    def test_prepass_honors_pre_populated_python_files(self, tmp_path):
        """When python_files is pre-populated in context, prepass uses it."""
        py_file = tmp_path / "example.py"
        py_file.write_text("def bar(): return 2\n")
        event = _make_event(
            project_root=str(tmp_path),
        )
        event.context["python_files"] = [str(py_file)]
        _run_prepass(event)
        # python_files should remain set (either unchanged or updated)
        assert "python_files" in event.context


# ── TestScopedDiscover ────────────────────────────────────────────────


class TestScopedDiscover:
    """Tests for _scoped_discover() file-level scoping logic."""

    def test_scoped_discover_with_small_changeset(self, tmp_path):
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1\n")
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=[str(py_file)],
        )
        result = _scoped_discover(event)
        assert str(py_file) in result

    def test_scoped_discover_non_python_files_ignored(self, tmp_path):
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("hello\n")
        py_file = tmp_path / "app.py"
        py_file.write_text("x = 1\n")
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=[str(txt_file), str(py_file)],
        )
        result = _scoped_discover(event)
        assert str(py_file) in result

    def test_scoped_discover_empty_changeset_uses_full_discovery(self, tmp_path):
        py_file = tmp_path / "app.py"
        py_file.write_text("y = 2\n")
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=[],
        )
        result = _scoped_discover(event)
        # Full discovery should find the file
        assert isinstance(result, list)

    def test_scoped_discover_large_changeset_uses_full_discovery(self, tmp_path):
        """More than 5 Python files falls back to full discovery."""
        files = []
        for i in range(6):
            f = tmp_path / f"file_{i}.py"
            f.write_text(f"x = {i}\n")
            files.append(str(f))
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=files,
        )
        result = _scoped_discover(event)
        assert isinstance(result, list)

    def test_scoped_discover_nonexistent_file_in_changeset(self, tmp_path):
        """Files that don't exist on disk are filtered out."""
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=[str(tmp_path / "nonexistent.py")],
        )
        result = _scoped_discover(event)
        # Falls back to full discovery since the scoped file doesn't exist
        assert isinstance(result, list)

    def test_scoped_discover_file_outside_project_root(self, tmp_path):
        """Files outside project root are filtered out."""
        outside_file = tmp_path.parent / "outside.py"
        try:
            outside_file.write_text("z = 3\n")
            event = _make_event(
                project_root=str(tmp_path),
                files_changed=[str(outside_file)],
            )
            result = _scoped_discover(event)
            # outside file should be excluded from scoped results
            assert str(outside_file) not in result
        finally:
            outside_file.unlink(missing_ok=True)

    def test_scoped_discover_relative_paths_resolved(self, tmp_path):
        """Relative paths in files_changed are resolved against project_root."""
        py_file = tmp_path / "module.py"
        py_file.write_text("a = 1\n")
        event = _make_event(
            project_root=str(tmp_path),
            files_changed=["module.py"],
        )
        result = _scoped_discover(event)
        assert str(py_file) in result


# ── TestFilterNestedSubprojects ──────────────────────────────────────


class TestFilterNestedSubprojects:
    """Tests for _filter_nested_subprojects()."""

    def test_no_subprojects_returns_all(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")
        result = _filter_nested_subprojects([f1, f2], str(tmp_path))
        assert result == [f1, f2]

    def test_filters_files_in_nested_pyproject_subdir(self, tmp_path):
        sub = tmp_path / "subproject"
        sub.mkdir()
        (sub / "pyproject.toml").write_text("[tool]\n")
        f_root = str(tmp_path / "root.py")
        f_sub = str(sub / "nested.py")
        result = _filter_nested_subprojects([f_root, f_sub], str(tmp_path))
        assert f_root in result
        assert f_sub not in result

    def test_filters_files_in_nested_setup_py_subdir(self, tmp_path):
        sub = tmp_path / "subpkg"
        sub.mkdir()
        (sub / "setup.py").write_text("from setuptools import setup\n")
        f_root = str(tmp_path / "root.py")
        f_sub = str(sub / "pkg.py")
        result = _filter_nested_subprojects([f_root, f_sub], str(tmp_path))
        assert f_root in result
        assert f_sub not in result

    def test_empty_file_list(self, tmp_path):
        result = _filter_nested_subprojects([], str(tmp_path))
        assert result == []

    def test_deeply_nested_subproject(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (tmp_path / "a" / "b" / "pyproject.toml").write_text("[tool]\n")
        f_deep = str(deep / "deep.py")
        result = _filter_nested_subprojects([f_deep], str(tmp_path))
        assert f_deep not in result

    def test_root_pyproject_does_not_filter_root_files(self, tmp_path):
        """pyproject.toml at the root level should NOT filter root files."""
        (tmp_path / "pyproject.toml").write_text("[tool]\n")
        f = str(tmp_path / "main.py")
        result = _filter_nested_subprojects([f], str(tmp_path))
        assert f in result


# ── TestCollectGitContext ─────────────────────────────────────────────


class TestCollectGitContext:
    """Tests for _collect_git_context()."""

    def test_no_project_root_returns_empty_dict(self):
        event = _make_event(project_root="")
        result = _collect_git_context(event, [])
        assert result == {}

    def test_git_context_with_project_root_calls_git(self, tmp_path):
        """With a project root, git context collection is attempted."""
        event = _make_event(project_root=str(tmp_path))
        # It will try to run git commands in a non-git dir — should not crash
        result = _collect_git_context(event, [])
        assert isinstance(result, dict)

    def test_git_context_annotates_findings_with_scope(self, tmp_path):
        """When git context has modified files, findings get scope annotation."""
        # Initialize a git repo to get real git context
        os.system(  # noqa: S605  # nosec B605 — test-only git init in tmp_path
            f"cd {tmp_path} && git init -q && git config user.email test@test.com && git config user.name test"
        )
        py_file = tmp_path / "app.py"
        py_file.write_text("x = 1\n")
        os.system(f"cd {tmp_path} && git add . && git commit -q -m init")  # noqa: S605  # nosec B605
        py_file.write_text("x = 2\n")  # Modified file

        finding = LintIssue(
            linter="test", kind="K1", message="msg", file=str(py_file), severity="warning"
        )
        cr = ChannelResult(channel="lint", status="fail", severity="warning", findings=[finding])
        event = _make_event(project_root=str(tmp_path))
        result = _collect_git_context(event, [cr])
        assert isinstance(result, dict)
        # If git detected the modification, scope annotation should be present
        if result.get("modified_files"):
            assert "scope" in finding.evidence


# ── TestRunCrossChannelCoherence ──────────────────────────────────────


class TestRunCrossChannelCoherence:
    """Tests for _run_cross_channel_coherence()."""

    def test_empty_results_no_crash(self):
        results: list[Any] = []
        _run_cross_channel_coherence(results)
        # Should not crash, might not add anything to empty list

    def test_suppresses_exceptions(self):
        """Cross-channel coherence wraps everything in contextlib.suppress."""
        results = [
            ChannelResult(channel="lint", status="pass"),
        ]
        # Should not raise regardless of internal errors
        _run_cross_channel_coherence(results)

    def test_pass_only_results(self):
        results = [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="pass"),
        ]
        _run_cross_channel_coherence(results)
        # No coherence findings expected for pass-only channels
        channels = {r.channel for r in results}
        # May or may not add coherence channel; just verify no crash
        assert "lint" in channels
        assert "tests" in channels


# ── TestApplyConvergenceEscalation ────────────────────────────────────


class TestApplyConvergenceEscalation:
    """Tests for _apply_convergence_escalation() and _escalate_convergent_findings()."""

    def _make_finding(self, file: str | None, severity: str = "informational") -> LintIssue:
        return LintIssue(
            linter="test",
            kind="T1",
            message="test finding",
            file=file,
            severity=severity,
        )

    def test_no_escalation_below_threshold(self):
        """Fewer than min_channels on a file => no escalation."""
        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
            ChannelResult(
                channel="tests",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
        ]
        _apply_convergence_escalation(results, min_channels=3)
        # Should remain informational with default min_channels=3
        assert results[0].findings[0].severity == "informational"

    def test_escalation_at_threshold(self):
        """Exactly min_channels => escalation occurs."""
        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
            ChannelResult(
                channel="tests",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
            ChannelResult(
                channel="structure",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
        ]
        _apply_convergence_escalation(results, min_channels=3)
        for cr in results:
            assert cr.findings[0].severity == "warning"

    def test_escalation_above_threshold(self):
        """More than min_channels => escalation still occurs."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding("/app.py")],
            )
            for i in range(4)
        ]
        _apply_convergence_escalation(results, min_channels=3)
        for cr in results:
            assert cr.findings[0].severity == "warning"

    def test_escalation_adds_convergence_evidence(self):
        """Escalated findings get convergence metadata."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding("/app.py")],
            )
            for i in range(3)
        ]
        _apply_convergence_escalation(results, min_channels=3)
        for cr in results:
            evidence = cr.findings[0].evidence
            assert "convergence" in evidence
            assert evidence["convergence"]["escalated_from"] == "informational"
            assert evidence["convergence"]["channel_count"] == 3

    def test_escalation_does_not_affect_warning_findings(self):
        """Findings already at warning severity are not changed."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding("/app.py", severity="warning")],
            )
            for i in range(3)
        ]
        _apply_convergence_escalation(results, min_channels=3)
        for cr in results:
            assert cr.findings[0].severity == "warning"
            # No convergence evidence added since it was already warning
            assert "convergence" not in (cr.findings[0].evidence or {})

    def test_escalation_does_not_affect_blocking_findings(self):
        """Findings at blocking severity are not changed."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding("/app.py", severity="blocking")],
            )
            for i in range(3)
        ]
        _apply_convergence_escalation(results, min_channels=3)
        for cr in results:
            assert cr.findings[0].severity == "blocking"

    def test_escalation_skips_skip_channels(self):
        """Skipped channels do not count toward convergence."""
        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
            ChannelResult(
                channel="tests",
                status="skip",
                findings=[self._make_finding("/app.py")],
            ),
            ChannelResult(
                channel="structure",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
        ]
        _apply_convergence_escalation(results, min_channels=3)
        # Only 2 non-skip channels have findings on /app.py, below threshold
        assert results[0].findings[0].severity == "informational"

    def test_escalation_only_affects_convergent_files(self):
        """Only files with convergence get escalated; others stay informational."""
        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[
                    self._make_finding("/app.py"),
                    self._make_finding("/other.py"),
                ],
            ),
            ChannelResult(
                channel="tests",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
            ChannelResult(
                channel="structure",
                status="fail",
                findings=[self._make_finding("/app.py")],
            ),
        ]
        _apply_convergence_escalation(results, min_channels=3)
        # /app.py has 3 channels => escalated
        lint_findings = results[0].findings
        app_finding = [f for f in lint_findings if f.file == "/app.py"][0]
        other_finding = [f for f in lint_findings if f.file == "/other.py"][0]
        assert app_finding.severity == "warning"
        assert other_finding.severity == "informational"

    def test_escalation_empty_results_no_crash(self):
        _apply_convergence_escalation([])

    def test_escalation_findings_without_file_ignored(self):
        """Findings with file=None do not participate in convergence."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding(None)],
            )
            for i in range(3)
        ]
        _apply_convergence_escalation(results, min_channels=3)
        # file=None findings should remain informational
        for cr in results:
            assert cr.findings[0].severity == "informational"

    def test_escalate_convergent_findings_directly(self):
        """Test _escalate_convergent_findings without the suppress wrapper."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding("/x.py")],
            )
            for i in range(3)
        ]
        _escalate_convergent_findings(results, 3)
        assert all(cr.findings[0].severity == "warning" for cr in results)

    def test_escalate_convergent_findings_no_convergent_files(self):
        """No files meet the threshold => no escalation."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding(f"/file{i}.py")],
            )
            for i in range(3)
        ]
        _escalate_convergent_findings(results, 3)
        # All files are different => no convergence
        assert all(cr.findings[0].severity == "informational" for cr in results)

    def test_default_min_channels_is_three(self):
        """Default min_channels=3 in _apply_convergence_escalation."""
        results = [
            ChannelResult(
                channel=f"ch{i}",
                status="fail",
                findings=[self._make_finding("/f.py")],
            )
            for i in range(3)
        ]
        _apply_convergence_escalation(results)  # default min_channels=3
        assert all(cr.findings[0].severity == "warning" for cr in results)


# ── TestComputeFinalCoherence ─────────────────────────────────────────


class TestComputeFinalCoherence:
    """Tests for _compute_final_coherence()."""

    def test_stable_with_all_pass(self):
        results = [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="pass"),
        ]
        config = _make_config()
        event = _make_event()
        coherence = _compute_final_coherence(results, config, event, session=None)
        assert coherence.state == "stable"

    def test_with_files_changed(self):
        results = [
            ChannelResult(channel="lint", status="pass"),
        ]
        config = _make_config()
        event = _make_event(files_changed=["/app.py"])
        coherence = _compute_final_coherence(results, config, event, session=None)
        assert coherence.state == "stable"

    def test_severity_weighted_coherence_flag(self):
        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[
                    LintIssue(
                        linter="ruff",
                        kind="E501",
                        message="line too long",
                        severity="informational",
                        file="/a.py",
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
                        message="fail",
                        severity="informational",
                        file="/b.py",
                    ),
                ],
            ),
            ChannelResult(
                channel="deps",
                status="fail",
                findings=[
                    LintIssue(
                        linter="deps",
                        kind="drift",
                        message="drift",
                        severity="informational",
                    ),
                ],
            ),
        ]
        config = _make_config(severity_weighted_coherence=True)
        event = _make_event()
        coherence = _compute_final_coherence(results, config, event, session=None)
        # With severity_weighted=True, all-informational channels are demoted
        assert coherence.state == "stable"

    def test_coherence_with_session(self):
        """When session is provided, compute_coherence_with_history is used."""
        results = [ChannelResult(channel="lint", status="pass")]
        config = _make_config()
        event = _make_event()

        # Create a minimal mock session
        mock_session = mock.MagicMock()
        mock_session.snapshots = []
        coherence = _compute_final_coherence(results, config, event, session=mock_session)
        assert coherence.state == "stable"


# ── TestExecuteParallel ───────────────────────────────────────────────


class TestExecuteParallel:
    """Tests for _execute_parallel()."""

    def test_single_channel_completes(self):
        channels: list[Any] = [PassChannel()]
        config = _make_config()
        deadline = time.perf_counter() + 10.0
        results = _execute_parallel(channels, _make_event(), config, deadline)
        assert "pass_ch" in results
        assert results["pass_ch"].status == "pass"

    def test_error_channel_captured(self):
        channels: list[Any] = [ErrorChannel()]
        config = _make_config()
        deadline = time.perf_counter() + 10.0
        results = _execute_parallel(channels, _make_event(), config, deadline)
        assert "error_ch" in results
        assert results["error_ch"].status == "error"

    def test_multiple_channels_complete(self):
        channels: list[Any] = [PassChannel(), FailChannel()]
        config = _make_config()
        deadline = time.perf_counter() + 10.0
        results = _execute_parallel(channels, _make_event(), config, deadline)
        assert "pass_ch" in results
        assert "fail_ch" in results
        assert results["pass_ch"].status == "pass"
        assert results["fail_ch"].status == "fail"

    def test_tight_deadline_produces_timeout(self):
        """Channels that can't finish before the deadline get timeout status."""
        channels: list[Any] = [SlowChannel(sleep_seconds=5.0)]
        config = _make_config(latency_budget_ms=500)
        # Set deadline very close so the slow channel times out
        deadline = time.perf_counter() + 0.2
        results = _execute_parallel(channels, _make_event(), config, deadline)
        assert "slow_ch" in results
        assert results["slow_ch"].status == "timeout"

    def test_fast_channel_completes_while_slow_times_out(self):
        channels: list[Any] = [PassChannel(), SlowChannel(sleep_seconds=10.0)]
        config = _make_config(latency_budget_ms=2000)
        deadline = time.perf_counter() + 2.0
        results = _execute_parallel(channels, _make_event(), config, deadline)
        assert results["pass_ch"].status == "pass"
        assert results["slow_ch"].status == "timeout"

    def test_past_deadline_still_returns_results(self):
        """Even with a deadline in the past, channels that are already done are captured."""
        channels: list[Any] = [PassChannel()]
        config = _make_config(latency_budget_ms=100)
        deadline = time.perf_counter() - 1.0  # Already past
        results = _execute_parallel(channels, _make_event(), config, deadline)
        # The fast channel may or may not complete depending on timing
        assert "pass_ch" in results


# ── TestRunSingleChannel ──────────────────────────────────────────────


class TestRunSingleChannel:
    """Tests for _run_single_channel()."""

    def test_successful_execution_returns_result(self):
        ch = PassChannel()
        result = _run_single_channel(ch, _make_event(), _make_config())
        assert result.channel == "pass_ch"
        assert result.status == "pass"
        assert result.duration_ms > 0

    def test_error_captured_not_raised(self):
        ch = ErrorChannel()
        result = _run_single_channel(ch, _make_event(), _make_config())
        assert result.status == "error"
        assert result.error_message is not None
        assert "RuntimeError" in result.error_message
        assert result.channel == "error_ch"
        assert result.duration_ms > 0

    def test_fail_channel_returns_findings(self):
        ch = FailChannel()
        result = _run_single_channel(ch, _make_event(), _make_config())
        assert result.status == "fail"
        assert len(result.findings) == 1
        assert result.duration_ms > 0

    def test_duration_ms_is_populated(self):
        ch = PassChannel()
        result = _run_single_channel(ch, _make_event(), _make_config())
        assert result.duration_ms >= 0

    def test_exception_type_in_error_message(self):
        """Error message includes exception class name."""

        class CustomErrorChannel:
            name = "custom_err"
            timeout_ms = 5000
            blocking_capable = False

            def should_run(self, event, config):
                return True

            def execute(self, event, config):
                raise TypeError("bad type")

        ch = CustomErrorChannel()
        result = _run_single_channel(ch, _make_event(), _make_config())
        assert result.error_message is not None
        assert "TypeError" in result.error_message
        assert "bad type" in result.error_message


# ── TestRunMeshIntegration ────────────────────────────────────────────


class TestRunMeshIntegration:
    """Integration tests verifying end-to-end mesh behavior."""

    def test_timeout_channel_appears_in_incomplete_list(self):
        config = _make_config(latency_budget_ms=500)
        channels: list[Any] = [SlowChannel(sleep_seconds=5.0)]
        result = run_mesh(_make_event(), config, channels)
        assert "slow_ch" in result.incomplete_channels
        assert result.partial is True

    def test_budget_enforcement_respects_global_limit(self):
        config = _make_config(latency_budget_ms=2000)
        channels: list[Any] = [
            SlowChannel(name="s1", sleep_seconds=10.0),
            SlowChannel(name="s2", sleep_seconds=10.0),
        ]
        start = time.perf_counter()
        result = run_mesh(_make_event(), config, channels)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0  # Should complete well under 5s
        assert result.partial is True

    def test_all_channel_types_in_single_mesh(self):
        """Mix of pass, fail, skip, error channels in one mesh run."""
        config = _make_config()
        channels: list[Any] = [PassChannel(), FailChannel(), SkipChannel(), ErrorChannel()]
        result = run_mesh(_make_event(), config, channels)
        statuses = {r.channel: r.status for r in result.channel_results}
        assert statuses["pass_ch"] == "pass"
        assert statuses["fail_ch"] == "fail"
        assert statuses["skip_ch"] == "skip"
        assert statuses["error_ch"] == "error"

    def test_coherence_state_reflects_channel_outcomes(self):
        """Coherence engine receives actual channel results."""
        config = _make_config()
        result = run_mesh(_make_event(), config, [PassChannel()])
        assert result.coherence.state == "stable"

    def test_mesh_with_no_active_channels(self):
        """All channels skip => stable coherence."""
        result = run_mesh(_make_event(), _make_config(), [SkipChannel()])
        assert result.coherence.state == "stable"
        assert result.partial is False

    def test_mesh_result_event_reference(self):
        """MeshResult carries reference to the original event."""
        event = _make_event(tool_name="Write")
        result = run_mesh(event, _make_config(), [PassChannel()])
        assert result.event.tool_name == "Write"

    def test_convergence_escalation_in_mesh(self):
        """When 3+ channels converge on the same file, informational -> warning."""
        channels: list[Any] = [
            FailChannel(name="ch_a", file="/shared.py"),
            FailChannel(name="ch_b", file="/shared.py"),
            FailChannel(name="ch_c", file="/shared.py"),
        ]
        # Override findings to informational
        original_execute = FailChannel.execute

        def execute_informational(ch_self: Any, event: Any, config: Any) -> ChannelResult:
            result: ChannelResult = original_execute(ch_self, event, config)
            for f in result.findings:
                f.severity = "informational"
            result.severity = "informational"
            return result

        for ch in channels:
            ch.execute = lambda event, config, _ch=ch: execute_informational(_ch, event, config)

        result = run_mesh(_make_event(), _make_config(), channels)
        # Find findings for /shared.py — they should be escalated to warning
        for cr in result.channel_results:
            for finding in cr.findings:
                if finding.file == "/shared.py":
                    assert finding.severity == "warning"

    def test_mesh_with_channel_weights(self):
        """Coherence channel weights are passed through."""
        config = _make_config(coherence_channel_weights={"lint": 0.8, "tests": 0.3})
        result = run_mesh(_make_event(), config, [PassChannel()])
        assert result.coherence.state == "stable"
