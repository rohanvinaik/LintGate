"""Tests for semantic test discovery (Layer 1.5 — TF-IDF fingerprint matching)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest


class TestExtractFingerprint:
    """Test fingerprint extraction from Python source files."""

    def test_extracts_imports(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text("import os\nimport json\nfrom pathlib import Path\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert "os" in tokens
        assert "json" in tokens
        assert "pathlib" in tokens
        assert "path" in tokens

    def test_extracts_function_names(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text("def calculate_score(x):\n    return x * 2\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert "calculate_score" in tokens

    def test_extracts_class_names(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text("class MyProcessor:\n    pass\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert "myprocessor" in tokens

    def test_extracts_called_names(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text("result = process_data(input)\nos.path.join('a', 'b')\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert "process_data" in tokens
        assert "join" in tokens

    def test_extracts_dotted_string_refs(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text('mock.patch("lintgate.core.process")\n')
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert "lintgate" in tokens
        assert "core" in tokens
        assert "process" in tokens

    def test_ignores_http_urls(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text('url = "http://example.com/api"\n')
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert "http://example.com/api" not in tokens

    def test_handles_syntax_error(self, tmp_path):
        source = tmp_path / "bad.py"
        source.write_text("def foo(:\n    pass\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert tokens == []

    def test_handles_missing_file(self):
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint("/nonexistent/path.py")
        assert tokens == []

    def test_tokens_are_lowercase(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text("class MyClass:\n    pass\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert all(t == t.lower() for t in tokens)

    def test_no_single_char_tokens(self, tmp_path):
        source = tmp_path / "module.py"
        source.write_text("import a\nx = 1\n")
        from lintgate.specification.semantic_discovery import extract_fingerprint

        tokens = extract_fingerprint(str(source))
        assert all(len(t) > 1 for t in tokens)


class TestTfidfCosine:
    """Test the pure-Python TF-IDF cosine similarity."""

    def test_identical_documents_high_score(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        query = ["import", "os", "json", "pathlib"]
        corpus = [["import", "os", "json", "pathlib"]]
        scores = _tfidf_cosine(query, corpus)
        assert scores[0] > 0.99

    def test_disjoint_documents_zero_score(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        query = ["alpha", "beta", "gamma"]
        corpus = [["delta", "epsilon", "zeta"]]
        scores = _tfidf_cosine(query, corpus)
        assert scores[0] == 0.0

    def test_partial_overlap_intermediate_score(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        query = ["os", "json", "pathlib", "unique_a"]
        corpus = [["os", "json", "requests", "unique_b"]]
        scores = _tfidf_cosine(query, corpus)
        assert 0.0 < scores[0] < 1.0

    def test_empty_query_returns_zeros(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        scores = _tfidf_cosine([], [["a", "b"]])
        assert scores == [0.0]

    def test_empty_corpus_returns_empty(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        scores = _tfidf_cosine(["a"], [])
        assert scores == []

    def test_multiple_corpus_docs_ranked(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        query = ["os", "json", "pathlib"]
        corpus = [
            ["os", "json", "pathlib"],  # Identical
            ["os", "requests"],  # Partial
            ["numpy", "pandas"],  # Disjoint
        ]
        scores = _tfidf_cosine(query, corpus)
        assert scores[0] > scores[1] > scores[2]

    def test_deterministic(self):
        from lintgate.specification.semantic_discovery import _tfidf_cosine

        query = ["os", "json", "ast"]
        corpus = [["os", "json"], ["ast", "sys"]]
        scores1 = _tfidf_cosine(query, corpus)
        scores2 = _tfidf_cosine(query, corpus)
        assert scores1 == scores2


class TestCache:
    """Test fingerprint cache management."""

    def test_save_and_load_cache(self, tmp_path):
        from lintgate.specification.semantic_discovery import _load_cache, _save_cache

        project = str(tmp_path)
        cache = {"file1.py": {"tokens": ["a", "b"], "mtime": 12345.0}}
        _save_cache(project, cache)
        loaded = _load_cache(project)
        assert loaded == cache

    def test_load_nonexistent_cache(self, tmp_path):
        from lintgate.specification.semantic_discovery import _load_cache

        loaded = _load_cache(str(tmp_path))
        assert loaded == {}

    def test_staleness_detection(self, tmp_path):
        from lintgate.specification.semantic_discovery import _is_stale

        f = tmp_path / "test.py"
        f.write_text("x = 1")
        entry = {"mtime": 0}  # Old mtime
        assert _is_stale(entry, str(f)) is True

        entry = {"mtime": os.path.getmtime(str(f))}
        assert _is_stale(entry, str(f)) is False

    def test_stale_for_missing_file(self):
        from lintgate.specification.semantic_discovery import _is_stale

        assert _is_stale({"mtime": 0}, "/nonexistent") is True


class TestDiscoverSemanticTestFiles:
    """Test end-to-end semantic test discovery."""

    def _setup_project(self, tmp_path):
        """Create a minimal project with source and test files."""
        src_dir = tmp_path / "lintgate"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("")

        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        # Source file: uses json, os, has specific function names
        (src_dir / "core.py").write_text(
            "import json\nimport os\n"
            "from pathlib import Path\n\n"
            "def process_data(input_val):\n"
            "    return json.loads(input_val)\n\n"
            "def validate_schema(data):\n"
            "    return isinstance(data, dict)\n"
        )

        # Test file that semantically matches: imports json, tests process_data
        (test_dir / "test_core.py").write_text(
            "import json\nimport pytest\n"
            "from lintgate.core import process_data\n\n"
            "def test_process_data():\n"
            "    assert process_data('{}')\n"
        )

        # Test file that partially matches: uses json but different focus
        (test_dir / "test_utils.py").write_text(
            "import json\nimport os\n\n"
            "def test_something():\n"
            "    pass\n"
        )

        # Test file that doesn't match: completely different domain
        (test_dir / "test_unrelated.py").write_text(
            "import numpy\nimport pandas\n\n"
            "def test_stats():\n"
            "    pass\n"
        )

        return str(tmp_path)

    def test_finds_semantically_similar_tests(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        project = self._setup_project(tmp_path)
        results = discover_semantic_test_files(project, "lintgate/core.py")
        assert len(results) >= 1
        # test_core.py should rank highest (shares imports + function names)
        filenames = [os.path.basename(f) for f, _ in results]
        assert "test_core.py" in filenames

    def test_scores_are_sorted_descending(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        project = self._setup_project(tmp_path)
        results = discover_semantic_test_files(project, "lintgate/core.py", threshold=0.01)
        if len(results) > 1:
            scores = [s for _, s in results]
            assert scores == sorted(scores, reverse=True)

    def test_respects_threshold(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        project = self._setup_project(tmp_path)
        # Very high threshold should return fewer results
        high = discover_semantic_test_files(project, "lintgate/core.py", threshold=0.9)
        low = discover_semantic_test_files(project, "lintgate/core.py", threshold=0.01)
        assert len(high) <= len(low)

    def test_respects_max_results(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        project = self._setup_project(tmp_path)
        results = discover_semantic_test_files(
            project, "lintgate/core.py", threshold=0.01, max_results=1
        )
        assert len(results) <= 1

    def test_missing_source_returns_empty(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        project = self._setup_project(tmp_path)
        results = discover_semantic_test_files(project, "nonexistent.py")
        assert results == []

    def test_no_test_dir_returns_empty(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        src = tmp_path / "source.py"
        src.write_text("x = 1")
        results = discover_semantic_test_files(str(tmp_path), "source.py")
        assert results == []

    def test_scores_are_floats(self, tmp_path):
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        project = self._setup_project(tmp_path)
        results = discover_semantic_test_files(project, "lintgate/core.py", threshold=0.01)
        for _, score in results:
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_caches_fingerprints(self, tmp_path):
        from lintgate.specification.semantic_discovery import (
            _load_cache,
            discover_semantic_test_files,
        )

        project = self._setup_project(tmp_path)
        discover_semantic_test_files(project, "lintgate/core.py")
        cache = _load_cache(project)
        assert len(cache) > 0  # Some entries should be cached
