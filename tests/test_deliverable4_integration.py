"""Tests for Deliverable 4: Signal Integration.

Covers:
- E1: Coherence no-data annotation
- E2: Bootstrap progress in compact reporter
- D1+D2: Import tracing + E402 evidence
- E3+: DecompositionCoordinator merge logic
- F: Co-change coupling analysis
- G: AST function cache
"""

from __future__ import annotations

import ast
import os
import tempfile

# ── E1: Coherence no-data annotation ─────────────────────────────────────


class TestCoherenceBootstrapAnnotation:
    """Test bootstrap-aware coherence annotations."""

    def _make_channel_result(self, channel, status, metrics=None, findings=None):
        from lintgate.controlplane.types import ChannelResult

        return ChannelResult(
            channel=channel,
            status=status,
            findings=findings or [],
            repairs=[],
            metrics=metrics or {},
        )

    def test_no_data_annotation_when_bootstrap_needed(self):
        """Tests channel with bootstrap_needed emits NO_DATA annotation."""
        from lintgate.controlplane.coherence import _detect_bootstrap_needed

        results = [
            self._make_channel_result("lint", "pass"),
            self._make_channel_result(
                "tests", "pass", metrics={"bootstrap_needed": True}
            ),
        ]
        notes = _detect_bootstrap_needed(results)
        assert len(notes) == 1
        assert "NO_DATA" in notes[0]
        assert "no test files exist" in notes[0]

    def test_no_annotation_when_bootstrap_not_needed(self):
        """Tests channel without bootstrap_needed emits no annotation."""
        from lintgate.controlplane.coherence import _detect_bootstrap_needed

        results = [
            self._make_channel_result("lint", "pass"),
            self._make_channel_result("tests", "pass", metrics={}),
        ]
        notes = _detect_bootstrap_needed(results)
        assert len(notes) == 0

    def test_no_annotation_without_tests_channel(self):
        """No tests channel → no annotation."""
        from lintgate.controlplane.coherence import _detect_bootstrap_needed

        results = [self._make_channel_result("lint", "pass")]
        notes = _detect_bootstrap_needed(results)
        assert len(notes) == 0

    def test_coherence_with_history_includes_bootstrap(self):
        """compute_coherence_with_history includes bootstrap notes."""
        from lintgate.controlplane.coherence import compute_coherence_with_history

        results = [
            self._make_channel_result("lint", "pass"),
            self._make_channel_result(
                "tests", "pass", metrics={"bootstrap_needed": True}
            ),
        ]
        coherence = compute_coherence_with_history(results, session=None)
        assert any("NO_DATA" in n for n in coherence.classification_notes)


# ── E2: Bootstrap progress in compact reporter ──────────────────────────


class TestBootstrapProgressReporter:
    """Test bootstrap progress display in compact reporter."""

    def _make_mesh_result(self, bootstrap_needed=False, metrics=None):
        from lintgate.controlplane.types import (
            ChannelResult,
            CoherenceResult,
            MeshResult,
            SupervisionEvent,
        )

        test_metrics = metrics or {}
        if bootstrap_needed:
            test_metrics["bootstrap_needed"] = True
            test_metrics["bootstrap_reason"] = "zero_test_files"

        return MeshResult(
            event=SupervisionEvent(
                event_id="test_run_001",
                surface="mcp",
                files_changed=[],
            ),
            channel_results=[
                ChannelResult(
                    channel="tests",
                    status="pass",
                    findings=[],
                    repairs=[],
                    metrics=test_metrics,
                ),
            ],
            coherence=CoherenceResult(
                state="stable",
                summary="All channels clean.",
                recommended_action="Continue.",
                silent_channels=["tests"],
                loud_channels=[],
                confidence=1.0,
            ),
            duration_ms=100.0,
        )

    def test_bootstrap_progress_when_needed(self):
        from lintgate.controlplane.reporter_compact import _build_bootstrap_progress

        mesh = self._make_mesh_result(bootstrap_needed=True)
        progress = _build_bootstrap_progress(mesh)
        assert progress is not None
        assert progress["needed"] is True
        assert progress["reason"] == "zero_test_files"

    def test_no_bootstrap_progress_when_not_needed(self):
        from lintgate.controlplane.reporter_compact import _build_bootstrap_progress

        mesh = self._make_mesh_result(bootstrap_needed=False)
        progress = _build_bootstrap_progress(mesh)
        assert progress is None

    def test_bootstrap_next_action_suggested(self):
        from lintgate.controlplane.reporter_compact import _build_cp_next_actions

        counts = {
            "blocking": 0,
            "warning": 0,
            "informational": 0,
            "repairs_available": 0,
        }
        bootstrap = {"needed": True, "status": None}
        actions = _build_cp_next_actions("run1", counts, bootstrap_progress=bootstrap)
        tool_names = [a.tool for a in actions]
        assert "bootstrap_tests" in tool_names

    def test_bootstrap_running_suggests_status(self):
        from lintgate.controlplane.reporter_compact import _build_cp_next_actions

        counts = {
            "blocking": 0,
            "warning": 0,
            "informational": 0,
            "repairs_available": 0,
        }
        bootstrap = {"needed": True, "status": "running", "phase": "skeletons"}
        actions = _build_cp_next_actions("run1", counts, bootstrap_progress=bootstrap)
        tool_names = [a.tool for a in actions]
        assert "bootstrap_status" in tool_names


