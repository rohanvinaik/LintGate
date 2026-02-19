"""Tests for Phase 1B: Falsifiable Prediction Requirement."""

from __future__ import annotations

from lintgate.controlplane.behavior_compass import (
    BehaviorCompass,
    BehaviorHypothesis,
    Prediction,
    PredictionExpectation,
    _check_predictions,
    compute_prediction_accuracy,
    record_tool_event,
)

# ── PredictionExpectation ────────────────────────────────────────────


class TestPredictionExpectation:
    def test_defaults(self) -> None:
        exp = PredictionExpectation()
        assert exp.type == "exit_code"
        assert exp.value == 0
        assert exp.negate is False

    def test_exit_code_type(self) -> None:
        exp = PredictionExpectation(type="exit_code", value=0)
        d = exp.to_dict()
        assert d["type"] == "exit_code"
        assert d["value"] == 0

    def test_error_signature_type(self) -> None:
        exp = PredictionExpectation(type="error_signature", value="ModuleNotFoundError")
        d = exp.to_dict()
        assert d["type"] == "error_signature"
        assert d["value"] == "ModuleNotFoundError"

    def test_stdout_contains_type(self) -> None:
        exp = PredictionExpectation(type="stdout_contains", value="PASS")
        d = exp.to_dict()
        assert d["type"] == "stdout_contains"

    def test_roundtrip(self) -> None:
        exp = PredictionExpectation(type="stdout_contains", value="success", negate=True)
        d = exp.to_dict()
        restored = PredictionExpectation.from_dict(d)
        assert restored.type == "stdout_contains"
        assert restored.value == "success"
        assert restored.negate is True

    def test_from_dict_defaults(self) -> None:
        restored = PredictionExpectation.from_dict({})
        assert restored.type == "exit_code"
        assert restored.value == 0
        assert restored.negate is False


# ── Prediction ──────────────────────────────────────────────────────


class TestPrediction:
    def test_defaults(self) -> None:
        pred = Prediction()
        assert pred.prediction_id == ""
        assert pred.status == "pending"
        assert pred.checked_at_event is None
        assert pred.actual_outcome is None

    def test_roundtrip(self) -> None:
        exp = PredictionExpectation(type="exit_code", value=0)
        pred = Prediction(
            prediction_id="abc123",
            claim="This command should succeed",
            expected=exp,
            declared_at_event=10,
            declared_sig="git:status",
            linked_hypothesis_id="hyp1",
        )
        d = pred.to_dict()
        restored = Prediction.from_dict(d)
        assert restored.prediction_id == "abc123"
        assert restored.claim == "This command should succeed"
        assert restored.expected.type == "exit_code"
        assert restored.expected.value == 0
        assert restored.declared_at_event == 10
        assert restored.declared_sig == "git:status"
        assert restored.linked_hypothesis_id == "hyp1"
        assert restored.status == "pending"

    def test_from_dict_without_expected(self) -> None:
        """Missing expected field gets default PredictionExpectation."""
        d = {"prediction_id": "x", "claim": "test"}
        pred = Prediction.from_dict(d)
        assert pred.expected.type == "exit_code"
        assert pred.expected.value == 0


# ── BehaviorCompass Serialization ────────────────────────────────────


class TestBehaviorCompassPredictions:
    def test_default_empty(self) -> None:
        compass = BehaviorCompass()
        assert compass.pending_predictions == []
        assert compass.prediction_log == []

    def test_roundtrip_with_predictions(self) -> None:
        compass = BehaviorCompass()
        exp = PredictionExpectation(type="exit_code", value=1)
        pred = Prediction(
            prediction_id="p1",
            claim="Should fail",
            expected=exp,
            declared_at_event=5,
            declared_sig="make:build",
        )
        compass.pending_predictions.append(pred)
        compass.prediction_log.append(
            {
                "prediction_id": "p0",
                "status": "confirmed",
                "event": 4,
            }
        )

        d = compass.to_dict()
        restored = BehaviorCompass.from_dict(d)
        assert len(restored.pending_predictions) == 1
        assert restored.pending_predictions[0].prediction_id == "p1"
        assert restored.pending_predictions[0].expected.type == "exit_code"
        assert restored.pending_predictions[0].expected.value == 1
        assert len(restored.prediction_log) == 1
        assert restored.prediction_log[0]["status"] == "confirmed"

    def test_backward_compat_without_predictions(self) -> None:
        """Old compass data without prediction fields loads cleanly."""
        old_data = {
            "event_counter": 50,
            "action_history": [],
            "hypotheses": [],
            "approaches": [],
        }
        compass = BehaviorCompass.from_dict(old_data)
        assert compass.pending_predictions == []
        assert compass.prediction_log == []
        assert compass.event_counter == 50


