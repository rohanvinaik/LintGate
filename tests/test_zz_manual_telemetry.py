from lintgate.telemetry import compute_quality_economics_summary, compute_telemetry_summary


def test_telemetry_summary_basic():
    # Should safely handle an empty directory or standard directory
    summary = compute_telemetry_summary(".", period="7d")
    assert isinstance(summary, dict)


def test_quality_economics_summary():
    summary = compute_quality_economics_summary(".", period="7d")
    assert summary["period"] == "7d"
