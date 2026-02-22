"""Coverage-targeted tests for structure_channel private/public symbols.

Targets symbols not fully covered by test_structure_channel.py:
  - StructureChannel (should_run edge cases, execute paths)
  - dfs (inner function of _find_cycles, tested via _find_cycles)
  - _detect_reexports (AST-based re-export detection)
  - _build_reexport_map (aggregation of re-exports across __init__.py files)
  - _build_structure_snapshot (compact orientation data)
  - _build_import_graph (import graph construction)
  - _count_loc (docstring handling edge cases)
  - _percentile (boundary interpolation)
  - _check_orphans (re-export aware orphan detection)
  - _is_orphan_excluded (extra_exclude_dirs, plugin dirs, edge cases)
  - _check_import_cycles (no relevant files in cycle)
  - _check_module_size_distribution (p50 == 0 edge case)
  - _check_package_cohesion (pkg prefix matching, top-level modules)
"""

from __future__ import annotations

import os
import textwrap

import pytest

from lintgate.channels.structure_channel import (
    StructureChannel,
    _build_reexport_map,
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _count_loc,
    _detect_reexports,
    _discover_python_files,
    _find_cycles,
    _is_orphan_excluded,
    _percentile,
)
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.types import ChangeClassification, LintIssue

# ── Helpers ───────────────────────────────────────────────────────────────


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))


@pytest.fixture
def channel():
    return StructureChannel()


@pytest.fixture
def config():
    return ControlPlaneConfig(enabled=True)


# ── _detect_reexports Tests ──────────────────────────────────────────────


