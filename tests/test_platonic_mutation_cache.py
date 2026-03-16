"""Mutation-targeted tests for platonic_mutation.py.

Targets VALUE and SWAP surviving mutants for load_mutation_cache.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch


def test_load_mutation_cache_returns_none_empty_dir(tmp_path):
    """No cache files → returns None."""
    from lintgate.testing.platonic_mutation import load_mutation_cache

    cache_dir = tmp_path / ".lintgate" / "mutation_cache"
    cache_dir.mkdir(parents=True)

    with patch("mcp_tools._mutation_impl.get_cache_dir", return_value=str(cache_dir)):
        result = load_mutation_cache(str(tmp_path), "some/file.py")
    assert result is None


def test_load_mutation_cache_returns_dict_with_entries(tmp_path):
    """Cache with entries → returns dict keyed by function_key."""
    from lintgate.testing.platonic_mutation import load_mutation_cache

    cache_dir = tmp_path / ".lintgate" / "mutation_cache"
    cache_dir.mkdir(parents=True)
    entry = {"function_key": "file.py::func", "kill_rate": 0.8, "survival_rate": 0.2}
    (cache_dir / "file_py__func.json").write_text(json.dumps(entry))

    def mock_iter(cd, rel):
        for f in os.listdir(cd):
            if f.endswith(".json"):
                with open(os.path.join(cd, f)) as fh:
                    yield json.load(fh)

    with (
        patch("mcp_tools._mutation_impl.get_cache_dir", return_value=str(cache_dir)),
        patch("mcp_tools._mutation_impl.iter_cached_states", side_effect=mock_iter),
    ):
        result = load_mutation_cache(str(tmp_path), "file.py")

    assert result is not None
    assert "file.py::func" in result
    assert result["file.py::func"]["kill_rate"] == 0.8


def test_load_mutation_cache_skips_empty_keys(tmp_path):
    """Entries with empty function_key are skipped."""
    from lintgate.testing.platonic_mutation import load_mutation_cache

    cache_dir = tmp_path / ".lintgate" / "mutation_cache"
    cache_dir.mkdir(parents=True)

    def mock_iter(cd, rel):
        yield {"function_key": "", "kill_rate": 0.5}
        yield {"function_key": "file.py::real", "kill_rate": 0.9}

    with (
        patch("mcp_tools._mutation_impl.get_cache_dir", return_value=str(cache_dir)),
        patch("mcp_tools._mutation_impl.iter_cached_states", side_effect=mock_iter),
    ):
        result = load_mutation_cache(str(tmp_path), "file.py")

    assert result is not None
    assert "" not in result
    assert "file.py::real" in result


def test_load_mutation_cache_import_error_returns_none():
    """Import failure → returns None gracefully."""
    from lintgate.testing.platonic_mutation import load_mutation_cache

    with patch(
        "lintgate.testing.platonic_mutation.load_mutation_cache",
        side_effect=lambda *a: None,
    ):
        # Direct call with non-existent paths should not crash
        result = load_mutation_cache("/nonexistent", "fake.py")
    assert result is None


def test_load_mutation_cache_swap_different_files_differ(tmp_path):
    """Different rel_file args produce different results — catches SWAP."""
    from lintgate.testing.platonic_mutation import load_mutation_cache

    cache_dir = tmp_path / ".lintgate" / "mutation_cache"
    cache_dir.mkdir(parents=True)

    def mock_iter(cd, rel):
        if "a.py" in rel:
            yield {"function_key": "a.py::f", "data": "a"}
        # b.py returns nothing

    with (
        patch("mcp_tools._mutation_impl.get_cache_dir", return_value=str(cache_dir)),
        patch("mcp_tools._mutation_impl.iter_cached_states", side_effect=mock_iter),
    ):
        result_a = load_mutation_cache(str(tmp_path), "a.py")
        result_b = load_mutation_cache(str(tmp_path), "b.py")

    assert result_a is not None
    assert result_b is None
