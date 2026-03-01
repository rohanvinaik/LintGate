"""Tests for test_effectiveness channel sampling strategy (#203)."""

from __future__ import annotations

import os
import tempfile

from lintgate.channels.test_effectiveness_channel import _select_test_files_for_analysis

# ── _select_test_files_for_analysis ──────────────────────────────────


class TestSelectTestFilesForAnalysis:
    def test_small_codebase_not_sampled(self):
        files = [f"/project/tests/test_{i}.py" for i in range(10)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=90.0
        )
        assert not was_sampled
        assert selected == files

    def test_large_codebase_sampled(self):
        files = [f"/project/tests/test_{i}.py" for i in range(500)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=90.0, estimated_per_file_seconds=0.3
        )
        assert was_sampled
        assert len(selected) <= 300  # 90 / 0.3 = 300
        assert len(selected) < len(files)

    def test_budget_controls_sample_size(self):
        files = [f"/project/tests/test_{i}.py" for i in range(100)]
        # Small budget → fewer files
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=10.0, estimated_per_file_seconds=0.3
        )
        assert was_sampled
        assert len(selected) <= 33  # 10 / 0.3 ≈ 33

    def test_empty_list(self):
        selected, was_sampled = _select_test_files_for_analysis(
            [], "/project", budget_seconds=90.0
        )
        assert not was_sampled
        assert selected == []

    def test_exact_threshold_not_sampled(self):
        max_files = int(90.0 / 0.3)  # 300
        files = [f"/project/tests/test_{i}.py" for i in range(max_files)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=90.0, estimated_per_file_seconds=0.3
        )
        assert not was_sampled
        assert len(selected) == max_files

    def test_prioritizes_largest_files(self):
        """Largest files should be included in the sample."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with varying sizes
            files = []
            for i in range(20):
                path = os.path.join(tmpdir, f"test_{i}.py")
                with open(path, "w") as f:
                    # File i gets i*100 bytes of content
                    f.write("x" * (i * 100))
                files.append(path)

            selected, was_sampled = _select_test_files_for_analysis(
                files, tmpdir, budget_seconds=3.0, estimated_per_file_seconds=0.3
            )
            assert was_sampled
            assert len(selected) <= 10  # 3.0 / 0.3 = 10

            # The largest file (test_19.py, 1900 bytes) should be included
            largest = os.path.join(tmpdir, "test_19.py")
            assert largest in selected

    def test_no_duplicates_in_sample(self):
        files = [f"/project/tests/test_{i}.py" for i in range(500)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=30.0, estimated_per_file_seconds=0.3
        )
        assert was_sampled
        assert len(selected) == len(set(selected))
