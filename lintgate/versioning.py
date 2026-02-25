"""Tool version auditing and optional repair helpers.

Tracks version compatibility between lintgate linters and project requirements.
Supports:
- Detecting missing/mismatched tool versions
- Optional auto-repair via pip install
- Verification via pip check after repair
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version


@dataclass(frozen=True)
class ToolSpec:
    """Metadata for a tool version target."""

    tool: str
    package: str | None
    executable: str | None


_TRACKED_TOOLS: dict[str, ToolSpec] = {
    "python": ToolSpec(tool="python", package=None, executable=None),
    "ruff": ToolSpec(tool="ruff", package="ruff", executable="ruff"),
    "mypy": ToolSpec(tool="mypy", package="mypy", executable="mypy"),
    "ty": ToolSpec(tool="ty", package="ty", executable="ty"),
    "radon": ToolSpec(tool="radon", package="radon", executable="radon"),
    "bandit": ToolSpec(tool="bandit", package="bandit", executable="bandit"),
    "vulture": ToolSpec(tool="vulture", package="vulture", executable="vulture"),
    "pip-audit": ToolSpec(tool="pip-audit", package="pip-audit", executable="pip-audit"),
}

_REQUIREMENTS_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.in",
)


def run_version_audit(
    project_root: str,
    config_requirements: dict[str, str] | None = None,
    auto_fix: bool = False,
    verify_after_fix: bool = True,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Run version compatibility checks and optionally repair mismatches."""
    requirements = collect_required_version_specs(project_root, config_requirements)
    observations = inspect_tool_versions(requirements, project_root=project_root)
    issues = [o for o in observations if o["status"] != "ok"]

    result: dict[str, Any] = {
        "project": project_root,
        "timestamp": time.time(),
        "requirements": requirements,
        "tools": observations,
        "issues": issues,
        "issue_count": len(issues),
        "auto_fix_applied": False,
    }

    if not auto_fix:
        return result

    python_exec = python_executable or sys.executable
    fixes = _attempt_repairs(issues, python_exec)
    verification = _verify_environment(python_exec) if verify_after_fix else None

    post_observations = inspect_tool_versions(requirements, project_root=project_root)
    unresolved = [o for o in post_observations if o["status"] != "ok"]

    result["auto_fix_applied"] = True
    result["fixes"] = fixes
    result["verification"] = verification
    result["post_fix_tools"] = post_observations
    result["post_fix_issues"] = unresolved
    result["post_fix_issue_count"] = len(unresolved)
    return result


