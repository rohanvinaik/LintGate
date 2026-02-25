"""Tests for NSIL training data extractors."""

import pytest

from lintgate.nsil.training_data import (
    ExtractionDiagnostics,
    TrainingExample,
    extract_from_constraint_outcomes,
    extract_from_controlplane_traces,
    extract_from_prediction_log,
    extract_from_ship_reports,
    extract_training_examples,
)


def test_training_example_defaults():
    """Test TrainingExample has correct defaults."""
    ex = TrainingExample()
    assert ex.prompt == ""
    assert ex.completion == ""
    assert ex.reward == 0.0
    assert ex.labels == ()
    assert ex.source == ""


def test_training_example_frozen():
    """Test TrainingExample is frozen."""
    ex = TrainingExample(prompt="test")
    with pytest.raises(AttributeError):
        ex.prompt = "other"  # type: ignore


def test_extraction_diagnostics():
    """Test ExtractionDiagnostics."""
    d = ExtractionDiagnostics()
    assert d.total_records == 0
    assert d.extracted_count == 0

    d.total_records = 10
    d.extracted_count = 8
    d.skipped_invalid = 1
    d.skipped_empty = 1

    assert d.to_dict() == {
        "total_records": 10,
        "extracted": 8,
        "skipped_invalid": 1,
        "skipped_empty": 1,
    }


def test_extract_from_controlplane_traces_empty():
    """Test empty traces returns empty list."""
    examples, diagnostics = extract_from_controlplane_traces([])
    assert examples == []
    assert diagnostics.extracted_count == 0
    assert diagnostics.total_records == 0


def test_extract_from_controlplane_traces_dict():
    """Test extraction from dict traces."""
    traces = [
        {"prompt": "test prompt", "completion": "test completion", "run_id": "run1"},
    ]
    examples, diagnostics = extract_from_controlplane_traces(traces)

    assert diagnostics.extracted_count == 1
    assert len(examples) == 1
    assert examples[0].prompt == "test prompt"
    assert examples[0].completion == "test completion"
    assert examples[0].source == "run1"


def test_extract_from_controlplane_traces_skips_invalid():
    """Test invalid records are skipped."""
    traces = [
        {},  # Empty - skipped
        "not a valid trace",  # Invalid - skipped
    ]
    examples, diagnostics = extract_from_controlplane_traces(traces)
    assert diagnostics.skipped_empty == 1
    assert diagnostics.skipped_invalid == 1
    assert diagnostics.extracted_count == 0


def test_extract_from_controlplane_traces_deterministic():
    """Test deterministic ordering."""
    traces = [
        {"prompt": "b prompt", "completion": "comp", "run_id": "run2"},
        {"prompt": "a prompt", "completion": "comp", "run_id": "run1"},
    ]
    examples, _ = extract_from_controlplane_traces(traces)

    # Should be sorted by source
    assert examples[0].source == "run1"
    assert examples[1].source == "run2"


def test_extract_from_prediction_log_empty():
    """Test empty path returns empty list."""
    examples, diagnostics = extract_from_prediction_log("/nonexistent/path.json")
    assert examples == []
    assert diagnostics.total_records == 0


def test_extract_from_constraint_outcomes():
    """Test extraction from constraint outcomes."""
    outcomes = [
        {
            "constraint": "no-rm-rf",
            "violated": True,
            "context": "Running rm -rf /tmp",
            "repair_suggestion": "Use safer command",
        },
    ]
    examples, diagnostics = extract_from_constraint_outcomes(outcomes)

    assert diagnostics.extracted_count == 1
    assert len(examples) == 1
    assert examples[0].reward == 0.0  # violated = 0.0
    assert "constraint:no-rm-rf" in examples[0].labels
    assert "violated:true" in examples[0].labels


def test_extract_from_ship_reports():
    """Test extraction from ship reports."""
    reports = [
        {
            "passed": True,
            "run_id": "ship-001",
            "issue_count": 0,
            "checks": ["lint", "tests"],
        },
    ]
    examples, diagnostics = extract_from_ship_reports(reports)

    assert diagnostics.extracted_count == 1
    assert len(examples) == 1
    assert examples[0].reward == 1.0
    assert "ship:passed" in examples[0].labels
    assert examples[0].source == "ship-001"


def test_extract_from_ship_reports_failed():
    """Test failed ship reports."""
    reports = [
        {
            "passed": False,
            "run_id": "ship-002",
            "issue_count": 5,
        },
    ]
    examples, _ = extract_from_ship_reports(reports)

    assert examples[0].reward == 0.0
    assert "ship:failed" in examples[0].labels


def test_extract_training_examples_combines():
    """Test combining examples from multiple sources."""
    artifact_paths = {
        "controlplane": [
            {"prompt": "cp prompt", "completion": "cp completion", "run_id": "cp1"},
        ],
        "predictions": [],
        "constraints": [],
        "ship": [],
    }
    examples, diags = extract_training_examples(artifact_paths)

    # May have 1 or 2 depending on implementation details
    assert len(examples) >= 1
    assert "controlplane" in diags


def test_extract_training_examples_empty():
    """Test empty artifact paths."""
    examples, diags = extract_training_examples({})
    assert examples == []
    assert diags == {}


def test_training_example_labels_deterministic():
    """Test TrainingExample accepts labels."""
    ex = TrainingExample(
        prompt="test",
        completion="result",
        labels=("a", "b", "c"),
    )
    # Labels should be preserved as passed
    assert ex.labels == ("a", "b", "c")


def test_extract_from_constraint_outcomes_not_violated():
    """Test constraint not violated gives reward 1.0."""
    outcomes = [
        {
            "constraint": "no-rm-rf",
            "violated": False,
            "context": "Running ls",
        },
    ]
    examples, _ = extract_from_constraint_outcomes(outcomes)
    assert examples[0].reward == 1.0


def test_extract_from_controlplane_traces_with_coherence():
    """Test coherence state in labels."""
    traces = [
        {
            "prompt": "test",
            "completion": "result",
            "run_id": "run1",
            "coherence": {"state": "stable"},
        },
    ]
    examples, _ = extract_from_controlplane_traces(traces)
    assert "coherence:stable" in examples[0].labels


def test_extract_from_controlplane_traces_blocking():
    """Test blocking channel in labels."""
    traces = [
        {
            "prompt": "test",
            "completion": "result",
            "run_id": "run1",
            "channel_results": [
                {"blocking": True, "name": "lint"},
            ],
        },
    ]
    examples, _ = extract_from_controlplane_traces(traces)
    assert "blocking:true" in examples[0].labels


def test_extract_from_ship_reports_issue_count_label():
    """Test issue count is capped for stability."""
    reports = [
        {
            "passed": False,
            "run_id": "ship-003",
            "issue_count": 100,
        },
    ]
    examples, _ = extract_from_ship_reports(reports)
    # Should be capped at 5
    assert "issues:5" in examples[0].labels
