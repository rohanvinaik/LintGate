"""Policy grammar compiler for NSIL."""

import re
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyGrammar:
    """Compiled policy grammar from gate contracts and constraints.

    Attributes:
        source_constraints: Original constraints that were compiled
        gbnf_rules: GBNF grammar rules for constraint enforcement
        regex_pattern: Regex pattern equivalent for simple constraints
        explanation: Human-readable explanation of compiled constraints
    """

    source_constraints: tuple[str, ...] = field(default_factory=tuple)
    gbnf_rules: str = ""
    regex_pattern: str = ""
    explanation: str = ""


# Known constraint families with their compile functions
_CONSTRAINT_COMPILERS: dict[str, callable] = {}


def _compile_dangerous_command_constraint(constraint: str) -> dict[str, str] | None:
    """Compile dangerous command constraints.

    Examples: "no-rm-rf", "forbid-rm-recursive"
    """
    # Map of dangerous patterns to their regex equivalents
    dangerous_patterns = {
        "rm -rf": r"rm\s+-rf?\s+",
        "rm -r": r"rm\s+-r\s+",
        "rm -f": r"rm\s+-f\s+",
        "sudo rm": r"sudo\s+rm\b",
        "rm -rf /": r"rm\s+-rf?\s+/",
        "dd if=": r"dd\s+if=",
        "> /dev/sd": r">\s+/dev/sd",
    }

    # Check if this is a "no-X" or "forbid-X" style constraint
    # that maps to a dangerous pattern
    no_match = re.match(r"(?:no[-_]|forbid[-_])(.+)", constraint, re.IGNORECASE)
    if no_match:
        pattern_name = no_match.group(1).lower().replace("-", " ").replace("_", " ")
        # Check if this maps to a known dangerous pattern
        # Also try matching by first word (e.g., "rm rf" -> "rm")
        first_word = pattern_name.split()[0] if pattern_name else ""
        for pattern, regex in dangerous_patterns.items():
            pattern_words = pattern.split()
            # Check if pattern words match (e.g., "rm rf" matches "rm -rf")
            if (
                pattern in pattern_name
                or pattern_name in pattern
                or (first_word and pattern_words[0] == first_word)
            ):
                gbnf = f'  forbidden_{pattern.replace(" ", "_").replace("-", "_")} = "\\"{regex}\\" ;'
                return {
                    "gbnf": f"grammar ::= (.*\\n)*  ;\\n{gbnf}",
                    "regex": f"(?:{regex})",
                    "explanation": f"Blocks dangerous command pattern: {pattern}",
                }

    # Original check for direct pattern matching
    gbnf_parts = []
    regex_parts = []

    for pattern, regex in dangerous_patterns.items():
        if pattern.lower() in constraint.lower():
            gbnf_parts.append(
                f'  forbidden_{pattern.replace(" ", "_").replace("-", "_")} = "\\"{regex}\\" ;'
            )
            regex_parts.append(f"(?:{regex})")

    if gbnf_parts:
        gbnf = "grammar ::= (.*\n)*  ;\n"
        gbnf += "\n".join(gbnf_parts)
        return {
            "gbnf": gbnf,
            "regex": f"(?:{')|('.join(regex_parts)})" if regex_parts else "",
            "explanation": f"Blocks dangerous command patterns: {', '.join(d for p, d in dangerous_patterns.items() if p.lower() in constraint.lower())}",
        }

    return None


def _compile_verification_constraint(constraint: str) -> dict[str, str] | None:
    """Compile verification-before-commit constraints.

    Examples: "verify-before-commit", "require-tests"
    """
    if "verify" in constraint.lower() and "commit" in constraint.lower():
        gbnf = """grammar ::= "Verification: tests passed" | "Verification: lint passed" | "Verification: all checks passed" ;
verification_check = "Verification: " , ( "tests passed" | "lint passed" | "all checks passed" ) ;"""
        return {
            "gbnf": gbnf,
            "regex": "Verification:\\s*(?:tests\\s+passed|lint\\s+passed|all\\s+checks\\s+passed)",
            "explanation": "Requires explicit verification before commit",
        }

    if "require" in constraint.lower() and "test" in constraint.lower():
        return {
            "gbnf": 'grammar ::= "Tests: passed" ;',
            "regex": "Tests:\\s*passed",
            "explanation": "Requires test verification",
        }

    return None


def _compile_path_scope_constraint(constraint: str) -> dict[str, str] | None:
    """Compile path scope constraints.

    Examples: "scope-lib", "scope-lintgate", "no-prod-changes"
    """
    # Match patterns like "scope-X" or "no-prod" or "path-only-X"
    scope_match = re.match(r"scope[-_]?(\w+)", constraint, re.IGNORECASE)
    if scope_match:
        scope = scope_match.group(1)
        gbnf = f"""grammar ::= "Path: allowed" ;
allowed_path = ( "/{scope}/" | "./{scope}/" | "{scope}/" ) ;"""
        regex = rf"(?:^|/)(?:{scope}/|\./{scope}/|{scope}/)"
        return {
            "gbnf": gbnf,
            "regex": regex,
            "explanation": f"Restricts operations to scope: {scope}",
        }

    if "no-prod" in constraint.lower() or "no-production" in constraint.lower():
        return {
            "gbnf": 'grammar ::= "Prod: not modified" ;',
            "regex": "Prod:\\s*not\\s+modified",
            "explanation": "Blocks production environment modifications",
        }

    return None


