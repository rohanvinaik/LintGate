"""Onboarding tools — getting_started entry point for LintGate MCP."""

from __future__ import annotations

import glob as glob_mod
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

_OPTIONAL_STARTUP_PACKAGES = {
    "pip-audit": "pip-audit",
    "ty": "ty",
}


def _tool_package_name(tool: str) -> str:
    """Map executable names to pip package names."""
    if tool in _OPTIONAL_STARTUP_PACKAGES:
        return _OPTIONAL_STARTUP_PACKAGES[tool]
    return tool


def _project_venv_python(project_root: str) -> str | None:
    """Return project venv python path, if present."""
    for venv_name in (".venv", "venv", "env"):
        py = Path(project_root) / venv_name / "bin" / "python"
        if py.exists() and py.is_file():
            return str(py)
    return None


def _format_cmd(cmd: list[str]) -> str:
    """Render shell-safe command text for output payloads."""
    return " ".join(shlex.quote(part) for part in cmd)


def _linter_available(linter: Any, project_root: str) -> bool:
    """Check linter availability with backward-compatible signatures."""
    try:
        return bool(linter.available(project_root=project_root))
    except TypeError:
        return bool(linter.available())


def _venv_create_command() -> tuple[list[str], str]:
    """Build preferred venv creation command and manager label."""
    uv_path = shutil.which("uv")
    if uv_path:
        return [uv_path, "venv", ".venv"], "uv"
    return [sys.executable, "-m", "venv", ".venv"], "python_venv"


def _ensure_project_venv(project_root: str) -> dict[str, Any]:
    """Ensure a project-local virtualenv exists and has pip available."""
    existing = _project_venv_python(project_root)
    if existing:
        return {"status": "present", "venv_python": existing}

    create_cmd, manager = _venv_create_command()
    try:
        create_result = subprocess.run(
            create_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "reason": "venv_create_timed_out_after_120s",
        }

    if create_result.returncode != 0:
        return {
            "status": "error",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "returncode": create_result.returncode,
            "stderr_tail": (create_result.stderr or "")[-240:],
            "reason": "venv_create_failed",
        }

    venv_python = _project_venv_python(project_root)
    if not venv_python:
        return {
            "status": "error",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "reason": "venv_created_but_python_missing",
        }

    pip_check_cmd = [venv_python, "-m", "pip", "--version"]
    try:
        pip_check = subprocess.run(
            pip_check_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "created",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "venv_python": venv_python,
            "pip_ready": False,
            "pip_check": "timeout",
        }

    if pip_check.returncode == 0:
        return {
            "status": "created",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "venv_python": venv_python,
            "pip_ready": True,
        }

    ensurepip_cmd = [venv_python, "-m", "ensurepip", "--upgrade"]
    try:
        ensure_result = subprocess.run(
            ensurepip_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "created",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "venv_python": venv_python,
            "pip_ready": False,
            "pip_bootstrap": "timeout",
        }

    return {
        "status": "created",
        "manager": manager,
        "command": _format_cmd(create_cmd),
        "venv_python": venv_python,
        "pip_ready": ensure_result.returncode == 0,
        "pip_bootstrap_command": _format_cmd(ensurepip_cmd),
        "pip_bootstrap_returncode": ensure_result.returncode,
        "pip_bootstrap_stderr_tail": (ensure_result.stderr or "")[-240:],
    }


def _install_commands_for_package(project_root: str, package: str) -> list[list[str]]:
    """Build preferred installer commands for a package in the project venv."""
    venv_python = _project_venv_python(project_root)
    if not venv_python:
        return []

    commands: list[list[str]] = []
    uv_path = shutil.which("uv")
    if uv_path:
        commands.append([uv_path, "pip", "install", "--python", venv_python, package])
    commands.append([venv_python, "-m", "pip", "install", package])
    return commands


def _install_command_for_package(project_root: str, package: str) -> list[str] | None:
    """Build an installer command targeting the project venv."""
    commands = _install_commands_for_package(project_root, package)
    return commands[0] if commands else None


def _collect_external_tool_gaps(project_root: str) -> dict[str, Any]:
    """Collect missing external tool information from the active registry."""
    from lintgate.config import load_config
    from lintgate.registry import build_registry

    config = load_config(project_root)
    registry = build_registry(config)

    tool_matrix: dict[str, dict[str, Any]] = {}
    for linter_name, linter in sorted(registry.items()):
        tool = linter.required_tool
        if not tool:
            continue
        entry = tool_matrix.setdefault(
            tool,
            {
                "tool": tool,
                "package": _tool_package_name(tool),
                "available": True,
                "required_by": [],
            },
        )
        entry["required_by"].append(linter_name)
        entry["available"] = entry["available"] and _linter_available(linter, project_root)

    missing_tools: list[dict[str, Any]] = []
    for tool in sorted(tool_matrix):
        entry = tool_matrix[tool]
        if entry["available"]:
            continue
        package = entry["package"]
        install_cmd = _install_command_for_package(project_root, package)
        missing_tools.append(
            {
                "tool": tool,
                "package": package,
                "required_by": entry["required_by"],
                "reason": "executable_not_found",
                "install_command": _format_cmd(install_cmd) if install_cmd else f"pip install {package}",
                "auto_installable": install_cmd is not None and tool in _OPTIONAL_STARTUP_PACKAGES,
            }
        )

    return {
        "tool_status": [tool_matrix[k] for k in sorted(tool_matrix)],
        "missing_tools": missing_tools,
    }