# ── _check_predictions ───────────────────────────────────────────────


class TestCheckPredictions:
    def _make_compass(self, event_counter: int = 100) -> BehaviorCompass:
        compass = BehaviorCompass()
        compass.event_counter = event_counter
        return compass

    def _make_prediction(
        self,
        pred_type: str = "exit_code",
        value: str | int = 0,
        negate: bool = False,
        declared_sig: str = "git:status",
        declared_at_event: int = 95,
        linked_hypothesis_id: str | None = None,
    ) -> Prediction:
        return Prediction(
            prediction_id="test_pred",
            claim="Test prediction",
            expected=PredictionExpectation(type=pred_type, value=value, negate=negate),
            declared_at_event=declared_at_event,
            declared_sig=declared_sig,
            linked_hypothesis_id=linked_hypothesis_id,
        )

    def test_confirmed_exit_code(self) -> None:
        compass = self._make_compass()
        pred = self._make_prediction(pred_type="exit_code", value=0)
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="",
            cfg={},
        )
        assert compass.pending_predictions == []
        assert len(compass.prediction_log) == 1
        assert compass.prediction_log[0]["status"] == "confirmed"

    def test_falsified_exit_code(self) -> None:
        compass = self._make_compass()
        pred = self._make_prediction(pred_type="exit_code", value=0)
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=1,
            error_sig="fatal",
            output_str="error",
            cfg={},
        )
        assert len(compass.prediction_log) == 1
        assert compass.prediction_log[0]["status"] == "falsified"

    def test_stdout_contains_match(self) -> None:
        compass = self._make_compass()
        pred = self._make_prediction(pred_type="stdout_contains", value="PASS")
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="All tests PASS",
            cfg={},
        )
        assert compass.prediction_log[0]["status"] == "confirmed"

    def test_stdout_contains_no_match(self) -> None:
        compass = self._make_compass()
        pred = self._make_prediction(pred_type="stdout_contains", value="PASS")
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="All tests FAIL",
            cfg={},
        )
        assert compass.prediction_log[0]["status"] == "falsified"

    def test_error_signature_match(self) -> None:
        compass = self._make_compass()
        pred = self._make_prediction(
            pred_type="error_signature",
            value="ModuleNotFoundError",
        )
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=1,
            error_sig="ModuleNotFoundError: No module named 'foo'",
            output_str="",
            cfg={},
        )
        assert compass.prediction_log[0]["status"] == "confirmed"

    def test_negated_match(self) -> None:
        """Negate=True: matched becomes NOT matched."""
        compass = self._make_compass()
        pred = self._make_prediction(pred_type="exit_code", value=0, negate=True)
        compass.pending_predictions = [pred]

        # Exit code IS 0, but negate=True, so this should be falsified
        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="",
            cfg={},
        )
        assert compass.prediction_log[0]["status"] == "falsified"

    def test_negated_no_match(self) -> None:
        """Negate=True with no match → confirmed (we expected NOT this)."""
        compass = self._make_compass()
        pred = self._make_prediction(pred_type="exit_code", value=0, negate=True)
        compass.pending_predictions = [pred]

        # Exit code is 1 (not 0), negate=True → confirmed
        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=1,
            error_sig="",
            output_str="",
            cfg={},
        )
        assert compass.prediction_log[0]["status"] == "confirmed"

    def test_skips_non_bash_events(self) -> None:
        compass = self._make_compass()
        pred = self._make_prediction()
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Read",
            "",
            exit_code=None,
            error_sig="",
            output_str="",
            cfg={},
        )
        # Prediction should still be pending — not checked
        assert len(compass.pending_predictions) == 1
        assert compass.pending_predictions[0].status == "pending"
        assert len(compass.prediction_log) == 0

    def test_prediction_expires_after_threshold(self) -> None:
        compass = self._make_compass(event_counter=130)
        pred = self._make_prediction(declared_at_event=100)
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="",
            cfg={},
        )
        # Should be expired (130 - 100 = 30 > 20)
        assert compass.pending_predictions == []
        assert compass.prediction_log[0]["status"] == "expired"

    def test_prediction_not_expired_within_threshold(self) -> None:
        compass = self._make_compass(event_counter=115)
        pred = self._make_prediction(declared_at_event=100)
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="",
            cfg={},
        )
        # 115 - 100 = 15 <= 20, so not expired
        assert compass.prediction_log[0]["status"] in ("confirmed", "falsified")

    def test_different_command_sig_stays_pending(self) -> None:
        """Prediction for 'git' doesn't match 'npm' command."""
        compass = self._make_compass()
        pred = self._make_prediction(declared_sig="git:status")
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "npm:install",
            exit_code=0,
            error_sig="",
            output_str="",
            cfg={},
        )
        # Different sig prefix — should stay pending
        assert len(compass.pending_predictions) == 1
        assert compass.pending_predictions[0].status == "pending"

    def test_confirmed_prediction_strengthens_linked_hypothesis(self) -> None:
        compass = self._make_compass()
        hyp = BehaviorHypothesis(
            id="hyp1",
            claim="git status may fail",
            confidence=0.5,
        )
        compass.hypotheses = [hyp]

        pred = self._make_prediction(
            pred_type="exit_code",
            value=0,
            linked_hypothesis_id="hyp1",
        )
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=0,
            error_sig="",
            output_str="",
            cfg={},
        )
        assert compass.hypotheses[0].confidence > 0.5
        assert any("prediction confirmed" in e for e in compass.hypotheses[0].evidence_for)

    def test_falsified_prediction_weakens_linked_hypothesis(self) -> None:
        compass = self._make_compass()
        hyp = BehaviorHypothesis(
            id="hyp1",
            claim="git status should succeed",
            confidence=0.7,
        )
        compass.hypotheses = [hyp]

        pred = self._make_prediction(
            pred_type="exit_code",
            value=0,
            linked_hypothesis_id="hyp1",
        )
        compass.pending_predictions = [pred]

        _check_predictions(
            compass,
            "Bash",
            "git:status",
            exit_code=1,
            error_sig="fatal error",
            output_str="error",
            cfg={},
        )
        assert compass.hypotheses[0].confidence < 0.7
        assert any("prediction falsified" in e for e in compass.hypotheses[0].evidence_against)


