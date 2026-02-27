from unittest.mock import patch

from lintgate.telemetry import compute_telemetry_summary


def test_compute_telemetry_summary_no_data():
    """Verify summary returns zeros when no data exists."""
    with patch("lintgate.telemetry.METRICS_DIR") as mock_dir:
        mock_dir.exists.return_value = False
        summary = compute_telemetry_summary("/tmp/proj")
        assert summary["total_runs"] == 0
        assert summary["total_issues_found"] == 0


def test_compute_telemetry_summary_with_data():
    """Verify summary aggregates data correctly."""
    mock_entries = [
        {
            "event": "mcp_lint_run",
            "project": "/tmp/proj",
            "blocking_count": 1,
            "warning_count": 2,
            "info_count": 0,
            "files_count": 5,
            "duration_ms": 100,
        },
        {
            "event": "mcp_lint_run",
            "project": "/tmp/proj",
            "blocking_count": 0,
            "warning_count": 1,
            "info_count": 1,
            "files_count": 3,
            "duration_ms": 50,
        },
    ]

    with (
        patch("lintgate.telemetry._load_entries", return_value=mock_entries),
        patch(
            "lintgate.telemetry.compute_token_economics_summary", return_value={"has_data": False}
        ),
    ):
        summary = compute_telemetry_summary("/tmp/proj")
        assert summary["total_runs"] == 2
        assert summary["total_blocking_found"] == 1
        assert summary["total_warnings_found"] == 3
        assert summary["total_issues_found"] == 5
        assert summary["fix_rate"] == 0.5  # 1 of 2 runs had 0 blocking
