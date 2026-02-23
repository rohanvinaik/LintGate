"""Tests for the quality infrastructure audit module."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.quality_infra import (
    _REQUIRED_ARTIFACTS,
    _REQUIRED_BADGE_FINGERPRINTS,
    QualityAuditResult,
    _check_badge_fingerprints,
    _cli_main,
    _has_github_remote,
    _is_git_repo,
    audit_quality_infrastructure,
)

# ── Non-git projects ─────────────────────────────────────────────────────


def test_audit_no_git_dir(tmp_path: Path) -> None:
    """Non-git directory returns complete=True, no GitHub remote."""
    result = audit_quality_infrastructure(str(tmp_path))
    assert isinstance(result, QualityAuditResult)
    assert result.complete is True
    assert result.has_github_remote is False
    assert result.missing == []


def test_audit_git_no_remote(tmp_path: Path) -> None:
    """Git repo without GitHub remote returns complete=True."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    result = audit_quality_infrastructure(str(tmp_path))
    assert result.complete is True
    assert result.has_github_remote is False


# ── GitHub projects ──────────────────────────────────────────────────────


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_audit_all_present(mock_remote: object, tmp_path: Path) -> None:
    """All artifacts present + badges → complete=True."""
    # Create .git dir
    (tmp_path / ".git").mkdir()

    # Create all required artifacts
    for _name, rel_path in _REQUIRED_ARTIFACTS.items():
        full_path = tmp_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("placeholder\n")

    # Create README with badge block containing all fingerprints
    badge_lines = []
    for fp in _REQUIRED_BADGE_FINGERPRINTS:
        badge_lines.append(f"[![badge]({fp})](link)")
    badge_block = (
        "# My Project\n\n"
        "<!-- lintgate:quality-badges:start -->\n"
        + "\n".join(badge_lines)
        + "\n<!-- lintgate:quality-badges:end -->\n"
    )
    (tmp_path / "README.md").write_text(badge_block)

    result = audit_quality_infrastructure(str(tmp_path))
    assert result.complete is True
    assert result.has_github_remote is True
    assert len(result.missing) == 0
    assert result.badge_fingerprints_ok is True
    assert result.badge_count == len(_REQUIRED_BADGE_FINGERPRINTS)


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_audit_missing_artifacts(mock_remote: object, tmp_path: Path) -> None:
    """Some artifacts missing → complete=False, correct missing list."""
    (tmp_path / ".git").mkdir()

    # Create only a few artifacts
    (tmp_path / ".codeclimate.yml").write_text("v: 1\n")
    (tmp_path / "sonar-project.properties").write_text("key=val\n")

    # No README or badge block
    result = audit_quality_infrastructure(str(tmp_path))
    assert result.complete is False
    assert result.has_github_remote is True
    assert len(result.missing) > 0
    assert "codeclimate" not in result.missing
    assert "sonar_properties" not in result.missing
    assert "workflow_tests" in result.missing
    assert result.badge_fingerprints_ok is False


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_audit_badges_missing_fingerprints(mock_remote: object, tmp_path: Path) -> None:
    """README exists but badge block has incomplete fingerprints."""
    (tmp_path / ".git").mkdir()

    # Create all artifact files
    for _name, rel_path in _REQUIRED_ARTIFACTS.items():
        full_path = tmp_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("placeholder\n")

    # README with incomplete badge block (only 2 fingerprints)
    badge_block = (
        "# My Project\n\n"
        "<!-- lintgate:quality-badges:start -->\n"
        "[![Tests](actions/workflows/tests.yml/badge.svg)](link)\n"
        "[![Security](actions/workflows/security-lite.yml/badge.svg)](link)\n"
        "<!-- lintgate:quality-badges:end -->\n"
    )
    (tmp_path / "README.md").write_text(badge_block)

    result = audit_quality_infrastructure(str(tmp_path))
    assert result.complete is False  # Incomplete badges
    assert result.badge_fingerprints_ok is False
    assert result.badge_count == 2


# ── Badge fingerprint checks ────────────────────────────────────────────


def test_badge_check_no_readme(tmp_path: Path) -> None:
    """No README → 0 badges, not OK."""
    count, ok = _check_badge_fingerprints(str(tmp_path))
    assert count == 0
    assert ok is False


