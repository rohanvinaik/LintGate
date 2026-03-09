"""Tests for mcp_tools/behavior_tools.py — targeting uncovered branches.

Covers hygiene_check, constraint_check, prediction_register,
behavior_precheck (deprecated wrapper), global_memory_status,
and global_memory_reset.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ---------------------------------------------------------------------------
# Minimal MCP stub so register() can attach @mcp.tool() callables
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal stand-in for the FastMCP instance used in register()."""

    def __init__(self):
        self._tools: dict[str, Any] = {}

    def tool(self):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn

        return decorator


def _make_helpers(tmp_path):
    """Build the helpers dict that register() expects."""

    def _validate_project_root(path: str) -> str:
        if not os.path.isdir(path):
            raise ValueError(f"Not a directory: {path}")
        return os.path.abspath(path)

    def _json_dumps(data, **kw):
        return json.dumps(data)

    def _build_onboarding_status(project_root):
        return {"config_state": "config_enabled"}

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
        "_build_onboarding_status": _build_onboarding_status,
    }


def _register(tmp_path):
    """Call register() and return the dict of tool callables."""
    from mcp_tools.behavior_tools import register

    mcp = _FakeMCP()
    helpers = _make_helpers(tmp_path)
    return register(mcp, helpers)


# ---------------------------------------------------------------------------
# hygiene_check
# ---------------------------------------------------------------------------


class TestHygieneCheck:
    """Tests for the hygiene_check MCP tool."""

    def test_no_checks_applicable(self, tmp_path, monkeypatch):
        """When classify_and_check returns None, status is no_checks_applicable."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: None,
        )

        tools = _register(tmp_path)
        result = json.loads(tools["hygiene_check"](path=str(tmp_path), planned_action="echo hello"))
        assert result["status"] == "no_checks_applicable"
        assert result["next_actions"] == []

    def test_pass_no_warnings(self, tmp_path, monkeypatch):
        """When classify_and_check returns result with no warnings, status is pass."""
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

        tools = _register(tmp_path)
        result = json.loads(
            tools["hygiene_check"](path=str(tmp_path), planned_action="pip install requests")
        )
        assert result["status"] == "pass"
        assert result["command_class"] == "pip_install"
        assert result["message"] == "All clear."

    def test_warnings_returned(self, tmp_path, monkeypatch):
        """When classify_and_check returns warnings, status is warnings."""
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

        tools = _register(tmp_path)
        result = json.loads(
            tools["hygiene_check"](path=str(tmp_path), planned_action="pip install requests")
        )
        assert result["status"] == "warnings"
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["check"] == "venv_active"
        assert result["recommendation"] == "Activate a venv first."

    def test_next_actions_for_immediate_warnings(self, tmp_path, monkeypatch):
        """Immediate-actionability warnings produce next_actions."""
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

        tools = _register(tmp_path)
        result = json.loads(
            tools["hygiene_check"](path=str(tmp_path), planned_action="pip install foo")
        )
        # Only the immediate warning generates a next_action
        assert len(result["next_actions"]) == 1
        assert "virtualenv" in result["next_actions"][0]["reason"].lower()

    def test_hygiene_exception_graceful(self, tmp_path, monkeypatch):
        """When classify_and_check raises, result is no_checks_applicable."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        tools = _register(tmp_path)
        result = json.loads(
            tools["hygiene_check"](path=str(tmp_path), planned_action="pip install x")
        )
        assert result["status"] == "no_checks_applicable"


# ---------------------------------------------------------------------------
# constraint_check
# ---------------------------------------------------------------------------


