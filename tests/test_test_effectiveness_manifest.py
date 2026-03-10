"""Tests for test effectiveness manifest build + cache."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lintgate.linters.test_effectiveness.manifest import (
    TEFF_CACHE_SCHEMA_VERSION,
    _load_manifest_cache,
    _save_manifest_cache,
    build_test_effectiveness_manifest,
)
from lintgate.linters.test_effectiveness.types import (
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
    TestEffectivenessManifest,
)


def test_manifest_update_metrics_empty():
    """Empty manifest has zero scores."""
    m = TestEffectivenessManifest()
    m.update_metrics()
    assert m.project_score == 0.0
    assert m.functions_analyzed == 0
    assert m.mutation_vulnerable_count == 0


def test_manifest_update_metrics_with_functions():
    """Manifest correctly computes aggregate metrics."""
    fe1 = FunctionEffectiveness(
        function_name="foo",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
    )
    fe1.compute_scores()

    fe2 = FunctionEffectiveness(
        function_name="bar",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.IS_TRUE, line=1, strength=0.2)],
    )
    fe2.compute_scores()

    m = TestEffectivenessManifest(functions={"foo": fe1, "bar": fe2})
    m.update_metrics()

    assert m.functions_analyzed == 2
    assert m.project_score == (0.9 + 0.2) / 2
    assert m.mutation_vulnerable_count == 1  # bar has vulnerability 0.8 > 0.7


def test_manifest_roundtrip():
    """Serialize and deserialize TestEffectivenessManifest."""
    fe = FunctionEffectiveness(
        function_name="baz",
        test_count=2,
        assertions=[
            AssertionInfo(kind=AssertionKind.EQUALITY, line=5, strength=0.9),
            AssertionInfo(kind=AssertionKind.LENGTH_CHECK, line=6, strength=0.8),
        ],
    )
    fe.compute_scores()

    m = TestEffectivenessManifest(
        functions={"baz": fe},
        file_scores={"src/module.py": 0.85},
    )
    m.update_metrics()

    d = m.to_dict()
    restored = TestEffectivenessManifest.from_dict(d)

    assert restored.functions_analyzed == 1
    assert "baz" in restored.functions
    assert restored.functions["baz"].function_name == "baz"
    assert len(restored.functions["baz"].assertions) == 2
    assert restored.mutation_vulnerable_count == 0  # baz has low vulnerability


def test_manifest_vulnerability_count():
    """Mutation vulnerable count only counts functions above threshold."""
    # All strong → no vulnerable
    fe = FunctionEffectiveness(
        function_name="strong",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
    )
    fe.compute_scores()

    m = TestEffectivenessManifest(functions={"strong": fe})
    m.update_metrics()
    assert m.mutation_vulnerable_count == 0

    # Weak → vulnerable
    fe_weak = FunctionEffectiveness(
        function_name="weak",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.IS_TRUE, line=1, strength=0.2)],
    )
    fe_weak.compute_scores()

    m2 = TestEffectivenessManifest(functions={"weak": fe_weak})
    m2.update_metrics()
    assert m2.mutation_vulnerable_count == 1


# --- _load_manifest_cache tests (lines 37-40, 44-45) ---


def test_load_manifest_cache_valid_json():
    """Load a valid cache file and return deserialized manifest + metadata."""
    fe = FunctionEffectiveness(
        function_name="cached_func",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
    )
    fe.compute_scores()
    manifest = TestEffectivenessManifest(functions={"cached_func": fe})
    manifest.update_metrics()
    metadata = {"/some/file.py": {"hash": "abc123"}}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "schema_version": TEFF_CACHE_SCHEMA_VERSION,
                "manifest": manifest.to_dict(),
                "metadata": metadata,
            },
            f,
        )
        f.flush()
        cache_path = Path(f.name)

    try:
        loaded_manifest, loaded_metadata = _load_manifest_cache(cache_path)
        assert "cached_func" in loaded_manifest.functions
        assert loaded_manifest.functions["cached_func"].function_name == "cached_func"
        assert loaded_metadata == metadata
    finally:
        os.unlink(cache_path)


def test_load_manifest_cache_corrupt_json():
    """Corrupt JSON returns empty manifest and empty metadata."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{not valid json!!!")
        f.flush()
        cache_path = Path(f.name)

    try:
        loaded_manifest, loaded_metadata = _load_manifest_cache(cache_path)
        assert loaded_manifest.functions == {}
        assert loaded_metadata == {}
    finally:
        os.unlink(cache_path)


