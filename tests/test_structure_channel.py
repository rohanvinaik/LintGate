"""Tests for the structure channel — codebase structural analysis.

Tests cover all four STRUCT checks:
  STRUCT001 — Import cycle detection
  STRUCT002 — Module-size distribution skew
  STRUCT003 — Orphan detection
  STRUCT004 — Package cohesion ratio

Also tests: should_run gating, structure snapshot, graceful degradation,
false-positive exclusions, and minimum sample-size guards.
"""

from __future__ import annotations

import os
import textwrap

import pytest

from lintgate.channels.structure_channel import (
    StructureChannel,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _count_loc,
    _discover_python_files,
    _find_cycles,
    _is_orphan_excluded,
    _percentile,
)
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.types import ChangeClassification

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def channel():
    return StructureChannel()


@pytest.fixture
def mcp_event(tmp_path):
    """Supervision event for MCP surface."""
    return SupervisionEvent(
        surface="mcp",
        project_root=str(tmp_path),
        tool_name="controlplane_run",
    )


@pytest.fixture
def config():
    return ControlPlaneConfig(enabled=True)


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))


# ── Channel Protocol Tests ───────────────────────────────────────────────


class TestChannelProtocol:
    """Verify the channel satisfies the Channel protocol."""

    def test_name(self, channel):
        assert channel.name == "structure"

    def test_timeout_ms(self, channel):
        assert channel.timeout_ms == 5000

    def test_blocking_capable(self, channel):
        assert channel.blocking_capable is False

    def test_should_run_mcp(self, channel, mcp_event, config):
        assert channel.should_run(mcp_event, config) is True

    def test_should_run_hook_with_changes(self, channel, config, tmp_path):
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            change_classification=ChangeClassification(
                files_changed=["foo.py"],
                change_kind="logic",
                risk_level="moderate",
            ),
        )
        assert channel.should_run(event, config) is True

    def test_should_run_hook_no_changes(self, channel, config, tmp_path):
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
            change_classification=ChangeClassification(
                files_changed=[],
                change_kind="none",
                risk_level="none",
            ),
        )
        assert channel.should_run(event, config) is False

    def test_should_run_hook_no_classification(self, channel, config, tmp_path):
        event = SupervisionEvent(
            surface="hook",
            project_root=str(tmp_path),
        )
        assert channel.should_run(event, config) is False

    def test_execute_too_few_files_skips(self, channel, mcp_event, config, tmp_path):
        """Channels skip gracefully when < minimum file count."""
        _write_file(str(tmp_path / "one.py"), "x = 1\n")
        result = channel.execute(mcp_event, config)
        assert result.status == "skip"
        assert result.metrics["reason"] == "too_few_files"


# ── STRUCT001: Import Cycle Tests ────────────────────────────────────────


