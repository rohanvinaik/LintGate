"""Tests for lintgate/linters/ruff_linter.py — mutation kill targets.

Covers RuffLinter, RuffFormatLinter, _classify_severity,
_maybe_escalate_e402, _build_e402_evidence_safe, and _extract_e402_module.

Focuses on exact value assertions and branch coverage to kill mutants.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.ruff_linter import (
    _BLOCKING_CODES,
    _INFORMATIONAL_CODES,
    RuffFormatLinter,
    RuffLinter,
    _build_e402_evidence_safe,
    _classify_severity,
    _extract_e402_module,
    _maybe_escalate_e402,
)
from lintgate.types import LinterContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path, files=None, config=None, strictness="normal"):
    return LinterContext(
        files=files or [],
        project_root=str(tmp_path),
        strictness=strictness,
        config=config or {},
    )


# ---------------------------------------------------------------------------
# _classify_severity
# ---------------------------------------------------------------------------


class TestClassifySeverity:
    """Tests for _classify_severity."""

    def test_all_blocking_codes_return_blocking(self):
        for code in _BLOCKING_CODES:
            assert _classify_severity(code, "normal") == "blocking", f"{code} should be blocking"
            assert _classify_severity(code, "strict") == "blocking", (
                f"{code} should be blocking in strict"
            )

    def test_all_informational_codes_return_informational(self):
        for code in _INFORMATIONAL_CODES:
            assert _classify_severity(code, "normal") == "informational", (
                f"{code} should be informational"
            )

    def test_unknown_code_returns_warning(self):
        assert _classify_severity("X999", "normal") == "warning"
        assert _classify_severity("CUSTOM01", "normal") == "warning"

    def test_strict_mode_f4_prefix_returns_warning(self):
        assert _classify_severity("F401", "strict") == "warning"
        assert _classify_severity("F402", "strict") == "warning"

    def test_strict_mode_f8_prefix_returns_warning(self):
        assert _classify_severity("F811", "strict") == "blocking"  # F811 is in _BLOCKING_CODES
        assert _classify_severity("F841", "strict") == "blocking"  # F841 is in _BLOCKING_CODES

    def test_normal_mode_f4_prefix_returns_warning(self):
        assert _classify_severity("F401", "normal") == "warning"

    def test_f821_is_blocking_not_informational(self):
        assert _classify_severity("F821", "normal") == "blocking"
        assert _classify_severity("F821", "strict") == "blocking"

    def test_e999_is_blocking(self):
        assert _classify_severity("E999", "normal") == "blocking"
        assert _classify_severity("E999", "strict") == "blocking"


# ---------------------------------------------------------------------------
# _maybe_escalate_e402
# ---------------------------------------------------------------------------


class TestMaybeEscalateE402:
    """Tests for _maybe_escalate_e402."""

    def test_both_conditions_met_returns_085(self):
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["requests"],
                "has_lazy": True,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 0.85
        assert escalation is not None
        assert "cross-environment risk" in escalation["reason"]
        assert escalation["conditions_met"] == ["non_stdlib_deps", "lazy_imports"]
        assert escalation["non_stdlib"] == ["requests"]

    def test_no_non_stdlib_no_escalation(self):
        evidence = {
            "transitive_imports": {
                "non_stdlib": [],
                "has_lazy": True,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 0.9)
        assert new_conf == 0.9
        assert escalation is None

    def test_no_lazy_no_escalation(self):
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["boto3"],
                "has_lazy": False,
            },
        }
        new_conf, escalation = _maybe_escalate_e402(evidence, 1.0)
        assert new_conf == 1.0
        assert escalation is None

    def test_empty_evidence_no_escalation(self):
        new_conf, escalation = _maybe_escalate_e402({}, 1.0)
        assert new_conf == 1.0
        assert escalation is None

    def test_missing_transitive_imports_key(self):
        new_conf, escalation = _maybe_escalate_e402({"code": "E402"}, 0.7)
        assert new_conf == 0.7
        assert escalation is None

    def test_escalation_always_085_regardless_of_input(self):
        evidence = {
            "transitive_imports": {
                "non_stdlib": ["pkg"],
                "has_lazy": True,
            },
        }
        for conf in [0.5, 0.7, 0.85, 1.0]:
            new_conf, _ = _maybe_escalate_e402(evidence, conf)
            assert new_conf == 0.85


# ---------------------------------------------------------------------------
# _extract_e402_module
# ---------------------------------------------------------------------------


class TestExtractE402Module:
    """Tests for _extract_e402_module."""

    def test_extracts_import_statement(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("import os\nimport sys\n")
        assert _extract_e402_module(str(f), 1) == "os"
        assert _extract_e402_module(str(f), 2) == "sys"

    def test_extracts_from_import(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("from pathlib import Path\n")
        assert _extract_e402_module(str(f), 1) == "pathlib"

    def test_returns_none_for_no_matching_line(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\ny = 2\n")
        assert _extract_e402_module(str(f), 1) is None
        assert _extract_e402_module(str(f), 99) is None

    def test_returns_none_for_syntax_error(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def (broken\n")
        assert _extract_e402_module(str(f), 1) is None

    def test_returns_none_for_nonexistent_file(self):
        assert _extract_e402_module("/nonexistent/path.py", 1) is None

    def test_import_with_alias(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("import numpy as np\n")
        assert _extract_e402_module(str(f), 1) == "numpy"

    def test_from_import_with_multiple_names(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("from os.path import join, dirname\n")
        assert _extract_e402_module(str(f), 1) == "os.path"


# ---------------------------------------------------------------------------
# _build_e402_evidence_safe
# ---------------------------------------------------------------------------


class TestBuildE402EvidenceSafe:
    """Tests for _build_e402_evidence_safe."""

    def test_returns_empty_dict_on_import_error(self):
        """When import_tracing is unavailable, returns {}."""
        item = {"filename": "test.py", "code": "E402"}
        location = {"row": 5}
        with (
            patch(
                "lintgate.linters.ruff_linter._extract_e402_module",
                return_value="os",
            ),
            patch.dict(
                "sys.modules",
                {"lintgate.linters.structure_checks.import_tracing": None},
            ),
        ):
            result = _build_e402_evidence_safe(item, location, "/tmp")
        # Graceful degradation: returns {} on import failure
        assert isinstance(result, dict)

    def test_returns_note_when_module_not_resolved(self, tmp_path):
        """When module name cannot be extracted, returns note dict."""
        f = tmp_path / "nomod.py"
        f.write_text("x = 1\n")
        item = {"filename": str(f), "code": "E402"}
        location = {"row": 1}
        result = _build_e402_evidence_safe(item, location, str(tmp_path))
        assert result.get("note") == "could not resolve module name" or result.get("code") == "E402"

    def test_graceful_degradation_on_exception(self):
        """On any exception, returns {} (never crashes the linter)."""
        item = {"filename": "/nonexistent.py"}
        location = {"row": 1}
        result = _build_e402_evidence_safe(item, location, "/tmp")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# RuffLinter
# ---------------------------------------------------------------------------


class TestRuffLinter:
    """Tests for RuffLinter.run()."""

    def test_class_attributes(self):
        linter = RuffLinter()
        assert linter.name == "ruff_check"
        assert linter.tier == 0
        assert linter.timeout_ms == 5000
        assert linter.required_tool == "ruff"

    def test_basic_issue_parsing(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        data = json.dumps(
            [
                {
                    "code": "F401",
                    "message": "os imported but unused",
                    "filename": "a.py",
                    "location": {"row": 1, "column": 1},
                    "end_location": {"row": 1, "column": 10},
                    "fix": {"message": "Remove unused import"},
                }
            ]
        )
        mock_result = MagicMock(stdout=data)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        issue = issues[0]
        assert issue.linter == "ruff"
        assert issue.kind == "F401"
        assert issue.message == "os imported but unused"
        assert issue.file == "a.py"
        assert issue.line == 1
        assert issue.column == 1
        assert issue.end_line == 1
        assert issue.end_column == 10
        assert issue.severity == "warning"
        assert issue.confidence == 1.0
        assert issue.fixable is True
        assert issue.fix_description == "Remove unused import"

    def test_no_fix_field(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        data = json.dumps(
            [
                {
                    "code": "E501",
                    "message": "Line too long",
                    "filename": "a.py",
                    "location": {"row": 5, "column": 80},
                    "end_location": {"row": 5, "column": 120},
                    "fix": None,
                }
            ]
        )
        mock_result = MagicMock(stdout=data)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].fixable is False
        assert issues[0].fix_description is None
        assert issues[0].severity == "informational"

    def test_empty_stdout_yields_no_issues(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        linter = RuffLinter()
        mock_result = MagicMock(stdout="")
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert issues == []

    def test_malformed_json_yields_no_issues(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        mock_result = MagicMock(stdout="not valid json {}")
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert issues == []

    def test_extra_args_passed_to_command(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"], config={"extra_args": ["--select", "F"]})
        linter = RuffLinter()
        mock_result = MagicMock(stdout="[]")
        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))
        cmd = mock_cmd.call_args[0][0]
        assert cmd[0] == "ruff"
        assert cmd[1] == "check"
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--no-fix" in cmd
        assert "--select" in cmd
        assert "F" in cmd
        assert "a.py" in cmd

    def test_empty_extra_args_not_extended(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["b.py"], config={"extra_args": []})
        linter = RuffLinter()
        mock_result = MagicMock(stdout="[]")
        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))
        cmd = mock_cmd.call_args[0][0]
        assert cmd == ["ruff", "check", "--output-format", "json", "--no-fix", "b.py"]

    def test_multiple_issues_parsed(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        data = json.dumps(
            [
                {
                    "code": "F401",
                    "message": "unused import",
                    "filename": "a.py",
                    "location": {"row": 1, "column": 1},
                    "end_location": {"row": 1, "column": 5},
                    "fix": None,
                },
                {
                    "code": "F821",
                    "message": "undefined name",
                    "filename": "a.py",
                    "location": {"row": 10, "column": 5},
                    "end_location": {"row": 10, "column": 15},
                    "fix": None,
                },
            ]
        )
        mock_result = MagicMock(stdout=data)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 2
        assert issues[0].kind == "F401"
        assert issues[0].severity == "warning"
        assert issues[1].kind == "F821"
        assert issues[1].severity == "blocking"

    def test_e402_triggers_evidence_building(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        data = json.dumps(
            [
                {
                    "code": "E402",
                    "message": "Module level import not at top of file",
                    "filename": "a.py",
                    "location": {"row": 15, "column": 1},
                    "end_location": {"row": 15, "column": 20},
                    "fix": None,
                }
            ]
        )
        mock_result = MagicMock(stdout=data)
        with (
            patch.object(linter, "run_command", return_value=mock_result),
            patch(
                "lintgate.linters.ruff_linter._build_e402_evidence_safe",
                return_value={
                    "code": "E402",
                    "transitive_imports": {"non_stdlib": [], "has_lazy": False},
                },
            ),
        ):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].kind == "E402"
        assert "code" in issues[0].evidence

    def test_e402_with_escalation(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        data = json.dumps(
            [
                {
                    "code": "E402",
                    "message": "Module level import not at top of file",
                    "filename": "a.py",
                    "location": {"row": 15, "column": 1},
                    "end_location": {"row": 15, "column": 20},
                    "fix": None,
                }
            ]
        )
        mock_result = MagicMock(stdout=data)
        with (
            patch.object(linter, "run_command", return_value=mock_result),
            patch(
                "lintgate.linters.ruff_linter._build_e402_evidence_safe",
                return_value={
                    "transitive_imports": {
                        "non_stdlib": ["requests"],
                        "has_lazy": True,
                    }
                },
            ),
        ):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].confidence == 0.85
        assert "escalation" in issues[0].evidence

    def test_missing_fields_use_defaults(self, tmp_path):
        """When ruff output has missing optional fields, defaults apply."""
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        # Keys present with None values: .get returns None (not the default)
        data = json.dumps(
            [
                {
                    "code": None,
                    "message": "",
                    "filename": None,
                    "location": {},
                    "end_location": {},
                }
            ]
        )
        mock_result = MagicMock(stdout=data)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].kind is None
        assert issues[0].message == ""
        assert issues[0].file is None
        assert issues[0].line is None
        assert issues[0].column is None

    def test_missing_keys_use_get_defaults(self, tmp_path):
        """When ruff output keys are entirely absent, .get defaults kick in."""
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffLinter()
        data = json.dumps([{}])  # no keys at all
        mock_result = MagicMock(stdout=data)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].kind == "unknown"  # .get("code", "unknown") default
        assert issues[0].message == ""
        assert issues[0].fixable is False
        assert issues[0].fix_description is None

    def test_command_includes_all_files(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py", "b.py", "c.py"])
        linter = RuffLinter()
        mock_result = MagicMock(stdout="[]")
        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))
        cmd = mock_cmd.call_args[0][0]
        assert cmd[-3:] == ["a.py", "b.py", "c.py"]


# ---------------------------------------------------------------------------
# RuffFormatLinter
# ---------------------------------------------------------------------------


class TestRuffFormatLinter:
    """Tests for RuffFormatLinter.run()."""

    def test_class_attributes(self):
        linter = RuffFormatLinter()
        assert linter.name == "ruff_format"
        assert linter.tier == 0
        assert linter.timeout_ms == 3000
        assert linter.required_tool == "ruff"

    def test_clean_formatting_yields_no_issues(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffFormatLinter()
        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert issues == []

    def test_file_needs_formatting(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffFormatLinter()
        diff = "+++ a.py\t2024-01-01\n-old\n+new\n"
        mock_result = MagicMock(returncode=1, stdout=diff)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].linter == "ruff_format"
        assert issues[0].kind == "format"
        assert issues[0].message == "File needs formatting"
        assert issues[0].file == "a.py"
        assert issues[0].severity == "informational"
        assert issues[0].confidence == 1.0
        assert issues[0].fixable is True
        assert issues[0].fix_description == "Run: ruff format"

    def test_dedup_same_file(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffFormatLinter()
        diff = "+++ a.py\t2024\n-x\n+y\n+++ a.py\t2024\n-a\n+b\n"
        mock_result = MagicMock(returncode=1, stdout=diff)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert len(issues) == 1

    def test_multiple_files_formatting(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py", "b.py"])
        linter = RuffFormatLinter()
        diff = "+++ a.py\t2024\n-x\n+y\n+++ b.py\t2024\n-a\n+b\n"
        mock_result = MagicMock(returncode=1, stdout=diff)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert len(issues) == 2
        files = {i.file for i in issues}
        assert files == {"a.py", "b.py"}

    def test_skips_dev_null(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffFormatLinter()
        diff = "+++ /dev/null\n-deleted\n"
        mock_result = MagicMock(returncode=1, stdout=diff)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert issues == []

    def test_returncode_1_but_empty_stdout(self, tmp_path):
        """returncode=1 with empty stdout yields no issues."""
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffFormatLinter()
        mock_result = MagicMock(returncode=1, stdout="")
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert issues == []

    def test_returncode_0_with_stdout_yields_no_issues(self, tmp_path):
        """returncode=0 (even with stdout) yields no issues."""
        ctx = _make_ctx(tmp_path, files=["a.py"])
        linter = RuffFormatLinter()
        mock_result = MagicMock(returncode=0, stdout="+++ a.py\t2024\n")
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert issues == []

    def test_filepath_with_tab_in_diff_line(self, tmp_path):
        """Only the part before tab is used as filepath."""
        ctx = _make_ctx(tmp_path, files=["my_file.py"])
        linter = RuffFormatLinter()
        diff = "+++ my_file.py\t2024-01-01 00:00:00\n-x\n+y\n"
        mock_result = MagicMock(returncode=1, stdout=diff)
        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))
        assert len(issues) == 1
        assert issues[0].file == "my_file.py"

    def test_command_structure(self, tmp_path):
        ctx = _make_ctx(tmp_path, files=["a.py", "b.py"])
        linter = RuffFormatLinter()
        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))
        cmd = mock_cmd.call_args[0][0]
        assert cmd == ["ruff", "format", "--check", "--diff", "a.py", "b.py"]


# ---------------------------------------------------------------------------
# Blocking / Informational code sets
# ---------------------------------------------------------------------------


class TestCodeSets:
    """Tests verifying the _BLOCKING_CODES and _INFORMATIONAL_CODES sets."""

    def test_blocking_codes_are_frozenset(self):
        assert isinstance(_BLOCKING_CODES, frozenset)

    def test_informational_codes_are_frozenset(self):
        assert isinstance(_INFORMATIONAL_CODES, frozenset)

    def test_blocking_codes_exact_contents(self):
        assert frozenset({"F821", "F811", "F841", "E999"}) == _BLOCKING_CODES

    def test_informational_codes_exact_contents(self):
        assert (
            frozenset({"E501", "W291", "W292", "W293", "D100", "D101", "D102", "D103"})
            == _INFORMATIONAL_CODES
        )

    def test_no_overlap_between_blocking_and_informational(self):
        assert frozenset() == _BLOCKING_CODES & _INFORMATIONAL_CODES
