"""pip-audit supply-chain integrity integration.

Tier 2 — runs on structural changes. Scans installed dependencies
for known vulnerabilities using the OSV and PyPI vulnerability databases.

Professional instinct modeled: "A senior engineer audits dependencies
for known vulnerabilities before shipping."
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class PipAuditLinter(BaseLinter):
    """pip-audit vulnerability scanner — catches known supply-chain risks.

    Uses --format json for structured output. Scans the current environment's
    installed packages against OSV and PyPI vulnerability databases.
    """

    name = "pip_audit"
    tier = 2
    timeout_ms = 15000  # Network calls to vulnerability databases
    required_tool = "pip-audit"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run pip-audit with JSON output."""

        cmd = [
            "pip-audit",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]

        # Add extra args from config
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            cmd.extend(extra_args)

        result = self.run_command(cmd, ctx.project_root)

        # pip-audit outputs JSON to stdout
        output = result.stdout or ""
        if not output.strip():
            return

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return

        # pip-audit JSON format: {"dependencies": [...], "fixes": [...]}
        # or a flat list of vulnerability objects depending on version
        vulnerabilities = []
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

        for vuln in vulnerabilities:
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

            yield LintIssue(
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
                },
                suggestions=suggestions,
            )

    def _filter_files(self, files: list[str]) -> list[str]:
        """pip-audit scans the environment, not specific files.

        Return input files unchanged — the linter ignores them and
        scans installed packages instead.
        """
        return files


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
