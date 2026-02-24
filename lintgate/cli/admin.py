import argparse
import json
import sys
from pathlib import Path

import yaml

from lintgate.agent_command_profiles import sync_agent_command_profile
from lintgate.agent_profiles import PROFILES
from lintgate.mcp_schema import ProviderSchemaError


def _load_contract() -> dict:
    contract_path = Path(__file__).parent.parent / "mcp_contract.yaml"
    if not contract_path.exists():
        return {}
    with open(contract_path) as f:
        return yaml.safe_load(f)


def cmd_install(args: argparse.Namespace) -> int:
    print(f"[*] Starting installation for agent: {args.agent}")

    profile = PROFILES.get(args.agent)
    if not profile:
        print(f"[!] Unknown agent profile: {args.agent}")
        return 1

    # Phase 1: Detect
    server_cmd = "lintgate-mcp"
    if args.dry_run:
        print("[Dry Run] Would configure MCP server with command:", server_cmd)
        print(f"[Dry Run] Target config path: {profile.config_path}")
        return 0

    # Phase 2: Configure
    configured = profile.config_writer(profile.config_path, server_cmd)
    command_profile = sync_agent_command_profile(args.agent, apply=True)
    if command_profile is not None:
        blocking = int(command_profile.get("blocking_issues", 0))
        if blocking > 0:
            print(
                f"[!] Command profile sync reported {blocking} blocking issue(s). "
                "Run doctor for details."
            )
            return 1
        summary = command_profile.get("summary", {})
        migrated = int(summary.get("migrated", 0))
        recovered = int(summary.get("recovered", 0))
        generated = int(summary.get("generated_templates", 0))
        if migrated or recovered or generated:
            print(
                "[*] Command profile sync:"
                f" generated={generated}, migrated={migrated}, recovered={recovered}"
            )

    # Phase 3: Report
    report = {
        "agent": args.agent,
        "config_path": str(profile.config_path),
        "status": "configured" if configured else "already_configured",
    }
    if command_profile is not None:
        report["command_profile"] = command_profile

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

    command_profile = sync_agent_command_profile(args.agent, apply=getattr(args, "fix", False))
    if command_profile is not None:
        blocking = int(command_profile.get("blocking_issues", 0))
        if blocking > 0:
            print(
                "[!] Command profile validation failed. "
                f"{blocking} file(s) are not provider-compatible."
            )
            if not getattr(args, "fix", False):
                print("  => Re-run with --fix to auto-migrate local command files.")
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
            compile_and_validate_schemas(tools, agent_profile="strict" if profile.schema_strict else "relaxed")
            print("[+] Schema validation passed.")
        except ProviderSchemaError as e:
            print(f"[!] Schema Validation Error: {e}")
            sys.exit(1)

        missing_expected = set(expected_tools) - set(tool_names)
        if missing_expected:
            print(f"[!] DEGRADED: Missing expected tools defined in contract: {missing_expected}")
            if getattr(args, "fix", False):
                print("  => --fix cannot dynamically write tool code. Please check mcp_tools registry.")
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
    p_install.add_argument("--agent", required=True, choices=list(PROFILES.keys()), help="Target agent")
    p_install.add_argument("--dry-run", action="store_true", help="Preview mode")

    # Bootstrap command
    p_bootstrap = subparsers.add_parser("bootstrap", help="Bootstrap everything to green")
    p_bootstrap.add_argument("--agent", required=True, choices=list(PROFILES.keys()), help="Target agent")
    p_bootstrap.add_argument("--dry-run", action="store_true")

    # Doctor command
    p_doctor = subparsers.add_parser("doctor", help="Verify integration health")
    p_doctor.add_argument("--agent", required=True, choices=list(PROFILES.keys()), help="Target agent")
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