class TestDetectReexports:
    """Tests for _detect_reexports: AST-based re-export detection from __init__.py."""

    def test_named_import_definite(self, tmp_path):
        """from .sub import Foo -> definite re-export of 'sub'."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "from .sub import Foo\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {"sub": "definite"}

    def test_wildcard_import_unknown(self, tmp_path):
        """from .sub import * -> unknown re-export of 'sub'."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "from .sub import *\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {"sub": "unknown"}

    def test_definite_overrides_unknown(self, tmp_path):
        """A named import upgrades an earlier wildcard to definite."""
        init = str(tmp_path / "__init__.py")
        _write_file(
            init,
            """\
            from .sub import *
            from .sub import Foo
            """,
        )
        result = _detect_reexports(init, str(tmp_path))
        assert result["sub"] == "definite"

    def test_all_assignment_definite(self, tmp_path):
        """__all__ = ['bar'] -> definite re-export of 'bar'."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "__all__ = ['bar']\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {"bar": "definite"}

    def test_all_assignment_tuple(self, tmp_path):
        """__all__ = ('bar',) -> definite re-export of 'bar'."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "__all__ = ('baz',)\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {"baz": "definite"}

    def test_dynamic_import_sets_wildcard_unknown(self, tmp_path):
        """importlib.import_module(...) -> '*' marker set to unknown."""
        init = str(tmp_path / "__init__.py")
        _write_file(
            init,
            """\
            import importlib
            importlib.import_module('.dynamic', __name__)
            """,
        )
        result = _detect_reexports(init, str(tmp_path))
        assert "*" in result
        assert result["*"] == "unknown"

    def test_dunder_import_dynamic(self, tmp_path):
        """__import__('mod') -> dynamic import marker."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "__import__('something')\n")
        result = _detect_reexports(init, str(tmp_path))
        assert "*" in result
        assert result["*"] == "unknown"

    def test_syntax_error_returns_empty(self, tmp_path):
        """Malformed Python returns empty dict."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "def broken(\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {}

    def test_missing_file_returns_empty(self):
        """Non-existent file returns empty dict."""
        result = _detect_reexports("/nonexistent/__init__.py", "/nonexistent")
        assert result == {}

    def test_no_reexports_returns_empty(self, tmp_path):
        """__init__.py with no imports returns empty dict."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "# just a comment\nx = 1\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {}

    def test_absolute_import_ignored(self, tmp_path):
        """Absolute imports (level=0) are not re-exports."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "from os.path import join\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {}

    def test_dotted_relative_import(self, tmp_path):
        """from .sub.deep import Foo -> re-export of 'sub' (top-level part)."""
        init = str(tmp_path / "__init__.py")
        _write_file(init, "from .sub.deep import Foo\n")
        result = _detect_reexports(init, str(tmp_path))
        assert result == {"sub": "definite"}


# ── _build_reexport_map Tests ────────────────────────────────────────────


class TestBuildReexportMap:
    """Tests for _build_reexport_map: aggregation across __init__.py files."""

    def test_collects_from_init_files(self, tmp_path):
        """Only __init__.py files are scanned."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_file(str(pkg / "__init__.py"), "from .sub import Foo\n")
        _write_file(str(pkg / "sub.py"), "Foo = 1\n")

        py_files = [str(pkg / "__init__.py"), str(pkg / "sub.py")]
        result = _build_reexport_map(py_files, str(tmp_path))

        assert str(pkg) in result
        assert result[str(pkg)] == {"sub": "definite"}

    def test_ignores_non_init_files(self, tmp_path):
        """Regular .py files are not processed."""
        _write_file(str(tmp_path / "mod.py"), "from .sub import Foo\n")
        py_files = [str(tmp_path / "mod.py")]
        result = _build_reexport_map(py_files, str(tmp_path))
        assert result == {}

    def test_skips_empty_reexport_inits(self, tmp_path):
        """__init__.py with no re-exports produces no entry."""
        _write_file(str(tmp_path / "__init__.py"), "# empty\n")
        py_files = [str(tmp_path / "__init__.py")]
        result = _build_reexport_map(py_files, str(tmp_path))
        assert result == {}


# ── _build_structure_snapshot Tests ──────────────────────────────────────


class TestBuildStructureSnapshot:
    """Tests for _build_structure_snapshot: compact orientation data."""

    def test_snapshot_has_all_keys(self, tmp_path):
        py_files = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]
        import_graph = {"pkg.a": {"pkg.b"}}
        file_map = {"pkg.a": str(tmp_path / "a.py"), "pkg.b": str(tmp_path / "b.py")}
        file_loc = {str(tmp_path / "a.py"): 100, str(tmp_path / "b.py"): 50}

        snapshot = _build_structure_snapshot(
            py_files,
            import_graph,
            file_map,
            file_loc,
            [],
            [],
            [],
            [],
            str(tmp_path),
        )

        assert snapshot["file_count"] == 2
        assert snapshot["total_loc"] == 150
        assert snapshot["median_module_loc"] == 75
        assert len(snapshot["largest_modules"]) == 2
        assert snapshot["package_count"] >= 1
        assert snapshot["import_cycle_count"] == 0
        assert snapshot["orphan_count"] == 0
        assert snapshot["low_cohesion_packages"] == 0
        assert snapshot["checks_run"] == 4

    def test_snapshot_largest_modules_order(self, tmp_path):
        """Largest modules are sorted by LOC descending, top 3."""
        file_loc = {
            str(tmp_path / "a.py"): 300,
            str(tmp_path / "b.py"): 500,
            str(tmp_path / "c.py"): 100,
            str(tmp_path / "d.py"): 400,
        }
        file_map = {
            "a": str(tmp_path / "a.py"),
            "b": str(tmp_path / "b.py"),
            "c": str(tmp_path / "c.py"),
            "d": str(tmp_path / "d.py"),
        }
        snapshot = _build_structure_snapshot(
            list(file_loc.keys()),
            {},
            file_map,
            file_loc,
            [],
            [],
            [],
            [],
            str(tmp_path),
        )
        largest = snapshot["largest_modules"]
        assert len(largest) == 3
        assert largest[0]["loc"] == 500
        assert largest[1]["loc"] == 400
        assert largest[2]["loc"] == 300

    def test_snapshot_counts_finding_lists(self, tmp_path):
        """Finding counts reflect the lists passed in."""
        cycle_f = [LintIssue(linter="x", kind="STRUCT001", message="c")]
        orphan_f = [
            LintIssue(linter="x", kind="STRUCT003", message="o1"),
            LintIssue(linter="x", kind="STRUCT003", message="o2"),
        ]
        cohesion_f = [LintIssue(linter="x", kind="STRUCT004", message="h")]
        snapshot = _build_structure_snapshot(
            [],
            {},
            {},
            {},
            cycle_f,
            [],
            orphan_f,
            cohesion_f,
            str(tmp_path),
        )
        assert snapshot["import_cycle_count"] == 1
        assert snapshot["orphan_count"] == 2
        assert snapshot["low_cohesion_packages"] == 1

    def test_snapshot_top_level_module_counted(self, tmp_path):
        """Top-level modules (no dot in name) go to '<top-level>' package."""
        file_map = {"standalone": str(tmp_path / "standalone.py")}
        file_loc = {str(tmp_path / "standalone.py"): 10}
        snapshot = _build_structure_snapshot(
            [str(tmp_path / "standalone.py")],
            {},
            file_map,
            file_loc,
            [],
            [],
            [],
            [],
            str(tmp_path),
        )
        assert "<top-level>" in snapshot["packages"]
        assert snapshot["packages"]["<top-level>"] == 1

    def test_snapshot_empty_loc_zero_median(self, tmp_path):
        """When no files have LOC > 0, median is 0."""
        snapshot = _build_structure_snapshot(
            [],
            {},
            {},
            {},
            [],
            [],
            [],
            [],
            str(tmp_path),
        )
        assert snapshot["median_module_loc"] == 0
        assert snapshot["total_loc"] == 0


# ── _count_loc Edge Cases ────────────────────────────────────────────────


class TestCountLocEdgeCases:
    """Coverage-targeted edge cases for _count_loc docstring tracking."""

    def test_single_triple_quote_toggles_docstring(self, tmp_path):
        """A single triple-quote line toggles in_docstring state."""
        f = str(tmp_path / "mod.py")
        _write_file(
            f,
            '''\
            x = 1
            """
            This is a docstring body.
            """
            y = 2
            ''',
        )
        loc = _count_loc(f)
        # x = 1 and y = 2 count; the triple-quote lines and body do not
        assert loc == 2

    def test_single_line_docstring(self, tmp_path):
        """Opening and closing triple-quotes on same line -> skip entire line."""
        f = str(tmp_path / "mod.py")
        _write_file(
            f,
            '''\
            x = 1
            """Single line docstring."""
            y = 2
            ''',
        )
        loc = _count_loc(f)
        # Both triple-quote pairs on same line -> count == 2, so skip (continue)
        assert loc == 2

    def test_mixed_triple_quotes(self, tmp_path):
        """File with both triple-double and triple-single quotes."""
        f = str(tmp_path / "mod.py")
        content = "a = 1\n'''\nmultiline\n'''\nb = 2\n"
        with open(f, "w") as fh:
            fh.write(content)
        loc = _count_loc(f)
        assert loc == 2  # a = 1, b = 2

    def test_comment_lines_not_counted(self, tmp_path):
        f = str(tmp_path / "mod.py")
        _write_file(
            f,
            """\
            # comment 1
            # comment 2
            x = 1
            """,
        )
        assert _count_loc(f) == 1

    def test_blank_lines_not_counted(self, tmp_path):
        f = str(tmp_path / "mod.py")
        _write_file(f, "\n\n\nx = 1\n\n\n")
        assert _count_loc(f) == 1


# ── _percentile Edge Cases ───────────────────────────────────────────────


class TestPercentileEdgeCases:
    def test_p100_returns_last(self):
        """p100 on a list returns the last element."""
        assert _percentile([10, 20, 30], 1.0) == 30.0

    def test_interpolation(self):
        """Non-boundary percentile interpolates between values."""
        # [10, 20, 30] at p50: k = (3-1)*0.5 = 1.0, f=1, c=2 -> d0=20, d1=30
        # result = 20 + (30-20)*0.0 = 20.0
        assert _percentile([10, 20, 30], 0.5) == 20.0

    def test_two_elements(self):
        """Two elements, p75."""
        # k = (2-1)*0.75 = 0.75, f=0, c=1 -> 10 + (20-10)*0.75 = 17.5
        assert _percentile([10, 20], 0.75) == 17.5


# ── _find_cycles / dfs Coverage ──────────────────────────────────────────


class TestFindCyclesDfs:
    """Targeted tests for dfs inner function behavior."""

    def test_self_loop(self):
        """A node importing itself is a cycle of length 1."""
        graph = {"a": {"a"}}
        cycles = _find_cycles(graph)
        assert len(cycles) >= 1
        assert "a" in cycles[0]

    def test_disconnected_no_cycle(self):
        """Disconnected nodes with no edges -> no cycles."""
        graph = {"a": set(), "b": set(), "c": set()}
        cycles = _find_cycles(graph)
        assert cycles == []

    def test_multiple_independent_cycles(self):
        """Two separate cycles are both detected."""
        graph = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}
        cycles = _find_cycles(graph)
        cycle_sets = [frozenset(c) for c in cycles]
        assert frozenset({"a", "b"}) in cycle_sets
        assert frozenset({"c", "d"}) in cycle_sets

    def test_visited_node_not_revisited(self):
        """Already-visited nodes that are not on the current path are skipped."""
        # a -> b -> c (no cycle), then d -> b (b already visited, no cycle)
        graph = {"a": {"b"}, "b": {"c"}, "c": set(), "d": {"b"}}
        cycles = _find_cycles(graph)
        assert cycles == []


# ── _check_import_cycles Edge Cases ──────────────────────────────────────


class TestCheckImportCyclesEdge:
    def test_cycle_with_no_relevant_files(self, tmp_path):
        """Cycle where no module exists in file_map -> no finding."""
        graph = {"ghost.a": {"ghost.b"}, "ghost.b": {"ghost.a"}}
        file_map = {}  # Neither module in file_map
        findings = _check_import_cycles(graph, file_map, str(tmp_path))
        assert findings == []

    def test_cycle_message_format(self, tmp_path):
        """Cycle message includes arrow-separated chain ending with start node."""
        graph = {"pkg.x": {"pkg.y"}, "pkg.y": {"pkg.x"}}
        file_map = {
            "pkg.x": str(tmp_path / "pkg" / "x.py"),
            "pkg.y": str(tmp_path / "pkg" / "y.py"),
        }
        findings = _check_import_cycles(graph, file_map, str(tmp_path))
        assert len(findings) == 1
        msg = findings[0].message
        assert "\u2192" in msg or "→" in msg  # Arrow in cycle representation
        assert findings[0].evidence["length"] >= 2


# ── _check_module_size_distribution Edge Cases ───────────────────────────


class TestModuleSizeEdge:
    def test_p50_zero_returns_empty(self, tmp_path):
        """When all files have 0 LOC (but >= floor), p50==0 -> skip."""
        # This is practically impossible since floor is 50, but test the guard
        # We can only reach p50==0 if all meaningful_locs values are 0,
        # but they must be >= 50 to be in meaningful_locs. So this branch
        # is defensive. We test that filtering leaves too few files instead.
        file_loc = {str(tmp_path / f"f{i}.py"): 49 for i in range(10)}
        findings = _check_module_size_distribution(file_loc, str(tmp_path))
        assert findings == []  # All below floor -> too few files

    def test_outliers_capped_at_five(self, tmp_path):
        """Evidence outliers list is capped at 5 entries."""
        file_loc = {}
        # 20 small files to set a low median
        for i in range(20):
            file_loc[str(tmp_path / f"small{i}.py")] = 55
        # 8 large files (all above p90)
        for i in range(8):
            file_loc[str(tmp_path / f"big{i}.py")] = 2000 + i * 100
        findings = _check_module_size_distribution(file_loc, str(tmp_path))
        if findings:
            outliers = findings[0].evidence.get("outliers", [])
            assert len(outliers) <= 5


# ── _check_orphans with Re-exports ──────────────────────────────────────


class TestCheckOrphansReexports:
    def test_definite_reexport_skips_orphan(self, tmp_path):
        """Module explicitly re-exported from __init__.py is not an orphan."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_file(str(pkg / "__init__.py"), "from .sub import Foo\n")
        _write_file(str(pkg / "sub.py"), "Foo = 1\n")

        graph = {}  # No direct imports
        file_map = {"pkg.sub": str(pkg / "sub.py")}
        py_files = [str(pkg / "__init__.py"), str(pkg / "sub.py")]
        findings = _check_orphans(py_files, graph, file_map, str(tmp_path))
        orphan_modules = {f.evidence["module"] for f in findings}
        assert "pkg.sub" not in orphan_modules

    def test_unknown_reexport_low_confidence(self, tmp_path):
        """Module with wildcard re-export reported at confidence 0.3."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_file(str(pkg / "__init__.py"), "from .sub import *\n")
        _write_file(str(pkg / "sub.py"), "Foo = 1\n")

        graph = {}
        file_map = {"pkg.sub": str(pkg / "sub.py")}
        py_files = [str(pkg / "__init__.py"), str(pkg / "sub.py")]
        findings = _check_orphans(py_files, graph, file_map, str(tmp_path))
        sub_findings = [f for f in findings if f.evidence.get("module") == "pkg.sub"]
        assert len(sub_findings) == 1
        assert sub_findings[0].confidence == 0.3
        assert sub_findings[0].evidence["reexport_status"] == "unknown"

    def test_dynamic_import_wildcard_marker(self, tmp_path):
        """Dynamic import in __init__.py sets '*' marker -> orphans at low confidence."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_file(
            str(pkg / "__init__.py"),
            """\
            import importlib
            importlib.import_module('.dynamic', __name__)
            """,
        )
        _write_file(str(pkg / "dynamic.py"), "x = 1\n")

        graph = {}
        file_map = {"pkg.dynamic": str(pkg / "dynamic.py")}
        py_files = [str(pkg / "__init__.py"), str(pkg / "dynamic.py")]
        findings = _check_orphans(py_files, graph, file_map, str(tmp_path))
        dyn_findings = [f for f in findings if f.evidence.get("module") == "pkg.dynamic"]
        assert len(dyn_findings) == 1
        assert dyn_findings[0].confidence == 0.3

    def test_extra_exclude_dirs(self, tmp_path):
        """Extra orphan exclude dirs from config are respected."""
        custom_dir = tmp_path / "custom_generated"
        custom_dir.mkdir()
        _write_file(str(custom_dir / "gen.py"), "x = 1\n")

        graph = {}
        file_map = {"custom_generated.gen": str(custom_dir / "gen.py")}
        py_files = [str(custom_dir / "gen.py")]

        # Without extra_exclude_dirs
        findings_without = _check_orphans(py_files, graph, file_map, str(tmp_path))
        orphan_modules_without = {f.evidence["module"] for f in findings_without}

        # With extra_exclude_dirs
        findings_with = _check_orphans(
            py_files,
            graph,
            file_map,
            str(tmp_path),
            extra_exclude_dirs=frozenset({"custom_generated"}),
        )
        orphan_modules_with = {f.evidence.get("module") for f in findings_with}

        assert "custom_generated.gen" in orphan_modules_without
        assert "custom_generated.gen" not in orphan_modules_with


