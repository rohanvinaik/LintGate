"""Toolchain manifest — declarative tool provisioning for LintGate.

Reads tool requirements from gate_contract.yaml, discovers installed state,
reconciles gaps against the linter registry, and optionally auto-installs.

Two layers:
  - Project deps (pyproject.toml → lockfile → venv) → dep_health_check/dep_sync
  - Toolchain (CLI tools LintGate depends on) → THIS MODULE

The manifest in gate_contract.yaml is the single source of truth.
Adding a new linter with required_tool="foo" that isn't in the manifest
triggers a drift warning during reconciliation.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolRequirement:
    """A single tool the project depends on."""

    id: str
    kind: str  # python_cli, native_binary, custom_installer
    package: str  # pip package name (for python_cli) or empty
    version_spec: str  # e.g. ">=0.4.0" or ""
    required: bool
    required_by: list[str]  # which gates/linters need it
    install_commands: dict[str, str]  # platform → command string
    auto_install: bool  # whether getting_started may install it


@dataclass
class ToolStatus:
    """Runtime health of a single tool."""

    id: str
    requirement: ToolRequirement
    installed: bool
    version: str | None = None
    location: str | None = None  # path to executable
    install_hint: str = ""


@dataclass
class ManifestReport:
    """Full toolchain health report."""

    tools: list[ToolStatus] = field(default_factory=list)
    drift_warnings: list[str] = field(default_factory=list)
    all_required_met: bool = True
    summary: str = ""


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

_DEFAULT_TOOLCHAIN: list[dict[str, Any]] = [
    # Core linter — always needed
    {
        "id": "ruff",
        "kind": "python_cli",
        "package": "ruff>=0.4.0",
        "required": True,
        "required_by": ["lint"],
        "auto_install": True,
    },
    # Optional linters
    {
        "id": "mypy",
        "kind": "python_cli",
        "package": "mypy>=1.8",
        "required": False,
        "required_by": ["lint"],
        "auto_install": True,
    },
    {
        "id": "ty",
        "kind": "python_cli",
        "package": "ty>=0.0.17",
        "required": False,
        "required_by": ["lint"],
        "auto_install": True,
    },
    {
        "id": "radon",
        "kind": "python_cli",
        "package": "radon>=6.0",
        "required": False,
        "required_by": ["lint"],
        "auto_install": True,
    },
    {
        "id": "bandit",
        "kind": "python_cli",
        "package": "bandit>=1.7",
        "required": False,
        "required_by": ["lint", "ci"],
        "auto_install": True,
    },
    {
        "id": "pip-audit",
        "kind": "python_cli",
        "package": "pip-audit>=2.6",
        "required": False,
        "required_by": ["pre_push", "ci"],
        "auto_install": True,
    },
    # Gate stack tools
    {
        "id": "qlty",
        "kind": "native_binary",
        "package": "",
        "required": True,
        "required_by": ["pre_push", "ci"],
        "install": {
            "darwin": "brew install qlty || curl -sSL https://qlty.sh/install | sh",
            "linux": "curl -sSL https://qlty.sh/install | sh",
        },
        "auto_install": False,
    },
    {
        "id": "gitleaks",
        "kind": "native_binary",
        "package": "",
        "required": True,
        "required_by": ["pre_push", "ci"],
        "install": {
            "darwin": "brew install gitleaks",
            "linux": "brew install gitleaks",
        },
        "auto_install": False,
    },
]


def _parse_version_spec(package_with_version: str) -> tuple[str, str]:
    """Split 'ruff>=0.4.0' into ('ruff', '>=0.4.0')."""
    for op in (">=", "<=", "==", "!=", "~=", ">", "<"):
        if op in package_with_version:
            idx = package_with_version.index(op)
            return package_with_version[:idx].strip(), package_with_version[
                idx:
            ].strip()
    return package_with_version.strip(), ""


def _parse_tool_entry(entry: dict[str, Any]) -> ToolRequirement:
    """Parse a single toolchain entry from YAML into a ToolRequirement."""
    package_raw = entry.get("package", "")
    if package_raw:
        pkg_name, version_spec = _parse_version_spec(package_raw)
    else:
        pkg_name = ""
        version_spec = ""

    install_cmds = entry.get("install", {})
    if not isinstance(install_cmds, dict):
        install_cmds = {}

    required_by = entry.get("required_by", [])
    if isinstance(required_by, str):
        required_by = [required_by]

    return ToolRequirement(
        id=entry["id"],
        kind=entry.get("kind", "python_cli"),
        package=pkg_name or entry["id"],
        version_spec=version_spec,
        required=entry.get("required", False),
        required_by=required_by,
        install_commands=install_cmds,
        auto_install=entry.get("auto_install", False),
    )


def load_toolchain_manifest(project_root: str) -> list[ToolRequirement]:
    """Load tool requirements from gate_contract.yaml, falling back to defaults."""
    contract_path = Path(project_root) / "gate_contract.yaml"
    if not contract_path.exists():
        return [_parse_tool_entry(e) for e in _DEFAULT_TOOLCHAIN]

    try:
        import yaml

        content = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except Exception:
        return [_parse_tool_entry(e) for e in _DEFAULT_TOOLCHAIN]

    if not isinstance(content, dict):
        return [_parse_tool_entry(e) for e in _DEFAULT_TOOLCHAIN]

    toolchain_entries = content.get("toolchain", {}).get("tools", [])
    if not isinstance(toolchain_entries, list) or not toolchain_entries:
        return [_parse_tool_entry(e) for e in _DEFAULT_TOOLCHAIN]

    return [
        _parse_tool_entry(e)
        for e in toolchain_entries
        if isinstance(e, dict) and "id" in e
    ]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _detect_platform() -> str:
    """Return normalized platform key: 'darwin', 'linux', 'windows'."""
    return platform.system().lower()


def _find_executable(name: str, project_root: str | None = None) -> str | None:
    """Find tool executable, preferring project venv then system PATH."""
    if project_root:
        for venv_name in (".venv", "venv", "env"):
            venv_path = Path(project_root) / venv_name / "bin" / name
            if venv_path.exists() and venv_path.is_file():
                return str(venv_path)
    # Check ~/.qlty/bin for qlty
    qlty_bin = Path.home() / ".qlty" / "bin" / name
    if qlty_bin.exists():
        return str(qlty_bin)
    return shutil.which(name)


def _get_version(executable_path: str, tool_id: str) -> str | None:
    """Try to get version string from a tool."""
    try:
        result = subprocess.run(
            [executable_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip() or result.stderr.strip()
        # Extract version-like string (first token with digits and dots)
        for token in output.replace(",", " ").split():
            if any(c.isdigit() for c in token) and "." in token:
                # Strip leading 'v' or package name prefix
                cleaned = token.lstrip("v").strip()
                if cleaned and cleaned[0].isdigit():
                    return cleaned
        return output[:60] if output else None
    except Exception:
        return None


def _build_install_hint(req: ToolRequirement, plat: str) -> str:
    """Build a human-readable install hint for a missing tool."""
    if req.kind == "python_cli":
        pkg = f"{req.package}{req.version_spec}" if req.version_spec else req.package
        return f"uv tool install {pkg}  OR  pip install {pkg}"

    # native_binary or custom_installer
    hint = req.install_commands.get(plat, "")
    if hint:
        return hint

    # Generic fallback
    if req.install_commands:
        first_key = next(iter(req.install_commands))
        return f"{req.install_commands[first_key]}  (shown for {first_key})"

    return f"install '{req.id}' manually"


def check_tool_health(
    project_root: str,
    manifest: list[ToolRequirement] | None = None,
) -> list[ToolStatus]:
    """Check installed status of all tools in the manifest."""
    if manifest is None:
        manifest = load_toolchain_manifest(project_root)

    plat = _detect_platform()
    statuses: list[ToolStatus] = []

    for req in manifest:
        exe_path = _find_executable(req.id, project_root)
        if exe_path:
            version = _get_version(exe_path, req.id)
            statuses.append(
                ToolStatus(
                    id=req.id,
                    requirement=req,
                    installed=True,
                    version=version,
                    location=exe_path,
                )
            )
        else:
            statuses.append(
                ToolStatus(
                    id=req.id,
                    requirement=req,
                    installed=False,
                    install_hint=_build_install_hint(req, plat),
                )
            )

    return statuses


# ---------------------------------------------------------------------------
# Reconciliation: linter registry vs manifest
# ---------------------------------------------------------------------------


def reconcile_with_registry(project_root: str) -> list[str]:
    """Check linter registry's required_tools against the manifest.

    Returns a list of drift warnings for tools that linters need but
    aren't declared in the manifest. This is the self-managing loop:
    adding a new linter with required_tool="foo" that isn't in the
    manifest triggers a warning.
    """
    try:
        from lintgate.config import load_config
        from lintgate.registry import build_registry
    except ImportError:
        return []

    manifest = load_toolchain_manifest(project_root)
    manifest_ids = {r.id for r in manifest}

    config = load_config(project_root)
    registry = build_registry(config)

    warnings: list[str] = []
    for linter_name, linter in sorted(registry.items()):
        tool = getattr(linter, "required_tool", None)
        if tool and tool not in manifest_ids:
            warnings.append(
                f"Linter '{linter_name}' requires tool '{tool}' "
                f"which is not in gate_contract.yaml toolchain manifest. "
                f"Add it to keep the manifest self-managing."
            )

    return warnings


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install_missing_tools(
    project_root: str,
    statuses: list[ToolStatus] | None = None,
    *,
    auto_only: bool = True,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Install missing tools from the manifest.

    Args:
        project_root: Project root path.
        statuses: Pre-computed tool statuses. If None, computed fresh.
        auto_only: Only install tools marked auto_install=True.
        dry_run: Report what would be installed without actually installing.

    Returns:
        List of install attempt results.
    """
    if statuses is None:
        statuses = check_tool_health(project_root)

    missing = [s for s in statuses if not s.installed]
    if auto_only:
        missing = [s for s in missing if s.requirement.auto_install]

    results: list[dict[str, Any]] = []
    for status in missing:
        req = status.requirement
        result_entry: dict[str, Any] = {
            "tool": req.id,
            "kind": req.kind,
            "auto_install": req.auto_install,
        }

        if dry_run:
            result_entry["status"] = "would_install"
            result_entry["hint"] = status.install_hint
            results.append(result_entry)
            continue

        if req.kind == "python_cli":
            installed = _install_python_cli(project_root, req)
            result_entry["status"] = "installed" if installed else "failed"
            result_entry["hint"] = status.install_hint if not installed else ""
        else:
            # Native binaries require manual install — we report the hint
            result_entry["status"] = "manual_required"
            result_entry["hint"] = status.install_hint

        results.append(result_entry)

    return results


