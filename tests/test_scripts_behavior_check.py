"""Behavioral tests for scripts/behavior_check.py.

Exercises the compute_* functions directly by importing them from the script.
The MCP wrapper in mcp_tools/behavior_tools.py just shells out to this script,
so its subprocess-argv tests live in tests/test_mcp_behavior_tools.py.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from scripts.behavior_check import (
    cmd_memory_reset,
    cmd_memory_status,
    compute_constraint,
    compute_hygiene,
    compute_memory_reset,
    compute_memory_status,
    compute_precheck,
    compute_predict,
)


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _load_emitted(capsys) -> dict:
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    envelope = json.loads(line)
    if "file" in envelope:
        with open(envelope["file"]) as f:
            return json.loads(f.read())
    return envelope


# ── compute_hygiene ────────────────────────────────────────────────────────


class TestComputeHygiene:
    def test_no_checks_applicable(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.hygiene.classify_and_check", lambda action, root: None)
        out, summary, na = compute_hygiene(str(tmp_path), "echo hello")
        assert out["status"] == "no_checks_applicable"
        assert na == []
        assert "Hygiene" in summary

    def test_pass_no_warnings(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.hygiene import HygieneResult

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: HygieneResult(
                command_class="pip_install",
                warnings=[],
                recommendation="All clear.",
            ),
        )
        out, _, _ = compute_hygiene(str(tmp_path), "pip install requests")
        assert out["status"] == "pass"
        assert out["command_class"] == "pip_install"
        assert out["message"] == "All clear."

    def test_warnings_returned(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.hygiene import HygieneResult, HygieneWarning

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: HygieneResult(
                command_class="pip_install",
                warnings=[
                    HygieneWarning(
                        check="venv_active",
                        message="No virtualenv detected",
                        confidence=0.9,
                        actionability="immediate",
                    ),
                ],
                recommendation="Activate a venv first.",
            ),
        )
        out, _, _ = compute_hygiene(str(tmp_path), "pip install requests")
        assert out["status"] == "warnings"
        assert len(out["warnings"]) == 1
        assert out["warnings"][0]["check"] == "venv_active"

    def test_next_actions_for_immediate_warnings(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.hygiene import HygieneResult, HygieneWarning

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: HygieneResult(
                command_class="pip_install",
                warnings=[
                    HygieneWarning(
                        check="venv_active",
                        message="No virtualenv detected",
                        confidence=0.9,
                        actionability="immediate",
                    ),
                    HygieneWarning(
                        check="lockfile",
                        message="Lockfile is stale",
                        confidence=0.7,
                        actionability="advisory",
                    ),
                ],
                recommendation="Fix venv.",
            ),
        )
        out, _, na = compute_hygiene(str(tmp_path), "pip install foo")
        assert len(na) == 1
        assert "virtualenv" in na[0]["reason"].lower()

    def test_hygiene_exception_graceful(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        out, _, _ = compute_hygiene(str(tmp_path), "pip install x")
        assert out["status"] == "no_checks_applicable"


# ── compute_constraint ─────────────────────────────────────────────────────


class TestComputeConstraint:
    def _write_cfg(self, tmp_path: Path) -> None:
        d = tmp_path / ".claude"
        d.mkdir(exist_ok=True)
        (d / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

    def test_basic_no_constraints(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, _ = compute_constraint(str(tmp_path), "pytest tests/")
        assert "constraint_ledger" in out
        assert "coverage" in out
        assert "recommendation" in out

    def test_with_known_constraints(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, _ = compute_constraint(
            str(tmp_path),
            "pytest tests/",
            known_constraints=["some tests may fail due to missing fixtures"],
        )
        assert out["coverage"]["agent_reported"] == 1

    def test_first_session_hint(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "session"
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory_ops.SESSION_DIR", tmp_path / "session"
        )
        monkeypatch.setattr(
            "lintgate.orchestration.knowledge.KNOWLEDGE_DIR", tmp_path / "knowledge"
        )
        self._write_cfg(tmp_path)
        out, _, _ = compute_constraint(str(tmp_path), "make build")
        assert "first_session_hint" in out

    def test_similar_failures_populated(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.controlplane.behavior_compass import BehaviorCompass
        from lintgate.controlplane.behavior_types import ApproachAttempt
        from lintgate.controlplane.session_memory import SessionMemory

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)

        compass = BehaviorCompass()
        compass.approaches.append(
            ApproachAttempt(
                approach_sig="pytest:run",
                outcome="failed",
                error_sigs=["ModuleNotFoundError"],
                event_count=3,
            )
        )
        session = SessionMemory(project_root=str(tmp_path))
        session.behavior_compass = compass.to_dict()

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, max_age=4.0: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )

        out, _, _ = compute_constraint(str(tmp_path), "pytest tests/")
        assert isinstance(out["similar_failures"], list)

    def test_next_actions_when_coverage_gap(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.controlplane.behavior_compass import BehaviorCompass
        from lintgate.controlplane.behavior_types import BehaviorHypothesis
        from lintgate.controlplane.session_memory import SessionMemory

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)

        compass = BehaviorCompass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="h1234567",
                claim="Tests require specific environment variables",
                confidence=0.5,
                source="command_failure",
                status="active",
                applies_to_sigs=["pytest:*"],
                applies_to_tools=["Bash"],
            )
        )
        session = SessionMemory(project_root=str(tmp_path))
        session.behavior_compass = compass.to_dict()

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, max_age=4.0: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )

        out, _, na = compute_constraint(str(tmp_path), "pytest tests/")
        if out["coverage"]["coverage_gap"] > 0:
            assert na


# ── compute_predict ────────────────────────────────────────────────────────


class TestComputePredict:
    def _write_cfg(self, tmp_path: Path) -> None:
        d = tmp_path / ".claude"
        d.mkdir(exist_ok=True)
        (d / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

    def test_invalid_prediction_type(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, _, err = compute_predict(
            str(tmp_path), "pytest tests/", "Tests will pass", "invalid_type", 0
        )
        assert out is None
        assert err is not None
        assert "error" in err
        assert "valid_types" in err

    def test_non_bash_action_not_applicable(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, _, err = compute_predict(
            str(tmp_path), "read file contents", "File will exist", "exit_code", 0
        )
        assert out is None
        assert err is not None
        assert err["status"] == "not_applicable"

    def test_successful_registration(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, na, err = compute_predict(
            str(tmp_path), "run pytest tests/", "All tests pass", "exit_code", 0
        )
        assert err is None
        assert out is not None
        assert out["status"] == "registered"
        assert out["prediction_type"] == "exit_code"
        assert out["prediction_value"] == 0
        assert "prediction_id" in out
        assert na

    def test_prediction_tracking_section(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, _, err = compute_predict(
            str(tmp_path), "bash pytest tests/", "Tests pass", "exit_code", 0
        )
        assert err is None
        assert out is not None
        tracking = out["prediction_tracking"]
        assert "pending_count" in tracking
        assert "checked_count" in tracking

    def test_error_signature_prediction(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        self._write_cfg(tmp_path)
        out, _, _, err = compute_predict(
            str(tmp_path),
            "run python script.py",
            "No import errors",
            "error_signature",
            "ImportError",
        )
        assert err is None
        assert out is not None
        assert out["status"] == "registered"
        assert out["prediction_type"] == "error_signature"


# ── compute_precheck (deprecated aggregator) ───────────────────────────────


class TestComputePrecheck:
    def _write_cfg(self, tmp_path: Path) -> None:
        d = tmp_path / ".claude"
        d.mkdir(exist_ok=True)
        (d / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

    def test_deprecation_notice_present(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.hygiene.classify_and_check", lambda action, root: None)
        self._write_cfg(tmp_path)
        out, _, _ = compute_precheck(str(tmp_path), "pytest tests/")
        assert "deprecation" in out
        assert "migration" in out["deprecation"]

    def test_precheck_with_prediction(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.hygiene.classify_and_check", lambda action, root: None)
        self._write_cfg(tmp_path)
        out, _, _ = compute_precheck(
            str(tmp_path),
            "run pytest tests/",
            prediction="Tests pass",
            prediction_type="exit_code",
            prediction_value=0,
        )
        assert "deprecation" in out
        if "prediction_tracking" in out:
            assert out["prediction_tracking"].get("prediction_registered") is True

    def test_precheck_prediction_missing_type(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.hygiene.classify_and_check", lambda action, root: None)
        self._write_cfg(tmp_path)
        out, _, _ = compute_precheck(
            str(tmp_path),
            "run pytest tests/",
            prediction="Tests pass",
        )
        assert "prediction_error" in out
        assert len(out["prediction_error"]["errors"]) >= 1

    def test_precheck_prediction_invalid_type(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.hygiene.classify_and_check", lambda action, root: None)
        self._write_cfg(tmp_path)
        out, _, _ = compute_precheck(
            str(tmp_path),
            "run pytest tests/",
            prediction="Tests pass",
            prediction_type="bad_type",
            prediction_value=0,
        )
        assert "prediction_error" in out

    def test_precheck_hygiene_warnings_merged(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.hygiene import HygieneResult, HygieneWarning

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: HygieneResult(
                command_class="pip_install",
                warnings=[
                    HygieneWarning(
                        check="venv_active",
                        message="No virtualenv",
                        confidence=0.9,
                        actionability="immediate",
                    ),
                ],
                recommendation="Activate venv.",
            ),
        )
        self._write_cfg(tmp_path)
        out, _, _ = compute_precheck(str(tmp_path), "pip install requests")
        assert "hygiene" in out
        assert out["hygiene"]["command_class"] == "pip_install"
        assert len(out["hygiene"]["warnings"]) == 1


# ── compute_memory_status / compute_memory_reset ───────────────────────────


class TestComputeMemoryStatus:
    def test_no_controlplane_config_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("lintgate.config.load_controlplane_config", lambda cwd: None)
        assert compute_memory_status(str(tmp_path)) is None

    def test_returns_profile_data(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.controlplane.global_behavior_profile import GlobalBehaviorProfile
        from lintgate.controlplane.types import ControlPlaneConfig

        cp_config = ControlPlaneConfig(
            enabled=True,
            global_memory_enabled=True,
            global_memory_ttl_days=90,
            global_memory_alpha=0.6,
            global_memory_decay_horizon=50,
        )
        monkeypatch.setattr("lintgate.config.load_controlplane_config", lambda cwd: cp_config)

        profile = GlobalBehaviorProfile(
            session_count=5,
            signal_priors={"approach_cycling": {"total_firings": 3, "sessions_present": 2}},
            intent_ratios={"inspect": 10, "modify": 5},
            nudge_outcomes={"approach_cycling": {"accepted": 2, "ignored": 1}},
            computed_bias_adjustments={"approach_cycling": 0.05},
        )
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            lambda ttl_days=90: profile,
        )

        out = compute_memory_status(str(tmp_path))
        assert out is not None
        assert out["scope"] == "project"
        assert out["session_count"] == 5
        assert out["enabled"] is True
        assert "approach_cycling" in out["nudge_outcomes"]
        assert out["nudge_outcomes"]["approach_cycling"]["acceptance_rate"] == 0.67
        assert "inspect" in out["intent_ratios_normalized"]

    def test_nudge_rates_zero_total_omitted(self, tmp_path: Path, monkeypatch) -> None:
        from lintgate.controlplane.global_behavior_profile import GlobalBehaviorProfile
        from lintgate.controlplane.types import ControlPlaneConfig

        cp_config = ControlPlaneConfig(enabled=True, global_memory_enabled=True)
        monkeypatch.setattr("lintgate.config.load_controlplane_config", lambda cwd: cp_config)

        profile = GlobalBehaviorProfile(
            session_count=2,
            nudge_outcomes={"some_signal": {"accepted": 0, "ignored": 0}},
        )
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            lambda ttl_days=90: profile,
        )

        out = compute_memory_status(str(tmp_path))
        assert out is not None
        assert "some_signal" not in out["nudge_outcomes"]


class TestComputeMemoryReset:
    def test_reset_returns_status(self, tmp_path: Path, monkeypatch) -> None:
        saved = []
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.save_global_profile",
            lambda p: saved.append(p),
        )
        out = compute_memory_reset(str(tmp_path))
        assert out["status"] == "reset"
        assert out["scope"] == "project"
        assert "profile_path" in out
        assert len(saved) == 1
        assert saved[0].session_count == 0


# ── cmd_* dispatchers (stdout-emitting) ────────────────────────────────────


class TestCmdMemory:
    def test_cmd_memory_reset_prints_plain_json(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.save_global_profile",
            lambda p: None,
        )
        cmd_memory_reset(_ns(path=str(tmp_path)))
        out = capsys.readouterr().out.strip()
        parsed = json.loads(out)
        assert parsed["status"] == "reset"
        # Historical contract: no disk envelope (no analysis_id/file)
        assert "analysis_id" not in parsed
        assert "file" not in parsed

    def test_cmd_memory_status_no_config_emits_error(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr("lintgate.config.load_controlplane_config", lambda cwd: None)
        import pytest

        with pytest.raises(SystemExit):
            cmd_memory_status(_ns(path=str(tmp_path)))
        out = capsys.readouterr().out.strip()
        parsed = json.loads(out)
        assert "error" in parsed

    def test_cmd_memory_status_emits_envelope(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        from lintgate.controlplane.global_behavior_profile import GlobalBehaviorProfile
        from lintgate.controlplane.types import ControlPlaneConfig

        cp_config = ControlPlaneConfig(enabled=True, global_memory_enabled=True)
        monkeypatch.setattr("lintgate.config.load_controlplane_config", lambda cwd: cp_config)
        profile = GlobalBehaviorProfile(session_count=3)
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            lambda ttl_days=90: profile,
        )
        cmd_memory_status(_ns(path=str(tmp_path)))
        full = _load_emitted(capsys)
        assert full["session_count"] == 3
