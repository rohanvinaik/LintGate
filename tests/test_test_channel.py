"""Phase 3B: Test channel tests.

Verifies:
- Channel protocol conformance
- should_run logic
- Impact detection (editing foo.py finds test_foo.py)
- Pytest output parsing
- Test runner wrapper (mock pytest execution)
- Channel execute integration

Skeleton, fallback, filter, and edge case tests are in test_test_channel_edge.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lintgate.channels.test_channel import (
    TestChannel,
    _parse_pytest_output,
    find_impacted_tests,
    run_tests,
)
from lintgate.controlplane.channel import Channel
from lintgate.controlplane.types import (
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification

# ── Protocol conformance ─────────────────────────────────────────────────


def test_test_channel_conforms_to_protocol() -> None:
    ch = TestChannel()
    assert isinstance(ch, Channel)


def test_test_channel_has_correct_name() -> None:
    assert TestChannel.name == "tests"


def test_test_channel_is_not_blocking() -> None:
    assert TestChannel.blocking_capable is False


# ── should_run tests ─────────────────────────────────────────────────────


def test_should_run_on_logic_change() -> None:
    classification = ChangeClassification(
        files_changed=["/tmp/app.py"],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=classification,
    )
    assert TestChannel().should_run(event, ControlPlaneConfig()) is True


def test_should_run_on_structural_change() -> None:
    classification = ChangeClassification(
        files_changed=["/tmp/app.py"],
        change_kind="structural",
        risk_level="structural",
    )
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=classification,
    )
    assert TestChannel().should_run(event, ControlPlaneConfig()) is True


def test_should_not_run_on_config_change() -> None:
    classification = ChangeClassification(
        files_changed=["/tmp/config.yaml"],
        change_kind="config",
        risk_level="cosmetic",
    )
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=classification,
    )
    assert TestChannel().should_run(event, ControlPlaneConfig()) is False


def test_should_not_run_without_classification() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=None,
    )
    assert TestChannel().should_run(event, ControlPlaneConfig()) is False


def test_should_run_on_mcp_without_classification() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        change_classification=None,
    )
    assert TestChannel().should_run(event, ControlPlaneConfig()) is True


# ── Impact detection tests ───────────────────────────────────────────────


def test_find_test_in_tests_directory(tmp_path: Path) -> None:
    """Editing app.py finds tests/test_app.py."""
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.write_text("def test_x(): pass")

    result = find_impacted_tests([str(tmp_path / "app.py")], str(tmp_path))
    assert str(test_file) in result


def test_find_test_in_same_directory(tmp_path: Path) -> None:
    """Editing app.py finds test_app.py in same dir."""
    (tmp_path / "app.py").write_text("x = 1")
    test_file = tmp_path / "test_app.py"
    test_file.write_text("def test_x(): pass")

    result = find_impacted_tests([str(tmp_path / "app.py")], str(tmp_path))
    assert str(test_file) in result


def test_changed_test_file_included(tmp_path: Path) -> None:
    """Editing a test file directly includes it."""
    test_file = tmp_path / "test_app.py"
    test_file.write_text("def test_x(): pass")

    result = find_impacted_tests([str(test_file)], str(tmp_path))
    assert str(test_file) in result


def test_no_test_file_found(tmp_path: Path) -> None:
    """No test file exists → empty list."""
    (tmp_path / "orphan.py").write_text("x = 1")

    result = find_impacted_tests([str(tmp_path / "orphan.py")], str(tmp_path))
    assert result == []


def test_non_python_files_skipped(tmp_path: Path) -> None:
    """Non-.py files are skipped."""
    (tmp_path / "readme.md").write_text("# Hello")

    result = find_impacted_tests([str(tmp_path / "readme.md")], str(tmp_path))
    assert result == []


def test_no_duplicate_test_files(tmp_path: Path) -> None:
    """Same test file found by multiple patterns shouldn't duplicate."""
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.write_text("def test_x(): pass")

    # Pass the source file twice
    result = find_impacted_tests(
        [str(tmp_path / "app.py"), str(tmp_path / "app.py")],
        str(tmp_path),
    )
    assert result.count(str(test_file)) == 1


