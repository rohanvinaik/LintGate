"""Tests for code_inference — claim derivation from code artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.code_inference import (
    _FRAMEWORK_MAP,
    _MAX_CONFIDENCE,
    _SKIP_DIRS,
    _claim,
    _collect_py_files,
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

# ── _infer_from_pyproject ─────────────────────────────────────────────


def test_infer_from_pyproject_extracts_claims(tmp_path: Path) -> None:
    """Minimal pyproject.toml with description, requires-python, [tool.ruff]."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "demo"\n'
        'description = "A demo project"\n'
        'requires-python = ">=3.10"\n'
        "\n"
        "[tool.ruff]\n"
        "line-length = 100\n"
    )

    claims = _infer_from_pyproject(str(tmp_path))

    texts = [c.text for c in claims]
    assert any("A demo project" in t for t in texts)
    assert any(">=3.10" in t for t in texts)
    assert any("Ruff" in t for t in texts)
    assert len(claims) == 3


def test_infer_from_pyproject_missing_file(tmp_path: Path) -> None:
    """No pyproject.toml returns empty list."""
    assert _infer_from_pyproject(str(tmp_path)) == []


def test_infer_from_pyproject_mypy_and_pytest(tmp_path: Path) -> None:
    """Detects [tool.mypy] and [tool.pytest.*] sections."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.mypy]\nstrict = true\n\n[tool.pytest.ini_options]\naddopts = "-v"\n'
    )

    claims = _infer_from_pyproject(str(tmp_path))
    texts = [c.text for c in claims]
    assert any("mypy" in t for t in texts)
    assert any("pytest" in t for t in texts)


# ── _infer_from_readme ────────────────────────────────────────────────


def test_infer_from_readme_extracts_paragraph(tmp_path: Path) -> None:
    """README.md with title and paragraph produces a claim."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "# My Project\n\nThis project does something useful for developers.\n"
    )

    claims = _infer_from_readme(str(tmp_path))

    assert len(claims) >= 1
    assert any("something useful" in c.text for c in claims)
    assert claims[0].source == "README.md"


def test_infer_from_readme_missing_file(tmp_path: Path) -> None:
    """No README returns empty list."""
    assert _infer_from_readme(str(tmp_path)) == []


def test_infer_from_readme_badges(tmp_path: Path) -> None:
    """README with CI badge pattern produces badge claim."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Proj\n"
        "\n"
        "[![CI](https://github.com/org/repo/actions/workflows/ci.yml/badge.svg)](link)\n"
        "\n"
        "A library for things.\n"
    )

    claims = _infer_from_readme(str(tmp_path))
    texts = [c.text for c in claims]
    assert any("CI pipeline" in t for t in texts)


# ── _infer_from_imports ───────────────────────────────────────────────


def test_infer_from_imports_detects_fastapi(tmp_path: Path) -> None:
    """.py file with `import fastapi` detects FastAPI."""
    py_file = tmp_path / "app.py"
    py_file.write_text("import fastapi\nfrom fastapi import FastAPI\n")

    claims = _infer_from_imports(str(tmp_path))

    assert len(claims) == 1
    assert "FastAPI" in claims[0].text
    assert claims[0].origin_facet == "architecture"


def test_infer_from_imports_no_py_files(tmp_path: Path) -> None:
    """No .py files returns empty list."""
    assert _infer_from_imports(str(tmp_path)) == []


def test_infer_from_imports_multiple_frameworks(tmp_path: Path) -> None:
    """Multiple known imports produce multiple claims."""
    py_file = tmp_path / "main.py"
    py_file.write_text("import fastapi\nimport pydantic\nimport redis\n")

    claims = _infer_from_imports(str(tmp_path))
    texts = [c.text for c in claims]
    assert len(claims) == 3
    assert any("FastAPI" in t for t in texts)
    assert any("Pydantic" in t for t in texts)
    assert any("Redis" in t for t in texts)


def test_infer_from_imports_skips_unknown(tmp_path: Path) -> None:
    """Unknown imports are ignored."""
    py_file = tmp_path / "lib.py"
    py_file.write_text("import some_obscure_lib\n")

    assert _infer_from_imports(str(tmp_path)) == []


# ── _infer_from_directory_structure ───────────────────────────────────


def test_infer_from_directory_structure_src_and_tests(tmp_path: Path) -> None:
    """src/ layout and tests/ dir produce claims."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    claims = _infer_from_directory_structure(str(tmp_path))

    texts = [c.text for c in claims]
    assert any("src/ layout" in t for t in texts)
    assert any("tests/" in t for t in texts)


