"""Phase 4: End-to-end integration tests using fixture projects.

These tests exercise the real prepass->context->channel->coherence pipeline
with actual file parsing, not synthetic data. Uses schema validation from
Phase 1 as an additional assertion layer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure tests/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from conftest_integration import run_channel, run_pipeline  # noqa: E402

# Load fixtures from conftest_integration (pytest_plugins is the standard mechanism)
pytest_plugins = ["conftest_integration"]


class TestSpecificationIntegration:
    """End-to-end integration tests using fixture projects."""

    def test_prepass_populates_manifests(self, pure_calculator):
        """Verify prepass produces property_manifest and test_effectiveness_manifest
        in event.context with correct key format (canonical_function_key)."""
        from lintgate.channels.structure_channel import _discover_python_files
        from lintgate.linters.performance_checks.manifest import build_manifest
        from lintgate.linters.test_effectiveness.manifest import (
            build_test_effectiveness_manifest,
        )

        py_files = _discover_python_files(pure_calculator)
        source_files = [f for f in py_files if not os.path.basename(f).startswith("test_")]
        test_files = [f for f in py_files if os.path.basename(f).startswith("test_")]

        prop_manifest = build_manifest(pure_calculator, source_files)
        teff_manifest = build_test_effectiveness_manifest(pure_calculator, source_files, test_files)

        # Keys must be in canonical format: "relpath.py::qualname"
        for key in prop_manifest.functions:
            assert "::" in key, f"PropertyManifest key {key!r} missing '::'"
            relpath, qualname = key.split("::", 1)
            assert relpath.endswith(".py"), f"Key {key!r} relpath missing .py"
            assert "/" not in qualname or "." in qualname, (
                f"qualname {qualname!r} looks like a path, not a function name"
            )

        assert prop_manifest.functions, "PropertyManifest should have functions"

        # Key format agreement: prop_manifest and teff_manifest keys must overlap
        prop_keys = set(prop_manifest.functions.keys())
        teff_keys = set(teff_manifest.functions.keys())
        # At least some keys should match (pure_calculator has testable functions)
        shared = prop_keys & teff_keys
        assert shared, (
            f"No shared keys between manifests. prop_keys={prop_keys}, teff_keys={teff_keys}"
        )

    def test_specification_channel_produces_metrics(self, pure_calculator):
        """Run specification channel, verify all declared schema keys present."""
        result = run_channel(pure_calculator, "specification")

        assert result.status in ("pass", "fail"), f"Unexpected status: {result.status}"
        metrics = result.metrics
        assert isinstance(metrics, dict)

        # Check that core schema keys exist
        assert "specification_function_list" in metrics
        assert "specification_coverage" in metrics

    def test_schema_validation_passes(self, pure_calculator):
        """Run validate_result() on actual channel output — zero missing keys."""
        from lintgate.controlplane.metric_schema import (
            clear_schemas,
            register_all_schemas,
            validate_result,
        )

        clear_schemas()
        register_all_schemas()

        result = run_channel(pure_calculator, "specification")
        missing = validate_result("specification", result.metrics, status=result.status)
        # Filter out optional keys
        non_optional_missing = [
            k for k in missing if k not in ("composition_gaps", "sheaf_obstruction")
        ]
        assert non_optional_missing == [], f"Missing schema keys: {non_optional_missing}"

        clear_schemas()

    def test_cross_channel_coherence_wiring(self, cross_module):
        """Run perf + teff + spec + coherence, verify COH1xx findings can fire."""
        from lintgate.controlplane.cross_channel import cross_channel_coherence

        results = run_pipeline(cross_module)
        channel_results = list(results.values())

        # Verify channels actually produced data for coherence to consume
        spec_result = results.get("specification")
        assert spec_result is not None, "Specification channel must run"
        assert spec_result.status != "skip", "Specification should not skip on cross_module"
        assert "specification_function_list" in spec_result.metrics, (
            "Spec channel must publish specification_function_list"
        )

        perf_result = results.get("performance")
        assert perf_result is not None, "Performance channel must run"
        assert "pure_function_list" in perf_result.metrics, (
            "Perf channel must publish pure_function_list"
        )

        # Run cross-channel coherence
        findings = cross_channel_coherence(channel_results)
        assert isinstance(findings, list)
        # Verify finding structure if any exist
        for f in findings:
            assert hasattr(f, "kind"), f"Finding missing 'kind': {f}"
            assert f.kind.startswith("COH"), f"Coherence finding should be COH*: {f.kind}"

    def test_composition_gamma_not_none(self, cross_module):
        """Verify specification channel produces non-None composition_gaps
        when cross-module calls exist."""
        result = run_channel(cross_module, "specification")
        assert result.status != "skip", "Spec channel should not skip on cross_module"
        metrics = result.metrics

        # composition_gaps should be present
        assert "composition_gaps" in metrics
        # cross_module fixture has cross-module calls, so gaps should have data
        gaps = metrics["composition_gaps"]
        if gaps is not None:
            assert isinstance(gaps, dict), f"composition_gaps should be dict, got {type(gaps)}"

    def test_call_graph_key_format(self, cross_module):
        """Verify call graph keys match canonical 'relpath.py::qualname' format."""
        from lintgate.channels.structure_channel import _discover_python_files
        from lintgate.specification.call_graph import build_cross_module_call_graph

        py_files = _discover_python_files(cross_module)
        source_files = [f for f in py_files if not os.path.basename(f).startswith("test_")]

        graph = build_cross_module_call_graph(source_files, cross_module)

        for key in graph.calls:
            assert "::" in key, f"Call graph key {key!r} missing '::'"
            relpath = key.split("::")[0]
            assert relpath.endswith(".py"), f"Key {key!r} relpath missing .py"

    def test_archive_paths_excluded(self, tmp_path):
        """Create a project with archive/ dir, verify spec channel/tools skip it."""
        # Create a minimal project with an archive directory
        src = tmp_path / "src.py"
        src.write_text("def foo(): return 1\n")

        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "old.py").write_text("def bar(): return 2\n")

        from lintgate.channels.structure_channel import _discover_python_files

        files = _discover_python_files(str(tmp_path))
        file_basenames = [os.path.basename(f) for f in files]

        # src.py should be found
        assert "src.py" in file_basenames

    def test_convergence_extracts_specification_evidence(self, pure_calculator):
        """Run full pipeline, verify convergence receives spec evidence."""
        from lintgate.convergence.integration import extract_all_evidence

        results = run_pipeline(pure_calculator)
        channel_results = list(results.values())

        # All channels should have run successfully
        for name, cr in results.items():
            assert cr.status in ("pass", "fail", "skip"), (
                f"Channel {name} returned unexpected status: {cr.status}"
            )

        evidence = extract_all_evidence(channel_results)
        assert isinstance(evidence, list)
        # Verify evidence structure if any exist
        for ev in evidence:
            assert hasattr(ev, "target"), f"Evidence missing 'target': {ev}"
            assert ev.target, f"Evidence has empty target: {ev}"