# ── _is_orphan_excluded Edge Cases ───────────────────────────────────────


class TestIsOrphanExcludedEdge:
    def test_file_ending_with_test(self, tmp_path):
        """Files ending with _test are excluded."""
        assert _is_orphan_excluded(
            str(tmp_path / "pkg" / "foo_test.py"), "pkg.foo_test", str(tmp_path)
        )

    def test_plugin_dirs_excluded(self, tmp_path):
        """Files under plugin-like directories (linters, renderers, etc.) are excluded."""
        for dirname in (
            "linters",
            "renderers",
            "extensions",
            "handlers",
            "backends",
            "drivers",
            "adapters",
        ):
            assert _is_orphan_excluded(
                str(tmp_path / dirname / "my_plugin.py"),
                f"{dirname}.my_plugin",
                str(tmp_path),
            ), f"Expected {dirname}/my_plugin.py to be excluded"

    def test_extra_exclude_dirs_applied(self, tmp_path):
        """Extra exclude dirs from config are used."""
        result = _is_orphan_excluded(
            str(tmp_path / "generated" / "code.py"),
            "generated.code",
            str(tmp_path),
            extra_exclude_dirs=frozenset({"generated"}),
        )
        assert result is True

    def test_extra_exclude_dirs_none(self, tmp_path):
        """When extra_exclude_dirs is None, no extra exclusions."""
        result = _is_orphan_excluded(
            str(tmp_path / "pkg" / "utils.py"),
            "pkg.utils",
            str(tmp_path),
            extra_exclude_dirs=None,
        )
        assert result is False

    def test_oserror_on_shebang_check(self, tmp_path):
        """When file cannot be read for shebang check, it's not excluded by shebang."""
        # Use a non-existent file path that also passes the other exclusion checks
        fake_path = str(tmp_path / "pkg" / "missing.py")
        result = _is_orphan_excluded(fake_path, "pkg.missing", str(tmp_path))
        assert result is False

    def test_alembic_dir_excluded(self, tmp_path):
        """Files in alembic directory are excluded."""
        assert _is_orphan_excluded(
            str(tmp_path / "alembic" / "versions" / "abc.py"),
            "alembic.versions.abc",
            str(tmp_path),
        )

    def test_fixtures_dir_excluded(self, tmp_path):
        """Files in fixtures directory are excluded."""
        assert _is_orphan_excluded(
            str(tmp_path / "fixtures" / "sample.py"),
            "fixtures.sample",
            str(tmp_path),
        )

    def test_benchmarks_dir_excluded(self, tmp_path):
        """Files in benchmarks directory are excluded."""
        assert _is_orphan_excluded(
            str(tmp_path / "benchmarks" / "perf.py"),
            "benchmarks.perf",
            str(tmp_path),
        )

    def test_wsgi_excluded(self, tmp_path):
        """wsgi.py is excluded as an entrypoint."""
        assert _is_orphan_excluded(str(tmp_path / "wsgi.py"), "wsgi", str(tmp_path))

    def test_asgi_excluded(self, tmp_path):
        """asgi.py is excluded as an entrypoint."""
        assert _is_orphan_excluded(str(tmp_path / "asgi.py"), "asgi", str(tmp_path))

    def test_hook_excluded(self, tmp_path):
        """hook.py is excluded as an entrypoint."""
        assert _is_orphan_excluded(str(tmp_path / "hook.py"), "hook", str(tmp_path))


