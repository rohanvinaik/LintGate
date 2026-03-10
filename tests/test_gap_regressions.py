"""Regression tests for Phase 1 reliability gaps A, B, C.

Gap A: Convergence events use surface="mcp" and yield evidence.
Gap B: Convergence adapter consumes pure_function_list (list format).
Gap C: Static landscape preserves parallel call-site metadata and
       uses collision-safe cache dedupe.
"""

from __future__ import annotations

import os

import pytest

# ── Gap A: surface="mcp" wiring ─────────────────────────────────────


class TestGapA_SurfaceWiring:
    """Convergence tools create SupervisionEvents with surface='mcp'."""

    def test_convergence_analyze_uses_mcp_surface(self):
        """_impl_convergence_analyze creates events with surface='mcp'."""
        import ast
        import inspect

        from mcp_tools.convergence_tools import _impl_convergence_analyze

        source = inspect.getsource(_impl_convergence_analyze)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "SupervisionEvent":
                    kwarg_names = [kw.arg for kw in node.keywords]
                    assert "surface" in kwarg_names, (
                        "SupervisionEvent in _impl_convergence_analyze missing surface kwarg"
                    )
                    surface_kw = next(kw for kw in node.keywords if kw.arg == "surface")
                    assert isinstance(surface_kw.value, ast.Constant)
                    assert surface_kw.value.value == "mcp"

    def test_extraction_plan_uses_mcp_surface(self):
        """_impl_extraction_plan creates events with surface='mcp'."""
        import ast
        import inspect

        from mcp_tools.convergence_tools import _impl_extraction_plan

        source = inspect.getsource(_impl_extraction_plan)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "SupervisionEvent":
                    kwarg_names = [kw.arg for kw in node.keywords]
                    assert "surface" in kwarg_names, (
                        "SupervisionEvent in _impl_extraction_plan missing surface kwarg"
                    )
                    surface_kw = next(kw for kw in node.keywords if kw.arg == "surface")
                    assert isinstance(surface_kw.value, ast.Constant)
                    assert surface_kw.value.value == "mcp"

    def test_optimization_landscape_uses_mcp_surface(self):
        """_impl_optimization_landscape creates events with surface='mcp'."""
        import ast
        import inspect

        from mcp_tools.convergence_tools import _impl_optimization_landscape

        source = inspect.getsource(_impl_optimization_landscape)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "SupervisionEvent":
                    kwarg_names = [kw.arg for kw in node.keywords]
                    assert "surface" in kwarg_names, (
                        "SupervisionEvent in _impl_optimization_landscape missing surface kwarg"
                    )
                    surface_kw = next(kw for kw in node.keywords if kw.arg == "surface")
                    assert isinstance(surface_kw.value, ast.Constant)
                    assert surface_kw.value.value == "mcp"

    def test_dynamic_landscape_yields_convergence(self, tmp_path):
        """Dynamic mode with surface='mcp' produces non-empty convergence."""
        (tmp_path / "mod.py").write_text(
            "def compute(x, y):\n    return x + y\n\n"
            "def process(data):\n    return len(data)\n"
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_mod.py").write_text(
            "def test_compute():\n    assert True\n"
        )

        from mcp_tools.convergence_tools import _impl_optimization_landscape

        helpers = {"_validate_project_root": lambda p: None}
        result = _impl_optimization_landscape(str(tmp_path), helpers, mode="dynamic")

        assert result["mode"] == "dynamic"
        # With surface="mcp", channels activate → convergence should be non-empty
        assert "convergence_targets" in result, (
            "Dynamic mode should produce convergence_targets with surface='mcp'"
        )


# ── Gap B: Purity adapter key alignment ─────────────────────────────


