"""Tests for lintgate.nsil.training_data module.

Covers: TrainingExample, ExtractionDiagnostics, extraction functions,
reward computation, curriculum ordering.
"""

import json
import math

import pytest

from lintgate.nsil.training_data import (
    CATEGORY_FAILURE_RATES,
    CURRECT_BUCKET,
    MULTI_STEP_BUCKET,
    OPTIMIZATION_BUCKET,
    ExtractionDiagnostics,
    TrainingExample,
    compute_combined_reward,
    compute_contract_adherence_reward,
    compute_cost_to_green_proxy,
    compute_difficulty_score,
    compute_prediction_accuracy_reward,
    extract_from_constraint_outcomes,
    extract_from_controlplane_traces,
    extract_from_prediction_log,
    extract_from_session,
    extract_from_ship_reports,
    extract_training_examples,
    get_curriculum_stage,
    order_by_curriculum,
)

# ── TrainingExample ──────────────────────────────────────────────────────


class TestTrainingExample:
    def test_defaults(self):
        ex = TrainingExample()
        assert ex.prompt == ""
        assert ex.completion == ""
        assert ex.reward == 0.0
        assert ex.labels == ()
        assert ex.source == ""

    def test_custom_fields(self):
        ex = TrainingExample(
            prompt="hello",
            completion="world",
            reward=0.8,
            labels=("a", "b"),
            source="test",
        )
        assert ex.prompt == "hello"
        assert ex.completion == "world"
        assert ex.reward == 0.8
        assert ex.labels == ("a", "b")
        assert ex.source == "test"

    def test_frozen(self):
        ex = TrainingExample(prompt="x")
        with pytest.raises(AttributeError):
            ex.prompt = "y"  # type: ignore[misc]

    def test_equality(self):
        a = TrainingExample(prompt="p", completion="c")
        b = TrainingExample(prompt="p", completion="c")
        assert a == b

    def test_inequality(self):
        a = TrainingExample(prompt="p1")
        b = TrainingExample(prompt="p2")
        assert a != b


# ── ExtractionDiagnostics ────────────────────────────────────────────────


class TestExtractionDiagnostics:
    def test_defaults(self):
        d = ExtractionDiagnostics()
        assert d.total_records == 0
        assert d.extracted_count == 0
        assert d.skipped_invalid == 0
        assert d.skipped_empty == 0

    def test_to_dict(self):
        d = ExtractionDiagnostics(
            total_records=10,
            extracted_count=7,
            skipped_invalid=2,
            skipped_empty=1,
        )
        result = d.to_dict()
        assert result == {
            "total_records": 10,
            "extracted": 7,
            "skipped_invalid": 2,
            "skipped_empty": 1,
        }

    def test_to_dict_key_name_extracted(self):
        """to_dict uses 'extracted' not 'extracted_count' as key."""
        d = ExtractionDiagnostics(extracted_count=3)
        assert "extracted" in d.to_dict()
        assert "extracted_count" not in d.to_dict()

    def test_mutable(self):
        d = ExtractionDiagnostics()
        d.total_records = 5
        d.extracted_count = 3
        assert d.total_records == 5
        assert d.extracted_count == 3


# ── extract_from_session ─────────────────────────────────────────────────