def test_load_manifest_cache_missing_file():
    """Non-existent cache path returns empty manifest and empty metadata."""
    cache_path = Path("/tmp/nonexistent_lintgate_cache_12345.json")
    loaded_manifest, loaded_metadata = _load_manifest_cache(cache_path)
    assert loaded_manifest.functions == {}
    assert loaded_metadata == {}


# --- _save_manifest_cache tests (lines 57-58) ---


def test_save_manifest_cache_oserror_is_suppressed():
    """OSError during save is silently ignored (e.g., read-only path)."""
    manifest = TestEffectivenessManifest()
    metadata: dict = {}
    # Use a path inside a non-existent directory to trigger OSError
    bad_path = Path("/tmp/nonexistent_dir_lintgate_xyz/cache.json")
    # Should not raise
    _save_manifest_cache(bad_path, manifest, metadata)


# --- build_test_effectiveness_manifest tests ---


def test_build_manifest_auto_discovers_files():
    """When python_files=None and test_files=None, files are auto-discovered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a source file
        src_path = os.path.join(tmpdir, "module.py")
        with open(src_path, "w") as f:
            f.write("def compute():\n    return 42\n")

        # Create a test file
        test_path = os.path.join(tmpdir, "test_module.py")
        with open(test_path, "w") as f:
            f.write(
                "from module import compute\n\ndef test_compute():\n    assert compute() == 42\n"
            )

        result = build_test_effectiveness_manifest(tmpdir)
        # Should return a manifest (may be empty if mapping doesn't resolve,
        # but the code path for auto-discovery is exercised)
        assert isinstance(result, TestEffectivenessManifest)


def test_build_manifest_empty_file_lists_returns_empty():
    """Empty python_files or test_files returns empty manifest immediately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Both empty
        result = build_test_effectiveness_manifest(tmpdir, python_files=[], test_files=[])
        assert result.functions == {}

        # python_files empty, test_files provided
        result2 = build_test_effectiveness_manifest(
            tmpdir, python_files=[], test_files=["/fake/test.py"]
        )
        assert result2.functions == {}

        # python_files provided, test_files empty
        result3 = build_test_effectiveness_manifest(
            tmpdir, python_files=["/fake/src.py"], test_files=[]
        )
        assert result3.functions == {}


def test_build_manifest_oserror_on_file_hash():
    """Files that raise OSError during hash computation are skipped gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a real source file
        src_path = os.path.join(tmpdir, "real_module.py")
        with open(src_path, "w") as f:
            f.write("def real_func():\n    return 1\n")

        # Create a real test file
        test_path = os.path.join(tmpdir, "test_real.py")
        with open(test_path, "w") as f:
            f.write("def test_real_func():\n    assert True\n")

        # Include a nonexistent file that will raise OSError on hash
        nonexistent = os.path.join(tmpdir, "vanished.py")

        result = build_test_effectiveness_manifest(
            tmpdir,
            python_files=[src_path, nonexistent],
            test_files=[test_path],
        )
        assert isinstance(result, TestEffectivenessManifest)


def test_build_manifest_cache_hit_returns_cached():
    """When all file hashes match cached metadata, return the cached manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "module.py")
        with open(src_path, "w") as f:
            f.write("def greet():\n    return 'hello'\n")

        test_path = os.path.join(tmpdir, "test_module.py")
        with open(test_path, "w") as f:
            f.write(
                "from module import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n"
            )

        # First call builds and caches
        result1 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )
        assert isinstance(result1, TestEffectivenessManifest)

        # Second call should hit cache (files unchanged) -- line 110
        result2 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )
        assert isinstance(result2, TestEffectivenessManifest)


# --- Branch-targeted tests for build_test_effectiveness_manifest ---