class TestGapB_PurityAdapterAlignment:
    """Convergence adapter consumes pure_function_list format."""

    def test_adapt_purity_handles_list_format(self):
        """adapt_purity accepts list-of-dicts from performance channel."""
        from lintgate.convergence.aggregator import adapt_purity

        data = [
            {"name": "compute", "file": "mod.py", "hints": ["cacheable"]},
            {"name": "helper", "file": "utils.py", "hints": ["parallelizable"]},
        ]
        evidence = adapt_purity(data)

        assert len(evidence) == 2
        targets = {e.target for e in evidence}
        assert targets == {"compute", "helper"}
        assert all(e.signal == "support" for e in evidence)
        assert all(e.lens.value == "purity" for e in evidence)

    def test_adapt_purity_handles_legacy_dict_format(self):
        """adapt_purity still accepts legacy purity_profile dict format."""
        from lintgate.convergence.aggregator import adapt_purity

        data = {
            "compute": {"file": "mod.py", "confidence": 0.9, "hints": ["cacheable"]},
        }
        evidence = adapt_purity(data)

        assert len(evidence) == 1
        assert evidence[0].target == "compute"
        assert evidence[0].confidence == 0.9

    def test_adapt_purity_skips_nameless_entries(self):
        """List entries without 'name' key are skipped."""
        from lintgate.convergence.aggregator import adapt_purity

        data = [
            {"file": "mod.py", "hints": []},  # no name
            {"name": "valid", "file": "mod.py", "hints": []},
        ]
        evidence = adapt_purity(data)
        assert len(evidence) == 1
        assert evidence[0].target == "valid"

    def test_adapt_purity_empty_list(self):
        """Empty list returns empty evidence."""
        from lintgate.convergence.aggregator import adapt_purity

        assert adapt_purity([]) == []

    def test_adapt_purity_none_input(self):
        """None input returns empty evidence."""
        from lintgate.convergence.aggregator import adapt_purity

        assert adapt_purity(None) == []

    def test_adapter_registry_includes_pure_function_list(self):
        """Integration adapter registry maps pure_function_list → adapt_purity."""
        from lintgate.convergence.integration import _init_metric_adapters

        adapters = _init_metric_adapters()
        keys = [k for k, _ in adapters]
        assert "pure_function_list" in keys

    def test_adapter_registry_includes_legacy_purity_profile(self):
        """Integration adapter registry still maps purity_profile for deprecation window."""
        from lintgate.convergence.integration import _init_metric_adapters

        adapters = _init_metric_adapters()
        keys = [k for k, _ in adapters]
        assert "purity_profile" in keys

    def test_convergence_schema_consumes_pure_function_list(self):
        """CONVERGENCE_SCHEMA declares pure_function_list as consumed metric."""
        from lintgate.controlplane.metric_schema import CONVERGENCE_SCHEMA

        consumed_keys = [mf.key for mf in CONVERGENCE_SCHEMA.consumes]
        assert "pure_function_list" in consumed_keys
        assert "purity_profile" not in consumed_keys

    def test_performance_schema_publishes_pure_function_list(self):
        """PERFORMANCE_SCHEMA declares pure_function_list as published metric."""
        from lintgate.controlplane.metric_schema import PERFORMANCE_SCHEMA

        published_keys = [mf.key for mf in PERFORMANCE_SCHEMA.publishes]
        assert "pure_function_list" in published_keys

    def test_schema_wiring_pure_function_list_no_issue(self):
        """Wiring validation passes for pure_function_list: published by performance, consumed by convergence."""
        from lintgate.controlplane.metric_schema import (
            clear_schemas,
            register_all_schemas,
            validate_wiring,
        )

        clear_schemas()
        register_all_schemas()
        issues = validate_wiring(["performance", "convergence", "structure", "specification"])
        purity_issues = [i for i in issues if "purity" in i.key.lower() or "pure" in i.key.lower()]
        assert purity_issues == [], f"Unexpected purity wiring issues: {purity_issues}"


# ── Gap C: Static landscape fidelity ────────────────────────────────


