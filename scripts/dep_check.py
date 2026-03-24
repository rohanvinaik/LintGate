#!/usr/bin/env python3
"""Dependency health checker — standalone.

Usage:
    python scripts/dep_check.py . health
    python scripts/dep_check.py . sync --create-venv --lock
    python scripts/dep_check.py . toolchain --install-missing
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from scripts._common import emit, emit_error, validate_project_root


def cmd_health(args):
    from lintgate.dependency_health import full_dependency_health

    project_root = validate_project_root(args.path)
    result = full_dependency_health(project_root)
    issues = len(result.get("issues", []))
    vulns = result.get("summary", {}).get("vulnerabilities", 0)
    summary = f"Dependencies: {issues} issues. {vulns} CVEs."
    emit(result, "dep_health", project_root, summary)


def cmd_sync(args):
    from lintgate.dependency_health import full_dependency_health

    project_root = validate_project_root(args.path)
    root = Path(project_root)
    result: dict[str, Any] = {"project": project_root, "actions": []}

    health = full_dependency_health(project_root)
    result["health_before"] = health.get("summary", {})

    uv_path = shutil.which("uv")
    if not uv_path:
        emit_error("uv not found — install with: curl -LsSf https://astral.sh/uv/install.sh | sh")

    if args.create_venv:
        venv_path = root / ".venv"
        if venv_path.exists():
            result["actions"].append({"action": "create_venv", "status": "skipped", "reason": ".venv exists"})
        else:
            try:
                proc = subprocess.run([uv_path, "venv", ".venv"], capture_output=True, text=True, timeout=60, cwd=project_root)
                result["actions"].append({"action": "create_venv", "status": "ok" if proc.returncode == 0 else "error"})
            except subprocess.TimeoutExpired:
                result["actions"].append({"action": "create_venv", "status": "timeout"})

    if args.lock:
        try:
            proc = subprocess.run([uv_path, "lock"], capture_output=True, text=True, timeout=120, cwd=project_root)
            result["actions"].append({"action": "lock", "status": "ok" if proc.returncode == 0 else "error"})
        except subprocess.TimeoutExpired:
            result["actions"].append({"action": "lock", "status": "timeout"})

    if args.create_venv or args.lock:
        result["health_after"] = full_dependency_health(project_root).get("summary", {})

    n = len(result["actions"])
    summary = f"Dep sync: {n} actions taken."
    emit(result, "dep_sync", project_root, summary)


def cmd_toolchain(args):
    from lintgate.tool_manifest import full_toolchain_report, install_missing_tools

    project_root = validate_project_root(args.path)
    report = full_toolchain_report(project_root)

    output: dict[str, Any] = {
        "summary": report.summary,
        "all_required_met": report.all_required_met,
        "tools": [],
        "drift_warnings": report.drift_warnings,
    }
    for s in report.tools:
        info: dict[str, Any] = {"id": s.id, "installed": s.installed, "required": s.requirement.required}
        if s.installed:
            info["version"] = s.version
        else:
            info["install_hint"] = s.install_hint
        output["tools"].append(info)

    if args.install_missing:
        output["install_results"] = install_missing_tools(project_root, report.tools, auto_only=True)

    met = "all met" if output["all_required_met"] else "gaps found"
    summary = f"Toolchain: {len(output['tools'])} tools, {met}."
    emit(output, "toolchain_health", project_root, summary)


def main():
    parser = argparse.ArgumentParser(prog="dep_check", description="Dependency health checker")
    parser.add_argument("path", help="Project root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Full dependency health audit")

    p_sync = sub.add_parser("sync", help="Sync dependencies")
    p_sync.add_argument("--create-venv", action="store_true")
    p_sync.add_argument("--lock", action="store_true")

    p_tc = sub.add_parser("toolchain", help="Check CLI tool availability")
    p_tc.add_argument("--install-missing", action="store_true")

    args = parser.parse_args()
    {"health": cmd_health, "sync": cmd_sync, "toolchain": cmd_toolchain}[args.command](args)


if __name__ == "__main__":
    main()