def test_badge_check_empty_readme(tmp_path: Path) -> None:
    """Empty README → 0 badges, not OK."""
    (tmp_path / "README.md").write_text("")
    count, ok = _check_badge_fingerprints(str(tmp_path))
    assert count == 0
    assert ok is False


def test_badge_check_complete(tmp_path: Path) -> None:
    """README with all fingerprints in managed block → OK."""
    lines = ["<!-- lintgate:quality-badges:start -->"]
    for fp in _REQUIRED_BADGE_FINGERPRINTS:
        lines.append(f"[![badge]({fp})](link)")
    lines.append("<!-- lintgate:quality-badges:end -->")
    (tmp_path / "README.md").write_text("\n".join(lines))

    count, ok = _check_badge_fingerprints(str(tmp_path))
    assert count == len(_REQUIRED_BADGE_FINGERPRINTS)
    assert ok is True


# ── CLI entry point ──────────────────────────────────────────────────────


def test_cli_no_git(tmp_path: Path) -> None:
    """CLI returns 0 for non-git directory."""
    with patch("sys.argv", ["quality_infra", "--enforce", str(tmp_path)]):
        exit_code = _cli_main()
    assert exit_code == 0


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_cli_enforce_missing(mock_remote: object, tmp_path: Path) -> None:
    """CLI returns 1 when --enforce and artifacts missing."""
    (tmp_path / ".git").mkdir()
    with patch("sys.argv", ["quality_infra", "--enforce", str(tmp_path)]):
        exit_code = _cli_main()
    assert exit_code == 1


@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_cli_no_enforce_missing(mock_remote: object, tmp_path: Path) -> None:
    """CLI returns 0 without --enforce even when artifacts missing."""
    (tmp_path / ".git").mkdir()
    with patch("sys.argv", ["quality_infra", str(tmp_path)]):
        exit_code = _cli_main()
    assert exit_code == 0


# ── Artifact count consistency ───────────────────────────────────────────


def test_artifact_count_is_15() -> None:
    """Verify the artifact checklist has exactly 15 items."""
    assert len(_REQUIRED_ARTIFACTS) == 15


def test_badge_fingerprint_count_is_8() -> None:
    """Verify badge fingerprints match the 8 badges."""
    assert len(_REQUIRED_BADGE_FINGERPRINTS) == 8


# ── _is_git_repo exception handling (lines 146-147) ─────────────────────


def test_is_git_repo_subprocess_timeout(tmp_path: Path) -> None:
    """_is_git_repo returns False when subprocess times out (line 146)."""
    # No .git dir, so it falls through to subprocess; mock that to timeout
    with patch(
        "lintgate.quality_infra.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 2)
    ):
        assert _is_git_repo(str(tmp_path)) is False


def test_is_git_repo_file_not_found(tmp_path: Path) -> None:
    """_is_git_repo returns False when git binary not found (line 146)."""
    with patch("lintgate.quality_infra.subprocess.run", side_effect=FileNotFoundError("git")):
        assert _is_git_repo(str(tmp_path)) is False


def test_is_git_repo_os_error(tmp_path: Path) -> None:
    """_is_git_repo returns False on generic OSError (line 147)."""
    with patch("lintgate.quality_infra.subprocess.run", side_effect=OSError("disk error")):
        assert _is_git_repo(str(tmp_path)) is False


# ── _has_github_remote branch coverage (lines 160-164) ──────────────────


def test_has_github_remote_nonzero_returncode(tmp_path: Path) -> None:
    """_has_github_remote returns False when git remote -v fails (branch 160,162)."""
    mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with patch("lintgate.quality_infra.subprocess.run", return_value=mock_result):
        assert _has_github_remote(str(tmp_path)) is False


def test_has_github_remote_empty_stdout(tmp_path: Path) -> None:
    """_has_github_remote returns False when stdout is empty (branch 160,162)."""
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("lintgate.quality_infra.subprocess.run", return_value=mock_result):
        assert _has_github_remote(str(tmp_path)) is False


def test_has_github_remote_no_github_url(tmp_path: Path) -> None:
    """_has_github_remote returns False when remote is not GitHub (line 162)."""
    mock_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="origin\tgit@gitlab.com:user/repo.git (fetch)\n",
        stderr="",
    )
    with patch("lintgate.quality_infra.subprocess.run", return_value=mock_result):
        assert _has_github_remote(str(tmp_path)) is False


