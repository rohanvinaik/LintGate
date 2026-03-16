"""PreToolUse hook — compass-aware advisories + quality gate before tool execution.

Theory mode: advisory on write operations (suggests reading more first).
Normal/Habit mode: alignment check against toward/away/forbidden directives.
Quality gate: blocks git push until quality checks pass, advises on git commit.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# Git command patterns — push checked first so "git commit && git push" classifies as push
_PUSH_RE = re.compile(r"\bgit\s+push\b")
_COMMIT_RE = re.compile(r"\bgit\s+commit\b")


def _load_mode(project_root: str) -> str:
    """Load current cognitive mode from session memory."""
    try:
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root)
        mode = session.behavior_compass.get("mode_state", {}).get("current", "normal")
        return str(mode or "normal")
    except Exception:
        return "normal"


def _check_theory_mode(mode: str, tool_name: str) -> str:
    """Return advisory if theory mode is active and tool is a write."""
    if mode == "theory" and tool_name in _WRITE_TOOLS:
        return (
            "[Compass] Theory mode active — consider reading more"
            " before writing. Use `theory_mode_freeze` when ready."
        )
    return ""


def _extract_bash_command(data: dict[str, Any]) -> str:
    """Extract the command string from a Bash tool_input."""
    tool_input = data.get("tool_input", data.get("input", {}))
    if isinstance(tool_input, dict):
        return str(tool_input.get("command", ""))
    if isinstance(tool_input, str):
        return tool_input
    return ""


def _check_bash_alignment(data: dict[str, Any], exec_compass: Any, project_root: str = "") -> str:
    """Check Bash command alignment against compass + prescriptive spec directives."""
    command = _extract_bash_command(data)
    if not command:
        return ""

    # Try enhanced alignment with prescriptive specs
    specs = None
    if project_root:
        try:
            from lintgate.specification.prescriptive.spec import load_all_specs

            all_specs = load_all_specs(project_root)
            if all_specs:
                specs = list(all_specs.values())
        except Exception:
            pass

    if specs:
        result = exec_compass.check_alignment_with_specs(command, specs=specs)
    else:
        result = exec_compass.check_alignment(command)

    if result.get("violations"):
        return f"[Compass] Alignment violation: {result['violations'][0]}"
    if result.get("warnings"):
        return f"[Compass] Alignment warning: {result['warnings'][0]}"
    return ""


def _check_prescriptive_obligations(data: dict[str, Any], project_root: str) -> str:
    """Check if Write/Edit target has prescriptive spec obligations.

    Resolves at function granularity when possible: for Edit operations,
    extracts function names from old_string/new_string context and matches
    against specific spec target_keys. Falls back to module-level matching
    only when function detection fails.
    """
    tool_input = data.get("input", {})
    filepath = tool_input.get("file_path", "")
    if not filepath or not filepath.endswith(".py"):
        return ""

    from lintgate.specification.prescriptive.spec import load_spec_index

    index = load_spec_index(project_root)
    if not index:
        return ""

    rel = os.path.relpath(filepath, project_root) if os.path.isabs(filepath) else filepath
    module = rel.replace(os.sep, ".").removesuffix(".py")

    # Try function-level matching first: extract function names from edit context
    matching: list[str] = []
    edit_text = tool_input.get("new_string", "") or tool_input.get("old_string", "") or ""
    if edit_text:
        # Look for function defs in the edit context
        import re as _re

        func_matches = _re.findall(r"\bdef\s+(\w+)\s*\(", edit_text)
        for func_name in func_matches:
            candidate = f"{module}::{func_name}"
            if candidate in index:
                matching.append(candidate)

    # Fall back to module-level matching if no function-level hits
    if not matching:
        matching = [k for k in index if module in k]
    if not matching:
        return ""

    from lintgate.specification.prescriptive.spec import load_spec

    invariant_descs: list[str] = []
    constraint_descs: list[str] = []
    func_names: list[str] = []
    for target_key in matching[:3]:
        spec = load_spec(project_root, target_key)
        if spec:
            func_name = target_key.split("::")[-1] if "::" in target_key else target_key
            func_names.append(func_name)
            for inv in spec.invariants[:2]:
                invariant_descs.append(inv.description[:80])
            for gc in spec.generation_constraints[:2]:
                if gc.constraint_type == "must_not_use":
                    constraint_descs.append(f"MUST NOT: {gc.description[:60]}")
                elif gc.constraint_type == "must_use":
                    constraint_descs.append(f"MUST: {gc.description[:60]}")

    parts: list[str] = []
    if func_names:
        parts.append(f"Specs: {', '.join(func_names[:3])}")
    if invariant_descs:
        parts.append(f"Obligations: {'; '.join(invariant_descs[:2])}")
    if constraint_descs:
        parts.append(f"Constraints: {'; '.join(constraint_descs[:2])}")
    if parts:
        return "[PSpec] " + " | ".join(parts)
    return ""


def _extract_write_signature(data: dict[str, Any]) -> str:
    """Extract an action signature from Write/Edit tool input for alignment checking.

    For Edit: uses old_string + new_string to summarize the change.
    For Write: uses file_path + first meaningful line of content.
    """
    tool_input = data.get("input", {})
    filepath = tool_input.get("file_path", "")
    parts: list[str] = []
    if filepath:
        parts.append(os.path.basename(filepath))

    # Edit tool: old_string → new_string gives the change intent
    old = tool_input.get("old_string", "")
    new = tool_input.get("new_string", "")
    if old or new:
        # Take first non-empty line of new content as the signature
        for line in (new or old).split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                parts.append(stripped[:80])
                break
    elif tool_input.get("content", ""):
        # Write tool: first meaningful line
        for line in tool_input["content"].split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                parts.append(stripped[:80])
                break

    return " ".join(parts)


def _classify_git_command(command: str) -> str | None:
    """Classify a command as 'push', 'commit', or None.

    Push is checked first so chained commands like
    ``git commit && git push`` classify as 'push'.
    """
    if _PUSH_RE.search(command):
        return "push"
    if _COMMIT_RE.search(command):
        return "commit"
    return None


@dataclass
class QualityGateResult:
    """Result of the quality gate check."""

    should_block: bool = False
    messages: list[str] = field(default_factory=list)


def load_controlplane_config(cwd: str):
    """Lazy wrapper for config loading — patchable at module level."""
    from lintgate.config import load_controlplane_config as _load

    return _load(cwd)


def load_runtime_state(project_root: str):
    """Lazy wrapper for runtime state loading — patchable at module level."""
    from lintgate.runtime_state import load_runtime_state as _load

    return _load(project_root)


def _check_diff_secrets(project_root: str):
    """Lazy wrapper for secrets scanning — patchable at module level."""
    from lintgate.channels.git_channel import _check_diff_secrets as _check

    return _check(project_root)


def load_compass(project_root: str):
    """Lazy wrapper for compass loading — patchable at module level."""
    from lintgate.compass_io import load_compass as _load

    return _load(project_root)


def _collect_state_failures(state: Any, qg: Any, project_root: str) -> list[str]:
    """Validate runtime state and return a list of failure messages."""
    failures: list[str] = []

    # Staleness
    age_s = time.time() - state.timestamp
    if age_s > qg.staleness_threshold_s:
        mins = int(age_s // 60)
        threshold_mins = int(qg.staleness_threshold_s // 60)
        failures.append(f"Last quality run is {mins}min old (threshold: {threshold_mins}min).")

    # Blocking issues
    if state.blocking_issues > 0:
        failures.append(f"{state.blocking_issues} blocking issue(s) remain.")
        symbol_blockers = int(getattr(state, "symbol_coverage_blockers", 0) or 0)
        if symbol_blockers > 0:
            failures.append(
                f"Symbol coverage remediation loop required: "
                f"{symbol_blockers} uncovered symbol blocker"
                f"{'s' if symbol_blockers != 1 else ''}. "
                "Add tests for the reported symbols and rerun `controlplane_run` "
                "until blockers are zero."
            )
        else:
            failures.append("Resolve blockers, then rerun `controlplane_run` before pushing.")

    # Tests
    if state.last_test_status == "fail":
        failures.append("Tests are failing.")

    # Secrets — fail-open on crash
    if qg.check_secrets:
        try:
            secret_findings = _check_diff_secrets(project_root)
            if secret_findings:
                failures.append(f"{len(secret_findings)} secret(s) detected in staged diff.")
        except Exception:
            pass

    return failures


def _check_quality_gate(command: str, project_root: str) -> QualityGateResult:
    """Check whether a git command should be blocked or advised.

    Fail-open on infrastructure errors (missing config, crashed secrets check).
    Fail-closed for git push when no quality run exists.
    """
    git_action = _classify_git_command(command)
    if git_action is None:
        return QualityGateResult()

    # Load config — fail-open if missing
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        return QualityGateResult()

    if cp_config is None or not cp_config.quality_gate.enabled:
        return QualityGateResult()

    qg = cp_config.quality_gate

    # Skip if the specific action type is disabled
    if git_action == "push" and not qg.block_push:
        return QualityGateResult()
    if git_action == "commit" and not qg.advise_commit:
        return QualityGateResult()

    # Load runtime state
    try:
        state = load_runtime_state(project_root)
    except Exception:
        state = None

    if state is None:
        if git_action != "push":
            return QualityGateResult()
        return QualityGateResult(
            should_block=True,
            messages=["[QualityGate] BLOCKED: No quality run found. Run `controlplane_run` first."],
        )

    failures = _collect_state_failures(state, qg, project_root)
    if not failures:
        return QualityGateResult()

    is_push = git_action == "push"
    prefix = "BLOCKED" if is_push else "Advisory"
    return QualityGateResult(
        should_block=is_push,
        messages=[f"[QualityGate] {prefix}: {f}" for f in failures],
    )


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process PreToolUse event."""
    tool_name = data.get("tool_name", data.get("toolName", ""))
    project_root = data.get("cwd", ".")
    command = _extract_bash_command(data) if tool_name == "Bash" else ""

    # Quality gate: block git push immediately if checks fail
    gate_result = QualityGateResult()
    if command:
        gate_result = _check_quality_gate(command, project_root)
        if gate_result.should_block:
            return {
                "continue": False,
                "systemMessage": " | ".join(gate_result.messages),
            }

    mode = _load_mode(project_root)

    try:
        from lintgate.modes.execution_compass import ExecutionCompass
    except ImportError:
        if gate_result.messages:
            return {
                "continue": True,
                "systemMessage": " | ".join(gate_result.messages),
            }
        return {"continue": True}

    compass = load_compass(project_root)  # uses module-level lazy wrapper
    if compass is None and not (mode == "theory" and tool_name in _WRITE_TOOLS):
        if gate_result.messages:
            return {
                "continue": True,
                "systemMessage": " | ".join(gate_result.messages),
            }
        return {"continue": True}

    exec_compass = ExecutionCompass.from_compass_state(compass) if compass is not None else None
    messages: list[str] = list(gate_result.messages)

    # Add compass messages
    theory_msg = _check_theory_mode(mode, tool_name)
    if theory_msg:
        messages.append(theory_msg)

    if tool_name == "Bash" and exec_compass:
        alignment_msg = _check_bash_alignment(data, exec_compass, project_root)
        if alignment_msg:
            messages.append(alignment_msg)

    # Write/Edit alignment: check compass away/forbidden + prescriptive forbidden behaviors
    if tool_name in _WRITE_TOOLS and exec_compass:
        try:
            write_sig = _extract_write_signature(data)
            if write_sig:
                alignment_msg = _check_bash_alignment(
                    {"input": {"command": write_sig}}, exec_compass, project_root
                )
                if alignment_msg:
                    messages.append(alignment_msg.replace("[Compass]", "[Compass/Write]"))
        except Exception:
            pass

    # PrescriptiveSpec obligation guidance for Write/Edit
    if tool_name in _WRITE_TOOLS:
        try:
            pspec_guidance = _check_prescriptive_obligations(data, project_root)
            if pspec_guidance:
                messages.append(pspec_guidance)
        except Exception:
            pass

    return {
        "continue": True,
        "systemMessage": " | ".join(messages) if messages else "",
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
