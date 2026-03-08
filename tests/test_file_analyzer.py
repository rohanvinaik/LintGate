"""Tests for lintgate.specification.file_analyzer — single-file spec analysis."""

from __future__ import annotations

import pytest

from lintgate.specification.file_analyzer import analyze_file


@pytest.fixture(autouse=True)
def _clear_ast_cache():
    """Clear the module-level AST cache to prevent test pollution."""
    from lintgate.specification.ledger import _AST_TREE_CACHE

    _AST_TREE_CACHE.clear()
    yield
    _AST_TREE_CACHE.clear()


class TestAnalyzeFile:
    def test_simple_file(self, tmp_path):
        src = tmp_path / "calc.py"
        src.write_text(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def subtract(a: int, b: int) -> int:\n"
            "    return a - b\n"
        )
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is None
        assert len(result.functions) >= 1
        assert result.total_sigma > 0

    def test_with_prescriptions(self, tmp_path):
        src = tmp_path / "calc.py"
        src.write_text(
            "def multiply(a: int, b: int) -> int:\n"
            "    return a * b\n"
        )
        result = analyze_file(str(src), str(tmp_path), include_prescriptions=True)

        assert result.error is None
        assert len(result.functions) >= 1
        # Pure function in bulk phase should get prescriptions
        assert isinstance(result.prescriptions, list)

    def test_nonexistent_file(self, tmp_path):
        result = analyze_file(str(tmp_path / "missing.py"), str(tmp_path))

        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_non_python_file(self, tmp_path):
        src = tmp_path / "readme.md"
        src.write_text("# Hello\n")
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is not None
        assert "Not a Python file" in result.error

    def test_to_dict_structure(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def greet(name: str) -> str:\n    return f'Hello {name}'\n")
        result = analyze_file(str(src), str(tmp_path))

        d = result.to_dict()
        assert "file" in d
        assert "total_functions" in d
        assert "total_sigma" in d
        assert "mean_spec_level" in d
        assert "functions" in d

    def test_regime_and_risk_distribution(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text(
            "def pure_func(x):\n    return x * 2\n\n"
            "def impure_func(x):\n    print(x)\n    return x\n"
        )
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is None
        assert len(result.regime_distribution) >= 1
        assert len(result.risk_distribution) >= 1

    def test_empty_file(self, tmp_path):
        src = tmp_path / "empty.py"
        src.write_text("# just a comment\n")
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is None
        assert len(result.functions) == 0
        assert result.total_sigma == 0
