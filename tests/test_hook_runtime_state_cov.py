"""Coverage tests for lintgate/hook_runtime_state.py."""

from __future__ import annotations

from unittest import mock

from lintgate.hook_runtime_state import (
    derive_focus_intent,
    mesh_finding_counts,
    mesh_symbol_blocker_count,
    refresh_runtime_state_lightweight,
    refresh_runtime_state_with_session,
    runtime_targets,
    write_dynamic_runtime_files,
)


class TestDeriveFocusIntent:
    def test_edit_tool(self):
        result = derive_focus_intent("Edit", {"file_path": "/foo/bar.py"})
        assert result == "Edit bar.py"

    def test_write_tool(self):
        result = derive_focus_intent("Write", {"file_path": "/foo/baz.py"})
        assert result == "Edit baz.py"

    def test_bash_tool(self):
        result = derive_focus_intent("Bash", {"command": "pytest tests/"})
        assert "pytest" in result

    def test_bash_str_input(self):
        result = derive_focus_intent("Bash", "ls -la")
        assert "ls -la" in result

    def test_other_tool(self):
        result = derive_focus_intent("Read", {"file_path": "/foo.py"})
        assert result == "Use Read"

    def test_empty_tool_name(self):
        result = derive_focus_intent("", {})
        assert result == ""

    def test_edit_empty_path(self):
        result = derive_focus_intent("Edit", {"file_path": ""})
        assert result == "Use Edit"


class TestMeshFindingCounts:
    def test_counts(self):
        f1 = mock.MagicMock(severity="blocking")
        f2 = mock.MagicMock(severity="warning")
        f3 = mock.MagicMock(severity="blocking")
        f4 = mock.MagicMock(severity="info")

        cr1 = mock.MagicMock()
        cr1.findings = [f1, f2]
        cr2 = mock.MagicMock()
        cr2.findings = [f3, f4]

        mesh = mock.MagicMock()
        mesh.channel_results = [cr1, cr2]

        blocking, warnings = mesh_finding_counts(mesh)
        assert blocking == 2
        assert warnings == 1

    def test_empty(self):
        mesh = mock.MagicMock()
        mesh.channel_results = []
        blocking, warnings = mesh_finding_counts(mesh)
        assert blocking == 0
        assert warnings == 0


class TestMeshSymbolBlockerCount:
    def test_counts_symbol_uncovered(self):
        f1 = mock.MagicMock(severity="blocking", kind="symbol_uncovered")
        f2 = mock.MagicMock(severity="blocking", kind="other")
        f3 = mock.MagicMock(severity="warning", kind="symbol_uncovered")

        cr = mock.MagicMock()
        cr.channel = "tests"
        cr.findings = [f1, f2, f3]

        mesh = mock.MagicMock()
        mesh.channel_results = [cr]

        assert mesh_symbol_blocker_count(mesh) == 1  # only blocking + symbol_uncovered

    def test_non_test_channel_ignored(self):
        f1 = mock.MagicMock(severity="blocking", kind="symbol_uncovered")
        cr = mock.MagicMock()
        cr.channel = "lint"
        cr.findings = [f1]

        mesh = mock.MagicMock()
        mesh.channel_results = [cr]

        assert mesh_symbol_blocker_count(mesh) == 0

    def test_unresolved_required(self):
        f1 = mock.MagicMock(severity="blocking", kind="unresolved_required_symbol")
        cr = mock.MagicMock()
        cr.channel = "tests"
        cr.findings = [f1]

        mesh = mock.MagicMock()
        mesh.channel_results = [cr]

        assert mesh_symbol_blocker_count(mesh) == 1


class TestRuntimeTargets:
    def test_detect_runtime_hosts(self):
        registry = mock.MagicMock()
        registry.detect_runtime_hosts.return_value = ["claude_code"]
        result = runtime_targets(registry, "/tmp")
        assert result == ["claude_code"]

    def test_fallback_to_detect_host(self):
        registry = mock.MagicMock()
        registry.detect_runtime_hosts.return_value = []
        registry.detect_host.return_value = "cursor"
        result = runtime_targets(registry, "/tmp")
        assert result == ["cursor"]

    def test_no_targets(self):
        registry = mock.MagicMock()
        registry.detect_runtime_hosts.return_value = []
        registry.detect_host.return_value = None
        result = runtime_targets(registry, "/tmp")
        assert result == []


class TestWriteDynamicRuntimeFiles:
    def test_no_targets(self):
        with mock.patch(
            "lintgate.renderers.build_default_registry"
        ) as mock_reg:
            reg = mock_reg.return_value
            reg.detect_runtime_hosts.return_value = []
            reg.detect_host.return_value = None
            success, status = write_dynamic_runtime_files("/tmp", mock.MagicMock())
        assert not success
        assert status == "no_targets"

    def test_success(self):
        with mock.patch(
            "lintgate.renderers.build_default_registry"
        ) as mock_reg, mock.patch(
            "lintgate.renderers.dynamic.write_dynamic_file", return_value=True
        ):
            reg = mock_reg.return_value
            reg.detect_runtime_hosts.return_value = ["claude_code"]
            reg.render_dynamic_for_targets.return_value = {".claude/rules/runtime.md": "content"}
            success, status = write_dynamic_runtime_files("/tmp", mock.MagicMock())
        assert success
        assert status == "success"

    def test_write_failed(self):
        with mock.patch(
            "lintgate.renderers.build_default_registry"
        ) as mock_reg, mock.patch(
            "lintgate.renderers.dynamic.write_dynamic_file", return_value=False
        ):
            reg = mock_reg.return_value
            reg.detect_runtime_hosts.return_value = ["claude_code"]
            reg.render_dynamic_for_targets.return_value = {".claude/rules/runtime.md": "content"}
            success, status = write_dynamic_runtime_files("/tmp", mock.MagicMock())
        assert not success
        assert status == "write_failed"

    def test_exception(self):
        with mock.patch(
            "lintgate.renderers.build_default_registry",
            side_effect=ImportError("no module"),
        ):
            success, status = write_dynamic_runtime_files("/tmp", mock.MagicMock())
        assert not success
        assert status == "error"


