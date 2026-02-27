"""NSIL action verification core.

Implements propose→verify engine for action interception with deterministic
violation codes. This is pure local logic with no network or LLM calls.
"""

from dataclasses import dataclass, field
from typing import Any

# Deterministic violation codes
VIOLATION_CODES = {
    "NSIL_UNKNOWN_ACTION": "Unknown action type - fail closed",
    "NSIL_DANGEROUS_CMD": "Dangerous command pattern detected",
    "NSIL_SCOPE_VIOLATION": "Action targets out-of-scope path",
    "NSIL_CONSTRAINT_VIOLATION": "Active constraint violation",
    "NSIL_HYGIENE_FAILURE": "Hygiene precondition not met",
    "NSIL_GATE_CONTRACT_VIOLATION": "Gate contract constraint violation",
    "NSIL_FILE_SCOPE_VIOLATION": "Action targets non-allowed file",
}


@dataclass(frozen=True)
class ActionProposal:
    """Represents an action to be verified.

    Attributes:
        action_type: Type of action (bash, write, edit, tool_call, read, grep, etc.)
        target: Target of the action (file path, command, etc.)
        content: Content of the action (for write/edit)
        context: Additional context for verification
    """

    action_type: str
    target: str = ""
    content: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    """Result of action verification.

    Attributes:
        approved: True if action is approved
        violations: List of violation descriptions
        repairs: List of suggested repair actions
        confidence: Confidence score (0.0-1.0)
        violation_codes: List of deterministic violation codes
    """

    approved: bool = True
    violations: tuple[str, ...] = field(default_factory=tuple)
    repairs: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    violation_codes: tuple[str, ...] = field(default_factory=tuple)


# Dangerous command patterns
DANGEROUS_PATTERNS = [
    (r"rm\s+-rf?\s+", "NSIL_DANGEROUS_CMD", "Recursive remove pattern"),
    (r"rm\s+-rf?\s+/", "NSIL_DANGEROUS_CMD", "Root recursive remove"),
    (r"sudo\s+rm\b", "NSIL_DANGEROUS_CMD", "Sudo remove"),
    (r"dd\s+if=", "NSIL_DANGEROUS_CMD", "dd with input file"),
    (r">\s*/dev/sd", "NSIL_DANGEROUS_CMD", "Direct block device write"),
    (r":\(\)\{", "NSIL_DANGEROUS_CMD", "Fork bomb pattern"),
    (r"chmod\s+-R\s+777", "NSIL_DANGEROUS_CMD", "World-writable permissions"),
    (r"curl\s+[^|]+\s*\|", "NSIL_DANGEROUS_CMD", "Pipe to shell (curl | bash)"),
    (r"wget\s+[^|]+\s*\|", "NSIL_DANGEROUS_CMD", "Pipe to shell (wget | bash)"),
    (r"eval\s+\$", "NSIL_DANGEROUS_CMD", "Eval of variable"),
    (
        r"\$\(echo\s+[\w+/=]+\s*\|\s*base64\s+-d\)",
        "NSIL_DANGEROUS_CMD",
        "Base64 obfuscated execution",
    ),
    (r"rm\s+-rf\s+[\"\'].*;\s*\w+", "NSIL_DANGEROUS_CMD", "Shell injection in quoted rm"),
]

# Blocked file patterns
BLOCKED_PATTERNS = [
    (r"\.env(?:\.|$)", "NSIL_FILE_SCOPE_VIOLATION", "Environment file"),
    (r"\.git/credentials", "NSIL_FILE_SCOPE_VIOLATION", "Git credentials"),
    (r"id_rsa", "NSIL_FILE_SCOPE_VIOLATION", "SSH private key"),
    (r"\.aws/credentials", "NSIL_FILE_SCOPE_VIOLATION", "AWS credentials"),
]


def _check_dangerous_command(proposal: ActionProposal) -> list[tuple[str, str]]:
    """Check for dangerous command patterns."""
    import re

    violations = []

    # Only check bash actions
    if proposal.action_type != "bash":
        return violations

    cmd = proposal.content or proposal.target

    # Unicode normalization check (NFKC) to catch homoglyphs
    import unicodedata

    normalized_cmd = unicodedata.normalize("NFKC", cmd)
    if normalized_cmd != cmd:
        # Check if normalized version contains dangerous patterns
        for pattern, code, desc in DANGEROUS_PATTERNS:
            if re.search(pattern, normalized_cmd, re.IGNORECASE):
                violations.append((code, f"{desc} (Unicode obfuscated)"))

    for pattern, code, desc in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            violations.append((code, desc))

    return violations


