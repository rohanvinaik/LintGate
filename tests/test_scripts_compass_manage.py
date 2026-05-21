"""Behavioral tests for scripts/compass_manage.py.

Exercises cmd_* dispatchers by passing argparse.Namespace objects and
capturing stdout. The MCP layer in mcp_tools/compass_tools.py just shells
out to this script, so its subprocess-argv tests live in
tests/test_mcp_compass_tools.py.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from scripts.compass_manage import (
    _parse_answers,
    cmd_update,
)


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _load_emitted(capsys) -> dict:
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    envelope = json.loads(line)
    if "file" in envelope:
        with open(envelope["file"]) as f:
            return json.loads(f.read())
    return envelope


# ── _parse_answers ─────────────────────────────────────────────────────────


class TestParseAnswers:
    def test_empty(self) -> None:
        assert _parse_answers([]) == {}

    def test_single(self) -> None:
        assert _parse_answers(["solution:0=text here"]) == {"solution:0": "text here"}

    def test_multiple(self) -> None:
        assert _parse_answers(["a:0=one", "b:1=two"]) == {"a:0": "one", "b:1": "two"}

    def test_missing_equals_skipped(self) -> None:
        assert _parse_answers(["bad_entry"]) == {}

    def test_value_with_equals_preserved(self) -> None:
        assert _parse_answers(["a:0=x=y=z"]) == {"a:0": "x=y=z"}


# ── cmd_update next_actions logic ──────────────────────────────────────────


class TestCmdUpdateNextActions:
    def test_write_true_no_update_in_next_actions(self, tmp_path: Path, capsys) -> None:
        update_result = {
            "compass_hash": "x",
            "axes": {},
            "gap_report": {"interview_recommended": False},
            "inferred_claims": 0,
            "written": True,
        }
        with patch("scripts.compass_manage._impl_update", return_value=update_result):
            cmd_update(_ns(path=str(tmp_path), target=[], write=True))
        result = _load_emitted(capsys)
        tools_suggested = [a["tool"] for a in result["next_actions"]]
        assert "compass_update" not in tools_suggested
        assert "compass_interview" not in tools_suggested

    def test_write_false_suggests_rerun(self, tmp_path: Path, capsys) -> None:
        update_result = {
            "compass_hash": "x",
            "axes": {},
            "gap_report": {"interview_recommended": False},
            "inferred_claims": 0,
        }
        with patch("scripts.compass_manage._impl_update", return_value=update_result):
            cmd_update(_ns(path=str(tmp_path), target=[], write=False))
        result = _load_emitted(capsys)
        tools_suggested = [a["tool"] for a in result["next_actions"]]
        assert "compass_update" in tools_suggested

    def test_interview_recommended(self, tmp_path: Path, capsys) -> None:
        update_result = {
            "compass_hash": "x",
            "axes": {},
            "gap_report": {"interview_recommended": True},
            "inferred_claims": 0,
        }
        with patch("scripts.compass_manage._impl_update", return_value=update_result):
            cmd_update(_ns(path=str(tmp_path), target=[], write=True))
        result = _load_emitted(capsys)
        tools_suggested = [a["tool"] for a in result["next_actions"]]
        assert "compass_interview" in tools_suggested
