"""Tests for lintgate/pattern_bank.py — anti-pattern tracking."""

from __future__ import annotations

from lintgate.pattern_bank import (
    _ALERT_THRESHOLD_RECENT_RUNS,
    _ALERT_THRESHOLD_SINGLE_RUN,
    _MAX_RUN_HISTORY,
    _RECENT_WINDOW,
    _load_bank,
    _project_hash,
    PatternAlert,
    update_pattern_bank,
)
from lintgate.types import LintIssue


# ── _project_hash ─────────────────────────────────────────────────


def test_project_hash_deterministic() -> None:
    h1 = _project_hash("/home/user/project")
    h2 = _project_hash("/home/user/project")
    assert h1 == h2


def test_project_hash_different_paths() -> None:
    h1 = _project_hash("/home/user/project_a")
    h2 = _project_hash("/home/user/project_b")
    assert h1 != h2


def test_project_hash_length() -> None:
    h = _project_hash("/some/path")
    assert len(h) == 16


# ── _load_bank ────────────────────────────────────────────────────


def test_load_bank_nonexistent_returns_empty(tmp_path: object) -> None:
    from pathlib import Path

    result = _load_bank(Path(str(tmp_path)) / "nonexistent.json")
    assert result == {"patterns": {}}


def test_load_bank_valid_json(tmp_path: object) -> None:
    import json
    from pathlib import Path

    bank_path = Path(str(tmp_path)) / "bank.json"
    data = {"patterns": {"ruff|F821": {"total_count": 5}}}
    bank_path.write_text(json.dumps(data))
    result = _load_bank(bank_path)
    assert "ruff|F821" in result["patterns"]


def test_load_bank_invalid_json(tmp_path: object) -> None:
    from pathlib import Path

    bank_path = Path(str(tmp_path)) / "bank.json"
    bank_path.write_text("not json {{{")
    result = _load_bank(bank_path)
    assert result == {"patterns": {}}


# ── update_pattern_bank ───────────────────────────────────────────


def test_update_pattern_bank_tracks_issues(tmp_path: object) -> None:
    issues = [
        LintIssue(linter="ruff", kind="F821", message="Undefined name", file="a.py"),
        LintIssue(linter="ruff", kind="F821", message="Undefined name", file="b.py"),
    ]
    from unittest.mock import patch

    with patch("lintgate.pattern_bank.PATTERN_BANK_DIR", tmp_path):
        result = update_pattern_bank(str(tmp_path), issues)
    assert "top_categories" in result
    assert len(result["top_categories"]) >= 1
    assert result["top_categories"][0]["kind"] == "F821"


def test_update_pattern_bank_no_issues(tmp_path: object) -> None:
    from unittest.mock import patch

    with patch("lintgate.pattern_bank.PATTERN_BANK_DIR", tmp_path):
        result = update_pattern_bank(str(tmp_path), [])
    assert result["alerted_patterns"] == []
    assert result["top_categories"] == []


def test_update_pattern_bank_single_run_alert(tmp_path: object) -> None:
    """When >=3 issues of same kind in one run, alert for single_run_volume."""
    issues = [
        LintIssue(linter="ruff", kind="E501", message="Line too long", file=f"f{i}.py")
        for i in range(_ALERT_THRESHOLD_SINGLE_RUN)
    ]
    from unittest.mock import patch

    with patch("lintgate.pattern_bank.PATTERN_BANK_DIR", tmp_path):
        result = update_pattern_bank(str(tmp_path), issues)
    alert_reasons = [a["alert_reason"] for a in result["alerted_patterns"]]
    assert "single_run_volume" in alert_reasons


# ── constants ─────────────────────────────────────────────────────


def test_constants_have_expected_values() -> None:
    assert _MAX_RUN_HISTORY == 10
    assert _ALERT_THRESHOLD_SINGLE_RUN == 3
    assert _ALERT_THRESHOLD_RECENT_RUNS == 3
    assert _RECENT_WINDOW == 5