# ── Pytest output parsing tests ──────────────────────────────────────────


def test_parse_all_passed() -> None:
    stdout = "5 passed in 0.03s"
    result = _parse_pytest_output(stdout, "", 0)
    assert result.passed == 5
    assert result.failed == 0
    assert result.failures == []


def test_parse_mixed_results() -> None:
    stdout = "3 passed, 2 failed, 1 error in 0.10s"
    result = _parse_pytest_output(stdout, "", 1)
    assert result.passed == 3
    assert result.failed == 2
    assert result.errors == 1


def test_parse_failure_lines() -> None:
    stdout = (
        "FAILED tests/test_app.py::test_something - AssertionError: expected 1\n"
        "FAILED tests/test_app.py::test_other - ValueError: bad input\n"
        "2 failed in 0.05s"
    )
    result = _parse_pytest_output(stdout, "", 1)
    assert len(result.failures) == 2
    assert result.failures[0].test_name == "test_something"
    assert result.failures[0].file == "tests/test_app.py"
    assert "AssertionError" in result.failures[0].message
    assert result.failures[1].test_name == "test_other"


def test_parse_no_output() -> None:
    result = _parse_pytest_output("", "", 0)
    assert result.passed == 0
    assert result.failed == 0
    assert result.failures == []


# ── Test runner (mocked) ────────────────────────────────────────────────


@patch("lintgate.channels.test_channel.subprocess.run")
def test_run_tests_success(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout="3 passed in 0.03s",
        stderr="",
        returncode=0,
    )
    result = run_tests(["test_app.py"], "/tmp/project")
    assert result.passed == 3
    assert result.failed == 0


@patch("lintgate.channels.test_channel.subprocess.run")
def test_run_tests_failure(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout="FAILED tests/test_app.py::test_x - AssertionError\n1 failed in 0.01s",
        stderr="",
        returncode=1,
    )
    result = run_tests(["tests/test_app.py"], "/tmp/project")
    assert result.failed == 1
    assert len(result.failures) == 1


@patch("lintgate.channels.test_channel.subprocess.run")
def test_run_tests_timeout(mock_run: MagicMock) -> None:
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=10)
    result = run_tests(["test_app.py"], "/tmp/project")
    assert result.timed_out is True


@patch("lintgate.channels.test_channel.subprocess.run")
def test_run_tests_preserves_coverage_json_for_symbol_gate(mock_run: MagicMock) -> None:
    """coverage.json path returned by run_tests should exist for downstream gating."""
    import re

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        cov_xml = None
        cov_json = None
        for arg in cmd:
            if arg.startswith("--cov-report=xml:"):
                cov_xml = arg.split(":", 1)[1]
            if arg.startswith("--cov-report=json:"):
                cov_json = arg.split(":", 1)[1]
        assert cov_xml is not None
        assert cov_json is not None
        Path(cov_xml).write_text(
            '<coverage line-rate="0.80"></coverage>', encoding="utf-8"
        )
        Path(cov_json).write_text(
            '{"files":{"app.py":{"executed_lines":[1],"missing_lines":[],"excluded_lines":[],"missing_branches":[]}}}',
            encoding="utf-8",
        )
        return MagicMock(stdout="1 passed in 0.01s", stderr="", returncode=0)

    mock_run.side_effect = _fake_run
    result = run_tests(
        ["test_app.py"],
        "/tmp/project",
        measure_coverage=True,
        source_packages=["lintgate"],
    )
    assert result.coverage_pct == 80.0
    assert result.coverage_json_path is not None
    assert Path(result.coverage_json_path).is_file()
    assert result.coverage_json_ephemeral is True
    # The returned path should contain coverage JSON (not an empty temp file).
    content = Path(result.coverage_json_path).read_text(encoding="utf-8")
    assert re.search(r'"files"\s*:', content)