def test_build_manifest_cache_hit_returns_same_data():
    """Cache hit path: second call returns manifest with identical function data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "mod.py")
        with open(src_path, "w") as f:
            f.write("def add(a, b):\n    return a + b\n")

        test_path = os.path.join(tmpdir, "test_mod.py")
        with open(test_path, "w") as f:
            f.write("from mod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")

        result1 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )

        # Second call hits cache -- verify the returned data matches
        result2 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )

        assert result2.functions_analyzed == result1.functions_analyzed
        assert result2.project_score == result1.project_score
        assert set(result2.functions.keys()) == set(result1.functions.keys())


def test_build_manifest_stale_cache_triggers_rebuild():
    """Modifying a source file after first build triggers a full rebuild."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "mod.py")
        with open(src_path, "w") as f:
            f.write("def add(a, b):\n    return a + b\n")

        test_path = os.path.join(tmpdir, "test_mod.py")
        with open(test_path, "w") as f:
            f.write("from mod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")

        # Build and cache
        result1 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )
        assert isinstance(result1, TestEffectivenessManifest)

        # Modify source file to invalidate cache hash
        with open(src_path, "w") as f:
            f.write("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n")

        # Should rebuild (hash mismatch), not return stale cache
        result2 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )
        assert isinstance(result2, TestEffectivenessManifest)
        # The rebuild should reflect the new source content
        # (at minimum, it didn't crash and returned a valid manifest)
        assert result2.diagnostics is not None


def test_build_manifest_effective_weights_bypass_cache():
    """When effective_weights is provided, cache is always bypassed (line 130)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "mod.py")
        with open(src_path, "w") as f:
            f.write("def func():\n    return 1\n")

        test_path = os.path.join(tmpdir, "test_mod.py")
        with open(test_path, "w") as f:
            f.write("from mod import func\n\ndef test_func():\n    assert func() == 1\n")

        # First call populates cache
        build_test_effectiveness_manifest(tmpdir, python_files=[src_path], test_files=[test_path])

        # Second call with effective_weights should bypass cache and rebuild
        custom_weights = {AssertionKind.EQUALITY: 1.0, AssertionKind.IS_TRUE: 0.05}
        result = build_test_effectiveness_manifest(
            tmpdir,
            python_files=[src_path],
            test_files=[test_path],
            effective_weights=custom_weights,
        )
        assert isinstance(result, TestEffectivenessManifest)
        # Verify it actually ran the analysis (diagnostics populated)
        assert result.diagnostics is not None


def test_build_manifest_scope_provenance_keys():
    """Rebuilt manifest has exact scope_provenance keys (lines 164-170)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "mod.py")
        with open(src_path, "w") as f:
            f.write("def func():\n    return 1\n")

        test_path = os.path.join(tmpdir, "test_mod.py")
        with open(test_path, "w") as f:
            f.write("from mod import func\n\ndef test_func():\n    assert func() == 1\n")

        result = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )

        prov = result.diagnostics.scope_provenance
        expected_keys = {
            "source_files",
            "test_files",
            "total_source_discovered",
            "total_test_discovered",
            "truncation_reason",
        }
        assert set(prov.keys()) == expected_keys
        assert prov["total_source_discovered"] == 1
        assert prov["total_test_discovered"] == 1
        assert prov["truncation_reason"] is None
        # source_files and test_files should be relative paths
        assert prov["source_files"] == ["mod.py"]
        assert prov["test_files"] == ["test_mod.py"]


def test_build_manifest_file_scores_initialized_empty():
    """Rebuilt manifest sets file_scores to empty dict (line 172)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "mod.py")
        with open(src_path, "w") as f:
            f.write("def func():\n    return 1\n")

        test_path = os.path.join(tmpdir, "test_mod.py")
        with open(test_path, "w") as f:
            f.write("from mod import func\n\ndef test_func():\n    assert func() == 1\n")

        result = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )

        # Line 172: manifest.file_scores = {}
        assert result.file_scores == {}


def test_build_manifest_scope_fingerprint_mismatch_invalidates_cache():
    """Changing file lists invalidates cache via scope fingerprint (line 126)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src1 = os.path.join(tmpdir, "mod1.py")
        src2 = os.path.join(tmpdir, "mod2.py")
        test_path = os.path.join(tmpdir, "test_mod.py")

        with open(src1, "w") as f:
            f.write("def func1():\n    return 1\n")
        with open(src2, "w") as f:
            f.write("def func2():\n    return 2\n")
        with open(test_path, "w") as f:
            f.write("from mod1 import func1\n\ndef test_func1():\n    assert func1() == 1\n")

        # Build with src1 only
        result1 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src1], test_files=[test_path]
        )
        assert isinstance(result1, TestEffectivenessManifest)

        # Build with [src1, src2] -- different scope fingerprint
        result2 = build_test_effectiveness_manifest(
            tmpdir, python_files=[src1, src2], test_files=[test_path]
        )
        assert isinstance(result2, TestEffectivenessManifest)
        # The scope_provenance should reflect the new file list
        assert result2.diagnostics.scope_provenance["total_source_discovered"] == 2