# ── compute_prediction_accuracy ──────────────────────────────────────


class TestComputePredictionAccuracy:
    def test_none_with_no_predictions(self) -> None:
        compass = BehaviorCompass()
        assert compute_prediction_accuracy(compass) is None

    def test_none_with_less_than_5_predictions(self) -> None:
        compass = BehaviorCompass()
        compass.prediction_log = [
            {"status": "confirmed", "prediction_id": f"p{i}"} for i in range(4)
        ]
        assert compute_prediction_accuracy(compass) is None

    def test_accuracy_with_5_all_confirmed(self) -> None:
        compass = BehaviorCompass()
        compass.prediction_log = [
            {"status": "confirmed", "prediction_id": f"p{i}"} for i in range(5)
        ]
        assert compute_prediction_accuracy(compass) == 1.0

    def test_accuracy_with_5_all_falsified(self) -> None:
        compass = BehaviorCompass()
        compass.prediction_log = [
            {"status": "falsified", "prediction_id": f"p{i}"} for i in range(5)
        ]
        assert compute_prediction_accuracy(compass) == 0.0

    def test_accuracy_mixed(self) -> None:
        compass = BehaviorCompass()
        compass.prediction_log = [
            {"status": "confirmed", "prediction_id": "p0"},
            {"status": "confirmed", "prediction_id": "p1"},
            {"status": "falsified", "prediction_id": "p2"},
            {"status": "confirmed", "prediction_id": "p3"},
            {"status": "falsified", "prediction_id": "p4"},
        ]
        assert compute_prediction_accuracy(compass) == 0.6

    def test_expired_predictions_not_counted(self) -> None:
        compass = BehaviorCompass()
        compass.prediction_log = [
            {"status": "confirmed", "prediction_id": "p0"},
            {"status": "confirmed", "prediction_id": "p1"},
            {"status": "confirmed", "prediction_id": "p2"},
            {"status": "expired", "prediction_id": "p3"},
            {"status": "expired", "prediction_id": "p4"},
            {"status": "expired", "prediction_id": "p5"},
        ]
        # Only 3 confirmed/falsified — below threshold
        assert compute_prediction_accuracy(compass) is None