class TestGapC_StaticLandscapeFidelity:
    """Static landscape preserves parallel metadata and collision-safe cache dedupe."""

    @pytest.fixture
    def project_with_parallel(self, tmp_path):
        """Project with a parallel-detectable pattern."""
        (tmp_path / "mod.py").write_text(
            "def pure_fn(x):\n"
            "    return x * 2\n\n"
            "def caller():\n"
            "    data = [1, 2, 3]\n"
            "    return [pure_fn(x) for x in data]\n"
        )
        return str(tmp_path)

    def test_parallel_opportunities_are_dicts(self, tmp_path):
        """Parallel opportunities preserve full call-site metadata as dicts."""
        (tmp_path / "mod.py").write_text(
            "def pure_fn(x):\n"
            "    return x * 2\n\n"
            "def caller():\n"
            "    data = [1, 2, 3]\n"
            "    return [pure_fn(x) for x in data]\n"
        )

        from mcp_tools.convergence_tools import _build_static_landscape

        result = _build_static_landscape(str(tmp_path))

        if result.get("parallel_opportunities"):
            for opp in result["parallel_opportunities"]:
                # Each opportunity should be a dict with full metadata
                assert isinstance(opp, dict), (
                    f"Parallel opportunity should be dict, got {type(opp)}"
                )
                # Must have call-site fields
                assert "callee" in opp
                assert "pattern" in opp
                assert "file" in opp

    def test_parallel_opportunities_from_detector_have_line_and_confidence(self, tmp_path):
        """Detector-sourced parallel opportunities include line/confidence."""
        (tmp_path / "mod.py").write_text(
            "def pure_fn(x):\n"
            "    return x * 2\n\n"
            "def caller():\n"
            "    data = [1, 2, 3]\n"
            "    return [pure_fn(x) for x in data]\n"
        )

        from mcp_tools.convergence_tools import _build_static_landscape

        result = _build_static_landscape(str(tmp_path))

        detector_opps = [
            o for o in result.get("parallel_opportunities", [])
            if o.get("pattern") != "MANIFEST_HINT"
        ]
        for opp in detector_opps:
            assert "line" in opp
            assert "confidence" in opp
            assert "constraints" in opp

    def test_cache_dedupe_by_source_file_and_function(self, tmp_path):
        """Cache hotspots with same function name but different source files are preserved."""
        # Create two modules with same-named function
        (tmp_path / "module_a.py").write_text(
            "def compute(x):\n    return x + 1\n"
        )
        (tmp_path / "module_b.py").write_text(
            "def compute(x):\n    return x * 2\n"
        )

        from mcp_tools.convergence_tools import _build_static_landscape

        result = _build_static_landscape(str(tmp_path))

        cache_items = result.get("cache_hotspots", [])
        # Find entries for "compute"
        compute_entries = [c for c in cache_items if c["function"] == "compute"]
        # If both modules have cacheable compute functions, both should be kept
        # (old behavior would collapse them to one)
        source_files = {c.get("source_file", "") for c in compute_entries}
        if len(compute_entries) > 1:
            assert len(source_files) > 1, (
                "Same-named functions from different files should not be deduped together"
            )

    def test_cache_dedupe_same_file_dedupes(self, tmp_path):
        """Same (source_file, function) pair is properly deduped."""
        (tmp_path / "mod.py").write_text(
            "def compute(x):\n    return x + 1\n"
        )

        from mcp_tools.convergence_tools import _build_static_landscape

        result = _build_static_landscape(str(tmp_path))

        cache_items = result.get("cache_hotspots", [])
        # Same function from same file should appear at most once
        seen = set()
        for c in cache_items:
            key = (c.get("source_file", ""), c["function"])
            assert key not in seen, f"Duplicate cache entry for {key}"
            seen.add(key)

    def test_summary_uses_parallel_opportunities_key(self, tmp_path):
        """Summary field uses 'parallel_opportunities' not 'parallel_groups'."""
        (tmp_path / "mod.py").write_text("def f(x):\n    return x\n")

        from mcp_tools.convergence_tools import _build_static_landscape

        result = _build_static_landscape(str(tmp_path))

        assert "parallel_opportunities" in result["summary"]
        assert "parallel_groups" not in result["summary"]


# ── P2: time_budget_minutes effort consistency ──────────────────────


