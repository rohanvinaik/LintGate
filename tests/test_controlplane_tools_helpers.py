"""Tests for extracted helper functions in mcp_tools/controlplane_tools.py.

Part 1: Channel selection, file collection, behavior injection, persistence,
         details extraction, and status helpers.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from mcp_tools.controlplane_tools import (
    _build_config_status,
    _collect_files_for_event,
    _extract_findings,
    _filter_channels,
    _get_session_status,
    _impl_controlplane_get_details,
    _impl_controlplane_run,
    _inject_behavior_priors,
    _persist_behavior_compass_delta,
    _persist_global_profile_delta,
    _persist_session_after_mesh,
    _select_channels,
)

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ── Fixtures ──────────────────────────────────────────────────────────


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


# ── _select_channels ──────────────────────────────────────────────────


class TestSelectChannels:
    def test_known_channels_returned(self):
        registry = {"lint": "L", "tests": "T"}
        active, requested, unknown = _select_channels("lint,tests", registry)
        assert active == ["L", "T"]
        assert unknown == []

    def test_unknown_channels_collected(self):
        registry = {"lint": "L"}
        active, requested, unknown = _select_channels("lint,bogus", registry)
        assert active == ["L"]
        assert unknown == ["bogus"]

    def test_all_unknown_raises(self):
        registry = {"lint": "L"}
        with pytest.raises(ValueError, match="No valid channels"):
            _select_channels("bogus,nope", registry)

    def test_none_channels_uses_all(self):
        registry = {
            "lint": "L",
            "tests": "T",
            "deps": "D",
            "git": "G",
            "behavior": "B",
            "structure": "S",
        }
        active, requested, unknown = _select_channels(None, registry)
        assert len(active) == 6


# ── _collect_files_for_event ──────────────────────────────────────────


class TestCollectFilesForEvent:
    def test_prefers_git_changed_files(self):
        helpers = _stub_helpers(
            _collect_python_files=lambda _r: ["/tmp/changed.py", "/tmp/other.py"]
        )
        fake_proc = mock.MagicMock()
        fake_proc.stdout = "changed.py\n"
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _collect_files_for_event("/tmp", None, None, helpers)
        assert result == ["/tmp/changed.py"]

    def test_falls_back_to_all_files(self):
        helpers = _stub_helpers(_collect_python_files=lambda _r: ["all1.py", "all2.py"])
        # If the import fails, suppress kicks in and fallback triggers
        result = _collect_files_for_event("/tmp", None, None, helpers)
        assert result == ["all1.py", "all2.py"]

    def test_caps_at_50(self):
        helpers = _stub_helpers(_collect_python_files=lambda _r: [f"f{i}.py" for i in range(100)])
        result = _collect_files_for_event("/tmp", None, None, helpers)
        assert len(result) == 50


# ── _inject_behavior_priors ──────────────────────────────────────────


class TestInjectBehaviorPriors:
    def _make_cp_config(self, global_memory=False, behavior_enabled=True):
        cfg = mock.MagicMock()
        cfg.global_memory_enabled = global_memory
        cfg.channel_enabled = lambda name: behavior_enabled if name == "behavior" else True
        cfg.global_memory_ttl_days = 90
        cfg.global_memory_alpha = 0.6
        cfg.global_memory_decay_horizon = 50
        return cfg

    def test_injects_compass_from_session(self):
        event = mock.MagicMock()
        event.raw_input = {}
        session = mock.MagicMock()
        session.behavior_compass = {"field": "value"}
        cfg = self._make_cp_config()
        _inject_behavior_priors(event, session, cfg)
        assert event.raw_input["behavior_compass"] == {"field": "value"}

    def test_global_priors_injected_when_enabled(self):
        event = mock.MagicMock()
        event.raw_input = {}
        session = mock.MagicMock()
        session.behavior_compass = {}
        cfg = self._make_cp_config(global_memory=True)

        fake_gp = mock.MagicMock()
        fake_gp.session_count = 10
        fake_gp.computed_bias_adjustments = {"sig": 0.1}

        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=fake_gp,
            ),
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE",
                3,
            ),
        ):
            _inject_behavior_priors(event, session, cfg)

        assert event.raw_input["behavior_global_priors"]["enabled"] is True

    def test_global_priors_skipped_when_disabled(self):
        event = mock.MagicMock()
        event.raw_input = {}
        cfg = self._make_cp_config(global_memory=False)
        _inject_behavior_priors(event, None, cfg)
        assert "behavior_global_priors" not in event.raw_input


# ── _persist_behavior_compass_delta ──────────────────────────────────


class TestPersistBehaviorCompassDelta:
    def test_returns_early_when_delta_not_dict(self):
        cr = mock.MagicMock()
        cr.metrics = {"behavior_compass_delta": "not-a-dict"}
        # Should not raise
        _persist_behavior_compass_delta(cr, mock.MagicMock(), mock.MagicMock())

    def test_persists_delta(self):
        cr = mock.MagicMock()
        cr.metrics = {"behavior_compass_delta": {"last_fired": "sig1"}}
        session = mock.MagicMock()
        cp_config = mock.MagicMock()
        cp_config.global_memory_enabled = False

        fake_compass = mock.MagicMock()
        with (
            mock.patch(
                "lintgate.controlplane.session_memory.load_behavior_compass",
                return_value=fake_compass,
            ) as load_bc,
            mock.patch(
                "lintgate.controlplane.session_memory.save_behavior_compass",
            ) as save_bc,
        ):
            _persist_behavior_compass_delta(cr, session, cp_config)
        load_bc.assert_called_once_with(session)
        save_bc.assert_called_once_with(session, fake_compass)


# ── _persist_global_profile_delta ────────────────────────────────────


class TestPersistGlobalProfileDelta:
    def test_noop_when_disabled(self):
        cr = mock.MagicMock()
        cp = mock.MagicMock()
        cp.global_memory_enabled = False
        _persist_global_profile_delta(cr, None, cp)  # Should not raise

    def test_noop_when_no_dict_delta(self):
        cr = mock.MagicMock()
        cr.metrics = {"global_profile_delta": 42}
        cp = mock.MagicMock()
        cp.global_memory_enabled = True
        _persist_global_profile_delta(cr, None, cp)

    def test_applies_and_saves(self):
        cr = mock.MagicMock()
        cr.metrics = {"global_profile_delta": {"key": "val"}}
        session = mock.MagicMock()
        session.session_id = "s1"
        cp = mock.MagicMock()
        cp.global_memory_enabled = True
        cp.global_memory_ttl_days = 90

        fake_gp = mock.MagicMock()
        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=fake_gp,
            ),
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.apply_session_delta",
            ) as apply_fn,
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.save_global_profile",
            ) as save_fn,
        ):
            _persist_global_profile_delta(cr, session, cp)
        apply_fn.assert_called_once_with(fake_gp, {"key": "val"}, session_id="s1")
        save_fn.assert_called_once_with(fake_gp)


# ── _persist_session_after_mesh ──────────────────────────────────────


class TestPersistSessionAfterMesh:
    def test_noop_when_session_none(self):
        _persist_session_after_mesh(None, mock.MagicMock(), {}, mock.MagicMock())

    def test_persists_behavior_channel(self):
        session = mock.MagicMock()
        behavior_cr = mock.MagicMock()
        behavior_cr.channel = "behavior"
        other_cr = mock.MagicMock()
        other_cr.channel = "lint"
        mesh_result = mock.MagicMock()
        mesh_result.channel_results = [other_cr, behavior_cr]
        cp_config = mock.MagicMock()
        cp_config.global_memory_enabled = False

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.record_mesh_run",
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
            mock.patch(
                "mcp_tools._controlplane_impl_run._persist_behavior_compass_delta",
            ) as persist_bc,
        ):
            _persist_session_after_mesh(session, mesh_result, {}, cp_config)
        persist_bc.assert_called_once_with(behavior_cr, session, cp_config)


# ── _filter_channels / _extract_findings ─────────────────────────────


class TestFilterChannelsAndExtract:
    def test_filter_channels_passes_all_when_no_filter(self):
        items = {"lint": "L", "tests": "T"}
        result = list(_filter_channels(items, None))
        assert len(result) == 2

    def test_filter_channels_restricts_by_name(self):
        items = {"lint": "L", "tests": "T"}
        result = list(_filter_channels(items, "lint"))
        assert result == [("lint", "L")]

    def test_extract_findings_filters_by_severity(self):
        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {"severity": "blocking", "msg": "a"},
                        {"severity": "warning", "msg": "b"},
                    ]
                }
            }
        }
        result = _extract_findings(details, None, "blocking", max_issues=10)
        assert result["total_matching"] == 1
        assert result["findings"][0]["severity"] == "blocking"

    def test_extract_findings_truncates(self):
        details = {
            "channels": {"lint": {"findings": [{"severity": "warning", "i": i} for i in range(5)]}}
        }
        result = _extract_findings(details, None, None, max_issues=2)
        assert result["total_matching"] == 5
        assert len(result["findings"]) == 2
        assert result["truncated"] == 3


# ── _impl_controlplane_get_details ───────────────────────────────────


class TestImplGetDetails:
    def test_raises_on_missing_run(self):
        with (
            mock.patch("lintgate.state.load_controlplane_run", return_value=None),
            pytest.raises(ValueError, match="No ControlPlane run found"),
        ):
            _impl_controlplane_get_details("missing", None, None, 10, None, _stub_helpers())

    def test_channel_filter_restricts_findings(self):
        """Branch: channel filter limits findings to a single channel."""
        details = {
            "duration_ms": 100,
            "coherence": {"state": "stable"},
            "channels": {
                "lint": {"findings": [{"severity": "warning", "msg": "a"}]},
                "tests": {"findings": [{"severity": "warning", "msg": "b"}]},
            },
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "r1", "lint", None, 10, ["findings"], _stub_helpers()
            )
        parsed = _load_tool_result(raw)
        assert parsed["total_matching"] == 1
        assert parsed["findings"][0]["channel"] == "lint"

    def test_severity_filter_with_no_matches(self):
        """Branch: severity filter that matches nothing → empty findings."""
        details = {
            "duration_ms": 50,
            "coherence": {},
            "channels": {
                "lint": {"findings": [{"severity": "warning", "msg": "w"}]},
            },
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "r1", None, "blocking", 10, ["findings"], _stub_helpers()
            )
        parsed = _load_tool_result(raw)
        assert parsed["total_matching"] == 0
        assert parsed["findings"] == []

    def test_evidence_section_empty_metrics(self):
        """Branch: evidence section requested but all channels have empty metrics."""
        details = {
            "duration_ms": 30,
            "coherence": {},
            "channels": {
                "lint": {"metrics": {}, "findings": []},
            },
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "r1", None, None, 10, ["evidence"], _stub_helpers()
            )
        parsed = _load_tool_result(raw)
        assert "evidence" not in parsed  # empty evidence not included

    def test_repairs_section(self):
        """Branch: repairs section extraction path."""
        details = {
            "duration_ms": 20,
            "coherence": {},
            "channels": {
                "lint": {
                    "findings": [],
                    "repairs": [{"action_id": "fix1", "kind": "command"}],
                },
            },
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details("r1", None, None, 10, ["repairs"], _stub_helpers())
        parsed = _load_tool_result(raw)
        assert len(parsed["repairs"]) == 1
        assert parsed["repairs"][0]["action_id"] == "fix1"

    def test_channel_details_section(self):
        """Branch: channel_details section extraction path."""
        details = {
            "duration_ms": 10,
            "coherence": {},
            "channels": {
                "lint": {
                    "status": "fail",
                    "severity": "warning",
                    "findings": [{"severity": "warning"}],
                    "duration_ms": 5,
                    "error": None,
                },
            },
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "r1", None, None, 10, ["channel_details"], _stub_helpers()
            )
        parsed = _load_tool_result(raw)
        assert "lint" in parsed["channel_details"]
        assert parsed["channel_details"]["lint"]["status"] == "fail"


# ── _build_config_status / _get_session_status ───────────────────────


class TestConfigAndSessionStatus:
    def test_build_config_status_includes_session(self):
        from lintgate.controlplane.types import ControlPlaneConfig

        cfg = ControlPlaneConfig(enabled=True, session_memory=True)
        helpers = _stub_helpers()
        with mock.patch(
            "mcp_tools._controlplane_impl_details._get_session_status",
            return_value={"session_id": "abc"},
        ):
            status = _build_config_status(cfg, "/tmp/test", helpers)
        assert status["session"] == {"session_id": "abc"}

    def test_get_session_status_returns_dict(self):
        fake_session = mock.MagicMock()
        fake_session.session_id = "s1"
        fake_session.snapshots = [1, 2]
        fake_session.coherence_trajectory = ["stable", "isolated", "stable"]
        fake_session.repair_outcomes = {"r1": "pending", "r2": "applied"}
        fake_session.proposed_constraints = [
            {"status": "proposed"},
            {"status": "accepted"},
        ]
        with mock.patch(
            "lintgate.controlplane.session_memory.load_session",
            return_value=fake_session,
        ):
            result = _get_session_status("/tmp")
        assert result["session_id"] == "s1"
        assert result["runs"] == 2
        assert result["pending_repairs"] == 1
        assert result["active_proposals"] == 1

    def test_get_session_status_returns_none_when_no_session(self):
        """Branch: load_session returns None (no active session) → returns None."""
        with mock.patch(
            "lintgate.controlplane.session_memory.load_session",
            return_value=None,
        ):
            assert _get_session_status("/tmp") is None

    def test_get_session_status_returns_none_on_failure(self):
        with mock.patch(
            "lintgate.controlplane.session_memory.load_session",
            side_effect=Exception("boom"),
        ):
            assert _get_session_status("/tmp") is None


# ── _impl_controlplane_run (unknown_channels path) ──────────────────


class TestImplControlplaneRun:
    def test_unknown_channels_key_set(self):
        """Line 316: compact['unknown_channels'] = unknown when unknown is truthy."""
        fake_mesh = mock.MagicMock()
        fake_mesh.channel_results = []
        fake_mesh.coherence.state = "stable"
        fake_compact = {"run_id": "r1"}

        with (
            mock.patch("lintgate.config.load_controlplane_config", return_value=None),
            mock.patch(
                "mcp_tools._controlplane_impl_run._build_channel_registry",
                return_value={"lint": mock.MagicMock()},
            ),
            mock.patch(
                "mcp_tools._controlplane_impl_run._select_channels",
                return_value=([mock.MagicMock()], ["lint", "bogus"], ["bogus"]),
            ),
            mock.patch(
                "mcp_tools._controlplane_impl_run._collect_files_for_event",
                return_value=["a.py"],
            ),
            mock.patch(
                "mcp_tools._controlplane_impl_run._build_supervision_event",
                return_value=mock.MagicMock(),
            ),
            mock.patch("mcp_tools._controlplane_impl_run._setup_session", return_value=None),
            mock.patch("mcp_tools._controlplane_impl_run._inject_behavior_priors"),
            mock.patch("lintgate.controlplane.runtime.run_mesh", return_value=fake_mesh),
            mock.patch("lintgate.controlplane.reporter.build_finding_index", return_value={}),
            mock.patch(
                "lintgate.controlplane.reporter.format_mesh_report_compact",
                return_value=dict(fake_compact),
            ),
            mock.patch("mcp_tools._controlplane_impl_run._persist_session_after_mesh"),
            mock.patch("mcp_tools._controlplane_impl_run._persist_runtime_state"),
            mock.patch("mcp_tools._controlplane_impl_run._save_run_details_for_drilldown"),
        ):
            raw = _impl_controlplane_run(
                "/tmp", "lint,bogus", "normal", None, None, _stub_helpers()
            )
        parsed = _load_tool_result(raw)
        assert parsed["unknown_channels"] == ["bogus"]
