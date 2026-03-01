"""Tests targeting specific uncovered lines across three modules.

Module 1: lintgate/channels/structure_channel.py
  - execute: severity="warning" branch (line 236)
  - _build_import_graph: filepath_to_module returns falsy (line 310)
  - _check_import_cycles: duplicate cycle detection (line 378)
  - _check_module_size_distribution: p50 == 0 early return (line 471)
  - _is_orphan_excluded: shebang line detection (line 812)

Module 2: lintgate/channels/symbol_coverage.py
  - _canonicalize_symbol_key: ValueError on relpath (lines 102-104)
  - _visit_node: end_lineno is None (line 153)
  - get_changed_line_ranges: ValueError on relpath (lines 271-272)
  - _find_file_coverage: ValueError branches (lines 585-586, 597-598)

Module 3: lintgate/context_auditor_checks.py
  - extract_path_refs: tree-drawing chars + space filter (lines 473-476)
  - _detect_generated_patterns: webpack.config.js branch (line 516)
"""

from __future__ import annotations

import ast
import os
import textwrap
from typing import TYPE_CHECKING
from unittest.mock import patch

from lintgate.channels.structure_channel import (
    StructureChannel,
    _build_import_graph,
    _check_import_cycles,
    _check_module_size_distribution,
    _is_orphan_excluded,
)
from lintgate.channels.symbol_coverage import (
    FileCoverage,
    SymbolSpan,
    _canonicalize_symbol_key,
    _find_file_coverage,
    _visit_node,
    get_changed_line_ranges,
)
from lintgate.context_auditor_checks import (
    _detect_generated_patterns,
    extract_path_refs,
)
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))


# ── Module 1: structure_channel.py ────────────────────────────────────────


class TestStructureChannelExecuteWarningSeverity:
    """Line 236: severity = "warning" when any finding has severity="warning"."""

    def test_severity_warning_when_finding_has_warning(self, tmp_path: Path) -> None:
        """Execute returns severity='warning' when at least one finding is warning."""
        channel = StructureChannel()
        event = SupervisionEvent(
            surface="mcp",
            project_root=str(tmp_path),
        )
        config = ControlPlaneConfig(enabled=True)

        # Create enough Python files (>= 5) with an import cycle that
        # produces a warning-severity finding. We'll mock _check_import_cycles
        # to return a finding with severity="warning".
        for i in range(6):
            _write_file(
                os.path.join(tmp_path, "pkg", f"mod{i}.py"),
                f"# module {i}\nx = {i}\n",
            )
        _write_file(os.path.join(tmp_path, "pkg", "__init__.py"), "")

        warning_finding = LintIssue(
            linter="structure",
            kind="STRUCT001",
            message="Import cycle detected",
            severity="warning",
        )

        with patch(
            "lintgate.channels.structure_channel._check_import_cycles",
            return_value=[warning_finding],
        ):
            result = channel.execute(event, config)

        assert result.severity == "warning"
        assert result.status == "fail"


class TestBuildImportGraphFalsyModule:
    """Line 310: continue when filepath_to_module returns falsy."""

    def test_skips_file_when_filepath_to_module_returns_none(
        self, tmp_path: Path
    ) -> None:
        root = str(tmp_path)
        py_file = os.path.join(root, "bad_file.py")
        _write_file(py_file, "x = 1\n")

        with patch(
            "lintgate.linters.architecture_checks._helpers.filepath_to_module",
            return_value=None,
        ):
            graph, file_map, file_loc, deferred = _build_import_graph([py_file], root)

        # File should be skipped entirely
        assert len(file_map) == 0
        assert len(file_loc) == 0
        assert len(deferred) == 0


class TestCheckImportCyclesDuplicate:
    """Line 378: continue when cycle_key in seen_cycles (duplicate)."""

    def test_deduplicates_identical_cycles(self) -> None:
        # Graph: A -> B -> A produces cycle [A, B].
        # The DFS visits both A and B as starting nodes, which can produce
        # the same cycle (as a frozenset) twice. We mock _find_cycles to
        # return explicit duplicates.
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        graph: dict[str, set[str]] = {"a": {"b"}, "b": {"a"}}

        with patch(
            "lintgate.channels.structure_logic._find_cycles",
            return_value=[["a", "b"], ["b", "a"]],
        ):
            findings = _check_import_cycles(graph, file_map, "/proj")

        # Both cycles have frozenset({"a","b"}) — second should be skipped
        assert len(findings) == 1


class TestCheckModuleSizeP50Zero:
    """Line 471: return findings when p50 == 0."""

    def test_returns_empty_when_p50_is_zero(self) -> None:
        # Need >= 5 files with LOC >= _ABSOLUTE_LOC_FLOOR (50),
        # but median == 0. That's impossible with the >= 50 filter...
        # Actually, p50 == 0 can happen if all values are 0, but they're
        # filtered by >= 50. So we need to bypass the filter.
        # The filter is: loc >= _ABSOLUTE_LOC_FLOOR. If all pass the filter
        # but median is 0, that's impossible. Let's mock statistics.median.
        file_loc = {f"/proj/f{i}.py": 50 for i in range(6)}

        with patch(
            "lintgate.channels.structure_logic.statistics.median", return_value=0
        ):
            findings = _check_module_size_distribution(file_loc, "/proj")

        assert findings == []