class TestImportCycles:
    """Tests for import cycle detection."""

    def test_no_cycles(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        cycles = _find_cycles(graph)
        assert cycles == []

    def test_simple_cycle(self):
        graph = {"a": {"b"}, "b": {"a"}}
        cycles = _find_cycles(graph)
        assert len(cycles) >= 1
        cycle_nodes = set(cycles[0])
        assert cycle_nodes == {"a", "b"}

    def test_triangle_cycle(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycles = _find_cycles(graph)
        assert len(cycles) >= 1
        cycle_nodes = set(cycles[0])
        assert cycle_nodes == {"a", "b", "c"}

    def test_max_depth_respected(self):
        """Cycles deeper than 5 are not reported."""
        # Chain: a→b→c→d→e→f→g→a — 7 nodes, exceeds max depth
        graph = {
            "a": {"b"},
            "b": {"c"},
            "c": {"d"},
            "d": {"e"},
            "e": {"f"},
            "f": {"g"},
            "g": {"a"},
        }
        cycles = _find_cycles(graph)
        # The 7-node cycle should NOT be found (max depth 5)
        for c in cycles:
            assert len(c) <= 5

    def test_cycle_findings_have_correct_code(self, tmp_path):
        graph = {"pkg.a": {"pkg.b"}, "pkg.b": {"pkg.a"}}
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.b": str(tmp_path / "pkg" / "b.py"),
        }
        findings = _check_import_cycles(graph, file_map, str(tmp_path))
        assert len(findings) >= 1
        assert findings[0].kind == "STRUCT001"
        assert findings[0].linter == "structure_channel"
        assert findings[0].evidence["code"] == "STRUCT001"
        assert "cycle" in findings[0].evidence

    def test_deduplicates_cycles(self, tmp_path):
        """Same cycle found from different start nodes is reported once."""
        graph = {"a": {"b"}, "b": {"a"}}
        file_map = {"a": str(tmp_path / "a.py"), "b": str(tmp_path / "b.py")}
        findings = _check_import_cycles(graph, file_map, str(tmp_path))
        assert len(findings) == 1


# ── STRUCT002: Module-Size Distribution Tests ────────────────────────────


class TestModuleSizeDistribution:
    def test_no_findings_when_balanced(self, tmp_path):
        """Balanced file sizes produce no findings."""
        file_loc = {str(tmp_path / f"mod{i}.py"): 100 + i * 5 for i in range(10)}
        findings = _check_module_size_distribution(file_loc, str(tmp_path))
        assert findings == []

    def test_findings_when_skewed(self, tmp_path):
        """Multiple large files among small ones triggers STRUCT002."""
        file_loc = {}
        for i in range(7):
            file_loc[str(tmp_path / f"small{i}.py")] = 60
        # Several large files to push p90 well above 5x the median
        file_loc[str(tmp_path / "big1.py")] = 800
        file_loc[str(tmp_path / "big2.py")] = 1200
        file_loc[str(tmp_path / "giant.py")] = 2000
        findings = _check_module_size_distribution(file_loc, str(tmp_path))
        assert len(findings) == 1
        assert findings[0].kind == "STRUCT002"
        assert findings[0].evidence["code"] == "STRUCT002"
        assert "ratio" in findings[0].evidence
        assert "outliers" in findings[0].evidence

    def test_skips_when_too_few_files(self, tmp_path):
        """Not enough files above floor → no findings."""
        file_loc = {str(tmp_path / "a.py"): 200}
        findings = _check_module_size_distribution(file_loc, str(tmp_path))
        assert findings == []

    def test_ignores_tiny_files(self, tmp_path):
        """Files below _ABSOLUTE_LOC_FLOOR are excluded."""
        file_loc = {}
        for i in range(10):
            file_loc[str(tmp_path / f"tiny{i}.py")] = 10  # Below floor
        file_loc[str(tmp_path / "big.py")] = 500
        # Only 1 file above floor → too few
        findings = _check_module_size_distribution(file_loc, str(tmp_path))
        assert findings == []


class TestPercentile:
    def test_p50(self):
        assert _percentile([1, 2, 3, 4, 5], 0.5) == 3.0

    def test_p90(self):
        data = list(range(1, 101))  # 1..100
        assert _percentile(data, 0.90) == pytest.approx(90.01, abs=0.1)

    def test_single_value(self):
        assert _percentile([42], 0.90) == 42.0

    def test_empty(self):
        assert _percentile([], 0.50) == 0.0


# ── STRUCT003: Orphan Detection Tests ────────────────────────────────────


class TestOrphanDetection:
    def test_no_orphans_when_all_imported(self, tmp_path):
        graph = {"pkg.a": {"pkg.b"}, "pkg.b": {"pkg.a"}}
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.b": str(tmp_path / "pkg" / "b.py"),
        }
        findings = _check_orphans(
            [str(tmp_path / "pkg" / "a.py"), str(tmp_path / "pkg" / "b.py")],
            graph,
            file_map,
            str(tmp_path),
        )
        assert findings == []

    def test_orphan_detected(self, tmp_path):
        graph = {"pkg.a": {"pkg.b"}}
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.b": str(tmp_path / "pkg" / "b.py"),
            "pkg.orphan": str(tmp_path / "pkg" / "orphan.py"),
        }
        py_files = [file_map[m] for m in file_map]
        findings = _check_orphans(py_files, graph, file_map, str(tmp_path))
        assert len(findings) >= 1
        orphan_modules = {f.evidence["module"] for f in findings}
        assert "pkg.orphan" in orphan_modules

    def test_parent_package_counts_as_referenced(self, tmp_path):
        """If pkg.sub.foo is imported, pkg.sub is considered referenced."""
        graph = {"pkg.main": {"pkg.sub.foo"}}
        file_map = {
            "pkg.main": str(tmp_path / "pkg" / "main.py"),
            "pkg.sub.foo": str(tmp_path / "pkg" / "sub" / "foo.py"),
            "pkg.sub": str(tmp_path / "pkg" / "sub" / "__init__.py"),
        }
        py_files = [file_map[m] for m in file_map]
        findings = _check_orphans(py_files, graph, file_map, str(tmp_path))
        orphan_modules = {f.evidence["module"] for f in findings}
        # pkg.sub should NOT be reported as orphan (parent of imported module)
        assert "pkg.sub" not in orphan_modules

    def test_orphan_has_correct_code(self, tmp_path):
        graph = {}
        file_map = {"pkg.lonely": str(tmp_path / "pkg" / "lonely.py")}
        findings = _check_orphans([file_map["pkg.lonely"]], graph, file_map, str(tmp_path))
        assert len(findings) == 1
        assert findings[0].kind == "STRUCT003"
        assert findings[0].confidence == 0.6


