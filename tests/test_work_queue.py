"""Tests for lintgate/controlplane/work_queue.py — guided work queue (#192)."""

from __future__ import annotations

from lintgate.controlplane.work_queue import (
    QueuedFinding,
    _compute_dependency_tiers,
    build_work_queue,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _finding(file: str, kind: str = "E501", severity: str = "warning") -> dict:
    return {"file": file, "kind": kind, "severity": severity}


# ── _compute_dependency_tiers ────────────────────────────────────────


class TestComputeDependencyTiers:
    def test_empty_graph(self):
        tiers = _compute_dependency_tiers({}, set())
        assert tiers == {}

    def test_leaf_modules(self):
        graph = {"a": [], "b": []}
        tiers = _compute_dependency_tiers(graph, {"a", "b"})
        assert tiers["a"] == 0
        assert tiers["b"] == 0

    def test_linear_chain(self):
        # main imports engine, engine imports helpers
        graph = {
            "main": ["engine"],
            "engine": ["helpers"],
            "helpers": [],
        }
        modules = {"main", "engine", "helpers"}
        tiers = _compute_dependency_tiers(graph, modules)
        assert tiers["helpers"] == 0
        assert tiers["engine"] == 1
        assert tiers["main"] == 2

    def test_diamond_dependency(self):
        # app imports both utils and parsers; both import base
        graph = {
            "app": ["utils", "parsers"],
            "utils": ["base"],
            "parsers": ["base"],
            "base": [],
        }
        modules = {"app", "utils", "parsers", "base"}
        tiers = _compute_dependency_tiers(graph, modules)
        assert tiers["base"] == 0
        assert tiers["utils"] == 1
        assert tiers["parsers"] == 1
        assert tiers["app"] == 2

    def test_external_imports_ignored(self):
        # "requests" is not in project_modules
        graph = {"app": ["requests", "utils"], "utils": []}
        modules = {"app", "utils"}
        tiers = _compute_dependency_tiers(graph, modules)
        assert tiers["utils"] == 0
        assert tiers["app"] == 1

    def test_cycle_gets_max_tier_plus_one(self):
        graph = {"a": ["b"], "b": ["a"], "c": []}
        modules = {"a", "b", "c"}
        tiers = _compute_dependency_tiers(graph, modules)
        assert tiers["c"] == 0
        # a and b are in a cycle, should get max_tier + 1
        assert tiers["a"] == tiers["b"]
        assert tiers["a"] > tiers["c"]


# ── build_work_queue ─────────────────────────────────────────────────


class TestBuildWorkQueue:
    def test_empty_findings(self):
        wq = build_work_queue([])
        assert wq.total_files == 0
        assert wq.items == []
        assert wq.parallelizable_groups == []

    def test_single_finding(self):
        wq = build_work_queue([_finding("mod.py")])
        assert wq.total_files == 1
        assert wq.items[0].file == "mod.py"
        assert wq.items[0].tier == 0  # No graph → all tier 0

    def test_findings_grouped_by_file(self):
        findings = [
            _finding("mod.py", "E501"),
            _finding("mod.py", "F841"),
            _finding("other.py", "E501"),
        ]
        wq = build_work_queue(findings)
        assert wq.total_files == 2
        mod_item = next(i for i in wq.items if i.file == "mod.py")
        assert set(mod_item.finding_ids) == {"E501", "F841"}

    def test_severity_ordering(self):
        findings = [
            _finding("a.py", severity="informational"),
            _finding("b.py", severity="blocking"),
            _finding("c.py", severity="warning"),
        ]
        wq = build_work_queue(findings)
        assert wq.items[0].file == "b.py"  # blocking first
        assert wq.items[1].file == "c.py"  # warning next
        assert wq.items[2].file == "a.py"  # informational last

    def test_tier_ordering_with_graph(self):
        graph = {
            "main": ["engine"],
            "engine": ["helpers"],
            "helpers": [],
        }
        file_map = {
            "main": "main.py",
            "engine": "core/engine.py",
            "helpers": "utils/helpers.py",
        }
        findings = [
            _finding("main.py"),
            _finding("core/engine.py"),
            _finding("utils/helpers.py"),
        ]
        wq = build_work_queue(findings, graph, file_map)
        assert wq.items[0].file == "utils/helpers.py"  # tier 0
        assert wq.items[1].file == "core/engine.py"  # tier 1
        assert wq.items[2].file == "main.py"  # tier 2

    def test_depends_on_populated(self):
        graph = {"app": ["utils"], "utils": []}
        file_map = {"app": "app.py", "utils": "utils.py"}
        findings = [_finding("app.py"), _finding("utils.py")]
        wq = build_work_queue(findings, graph, file_map)
        app_item = next(i for i in wq.items if i.file == "app.py")
        assert "utils.py" in app_item.depends_on

    def test_cross_file_locality(self):
        graph = {"app": ["utils"], "utils": []}
        file_map = {"app": "app.py", "utils": "utils.py"}
        findings = [_finding("app.py"), _finding("utils.py")]
        wq = build_work_queue(findings, graph, file_map)
        app_item = next(i for i in wq.items if i.file == "app.py")
        assert app_item.locality == "cross_file"
        utils_item = next(i for i in wq.items if i.file == "utils.py")
        assert utils_item.locality == "single_file"

    def test_delegation_safe(self):
        graph = {"app": ["utils"], "utils": []}
        file_map = {"app": "app.py", "utils": "utils.py"}
        findings = [
            _finding("app.py", severity="warning"),
            _finding("utils.py", severity="warning"),
        ]
        wq = build_work_queue(findings, graph, file_map)
        utils_item = next(i for i in wq.items if i.file == "utils.py")
        assert utils_item.delegation_safe is True  # tier 0, single_file, non-blocking
        app_item = next(i for i in wq.items if i.file == "app.py")
        assert app_item.delegation_safe is False  # cross_file

    def test_blocking_not_delegation_safe(self):
        findings = [_finding("mod.py", severity="blocking")]
        wq = build_work_queue(findings)
        assert wq.items[0].delegation_safe is False

    def test_parallelizable_groups(self):
        findings = [
            _finding("a.py", severity="warning"),
            _finding("b.py", severity="warning"),
            _finding("c.py", severity="warning"),
        ]
        wq = build_work_queue(findings)
        # All tier 0, single_file, non-blocking → one parallel group
        assert len(wq.parallelizable_groups) == 1
        assert set(wq.parallelizable_groups[0]) == {"a.py", "b.py", "c.py"}

    def test_no_parallel_group_for_single_file(self):
        findings = [_finding("a.py")]
        wq = build_work_queue(findings)
        assert wq.parallelizable_groups == []

    def test_no_graph_all_tier_zero(self):
        findings = [_finding("a.py"), _finding("b.py"), _finding("c.py")]
        wq = build_work_queue(findings, None, None)
        assert all(i.tier == 0 for i in wq.items)


# ── Serialization ────────────────────────────────────────────────────


class TestWorkQueueSerialization:
    def test_to_dict_round_trip(self):
        wq = build_work_queue([_finding("a.py"), _finding("b.py")])
        d = wq.to_dict()
        assert isinstance(d, dict)
        assert "items" in d
        assert "total_files" in d
        assert "parallelizable_groups" in d
        assert d["total_files"] == 2
        assert all(isinstance(item, dict) for item in d["items"])

    def test_queued_finding_to_dict(self):
        qf = QueuedFinding(
            file="mod.py",
            finding_ids=["E501"],
            tier=1,
            severity="warning",
            locality="single_file",
            depends_on=["utils.py"],
            delegation_safe=False,
        )
        d = qf.to_dict()
        assert d["file"] == "mod.py"
        assert d["tier"] == 1
        assert d["depends_on"] == ["utils.py"]
