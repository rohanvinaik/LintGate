"""Tests for lintgate.linters.secret_checker."""

from __future__ import annotations

from lintgate.linters.secret_checker import (
    SecretChecker,
    _redact,
    _scan_line,
    _LIKELY_PLACEHOLDER_RE,
)
from lintgate.types import LintIssue


# ── _redact ──────────────────────────────────────────────────────────


class TestRedact:
    def test_short_value(self):
        assert _redact("abcdefgh") == "ab***"
        assert _redact("12345") == "12***"

    def test_exact_ten_chars(self):
        assert _redact("1234567890") == "12***"

    def test_long_value(self):
        result = _redact("AKIA1234567890ABCDEF")
        assert result == "AKIA***CDEF"
        assert len(result) < len("AKIA1234567890ABCDEF")

    def test_eleven_chars(self):
        assert _redact("12345678901") == "1234***8901"


# ── _LIKELY_PLACEHOLDER_RE ───────────────────────────────────────────


class TestPlaceholderRegex:
    def test_matches_example(self):
        assert _LIKELY_PLACEHOLDER_RE.search("example_key") is not None

    def test_matches_test_key(self):
        assert _LIKELY_PLACEHOLDER_RE.search("test_key_value") is not None

    def test_matches_changeme(self):
        assert _LIKELY_PLACEHOLDER_RE.search("changeme") is not None

    def test_no_match_real_looking(self):
        assert _LIKELY_PLACEHOLDER_RE.search("production_value_abc") is None


# ── _scan_line ───────────────────────────────────────────────────────


class TestScanLine:
    def test_detects_aws_key(self):
        # "EXAMPLE" triggers placeholder → skip
        # Use a non-placeholder key
        line2 = 'aws_key = "AKIA1234567890ABCDEF"'
        issues = list(_scan_line("test", "file.py", 1, line2))
        assert len(issues) == 1
        assert issues[0].kind == "aws_access_key"
        assert issues[0].confidence == 0.98
        assert issues[0].line == 1

    def test_detects_private_key_header(self):
        line = "-----BEGIN RSA PRIVATE KEY-----"
        issues = list(_scan_line("test", "file.py", 5, line))
        assert len(issues) == 1
        assert issues[0].kind == "private_key"
        assert issues[0].confidence == 1.0
        assert issues[0].severity == "blocking"

    def test_detects_github_token(self):
        line = 'token = "ghp_abcdefghijklmnopqrstuvwxyz"'
        issues = list(_scan_line("test", "file.py", 10, line))
        assert len(issues) >= 1
        kinds = [i.kind for i in issues]
        assert "github_token" in kinds

    def test_skips_placeholder_lines(self):
        line = 'api_key = "example_placeholder_key_here"'
        issues = list(_scan_line("test", "file.py", 1, line))
        assert issues == []

    def test_clean_line_no_issues(self):
        line = "x = 42"
        issues = list(_scan_line("test", "file.py", 1, line))
        assert issues == []

    def test_generic_secret_suppressed_by_specific(self):
        # A line with both an AWS key and a generic secret assignment
        # should yield the specific one but suppress generic
        line = 'api_key = "AKIA1234567890ABCDEF"'
        issues = list(_scan_line("test", "file.py", 1, line))
        kinds = [i.kind for i in issues]
        assert "aws_access_key" in kinds
        # generic_secret_assignment should be suppressed
        assert "generic_secret_assignment" not in kinds


# ── SecretChecker._filter_files ──────────────────────────────────────


class TestSecretCheckerFilterFiles:
    def test_allows_py_files(self):
        checker = SecretChecker()
        result = checker._filter_files(["foo.py", "bar.txt", "baz.rs"])
        assert "foo.py" in result
        assert "bar.txt" in result
        assert "baz.rs" not in result

    def test_allows_env_files(self):
        checker = SecretChecker()
        result = checker._filter_files([".env", ".env.local", "config.yaml"])
        assert ".env" in result
        assert ".env.local" in result
        assert "config.yaml" in result

    def test_rejects_binary_extensions(self):
        checker = SecretChecker()
        result = checker._filter_files(["image.png", "lib.so", "app.wasm"])
        assert result == []