def test_run_tests_empty_list() -> None:
    result = run_tests([], "/tmp/project")
    assert result.passed == 0


# ── Channel execute (integration with mocked tests) ─────────────────────


def test_channel_detects_missing_test(tmp_path: Path) -> None:
    """Source file without test file → informational finding."""
    src = tmp_path / "module.py"
    src.write_text("def hello(): return 'hi'")

    classification = ChangeClassification(
        files_changed=[str(src)],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(src)],
        change_classification=classification,
    )

    channel = TestChannel()
    result = channel.execute(event, ControlPlaneConfig())

    missing = [f for f in result.findings if f.kind == "missing_test"]
    assert len(missing) == 1
    assert "module.py" in missing[0].message


def test_channel_proposes_skeleton_repair(tmp_path: Path) -> None:
    """Missing test should propose a skeleton repair action."""
    src = tmp_path / "module.py"
    src.write_text("def process(data: str) -> str:\n    return data.strip()")

    classification = ChangeClassification(
        files_changed=[str(src)],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(src)],
        change_classification=classification,
    )

    channel = TestChannel()
    result = channel.execute(event, ControlPlaneConfig())

    assert len(result.repairs) >= 1
    assert result.repairs[0].kind == "create_test_skeleton"
    assert result.repairs[0].channel == "tests"


@patch("lintgate.channels.test_channel.subprocess.run")
def test_channel_reports_test_failures(mock_run: MagicMock, tmp_path: Path) -> None:
    """Channel runs impacted tests and reports failures."""
    src = tmp_path / "app.py"
    src.write_text("x = 1")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.write_text("def test_x(): assert False")

    mock_run.return_value = MagicMock(
        stdout="FAILED tests/test_app.py::test_x - AssertionError\n1 failed in 0.01s",
        stderr="",
        returncode=1,
    )

    classification = ChangeClassification(
        files_changed=[str(src)],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(src)],
        change_classification=classification,
    )

    channel = TestChannel()
    result = channel.execute(event, ControlPlaneConfig())

    test_failures = [f for f in result.findings if f.kind == "test_failure"]
    assert len(test_failures) >= 1
    assert len(test_failures) >= 1
    assert result.status == "fail"
    assert result.severity == "warning"  # Advisory


# ── Targeted Coverage Fixes ──────────────────────────────────────────


# NOTE: _get_test_file_candidates, _is_recent_enough, and find_covered_symbols
# were refactored into internal logic of find_impacted_tests and
# symbol_coverage.py respectively. Targeted tests for those are
# now part of the class-level or orchestrator tests.

# ── Phase 2 & 4: Branch-aware texts and Gate Context ─────────────────────


class MockSymbol:
    def __init__(self, name="foo", file="foo.py", start_line=1):
        self.name = name
        self.file = file
        self.start_line = start_line
        self.symbol_key = f"{file}::{name}"


class MockSymbolResult:
    def __init__(
        self,
        name="foo",
        covered=False,
        missing_lines=None,
        missing_branches=None,
        total_lines=10,
        executed_lines=5,
    ):
        self.symbol = MockSymbol(name=name)
        self.covered = covered
        self.missing_lines = missing_lines or []
        self.missing_branches = missing_branches or []
        self.total_lines_in_span = total_lines
        self.executed_lines_in_span = executed_lines


class MockGateResult:
    def __init__(self, symbol_results=None):
        self.symbol_results = symbol_results or []
        self.unresolved_required = []
        self.waivers_expired = []
        self.waivers_applied = []
        self.skipped_reasons = []


def test_suggestion_line_only():
    from lintgate.channels.test_channel import _build_symbol_suggestions

    sr = MockSymbolResult(missing_lines=[1, 2, 3])
    suggs = _build_symbol_suggestions(sr)
    assert any("execute lines 1, 2, 3 in foo" in s for s in suggs)
    assert any("waiver with reason" in s for s in suggs)


