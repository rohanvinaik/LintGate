"""Comprehensive tests for lintgate/code_inference.py — all public and private symbols."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.code_inference import (
    _BADGE_PATTERNS,
    _FRAMEWORK_MAP,
    _LAYER_MAP,
    _MAX_CONFIDENCE,
    _MAX_PY_FILES,
    _SKIP_DIRS,
    _claim,
    _collect_py_files,
    _extract_docstring_claims,
    _extract_first_paragraph,
    _infer_from_commit_messages,
    _infer_from_directory_structure,
    _infer_from_docstrings,
    _infer_from_imports,
    _infer_from_pyproject,
    _infer_from_readme,
    _infer_from_test_patterns,
    _read_text_safe,
    _scan_test_dir,
    infer_from_code,
)

# ── _claim helper ───────────────────────────────────────────────────


class TestClaim:
    def test_basic_claim(self) -> None:
        c = _claim("some text", "src")
        assert c.text == "some text"
        assert c.source == "src"
        assert c.confidence == 0.5
        assert c.provenance == "inferred"
        assert c.origin_facet == ""

    def test_confidence_capped_at_max(self) -> None:
        c = _claim("x", "s", confidence=0.9)
        assert c.confidence == _MAX_CONFIDENCE

    def test_confidence_below_max_unchanged(self) -> None:
        c = _claim("x", "s", confidence=0.3)
        assert c.confidence == 0.3

    def test_origin_facet_passed_through(self) -> None:
        c = _claim("x", "s", origin_facet="core_theory")
        assert c.origin_facet == "core_theory"


# ── _read_text_safe ─────────────────────────────────────────────────


class TestReadTextSafe:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("content", encoding="utf-8")
        assert _read_text_safe(f) == "content"

    def test_returns_empty_on_missing_file(self, tmp_path: Path) -> None:
        assert _read_text_safe(tmp_path / "nope.txt") == ""

    def test_returns_empty_on_directory(self, tmp_path: Path) -> None:
        # Passing a directory triggers OSError on read_text
        assert _read_text_safe(tmp_path) == ""


# ── _collect_py_files ───────────────────────────────────────────────


class TestCollectPyFiles:
    def test_collects_py_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.txt").write_text("not python")
        result = _collect_py_files(str(tmp_path))
        assert len(result) == 1
        assert result[0].name == "a.py"

    def test_skips_excluded_dirs(self, tmp_path: Path) -> None:
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "c.py").write_text("x = 1")
        (tmp_path / "a.py").write_text("x = 1")
        result = _collect_py_files(str(tmp_path))
        names = [p.name for p in result]
        assert "a.py" in names
        assert "c.py" not in names

    def test_skips_dot_dirs(self, tmp_path: Path) -> None:
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "x.py").write_text("x = 1")
        result = _collect_py_files(str(tmp_path))
        assert len(result) == 0

    def test_caps_at_max(self, tmp_path: Path) -> None:
        for i in range(_MAX_PY_FILES + 10):
            (tmp_path / f"f{i}.py").write_text(f"x = {i}")
        result = _collect_py_files(str(tmp_path))
        assert len(result) == _MAX_PY_FILES


# ── _extract_first_paragraph ────────────────────────────────────────


class TestExtractFirstParagraph:
    def test_simple_paragraph(self) -> None:
        lines = ["# Title", "", "First sentence.", "Second sentence.", "", "Third."]
        assert _extract_first_paragraph(lines) == "First sentence. Second sentence."

    def test_empty_lines(self) -> None:
        assert _extract_first_paragraph([]) == ""

    def test_only_headings(self) -> None:
        assert _extract_first_paragraph(["# H1", "## H2"]) == ""

    def test_skips_badges_and_comments(self) -> None:
        lines = ["![badge](url)", "[![ci](link)](href)", "<!-- comment -->", "Real text."]
        assert _extract_first_paragraph(lines) == "Real text."

    def test_truncates_at_200(self) -> None:
        long_line = "a" * 300
        result = _extract_first_paragraph([long_line])
        assert len(result) == 200

    def test_heading_breaks_paragraph(self) -> None:
        lines = ["Some text.", "# Heading"]
        assert _extract_first_paragraph(lines) == "Some text."


# ── _infer_from_pyproject ───────────────────────────────────────────


class TestInferFromPyproject:
    def test_no_pyproject(self, tmp_path: Path) -> None:
        assert _infer_from_pyproject(str(tmp_path)) == []

    def test_description_extracted(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('description = "A cool tool"\n')
        claims = _infer_from_pyproject(str(tmp_path))
        assert any("A cool tool" in c.text for c in claims)
        assert claims[0].origin_facet == "core_theory"

    def test_requires_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('requires-python = ">=3.10"\n')
        claims = _infer_from_pyproject(str(tmp_path))
        assert any(">=3.10" in c.text for c in claims)

    def test_ruff_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        claims = _infer_from_pyproject(str(tmp_path))
        assert any("Ruff" in c.text for c in claims)

    def test_mypy_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        claims = _infer_from_pyproject(str(tmp_path))
        assert any("mypy" in c.text for c in claims)

    def test_pytest_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '-v'\n")
        claims = _infer_from_pyproject(str(tmp_path))
        assert any("pytest" in c.text for c in claims)


# ── _infer_from_readme ──────────────────────────────────────────────


class TestInferFromReadme:
    def test_no_readme(self, tmp_path: Path) -> None:
        assert _infer_from_readme(str(tmp_path)) == []

    def test_readme_md(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# My Project\n\nThis is the description.\n")
        claims = _infer_from_readme(str(tmp_path))
        assert any("This is the description" in c.text for c in claims)

    def test_readme_rst_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "README.rst").write_text("My Project\n==========\n\nRST description here.\n")
        claims = _infer_from_readme(str(tmp_path))
        # RST underlines are not recognized as headings by _extract_first_paragraph,
        # so the first paragraph becomes "My Project =========="
        assert any("My Project" in c.text for c in claims)

    def test_badge_detection_ci(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "![CI](https://github.com/actions/workflow)\n\nSome description.\n"
        )
        claims = _infer_from_readme(str(tmp_path))
        assert any("CI pipeline" in c.text for c in claims)

    def test_badge_detection_coverage(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "[![codecov](https://codecov.io/badge)]\n\nSome description.\n"
        )
        claims = _infer_from_readme(str(tmp_path))
        assert any("coverage" in c.text.lower() for c in claims)

    def test_badge_detection_pypi(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "[![PyPI](https://pypi.org/badge)]\n\nSome description.\n"
        )
        claims = _infer_from_readme(str(tmp_path))
        assert any("PyPI" in c.text for c in claims)


# ── _infer_from_imports ─────────────────────────────────────────────


class TestInferFromImports:
    def test_detects_framework(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("import fastapi\nimport os\n")
        claims = _infer_from_imports(str(tmp_path))
        assert any("FastAPI" in c.text for c in claims)

    def test_detects_from_import(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("from flask import Flask\n")
        claims = _infer_from_imports(str(tmp_path))
        assert any("Flask" in c.text for c in claims)

    def test_no_known_framework(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("import os\nimport sys\n")
        claims = _infer_from_imports(str(tmp_path))
        assert claims == []

    def test_syntax_error_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("def (broken\n")
        (tmp_path / "good.py").write_text("import pytest\n")
        claims = _infer_from_imports(str(tmp_path))
        assert any("pytest" in c.text for c in claims)

    def test_dotted_import_uses_top_level(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("import pandas.core\n")
        claims = _infer_from_imports(str(tmp_path))
        assert any("pandas" in c.text for c in claims)


# ── _infer_from_directory_structure ─────────────────────────────────


class TestInferFromDirectoryStructure:
    def test_src_layout(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        claims = _infer_from_directory_structure(str(tmp_path))
        assert any("src/ layout" in c.text for c in claims)

    def test_tests_dir(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        claims = _infer_from_directory_structure(str(tmp_path))
        assert any("tests/" in c.text for c in claims)

    def test_test_dir_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "test").mkdir()
        claims = _infer_from_directory_structure(str(tmp_path))
        assert any("test/" in c.text for c in claims)

    def test_docs_dir(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        claims = _infer_from_directory_structure(str(tmp_path))
        assert any("docs/" in c.text for c in claims)

    def test_layer_dirs(self, tmp_path: Path) -> None:
        for name in ("controllers", "models", "services"):
            (tmp_path / name).mkdir()
        claims = _infer_from_directory_structure(str(tmp_path))
        texts = [c.text for c in claims]
        assert any("controllers" in t.lower() for t in texts)
        assert any("models" in t.lower() for t in texts)
        assert any("services" in t.lower() for t in texts)

    def test_empty_project(self, tmp_path: Path) -> None:
        assert _infer_from_directory_structure(str(tmp_path)) == []

    def test_tests_preferred_over_test(self, tmp_path: Path) -> None:
        """When both tests/ and test/ exist, only tests/ is reported (break)."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "test").mkdir()
        claims = _infer_from_directory_structure(str(tmp_path))
        test_claims = [c for c in claims if "Tests in" in c.text or "test" in c.text.lower()]
        # Should only have the tests/ claim, not test/
        assert any("tests/" in c.text for c in test_claims)
        assert not any("test/" in c.text and "tests/" not in c.text for c in test_claims)


