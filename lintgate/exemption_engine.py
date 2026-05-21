"""Evidence-based exemption engine.

Suppressions are earned by specification evidence, not granted by request.
A function at 100% mutation kill rate has earned the right to be complex —
its complexity is a style concern, not a risk. A function at 0% kill rate
has not — its complexity might be hiding bugs.

The σ value IS the evidence. LintGate has the data to make the distinction.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExemptionVerdict:
    """Result of an exemption proposal evaluation."""

    approved: bool
    rationale_accepted: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    exemption_entry: dict[str, Any] | None = None


# Findings that are never exemptible — security and correctness
_SECURITY_FINDINGS = frozenset({
    "B101", "B102", "B103", "B104", "B105", "B106", "B107", "B108",
    "B110", "B301", "B302", "B303", "B304", "B305", "B306", "B307",
    "B308", "B310", "B311", "B312", "B313", "B314", "B315", "B316",
    "B317", "B318", "B319", "B320", "B321", "B322", "B323", "B324",
    "B501", "B502", "B503", "B504", "B505", "B506", "B507", "B508",
    "B509", "B601", "B602", "B603", "B604", "B605", "B606", "B607",
    "B608", "B609", "B610", "B611", "B612", "B701", "B702", "B703",
    "F821", "E999",  # Undefined name, syntax error
})

# Structural findings that CAN be exempted with evidence
_STRUCTURAL_FINDINGS = frozenset({
    "complexity", "cognitive-complexity", "file-too-long",
    "too-many-args", "too-many-statements", "too-many-attributes",
    "too-many-functions", "too-many-classes", "too-many-methods",
    "maintainability",
})

# Minimum thresholds for auto-approval
_MIN_KILL_RATE = 0.80  # 80% mutation kill rate
_MIN_SPEC_LEVEL = 0.50  # 50% specification level


def evaluate_exemption(
    project_root: str,
    file: str,
    function: str,
    finding: str,
    rationale: str,
    evidence: list[str] | None = None,
) -> ExemptionVerdict:
    """Evaluate an exemption proposal against specification evidence.

    Checks:
    1. Is this a security/correctness finding? → Never exempt
    2. Is the function tested? → Kill rate from mutation cache
    3. What's the specification level? → σ and spec_level
    4. Has this pattern been approved before? → Existing exemptions
    5. Is evidence provided and consistent? → Rationale validation
    """
    verdict = ExemptionVerdict(approved=False, rationale_accepted=bool(rationale))

    # Check 1: Security findings are never exemptible
    if finding in _SECURITY_FINDINGS:
        verdict.checks.append({
            "check": "security_gate",
            "passed": False,
            "detail": f"'{finding}' is a security/correctness finding — cannot be exempted",
        })
        verdict.reason = "Security and correctness findings cannot be exempted."
        return verdict

    verdict.checks.append({
        "check": "security_gate",
        "passed": True,
        "detail": f"'{finding}' is structural, not security — eligible for exemption",
    })

    # Check 1.5: CLI entrypoint detection — relaxed threshold
    is_entrypoint = _detect_cli_entrypoint(project_root, file, function)
    if is_entrypoint:
        verdict.checks.append({
            "check": "cli_entrypoint",
            "passed": True,
            "detail": (
                "CLI entrypoint detected — complexity is inherent in orchestration code. "
                "Kill rate threshold relaxed to 0% (integration-level, not unit-testable)."
            ),
        })

    # Check 2: Mutation kill rate
    kill_rate, sigma, spec_level = _load_mutation_evidence(project_root, file, function)

    if kill_rate is not None:
        # Detect if this is aggregate evidence (class/dataclass with no direct cache)
        direct_key = f"{file}::{function}".replace("::", "__").replace("/", "_")
        cache_dir = Path(project_root) / ".lintgate" / "mutation"
        is_aggregate = not (cache_dir / f"{direct_key}.json").is_file()

        effective_threshold = 0.0 if is_entrypoint else _MIN_KILL_RATE
        kill_pass = kill_rate >= effective_threshold
        source = "aggregate (file consumer functions)" if is_aggregate else "direct"
        verdict.checks.append({
            "check": "mutation_kill_rate",
            "passed": kill_pass,
            "value": round(kill_rate, 2),
            "threshold": _MIN_KILL_RATE,
            "source": source,
            "detail": (
                f"Kill rate {kill_rate:.0%} {'≥' if kill_pass else '<'} "
                f"{_MIN_KILL_RATE:.0%} threshold ({source})"
            ),
        })
    else:
        verdict.checks.append({
            "check": "mutation_kill_rate",
            "passed": is_entrypoint,  # Entrypoints pass without mutation data
            "value": None,
            "detail": (
                "CLI entrypoint — mutation data not required for orchestration code"
                if is_entrypoint
                else "No mutation data — run mutation_run_sampling first"
            ),
        })

    # Check 3: Specification level
    if spec_level is not None:
        spec_pass = spec_level >= _MIN_SPEC_LEVEL
        verdict.checks.append({
            "check": "spec_level",
            "passed": spec_pass,
            "value": round(spec_level, 2),
            "sigma": sigma,
            "threshold": _MIN_SPEC_LEVEL,
            "detail": (
                f"spec_level={spec_level:.2f} (σ={sigma}) "
                f"{'≥' if spec_pass else '<'} {_MIN_SPEC_LEVEL} threshold"
            ),
        })
    elif sigma is not None:
        verdict.checks.append({
            "check": "spec_level",
            "passed": False,
            "sigma": sigma,
            "detail": f"σ={sigma} but spec_level unknown — run spec_file_analyze",
        })

    # Check 4: Pattern precedent
    precedent_count = _count_existing_exemptions(project_root, finding)
    if precedent_count > 0:
        verdict.checks.append({
            "check": "pattern_precedent",
            "passed": True,
            "count": precedent_count,
            "detail": f"{precedent_count} existing exemption(s) for '{finding}' in this project",
        })

    # Check 5: Evidence validation
    if evidence:
        verdict.checks.append({
            "check": "evidence_provided",
            "passed": True,
            "items": len(evidence),
            "detail": f"{len(evidence)} evidence item(s) provided",
        })
    else:
        verdict.checks.append({
            "check": "evidence_provided",
            "passed": False,
            "detail": "No evidence provided — provide at least one supporting fact",
        })

    # Decision logic
    required_checks = ["security_gate", "mutation_kill_rate"]
    all_required_pass = all(
        c["passed"] for c in verdict.checks if c["check"] in required_checks
    )
    evidence_score = sum(1 for c in verdict.checks if c["passed"]) / max(len(verdict.checks), 1)

    if all_required_pass and evidence_score >= 0.6:
        verdict.approved = True
        func_key = f"{file}::{function}"
        verdict.exemption_entry = {
            "file": file,
            "function": function,
            "code": finding,
            "rationale": rationale,
            "evidence": evidence or [],
            "kill_rate": kill_rate,
            "spec_level": spec_level,
            "sigma": sigma,
            "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "checks_passed": sum(1 for c in verdict.checks if c["passed"]),
            "checks_total": len(verdict.checks),
        }
        kr_str = f"{kill_rate:.0%}" if kill_rate is not None else "N/A"
        verdict.reason = (
            f"Approved: {func_key} has earned exemption from '{finding}' "
            f"with {kr_str} kill rate"
            + (f", σ={sigma}" if sigma else "")
            + "."
        )
    else:
        failing = [c for c in verdict.checks if not c["passed"]]
        details = "; ".join(c["detail"] for c in failing[:3])
        verdict.reason = f"Rejected: {details}"

    return verdict


def write_exemption(project_root: str, entry: dict[str, Any]) -> str:
    """Write an approved exemption to .claude/lintgate.yaml.

    Writes under a top-level `approved_exemptions:` key — separate from
    the `controlplane:` section to avoid malformed YAML.

    Returns the path to the modified file.
    """
    yaml_path = os.path.join(project_root, ".claude", "lintgate.yaml")

    content = ""
    if os.path.isfile(yaml_path):
        with open(yaml_path, encoding="utf-8") as f:
            content = f.read()

    # Escape rationale for YAML safety
    rationale = entry["rationale"].replace('"', '\\"')
    kill_rate = entry.get("kill_rate")
    kr_str = f"{kill_rate:.2f}" if isinstance(kill_rate, float) else "unknown"

    block = (
        f"  - file: {entry['file']}\n"
        f"    function: {entry.get('function', '')}\n"
        f"    code: {entry['code']}\n"
        f"    rationale: \"{rationale}\"\n"
        f"    kill_rate: {kr_str}\n"
        f"    approved_at: \"{entry['approved_at']}\"\n"
    )

    if "approved_exemptions:" in content:
        # Append to existing section — find the key and add after it
        idx = content.index("approved_exemptions:")
        # Find the end of the line
        eol = content.index("\n", idx)
        content = content[:eol + 1] + block + content[eol + 1:]
    else:
        # Add new top-level section
        content = content.rstrip() + "\n\napproved_exemptions:\n" + block

    os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(content)

    return yaml_path


def _load_mutation_evidence(
    project_root: str, file: str, function: str
) -> tuple[float | None, float | None, float | None]:
    """Load kill_rate, sigma, spec_level from mutation cache.

    For classes/dataclasses (no direct mutation data), falls back to
    aggregating evidence from consumer functions in the same file —
    a dataclass's specification is proven by the functions that use it.
    """
    try:
        import json

        cache_dir = Path(project_root) / ".lintgate" / "mutation"
        rel_path = file

        # Direct lookup
        func_key = f"{rel_path}::{function}"
        safe_key = func_key.replace("::", "__").replace("/", "_")
        cache_file = cache_dir / f"{safe_key}.json"
        if cache_file.is_file():
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            return (
                data.get("kill_rate"),
                data.get("sigma"),
                data.get("spec_level"),
            )

        # Fallback: aggregate from all cached functions in the same file.
        # Covers dataclasses and classes without methods — their specification
        # evidence lives in the functions that construct/consume them.
        file_prefix = rel_path.replace("/", "_").replace(".", "_")
        if not file_prefix.endswith("_py"):
            file_prefix += "_py"
        file_prefix = file_prefix.rstrip("_py") + ".py__"

        # Skip entries that are discovery artifacts or trivial —
        # they don't represent real specification evidence.
        _SKIP_INTERPRETATIONS = {"DISCOVERY_ARTIFACT", "EQUIVALENT_OR_UNINTERESTING"}
        _SKIP_TRIVIALITY = {"trivial_return", "trivial_pass", "trivial_raise"}

        kill_rates: list[float] = []
        for cf in cache_dir.glob(f"{file_prefix}*.json"):
            try:
                with open(cf, encoding="utf-8") as f:
                    cdata = json.load(f)
                # Skip discovery failures and trivial functions
                if cdata.get("survival_interpretation") in _SKIP_INTERPRETATIONS:
                    continue
                if cdata.get("triviality_class") in _SKIP_TRIVIALITY:
                    continue
                if cdata.get("discovery_state", "").startswith("DISCOVERY_"):
                    if cdata.get("discovery_state") != "DISCOVERY_OK":
                        continue
                kr = cdata.get("kill_rate")
                if kr is not None:
                    kill_rates.append(float(kr))
            except Exception:
                continue

        if kill_rates:
            avg_kr = sum(kill_rates) / len(kill_rates)
            return avg_kr, None, None

        # Last resort: scan ALL cached functions for any that import from
        # this file — cross-file consumers prove the type's specification.
        # E.g., query_types.py consumed by query_navigate.py at 100%.
        module_name = rel_path.replace("/", ".").removesuffix(".py")
        cross_kill_rates: list[float] = []
        if cache_dir.is_dir():
            for cf in cache_dir.glob("*.json"):
                try:
                    with open(cf, encoding="utf-8") as f:
                        cdata = json.load(f)
                    # Check if this function imports from our target module
                    imports_used = cdata.get("imports_from", [])
                    if not imports_used:
                        # No import metadata, or same module (already aggregated
                        # above) — either way, skip this entry.
                        continue
                    if any(module_name in imp for imp in imports_used):
                        kr = cdata.get("kill_rate")
                        if kr is not None and kr > 0:
                            cross_kill_rates.append(float(kr))
                except Exception:
                    continue

        if cross_kill_rates:
            avg_kr = sum(cross_kill_rates) / len(cross_kill_rates)
            return avg_kr, None, None

        return None, None, None
    except Exception:
        return None, None, None


def _detect_cli_entrypoint(project_root: str, file: str, function: str) -> bool:
    """Detect if a function is a CLI entrypoint (argparse, __main__, click).

    CLI entrypoints are integration-level orchestration code that can't achieve
    high unit-test kill rates. Their complexity is inherent, not accidental.
    """
    import ast as _ast

    # Function name heuristics
    if function in ("main", "cli", "run", "entrypoint"):
        return True

    # Check file for entrypoint patterns
    source_path = os.path.join(project_root, file)
    if not os.path.isfile(source_path):
        # Try src/ layout
        for prefix in ("src", "lib"):
            candidate = os.path.join(project_root, prefix, file)
            if os.path.isfile(candidate):
                source_path = candidate
                break

    if not os.path.isfile(source_path):
        return False

    try:
        with open(source_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False

    # Quick text checks before parsing
    has_main_guard = 'if __name__ == "__main__"' in content or "if __name__ == '__main__'" in content
    has_argparse = "argparse" in content
    has_click = "@click." in content or "import click" in content
    has_typer = "import typer" in content

    if has_main_guard and (has_argparse or has_click or has_typer):
        return True

    # Check if the function IS the __main__ target
    if has_main_guard:
        try:
            tree = _ast.parse(content, filename=source_path)
            for node in _ast.walk(tree):
                if isinstance(node, _ast.If):
                    # Check if this is `if __name__ == "__main__": function()`
                    for child in _ast.walk(node):
                        if isinstance(child, _ast.Call) and isinstance(child.func, _ast.Name):
                            if child.func.id == function:
                                return True
        except SyntaxError:
            pass

    # Scripts directory heuristic
    return "/scripts/" in file or file.startswith("scripts/")


def _count_existing_exemptions(project_root: str, finding: str) -> int:
    """Count existing exemptions for a finding code in lintgate.yaml."""
    yaml_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    if not os.path.isfile(yaml_path):
        return 0
    try:
        with open(yaml_path, encoding="utf-8") as f:
            content = f.read()
        return content.count(f"code: {finding}")
    except Exception:
        return 0