def _install_python_cli(project_root: str, req: ToolRequirement) -> bool:
    """Install a Python CLI tool, preferring uv tool install, then venv pip."""
    pkg = f"{req.package}{req.version_spec}" if req.version_spec else req.package

    # Strategy 1: uv tool install (isolated, preferred)
    uv_path = shutil.which("uv")
    if uv_path:
        try:
            result = subprocess.run(
                [uv_path, "tool", "install", pkg],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=project_root,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Strategy 2: pip install into project venv
    for venv_name in (".venv", "venv", "env"):
        venv_python = Path(project_root) / venv_name / "bin" / "python"
        if venv_python.exists():
            try:
                result = subprocess.run(
                    [str(venv_python), "-m", "pip", "install", pkg],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=project_root,
                )
                if result.returncode == 0:
                    return True
            except (subprocess.TimeoutExpired, OSError):
                pass
            break  # Only try first found venv

    return False


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------


def full_toolchain_report(project_root: str) -> ManifestReport:
    """Generate a complete toolchain health report with drift detection."""
    manifest = load_toolchain_manifest(project_root)
    statuses = check_tool_health(project_root, manifest)
    drift = reconcile_with_registry(project_root)

    all_required_met = all(s.installed for s in statuses if s.requirement.required)

    # Build summary
    installed_count = sum(1 for s in statuses if s.installed)
    total = len(statuses)
    missing_required = [
        s for s in statuses if not s.installed and s.requirement.required
    ]
    missing_optional = [
        s for s in statuses if not s.installed and not s.requirement.required
    ]

    lines = [f"Toolchain: {installed_count}/{total} tools installed"]
    if missing_required:
        lines.append(f"  MISSING REQUIRED: {', '.join(s.id for s in missing_required)}")
    if missing_optional:
        lines.append(f"  missing optional: {', '.join(s.id for s in missing_optional)}")
    if drift:
        lines.append(f"  drift warnings: {len(drift)}")

    return ManifestReport(
        tools=statuses,
        drift_warnings=drift,
        all_required_met=all_required_met,
        summary="\n".join(lines),
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli_main() -> int:
    """CLI: python -m lintgate.tool_manifest [--check|--install|--reconcile]"""
    import argparse

    parser = argparse.ArgumentParser(description="LintGate toolchain manifest")
    parser.add_argument(
        "--check-required",
        action="store_true",
        help="Print missing required tools (for pre-push hook). Exit 0 if all present.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print full toolchain health report.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install missing auto-installable tools.",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Check linter registry against manifest for drift.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root (default: cwd)",
    )
    args = parser.parse_args()

    root = str(Path(args.project_root).resolve())

    if args.check_required:
        statuses = check_tool_health(root)
        missing_required = [
            s for s in statuses if not s.installed and s.requirement.required
        ]
        if missing_required:
            for s in missing_required:
                print(f"  {s.id}: {s.install_hint}")
            return 1
        return 0

    if args.check:
        report = full_toolchain_report(root)
        print(report.summary)
        for s in report.tools:
            icon = (
                "\u2705"
                if s.installed
                else ("\u274c" if s.requirement.required else "\u26a0\ufe0f")
            )
            ver = f" {s.version}" if s.version else ""
            loc = f" ({s.location})" if s.location else ""
            req = "required" if s.requirement.required else "optional"
            hint = f" \u2192 {s.install_hint}" if not s.installed else ""
            print(f"  {icon} {s.id:12}{ver:12} {req:10}{loc}{hint}")
        if report.drift_warnings:
            print("\nDrift warnings:")
            for w in report.drift_warnings:
                print(f"  \u26a0\ufe0f  {w}")
        return 0 if report.all_required_met else 1

    if args.install:
        results = install_missing_tools(root, auto_only=True)
        for r in results:
            status = r["status"]
            hint = r.get("hint", "")
            print(f"  {r['tool']}: {status}" + (f" ({hint})" if hint else ""))
        return 0

    if args.reconcile:
        drift = reconcile_with_registry(root)
        if drift:
            for w in drift:
                print(f"  \u26a0\ufe0f  {w}")
            return 1
        print("  Manifest in sync with linter registry.")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
