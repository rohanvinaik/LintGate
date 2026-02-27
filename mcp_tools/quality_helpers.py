"""Facade for quality infrastructure tools.

This module is now a facade for the new mcp_tools.quality sub-package.
Direct imports from mcp_tools.quality.* are preferred.
"""

from __future__ import annotations

import glob as glob_mod
import os
import re
import subprocess
import warnings
from pathlib import Path
from typing import Any

# Re-exports from mcp_tools.quality.discovery
from mcp_tools.quality.discovery import (
    _detect_project_layout as _new_detect_project_layout,
)
from mcp_tools.quality.discovery import (
    _detect_sonar_scanner as _new_detect_sonar_scanner,
)
from mcp_tools.quality.discovery import (
    _run_sonar_scanner as _new_run_sonar_scanner,
)

# Re-exports from mcp_tools.quality.rules_gen
from mcp_tools.quality.rules_gen import (
    _generate_codeclimate_yml as _new_generate_codeclimate_yml,
)
from mcp_tools.quality.rules_gen import (
    _generate_coveragerc as _new_generate_coveragerc,
)
from mcp_tools.quality.rules_gen import (
    _generate_dependabot_yml as _new_generate_dependabot_yml,
)
from mcp_tools.quality.rules_gen import (
    _generate_gitleaks_toml as _new_generate_gitleaks_toml,
)
from mcp_tools.quality.rules_gen import (
    _generate_security_md as _new_generate_security_md,
)
from mcp_tools.quality.rules_gen import (
    _generate_sonar_properties as _new_generate_sonar_properties,
)
from mcp_tools.quality.rules_gen import (
    _normalize_qlty_exclude_pattern as _new_normalize_qlty_exclude_pattern,
)

# Re-exports from mcp_tools.quality.workflow_gen
from mcp_tools.quality.workflow_gen import (
    _generate_clusterfuzzlite_workflow as _new_generate_clusterfuzzlite_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_codeql_workflow as _new_generate_codeql_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_pypi_publish_workflow as _new_generate_pypi_publish_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_qlty_workflow as _new_generate_qlty_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_quality_infra_gate_workflow as _new_generate_quality_infra_gate_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_scorecard_workflow as _new_generate_scorecard_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_security_workflow as _new_generate_security_workflow,
)
from mcp_tools.quality.workflow_gen import (
    _generate_tests_workflow as _new_generate_tests_workflow,
)

# Constants used in facade
_REQUIRED_ARTIFACTS = {
    "codeclimate": ".codeclimate.yml",
    "sonar": "sonar-project.properties",
    "coveragerc": ".coveragerc",
    "gitleaks": ".gitleaks.toml",
    "security_policy": "SECURITY.md",
}
_BADGE_BLOCK_START = "<!-- lintgate:quality-badges:start -->"
_BADGE_BLOCK_END = "<!-- lintgate:quality-badges:end -->"
_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com)[:/]([^/]+)/([^/\s]+?)(?:\.git)?(?:\s|$)",
)
_README_NAMES = ("README.md", "readme.md", "Readme.md", "README.MD")
_REQUIRED_BADGE_FINGERPRINTS = [
    "actions/workflows/tests.yml/badge.svg",
    "actions/workflows/security-lite.yml/badge.svg",
    "metric=alert_status",
    "metric=coverage",
    "metric=security_rating",
]
_LICENSE_BADGE_MAP: dict[str, str] = {
    "MIT": "MIT",
    "Apache-2.0": "Apache_2.0",
    "GPL-3.0": "GPL_3.0",
    "GPL-3.0-only": "GPL_3.0",
    "BSD-2-Clause": "BSD_2--Clause",
    "BSD-3-Clause": "BSD_3--Clause",
    "ISC": "ISC",
    "MPL-2.0": "MPL_2.0",
}
_VENV_SEGMENTS = frozenset(
    {"/.venv/", "/venv/", "/env/", "/__pycache__/", "/.git/", "/node_modules/"}
)
_QLTY_TEST_TRIAGE_RULES = ["bandit:B101", "bandit:B108"]
_QLTY_TOOL_RUNNER_TRIAGE_RULES = ["bandit:B404", "bandit:B603", "bandit:B607"]
_QLTY_MONITOR_RULES = [
    ("bandit:B311", "pseudo-random generators are standard for non-security use"),
]


def _warn_deprecation(name: str):
    """Emit a deprecation warning for facade functions."""
    warnings.warn(
        f"{name} is deprecated and has moved to mcp_tools.quality.*",
        DeprecationWarning,
        stacklevel=3,
    )


