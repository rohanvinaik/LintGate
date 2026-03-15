"""Tests for lintgate.linters.mypy_linter — severity classification and dep scanning."""

from __future__ import annotations

from lintgate.linters.mypy_linter import (
    _BLOCKING_CODES,
    _HEAVY_DEPS,
    _HEAVY_TIMEOUT_MS,
    _INFORMATIONAL_CODES,
    _MYPY_LINE_RE,
    _classify_severity,
    _detect_heavy_deps,
    _scan_pyproject_toml,
    _scan_requirements_file,
)

# ── _classify_severity ───────────────────────────────────────────────


class TestClassifySeverity:
    """Tests for _classify_severity mapping mypy outputs to LintGate severities."""

    def test_note_always_informational(self) -> None:
        assert _classify_severity("note", "attr-defined", "strict") == "informational"
        assert _classify_severity("note", None, "normal") == "informational"

    def test_blocking_code_returns_blocking(self) -> None:
        for code in ("syntax", "name-defined", "attr-defined", "import", "valid-type"):
            assert _classify_severity("error", code, "normal") == "blocking"

    def test_informational_code_returns_informational(self) -> None:
        for code in ("no-untyped-def", "type-arg", "unused-ignore"):
            assert _classify_severity("error", code, "normal") == "informational"

    def test_strict_mode_error_is_blocking(self) -> None:
        assert _classify_severity("error", "misc", "strict") == "blocking"

    def test_normal_mode_error_is_warning(self) -> None:
        assert _classify_severity("error", "misc", "normal") == "warning"

    def test_warning_severity_is_informational(self) -> None:
        assert _classify_severity("warning", None, "normal") == "informational"

    def test_no_error_code_normal_error(self) -> None:
        assert _classify_severity("error", None, "normal") == "warning"

    def test_no_error_code_strict_error(self) -> None:
        assert _classify_severity("error", None, "strict") == "blocking"


# ── _MYPY_LINE_RE ────────────────────────────────────────────────────


class TestMypyLineRegex:
    """Tests for the mypy output line regex."""

    def test_full_match_with_column_and_code(self) -> None:
        line = 'foo.py:10: 5: error: Incompatible types [assignment]'
        m = _MYPY_LINE_RE.match(line)
        assert m is not None
        assert m.group(1) == "foo.py"
        assert m.group(2) == "10"
        assert m.group(3) == "5"
        assert m.group(4) == "error"
        assert m.group(5) == "Incompatible types"
        assert m.group(6) == "assignment"

    def test_match_without_column(self) -> None:
        line = 'bar.py:20: error: Name not defined [name-defined]'
        m = _MYPY_LINE_RE.match(line)
        assert m is not None
        assert m.group(1) == "bar.py"
        assert m.group(2) == "20"
        assert m.group(3) is None
        assert m.group(4) == "error"
        assert m.group(6) == "name-defined"

    def test_match_without_error_code(self) -> None:
        line = 'baz.py:1: 1: note: Some note message'
        m = _MYPY_LINE_RE.match(line)
        assert m is not None
        assert m.group(4) == "note"
        assert m.group(6) is None

    def test_no_match_on_garbage(self) -> None:
        assert _MYPY_LINE_RE.match("Success: no issues found") is None


# ── _scan_requirements_file ──────────────────────────────────────────


class TestScanRequirementsFile:
    """Tests for scanning requirements.txt for heavy deps."""

    def test_finds_torch(self, tmp_path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("requests==2.28.0\ntorch==2.0.0\nflask\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert found == ["torch"]

    def test_handles_extras_bracket(self, tmp_path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("transformers[torch]>=4.0\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert found == ["transformers"]

    def test_no_heavy_deps(self, tmp_path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("flask\nrequests\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert found == []

    def test_nonexistent_file(self) -> None:
        found: list[str] = []
        _scan_requirements_file("/nonexistent/requirements.txt", found)
        assert found == []

    def test_no_duplicate_entries(self, tmp_path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("torch\ntorch==2.0\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert found == ["torch"]


# ── _scan_pyproject_toml ─────────────────────────────────────────────


class TestScanPyprojectToml:
    """Tests for scanning pyproject.toml for heavy dep mentions."""

    def test_finds_numpy_in_toml(self, tmp_path) -> None:
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\ndependencies = ["numpy>=1.24"]\n')
        found: list[str] = []
        _scan_pyproject_toml(str(toml), found)
        assert "numpy" in found

    def test_empty_toml(self, tmp_path) -> None:
        toml = tmp_path / "pyproject.toml"
        toml.write_text("[project]\n")
        found: list[str] = []
        _scan_pyproject_toml(str(toml), found)
        assert found == []

    def test_nonexistent_toml(self) -> None:
        found: list[str] = []
        _scan_pyproject_toml("/nonexistent/pyproject.toml", found)
        assert found == []


# ── _detect_heavy_deps ───────────────────────────────────────────────


class TestDetectHeavyDeps:
    """Tests for the top-level heavy dependency detector."""

    def test_empty_project(self, tmp_path) -> None:
        assert _detect_heavy_deps(str(tmp_path)) == []

    def test_finds_from_requirements(self, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("pandas>=1.5\n")
        result = _detect_heavy_deps(str(tmp_path))
        assert result == ["pandas"]

    def test_finds_from_pyproject(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["scipy"]\n'
        )
        result = _detect_heavy_deps(str(tmp_path))
        assert result == ["scipy"]


# ── Constants ────────────────────────────────────────────────────────


class TestConstants:
    """Verify constant membership and values."""

    def test_heavy_deps_contains_torch(self) -> None:
        assert "torch" in _HEAVY_DEPS
        assert "tensorflow" in _HEAVY_DEPS

    def test_heavy_timeout_ms(self) -> None:
        assert _HEAVY_TIMEOUT_MS == 60000

    def test_blocking_codes_set(self) -> None:
        assert "syntax" in _BLOCKING_CODES
        assert "import" in _BLOCKING_CODES
        assert len(_BLOCKING_CODES) == 5

    def test_informational_codes_set(self) -> None:
        assert "unused-ignore" in _INFORMATIONAL_CODES
        assert len(_INFORMATIONAL_CODES) == 3
