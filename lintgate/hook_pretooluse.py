#!/usr/bin/env python3
"""LintGate PreToolUse hook — system mutation guard.

Intercepts risky Bash commands (global installs, system directory writes,
shell config modifications) before execution. Warns the agent and forces
an explicit override escape hatch to proceed, mitigating prompt injection
or hallucinated global state mutations.
"""

from __future__ import annotations

import json
import re
import sys

# Detection categories matching Issue #43
_BLOCKED_PATTERNS = [
    # 1. Package managers (global context)
    re.compile(r"brew\s+(install|tap|cask)"),
    re.compile(r"pip3?\s+install\s+(?!-e\s+\.)(?!-r\s+)"),  # heuristically catches global/unscoped installs
    re.compile(r"uv\s+tool\s+install"),
    re.compile(r"npm\s+(i|install)\s+-g"),
    re.compile(r"mas\s+install"),
    re.compile(r"apt(-get)?\s+install"),
    re.compile(r"cargo\s+install"),
    re.compile(r"gem\s+install"),

    # 2. System directories
    re.compile(r"(/etc/|/usr/local/|/opt/|/Applications/)"),
    re.compile(r"~/Library/LaunchAgents"),

    # 3. Shell config (modifications/writes)
    re.compile(r"(?:>>|>|nano|vim|rm|cp|mv)\s+.*~/\.(zshrc|bashrc|profile|config)"),

    # 4. Network fetches with execution
    re.compile(r"(?:curl|wget)\s+[^|&]+[|&]+\s*(?:sudo\s+)?(?:ba)?sh"),

    # 5. Privilege escalation
    re.compile(r"^\s*sudo\s+"),
]

def _is_mutation(command: str) -> bool:
    """Check if command matches any known mutation pattern."""
    return any(pattern.search(command) for pattern in _BLOCKED_PATTERNS)

def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        print(json.dumps({}))
        sys.exit(0)

    tool_name = input_data.get("tool_name")
    if tool_name != "Bash":
        # Only guarding Bash mutations for now
        print(json.dumps({}))
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    command = tool_input if isinstance(tool_input, str) else tool_input.get("command", "")

    if not command:
        print(json.dumps({}))
        sys.exit(0)

    # Check for the explicit escape hatch override
    if "# lintgate-override" in command or "--lintgate-override" in command:
        print(json.dumps({}))
        sys.exit(0)

    if _is_mutation(command):
        message = (
            f"LINTGATE SYSTEM MUTATION GUARD TRIGGERED.\n\n"
            f"The command `{command[:80]}...` appears to modify global system state "
            "outside the project directory (e.g. package installations, system directories).\n\n"
            "By default, LintGate blocks unscoped system mutations to protect against "
            "prompt injection and hallucinated behaviors.\n\n"
            "If the user EXPLICITLY requested this installation, you may proceed by "
            "remaking the tool call and appending ` # lintgate-override` to the end "
            "of the command string."
        )
        print(json.dumps({"error": message}))
        sys.exit(0)

    print(json.dumps({}))
    sys.exit(0)

if __name__ == "__main__":
    main()
