"""Tests for lintgate/testing/platonic_mutation.py — mutation/cache helpers."""

from __future__ import annotations

from unittest.mock import patch

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
    assert isinstance(result, type(None))


def test_load_mutation_cache_graceful_on_import_error():
    """Should return None if dependencies are unavailable."""
    with patch.dict("sys.modules", {"mcp_tools._mutation_impl": None}):
        result = load_mutation_cache("/tmp", "file.py")
    assert result is None
    assert isinstance(result, type(None))


def test_load_mutation_cache_returns_dict_when_entries_exist():
    """When cache has entries, returns dict keyed by function_key."""
    mock_states = [
        {"function_key": "mod.func_a", "survived": 3},
        {"function_key": "mod.func_b", "survived": 0},
    ]
    with (
        patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache"),
        patch("mcp_tools._mutation_impl.iter_cached_states", return_value=mock_states),
    ):
        result = load_mutation_cache("/proj", "mod.py")
    assert result == {
        "mod.func_a": {"function_key": "mod.func_a", "survived": 3},
        "mod.func_b": {"function_key": "mod.func_b", "survived": 0},
    }


# --- persist_mutation_cache_entries ---


def test_persist_mutation_cache_none_input():
    """None input should be a no-op — save_cached_state never called."""
    with patch("mcp_tools._mutation_impl.save_cached_state") as mock_save:
        persist_mutation_cache_entries("/tmp", None)
        mock_save.assert_not_called()


def test_persist_mutation_cache_empty_dict():
    """Empty dict should be a no-op — save_cached_state never called."""
    with patch("mcp_tools._mutation_impl.save_cached_state") as mock_save:
        persist_mutation_cache_entries("/tmp", {})
        mock_save.assert_not_called()


def test_persist_mutation_cache_skips_empty_keys():
    """Entries with empty function_key should be skipped."""
    with (
        patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache"),
        patch("mcp_tools._mutation_impl.save_cached_state") as mock_save,
    ):
        persist_mutation_cache_entries("/tmp", {"": {"data": 1}})
        mock_save.assert_not_called()


def test_persist_mutation_cache_saves_valid_entries():
    """Valid entries are saved with function_key injected into payload."""
    with (
        patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache"),
        patch("mcp_tools._mutation_impl.save_cached_state") as mock_save,
    ):
        persist_mutation_cache_entries("/proj", {"mod.func": {"survived": 2}})
        mock_save.assert_called_once_with(
            "/tmp/cache",
            "mod.func",
            {"survived": 2, "function_key": "mod.func"},
        )


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