class TestRefreshRuntimeStateWithSession:
    def test_with_mesh_result(self):
        session = mock.MagicMock()
        session.behavior_compass = {}

        save_result = mock.MagicMock()
        save_result.written = True
        save_result.lock_acquired = True
        save_result.contention_count = 0

        runtime = mock.MagicMock()
        runtime.generation = 1
        runtime.mode = "normal"

        scheduler = mock.MagicMock()
        scheduler.to_dict.return_value = {"gen": 1}

        mesh = mock.MagicMock()
        mesh.coherence.state = "stable"
        mesh.channel_results = []

        with mock.patch(
            "lintgate.runtime_state.build_runtime_state", return_value=runtime
        ), mock.patch(
            "lintgate.runtime_state.save_runtime_state_with_meta", return_value=save_result
        ), mock.patch(
            "lintgate.write_scheduler.WriteScheduler.from_dict", return_value=scheduler
        ), mock.patch(
            "lintgate.write_scheduler.mark_dirty"
        ), mock.patch(
            "lintgate.write_scheduler.record_tool_call"
        ), mock.patch(
            "lintgate.write_scheduler.should_write", return_value=False
        ), mock.patch(
            "lintgate.hook_runtime_state.log_runtime_state_write_metric"
        ), mock.patch(
            "lintgate.hook_runtime_state.mesh_finding_counts", return_value=(0, 0)
        ), mock.patch(
            "lintgate.hook_runtime_state.mesh_symbol_blocker_count", return_value=0
        ):
            refresh_runtime_state_with_session(
                "/tmp", session, mesh_result=mesh, tool_name="Read", trigger="tool_call"
            )


class TestRefreshRuntimeStateLightweight:
    def test_basic_call(self):
        save_result = mock.MagicMock()
        save_result.written = True
        save_result.lock_acquired = True
        save_result.contention_count = 0

        runtime = mock.MagicMock()
        runtime.generation = 1
        runtime.mode = "normal"
        runtime.coherence_state = ""

        scheduler = mock.MagicMock()
        scheduler.to_dict.return_value = {"gen": 1}

        with mock.patch(
            "lintgate.runtime_state.build_runtime_state", return_value=runtime
        ), mock.patch(
            "lintgate.runtime_state.save_runtime_state_with_meta", return_value=save_result
        ), mock.patch(
            "lintgate.write_scheduler.WriteScheduler.from_dict", return_value=scheduler
        ), mock.patch(
            "lintgate.write_scheduler.mark_dirty"
        ), mock.patch(
            "lintgate.write_scheduler.record_tool_call"
        ), mock.patch(
            "lintgate.write_scheduler.should_write", return_value=False
        ), mock.patch(
            "lintgate.hook_runtime_state.log_runtime_state_write_metric"
        ):
            result = refresh_runtime_state_lightweight(
                "/tmp", tool_name="Read", trigger="tool_call", scheduler_dict={"gen": 0}
            )
        assert result == {"gen": 1}

    def test_with_mesh_result(self):
        save_result = mock.MagicMock()
        save_result.written = True
        save_result.lock_acquired = True
        save_result.contention_count = 0

        runtime = mock.MagicMock()
        runtime.generation = 1
        runtime.mode = "normal"
        runtime.coherence_state = ""

        scheduler = mock.MagicMock()
        scheduler.to_dict.return_value = {}

        mesh = mock.MagicMock()
        mesh.coherence.state = "coupled"
        mesh.channel_results = []

        with mock.patch(
            "lintgate.runtime_state.build_runtime_state", return_value=runtime
        ), mock.patch(
            "lintgate.runtime_state.save_runtime_state_with_meta", return_value=save_result
        ), mock.patch(
            "lintgate.write_scheduler.WriteScheduler.from_dict", return_value=scheduler
        ), mock.patch(
            "lintgate.write_scheduler.mark_dirty"
        ), mock.patch(
            "lintgate.write_scheduler.should_write", return_value=True
        ), mock.patch(
            "lintgate.hook_runtime_state.write_dynamic_runtime_files",
            return_value=(True, "success"),
        ), mock.patch(
            "lintgate.write_scheduler.record_write"
        ), mock.patch(
            "lintgate.hook_runtime_state.log_runtime_state_write_metric"
        ), mock.patch(
            "lintgate.hook_runtime_state.mesh_finding_counts", return_value=(1, 2)
        ), mock.patch(
            "lintgate.hook_runtime_state.mesh_symbol_blocker_count", return_value=0
        ):
            result = refresh_runtime_state_lightweight(
                "/tmp", mesh_result=mesh, tool_name="Bash", tool_input={"command": "pytest"}
            )
        assert isinstance(result, dict)
