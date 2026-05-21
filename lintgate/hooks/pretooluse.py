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
    re.compile(
        r"pip3?\s+install\s+(?!-e\s+\.)(?!-r\s+)"
    ),  # heuristically catches global/unscoped installs
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


def _stash_pre_edit_snapshot(tool_name: str, tool_input: dict, cwd: str) -> None:
    """Stash pre-edit finding counts for surgical mode delta computation.

    Only runs when workflow_mode is surgical. Captures per-file finding
    counts from the last lint run state so PostToolUse can compute a delta.

    Fast path: reads cached state from .lintgate/, no linter execution.
    """
    import os as _os

    # Only stash for edit tools
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return

    # Check if workflow_mode is surgical via runtime state
    try:
        from lintgate.runtime_state import load_runtime_state

        runtime = load_runtime_state(cwd)
        if runtime is None or runtime.workflow_mode != "surgical":
            return
    except Exception:
        return

    # Extract target file from tool_input
    filepath = ""
    if isinstance(tool_input, dict):
        filepath = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not filepath:
        return

    # Load the last lint run's finding index for this file
    try:
        from lintgate.state import load_last_run

        last_run = load_last_run(cwd)
        finding_index = last_run.get("finding_index", {}) if last_run else {}

        rel_path = _os.path.relpath(filepath, cwd) if _os.path.isabs(filepath) else filepath
        file_findings = finding_index.get(rel_path, {})

        # Stash to .lintgate/pre_edit_snapshot.json
        stash_dir = _os.path.join(cwd, ".lintgate")
        _os.makedirs(stash_dir, exist_ok=True)
        stash_path = _os.path.join(stash_dir, "pre_edit_snapshot.json")
        stash_data = {
            "file": rel_path,
            "abs_path": filepath,
            "finding_count": file_findings.get("total", 0) if isinstance(file_findings, dict) else 0,
            "blocking_count": file_findings.get("blocking", 0) if isinstance(file_findings, dict) else 0,
            "finding_signatures": file_findings.get("signatures", []) if isinstance(file_findings, dict) else [],
            "timestamp": __import__("time").time(),
        }
        with open(stash_path, "w") as f:
            json.dump(stash_data, f)
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        print(json.dumps({}))
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    if isinstance(tool_input, str):
        tool_input_dict: dict = {"command": tool_input} if tool_name == "Bash" else {}
    elif isinstance(tool_input, dict):
        tool_input_dict = tool_input
    else:
        tool_input_dict = {}

    cwd = input_data.get("cwd", "")

    # Surgical mode: stash pre-edit finding snapshot (non-blocking)
    try:
        if tool_name in ("Write", "Edit", "MultiEdit") and cwd:
            _stash_pre_edit_snapshot(tool_name, tool_input_dict, cwd)
    except Exception:
        pass

    if tool_name != "Bash":
        print(json.dumps({}))
        sys.exit(0)

    command = tool_input if isinstance(tool_input, str) else tool_input_dict.get("command", "")

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
