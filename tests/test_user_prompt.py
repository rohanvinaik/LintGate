"""Tests for lintgate/hooks/user_prompt.py."""

from __future__ import annotations

from unittest.mock import patch

from lintgate.hooks.user_prompt import (
    _PRIMER_MAX_CHARS,
    _THEORY_KEYWORDS,
    _build_primer,
    _load_mode,
    handle,
)


# ── _THEORY_KEYWORDS ───────────────────────────────────────────────────


def test_theory_keywords_contains_expected():
    assert "theory" in _THEORY_KEYWORDS
    assert "compass" in _THEORY_KEYWORDS
    assert "why" in _THEORY_KEYWORDS
    assert "architecture" in _THEORY_KEYWORDS


def test_theory_keywords_is_frozenset():
    assert isinstance(_THEORY_KEYWORDS, frozenset)
    assert len(_THEORY_KEYWORDS) == 8


# ── _PRIMER_MAX_CHARS ──────────────────────────────────────────────────


def test_primer_max_chars():
    assert _PRIMER_MAX_CHARS == 480


# ── _load_mode ──────────────────────────────────────────────────────────


def test_load_mode_fallback_on_import_error():
    with patch(
        "lintgate.hooks.user_prompt.importlib",
        side_effect=ImportError,
        create=True,
    ):
        # The function catches all exceptions and returns "normal"
        result = _load_mode("/nonexistent")
    assert result == "normal"


def test_load_mode_returns_normal_on_exception():
    # Even with a valid path, if session_memory raises, we get "normal"
    with patch(
        "lintgate.controlplane.session_memory.get_or_create_session",
        side_effect=RuntimeError("no session"),
    ):
        result = _load_mode("/tmp")
    assert result == "normal"


# ── _build_primer ───────────────────────────────────────────────────────


def test_build_primer_returns_none_on_import_error():
    with patch(
        "lintgate.hooks.user_prompt.load_runtime_state",
        side_effect=ImportError,
        create=True,
    ):
        assert _build_primer("/nonexistent") is None


def test_build_primer_returns_none_on_no_state():
    with patch("lintgate.runtime_state.load_runtime_state", return_value=None):
        assert _build_primer("/tmp") is None


def test_build_primer_with_runtime_state():
    class FakeRuntime:
        mode = "habit"
        habit_score = 0.75
        active_files = ["/project/src/foo.py", "/project/src/bar.py"]
        blocking_issues = 2
        approach_failures = 0
        prediction_accuracy = 0.8
        coherence_state = "independent"

    with patch("lintgate.runtime_state.load_runtime_state", return_value=FakeRuntime()):
        primer = _build_primer("/project")
    assert primer is not None
    assert "Mode: habit (75%)" in primer
    assert "foo.py" in primer
    assert "BLOCKING: 2" in primer


def test_build_primer_approach_failure_warning():
    class FakeRuntime:
        mode = "normal"
        habit_score = 0
        active_files = []
        blocking_issues = 0
        approach_failures = 3
        prediction_accuracy = -1
        coherence_state = "independent"

    with patch("lintgate.runtime_state.load_runtime_state", return_value=FakeRuntime()):
        primer = _build_primer("/project")
    assert "3 failed approaches" in primer


# ── handle ──────────────────────────────────────────────────────────────


def test_handle_returns_continue():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value=None):
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="normal"):
            result = handle({"cwd": "/tmp", "userMessage": "hello"})
    assert result["continue"] is True


def test_handle_normal_mode_no_theory():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value=None):
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="normal"):
            result = handle({"cwd": "/tmp", "userMessage": "fix the bug"})
    # normal mode + no theory keywords => empty system message
    assert result["systemMessage"] == ""


def test_handle_theory_keyword_legacy():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value=None):
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="theory"):
            result = handle({"cwd": "/tmp", "userMessage": "why does this happen?"})
    assert "theory-relevant" in result["systemMessage"]
    assert "Mode: theory" in result["systemMessage"]


def test_handle_nonstandard_mode_legacy():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value=None):
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="habit"):
            result = handle({"cwd": "/tmp", "userMessage": "do the thing"})
    assert "Mode: habit" in result["systemMessage"]


def test_handle_enhanced_primer():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value="Mode: normal"):
        result = handle({"cwd": "/tmp", "userMessage": "hello"})
    assert result["systemMessage"] == "[LG] Mode: normal"


def test_handle_enhanced_primer_with_theory():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value="Mode: normal"):
        result = handle({"cwd": "/tmp", "userMessage": "explain the architecture"})
    assert "(theory-relevant prompt)" in result["systemMessage"]


def test_handle_dict_user_message():
    with patch("lintgate.hooks.user_prompt._build_primer", return_value=None):
        with patch("lintgate.hooks.user_prompt._load_mode", return_value="normal"):
            result = handle({"cwd": "/tmp", "userMessage": {"content": "why this?"}})
    # dict message is handled — "why" is a theory keyword
    assert result["continue"] is True
