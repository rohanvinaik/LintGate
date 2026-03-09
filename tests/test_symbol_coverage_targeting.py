"""Tests for symbol coverage: git diff parsing, range overlap, target set building,
waivers, and waiver parsing.
"""

from __future__ import annotations

import textwrap
from datetime import date
from unittest.mock import patch

from lintgate.channels.symbol_coverage import (
    SymbolCoverageWaiver,
    SymbolSpan,
    _parse_waivers,
    _ranges_overlap,
    apply_waivers,
    build_target_set,
    get_changed_line_ranges,
)

# ── TestGetChangedLineRanges ─────────────────────────────────────────────


class TestGetChangedLineRanges:
    def test_normal_diff(self, tmp_path):
        diff_output = textwrap.dedent("""\
            diff --git a/mod.py b/mod.py
            --- a/mod.py
            +++ b/mod.py
            @@ -10,0 +11,3 @@
            +    new line 1
            +    new line 2
            +    new line 3
            @@ -20,2 +24,1 @@
            -    old line
            -    old line
            +    replaced
        """)
        with patch("lintgate.channels._target_building.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": diff_output,
                    "stderr": "",
                },
            )()
            ranges = get_changed_line_ranges(str(tmp_path / "mod.py"), str(tmp_path))
        assert ranges is not None
        assert range(11, 14) in ranges
        assert range(24, 25) in ranges

    def test_git_failure_returns_none(self, tmp_path):
        with patch("lintgate.channels._target_building.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R",
                (),
                {
                    "returncode": 128,
                    "stdout": "",
                    "stderr": "fatal: not a git repo",
                },
            )()
            result = get_changed_line_ranges(str(tmp_path / "mod.py"), str(tmp_path))
        assert result is None

    def test_timeout_returns_none(self, tmp_path):
        import subprocess as sp

        with patch("lintgate.channels._target_building.subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired("git", 10)
            result = get_changed_line_ranges(str(tmp_path / "mod.py"), str(tmp_path))
        assert result is None


# ── TestRangesOverlap ────────────────────────────────────────────────────


class TestRangesOverlap:
    def test_overlap(self):
        assert _ranges_overlap(range(1, 5), range(3, 7)) is True

    def test_no_overlap(self):
        assert _ranges_overlap(range(1, 3), range(5, 7)) is False

    def test_adjacent_no_overlap(self):
        assert _ranges_overlap(range(1, 3), range(3, 5)) is False

    def test_contained(self):
        assert _ranges_overlap(range(1, 10), range(3, 5)) is True


# ── TestBuildTargetSet ───────────────────────────────────────────────────


class TestBuildTargetSet:
    def _write_py(self, path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))

    def test_changed_only_mode(self, tmp_path):
        mod = tmp_path / "mod.py"
        self._write_py(
            mod,
            """\
            def unchanged():
                pass

            def changed():
                return 1
        """,
        )
        # Mock git diff to show changes only in lines 4-5 (the changed function)
        with patch("lintgate.channels._target_building.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(4, 6)]
            targets, unresolved = build_target_set([str(mod)], str(tmp_path), {"mode": "changed"})
        names = [t.name for t in targets]
        assert "changed" in names
        assert "unchanged" not in names
        assert unresolved == []

    def test_new_file_targets_all(self, tmp_path):
        mod = tmp_path / "new_mod.py"
        self._write_py(
            mod,
            """\
            def func_a():
                pass

            def func_b():
                return 1
        """,
        )
        # git diff returns None for new/untracked files
        with patch("lintgate.channels._target_building.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = None
            targets, _ = build_target_set([str(mod)], str(tmp_path), {"mode": "changed"})
        names = [t.name for t in targets]
        assert "func_a" in names
        assert "func_b" in names

    def test_required_symbols(self, tmp_path):
        mod = tmp_path / "pkg" / "mod.py"
        self._write_py(
            mod,
            """\
            def target_func():
                pass
        """,
        )
        targets, unresolved = build_target_set(
            [],
            str(tmp_path),
            {"mode": "changed", "required_symbols": ["pkg/mod.py::target_func"]},
        )
        assert len(targets) == 1
        assert targets[0].name == "target_func"
        assert unresolved == []

    def test_unresolved_required_symbols(self, tmp_path):
        targets, unresolved = build_target_set(
            [],
            str(tmp_path),
            {"mode": "changed", "required_symbols": ["nonexistent.py::func"]},
        )
        assert targets == []
        assert "nonexistent.py::func" in unresolved

    def test_unresolved_symbol_in_existing_file(self, tmp_path):
        mod = tmp_path / "mod.py"
        self._write_py(
            mod,
            """\
            def real_func():
                pass
        """,
        )
        targets, unresolved = build_target_set(
            [],
            str(tmp_path),
            {"mode": "changed", "required_symbols": ["mod.py::ghost_func"]},
        )
        assert targets == []
        assert "mod.py::ghost_func" in unresolved

    def test_invalid_required_symbol_format(self, tmp_path):
        targets, unresolved = build_target_set(
            [],
            str(tmp_path),
            {"mode": "changed", "required_symbols": ["no_separator"]},
        )
        assert "no_separator" in unresolved

    def test_non_python_files_skipped(self, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        with patch("lintgate.channels._target_building.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 2)]
            targets, _ = build_target_set([str(txt)], str(tmp_path), {"mode": "changed"})
        assert targets == []

    def test_diff_base_override(self, tmp_path):
        mod = tmp_path / "mod.py"
        self._write_py(
            mod,
            """\
            def func():
                pass
        """,
        )
        with patch("lintgate.channels._target_building.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            build_target_set(
                [str(mod)],
                str(tmp_path),
                {"mode": "changed", "diff_base": "origin/main"},
            )
            mock_diff.assert_called_once_with(str(mod), str(tmp_path), diff_base="origin/main")


# ── TestWaivers ──────────────────────────────────────────────────────────


class TestWaivers:
    def _make_span(self, key: str) -> SymbolSpan:
        return SymbolSpan(
            file="/src/mod.py",
            symbol_key=key,
            name=key.split("::")[-1],
            start_line=1,
            end_line=5,
            is_method=False,
            class_name=None,
        )

    def test_waiver_removes_target(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Tested via integration",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert filtered == []
        assert len(applied) == 1
        assert applied[0][0] == "mod.py::func"

    def test_waiver_no_match(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::other_func",
                reason="Not relevant",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert len(filtered) == 1
        assert applied == []

    def test_expired_waiver(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Old exemption",
                expires="2025-01-01",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert len(filtered) == 1  # Not removed — waiver expired
        assert applied == []
        assert len(expired) == 1

    def test_permanent_waiver(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Permanent exemption",
                expires=None,
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert filtered == []
        assert len(applied) == 1
        assert expired == []

    def test_glob_waiver_matches_multiple_symbols(self):
        targets = [
            self._make_span("pkg/checks/a.py::func_a"),
            self._make_span("pkg/checks/b.py::func_b"),
            self._make_span("pkg/other.py::func_c"),
        ]
        waivers = [
            SymbolCoverageWaiver(
                symbol="pkg/checks/*::*",
                reason="WIP module — tests pending.",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert len(filtered) == 1
        assert filtered[0].symbol_key == "pkg/other.py::func_c"
        assert len(applied) == 2

    def test_glob_waiver_no_false_positive(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="other/*::*",
                reason="Should not match.",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert len(filtered) == 1
        assert applied == []

    def test_future_expiry_still_active(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Not expired yet",
                expires="2026-12-31",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert filtered == []
        assert len(applied) == 1


# ── TestParseWaivers ─────────────────────────────────────────────────────


class TestParseWaivers:
    def test_valid_waiver(self):
        raw = [{"symbol": "mod.py::func", "reason": "Test reason"}]
        waivers = _parse_waivers(raw)
        assert len(waivers) == 1
        assert waivers[0].symbol == "mod.py::func"

    def test_missing_reason_rejected(self):
        raw = [{"symbol": "mod.py::func"}]
        waivers = _parse_waivers(raw)
        assert waivers == []

    def test_missing_symbol_rejected(self):
        raw = [{"reason": "No symbol"}]
        waivers = _parse_waivers(raw)
        assert waivers == []

    def test_not_a_list(self):
        assert _parse_waivers("not a list") == []
        assert _parse_waivers(None) == []
