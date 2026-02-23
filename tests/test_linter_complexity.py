"""Tests for complexity_checker linter (radon CC + MI)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.complexity_checker import (
    ComplexityChecker,
    _cc_suggestions,
)
from lintgate.types import LinterContext

# ── _cc_suggestions ──────────────────────────────────────────────────


def test_cc_suggestions_extreme():
    s = _cc_suggestions("big_func", 45, 15)
    assert len(s) >= 2
    assert "extreme" in s[0].lower()


def test_cc_suggestions_very_high():
    s = _cc_suggestions("big_func", 28, 15)
    assert any("very high" in x.lower() for x in s)


def test_cc_suggestions_above_threshold():
    s = _cc_suggestions("func", 18, 15)
    assert any("extract" in x.lower() for x in s)


def test_cc_suggestions_always_has_guard_clause_hint():
    s = _cc_suggestions("func", 50, 15)
    assert any("early-return" in x.lower() for x in s)


# ── ComplexityChecker ────────────────────────────────────────────────


def _make_ctx(tmp_path, config=None):
    f = tmp_path / "mod.py"
    f.write_text("def foo(): pass\n")
    return LinterContext(
        files=[str(f)],
        project_root=str(tmp_path),
        strictness="normal",
        config=config or {},
    )


def test_checker_cc_above_threshold(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    cc_data = json.dumps(
        {
            str(tmp_path / "mod.py"): [
                {
                    "name": "complex_fn",
                    "complexity": 25,
                    "rank": "E",
                    "lineno": 1,
                    "type": "function",
                }
            ]
        }
    )
    mock_result = MagicMock(stdout=cc_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_cc(ctx))
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].kind == "complexity"


def test_checker_cc_extreme_is_blocking(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    cc_data = json.dumps(
        {"mod.py": [{"name": "fn", "complexity": 35, "rank": "F", "lineno": 1, "type": "function"}]}
    )
    mock_result = MagicMock(stdout=cc_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_cc(ctx))
    assert len(issues) == 1
    assert issues[0].severity == "blocking"


def test_checker_cc_below_threshold(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    cc_data = json.dumps(
        {"mod.py": [{"name": "fn", "complexity": 5, "rank": "A", "lineno": 1, "type": "function"}]}
    )
    mock_result = MagicMock(stdout=cc_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_cc(ctx))
    assert issues == []


def test_checker_cc_no_output(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mock_result = MagicMock(stdout="")
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_cc(ctx))
    assert issues == []


def test_checker_cc_bad_json(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mock_result = MagicMock(stdout="not json")
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_cc(ctx))
    assert issues == []


def test_checker_mi_below_threshold(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mi_data = json.dumps({"mod.py": {"mi": 5.0, "rank": "C"}})
    mock_result = MagicMock(stdout=mi_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_mi(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "maintainability"


def test_checker_mi_very_low_is_blocking(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mi_data = json.dumps({"mod.py": {"mi": 3.0, "rank": "C"}})
    mock_result = MagicMock(stdout=mi_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_mi(ctx))
    assert len(issues) == 1
    assert issues[0].severity == "blocking"


def test_checker_mi_above_threshold(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mi_data = json.dumps({"mod.py": {"mi": 50.0, "rank": "A"}})
    mock_result = MagicMock(stdout=mi_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_mi(ctx))
    assert issues == []


def test_checker_mi_no_output(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mock_result = MagicMock(stdout="")
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_mi(ctx))
    assert issues == []


def test_checker_mi_bad_json(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    mock_result = MagicMock(stdout="not json")
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_mi(ctx))
    assert issues == []


def test_checker_run_dispatches_both(tmp_path):
    ctx = _make_ctx(tmp_path)
    checker = ComplexityChecker()
    with (
        patch.object(checker, "_check_cc", return_value=iter([])),
        patch.object(checker, "_check_mi", return_value=iter([])),
    ):
        list(checker.run(ctx))


def test_checker_cc_custom_threshold(tmp_path):
    ctx = _make_ctx(tmp_path, config={"cc_threshold": 5})
    checker = ComplexityChecker()
    cc_data = json.dumps(
        {"mod.py": [{"name": "fn", "complexity": 8, "rank": "B", "lineno": 1, "type": "function"}]}
    )
    mock_result = MagicMock(stdout=cc_data)
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._check_cc(ctx))
    assert len(issues) == 1
