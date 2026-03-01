"""Tests for secrets scanning in git channel diff detection."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from lintgate.channels.git_channel import _SECRET_PATTERNS, _check_diff_secrets


class TestSecretPatterns:
    """Test individual regex patterns against known secret formats."""

    def _match(self, pattern_name: str, text: str) -> bool:
        """Check if a named pattern matches the text."""
        for name, regex, _ in _SECRET_PATTERNS:
            if name == pattern_name:
                return bool(regex.search(text))
        raise ValueError(f"Unknown pattern: {pattern_name}")

    def test_aws_key_matches(self):
        assert self._match("aws_access_key", "AKIAIOSFODNN7EXAMPLE")

    def test_aws_key_no_match_short(self):
        assert not self._match("aws_access_key", "AKIA1234")

    def test_private_key_rsa(self):
        assert self._match("private_key", "-----BEGIN RSA PRIVATE KEY-----")

    def test_private_key_ec(self):
        assert self._match("private_key", "-----BEGIN EC PRIVATE KEY-----")

    def test_private_key_openssh(self):
        assert self._match("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----")

    def test_private_key_generic(self):
        assert self._match("private_key", "-----BEGIN PRIVATE KEY-----")

    def test_github_token_ghp(self):
        assert self._match("github_token", "ghp_ABCDEFGHIJ1234567890KL")

    def test_github_token_ghs(self):
        assert self._match("github_token", "ghs_ABCDEFGHIJ1234567890KL")

    def test_gitlab_token(self):
        assert self._match("gitlab_token", "glpat-ABCDEFGHIJ1234567890KL")

    def test_connection_string_postgres(self):
        assert self._match("connection_string", "postgres://user:password@host:5432/db")

    def test_connection_string_redis(self):
        assert self._match(
            "connection_string", "redis://default:secretpass@cache.example.com:6379"
        )

    def test_generic_api_key(self):
        assert self._match("generic_api_key", "api_key = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'")
        assert self._match("generic_api_key", 'API_KEY="sk-ABCDEFGHIJKLMNOPQRSTuvwxyz"')
        assert self._match("generic_api_key", 'secret_key: "ABCDEFGHIJ123456789012"')

    def test_generic_secret(self):
        assert self._match("generic_secret", 'token = "ABCDEFGHIJ12345678"')
        assert self._match("generic_secret", "password='longEnoughPassword123'")

    def test_short_value_no_match(self):
        """Values under 16 chars should not match generic patterns."""
        assert not self._match("generic_secret", 'password = "short"')
        assert not self._match("generic_secret", 'token = "123"')

    def test_placeholder_no_match(self):
        """Common placeholder patterns should not match (too short)."""
        assert not self._match("generic_secret", "password = 'xxx'")
        assert not self._match("generic_api_key", "api_key = 'placeholder'")


class TestCheckDiffSecrets:
    """Test the _check_diff_secrets function."""

    def _mock_diff(self, diff_content: str, project_root: str = "/tmp/proj"):
        """Create a mock for git diff that returns diff_content."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = diff_content

        with patch("subprocess.run", return_value=mock_result):
            return _check_diff_secrets(project_root)

    def test_aws_key_in_addition(self):
        diff = "+++ b/config.py\n@@ -0,0 +1,3 @@\n+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        findings = self._mock_diff(diff)
        assert len(findings) >= 1
        assert any(f.kind == "secret_in_diff" for f in findings)
        assert any("aws_access_key" in f.evidence.get("pattern", "") for f in findings)

    def test_private_key_detected(self):
        diff = "+++ b/id_rsa\n@@ -0,0 +1,1 @@\n+-----BEGIN RSA PRIVATE KEY-----\n"
        findings = self._mock_diff(diff)
        assert len(findings) >= 1

    def test_deletion_not_flagged(self):
        """Lines being removed should not trigger secrets detection."""
        diff = "+++ b/config.py\n@@ -1,1 +0,0 @@\n-AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        findings = self._mock_diff(diff)
        assert len(findings) == 0

    def test_context_line_not_flagged(self):
        """Context lines (no +/-) should not trigger detection."""
        diff = (
            "+++ b/config.py\n"
            "@@ -1,3 +1,3 @@\n"
            " AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
            "+# This is a comment\n"
        )
        findings = self._mock_diff(diff)
        # The AWS key is on a context line (no +), only the comment is an addition
        assert not any(
            "aws_access_key" in f.evidence.get("pattern", "") for f in findings
        )

    def test_secret_value_not_in_message(self):
        """Issue messages must never contain actual secret values."""
        diff = (
            "+++ b/config.py\n@@ -0,0 +1,1 @@\n+TOKEN = 'ghp_ABCDEFGHIJ1234567890KL'\n"
        )
        findings = self._mock_diff(diff)
        assert len(findings) >= 1
        for finding in findings:
            assert "ghp_ABCDEFGHIJ" not in finding.message
            assert "ABCDEFGHIJ" not in finding.message

    def test_empty_diff(self):
        findings = self._mock_diff("")
        assert len(findings) == 0

    def test_no_staged_changes(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            findings = _check_diff_secrets("/tmp/proj")

        assert len(findings) == 0

    def test_git_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            findings = _check_diff_secrets("/tmp/proj")

        assert len(findings) == 0

    def test_git_not_installed(self):
        with patch("subprocess.run", side_effect=OSError("git not found")):
            findings = _check_diff_secrets("/tmp/proj")

        assert len(findings) == 0

    def test_git_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            findings = _check_diff_secrets("/tmp/proj")

        assert len(findings) == 0

    def test_file_tracking(self):
        """Findings should include the file path."""
        diff = (
            "+++ b/src/config.py\n@@ -0,0 +1,1 @@\n+API_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        )
        findings = self._mock_diff(diff)
        assert len(findings) >= 1
        assert findings[0].evidence.get("file") == "src/config.py"

    def test_line_number_tracking(self):
        """Findings should include approximate line numbers from hunk headers."""
        diff = (
            "+++ b/config.py\n@@ -0,0 +10,1 @@\n+TOKEN = 'ghp_ABCDEFGHIJ1234567890KL'\n"
        )
        findings = self._mock_diff(diff)
        assert len(findings) >= 1
        assert findings[0].line == 10

    def test_severity_is_warning(self):
        diff = "+++ b/config.py\n@@ -0,0 +1,1 @@\n+-----BEGIN RSA PRIVATE KEY-----\n"
        findings = self._mock_diff(diff)
        assert len(findings) >= 1
        assert all(f.severity == "warning" for f in findings)

    def test_one_finding_per_line(self):
        """Multiple patterns matching the same line should only produce one finding."""
        diff = "+++ b/config.py\n@@ -0,0 +1,1 @@\n+password = 'AKIAIOSFODNN7EXAMPLE'\n"
        findings = self._mock_diff(diff)
        # The line matches both aws_access_key and potentially generic_secret
        # but we should get at most one finding due to the break
        assert len(findings) <= 1