# ── D1+D2: Import tracing + E402 evidence ───────────────────────────────


class TestImportTracing:
    """Test import tracing and E402 evidence attachment."""

    def test_stdlib_detection(self):
        from lintgate.linters.structure_checks.import_tracing import is_stdlib_module

        assert is_stdlib_module("os")
        assert is_stdlib_module("json")
        assert is_stdlib_module("pathlib")
        assert is_stdlib_module("collections.abc")
        assert not is_stdlib_module("requests")
        assert not is_stdlib_module("numpy")

    def test_trace_stdlib_only(self):
        """Tracing a stdlib module → no non-stdlib deps."""
        from lintgate.linters.structure_checks.import_tracing import (
            trace_transitive_imports,
        )

        result = trace_transitive_imports("os", "/tmp/fake_project")
        assert len(result.non_stdlib_deps) == 0

    def test_trace_non_stdlib(self):
        """Tracing a non-stdlib module → included in deps."""
        from lintgate.linters.structure_checks.import_tracing import (
            trace_transitive_imports,
        )

        result = trace_transitive_imports("requests", "/tmp/fake_project")
        assert "requests" in result.non_stdlib_deps

    def test_lazy_import_detection_try_except(self):
        """Detect import inside try/except as lazy."""
        from lintgate.linters.structure_checks.import_tracing import (
            _detect_lazy_import_at_line,
        )

        source = """
try:
    import requests
except ImportError:
    requests = None
"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            lazy = _detect_lazy_import_at_line(f.name, "requests", 3)
        os.unlink(f.name)
        assert lazy is not None
        assert lazy.guardian == "try_except"

    def test_lazy_import_detection_function(self):
        """Detect import inside function as lazy."""
        from lintgate.linters.structure_checks.import_tracing import (
            _detect_lazy_import_at_line,
        )

        source = """
def foo():
    import numpy
    return numpy.array([1])
"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            lazy = _detect_lazy_import_at_line(f.name, "numpy", 3)
        os.unlink(f.name)
        assert lazy is not None
        assert lazy.guardian == "function"

    def test_non_lazy_import(self):
        """Top-level import → not lazy."""
        from lintgate.linters.structure_checks.import_tracing import (
            _detect_lazy_import_at_line,
        )

        source = """import os\nimport sys\n"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            lazy = _detect_lazy_import_at_line(f.name, "os", 1)
        os.unlink(f.name)
        assert lazy is None

    def test_build_e402_evidence(self):
        """build_e402_evidence returns structured evidence dict."""
        from lintgate.linters.structure_checks.import_tracing import build_e402_evidence

        source = """import sys\nx = 1\nimport requests\n"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            evidence = build_e402_evidence("requests", f.name, 3, "/tmp")
        os.unlink(f.name)
        assert evidence["code"] == "E402"
        assert evidence["module"] == "requests"
        assert "transitive_imports" in evidence
        assert "requests" in evidence["transitive_imports"]["non_stdlib"]

    def test_e402_module_extraction(self):
        """_extract_e402_module extracts module name from source line."""
        from lintgate.linters.ruff_linter import _extract_e402_module

        source = """import os\nx = 1\nimport requests\n"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            module = _extract_e402_module(f.name, 3)
        os.unlink(f.name)
        assert module == "requests"

    def test_e402_module_extraction_from_import(self):
        """_extract_e402_module handles from-import."""
        from lintgate.linters.ruff_linter import _extract_e402_module

        source = """import os\nx = 1\nfrom pathlib import Path\n"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            module = _extract_e402_module(f.name, 3)
        os.unlink(f.name)
        assert module == "pathlib"