# ── record_tool_event integration ────────────────────────────────────


class TestRecordToolEventPredictions:
    def test_predictions_checked_on_bash_event(self) -> None:
        """Predictions are checked when record_tool_event is called with Bash."""
        compass = BehaviorCompass()
        compass.event_counter = 50
        exp = PredictionExpectation(type="exit_code", value=0)
        pred = Prediction(
            prediction_id="p1",
            claim="Test",
            expected=exp,
            declared_at_event=48,
            declared_sig="pytest:tests/",
        )
        compass.pending_predictions = [pred]

        record_tool_event(
            compass,
            "Bash",
            {"command": "pytest tests/"},
            "exit_code: 0\nAll tests passed",
            now=1000.0,
        )
        # Prediction should be resolved
        assert len(compass.pending_predictions) == 0
        assert len(compass.prediction_log) == 1
        assert compass.prediction_log[0]["status"] == "confirmed"

    def test_predictions_not_checked_on_read_event(self) -> None:
        """Non-Bash events don't trigger prediction checking."""
        compass = BehaviorCompass()
        compass.event_counter = 50
        pred = Prediction(
            prediction_id="p1",
            claim="Test",
            expected=PredictionExpectation(type="exit_code", value=0),
            declared_at_event=48,
            declared_sig="pytest",
        )
        compass.pending_predictions = [pred]

        record_tool_event(
            compass,
            "Read",
            "/path/to/file.py",
            "file contents here",
            now=1000.0,
        )
        # Prediction should still be pending
        assert len(compass.pending_predictions) == 1
        assert compass.pending_predictions[0].status == "pending"

    def test_stale_predictions_expire_during_event(self) -> None:
        """Predictions older than 20 events are expired during record_tool_event."""
        compass = BehaviorCompass()
        compass.event_counter = 75  # Will become 76 after event
        pred = Prediction(
            prediction_id="p1",
            claim="Old prediction",
            expected=PredictionExpectation(type="exit_code", value=0),
            declared_at_event=50,  # 76 - 50 = 26 > 20
            declared_sig="git",
        )
        compass.pending_predictions = [pred]

        record_tool_event(
            compass,
            "Bash",
            {"command": "git status"},
            "exit_code: 0\nOn branch main",
            now=1000.0,
        )
        assert compass.prediction_log[0]["status"] == "expired"


# ── Old snapshot fixture compatibility ───────────────────────────────


class TestOldSnapshotCompat:
    def test_old_compass_without_prediction_fields(self) -> None:
        """Pre-prediction BehaviorCompass dicts load cleanly."""
        old_data = {
            "event_counter": 42,
            "action_history": [{"tool": "Bash", "ts": 1000}],
            "hypotheses": [
                {
                    "id": "h1",
                    "claim": "test",
                    "confidence": 0.5,
                    "evidence_for": [],
                    "evidence_against": [],
                    "source": "command_failure",
                    "status": "active",
                }
            ],
            "approaches": [],
            "error_memory": {},
            "last_fired": {"approach_cycling": 30},
            "signal_fire_counts": {"approach_cycling": 2},
        }
        compass = BehaviorCompass.from_dict(old_data)
        assert compass.pending_predictions == []
        assert compass.prediction_log == []
        assert compass.event_counter == 42
        assert len(compass.hypotheses) == 1

    def test_old_session_snapshot_without_prediction_fields(self) -> None:
        """Pre-prediction SessionSnapshot loads cleanly."""
        from lintgate.controlplane.session_memory import SessionSnapshot

        old_data = {
            "run_id": "r1",
            "timestamp": 1000.0,
            "coherence_state": "stable",
        }
        snap = SessionSnapshot.from_dict(old_data)
        assert snap.prediction_accuracy is None
        assert snap.predictions_checked == 0