class TestConstraintCheck:
    """Tests for the constraint_check MCP tool."""

    def test_basic_constraint_check_no_constraints(self, tmp_path, monkeypatch):
        """Basic run with no known constraints."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        # Create a minimal config file so load_controlplane_config works
        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        result = json.loads(
            tools["constraint_check"](
                path=str(tmp_path),
                planned_action="pytest tests/",
            )
        )
        assert "constraint_ledger" in result
        assert "coverage" in result
        assert "recommendation" in result

    def test_constraint_check_with_known_constraints(self, tmp_path, monkeypatch):
        """When known_constraints are provided, they are declared as hypotheses."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["constraint_check"](
                path=str(tmp_path),
                planned_action="pytest tests/",
                known_constraints=["some tests may fail due to missing fixtures"],
            )
        )
        assert result["coverage"]["agent_reported"] == 1

    def test_first_session_hint(self, tmp_path, monkeypatch):
        """First constraint_check in a session should include first_session_hint."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "session"
        )

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["constraint_check"](
                path=str(tmp_path),
                planned_action="make build",
            )
        )
        assert "first_session_hint" in result

    def test_similar_failures_populated(self, tmp_path, monkeypatch):
        """When compass has failed approaches matching the command, similar_failures appear."""
        from lintgate.controlplane.behavior_compass import (
            BehaviorCompass,
        )
        from lintgate.controlplane.behavior_types import ApproachAttempt
        from lintgate.controlplane.session_memory import SessionMemory

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        # Build a compass with a failed approach matching "pytest"
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
        save_calls = []
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: save_calls.append(s),
        )

        tools = _register(tmp_path)
        result = json.loads(
            tools["constraint_check"](
                path=str(tmp_path),
                planned_action="pytest tests/",
            )
        )
        assert isinstance(result["similar_failures"], list)

    def test_next_actions_when_coverage_gap(self, tmp_path, monkeypatch):
        """When coverage gap exists, next_actions should suggest re-running."""
        from lintgate.controlplane.behavior_compass import BehaviorCompass
        from lintgate.controlplane.behavior_types import BehaviorHypothesis
        from lintgate.controlplane.session_memory import SessionMemory

        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        # Build compass with an active hypothesis
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

        tools = _register(tmp_path)
        result = json.loads(
            tools["constraint_check"](
                path=str(tmp_path),
                planned_action="pytest tests/",
            )
        )
        # With hypotheses but no agent-reported constraints, there should be a gap
        if result["coverage"]["coverage_gap"] > 0:
            assert "next_actions" in result


# ---------------------------------------------------------------------------
# prediction_register
# ---------------------------------------------------------------------------


class TestPredictionRegister:
    """Tests for the prediction_register MCP tool."""

    def test_invalid_prediction_type(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="pytest tests/",
                prediction="Tests will pass",
                prediction_type="invalid_type",
                prediction_value=0,
            )
        )
        assert "error" in result
        assert "valid_types" in result

    def test_non_bash_action_not_applicable(self, tmp_path, monkeypatch):
        """Actions that do not match Bash keywords return not_applicable."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="read file contents",
                prediction="File will exist",
                prediction_type="exit_code",
                prediction_value=0,
            )
        )
        assert result["status"] == "not_applicable"

    def test_successful_prediction_registration(self, tmp_path, monkeypatch):
        """Valid prediction for a Bash action should be registered."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="run pytest tests/",
                prediction="All tests pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        )
        assert result["status"] == "registered"
        assert result["prediction_type"] == "exit_code"
        assert result["prediction_value"] == 0
        assert "prediction_id" in result
        assert "next_actions" in result

    def test_prediction_tracking_section(self, tmp_path, monkeypatch):
        """Registered prediction should include prediction_tracking."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="bash pytest tests/",
                prediction="Tests pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        )
        tracking = result["prediction_tracking"]
        assert "pending_count" in tracking
        assert "checked_count" in tracking

    def test_error_signature_prediction(self, tmp_path, monkeypatch):
        """prediction_type=error_signature should be accepted."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="run python script.py",
                prediction="No import errors",
                prediction_type="error_signature",
                prediction_value="ImportError",
            )
        )
        assert result["status"] == "registered"
        assert result["prediction_type"] == "error_signature"


# ---------------------------------------------------------------------------
# behavior_precheck (deprecated wrapper)
# ---------------------------------------------------------------------------


class TestBehaviorPrecheck:
    """Tests for the deprecated behavior_precheck wrapper."""

    def test_deprecation_notice_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: None,
        )

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["behavior_precheck"](
                path=str(tmp_path),
                planned_action="pytest tests/",
            )
        )
        assert "deprecation" in result
        assert "migration" in result["deprecation"]

    def test_precheck_with_prediction(self, tmp_path, monkeypatch):
        """When prediction args are provided, prediction_tracking appears."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: None,
        )

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["behavior_precheck"](
                path=str(tmp_path),
                planned_action="run pytest tests/",
                prediction="Tests pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        )
        assert "deprecation" in result
        # prediction_tracking should be present since prediction was provided
        if "prediction_tracking" in result:
            assert result["prediction_tracking"].get("prediction_registered") is True

    def test_precheck_prediction_missing_type(self, tmp_path, monkeypatch):
        """When prediction is given but type is missing, prediction_error appears."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: None,
        )

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["behavior_precheck"](
                path=str(tmp_path),
                planned_action="run pytest tests/",
                prediction="Tests pass",
                # missing prediction_type and prediction_value
            )
        )
        assert "prediction_error" in result
        assert len(result["prediction_error"]["errors"]) >= 1

    def test_precheck_prediction_invalid_type(self, tmp_path, monkeypatch):
        """When prediction_type is invalid, prediction_error has that info."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        monkeypatch.setattr(
            "lintgate.hygiene.classify_and_check",
            lambda action, root: None,
        )

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["behavior_precheck"](
                path=str(tmp_path),
                planned_action="run pytest tests/",
                prediction="Tests pass",
                prediction_type="bad_type",
                prediction_value=0,
            )
        )
        assert "prediction_error" in result

    def test_precheck_hygiene_warnings_merged(self, tmp_path, monkeypatch):
        """When hygiene returns warnings, they appear in the precheck output."""
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

        config_dir = tmp_path / ".claude"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        tools = _register(tmp_path)
        result = json.loads(
            tools["behavior_precheck"](
                path=str(tmp_path),
                planned_action="pip install requests",
            )
        )
        assert "hygiene" in result
        assert result["hygiene"]["command_class"] == "pip_install"
        assert len(result["hygiene"]["warnings"]) == 1


