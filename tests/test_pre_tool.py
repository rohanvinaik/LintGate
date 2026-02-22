"""Tests for lintgate/hooks/pre_tool.py.

Core hook helper tests live in test_hook_helpers.py. This file provides
the standard test_<module>.py entry point for test channel discovery.
"""

from __future__ import annotations

from unittest.mock import patch

from lintgate.hooks.pre_tool import (
    _COMMIT_RE,
    _PUSH_RE,
    _WRITE_TOOLS,
    _check_theory_mode,
    _load_mode,
)

# ── Constants ────────────────────────────────────────────────────────────


def test_write_tools_contains_expected() -> None:
    assert "Write" in _WRITE_TOOLS
    assert "Edit" in _WRITE_TOOLS
    assert "NotebookEdit" in _WRITE_TOOLS


def test_push_re_matches_git_push() -> None:
    assert _PUSH_RE.search("git push origin main")
    assert not _PUSH_RE.search("git commit -m 'msg'")


def test_commit_re_matches_git_commit() -> None:
    assert _COMMIT_RE.search("git commit -m 'msg'")
    assert not _COMMIT_RE.search("git push origin main")


# ── _load_mode ───────────────────────────────────────────────────────────


def test_load_mode_returns_normal_on_import_error() -> None:
    with patch(
        "lintgate.hooks.pre_tool.get_or_create_session",
        side_effect=ImportError("no module"),
        create=True,
    ):
        # Force re-import path to hit the except branch
        result = _load_mode("/tmp/test")
    assert result == "normal"


def test_load_mode_returns_normal_on_exception() -> None:
    with patch(
        "lintgate.hooks.pre_tool.get_or_create_session",
        side_effect=RuntimeError("broken"),
        create=True,
    ):
        result = _load_mode("/tmp/test")
    assert result == "normal"


# ── _check_theory_mode ──────────────────────────────────────────────────


def test_theory_mode_advisory_on_write_tool() -> None:
    result = _check_theory_mode("theory", "Write")
    assert result  # Non-empty advisory string


def test_theory_mode_no_advisory_on_read_tool() -> None:
    result = _check_theory_mode("theory", "Read")
    assert result == ""


def test_normal_mode_no_advisory_on_write_tool() -> None:
    result = _check_theory_mode("normal", "Write")
    assert result == ""
