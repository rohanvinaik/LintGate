"""Tests for Phase 1 mutation stats parser."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lintgate.mutation.ci_stats import (
    MutationCIStats,
    compute_badge_color,
    load_mutation_hotspots,
    parse_stats_for_ci,
)


def test_compute_badge_color():
    """Verify badge color thresholds."""
    assert compute_badge_color(100.0) == "brightgreen"
    assert compute_badge_color(80.0) == "brightgreen"
    assert compute_badge_color(79.9) == "yellow"
    assert compute_badge_color(60.0) == "yellow"
    assert compute_badge_color(59.9) == "red"
    assert compute_badge_color(0.0) == "red"


def test_mutation_ci_stats_from_json_path_missing():
    """Missing file yields run_state='missing'."""
    stats = MutationCIStats.from_json_path("/does/not/exist.json")
    assert stats.run_state == "missing"
    assert stats.source == "missing"
    assert not stats.is_valid()


def test_mutation_ci_stats_from_json_path_invalid_json():
    """Unparseable file yields run_state='invalid'."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("{bad json")
        path = f.name

    try:
        stats = MutationCIStats.from_json_path(path)
        assert stats.run_state == "invalid"
        assert not stats.is_valid()
    finally:
        os.unlink(path)


def test_mutation_ci_stats_from_json_path_zero_total():
    """Zero total mutants yields run_state='invalid'."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        json.dump({"total": 0}, f)
        path = f.name

    try:
        stats = MutationCIStats.from_json_path(path)
        assert stats.run_state == "invalid"
        assert stats.total == 0
        assert not stats.is_valid()
    finally:
        os.unlink(path)


def test_mutation_ci_stats_from_json_path_valid():
    """Correctly parses a valid mutmut stats file with total override."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        # Note: Sum is 1+2+3+4+5+6 = 21, but total=100 takes precedence
        json.dump({
            "killed": 1,
            "survived": 2,
            "timeout": 3,
            "suspicious": 4,
            "no_tests": 5,
            "skipped": 6,
            "equivalent_suspect": 7,
            "skipped_equivalent_policy": 8,
            "total": 100
        }, f)
        path = f.name

    try:
        stats = MutationCIStats.from_json_path(path, source="ci_artifact")
        assert stats.run_state == "valid"
        assert stats.source == "ci_artifact"
        assert stats.killed == 1
        assert stats.survived == 2
        assert stats.timeout == 3
        assert stats.suspicious == 4
        assert stats.no_tests == 5
        assert stats.skipped == 6
        assert stats.total == 100
        assert stats.equivalent_suspect == 7
        assert stats.skipped_equivalent_policy == 8
        assert stats.effective_total_for_score == 85
        assert stats.score == round(1 / 85 * 100, 1)
        assert stats.is_valid()
    finally:
        os.unlink(path)


def test_mutation_ci_stats_from_json_path_computed_total():
    """Computes total when mutmut omits it."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        json.dump({
            "killed": 10,
            "survived": 10,
            "no_tests": 5,
        }, f)
        path = f.name

    try:
        stats = MutationCIStats.from_json_path(path)
        assert stats.total == 25  # 10 + 10 + 5
        assert stats.score == 40.0  # 10 / 25
        assert stats.is_valid()
    finally:
        os.unlink(path)


def test_parse_stats_for_ci_missing(capsys):
    """Missing stats file skips badge but doesn't fail the workflow."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        github_output = f.name

    try:
        rc = parse_stats_for_ci("/does/not/exist.json", github_output)
        assert rc == 1

        out = capsys.readouterr().out
        assert "No mutmut stats found — failing workflow" in out

        output_content = Path(github_output).read_text()
        assert "skip=true" in output_content
        assert "mutation_integrity=fail" in output_content
    finally:
        os.unlink(github_output)


def test_parse_stats_for_ci_invalid(capsys):
    """Invalid stats (e.g. 0 total) fails the workflow."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        json.dump({"total": 0}, f)
        stats_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        github_output = f.name

    try:
        rc = parse_stats_for_ci(stats_path, github_output)
        assert rc == 1

        out = capsys.readouterr().out
        assert "::error::Mutation stats invalid" in out

        output_content = Path(github_output).read_text()
        assert "skip=true" in output_content
        assert "mutation_integrity=fail" in output_content
    finally:
        os.unlink(stats_path)
        os.unlink(github_output)


def test_parse_stats_for_ci_valid(capsys):
    """Valid stats configures badge and passes workflow."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        json.dump({
            "killed": 80,
            "survived": 20,
        }, f)
        stats_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        github_output = f.name

    try:
        rc = parse_stats_for_ci(stats_path, github_output)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Mutation score: 80.0%" in out

        output_content = sorted(Path(github_output).read_text().splitlines())
        assert "color=brightgreen" in output_content
        assert "killed=80" in output_content
        assert "mutation_integrity=pass" in output_content
        assert "mutation_quality=80.0" in output_content
        assert "score=80.0" in output_content
        assert "skip=false" in output_content
        assert "survived=20" in output_content
        assert "total=100" in output_content
    finally:
        os.unlink(stats_path)
        os.unlink(github_output)