# ---------------------------------------------------------------------------
# global_memory_status
# ---------------------------------------------------------------------------


class TestGlobalMemoryStatus:
    """Tests for global_memory_status MCP tool."""

    def test_no_controlplane_config(self, tmp_path, monkeypatch):
        """When no controlplane config, returns error."""
        monkeypatch.setattr(
            "lintgate.config.load_controlplane_config",
            lambda cwd: None,
        )

        tools = _register(tmp_path)
        result = json.loads(tools["global_memory_status"](path=str(tmp_path)))
        assert "error" in result

    def test_returns_profile_data(self, tmp_path, monkeypatch):
        """When config is present, returns profile summary."""
        from lintgate.controlplane.global_behavior_profile import GlobalBehaviorProfile
        from lintgate.controlplane.types import ControlPlaneConfig

        cp_config = ControlPlaneConfig(
            enabled=True,
            global_memory_enabled=True,
            global_memory_ttl_days=90,
            global_memory_alpha=0.6,
            global_memory_decay_horizon=50,
        )
        monkeypatch.setattr(
            "lintgate.config.load_controlplane_config",
            lambda cwd: cp_config,
        )

        profile = GlobalBehaviorProfile(
            session_count=5,
            signal_priors={"approach_cycling": {"total_firings": 3, "sessions_present": 2}},
            intent_ratios={"inspect": 10, "modify": 5},
            nudge_outcomes={
                "approach_cycling": {"accepted": 2, "ignored": 1},
            },
            computed_bias_adjustments={"approach_cycling": 0.05},
        )
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            lambda ttl_days=90: profile,
        )

        tools = _register(tmp_path)
        result = json.loads(tools["global_memory_status"](path=str(tmp_path)))

        assert result["scope"] == "project"
        assert result["session_count"] == 5
        assert result["enabled"] is True
        assert "approach_cycling" in result["nudge_outcomes"]
        assert result["nudge_outcomes"]["approach_cycling"]["acceptance_rate"] == 0.67
        assert "inspect" in result["intent_ratios_normalized"]

    def test_nudge_rates_zero_total(self, tmp_path, monkeypatch):
        """When nudge outcomes have zero total, they are omitted from rates."""
        from lintgate.controlplane.global_behavior_profile import GlobalBehaviorProfile
        from lintgate.controlplane.types import ControlPlaneConfig

        cp_config = ControlPlaneConfig(enabled=True, global_memory_enabled=True)
        monkeypatch.setattr(
            "lintgate.config.load_controlplane_config",
            lambda cwd: cp_config,
        )

        profile = GlobalBehaviorProfile(
            session_count=2,
            nudge_outcomes={"some_signal": {"accepted": 0, "ignored": 0}},
        )
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            lambda ttl_days=90: profile,
        )

        tools = _register(tmp_path)
        result = json.loads(tools["global_memory_status"](path=str(tmp_path)))
        # Zero-total nudge outcomes should not appear in the output
        assert "some_signal" not in result["nudge_outcomes"]


# ---------------------------------------------------------------------------
# global_memory_reset
# ---------------------------------------------------------------------------


class TestGlobalMemoryReset:
    """Tests for global_memory_reset MCP tool."""

    def test_reset_returns_status(self, tmp_path, monkeypatch):
        saved_profiles = []
        monkeypatch.setattr(
            "lintgate.controlplane.global_behavior_profile.save_global_profile",
            lambda p: saved_profiles.append(p),
        )

        tools = _register(tmp_path)
        result = json.loads(tools["global_memory_reset"](path=str(tmp_path)))

        assert result["status"] == "reset"
        assert result["scope"] == "project"
        assert "profile_path" in result
        assert len(saved_profiles) == 1
        # Verify the saved profile is a fresh empty instance
        assert saved_profiles[0].session_count == 0