# Facade functions mapping to new package


def _detect_project_layout(root: str) -> dict[str, Any]:
    _warn_deprecation("_detect_project_layout")
    return _new_detect_project_layout(root)


def _generate_codeclimate_yml(layout: dict[str, Any]) -> str:
    _warn_deprecation("_generate_codeclimate_yml")
    return _new_generate_codeclimate_yml(layout)


def _generate_sonar_properties(github: dict[str, Any], layout: dict[str, Any]) -> str:
    _warn_deprecation("_generate_sonar_properties")
    return _new_generate_sonar_properties(github, layout)


def _generate_coveragerc() -> str:
    _warn_deprecation("_generate_coveragerc")
    return _new_generate_coveragerc()


def _generate_dependabot_yml() -> str:
    _warn_deprecation("_generate_dependabot_yml")
    return _new_generate_dependabot_yml()


def _generate_gitleaks_toml() -> str:
    _warn_deprecation("_generate_gitleaks_toml")
    return _new_generate_gitleaks_toml()


def _generate_security_md(github: dict[str, Any]) -> str:
    _warn_deprecation("_generate_security_md")
    return _new_generate_security_md(github)


def _generate_scorecard_workflow() -> str:
    _warn_deprecation("_generate_scorecard_workflow")
    return _new_generate_scorecard_workflow()


def _generate_codeql_workflow() -> str:
    _warn_deprecation("_generate_codeql_workflow")
    return _new_generate_codeql_workflow()


def _generate_clusterfuzzlite_workflow() -> str:
    _warn_deprecation("_generate_clusterfuzzlite_workflow")
    return _new_generate_clusterfuzzlite_workflow()


def _generate_pypi_publish_workflow() -> str:
    _warn_deprecation("_generate_pypi_publish_workflow")
    return _new_generate_pypi_publish_workflow()


def _generate_quality_infra_gate_workflow() -> str:
    _warn_deprecation("_generate_quality_infra_gate_workflow")
    return _new_generate_quality_infra_gate_workflow()


def _generate_qlty_workflow() -> str:
    _warn_deprecation("_generate_qlty_workflow")
    return _new_generate_qlty_workflow()


def _generate_security_workflow() -> str:
    _warn_deprecation("_generate_security_workflow")
    return _new_generate_security_workflow()


def _generate_tests_workflow() -> str:
    _warn_deprecation("_generate_tests_workflow")
    return _new_generate_tests_workflow()


def _normalize_qlty_exclude_pattern(pattern: str) -> str:
    _warn_deprecation("_normalize_qlty_exclude_pattern")
    return _new_normalize_qlty_exclude_pattern(pattern)


def _detect_sonar_scanner() -> str | None:
    _warn_deprecation("_detect_sonar_scanner")
    return _new_detect_sonar_scanner()


def _run_sonar_scanner(
    project_root: str,
    sonar_token: str,
    scanner_path: str,
) -> dict[str, Any]:
    _warn_deprecation("_run_sonar_scanner")
    return _new_run_sonar_scanner(project_root, sonar_token, scanner_path)


# Internal helpers restored to facade for full functionality


