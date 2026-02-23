"""Tests for mutation-aware skeleton generator enhancements."""

from __future__ import annotations

import os
import tempfile

from lintgate.controlplane.skeleton_generator import generate_test_skeleton


def test_skeleton_uses_equality_not_is_not_none():
    """Generated skeletons use assert == EXPECTED instead of assert is not None."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def compute(x):\n    return x + 1\n")
        f.flush()
        source_file = f.name

    try:
        skeleton = generate_test_skeleton(source_file)
        assert "assert result == EXPECTED" in skeleton
        assert "assert result is not None" not in skeleton
    finally:
        os.unlink(source_file)


def test_skeleton_generates_boundary_tests():
    """Generated skeletons include boundary test stubs for functions with args."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def process(data, count):\n    return data[:count]\n")
        f.flush()
        source_file = f.name

    try:
        skeleton = generate_test_skeleton(source_file)
        assert "boundary" in skeleton.lower()
        assert "TODO" in skeleton
    finally:
        os.unlink(source_file)


def test_skeleton_class_uses_equality():
    """Generated class test skeletons use equality assertions."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Config:\n"
            "    name: str = 'default'\n"
            "    count: int = 0\n"
        )
        f.flush()
        source_file = f.name

    try:
        skeleton = generate_test_skeleton(source_file)
        # Should not contain the old weak pattern
        assert "assert obj is not None" not in skeleton
    finally:
        os.unlink(source_file)
