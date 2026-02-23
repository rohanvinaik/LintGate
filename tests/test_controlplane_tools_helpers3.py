"""Tests for extracted helper functions in mcp_tools/controlplane_tools.py.

Part 3: Session setup, runtime state persistence, drilldown save,
         evidence extraction, and controlplane_status implementation.
"""

from __future__ import annotations

import json
from unittest import mock

from mcp_tools.controlplane_tools import (
    _extract_evidence,
    _impl_controlplane_status,
    _persist_runtime_state,
    _save_run_details_for_drilldown,
    _setup_session,
)


def _stub_helpers(**overrides):
    """Build a helpers dict with sensible defaults for testing."""
    defaults = {
        "_validate_project_root": lambda p: p or "/tmp/test",
        "_collect_python_files": lambda _root: ["a.py", "b.py"],
        "_build_cp_full_details": lambda _mr, _fi: {},
        "_build_onboarding_status": lambda _root: {"config_state": "config_enabled"},
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


# ── _setup_session ───────────────────────────────────────────────────


class TestSetupSession:
    def test_returns_none_when_disabled(self):
        cfg = mock.MagicMock()
        cfg.session_memory = False
        assert _setup_session("/tmp", cfg) is None

    def test_returns_session_when_enabled(self):
        cfg = mock.MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4.0
        fake = mock.MagicMock()
        with mock.patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=fake,
        ):
            assert _setup_session("/tmp", cfg) is fake

    def test_returns_none_on_exception(self):
        cfg = mock.MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4.0
        with mock.patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            side_effect=RuntimeError("boom"),
        ):
            assert _setup_session("/tmp", cfg) is None


# ── _persist_runtime_state ───────────────────────────────────────────


class TestPersistRuntimeState:
    def test_counts_blocking_warning_and_symbol_blockers(self):
        blocking_f = mock.MagicMock(severity="blocking", kind="F821")
        warning_f = mock.MagicMock(severity="warning", kind="W001")
        symbol_f = mock.MagicMock(severity="blocking", kind="symbol_uncovered")

        cr_lint = mock.MagicMock(channel="lint", findings=[blocking_f, warning_f])
        cr_tests = mock.MagicMock(channel="tests", findings=[symbol_f])

        mesh = mock.MagicMock()
        mesh.channel_results = [cr_lint, cr_tests]
        mesh.coherence.state = "isolated"

        fake_rt = mock.MagicMock()
        with (
            mock.patch(
                "lintgate.runtime_state.build_runtime_state",
                return_value=fake_rt,
            ),
            mock.patch(
                "lintgate.runtime_state.save_runtime_state",
            ) as save_fn,
        ):
            _persist_runtime_state(mesh, "/proj", None)

        save_fn.assert_called_once_with("/proj", fake_rt)
        assert fake_rt.symbol_coverage_blockers == 1


# ── _save_run_details_for_drilldown ──────────────────────────────────


class TestSaveRunDetailsForDrilldown:
    def test_saves_via_state(self):
        helpers = _stub_helpers(
            _build_cp_full_details=lambda _mr, _fi: {"full": True},
        )
        compact = {"run_id": "cp_x"}
        with mock.patch("lintgate.state.save_controlplane_run") as save_fn:
            _save_run_details_for_drilldown(mock.MagicMock(), {}, compact, helpers)
        save_fn.assert_called_once_with("cp_x", {"full": True})


# ── _extract_evidence ────────────────────────────────────────────────


class TestExtractEvidence:
    def test_returns_channels_with_metrics(self):
        details = {
            "channels": {
                "lint": {"metrics": {"complexity": 5}},
                "tests": {"metrics": {}},
            }
        }
        result = _extract_evidence(details, None)
        assert "lint" in result
        assert "tests" not in result

    def test_filters_by_channel(self):
        details = {
            "channels": {
                "lint": {"metrics": {"x": 1}},
                "tests": {"metrics": {"y": 2}},
            }
        }
        result = _extract_evidence(details, "tests")
        assert "tests" in result
        assert "lint" not in result


# ── _impl_controlplane_status ────────────────────────────────────────


class TestImplControlplaneStatus:
    def test_with_config(self):
        from lintgate.controlplane.types import ControlPlaneConfig

        cfg = ControlPlaneConfig(enabled=True)
        with mock.patch(
            "lintgate.config.load_controlplane_config",
            return_value=cfg,
        ):
            raw = _impl_controlplane_status("/tmp", _stub_helpers())
        parsed = json.loads(raw)
        assert parsed["controlplane_enabled"] is True
        assert "available_channels" in parsed

    def test_without_config(self):
        helpers = _stub_helpers(
            _build_onboarding_status=lambda _: {"config_state": "missing"},
        )
        with mock.patch(
            "lintgate.config.load_controlplane_config",
            return_value=None,
        ):
            raw = _impl_controlplane_status("/tmp", helpers)
        parsed = json.loads(raw)
        assert parsed["controlplane_enabled"] is False
        assert "onboarding" in parsed

    def test_uses_cwd_when_path_none(self):
        from lintgate.controlplane.types import ControlPlaneConfig

        cfg = ControlPlaneConfig(enabled=True)
        with (
            mock.patch(
                "lintgate.config.load_controlplane_config",
                return_value=cfg,
            ),
            mock.patch("os.getcwd", return_value="/cwd"),
        ):
            raw = _impl_controlplane_status(None, _stub_helpers())
        parsed = json.loads(raw)
        assert parsed["project"] == "/cwd"