def _compile_tool_call_constraint(constraint: str) -> dict[str, str] | None:
    """Compile tool-call constraints.

    Examples: "allow-read", "forbid-bash", "only-grep-glob"
    """
    if "allow-" in constraint.lower():
        tool = constraint.lower().split("allow-")[1]
        return {
            "gbnf": f'grammar ::= "Action: {tool}" ;',
            "regex": rf"Action:\s*{tool}\b",
            "explanation": f"Explicitly allows tool: {tool}",
        }
    if "forbid-" in constraint.lower():
        tool = constraint.lower().split("forbid-")[1]
        return {
            "gbnf": f'grammar ::= ( [^A] | "A" [^c] | "Ac" [^t] | "Act" [^i] | "Acti" [^o] | "Actio" [^n] | "Action: " [^{tool[0]}] )* ;',
            "regex": rf"Action:\s*(?!{tool}\b)\w+",
            "explanation": f"Forbids tool: {tool}",
        }
    return None


def _compile_env_var_constraint(constraint: str) -> dict[str, str] | None:
    """Compile environment variable constraints.

    Examples: "no-export-aws", "forbid-env-secret"
    """
    if "no-export-" in constraint.lower():
        var_prefix = constraint.lower().split("no-export-")[1]
        regex = rf"export\s+{var_prefix}\w*="
        return {
            "gbnf": f'grammar ::= (.*\\n)* ; forbidden_env = "export {var_prefix}" ;',
            "regex": regex,
            "explanation": f"Blocks export of environment variables starting with: {var_prefix}",
        }
    return None


def _compile_default_constraint(constraint: str) -> dict[str, str]:
    """Handle unknown constraint shapes.

    Returns explanation and skips safely without crashing.
    """
    return {
        "gbnf": "grammar ::= .* ;",
        "regex": ".*",
        "explanation": f"Unknown constraint type: '{constraint}' - constraint skipped",
    }


# Register compilers in priority order
_CONSTRAINT_COMPILERS = [
    _compile_dangerous_command_constraint,
    _compile_verification_constraint,
    _compile_path_scope_constraint,
    _compile_tool_call_constraint,
    _compile_env_var_constraint,
    _compile_default_constraint,
]


def compile_policy_grammar(
    constraints: list[str],
    gate_contract: dict[str, Any],
) -> PolicyGrammar:
    """Compile gate contracts and constraints into PolicyGrammar.

    This function is deterministic: identical inputs produce byte-identical outputs.

    Args:
        constraints: List of active constraint strings
        gate_contract: Gate contract dict (from YAML or other source)

    Returns:
        PolicyGrammar with compiled outputs
    """
    # Sort constraints for deterministic output
    sorted_constraints = sorted(set(constraints))

    gbnf_parts = []
    regex_parts = []
    explanation_parts = []
    unsupported_notes = []

    for constraint in sorted_constraints:
        compiled = None

        # Try each compiler in priority order
        for compiler in _CONSTRAINT_COMPILERS:
            compiled = compiler(constraint)
            if compiled is not None:
                break

        if compiled:
            gbnf_parts.append(compiled.get("gbnf", ""))
            if compiled.get("regex"):
                regex_parts.append(compiled["regex"])
            if "Unknown constraint" in compiled.get("explanation", ""):
                unsupported_notes.append(compiled["explanation"])
            elif compiled.get("explanation"):
                explanation_parts.append(compiled["explanation"])

    # Extract additional constraints from gate_contract directly
    if allowed_tools := gate_contract.get("allowed_tools"):
        tools_list = "|".join(f'"{t}"' for t in allowed_tools)
        gbnf_parts.append(f'grammar ::= "Action: " ( {tools_list} ) ;')
        regex_parts.append(rf"Action:\s*(?:{'|'.join(allowed_tools)})\b")
        explanation_parts.append(f"Restricted to tools: {', '.join(allowed_tools)}")

    if blocked_vars := gate_contract.get("blocked_env_vars"):
        for var in blocked_vars:
            regex_parts.append(rf"export\s+{var}=")
            explanation_parts.append(f"Blocked env var: {var}")

    # Build final outputs
    gbnf_rules = "\n\n".join(gbnf_parts) if gbnf_parts else "grammar ::= .* ;"
    regex_pattern = "(?:" + ")|(".join(regex_parts) + ")" if regex_parts else ".*"
    explanation = (
        "; ".join(explanation_parts) if explanation_parts else "No constraints active"
    )

    if unsupported_notes:
        explanation += " | Unsupported: " + "; ".join(unsupported_notes)

    return PolicyGrammar(
        source_constraints=tuple(sorted_constraints),
        gbnf_rules=gbnf_rules,
        regex_pattern=regex_pattern,
        explanation=explanation,
    )


def compile_from_gate_contract(
    contract_path: str | None = None,
    constraints: list[str] | None = None,
) -> PolicyGrammar:
    """Compile policy grammar from gate contract file.

    Args:
        contract_path: Path to gate_contract.yaml (optional)
        constraints: Additional constraints (optional)

    Returns:
        PolicyGrammar with compiled outputs
    """
    from pathlib import Path

    import yaml

    gate_contract: dict[str, Any] = {}

    if contract_path:
        path = Path(contract_path)
        if path.exists():
            with suppress(Exception):
                gate_contract = yaml.safe_load(path.read_text()) or {}

    # Extract constraints from contract if not provided
    active_constraints = list(constraints or [])

    # Add constraints from contract if present
    if "constraints" in gate_contract:
        active_constraints.extend(gate_contract["constraints"])

    return compile_policy_grammar(active_constraints, gate_contract)