def test_load_mutation_hotspots_missing():
    """Missing survivor file returns empty list."""
    assert load_mutation_hotspots("/does/not/exist.json") == []


def test_load_mutation_hotspots_json():
    """Parses exported survivor JSON."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        json.dump([
            {"file": "foo.py", "line": 42},
            {"filename": "bar.py", "lineno": 10, "func": "hello", "mutation_type": "NumberReplacer"}
        ], f)
        path = f.name

    try:
        hotspots = load_mutation_hotspots(path)
        assert len(hotspots) == 2
        assert hotspots[0]["file"] == "foo.py"
        assert hotspots[0]["line"] == 42
        assert hotspots[1]["file"] == "bar.py"
        assert hotspots[1]["line"] == 10
        assert hotspots[1]["function"] == "hello"
        assert hotspots[1]["operator"] == "NumberReplacer"

        # Schema defaults
        for h in hotspots:
            assert h["run_id"] == "default"
            assert h["status"] == "survived"
            assert h["category"] == "unknown"
            assert isinstance(h["test_ids"], list)
            assert h["confidence"] == "low"
            assert "mutation_id" in h
    finally:
        os.unlink(path)


def test_load_mutation_hotspots_text(tmp_path):
    """Parses raw `mutmut results` text line format and enriches with AST."""
    src = tmp_path / "src"
    src.mkdir()

    main_py = src / "main.py"
    main_py.write_text("def do_main():\n    pass\n")

    utils_py = src / "utils.py"
    utils_py.write_text("def utils_func():\n    pass\n")

    with tempfile.NamedTemporaryFile(mode="w", delete=False, dir=str(tmp_path)) as f:
        f.write("# Suspicious\n\n123  src/main.py:1  suspicious\n\n# Survived\n\n456  src/utils.py:1  survived\n789  bad_format\n")
        path = f.name

    try:
        hotspots = load_mutation_hotspots(path)
        assert len(hotspots) == 3

        assert hotspots[0]["file"] == "src/main.py"
        assert hotspots[0]["line"] == 1
        assert hotspots[0]["function"] == "do_main"
        assert hotspots[0]["mutation_id"] == "123"
        assert hotspots[0]["status"] == "suspicious"

        assert hotspots[1]["file"] == "src/utils.py"
        assert hotspots[1]["line"] == 1
        assert hotspots[1]["function"] == "utils_func"
        assert hotspots[1]["mutation_id"] == "456"
        assert hotspots[1]["status"] == "survived"

        assert hotspots[2]["file"] == "bad_format"
        assert hotspots[2]["line"] == 0
        assert hotspots[2]["mutation_id"] == "789"
        assert hotspots[2]["status"] == "survived"

        # Schema defaults
        for h in hotspots:
            assert h["run_id"] == "default"
            assert h["category"] == "unknown"
            assert isinstance(h["test_ids"], list)
            assert h["confidence"] == "low"
    finally:
        os.unlink(path)
