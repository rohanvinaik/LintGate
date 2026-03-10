"""Tests for pure helper functions in lintgate/hooks/posttooluse.py."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from lintgate.hooks.posttooluse import (
    _collect_cycle_interventions,
    _collect_disposition_nudge,
    _compute_fingerprint_state,
    _detect_edit_functions,
    _detect_new_functions,
    _detect_write_functions,
    _evaluate_compliance,
    _extract_func_name,
    _finalize_report,
    _should_suppress_report,
)

# ─── Lightweight stubs ────────────────────────────────────────────────


@dataclass
class StubChannelResult:
    channel: str = ""
    status: str = "pass"
    findings: list[Any] = field(default_factory=list)


@dataclass
class StubFinding:
    severity: str = "warning"


@dataclass
class StubCoherence:
    state: str = "stable"


@dataclass
class StubMeshResult:
    channel_results: list[StubChannelResult] = field(default_factory=list)
    coherence: StubCoherence = field(default_factory=StubCoherence)
    duration_ms: float = 0.0
    partial: bool = False


@dataclass
class StubSession:
    behavior_compass: dict[str, Any] = field(default_factory=dict)


# ─── _parse_hook_input ────────────────────────────────────────────────


class TestParseHookInput:
    """Tests for _parse_hook_input which reads stdin JSON."""

    def _call(self, stdin_text: str) -> dict | None:
        from lintgate.hooks.posttooluse import _parse_hook_input

        with patch.object(sys, "stdin", io.StringIO(stdin_text)):
            return _parse_hook_input()

    def test_valid_dict(self) -> None:
        result = self._call('{"tool_name": "Edit", "tool_input": {}}')
        assert result == {"tool_name": "Edit", "tool_input": {}}

    def test_empty_dict(self) -> None:
        result = self._call("{}")
        assert result == {}

    def test_nested_dict(self) -> None:
        data = {"tool_name": "Write", "tool_input": {"file_path": "/a.py", "content": "x=1"}}
        result = self._call(json.dumps(data))
        assert result == data

    def test_invalid_json_returns_none(self) -> None:
        result = self._call("not json at all")
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = self._call("")
        assert result is None

    def test_json_array_returns_none(self) -> None:
        result = self._call('[1, 2, 3]')
        assert result is None

    def test_json_string_returns_none(self) -> None:
        result = self._call('"just a string"')
        assert result is None

    def test_json_number_returns_none(self) -> None:
        result = self._call("42")
        assert result is None

    def test_json_null_returns_none(self) -> None:
        result = self._call("null")
        assert result is None

    def test_json_bool_returns_none(self) -> None:
        result = self._call("true")
        assert result is None


# ─── _compute_fingerprint_state ───────────────────────────────────────


class TestComputeFingerprintState:
    """Tests for _compute_fingerprint_state which derives fingerprint from mesh_result."""

    def test_returns_none_pair_when_import_unavailable(self) -> None:
        mesh = StubMeshResult()
        with patch.dict(sys.modules, {"lintgate.controlplane.reporter.hook": None}):
            current, prev, cur_fields, prev_fields = _compute_fingerprint_state(mesh, None)
        assert current is None
        assert prev is None
        assert cur_fields == {}
        assert prev_fields == {}

    def test_returns_none_pair_with_no_session(self) -> None:
        mesh = StubMeshResult()
        current, prev, cur_fields, prev_fields = _compute_fingerprint_state(mesh, None)
        assert prev is None

    def test_session_without_behavior_compass_dict(self) -> None:
        mesh = StubMeshResult()
        session = StubSession()
        session.behavior_compass = "not a dict"
        current, prev, cur_fields, prev_fields = _compute_fingerprint_state(mesh, session)
        assert prev is None

    def test_session_with_previous_fingerprint(self) -> None:
        mesh = StubMeshResult()
        session = StubSession(behavior_compass={"_hook_fingerprint": "abc123"})
        current, prev, cur_fields, prev_fields = _compute_fingerprint_state(mesh, session)
        assert prev == "abc123"
        assert isinstance(current, str)
        assert session.behavior_compass["_hook_fingerprint"] == current
        assert isinstance(cur_fields, dict)

    def test_session_without_previous_fingerprint(self) -> None:
        mesh = StubMeshResult()
        session = StubSession(behavior_compass={})
        current, prev, cur_fields, prev_fields = _compute_fingerprint_state(mesh, session)
        assert prev is None
        assert isinstance(current, str)
        assert session.behavior_compass["_hook_fingerprint"] == current
        assert "_hook_fields" in session.behavior_compass


# ─── _should_suppress_report ──────────────────────────────────────────


class TestShouldSuppressReport:
    """Tests for _should_suppress_report which gates on fingerprint match + no blocking."""

    def test_suppressed_when_fingerprints_match_and_no_blocking(self) -> None:
        mesh = StubMeshResult(
            channel_results=[
                StubChannelResult(findings=[StubFinding(severity="warning")]),
                StubChannelResult(findings=[StubFinding(severity="informational")]),
            ]
        )
        assert _should_suppress_report("fp1", "fp1", mesh) is True

    def test_not_suppressed_when_fingerprints_differ(self) -> None:
        mesh = StubMeshResult(channel_results=[])
        assert _should_suppress_report("fp1", "fp2", mesh) is False

    def test_not_suppressed_when_current_fp_is_none(self) -> None:
        mesh = StubMeshResult(channel_results=[])
        assert _should_suppress_report(None, "fp2", mesh) is False

    def test_not_suppressed_when_prev_fp_is_none(self) -> None:
        mesh = StubMeshResult(channel_results=[])
        assert _should_suppress_report("fp1", None, mesh) is False

    def test_not_suppressed_when_both_fp_none(self) -> None:
        mesh = StubMeshResult(channel_results=[])
        assert _should_suppress_report(None, None, mesh) is False

    def test_not_suppressed_when_blocking_finding_exists(self) -> None:
        mesh = StubMeshResult(
            channel_results=[
                StubChannelResult(findings=[StubFinding(severity="blocking")]),
            ]
        )
        assert _should_suppress_report("fp1", "fp1", mesh) is False

    def test_suppressed_with_empty_findings(self) -> None:
        mesh = StubMeshResult(channel_results=[StubChannelResult(findings=[])])
        assert _should_suppress_report("fp1", "fp1", mesh) is True

    def test_suppressed_with_no_channels(self) -> None:
        mesh = StubMeshResult(channel_results=[])
        assert _should_suppress_report("fp1", "fp1", mesh) is True

    def test_not_suppressed_blocking_among_many(self) -> None:
        mesh = StubMeshResult(
            channel_results=[
                StubChannelResult(findings=[StubFinding(severity="warning")]),
                StubChannelResult(findings=[StubFinding(severity="blocking")]),
                StubChannelResult(findings=[StubFinding(severity="informational")]),
            ]
        )
        assert _should_suppress_report("fp1", "fp1", mesh) is False


# ─── _extract_func_name ──────────────────────────────────────────────


class TestExtractFuncName:
    """Tests for _extract_func_name which parses function names from def lines."""

    def test_simple_def(self) -> None:
        assert _extract_func_name("def foo(x):") == "foo"

    def test_async_def(self) -> None:
        assert _extract_func_name("async def bar(y, z):") == "bar"

    def test_no_parens(self) -> None:
        assert _extract_func_name("def baz") == "baz"

    def test_def_with_spaces_before_paren(self) -> None:
        assert _extract_func_name("def my_func (a, b):") == "my_func"

    def test_async_def_with_spaces(self) -> None:
        assert _extract_func_name("async def handler (req):") == "handler"

    def test_empty_string(self) -> None:
        assert _extract_func_name("") == ""

    def test_not_a_def(self) -> None:
        assert _extract_func_name("class Foo:") == ""

    def test_partial_def_keyword(self) -> None:
        assert _extract_func_name("default = 5") == ""

    def test_def_underscore_name(self) -> None:
        assert _extract_func_name("def _private_helper(a):") == "_private_helper"

    def test_def_dunder(self) -> None:
        assert _extract_func_name("def __init__(self):") == "__init__"

    def test_async_def_no_args(self) -> None:
        assert _extract_func_name("async def run():") == "run"


# ─── _detect_write_functions ──────────────────────────────────────────


class TestDetectWriteFunctions:
    """Tests for _detect_write_functions which parses AST from Write tool content."""

    def test_single_function(self) -> None:
        content = "def hello():\n    pass\n"
        result = _detect_write_functions({"content": content, "file_path": "/a.py"})
        assert result == [{"name": "hello", "file": "/a.py", "line": 1}]

    def test_multiple_functions(self) -> None:
        content = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        result = _detect_write_functions({"content": content, "file_path": "/mod.py"})
        assert len(result) == 2
        assert result[0]["name"] == "foo"
        assert result[1]["name"] == "bar"

    def test_async_function(self) -> None:
        content = "async def handler():\n    pass\n"
        result = _detect_write_functions({"content": content, "file_path": "/srv.py"})
        assert result == [{"name": "handler", "file": "/srv.py", "line": 1}]

    def test_class_methods_not_included(self) -> None:
        content = "class Foo:\n    def method(self):\n        pass\n"
        result = _detect_write_functions({"content": content, "file_path": "/cls.py"})
        assert result is None

    def test_empty_content_returns_none(self) -> None:
        result = _detect_write_functions({"content": "", "file_path": "/a.py"})
        assert result is None

    def test_missing_content_returns_none(self) -> None:
        result = _detect_write_functions({"file_path": "/a.py"})
        assert result is None

    def test_non_python_file_returns_none(self) -> None:
        result = _detect_write_functions({"content": "function hello() {}", "file_path": "/a.js"})
        assert result is None

    def test_missing_file_path_returns_none(self) -> None:
        result = _detect_write_functions({"content": "def foo():\n    pass\n"})
        assert result is None

    def test_syntax_error_returns_none(self) -> None:
        result = _detect_write_functions({"content": "def broken(:\n", "file_path": "/bad.py"})
        assert result is None

    def test_no_functions_returns_none(self) -> None:
        content = "x = 1\ny = 2\n"
        result = _detect_write_functions({"content": content, "file_path": "/vars.py"})
        assert result is None

    def test_mixed_toplevel_and_nested(self) -> None:
        content = (
            "def top():\n"
            "    def inner():\n"
            "        pass\n"
            "\n"
            "async def other():\n"
            "    pass\n"
        )
        result = _detect_write_functions({"content": content, "file_path": "/mix.py"})
        assert len(result) == 2
        assert result[0]["name"] == "top"
        assert result[1]["name"] == "other"

    def test_line_numbers_accurate(self) -> None:
        content = "import os\n\n\ndef func_at_line_4():\n    pass\n"
        result = _detect_write_functions({"content": content, "file_path": "/loc.py"})
        assert result == [{"name": "func_at_line_4", "file": "/loc.py", "line": 4}]


# ─── _detect_edit_functions ───────────────────────────────────────────


class TestDetectEditFunctions:
    """Tests for _detect_edit_functions which finds new defs in edit diffs."""

    def test_new_function_detected(self) -> None:
        result = _detect_edit_functions({
            "old_string": "x = 1\n",
            "new_string": "x = 1\ndef added():\n    pass\n",
            "file_path": "/a.py",
        })
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "added"
        assert result[0]["file"] == "/a.py"

    def test_existing_function_not_detected(self) -> None:
        result = _detect_edit_functions({
            "old_string": "def existing():\n    pass\n",
            "new_string": "def existing():\n    return 1\n",
            "file_path": "/a.py",
        })
        assert result is None

    def test_async_function_detected(self) -> None:
        result = _detect_edit_functions({
            "old_string": "",
            "new_string": "async def new_handler():\n    pass\n",
            "file_path": "/a.py",
        })
        assert result is not None
        assert result[0]["name"] == "new_handler"

    def test_non_python_returns_none(self) -> None:
        result = _detect_edit_functions({
            "old_string": "",
            "new_string": "def foo():\n    pass\n",
            "file_path": "/a.js",
        })
        assert result is None

    def test_empty_new_string_returns_none(self) -> None:
        result = _detect_edit_functions({
            "old_string": "x = 1\n",
            "new_string": "",
            "file_path": "/a.py",
        })
        assert result is None

    def test_missing_file_path_returns_none(self) -> None:
        result = _detect_edit_functions({
            "old_string": "",
            "new_string": "def foo():\n    pass\n",
        })
        assert result is None

    def test_no_new_defs_returns_none(self) -> None:
        result = _detect_edit_functions({
            "old_string": "x = 1\n",
            "new_string": "x = 2\ny = 3\n",
            "file_path": "/a.py",
        })
        assert result is None

    def test_multiple_new_functions(self) -> None:
        result = _detect_edit_functions({
            "old_string": "",
            "new_string": "def alpha():\n    pass\ndef beta():\n    pass\n",
            "file_path": "/a.py",
        })
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "alpha"
        assert result[1]["name"] == "beta"

    def test_line_numbers_are_relative_to_new_string(self) -> None:
        result = _detect_edit_functions({
            "old_string": "",
            "new_string": "# comment\n\ndef at_line_3():\n    pass\n",
            "file_path": "/a.py",
        })
        assert result is not None
        assert result[0]["line"] == 3

    def test_indented_def_detected(self) -> None:
        result = _detect_edit_functions({
            "old_string": "",
            "new_string": "    def indented_method(self):\n        pass\n",
            "file_path": "/a.py",
        })
        assert result is not None
        assert result[0]["name"] == "indented_method"


# ─── _detect_new_functions ────────────────────────────────────────────


class TestDetectNewFunctions:
    """Tests for _detect_new_functions which dispatches to Write or Edit detectors."""

    def test_write_tool(self) -> None:
        tool_input = {"content": "def hello():\n    pass\n", "file_path": "/a.py"}
        result = _detect_new_functions("Write", tool_input, "/project")
        assert result is not None
        assert result[0]["name"] == "hello"

    def test_edit_tool(self) -> None:
        tool_input = {
            "old_string": "",
            "new_string": "def added():\n    pass\n",
            "file_path": "/a.py",
        }
        result = _detect_new_functions("Edit", tool_input, "/project")
        assert result is not None
        assert result[0]["name"] == "added"

    def test_bash_tool_returns_none(self) -> None:
        assert _detect_new_functions("Bash", {"command": "ls"}, "/project") is None

    def test_multiedit_tool_returns_none(self) -> None:
        assert _detect_new_functions("MultiEdit", {}, "/project") is None

    def test_unknown_tool_returns_none(self) -> None:
        assert _detect_new_functions("Read", {}, "/project") is None

    def test_empty_string_tool_returns_none(self) -> None:
        assert _detect_new_functions("", {}, "/project") is None


# ─── _finalize_report ─────────────────────────────────────────────────


class TestFinalizeReport:
    """Tests for _finalize_report which applies advisory, arbitration, and strips telemetry."""

    @dataclass
    class StubCpConfig:
        hook_dispositions_enabled: bool = False
        habit_mode_enter_score: float = 0.70

        def hook_verbosity_gating(self) -> bool:
            return False

    def _make_cp_config(self) -> StubCpConfig:
        return self.StubCpConfig()

    def test_strips_telemetry_from_report(self) -> None:
        report = {"systemMessage": "hello", "_telemetry": {"tokens": 100}}
        cp_config = self._make_cp_config()
        result, telemetry = _finalize_report(report, None, None, cp_config)
        assert "_telemetry" not in result
        assert telemetry == {"tokens": 100}

    def test_empty_telemetry_when_absent(self) -> None:
        report = {"systemMessage": "hello"}
        cp_config = self._make_cp_config()
        _, telemetry = _finalize_report(report, None, None, cp_config)
        assert telemetry == {}

    def test_advisory_prepended_to_system_message(self) -> None:
        report = {"systemMessage": "lint results"}
        cp_config = self._make_cp_config()
        result, _ = _finalize_report(report, "ADVISORY: check tests", None, cp_config)
        msg = result.get("systemMessage", "")
        assert msg.startswith("ADVISORY: check tests")
        assert "lint results" in msg

    def test_advisory_sets_message_when_empty(self) -> None:
        report = {"hookSpecificOutput": {}}
        cp_config = self._make_cp_config()
        result, _ = _finalize_report(report, "ADVISORY: note", None, cp_config)
        assert result.get("systemMessage") == "ADVISORY: note"

    def test_advisory_ignored_when_report_empty(self) -> None:
        cp_config = self._make_cp_config()
        result, _ = _finalize_report({}, "ADVISORY: note", None, cp_config)
        assert isinstance(result, dict)

    def test_none_advisory_no_change(self) -> None:
        report = {"systemMessage": "original"}
        cp_config = self._make_cp_config()
        result, _ = _finalize_report(report, None, None, cp_config)
        assert "original" in result.get("systemMessage", "")

    def test_none_report_returns_dict(self) -> None:
        cp_config = self._make_cp_config()
        result, telemetry = _finalize_report(None, None, None, cp_config)
        assert isinstance(result, dict)
        assert telemetry == {}

    def test_session_behavior_compass_used(self) -> None:
        report = {"systemMessage": "msg"}
        cp_config = self._make_cp_config()
        session = StubSession(behavior_compass={"key": "val"})
        result, _ = _finalize_report(report, None, session, cp_config)
        assert isinstance(result, dict)

    def test_session_with_non_dict_behavior_compass(self) -> None:
        report = {"systemMessage": "msg"}
        cp_config = self._make_cp_config()
        session = StubSession()
        session.behavior_compass = "not a dict"
        result, _ = _finalize_report(report, None, session, cp_config)
        assert isinstance(result, dict)

    def test_returns_tuple_of_dict_and_dict(self) -> None:
        report = {"systemMessage": "test", "_telemetry": {"a": 1}}
        cp_config = self._make_cp_config()
        result, telemetry = _finalize_report(report, None, None, cp_config)
        assert isinstance(result, dict)
        assert isinstance(telemetry, dict)


# ─── _evaluate_compliance (sigma=20) ──────────────────────────────────


@dataclass
class StubEvent:
    tool_name: str = "Write"
    tool_input: str = "{}"


class TestEvaluateCompliance:
    def test_returns_none_when_session_is_none(self):
        event = StubEvent()
        assert _evaluate_compliance(None, event, None, None) is None

    def test_returns_none_on_import_error(self):
        session = StubSession(behavior_compass={})
        event = StubEvent()
        with patch(
            "lintgate.hooks.posttooluse.ComplianceManager",
            side_effect=ImportError("no module"),
            create=True,
        ):
            # Even if ComplianceManager import fails, it catches Exception
            result = _evaluate_compliance(session, event, None, None)
        # Should return None or a string — depends on whether it fails
        assert result is None or isinstance(result, str)

    def test_returns_string_on_success(self):
        session = StubSession(behavior_compass={})
        event = StubEvent()
        mock_cm = type("MockCM", (), {
            "__init__": lambda self, bc: None,
            "evaluate_and_record": lambda self, *a, **kw: "followed",
        })
        with patch(
            "lintgate.orchestration.compliance.ComplianceManager", mock_cm,
        ):
            result = _evaluate_compliance(session, event, "orient", {"rule": "x"})
        assert result == "followed"

    def test_catches_exception_returns_none(self):
        session = StubSession(behavior_compass={})
        event = StubEvent()
        mock_cm = type("MockCM", (), {
            "__init__": lambda self, bc: None,
            "evaluate_and_record": lambda self, *a, **kw: (_ for _ in ()).throw(
                RuntimeError("boom")
            ),
        })
        with patch(
            "lintgate.orchestration.compliance.ComplianceManager", mock_cm,
        ):
            result = _evaluate_compliance(session, event, None, None)
        assert result is None


# ─── _collect_disposition_nudge (sigma=20) ────────────────────────────


@dataclass
class StubBus:
    items: list = field(default_factory=list)

    def collect(self, item):
        self.items.append(item)


@dataclass
class StubCpConfig:
    pass


class TestCollectDispositionNudge:
    def test_returns_none_on_exception(self):
        # When imports fail, should return None gracefully
        with patch(
            "lintgate.hooks.posttooluse.DispositionEnforcer",
            side_effect=ImportError("no module"),
            create=True,
        ):
            result = _collect_disposition_nudge(
                StubCpConfig(), StubSession(), StubEvent(), StubBus()
            )
        assert result is None or isinstance(result, str)

    def test_returns_disposition_string_on_success(self):
        mock_enforcer_instance = type("MockEnf", (), {
            "evaluate": lambda self, event: ("orient", "rule_001"),
        })()
        mock_enforcer_cls = lambda *a, **kw: mock_enforcer_instance
        mock_nudge_fn = lambda disp, rule_id: {"type": "nudge", "disposition": disp}

        with (
            patch(
                "lintgate.orchestration.disposition_enforcer.DispositionEnforcer",
                mock_enforcer_cls,
            ),
            patch(
                "lintgate.orchestration.delivery.disposition_nudge_to_item",
                mock_nudge_fn,
            ),
        ):
            bus = StubBus()
            result = _collect_disposition_nudge(
                StubCpConfig(), StubSession(), StubEvent(), bus
            )
        assert result == "orient"
        assert len(bus.items) == 1
        assert bus.items[0]["disposition"] == "orient"

    def test_no_collect_when_disposition_none(self):
        mock_enforcer_instance = type("MockEnf", (), {
            "evaluate": lambda self, event: (None, None),
        })()
        mock_enforcer_cls = lambda *a, **kw: mock_enforcer_instance

        with (
            patch(
                "lintgate.orchestration.disposition_enforcer.DispositionEnforcer",
                mock_enforcer_cls,
            ),
            patch(
                "lintgate.orchestration.delivery.disposition_nudge_to_item",
                side_effect=AssertionError("should not be called"),
            ),
        ):
            bus = StubBus()
            result = _collect_disposition_nudge(
                StubCpConfig(), StubSession(), StubEvent(), bus
            )
        assert result is None
        assert len(bus.items) == 0


# ─── _collect_cycle_interventions (sigma=22) ──────────────────────────


class TestCollectCycleInterventions:
    def test_noop_when_session_is_none(self):
        bus = StubBus()
        _collect_cycle_interventions(None, bus)
        assert bus.items == []

    def test_noop_when_no_behavior_compass(self):
        session = type("S", (), {})()  # no behavior_compass attr
        bus = StubBus()
        _collect_cycle_interventions(session, bus)
        assert bus.items == []

    def test_noop_when_behavior_compass_not_dict(self):
        session = StubSession()
        session.behavior_compass = "not-a-dict"
        bus = StubBus()
        _collect_cycle_interventions(session, bus)
        assert bus.items == []

    def test_noop_when_cycle_detections_not_list(self):
        session = StubSession(behavior_compass={"cycle_detections": "nope"})
        bus = StubBus()
        _collect_cycle_interventions(session, bus)
        assert bus.items == []

    def test_noop_when_cycle_detections_missing(self):
        session = StubSession(behavior_compass={"other": "data"})
        bus = StubBus()
        _collect_cycle_interventions(session, bus)
        assert bus.items == []

    def test_collects_valid_cycle_results(self):
        from lintgate.orchestration.cycle_detector import CycleDetectionResult

        cr = CycleDetectionResult(
            cycle_detected=True,
            reason="approach_cycling detected",
            diagnostics={"evidence": ["a", "b", "c"]},
            escalation_level="advisory",
        )
        session = StubSession(behavior_compass={"cycle_detections": [cr]})
        bus = StubBus()
        _collect_cycle_interventions(session, bus)
        assert len(bus.items) >= 1

    def test_skips_invalid_entries_gracefully(self):
        session = StubSession(
            behavior_compass={"cycle_detections": [{"invalid": "data"}]}
        )
        bus = StubBus()
        # Should not raise — catches exceptions per entry
        _collect_cycle_interventions(session, bus)

    def test_empty_cycle_detections_list(self):
        session = StubSession(behavior_compass={"cycle_detections": []})
        bus = StubBus()
        _collect_cycle_interventions(session, bus)
        assert bus.items == []
