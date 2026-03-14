"""Comprehensive tests for lintgate/channels/structure/logic.py.

Covers:
  - _find_cycles (DFS cycle detection)
  - _classify_cycle (hard vs soft classification)
  - _check_import_cycles (STRUCT001 finding generation)
  - _percentile (quantile computation)
  - _check_module_size_distribution (STRUCT002 skew detection)
  - _check_package_cohesion (STRUCT004 cohesion ratio)
  - StructureSnapshotInputs + _build_structure_snapshot (orientation data)
"""

from __future__ import annotations

import statistics

from lintgate.channels.structure.logic import (
    _ABSOLUTE_LOC_FLOOR,
    _MIN_FILES_FOR_COHESION,
    _MIN_FILES_FOR_SIZE_ANALYSIS,
    _MIN_IMPORTS_FOR_COHESION,
    _P90_P50_WARNING_RATIO,
    _STRUCTURAL_CONFIG_FILES,
    StructureSnapshotInputs,
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_package_cohesion,
    _classify_cycle,
    _find_cycles,
    _percentile,
)

# ── _find_cycles ─────────────────────────────────────────────────────────


class TestFindCycles:
    """Tests for the DFS cycle detection algorithm."""

    def test_empty_graph(self):
        result = _find_cycles({})
        assert result == []

    def test_single_node_no_cycle(self):
        result = _find_cycles({"a": set()})
        assert result == []

    def test_self_loop(self):
        result = _find_cycles({"a": {"a"}})
        assert len(result) == 1
        assert result[0] == ["a"]

    def test_two_node_cycle(self):
        graph = {"a": {"b"}, "b": {"a"}}
        result = _find_cycles(graph)
        assert len(result) >= 1
        # At least one cycle should contain both nodes
        found = any(set(c) == {"a", "b"} for c in result)
        assert found

    def test_three_node_cycle(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        result = _find_cycles(graph)
        assert len(result) >= 1
        found = any(set(c) == {"a", "b", "c"} for c in result)
        assert found

    def test_no_cycle_dag(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        result = _find_cycles(graph)
        assert result == []

    def test_diamond_no_cycle(self):
        graph = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}
        result = _find_cycles(graph)
        assert result == []

    def test_max_depth_5_cap(self):
        """Cycles longer than 5 nodes should not be detected (depth limit)."""
        # Build a chain a->b->c->d->e->f->g->a (length 7)
        graph = {
            "a": {"b"},
            "b": {"c"},
            "c": {"d"},
            "d": {"e"},
            "e": {"f"},
            "f": {"g"},
            "g": {"a"},
        }
        result = _find_cycles(graph)
        # The DFS max depth is 5, so a 7-node cycle should not be found
        assert result == []

    def test_cycle_of_length_5(self):
        """Cycles of exactly length 5 should be detectable."""
        graph = {
            "a": {"b"},
            "b": {"c"},
            "c": {"d"},
            "d": {"e"},
            "e": {"a"},
        }
        result = _find_cycles(graph)
        assert len(result) >= 1
        found = any(set(c) == {"a", "b", "c", "d", "e"} for c in result)
        assert found

    def test_disconnected_components_one_cycle(self):
        graph = {
            "a": {"b"},
            "b": {"a"},
            "x": {"y"},
            "y": set(),
        }
        result = _find_cycles(graph)
        assert len(result) >= 1
        # Only a-b cycle exists
        for cycle in result:
            assert set(cycle).issubset({"a", "b"})

    def test_multiple_independent_cycles(self):
        graph = {
            "a": {"b"},
            "b": {"a"},
            "x": {"y"},
            "y": {"x"},
        }
        result = _find_cycles(graph)
        assert len(result) >= 2


# ── _classify_cycle ──────────────────────────────────────────────────────


class TestClassifyCycle:
    """Tests for hard vs soft cycle classification."""

    def test_hard_cycle_no_deferred_edges(self):
        cycle = ["a", "b", "c"]
        deferred: set[tuple[str, str]] = set()
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "hard"
        assert info == []

    def test_soft_cycle_one_deferred_edge(self):
        cycle = ["a", "b", "c"]
        deferred = {("a", "b")}
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "soft"
        assert "a \u2192 b" in info

    def test_soft_cycle_multiple_deferred_edges(self):
        cycle = ["a", "b", "c"]
        deferred = {("a", "b"), ("b", "c")}
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "soft"
        assert len(info) == 2

    def test_deferred_edge_wraps_around(self):
        """The last node in the cycle connects back to the first."""
        cycle = ["a", "b"]
        deferred = {("b", "a")}
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "soft"
        assert "b \u2192 a" in info

    def test_irrelevant_deferred_edges_ignored(self):
        cycle = ["a", "b"]
        deferred = {("x", "y")}
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "hard"
        assert info == []

    def test_single_node_cycle(self):
        cycle = ["a"]
        deferred = {("a", "a")}
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "soft"
        assert "a \u2192 a" in info

    def test_single_node_hard(self):
        cycle = ["a"]
        deferred: set[tuple[str, str]] = set()
        classification, info = _classify_cycle(cycle, deferred)
        assert classification == "hard"


# ── _check_import_cycles ─────────────────────────────────────────────────


class TestCheckImportCycles:
    """Tests for STRUCT001 finding generation."""

    def test_no_cycles_no_findings(self):
        graph = {"a": {"b"}, "b": set()}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        assert findings == []

    def test_hard_cycle_produces_struct001(self):
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        assert len(findings) >= 1
        f = findings[0]
        assert f.kind == "STRUCT001"
        assert f.linter == "structure_channel"
        assert f.evidence["classification"] == "hard"
        assert f.confidence == 0.9

    def test_hard_cycle_two_nodes_informational(self):
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        # 2-node cycle -> informational severity
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        assert any(f.severity == "informational" for f in struct001)

    def test_hard_cycle_three_nodes_warning(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py", "c": "/proj/c.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        assert any(f.severity == "warning" for f in struct001)

    def test_soft_cycle_lower_confidence(self):
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        deferred = {("a", "b")}
        findings = _check_import_cycles(graph, file_map, "/proj", deferred)
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        assert len(struct001) >= 1
        f = struct001[0]
        assert f.evidence["classification"] == "soft"
        assert f.confidence == 0.5
        assert f.severity == "informational"

    def test_soft_cycle_message_mentions_deferred(self):
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        deferred = {("b", "a")}
        findings = _check_import_cycles(graph, file_map, "/proj", deferred)
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        assert any("Soft import cycle" in f.message or "deferred" in f.message for f in struct001)

    def test_duplicate_cycles_deduped(self):
        """frozenset dedup prevents reporting the same cycle twice."""
        # A->B->A detected from both start nodes should appear once
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        assert len(struct001) == 1

    def test_cycle_with_no_relevant_files_skipped(self):
        """Cycles where no module has a file_map entry are skipped."""
        graph = {"x": {"y"}, "y": {"x"}}
        file_map: dict[str, str] = {}  # No file mappings
        findings = _check_import_cycles(graph, file_map, "/proj")
        assert findings == []

    def test_cycle_evidence_contains_length(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py", "c": "/proj/c.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        assert struct001[0].evidence["length"] == 3

    def test_cycle_str_format(self):
        """The cycle string should be 'a -> b -> ... -> a' (wraps back)."""
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": "/proj/a.py", "b": "/proj/b.py"}
        findings = _check_import_cycles(graph, file_map, "/proj")
        struct001 = [f for f in findings if f.kind == "STRUCT001"]
        msg = struct001[0].message
        # The message should contain the wrap-back arrow
        assert "\u2192" in msg


# ── _percentile ──────────────────────────────────────────────────────────


class TestPercentile:
    """Tests for the quantile computation helper."""

    def test_empty_data(self):
        assert _percentile([], 0.5) == 0.0

    def test_single_element(self):
        assert _percentile([42], 0.5) == 42.0
        assert _percentile([42], 0.0) == 42.0
        assert _percentile([42], 1.0) == 42.0

    def test_two_elements_median(self):
        result = _percentile([10, 20], 0.5)
        assert result == 15.0

    def test_p0_returns_first(self):
        result = _percentile([10, 20, 30], 0.0)
        assert result == 10.0

    def test_p100_returns_last(self):
        result = _percentile([10, 20, 30], 1.0)
        assert result == 30.0

    def test_p90_interpolation(self):
        data = sorted([100, 200, 300, 400, 500])
        result = _percentile(data, 0.90)
        # (n-1)*0.9 = 4*0.9 = 3.6 -> f=3, c=4
        # d0=400, d1=500, result = 400 + (500-400)*0.6 = 460
        assert result == 460.0

    def test_p50_of_odd_length(self):
        data = sorted([1, 2, 3, 4, 5])
        result = _percentile(data, 0.5)
        # (n-1)*0.5 = 4*0.5 = 2.0 -> f=2, c=3, k-f=0 -> exactly data[2]
        assert result == 3.0

    def test_p25_interpolation(self):
        data = sorted([10, 20, 30, 40])
        result = _percentile(data, 0.25)
        # (n-1)*0.25 = 3*0.25 = 0.75 -> f=0, c=1
        # d0=10, d1=20, result = 10 + (20-10)*0.75 = 17.5
        assert result == 17.5

    def test_result_is_float(self):
        result = _percentile([100], 0.5)
        assert isinstance(result, float)


# ── _check_module_size_distribution ──────────────────────────────────────


class TestCheckModuleSizeDistribution:
    """Tests for STRUCT002 size skew detection."""

    def test_too_few_files_no_findings(self):
        """Fewer than _MIN_FILES_FOR_SIZE_ANALYSIS meaningful files -> no findings."""
        file_loc = {f"/proj/f{i}.py": 100 for i in range(_MIN_FILES_FOR_SIZE_ANALYSIS - 1)}
        findings = _check_module_size_distribution(file_loc, "/proj")
        assert findings == []

    def test_files_below_floor_filtered_out(self):
        """Files below _ABSOLUTE_LOC_FLOOR don't count toward the sample."""
        # 10 files but all below the floor
        file_loc = {f"/proj/f{i}.py": _ABSOLUTE_LOC_FLOOR - 1 for i in range(10)}
        findings = _check_module_size_distribution(file_loc, "/proj")
        assert findings == []

    def test_uniform_sizes_no_finding(self):
        """All files same size -> p90/p50 = 1.0, below threshold."""
        file_loc = {f"/proj/f{i}.py": 100 for i in range(10)}
        findings = _check_module_size_distribution(file_loc, "/proj")
        assert findings == []

    def test_high_skew_produces_struct002(self):
        """Enough large outliers to push p90/p50 above threshold."""
        # 7 files at 60 LOC (p50=60), 3 files at 1000 LOC (p90=1000)
        # ratio = 1000/60 = 16.67, well above _P90_P50_WARNING_RATIO=5
        file_loc = {}
        for i in range(7):
            file_loc[f"/proj/f{i}.py"] = 60
        for i in range(3):
            file_loc[f"/proj/big{i}.py"] = 1000
        findings = _check_module_size_distribution(file_loc, "/proj")
        struct002 = [f for f in findings if f.kind == "STRUCT002"]
        assert len(struct002) == 1
        f = struct002[0]
        assert f.severity == "informational"
        assert f.confidence == 0.85
        assert f.linter == "structure_channel"
        assert "ratio" in f.evidence
        assert f.evidence["ratio"] >= _P90_P50_WARNING_RATIO

    def test_struct002_evidence_fields(self):
        """Verify all expected evidence fields are present."""
        # 7 small files + 3 large files to ensure p90/p50 >= 5
        file_loc = {}
        for i in range(7):
            file_loc[f"/proj/f{i}.py"] = 55
        for i in range(3):
            file_loc[f"/proj/huge{i}.py"] = 2000
        findings = _check_module_size_distribution(file_loc, "/proj")
        struct002 = [f for f in findings if f.kind == "STRUCT002"]
        assert len(struct002) == 1
        ev = struct002[0].evidence
        assert "p50_loc" in ev
        assert "p90_loc" in ev
        assert "sample_size" in ev
        assert "outlier_count" in ev
        assert "outliers" in ev
        assert ev["code"] == "STRUCT002"

    def test_outliers_capped_at_5(self):
        """Only top 5 outliers appear in evidence."""
        file_loc = {}
        for i in range(20):
            file_loc[f"/proj/small{i}.py"] = 55
        for i in range(10):
            file_loc[f"/proj/big{i}.py"] = 5000 + i * 100
        findings = _check_module_size_distribution(file_loc, "/proj")
        struct002 = [f for f in findings if f.kind == "STRUCT002"]
        assert len(struct002) == 1
        assert len(struct002[0].evidence["outliers"]) <= 5

    def test_outlier_relpath_is_relative(self):
        """Outlier file paths in evidence should be relative to project root."""
        file_loc = {}
        for i in range(9):
            file_loc[f"/proj/src/f{i}.py"] = 55
        file_loc["/proj/src/monster.py"] = 5000
        findings = _check_module_size_distribution(file_loc, "/proj")
        struct002 = [f for f in findings if f.kind == "STRUCT002"]
        if struct002:
            for outlier in struct002[0].evidence["outliers"]:
                assert not outlier["file"].startswith("/")

    def test_p50_zero_no_crash(self):
        """If p50 is 0, function should return without ZeroDivisionError."""
        # All files at exactly _ABSOLUTE_LOC_FLOOR
        file_loc = {f"/proj/f{i}.py": _ABSOLUTE_LOC_FLOOR for i in range(10)}
        # This should not raise
        findings = _check_module_size_distribution(file_loc, "/proj")
        assert isinstance(findings, list)

    def test_moderate_skew_no_finding(self):
        """Ratio below threshold should not trigger STRUCT002."""
        # Create distribution with p90/p50 < 5
        file_loc = {}
        for i in range(8):
            file_loc[f"/proj/f{i}.py"] = 100
        file_loc["/proj/bigger.py"] = 200
        file_loc["/proj/biggest.py"] = 300
        findings = _check_module_size_distribution(file_loc, "/proj")
        struct002 = [f for f in findings if f.kind == "STRUCT002"]
        assert struct002 == []

    def test_empty_file_loc(self):
        findings = _check_module_size_distribution({}, "/proj")
        assert findings == []


# ── _check_package_cohesion ──────────────────────────────────────────────


class TestCheckPackageCohesion:
    """Tests for STRUCT004 package cohesion analysis."""

    def test_fewer_than_two_packages_no_findings(self):
        """Need at least 2 packages for meaningful cohesion analysis."""
        import_graph = {"pkg.a": {"pkg.b"}, "pkg.b": set()}
        file_map = {"pkg.a": "/proj/pkg/a.py", "pkg.b": "/proj/pkg/b.py"}
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        assert findings == []

    def test_top_level_modules_skipped(self):
        """Modules without dots (top-level) are not assigned to any package."""
        import_graph = {"utils": {"helpers"}, "helpers": set()}
        file_map = {"utils": "/proj/utils.py", "helpers": "/proj/helpers.py"}
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        assert findings == []

    def test_high_cohesion_no_finding(self):
        """Packages with > 30% intra-package imports are fine."""
        import_graph = {
            "alpha.a": {"alpha.b", "alpha.c"},
            "alpha.b": {"alpha.c"},
            "alpha.c": set(),
            "beta.x": {"beta.y"},
            "beta.y": set(),
        }
        file_map = {
            "alpha.a": "/proj/alpha/a.py",
            "alpha.b": "/proj/alpha/b.py",
            "alpha.c": "/proj/alpha/c.py",
            "beta.x": "/proj/beta/x.py",
            "beta.y": "/proj/beta/y.py",
        }
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        struct004 = [f for f in findings if f.kind == "STRUCT004"]
        # alpha has 3 intra-imports, 0 inter-imports -> 100% cohesion
        assert not any(f.evidence["package"] == "alpha" for f in struct004)

    def test_low_cohesion_triggers_struct004(self):
        """Package with mostly inter-package imports triggers finding."""
        import_graph = {
            "alpha.a": {"beta.x", "beta.y", "gamma.z"},
            "alpha.b": {"beta.x"},
            "beta.x": set(),
            "beta.y": set(),
            "gamma.z": set(),
        }
        file_map = {
            "alpha.a": "/proj/alpha/a.py",
            "alpha.b": "/proj/alpha/b.py",
            "beta.x": "/proj/beta/x.py",
            "beta.y": "/proj/beta/y.py",
            "gamma.z": "/proj/gamma/z.py",
        }
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        struct004 = [f for f in findings if f.kind == "STRUCT004"]
        alpha_findings = [f for f in struct004 if f.evidence["package"] == "alpha"]
        assert len(alpha_findings) == 1
        f = alpha_findings[0]
        assert f.severity == "informational"
        assert f.confidence == 0.7
        assert f.evidence["cohesion_ratio"] < 0.3
        assert f.evidence["inter_imports"] > f.evidence["intra_imports"]

    def test_too_few_imports_skipped(self):
        """Packages with fewer than _MIN_IMPORTS_FOR_COHESION imports are skipped."""
        import_graph = {
            "alpha.a": {"beta.x"},
            "beta.x": {"alpha.a"},
        }
        file_map = {
            "alpha.a": "/proj/alpha/a.py",
            "beta.x": "/proj/beta/x.py",
        }
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        struct004 = [f for f in findings if f.kind == "STRUCT004"]
        # Each package has only 1 import, below threshold
        assert struct004 == []

    def test_cohesion_evidence_fields(self):
        """Verify evidence contains expected fields."""
        import_graph = {
            "alpha.a": {"beta.x", "beta.y", "beta.z"},
            "alpha.b": {"beta.x"},
            "beta.x": {"beta.y"},
            "beta.y": set(),
            "beta.z": set(),
        }
        file_map = {
            "alpha.a": "/proj/alpha/a.py",
            "alpha.b": "/proj/alpha/b.py",
            "beta.x": "/proj/beta/x.py",
            "beta.y": "/proj/beta/y.py",
            "beta.z": "/proj/beta/z.py",
        }
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        struct004 = [f for f in findings if f.kind == "STRUCT004"]
        alpha_findings = [f for f in struct004 if f.evidence["package"] == "alpha"]
        if alpha_findings:
            ev = alpha_findings[0].evidence
            assert "intra_imports" in ev
            assert "inter_imports" in ev
            assert "total_imports" in ev
            assert "cohesion_ratio" in ev
            assert "module_count" in ev
            assert ev["code"] == "STRUCT004"

    def test_prefix_matching_for_intra_imports(self):
        """Imports matching pkg_prefix (e.g., 'alpha.sub.x') count as intra."""
        import_graph = {
            "alpha.a": {"alpha.sub.x", "alpha.sub.y", "alpha.b"},
            "alpha.b": set(),
            "beta.z": set(),
        }
        file_map = {
            "alpha.a": "/proj/alpha/a.py",
            "alpha.b": "/proj/alpha/b.py",
            "alpha.sub.x": "/proj/alpha/sub/x.py",
            "alpha.sub.y": "/proj/alpha/sub/y.py",
            "beta.z": "/proj/beta/z.py",
        }
        findings = _check_package_cohesion(import_graph, file_map, "/proj")
        struct004 = [f for f in findings if f.kind == "STRUCT004"]
        # alpha.a imports 3 intra-package modules and 0 inter-package
        alpha_findings = [f for f in struct004 if f.evidence.get("package") == "alpha"]
        assert alpha_findings == []  # High cohesion, no finding

    def test_empty_graph_no_findings(self):
        findings = _check_package_cohesion({}, {}, "/proj")
        assert findings == []


# ── StructureSnapshotInputs & _build_structure_snapshot ──────────────────


class TestBuildStructureSnapshot:
    """Tests for the compact orientation snapshot builder."""

    def _make_inputs(self, **overrides) -> StructureSnapshotInputs:
        defaults = {
            "py_files": ["/proj/pkg/a.py", "/proj/pkg/b.py"],
            "file_map": {"pkg.a": "/proj/pkg/a.py", "pkg.b": "/proj/pkg/b.py"},
            "file_loc": {"/proj/pkg/a.py": 100, "/proj/pkg/b.py": 200},
            "project_root": "/proj",
            "cycle_count": 0,
            "orphan_count": 0,
            "cohesion_count": 0,
            "module_fan_in": None,
        }
        defaults.update(overrides)
        return StructureSnapshotInputs(**defaults)  # type: ignore[arg-type]  # dict[str, object] unpacking

    def test_basic_snapshot_fields(self):
        inputs = self._make_inputs()
        snap = _build_structure_snapshot(inputs)
        assert snap["file_count"] == 2
        assert snap["total_loc"] == 300
        assert snap["median_module_loc"] == int(statistics.median([100, 200]))
        assert snap["package_count"] >= 1
        assert snap["import_cycle_count"] == 0
        assert snap["orphan_count"] == 0
        assert snap["low_cohesion_packages"] == 0
        assert snap["checks_run"] == 4

    def test_largest_modules_sorted_desc(self):
        file_loc = {
            "/proj/pkg/a.py": 50,
            "/proj/pkg/b.py": 300,
            "/proj/pkg/c.py": 150,
        }
        inputs = self._make_inputs(
            py_files=list(file_loc.keys()),
            file_map={
                "pkg.a": "/proj/pkg/a.py",
                "pkg.b": "/proj/pkg/b.py",
                "pkg.c": "/proj/pkg/c.py",
            },
            file_loc=file_loc,
        )
        snap = _build_structure_snapshot(inputs)
        largest = snap["largest_modules"]
        assert len(largest) == 3
        assert largest[0]["loc"] >= largest[1]["loc"] >= largest[2]["loc"]

    def test_largest_modules_capped_at_3(self):
        file_loc = {f"/proj/pkg/f{i}.py": (i + 1) * 10 for i in range(10)}
        file_map = {f"pkg.f{i}": f"/proj/pkg/f{i}.py" for i in range(10)}
        inputs = self._make_inputs(
            py_files=list(file_loc.keys()),
            file_map=file_map,
            file_loc=file_loc,
        )
        snap = _build_structure_snapshot(inputs)
        assert len(snap["largest_modules"]) == 3

    def test_largest_modules_relpath(self):
        inputs = self._make_inputs()
        snap = _build_structure_snapshot(inputs)
        for entry in snap["largest_modules"]:
            assert not entry["file"].startswith("/")

    def test_package_distribution(self):
        file_map = {
            "alpha.a": "/proj/alpha/a.py",
            "alpha.b": "/proj/alpha/b.py",
            "beta.x": "/proj/beta/x.py",
            "utils": "/proj/utils.py",
        }
        inputs = self._make_inputs(
            py_files=["/proj/alpha/a.py", "/proj/alpha/b.py", "/proj/beta/x.py", "/proj/utils.py"],
            file_map=file_map,
            file_loc=dict.fromkeys(
                ["/proj/alpha/a.py", "/proj/alpha/b.py", "/proj/beta/x.py", "/proj/utils.py"], 50
            ),
        )
        snap = _build_structure_snapshot(inputs)
        assert snap["packages"]["alpha"] == 2
        assert snap["packages"]["beta"] == 1
        assert snap["packages"]["<top-level>"] == 1
        assert snap["package_count"] == 3

    def test_zero_loc_files_excluded_from_median(self):
        """Files with loc=0 are excluded from median calculation."""
        file_loc = {
            "/proj/pkg/a.py": 100,
            "/proj/pkg/b.py": 0,
            "/proj/pkg/c.py": 200,
        }
        inputs = self._make_inputs(
            py_files=list(file_loc.keys()),
            file_map={
                "pkg.a": "/proj/pkg/a.py",
                "pkg.b": "/proj/pkg/b.py",
                "pkg.c": "/proj/pkg/c.py",
            },
            file_loc=file_loc,
        )
        snap = _build_structure_snapshot(inputs)
        # Only non-zero locs: [100, 200], median = 150
        assert snap["median_module_loc"] == 150
        # total_loc excludes zero
        assert snap["total_loc"] == 300

    def test_all_zero_loc(self):
        file_loc = {"/proj/pkg/a.py": 0, "/proj/pkg/b.py": 0}
        inputs = self._make_inputs(
            py_files=list(file_loc.keys()),
            file_map={"pkg.a": "/proj/pkg/a.py", "pkg.b": "/proj/pkg/b.py"},
            file_loc=file_loc,
        )
        snap = _build_structure_snapshot(inputs)
        assert snap["median_module_loc"] == 0
        assert snap["total_loc"] == 0

    def test_cycle_orphan_cohesion_counts_passthrough(self):
        inputs = self._make_inputs(cycle_count=3, orphan_count=7, cohesion_count=2)
        snap = _build_structure_snapshot(inputs)
        assert snap["import_cycle_count"] == 3
        assert snap["orphan_count"] == 7
        assert snap["low_cohesion_packages"] == 2

    def test_fan_in_enrichment_present(self):
        fan_in = {"pkg.a": 5, "pkg.b": 0, "pkg.c": 3}
        inputs = self._make_inputs(module_fan_in=fan_in)
        snap = _build_structure_snapshot(inputs)
        assert "zero_fan_in_count" in snap
        assert snap["zero_fan_in_count"] == 1  # pkg.b has fan_in=0
        assert "high_fan_in_modules" in snap
        # pkg.a (5) and pkg.c (3) have fan_in >= 2
        high = snap["high_fan_in_modules"]
        assert len(high) == 2
        assert high[0]["fan_in"] >= high[1]["fan_in"]

    def test_fan_in_enrichment_absent(self):
        inputs = self._make_inputs(module_fan_in=None)
        snap = _build_structure_snapshot(inputs)
        assert "zero_fan_in_count" not in snap
        assert "high_fan_in_modules" not in snap

    def test_fan_in_high_capped_at_3(self):
        fan_in = {f"pkg.m{i}": i + 2 for i in range(10)}  # all >= 2
        inputs = self._make_inputs(module_fan_in=fan_in)
        snap = _build_structure_snapshot(inputs)
        assert len(snap["high_fan_in_modules"]) <= 3

    def test_fan_in_below_threshold_excluded(self):
        fan_in = {"pkg.a": 1, "pkg.b": 0}  # all below 2
        inputs = self._make_inputs(module_fan_in=fan_in)
        snap = _build_structure_snapshot(inputs)
        assert snap["high_fan_in_modules"] == []
        assert snap["zero_fan_in_count"] == 1

    def test_empty_inputs(self):
        inputs = StructureSnapshotInputs(
            py_files=[],
            file_map={},
            file_loc={},
            project_root="/proj",
        )
        snap = _build_structure_snapshot(inputs)
        assert snap["file_count"] == 0
        assert snap["total_loc"] == 0
        assert snap["median_module_loc"] == 0
        assert snap["largest_modules"] == []
        assert snap["package_count"] == 0


# ── Constants sanity checks ──────────────────────────────────────────────


class TestConstants:
    """Sanity checks for module-level constants."""

    def test_min_files_for_size_analysis(self):
        assert _MIN_FILES_FOR_SIZE_ANALYSIS >= 1

    def test_min_files_for_cohesion(self):
        assert _MIN_FILES_FOR_COHESION >= 1

    def test_p90_p50_ratio_positive(self):
        assert _P90_P50_WARNING_RATIO > 0

    def test_absolute_loc_floor_positive(self):
        assert _ABSOLUTE_LOC_FLOOR > 0

    def test_min_imports_for_cohesion_positive(self):
        assert _MIN_IMPORTS_FOR_COHESION > 0

    def test_structural_config_files_nonempty(self):
        assert len(_STRUCTURAL_CONFIG_FILES) > 0
        assert "pyproject.toml" in _STRUCTURAL_CONFIG_FILES
        assert "setup.py" in _STRUCTURAL_CONFIG_FILES