class TestOrphanExclusions:
    def test_init_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "pkg" / "__init__.py"), "pkg", str(tmp_path))

    def test_main_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "main.py"), "main", str(tmp_path))

    def test_conftest_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "conftest.py"), "conftest", str(tmp_path))

    def test_migration_dir_excluded(self, tmp_path):
        assert _is_orphan_excluded(
            str(tmp_path / "migrations" / "0001.py"), "migrations.0001", str(tmp_path)
        )

    def test_test_file_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "test_foo.py"), "test_foo", str(tmp_path))

    def test_test_dir_excluded(self, tmp_path):
        assert _is_orphan_excluded(
            str(tmp_path / "tests" / "helpers.py"), "tests.helpers", str(tmp_path)
        )

    def test_regular_file_not_excluded(self, tmp_path):
        assert not _is_orphan_excluded(
            str(tmp_path / "pkg" / "utils.py"), "pkg.utils", str(tmp_path)
        )

    def test_top_level_module_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "tool.py"), "tool", str(tmp_path))

    def test_shebang_file_excluded(self, tmp_path):
        filepath = str(tmp_path / "script.py")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write("#!/usr/bin/env python3\nprint('hello')\n")
        assert _is_orphan_excluded(filepath, "script", str(tmp_path))

    def test_main_guard_file_excluded(self, tmp_path):
        filepath = str(tmp_path / "pkg" / "runner.py")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write("if __name__ == '__main__':\n    raise SystemExit(0)\n")
        assert _is_orphan_excluded(filepath, "pkg.runner", str(tmp_path))

    def test_cli_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "cli.py"), "cli", str(tmp_path))

    def test_server_excluded(self, tmp_path):
        assert _is_orphan_excluded(str(tmp_path / "server.py"), "server", str(tmp_path))

    def test_plugins_dir_excluded(self, tmp_path):
        assert _is_orphan_excluded(
            str(tmp_path / "plugins" / "custom.py"), "plugins.custom", str(tmp_path)
        )


# ── STRUCT004: Package Cohesion Tests ────────────────────────────────────


