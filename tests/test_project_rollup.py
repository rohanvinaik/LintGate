"""Tests for lintgate.specification.project_rollup — project-wide spec aggregation."""

from __future__ import annotations

import pytest

from lintgate.specification.file_analyzer import FileSpecResult
from lintgate.specification.project_rollup import (
    ProjectRollup,
    _aggregate,
    _cache_key,
    _deserialize_file_result,
    _load_file_cache,
    _save_file_cache,
    rollup_project,
)


@pytest.fixture(autouse=True)
def _clear_ast_cache():
    """Clear the module-level AST cache to prevent test pollution."""
    from lintgate.specification.ledger import _AST_TREE_CACHE

    _AST_TREE_CACHE.clear()
    yield
    _AST_TREE_CACHE.clear()


class TestCacheKey:
    def test_consistent_for_same_content(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        k1 = _cache_key(str(f))
        k2 = _cache_key(str(f))
        assert k1 == k2
        assert len(k1) == 16

    def test_differs_for_different_content(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x = 1\n")
        f2.write_text("x = 2\n")
        assert _cache_key(str(f1)) != _cache_key(str(f2))

    def test_missing_file_returns_empty(self):
        assert _cache_key("/nonexistent/file.py") == ""


class TestFileCache:
    def test_roundtrip(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def f(): pass\n")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        result = FileSpecResult(
            file="mod.py",
            project_root=str(tmp_path),
            functions={"mod.py::f": {"sigma": 3, "phase": "bulk"}},
            total_sigma=3,
            mean_spec_level=0.5,
            regime_distribution={"A": 1},
            risk_distribution={"P2": 1},
        )
        _save_file_cache(str(src), cache_dir, result)
        loaded = _load_file_cache(str(src), cache_dir)

        assert loaded is not None
        assert loaded.file == "mod.py"
        assert loaded.total_sigma == 3
        assert loaded.mean_spec_level == 0.5

    def test_cache_miss_on_changed_content(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def f(): pass\n")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        result = FileSpecResult(file="mod.py", project_root=str(tmp_path))
        _save_file_cache(str(src), cache_dir, result)

        # Change file content → different hash → cache miss
        src.write_text("def g(): pass\n")
        loaded = _load_file_cache(str(src), cache_dir)
        assert loaded is None

    def test_cache_miss_on_empty_dir(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("x = 1\n")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        assert _load_file_cache(str(src), cache_dir) is None


class TestDeserializeFileResult:
    def test_minimal(self):
        data = {"file": "a.py", "total_functions": 0, "total_sigma": 0, "mean_spec_level": 0.0}
        result = _deserialize_file_result(data)
        assert result.file == "a.py"
        assert result.total_sigma == 0

    def test_with_functions(self):
        data = {
            "file": "b.py",
            "project_root": "/proj",
            "total_sigma": 5,
            "mean_spec_level": 0.4,
            "functions": {"b.py::f": {"sigma": 5, "phase": "bulk"}},
            "regime_distribution": {"A": 1},
            "risk_distribution": {"P2": 1},
        }
        result = _deserialize_file_result(data)
        assert result.total_sigma == 5
        assert len(result.functions) == 1


class TestAggregate:
    def test_empty_results(self):
        rollup = ProjectRollup(project_root="/proj")
        _aggregate(rollup, [])
        assert rollup.total_files == 0
        assert rollup.total_functions == 0
        assert rollup.total_sigma == 0

    def test_single_file(self):
        rollup = ProjectRollup(project_root="/proj")
        results = [
            FileSpecResult(
                file="a.py",
                project_root="/proj",
                functions={
                    "a.py::f1": {"sigma": 5, "phase": "bulk"},
                    "a.py::f2": {"sigma": 3, "phase": "transition"},
                },
                total_sigma=8,
                mean_spec_level=0.4,
                regime_distribution={"A": 2},
                risk_distribution={"P2": 2},
            ),
        ]
        _aggregate(rollup, results)
        assert rollup.total_files == 1
        assert rollup.total_functions == 2
        assert rollup.total_sigma == 8
        assert rollup.phase_distribution == {"bulk": 1, "transition": 1}

    def test_multiple_files(self):
        rollup = ProjectRollup(project_root="/proj")
        results = [
            FileSpecResult(
                file="a.py",
                project_root="/proj",
                functions={"a.py::f": {"sigma": 5, "phase": "bulk"}},
                total_sigma=5,
                mean_spec_level=0.3,
                regime_distribution={"A": 1},
                risk_distribution={"P1": 1},
            ),
            FileSpecResult(
                file="b.py",
                project_root="/proj",
                functions={"b.py::g": {"sigma": 10, "phase": "tail"}},
                total_sigma=10,
                mean_spec_level=0.8,
                regime_distribution={"B": 1},
                risk_distribution={"P0": 1},
            ),
        ]
        _aggregate(rollup, results)
        assert rollup.total_files == 2
        assert rollup.total_functions == 2
        assert rollup.total_sigma == 15
        assert rollup.regime_distribution == {"A": 1, "B": 1}
        assert rollup.risk_distribution == {"P1": 1, "P0": 1}
        assert rollup.phase_distribution == {"bulk": 1, "tail": 1}

    def test_hotspot_ordering(self):
        rollup = ProjectRollup(project_root="/proj")
        results = [
            FileSpecResult(
                file="low.py",
                project_root="/proj",
                functions={"low.py::f": {"sigma": 2, "phase": "bulk"}},
                total_sigma=2,
                mean_spec_level=0.5,
            ),
            FileSpecResult(
                file="high.py",
                project_root="/proj",
                functions={"high.py::f": {"sigma": 20, "phase": "bulk"}},
                total_sigma=20,
                mean_spec_level=0.1,
            ),
        ]
        _aggregate(rollup, results)
        assert rollup.hotspot_files[0]["file"] == "high.py"
        assert rollup.hotspot_files[0]["sigma"] == 20


class TestRollupProject:
    def test_simple_project(self, tmp_path):
        src = tmp_path / "calc.py"
        src.write_text(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def sub(a: int, b: int) -> int:\n"
            "    return a - b\n"
        )
        # Create a minimal pyproject.toml so discovery works
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        rollup = rollup_project(str(tmp_path), use_cache=False)
        assert rollup.total_files >= 1
        assert rollup.total_functions >= 2
        assert rollup.total_sigma > 0
        assert len(rollup.hotspot_files) >= 1

    def test_empty_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        rollup = rollup_project(str(tmp_path), use_cache=False)
        assert rollup.total_files == 0
        assert rollup.total_sigma == 0

    def test_to_dict_structure(self, tmp_path):
        rollup = ProjectRollup(
            project_root=str(tmp_path),
            total_files=3,
            total_functions=10,
            total_sigma=50,
            mean_spec_level=0.4,
            regime_distribution={"A": 8, "B": 2},
            risk_distribution={"P0": 1, "P1": 3, "P2": 6},
            phase_distribution={"bulk": 4, "transition": 3, "tail": 2, "complete": 1},
        )
        d = rollup.to_dict()
        assert d["total_files"] == 3
        assert d["total_sigma"] == 50
        assert "hotspot_files" in d
        assert "cache_hits" in d
        assert "cache_misses" in d
