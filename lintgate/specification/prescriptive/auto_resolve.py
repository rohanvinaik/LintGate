"""Autonomous code resolution engine.

Pipeline: PrescriptiveSpec → synthesis gate → [caller generates] → verify → accept.

The calling LLM IS the intelligence — the MCP tool never makes API calls.
Deterministic layers (cache, synthesis gate, AST verification, witness
execution) handle what they can. When a spec exceeds deterministic
resolution, the tool returns a constrained generation prompt and the
caller produces the body, passing it back for verification.

Resolution strategies (in order of preference):
1. Cache hit — spec content hash matches prior verification (0ms)
2. Synthesis gate — pure function matching known algebraic form (10ms)
3. Caller generation — tool returns constrained prompt, caller generates,
   tool verifies deterministically on re-call with proposed_body

Verification layers (all CPU, no LLM):
1. AST parse — syntactically valid Python
2. Structural checks — purity, return statement, no forbidden side effects
3. Witness execution — run against known I/O pairs in subprocess
4. Pytest execution — run generated test file (optional, slower)
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .backends import CompilationTargets
    from .gate import WitnessRecord
    from .spec import PrescriptiveSpec

from .resolution_cache import CachedResolution, get_cached, put_cached, spec_cache_key


@dataclass
class ResolveResult:
    """Result of autonomous code resolution."""

    target_key: str
    # resolved — body verified and cached
    # cached — returned from resolution cache
    # needs_generation — deterministic paths exhausted, prompt returned for caller
    # failed — proposed_body failed verification
    status: str = "pending"
    body: str = ""
    method: str = ""  # synthesis_gate | caller_verified | cache_hit
    confidence: float = 0.0
    verification: dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""
    elapsed_ms: int = 0
    resolved_path: str = ""

    # Populated when status == "needs_generation"
    generation_prompt: str = ""
    signature: str = ""

    # Populated when status == "failed" (verification failures for retry)
    retry_constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "target_key": self.target_key,
            "status": self.status,
            "method": self.method,
            "confidence": self.confidence,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.body:
            d["body"] = self.body
        if self.verification:
            d["verification"] = self.verification
        if self.failure_reason:
            d["failure_reason"] = self.failure_reason
        if self.resolved_path:
            d["resolved_path"] = self.resolved_path
        if self.generation_prompt:
            d["generation_prompt"] = self.generation_prompt
        if self.signature:
            d["signature"] = self.signature
        if self.retry_constraints:
            d["retry_constraints"] = self.retry_constraints
        return d


@dataclass
class ResolveConfig:
    """Configuration for resolution behavior."""

    verify_with_pytest: bool = False
    write_to_file: bool = True


# ── Main entry point ─────────────────────────────────────────────────


def resolve_one(
    spec: PrescriptiveSpec,
    targets: CompilationTargets,
    project_root: str,
    config: ResolveConfig | None = None,
    proposed_body: str = "",
) -> ResolveResult:
    """Resolve a single function from its prescriptive spec.

    Two calling modes:

    **First call** (no proposed_body):
      1. Check cache → return if hit
      2. Try synthesis gate (deterministic) → return if hit
      3. Return status="needs_generation" with generation_prompt for caller

    **Second call** (with proposed_body):
      1. Verify the proposed body (AST + structural + witnesses)
      2. If passed → cache, write, return status="resolved"
      3. If failed → return status="failed" with retry_constraints
    """
    if config is None:
        config = ResolveConfig()

    t0 = time.monotonic_ns()
    result = ResolveResult(target_key=spec.target_key)

    # ── Witness generation (shared by both paths) ────────────────
    from .gate import check_synthesis_gate, generate_executable_witnesses

    witnesses: list[WitnessRecord] = []
    if spec.problem_class == "pure" and spec.parameters:
        try:
            witnesses = generate_executable_witnesses(spec, project_root)
            if any(w.has_oracle_value for w in witnesses):
                targets.synthesis_profile["has_executable_witnesses"] = True
        except Exception:
            pass

    # ── Proposed body → verify ───────────────────────────────────
    if proposed_body:
        body = _normalize_body(proposed_body)
        verification = _verify_body(spec, body, project_root, witnesses, config)
        if verification["passed"]:
            result.status = "resolved"
            result.body = body
            result.method = "caller_verified"
            result.confidence = _score_confidence(verification)
            result.verification = verification
            result.elapsed_ms = _elapsed(t0)
            ck = spec_cache_key(spec)
            _accept(spec, result, project_root, ck, config)
            return result
        else:
            result.status = "failed"
            result.verification = verification
            result.retry_constraints = _failures_to_constraints(verification)
            result.generation_prompt = _build_prompt(spec, targets, result.retry_constraints)
            result.signature = _build_signature_line(spec)
            result.failure_reason = "; ".join(result.retry_constraints[:3])
            result.elapsed_ms = _elapsed(t0)
            return result

    # ── Step 1: Cache ────────────────────────────────────────────
    ck = spec_cache_key(spec)
    cached = get_cached(project_root, ck)
    if cached is not None:
        result.status = "cached"
        result.body = cached.body
        result.method = "cache_hit"
        result.confidence = cached.confidence
        result.verification = cached.verification
        result.elapsed_ms = _elapsed(t0)
        if config.write_to_file:
            result.resolved_path = _write_resolved(spec, cached.body, project_root) or ""
        return result

    # ── Step 2: Synthesis gate ───────────────────────────────────
    from .synthesis import synthesize_body

    gate = check_synthesis_gate(spec, targets)
    if gate.eligible:
        synth = synthesize_body(spec, witnesses, project_root)
        if synth.success:
            verification = _verify_body(spec, synth.body, project_root, witnesses, config)
            if verification["passed"]:
                result.status = "resolved"
                result.body = synth.body
                result.method = "synthesis_gate"
                result.confidence = synth.confidence
                result.verification = verification
                result.elapsed_ms = _elapsed(t0)
                _accept(spec, result, project_root, ck, config)
                return result

    # ── Step 3: Return prompt for caller ─────────────────────────
    result.status = "needs_generation"
    result.generation_prompt = _build_prompt(spec, targets)
    result.signature = _build_signature_line(spec)
    result.elapsed_ms = _elapsed(t0)
    return result


# ── Prompt Construction ──────────────────────────────────────────────


def _build_prompt(
    spec: PrescriptiveSpec,
    targets: CompilationTargets,
    failures: list[str] | None = None,
) -> str:
    """Build a minimal generation prompt from spec IR.

    This prompt is returned to the calling LLM. It contains the function
    signature, MUST/MUST NOT constraints, invariants, algebraic laws,
    and forbidden behaviors — everything the caller needs to generate
    a correct body with no additional context.
    """
    sig = _build_signature_line(spec)
    lines: list[str] = [
        f"Write ONLY the indented Python function body for:\n\n{sig}\n",
    ]

    # Generation constraints (sorted by priority, max 10)
    constraint_lines: list[str] = []
    for gc in sorted(targets.generation_constraints, key=lambda c: c.get("priority", 5))[:10]:
        ct = gc.get("constraint_type", "")
        desc = gc.get("description", "")
        if ct == "must_not_use":
            constraint_lines.append(f"MUST NOT: {desc}")
        elif ct == "must_use":
            constraint_lines.append(f"MUST: {desc}")
        elif desc:
            constraint_lines.append(desc)
    if constraint_lines:
        lines.append("CONSTRAINTS:")
        lines.extend(constraint_lines)
        lines.append("")

    # Invariant descriptions (compact, max 8)
    inv_descs = [inv.description for inv in spec.invariants[:8]]
    if inv_descs:
        for d in inv_descs:
            lines.append(f"- {d}")
        lines.append("")

    # Algebraic laws (max 4)
    for law in spec.algebraic_laws[:4]:
        name = law.get("name", law.get("property_name", ""))
        if name:
            lines.append(f"LAW: {name}")
    if spec.algebraic_laws:
        lines.append("")

    # Forbidden behaviors (max 4)
    for fb in spec.forbidden_behaviors[:4]:
        lines.append(f"FORBIDDEN: {fb.description}")
    if spec.forbidden_behaviors:
        lines.append("")

    # Retry context from previous verification failures
    if failures:
        lines.append("PREVIOUS ATTEMPT FAILED — fix these:")
        for f in failures[:5]:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("Style: pure, functional, SICP-inspired. No mutation. No side effects.")
    lines.append("Return ONLY the indented body lines (4-space indent). No def line. No explanation. No markdown fences.")

    return "\n".join(lines)


def _build_signature_line(spec: PrescriptiveSpec) -> str:
    """Build `def name(params) -> ret:` from spec interface."""
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key
    params: list[str] = []
    for p in spec.parameters:
        name = p.get("name", "arg")
        ptype = p.get("type", "")
        params.append(f"{name}: {ptype}" if ptype else name)
    ret = f" -> {spec.return_type}" if spec.return_type else ""
    return f"def {func_name}({', '.join(params)}){ret}:"


# ── Body Normalization ───────────────────────────────────────────────


def _normalize_body(raw: str) -> str:
    """Normalize a proposed body from the caller.

    Strips markdown fences, def lines, ensures 4-space indent.
    """
    text = raw.strip()

    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            code = parts[1]
            first_nl = code.find("\n")
            if first_nl != -1 and first_nl < 20:
                code = code[first_nl + 1:]
            text = code.strip()

    lines = text.split("\n")

    # Remove def line if the caller included it
    if lines and lines[0].strip().startswith("def "):
        lines = lines[1:]

    # Ensure 4-space indent
    cleaned: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            cleaned.append("")
        elif not stripped.startswith("    "):
            cleaned.append("    " + stripped.lstrip())
        else:
            cleaned.append(stripped)

    return "\n".join(cleaned).rstrip()


# ── Verification ─────────────────────────────────────────────────────


def _verify_body(
    spec: PrescriptiveSpec,
    body: str,
    project_root: str,
    witnesses: list[WitnessRecord],
    config: ResolveConfig,
) -> dict[str, Any]:
    """Multi-layer verification. Returns {passed: bool, checks: [...]}."""
    checks: list[dict[str, Any]] = []
    all_passed = True
    full_func = _build_full_func(spec, body)

    # Layer 1: AST parse
    try:
        ast.parse(full_func)
        checks.append({"check": "ast_parse", "passed": True})
    except SyntaxError as e:
        checks.append({"check": "ast_parse", "passed": False, "error": str(e)})
        return {"passed": False, "checks": checks}

    # Layer 2: Structural checks
    for sc in _structural_checks(full_func, spec):
        checks.append(sc)
        if not sc["passed"]:
            all_passed = False

    # Layer 3: Witness execution
    oracle_witnesses = [w for w in witnesses if w.has_oracle_value and w.output]
    if oracle_witnesses:
        wc = _run_witnesses(spec, body, oracle_witnesses, project_root)
        checks.append(wc)
        if not wc["passed"]:
            all_passed = False

    # Layer 4: Pytest (optional, expensive)
    if config.verify_with_pytest and all_passed:
        pc = _run_pytest(spec, project_root)
        checks.append(pc)
        if not pc["passed"]:
            all_passed = False

    return {"passed": all_passed, "checks": checks}


def _build_full_func(spec: PrescriptiveSpec, body: str) -> str:
    """Assemble `def ...: body` for AST parsing."""
    return f"{_build_signature_line(spec)}\n{body}"


def _structural_checks(
    full_func: str, spec: PrescriptiveSpec,
) -> list[dict[str, Any]]:
    """AST structural checks on the assembled function."""
    checks: list[dict[str, Any]] = []
    try:
        tree = ast.parse(full_func)
    except SyntaxError:
        return [{"check": "structure", "passed": False, "error": "parse failed"}]

    func_def = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)), None,
    )
    if func_def is None:
        return [{"check": "structure", "passed": False, "error": "no function found"}]

    # Return statement for non-None return types
    if spec.return_type and spec.return_type != "None":
        has_return = any(isinstance(n, ast.Return) for n in ast.walk(func_def))
        checks.append({
            "check": "has_return",
            "passed": has_return,
            "error": "" if has_return else "missing return statement",
        })

    # Purity: no global/nonlocal
    if spec.problem_class == "pure":
        has_global = any(
            isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(func_def)
        )
        checks.append({
            "check": "no_global",
            "passed": not has_global,
            "error": "" if not has_global else "pure function uses global/nonlocal",
        })

    # Purity: no side-effect calls
    if spec.problem_class == "pure" and not spec.allowed_side_effects:
        _SIDE_EFFECT_NAMES = {"print", "logging", "open", "write"}
        has_side = False
        for node in ast.walk(func_def):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _SIDE_EFFECT_NAMES:
                    has_side = True
                    break
        checks.append({
            "check": "no_side_effects",
            "passed": not has_side,
            "error": "" if not has_side else "pure function has side-effect calls",
        })

    return checks


def _run_witnesses(
    spec: PrescriptiveSpec,
    body: str,
    witnesses: list[WitnessRecord],
    project_root: str,
) -> dict[str, Any]:
    """Execute body against witness I/O pairs in a subprocess."""
    func_code = _build_full_func(spec, body)
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key

    for witness in witnesses:
        args_str = ", ".join(f"{k}={v}" for k, v in witness.inputs.items())
        import_lines = "\n".join(witness.imports) if witness.imports else ""
        script = (
            f"{import_lines}\n"
            f"{func_code}\n"
            f"result = {func_name}({args_str})\n"
            f"expected = {witness.output}\n"
            f"assert result == expected, f'got {{result!r}}, expected {{expected!r}}'"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=project_root,
            )
            if proc.returncode != 0:
                err = (proc.stderr or "assertion failed").strip()
                return {
                    "check": "witness",
                    "passed": False,
                    "error": err[-200:],
                }
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"check": "witness", "passed": False, "error": str(e)}

    return {"check": "witness", "passed": True}


def _run_pytest(spec: PrescriptiveSpec, project_root: str) -> dict[str, Any]:
    """Run pytest on generated test file for this target."""
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key
    test_path = os.path.join(
        project_root, "tests", "generated", f"test_prescriptive_{func_name}.py",
    )
    if not os.path.isfile(test_path):
        return {"check": "pytest", "passed": True, "note": "no test file"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-q", "--tb=line", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        return {
            "check": "pytest",
            "passed": proc.returncode == 0,
            "error": proc.stdout[-200:] if proc.returncode != 0 else "",
        }
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"check": "pytest", "passed": False, "error": str(e)}


# ── Failure → Constraint Extraction ──────────────────────────────────


def _failures_to_constraints(verification: dict[str, Any]) -> list[str]:
    """Convert verification failures into retry constraints for the caller."""
    constraints: list[str] = []
    for check in verification.get("checks", []):
        if check.get("passed", True):
            continue
        name = check.get("check", "")
        error = check.get("error", "")
        _MESSAGES = {
            "ast_parse": f"Syntax error: {error}",
            "has_return": "MUST include a return statement",
            "no_global": "MUST NOT use global or nonlocal",
            "no_side_effects": "MUST NOT call print/logging/open/write",
            "witness": f"Wrong output: {error}",
            "pytest": f"Tests failed: {error}",
        }
        msg = _MESSAGES.get(name, f"{name}: {error}")
        constraints.append(msg)
    return constraints


# ── Scoring ──────────────────────────────────────────────────────────


def _score_confidence(verification: dict[str, Any]) -> float:
    """Compute confidence from verification check results."""
    checks = verification.get("checks", [])
    if not checks:
        return 0.5
    passed = sum(1 for c in checks if c.get("passed"))
    total = len(checks)
    base = passed / total if total else 0.5
    if any(c.get("check") == "witness" and c.get("passed") for c in checks):
        base = min(base + 0.15, 1.0)
    return round(base, 3)


# ── Accept + Persist ─────────────────────────────────────────────────


def _accept(
    spec: PrescriptiveSpec,
    result: ResolveResult,
    project_root: str,
    cache_key: str,
    config: ResolveConfig,
) -> None:
    """Cache the resolution and optionally write to the resolved directory."""
    put_cached(
        project_root,
        cache_key,
        CachedResolution(
            body=result.body,
            method=result.method,
            confidence=result.confidence,
            tokens_used=0,
            verification=result.verification,
        ),
    )
    if config.write_to_file:
        result.resolved_path = _write_resolved(spec, result.body, project_root) or ""


def _write_resolved(
    spec: PrescriptiveSpec,
    body: str,
    project_root: str,
) -> str | None:
    """Write the resolved function to .lintgate/resolved/{name}.py."""
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key

    doc_lines: list[str] = []
    if spec.return_description:
        doc_lines.append(spec.return_description)
    for inv in spec.invariants[:3]:
        doc_lines.append(f"Invariant: {inv.description}")
    docstring = ""
    if doc_lines:
        doc_content = "\n    ".join(doc_lines)
        docstring = f'    """{doc_content}"""\n'

    full_func = f"{_build_signature_line(spec)}\n{docstring}{body}\n"

    resolved_dir = os.path.join(project_root, ".lintgate", "resolved")
    os.makedirs(resolved_dir, exist_ok=True)
    out_path = os.path.join(resolved_dir, f"{func_name}.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_func)
    return out_path


# ── Helpers ──────────────────────────────────────────────────────────


def _elapsed(t0_ns: int) -> int:
    """Nanosecond start → millisecond delta."""
    return int((time.monotonic_ns() - t0_ns) / 1_000_000)
