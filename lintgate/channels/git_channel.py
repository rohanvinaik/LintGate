"""Git channel — supervision for git hygiene.

Checks:
1. Large uncommitted changes (>500 lines staged)
2. Lockfile-manifest mismatch (pyproject.toml newer than uv.lock)
3. Untracked sensitive files (.env, credentials)
4. Secrets in staged diffs (API keys, tokens, private keys, connection strings)
5. Quality infrastructure completeness (CI, badges, configs)

Advisory for checks 1-4. Check 5 escalates to warning severity when
quality infrastructure is incomplete (CI quality-infra-gate would fail).
All checks use subprocess git commands with timeout protection.

Implementation split across sub-modules:
- _git_helpers.py: git subprocess helpers, working tree context collection
- _git_secrets.py: secrets detection patterns and staged diff scanning
- _git_checks.py: individual check functions (working tree, large changes,
  lockfile, sensitive files, quality infrastructure)
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)

if TYPE_CHECKING:
    from lintgate.types import LintIssue

# ── Sub-module imports ────────────────────────────────────────────────────
# Imported here and re-exported for backward compatibility. All external
# code that does ``from lintgate.channels.git_channel import X`` continues
# to work without changes.
from ._git_checks import (  # noqa: F401 — re-exports
    _check_large_changes,
    _check_lockfile_freshness,
    _check_quality_infrastructure,
    _check_sensitive_files,
    _check_working_tree_scope,
)
from ._git_helpers import (  # noqa: F401 — re-exports
    _collect_branch_name,
    _collect_file_status,
    _collect_loc_delta,
    _is_git_repo,
    _parse_diff_stat_totals,
    classify_finding_scope,
    collect_working_tree_context,
)
from ._git_secrets import (  # noqa: F401 — re-exports
    _SECRET_PATTERNS,
    _check_diff_secrets,
    _iter_diff_additions,
    _match_secret_pattern,
)

# Dependency manifest/lockfile basenames — when these are in files_changed,
# the lockfile freshness check should still run even on hooks.
_DEPENDENCY_FILES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
    }
)


class GitChannel:
    """Supervision channel for git hygiene.

    Advisory only — git issues are informational suggestions.
    """

    name = "git"
    timeout_ms = 3000
    blocking_capable = False  # Advisory

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on any change that modifies files (i.e., most events)."""
        if event.surface == "mcp":
            return True
        classification = event.change_classification
        if classification is None:
            return False
        # Skip read-only operations
        return classification.risk_level != "none"

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute git hygiene checks."""
        start = time.perf_counter()
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        project_root = event.project_root

        # Only run if this is a git repository
        if not _is_git_repo(project_root):
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "not_a_git_repo"},
            )

        is_hook = event.surface == "hook"

        # Check 0: Working tree scope — skip on hooks
        if not is_hook:
            findings.extend(_check_working_tree_scope(project_root))

        # Check 1: Large uncommitted changes — skip on hooks (always true during dev)
        if not is_hook:
            findings.extend(_check_large_changes(project_root))

        # Check 2: Lockfile-manifest mismatch — skip on hooks unless dep files changed
        run_lockfile_check = not is_hook or bool(
            {os.path.basename(f) for f in event.files_changed} & _DEPENDENCY_FILES
        )
        if run_lockfile_check:
            lockfile_findings, lockfile_repairs = _check_lockfile_freshness(project_root)
            findings.extend(lockfile_findings)
            repairs.extend(lockfile_repairs)

        # Check 3: Sensitive files — always run (security-relevant)
        findings.extend(_check_sensitive_files(project_root))

        # Check 4: Secrets in staged diffs — always run (security-relevant)
        secrets_count = len(findings)  # snapshot before secrets check
        findings.extend(_check_diff_secrets(project_root))
        secrets_count = len(findings) - secrets_count  # delta = secrets found

        # Check 5: Quality infrastructure completeness — skip on hooks
        qi_findings: list[LintIssue] = []
        qi_repairs: list[RepairAction] = []
        if not is_hook:
            qi_findings, qi_repairs = _check_quality_infrastructure(project_root)
            findings.extend(qi_findings)
            repairs.extend(qi_repairs)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        # Escalate severity if secrets or quality infra issues found (warning-level)
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = "informational"
            if any(f.severity == "warning" for f in findings):
                severity = "warning"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics={
                "checks_run": (
                    2
                    + (0 if is_hook else 1)  # Check 0: working tree scope
                    + (0 if is_hook else 1)  # Check 1: large changes
                    + (1 if run_lockfile_check else 0)  # Check 2
                    + (0 if is_hook else 1)  # Check 5: quality infra
                ),
                "issue_count": len(findings),
                "secrets_found": secrets_count,
                "quality_infra_findings": len(qi_findings),
            },
            duration_ms=elapsed_ms,
        )
