"""Phase 4: Git channel tests.

Verifies:
- Channel protocol conformance
- should_run logic
- Git checks: large changes, lockfile freshness, sensitive files
- Graceful handling of non-git directories
- Full channel execute in git repos
- Check 5: quality infrastructure

Utility function tests and branch coverage tests are in test_git_channel_helpers.py.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.channels.git_channel import (
    GitChannel,
    _check_large_changes,
    _check_lockfile_freshness,
    _check_quality_infrastructure,
    _check_sensitive_files,
    _check_working_tree_scope,
    _is_git_repo,
)
from lintgate.controlplane.channel import Channel
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification

# ── Protocol conformance ─────────────────────────────────────────────────


def test_git_channel_conforms_to_protocol() -> None:
    ch = GitChannel()
    assert isinstance(ch, Channel)


def test_git_channel_has_correct_name() -> None:
    assert GitChannel.name == "git"


def test_git_channel_is_not_blocking() -> None:
    assert GitChannel.blocking_capable is False


# ── should_run tests ─────────────────────────────────────────────────────


def test_should_run_on_logic_change() -> None:
    classification = ChangeClassification(
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=classification,
    )
    assert GitChannel().should_run(event, ControlPlaneConfig()) is True


def test_should_not_run_on_none_risk() -> None:
    classification = ChangeClassification(
        change_kind="logic",
        risk_level="none",
    )
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Bash",
        change_classification=classification,
    )
    assert GitChannel().should_run(event, ControlPlaneConfig()) is False


def test_should_not_run_without_classification() -> None:
    event = SupervisionEvent(
        project_root="/tmp",
        tool_name="Edit",
        change_classification=None,
    )
    assert GitChannel().should_run(event, ControlPlaneConfig()) is False


def test_should_run_on_mcp_without_classification() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        change_classification=None,
    )
    assert GitChannel().should_run(event, ControlPlaneConfig()) is True


# ── Non-git directory ────────────────────────────────────────────────────


def test_execute_skips_non_git_directory(tmp_path: Path) -> None:
    """Non-git directory → skip result."""
    classification = ChangeClassification(
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        change_classification=classification,
    )

    channel = GitChannel()
    result = channel.execute(event, ControlPlaneConfig())

    assert result.status == "skip"
    assert result.metrics.get("reason") == "not_a_git_repo"


# ── Lockfile freshness ───────────────────────────────────────────────────


def test_missing_lockfile_detected(tmp_path: Path) -> None:
    """Project with pyproject.toml but no lockfile → finding."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

    findings, repairs = _check_lockfile_freshness(str(tmp_path))

    assert len(findings) == 1
    assert findings[0].kind == "missing_lockfile"
    assert len(repairs) >= 1
    assert "uv lock" in repairs[0].summary


def test_stale_lockfile_detected(tmp_path: Path) -> None:
    """Lockfile older than manifest → stale finding."""
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "uv.lock"

    # Create lockfile first (older)
    lockfile.write_text("# lockfile\n")
    import time

    time.sleep(0.05)
    # Then manifest (newer)
    manifest.write_text("[project]\nname = 'test'\n")

    findings, repairs = _check_lockfile_freshness(str(tmp_path))

    assert len(findings) == 1
    assert findings[0].kind == "stale_lockfile"


def test_fresh_lockfile_no_findings(tmp_path: Path) -> None:
    """Lockfile newer than manifest → no findings."""
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "uv.lock"

    # Create manifest first (older)
    manifest.write_text("[project]\nname = 'test'\n")
    import time

    time.sleep(0.05)
    # Then lockfile (newer)
    lockfile.write_text("# lockfile\n")

    findings, repairs = _check_lockfile_freshness(str(tmp_path))
    assert len(findings) == 0


def test_no_manifest_no_findings(tmp_path: Path) -> None:
    """No pyproject.toml → no findings."""
    findings, repairs = _check_lockfile_freshness(str(tmp_path))
    assert len(findings) == 0