def test_infer_from_directory_structure_docs(tmp_path: Path) -> None:
    """docs/ directory produces a claim."""
    (tmp_path / "docs").mkdir()

    claims = _infer_from_directory_structure(str(tmp_path))
    assert any("docs/" in c.text for c in claims)


def test_infer_from_directory_structure_layer_dirs(tmp_path: Path) -> None:
    """Known layer directories (models, services) produce architecture claims."""
    (tmp_path / "models").mkdir()
    (tmp_path / "services").mkdir()

    claims = _infer_from_directory_structure(str(tmp_path))
    texts = [c.text for c in claims]
    assert any("Data models" in t for t in texts)
    assert any("Business logic" in t for t in texts)


def test_infer_from_directory_structure_empty(tmp_path: Path) -> None:
    """Empty directory returns empty list."""
    assert _infer_from_directory_structure(str(tmp_path)) == []


# ── _infer_from_test_patterns ─────────────────────────────────────────


def test_infer_from_test_patterns_detects_pytest(tmp_path: Path) -> None:
    """conftest.py + test files detect pytest."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef client():\n    return 'c'\n"
    )
    (tests_dir / "test_app.py").write_text(
        "import pytest\n\ndef test_hello():\n    assert True\n"
    )

    claims = _infer_from_test_patterns(str(tmp_path))

    texts = [c.text for c in claims]
    assert any(t == "Uses pytest" for t in texts)
    assert any("conftest" in t for t in texts)
    assert any("fixtures" in t for t in texts)
    assert any("1 test file" in t for t in texts)


def test_infer_from_test_patterns_no_tests(tmp_path: Path) -> None:
    """No test directory returns empty list."""
    assert _infer_from_test_patterns(str(tmp_path)) == []


def test_infer_from_test_patterns_unittest(tmp_path: Path) -> None:
    """Detects unittest when used instead of pytest."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_basic.py").write_text(
        "import unittest\n\nclass TestBasic(unittest.TestCase):\n"
        "    def test_one(self):\n        self.assertTrue(True)\n"
    )

    claims = _infer_from_test_patterns(str(tmp_path))
    texts = [c.text for c in claims]
    assert any("unittest" in t for t in texts)
    assert not any(t == "pytest" for t in texts)


# ── _infer_from_docstrings ────────────────────────────────────────────


def test_infer_from_docstrings_module_docstring(tmp_path: Path) -> None:
    """.py file with module docstring extracts claim."""
    py_file = tmp_path / "engine.py"
    py_file.write_text(
        '"""Core execution engine for pipeline orchestration."""\n\ndef run():\n    pass\n'
    )

    claims = _infer_from_docstrings(str(tmp_path))

    assert len(claims) == 1
    assert "engine.py" in claims[0].text
    assert "execution engine" in claims[0].text
    assert claims[0].origin_facet == "core_theory"


def test_infer_from_docstrings_short_docstring_ignored(tmp_path: Path) -> None:
    """Docstrings <= 15 chars are filtered out."""
    py_file = tmp_path / "tiny.py"
    py_file.write_text('"""Short doc."""\n')

    claims = _infer_from_docstrings(str(tmp_path))
    assert claims == []


def test_infer_from_docstrings_class_docstring(tmp_path: Path) -> None:
    """Class-level docstrings produce claims with abstractions facet."""
    py_file = tmp_path / "models.py"
    py_file.write_text(
        "class DataProcessor:\n"
        '    """Transforms raw input data into normalized output format."""\n'
        "    pass\n"
    )

    claims = _infer_from_docstrings(str(tmp_path))

    assert len(claims) == 1
    assert "DataProcessor" in claims[0].text
    assert claims[0].origin_facet == "abstractions"


# ── infer_from_code (integration) ─────────────────────────────────────


