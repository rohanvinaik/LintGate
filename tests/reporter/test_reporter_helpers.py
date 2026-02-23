"""Coverage tests for helper functions in lintgate/controlplane/reporter.py."""

from __future__ import annotations

from lintgate.controlplane.reporter import (
    _BUDGET_BASE,
    _BUDGET_HARD_CAP,
    _BUDGET_PER_BLOCKING,
    _BUDGET_PER_INFO,
    _BUDGET_PER_REPAIR,
    _BUDGET_PER_WARNING,
    _estimate_tokens,
    _short_path,
)

# ── _estimate_tokens ─────────────────────────────────────────────────────


def test_estimate_tokens_empty() -> None:
    assert _estimate_tokens("") == 0


def test_estimate_tokens_short_string() -> None:
    # "hello" = 5 chars -> 5 // 4 = 1
    assert _estimate_tokens("hello") == 1


def test_estimate_tokens_longer_string() -> None:
    text = "a" * 100
    assert _estimate_tokens(text) == 25


# ── _short_path ──────────────────────────────────────────────────────────


def test_short_path_with_none() -> None:
    assert _short_path(None) == ""


def test_short_path_with_empty_string() -> None:
    assert _short_path("") == ""


def test_short_path_extracts_basename() -> None:
    assert _short_path("/home/user/project/foo.py") == "foo.py"


def test_short_path_bare_filename() -> None:
    assert _short_path("foo.py") == "foo.py"


# ── Module-level constants ───────────────────────────────────────────────


def test_budget_constants_are_positive() -> None:
    assert _BUDGET_BASE > 0
    assert _BUDGET_PER_BLOCKING > 0
    assert _BUDGET_PER_WARNING > 0
    assert _BUDGET_PER_INFO > 0
    assert _BUDGET_PER_REPAIR > 0
    assert _BUDGET_HARD_CAP > 0


def test_budget_ordering() -> None:
    """Blocking costs more than warning which costs more than info."""
    assert _BUDGET_PER_BLOCKING > _BUDGET_PER_WARNING > _BUDGET_PER_INFO
