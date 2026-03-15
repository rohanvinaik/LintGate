"""Semantic test discovery — TF-IDF fingerprint matching (Layer 1.5).

Low-confidence fallback layer for test file discovery. Consulted ONLY when
both dynamic coverage (Layer 1) AND static AST impact map (Layer 2) return
zero results from name-based matching.

Matches are tagged with ``linkage_source="semantic"`` and a confidence score
so downstream consumers (test_topology, mutation profiler) can distinguish
semantic linkage from higher-confidence dynamic/static linkage.

Zero external dependencies — pure-Python TF-IDF using collections.Counter
and math.log.
"""

from __future__ import annotations

import ast
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any


def extract_fingerprint(filepath: str) -> list[str]:
    """Extract a token fingerprint from a Python source file.

    Fingerprint includes:
    - Import names (bare and dotted)
    - Defined function/class names
    - Called function names
    - Dotted string references (e.g., 'module.func' in mock.patch)
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return []

    tokens: list[str] = []

    for node in ast.walk(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                tokens.append(alias.name)
                # Also add leaf component
                if "." in alias.name:
                    tokens.append(alias.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.append(node.module)
                if "." in node.module:
                    tokens.append(node.module.rsplit(".", 1)[-1])
            for alias in node.names:
                tokens.append(alias.name)

        # Defined names
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.append(node.name)

        # Called names
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                tokens.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                tokens.append(node.func.attr)

        # String constants that look like dotted module paths
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if "." in val and len(val) < 200 and not val.startswith("http"):
                parts = val.split(".")
                # Only collect if it looks like a Python path (no spaces, reasonable parts)
                if all(p.isidentifier() for p in parts if p):
                    for p in parts:
                        if p:
                            tokens.append(p)

    # Lowercase and deduplicate while preserving order for determinism
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        t_lower = t.lower()
        if t_lower not in seen and len(t_lower) > 1:
            seen.add(t_lower)
            result.append(t_lower)
    return result


def _tfidf_cosine(query_tokens: list[str], corpus: list[list[str]]) -> list[float]:
    """Compute TF-IDF cosine similarity between query and each corpus document.

    Pure-Python implementation — no sklearn dependency.

    Args:
        query_tokens: Token list for the query document.
        corpus: List of token lists, one per document.

    Returns:
        List of cosine similarity scores, one per corpus document.
    """
    if not query_tokens or not corpus:
        return [0.0] * len(corpus)

    # Build vocabulary from all documents (query + corpus)
    all_docs = [query_tokens] + corpus
    n_docs = len(all_docs)

    # Document frequency: how many docs contain each term
    df: Counter[str] = Counter()
    for doc in all_docs:
        unique_terms = set(doc)
        for term in unique_terms:
            df[term] += 1

    def _tfidf_vector(doc: list[str]) -> dict[str, float]:
        """Compute TF-IDF vector for a single document."""
        tf = Counter(doc)
        vec: dict[str, float] = {}
        for term, count in tf.items():
            # TF: raw count normalized by doc length
            tf_val = count / len(doc) if doc else 0.0
            # IDF: log(N / df) with smoothing
            idf_val = math.log((n_docs + 1) / (df.get(term, 0) + 1)) + 1.0
            vec[term] = tf_val * idf_val
        return vec

    def _cosine_sim(v1: dict[str, float], v2: dict[str, float]) -> float:
        """Cosine similarity between two sparse vectors."""
        # Dot product over shared keys
        shared = set(v1) & set(v2)
        if not shared:
            return 0.0
        dot = sum(v1[k] * v2[k] for k in shared)
        norm1 = math.sqrt(sum(v * v for v in v1.values()))
        norm2 = math.sqrt(sum(v * v for v in v2.values()))
        if norm1 < 1e-12 or norm2 < 1e-12:
            return 0.0
        return dot / (norm1 * norm2)

    query_vec = _tfidf_vector(query_tokens)
    return [_cosine_sim(query_vec, _tfidf_vector(doc)) for doc in corpus]


# -- Cache management ----------------------------------------------------------


_CACHE_FILENAME = "test_discovery_embeddings.json"


def _cache_path(project_root: str) -> Path:
    return Path(project_root) / ".lintgate" / _CACHE_FILENAME


def _load_cache(project_root: str) -> dict[str, Any]:
    """Load the fingerprint cache from disk."""
    path = _cache_path(project_root)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_cache(project_root: str, cache: dict[str, Any]) -> None:
    """Save the fingerprint cache to disk."""
    path = _cache_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass


def _is_stale(cache_entry: dict[str, Any], filepath: str) -> bool:
    """Check if a cache entry is stale based on mtime."""
    try:
        current_mtime = os.path.getmtime(filepath)
    except OSError:
        return True
    cached_mtime = cache_entry.get("mtime", 0)
    return current_mtime != cached_mtime


def _get_or_extract_fingerprint(
    filepath: str,
    cache: dict[str, Any],
) -> list[str]:
    """Get fingerprint from cache or extract fresh."""
    entry = cache.get(filepath)
    if entry and not _is_stale(entry, filepath):
        tokens = entry.get("tokens", [])
        if isinstance(tokens, list):
            return tokens

    tokens = extract_fingerprint(filepath)
    try:
        mtime = os.path.getmtime(filepath)
    except OSError:
        mtime = 0
    cache[filepath] = {"tokens": tokens, "mtime": mtime}
    return tokens


# -- Public API ----------------------------------------------------------------


def discover_semantic_test_files(
    project_root: str,
    source_file: str,
    *,
    threshold: float = 0.15,
    max_results: int = 5,
) -> list[tuple[str, float]]:
    """Find test files semantically similar to a source file.

    Uses TF-IDF cosine similarity between file fingerprints (imports,
    defined names, called names, dotted string refs).

    This is a LOW-CONFIDENCE fallback. Only use when name-based discovery
    (dynamic coverage + static AST impact map + filename matching) fails entirely.

    Args:
        project_root: Absolute path to project root.
        source_file: Relative or absolute path to the source file.
        threshold: Minimum similarity score to include (default 0.15).
        max_results: Maximum number of results to return.

    Returns:
        List of (test_file_path, score) tuples, sorted by score descending.
    """
    full_source = (
        os.path.join(project_root, source_file)
        if not os.path.isabs(source_file)
        else source_file
    )
    if not os.path.isfile(full_source):
        return []

    # Collect test files
    test_files: list[str] = []
    for td in ("tests", "test"):
        test_dir = os.path.join(project_root, td)
        if not os.path.isdir(test_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(test_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            for fname in filenames:
                if fname.endswith(".py") and (fname.startswith("test_") or fname.endswith("_test.py")):
                    test_files.append(os.path.join(dirpath, fname))

    if not test_files:
        return []

    # Load cache and extract fingerprints
    cache = _load_cache(project_root)
    source_tokens = _get_or_extract_fingerprint(full_source, cache)
    if not source_tokens:
        return []

    corpus_tokens: list[list[str]] = []
    for tf in test_files:
        corpus_tokens.append(_get_or_extract_fingerprint(tf, cache))

    # Save updated cache
    _save_cache(project_root, cache)

    # Compute similarities
    scores = _tfidf_cosine(source_tokens, corpus_tokens)

    # Filter and sort
    results: list[tuple[str, float]] = []
    for tf, score in zip(test_files, scores, strict=False):
        if score >= threshold:
            results.append((tf, round(score, 4)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:max_results]