def test_has_github_remote_with_github_url(tmp_path: Path) -> None:
    """_has_github_remote returns True when remote is GitHub."""
    mock_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="origin\tgit@github.com:user/repo.git (fetch)\n",
        stderr="",
    )
    with patch("lintgate.quality_infra.subprocess.run", return_value=mock_result):
        assert _has_github_remote(str(tmp_path)) is True


def test_has_github_remote_timeout(tmp_path: Path) -> None:
    """_has_github_remote returns False on timeout (lines 163-164)."""
    with patch(
        "lintgate.quality_infra.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)
    ):
        assert _has_github_remote(str(tmp_path)) is False


def test_has_github_remote_os_error(tmp_path: Path) -> None:
    """_has_github_remote returns False on OSError (lines 163-164)."""
    with patch("lintgate.quality_infra.subprocess.run", side_effect=OSError("nope")):
        assert _has_github_remote(str(tmp_path)) is False


# ── _check_badge_fingerprints edge cases (lines 186-187, 193-194) ───────


def test_badge_check_readme_oserror(tmp_path: Path) -> None:
    """_check_badge_fingerprints returns (0, False) on OSError reading README (lines 186-187)."""
    readme = tmp_path / "README.md"
    readme.write_text("content")
    with patch("pathlib.Path.read_text", side_effect=OSError("permission denied")):
        count, ok = _check_badge_fingerprints(str(tmp_path))
    assert count == 0
    assert ok is False


def test_badge_check_end_marker_before_start(tmp_path: Path) -> None:
    """_check_badge_fingerprints returns (0, False) when end marker not found after start (branch 193,194).

    This covers the edge case where _BADGE_BLOCK_START and _BADGE_BLOCK_END
    both exist in content (so the outer `if` passes) but `content.find(_BADGE_BLOCK_END, start)`
    returns -1 because end appears only before start.
    """
    # Construct content where end marker appears before start marker only
    content = (
        "<!-- lintgate:quality-badges:end -->\n"
        "some text\n"
        "<!-- lintgate:quality-badges:start -->\n"
        "badges here\n"
    )
    (tmp_path / "README.md").write_text(content)
    count, ok = _check_badge_fingerprints(str(tmp_path))
    assert count == 0
    assert ok is False


# ── _cli_main branch coverage (lines 222-236) ───────────────────────────


def test_cli_no_args_uses_cwd(tmp_path: Path) -> None:
    """_cli_main falls back to cwd when no path argument given (branch 222,223)."""
    with (
        patch("sys.argv", ["quality_infra"]),
        patch("os.getcwd", return_value=str(tmp_path)),
        patch(
            "lintgate.quality_infra.audit_quality_infrastructure",
            return_value=QualityAuditResult(complete=True, has_github_remote=False),
        ) as mock_audit,
    ):
        exit_code = _cli_main()
    assert exit_code == 0
    mock_audit.assert_called_once_with(str(tmp_path))


def test_cli_enforce_only_uses_cwd(tmp_path: Path) -> None:
    """_cli_main with only --enforce falls back to cwd (branch 217,222 + 222,223)."""
    with (
        patch("sys.argv", ["quality_infra", "--enforce"]),
        patch("os.getcwd", return_value=str(tmp_path)),
        patch(
            "lintgate.quality_infra.audit_quality_infrastructure",
            return_value=QualityAuditResult(complete=True, has_github_remote=False),
        ) as mock_audit,
    ):
        exit_code = _cli_main()
    assert exit_code == 0
    mock_audit.assert_called_once_with(str(tmp_path))


def test_cli_complete_github_project(tmp_path: Path) -> None:
    """_cli_main prints success message when audit is complete (branch 231,232)."""
    with (
        patch("sys.argv", ["quality_infra", "--enforce", str(tmp_path)]),
        patch(
            "lintgate.quality_infra.audit_quality_infrastructure",
            return_value=QualityAuditResult(
                complete=True,
                has_github_remote=True,
                present=["a", "b"],
                badge_count=8,
                expected_badge_count=8,
                badge_fingerprints_ok=True,
            ),
        ),
    ):
        exit_code = _cli_main()
    assert exit_code == 0
