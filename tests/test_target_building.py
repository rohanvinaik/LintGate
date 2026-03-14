"""Tests for lintgate/channels/_target_building.py.

Covers all 8 functions with exact-value assertions targeting surviving
mutation categories: VALUE, SWAP, BOUNDARY, STATE, TYPE.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from lintgate.channels._target_building import (
    _add_overlapping_spans,
    _add_spans,
    _collect_changed_symbols,
    _find_span_by_key,
    _ranges_overlap,
    _resolve_required_symbols,
    build_target_set,
    get_changed_line_ranges,
)
from lintgate.channels._symbol_types import SymbolSpan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(
    symbol_key: str,
    start_line: int = 1,
    end_line: int = 10,
    *,
    file: str = "/repo/src/mod.py",
    name: str | None = None,
    is_method: bool = False,
    class_name: str | None = None,
) -> SymbolSpan:
    return SymbolSpan(
        file=file,
        symbol_key=symbol_key,
        name=name or symbol_key.split("::")[-1],
        start_line=start_line,
        end_line=end_line,
        is_method=is_method,
        class_name=class_name,
    )


# ===================================================================
# _ranges_overlap
# ===================================================================


class TestRangesOverlap:
    """Pure function, no dependencies."""

    def test_identical_ranges_overlap(self) -> None:
        assert _ranges_overlap(range(1, 5), range(1, 5)) is True

    def test_disjoint_ranges_do_not_overlap(self) -> None:
        assert _ranges_overlap(range(1, 5), range(5, 10)) is False

    def test_adjacent_ranges_do_not_overlap(self) -> None:
        # range(1,5) = {1,2,3,4}, range(5,8) = {5,6,7} -- no common element
        assert _ranges_overlap(range(1, 5), range(5, 8)) is False

    def test_partial_overlap(self) -> None:
        # range(1,5) = {1,2,3,4}, range(3,8) = {3,4,5,6,7}
        assert _ranges_overlap(range(1, 5), range(3, 8)) is True

    def test_one_contains_the_other(self) -> None:
        assert _ranges_overlap(range(2, 4), range(1, 10)) is True

    def test_overlap_is_symmetric(self) -> None:
        assert _ranges_overlap(range(3, 8), range(1, 5)) is True

    def test_empty_range_a_with_start_inside_b(self) -> None:
        # range(5, 5) is empty but implementation uses start/stop comparison:
        # 5 < 10 and 1 < 5 -> True. This is safe because callers never
        # produce zero-length ranges (count > 0 guard in get_changed_line_ranges).
        assert _ranges_overlap(range(5, 5), range(1, 10)) is True

    def test_empty_range_b_with_start_inside_a(self) -> None:
        assert _ranges_overlap(range(1, 10), range(5, 5)) is True

    def test_truly_disjoint_empty_range(self) -> None:
        # range(20, 20) vs range(1, 10): 20 < 10 is False -> no overlap
        assert _ranges_overlap(range(20, 20), range(1, 10)) is False

    def test_single_element_overlap(self) -> None:
        # range(3, 4) = {3}, range(3, 4) = {3}
        assert _ranges_overlap(range(3, 4), range(3, 4)) is True

    def test_single_element_disjoint(self) -> None:
        assert _ranges_overlap(range(3, 4), range(4, 5)) is False


# ===================================================================
# _add_spans
# ===================================================================


class TestAddSpans:
    def test_adds_all_spans_to_empty_targets(self) -> None:
        s1 = _make_span("mod.py::foo")
        s2 = _make_span("mod.py::bar")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _add_spans([s1, s2], targets, seen)

        assert len(targets) == 2
        assert targets[0].symbol_key == "mod.py::foo"
        assert targets[1].symbol_key == "mod.py::bar"
        assert seen == {"mod.py::foo", "mod.py::bar"}

    def test_deduplicates_by_symbol_key(self) -> None:
        s1 = _make_span("mod.py::foo")
        s2 = _make_span("mod.py::foo")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _add_spans([s1, s2], targets, seen)

        assert len(targets) == 1

    def test_skips_already_seen_key(self) -> None:
        s1 = _make_span("mod.py::foo")
        targets: list[SymbolSpan] = []
        seen: set[str] = {"mod.py::foo"}

        _add_spans([s1], targets, seen)

        assert len(targets) == 0

    def test_empty_input_leaves_targets_unchanged(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _add_spans([], targets, seen)

        assert targets == []
        assert seen == set()

    def test_mutates_both_targets_and_seen(self) -> None:
        s1 = _make_span("mod.py::baz")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _add_spans([s1], targets, seen)

        assert len(targets) == 1
        assert "mod.py::baz" in seen


# ===================================================================
# _add_overlapping_spans
# ===================================================================


class TestAddOverlappingSpans:
    def test_adds_span_overlapping_changed_range(self) -> None:
        span = _make_span("mod.py::foo", start_line=5, end_line=15)
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _add_overlapping_spans([span], [range(10, 20)], targets, seen)

        assert len(targets) == 1
        assert targets[0].symbol_key == "mod.py::foo"

    def test_skips_span_not_overlapping(self) -> None:
        span = _make_span("mod.py::foo", start_line=5, end_line=10)
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        # Changed range is after the span; span_range = range(5, 11), cr = range(20, 30)
        _add_overlapping_spans([span], [range(20, 30)], targets, seen)

        assert len(targets) == 0
        assert seen == set()

    def test_skips_already_seen_key(self) -> None:
        span = _make_span("mod.py::foo", start_line=5, end_line=15)
        targets: list[SymbolSpan] = []
        seen: set[str] = {"mod.py::foo"}

        _add_overlapping_spans([span], [range(10, 20)], targets, seen)

        assert len(targets) == 0

    def test_boundary_end_line_plus_one(self) -> None:
        """span_range uses end_line + 1, so a span ending at line 10 has span_range(5, 11)."""
        span = _make_span("mod.py::foo", start_line=5, end_line=10)
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        # Changed range starts at 10 -> span_range(5,11) overlaps range(10,15) since 5 < 15 and 10 < 11
        _add_overlapping_spans([span], [range(10, 15)], targets, seen)

        assert len(targets) == 1

    def test_boundary_no_overlap_at_end_line_plus_one(self) -> None:
        """span ending at line 10 has span_range(5, 11); changed range starting at 11 does NOT overlap."""
        span = _make_span("mod.py::foo", start_line=5, end_line=10)
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _add_overlapping_spans([span], [range(11, 15)], targets, seen)

        assert len(targets) == 0

    def test_multiple_changed_ranges_any_match(self) -> None:
        span = _make_span("mod.py::foo", start_line=5, end_line=10)
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        # First range doesn't overlap, second does
        _add_overlapping_spans(
            [span], [range(100, 200), range(8, 12)], targets, seen
        )

        assert len(targets) == 1


# ===================================================================
# get_changed_line_ranges
# ===================================================================


class TestGetChangedLineRanges:
    @patch("lintgate.channels._target_building.run_cmd")
    def test_parses_hunk_headers(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout="@@ -1,3 +10,5 @@ def foo\n@@ -20,2 +30,3 @@ def bar\n"
        )

        result = get_changed_line_ranges("/repo/src/mod.py", "/repo")

        assert result == [range(10, 15), range(30, 33)]

    @patch("lintgate.channels._target_building.run_cmd")
    def test_single_line_change_no_count(self, mock_run: MagicMock) -> None:
        """When count is omitted, defaults to 1."""
        mock_run.return_value = MagicMock(stdout="@@ -1 +7 @@ def foo\n")

        result = get_changed_line_ranges("/repo/src/mod.py", "/repo")

        assert result == [range(7, 8)]

    @patch("lintgate.channels._target_building.run_cmd")
    def test_zero_count_hunk_skipped(self, mock_run: MagicMock) -> None:
        """A hunk with +N,0 means deletion only — no new-side range."""
        mock_run.return_value = MagicMock(stdout="@@ -1,3 +5,0 @@ def foo\n")

        result = get_changed_line_ranges("/repo/src/mod.py", "/repo")

        assert result == []

    @patch("lintgate.channels._target_building.run_cmd")
    def test_returns_none_on_git_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = None

        result = get_changed_line_ranges("/repo/src/mod.py", "/repo")

        assert result is None

    @patch("lintgate.channels._target_building.run_cmd")
    def test_empty_diff_returns_empty_list(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")

        result = get_changed_line_ranges("/repo/src/mod.py", "/repo")

        assert result == []

    @patch("lintgate.channels._target_building.run_cmd")
    def test_passes_diff_base_and_rel_path(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")

        get_changed_line_ranges("/repo/src/mod.py", "/repo", diff_base="main")

        mock_run.assert_called_once_with(
            ["git", "diff", "main", "--unified=0", "--", "src/mod.py"],
            cwd="/repo",
            timeout=10,
        )


# ===================================================================
# _find_span_by_key
# ===================================================================


class TestFindSpanByKey:
    @patch("lintgate.channels._target_building.extract_symbol_spans")
    def test_returns_matching_span(self, mock_extract: MagicMock) -> None:
        s1 = _make_span("mod.py::foo")
        s2 = _make_span("mod.py::bar")
        mock_extract.return_value = [s1, s2]

        result = _find_span_by_key("/repo/mod.py", "/repo", "mod.py::bar")

        assert result is s2

    @patch("lintgate.channels._target_building.extract_symbol_spans")
    def test_returns_none_when_not_found(self, mock_extract: MagicMock) -> None:
        s1 = _make_span("mod.py::foo")
        mock_extract.return_value = [s1]

        result = _find_span_by_key("/repo/mod.py", "/repo", "mod.py::baz")

        assert result is None

    @patch("lintgate.channels._target_building.extract_symbol_spans")
    def test_returns_none_for_empty_spans(self, mock_extract: MagicMock) -> None:
        mock_extract.return_value = []

        result = _find_span_by_key("/repo/mod.py", "/repo", "mod.py::foo")

        assert result is None

    @patch("lintgate.channels._target_building.extract_symbol_spans")
    def test_returns_first_match(self, mock_extract: MagicMock) -> None:
        """If duplicates exist, returns the first one."""
        s1 = _make_span("mod.py::foo", start_line=1)
        s2 = _make_span("mod.py::foo", start_line=20)
        mock_extract.return_value = [s1, s2]

        result = _find_span_by_key("/repo/mod.py", "/repo", "mod.py::foo")

        assert result is s1
        assert result.start_line == 1


# ===================================================================
# _resolve_required_symbols
# ===================================================================


class TestResolveRequiredSymbols:
    def test_non_list_input_returns_empty(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols("not-a-list", "/repo", targets, seen)

        assert result == []
        assert len(targets) == 0

    def test_none_input_returns_empty(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols(None, "/repo", targets, seen)

        assert result == []

    def test_entry_without_double_colon_is_unresolved(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols(["bad_entry"], "/repo", targets, seen)

        assert result == ["bad_entry"]
        assert len(targets) == 0

    def test_non_string_entry_is_unresolved(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols([123], "/repo", targets, seen)

        assert result == ["123"]

    @patch("lintgate.channels._target_building.os.path.isfile", return_value=False)
    def test_missing_file_is_unresolved(self, _mock: MagicMock) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols(
            ["src/mod.py::foo"], "/repo", targets, seen
        )

        assert result == ["src/mod.py::foo"]

    @patch("lintgate.channels._target_building._find_span_by_key", return_value=None)
    @patch("lintgate.channels._target_building._canonicalize_symbol_key", return_value="src/mod.py::foo")
    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    def test_span_not_found_is_unresolved(
        self, _isfile: MagicMock, _canon: MagicMock, _find: MagicMock
    ) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols(
            ["src/mod.py::foo"], "/repo", targets, seen
        )

        assert result == ["src/mod.py::foo"]

    @patch("lintgate.channels._target_building._find_span_by_key")
    @patch("lintgate.channels._target_building._canonicalize_symbol_key", return_value="src/mod.py::foo")
    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    def test_resolved_span_added_to_targets(
        self, _isfile: MagicMock, _canon: MagicMock, mock_find: MagicMock
    ) -> None:
        span = _make_span("src/mod.py::foo")
        mock_find.return_value = span
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols(
            ["src/mod.py::foo"], "/repo", targets, seen
        )

        assert result == []
        assert len(targets) == 1
        assert targets[0] is span
        assert "src/mod.py::foo" in seen

    @patch("lintgate.channels._target_building._canonicalize_symbol_key", return_value="src/mod.py::foo")
    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    def test_already_seen_key_is_skipped(
        self, _isfile: MagicMock, _canon: MagicMock
    ) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = {"src/mod.py::foo"}

        result = _resolve_required_symbols(
            ["src/mod.py::foo"], "/repo", targets, seen
        )

        assert result == []
        assert len(targets) == 0

    def test_empty_list_returns_empty(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        result = _resolve_required_symbols([], "/repo", targets, seen)

        assert result == []


# ===================================================================
# _collect_changed_symbols
# ===================================================================


class TestCollectChangedSymbols:
    @patch("lintgate.channels._target_building._add_spans")
    @patch("lintgate.channels._target_building.get_changed_line_ranges", return_value=None)
    @patch("lintgate.channels._target_building.extract_symbol_spans")
    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    @patch("lintgate.channels._target_building.os.path.basename", return_value="mod.py")
    def test_git_failure_targets_all_symbols(
        self,
        _basename: MagicMock,
        _isfile: MagicMock,
        mock_extract: MagicMock,
        _mock_ranges: MagicMock,
        mock_add: MagicMock,
    ) -> None:
        spans = [_make_span("mod.py::foo")]
        mock_extract.return_value = spans
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _collect_changed_symbols(["/repo/mod.py"], "/repo", "HEAD", targets, seen)

        mock_add.assert_called_once_with(spans, targets, seen)

    @patch("lintgate.channels._target_building._add_overlapping_spans")
    @patch("lintgate.channels._target_building.get_changed_line_ranges")
    @patch("lintgate.channels._target_building.extract_symbol_spans")
    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    @patch("lintgate.channels._target_building.os.path.basename", return_value="mod.py")
    def test_with_changed_ranges_calls_overlapping(
        self,
        _basename: MagicMock,
        _isfile: MagicMock,
        mock_extract: MagicMock,
        mock_ranges: MagicMock,
        mock_overlap: MagicMock,
    ) -> None:
        spans = [_make_span("mod.py::foo")]
        mock_extract.return_value = spans
        changed = [range(5, 10)]
        mock_ranges.return_value = changed
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _collect_changed_symbols(["/repo/mod.py"], "/repo", "HEAD", targets, seen)

        mock_overlap.assert_called_once_with(spans, changed, targets, seen)

    def test_skips_non_python_files(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _collect_changed_symbols(["/repo/readme.md"], "/repo", "HEAD", targets, seen)

        assert len(targets) == 0

    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    def test_skips_test_files(self, _mock: MagicMock) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _collect_changed_symbols(
            ["/repo/test_foo.py", "/repo/bar_test.py", "/repo/conftest.py"],
            "/repo",
            "HEAD",
            targets,
            seen,
        )

        assert len(targets) == 0

    @patch("lintgate.channels._target_building.extract_symbol_spans", return_value=[])
    @patch("lintgate.channels._target_building.os.path.isfile", return_value=True)
    @patch("lintgate.channels._target_building.os.path.basename", return_value="mod.py")
    def test_no_spans_skips_file(
        self, _basename: MagicMock, _isfile: MagicMock, _extract: MagicMock
    ) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()

        _collect_changed_symbols(["/repo/mod.py"], "/repo", "HEAD", targets, seen)

        assert len(targets) == 0


# ===================================================================
# build_target_set
# ===================================================================


class TestBuildTargetSet:
    @patch("lintgate.channels._target_building._resolve_required_symbols", return_value=[])
    @patch("lintgate.channels._target_building._collect_changed_symbols")
    def test_changed_mode_calls_collect(
        self, mock_collect: MagicMock, _resolve: MagicMock
    ) -> None:
        targets, unresolved = build_target_set(
            ["/repo/mod.py"], "/repo", {"mode": "changed"}
        )

        mock_collect.assert_called_once()
        assert unresolved == []

    @patch("lintgate.channels._target_building._resolve_required_symbols", return_value=[])
    @patch("lintgate.channels._target_building._collect_changed_symbols")
    def test_all_mode_calls_collect(
        self, mock_collect: MagicMock, _resolve: MagicMock
    ) -> None:
        build_target_set(["/repo/mod.py"], "/repo", {"mode": "all"})

        mock_collect.assert_called_once()

    @patch("lintgate.channels._target_building._resolve_required_symbols", return_value=[])
    @patch("lintgate.channels._target_building._collect_changed_symbols")
    def test_required_only_mode_skips_collect(
        self, mock_collect: MagicMock, _resolve: MagicMock
    ) -> None:
        build_target_set(["/repo/mod.py"], "/repo", {"mode": "required_only"})

        mock_collect.assert_not_called()

    @patch("lintgate.channels._target_building._resolve_required_symbols", return_value=["missing::sym"])
    @patch("lintgate.channels._target_building._collect_changed_symbols")
    def test_returns_unresolved_from_resolve(
        self, _collect: MagicMock, _resolve: MagicMock
    ) -> None:
        _targets, unresolved = build_target_set(
            [], "/repo", {"mode": "changed", "required_symbols": ["missing::sym"]}
        )

        assert unresolved == ["missing::sym"]

    @patch("lintgate.channels._target_building._resolve_required_symbols", return_value=[])
    @patch("lintgate.channels._target_building._collect_changed_symbols")
    def test_default_mode_is_changed(
        self, mock_collect: MagicMock, _resolve: MagicMock
    ) -> None:
        """When mode is omitted, defaults to 'changed' and calls collect."""
        build_target_set(["/repo/mod.py"], "/repo", {})

        mock_collect.assert_called_once()

    @patch("lintgate.channels._target_building._resolve_required_symbols", return_value=[])
    @patch("lintgate.channels._target_building._collect_changed_symbols")
    def test_diff_base_passed_through(
        self, mock_collect: MagicMock, _resolve: MagicMock
    ) -> None:
        build_target_set(
            ["/repo/mod.py"], "/repo", {"mode": "changed", "diff_base": "main"}
        )

        # diff_base is the 3rd positional arg to _collect_changed_symbols
        call_args = mock_collect.call_args
        assert call_args[0][2] == "main"