# ── E3+: DecompositionCoordinator ────────────────────────────────────────


class TestDecompositionCoordinator:
    """Test DecompositionCoordinator merge logic."""

    def test_merge_dynamic_priority(self):
        """Dynamic candidates take priority over static for same function."""
        from lintgate.mutation.decomposition import (
            DecompositionCandidate,
            merge_candidates,
        )

        dynamic = [
            DecompositionCandidate(
                function_id="foo.py::bar",
                file_path="foo.py",
                survival_rate=0.8,
                surviving_categories=["arith", "compare", "branch"],
                total_mutants=20,
                reason="high survival",
                source="dynamic",
                confidence=0.75,
                evidence=["mutation_survival"],
            )
        ]
        static = [
            DecompositionCandidate(
                function_id="foo.py::bar",
                file_path="foo.py",
                survival_rate=None,
                surviving_categories=None,
                total_mutants=None,
                reason="CC=25",
                source="static",
                confidence=0.60,
                evidence=["variable_clustering"],
            )
        ]
        merged = merge_candidates(dynamic, static)
        assert len(merged) == 1
        assert merged[0].source == "converged"
        assert merged[0].confidence > 0.75  # boosted
        assert "mutation_survival" in merged[0].evidence
        assert "variable_clustering" in merged[0].evidence

    def test_merge_static_only(self):
        """Static-only candidates kept when no dynamic match."""
        from lintgate.mutation.decomposition import (
            DecompositionCandidate,
            merge_candidates,
        )

        static = [
            DecompositionCandidate(
                function_id="baz.py::qux",
                file_path="baz.py",
                survival_rate=None,
                surviving_categories=None,
                total_mutants=None,
                reason="CC=30",
                source="static",
                confidence=0.55,
                evidence=["cognitive_complexity"],
            )
        ]
        merged = merge_candidates([], static)
        assert len(merged) == 1
        assert merged[0].source == "static"
        assert merged[0].confidence == 0.55

    def test_merge_preserves_both_when_different(self):
        """Different function IDs → both kept."""
        from lintgate.mutation.decomposition import (
            DecompositionCandidate,
            merge_candidates,
        )

        dynamic = [
            DecompositionCandidate(
                function_id="a.py::f1",
                file_path="a.py",
                survival_rate=0.7,
                surviving_categories=["arith"],
                total_mutants=10,
                reason="",
                source="dynamic",
                confidence=0.75,
                evidence=["mutation_survival"],
            )
        ]
        static = [
            DecompositionCandidate(
                function_id="b.py::f2",
                file_path="b.py",
                survival_rate=None,
                surviving_categories=None,
                total_mutants=None,
                reason="",
                source="static",
                confidence=0.60,
                evidence=["variable_clustering"],
            )
        ]
        merged = merge_candidates(dynamic, static)
        assert len(merged) == 2


# ── F: Co-change coupling ────────────────────────────────────────────────


class TestCoChangeCoupling:
    """Test co-change coupling analysis."""

    def test_parse_git_log(self):
        from lintgate.linters.structure_checks.cochange_analysis import _parse_git_log

        stdout = "COMMIT\nfoo.py\nbar.py\nCOMMIT\nfoo.py\nbaz.py\n"
        commits = _parse_git_log(stdout, "/project")
        assert len(commits) == 2
        assert {"foo.py", "bar.py"} in commits
        assert {"foo.py", "baz.py"} in commits

    def test_cochange_pair_coupling_strength(self):
        from lintgate.linters.structure_checks.cochange_analysis import CoChangePair

        pair = CoChangePair(
            file_a="a.py",
            file_b="b.py",
            cochange_count=5,
            total_commits_a=10,
            total_commits_b=8,
        )
        # Jaccard: 5 / (10 + 8 - 5) = 5/13 ≈ 0.385
        assert abs(pair.coupling_strength - 5 / 13) < 0.001

    def test_cochange_coupling_for(self):
        from lintgate.linters.structure_checks.cochange_analysis import (
            CoChangeCoupling,
            CoChangePair,
        )

        coupling = CoChangeCoupling(
            pairs=[
                CoChangePair("a.py", "b.py", 5, 10, 8),
                CoChangePair("a.py", "c.py", 2, 10, 4),
            ],
            file_commit_counts={"a.py": 10, "b.py": 8, "c.py": 4},
            total_commits_analyzed=15,
        )
        assert coupling.coupling_for("a.py", "b.py") > 0
        assert coupling.coupling_for("b.py", "a.py") > 0  # symmetric
        assert coupling.coupling_for("x.py", "y.py") == 0.0  # unknown

    def test_annotate_split_proposals_no_data(self):
        from lintgate.linters.structure_checks.cochange_analysis import (
            CoChangeCoupling,
            annotate_split_proposals,
        )

        proposals = [{"action": "split foo.py"}]
        coupling = CoChangeCoupling()
        result = annotate_split_proposals(proposals, coupling, "foo.py")
        assert result[0]["cochange_annotation"]["status"] == "no_data"


