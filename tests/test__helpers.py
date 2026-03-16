"""Tests for lintgate/renderers/_helpers.py — shared renderer helpers."""

from __future__ import annotations

from lintgate.compass import CompassAxis, CompassDirective, CompassState
from lintgate.renderers._helpers import (
    axis_summary,
    format_directives,
    project_name,
    truncate_lines,
)

# ── format_directives ─────────────────────────────────────────────


def test_format_directives_filters_by_kind() -> None:
    directives = [
        CompassDirective(kind="toward", text="Do X"),
        CompassDirective(kind="away", text="Avoid Y"),
        CompassDirective(kind="toward", text="Do Z"),
    ]
    result = format_directives(directives, "toward")
    assert result == ["Do X", "Do Z"]


def test_format_directives_empty_list() -> None:
    assert format_directives([], "toward") == []


def test_format_directives_no_match() -> None:
    directives = [CompassDirective(kind="toward", text="Do X")]
    assert format_directives(directives, "forbidden") == []


# ── axis_summary ──────────────────────────────────────────────────


def test_axis_summary_returns_summary() -> None:
    compass = CompassState(axes={"problem": CompassAxis(name="problem", summary="Quality checks.")})
    assert axis_summary(compass, "problem") == "Quality checks."


def test_axis_summary_missing_axis_returns_empty() -> None:
    compass = CompassState()
    assert axis_summary(compass, "problem") == ""


def test_axis_summary_empty_summary_returns_empty() -> None:
    compass = CompassState(axes={"problem": CompassAxis(name="problem", summary="")})
    assert axis_summary(compass, "problem") == ""


# ── project_name ──────────────────────────────────────────────────


def test_project_name_from_project_name_key() -> None:
    assert project_name({"project_name": "myproject"}) == "myproject"


def test_project_name_from_name_key() -> None:
    assert project_name({"name": "alt"}) == "alt"


def test_project_name_from_project_root() -> None:
    assert project_name({"project_root": "/home/user/coolproject"}) == "coolproject"


def test_project_name_fallback() -> None:
    assert project_name({}) == "project"


def test_project_name_prefers_project_name_over_name() -> None:
    assert project_name({"project_name": "first", "name": "second"}) == "first"


# ── truncate_lines ────────────────────────────────────────────────


def test_truncate_lines_within_budget() -> None:
    lines = ["short", "also short"]
    result = truncate_lines(lines, 100)
    assert result == lines


def test_truncate_lines_exceeds_budget() -> None:
    lines = ["a" * 100] * 10  # 10 lines of 100 chars each
    result = truncate_lines(lines, 50)  # 50 tokens = 200 chars
    assert len(result) < len(lines)
    assert len(result) >= 1


def test_truncate_lines_empty() -> None:
    assert truncate_lines([], 100) == []