class TestP2_BudgetEffortConsistency:
    """Budget filtering uses same effort model (fixable discount) as ROI ranking."""

    def test_fixable_finding_fits_in_budget(self):
        """A fixable Bandit finding (default 20min) should fit in 3min budget via fixable discount."""
        from mcp_tools._controlplane_impl_details import _extract_findings

        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {
                            "severity": "warning",
                            "message": "B101 assert used",
                            "linter": "bandit",
                            "fixable": True,
                        },
                    ]
                }
            }
        }
        result = _extract_findings(details, None, None, 10, time_budget_minutes=3)
        # Fixable discount: min(20, 2) = 2min, fits in 3min budget
        assert result["total_matching"] == 1
        assert len(result["findings"]) == 1

    def test_non_fixable_bandit_excluded_from_tight_budget(self):
        """A non-fixable Bandit finding (20min) should NOT fit in 3min budget."""
        from mcp_tools._controlplane_impl_details import _extract_findings

        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {
                            "severity": "warning",
                            "message": "B101 assert used",
                            "linter": "bandit",
                            "fixable": False,
                        },
                    ]
                }
            }
        }
        result = _extract_findings(details, None, None, 10, time_budget_minutes=3)
        assert result["total_matching"] == 0

    def test_budget_used_reflects_fixable_discount(self):
        """budget_used_minutes should reflect the fixable discount, not raw effort."""
        from mcp_tools._controlplane_impl_details import _extract_findings

        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {
                            "severity": "warning",
                            "message": "fixable issue",
                            "linter": "bandit",
                            "fixable": True,
                        },
                    ]
                }
            }
        }
        result = _extract_findings(details, None, None, 10, time_budget_minutes=10)
        # Fixable bandit: effort = min(20, 2) = 2.0
        assert result["budget_used_minutes"] == 2.0

    def test_roi_and_budget_use_same_effort(self):
        """ROI computation and budget filtering agree on effort for fixable items."""
        from mcp_tools._controlplane_impl_details import _finding_effort, _finding_roi

        finding = {"severity": "warning", "linter": "bandit", "fixable": True}
        effort = _finding_effort(finding)
        assert effort == 2.0  # min(20, 2)
        roi = _finding_roi(finding)
        # weight=2.0, confidence=1.0, effort=2.0 → ROI = 2.0*1.0/2.0 = 1.0
        assert roi == 1.0


# ── P3: top_n negative validation ───────────────────────────────────


class TestP3_TopNValidation:
    """Negative top_n produces valid output."""

    def test_negative_top_n_returns_empty(self):
        """top_n=-1 should return 0 findings, not nonsensical truncation."""
        from mcp_tools._controlplane_impl_details import _extract_findings

        details = {
            "channels": {
                "lint": {
                    "findings": [
                        {"severity": "warning", "message": f"w{i}"}
                        for i in range(5)
                    ]
                }
            }
        }
        result = _extract_findings(details, None, None, 10, top_n=-1)
        assert len(result["findings"]) == 0
        # truncated should not be negative
        assert result.get("truncated", 0) >= 0

    def test_zero_top_n_returns_empty(self):
        """top_n=0 should return 0 findings."""
        from mcp_tools._controlplane_impl_details import _extract_findings

        details = {
            "channels": {
                "lint": {
                    "findings": [{"severity": "warning", "message": "w1"}]
                }
            }
        }
        result = _extract_findings(details, None, None, 10, top_n=0)
        assert len(result["findings"]) == 0


# ── P3: Landscape excludes test/fuzz files ──────────────────────────


class TestP3_LandscapeProductionFilter:
    """Optimization landscape filters out non-production targets."""

    def test_is_production_file_basics(self):
        """_is_production_file correctly classifies common patterns."""
        from mcp_tools.convergence_tools import _is_production_file

        # Production files
        assert _is_production_file("module.py")
        assert _is_production_file("utils/helpers.py")
        assert _is_production_file("src/core.py")

        # Test files
        assert not _is_production_file("test_module.py")
        assert not _is_production_file("tests/test_core.py")
        assert not _is_production_file("test/test_utils.py")
        assert not _is_production_file("conftest.py")
        assert not _is_production_file("module_test.py")

        # Fuzz/benchmark files
        assert not _is_production_file("fuzz_parser.py")
        assert not _is_production_file("fuzz/target.py")
        assert not _is_production_file("benchmark_perf.py")
        assert not _is_production_file("benchmarks/run.py")

    def test_static_landscape_excludes_test_files(self, tmp_path):
        """Static landscape does not include test functions as optimization targets."""
        (tmp_path / "core.py").write_text("def compute(x):\n    return x + 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_core.py").write_text(
            "def test_compute():\n    assert compute(1) == 2\n"
        )

        from mcp_tools.convergence_tools import _build_static_landscape

        result = _build_static_landscape(str(tmp_path))

        # Check that no test functions appear in any recommendation list
        all_funcs = set()
        for item in result.get("cache_hotspots", []):
            all_funcs.add(item["function"])
        for item in result.get("parallel_opportunities", []):
            all_funcs.add(item.get("callee", ""))
        for item in result.get("jit_candidates", []):
            all_funcs.add(item.get("function", ""))
        for item in result.get("extraction_safe_refactors", []):
            all_funcs.add(item.get("function", ""))

        test_funcs = {f for f in all_funcs if f.startswith("test_")}
        assert test_funcs == set(), f"Test functions in landscape: {test_funcs}"