def _auto_install_optional_tools(
    project_root: str,
    missing_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attempt to install optional missing tools into the project venv."""
    attempts: list[dict[str, Any]] = []
    for item in missing_tools:
        tool = str(item.get("tool", ""))
        package = str(item.get("package", tool))
        if tool not in _OPTIONAL_STARTUP_PACKAGES:
            continue

        cmds = _install_commands_for_package(project_root, package)
        if not cmds:
            attempts.append(
                {
                    "tool": tool,
                    "package": package,
                    "status": "skipped",
                    "reason": "no_project_venv_detected",
                }
            )
            continue

        command_results: list[dict[str, Any]] = []
        installed = False
        for cmd in cmds:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except subprocess.TimeoutExpired:
                command_results.append(
                    {
                        "status": "timeout",
                        "command": _format_cmd(cmd),
                        "reason": "install_timed_out_after_180s",
                    }
                )
                continue

            command_result = {
                "status": "installed" if result.returncode == 0 else "error",
                "command": _format_cmd(cmd),
                "returncode": result.returncode,
                "stderr_tail": (result.stderr or "")[-240:],
            }
            command_results.append(command_result)
            if result.returncode == 0:
                attempts.append(
                    {
                        "tool": tool,
                        "package": package,
                        "status": "installed",
                        "command": command_result["command"],
                        "returncode": 0,
                        "attempted_commands": command_results,
                    }
                )
                installed = True
                break

        if installed:
            continue

        last_result = command_results[-1] if command_results else {}
        attempts.append(
            {
                "tool": tool,
                "package": package,
                "status": "error",
                "reason": "all_install_commands_failed",
                "command": last_result.get("command"),
                "returncode": last_result.get("returncode"),
                "stderr_tail": last_result.get("stderr_tail"),
                "attempted_commands": command_results,
            }
        )

    return attempts


def _scaffold_config_yaml(project_root: str, helpers: dict) -> str:
    """Analyze project and generate a tailored lintgate.yaml."""
    lines: list[str] = []
    lines.append("# LintGate configuration — generated by scaffold_config")
    lines.append("# Review and adjust to match your project's needs.")
    lines.append("")

    # Detect Python source files for critical path analysis
    py_files = sorted(glob_mod.glob(os.path.join(project_root, "**", "*.py"), recursive=True))
    # Exclude venv, __pycache__, .git
    py_files = [
        f for f in py_files
        if not any(seg in f for seg in ("/.venv/", "/__pycache__/", "/.git/", "/node_modules/"))
    ]

    # Find large files (potential critical paths)
    critical_paths: list[str] = []
    for fpath in py_files:
        try:
            with open(fpath) as f:
                line_count = sum(1 for _ in f)
            if line_count > 300:
                rel = os.path.relpath(fpath, project_root)
                critical_paths.append(rel)
        except OSError:
            continue

    if critical_paths:
        lines.append("pipeline_critical_paths:")
        for cp in sorted(critical_paths)[:10]:
            lines.append(f'  - "{cp}"')
        lines.append("")

    # Check if project uses subprocess-heavy patterns (tool-orchestration)
    has_subprocess = False
    for fpath in py_files[:50]:  # Sample first 50 files
        try:
            with open(fpath) as f:
                content = f.read(8192)
            if "subprocess" in content:
                has_subprocess = True
                break
        except OSError:
            continue

    if has_subprocess:
        lines.append("severity_overrides:")
        lines.append("  B603: informational  # subprocess calls — expected for tool orchestration")
        lines.append("  B107: informational  # hardcoded passwords — review if unexpected")
        lines.append("")

    # ControlPlane config
    lines.append("controlplane:")
    lines.append("  enabled: true")
    lines.append("  severity_weighted_coherence: true")
    lines.append("  channels:")
    lines.append("    behavior:")
    lines.append("      enabled: true")
    lines.append("      thresholds:")
    lines.append("        approach_cycling_count: 3")
    lines.append("        failure_amnesia_lookback: 30")
    lines.append("  inquiry:")
    lines.append("    theory_grounded_signals: true")
    lines.append("    prediction_tracking: true")
    lines.append("    theory_coherence_check: true")
    lines.append("    living_context: true")
    lines.append("    session_gate: true")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# GitHub quality infrastructure helpers
# ---------------------------------------------------------------------------

_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com)[:/]([^/]+)/([^/\s]+?)(?:\.git)?(?:\s|$)",
)

_STANDARD_GITIGNORE_PATTERNS: list[str] = [
    "# Virtual environments",
    ".venv/",
    "venv/",
    "env/",
    "",
    "# Python caches",
    "__pycache__/",
    "*.py[cod]",
    "*.egg-info/",
    "*.egg",
    "dist/",
    "build/",
    "",
    "# Tool caches",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pytest_cache/",
    "",
    "# External quality tools (local analysis artifacts)",
    ".qlty/logs/",
    ".qlty/out/",
    ".qlty/plugin_cachedir/",
    ".qlty/results/",
    ".scannerwork/",
    "",
    "# OS artifacts",
    ".DS_Store",
    "Thumbs.db",
    "",
    "# IDE",
    ".idea/",
    ".vscode/",
    "*.swp",
    "*.swo",
    "",
    "# LintGate session state",
    ".claude/continuity/",
    ".lintgate/",
]

_README_NAMES = ("README.md", "readme.md", "Readme.md", "README.MD")

_REQUIRED_BADGE_FINGERPRINTS = (
    "actions/workflows/security-lite.yml/badge.svg",
    "metric=alert_status",
    "metric=coverage",
    "metric=security_rating",
)
_BADGE_BLOCK_START = "<!-- lintgate:quality-badges:start -->"
_BADGE_BLOCK_END = "<!-- lintgate:quality-badges:end -->"

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

_VENV_SEGMENTS = frozenset({"/.venv/", "/venv/", "/env/", "/__pycache__/", "/.git/", "/node_modules/"})

# ── qlty (Code Climate CLI) triage patterns ─────────────────────────────

# Bandit rules that are domain-expected in test code
_QLTY_TEST_TRIAGE_RULES = ["bandit:B101", "bandit:B108"]

# Bandit rules that are expected in tool-runner projects (subprocess usage)
_QLTY_TOOL_RUNNER_TRIAGE_RULES = ["bandit:B404", "bandit:B603", "bandit:B607"]

# Radarlint rules to set to monitor mode (intentional patterns)
_QLTY_MONITOR_RULES = [
    ("radarlint-python:python:S1244", "Float equality is intentional in scoring/threshold code"),
    ("radarlint-python:python:S1481", "Unused vars from tuple unpacking are idiomatic Python"),
]


def _detect_github_remote(project_root: str) -> dict[str, Any]:
    """Parse git remote -v for GitHub owner/repo."""
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"detected": False, "reason": "git_not_available"}

    if result.returncode != 0 or not result.stdout:
        return {"detected": False, "reason": "no_git_remotes"}

    # Prefer 'origin' remote
    for line in result.stdout.splitlines():
        if line.startswith("origin"):
            m = _GITHUB_REMOTE_RE.search(line)
            if m:
                return {"detected": True, "owner": m.group(1), "repo": m.group(2)}

    # Fall back to first GitHub remote
    m = _GITHUB_REMOTE_RE.search(result.stdout)
    if m:
        return {"detected": True, "owner": m.group(1), "repo": m.group(2)}

    return {"detected": False, "reason": "no_github_remote_found"}


def _detect_project_layout(project_root: str) -> dict[str, Any]:
    """Detect source dirs, test dirs, Python version, and license."""
    root = Path(project_root)
    source_dirs: list[str] = []
    test_dirs: list[str] = []
    doc_dirs: list[str] = []
    python_version: str = "3"
    license_id: str | None = None

    # --- Try pyproject.toml first ---
    pyproject = root / "pyproject.toml"
    has_pyproject = pyproject.exists()
    if has_pyproject:
        try:
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore[no-redef]
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)

            # Python version
            requires_python = data.get("project", {}).get("requires-python", "")
            if requires_python:
                ver_match = re.search(r"(\d+\.\d+)", requires_python)
                if ver_match:
                    python_version = ver_match.group(1)

            # License
            lic = data.get("project", {}).get("license", {})
            if isinstance(lic, dict):
                license_id = lic.get("text") or lic.get("file")
            elif isinstance(lic, str):
                license_id = lic

            # Test paths from pytest config
            test_paths = (
                data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", [])
            )
            if test_paths:
                test_dirs = list(test_paths)

        except Exception:
            pass

    # --- .python-version fallback ---
    if python_version == "3":
        pv_file = root / ".python-version"
        if pv_file.exists():
            try:
                ver_match = re.search(r"(\d+\.\d+)", pv_file.read_text())
                if ver_match:
                    python_version = ver_match.group(1)
            except OSError:
                pass

    # --- License fallback from LICENSE file ---
    if not license_id:
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"):
            lic_path = root / name
            if lic_path.exists():
                try:
                    content = lic_path.read_text(errors="ignore")[:500]
                    if "MIT" in content:
                        license_id = "MIT"
                    elif "Apache" in content:
                        license_id = "Apache-2.0"
                    elif "GNU GENERAL PUBLIC LICENSE" in content.upper():
                        license_id = "GPL-3.0"
                    elif "BSD" in content:
                        license_id = "BSD-3-Clause"
                except OSError:
                    pass
                break

    # --- Directory scanning for source/test/doc ---
    skip_dirs = {".venv", "venv", "env", ".git", "__pycache__", "node_modules",
                 ".mypy_cache", ".ruff_cache", ".pytest_cache", ".claude", "dist", "build"}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in skip_dirs:
            continue
        if entry.name in ("tests", "test"):
            if not test_dirs:
                test_dirs.append(entry.name)
        elif entry.name in ("docs", "doc"):
            doc_dirs.append(entry.name)
        elif (entry / "__init__.py").exists():
            source_dirs.append(entry.name)
        elif entry.name == "src":
            # PEP 517 src-layout: look for packages inside src/
            for sub in sorted(entry.iterdir()):
                if sub.is_dir() and (sub / "__init__.py").exists():
                    source_dirs.append(f"src/{sub.name}")

    # Exclude patterns for quality tools
    exclude_patterns = ["**/__pycache__/", "*.egg-info/"]
    for d in test_dirs:
        exclude_patterns.append(f"{d}/")
    for d in doc_dirs:
        exclude_patterns.append(f"{d}/")
    exclude_patterns.append(".claude/")

    return {
        "source_dirs": source_dirs or ["."],
        "test_dirs": test_dirs,
        "doc_dirs": doc_dirs,
        "python_version": python_version,
        "license": license_id,
        "has_pyproject_toml": has_pyproject,
        "exclude_patterns": exclude_patterns,
    }


def _generate_codeclimate_yml(layout: dict[str, Any]) -> str:
    """Generate a tailored .codeclimate.yml."""
    excludes = list(layout.get("exclude_patterns", []))
    # De-duplicate
    seen: set[str] = set()
    unique_excludes: list[str] = []
    for e in excludes:
        if e not in seen:
            seen.add(e)
            unique_excludes.append(e)

    lines = [
        'version: "2"',
        "",
        "checks:",
        "  method-complexity:",
        "    enabled: true",
        "    config:",
        "      threshold: 20",
        "",
        "  file-lines:",
        "    enabled: true",
        "    config:",
        "      threshold: 500",
        "",
        "  method-lines:",
        "    enabled: true",
        "    config:",
        "      threshold: 100",
        "",
        "  method-count:",
        "    enabled: true",
        "    config:",
        "      threshold: 30",
        "",
        "  return-statements:",
        "    enabled: true",
        "    config:",
        "      threshold: 8",
        "",
        "  argument-count:",
        "    enabled: true",
        "    config:",
        "      threshold: 8",
        "",
        "  identical-code:",
        "    enabled: true",
        "    config:",
        "      threshold: 3",
        "",
        "  similar-code:",
        "    enabled: true",
        "    config:",
        "      threshold: 3",
        "",
        "plugins:",
        "  radon:",
        "    enabled: true",
        "    config:",
        '      threshold: "C"',
        "",
        "  duplication:",
        "    enabled: true",
        "    config:",
        "      languages:",
        "        python:",
        "          mass_threshold: 40",
        "",
        "exclude_patterns:",
    ]
    for pattern in unique_excludes:
        lines.append(f'  - "{pattern}"')

    return "\n".join(lines) + "\n"


def _generate_sonar_properties(github: dict[str, Any], layout: dict[str, Any]) -> str:
    """Generate a tailored sonar-project.properties."""
    owner = github.get("owner", "OWNER")
    repo = github.get("repo", "REPO")
    # SonarCloud keys: alphanumeric, _, -, .
    project_key = re.sub(r"[^a-zA-Z0-9_.\-]", "_", f"{owner}_{repo}")

    source_dirs = ",".join(layout.get("source_dirs", ["."]))
    test_dirs = ",".join(layout.get("test_dirs", ["tests"]))
    python_version = layout.get("python_version", "3")

    exclude_parts = list(layout.get("exclude_patterns", []))
    # Shell scripts are not Python — SonarCloud's Python analyzer misclassifies them
    if "*.sh" not in exclude_parts:
        exclude_parts.append("*.sh")
    exclusions = ",\\\n  ".join(
        p if (p.endswith("**") or p.endswith("**/*") or p.startswith("*."))
        else f"{p}**"
        for p in exclude_parts
    )

    lines = [
        "# SonarCloud configuration — generated by LintGate setup_github_quality",
        f"sonar.projectKey={project_key}",
        f"sonar.organization={owner}",
        f"sonar.projectName={repo}",
        "",
        "# Source layout",
        f"sonar.sources={source_dirs}",
        f"sonar.tests={test_dirs}",
        f"sonar.python.version={python_version}",
        "",
        "# Exclusions",
        "sonar.exclusions=\\",
        f"  {exclusions}",
        "",
        "# Coverage (generate with: pytest --cov --cov-report=xml)",
        "sonar.python.coverage.reportPaths=coverage.xml",
    ]
    return "\n".join(lines) + "\n"


def _generate_sonar_workflow(layout: dict[str, Any]) -> str:
    """Generate a GitHub Actions workflow for SonarQube Cloud analysis.

    Uses a single-job pattern with step-level token check.  The ``secrets``
    context is NOT available in job-level ``if:`` conditions
    (actions/runner#520), so we probe for the token via an ``env:`` variable
    in the first step and gate subsequent steps on the output.
    """
    raw_version = str(layout.get("python_version", "3.11")).strip()
    python_version = raw_version if re.fullmatch(r"\d+(?:\.\d+)?", raw_version) else "3.11"

    lines = [
        "name: SonarQube Cloud Analysis",
        "",
        "on:",
        "  push:",
        "  pull_request:",
        "    types: [opened, synchronize, reopened]",
        "  workflow_dispatch:",
        "",
        "permissions:",
        "  contents: read",
        "  pull-requests: read",
        "",
        "concurrency:",
        "  group: sonarcloud-${{ github.workflow }}-${{ github.ref }}",
        "  cancel-in-progress: true",
        "",
        "jobs:",
        "  sonarcloud:",
        "    name: SonarQube Cloud Scan",
        "    runs-on: ubuntu-latest",
        "    if: >",
        "      github.event_name != 'pull_request' ||",
        "      github.event.pull_request.head.repo.full_name == github.repository",
        "    steps:",
        "      - name: Check for SONAR_TOKEN",
        "        id: check_token",
        "        env:",
        "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}",
        "        run: |",
        '          if [ -z "$SONAR_TOKEN" ]; then',
        '            echo "has_token=false" >> "$GITHUB_OUTPUT"',
        '            echo "::notice::SONAR_TOKEN secret is not configured; skipping SonarQube Cloud scan."',
        '            echo "Add SONAR_TOKEN at: https://github.com/${{ github.repository }}/settings/secrets/actions"',
        "          else",
        '            echo "has_token=true" >> "$GITHUB_OUTPUT"',
        "          fi",
        "",
        "      - name: Checkout full history",
        "        if: steps.check_token.outputs.has_token == 'true'",
        "        uses: actions/checkout@v4",
        "        with:",
        "          fetch-depth: 0",
        "",
        "      - name: Set up Python",
        "        if: steps.check_token.outputs.has_token == 'true'",
        "        uses: actions/setup-python@v5",
        "        with:",
        f"          python-version: \"{python_version}\"",
        "",
        "      - name: SonarQube Cloud Scan",
        "        if: steps.check_token.outputs.has_token == 'true'",
        "        uses: SonarSource/sonarqube-scan-action@v7",
        "        env:",
        "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}",
    ]
    return "\n".join(lines) + "\n"


def _generate_qlty_workflow() -> str:
    """Generate a GitHub Actions workflow for qlty checks on push/PR.

    Uses the official ``qltysh/qlty-action/install@main`` GitHub Action
    instead of ``curl | sh`` for reliable CI installs.
    """
    lines = [
        "name: Qlty Analysis",
        "",
        "on:",
        "  push:",
        "  pull_request:",
        "    types: [opened, synchronize, reopened]",
        "  workflow_dispatch:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "concurrency:",
        "  group: qlty-${{ github.workflow }}-${{ github.ref }}",
        "  cancel-in-progress: true",
        "",
        "jobs:",
        "  qlty:",
        "    name: Qlty Check",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - name: Checkout",
        "        uses: actions/checkout@v4",
        "",
        "      - name: Install qlty",
        "        uses: qltysh/qlty-action/install@main",
        "",
        "      - name: Run qlty checks",
        "        run: qlty check --all",
    ]
    return "\n".join(lines) + "\n"


def _compute_bandit_ci_skips(
    project_root: str | None,
    *,
    is_tool_runner: bool = False,
) -> list[str]:
    """Compute bandit CI skip list aligned with lintgate.yaml severity_overrides.

    - Always skip B101 (assert) and B108 (tmp files)
    - Skip any bandit code with severity_overrides == 'informational'
    - If tool-runner, also skip B404, B603, B607
    - Fail-open: missing/broken config → hardcoded defaults
    """
    base_skips = ["B101", "B108"]

    # Read severity_overrides from lintgate.yaml
    if project_root:
        config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
        if os.path.isfile(config_path):
            try:
                import yaml  # noqa: PLC0415

                with open(config_path) as f:
                    cfg = yaml.safe_load(f)
                if isinstance(cfg, dict):
                    overrides = cfg.get("severity_overrides", {})
                    if isinstance(overrides, dict):
                        for code, severity in overrides.items():
                            code_str = str(code)
                            if (
                                re.fullmatch(r"B\d+", code_str)
                                and str(severity).lower() == "informational"
                                and code_str not in base_skips
                            ):
                                base_skips.append(code_str)
            except Exception:  # noqa: BLE001
                pass  # Fail-open: broken config → use defaults

    if is_tool_runner:
        for code in ("B404", "B603", "B607"):
            if code not in base_skips:
                base_skips.append(code)

    # Sort for deterministic output (B-codes sort naturally)
    return sorted(base_skips)


def _generate_security_workflow(
    layout: dict[str, Any],
    *,
    is_tool_runner: bool = False,
    project_root: str | None = None,
) -> str:
    """Generate a lightweight security workflow for push/PR."""
    raw_version = str(layout.get("python_version", "3.11")).strip()
    python_version = raw_version if re.fullmatch(r"\d+(?:\.\d+)?", raw_version) else "3.11"

    bandit_skips = _compute_bandit_ci_skips(project_root, is_tool_runner=is_tool_runner)
    bandit_skip_str = ",".join(bandit_skips)

    lines = [
        "name: Security Lite",
        "",
        "on:",
        "  push:",
        "  pull_request:",
        "    types: [opened, synchronize, reopened]",
        "  workflow_dispatch:",
        "",
        "permissions:",
        "  contents: read",
        "  pull-requests: read",
        "",
        "concurrency:",
        "  group: security-lite-${{ github.workflow }}-${{ github.ref }}",
        "  cancel-in-progress: true",
        "",
        "jobs:",
        "  security:",
        "    name: Secrets + SAST + Supply Chain",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - name: Checkout full history",
        "        uses: actions/checkout@v4",
        "        with:",
        "          fetch-depth: 0",
        "",
        "      - name: Scan for committed secrets (gitleaks)",
        "        uses: gitleaks/gitleaks-action@v2",
        "        env:",
        "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
        "",
        "      - name: Set up Python",
        "        uses: actions/setup-python@v5",
        "        with:",
        f"          python-version: \"{python_version}\"",
        "",
        "      - name: Install security linters",
        "        run: |",
        "          python -m pip install --upgrade pip",
        "          python -m pip install bandit pip-audit",
        "",
        "      - name: Run Bandit (fast, scoped)",
        "        run: |",
        "          bandit -q -r . \\",
        "            -x tests,.venv,venv,env,__pycache__,.git,node_modules,docs \\",
        f"            -s {bandit_skip_str}",
        "",
        "      - name: Run pip-audit (requirements if present)",
        "        shell: bash",
        "        run: |",
        "          shopt -s nullglob",
        "          reqs=(requirements*.txt)",
        "          if [ ${#reqs[@]} -eq 0 ]; then",
        "            echo \"No requirements*.txt found; skipping pip-audit.\"",
        "            exit 0",
        "          fi",
        "          for f in \"${reqs[@]}\"; do",
        "            echo \"Auditing $f\"",
        "            pip-audit -r \"$f\"",
        "          done",
    ]
    return "\n".join(lines) + "\n"


def _compute_gitignore_additions(project_root: str) -> dict[str, Any]:
    """Compare standard patterns against existing .gitignore, return delta."""
    gitignore_path = os.path.join(project_root, ".gitignore")
    existing_lines: set[str] = set()
    gitignore_exists = os.path.exists(gitignore_path)

    if gitignore_exists:
        try:
            with open(gitignore_path) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        existing_lines.add(stripped)
        except OSError:
            pass

    # Compute missing patterns (only actual patterns, not comments/blanks)
    additions: list[str] = []
    already_present: list[str] = []
    for pat in _STANDARD_GITIGNORE_PATTERNS:
        if not pat or pat.startswith("#"):
            continue
        if pat in existing_lines:
            already_present.append(pat)
        else:
            additions.append(pat)

    return {
        "gitignore_exists": gitignore_exists,
        "existing_pattern_count": len(existing_lines),
        "additions": additions,
        "already_present": already_present,
    }


def _generate_badge_markdown(github: dict[str, Any], layout: dict[str, Any]) -> str:
    """Generate badge markdown for CI status, SonarCloud metrics, and License."""
    owner = github.get("owner", "OWNER")
    repo = github.get("repo", "REPO")
    project_key = re.sub(r"[^a-zA-Z0-9_.\-]", "_", f"{owner}_{repo}")

    badges: list[str] = [
        f"[![Security](https://github.com/{owner}/{repo}/actions/workflows/security-lite.yml/badge.svg)]"
        f"(https://github.com/{owner}/{repo}/actions/workflows/security-lite.yml)",
        f"[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?"
        f"project={project_key}&metric=alert_status)]"
        f"(https://sonarcloud.io/summary/new_code?id={project_key})",
        f"[![Coverage](https://sonarcloud.io/api/project_badges/measure?"
        f"project={project_key}&metric=coverage)]"
        f"(https://sonarcloud.io/summary/new_code?id={project_key})",
        f"[![Security Rating](https://sonarcloud.io/api/project_badges/measure?"
        f"project={project_key}&metric=security_rating)]"
        f"(https://sonarcloud.io/summary/new_code?id={project_key})",
    ]

    license_id = layout.get("license")
    if license_id:
        badge_label = _LICENSE_BADGE_MAP.get(license_id, license_id.replace("-", "_"))
        badges.append(
            f"[![License: {license_id}](https://img.shields.io/badge/License-{badge_label}-blue.svg)]"
            f"(https://opensource.org/licenses/{license_id})"
        )

    return "\n".join(badges)


def _inject_badges_into_readme(
    project_root: str,
    badge_markdown: str,
    write: bool,
) -> dict[str, Any]:
    """Find README, inject badges after title. Skip if badges already present."""
    root = Path(project_root)
    readme_path: Path | None = None
    for name in _README_NAMES:
        candidate = root / name
        if candidate.exists():
            readme_path = candidate
            break

    if readme_path is None:
        return {"status": "no_readme_found", "searched": list(_README_NAMES)}

    try:
        content = readme_path.read_text()
    except OSError as exc:
        return {"status": "read_error", "error": str(exc)}

    managed_block = f"{_BADGE_BLOCK_START}\n{badge_markdown}\n{_BADGE_BLOCK_END}"
    managed_pattern = re.compile(
        rf"{re.escape(_BADGE_BLOCK_START)}.*?{re.escape(_BADGE_BLOCK_END)}",
        flags=re.DOTALL,
    )
    managed_match = managed_pattern.search(content)
    if managed_match:
        existing_block = managed_match.group(0).strip()
        if existing_block == managed_block:
            return {
                "status": "badges_already_present",
                "path": str(readme_path),
                "source": "managed_block",
            }

        updated_content = managed_pattern.sub(managed_block, content, count=1)
        result: dict[str, Any] = {
            "status": "updated" if write else "preview_update",
            "path": str(readme_path),
        }
        if write:
            readme_path.write_text(updated_content)
        else:
            result["preview_snippet"] = managed_block
        return result

    if all(fp in content for fp in _REQUIRED_BADGE_FINGERPRINTS):
        return {
            "status": "badges_already_present",
            "path": str(readme_path),
            "source": "fingerprints",
        }

    # Find the first heading line and inject after it
    lines = content.splitlines(keepends=True)
    injection_index: int | None = None
    for i, line in enumerate(lines):
        if line.startswith("# "):
            injection_index = i + 1
            break

    if injection_index is None:
        # No heading found — prepend
        injection_index = 0

    # Build the injected block
    badge_block = f"\n{managed_block}\n\n"
    new_lines = lines[:injection_index] + [badge_block] + lines[injection_index:]
    new_content = "".join(new_lines)

    result: dict[str, Any] = {
        "status": "injected" if write else "preview",
        "path": str(readme_path),
        "injection_point": f"line {injection_index + 1}",
    }

    if write:
        readme_path.write_text(new_content)
    else:
        result["preview_snippet"] = badge_markdown

    return result


def _readme_has_quality_badges(project_root: str) -> bool:
    """Return True if README contains the minimum badge fingerprints."""
    root = Path(project_root)
    readme_path: Path | None = None
    for name in _README_NAMES:
        candidate = root / name
        if candidate.exists():
            readme_path = candidate
            break

    if readme_path is None:
        return False

    try:
        content = readme_path.read_text(errors="ignore")
    except OSError:
        return False

    if _BADGE_BLOCK_START in content and _BADGE_BLOCK_END in content:
        start = content.find(_BADGE_BLOCK_START)
        end = content.find(_BADGE_BLOCK_END, start)
        if end == -1:
            return False
        managed_block = content[start:end + len(_BADGE_BLOCK_END)]
        return all(fp in managed_block for fp in _REQUIRED_BADGE_FINGERPRINTS)

    return all(fp in content for fp in _REQUIRED_BADGE_FINGERPRINTS)


def _generate_qlty_toml(layout: dict[str, Any], *, is_tool_runner: bool = False) -> str:
    """Generate a tailored .qlty/qlty.toml with smart triage rules.

    Args:
        layout: Project layout from _detect_project_layout.
        is_tool_runner: If True, suppress subprocess-related bandit rules
            (B404/B603/B607) project-wide — appropriate for projects that
            invoke external tools by design.
    """
    test_dirs = layout.get("test_dirs", ["tests"])
    exclude_patterns = list(layout.get("exclude_patterns", []))

    # Add standard qlty exclude patterns
    qlty_excludes = [
        "*_min.*", "*-min.*", "*.min.*",
        "**/__pycache__/**", "**/.mypy_cache/**", "**/.ruff_cache/**",
        "**/.pytest_cache/**", "**/node_modules/**", "**/dist/**",
        "**/build/**", "**/vendor/**",
    ]
    # Merge project-specific excludes (de-duplicate)
    seen: set[str] = set()
    final_excludes: list[str] = []
    for pat in qlty_excludes + exclude_patterns:
        normalized = _normalize_qlty_exclude_pattern(pat)
        if not normalized:
            continue
        if normalized not in seen:
            seen.add(normalized)
            final_excludes.append(normalized)

    # Build test patterns from detected test dirs
    test_patterns = []
    for td in test_dirs:
        test_patterns.append(f"**/{td}/**")
    test_patterns.extend([
        "**/*.test.*", "**/*.spec.*", "**/*_test.*",
        "**/*_spec.*", "**/test_*.*", "**/spec_*.*",
    ])

    lines = [
        "# qlty configuration — generated by LintGate setup_github_quality",
        "# Docs: https://docs.qlty.sh/qlty-toml",
        "# Run:  qlty check --all",
        'config_version = "0"',
        "",
        "exclude_patterns = [",
    ]
    for pat in final_excludes:
        lines.append(f'  "{pat}",')
    lines.append("]")
    lines.append("")
    lines.append("test_patterns = [")
    for pat in test_patterns:
        lines.append(f'  "{pat}",')
    lines.append("]")
    lines.append("")

    lines.extend([
        "[smells]",
        'mode = "comment"',
        "",
        "[[source]]",
        'name = "default"',
        "default = true",
        "",
        "# ── Plugins ────────────────────────────────────────────────────",
        "",
        "[[plugin]]",
        'name = "bandit"',
        "",
        "[[plugin]]",
        'name = "radarlint-python"',
        'mode = "comment"',
        "",
        "[[plugin]]",
        'name = "ruff"',
        'drivers = ["lint"]',
        "",
        "# Keep default plugin set intentionally lean to reduce false positives in CI.",
        "# ── Triage: Silence domain-expected false positives ────────────",
        "",
    ])

    # Test-file triage rules
    test_file_patterns = []
    for td in test_dirs:
        test_file_patterns.append(f"**/{td}/**")
    test_file_patterns.append("**/test_*.*")

    for rule in _QLTY_TEST_TRIAGE_RULES:
        label = "assert in test files is standard pytest usage" if "B101" in rule else \
                "temp file usage in test fixtures is expected"
        lines.append(f"# {rule} — {label}")
        lines.append("[[triage]]")
        lines.append(f'match.rules = ["{rule}"]')
        patterns_str = ", ".join(f'"{p}"' for p in test_file_patterns)
        lines.append(f"match.file_patterns = [{patterns_str}]")
        lines.append("set.ignored = true")
        lines.append("")

    # Tool-runner triage rules (subprocess usage)
    if is_tool_runner:
        for rule in _QLTY_TOOL_RUNNER_TRIAGE_RULES:
            label = {
                "bandit:B404": "subprocess import is core to a tool runner",
                "bandit:B603": "subprocess calls with variable args are intentional",
                "bandit:B607": "invoking tools by name is the product",
            }.get(rule, "domain-expected")
            lines.append(f"# {rule} — {label}")
            lines.append("[[triage]]")
            lines.append(f'match.rules = ["{rule}"]')
            lines.append("set.ignored = true")
            lines.append("")

    # Monitor-mode rules
    for rule, comment in _QLTY_MONITOR_RULES:
        lines.append(f"# {rule} — {comment}")
        lines.append("[[triage]]")
        lines.append(f'match.rules = ["{rule}"]')
        lines.append('set.mode = "monitor"')
        lines.append("")

    return "\n".join(lines) + "\n"


def _normalize_qlty_exclude_pattern(pattern: str) -> str:
    """Normalize qlty exclude pattern without corrupting wildcard file globs."""
    pat = pattern.strip()
    if not pat:
        return ""
    if pat.endswith("/**"):
        return pat
    if pat.endswith("/"):
        return f"{pat}**"
    if any(ch in pat for ch in "*?[]"):
        return pat
    return f"{pat}/**"


def _detect_subprocess_usage(project_root: str) -> bool:
    """Return True if the project imports subprocess — indicating a tool-runner."""
    root = Path(project_root)
    for py_file in root.rglob("*.py"):
        # Skip test files, venvs
        parts = str(py_file)
        if any(seg in parts for seg in _VENV_SEGMENTS):
            continue
        if "/tests/" in parts or "/test/" in parts:
            continue
        try:
            content = py_file.read_text(errors="ignore")
            if "import subprocess" in content or "from subprocess" in content:
                return True
        except OSError:
            continue
    return False


def _detect_sonar_scanner() -> str | None:
    """Find pysonar-scanner or sonar-scanner executable."""
    for name in ("pysonar-scanner", "sonar-scanner"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _run_sonar_scanner(
    project_root: str,
    sonar_token: str,
    scanner_path: str,
) -> dict[str, Any]:
    """Execute sonar-scanner to push results to SonarCloud."""
    cmd = [scanner_path]
    if "pysonar-scanner" in scanner_path:
        cmd.extend([
            f"-Dproject.home={project_root}",
            "-read.project.config",
        ])
    else:
        cmd.extend([
            f"-Dsonar.projectBaseDir={project_root}",
        ])

    env = os.environ.copy()
    env["SONAR_TOKEN"] = sonar_token

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

        success = result.returncode == 0
        # Extract key info from output
        output_text = result.stdout + result.stderr
        analysis_url = None
        for line in output_text.splitlines():
            if "ANALYSIS SUCCESSFUL" in line or "task?id=" in line:
                success = True
            if "ceTaskUrl" in line or "dashboard/index" in line:
                # Try to extract URL
                import re as _re
                url_match = _re.search(r"(https?://\S+)", line)
                if url_match:
                    analysis_url = url_match.group(1)

        return {
            "status": "success" if success else "failed",
            "exit_code": result.returncode,
            "scanner": scanner_path,
            "analysis_url": analysis_url,
            "output_tail": output_text[-500:] if output_text else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "scanner": scanner_path}
    except (FileNotFoundError, OSError) as exc:
        return {"status": "error", "error": str(exc), "scanner": scanner_path}


def _build_quality_guidance(
    github: dict[str, Any],
    layout: dict[str, Any],
    scanner_path: str | None,
) -> dict[str, Any]:
    """Build comprehensive guidance for using qlty and sonar-scanner."""
    owner = github.get("owner", "OWNER")
    repo = github.get("repo", "REPO")
    project_key = re.sub(r"[^a-zA-Z0-9_.\-]", "_", f"{owner}_{repo}")

    guidance: dict[str, Any] = {
        "three_layer_stack": {
            "development": {
                "tool": "LintGate (18+ linters, PostToolUse hook)",
                "when": "Every code change — automatic via hook",
                "purpose": "Inline feedback, behavioral drift detection",
            },
            "local_validation": {
                "tool": "qlty check --all",
                "when": "Pre-commit or CI — independent second opinion",
                "purpose": "Code smells, security scanning, duplication detection",
                "install": "curl -fsSL https://qlty.sh | sh",
                "first_run": "qlty check --all",
                "workflow_path": ".github/workflows/qlty.yml",
            },
            "public_proof": {
                "tool": "SonarCloud (via sonar-scanner)",
                "when": "CI push — generates badges and quality gate",
                "purpose": "Public dashboard, quality gate, badges in README",
                "setup_url": f"https://sonarcloud.io/project/create?id={project_key}",
                "workflow_path": ".github/workflows/sonarcloud.yml",
            },
            "security_guardrail": {
                "tool": "Security Lite workflow (gitleaks + bandit + pip-audit)",
                "when": "Every push/PR",
                "purpose": "Cheap secret scanning and high-signal security checks",
                "workflow_path": ".github/workflows/security-lite.yml",
            },
        },
        "silencing_invalid_issues": {
            "qlty": {
                "method": "Add [[triage]] blocks to .qlty/qlty.toml",
                "example": (
                    '[[triage]]\nmatch.rules = ["bandit:B101"]\n'
                    'match.file_patterns = ["**/tests/**"]\nset.ignored = true'
                ),
                "docs": "https://docs.qlty.sh/qlty-toml",
            },
            "sonarcloud": {
                "method": "Mark issues as 'Won't Fix' in SonarCloud dashboard, "
                          "or configure quality profiles",
                "setup_url": f"https://sonarcloud.io/project/configuration?id={project_key}",
            },
            "lintgate": {
                "method": "Add severity overrides in .claude/lintgate.yaml",
                "docs": "See scaffold_config() or docs/reference.md",
            },
        },
    }

    # Scanner-specific guidance
    if scanner_path:
        guidance["sonar_scanner"] = {
            "local_run": (
                f"SONAR_TOKEN=<token> {scanner_path} "
                f"-Dproject.home=. -read.project.config"
            ),
            "workflow_path": ".github/workflows/sonarcloud.yml",
            "github_actions": (
                "Add SONAR_TOKEN as repository secret at:\n"
                f"https://github.com/{owner}/{repo}/settings/secrets/actions"
            ),
            "token_note": (
                "The SONAR_TOKEN is passed via environment variable — "
                "never committed to source control."
            ),
        }
    else:
        guidance["sonar_scanner"] = {
            "install": "pip install pysonar-scanner",
            "note": "sonar-scanner not found on PATH. Install to enable local analysis.",
            "workflow_path": ".github/workflows/sonarcloud.yml",
        }

    return guidance


def register(mcp, helpers):
    """Register onboarding tools on the shared MCP instance."""

    @mcp.tool()
    def getting_started(
        path: str,
        auto_setup: bool = True,
        auto_install_optional_linters: bool = True,
    ) -> str:
        """Start here. Get oriented with LintGate on any project.

        WHEN TO USE: First time using LintGate on a project, or when unsure
        what to do next. Returns project status, recommended next steps, and
        the essential tool workflow.

        Startup automation (default ON):
        - Auto-generates .claude/lintgate.yaml when missing
        - Auto-provisions project venv (.venv) with uv fallback to stdlib venv
        - Detects missing linter executables with install commands
        - Attempts auto-install of optional tools (ty, pip-audit) in project venv

        Example: getting_started(path="/my/project")
        """
        project_root = helpers["_validate_project_root"](path)
        config_path = os.path.join(project_root, ".claude", "lintgate.yaml")

        config_status_before = helpers["_build_onboarding_status"](project_root)
        startup_actions: list[dict[str, Any]] = []
        venv_setup: dict[str, Any] = {"status": "not_requested"}

        if auto_setup and not os.path.exists(config_path):
            yaml_content = _scaffold_config_yaml(project_root, helpers)
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                f.write(yaml_content)
            startup_actions.append(
                {
                    "action": "config_scaffolded",
                    "path": config_path,
                }
            )

        if auto_setup:
            venv_setup = _ensure_project_venv(project_root)
            if venv_setup.get("status") == "created":
                startup_actions.append(
                    {
                        "action": "venv_provisioned",
                        "manager": venv_setup.get("manager"),
                        "venv_python": venv_setup.get("venv_python"),
                        "pip_ready": venv_setup.get("pip_ready"),
                    }
                )
            elif venv_setup.get("status") in {"error", "timeout"}:
                startup_actions.append(
                    {
                        "action": "venv_provision_failed",
                        "manager": venv_setup.get("manager"),
                        "reason": venv_setup.get("reason", "unknown"),
                    }
                )

        tool_gaps_before = _collect_external_tool_gaps(project_root)
        install_attempts: list[dict[str, Any]] = []
        if auto_install_optional_linters and tool_gaps_before["missing_tools"]:
            install_attempts = _auto_install_optional_tools(
                project_root,
                tool_gaps_before["missing_tools"],
            )
            if install_attempts:
                startup_actions.append(
                    {
                        "action": "optional_tool_install_attempted",
                        "count": len(install_attempts),
                    }
                )

        config_status = helpers["_build_onboarding_status"](project_root)
        tool_gaps_after = _collect_external_tool_gaps(project_root)
        venv_python_after = _project_venv_python(project_root)

        # Build dynamic next_actions based on project state
        next_actions: list[dict[str, str]] = []
        if config_status["config_state"] != "config_enabled":
            next_actions.append(
                {
                    "tool": "controlplane_run",
                    "reason": "Run a comprehensive health check (works without config)",
                    "example": f'controlplane_run(path="{project_root}")',
                }
            )
        else:
            next_actions.append(
                {
                    "tool": "controlplane_run",
                    "reason": "Run a comprehensive health check",
                    "example": f'controlplane_run(path="{project_root}")',
                }
            )

        # Suggest scaffold_config when startup automation is disabled or config still not enabled
        if config_status["config_state"] != "config_enabled":
            next_actions.append(
                {
                    "tool": "scaffold_config",
                    "reason": "Generate/repair project-specific lintgate.yaml for persistent config",
                    "example": f'scaffold_config(path="{project_root}", write=True)',
                }
            )

        if not venv_python_after:
            create_cmd, _ = _venv_create_command()
            next_actions.append(
                {
                    "tool": "Bash",
                    "reason": "Create project virtual environment for tool installs and isolated runs",
                    "example": _format_cmd(create_cmd),
                }
            )

        # Surface explicit install actions if tools remain missing after auto-install.
        for gap in tool_gaps_after["missing_tools"]:
            next_actions.append(
                {
                    "tool": "Bash",
                    "reason": (
                        f"Install missing tool '{gap['tool']}' required by "
                        f"{', '.join(gap['required_by'])}"
                    ),
                    "example": gap["install_command"],
                }
            )

        # Check if bootstrap files exist
        claude_md = os.path.join(project_root, ".claude", "CLAUDE.md")
        if not os.path.exists(claude_md):
            next_actions.append(
                {
                    "tool": "bootstrap_context_files",
                    "reason": "Generate project-specific CLAUDE.md with documented principles",
                    "example": f'bootstrap_context_files(path="{project_root}", write=True)',
                }
            )

        # Auto-bootstrap GitHub quality/security infrastructure when possible.
        _gh = _detect_github_remote(project_root)
        _has_quality_configs = (
            os.path.exists(os.path.join(project_root, ".codeclimate.yml"))
            and os.path.exists(os.path.join(project_root, "sonar-project.properties"))
            and os.path.exists(os.path.join(project_root, ".qlty", "qlty.toml"))
            and os.path.exists(os.path.join(project_root, ".github", "workflows", "sonarcloud.yml"))
            and os.path.exists(os.path.join(project_root, ".github", "workflows", "qlty.yml"))
            and os.path.exists(os.path.join(project_root, ".github", "workflows", "security-lite.yml"))
            and _readme_has_quality_badges(project_root)
        )
        quality_bootstrap_result: dict[str, Any] = {"status": "not_requested"}
        if auto_setup and _gh.get("detected") and not _has_quality_configs:
            with suppress(Exception):
                quality_bootstrap_result = json.loads(
                    setup_github_quality(path=project_root, write=True),
                )
                startup_actions.append(
                    {
                        "action": "github_quality_bootstrapped",
                        "status": quality_bootstrap_result.get("status", "unknown"),
                    }
                )
                _has_quality_configs = (
                    os.path.exists(os.path.join(project_root, ".codeclimate.yml"))
                    and os.path.exists(os.path.join(project_root, "sonar-project.properties"))
                    and os.path.exists(os.path.join(project_root, ".qlty", "qlty.toml"))
                    and os.path.exists(os.path.join(project_root, ".github", "workflows", "sonarcloud.yml"))
                    and os.path.exists(os.path.join(project_root, ".github", "workflows", "qlty.yml"))
                    and os.path.exists(os.path.join(project_root, ".github", "workflows", "security-lite.yml"))
                    and _readme_has_quality_badges(project_root)
                )

        # Suggest GitHub quality setup when remote detected but configs remain missing.
        if _gh.get("detected") and not _has_quality_configs:
            next_actions.append(
                {
                    "tool": "setup_github_quality",
                    "reason": "Add code quality badges and GitHub CI infrastructure",
                    "example": f'setup_github_quality(path="{project_root}", write=True)',
                }
            )

        next_actions.append(
            {
                "tool": "lint_project",
                "reason": "Full project lint scan",
                "example": f'lint_project(path="{project_root}")',
            }
        )

        output: dict[str, Any] = {
            "project": project_root,
            "config_status": config_status,
            "essential_tools": {
                "lint_files": "Check specific files after edits — "
                'lint_files(files=["/path/to/file.py"])',
                "lint_project": 'Full project scan — lint_project(path="/my/project")',
                "lint_fix": 'Auto-fix safe issues — lint_fix(path="/my/project", dry_run=False)',
                "controlplane_run": "6-channel health check (lint + tests + deps + git + behavior + structure) — "
                'controlplane_run(path="/my/project")',
                "controlplane_get_details": "Drill into health check findings — "
                'controlplane_get_details(run_id="...")',
                "bootstrap_context_files": "Generate project CLAUDE.md — "
                'bootstrap_context_files(path="/my/project", write=True)',
            },
            "first_session_workflow": [
                "1. getting_started(path) applies startup setup automatically",
                "2. Run controlplane_run(path) for a full project health check",
                "3. Run controlplane_get_details(run_id) to review specific findings",
                "4. Run lint_fix(path, dry_run=False) to auto-fix safe issues",
                "5. Run bootstrap_context_files(path, write=true) to generate persistent context files",
            ],
            "all_tools_count": 49,
            "startup_setup": {
                "auto_setup_requested": auto_setup,
                "auto_install_optional_linters_requested": auto_install_optional_linters,
                "config_status_before": config_status_before,
                "config_status_after": config_status,
                "venv_setup": venv_setup,
                "venv_python": venv_python_after,
                "missing_tools_before": tool_gaps_before["missing_tools"],
                "install_attempts": install_attempts,
                "missing_tools_after": tool_gaps_after["missing_tools"],
                "actions_applied": startup_actions,
                "github_quality": quality_bootstrap_result,
                "startup_ready": (
                    config_status["config_state"] == "config_enabled"
                    and venv_python_after is not None
                    and len(tool_gaps_after["missing_tools"]) == 0
                ),
            },
            "next_actions": next_actions,
        }

        return json.dumps(output, indent=2)

    @mcp.tool()
    def scaffold_config(path: str, write: bool = False) -> str:
        """Generate a project-specific lintgate.yaml from observed signals.

        WHEN TO USE: After running controlplane_run and reviewing findings.
        Analyzes the project to produce a tailored config with:
        - ControlPlane enabled with sensible channel defaults
        - Severity overrides for domain-expected bandit findings
        - Pipeline critical paths from file-too-long / CC hotspots
        - Inquiry features enabled

        Default mode is non-destructive (write=false) — returns the YAML
        for review. Set write=true to create .claude/lintgate.yaml.

        Example: scaffold_config(path="/my/project", write=True)
        """
        project_root = helpers["_validate_project_root"](path)
        config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
        existing_config = os.path.exists(config_path)
        yaml_content = _scaffold_config_yaml(project_root, helpers)

        if write:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                f.write(yaml_content)

        status = "written" if write else ("preview_existing" if existing_config else "preview")
        output = {
            "status": status,
            "path": config_path,
            "yaml": yaml_content,
            "next_actions": [
                {
                    "tool": "controlplane_run",
                    "reason": "Verify the new config works correctly",
                    "example": f'controlplane_run(path="{project_root}")',
                },
            ],
        }
        if existing_config and not write:
            output["message"] = "Config already exists. Returning scaffold preview only."
        return json.dumps(output, indent=2)

    @mcp.tool()
    def setup_github_quality(
        path: str,
        write: bool = False,
        sonar_token: str | None = None,
    ) -> str:
        """Set up GitHub code quality badges and infrastructure for a project.

        WHEN TO USE: After getting_started when you want to add code quality
        badges and CI configuration to a project. Detects GitHub remote,
        project layout, and generates tailored configs for Code Climate,
        SonarCloud, qlty CLI, .gitignore augmentation, and README badge injection.

        Generates eight artifacts:
        - .codeclimate.yml — Code Climate / qlty Cloud config
        - sonar-project.properties — SonarCloud scanner config
        - .github/workflows/sonarcloud.yml — SonarCloud analysis on push/PR
        - .github/workflows/qlty.yml — qlty analysis on push/PR
        - .github/workflows/security-lite.yml — secrets + SAST + supply-chain checks
        - .qlty/qlty.toml — qlty analysis config with smart triage (commit to repo)
        - .gitignore augmentation — standard Python patterns
        - README badge injection — quality badges after title

        Default mode is non-destructive (write=false) — returns previews
        of all generated files. Set write=true to create/modify files.

        When sonar_token is provided with write=true, runs sonar-scanner
        to push initial analysis to SonarCloud (activates the badge).
        The token is passed via environment variable — never written to
        any file that could be committed.

        Example: setup_github_quality(path="/my/project", write=True)
        Example: setup_github_quality(path="/my/project", write=True,
                 sonar_token="your_token_here")
        """
        project_root = helpers["_validate_project_root"](path)
        github = _detect_github_remote(project_root)
        layout = _detect_project_layout(project_root)

        # Detect if project is a tool-runner (uses subprocess)
        is_tool_runner = _detect_subprocess_usage(project_root)

        # --- .codeclimate.yml ---
        cc_path = os.path.join(project_root, ".codeclimate.yml")
        cc_exists = os.path.exists(cc_path)
        cc_content = _generate_codeclimate_yml(layout)
        cc_result: dict[str, Any] = {"path": cc_path}

        if cc_exists:
            cc_result["status"] = "already_exists"
        elif write:
            with open(cc_path, "w") as f:
                f.write(cc_content)
            cc_result["status"] = "written"
        else:
            cc_result["status"] = "preview"
            cc_result["content"] = cc_content

        # --- sonar-project.properties ---
        sonar_path = os.path.join(project_root, "sonar-project.properties")
        sonar_exists = os.path.exists(sonar_path)
        sonar_content = _generate_sonar_properties(github, layout)
        sonar_result: dict[str, Any] = {"path": sonar_path}

        if sonar_exists:
            sonar_result["status"] = "already_exists"
        elif write:
            with open(sonar_path, "w") as f:
                f.write(sonar_content)
            sonar_result["status"] = "written"
        else:
            sonar_result["status"] = "preview"
            sonar_result["content"] = sonar_content

        # --- .github/workflows/sonarcloud.yml ---
        workflow_path = os.path.join(project_root, ".github", "workflows", "sonarcloud.yml")
        workflow_exists = os.path.exists(workflow_path)
        workflow_content = _generate_sonar_workflow(layout)
        workflow_result: dict[str, Any] = {"path": workflow_path}

        if workflow_exists:
            workflow_result["status"] = "already_exists"
        elif write:
            os.makedirs(os.path.dirname(workflow_path), exist_ok=True)
            with open(workflow_path, "w") as f:
                f.write(workflow_content)
            workflow_result["status"] = "written"
        else:
            workflow_result["status"] = "preview"
            workflow_result["content"] = workflow_content

        # --- .github/workflows/qlty.yml ---
        qlty_workflow_path = os.path.join(project_root, ".github", "workflows", "qlty.yml")
        qlty_workflow_exists = os.path.exists(qlty_workflow_path)
        qlty_workflow_content = _generate_qlty_workflow()
        qlty_workflow_result: dict[str, Any] = {"path": qlty_workflow_path}

        if qlty_workflow_exists:
            qlty_workflow_result["status"] = "already_exists"
        elif write:
            os.makedirs(os.path.dirname(qlty_workflow_path), exist_ok=True)
            with open(qlty_workflow_path, "w") as f:
                f.write(qlty_workflow_content)
            qlty_workflow_result["status"] = "written"
        else:
            qlty_workflow_result["status"] = "preview"
            qlty_workflow_result["content"] = qlty_workflow_content

        # --- .github/workflows/security-lite.yml ---
        security_workflow_path = os.path.join(project_root, ".github", "workflows", "security-lite.yml")
        security_workflow_exists = os.path.exists(security_workflow_path)
        security_workflow_content = _generate_security_workflow(
            layout, is_tool_runner=is_tool_runner, project_root=project_root,
        )
        security_workflow_result: dict[str, Any] = {"path": security_workflow_path}

        if security_workflow_exists:
            security_workflow_result["status"] = "already_exists"
        elif write:
            os.makedirs(os.path.dirname(security_workflow_path), exist_ok=True)
            with open(security_workflow_path, "w") as f:
                f.write(security_workflow_content)
            security_workflow_result["status"] = "written"
        else:
            security_workflow_result["status"] = "preview"
            security_workflow_result["content"] = security_workflow_content

        # --- .qlty/qlty.toml ---
        qlty_dir = os.path.join(project_root, ".qlty")
        qlty_path = os.path.join(qlty_dir, "qlty.toml")
        qlty_exists = os.path.exists(qlty_path)
        qlty_content = _generate_qlty_toml(layout, is_tool_runner=is_tool_runner)
        qlty_result: dict[str, Any] = {
            "path": qlty_path,
            "is_tool_runner": is_tool_runner,
            "local_only": False,
            "tracked_in_git": True,
            "note": ".qlty/qlty.toml is intended to be committed so CI matches local triage.",
        }

        qlty_gitignore = os.path.join(qlty_dir, ".gitignore")
        qlty_gitignore_written = False
        if qlty_exists:
            qlty_result["status"] = "already_exists"
        elif write:
            os.makedirs(qlty_dir, exist_ok=True)
            with open(qlty_path, "w") as f:
                f.write(qlty_content)
            qlty_result["status"] = "written"
        else:
            qlty_result["status"] = "preview"
            qlty_result["content"] = qlty_content

        if write:
            os.makedirs(qlty_dir, exist_ok=True)
            if not os.path.exists(qlty_gitignore):
                with open(qlty_gitignore, "w") as f:
                    f.write("logs\nout\nplugin_cachedir\nresults\n")
                qlty_gitignore_written = True
        qlty_result["gitignore_path"] = qlty_gitignore
        qlty_result["gitignore_written"] = qlty_gitignore_written

        # --- .gitignore ---
        gi_info = _compute_gitignore_additions(project_root)
        gi_result: dict[str, Any] = {
            "existing_pattern_count": gi_info["existing_pattern_count"],
            "additions_count": len(gi_info["additions"]),
            "already_present_count": len(gi_info["already_present"]),
        }

        if not gi_info["additions"]:
            gi_result["status"] = "no_changes_needed"
        elif write:
            gi_path = os.path.join(project_root, ".gitignore")
            with open(gi_path, "a") as f:
                if gi_info["gitignore_exists"]:
                    f.write("\n")
                f.write("# Added by LintGate setup_github_quality\n")
                for pat in gi_info["additions"]:
                    f.write(f"{pat}\n")
            gi_result["status"] = "augmented" if gi_info["gitignore_exists"] else "created"
            gi_result["patterns_added"] = gi_info["additions"]
        else:
            gi_result["status"] = "preview"
            gi_result["additions"] = gi_info["additions"]

        # --- README badges ---
        badge_result: dict[str, Any] = {}
        if github.get("detected"):
            badge_md = _generate_badge_markdown(github, layout)
            readme_result = _inject_badges_into_readme(project_root, badge_md, write)
            badge_result = readme_result
            badge_result["markdown"] = badge_md
            badge_result["codeclimate_note"] = (
                "Replace PLACEHOLDER with your Code Climate badge token after "
                "connecting your repo at https://codeclimate.com"
            )
        else:
            badge_result["status"] = "skipped"
            badge_result["reason"] = "no_github_remote_detected"

        # --- SonarCloud scanner execution ---
        scanner_result: dict[str, Any] = {"status": "not_requested"}
        scanner_path = _detect_sonar_scanner()

        if sonar_token and write:
            if not scanner_path:
                scanner_result = {
                    "status": "scanner_not_found",
                    "install": "pip install pysonar-scanner",
                    "note": "Install sonar-scanner to push analysis to SonarCloud.",
                }
            elif not os.path.exists(
                os.path.join(project_root, "sonar-project.properties")
            ):
                scanner_result = {
                    "status": "no_config",
                    "note": "sonar-project.properties must exist before scanning.",
                }
            else:
                scanner_result = _run_sonar_scanner(
                    project_root, sonar_token, scanner_path,
                )
        elif sonar_token and not write:
            scanner_result = {
                "status": "preview",
                "note": "Scanner will run when write=True. Token will be passed "
                        "via SONAR_TOKEN env var (never written to disk).",
                "scanner_found": scanner_path is not None,
            }

        # --- Guidance ---
        guidance = _build_quality_guidance(github, layout, scanner_path)

        # --- Next actions ---
        owner = github.get("owner", "OWNER")
        repo = github.get("repo", "REPO")
        next_actions: list[dict[str, str]] = []

        files_to_stage: list[str] = []
        if cc_result.get("status") == "written":
            files_to_stage.append(".codeclimate.yml")
        if sonar_result.get("status") == "written":
            files_to_stage.append("sonar-project.properties")
        if workflow_result.get("status") == "written":
            files_to_stage.append(".github/workflows/sonarcloud.yml")
        if qlty_workflow_result.get("status") == "written":
            files_to_stage.append(".github/workflows/qlty.yml")
        if security_workflow_result.get("status") == "written":
            files_to_stage.append(".github/workflows/security-lite.yml")
        if qlty_result.get("status") == "written":
            files_to_stage.append(".qlty/qlty.toml")
        if qlty_result.get("gitignore_written"):
            files_to_stage.append(".qlty/.gitignore")
        if gi_result.get("status") in ("augmented", "created"):
            files_to_stage.append(".gitignore")
        if badge_result.get("status") in {"injected", "updated"}:
            files_to_stage.append("README.md")

        if files_to_stage:
            next_actions.append({
                "tool": "Bash",
                "reason": "Stage and commit quality infrastructure",
                "example": (
                    f"git add {' '.join(files_to_stage)} && "
                    "git commit -m 'Add quality and security infrastructure (Code Climate + SonarCloud + qlty + security-lite)'"
                ),
            })

        if github.get("detected"):
            if not sonar_token:
                next_actions.append({
                    "tool": "Bash",
                    "reason": "Configure GitHub Actions secret required by SonarCloud workflow",
                    "example": (
                        f"gh secret set SONAR_TOKEN --repo {owner}/{repo} --body '<your_sonar_token>'"
                    ),
                })
                next_actions.append({
                    "tool": "setup_github_quality",
                    "reason": "Run sonar-scanner to push initial analysis and activate badge",
                    "example": (
                        f'setup_github_quality(path="{project_root}", '
                        'write=True, sonar_token="<your_token>")'
                    ),
                })
            next_actions.append({
                "tool": "Bash",
                "reason": "Connect repo to Code Climate, then replace PLACEHOLDER in README badge",
                "example": f"open https://codeclimate.com/github/{owner}/{repo}",
            })

        # Suggest qlty check if qlty is available
        qlty_path_bin = shutil.which("qlty")
        if qlty_path_bin:
            next_actions.append({
                "tool": "Bash",
                "reason": "Run qlty local analysis for independent code quality check",
                "example": f"cd {project_root} && qlty check --all",
            })

        output: dict[str, Any] = {
            "status": "written" if write else "preview",
            "github": github,
            "layout": layout,
            "codeclimate": cc_result,
            "sonar": sonar_result,
            "workflow": workflow_result,
            "qlty_workflow": qlty_workflow_result,
            "security_workflow": security_workflow_result,
            "qlty": qlty_result,
            "gitignore": gi_result,
            "badges": badge_result,
            "scanner": scanner_result,
            "guidance": guidance,
            "next_actions": next_actions,
        }

        return json.dumps(output, indent=2)

    return {
        "getting_started": getting_started,
        "scaffold_config": scaffold_config,
        "setup_github_quality": setup_github_quality,
    }