def test_requirements_txt_counts_as_lockfile(tmp_path: Path) -> None:
    """requirements.txt present → no missing lockfile finding."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")

    findings, _ = _check_lockfile_freshness(str(tmp_path))
    # Should not report missing lockfile since requirements.txt exists
    missing = [f for f in findings if f.kind == "missing_lockfile"]
    assert len(missing) == 0


# ── Sensitive files ──────────────────────────────────────────────────────


@patch("lintgate.channels.git_channel.subprocess.run")
def test_sensitive_env_file_detected(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout="?? .env\n",
        stderr="",
        returncode=0,
    )

    findings = _check_sensitive_files("/tmp/project")

    assert len(findings) == 1
    assert findings[0].kind == "sensitive_file"
    assert ".env" in findings[0].message


@patch("lintgate.channels.git_channel.subprocess.run")
def test_no_sensitive_files(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout="M app.py\nM tests/test_app.py\n",
        stderr="",
        returncode=0,
    )

    findings = _check_sensitive_files("/tmp/project")
    assert len(findings) == 0


# ── Large changes ────────────────────────────────────────────────────────


@patch("lintgate.channels.git_channel.subprocess.run")
def test_large_staged_changes_detected(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout=" 5 files changed, 400 insertions(+), 200 deletions(-)\n",
        stderr="",
        returncode=0,
    )

    findings = _check_large_changes("/tmp/project")

    assert len(findings) == 1
    assert findings[0].kind == "large_staged_changes"
    assert "600" in findings[0].message or "400" in findings[0].message


@patch("lintgate.channels.git_channel.subprocess.run")
def test_small_changes_no_finding(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout=" 2 files changed, 10 insertions(+), 5 deletions(-)\n",
        stderr="",
        returncode=0,
    )

    findings = _check_large_changes("/tmp/project")
    assert len(findings) == 0


# ── Git repo detection ───────────────────────────────────────────────────


def test_is_git_repo_with_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert _is_git_repo(str(tmp_path)) is True


def test_is_not_git_repo(tmp_path: Path) -> None:
    assert _is_git_repo(str(tmp_path)) is False


# ── Full channel execute in git repo ─────────────────────────────────────


def test_execute_in_git_repo(tmp_path: Path) -> None:
    """Test execute in a real git repo (created for this test)."""
    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], capture_output=True, cwd=str(tmp_path)
    )
    subprocess.run(["git", "config", "user.name", "Test"], capture_output=True, cwd=str(tmp_path))

    # Create a file and commit it
    (tmp_path / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "app.py"], capture_output=True, cwd=str(tmp_path))
    subprocess.run(["git", "commit", "-m", "init"], capture_output=True, cwd=str(tmp_path))

    classification = ChangeClassification(
        files_changed=[str(tmp_path / "app.py")],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=classification.files_changed,
        change_classification=classification,
    )

    channel = GitChannel()
    result = channel.execute(event, ControlPlaneConfig())

    assert isinstance(result, ChannelResult)
    assert result.channel == "git"
    # Should not skip (we're in a git repo)
    assert result.status != "skip"
    assert result.duration_ms >= 0


# ── Check 5: Quality infrastructure ──────────────────────────────────────


def test_check5_no_github_remote(tmp_path: Path) -> None:
    """No GitHub remote → no findings from Check 5."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    findings, repairs = _check_quality_infrastructure(str(tmp_path))
    assert len(findings) == 0
    assert len(repairs) == 0


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_check5_missing_artifacts(mock_remote: object, tmp_path: Path) -> None:
    """GitHub project missing artifacts → warning finding + repair."""
    (tmp_path / ".git").mkdir()
    findings, repairs = _check_quality_infrastructure(str(tmp_path))
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].kind == "missing_quality_infra"
    assert len(repairs) == 1
    assert repairs[0].safe is True
    assert repairs[0].channel == "git"


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
@patch("lintgate.quality_infra.audit_quality_infrastructure")
def test_check5_complete(mock_audit: MagicMock, mock_remote: object, tmp_path: Path) -> None:
    """Complete quality infrastructure → no findings."""
    from lintgate.quality_infra import QualityAuditResult

    mock_audit.return_value = QualityAuditResult(
        complete=True,
        present=["all"],
        missing=[],
        has_github_remote=True,
        badge_fingerprints_ok=True,
    )
    (tmp_path / ".git").mkdir()
    findings, repairs = _check_quality_infrastructure(str(tmp_path))
    assert len(findings) == 0
    assert len(repairs) == 0


def test_check5_skipped_on_hooks(tmp_path: Path) -> None:
    """Check 5 not run during hook events (is_hook=True in execute)."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], capture_output=True, cwd=str(tmp_path)
    )
    subprocess.run(["git", "config", "user.name", "Test"], capture_output=True, cwd=str(tmp_path))
    (tmp_path / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "app.py"], capture_output=True, cwd=str(tmp_path))
    subprocess.run(["git", "commit", "-m", "init"], capture_output=True, cwd=str(tmp_path))

    classification = ChangeClassification(
        files_changed=[str(tmp_path / "app.py")],
        change_kind="logic",
        risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=classification.files_changed,
        change_classification=classification,
        surface="hook",  # Hook event → Check 5 should be skipped
    )

    channel = GitChannel()
    result = channel.execute(event, ControlPlaneConfig())
    # quality_infra_findings should be 0 since Check 5 is skipped on hooks
    assert result.metrics.get("quality_infra_findings", 0) == 0


# ── Check 0: Working tree scope advisory ────────────────────────────────


class TestWorkingTreeScope:
    """Tests for _check_working_tree_scope advisory."""

    def test_advisory_fires_above_threshold(self) -> None:
        """15 files (10 modified + 5 untracked) → finding emitted."""
        modified_lines = [f" M file{i}.py" for i in range(10)]
        untracked_lines = [f"?? newfile{i}.py" for i in range(5)]
        porcelain = "\n".join(modified_lines + untracked_lines) + "\n"

        with patch("lintgate.channels.git_channel.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
            findings = _check_working_tree_scope("/fake/repo")

        assert len(findings) == 1
        assert findings[0].kind == "wide_working_tree"
        assert findings[0].severity == "informational"
        assert findings[0].evidence["modified_count"] == 10
        assert findings[0].evidence["untracked_count"] == 5
        assert findings[0].evidence["total"] == 15

    def test_under_threshold_no_finding(self) -> None:
        """5 files → no finding."""
        porcelain = "\n".join(f" M file{i}.py" for i in range(5)) + "\n"

        with patch("lintgate.channels.git_channel.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
            findings = _check_working_tree_scope("/fake/repo")

        assert len(findings) == 0

    def test_skipped_on_hook(self, tmp_path: Path) -> None:
        """surface='hook' → Check 0 not run."""
        # Create a git repo so the channel doesn't skip entirely
        (tmp_path / ".git").mkdir()

        classification = ChangeClassification(
            files_changed=["test.py"],
            change_kind="logic",
            risk_level="moderate",
        )
        event = SupervisionEvent(
            project_root=str(tmp_path),
            tool_name="Edit",
            files_changed=classification.files_changed,
            change_classification=classification,
            surface="hook",
        )

        with patch(
            "lintgate.channels.git_channel._check_working_tree_scope"
        ) as mock_scope:
            channel = GitChannel()
            channel.execute(event, ControlPlaneConfig())
            mock_scope.assert_not_called()