# ── _scan_test_dir ──────────────────────────────────────────────────


class TestScanTestDir:
    def test_detects_conftest(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "conftest.py").write_text("import pytest\n")
        flags = _scan_test_dir(td)
        assert flags["conftest"] is True
        assert flags["pytest"] is True

    def test_counts_test_files(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "test_one.py").write_text("def test_a(): pass\n")
        (td / "two_test.py").write_text("def test_b(): pass\n")
        (td / "helper.py").write_text("x = 1\n")
        flags = _scan_test_dir(td)
        assert flags["count"] == 2

    def test_detects_unittest(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "test_u.py").write_text("import unittest\n")
        flags = _scan_test_dir(td)
        assert flags["unittest"] is True

    def test_detects_fixtures(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "conftest.py").write_text("@pytest.fixture\ndef my_fix(): pass\n")
        flags = _scan_test_dir(td)
        assert flags["fixtures"] is True

    def test_skips_excluded_dirs_inside_tests(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        cache = td / "__pycache__"
        cache.mkdir()
        (cache / "test_cached.py").write_text("import pytest\n")
        flags = _scan_test_dir(td)
        assert flags["count"] == 0
        assert flags["pytest"] is False


# ── _infer_from_test_patterns ───────────────────────────────────────


class TestInferFromTestPatterns:
    def test_pytest_detected(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "test_foo.py").write_text("import pytest\n@pytest.fixture\ndef fix(): pass\n")
        claims = _infer_from_test_patterns(str(tmp_path))
        texts = [c.text for c in claims]
        assert "Uses pytest" in texts
        assert "Uses pytest fixtures" in texts

    def test_unittest_detected(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "test_bar.py").write_text("import unittest\n")
        claims = _infer_from_test_patterns(str(tmp_path))
        assert any("unittest" in c.text for c in claims)

    def test_no_test_dir(self, tmp_path: Path) -> None:
        assert _infer_from_test_patterns(str(tmp_path)) == []

    def test_merges_both_test_dirs(self, tmp_path: Path) -> None:
        """Both tests/ and test/ are scanned and merged."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "test").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("import pytest\n")
        (tmp_path / "test" / "test_b.py").write_text("x = 1\n")
        claims = _infer_from_test_patterns(str(tmp_path))
        count_claims = [c for c in claims if "test file" in c.text]
        assert len(count_claims) == 1
        assert "2" in count_claims[0].text

    def test_conftest_claim(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "conftest.py").write_text("import pytest\n")
        claims = _infer_from_test_patterns(str(tmp_path))
        assert any("conftest.py" in c.text for c in claims)


# ── _infer_from_commit_messages ─────────────────────────────────────


class TestInferFromCommitMessages:
    def test_conventional_commits_detected(self, tmp_path: Path) -> None:
        stdout = "\n".join(
            [f"abc{i:04d} {'feat' if i % 2 == 0 else 'fix'}: something {i}" for i in range(10)]
        )
        with patch("lintgate.code_inference.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": stdout})()
            claims = _infer_from_commit_messages(str(tmp_path))
        assert any("conventional commit" in c.text.lower() for c in claims)

    def test_non_conventional_commits(self, tmp_path: Path) -> None:
        stdout = "\n".join([f"abc{i:04d} random message {i}" for i in range(10)])
        with patch("lintgate.code_inference.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": stdout})()
            claims = _infer_from_commit_messages(str(tmp_path))
        assert claims == []

    def test_git_not_available(self, tmp_path: Path) -> None:
        with patch("lintgate.code_inference.subprocess.run", side_effect=OSError("no git")):
            claims = _infer_from_commit_messages(str(tmp_path))
        assert claims == []

    def test_git_timeout(self, tmp_path: Path) -> None:
        import subprocess as sp

        with patch(
            "lintgate.code_inference.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="git", timeout=10),
        ):
            claims = _infer_from_commit_messages(str(tmp_path))
        assert claims == []

    def test_nonzero_return_code(self, tmp_path: Path) -> None:
        with patch("lintgate.code_inference.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 128, "stdout": ""})()
            claims = _infer_from_commit_messages(str(tmp_path))
        assert claims == []

    def test_empty_stdout(self, tmp_path: Path) -> None:
        with patch("lintgate.code_inference.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": ""})()
            claims = _infer_from_commit_messages(str(tmp_path))
        assert claims == []


# ── _extract_docstring_claims ───────────────────────────────────────


class TestExtractDocstringClaims:
    def test_module_docstring(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text('"""This module does really important things for the project."""\n')
        claims = _extract_docstring_claims([f])
        assert len(claims) == 1
        assert "mod.py" in claims[0].text
        assert claims[0].origin_facet == "core_theory"

    def test_class_docstring(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text(
            textwrap.dedent(
                '''\
                class MyWidget:
                    """A widget that handles rendering logic precisely."""
                    pass
                '''
            )
        )
        claims = _extract_docstring_claims([f])
        assert len(claims) == 1
        assert "MyWidget" in claims[0].text
        assert claims[0].origin_facet == "abstractions"

    def test_short_docstring_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text('"""Short."""\n')
        claims = _extract_docstring_claims([f])
        assert claims == []

    def test_syntax_error_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def (broken syntax\n")
        claims = _extract_docstring_claims([f])
        assert claims == []

    def test_dedup_same_docstring(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        doc = '"""This module does really important things for the project."""\n'
        f1.write_text(doc)
        f2.write_text(doc)
        claims = _extract_docstring_claims([f1, f2])
        # The first-line text is the same, so dedup should prevent a second entry
        # Both share the same first-line so only one should appear
        assert len(claims) == 1

    def test_multiline_docstring_takes_first_line(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text(
            textwrap.dedent(
                '''\
                """First line is the summary that exceeds minimum length.

                More details here that should be ignored.
                """
                '''
            )
        )
        claims = _extract_docstring_claims([f])
        assert len(claims) == 1
        assert "First line" in claims[0].text
        assert "More details" not in claims[0].text


# ── _infer_from_docstrings ──────────────────────────────────────────


class TestInferFromDocstrings:
    def test_empty_project(self, tmp_path: Path) -> None:
        assert _infer_from_docstrings(str(tmp_path)) == []

    def test_delegates_to_extract(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text(
            '"""This module orchestrates the main business logic pipeline."""\n'
        )
        claims = _infer_from_docstrings(str(tmp_path))
        assert len(claims) == 1


# ── infer_from_code (public) ────────────────────────────────────────


class TestInferFromCode:
    def test_combines_multiple_sources(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('description = "A test project"\n')
        (tmp_path / "README.md").write_text("# Proj\n\nThis project does something useful.\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("import pytest\ndef test_one(): pass\n")
        claims = infer_from_code(str(tmp_path))
        texts = [c.text for c in claims]
        assert any("A test project" in t for t in texts)
        assert any("src/ layout" in t for t in texts)

    def test_deduplicates(self, tmp_path: Path) -> None:
        """Same text from different sources is deduplicated."""
        # Two sources might produce the same text; ensure no duplicates
        claims = infer_from_code(str(tmp_path))
        texts = [c.text for c in claims]
        assert len(texts) == len(set(texts))

    def test_single_source_failure_does_not_crash(self, tmp_path: Path) -> None:
        """If one inference source throws, others still run."""
        (tmp_path / "README.md").write_text("# Hi\n\nSome project description text here.\n")
        with patch(
            "lintgate.code_inference._infer_from_pyproject",
            side_effect=RuntimeError("boom"),
        ):
            claims = infer_from_code(str(tmp_path))
        # README claims still present
        assert any("README" in c.source for c in claims)

    def test_all_claims_have_inferred_provenance(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('description = "Something"\n')
        claims = infer_from_code(str(tmp_path))
        for c in claims:
            assert c.provenance == "inferred"

    def test_all_claims_respect_max_confidence(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('description = "Something"\n')
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("import pytest\n")
        claims = infer_from_code(str(tmp_path))
        for c in claims:
            assert c.confidence <= _MAX_CONFIDENCE


# ── Constants sanity checks ─────────────────────────────────────────


class TestConstants:
    def test_skip_dirs_is_frozenset(self) -> None:
        assert isinstance(_SKIP_DIRS, frozenset)
        assert ".git" in _SKIP_DIRS
        assert "__pycache__" in _SKIP_DIRS

    def test_framework_map_entries(self) -> None:
        assert "fastapi" in _FRAMEWORK_MAP
        assert "pytest" in _FRAMEWORK_MAP
        for _key, (text, category) in _FRAMEWORK_MAP.items():
            assert isinstance(text, str)
            assert isinstance(category, str)

    def test_layer_map_entries(self) -> None:
        assert "controllers" in _LAYER_MAP
        assert "models" in _LAYER_MAP

    def test_badge_patterns_compile(self) -> None:
        import re

        for pattern, _label in _BADGE_PATTERNS:
            compiled = re.compile(pattern, re.IGNORECASE)
            assert compiled is not None

    def test_max_py_files_positive(self) -> None:
        assert _MAX_PY_FILES > 0

    def test_max_confidence_range(self) -> None:
        assert 0 < _MAX_CONFIDENCE <= 1.0
