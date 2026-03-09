"""pip-audit supply-chain integrity integration.

Tier 2 — runs on structural changes. Scans installed dependencies
for known vulnerabilities using the OSV and PyPI vulnerability databases.

Professional instinct modeled: "A senior engineer audits dependencies
for known vulnerabilities before shipping."
"""

from __future__ import annotations

import json
import os
from glob import glob
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class PipAuditLinter(BaseLinter):
    """pip-audit vulnerability scanner — catches known supply-chain risks.

    Uses --format json for structured output. Prefers requirements files when
    present, otherwise falls back to scanning the active environment.
    """

    name = "pip_audit"
    tier = 2
    timeout_ms = 15000  # Network calls to vulnerability databases
    required_tool = "pip-audit"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run pip-audit with JSON output."""

        base_cmd = [
            "pip-audit",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            base_cmd.extend(extra_args)

        requirement_files = _discover_requirement_files(ctx.project_root, ctx.config)
        scan_targets: list[tuple[str, str | None]] = []
        if requirement_files:
            for rf in requirement_files:
                scan_targets.append((f"requirements:{os.path.basename(rf)}", rf))
        else:
            # Fallback: audit active environment if no requirements files are present.
            scan_targets.append(("environment", None))

        seen: set[tuple[str, str, str]] = set()
        for source_label, req_file in scan_targets:
            cmd = list(base_cmd)
            if req_file:
                cmd.extend(["-r", req_file])
            result = self.run_command(cmd, ctx.project_root)
            output = result.stdout or ""
            if not output.strip():
                continue

            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                continue

            vulnerabilities = _extract_vulnerabilities(data)
            for vuln in vulnerabilities:
                pkg_name = vuln.get("name", "unknown")
                vuln_id = vuln.get("vuln_id") or vuln.get("id", "")
                dedupe_key = (source_label, pkg_name, vuln_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                yield _to_issue(vuln, source_label)

    def _filter_files(self, files: list[str]) -> list[str]:
        """pip-audit scans dependency sources, not code files."""
        return files


def _extract_vulnerabilities(data: object) -> list[dict]:
    vulnerabilities: list[dict] = []
    if isinstance(data, dict):
        deps = data.get("dependencies", [])
        for dep in deps:
            vulns = dep.get("vulns", [])
            for vuln in vulns:
                vulnerabilities.append(
                    {
                        "name": dep.get("name", ""),
                        "version": dep.get("version", ""),
                        "vuln_id": vuln.get("id", ""),
                        "fix_versions": vuln.get("fix_versions", []),
                        "description": vuln.get("description", ""),
                        "aliases": vuln.get("aliases", []),
                    }
                )
    elif isinstance(data, list):
        vulnerabilities = data
    return vulnerabilities


def _to_issue(vuln: dict, source_label: str) -> LintIssue:
    pkg_name = vuln.get("name", "unknown")
    pkg_version = vuln.get("version", "?")
    vuln_id = vuln.get("vuln_id") or vuln.get("id", "")
    description = vuln.get("description", "")
    fix_versions = vuln.get("fix_versions", [])
    aliases = vuln.get("aliases", [])

    # Build concise message
    msg_parts = [f"Vulnerable dependency: {pkg_name}=={pkg_version}"]
    if vuln_id:
        msg_parts.append(f"({vuln_id})")
    if description:
        # Truncate long descriptions
        desc_short = description[:120].rstrip()
        if len(description) > 120:
            desc_short += "..."
        msg_parts.append(f"— {desc_short}")

    suggestions = []
    if fix_versions:
        suggestions.append(f"Upgrade to {pkg_name}>={fix_versions[0]}")
    suggestions.append("Pin to a non-vulnerable version")
    suggestions.append("Review if this dependency is necessary")

    return LintIssue(
        linter="pip_audit",
        kind=vuln_id or "vulnerability",
        message=" ".join(msg_parts),
        severity=_classify_severity(vuln_id, aliases),
        confidence=1.0,  # Database-backed, deterministic
        evidence={
            "package": pkg_name,
            "installed_version": pkg_version,
            "vulnerability_id": vuln_id,
            "fix_versions": fix_versions,
            "aliases": aliases[:5],
            "scan_source": source_label,
        },
        suggestions=suggestions,
    )


def _discover_requirement_files(project_root: str, config: dict) -> list[str]:
    configured = config.get("requirement_files")
    if isinstance(configured, list):
        discovered = [os.path.join(project_root, str(p)) for p in configured]
        return [p for p in discovered if os.path.exists(p)]

    patterns = [
        "requirements*.txt",
        "requirements/*.txt",
    ]
    found: set[str] = set()
    for pattern in patterns:
        for path in glob(os.path.join(project_root, pattern)):
            if os.path.isfile(path):
                found.add(path)
    return sorted(found)


def _classify_severity(vuln_id: str, aliases: list[str]) -> str:
    """Classify vulnerability severity.

    CVE references and GHSA advisories are treated as warnings.
    Everything else is informational.
    """
    all_ids = [vuln_id] + aliases
    for vid in all_ids:
        vid_upper = vid.upper()
        # CVEs and GitHub Security Advisories are high-signal
        if vid_upper.startswith("CVE-") or vid_upper.startswith("GHSA-"):
            return "warning"
    return "informational"
