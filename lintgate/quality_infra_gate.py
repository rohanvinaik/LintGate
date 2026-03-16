"""Gate contract validation, CI workflow parsing, and branch protection checks.

Split from quality_infra.py — gate contract drift detection, workflow
declared-check expansion, and branch-protection parity enforcement.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.\s]+)")
_PRE_PUSH_GATE_ID_RE = re.compile(r"_should_run\s+(\w+)")
_MATRIX_EXPR_RE = re.compile(r"\${{\s*matrix\.([A-Za-z0-9_-]+)\s*}}")

# ── Gate contract validation ──────────────────────────────────────────────


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
    parity_workflows = _contract_string_list(contract.get("parity_workflows")) or workflows
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
    for rel in parity_workflows:
        if rel not in workflows:
            errors.append(f"Contract parity_workflow is not listed in ci_workflows: {rel}")

    parity_contents = ""
    for rel in parity_workflows:
        wf = root / rel
        if wf.exists():
            parity_contents += "\n" + wf.read_text(errors="ignore")

    if not pre_push_path.exists():
        errors.append("Missing .githooks/pre-push required by gate contract")
    else:
        pre_push_content = pre_push_path.read_text(errors="ignore")
        for cmd in local_steps:
            if cmd not in pre_push_content:
                errors.append(f"pre-push missing contract command fragment: {cmd}")
            if cmd not in parity_contents:
                errors.append(f"parity_workflows missing contract command fragment: {cmd}")
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

    declared_checks = _collect_workflow_declared_checks(root, parity_workflows)

    _check_parity_map(contract, declared_checks, errors)

    missing_declared = sorted(set(required_checks) - declared_checks)
    if missing_declared:
        errors.append(
            "Contract required check(s) not declared by parity_workflows: "
            + ", ".join(missing_declared)
        )

    sonar_mode = (
        ((contract.get("tools") or {}).get("sonar") or {}).get("local_mode", "")
        if isinstance(contract.get("tools"), dict)
        else ""
    )
    if "SonarQube Cloud Scan" in required_checks and str(sonar_mode).strip() != "local_scan":
        errors.append("tools.sonar.local_mode must be 'local_scan' for local/CI parity")

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


def _check_parity_map(
    contract: dict[str, Any],
    declared_checks: set[str],
    errors: list[str],
) -> None:
    """Validate parity_map ties local gates to CI checks bidirectionally.

    1. Every required_checks entry must appear as a CI check name in parity_map values.
    2. Every local_pre_push ID must appear as a parity_map key.
    3. Every parity_map CI check must be emitted by parity_workflows.
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

    undeclared_checks = sorted(ci_check_names - declared_checks)
    if declared_checks and undeclared_checks:
        errors.append(
            "parity_map references check(s) not declared by parity_workflows: "
            + ", ".join(undeclared_checks)
        )

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


# ── Contract helpers ──────────────────────────────────────────────────────


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


# ── CI workflow parsing ───────────────────────────────────────────────────


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


# ── Branch protection ─────────────────────────────────────────────────────


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
