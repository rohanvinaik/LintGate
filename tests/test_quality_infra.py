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
    _check_gate_contract_drift,
    _check_parity_map,
    _cli_main,
    _collect_workflow_declared_checks,
    _contract_local_ids,
    _contract_local_steps,
    _contract_string_list,
    _extract_pre_push_gate_ids,
    _fetch_branch_protection_required_checks,
    _github_repo_slug,
    _has_github_remote,
    _is_git_repo,
    _load_gate_contract,
    _matrix_axis_values,
    _workflow_declared_checks,
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


@patch("lintgate.quality_infra._check_gate_contract_drift", return_value=[])
@patch("lintgate.quality_infra._has_github_remote", return_value=True)
def test_audit_all_present(mock_remote: object, mock_contract: object, tmp_path: Path) -> None:
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


# ── Gate contract drift checks ──────────────────────────────────────────


def _write_valid_gate_contract(tmp_path: Path) -> None:
    (tmp_path / "gate_contract.yaml").write_text(
        """
version: "1.0"
required_checks:
  - "Tests (3.11)"
  - "Tests (3.12)"
  - "Qlty"
  - "SonarQube Cloud Scan"
ci_workflows:
  - ".github/workflows/tests.yml"
  - ".github/workflows/qlty.yml"
  - ".github/workflows/sonarcloud.yml"
  - ".github/workflows/quality-infra-gate.yml"
local_pre_push:
  - command: "python -m lintgate.quality_infra --enforce"
  - command: "qlty check --all"
"""
    )


def _write_contract_workflows(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "tests.yml").write_text(
        """
name: Tests
on: push
jobs:
  tests:
    name: Tests (${{ matrix.python-version }})
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
    )
    (workflow_dir / "qlty.yml").write_text(
        """
name: Qlty Analysis
on: push
jobs:
  qlty:
    name: Qlty
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
    )
    (workflow_dir / "sonarcloud.yml").write_text(
        """
name: Sonar
on: push
jobs:
  sonarcloud:
    name: SonarQube Cloud Scan
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
    )
    (workflow_dir / "quality-infra-gate.yml").write_text(
        """
name: Quality Infra
on: push
jobs:
  gate:
    name: Quality Infrastructure Gate
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
    )


def test_gate_contract_drift_none_when_all_parity_checks_pass(tmp_path: Path) -> None:
    _write_valid_gate_contract(tmp_path)
    _write_contract_workflows(tmp_path)
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text(
        "python -m lintgate.quality_infra --enforce .\nqlty check --all\n"
    )

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=[
            "Tests (3.11)",
            "Tests (3.12)",
            "Qlty",
            "SonarQube Cloud Scan",
        ],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert errors == []


def test_gate_contract_drift_detects_missing_pre_push_command(tmp_path: Path) -> None:
    _write_valid_gate_contract(tmp_path)
    _write_contract_workflows(tmp_path)
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text("python -m lintgate.quality_infra --enforce .\n")

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=[
            "Tests (3.11)",
            "Tests (3.12)",
            "Qlty",
            "SonarQube Cloud Scan",
        ],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any("pre-push missing contract command fragment: qlty check --all" in e for e in errors)


def test_gate_contract_drift_detects_branch_protection_mismatch(tmp_path: Path) -> None:
    _write_valid_gate_contract(tmp_path)
    _write_contract_workflows(tmp_path)
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text(
        "python -m lintgate.quality_infra --enforce .\nqlty check --all\n"
    )

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=["Tests (3.11)", "Tests (3.12)", "Qlty"],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any("Branch protection missing contract required check(s)" in e for e in errors)


def test_gate_contract_drift_best_effort_when_remote_unavailable_by_default(
    tmp_path: Path,
) -> None:
    _write_valid_gate_contract(tmp_path)
    _write_contract_workflows(tmp_path)
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text(
        "python -m lintgate.quality_infra --enforce .\nqlty check --all\n"
    )

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=None,
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert not any("Unable to read main branch protection checks via gh api" in e for e in errors)


