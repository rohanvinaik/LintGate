"""Tests for lintgate.linters.context_rule_checker — context guidance rule enforcement."""

from __future__ import annotations

from lintgate.linters.context_rule_checker import (
    _build_issue,
    _line_number,
    _read_text,
    _run_forbid_rule,
    _run_require_rule,
    _safe_relpath,
)

# ── _line_number ─────────────────────────────────────────────────────


class TestLineNumber:
    def test_first_character(self):
        assert _line_number("hello\nworld\n", 0) == 1

    def test_second_line(self):
        assert _line_number("hello\nworld\n", 6) == 2

    def test_third_line(self):
        assert _line_number("a\nb\nc\n", 4) == 3

    # Clear functools.cache between tests to avoid cross-test pollution
    def teardown_method(self):
        _line_number.cache_clear()


# ── _safe_relpath ────────────────────────────────────────────────────


class TestSafeRelpath:
    def test_normal_relpath(self):
        result = _safe_relpath("/project/src/foo.py", "/project")
        assert result == "src/foo.py"

    def test_same_path(self):
        result = _safe_relpath("/project/foo.py", "/project")
        assert result == "foo.py"


# ── _read_text ───────────────────────────────────────────────────────


class TestReadText:
    def test_reads_file(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello world")
        assert _read_text(str(p)) == "hello world"

    def test_nonexistent_returns_none(self):
        assert _read_text("/no/such/file.txt") is None

    def test_directory_returns_none(self, tmp_path):
        assert _read_text(str(tmp_path)) is None


# ── _build_issue ─────────────────────────────────────────────────────


class TestBuildIssue:
    def test_default_severity_is_warning(self):
        issue = _build_issue(
            rule={},
            file_path="/test.py",
            rel_path="test.py",
            line=1,
            kind_suffix="forbid",
            default_message="default msg",
            evidence={},
        )
        assert issue.severity == "warning"
        assert issue.kind == "context-forbid"
        assert issue.message == "default msg"

    def test_custom_severity(self):
        issue = _build_issue(
            rule={"severity": "blocking"},
            file_path="/test.py",
            rel_path="test.py",
            line=5,
            kind_suffix="require",
            default_message="default",
            evidence={},
        )
        assert issue.severity == "blocking"
        assert issue.kind == "context-require"

    def test_invalid_severity_defaults_to_warning(self):
        issue = _build_issue(
            rule={"severity": "critical"},
            file_path="/test.py",
            rel_path="test.py",
            line=1,
            kind_suffix="forbid",
            default_message="default",
            evidence={},
        )
        assert issue.severity == "warning"

    def test_custom_message_from_rule(self):
        issue = _build_issue(
            rule={"message": "custom rule message"},
            file_path="/test.py",
            rel_path="test.py",
            line=1,
            kind_suffix="forbid",
            default_message="default msg",
            evidence={},
        )
        assert issue.message == "custom rule message"

    def test_evidence_includes_rel_path(self):
        issue = _build_issue(
            rule={},
            file_path="/project/src/foo.py",
            rel_path="src/foo.py",
            line=1,
            kind_suffix="forbid",
            default_message="test",
            evidence={"pattern": "foo"},
        )
        assert issue.evidence["rel_path"] == "src/foo.py"
        assert issue.evidence["pattern"] == "foo"


# ── _run_forbid_rule ────────────────────────────────────────────────


class TestRunForbidRule:
    def test_matching_pattern_yields_issue(self):
        rule = {"pattern": r"import os", "severity": "warning"}
        issues = list(_run_forbid_rule(rule, "/test.py", "test.py", "import os\nimport sys\n"))
        assert len(issues) == 1
        assert issues[0].kind == "context-forbid"
        assert issues[0].line == 1

    def test_no_match_yields_nothing(self):
        rule = {"pattern": r"import nonexistent"}
        issues = list(_run_forbid_rule(rule, "/test.py", "test.py", "import os\n"))
        assert issues == []

    def test_empty_pattern_yields_nothing(self):
        rule = {"pattern": ""}
        issues = list(_run_forbid_rule(rule, "/test.py", "test.py", "import os\n"))
        assert issues == []

    def test_invalid_regex_yields_nothing(self):
        rule = {"pattern": "[invalid(regex"}
        issues = list(_run_forbid_rule(rule, "/test.py", "test.py", "anything"))
        assert issues == []

    def test_multiple_matches(self):
        rule = {"pattern": r"TODO"}
        source = "# TODO: fix\n# TODO: refactor\n"
        issues = list(_run_forbid_rule(rule, "/test.py", "test.py", source))
        assert len(issues) == 2
        assert issues[0].line == 1
        assert issues[1].line == 2

    # Clear _line_number cache after tests
    def teardown_method(self):
        _line_number.cache_clear()


# ── _run_require_rule ────────────────────────────────────────────────


class TestRunRequireRule:
    def test_pattern_present_returns_none(self):
        rule = {"pattern": r"from __future__ import annotations"}
        result = _run_require_rule(rule, "/test.py", "test.py", "from __future__ import annotations\n")
        assert result is None

    def test_pattern_missing_returns_issue(self):
        rule = {"pattern": r"from __future__ import annotations"}
        result = _run_require_rule(rule, "/test.py", "test.py", "import os\n")
        assert result is not None
        assert result.kind == "context-require"
        assert result.line == 1

    def test_empty_pattern_returns_none(self):
        rule = {"pattern": ""}
        result = _run_require_rule(rule, "/test.py", "test.py", "anything")
        assert result is None

    def test_invalid_regex_returns_none(self):
        rule = {"pattern": "[bad(regex"}
        result = _run_require_rule(rule, "/test.py", "test.py", "anything")
        assert result is None

    # Clear _line_number cache after tests
    def teardown_method(self):
        _line_number.cache_clear()
