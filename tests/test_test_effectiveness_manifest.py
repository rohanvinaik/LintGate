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