def test_gate_contract_drift_fails_closed_when_env_enabled(tmp_path: Path) -> None:
    _write_valid_gate_contract(tmp_path)
    _write_contract_workflows(tmp_path)
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text(
        "python -m lintgate.quality_infra --enforce .\nqlty check --all\n"
    )

    with (
        patch(
            "lintgate.quality_infra._fetch_branch_protection_required_checks",
            return_value=None,
        ),
        patch.dict("os.environ", {"LINTGATE_BRANCH_PROTECTION_FAIL_CLOSED": "1"}),
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any("Unable to read main branch protection checks via gh api" in e for e in errors)


def test_gate_contract_drift_detects_empty_sections_and_missing_pre_push(
    tmp_path: Path,
) -> None:
    (tmp_path / "gate_contract.yaml").write_text(
        """
version: "1.0"
required_checks: []
ci_workflows: []
local_pre_push: []
"""
    )

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=[],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any("required_checks is missing or empty" in e for e in errors)
    assert any("ci_workflows is missing or empty" in e for e in errors)
    assert any("local_pre_push is missing or empty" in e for e in errors)
    assert any("Missing .githooks/pre-push required by gate contract" in e for e in errors)


def test_gate_contract_drift_detects_missing_workflow_file(tmp_path: Path) -> None:
    (tmp_path / "gate_contract.yaml").write_text(
        """
version: "1.0"
required_checks:
  - "Tests (3.11)"
ci_workflows:
  - ".github/workflows/tests.yml"
local_pre_push:
  - command: "python -m lintgate.quality_infra --enforce"
"""
    )
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text("python -m lintgate.quality_infra --enforce\n")

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=["Tests (3.11)"],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any(
        "Contract workflow missing in repo: .github/workflows/tests.yml" in e for e in errors
    )


def test_gate_contract_drift_detects_extra_remote_required_checks(
    tmp_path: Path,
) -> None:
    _write_valid_gate_contract(tmp_path)
    _write_contract_workflows(tmp_path)
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text(
        "python -m lintgate.quality_infra --enforce .\nqlty check --all\n"
    )

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=[
            "Tests (3.11)",
            "Tests (3.12)",
            "Qlty",
            "SonarQube Cloud Scan",
            "Extra Check",
        ],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any("extra required check(s) not in contract: Extra Check" in e for e in errors)


def test_gate_contract_drift_detects_required_check_missing_from_workflows(
    tmp_path: Path,
) -> None:
    _write_valid_gate_contract(tmp_path)
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "tests.yml").write_text(
        "name: Tests\non: push\njobs:\n  tests:\n    name: Tests (3.11)\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    )
    (workflow_dir / "qlty.yml").write_text(
        "name: Qlty Analysis\non: push\njobs:\n  qlty:\n    name: Qlty\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    )
    (workflow_dir / "sonarcloud.yml").write_text(
        "name: Sonar\non: push\njobs:\n  sonarcloud:\n    name: SonarQube Cloud Scan\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    )
    (workflow_dir / "quality-infra-gate.yml").write_text(
        "name: Quality Infra\non: push\njobs:\n  gate:\n    name: Quality Infrastructure Gate\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    )
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "pre-push").write_text(
        "python -m lintgate.quality_infra --enforce .\nqlty check --all\n"
    )

    with patch(
        "lintgate.quality_infra._fetch_branch_protection_required_checks",
        return_value=[
            "Tests (3.11)",
            "Tests (3.12)",
            "Qlty",
            "SonarQube Cloud Scan",
        ],
    ):
        errors = _check_gate_contract_drift(str(tmp_path))

    assert any(
        "Contract required check(s) not declared by ci_workflows: Tests (3.12)" in e
        for e in errors
    )


# ── Contract helper coverage ────────────────────────────────────────────


def test_load_gate_contract_returns_none_on_read_error(tmp_path: Path) -> None:
    contract = tmp_path / "gate_contract.yaml"
    contract.write_text("required_checks: []\n")

    with patch("pathlib.Path.read_text", side_effect=OSError("boom")):
        assert _load_gate_contract(contract) is None


def test_contract_string_list_non_list_returns_empty() -> None:
    assert _contract_string_list("not-a-list") == []


def test_contract_local_steps_handles_non_list_and_string_entries() -> None:
    assert _contract_local_steps("not-a-list") == []
    assert _contract_local_steps(["qlty check --all"]) == ["qlty check --all"]


def test_contract_local_ids_extracts_ids_only() -> None:
    assert _contract_local_ids("not-a-list") == []
    assert _contract_local_ids(
        [
            {"id": "qlty", "command": "qlty check --all"},
            {"id": "tests"},
            {"command": "pytest"},
            "not-a-dict",
        ]
    ) == ["qlty", "tests"]


def test_extract_pre_push_gate_ids_parses_should_run_blocks() -> None:
    content = """
if _should_run qlty; then
  qlty check --all
fi
if _should_run tests && [ -d tests ]; then
  python -m pytest
fi
"""
    assert _extract_pre_push_gate_ids(content) == ["qlty", "tests"]


def test_matrix_axis_values_expand_string_expression() -> None:
    raw = '${{ fromJSON(github.event_name == "pull_request" && \'["3.11", "3.12"]\' || \'["3.12"]\') }}'
    assert _matrix_axis_values(raw) == ["3.11", "3.12"]


def test_workflow_declared_checks_expands_matrix_names(tmp_path: Path) -> None:
    workflow = tmp_path / "tests.yml"
    workflow.write_text(
        """
name: Tests
on: push
jobs:
  tests:
    name: Tests (${{ matrix.python-version }})
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
    )
    assert _workflow_declared_checks(workflow) == {"Tests (3.11)", "Tests (3.12)"}


def test_collect_workflow_declared_checks_unions_all_workflows(tmp_path: Path) -> None:
    _write_contract_workflows(tmp_path)
    checks = _collect_workflow_declared_checks(
        tmp_path,
        [
            ".github/workflows/tests.yml",
            ".github/workflows/qlty.yml",
            ".github/workflows/sonarcloud.yml",
        ],
    )
    assert {"Tests (3.11)", "Tests (3.12)", "Qlty", "SonarQube Cloud Scan"} <= checks


# ── Branch protection fetch helper coverage ─────────────────────────────


def test_github_repo_slug_handles_timeout(tmp_path: Path) -> None:
    with patch(
        "lintgate.quality_infra.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 3),
    ):
        assert _github_repo_slug(str(tmp_path)) is None


def test_github_repo_slug_nonzero_and_non_github_remote(tmp_path: Path) -> None:
    nonzero = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with patch("lintgate.quality_infra.subprocess.run", return_value=nonzero):
        assert _github_repo_slug(str(tmp_path)) is None

    no_match = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="git@gitlab.com:user/repo.git\n",
        stderr="",
    )
    with patch("lintgate.quality_infra.subprocess.run", return_value=no_match):
        assert _github_repo_slug(str(tmp_path)) is None


def test_github_repo_slug_success(tmp_path: Path) -> None:
    ok = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="git@github.com:owner/repo.git\n",
        stderr="",
    )
    with patch("lintgate.quality_infra.subprocess.run", return_value=ok):
        assert _github_repo_slug(str(tmp_path)) == "owner/repo"


def test_fetch_branch_protection_required_checks_paths(tmp_path: Path) -> None:
    with patch("lintgate.quality_infra._github_repo_slug", return_value=None):
        assert _fetch_branch_protection_required_checks(str(tmp_path)) is None

    with (
        patch("lintgate.quality_infra._github_repo_slug", return_value="owner/repo"),
        patch(
            "lintgate.quality_infra.subprocess.run",
            side_effect=subprocess.TimeoutExpired("gh", 8),
        ),
    ):
        assert _fetch_branch_protection_required_checks(str(tmp_path)) is None

    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="x")
    with (
        patch("lintgate.quality_infra._github_repo_slug", return_value="owner/repo"),
        patch("lintgate.quality_infra.subprocess.run", return_value=failed),
    ):
        assert _fetch_branch_protection_required_checks(str(tmp_path)) is None

    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="A\n\nB\n", stderr="")
    with (
        patch("lintgate.quality_infra._github_repo_slug", return_value="owner/repo"),
        patch("lintgate.quality_infra.subprocess.run", return_value=ok),
    ):
        assert _fetch_branch_protection_required_checks(str(tmp_path)) == ["A", "B"]


# ── Artifact count consistency ───────────────────────────────────────────


def test_artifact_count_is_18() -> None:
    """Verify the artifact checklist has exactly 18 items."""
    assert len(_REQUIRED_ARTIFACTS) == 18


def test_badge_fingerprint_count_is_7() -> None:
    """Verify badge fingerprints match the 7 service badges."""
    assert len(_REQUIRED_BADGE_FINGERPRINTS) == 7


# ── _is_git_repo exception handling (lines 146-147) ─────────────────────


def test_is_git_repo_subprocess_timeout(tmp_path: Path) -> None:
    """_is_git_repo returns False when subprocess times out (line 146)."""
    # No .git dir, so it falls through to subprocess; mock that to timeout
    with patch(
        "lintgate.quality_infra.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 2),
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
        "lintgate.quality_infra.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 5),
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


# ── Parity map validation ─────────────────────────────────────────────


def test_parity_map_valid_passes() -> None:
    """Valid parity_map with all required_checks and local_pre_push IDs → no errors."""
    contract = {
        "required_checks": ["Tests (3.11)", "Tests (3.12)", "Qlty", "SonarQube Cloud Scan"],
        "local_pre_push": [
            {"id": "quality_infra", "command": "python -m lintgate.quality_infra --enforce"},
            {"id": "qlty", "command": "qlty check --all"},
            {"id": "gitleaks", "command": "gitleaks detect"},
            {"id": "tests", "command": "python -m pytest"},
            {"id": "symbol_gate", "command": "python -m lintgate.symbol_gate_runner"},
            {"id": "pip_audit", "command": "pip-audit"},
            {"id": "sonar"},
        ],
        "parity_map": {
            "quality_infra": None,
            "qlty": "Qlty",
            "gitleaks": None,
            "tests": ["Tests (3.11)", "Tests (3.12)"],
            "symbol_gate": None,
            "pip_audit": None,
            "sonar": {"ci_check": "SonarQube Cloud Scan", "local_mode": "ci_only"},
        },
    }
    errors: list[str] = []
    _check_parity_map(contract, errors)
    assert errors == []


def test_parity_map_missing_required_check_fails() -> None:
    """required_check not in parity_map values → error."""
    contract = {
        "required_checks": ["Tests (3.11)", "Qlty", "SonarQube Cloud Scan"],
        "local_pre_push": [
            {"id": "qlty", "command": "qlty check --all"},
        ],
        "parity_map": {
            "qlty": "Qlty",
            # Missing Tests (3.11) and SonarQube Cloud Scan mappings
        },
    }
    errors: list[str] = []
    _check_parity_map(contract, errors)
    assert any(
        "parity_map missing CI mapping for required_check: Tests (3.11)" in e for e in errors
    )
    assert any(
        "parity_map missing CI mapping for required_check: SonarQube Cloud Scan" in e
        for e in errors
    )


def test_parity_map_missing_local_pre_push_key_fails() -> None:
    """local_pre_push ID not in parity_map keys → error."""
    contract = {
        "required_checks": ["Qlty"],
        "local_pre_push": [
            {"id": "qlty", "command": "qlty check --all"},
            {"id": "gitleaks", "command": "gitleaks detect"},
        ],
        "parity_map": {
            "qlty": "Qlty",
            # Missing gitleaks key
        },
    }
    errors: list[str] = []
    _check_parity_map(contract, errors)
    assert any("parity_map missing key for local_pre_push gate: gitleaks" in e for e in errors)


def test_parity_map_absent_skips_validation() -> None:
    """No parity_map in contract → no errors (backwards compatible)."""
    contract = {
        "required_checks": ["Tests"],
        "local_pre_push": [{"id": "tests", "command": "pytest"}],
    }
    errors: list[str] = []
    _check_parity_map(contract, errors)
    assert errors == []


def test_parity_map_not_a_dict_skips_validation() -> None:
    """parity_map is not a dict → no errors (graceful degradation)."""
    contract = {
        "required_checks": ["Tests"],
        "local_pre_push": [{"id": "tests", "command": "pytest"}],
        "parity_map": "invalid",
    }
    errors: list[str] = []
    _check_parity_map(contract, errors)
    assert errors == []