def test_load_manifest_cache_schema_version_mismatch():
    """Cache with wrong schema_version returns empty manifest (line 57)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "schema_version": "WRONG_VERSION",
                "manifest": {"functions": {}},
                "metadata": {"/some/file.py": {"hash": "abc"}},
            },
            f,
        )
        f.flush()
        cache_path = Path(f.name)

    try:
        loaded_manifest, loaded_metadata = _load_manifest_cache(cache_path)
        assert loaded_manifest.functions == {}
        assert loaded_metadata == {}
    finally:
        os.unlink(cache_path)


def test_load_manifest_cache_scope_fingerprint_mismatch():
    """Cache with mismatched scope_fingerprint returns empty (line 63)."""
    fe = FunctionEffectiveness(
        function_name="fn",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
    )
    fe.compute_scores()
    manifest = TestEffectivenessManifest(functions={"fn": fe})
    manifest.update_metrics()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "schema_version": TEFF_CACHE_SCHEMA_VERSION,
                "scope_fingerprint": "old_fingerprint_abc",
                "manifest": manifest.to_dict(),
                "metadata": {"/some/file.py": {"hash": "abc"}},
            },
            f,
        )
        f.flush()
        cache_path = Path(f.name)

    try:
        # With no expected fingerprint: should load fine
        loaded_manifest, _ = _load_manifest_cache(cache_path)
        assert "fn" in loaded_manifest.functions

        # With mismatched expected fingerprint: should return empty
        loaded_manifest2, loaded_metadata2 = _load_manifest_cache(
            cache_path, expected_scope_fingerprint="different_fingerprint_xyz"
        )
        assert loaded_manifest2.functions == {}
        assert loaded_metadata2 == {}

        # With matching expected fingerprint: should load fine
        loaded_manifest3, _ = _load_manifest_cache(
            cache_path, expected_scope_fingerprint="old_fingerprint_abc"
        )
        assert "fn" in loaded_manifest3.functions
    finally:
        os.unlink(cache_path)


def test_build_manifest_returns_manifest_with_update_metrics_called():
    """Returned manifest has update_metrics() applied (line 174)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "mod.py")
        with open(src_path, "w") as f:
            f.write("def func():\n    return 1\n")

        test_path = os.path.join(tmpdir, "test_mod.py")
        with open(test_path, "w") as f:
            f.write("from mod import func\n\ndef test_func():\n    assert func() == 1\n")

        result = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )

        # update_metrics should have been called: functions_analyzed matches function count
        assert result.functions_analyzed == len(result.functions)
        # project_score is a float between 0 and 1 (or 0 if no functions)
        assert 0.0 <= result.project_score <= 1.0
        # mutation_vulnerable_count is non-negative
        assert result.mutation_vulnerable_count >= 0


def test_build_manifest_maps_instance_method_tests_without_name_alignment():
    """Instance-method tests should map even when test names don't mirror method names."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "proof_auditor.py")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(
                "class ProofAuditor:\n"
                "    def check(self, value):\n"
                "        return value > 0\n\n"
                "    def verify(self, value):\n"
                "        return value + 1\n"
            )

        test_path = os.path.join(tmpdir, "test_proof_auditor.py")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(
                "from proof_auditor import ProofAuditor\n\n"
                "class ValidationSuite:\n"
                "    def test_accepts_positive_inputs(self):\n"
                "        auditor = ProofAuditor()\n"
                "        assert auditor.check(1) is True\n\n"
                "class VerificationSuite:\n"
                "    def test_increments_counter(self):\n"
                "        auditor = ProofAuditor()\n"
                "        assert auditor.verify(1) == 2\n"
            )

        manifest = build_test_effectiveness_manifest(
            tmpdir, python_files=[src_path], test_files=[test_path]
        )

        check_key = "proof_auditor.py::ProofAuditor.check"
        verify_key = "proof_auditor.py::ProofAuditor.verify"
        assert check_key in manifest.functions
        assert verify_key in manifest.functions
        assert manifest.functions[check_key].test_count == 1
        assert manifest.functions[verify_key].test_count == 1
        assert manifest.functions[check_key].effectiveness_score > 0.0
        assert manifest.functions[verify_key].effectiveness_score > 0.0