class TestIsOrphanExcludedShebang:
    """Line 812: return True when file starts with shebang."""

    def test_shebang_file_excluded(self, tmp_path: Path) -> None:
        filepath = os.path.join(tmp_path, "pkg", "myscript.py")
        _write_file(filepath, "#!/usr/bin/env python3\nprint('hello')\n")

        result = _is_orphan_excluded(
            filepath,
            module="pkg.myscript",
            project_root=str(tmp_path),
        )
        assert result is True


# ── Module 2: symbol_coverage.py ──────────────────────────────────────────


class TestCanonicalizeSymbolKeyValueError:
    """Lines 102-104: ValueError on os.path.relpath (different drives on Windows)."""

    def test_falls_back_to_full_path_on_relpath_error(self) -> None:
        with patch("os.path.relpath", side_effect=ValueError("different drives")):
            result = _canonicalize_symbol_key("/D/proj/mod.py", "func", "/C/root")

        # Should use the normalized filepath as-is (fpath) instead of relpath
        assert "::func" in result


class TestVisitNodeEndLinenoNone:
    """Line 153: continue when end_lineno is None."""

    def test_skips_function_without_end_lineno(self) -> None:
        source = "def foo():\n    pass\n"
        tree = ast.parse(source)

        # Remove end_lineno from the function def
        func_node = tree.body[0]
        func_node.end_lineno = None  # type: ignore[attr-defined]

        spans: list[SymbolSpan] = []
        _visit_node(tree, "/proj/mod.py", "/proj", spans, current_class=None, depth=0)

        # Function should be skipped — no spans collected
        assert len(spans) == 0


class TestGetChangedLineRangesValueError:
    """Lines 271-272: ValueError on os.path.relpath returns None."""

    def test_returns_none_on_relpath_error(self) -> None:
        with patch("os.path.relpath", side_effect=ValueError("different drives")):
            result = get_changed_line_ranges("/D/proj/file.py", "/C/root")

        assert result is None


class TestFindFileCoverageValueErrors:
    """Lines 585-586, 597-598: ValueError branches in _find_file_coverage."""

    def test_relpath_valueerror_falls_through(self) -> None:
        """Line 585-586: ValueError on relpath falls through to suffix matching."""
        cov = FileCoverage(
            executed_lines={1, 2},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        # Key that won't match abs_path directly but will match via normpath join
        coverage_data = {"src/mod.py": cov}

        with patch(
            "lintgate.channels.symbol_coverage.os.path.relpath",
            side_effect=ValueError("different drives"),
        ):
            result = _find_file_coverage("/proj/src/mod.py", coverage_data, "/proj")

        # Should still find via the normpath(join()) fallback
        assert result is cov

    def test_join_valueerror_in_suffix_matching(self) -> None:
        """Lines 597-598: ValueError/TypeError in os.path.join during suffix matching."""
        cov = FileCoverage(
            executed_lines={1},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        # Use a key that won't match via normpath equality
        coverage_data = {"weird_key": cov}

        original_join = os.path.join

        def patched_join(*args: str) -> str:
            # Raise on the join inside the suffix-matching loop
            if len(args) == 2 and args[1] == "weird_key":
                raise ValueError("bad join")
            return original_join(*args)

        with (
            patch(
                "lintgate.channels.symbol_coverage.os.path.relpath",
                side_effect=ValueError("drives"),
            ),
            patch(
                "lintgate.channels.symbol_coverage.os.path.join",
                side_effect=patched_join,
            ),
        ):
            result = _find_file_coverage("/proj/src/mod.py", coverage_data, "/proj")

        # Should return None after all fallbacks fail
        assert result is None


# ── Module 3: context_auditor_checks.py ──────────────────────────────────


class TestExtractPathRefsTreeDrawingChars:
    """Line 473-474: skip candidates with tree-drawing characters."""

    def test_skips_candidates_with_tree_chars(self) -> None:
        text = "See `├── src/main.py` for details."
        refs = extract_path_refs(text)
        assert refs == []

    def test_skips_candidates_with_newline(self) -> None:
        # A backtick-quoted string with a newline inside
        text = "Check `path/to\nfile.py` here."
        refs = extract_path_refs(text)
        assert refs == []


class TestExtractPathRefsSpaceFilter:
    """Line 475-476: skip candidates with space but no os.sep."""

    def test_skips_path_with_space_no_sep(self) -> None:
        text = "Run `some command/thing` to check."
        refs = extract_path_refs(text)
        # "some command/thing" has a space and "/" but space + no os.sep → skip
        # On Unix os.sep is "/", so this actually contains os.sep.
        # We need a candidate where " " is present but os.sep is NOT.
        # On Unix that means no "/" — but then is_path_like is False.
        # This branch is effectively for paths with space on systems
        # where os.sep != "/" but the path has "/".
        # Let's mock os.sep to trigger the branch.
        with patch("lintgate.context_auditor_checks.os.sep", "\\"):
            refs = extract_path_refs(text)
        assert refs == []


class TestDetectGeneratedPatternsWebpack:
    """Line 516: webpack.config.js triggers dist/bundle patterns."""

    def test_webpack_config_triggers_patterns(self, tmp_path: Path) -> None:
        # Create webpack.config.js (but not package.json to isolate this branch)
        (tmp_path / "webpack.config.js").write_text("module.exports = {};")

        patterns = _detect_generated_patterns(str(tmp_path))

        assert "dist" in patterns
        assert "bundle" in patterns