class TestPackageCohesion:
    def test_high_cohesion_no_findings(self, tmp_path):
        """Package with mostly intra-package imports → no finding."""
        graph = {
            "pkg.a": {"pkg.b", "pkg.c"},
            "pkg.b": {"pkg.a"},
            "pkg.c": {"pkg.a"},
        }
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.b": str(tmp_path / "pkg" / "b.py"),
            "pkg.c": str(tmp_path / "pkg" / "c.py"),
            "other.x": str(tmp_path / "other" / "x.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        # pkg has high cohesion (all imports are intra-package)
        pkg_findings = [f for f in findings if f.evidence.get("package") == "pkg"]
        assert pkg_findings == []

    def test_low_cohesion_finding(self, tmp_path):
        """Package where most imports are external → STRUCT004 finding."""
        graph = {
            "pkg.a": {"other.x", "other.y", "other.z"},
            "pkg.b": {"other.x", "other.y"},
        }
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.b": str(tmp_path / "pkg" / "b.py"),
            "other.x": str(tmp_path / "other" / "x.py"),
            "other.y": str(tmp_path / "other" / "y.py"),
            "other.z": str(tmp_path / "other" / "z.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        pkg_findings = [f for f in findings if f.evidence.get("package") == "pkg"]
        assert len(pkg_findings) == 1
        assert pkg_findings[0].kind == "STRUCT004"
        assert pkg_findings[0].evidence["cohesion_ratio"] < 0.3

    def test_skips_tiny_packages(self, tmp_path):
        """Packages with too few imports are skipped."""
        graph = {"pkg.a": {"other.x"}}  # Only 1 import
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "other.x": str(tmp_path / "other" / "x.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        assert findings == []

    def test_needs_multiple_packages(self, tmp_path):
        """With only one package, no findings (nothing to compare)."""
        graph = {"pkg.a": {"pkg.b"}, "pkg.b": set()}
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "pkg.b": str(tmp_path / "pkg" / "b.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        assert findings == []

    def test_evidence_payload(self, tmp_path):
        """Verify evidence payload structure."""
        graph = {
            "pkg.a": {"other.x", "other.y", "other.z", "other.w"},
        }
        file_map = {
            "pkg.a": str(tmp_path / "pkg" / "a.py"),
            "other.x": str(tmp_path / "other" / "x.py"),
            "other.y": str(tmp_path / "other" / "y.py"),
            "other.z": str(tmp_path / "other" / "z.py"),
            "other.w": str(tmp_path / "other" / "w.py"),
        }
        findings = _check_package_cohesion(graph, file_map, str(tmp_path))
        pkg_findings = [f for f in findings if f.evidence.get("package") == "pkg"]
        if pkg_findings:
            evidence = pkg_findings[0].evidence
            assert "code" in evidence
            assert evidence["code"] == "STRUCT004"
            assert "intra_imports" in evidence
            assert "inter_imports" in evidence
            assert "cohesion_ratio" in evidence
            assert "module_count" in evidence


# ── LOC Counter Tests ────────────────────────────────────────────────────


class TestCountLoc:
    def test_basic_counting(self, tmp_path):
        f = str(tmp_path / "mod.py")
        _write_file(
            f,
            """\
            import os

            # A comment
            x = 1
            y = 2
        """,
        )
        loc = _count_loc(f)
        assert loc == 3  # import os, x = 1, y = 2

    def test_empty_file(self, tmp_path):
        f = str(tmp_path / "empty.py")
        _write_file(f, "")
        assert _count_loc(f) == 0

    def test_missing_file(self):
        assert _count_loc("/nonexistent/file.py") == 0


# ── File Discovery Tests ────────────────────────────────────────────────


class TestDiscoverFiles:
    def test_finds_py_files(self, tmp_path):
        _write_file(str(tmp_path / "a.py"), "x = 1\n")
        _write_file(str(tmp_path / "pkg" / "b.py"), "y = 2\n")
        files = _discover_python_files(str(tmp_path))
        basenames = {os.path.basename(f) for f in files}
        assert "a.py" in basenames
        assert "b.py" in basenames

    def test_excludes_venv(self, tmp_path):
        _write_file(str(tmp_path / ".venv" / "lib.py"), "x = 1\n")
        _write_file(str(tmp_path / "app.py"), "y = 2\n")
        files = _discover_python_files(str(tmp_path))
        assert all(".venv" not in f for f in files)

    def test_excludes_pycache(self, tmp_path):
        _write_file(str(tmp_path / "__pycache__" / "mod.cpython-312.py"), "x = 1\n")
        files = _discover_python_files(str(tmp_path))
        assert all("__pycache__" not in f for f in files)

    def test_excludes_backup_like_directories(self, tmp_path):
        _write_file(str(tmp_path / "backup_20260220" / "old.py"), "x = 1\n")
        _write_file(str(tmp_path / "src" / "live.py"), "y = 2\n")
        files = _discover_python_files(str(tmp_path))
        basenames = {os.path.basename(f) for f in files}
        assert "live.py" in basenames
        assert "old.py" not in basenames


# ── Structure Snapshot Tests ─────────────────────────────────────────────


class TestStructureSnapshot:
    def test_snapshot_in_metrics(self, tmp_path, channel, config):
        """Execute on a real project structure and verify snapshot fields."""
        # Create enough files for analysis
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        for i in range(6):
            content = "\n".join([f"var_{j} = {j}" for j in range(20 + i * 10)])
            _write_file(str(pkg_dir / f"mod{i}.py"), content)
        _write_file(str(pkg_dir / "__init__.py"), "")
        _write_file(str(tmp_path / "other" / "helper.py"), "x = 1\n" * 30)
        _write_file(str(tmp_path / "other" / "__init__.py"), "")

        event = SupervisionEvent(
            surface="mcp",
            project_root=str(tmp_path),
        )
        result = channel.execute(event, config)
        metrics = result.metrics

        assert "file_count" in metrics
        assert "total_loc" in metrics
        assert "median_module_loc" in metrics
        assert "largest_modules" in metrics
        assert "package_count" in metrics
        assert "import_cycle_count" in metrics
        assert "orphan_count" in metrics
        assert "checks_run" in metrics
        assert metrics["checks_run"] == 4


# ── Integration Test ─────────────────────────────────────────────────────


class TestIntegration:
    def test_execute_on_lintgate_itself(self, channel, config):
        """Run the structure channel on the LintGate codebase itself."""
        # Find the LintGate project root
        this_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(this_file))

        event = SupervisionEvent(
            surface="mcp",
            project_root=project_root,
        )

        result = channel.execute(event, config)

        # Should complete without error
        assert result.status in ("pass", "fail")
        assert result.channel == "structure"
        assert result.duration_ms > 0

        # Snapshot should have real data
        assert result.metrics["file_count"] > 10
        assert result.metrics["total_loc"] > 1000
        assert result.metrics["checks_run"] == 4

        # All findings should have STRUCT codes
        for f in result.findings:
            assert f.kind.startswith("STRUCT")
            assert f.linter == "structure_channel"
            assert "code" in f.evidence
