"""Tests for lintgate/testing/platonic_mutation.py — mutation/cache helpers."""

from __future__ import annotations

from lintgate.testing.platonic_mutation import (
    find_mutation_result,
    load_mutation_cache,
    persist_mutation_cache_entries,
    run_mutation_sampling,
)


# --- find_mutation_result ---


def test_find_mutation_result_found():
    results = [
        {"function_key": "mod.func_a", "survived": 2},
        {"function_key": "mod.func_b", "survived": 0},
    ]
    match = find_mutation_result(results, "mod.func_b")
    assert match is not None
    assert match["function_key"] == "mod.func_b"
    assert match["survived"] == 0


def test_find_mutation_result_not_found():
    results = [
        {"function_key": "mod.func_a", "survived": 2},
    ]
    match = find_mutation_result(results, "mod.nonexistent")
    assert match is None


def test_find_mutation_result_empty_list():
    match = find_mutation_result([], "any_key")
    assert match is None


def test_find_mutation_result_first_match_wins():
    results = [
        {"function_key": "mod.func", "index": 0},
        {"function_key": "mod.func", "index": 1},
    ]
    match = find_mutation_result(results, "mod.func")
    assert match is not None
    assert match["index"] == 0


# --- load_mutation_cache ---


def test_load_mutation_cache_nonexistent_project():
    """Should return None when project doesn't exist."""
    result = load_mutation_cache("/definitely/not/a/project", "src/module.py")
    assert result is None


def test_load_mutation_cache_graceful_on_import_error():
    """Should return None if dependencies are unavailable."""
    from unittest.mock import patch

    with patch.dict("sys.modules", {"mcp_tools._mutation_impl": None}):
        result = load_mutation_cache("/tmp", "file.py")
    assert result is None


# --- persist_mutation_cache_entries ---


def test_persist_mutation_cache_none_input():
    """None input should be a no-op."""
    persist_mutation_cache_entries("/tmp", None)  # Should not raise


def test_persist_mutation_cache_empty_dict():
    """Empty dict should be a no-op."""
    persist_mutation_cache_entries("/tmp", {})  # Should not raise


def test_persist_mutation_cache_skips_empty_keys():
    """Entries with empty function_key should be skipped."""
    persist_mutation_cache_entries("/tmp", {"": {"data": 1}})  # Should not raise


# --- run_mutation_sampling ---


def test_run_mutation_sampling_nonexistent():
    """Should return empty list for nonexistent project."""
    result = run_mutation_sampling("/definitely/not/a/project", "file.py")
    assert result == []


def test_run_mutation_sampling_with_options():
    """Should accept all keyword arguments without crashing."""
    result = run_mutation_sampling(
        "/definitely/not/a/project",
        "file.py",
        budget_ms=1000,
        generated_test_files=["test_file.py"],
        max_per_category=2,
        seed=42,
    )
    assert result == []
