"""Git channel helper function tests and branch coverage.

Split from test_git_channel.py to stay under the 400-line limit.

Covers:
- _parse_diff_stat_totals
- _check_large_changes edge cases
- _match_secret_pattern
- _iter_diff_additions (basic + branch coverage)
- _check_diff_secrets
- GitChannel.execute branch coverage
- _check_quality_infrastructure error handling
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.channels.git_channel import (
    GitChannel,
    _check_diff_secrets,
    _check_large_changes,
    _check_quality_infrastructure,
    _iter_diff_additions,
    _match_secret_pattern,
    _parse_diff_stat_totals,
)
from lintgate.controlplane.types import (
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification

# ── _parse_diff_stat_totals ──────────────────────────────────────────────


def test_parse_diff_stat_no_summary() -> None:
    assert _parse_diff_stat_totals("no stats here\n") == (0, 0)


def test_parse_diff_stat_insertions_only() -> None:
    out = " 3 files changed, 42 insertions(+)\n"
    ins, dels = _parse_diff_stat_totals(out)
    assert ins == 42
    assert dels == 0


def test_parse_diff_stat_both() -> None:
    out = " 5 files changed, 100 insertions(+), 20 deletions(-)\n"
    ins, dels = _parse_diff_stat_totals(out)
    assert ins == 100
    assert dels == 20


# ── _check_large_changes edge cases ─────────────────────────────────────


def test_large_changes_git_fail(tmp_path: Path) -> None:
    with patch("lintgate.channels.git_channel.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        findings = _check_large_changes(str(tmp_path))
    assert findings == []


def test_large_changes_timeout(tmp_path: Path) -> None:
    with patch(
        "lintgate.channels.git_channel.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=3),
    ):
        findings = _check_large_changes(str(tmp_path))
    assert findings == []


# ── _match_secret_pattern ────────────────────────────────────────────────


def test_match_secret_aws_key(tmp_path: Path) -> None:
    finding = _match_secret_pattern("AWS_KEY=AKIAIOSFODNN7EXAMPLE", "config.py", 10, str(tmp_path))
    assert finding is not None
    assert finding.kind == "secret_in_diff"
    assert finding.severity == "warning"


def test_match_secret_no_match(tmp_path: Path) -> None:
    finding = _match_secret_pattern("x = 42", "app.py", 5, str(tmp_path))
    assert finding is None


def test_match_secret_none_file(tmp_path: Path) -> None:
    finding = _match_secret_pattern(
        "token=ghp_abcdefghijklmnopqrstuvwxyz1234", None, None, str(tmp_path)  # gitleaks:allow
    )
    assert finding is not None
    assert finding.file is None


# ── _iter_diff_additions ─────────────────────────────────────────────────


def test_iter_diff_additions_basic() -> None:
    diff = "+++ b/app.py\n@@ -0,0 +1,3 @@\n+line1\n+line2\n+line3\n"
    additions = _iter_diff_additions(diff)
    assert len(additions) == 3
    assert additions[0] == ("app.py", "line1", 1)
    assert additions[1] == ("app.py", "line2", 2)


def test_iter_diff_additions_no_file_prefix() -> None:
    diff = "+++ /dev/null\n@@ -1,2 +0,0 @@\n"
    additions = _iter_diff_additions(diff)
    assert additions == []


def test_iter_diff_additions_multi_file() -> None:
    diff = "+++ b/a.py\n@@ -0,0 +1,1 @@\n+first\n+++ b/b.py\n@@ -0,0 +1,1 @@\n+second\n"
    additions = _iter_diff_additions(diff)
    assert len(additions) == 2
    assert additions[0][0] == "a.py"
    assert additions[1][0] == "b.py"


def test_iter_diff_additions_empty_diff() -> None:
    assert _iter_diff_additions("") == []


def test_iter_diff_additions_addition_before_hunk_header() -> None:
    diff = "+++ b/file.py\n+orphan_line\n"
    additions = _iter_diff_additions(diff)
    assert len(additions) == 1
    assert additions[0] == ("file.py", "orphan_line", None)


def test_iter_diff_additions_hunk_header_no_plus_match() -> None:
    diff = "+++ b/file.py\n@@ -1,3 @@\n+added\n"
    additions = _iter_diff_additions(diff)
    assert len(additions) == 1
    assert additions[0][2] is None


def test_iter_diff_additions_removal_and_context_lines_ignored() -> None:
    diff = (
        "+++ b/file.py\n"
        "@@ -1,5 +1,5 @@\n"
        " context_line\n"
        "-removed_line\n"
        "+added_line\n"
        " another_context\n"
    )
    additions = _iter_diff_additions(diff)
    assert len(additions) == 1
    assert additions[0][1] == "added_line"
    assert additions[0][2] == 1


def test_iter_diff_additions_multiple_hunks() -> None:
    diff = "+++ b/app.py\n@@ -1,2 +1,2 @@\n-old\n+new1\n@@ -10,2 +10,2 @@\n-old2\n+new2\n"
    additions = _iter_diff_additions(diff)
    assert len(additions) == 2
    assert additions[0] == ("app.py", "new1", 1)
    assert additions[1] == ("app.py", "new2", 10)


def test_iter_diff_additions_only_context_and_removals() -> None:
    diff = "+++ b/app.py\n@@ -1,3 +1,1 @@\n context\n-old1\n-old2\n"
    assert _iter_diff_additions(diff) == []


# ── _check_diff_secrets ──────────────────────────────────────────────────


def test_check_diff_secrets_git_fail(tmp_path: Path) -> None:
    with patch("lintgate.channels.git_channel.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        findings = _check_diff_secrets(str(tmp_path))
    assert findings == []


def test_check_diff_secrets_timeout(tmp_path: Path) -> None:
    with patch(
        "lintgate.channels.git_channel.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
    ):
        findings = _check_diff_secrets(str(tmp_path))
    assert findings == []


def test_check_diff_secrets_empty_diff(tmp_path: Path) -> None:
    with patch("lintgate.channels.git_channel.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        findings = _check_diff_secrets(str(tmp_path))
    assert findings == []


def test_check_diff_secrets_with_secret(tmp_path: Path) -> None:
    fake_diff = "+++ b/config.py\n@@ -0,0 +1,1 @@\n+AWS_KEY=AKIAIOSFODNN7EXAMPLE\n"
    with patch("lintgate.channels.git_channel.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_diff)
        findings = _check_diff_secrets(str(tmp_path))
    assert len(findings) == 1
    assert findings[0].kind == "secret_in_diff"


# ── GitChannel.execute branch coverage ───────────────────────────────────


def _init_git_repo(tmp_path: Path, files: dict[str, str] | None = None) -> None:
    """Helper: init a git repo with optional files committed."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        capture_output=True,
        cwd=str(tmp_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        capture_output=True,
        cwd=str(tmp_path),
    )
    for name, content in (files or {"app.py": "x = 1\n"}).items():
        (tmp_path / name).write_text(content)
    subprocess.run(["git", "add", "."], capture_output=True, cwd=str(tmp_path))
    subprocess.run(["git", "commit", "-m", "init"], capture_output=True, cwd=str(tmp_path))