def test_suggestion_branch_only():
    from lintgate.channels.test_channel import _build_symbol_suggestions

    sr = MockSymbolResult(missing_branches=[(1, 2), (2, 3)])
    suggs = _build_symbol_suggestions(sr)
    assert any("execute branches 1->2, 2->3 in foo" in s for s in suggs)


def test_suggestion_mixed():
    from lintgate.channels.test_channel import _build_symbol_suggestions

    sr = MockSymbolResult(missing_lines=[1], missing_branches=[(1, 2)])
    suggs = _build_symbol_suggestions(sr)
    assert any("execute lines 1 and branches 1->2 in foo" in s for s in suggs)


def test_suggestion_no_evidence():
    from lintgate.channels.test_channel import _build_symbol_suggestions

    sr = MockSymbolResult()
    suggs = _build_symbol_suggestions(sr)
    assert any("missing tests for foo" in s for s in suggs)


def test_message_text_branch_only():
    from lintgate.channels.test_channel import _emit_symbol_findings

    findings = []
    gate = MockGateResult([MockSymbolResult(missing_branches=[(1, 2), (2, 3)])])
    _emit_symbol_findings(gate, findings)
    assert "(missing 2 branches)" in findings[0].message
    assert "missing lines" not in findings[0].message


def test_partial_run_confidence_reduced():
    from lintgate.channels.test_channel import _emit_symbol_findings

    findings = []
    gate = MockGateResult([MockSymbolResult(missing_lines=[1])])
    _emit_symbol_findings(gate, findings, is_partial_run=True, coverage_ok=True)
    assert findings[0].severity == "warning"
    assert getattr(findings[0], "confidence", 1.0) == 0.6
    assert (
        "downgraded: partial test run with healthy line coverage" in findings[0].message
    )


def test_full_run_confidence_1():
    from lintgate.channels.test_channel import _emit_symbol_findings

    findings = []
    gate = MockGateResult([MockSymbolResult(missing_lines=[1])])
    _emit_symbol_findings(gate, findings, is_partial_run=False, coverage_ok=True)
    assert findings[0].severity == "blocking"
    assert getattr(findings[0], "confidence", 1.0) == 1.0
    assert "downgraded" not in findings[0].message


def test_gate_context_in_evidence():
    from lintgate.channels.test_channel import _emit_symbol_findings

    findings = []
    gate = MockGateResult([MockSymbolResult(missing_lines=[1])])
    _emit_symbol_findings(
        gate,
        findings,
        is_partial_run=True,
        coverage_ok=True,
        targets_mode="impacted",
        coverage_pct=85.0,
    )
    ev = findings[0].evidence
    assert ev.get("is_partial_run") is True
    assert ev.get("coverage_ok") is True
    assert ev.get("targets_mode") == "impacted"
    assert ev.get("coverage_pct") == 85.0


def test_reconciliation_metadata_in_channel_result():
    from lintgate.channels.test_channel import TestChannelContext, _build_channel_result

    cfg = {"measure": True, "threshold": 80.0}
    gate = MockGateResult([MockSymbolResult(covered=False)])
    ctx = TestChannelContext(
        channel_name="tests",
        start=0.0,
        findings=[],
        repairs=[],
        impacted_tests=[],
        test_result=None,
        cov_cfg=cfg,
        gate_result=gate,
        targets_mode="fallback",
        coverage_pct=90.0,
        is_partial_run=False,
        coverage_ok=True,
    )
    res = _build_channel_result(ctx)
    sym_ctx = res.metrics.get("symbol_gate_context")
    assert sym_ctx is not None
    assert sym_ctx["targets_mode"] == "fallback"
    assert sym_ctx["is_partial_run"] is False
    assert sym_ctx["coverage_ok"] is True
    assert sym_ctx["coverage_pct"] == 90.0