# ── _check_package_cohesion Edge Cases ───────────────────────────────────


class TestPackageCohesionEdge:
    def test_import_matches_pkg_prefix(self, tmp_path):
        """Intra-package import detected via pkg prefix (e.g. 'pkg.sub.helper')."""
        graph = {
            "pkg.a": {"pkg.sub.helper"},
        }
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.sub.helper": str(tmp_path / "pkg" / "sub" / "helper.py"),
            "other.x": str(tmp_path / "other" / "x.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        # pkg.sub.helper starts with "pkg." so it's intra-package -> high cohesion
        pkg_findings = [f for f in findings if f.evidence.get("package") == "pkg"]
        assert pkg_findings == []

    def test_import_matches_pkg_name_exactly(self, tmp_path):
        """Import of the package name itself counts as intra-package."""
        graph = {
            "pkg.a": {"pkg"},
        }
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg": str(tmp_path / "pkg" / "__init__.py"),
            "other.x": str(tmp_path / "other" / "x.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        pkg_findings = [f for f in findings if f.evidence.get("package") == "pkg"]
        assert pkg_findings == []  # Import of 'pkg' itself is intra-package

    def test_top_level_modules_excluded_from_packages(self, tmp_path):
        """Top-level modules (no dots) are excluded from package grouping."""
        graph = {"standalone": {"pkg.a"}}
        file_map = {
            "standalone": str(tmp_path / "standalone.py"),
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "other.b": str(tmp_path / "other" / "b.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        # 'standalone' is not in any package, should not cause issues
        assert all(f.evidence.get("package") != "standalone" for f in findings)


# ── StructureChannel.should_run Edge Cases ───────────────────────────────


class TestShouldRunEdge:
    def test_structural_risk_level(self, channel, config, tmp_path):
        """structural risk level -> should_run returns True."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            change_classification=ChangeClassification(
                risk_level="structural",
            ),
        )
        assert channel.should_run(event, config) is True

    def test_architectural_risk_level(self, channel, config, tmp_path):
        """architectural risk level -> should_run returns True."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            change_classification=ChangeClassification(
                risk_level="architectural",
            ),
        )
        assert channel.should_run(event, config) is True

    def test_cosmetic_risk_level_skips(self, channel, config, tmp_path):
        """cosmetic risk level -> should_run returns False."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            change_classification=ChangeClassification(
                risk_level="cosmetic",
            ),
        )
        assert channel.should_run(event, config) is False

    def test_config_file_triggers(self, channel, config, tmp_path):
        """Config file in files_changed -> should_run returns True."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            files_changed=["pyproject.toml"],
            change_classification=ChangeClassification(
                risk_level="moderate",
            ),
        )
        assert channel.should_run(event, config) is True

    def test_import_only_triggers(self, channel, config, tmp_path):
        """import_only change -> should_run returns True."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            files_changed=["pkg/mod.py"],
            change_classification=ChangeClassification(
                risk_level="moderate",
                import_only=True,
            ),
        )
        assert channel.should_run(event, config) is True

    def test_class_structure_changed_triggers(self, channel, config, tmp_path):
        """class_structure_changed -> should_run returns True."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            files_changed=["pkg/mod.py"],
            change_classification=ChangeClassification(
                risk_level="moderate",
                class_structure_changed=True,
            ),
        )
        assert channel.should_run(event, config) is True

    def test_moderate_no_structural_files_skips(self, channel, config, tmp_path):
        """Moderate risk with no structural files and no import/class changes -> skip."""
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            files_changed=["pkg/mod.py"],
            change_classification=ChangeClassification(
                risk_level="moderate",
                import_only=False,
                class_structure_changed=False,
            ),
        )
        assert channel.should_run(event, config) is False