def test_infer_from_code_integration(tmp_path: Path) -> None:
    """Integration: tmp_path project with pyproject + README + .py files."""
    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "testproj"\n'
        'description = "Integration test project"\n'
        'requires-python = ">=3.11"\n'
        "\n"
        "[tool.ruff]\n"
        "line-length = 88\n"
    )

    # README.md
    (tmp_path / "README.md").write_text(
        "# TestProj\n\nA project for integration testing of code inference.\n"
    )

    # src layout with a .py file
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        '"""Application entry point for the test project."""\n'
        "import fastapi\n"
        "\napp = fastapi.FastAPI()\n"
    )

    # tests directory
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef app():\n    return None\n"
    )
    (tests / "test_main.py").write_text("def test_placeholder():\n    assert True\n")

    claims = infer_from_code(str(tmp_path))

    assert len(claims) > 0
    texts = [c.text for c in claims]
    # pyproject claims
    assert any("Integration test project" in t for t in texts)
    assert any(">=3.11" in t for t in texts)
    assert any("Ruff" in t for t in texts)
    # README claim
    assert any("integration testing" in t for t in texts)
    # import claim
    assert any("FastAPI" in t for t in texts)
    # directory structure claims
    assert any("src/ layout" in t for t in texts)
    assert any("tests/" in t for t in texts)
    # test pattern claims
    assert any("pytest" in t.lower() for t in texts)

    # No duplicate claim texts
    assert len(texts) == len(set(texts))


# ── Provenance and confidence invariants ──────────────────────────────


def test_all_claims_have_inferred_provenance(tmp_path: Path) -> None:
    """Every claim from infer_from_code has provenance='inferred'."""
    (tmp_path / "pyproject.toml").write_text('[project]\ndescription = "test"\n')
    (tmp_path / "README.md").write_text(
        "# Proj\n\nSome description of the project here.\n"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        '"""Module for handling data transformations."""\nimport pandas\n'
    )

    claims = infer_from_code(str(tmp_path))

    assert len(claims) > 0
    for claim in claims:
        assert claim.provenance == "inferred", (
            f"Claim '{claim.text}' has wrong provenance"
        )
        assert claim.confidence <= _MAX_CONFIDENCE, (
            f"Claim '{claim.text}' confidence {claim.confidence} exceeds cap {_MAX_CONFIDENCE}"
        )


# ── Targeted Coverage Fixes ──────────────────────────────────────────


class TestInternalHelpers:
    def test_claim_basic(self) -> None:
        c = _claim("some text", "src")
        assert c.text == "some text"
        assert c.source == "src"
        assert c.confidence == 0.5
        assert c.provenance == "inferred"

    def test_claim_capped(self) -> None:
        c = _claim("x", "s", confidence=0.9)
        assert c.confidence == _MAX_CONFIDENCE

    def test_read_text_safe_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("content", encoding="utf-8")
        assert _read_text_safe(f) == "content"

    def test_read_text_safe_missing(self, tmp_path: Path) -> None:
        assert _read_text_safe(tmp_path / "nope.txt") == ""

    def test_collect_py_files_basic(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.txt").write_text("not python")
        result = _collect_py_files(str(tmp_path))
        assert len(result) == 1

    def test_extract_first_paragraph_skips_badges(self) -> None:
        lines = [
            "![badge](url)",
            "[![ci](link)](href)",
            "<!-- comment -->",
            "Real text.",
        ]
        assert _extract_first_paragraph(lines) == "Real text."

    def test_scan_test_dir_detects_conftest(self, tmp_path: Path) -> None:
        td = tmp_path / "tests"
        td.mkdir()
        (td / "conftest.py").write_text("import pytest\n")
        flags = _scan_test_dir(td)
        assert flags["conftest"] is True
        assert flags["pytest"] is True


class TestCommitInference:
    def test_conventional_commits_detected(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        stdout = "\n".join(
            [
                f"abc{i:04d} {'feat' if i % 2 == 0 else 'fix'}: something {i}"
                for i in range(10)
            ]
        )
        with patch("lintgate.code_inference.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": stdout})()
            claims = _infer_from_commit_messages(str(tmp_path))
        assert any("conventional commit" in c.text.lower() for c in claims)


class TestConstantsSanity:
    def test_skip_dirs(self) -> None:
        assert ".git" in _SKIP_DIRS
        assert "__pycache__" in _SKIP_DIRS

    def test_framework_map(self) -> None:
        assert "fastapi" in _FRAMEWORK_MAP
        assert "pytest" in _FRAMEWORK_MAP