def collect_required_version_specs(
    project_root: str,
    config_requirements: dict[str, str] | None = None,
    enforced_groups: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Collect tool version requirements from project metadata and config."""
    requirements: dict[str, dict[str, Any]] = {
        tool: {"specifiers": [], "sources": [], "is_optional": []} for tool in _TRACKED_TOOLS
    }

    if config_requirements:
        for tool, specifier in config_requirements.items():
            canonical = _canonical_tool_name(tool)
            if canonical in requirements and str(specifier).strip():
                requirements[canonical]["specifiers"].append(str(specifier).strip())
                requirements[canonical]["sources"].append(".claude/lintgate.yaml:tool_versions")
                requirements[canonical]["is_optional"].append(False)

    project_path = Path(project_root)
    _collect_from_pyproject(project_path, requirements, enforced_groups=enforced_groups)
    _collect_from_requirements_files(project_path, requirements)

    # Normalize unique specifiers while preserving order.
    for tool in requirements:
        seen: set[str] = set()
        deduped_specs = []
        is_optional = True
        for i, spec in enumerate(requirements[tool]["specifiers"]):
            if spec not in seen:
                seen.add(spec)
                deduped_specs.append(spec)
            # If ANY source says it's NOT optional, then the combined requirement is NOT optional
            if not requirements[tool]["is_optional"][i]:
                is_optional = False

        requirements[tool]["specifiers"] = deduped_specs
        requirements[tool]["combined_specifier"] = ",".join(deduped_specs)
        requirements[tool]["is_optional_combined"] = is_optional if deduped_specs else False

    return requirements


def inspect_tool_versions(
    requirements: dict[str, dict[str, Any]],
    project_root: str | None = None,
) -> list[dict[str, Any]]:
    """Inspect installed versions against collected requirements."""
    observations: list[dict[str, Any]] = []

    for tool_name, spec in sorted(_TRACKED_TOOLS.items()):
        req_info = requirements.get(
            tool_name,
            {
                "specifiers": [],
                "sources": [],
                "is_optional": [],
                "combined_specifier": "",
            },
        )
        required_spec = str(req_info.get("combined_specifier", "") or "")
        installed_version = _installed_version(spec, project_root=project_root)
        executable_path = _which(spec.executable, project_root=project_root)

        status = "ok"
        message = "Version satisfies requirements"

        if required_spec:
            if installed_version is None:
                status = "missing"
                message = f"{tool_name} is not installed but required ({required_spec})"
            elif not _version_satisfies(installed_version, required_spec):
                status = "mismatch"
                message = (
                    f"{tool_name} version {installed_version} does not satisfy "
                    f"required specifier {required_spec}"
                )
        if (
            status == "ok"
            and spec.executable
            and executable_path is None
            and (required_spec or installed_version is not None)
        ):
            status = "missing-executable"
            message = f"Executable '{spec.executable}' is missing from PATH"

        suggestion = _suggest_fix_command(spec, required_spec)

        observations.append(
            {
                "tool": tool_name,
                "required_specifier": required_spec,
                "requirement_sources": req_info.get("sources", []),
                "is_optional": req_info.get("is_optional_combined", False),
                "installed_version": installed_version,
                "executable_path": executable_path,
                "status": status,
                "message": message,
                "suggested_fix": suggestion,
            }
        )

    return observations


def _collect_from_pyproject(
    project_path: Path,
    requirements: dict[str, dict[str, Any]],
    enforced_groups: list[str] | None = None,
) -> None:
    """Collect relevant requirements from pyproject.toml."""
    pyproject = project_path / "pyproject.toml"
    if not pyproject.exists():
        return

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return

    project = data.get("project", {})
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        _ensure_entry(requirements, "python")
        requirements["python"]["specifiers"].append(requires_python.strip())
        requirements["python"]["sources"].append("pyproject.toml:project.requires-python")
        requirements["python"]["is_optional"].append(False)

    dep_groups: list[tuple[str, list[str], bool]] = []
    deps = project.get("dependencies", [])
    if isinstance(deps, list):
        dep_groups.append(("pyproject.toml:project.dependencies", deps, False))

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for group_name, group_deps in optional.items():
            if isinstance(group_deps, list):
                is_enforced = (enforced_groups is not None) and (group_name in enforced_groups)
                dep_groups.append(
                    (
                        f"pyproject.toml:project.optional-dependencies.{group_name}",
                        group_deps,
                        not is_enforced,
                    )
                )

    for source, entries, is_opt in dep_groups:
        for entry in entries:
            parsed = _parse_requirement_entry(entry)
            if not parsed:
                continue
            tool_name, specifier = parsed
            if specifier:
                _ensure_entry(requirements, tool_name)
                requirements[tool_name]["specifiers"].append(specifier)
                requirements[tool_name]["sources"].append(source)
                requirements[tool_name]["is_optional"].append(is_opt)


def _collect_from_requirements_files(
    project_path: Path,
    requirements: dict[str, dict[str, Any]],
) -> None:
    """Collect relevant requirements from requirements*.txt style files."""
    for filename in _REQUIREMENTS_FILES:
        req_path = project_path / filename
        if not req_path.exists():
            continue
        try:
            lines = req_path.read_text().splitlines()
        except OSError:
            continue

        for idx, line in enumerate(lines, 1):
            parsed = _parse_requirement_entry(line)
            if not parsed:
                continue
            tool_name, specifier = parsed
            if specifier:
                _ensure_entry(requirements, tool_name)
                requirements[tool_name]["specifiers"].append(specifier)
                requirements[tool_name]["sources"].append(f"{filename}:{idx}")
                requirements[tool_name]["is_optional"].append(False)


def _ensure_entry(requirements: dict[str, dict[str, Any]], tool_name: str) -> None:
    """Ensure a tool entry has all required keys."""
    if tool_name not in requirements:
        requirements[tool_name] = {"specifiers": [], "sources": [], "is_optional": []}
    else:
        entry = requirements[tool_name]
        for key in ("specifiers", "sources", "is_optional"):
            if key not in entry:
                entry[key] = []


def _parse_requirement_entry(entry: str) -> tuple[str, str] | None:
    """Parse a requirement string into tracked-tool name and specifier."""
    line = entry.strip()
    if not line or line.startswith("#"):
        return None
    # Skip pip options and VCS/URL requirements.
    if line.startswith("-") or "://" in line or line.startswith("git+"):
        return None
    line = re.sub(r"\s+#.*$", "", line).strip()
    if not line:
        return None

    try:
        req = Requirement(line)
    except Exception:
        return None

    tool_name = _tool_from_package(req.name)
    if not tool_name:
        return None
    return tool_name, str(req.specifier)


def _tool_from_package(package_name: str) -> str | None:
    """Map package name to tracked tool name."""
    normalized = _normalize_name(package_name)
    for tool_name, spec in _TRACKED_TOOLS.items():
        if spec.package and _normalize_name(spec.package) == normalized:
            return tool_name
    return None


def _canonical_tool_name(tool: str) -> str:
    """Normalize user-provided tool aliases."""
    normalized = _normalize_name(tool)
    if normalized in _TRACKED_TOOLS:
        return normalized
    for tool_name, spec in _TRACKED_TOOLS.items():
        if spec.package and _normalize_name(spec.package) == normalized:
            return tool_name
    return normalized


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _find_venv_bin(project_root: str | None) -> str | None:
    """Probe for a virtual environment bin directory in the project.

    Checks .venv/bin/, venv/bin/, env/bin/ (in that order).
    Returns the bin directory path if found, else None.
    """
    if not project_root:
        return None
    for venv_name in (".venv", "venv", "env"):
        bin_dir = Path(project_root) / venv_name / "bin"
        if bin_dir.is_dir():
            return str(bin_dir)
    return None


def _installed_version(
    spec: ToolSpec,
    project_root: str | None = None,
) -> str | None:
    """Get installed version string for a tool.

    When project_root is provided and contains a venv, queries the venv's
    Python for package version instead of using the host interpreter's metadata.
    """
    if spec.tool == "python":
        # For Python version, check venv's python if available
        venv_bin = _find_venv_bin(project_root)
        if venv_bin:
            venv_python = Path(venv_bin) / "python"
            if venv_python.exists():
                try:
                    result = subprocess.run(
                        [
                            str(venv_python),
                            "-c",
                            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        return result.stdout.strip()
                except (subprocess.TimeoutExpired, OSError):
                    pass
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    if not spec.package:
        return None

    # Try venv Python for package version query
    venv_bin = _find_venv_bin(project_root)
    if venv_bin:
        venv_python = Path(venv_bin) / "python"
        if venv_python.exists():
            try:
                result = subprocess.run(
                    [
                        str(venv_python),
                        "-c",
                        f"import importlib.metadata; print(importlib.metadata.version('{spec.package}'))",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except (subprocess.TimeoutExpired, OSError):
                pass
            # Package not found in venv — fall through to host check
            if result.returncode != 0:
                return None

    # Fallback: host interpreter metadata
    try:
        return importlib.metadata.version(spec.package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _which(
    executable: str | None,
    project_root: str | None = None,
) -> str | None:
    """Locate an executable, preferring project venv over system PATH."""
    if not executable:
        return None

    # Check venv bin first
    venv_bin = _find_venv_bin(project_root)
    if venv_bin:
        venv_path = Path(venv_bin) / executable
        if venv_path.exists() and venv_path.is_file():
            return str(venv_path)

    return shutil.which(executable)


def _version_satisfies(version: str, specifier: str) -> bool:
    """Check whether a version satisfies a PEP 440 specifier set."""
    try:
        return Version(version) in SpecifierSet(specifier)
    except Exception:
        return False


def _suggest_fix_command(spec: ToolSpec, specifier: str) -> str | None:
    """Build a pip install suggestion for a mismatched tool."""
    if spec.tool == "python" or not spec.package:
        return None
    target = f"{spec.package}{specifier}" if specifier else spec.package
    return f"{sys.executable} -m pip install '{target}'"


def _attempt_repairs(
    issues: list[dict[str, Any]],
    python_exec: str,
) -> list[dict[str, Any]]:
    """Attempt repairs for missing/mismatched package versions."""
    fixes: list[dict[str, Any]] = []
    for issue in issues:
        tool_name = issue.get("tool", "")
        status = issue.get("status", "")
        spec = _TRACKED_TOOLS.get(tool_name)
        if not spec or not spec.package:
            continue
        if status not in {"missing", "mismatch", "missing-executable"}:
            continue

        required_spec = str(issue.get("required_specifier", "") or "")
        target = f"{spec.package}{required_spec}" if required_spec else spec.package
        cmd = [python_exec, "-m", "pip", "install", target]
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            fixes.append(
                {
                    "tool": tool_name,
                    "command": cmd,
                    "returncode": proc.returncode,
                    "duration_ms": elapsed_ms,
                    "stdout_tail": _tail(proc.stdout),
                    "stderr_tail": _tail(proc.stderr),
                    "success": proc.returncode == 0,
                }
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            fixes.append(
                {
                    "tool": tool_name,
                    "command": cmd,
                    "returncode": None,
                    "duration_ms": elapsed_ms,
                    "stdout_tail": _tail(exc.stdout or ""),
                    "stderr_tail": _tail(exc.stderr or ""),
                    "success": False,
                    "error": "pip install timed out after 300s",
                }
            )

    return fixes


def _verify_environment(python_exec: str) -> dict[str, Any]:
    """Run pip check to verify package dependency health."""
    cmd = [python_exec, "-m", "pip", "check"]
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "command": cmd,
            "returncode": proc.returncode,
            "duration_ms": elapsed_ms,
            "stdout": _tail(proc.stdout, 2000),
            "stderr": _tail(proc.stderr, 2000),
            "ok": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "command": cmd,
            "returncode": None,
            "duration_ms": elapsed_ms,
            "stdout": _tail(exc.stdout or "", 2000),
            "stderr": _tail(exc.stderr or "", 2000),
            "ok": False,
            "error": "pip check timed out after 120s",
        }


def _tail(text: str, max_chars: int = 1200) -> str:
    """Return the trailing slice of potentially long command output."""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def format_version_audit_summary(audit: dict[str, Any]) -> dict[str, Any]:
    """Generate a compact summary from a full version-audit payload."""
    issues = audit.get("issues", [])
    post_fix = audit.get("post_fix_issues", [])
    return {
        "timestamp": audit.get("timestamp"),
        "issue_count": len(issues),
        "post_fix_issue_count": len(post_fix) if isinstance(post_fix, list) else None,
        "auto_fix_applied": bool(audit.get("auto_fix_applied")),
        "tools_checked": len(audit.get("tools", [])),
    }