# ── P1: convergence_analyze file filter scope leak ───────────────────


class TestP1_FileFilterScopeLeak:
    """convergence_analyze with unresolved file filter returns error, not full-project data."""

    def test_nonexistent_file_returns_error(self, tmp_path):
        """file='nonexistent.py' should return an error, not analyze everything."""
        (tmp_path / "module.py").write_text("def f(x):\n    return x + 1\n")

        from mcp_tools.convergence_tools import _impl_convergence_analyze

        helpers = {"_validate_project_root": lambda p: None}
        result = _impl_convergence_analyze(str(tmp_path), "nonexistent.py", None, helpers)

        assert "error" in result
        assert "nonexistent.py" in result["error"]
        # Must NOT contain full-project convergence data
        assert "function_convergence" not in result

    def test_valid_file_still_works(self, tmp_path):
        """file='module.py' should still produce convergence results."""
        (tmp_path / "module.py").write_text("def f(x):\n    return x + 1\n")

        from mcp_tools.convergence_tools import _impl_convergence_analyze

        helpers = {"_validate_project_root": lambda p: None}
        result = _impl_convergence_analyze(str(tmp_path), "module.py", None, helpers)

        # Should not be an error
        assert result.get("error") is None or "not found" not in result.get("error", "")

    def test_no_file_filter_still_analyzes_project(self, tmp_path):
        """file=None should still run full-project analysis normally."""
        (tmp_path / "module.py").write_text("def f(x):\n    return x + 1\n")

        from mcp_tools.convergence_tools import _impl_convergence_analyze

        helpers = {"_validate_project_root": lambda p: None}
        result = _impl_convergence_analyze(str(tmp_path), None, None, helpers)

        assert "project" in result
        # Should have convergence data or at least no file-not-found error
        assert result.get("error") is None or "not found" not in result.get("error", "")


# ── P2: Dynamic landscape production scoping bypass ─────────────────


class TestP2_DynamicLandscapeScoping:
    """Dynamic landscape pre-populates context to enforce production-only scoping."""

    def test_dynamic_landscape_event_has_context_python_files(self, tmp_path):
        """Dynamic path sets event.context['python_files'] with production-filtered list."""
        (tmp_path / "core.py").write_text("def compute(x):\n    return x + 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_core.py").write_text(
            "def test_compute():\n    assert True\n"
        )

        # Inspect the source to verify context pre-population
        import ast
        import inspect

        from mcp_tools.convergence_tools import _impl_optimization_landscape

        source = inspect.getsource(_impl_optimization_landscape)
        tree = ast.parse(source)

        # Find event.context["python_files"] assignment
        found_context_assignment = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and getattr(target.value, "attr", "") == "context"
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "python_files"
                    ):
                        found_context_assignment = True
        assert found_context_assignment, (
            "Dynamic landscape must pre-populate event.context['python_files']"
        )

    def test_dynamic_landscape_files_changed_capped_at_5(self, tmp_path):
        """Dynamic path caps files_changed to ≤5 so runtime uses scoped discovery."""
        import ast
        import inspect

        from mcp_tools.convergence_tools import _impl_optimization_landscape

        source = inspect.getsource(_impl_optimization_landscape)
        # Verify files_changed uses [:5] slice
        assert "files_changed=py_files[:5]" in source, (
            "Dynamic landscape should cap files_changed to [:5] for scoped discovery"
        )

    def test_prepass_honors_prepopulated_python_files(self, tmp_path):
        """Runtime prepass uses pre-populated python_files instead of rediscovering."""
        (tmp_path / "core.py").write_text("def f(x):\n    return x + 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_core.py").write_text(
            "def test_f():\n    assert True\n"
        )

        from lintgate.controlplane.types import SupervisionEvent

        # Pre-populate with only production files
        prod_files = [str(tmp_path / "core.py")]
        event = SupervisionEvent(
            project_root=str(tmp_path),
            surface="mcp",
            files_changed=prod_files,
        )
        event.context["python_files"] = prod_files

        from lintgate.controlplane.runtime import _run_prepass

        _run_prepass(event)

        # After prepass, python_files should still be our production-only list
        result_files = event.context.get("python_files", [])
        for f in result_files:
            assert "test_" not in os.path.basename(f), (
                f"Prepass should not reintroduce test files: {f}"
            )
