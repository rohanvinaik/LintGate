"""ControlPlane CLI entry point.

Commands:
- controlplane run <path> [--channels lint,tests,deps,git] [--strictness normal]
- controlplane status <path>

This is a standalone CLI that runs the supervision mesh directly,
independent of the PostToolUse hook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING

from .types import ControlPlaneConfig, SupervisionEvent

if TYPE_CHECKING:
    from .channel import Channel


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ControlPlane."""
    parser = argparse.ArgumentParser(
        prog="controlplane",
        description="ControlPlane — orthogonal supervision mesh for coding agents",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run supervision mesh on a project")
    run_parser.add_argument("path", help="Project root path")
    run_parser.add_argument(
        "--channels",
        default="lint,tests,deps,git,performance,test_effectiveness,mutation",
        help="Comma-separated channel list (default: lint,tests,deps,git,performance,test_effectiveness,mutation)",
    )
    run_parser.add_argument(
        "--strictness",
        default="normal",
        choices=["relaxed", "normal", "strict"],
        help="Strictness level (default: normal)",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Show ControlPlane status")
    status_parser.add_argument("path", nargs="?", default=".", help="Project root path")

    args = parser.parse_args(argv)

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "status":
        _cmd_status(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the supervision mesh."""
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.mutation_channel import MutationChannel
    from lintgate.channels.performance_channel import PerformanceChannel
    from lintgate.channels.test_channel import TestChannel
    from lintgate.channels.test_effectiveness_channel import TestEffectivenessChannel

    from .reporter import format_mesh_report
    from .runtime import run_mesh

    project_root = os.path.abspath(args.path)
    requested_channels = [c.strip() for c in args.channels.split(",")]

    # Build channel registry
    channel_registry: dict[str, Channel] = {
        "lint": LintChannel(),
        "tests": TestChannel(),
        "deps": DependencyChannel(),
        "git": GitChannel(),
        "performance": PerformanceChannel(),
        "test_effectiveness": TestEffectivenessChannel(),
        "mutation": MutationChannel(),
    }

    # Select requested channels
    channels: list[Channel] = []
    for name in requested_channels:
        if name in channel_registry:
            channels.append(channel_registry[name])
        else:
            print(f"Warning: Unknown channel '{name}', skipping", file=sys.stderr)

    if not channels:
        print("Error: No valid channels specified", file=sys.stderr)
        sys.exit(1)

    # Build event (for CLI, we create a synthetic event)
    event = SupervisionEvent(
        surface="ci",
        project_root=project_root,
        tool_name="controlplane_run",
        files_changed=_discover_python_files(project_root),
    )

    # Optionally classify changes
    try:
        from lintgate.change_classifier import classify_change
        from lintgate.config import load_config

        config = load_config(project_root)
        classification = classify_change(
            "Edit",
            {"file_path": project_root},
            "CLI run",
            project_root,
            config,
        )
        event.change_classification = classification
    except Exception:
        pass  # Classification failure is non-fatal for CLI

    # Build config
    cp_config = ControlPlaneConfig(
        enabled=True,
        latency_budget_ms=30000,  # CLI gets more time
    )

    # Run mesh
    mesh_result = run_mesh(event, cp_config, channels)

    # Format output
    if args.json:
        report = format_mesh_report(mesh_result, cp_config)
        print(json.dumps(report, indent=2))
    else:
        _print_human_readable(mesh_result)


def _cmd_status(args: argparse.Namespace) -> None:
    """Show ControlPlane status."""
    project_root = os.path.abspath(args.path)
    print(f"ControlPlane Status for: {project_root}")
    print(f"{'─' * 50}")

    # Check for config
    try:
        from lintgate.config import load_config

        config = load_config(project_root)
        print("Project config: Found")
        print(f"Total timeout: {config.total_timeout_ms}ms")
    except Exception:
        print("Project config: Not found (using defaults)")

    # List available channels
    print("\nAvailable channels:")
    print("  ✓ lint      — Code quality (ruff, mypy, complexity)")
    print("  ✓ tests     — Test coverage and health")
    print("  ✓ deps      — Dependency health")
    print("  ✓ git       — Git hygiene")
    print("  ✓ performance — Algebraic performance analysis")
    print("  ✓ test_effectiveness — Test assertion quality analysis")
    print("  ✓ mutation — Mutation testing and specification quality")


def _discover_python_files(project_root: str, max_files: int = 50) -> list[str]:
    """Discover Python files in the project for CLI analysis."""
    files: list[str] = []
    root = os.path.abspath(project_root)

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs and common non-source dirs
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and d
            not in (
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
                "build",
                "dist",
                ".git",
                ".tox",
            )
        ]
        for f in filenames:
            if f.endswith(".py"):
                files.append(os.path.join(dirpath, f))
                if len(files) >= max_files:
                    return files

    return files


def _print_human_readable(mesh_result) -> None:
    """Print mesh result in human-readable format."""
    coherence = mesh_result.coherence
    print(f"\n{'═' * 60}")
    print(f"  ControlPlane Report — {coherence.state.upper()}")
    print(f"  Duration: {mesh_result.duration_ms:.0f}ms")
    print(f"{'═' * 60}")

    for cr in mesh_result.channel_results:
        if cr.status == "skip":
            continue

        icon = {"pass": "✓", "fail": "✗", "error": "⚠", "timeout": "⏱"}.get(
            cr.status, "?"
        )
        print(f"\n  {icon} {cr.channel}: {cr.status}")

        if cr.findings:
            for f in cr.findings[:5]:
                sev_icon = {
                    "blocking": "🔴",
                    "warning": "🟡",
                    "informational": "🔵",
                }.get(f.severity, "⚪")
                location = f.short_location() if hasattr(f, "short_location") else ""
                print(f"    {sev_icon} [{f.kind}] {location}: {f.message}")
            if len(cr.findings) > 5:
                print(f"    ... and {len(cr.findings) - 5} more")

        if cr.repairs:
            for r in cr.repairs[:3]:
                print(f"    💡 {r.summary}")

    if coherence.summary:
        print(f"\n  Coherence: {coherence.summary}")
    if coherence.recommended_action:
        print(f"  Action: {coherence.recommended_action}")

    if mesh_result.partial:
        print(
            f"\n  ⚠ Partial: channels timed out: {', '.join(mesh_result.incomplete_channels)}"
        )

    print(f"\n{'═' * 60}")


if __name__ == "__main__":
    main()
