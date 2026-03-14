"""Quality infrastructure audit — checks completeness of CI/badge/config artifacts.

Shared module used by:
- hygiene.py (pre-commit/pre-push agent check)
- git_channel.py (ControlPlane Check 5)
- onboarding_tools.py (getting_started quality check)
- Pre-push hook and CI gate (via CLI entry point)

Each project managed by LintGate should have a complete set of quality
infrastructure artifacts. This module audits that completeness and provides
a CLI entry point for hard enforcement in hooks and CI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Required artifacts ───────────────────────────────────────────────────

# Maps artifact name to relative path from project root.
_REQUIRED_ARTIFACTS: dict[str, str] = {
    "codeclimate": ".codeclimate.yml",
    "sonar_properties": "sonar-project.properties",
    "coveragerc": ".coveragerc",
    "gitleaks": ".gitleaks.toml",
    "workflow_sonarcloud": os.path.join(".github", "workflows", "sonarcloud.yml"),
    "workflow_tests": os.path.join(".github", "workflows", "tests.yml"),
    "workflow_qlty": os.path.join(".github", "workflows", "qlty.yml"),
    "workflow_security": os.path.join(".github", "workflows", "security-lite.yml"),
    "workflow_scorecard": os.path.join(".github", "workflows", "scorecard.yml"),
    "workflow_codeql": os.path.join(".github", "workflows", "codeql.yml"),
    "workflow_quality_gate": os.path.join(".github", "workflows", "quality-infra-gate.yml"),
    "pre_push_hook": os.path.join(".githooks", "pre-push"),
    "qlty_toml": os.path.join(".qlty", "qlty.toml"),
    "dependabot": os.path.join(".github", "dependabot.yml"),
    "security_md": "SECURITY.md",
    "workflow_clusterfuzzlite": os.path.join(".github", "workflows", "cif.yml"),
    "workflow_pypi_publish": os.path.join(".github", "workflows", "pypi-publish.yml"),
    "gate_contract": "gate_contract.yaml",
}

# Badge fingerprints that must appear in the README managed block.
_REQUIRED_BADGE_FINGERPRINTS: tuple[str, ...] = (
    "actions/workflows/tests.yml/badge.svg",
    "actions/workflows/security-lite.yml/badge.svg",
    "metric=alert_status",
    "metric=coverage",
    "metric=security_rating",
    "metric=sqale_rating",
    "metric=reliability_rating",
)

# Managed badge block markers (must match onboarding_tools.py).
_BADGE_BLOCK_START = "<!-- lintgate:quality-badges:start -->"
_BADGE_BLOCK_END = "<!-- lintgate:quality-badges:end -->"

_README_NAMES = ("README.md", "readme.md", "Readme.md", "README.MD")

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.\s]+)")
_PRE_PUSH_GATE_ID_RE = re.compile(r"_should_run\s+([A-Za-z0-9_]+)")
_MATRIX_EXPR_RE = re.compile(r"\${{\s*matrix\.([A-Za-z0-9_-]+)\s*}}")


# ── Result dataclass ─────────────────────────────────────────────────────


@dataclass
class QualityAuditResult:
    """Result of auditing quality infrastructure completeness."""

    complete: bool
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    has_github_remote: bool = False
    badge_count: int = 0
    expected_badge_count: int = len(_REQUIRED_BADGE_FINGERPRINTS)
    badge_fingerprints_ok: bool = False
    gate_contract_errors: list[str] = field(default_factory=list)


# ── Core audit function ──────────────────────────────────────────────────


def audit_quality_infrastructure(project_root: str) -> QualityAuditResult:
    """Audit quality infrastructure completeness for a project.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        QualityAuditResult with completeness status and artifact details.
        For non-git or non-GitHub projects, returns complete=True to avoid
        false blocks.
    """
    root = Path(project_root)

    # Not a git repo → no quality infra expected
    if not _is_git_repo(project_root):
        return QualityAuditResult(complete=True, has_github_remote=False)

    # No GitHub remote → quality infra not applicable
    has_github = _has_github_remote(project_root)
    if not has_github:
        return QualityAuditResult(complete=True, has_github_remote=False)

    # Check each artifact
    present: list[str] = []
    missing: list[str] = []
    for name, rel_path in _REQUIRED_ARTIFACTS.items():
        if (root / rel_path).exists():
            present.append(name)
        else:
            missing.append(name)

    # Check badge fingerprints in README
    badge_count, badge_ok = _check_badge_fingerprints(project_root)

    gate_contract_errors = _check_gate_contract_drift(project_root)

    complete = len(missing) == 0 and badge_ok and not gate_contract_errors

    return QualityAuditResult(
        complete=complete,
        present=present,
        missing=missing,
        has_github_remote=True,
        badge_count=badge_count,
        expected_badge_count=len(_REQUIRED_BADGE_FINGERPRINTS),
        badge_fingerprints_ok=badge_ok,
        gate_contract_errors=gate_contract_errors,
    )


# ── Helper functions ─────────────────────────────────────────────────────


def _is_git_repo(project_root: str) -> bool:
    """Check if the directory is inside a git repository."""
    if (Path(project_root) / ".git").exists():
        return True
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=project_root,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _has_github_remote(project_root: str) -> bool:
    """Check if the git repo has a GitHub remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=project_root,
        )
        if result.returncode != 0 or not result.stdout:
            return False
        return bool(_GITHUB_REMOTE_RE.search(result.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_badge_fingerprints(project_root: str) -> tuple[int, bool]:
    """Check README for badge managed block with required fingerprints.

    Returns:
        (badge_count, all_fingerprints_present)
    """
    root = Path(project_root)
    readme_path = None
    for name in _README_NAMES:
        candidate = root / name
        if candidate.exists():
            readme_path = candidate
            break

    if readme_path is None:
        return 0, False

    try:
        content = readme_path.read_text(errors="ignore")
    except OSError:
        return 0, False

    # Check for managed block
    if _BADGE_BLOCK_START in content and _BADGE_BLOCK_END in content:
        start = content.find(_BADGE_BLOCK_START)
        end = content.find(_BADGE_BLOCK_END, start)
        if end == -1:
            return 0, False
        managed_block = content[start : end + len(_BADGE_BLOCK_END)]
        found = sum(1 for fp in _REQUIRED_BADGE_FINGERPRINTS if fp in managed_block)
        return found, found == len(_REQUIRED_BADGE_FINGERPRINTS)

    # Fallback: check content directly
    found = sum(1 for fp in _REQUIRED_BADGE_FINGERPRINTS if fp in content)
    return found, found == len(_REQUIRED_BADGE_FINGERPRINTS)


def _check_gate_contract_drift(project_root: str) -> list[str]:
    """Validate gate_contract.yaml parity across local/CI/branch-protection.

    This is the authoritative split-brain guard:
    - local_pre_push commands in contract must be present in .githooks/pre-push
    - ci_workflows in contract must exist in the repo
    - required_checks in contract must match main branch protection checks

    Branch-protection parity is fail-closed in CI and best-effort locally.
    """
    errors: list[str] = []
    root = Path(project_root)
    contract_path = root / "gate_contract.yaml"
    pre_push_path = root / ".githooks" / "pre-push"

    if not contract_path.exists():
        return ["gate_contract.yaml is missing"]

    contract = _load_gate_contract(contract_path)
    if not isinstance(contract, dict):
        return ["gate_contract.yaml is invalid or unreadable"]

    required_checks = _contract_string_list(contract.get("required_checks"))
    workflows = _contract_string_list(contract.get("ci_workflows"))
    local_steps = _contract_local_steps(contract.get("local_pre_push"))
    local_ids = _contract_local_ids(contract.get("local_pre_push"))

    if not required_checks:
        errors.append("gate_contract.yaml required_checks is missing or empty")
    if not workflows:
        errors.append("gate_contract.yaml ci_workflows is missing or empty")
    if not local_steps:
        errors.append("gate_contract.yaml local_pre_push is missing or empty")

    for rel in workflows:
        wf = root / rel
        if not wf.exists():
            errors.append(f"Contract workflow missing in repo: {rel}")

    if not pre_push_path.exists():
        errors.append("Missing .githooks/pre-push required by gate contract")
    else:
        pre_push_content = pre_push_path.read_text(errors="ignore")
        for cmd in local_steps:
            if cmd not in pre_push_content:
                errors.append(f"pre-push missing contract command fragment: {cmd}")
        hook_gate_ids = _extract_pre_push_gate_ids(pre_push_content)
        if hook_gate_ids:
            missing_hook_ids = sorted(set(local_ids) - set(hook_gate_ids))
            extra_hook_ids = sorted(set(hook_gate_ids) - set(local_ids))
            if missing_hook_ids:
                errors.append(
                    "pre-push missing contract gate id(s): " + ", ".join(missing_hook_ids)
                )
            if extra_hook_ids:
                errors.append(
                    "pre-push exposes gate id(s) not in gate_contract.yaml: "
                    + ", ".join(extra_hook_ids)
                )

    _check_parity_map(contract, errors)

    declared_checks = _collect_workflow_declared_checks(root, workflows)
    missing_declared = sorted(set(required_checks) - declared_checks)
    if missing_declared:
        errors.append(
            "Contract required check(s) not declared by ci_workflows: "
            + ", ".join(missing_declared)
        )

    remote_checks = _fetch_branch_protection_required_checks(project_root)
    require_remote = _branch_protection_fail_closed()
    if remote_checks is None:
        if require_remote:
            errors.append(
                "Unable to read main branch protection checks via gh api (fail-closed mode)"
            )
    else:
        missing_remote = sorted(set(required_checks) - set(remote_checks))
        extra_remote = sorted(set(remote_checks) - set(required_checks))
        if missing_remote:
            errors.append(
                "Branch protection missing contract required check(s): " + ", ".join(missing_remote)
            )
        if extra_remote:
            errors.append(
                "Branch protection has extra required check(s) not in contract: "
                + ", ".join(extra_remote)
            )

    return errors


def _collect_ci_check_names(parity_map: dict[str, Any]) -> set[str]:
    """Extract all CI check names from parity_map values."""
    ci_check_names: set[str] = set()
    for value in parity_map.values():
        if value is None:
            continue
        if isinstance(value, str):
            ci_check_names.add(value)
        elif isinstance(value, list):
            ci_check_names.update(item for item in value if isinstance(item, str))
        elif isinstance(value, dict):
            ci_check = value.get("ci_check")
            if isinstance(ci_check, str):
                ci_check_names.add(ci_check)
    return ci_check_names


def _check_parity_map(contract: dict[str, Any], errors: list[str]) -> None:
    """Validate parity_map ties local gates to CI checks bidirectionally.

    1. Every required_checks entry must appear as a CI check name in parity_map values.
    2. Every local_pre_push ID must appear as a parity_map key.
    """
    parity_map = contract.get("parity_map")
    if not isinstance(parity_map, dict):
        return

    required_checks = _contract_string_list(contract.get("required_checks"))
    local_ids = [
        entry.get("id") if isinstance(entry, dict) else None
        for entry in (contract.get("local_pre_push") or [])
    ]
    local_ids = [lid for lid in local_ids if isinstance(lid, str) and lid.strip()]

    ci_check_names = _collect_ci_check_names(parity_map)

    for check in required_checks:
        if check not in ci_check_names:
            errors.append(f"parity_map missing CI mapping for required_check: {check}")

    parity_keys = set(parity_map.keys())
    for lid in local_ids:
        if lid not in parity_keys:
            errors.append(f"parity_map missing key for local_pre_push gate: {lid}")


def _branch_protection_fail_closed() -> bool:
    """Whether branch-protection API parity is enforced fail-closed.

    Default is best-effort because GitHub Actions' default GITHUB_TOKEN
    typically cannot read branch protection endpoints. Local pre-push can
    opt into strict mode by exporting:
      LINTGATE_BRANCH_PROTECTION_FAIL_CLOSED=1
    """
    raw = os.getenv("LINTGATE_BRANCH_PROTECTION_FAIL_CLOSED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_gate_contract(contract_path: Path) -> dict[str, Any] | None:
    """Load gate contract YAML as a mapping."""
    try:
        import yaml

        content = yaml.safe_load(contract_path.read_text())
        if isinstance(content, dict):
            return content
    except Exception:
        return None
    return None


def _contract_string_list(value: Any) -> list[str]:
    """Extract a normalized list[str] from contract values."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
    return out


def _contract_local_steps(value: Any) -> list[str]:
    """Extract local pre-push command fragments from contract."""
    if not isinstance(value, list):
        return []
    commands: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            commands.append(entry.strip())
            continue
        if isinstance(entry, dict):
            command = entry.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())
    return commands


def _contract_local_ids(value: Any) -> list[str]:
    """Extract local pre-push gate IDs from contract."""
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for entry in value:
        if isinstance(entry, dict):
            gate_id = entry.get("id")
            if isinstance(gate_id, str) and gate_id.strip():
                ids.append(gate_id.strip())
    return ids


def _extract_pre_push_gate_ids(pre_push_content: str) -> list[str]:
    """Extract explicit gate IDs from .githooks/pre-push.

    The canonical hook surface is `_should_run <gate_id>`.
    """
    return sorted(set(_PRE_PUSH_GATE_ID_RE.findall(pre_push_content)))


def _collect_workflow_declared_checks(
    root: Path,
    workflows: list[str],
) -> set[str]:
    """Collect concrete check names declared by the workflow files."""
    declared: set[str] = set()
    for rel in workflows:
        declared.update(_workflow_declared_checks(root / rel))
    return declared


def _workflow_declared_checks(workflow_path: Path) -> set[str]:
    """Extract emitted check names from a GitHub Actions workflow file."""
    try:
        import yaml

        content = yaml.safe_load(workflow_path.read_text())
    except Exception:
        return set()
    if not isinstance(content, dict):
        return set()
    jobs = content.get("jobs")
    if not isinstance(jobs, dict):
        return set()

    declared: set[str] = set()
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        declared.update(_expand_workflow_job_names(job_id, job))
    return declared


def _expand_workflow_job_names(job_id: str, job: dict[str, Any]) -> set[str]:
    """Expand a job `name:` into concrete emitted check names.

    Supports simple matrix substitutions such as:
      name: Tests (${{ matrix.python-version }})
      strategy.matrix.python-version: ["3.11", "3.12"]
    """
    raw_name = job.get("name")
    name_template = raw_name if isinstance(raw_name, str) and raw_name.strip() else job_id
    names = {name_template}

    matrix = (job.get("strategy") or {}).get("matrix")
    if not isinstance(matrix, dict):
        return names

    axes = _MATRIX_EXPR_RE.findall(name_template)
    if not axes:
        return names

    expanded = [name_template]
    for axis in axes:
        values = _matrix_axis_values(matrix.get(axis))
        if not values:
            continue
        axis_re = re.compile(r"\${{\s*matrix\." + re.escape(axis) + r"\s*}}")
        next_expanded: list[str] = []
        for current in expanded:
            for value in values:
                next_expanded.append(axis_re.sub(value, current))
        expanded = next_expanded or expanded
    return set(expanded)


def _matrix_axis_values(value: Any) -> list[str]:
    """Extract concrete values from a workflow matrix axis."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        values: list[str] = []
        for candidate in re.findall(r"\[[^\]]*\]", value):
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, list):
                values.extend(str(item) for item in parsed)
        return list(dict.fromkeys(values))
    return []


def _fetch_branch_protection_required_checks(project_root: str) -> list[str] | None:
    """Fetch required check contexts from main branch protection.

    Returns None when unavailable (e.g., missing gh auth / non-GitHub repo).
    """
    slug = _github_repo_slug(project_root)
    if not slug:
        return None

    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{slug}/branches/main/protection/required_status_checks",
                "--jq",
                'if (.checks | type) == "array" and (.checks | length) > 0 '
                "then .checks[].context else (.contexts // [])[] end",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=project_root,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    checks = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return checks


def _github_repo_slug(project_root: str) -> str | None:
    """Resolve owner/repo slug from origin remote URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    remote = result.stdout.strip()
    match = _GITHUB_REMOTE_RE.search(remote)
    if not match:
        return None
    owner, repo = match.groups()
    return f"{owner}/{repo}"


# ── CLI entry point ──────────────────────────────────────────────────────


def _cli_main() -> int:
    """CLI entry point for pre-push hook and CI gate.

    Usage: python -m lintgate.quality_infra --enforce /path/to/project
    Returns 0 if complete, 1 if missing artifacts.
    """
    args = sys.argv[1:]

    enforce = "--enforce" in args
    project_root = None
    for arg in args:
        if arg != "--enforce":
            project_root = arg
            break

    if project_root is None:
        project_root = os.getcwd()

    result = audit_quality_infrastructure(project_root)

    if not result.has_github_remote:
        print("[quality-infra] No GitHub remote detected; skipping audit.")
        return 0

    if result.complete:
        print(
            f"[quality-infra] Complete: {len(result.present)} artifacts present, "
            f"{result.badge_count}/{result.expected_badge_count} badge fingerprints OK."
        )
        return 0

    # Report missing items
    print(f"[quality-infra] INCOMPLETE: {len(result.missing)} artifact(s) missing:")
    for name in result.missing:
        rel_path = _REQUIRED_ARTIFACTS.get(name, name)
        print(f"  - {name}: {rel_path}")

    if not result.badge_fingerprints_ok:
        print(
            f"  - badges: {result.badge_count}/{result.expected_badge_count} "
            "fingerprints found in README"
        )
    if result.gate_contract_errors:
        print("  - gate_contract drift:")
        for err in result.gate_contract_errors:
            print(f"      * {err}")

    print()
    print("Fix: run setup_github_quality(path=..., write=True)")

    return 1 if enforce else 0


if __name__ == "__main__":
    sys.exit(_cli_main())
