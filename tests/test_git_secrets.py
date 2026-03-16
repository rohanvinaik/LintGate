"""Tests for lintgate/channels/_git_secrets.py — all 3 functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.channels._git_secrets import (
    _check_diff_secrets,
    _iter_diff_additions,
    _match_secret_pattern,
)

# ─── _match_secret_pattern ────────────────────────────────────────────────


def test_match_aws_key():
    """AWS access key pattern matches AKIA prefix with 16 uppercase chars."""
    result = _match_secret_pattern(
        "aws_key = AKIAIOSFODNN7EXAMPLE",
        "config.py",
        10,
        "/proj",
    )
    assert result is not None
    assert result.kind == "secret_in_diff"
    assert result.confidence == 0.95
    assert result.evidence["pattern"] == "aws_access_key"
    assert result.file == "/proj/config.py"
    assert result.line == 10


def test_match_private_key():
    """PEM private key header matches with high confidence."""
    result = _match_secret_pattern(
        "-----BEGIN RSA PRIVATE KEY-----",
        "key.pem",
        1,
        "/proj",
    )
    assert result is not None
    assert result.confidence == 0.99
    assert result.evidence["pattern"] == "private_key"


def test_match_github_token():
    """GitHub personal access token pattern matches ghp_ prefix."""
    result = _match_secret_pattern(
        'TOKEN = "ghp_ABCDEFghijklmnopqrst1234"',
        "ci.py",
        5,
        "/proj",
    )
    assert result is not None
    assert result.evidence["pattern"] == "github_token"


def test_match_no_secret():
    """Clean code line returns None."""
    result = _match_secret_pattern(
        "x = compute_value(42)",
        "app.py",
        1,
        "/proj",
    )
    assert result is None


def test_match_connection_string():
    """Database connection string with credentials is detected."""
    result = _match_secret_pattern(
        "DB = 'postgres://admin:s3cret@db.host:5432/mydb'",
        "settings.py",
        20,
        "/proj",
    )
    assert result is not None
    assert result.evidence["pattern"] == "connection_string"


def test_match_generic_api_key():
    """Generic API key assignment is detected."""
    result = _match_secret_pattern(
        "api_key = 'abcdefghijklmnopqrstuvwx'",
        "config.py",
        3,
        "/proj",
    )
    assert result is not None
    assert result.evidence["pattern"] == "generic_api_key"


def test_match_no_file_context():
    """When current_file is None, result.file is None."""
    result = _match_secret_pattern(
        "-----BEGIN PRIVATE KEY-----",
        None,
        None,
        "/proj",
    )
    assert result is not None
    assert result.file is None
    assert result.line is None


# ─── _iter_diff_additions ─────────────────────────────────────────────────


def test_iter_simple_diff():
    """Parse a minimal unified diff into additions."""
    diff = "diff --git a/f.py b/f.py\n+++ b/f.py\n@@ -0,0 +1,2 @@\n+line_one\n+line_two\n"
    adds = _iter_diff_additions(diff)
    assert len(adds) == 2
    assert adds[0] == ("f.py", "line_one", 1)
    assert adds[1] == ("f.py", "line_two", 2)


def test_iter_skips_removals():
    """Removal lines (starting with -) are not included in additions."""
    diff = "+++ b/a.py\n@@ -1,2 +1,2 @@\n-old_line\n+new_line\n"
    adds = _iter_diff_additions(diff)
    assert len(adds) == 1
    assert adds[0][1] == "new_line"


def test_iter_multiple_files():
    """Additions from multiple files are tracked with correct file context."""
    diff = "+++ b/alpha.py\n@@ -0,0 +1 @@\n+a_line\n+++ b/beta.py\n@@ -0,0 +5 @@\n+b_line\n"
    adds = _iter_diff_additions(diff)
    assert len(adds) == 2
    assert adds[0][0] == "alpha.py"
    assert adds[1][0] == "beta.py"
    assert adds[1][2] == 5  # hunk starts at line 5


def test_iter_empty_diff():
    """Empty diff string returns empty list."""
    assert _iter_diff_additions("") == []


def test_iter_dev_null_file():
    """'+++ ' without 'b/' prefix sets current_file to None."""
    diff = "+++ /dev/null\n@@ -1 +0,0 @@\n"
    adds = _iter_diff_additions(diff)
    assert adds == []


# ─── _check_diff_secrets ─────────────────────────────────────────────────


def test_check_diff_secrets_finds_secret():
    """Integration: staged diff containing a secret produces a finding."""
    mock_result = MagicMock()
    mock_result.stdout = "+++ b/config.py\n@@ -0,0 +1 @@\n+TOKEN = AKIAIOSFODNN7EXAMPLE\n"
    with patch("lintgate.channels._git_secrets.run_cmd", return_value=mock_result):
        findings = _check_diff_secrets("/proj")
    assert len(findings) == 1
    assert findings[0].evidence["pattern"] == "aws_access_key"


def test_check_diff_secrets_clean():
    """Clean diff produces no findings."""
    mock_result = MagicMock()
    mock_result.stdout = "+++ b/app.py\n@@ -0,0 +1 @@\n+x = 42\n"
    with patch("lintgate.channels._git_secrets.run_cmd", return_value=mock_result):
        findings = _check_diff_secrets("/proj")
    assert findings == []


def test_check_diff_secrets_no_output():
    """When run_cmd returns None, result is an empty list."""
    with patch("lintgate.channels._git_secrets.run_cmd", return_value=None):
        findings = _check_diff_secrets("/proj")
    assert findings == []


def test_check_diff_secrets_empty_stdout():
    """When stdout is empty, result is an empty list."""
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch("lintgate.channels._git_secrets.run_cmd", return_value=mock_result):
        findings = _check_diff_secrets("/proj")
    assert findings == []