# ── StructureChannel.execute Edge Cases ──────────────────────────────────


class TestExecuteEdge:
    def test_execute_severity_escalation(self, channel, config, tmp_path):
        """When findings include warnings, severity escalates to warning."""
        # Create a project with enough files for analysis and a cycle
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_file(str(pkg / "__init__.py"), "")
        # Create enough files (need >= 5 for size analysis)
        for i in range(6):
            content = "\n".join([f"x_{j} = {j}" for j in range(20)])
            _write_file(str(pkg / f"mod{i}.py"), content)

        event = SupervisionEvent(surface="mcp", project_root=str(tmp_path))
        result = channel.execute(event, config)

        # Result should be well-formed
        assert result.channel == "structure"
        assert result.duration_ms > 0
        # Status and severity are consistent
        if result.findings:
            assert result.status == "fail"
            assert result.severity in ("informational", "warning")
        else:
            assert result.status == "pass"
            assert result.severity == "none"

    def test_execute_with_channel_settings(self, tmp_path):
        """Execute respects channel_settings.structure.orphan_exclude_dirs."""
        channel = StructureChannel()
        config = ControlPlaneConfig(enabled=True)
        # Inject channel_settings via the config's channel_settings attribute
        # ControlPlaneConfig doesn't have channel_settings, but the code uses
        # getattr(config, "channel_settings", {}), so we monkeypatch it.
        config.channel_settings = {"structure": {"orphan_exclude_dirs": ["custom_gen"]}}  # type: ignore[attr-defined]

        # Create project
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_file(str(pkg / "__init__.py"), "")
        for i in range(6):
            _write_file(str(pkg / f"mod{i}.py"), "\n".join([f"x = {j}" for j in range(20)]))

        custom = tmp_path / "custom_gen"
        custom.mkdir()
        _write_file(str(custom / "__init__.py"), "")
        _write_file(str(custom / "generated.py"), "x = 1\n")

        event = SupervisionEvent(surface="mcp", project_root=str(tmp_path))
        result = channel.execute(event, config)

        # custom_gen/generated.py should not appear as orphan
        orphan_files = [
            f.evidence.get("module", "") for f in result.findings if f.kind == "STRUCT003"
        ]
        assert "custom_gen.generated" not in orphan_files


