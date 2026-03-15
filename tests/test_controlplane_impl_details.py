"""Tests for mcp_tools/_controlplane_impl_details.py — 20 functions, 2-4 tests each."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_tools._controlplane_impl_details import (
    _DEFAULT_SECTIONS,
    _EFFORT_DEFAULTS,
    _SECTION_POPULATORS,
    _SEV_WEIGHT,
    _build_config_status,
    _build_details_next_actions,
    _extract_channel_details,
    _extract_evidence,
    _extract_findings,
    _extract_proven_resolutions_from_details,
    _extract_repairs,
    _filter_channels,
    _finding_effort,
    _finding_roi,
    _get_session_status,
    _impl_controlplane_get_details,
    _impl_controlplane_status,
    _populate_channel_details,
    _populate_coherence,
    _populate_evidence,
    _populate_findings,
    _populate_findings_section,
    _populate_next_actions,
    _populate_proven_resolutions,
    _populate_repairs,
)

# Lazy imports for finding_domain tests (may not exist in older versions)
try:
    from mcp_tools._controlplane_impl_details import _finding_domain, _summarize_findings
except ImportError:
    _finding_domain = None  # type: ignore[assignment]
    _summarize_findings = None  # type: ignore[assignment]

# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_details(**overrides):
    """Build a minimal ControlPlane run details dict."""
    base = {
        "duration_ms": 100,
        "channels": {
            "lint": {
                "status": "pass",
                "severity": "informational",
                "findings": [
                    {
                        "kind": "ruff",
                        "linter": "ruff",
                        "severity": "warning",
                        "message": "unused import",
                        "confidence": 0.9,
                        "fixable": True,
                    },
                ],
                "repairs": [{"action": "remove-import", "safe": True}],
                "metrics": {"lines_checked": 500},
                "duration_ms": 42,
                "error": None,
            },
            "tests": {
                "status": "fail",
                "severity": "blocking",
                "findings": [
                    {
                        "kind": "test_fail",
                        "linter": "mypy",
                        "severity": "blocking",
                        "message": "type error",
                        "confidence": 1.0,
                        "fixable": False,
                        "estimated_effort_minutes": 20,
                    },
                ],
                "repairs": [],
                "metrics": {},
                "duration_ms": 200,
                "error": None,
            },
        },
        "coherence": {"score": 0.85},
    }
    base.update(overrides)
    return base


# ── _filter_channels ──────────────────────────────────────────────────────


class TestFilterChannels:
    def test_no_filter_yields_all(self):
        channels = {"lint": {"a": 1}, "tests": {"b": 2}}
        result = list(_filter_channels(channels, None))
        assert result == [("lint", {"a": 1}), ("tests", {"b": 2})]

    def test_filter_by_name(self):
        channels = {"lint": {"a": 1}, "tests": {"b": 2}}
        result = list(_filter_channels(channels, "tests"))
        assert result == [("tests", {"b": 2})]

    def test_filter_no_match_yields_empty(self):
        channels = {"lint": {"a": 1}}
        result = list(_filter_channels(channels, "nonexistent"))
        assert result == []

    def test_empty_dict(self):
        result = list(_filter_channels({}, None))
        assert result == []


# ── _finding_effort ───────────────────────────────────────────────────────


class TestFindingEffort:
    def test_explicit_effort(self):
        assert _finding_effort({"estimated_effort_minutes": 7.0}) == 7.0

    def test_linter_default(self):
        assert _finding_effort({"linter": "ruff"}) == 2.0

    def test_unknown_linter_fallback(self):
        assert _finding_effort({"linter": "unknown_tool"}) == 10.0

    def test_fixable_caps_at_two(self):
        assert _finding_effort({"estimated_effort_minutes": 30, "fixable": True}) == 2.0

    def test_fixable_already_below_two(self):
        assert _finding_effort({"estimated_effort_minutes": 1.5, "fixable": True}) == 1.5

    def test_no_linter_no_effort(self):
        # No linter key and no estimated_effort_minutes -> fallback 10.0
        assert _finding_effort({}) == 10.0


# ── _finding_roi ──────────────────────────────────────────────────────────


class TestFindingRoi:
    def test_basic_roi(self):
        f = {"severity": "blocking", "confidence": 1.0, "estimated_effort_minutes": 3}
        # weight=3.0, conf=1.0, effort=3 -> 3*1/3=1.0
        assert _finding_roi(f) == 1.0

    def test_roi_with_fixable(self):
        f = {
            "severity": "warning",
            "confidence": 1.0,
            "estimated_effort_minutes": 30,
            "fixable": True,
        }
        # weight=2.0, conf=1.0, effort=min(30,2)=2.0 -> 2/2=1.0
        assert _finding_roi(f) == 1.0

    def test_roi_with_zero_confidence(self):
        f = {"severity": "blocking", "confidence": 0.0, "estimated_effort_minutes": 5}
        assert _finding_roi(f) == 0.0

    def test_roi_default_severity(self):
        # unknown severity -> weight 1.0, default effort 10.0
        f = {"severity": "unknown", "confidence": 1.0}
        # weight=1.0, effort=10.0 -> 1/10=0.1
        assert _finding_roi(f) == 0.1


# ── _extract_findings ─────────────────────────────────────────────────────


class TestExtractFindings:
    def test_no_filter(self):
        details = _make_details()
        result = _extract_findings(details, None, None, 100)
        assert result["total_matching"] == 2
        assert len(result["findings"]) == 2
        assert result["finding_summary"]["domains"]["code"]["total"] == 2
        assert result["finding_summary"]["domains"]["environment"]["total"] == 0
        assert "truncated" not in result

    def test_channel_filter(self):
        details = _make_details()
        result = _extract_findings(details, "lint", None, 100)
        assert result["total_matching"] == 1
        assert result["findings"][0]["channel"] == "lint"

    def test_severity_filter(self):
        details = _make_details()
        result = _extract_findings(details, None, "blocking", 100)
        assert result["total_matching"] == 1
        assert result["findings"][0]["severity"] == "blocking"

    def test_max_issues_truncation(self):
        details = _make_details()
        result = _extract_findings(details, None, None, 1)
        assert result["total_matching"] == 2
        assert len(result["findings"]) == 1
        assert result["truncated"] == 1

    def test_top_n_sorting(self):
        details = _make_details()
        result = _extract_findings(details, None, None, 100, top_n=1)
        assert result["sorted_by"] == "roi"
        assert len(result["findings"]) == 1
        # Each finding should have an roi key
        assert "roi" in result["findings"][0]

    def test_time_budget(self):
        details = _make_details()
        # The lint finding is fixable -> effort 2.0; tests finding effort 20
        # With budget 3.0, only the lint finding fits
        result = _extract_findings(details, None, None, 100, time_budget_minutes=3.0)
        assert result["sorted_by"] == "roi"
        assert result["time_budget_minutes"] == 3.0
        assert "budget_used_minutes" in result
        # Only fixable finding fits in budget of 3
        assert all(f["fixable"] for f in result["findings"] if "fixable" in f)

    def test_finding_domain_filter(self):
        details = _make_details(
            channels={
                "lint": {
                    "findings": [
                        {
                            "kind": "ruff",
                            "linter": "ruff",
                            "severity": "warning",
                            "message": "unused import",
                        }
                    ]
                },
                "deps": {
                    "findings": [
                        {
                            "kind": "dependency_vulnerability",
                            "linter": "pip_audit",
                            "severity": "warning",
                            "message": "CVE-123",
                        }
                    ]
                },
            }
        )
        result = _extract_findings(details, None, None, 100, finding_domain="code")
        assert result["total_matching"] == 1
        assert result["findings"][0]["channel"] == "lint"
        assert result["finding_summary"]["domains"]["code"]["total"] == 1
        assert result["finding_summary"]["domains"]["environment"]["total"] == 0


# ── _build_details_next_actions ───────────────────────────────────────────


class TestBuildDetailsNextActions:
    def test_no_repairs_no_findings(self):
        actions = _build_details_next_actions("run-1", {})
        assert actions == []

    def test_safe_repairs_action(self):
        output = {"repairs": [{"safe": True}, {"safe": False}]}
        actions = _build_details_next_actions("run-1", output)
        repair_action = [a for a in actions if a["tool"] == "controlplane_apply_repairs"]
        assert len(repair_action) == 1
        assert repair_action[0]["priority"] == 1
        assert repair_action[0]["args"]["run_id"] == "run-1"
        assert "1 safe" in repair_action[0]["reason"]

    def test_findings_action(self):
        output = {"findings": [{"kind": "ruff"}]}
        actions = _build_details_next_actions("run-1", output)
        edit_action = [a for a in actions if a["priority"] == 2]
        assert len(edit_action) == 1

    def test_truncated_action(self):
        output = {"findings": [{"kind": "a"}], "truncated": 5}
        actions = _build_details_next_actions("run-1", output)
        trunc_action = [a for a in actions if a["priority"] == 3]
        assert len(trunc_action) == 1
        assert trunc_action[0]["args"]["max_issues"] == 6  # 1 finding + 5 truncated
        assert "5" in trunc_action[0]["reason"]

    def test_code_only_follow_up_when_environment_present(self):
        output = {
            "finding_summary": {
                "domains": {
                    "code": {"total": 2, "blocking": 0, "warning": 2, "informational": 0},
                    "environment": {
                        "total": 5,
                        "blocking": 0,
                        "warning": 5,
                        "informational": 0,
                    },
                }
            }
        }
        actions = _build_details_next_actions("run-1", output)
        code_only = [
            a
            for a in actions
            if a["tool"] == "controlplane_get_details"
            and a.get("args", {}).get("finding_domain") == "code"
        ]
        assert len(code_only) == 1
        assert code_only[0]["args"]["run_id"] == "run-1"


# ── _extract_channel_details ──────────────────────────────────────────────


class TestExtractChannelDetails:
    def test_all_channels(self):
        details = _make_details()
        result = _extract_channel_details(details, None)
        assert "lint" in result
        assert "tests" in result
        assert result["lint"]["status"] == "pass"
        assert result["lint"]["finding_count"] == 1
        assert result["lint"]["duration_ms"] == 42

    def test_single_channel(self):
        details = _make_details()
        result = _extract_channel_details(details, "tests")
        assert list(result.keys()) == ["tests"]
        assert result["tests"]["status"] == "fail"

    def test_empty_channels(self):
        result = _extract_channel_details({"channels": {}}, None)
        assert result == {}


# ── _extract_repairs ──────────────────────────────────────────────────────


class TestExtractRepairs:
    def test_all_repairs(self):
        details = _make_details()
        repairs = _extract_repairs(details, None)
        assert len(repairs) == 1
        assert repairs[0]["action"] == "remove-import"

    def test_filtered_channel(self):
        details = _make_details()
        repairs = _extract_repairs(details, "tests")
        assert repairs == []

    def test_no_channels(self):
        assert _extract_repairs({"channels": {}}, None) == []


# ── _extract_evidence ─────────────────────────────────────────────────────


class TestExtractEvidence:
    def test_with_metrics(self):
        details = _make_details()
        evidence = _extract_evidence(details, None)
        assert "lint" in evidence
        assert evidence["lint"]["lines_checked"] == 500
        # tests has empty metrics, should not appear
        assert "tests" not in evidence

    def test_filtered_channel(self):
        details = _make_details()
        evidence = _extract_evidence(details, "lint")
        assert list(evidence.keys()) == ["lint"]

    def test_no_metrics(self):
        evidence = _extract_evidence({"channels": {"ch": {"metrics": {}}}}, None)
        assert evidence == {}


# ── _extract_proven_resolutions_from_details ──────────────────────────────


class TestExtractProvenResolutions:
    def test_with_proven_resolution(self):
        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {
                            "kind": "ruff",
                            "message": "unused import",
                            "proven_resolution": {
                                "repertoire": "remove-import",
                                "confidence": 0.95,
                            },
                        }
                    ]
                }
            }
        }
        result = _extract_proven_resolutions_from_details(details, None)
        assert len(result) == 1
        assert result[0]["channel"] == "lint"
        assert result[0]["resolution"] == "remove-import"
        assert result[0]["confidence"] == 0.95

    def test_no_proven_resolution(self):
        details = _make_details()
        result = _extract_proven_resolutions_from_details(details, None)
        assert result == []

    def test_channel_filter(self):
        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {
                            "kind": "ruff",
                            "message": "m",
                            "proven_resolution": {"repertoire": "r", "confidence": 0.9},
                        }
                    ]
                },
                "tests": {
                    "findings": [
                        {
                            "kind": "test",
                            "message": "m2",
                            "proven_resolution": {"repertoire": "r2", "confidence": 0.8},
                        }
                    ]
                },
            }
        }
        result = _extract_proven_resolutions_from_details(details, "tests")
        assert len(result) == 1
        assert result[0]["channel"] == "tests"


# ── _populate_findings_section ────────────────────────────────────────────


class TestPopulateFindingsSection:
    def test_populates_findings(self):
        output: dict = {}
        details = _make_details()
        _populate_findings_section(output, details, None, None, 100)
        assert "total_matching" in output
        assert "findings" in output

    def test_delegation_import_failure_suppressed(self):
        """If the delegation import fails, findings should still be populated."""
        output: dict = {}
        details = _make_details()
        with patch(
            "mcp_tools._controlplane_impl_details.contextlib.suppress",
            side_effect=lambda *_: MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False),
        ):
            _populate_findings_section(output, details, None, None, 100)
        assert "findings" in output

    def test_passes_kwargs_through(self):
        output: dict = {}
        details = _make_details()
        _populate_findings_section(output, details, None, None, 100, top_n=1)
        assert output.get("sorted_by") == "roi"
        assert len(output["findings"]) == 1


# ── _populate_coherence ───────────────────────────────────────────────────


class TestPopulateCoherence:
    def test_sets_coherence(self):
        output: dict = {}
        details = {"coherence": {"score": 0.9}}
        _populate_coherence(output, details, None, None, None, "r1")
        assert output["coherence"] == {"score": 0.9}

    def test_missing_coherence(self):
        output: dict = {}
        _populate_coherence(output, {}, None, None, None, "r1")
        assert output["coherence"] == {}


# ── _populate_findings (wrapper) ──────────────────────────────────────────


class TestPopulateFindings:
    def test_delegates_to_section(self):
        output: dict = {}
        details = _make_details()
        _populate_findings(output, details, None, None, 100, "r1")
        assert "findings" in output
        assert "total_matching" in output


# ── _populate_channel_details (wrapper) ───────────────────────────────────


class TestPopulateChannelDetails:
    def test_sets_channel_details(self):
        output: dict = {}
        details = _make_details()
        _populate_channel_details(output, details, None, None, None, "r1")
        assert "lint" in output["channel_details"]
        assert "tests" in output["channel_details"]


# ── _populate_repairs (wrapper) ───────────────────────────────────────────


class TestPopulateRepairs:
    def test_sets_repairs(self):
        output: dict = {}
        details = _make_details()
        _populate_repairs(output, details, None, None, None, "r1")
        assert len(output["repairs"]) == 1

    def test_filtered_empty(self):
        output: dict = {}
        details = _make_details()
        _populate_repairs(output, details, "tests", None, None, "r1")
        assert output["repairs"] == []


# ── _populate_evidence (wrapper) ──────────────────────────────────────────


class TestPopulateEvidence:
    def test_sets_evidence_when_present(self):
        output: dict = {}
        details = _make_details()
        _populate_evidence(output, details, None, None, None, "r1")
        assert "evidence" in output

    def test_no_evidence_key_when_empty(self):
        output: dict = {}
        _populate_evidence(output, {"channels": {}}, None, None, None, "r1")
        assert "evidence" not in output


# ── _populate_proven_resolutions (wrapper) ────────────────────────────────


class TestPopulateProvenResolutions:
    def test_sets_resolutions_when_present(self):
        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {
                            "kind": "k",
                            "message": "m",
                            "proven_resolution": {"repertoire": "r", "confidence": 0.5},
                        }
                    ]
                }
            }
        }
        output: dict = {}
        _populate_proven_resolutions(output, details, None, None, None, "r1")
        assert "proven_resolutions" in output
        assert len(output["proven_resolutions"]) == 1

    def test_no_key_when_empty(self):
        output: dict = {}
        _populate_proven_resolutions(output, _make_details(), None, None, None, "r1")
        assert "proven_resolutions" not in output


# ── _populate_next_actions (wrapper) ──────────────────────────────────────


class TestPopulateNextActions:
    def test_empty_output(self):
        output: dict = {}
        _populate_next_actions(output, {}, None, None, None, "r1")
        assert output["next_actions"] == []

    def test_with_findings(self):
        output: dict = {"findings": [{"kind": "x"}]}
        _populate_next_actions(output, {}, None, None, None, "r1")
        assert len(output["next_actions"]) == 1
        assert output["next_actions"][0]["priority"] == 2


# ── _impl_controlplane_get_details ────────────────────────────────────────


class TestImplControlplaneGetDetails:
    @patch("mcp_tools._controlplane_impl_details.load_controlplane_run", create=True)
    def test_run_not_found_raises(self, _mock_load):
        # Patch the actual import inside the function
        with (
            patch("lintgate.state.load_controlplane_run", return_value=None),
            pytest.raises(ValueError, match="No ControlPlane run found"),
        ):
            _impl_controlplane_get_details(
                "bad-id", None, None, 10, None, {"_json_dumps": json.dumps}
            )

    def test_basic_details(self):
        details = _make_details()
        with patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "run-1", None, None, 100, ["findings", "coherence"], {"_json_dumps": json.dumps}
            )
        result = json.loads(raw)
        assert result["run_id"] == "run-1"
        assert result["duration_ms"] == 100
        assert "coherence" in result
        assert "findings" in result
        # sections not requested should not appear
        assert "repairs" not in result

    def test_default_sections(self):
        details = _make_details()
        with patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "run-1", None, None, 100, None, {"_json_dumps": json.dumps}
            )
        result = json.loads(raw)
        assert "findings" in result
        assert "coherence" in result
        assert "channel_details" in result
        assert "repairs" in result
        assert "next_actions" in result
        assert "finding_summary" in result

    def test_with_top_n(self):
        details = _make_details()
        with patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "run-1",
                None,
                None,
                100,
                ["findings"],
                {"_json_dumps": json.dumps},
                top_n=1,
            )
        result = json.loads(raw)
        assert result.get("sorted_by") == "roi"
        assert len(result["findings"]) == 1

    def test_with_finding_domain_filter(self):
        details = {
            "duration_ms": 100,
            "channels": {
                "lint": {
                    "findings": [{"severity": "warning", "linter": "ruff"}],
                    "repairs": [],
                },
                "deps": {
                    "findings": [{"severity": "warning", "linter": "pip_audit"}],
                    "repairs": [],
                },
            },
            "coherence": {"score": 0.85},
        }
        with patch("lintgate.state.load_controlplane_run", return_value=details):
            raw = _impl_controlplane_get_details(
                "run-1",
                None,
                None,
                100,
                ["findings"],
                {"_json_dumps": json.dumps},
                finding_domain="code",
            )
        result = json.loads(raw)
        assert result["total_matching"] == 1
        assert result["findings"][0]["channel"] == "lint"
        assert result["finding_summary"]["domains"]["code"]["total"] == 1


# ── _build_config_status ─────────────────────────────────────────────────


def _make_cp_config(enabled=True, session_memory=False, channels=None):
    """Build a mock ControlPlane config."""
    token_policy = SimpleNamespace(hook_max_tokens=500, include_pass_details=False)
    if channels is None:
        ch = SimpleNamespace(enabled=True, blocking=False, timeout_ms=1000)
        channels = {"lint": ch}
    return SimpleNamespace(
        enabled=enabled,
        latency_budget_ms=3000,
        advisory_default=True,
        session_memory=session_memory,
        session_max_age_hours=24,
        constraint_proposal_threshold=0.7,
        token_policy=token_policy,
        channels=channels,
    )


class TestBuildConfigStatus:
    def test_basic_status(self):
        helpers = {"_build_onboarding_status": lambda _: {"step": "done"}}
        cfg = _make_cp_config()
        status = _build_config_status(cfg, "/tmp/project", helpers)
        assert status["controlplane_enabled"] is True
        assert status["latency_budget_ms"] == 3000
        assert "lint" in status["channels"]
        assert status["channels"]["lint"]["enabled"] is True

    def test_disabled_includes_onboarding(self):
        helpers = {"_build_onboarding_status": lambda _: {"onboard": True}}
        cfg = _make_cp_config(enabled=False)
        status = _build_config_status(cfg, "/tmp/project", helpers)
        assert status["onboarding"] == {"onboard": True}

    def test_session_memory_includes_session(self):
        helpers: dict[str, Any] = {"_build_onboarding_status": lambda _: {}}
        cfg = _make_cp_config(session_memory=True)
        with patch(
            "mcp_tools._controlplane_impl_details._get_session_status",
            return_value={"session_id": "s1"},
        ):
            status = _build_config_status(cfg, "/tmp/project", helpers)
        assert status["session"] == {"session_id": "s1"}

    def test_token_policy(self):
        helpers: dict[str, Any] = {"_build_onboarding_status": lambda _: {}}
        cfg = _make_cp_config()
        status = _build_config_status(cfg, "/tmp/project", helpers)
        assert status["token_policy"]["hook_max_tokens"] == 500
        assert status["token_policy"]["include_pass_details"] is False


# ── _get_session_status ───────────────────────────────────────────────────


class TestGetSessionStatus:
    def test_no_session(self):
        with patch("lintgate.controlplane.session_memory.load_session", return_value=None):
            result = _get_session_status("/tmp/project")
        assert result is None

    def test_with_session(self):
        session = SimpleNamespace(
            session_id="s-123",
            snapshots=["snap1", "snap2"],
            coherence_trajectory=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            repair_outcomes={"r1": "pending", "r2": "applied"},
            proposed_constraints=[
                {"status": "proposed"},
                {"status": "accepted"},
            ],
            last_active=1000.0,
            latest_transfer_packet="pkt-1",
            delivery_health_summary={"ok": True},
        )
        with patch("lintgate.controlplane.session_memory.load_session", return_value=session):
            result = _get_session_status("/tmp/project")
        assert result["session_id"] == "s-123"
        assert result["runs"] == 2
        # coherence_trajectory is sliced to last 5
        assert result["coherence_trajectory"] == [0.2, 0.3, 0.4, 0.5, 0.6]
        assert result["pending_repairs"] == 1
        assert result["proposed_constraints"] == 2
        assert result["active_proposals"] == 1

    def test_import_failure_returns_none(self):
        with patch(
            "mcp_tools._controlplane_impl_details.contextlib.suppress",
            side_effect=lambda *_: MagicMock(
                __enter__=lambda s: s,
                __exit__=lambda *a: False,
            ),
        ):
            # Even if we can't mock the import cleanly, the contextlib.suppress
            # in the real code handles import errors gracefully
            pass
        # Direct test: if load_session raises, suppress catches it
        with patch(
            "lintgate.controlplane.session_memory.load_session",
            side_effect=ImportError("no module"),
        ):
            result = _get_session_status("/tmp/project")
        assert result is None


# ── _impl_controlplane_status ─────────────────────────────────────────────


class TestImplControlplaneStatus:
    def test_with_config(self):
        cfg = _make_cp_config()
        helpers = {
            "_validate_project_root": lambda p: p,
            "_build_onboarding_status": lambda _: {},
        }
        with patch("lintgate.config.load_controlplane_config", return_value=cfg):
            raw = _impl_controlplane_status("/tmp/proj", helpers)
        status = json.loads(raw)
        assert status["project"] == "/tmp/proj"
        assert status["controlplane_enabled"] is True
        assert "available_channels" in status

    def test_no_config(self):
        helpers = {
            "_validate_project_root": lambda p: p,
            "_build_onboarding_status": lambda _: {"step": 1},
        }
        with patch("lintgate.config.load_controlplane_config", return_value=None):
            raw = _impl_controlplane_status("/tmp/proj", helpers)
        status = json.loads(raw)
        assert status["controlplane_enabled"] is False
        assert "note" in status
        assert status["onboarding"] == {"step": 1}

    def test_path_none_uses_cwd(self):
        cfg = _make_cp_config()
        helpers = {
            "_validate_project_root": lambda p: p,
            "_build_onboarding_status": lambda _: {},
        }
        with (
            patch("lintgate.config.load_controlplane_config", return_value=cfg),
            patch("os.getcwd", return_value="/mock/cwd"),
        ):
            raw = _impl_controlplane_status(None, helpers)
        status = json.loads(raw)
        assert status["project"] == "/mock/cwd"


# ── Constants / structural assertions ─────────────────────────────────────


class TestConstants:
    def test_effort_defaults_keys(self):
        assert set(_EFFORT_DEFAULTS.keys()) == {
            "ruff",
            "mypy",
            "radon",
            "bandit",
            "vulture",
            "structure",
        }

    def test_sev_weight_keys(self):
        assert set(_SEV_WEIGHT.keys()) == {"blocking", "warning", "informational"}
        assert _SEV_WEIGHT["blocking"] == 3.0
        assert _SEV_WEIGHT["warning"] == 2.0
        assert _SEV_WEIGHT["informational"] == 1.0

    def test_default_sections_content(self):
        assert (
            frozenset(
                [
                    "findings",
                    "channel_details",
                    "evidence",
                    "repairs",
                    "coherence",
                    "next_actions",
                    "proven_resolutions",
                ]
            )
            == _DEFAULT_SECTIONS
        )

    def test_section_populators_order(self):
        names = [name for name, _ in _SECTION_POPULATORS]
        # next_actions must be last
        assert names[-1] == "next_actions"
        assert len(names) == 7


# ── Finding domain tests (VALUE mutation targets) ─────────────────────


class TestFindingDomain:
    def test_deps_channel_is_environment(self):
        assert _finding_domain({"channel": "deps"}) == "environment"

    def test_pip_audit_linter_is_environment(self):
        assert _finding_domain({"linter": "pip_audit"}) == "environment"

    def test_version_checker_linter_is_environment(self):
        assert _finding_domain({"linter": "version_checker"}) == "environment"

    def test_lint_channel_is_code(self):
        assert _finding_domain({"channel": "lint"}) == "code"

    def test_empty_finding_is_code(self):
        assert _finding_domain({}) == "code"

    def test_unknown_channel_is_code(self):
        assert _finding_domain({"channel": "tests", "linter": "ruff"}) == "code"


class TestSummarizeFindings:
    def test_empty_list(self):
        result = _summarize_findings([])
        assert result["domains"]["code"]["total"] == 0
        assert result["domains"]["environment"]["total"] == 0
        assert result["channels"] == {}

    def test_code_finding_counted(self):
        findings = [{"channel": "lint", "severity": "blocking"}]
        result = _summarize_findings(findings)
        assert result["domains"]["code"]["total"] == 1
        assert result["domains"]["code"]["blocking"] == 1

    def test_environment_finding_counted(self):
        findings = [{"channel": "deps", "severity": "warning"}]
        result = _summarize_findings(findings)
        assert result["domains"]["environment"]["total"] == 1
        assert result["domains"]["environment"]["warning"] == 1

    def test_mixed_domains(self):
        findings = [
            {"channel": "lint", "severity": "blocking"},
            {"channel": "deps", "severity": "warning"},
            {"channel": "tests", "severity": "informational"},
        ]
        result = _summarize_findings(findings)
        assert result["domains"]["code"]["total"] == 2
        assert result["domains"]["environment"]["total"] == 1
        assert result["channels"]["lint"] == 1
        assert result["channels"]["deps"] == 1
