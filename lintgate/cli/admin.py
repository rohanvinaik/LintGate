import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from lintgate.agent_profiles import PROFILES
from lintgate.mcp_schema import ProviderSchemaError


def _load_contract() -> dict:
    contract_path = Path(__file__).parent.parent / "mcp_contract.yaml"
    if not contract_path.exists():
        return {}
    with open(contract_path) as f:
        return yaml.safe_load(f)


def _is_executable(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _resolve_server_command() -> tuple[str, str]:
    """Resolve a runnable lintgate MCP command with deterministic fallbacks."""
    from_path = shutil.which("lintgate-mcp")
    if from_path:
        return from_path, "PATH"

    sibling = Path(sys.executable).resolve().parent / "lintgate-mcp"
    if _is_executable(sibling):
        return str(sibling), "python_sibling"

    repo_root = Path(__file__).resolve().parents[2]
    repo_venv = repo_root / ".venv" / "bin" / "lintgate-mcp"
    if _is_executable(repo_venv):
        return str(repo_venv), "repo_venv"

    raise RuntimeError(
        "Unable to resolve lintgate MCP executable. "
        "Install lintgate with MCP extras or run setup.sh to provision .venv."
    )


def _load_configured_server_command(config_path: Any) -> str | None:
    """Read lintgate MCP command from agent config if present."""
    if not isinstance(config_path, Path) or not config_path.exists():
        return None
    try:
        with open(config_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return None
    lintgate_entry = mcp_servers.get("lintgate")
    if not isinstance(lintgate_entry, dict):
        return None
    command = lintgate_entry.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None


def _command_runnable(command: str) -> bool:
    """Return True if command resolves on this host."""
    if "/" in command:
        return _is_executable(Path(command))
    return shutil.which(command) is not None


def cmd_install(args: argparse.Namespace) -> int:
    print(f"[*] Starting installation for agent: {args.agent}")

    profile = PROFILES.get(args.agent)
    if not profile:
        print(f"[!] Unknown agent profile: {args.agent}")
        return 1

    # Phase 1: Detect
    try:
        server_cmd, server_cmd_source = _resolve_server_command()
    except RuntimeError as exc:
        print(f"[!] {exc}")
        return 1

    if args.dry_run:
        print("[Dry Run] Would configure MCP server with command:", server_cmd)
        print("[Dry Run] Resolution source:", server_cmd_source)
        print(f"[Dry Run] Target config path: {profile.config_path}")
        return 0

    # Phase 2: Configure
    configured = profile.config_writer(profile.config_path, server_cmd)

    # Phase 3: Report
    report = {
        "agent": args.agent,
        "config_path": str(profile.config_path),
        "status": "configured" if configured else "already_configured",
        "server_command": server_cmd,
        "server_command_source": server_cmd_source,
    }

    report_path = Path("install_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"[*] Wrote install report to {report_path.absolute()}")
    if configured:
        print("[+] Seamless configuration complete!")
    else:
        print("[*] Agent was already correctly configured.")

    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    print("[*] Bootstrapping LintGate environment...")

    # Run install
    install_status = cmd_install(args)
    if install_status != 0:
        return install_status

    # Run doctor via check
    return cmd_doctor(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    profile = PROFILES.get(args.agent)
    if not profile:
        print(f"[!] Unknown agent profile: {args.agent}")
        return 1

    print(f"=== LintGate Doctor: {profile.display_name} ===")

    print("[*] Checking Contract Requirements...")
    contract = _load_contract()
    safety_critical = contract.get("safety_critical_tools", [])
    expected_tools = contract.get("expected_tools", {}).get(args.agent, [])

    print(f"  - Safety critical tools required: {safety_critical}")
    print(f"  - Total tools expected: {len(expected_tools)}")

    if args.dry_run:
        print("[Dry Run] Skipping active schema validation.")
        return 0

    configured_command = _load_configured_server_command(getattr(profile, "config_path", None))
    if configured_command and not _command_runnable(configured_command):
        print(f"[!] MCP server command is not runnable: {configured_command}")
        if getattr(args, "fix", False):
            print("  => Re-running install to rewrite command path...")
            if cmd_install(args) != 0:
                sys.exit(1)
        else:
            print("  => Run `lintgate-admin install --agent <agent>` to repair it.")
            sys.exit(1)

    import asyncio

    async def validate_schemas():
        from lintgate.mcp_schema import compile_and_validate_schemas
        from mcp_server import mcp

        # Build the fastmcp instance and derive tools natively
        tools = mcp._tool_manager.list_tools()
        if not tools:
            # Need to initialize some internals if the server module didn't run .run()
            # FastMCP initialization captures tools via decorator
            pass

        tool_names = [t.name for t in tools]

        missing_safety = set(safety_critical) - set(tool_names)
        if missing_safety:
            print(f"[!] FATAL: Missing safety critical tools: {missing_safety}")
            sys.exit(1)

        try:
            compile_and_validate_schemas(
                tools, agent_profile="strict" if profile.schema_strict else "relaxed"
            )
            print("[+] Schema validation passed.")
        except ProviderSchemaError as e:
            print(f"[!] Schema Validation Error: {e}")
            sys.exit(1)

        missing_expected = set(expected_tools) - set(tool_names)
        if missing_expected:
            print(f"[!] DEGRADED: Missing expected tools defined in contract: {missing_expected}")
            if getattr(args, "fix", False):
                print(
                    "  => --fix cannot dynamically write tool code. Please check mcp_tools registry."
                )
            sys.exit(1)

    asyncio.run(validate_schemas())

    if getattr(args, "fix", False):
        print("\n[*] Running auto-fixes for registration...")
        cmd_install(args)

    print("\n[+] Doctor check passed. Environment is GREEN.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LintGate Admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Install command
    p_install = subparsers.add_parser("install", help="Install config for a specific agent")
    p_install.add_argument(
        "--agent", required=True, choices=list(PROFILES.keys()), help="Target agent"
    )
    p_install.add_argument("--dry-run", action="store_true", help="Preview mode")

    # Bootstrap command
    p_bootstrap = subparsers.add_parser("bootstrap", help="Bootstrap everything to green")
    p_bootstrap.add_argument(
        "--agent", required=True, choices=list(PROFILES.keys()), help="Target agent"
    )
    p_bootstrap.add_argument("--dry-run", action="store_true")

    # Doctor command
    p_doctor = subparsers.add_parser("doctor", help="Verify integration health")
    p_doctor.add_argument(
        "--agent", required=True, choices=list(PROFILES.keys()), help="Target agent"
    )
    p_doctor.add_argument("--fix", action="store_true", help="Attempt auto-fix")
    p_doctor.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "install":
        return cmd_install(args)
    elif args.command == "bootstrap":
        return cmd_bootstrap(args)
    elif args.command == "doctor":
        return cmd_doctor(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