def _check_file_scope(proposal: ActionProposal, project_root: str) -> list[tuple[str, str]]:
    """Check for file scope violations."""
    import re

    violations = []

    target = proposal.target or ""

    # Check blocked file patterns
    for pattern, code, desc in BLOCKED_PATTERNS:
        if re.search(pattern, target, re.IGNORECASE):
            violations.append((code, desc))

    # Check for path traversal attempts
    if ".." in target:
        violations.append(("NSIL_SCOPE_VIOLATION", "Path traversal detected"))

    return violations


def _check_gate_contract_constraints(
    proposal: ActionProposal,
    gate_contract: dict[str, Any],
) -> list[tuple[str, str]]:
    """Check against gate contract constraints."""
    violations = []

    # Check local_pre_push restrictions
    pre_push = gate_contract.get("local_pre_push", [])
    if pre_push:
        # Extract constraint names
        constraint_names = [p.get("id", "") for p in pre_push]

        # If there's a "required_profile" check, verify it's not being bypassed
        if (
            "required_profile" in constraint_names
            and "git commit" in (proposal.content or proposal.target)
            and "check_required_profile" not in proposal.context
        ):
            violations.append(
                (
                    "NSIL_GATE_CONTRACT_VIOLATION",
                    "Git commit without required profile check",
                )
            )

    # Check required_checks
    required = gate_contract.get("required_checks", [])
    if required and proposal.action_type == "bash":
        cmd = proposal.content or proposal.target
        if "pytest" in cmd and "coverage" not in cmd:
            # Tests without coverage might violate quality gate
            pass  # This is just informational

    return violations


def _check_active_constraints(
    proposal: ActionProposal,
    active_constraints: list[str],
) -> list[tuple[str, str]]:
    """Check against active behavioral constraints."""
    import re

    violations = []

    for constraint in active_constraints:
        constraint_lower = constraint.lower()

        # No dangerous commands constraint
        if "no-rm-rf" in constraint_lower or "no-dangerous" in constraint_lower:
            dangerous_violations = _check_dangerous_command(proposal)
            for code, _desc in dangerous_violations:
                if code == "NSIL_DANGEROUS_CMD":
                    violations.append(
                        ("NSIL_CONSTRAINT_VIOLATION", f"Violates constraint: {constraint}")
                    )

        # Path scope constraint
        if constraint_lower.startswith("scope-"):
            scope = constraint_lower[6:]  # Get scope name after "scope-"
            target = proposal.target or ""
            if scope and scope not in target:
                violations.append(
                    ("NSIL_SCOPE_VIOLATION", f"Violates scope constraint: {constraint}")
                )

        # No prod changes constraint
        if "no-prod" in constraint_lower or "no-production" in constraint_lower:
            target = proposal.target or ""
            if re.search(r"(?:^|/)prod(?:/|$)", target, re.IGNORECASE):
                violations.append(
                    ("NSIL_SCOPE_VIOLATION", f"Violates no-prod constraint: {constraint}")
                )

        # Verify before commit constraint
        if (
            "verify" in constraint_lower
            and "commit" in constraint_lower
            and "git commit" in (proposal.content or proposal.target)
            and not proposal.context.get("verified", False)
        ):
            violations.append(
                (
                    "NSIL_CONSTRAINT_VIOLATION",
                    f"Commit without verification: {constraint}",
                )
            )

    return violations


def _check_hygiene_preconditions(
    proposal: ActionProposal,
    hygiene_state: dict[str, Any],
) -> list[tuple[str, str]]:
    """Check hygiene preconditions."""
    violations = []

    if proposal.action_type == "bash":
        cmd = proposal.content or proposal.target

        # Check for git operations
        if cmd.startswith("git commit"):
            # Verify there's a message
            if "-m" not in cmd and "--message" not in cmd:
                violations.append(("NSIL_HYGIENE_FAILURE", "Git commit without message"))

            # Check if hook bypass flags are used
            if "--no-verify" in cmd:
                violations.append(("NSIL_HYGIENE_FAILURE", "Git commit with --no-verify"))

        # Check for git push
        if cmd.startswith("git push"):
            if hygiene_state.get("uncommitted_changes", False):
                violations.append(("NSIL_HYGIENE_FAILURE", "Git push with uncommitted changes"))

            if hygiene_state.get("lint_dirty", False):
                violations.append(("NSIL_HYGIENE_FAILURE", "Git push with lint issues"))

    return violations