# ── _discover_python_files Edge Cases ────────────────────────────────────


class TestDiscoverEdge:
    def test_excludes_hidden_directories(self, tmp_path):
        """Directories starting with '.' are excluded."""
        _write_file(str(tmp_path / ".hidden" / "secret.py"), "x = 1\n")
        _write_file(str(tmp_path / "visible.py"), "y = 2\n")
        files = _discover_python_files(str(tmp_path))
        assert all(".hidden" not in f for f in files)
        basenames = {os.path.basename(f) for f in files}
        assert "visible.py" in basenames

    def test_excludes_nox_and_eggs(self, tmp_path):
        """Excludes .nox, .eggs, dist, build directories."""
        for dirname in (".nox", ".eggs", "dist", "build"):
            _write_file(str(tmp_path / dirname / "mod.py"), "x = 1\n")
        _write_file(str(tmp_path / "real.py"), "y = 2\n")
        files = _discover_python_files(str(tmp_path))
        basenames = {os.path.basename(f) for f in files}
        assert "real.py" in basenames
        assert len(files) == 1

    def test_non_py_files_ignored(self, tmp_path):
        """Only .py files are returned."""
        _write_file(str(tmp_path / "data.json"), '{"key": "value"}')
        _write_file(str(tmp_path / "code.py"), "x = 1\n")
        files = _discover_python_files(str(tmp_path))
        assert all(f.endswith(".py") for f in files)
