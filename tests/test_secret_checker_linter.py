"""Tests for built-in secret checker linter."""

from __future__ import annotations

from lintgate.linters.secret_checker import SecretChecker
from lintgate.types import LinterContext


def _ctx(tmp_path) -> LinterContext:
    return LinterContext(
        files=[],
        project_root=str(tmp_path),
        config={},
    )


def test_secret_checker_metadata() -> None:
    linter = SecretChecker()
    assert linter.name == "secret_checker"
    assert linter.tier == 2
    assert linter.required_tool is None


def test_detects_high_confidence_token(tmp_path) -> None:
    p = tmp_path / "a.py"
    p.write_text('TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"\n')
    ctx = _ctx(tmp_path)
    ctx.files = [str(p)]

    issues = list(SecretChecker().run(ctx))
    assert len(issues) == 1
    issue = issues[0]
    assert issue.kind == "github_token"
    assert issue.severity == "warning"
    assert issue.file == str(p)
    assert issue.line == 1
    assert "***" in issue.message


def test_detects_private_key_blocking(tmp_path) -> None:
    p = tmp_path / "k.py"
    p.write_text(
        "key = '''-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----'''\n"
    )
    ctx = _ctx(tmp_path)
    ctx.files = [str(p)]

    issues = list(SecretChecker().run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "private_key"
    assert issues[0].severity == "blocking"


def test_ignores_placeholder_values(tmp_path) -> None:
    p = tmp_path / "placeholder.py"
    p.write_text('API_KEY = "replace_me_with_real_token"\n')
    ctx = _ctx(tmp_path)
    ctx.files = [str(p)]

    issues = list(SecretChecker().run(ctx))
    assert issues == []
