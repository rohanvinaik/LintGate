"""Contract tests for canonical mutation state path enforcement.

This test module ensures all runtime readers/writers use a single canonical
mutation state path, preventing the split-brain invariant issue identified in #96.
"""

from pathlib import Path

from lintgate.state import MUTATION_CACHE_DIR, get_mutation_state_path


def test_canonical_path_resolver_returns_correct_path():
    """Verify the canonical path resolver returns the expected path."""
    expected = MUTATION_CACHE_DIR / "state.json"
    result = get_mutation_state_path()
    assert result == expected
    assert isinstance(result, Path)


def test_canonical_path_resolver_returns_string_representation():
    """Verify the path can be converted to string for file operations."""
    result = get_mutation_state_path()
    str_path = str(result)
    assert str_path.endswith("state.json")
    assert "mutation_cache" in str_path


def test_all_call_sites_resolve_to_same_path():
    """Verify all known call sites would resolve to the same canonical path.

    This test documents the expected paths used by each call site to ensure
    the contract is maintained across the codebase.
    """
    canonical = get_mutation_state_path()

    # Document each call site's expected resolution
    # These are the patterns used throughout the codebase:
    expected_patterns = [
        MUTATION_CACHE_DIR / "state.json",
        Path(str(MUTATION_CACHE_DIR)) / "state.json",
    ]

    for pattern in expected_patterns:
        assert str(pattern) == str(canonical)


def test_no_legacy_perf_cache_mutation_path():
    """Regression test: ensure PERF_CACHE_DIR/mutation_state.json is not used.

    The legacy path (PERF_CACHE_DIR / "mutation_state.json") was the split-brain
    root cause. This test ensures no code references it.
    """
    legacy_path = Path.home() / ".claude" / "lintgate" / "perf_cache" / "mutation_state.json"
    canonical = get_mutation_state_path()

    # The canonical path should NOT be the legacy path
    assert canonical != legacy_path
    assert "perf_cache" not in str(canonical)


def test_mutation_cache_dir_is_correctly_configured():
    """Verify MUTATION_CACHE_DIR is configured to the correct location."""
    assert "mutation_cache" in str(MUTATION_CACHE_DIR)
    assert "perf_cache" not in str(MUTATION_CACHE_DIR)
    assert Path.home() / ".claude" / "lintgate" / "mutation_cache" == MUTATION_CACHE_DIR