def generate_repairs(
    proposal: ActionProposal,
    violation_codes: list[str],
) -> list[str]:
    """Generate constraint-compliant repair suggestions.

    Repairs are syntactically valid strings and do not repeat the violating payload verbatim.

    Args:
        proposal: The action proposal that was rejected
        violation_codes: List of violation codes

    Returns:
        List of repair suggestions
    """
    import re

    repairs: list[str] = []
    cmd = proposal.content or proposal.target

    for code in set(violation_codes):  # Deduplicate
        if code == "NSIL_DANGEROUS_CMD":
            # Analyze the dangerous pattern and suggest safer alternative
            if re.search(r"rm\s+-rf?", cmd):
                # Suggest safer rm with confirmation
                repairs.append(
                    "Use 'rm -i' for interactive deletion or 'rm -rf' with explicit paths"
                )
            elif re.search(r"curl.*\|", cmd):
                # Suggest downloading first
                repairs.append("Download script to file first, review, then execute")
            elif re.search(r"sudo\s+rm", cmd):
                repairs.append(
                    "Use 'sudo -k' to invalidate cache, or run with explicit confirmation"
                )
            elif re.search(r"chmod\s+-R\s+777", cmd):
                repairs.append(
                    "Use more restrictive permissions like '755' for dirs, '644' for files"
                )
            elif re.search(r":\(\)\{", cmd):
                repairs.append("Use a safe test pattern instead of fork bomb")
            else:
                repairs.append("Review and modify the dangerous command pattern")

        elif code == "NSIL_SCOPE_VIOLATION":
            target = proposal.target or ""
            # Suggest restricting to allowed scope
            if "prod" in target.lower():
                repairs.append("Restrict changes to non-production environment paths")
            elif ".." in target:
                repairs.append("Use absolute paths or paths relative to project root")
            else:
                repairs.append("Restrict action to allowed scope")

        elif code == "NSIL_CONSTRAINT_VIOLATION":
            repairs.append("Satisfy the active constraint before proceeding")

        elif code == "NSIL_HYGIENE_FAILURE":
            if "git commit" in cmd:
                if "--no-verify" in cmd:
                    repairs.append("Remove --no-verify flag and allow hooks to run")
                else:
                    repairs.append("Add commit message with -m flag")
            elif "git push" in cmd:
                repairs.append("Commit changes first, or address lint issues before push")
            else:
                repairs.append("Fix hygiene precondition issues")

        elif code == "NSIL_GATE_CONTRACT_VIOLATION":
            repairs.append("Complete required gate checks before proceeding")

        elif code == "NSIL_FILE_SCOPE_VIOLATION":
            repairs.append("Do not modify protected file types (.env, credentials, keys)")

        elif code == "NSIL_UNKNOWN_ACTION":
            repairs.append("Use a known action type (bash, write, edit, read, grep, glob)")

    return repairs


def verify_action(
    proposal: ActionProposal,
    project_root: str = ".",
    gate_contract: dict[str, Any] | None = None,
    active_constraints: list[str] | None = None,
    hygiene_state: dict[str, Any] | None = None,
) -> VerificationResult:
    """Verify an action proposal.

    This is pure local logic with no network or LLM calls.

    Args:
        proposal: The action to verify
        project_root: Project root path
        gate_contract: Gate contract dict (optional)
        active_constraints: List of active constraints (optional)
        hygiene_state: Hygiene state dict (optional)

    Returns:
        VerificationResult with approval status and violation details
    """
    violations: list[tuple[str, str]] = []
    repairs: list[str] = []
    violation_codes: list[str] = []

    # Default empty states
    gate_contract = gate_contract or {}
    active_constraints = active_constraints or []
    hygiene_state = hygiene_state or {}
    # Fail closed for unknown action types (adversarial requirement)
    known_actions = {
        "bash",
        "write",
        "edit",
        "read",
        "grep",
        "glob",
        "tool_call",
        "mkdir",
        "delete",
    }
    if proposal.action_type not in known_actions:
        return VerificationResult(
            approved=False,
            violations=("Unknown action type",),
            repairs=("Use a known action type",),
            confidence=1.0,
            violation_codes=("NSIL_UNKNOWN_ACTION",),
        )

    # Run all checks
    violations.extend(_check_dangerous_command(proposal))
    violations.extend(_check_file_scope(proposal, project_root))
    violations.extend(_check_gate_contract_constraints(proposal, gate_contract))
    # Pass converged context to active constraints check
    violations.extend(_check_active_constraints(proposal, active_constraints))
    violations.extend(_check_hygiene_preconditions(proposal, hygiene_state))

    # Build result
    if violations:
        violation_codes = [v[0] for v in violations]
        violation_descs = [v[1] for v in violations]

        # Generate repair suggestions using the repair generator
        repairs = generate_repairs(proposal, violation_codes)

        return VerificationResult(
            approved=False,
            violations=tuple(violation_descs),
            repairs=tuple(dict.fromkeys(repairs)),  # Deduplicate while preserving order
            confidence=1.0,
            violation_codes=tuple(dict.fromkeys(violation_codes)),
        )

    return VerificationResult(approved=True)