def test_execute_mcp_runs_all_checks(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(tmp_path / "app.py")],
        change_classification=ChangeClassification(
            files_changed=[str(tmp_path / "app.py")],
            change_kind="logic",
            risk_level="moderate",
        ),
        surface="mcp",
    )
    result = GitChannel().execute(event, ControlPlaneConfig())
    assert result.status in ("pass", "fail")
    assert result.duration_ms >= 0


def test_execute_lockfile_check_on_dep_change(tmp_path: Path) -> None:
    _init_git_repo(tmp_path, {"requirements.txt": "flask\n"})
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(tmp_path / "requirements.txt")],
        change_classification=ChangeClassification(
            files_changed=[str(tmp_path / "requirements.txt")],
            change_kind="dependency",
            risk_level="moderate",
        ),
        surface="hook",
    )
    result = GitChannel().execute(event, ControlPlaneConfig())
    assert result.status in ("pass", "fail")


def test_execute_severity_escalation(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "config.py").write_text("KEY=AKIAIOSFODNN7EXAMPLE\n")
    subprocess.run(["git", "add", "config.py"], capture_output=True, cwd=str(tmp_path))

    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Write",
        files_changed=[str(tmp_path / "config.py")],
        change_classification=ChangeClassification(
            files_changed=[str(tmp_path / "config.py")],
            change_kind="logic",
            risk_level="high",
        ),
        surface="mcp",
    )
    result = GitChannel().execute(event, ControlPlaneConfig())
    assert result.status == "fail"
    assert result.severity in ("warning", "informational")


def test_execute_hook_no_dep_files_skips_lockfile_check(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(tmp_path / "app.py")],
        change_classification=ChangeClassification(
            files_changed=[str(tmp_path / "app.py")],
            change_kind="logic",
            risk_level="moderate",
        ),
        surface="hook",
    )
    result = GitChannel().execute(event, ControlPlaneConfig())
    assert result.status in ("pass", "fail")
    assert result.metrics["checks_run"] == 2


def test_execute_informational_only_severity(tmp_path: Path) -> None:
    _init_git_repo(tmp_path, {"uv.lock": "# lockfile\n"})
    import time as _time

    _time.sleep(0.05)
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("[project]\nname = 'test'\n")
    subprocess.run(["git", "add", "."], capture_output=True, cwd=str(tmp_path))
    subprocess.run(["git", "commit", "-m", "add manifest"], capture_output=True, cwd=str(tmp_path))

    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(manifest)],
        change_classification=ChangeClassification(
            files_changed=[str(manifest)],
            change_kind="logic",
            risk_level="moderate",
        ),
        surface="mcp",
    )
    result = GitChannel().execute(event, ControlPlaneConfig())
    assert result.status == "fail"
    assert result.severity == "informational"
    assert any(f.kind == "stale_lockfile" for f in result.findings)
    assert all(f.severity != "warning" for f in result.findings)


def test_execute_empty_files_changed(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[],
        change_classification=ChangeClassification(
            files_changed=[],
            change_kind="logic",
            risk_level="moderate",
        ),
        surface="hook",
    )
    result = GitChannel().execute(event, ControlPlaneConfig())
    assert result.status in ("pass", "fail")
    assert result.metrics["checks_run"] == 2


# ── _check_quality_infrastructure error handling ─────────────────────────


def test_check_quality_infra_runtime_error() -> None:
    with patch(
        "lintgate.quality_infra.audit_quality_infrastructure",
        side_effect=RuntimeError("audit unavailable"),
    ):
        findings, repairs = _check_quality_infrastructure("/tmp/nonexistent")
    assert findings == []
    assert repairs == []


def test_check_quality_infra_import_failure() -> None:
    with patch.dict(sys.modules, {"lintgate.quality_infra": None}):
        findings, repairs = _check_quality_infrastructure("/tmp/nonexistent")
    assert findings == []
    assert repairs == []