# ── G: AST function cache ───────────────────────────────────────────────


class TestASTFunctionCache:
    """Test AST function analysis cache."""

    def test_cache_hit(self):
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache()
        cache.set("foo.py", "bar", "hash123", {"cc": 10})
        result = cache.get("foo.py", "bar", "hash123")
        assert result is not None
        assert result.analysis["cc"] == 10
        assert cache.hits == 1

    def test_cache_miss_wrong_hash(self):
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache()
        cache.set("foo.py", "bar", "hash123", {"cc": 10})
        result = cache.get("foo.py", "bar", "hash_different")
        assert result is None
        assert cache.misses == 1

    def test_cache_miss_not_present(self):
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache()
        result = cache.get("foo.py", "bar", "hash123")
        assert result is None

    def test_invalidate_file(self):
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache()
        cache.set("foo.py", "bar", "hash1", {"cc": 10})
        cache.set("foo.py", "baz", "hash2", {"cc": 5})
        assert cache.total_entries == 2

        cache.invalidate_file("foo.py")
        assert cache.total_entries == 0

    def test_import_hash_invalidation(self):
        """Changing import hash invalidates all functions in file."""
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache()
        cache.set("foo.py", "bar", "hash1", {"cc": 10}, import_hash="imp_v1")
        cache.set("foo.py", "baz", "hash2", {"cc": 5}, import_hash="imp_v1")
        assert cache.total_entries == 2

        # Setting with different import hash invalidates existing
        cache.set("foo.py", "qux", "hash3", {"cc": 15}, import_hash="imp_v2")
        assert cache.total_entries == 1  # only qux remains

    def test_hash_function_source_deterministic(self):
        from lintgate.linters.structure_checks.ast_cache import hash_function_source

        source = "def foo(x):\n    return x + 1\n"
        tree = ast.parse(source)
        func = tree.body[0]
        h1 = hash_function_source(func)
        h2 = hash_function_source(func)
        assert h1 == h2

    def test_hash_function_source_changes_on_edit(self):
        from lintgate.linters.structure_checks.ast_cache import hash_function_source

        source1 = "def foo(x):\n    return x + 1\n"
        source2 = "def foo(x):\n    return x + 2\n"
        tree1 = ast.parse(source1)
        tree2 = ast.parse(source2)
        h1 = hash_function_source(tree1.body[0])
        h2 = hash_function_source(tree2.body[0])
        assert h1 != h2

    def test_hash_file_imports(self):
        from lintgate.linters.structure_checks.ast_cache import hash_file_imports

        source = "import os\nimport sys\ndef foo(): pass\n"
        tree = ast.parse(source)
        h = hash_file_imports(tree)
        assert h != "no_imports"
        assert len(h) == 16

    def test_hash_file_no_imports(self):
        from lintgate.linters.structure_checks.ast_cache import hash_file_imports

        source = "def foo(): pass\n"
        tree = ast.parse(source)
        h = hash_file_imports(tree)
        assert h == "no_imports"

    def test_cache_stats(self):
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache()
        cache.set("a.py", "f1", "h1", {"cc": 1})
        cache.get("a.py", "f1", "h1")  # hit
        cache.get("a.py", "f2", "h2")  # miss

        stats = cache.stats()
        assert stats["files_cached"] == 1
        assert stats["functions_cached"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_eviction(self):
        from lintgate.linters.structure_checks.ast_cache import FunctionAnalysisCache

        cache = FunctionAnalysisCache(max_entries=3)
        cache.set("a.py", "f1", "h1", {"cc": 1})
        cache.set("a.py", "f2", "h2", {"cc": 2})
        cache.set("a.py", "f3", "h3", {"cc": 3})
        cache.set("a.py", "f4", "h4", {"cc": 4})  # triggers eviction
        assert cache.total_entries <= 3