class TestExtractFromSession:
    def test_empty_session(self):
        examples, diag = extract_from_session({})
        assert examples == []
        assert diag.total_records == 0

    def test_empty_snapshots(self):
        examples, diag = extract_from_session({"snapshots": []})
        assert examples == []
        assert diag.total_records == 0

    def test_single_snapshot_skipped(self):
        """First snapshot is always skipped (no previous nudge)."""
        session = {"snapshots": [{"disposition": "do X", "command_signature": "ls"}]}
        examples, diag = extract_from_session(session)
        assert examples == []
        assert diag.total_records == 1

    def test_two_snapshots_with_nudge_followed(self):
        session = {
            "snapshots": [
                {"disposition": "run tests"},
                {
                    "command_signature": "pytest",
                    "compliance_outcome": "followed",
                    "run_id": "r1",
                },
            ]
        }
        examples, diag = extract_from_session(session)
        assert len(examples) == 1
        assert examples[0].prompt == "Nudge: run tests"
        assert examples[0].completion == "pytest"
        assert examples[0].reward == 1.0
        assert "outcome:followed" in examples[0].labels
        assert examples[0].source == "snapshot:r1"
        assert diag.extracted_count == 1

    def test_compliance_outcome_ignored(self):
        session = {
            "snapshots": [
                {"disposition": "lint first"},
                {"command_signature": "git push", "compliance_outcome": "ignored"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples[0].reward == 0.0
        assert "outcome:ignored" in examples[0].labels

    def test_compliance_outcome_overridden(self):
        session = {
            "snapshots": [
                {"disposition": "check types"},
                {"command_signature": "mypy --ignore", "compliance_outcome": "overridden"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples[0].reward == 0.2

    def test_no_compliance_outcome_neutral_reward(self):
        session = {
            "snapshots": [
                {"disposition": "run lint"},
                {"command_signature": "ruff check"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples[0].reward == 0.5

    def test_nudge_from_last_nudge_dict(self):
        session = {
            "snapshots": [
                {"last_nudge": {"message": "fix imports"}},
                {"command_signature": "isort .", "run_id": "r2"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert len(examples) == 1
        assert examples[0].prompt == "Nudge: fix imports"

    def test_no_nudge_skips(self):
        session = {
            "snapshots": [
                {},  # no disposition or last_nudge
                {"command_signature": "echo hi"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples == []

    def test_no_action_skipped_empty(self):
        session = {
            "snapshots": [
                {"disposition": "do something"},
                {},  # no command_signature or action_type
            ]
        }
        examples, diag = extract_from_session(session)
        assert examples == []
        assert diag.skipped_empty == 1

    def test_action_from_action_type(self):
        session = {
            "snapshots": [
                {"disposition": "check"},
                {"action_type": "file_edit", "run_id": "r3"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples[0].completion == "file_edit"

    def test_run_id_fallback_to_index(self):
        session = {
            "snapshots": [
                {"disposition": "check"},
                {"command_signature": "echo"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples[0].source == "snapshot:1"

    def test_multiple_snapshots(self):
        session = {
            "snapshots": [
                {"disposition": "step1"},
                {"command_signature": "cmd1", "disposition": "step2", "run_id": "a"},
                {"command_signature": "cmd2", "run_id": "b"},
            ]
        }
        examples, diag = extract_from_session(session)
        assert len(examples) == 2
        assert diag.extracted_count == 2
        assert diag.total_records == 3

    def test_last_nudge_non_dict_ignored(self):
        """When last_nudge is not a dict, it should not provide a nudge."""
        session = {
            "snapshots": [
                {"last_nudge": "just a string"},
                {"command_signature": "echo"},
            ]
        }
        examples, _ = extract_from_session(session)
        assert examples == []


# ── extract_from_controlplane_traces ─────────────────────────────────────


class TestExtractFromControlplaneTraces:
    def test_empty_traces(self):
        examples, diag = extract_from_controlplane_traces([])
        assert examples == []
        assert diag.total_records == 0

    def test_dict_trace_with_prompt_completion(self):
        traces = [{"prompt": "fix bug", "completion": "applied patch", "run_id": "t1"}]
        examples, diag = extract_from_controlplane_traces(traces)
        assert len(examples) == 1
        assert examples[0].prompt == "fix bug"
        assert examples[0].completion == "applied patch"
        assert examples[0].source == "t1"
        assert diag.extracted_count == 1

    def test_nested_prompt_completion(self):
        traces = [
            {
                "event": {"prompt": "nested prompt"},
                "response": {"content": "nested completion"},
            }
        ]
        examples, _ = extract_from_controlplane_traces(traces)
        assert examples[0].prompt == "nested prompt"
        assert examples[0].completion == "nested completion"

    def test_coherence_label(self):
        traces = [
            {
                "prompt": "p",
                "completion": "c",
                "coherence": {"state": "aligned"},
            }
        ]
        examples, _ = extract_from_controlplane_traces(traces)
        assert "coherence:aligned" in examples[0].labels

    def test_blocking_label(self):
        traces = [
            {
                "prompt": "p",
                "completion": "c",
                "channel_results": [{"blocking": True}],
            }
        ]
        examples, _ = extract_from_controlplane_traces(traces)
        assert "blocking:true" in examples[0].labels

    def test_non_blocking_no_label(self):
        traces = [
            {
                "prompt": "p",
                "completion": "c",
                "channel_results": [{"blocking": False}],
            }
        ]
        examples, _ = extract_from_controlplane_traces(traces)
        assert "blocking:true" not in examples[0].labels

    def test_empty_prompt_and_completion_skipped(self):
        traces = [{"run_id": "t2"}]  # no prompt or completion
        examples, diag = extract_from_controlplane_traces(traces)
        assert examples == []
        assert diag.skipped_empty == 1

    def test_file_path_trace_valid(self, tmp_path):
        trace_file = tmp_path / "trace.json"
        trace_file.write_text(json.dumps({"prompt": "from file", "completion": "ok"}))
        examples, _diag = extract_from_controlplane_traces([str(trace_file)])
        assert len(examples) == 1
        assert examples[0].prompt == "from file"

    def test_file_path_nonexistent(self, tmp_path):
        examples, diag = extract_from_controlplane_traces([str(tmp_path / "missing.json")])
        assert examples == []
        assert diag.skipped_empty == 1

    def test_file_path_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all")
        examples, diag = extract_from_controlplane_traces([str(bad_file)])
        assert examples == []
        assert diag.skipped_invalid == 1

    def test_deterministic_ordering_by_source(self):
        traces = [
            {"prompt": "b", "completion": "x", "run_id": "z"},
            {"prompt": "a", "completion": "y", "run_id": "a"},
        ]
        examples, _ = extract_from_controlplane_traces(traces)
        assert examples[0].source == "a"
        assert examples[1].source == "z"

    def test_reward_passthrough(self):
        traces = [{"prompt": "p", "completion": "c", "reward": 0.75}]
        examples, _ = extract_from_controlplane_traces(traces)
        assert examples[0].reward == 0.75

    def test_reward_default_zero(self):
        traces = [{"prompt": "p", "completion": "c"}]
        examples, _ = extract_from_controlplane_traces(traces)
        assert examples[0].reward == 0.0

    def test_source_fallback_to_controlplane(self):
        traces = [{"prompt": "p", "completion": "c"}]
        examples, _ = extract_from_controlplane_traces(traces)
        assert examples[0].source == "controlplane"

    def test_coherence_non_dict_ignored(self):
        traces = [{"prompt": "p", "completion": "c", "coherence": "not a dict"}]
        examples, _ = extract_from_controlplane_traces(traces)
        assert all("coherence:" not in lb for lb in examples[0].labels)

    def test_channel_results_non_list_ignored(self):
        traces = [{"prompt": "p", "completion": "c", "channel_results": "bad"}]
        examples, _ = extract_from_controlplane_traces(traces)
        assert "blocking:true" not in examples[0].labels


# ── extract_from_prediction_log ──────────────────────────────────────────


class TestExtractFromPredictionLog:
    def test_nonexistent_file(self, tmp_path):
        examples, diag = extract_from_prediction_log(str(tmp_path / "no.jsonl"))
        assert examples == []
        assert diag.total_records == 0

    def test_empty_file(self, tmp_path):
        log = tmp_path / "empty.jsonl"
        log.write_text("")
        examples, diag = extract_from_prediction_log(str(log))
        assert examples == []

    def test_single_correct_prediction(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(
            json.dumps({"input": "what is 2+2", "prediction": "4", "correct": True, "model": "m1"})
        )
        examples, diag = extract_from_prediction_log(str(log))
        assert len(examples) == 1
        assert examples[0].prompt == "what is 2+2"
        assert examples[0].completion == "4"
        assert examples[0].reward == 1.0
        assert "accuracy:correct" in examples[0].labels
        assert examples[0].source == "m1"
        assert diag.extracted_count == 1

    def test_incorrect_prediction(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(json.dumps({"input": "q", "prediction": "wrong", "correct": False}))
        examples, _ = extract_from_prediction_log(str(log))
        assert examples[0].reward == 0.0
        assert "accuracy:incorrect" in examples[0].labels

    def test_unknown_correctness_neutral(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(json.dumps({"input": "q", "prediction": "maybe"}))
        examples, _ = extract_from_prediction_log(str(log))
        assert examples[0].reward == 0.5

    def test_confidence_label(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(json.dumps({"input": "q", "prediction": "a", "confidence": 0.9}))
        examples, _ = extract_from_prediction_log(str(log))
        assert "confidence:0.9" in examples[0].labels

    def test_prompt_field_fallback(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(json.dumps({"prompt": "alt prompt", "output": "alt out"}))
        examples, _ = extract_from_prediction_log(str(log))
        assert examples[0].prompt == "alt prompt"
        assert examples[0].completion == "alt out"

    def test_missing_prompt_skipped(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(json.dumps({"prediction": "orphan"}))
        examples, diag = extract_from_prediction_log(str(log))
        assert examples == []
        assert diag.skipped_empty == 1

    def test_invalid_json_line_skipped(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text("not json\n" + json.dumps({"input": "q", "prediction": "a"}))
        examples, diag = extract_from_prediction_log(str(log))
        assert len(examples) == 1
        assert diag.skipped_invalid == 1

    def test_multiple_lines(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        lines = [
            json.dumps({"input": "q1", "prediction": "a1", "model": "b"}),
            json.dumps({"input": "q2", "prediction": "a2", "model": "a"}),
        ]
        log.write_text("\n".join(lines))
        examples, diag = extract_from_prediction_log(str(log))
        assert len(examples) == 2
        assert diag.extracted_count == 2
        # Sorted by source
        assert examples[0].source == "a"
        assert examples[1].source == "b"

    def test_blank_lines_ignored(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text("\n\n" + json.dumps({"input": "q", "prediction": "a"}) + "\n\n")
        examples, _ = extract_from_prediction_log(str(log))
        assert len(examples) == 1

    def test_source_default_prediction(self, tmp_path):
        log = tmp_path / "pred.jsonl"
        log.write_text(json.dumps({"input": "q", "prediction": "a"}))
        examples, _ = extract_from_prediction_log(str(log))
        assert examples[0].source == "prediction"


# ── extract_from_constraint_outcomes ─────────────────────────────────────


class TestExtractFromConstraintOutcomes:
    def test_empty_outcomes(self):
        examples, diag = extract_from_constraint_outcomes([])
        assert examples == []
        assert diag.total_records == 0

    def test_dict_violated_outcome(self):
        outcomes = [
            {
                "constraint": "no_force_push",
                "violated": True,
                "context": "git push --force",
                "repair_suggestion": "remove --force",
            }
        ]
        examples, diag = extract_from_constraint_outcomes(outcomes)
        assert len(examples) == 1
        assert examples[0].prompt == "git push --force"
        assert examples[0].completion == "remove --force"
        assert examples[0].reward == 0.0
        assert "violated:true" in examples[0].labels
        assert "repair:proposed" in examples[0].labels
        assert "constraint:no_force_push" in examples[0].labels
        assert diag.extracted_count == 1

    def test_dict_satisfied_outcome(self):
        outcomes = [
            {
                "constraint": "lint_pass",
                "violated": False,
                "context": "ruff check passed",
            }
        ]
        examples, _ = extract_from_constraint_outcomes(outcomes)
        assert examples[0].reward == 1.0
        assert examples[0].completion == "constraint satisfied"
        assert "violated:false" in examples[0].labels

    def test_missing_context_skipped(self):
        outcomes = [{"constraint": "c", "violated": False}]
        examples, diag = extract_from_constraint_outcomes(outcomes)
        assert examples == []
        assert diag.skipped_empty == 1

    def test_file_path_outcome(self, tmp_path):
        f = tmp_path / "outcome.json"
        f.write_text(
            json.dumps(
                {
                    "constraint": "test_pass",
                    "violated": False,
                    "context": "all tests pass",
                }
            )
        )
        examples, diag = extract_from_constraint_outcomes([str(f)])
        assert len(examples) == 1
        assert diag.extracted_count == 1

    def test_file_path_nonexistent(self, tmp_path):
        examples, diag = extract_from_constraint_outcomes([str(tmp_path / "missing.json")])
        assert examples == []
        assert diag.skipped_empty == 1

    def test_file_path_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{{{")
        examples, diag = extract_from_constraint_outcomes([str(f)])
        assert examples == []
        assert diag.skipped_invalid == 1

    def test_non_str_non_dict_skipped(self):
        examples, diag = extract_from_constraint_outcomes([42])  # type: ignore[list-item, arg-type]
        assert examples == []
        assert diag.skipped_invalid == 1

    def test_source_truncated_to_20_chars(self):
        outcomes = [
            {
                "constraint": "a_very_long_constraint_name_here",
                "violated": False,
                "context": "ctx",
            }
        ]
        examples, _ = extract_from_constraint_outcomes(outcomes)
        # constraint[:20] = "a_very_long_constrai"
        assert examples[0].source == "constraint:a_very_long_constrai"

    def test_deterministic_ordering(self):
        outcomes = [
            {"constraint": "z", "violated": False, "context": "c1"},
            {"constraint": "a", "violated": False, "context": "c2"},
        ]
        examples, _ = extract_from_constraint_outcomes(outcomes)
        assert examples[0].source.endswith(":a")
        assert examples[1].source.endswith(":z")

    def test_no_repair_no_label(self):
        outcomes = [{"constraint": "c", "violated": True, "context": "ctx"}]
        examples, _ = extract_from_constraint_outcomes(outcomes)
        assert "repair:proposed" not in examples[0].labels


# ── extract_from_ship_reports ────────────────────────────────────────────


class TestExtractFromShipReports:
    def test_empty_reports(self):
        examples, diag = extract_from_ship_reports([])
        assert examples == []
        assert diag.total_records == 0

    def test_passed_report(self):
        reports = [{"passed": True, "checks": [], "issue_count": 0, "run_id": "s1"}]
        examples, _ = extract_from_ship_reports(reports)
        assert len(examples) == 1
        assert examples[0].reward == 1.0
        assert "ship:passed" in examples[0].labels
        assert examples[0].source == "s1"

    def test_failed_report(self):
        reports = [{"passed": False, "issue_count": 3, "run_id": "s2"}]
        examples, _ = extract_from_ship_reports(reports)
        assert examples[0].reward == 0.0
        assert "ship:failed" in examples[0].labels
        assert "issues:3" in examples[0].labels

    def test_issue_count_capped_at_5(self):
        reports = [{"passed": False, "issue_count": 100, "run_id": "s3"}]
        examples, _ = extract_from_ship_reports(reports)
        assert "issues:5" in examples[0].labels
        assert "issues:100" not in examples[0].labels

    def test_zero_issues_no_label(self):
        reports = [{"passed": True, "issue_count": 0, "run_id": "s4"}]
        examples, _ = extract_from_ship_reports(reports)
        assert all(not lb.startswith("issues:") for lb in examples[0].labels)

    def test_checks_string_labels(self):
        reports = [{"passed": True, "checks": ["lint", "test"], "run_id": "s5"}]
        examples, _ = extract_from_ship_reports(reports)
        assert "check:lint" in examples[0].labels
        assert "check:test" in examples[0].labels

    def test_checks_dict_labels(self):
        reports = [
            {
                "passed": True,
                "checks": [{"name": "lint", "status": "pass"}],
                "run_id": "s6",
            }
        ]
        examples, _ = extract_from_ship_reports(reports)
        assert "check:lint:pass" in examples[0].labels

    def test_checks_non_list_ignored(self):
        """Non-list checks produces no check labels."""
        reports = [{"passed": True, "checks": "not a list", "run_id": "s7"}]
        examples, _ = extract_from_ship_reports(reports)
        assert all(not lb.startswith("check:") for lb in examples[0].labels)

    def test_file_path_report(self, tmp_path):
        f = tmp_path / "report.json"
        f.write_text(json.dumps({"passed": True, "run_id": "f1"}))
        examples, _diag = extract_from_ship_reports([str(f)])
        assert len(examples) == 1

    def test_file_path_nonexistent(self, tmp_path):
        _examples, diag = extract_from_ship_reports([str(tmp_path / "nope.json")])
        assert diag.skipped_empty == 1

    def test_file_path_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("oops")
        _examples, diag = extract_from_ship_reports([str(f)])
        assert diag.skipped_invalid == 1

    def test_default_source_ship(self):
        reports = [{"passed": True}]
        examples, _ = extract_from_ship_reports(reports)
        assert examples[0].source == "ship"

    def test_prompt_format(self):
        reports = [{"passed": True, "run_id": "r1"}]
        examples, _ = extract_from_ship_reports(reports)
        assert examples[0].prompt == "Ship report for run r1"

    def test_completion_format(self):
        reports = [{"passed": False, "issue_count": 2}]
        examples, _ = extract_from_ship_reports(reports)
        assert examples[0].completion == "Passed: False, Issues: 2"


# ── extract_training_examples (combined) ─────────────────────────────────


class TestExtractTrainingExamples:
    def test_empty_artifact_paths(self):
        examples, diag_by_type = extract_training_examples({})
        assert examples == []
        assert diag_by_type == {}

    def test_unknown_artifact_type(self):
        examples, diag_by_type = extract_training_examples({"unknown_type": ["a", "b"]})
        assert examples == []
        assert diag_by_type["unknown_type"].total_records == 2

    def test_multiple_artifact_types(self, tmp_path):
        # Create a prediction log
        pred_log = tmp_path / "pred.jsonl"
        pred_log.write_text(json.dumps({"input": "q", "prediction": "a", "model": "m"}))

        # Ship report as file
        ship_file = tmp_path / "ship.json"
        ship_file.write_text(json.dumps({"passed": True, "run_id": "s1"}))

        examples, diag_by_type = extract_training_examples(
            {
                "predictions": [str(pred_log)],
                "ship": [str(ship_file)],
            }
        )
        assert "predictions" in diag_by_type
        assert "ship" in diag_by_type
        assert len(examples) == 2

    def test_combined_deterministic_sort(self, tmp_path):
        f1 = tmp_path / "ship_z.json"
        f1.write_text(json.dumps({"passed": True, "run_id": "z_ship"}))
        f2 = tmp_path / "ship_a.json"
        f2.write_text(json.dumps({"passed": False, "run_id": "a_ship"}))
        examples, _ = extract_training_examples({"ship": [str(f2), str(f1)]})
        assert examples[0].source == "a_ship"
        assert examples[1].source == "z_ship"

    def test_controlplane_dispatch(self, tmp_path):
        trace_file = tmp_path / "trace.json"
        trace_file.write_text(json.dumps({"prompt": "p", "completion": "c", "run_id": "cp1"}))
        examples, diag = extract_training_examples({"controlplane": [str(trace_file)]})
        assert len(examples) == 1
        assert diag["controlplane"].extracted_count == 1

    def test_constraints_dispatch(self, tmp_path):
        outcome_file = tmp_path / "constraint.json"
        outcome_file.write_text(
            json.dumps({"constraint": "c", "violated": False, "context": "ctx"})
        )
        examples, diag = extract_training_examples({"constraints": [str(outcome_file)]})
        assert len(examples) == 1
        assert diag["constraints"].extracted_count == 1

    def test_session_dispatch(self, tmp_path):
        session_file = tmp_path / "session.json"
        session_file.write_text(
            json.dumps(
                {
                    "snapshots": [
                        {"disposition": "nudge"},
                        {"command_signature": "cmd", "run_id": "s1"},
                    ]
                }
            )
        )
        examples, diag = extract_training_examples({"session": [str(session_file)]})
        assert len(examples) == 1
        assert diag["session"].extracted_count == 1


# ── Reward computation ───────────────────────────────────────────────────


class TestComputeContractAdherenceReward:
    def test_no_required_checks(self):
        assert compute_contract_adherence_reward(["a"], []) == 0.5

    def test_all_passed(self):
        assert compute_contract_adherence_reward(["a", "b"], ["a", "b"]) == 1.0

    def test_all_missing(self):
        result = compute_contract_adherence_reward([], ["a", "b"])
        assert result == -1.0

    def test_partial_missing(self):
        result = compute_contract_adherence_reward(["a"], ["a", "b"])
        assert result == pytest.approx(-0.5)

    def test_extra_passed_ignored(self):
        result = compute_contract_adherence_reward(["a", "b", "c"], ["a", "b"])
        assert result == 1.0

    def test_result_bounded_at_minus_one(self):
        result = compute_contract_adherence_reward([], ["a", "b", "c", "d"])
        assert result >= -1.0


class TestComputeCostToGreenProxy:
    def test_no_initial_violations(self):
        assert compute_cost_to_green_proxy(0, 0, 0) == 1.0

    def test_got_worse(self):
        assert compute_cost_to_green_proxy(5, 5, 3) == -0.5

    def test_got_worse_increased(self):
        assert compute_cost_to_green_proxy(5, 6, 3) == -0.5

    def test_full_fix_one_step(self):
        result = compute_cost_to_green_proxy(10, 0, 1)
        assert result == pytest.approx(1.0)

    def test_partial_fix(self):
        result = compute_cost_to_green_proxy(10, 5, 1)
        assert result == pytest.approx(0.5)

    def test_zero_effort_full_improvement(self):
        result = compute_cost_to_green_proxy(10, 0, 0)
        assert result == pytest.approx(1.0)

    def test_bounded_output(self):
        result = compute_cost_to_green_proxy(100, 50, 100)
        assert -1.0 <= result <= 1.0


class TestComputePredictionAccuracyReward:
    def test_no_predictions(self):
        assert compute_prediction_accuracy_reward(0, 0) == 0.5

    def test_all_correct(self):
        assert compute_prediction_accuracy_reward(10, 10) == pytest.approx(1.0)

    def test_all_wrong(self):
        assert compute_prediction_accuracy_reward(10, 0) == pytest.approx(-1.0)

    def test_half_correct(self):
        assert compute_prediction_accuracy_reward(10, 5) == pytest.approx(0.0)


class TestComputeCombinedReward:
    def test_default_weights(self):
        result = compute_combined_reward(
            contract_passed=["a"],
            contract_required=["a"],
            initial_violations=10,
            final_violations=0,
            effort_steps=1,
            predictions_made=10,
            predictions_correct=10,
        )
        # contract=1.0*0.4 + cost=1.0*0.4 + pred=1.0*0.2 = 1.0
        assert result == pytest.approx(1.0)

    def test_custom_weights(self):
        result = compute_combined_reward(
            contract_passed=[],
            contract_required=[],
            initial_violations=0,
            final_violations=0,
            effort_steps=0,
            weights={"contract": 1.0, "cost_to_green": 0.0, "prediction": 0.0},
        )
        # contract neutral 0.5 * 1.0 = 0.5
        assert result == pytest.approx(0.5)

    def test_bounded_output(self):
        result = compute_combined_reward(
            contract_passed=[],
            contract_required=["a", "b", "c"],
            initial_violations=10,
            final_violations=10,
            effort_steps=5,
            predictions_made=10,
            predictions_correct=0,
        )
        assert -1.0 <= result <= 1.0

    def test_all_bad_signals(self):
        result = compute_combined_reward(
            contract_passed=[],
            contract_required=["a"],
            initial_violations=5,
            final_violations=5,
            effort_steps=3,
            predictions_made=10,
            predictions_correct=0,
        )
        assert result < 0


# ── Curriculum ordering ──────────────────────────────────────────────────


class TestGetCurriculumStage:
    def test_default_compliance(self):
        ex = TrainingExample(prompt="p", labels=())
        assert get_curriculum_stage(ex) == CURRECT_BUCKET

    def test_multi_step_label(self):
        ex = TrainingExample(labels=("multi_step",))
        assert get_curriculum_stage(ex) == MULTI_STEP_BUCKET

    def test_multi_step_hyphen_label(self):
        ex = TrainingExample(labels=("multi-step",))
        assert get_curriculum_stage(ex) == MULTI_STEP_BUCKET

    def test_planning_label(self):
        ex = TrainingExample(labels=("planning",))
        assert get_curriculum_stage(ex) == MULTI_STEP_BUCKET

    def test_reasoning_label(self):
        ex = TrainingExample(labels=("reasoning",))
        assert get_curriculum_stage(ex) == MULTI_STEP_BUCKET

    def test_optimization_label(self):
        ex = TrainingExample(labels=("optimization",))
        assert get_curriculum_stage(ex) == OPTIMIZATION_BUCKET

    def test_tradeoff_label(self):
        ex = TrainingExample(labels=("tradeoff",))
        assert get_curriculum_stage(ex) == OPTIMIZATION_BUCKET

    def test_refactor_label(self):
        ex = TrainingExample(labels=("refactor",))
        assert get_curriculum_stage(ex) == OPTIMIZATION_BUCKET

    def test_improve_label(self):
        ex = TrainingExample(labels=("improve",))
        assert get_curriculum_stage(ex) == OPTIMIZATION_BUCKET

    def test_compliance_indicators(self):
        for label in ("violated", "blocking", "constraint", "lint", "test", "gate"):
            ex = TrainingExample(labels=(label,))
            assert get_curriculum_stage(ex) == CURRECT_BUCKET, f"Failed for {label}"

    def test_multi_step_takes_priority_over_optimization(self):
        ex = TrainingExample(labels=("multi_step", "optimization"))
        assert get_curriculum_stage(ex) == MULTI_STEP_BUCKET

    def test_optimization_takes_priority_over_compliance(self):
        ex = TrainingExample(labels=("optimization", "lint"))
        assert get_curriculum_stage(ex) == OPTIMIZATION_BUCKET


class TestComputeDifficultyScore:
    def test_basic_computation(self):
        ex = TrainingExample(
            completion="hello",  # len=5
            reward=0.5,
            labels=(),
        )
        failure_rate = CATEGORY_FAILURE_RATES[CURRECT_BUCKET]
        expected = (0.5 + math.log1p(5)) * failure_rate
        assert compute_difficulty_score(ex) == pytest.approx(expected)

    def test_empty_completion(self):
        ex = TrainingExample(completion="", reward=0.0, labels=())
        # log1p(0) = 0.0, so score = (0.0 + 0.0) * failure_rate = 0.0
        assert compute_difficulty_score(ex) == pytest.approx(0.0)

    def test_multi_step_higher_failure_rate(self):
        ex = TrainingExample(completion="x", reward=0.5, labels=("multi_step",))
        failure_rate = CATEGORY_FAILURE_RATES[MULTI_STEP_BUCKET]
        expected = (0.5 + math.log1p(1)) * failure_rate
        assert compute_difficulty_score(ex) == pytest.approx(expected)


class TestOrderByCurriculum:
    def test_empty_list(self):
        assert order_by_curriculum([]) == []

    def test_compliance_before_optimization(self):
        compliance = TrainingExample(labels=("lint",), source="a")
        optimization = TrainingExample(labels=("optimization",), source="b")
        result = order_by_curriculum([optimization, compliance])
        assert get_curriculum_stage(result[0]) == CURRECT_BUCKET
        assert get_curriculum_stage(result[1]) == OPTIMIZATION_BUCKET

    def test_optimization_before_multi_step(self):
        opt = TrainingExample(labels=("optimization",), source="a")
        multi = TrainingExample(labels=("multi_step",), source="b")
        result = order_by_curriculum([multi, opt])
        assert get_curriculum_stage(result[0]) == OPTIMIZATION_BUCKET
        assert get_curriculum_stage(result[1]) == MULTI_STEP_BUCKET

    def test_within_stage_sorted_by_difficulty(self):
        easy = TrainingExample(completion="x", reward=0.1, labels=("lint",), source="a")
        hard = TrainingExample(completion="x" * 100, reward=0.9, labels=("gate",), source="b")
        result = order_by_curriculum([hard, easy])
        # Both compliance, easy should come first (lower difficulty score)
        assert result[0] is easy
        assert result[1] is hard

    def test_deterministic_stable_sort(self):
        """Same examples always produce same order."""
        examples = [
            TrainingExample(labels=("multi_step",), source="c"),
            TrainingExample(labels=("lint",), source="a"),
            TrainingExample(labels=("optimization",), source="b"),
        ]
        result1 = order_by_curriculum(list(examples))
        result2 = order_by_curriculum(list(examples))
        assert result1 == result2

    def test_source_as_tiebreaker(self):
        a = TrainingExample(completion="", reward=0.0, labels=(), source="alpha")
        b = TrainingExample(completion="", reward=0.0, labels=(), source="beta")
        result = order_by_curriculum([b, a])
        assert result[0].source == "alpha"
        assert result[1].source == "beta"


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_bucket_names(self):
        assert CURRECT_BUCKET == "compliance"
        assert OPTIMIZATION_BUCKET == "optimization"
        assert MULTI_STEP_BUCKET == "multi_step"

    def test_category_failure_rates_keys(self):
        assert set(CATEGORY_FAILURE_RATES.keys()) == {
            CURRECT_BUCKET,
            OPTIMIZATION_BUCKET,
            MULTI_STEP_BUCKET,
        }

    def test_category_failure_rates_values_bounded(self):
        for rate in CATEGORY_FAILURE_RATES.values():
            assert 0.0 <= rate <= 1.0