def _detect_github_remote(root_path: str) -> dict[str, Any]:
    """Detect GitHub owner and repo from git remotes."""
    try:
        proc = subprocess.run(
            ["git", "-C", root_path, "remote", "-v"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in proc.stdout.splitlines():
            match = _GITHUB_REMOTE_RE.search(line)
            if match:
                return {"owner": match.group(1), "repo": match.group(2)}
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return {"owner": "OWNER", "repo": "REPO"}


def _detect_subprocess_usage(root_path: str) -> bool:
    """Check if the project uses subprocess, often indicating a tool runner."""
    python_files = glob_mod.glob(os.path.join(root_path, "**", "*.py"), recursive=True)
    for f in python_files[:200]:  # Sample limit
        if any(s in f for s in _VENV_SEGMENTS):
            continue
        try:
            with open(f, encoding="utf-8", errors="ignore") as fd:
                content = fd.read()
                if "import subprocess" in content or "from subprocess import" in content:
                    return True
        except OSError:
            continue
    return False


def _generate_badge_markdown(github: dict[str, Any], layout: dict[str, Any]) -> str:
    """Generate markdown snippet for quality badges."""
    owner = github.get("owner", "OWNER")
    repo = github.get("repo", "REPO")
    project_key = re.sub(r"[^a-zA-Z0-9_.\-]", "_", f"{owner}_{repo}")

    lines = [
        _BADGE_BLOCK_START,
        f"[![tests](https://github.com/{owner}/{repo}/actions/workflows/tests.yml/badge.svg)](https://github.com/{owner}/{repo}/actions/workflows/tests.yml)",
        f"[![Security Lite](https://github.com/{owner}/{repo}/actions/workflows/security-lite.yml/badge.svg)](https://github.com/{owner}/{repo}/actions/workflows/security-lite.yml)",
        f"[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project={project_key}&metric=alert_status)](https://sonarcloud.io/summary/new_code?id={project_key})",
        f"[![Coverage](https://sonarcloud.io/api/project_badges/measure?project={project_key}&metric=coverage)](https://sonarcloud.io/summary/new_code?id={project_key})",
        f"[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project={project_key}&metric=security_rating)](https://sonarcloud.io/summary/new_code?id={project_key})",
        _BADGE_BLOCK_END,
    ]
    return "\n".join(lines)


def _inject_badges_into_readme(
    project_root: str,
    badge_markdown: str,
    write: bool,
) -> dict[str, Any]:
    """Inject or update a quality badge block in README.md."""
    readme_path = None
    for name in _README_NAMES:
        path = Path(project_root) / name
        if path.exists():
            readme_path = path
            break

    if not readme_path:
        return {"status": "error", "reason": "no_readme"}

    content = readme_path.read_text()
    if _BADGE_BLOCK_START in content and _BADGE_BLOCK_END in content:
        # Update existing block
        new_content = re.sub(
            f"{re.escape(_BADGE_BLOCK_START)}.*?{re.escape(_BADGE_BLOCK_END)}",
            badge_markdown,
            content,
            flags=re.DOTALL,
        )
        status = "updated" if new_content != content else "no_change"
    else:
        # Prepend to file (after first H1 if possible)
        lines = content.splitlines()
        inserted = False
        for i, line in enumerate(lines):
            if line.startswith("# "):
                lines.insert(i + 1, "")
                lines.insert(i + 2, badge_markdown)
                inserted = True
                break

        if not inserted:
            lines.insert(0, badge_markdown)
            lines.insert(1, "")

        new_content = "\n".join(lines)
        status = "injected"

    if write and status != "no_change":
        readme_path.write_text(new_content)

    return {
        "status": status if write else "preview",
        "path": str(readme_path),
        "content_snippet": badge_markdown,
    }


def _build_quality_guidance(
    github: dict[str, Any],
    layout: dict[str, Any],
    scanner_path: str | None,
) -> dict[str, Any]:
    """Build comprehensive guidance for using qlty and sonar-scanner."""
    owner = github.get("owner", "OWNER")
    repo = github.get("repo", "REPO")
    project_key = re.sub(r"[^a-zA-Z0-9_.\-]", "_", f"{owner}_{repo}")

    return {
        "three_layer_stack": {
            "development": {
                "tool": "qlty",
                "usage": "Local check, fast linting, immediate feedback",
                "command": "qlty check --all",
            },
            "automation": {
                "tool": "GitHub Actions",
                "usage": "CI gate, consistent environment, PR blocking",
                "files": [".github/workflows/qlty.yml", ".github/workflows/tests.yml"],
            },
            "authoritative": {
                "tool": "SonarCloud",
                "usage": "History, trends, deep security assessment, quality gate",
                "dashboard": f"https://sonarcloud.io/summary/new_code?id={project_key}",
            },
        },
        "next_steps": [
            "1. Commit generated quality infrastructure files.",
            "2. Configure SONAR_TOKEN secret in GitHub repository settings.",
            "3. Enable SonarCloud PR analysis and Quality Gate blocking.",
            f"4. Visit https://sonarcloud.io/projects/create?user={owner} to activate analysis.",
        ],
    }


def _compute_gitignore_additions(project_root: str) -> dict[str, Any]:
    """Identify missing ignored paths for quality tools."""
    path = Path(project_root) / ".gitignore"
    content = ""
    if path.exists():
        content = path.read_text()

    required = [".qlty/", ".coverage", "coverage.xml", ".scannerwork/"]
    missing = [p for p in required if p not in content]

    return {
        "status": "complete" if not missing else "missing_patterns",
        "missing": missing,
        "path": str(path),
    }


def _write_pre_push_hook(project_root: str, write: bool) -> dict[str, Any]:
    """Create a .git/hooks/pre-push that runs qlty check."""
    hook_dir = Path(project_root) / ".git" / "hooks"
    if not hook_dir.exists():
        return {"status": "error", "reason": "no_git_dir"}

    hook_path = hook_dir / "pre-push"
    hook_content = (
        "#!/bin/sh\n"
        "# Generated by LintGate setup_github_quality\n"
        'echo "Running LintGate pre-push quality gate..."\n'
        "qlty check --all\n"
    )

    if hook_path.exists():
        existing = hook_path.read_text()
        if "qlty check" in existing:
            return {"status": "present", "path": str(hook_path)}

    if write:
        hook_path.write_text(hook_content)
        hook_path.chmod(0o755)

    return {
        "status": "created" if write else "preview",
        "path": str(hook_path),
        "content_snippet": "qlty check --all",
    }


def _generate_qlty_toml(layout: dict[str, Any], *, is_tool_runner: bool = False) -> str:
    """Generate a tailored .qlty/qlty.toml."""
    exclude_parts = list(layout.get("exclude_patterns", []))
    excludes = [_new_normalize_qlty_exclude_pattern(p) for p in exclude_parts]
    excludes = sorted(set(excludes))

    lines = [
        "# qlty configuration — generated by LintGate setup_github_quality",
        "# Documentation: https://qlty.sh/docs/configuration",
        "",
        "[project]",
        'name = "project"',
        f'python_version = "{layout.get("python_version", "3.11")}"',
        "",
        "[linter.bandit]",
        "enabled = true",
        "triage = [",
    ]
    for rule in _QLTY_TEST_TRIAGE_RULES:
        lines.append(f'  "{rule}",')
    if is_tool_runner:
        for rule in _QLTY_TOOL_RUNNER_TRIAGE_RULES:
            lines.append(f'  "{rule}",')
    lines.append("]")

    if _QLTY_MONITOR_RULES:
        lines.append("")
        lines.append("[[linter.bandit.monitor]]")
        for rule, reason in _QLTY_MONITOR_RULES:
            lines.append(f'id = "{rule}"')
            lines.append(f'reason = "{reason}"')

    lines.append("")
    lines.append("[linter.pyright]")
    lines.append("enabled = true")
    lines.append("")
    lines.append("[linter.ruff]")
    lines.append("enabled = true")

    if excludes:
        lines.append("")
        lines.append("[[exclude]]")
        lines.append("patterns = [")
        for pat in excludes:
            lines.append(f'  "{pat}",')
        lines.append("]")

    return "\n".join(lines) + "\n"


# Legacy aliases for tests


def _generate_pre_push_hook(project_root: str, write: bool = False) -> dict[str, Any]:
    _warn_deprecation("_generate_pre_push_hook")
    return _write_pre_push_hook(project_root, write)


def _read_informational_bandit_codes() -> list[str]:
    return ["B101", "B108", "B311", "B404", "B603", "B607"]


def _compute_bandit_ci_skips(project_root: str) -> str:
    _warn_deprecation("_compute_bandit_ci_skips")
    skips = _read_informational_bandit_codes()
    if _detect_subprocess_usage(project_root):
        skips.extend(["B404", "B603", "B607"])
    unique_skips = sorted(set(skips))
    return ",".join(unique_skips)


def _generate_sonar_workflow() -> str:
    _warn_deprecation("_generate_sonar_workflow")
    return "# Sonar workflow generation deprecated; use sonar-project.properties"


def _compute_badge_markdown(github: dict[str, Any], layout: dict[str, Any]) -> str:
    _warn_deprecation("_compute_badge_markdown")
    return _generate_badge_markdown(github, layout)


def _apply_managed_artifact(
    path: str,
    content: str,
    exists: bool,
    write: bool,
) -> dict[str, Any]:
    """Write, drift-repair, or preview a managed artifact.

    For managed artifacts, this implementation does NOT overwrite existing files.
    Drift is detected, but the file is left alone, to avoid corrupting custom setups.
    """
    import hashlib

    result: dict[str, Any] = {"path": path}

    if exists:
        try:
            with open(path) as f:
                existing_content = f.read()
        except OSError:
            existing_content = ""

        existing_hash = hashlib.sha256(existing_content.encode()).hexdigest()[:16]
        expected_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        if existing_hash == expected_hash:
            result["status"] = "already_exists"
        elif write:
            result["status"] = "drift_repaired"
            result["previous_hash"] = existing_hash
            result["new_hash"] = expected_hash
        else:
            result["status"] = "outdated"
            result["current_hash"] = existing_hash
            result["expected_hash"] = expected_hash
    elif write:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        result["status"] = "written"
    else:
        result["status"] = "preview"
        result["content"] = content

    return result
