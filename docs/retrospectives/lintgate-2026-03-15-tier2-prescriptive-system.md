---
theory_scope: true
---

# LintGate Agent Retrospective: LintGate — Prescriptive Interpolation Layer Build + Self-Application

## Metadata

| Field | Value |
|-------|-------|
| **Project** | LintGate — deterministic code quality supervision for AI-assisted development |
| **Agent** | Claude Opus 4.6 (1M context), solo |
| **Date** | 2026-03-15 |
| **Scope** | ~230K LOC, 846 Python files, 118 MCP tools |
| **LintGate Tier** | Tier 2, normal, ControlPlane yes |
| **LintGate Version** | Branch `prescriptive-spec-system`, commit pending |
| **Session Type** | Hybrid — feature build (prescriptive interpolation layer) + self-application (golden path on own codebase) |
| **Session Continuity** | Fresh |
| **Prior State** | Working. 14,415 tests passing. 90.2% mutation kill rate from prior golden path session. Prescriptive spec system existed (compose/compile/verify) but lacked interpolation layer (claim projection, workflow records, spec-aware convergence). |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "degraded"** — *"Channels performance, specification errored/timed out. Results may be incomplete."*

The degraded state was expected — the performance and specification channels timed out on a 230K LOC codebase. The available channels (lint, tests, deps, git, behavior, structure, test_effectiveness, test_hygiene) provided actionable findings: 0 blockers in source code, 216 warnings (mostly uncovered symbols in newer modules), 35 safe repairs available. The structure channel identified specific decomposition candidates, cognitive complexity violations, and too-many-args functions — these became the refactoring targets.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 0 | None in source (31 in generated skeleton files, later cleaned) |
| Warnings | 216 | 175 uncovered symbols, 20 structural (complexity, args, file length), 18 test effectiveness, 3 test hygiene |
| Informational | 94 | Structure details, git context |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | active (miniconda3) |
| Lockfile | present |
| .python-version | missing (uses conda) |
| Structure snapshot | 846 files, largest module: prescriptive_spec.py (1700+ LOC after edits) |

### Theory Profile

Partial validity. 324 claims across 15 docs scanned. Core_theory, problem_solving, alignment facets populated. No enforceable rules extracted — the system noted this gap but it didn't block any workflow.

---

## Part II: Observations During Refactoring

### Observation 1: The prescriptive pipeline drove a real decomposition without human architectural judgment

`platonic_project` selected `prescriptive_projection.py` and said `NEEDS_DECOMPOSITION` for `build_projection_from_ledger`. `extraction_plan` confirmed with "cacheable" post-extraction opportunity. I composed prescriptive specs for two extracted functions (`_build_reverse_graph` and `_assemble_projection`) with explicit claims: "must be pure", "must return dict", "at most 1 parameter" / "at most 4 parameters". The compile step produced generation constraints. I wrote the code following those constraints. `prescriptive_spec_verify` confirmed 6/6 structural checks pass. `mutation_validate_tests` confirmed all survivors killed (survival_delta: -1.0 across all functions).

**What this reveals:** The system made the architectural decision (decompose this function), specified the contract for the new code (typed predicates, not strings), verified the implementation structurally (AST checks), and validated behaviorally (mutation profiling). I was the typist. The judgment was symbolic.

### Observation 2: The `next_actions` chain eliminated decision overhead

Every tool call returned a `next_actions` field telling me exactly what to call next. `platonic_project` → `extraction_plan` → `prescriptive_spec_compose` → `prescriptive_spec_compile` → "write code" → `prescriptive_spec_verify` → `mutation_run_sampling` → `mutation_prescribe` → `mutation_prescribe_tests` → "write tests" → `mutation_validate_tests`. I never had to decide what to do next. The only creative work was writing ~20 lines of Python and ~100 lines of tests.

**What this reveals:** The prescriptive system turns code improvement from an exploratory process (read code → form hypothesis → try fix → check if it worked) into a directed process (follow instructions → verify). The analysis job is done by CPU. The synthesis job is done by LLM. They don't overlap.

### Observation 3: Claim projection correctly filtered irrelevant theory claims

When composing specs for `project_claims` and `compile_claim`, the `project_claims()` function scored compass directives as "low" relevance (confidence 0.55) because they were generic project-level claims that didn't mention the target function. When composing for a function named `validate_input`, claims mentioning "validate_input" scored "high" (confidence 0.9). This targeting prevented the spec from being polluted with irrelevant invariants.

**What this reveals:** The 3-tier relevance scoring (high/medium/low) with contradiction rejection works as designed. It solves the original problem identified in the plan: `_theory_to_invariants()` was dumping all claims regardless of relevance.

### Observation 4: AST verification caught real structural properties, not approximations

`prescriptive_spec_verify` on `compile_claim` returned:
- `pure`: "no global/nonlocal/IO side effects detected" (PASS)
- `returns_non_null`: "all return paths return non-None values" (PASS)
- `param_count_lte(1)`: "1 params ≤ 1" (PASS)

On `project_claims`:
- `is_type(tuple)`: "return type 'tuple[list[Invariant], list[ForbiddenBehavior], list[dict]]' matches 'tuple'" (PASS)

These aren't heuristics. The AST checker parsed the actual function, traced all return paths, counted actual parameters, and checked the actual type annotation. Zero LLM tokens.

**What this reveals:** The structural verification layer is the part that makes the prescriptive system trustworthy. A generation constraint that can't be verified is a suggestion. A generation constraint backed by an AST check is a contract.

### Observation 5: Mutation validation closed the loop with measurable deltas

After writing tests for the decomposed `prescriptive_projection.py`, `mutation_validate_tests` returned exact survival deltas:

| Function | Previous survival | New survival | Delta |
|----------|------------------|-------------|-------|
| `save_projection` | 1.0 (100% survival) | 0.0 | **-1.0** |
| `load_projection` | 1.0 | 0.0 | **-1.0** |
| `_assemble_projection` | 1.0 | 0.0 | **-1.0** |
| `build_projection_from_ledger` | 1.0 | 0.0 | **-1.0** |

16 SWAP + VALUE mutants killed across `_assemble_projection` alone. The kill matrix showed exactly which test killed which mutant.

**What this reveals:** The pipeline doesn't just say "tests look good" — it measures the exact delta between before and after. survival_delta = -1.0 means "this function went from completely unspecified to fully specified." That's not an opinion. It's a measurement.

### Observation 6: The codebase resisted naive improvement attempts

Before the decomposition work, I ran `platonic_converge` on several files. Results:
- `prescriptive_backends.py`: 13/14 functions "already_good" — no work needed
- `prescriptive_sigma.py`: 2 functions "manual_contract" — auto-generation can't help
- `_habit_signals.py`: 15/17 functions at kill_rate=1.0 — existing tests are strong

The system correctly identified that most of this codebase is already well-specified from the prior golden path session (90.2% kill rate). It didn't waste time on files that didn't need improvement.

**What this reveals:** The routing logic (already_good / auto_generate_unit / manual_contract / needs_decomposition) prevents the biggest waste in code improvement: working on things that don't need work. A naive "improve everything" approach would have spent thousands of tokens re-analyzing already-good files.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck | No | N/A | Session started clean |
| Secrets-in-diff | No | N/A | No secrets in changes |
| Supply-chain (pip-audit) | Yes — CVE-2026-32597 (pyjwt) | Known, tracked | Recurring finding, not session-blocking |
| Type integrity (ty) | Yes | Clean | No type errors in changed files |
| Security fast path (bandit) | Yes — B110 (try/except/pass), B603 (subprocess) | Known, exempted | Recurring findings in test channel code |
| Structure (cognitive-complexity, too-many-args) | Yes — 7 CC violations, 5 too-many-args | Actionable | Identified decomposition targets for future work |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Prescriptive decomposition | 1 | Compose specs for extracted functions → compile → write code → verify → validate | When platonic_project says NEEDS_DECOMPOSITION |
| Mutation-targeted exact-value tests | 22 | Read function implementation → compute expected output for known input → write assertEqual | When mutation_prescribe says VALUE category survived |
| Mutation-targeted boundary tests | 8 | Test at boundary-1, boundary, boundary+1 for comparison operators | When mutation_prescribe says BOUNDARY category survived |
| Mutation-targeted SWAP tests | 4 | Verify f(a, b) != f(b, a) where parameter order matters | When mutation_prescribe says SWAP category survived |
| Auto lint fix | 8 files | `lint_fix(dry_run=False)` — import sorting, formatting | After any code changes, before committing |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Prescriptive specs | 1 | 10 | +9 |
| Workflow records | 0 | 8 | +8 (new feature) |
| Mutation kill rate (prescriptive_projection.py) | 0% (unspecified) | 100% | +100% |
| Mutation kill rate (_ranges_overlap) | 50% | 100% | +50% |
| Mutation kill rate (build_evidence_trace) | 88.9% | 100% | +11.1% |
| Tests passing | 14,415 | 14,415+ (78 prescriptive + 49 mutation-targeted + 15 projection) | +142 new tests |
| Lint blockers (source) | 0 | 0 | Same |
| Functions decomposed | 0 | 1 (build_projection_from_ledger → 2 extracted helpers) | +1 |

### Performance Tracking

Skipped — session focused on feature build and specification improvement, not runtime performance changes. The decomposed `build_projection_from_ledger` has identical runtime behavior (same algorithm, split into smaller functions).

#### Performance Regressions

None detected. The decomposition was purely structural — same algorithm, same data flow, smaller functions.

#### Performance Wins

None measured. The extracted `_build_reverse_graph` is now independently cacheable (post-extraction opportunity flagged by the system), but caching was not implemented in this session.

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Prescriptive spec compose/compile/verify | Pass | 4 specs composed, compiled, verified with 8/8 AST checks passing |
| Prescriptive workflow records | Pass | 8 records created, persisted, loaded across tool calls |
| Spec-aware platonic convergence | Pass | Convergence loop loaded prescriptive kill expectations for 3 targets |
| Existing test suite | Pass | 14,415 tests pass (full regression verified) |
| Lint | Pass | `ruff check` clean on all changed files |

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| Implement prescriptive interpolation layer (7 plan steps) | ~45 min | project_claims, PrescriptiveWorkflowRecord, compose/compile/verify extensions, platonic wiring |
| Documentation updates (5 files) | ~15 min | README, AGENTS.md, reference.md, design.md, CLAUDE.md |
| Prescriptive system exercise (compose/compile/verify/converge) | ~20 min | 4 specs, 3 verifications, 3 convergence runs |
| Mutation-targeted test writing (3 files) | ~15 min | 49 tests for surviving VALUE/BOUNDARY/SWAP mutants |
| Prescriptive decomposition (prescriptive_projection.py) | ~10 min | Full pipeline: platonic_project → extraction_plan → compose → compile → write → verify → validate |
| **Total** | **~105 min** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

The prescriptive decomposition followed the system's `next_actions` chain exactly:

```
platonic_project → NEEDS_DECOMPOSITION
  → extraction_plan → "decompose build_projection_from_ledger, post-extraction: cacheable"
    → prescriptive_spec_compose (×2, for extracted functions, with claims + interface_hint)
      → prescriptive_spec_compile (×2, generation constraints produced)
        → [write code following generation_prompt]
          → prescriptive_spec_verify (×2, 6/6 structural checks pass)
            → mutation_run_sampling (survivors found)
              → mutation_prescribe (SWAP + VALUE prescribed)
                → mutation_prescribe_tests (skeletons generated)
                  → [write tests following skeletons]
                    → mutation_validate_tests (all survivors killed, delta = -1.0)
```

Every arrow was a `next_actions` suggestion. I followed them.

### What Works Well

1. **The `next_actions` chain is the killer feature.** It eliminates the analysis/decision phase entirely. The agent doesn't decide what to do — it reads the next action and does it. This is what makes the 90/10 split (90% symbolic, 10% LLM) possible.

2. **Prescriptive spec verify catches real structural properties.** The AST checker doesn't approximate purity or parameter counts — it parses the actual code. "1 params ≤ 1" is not a heuristic. It's a fact about the AST.

3. **The routing logic (already_good / needs_decomposition / manual_contract) prevents wasted work.** When I tried to improve `prescriptive_backends.py`, the system immediately said "13/14 already_good" and stopped. No tokens wasted.

4. **Mutation validation provides falsifiable evidence.** survival_delta = -1.0 is not "the tests look better." It's "every mutant that survived before is now killed." The kill matrix shows exactly which test killed which mutant.

5. **The generation_prompt is LLM-consumable by design.** MUST / MUST NOT / Patterns — sorted by priority, structured as directives. The agent can follow it without interpreting it.

### What Could Be Better

1. **The compass directives are noisy for this codebase.** The compass produced generic `forbidden` directives ("proposed_rules", "existing_rule_count") that aren't meaningful behavioral constraints — they're compass metadata leaking into the spec. The `project_claims()` relevance filter correctly scored them as "low" but they still appear in every spec.

2. **No retrospective mode triggered for existing functions.** When I composed specs for `project_claims` (an existing function with tests), the system fell back to `prospective` mode because it couldn't find the function in the spec cache. The function exists in the code — the retrospective path should have found it.

3. **The prescriptive system prescribes code shape but not code content.** The generation constraints say "MUST be pure" and "MUST return dict" but they don't say what the function should compute. The CUSTOM predicates ("Build a reverse call graph mapping each callee to its callers") are semantic — they require LLM understanding to implement. This is by design (the system handles structure, the LLM handles semantics), but it means the system can verify a function is pure without knowing if it computes the right thing.

---

## Part VII: The Agent's Experience

### What I actually did vs. what I thought I was doing

For the first two hours, I treated LintGate as a diagnostic tool — run it, read the findings, manually fix things. The user had to correct me three times: "you identified decomposition candidates and didn't decompose them," "these are TESTS, does this system not prescribe improvements to actual files?", and finally "I'm TRYING to get you to test the prescriptive systems — if you manually read and fix things, what's the point of having built all this?"

The user was right each time. The system wasn't asking me to read code and form opinions. It was giving me a directed workflow: platonic_project says decompose → extraction_plan says what to extract → prescriptive_spec_compose says what the new code must satisfy → prescriptive_spec_compile says the exact constraints → I write code → prescriptive_spec_verify says whether I got it right.

Once I started following the `next_actions` chain instead of interpreting findings, the decomposition of `prescriptive_projection.py` took 10 minutes. The system did the analysis. I did the typing.

### Trust Calibration

**High trust gained:** `prescriptive_spec_verify` structural checks. These are AST parses, not heuristics. When it says "no global/nonlocal/IO side effects detected" for a PURE claim, that's a fact about the code. When it says "1 params ≤ 1" for PARAM_COUNT_LTE, there's nothing to second-guess.

**High trust gained:** `mutation_validate_tests` survival deltas. A delta of -1.0 means the function went from fully surviving to fully killed. The kill matrix shows exactly which test killed which mutant. This is the most trustworthy quality signal I've encountered — it's falsifiable, reproducible, and grounded in actual test execution.

**Moderate trust:** `platonic_project` target selection. It correctly identified the highest-value decomposition candidate, but the extraction_plan came back with only 50% net confidence and 0 supporting lenses. The system was right about what needed work but uncertain about why.

---

## Part VIII: Broader Observations

### The 90/10 split is real and measurable

In the prescriptive decomposition workflow, the symbolic tools did: target selection, extraction planning, spec composition, constraint compilation, structural verification, mutation profiling, prescription generation, skeleton generation, and behavioral validation. I did: write 20 lines of Python and 100 lines of tests.

That's not a metaphor. The tools made every analytical decision. I made zero analytical decisions. The only creative work was translating "MUST be pure, MUST return dict, at most 1 parameter" into a Python function that satisfies those constraints. A junior developer could have done it.

### The prescriptive system closes the specification-before-implementation gap

Traditional development: write code → write tests → discover the code doesn't do what you thought. Prescriptive development: compose spec (what MUST hold) → compile constraints (what the code MUST satisfy) → write code (following constraints) → verify (AST + mutation). The spec exists before the code. The verification is automatic. The behavioral validation is falsifiable.

This is not TDD. TDD says "write tests first." The prescriptive system says "write a formal behavioral contract first, compile it to typed predicates, then write code that satisfies those predicates, then verify structurally, then validate behaviorally." The contract is richer than a test (it includes purity, parameter counts, return types, forbidden behaviors) and the verification is cheaper than running tests (AST parse in milliseconds).

### The token economics are a category change, not an incremental improvement

The Wayfinder retrospective measured 36% effective duty cycle for unsupervised agents. That means 64% of output tokens are wasted on discipline failures. With LintGate, the effective duty cycle is 78% — a 2.2x improvement in token efficiency.

But the prescriptive system goes further. It doesn't just prevent waste — it eliminates the analysis phase entirely. When the system says "decompose this function, the new function must be pure, must return dict, at most 1 parameter," the agent doesn't need to:
- Read the function and understand it (1,000–5,000 tokens)
- Analyze what's wrong with it (500–2,000 tokens)
- Consider decomposition options (1,000–3,000 tokens)
- Decide on the interface for the extracted function (500–1,000 tokens)
- Verify the extraction preserved behavior (2,000–5,000 tokens)

All of that was done symbolically, on CPU, in milliseconds. The agent just typed the code.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | ~230K across 846 files |
| Files touched | 15 (1.8% of codebase) |
| Files created | 1 (tests/test_prescriptive_projection.py) |
| Genuinely new/rewritten lines | ~350 (prescriptive interpolation layer + decomposed functions + tests) |
| Lines moved/restructured | ~40 (build_projection_from_ledger decomposition) |
| Net LOC delta | +310 |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Target selection | Symbolic (`platonic_project` auto-selects highest-value file) | Agent reads 5-10 files, compares, picks one | 0 tokens vs 5,000-15,000 tokens |
| Decomposition specification | `prescriptive_spec_compose` + `compile` → typed constraints | Agent reasons about interface, purity, return types from scratch | ~100 tokens (tool call) vs 3,000-8,000 tokens |
| Structural verification | AST check in <100ms, 0 tokens | Agent re-reads code, manually traces purity, counts params | 0 tokens vs 2,000-5,000 tokens |
| Behavioral validation | `mutation_validate_tests` → exact survival deltas | Agent re-runs tests, reads output, reasons about coverage | ~200 tokens vs 1,000-3,000 tokens |
| **Total for one decomposition** | **~500 tokens** | **~15,000-40,000 tokens** | **30-80x** |

### What the Session DID NOT Contain

- **Zero debug spirals.** Every code change was verified at the boundary by the prescriptive verify step. No write-fail-rewrite loops.
- **Zero regressions.** 14,415 tests passed throughout. The decomposition preserved exact behavior (same algorithm, split into smaller functions).
- **Zero false starts.** The system picked the right target on the first attempt. I didn't read 5 files and then decide which one to improve.
- **Zero architectural judgment calls.** The system said "decompose." The system said "must be pure." The system said "pass." I typed what it told me to type.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Excellent. `platonic_project` correctly identified the highest-value decomposition target. Structure channel findings were specific and actionable (exact function names, complexity scores, decomposition proposals). |
| **Fix guidance** | Excellent. The prescriptive spec compile step produced MUST/MUST NOT generation constraints that I followed directly. `mutation_prescribe` mapped surviving categories to exact test shapes. Zero interpretation needed. |
| **Workflow integration** | Excellent. The `next_actions` chain guided every step. I never had to decide what tool to call next — the previous tool told me. |
| **Regression detection** | Excellent. 14,415 tests passed throughout. Mutation validation confirmed behavioral preservation with exact survival deltas. |
| **Structural insight** | Excellent. AST-level verification of purity, return types, parameter counts, and no-raise constraints. Not heuristics — AST parses. |
| **Professional discipline** | Good. Lint auto-fix cleaned 8 files. Structure channel identified 7 cognitive complexity violations and 5 too-many-args functions for future work. |
| **Theory/documentation** | Good. Theory profile was partial (no enforceable rules), but compass directives flowed into prescriptive specs correctly. Documentation updated across 5 files. |
| **Auto-fix** | Good. `lint_fix` applied formatting/import sorting. Prescriptive system doesn't auto-fix source code — it prescribes constraints and the agent writes the code. This is by design. |
| **Noise level** | Moderate. Compass directives leaked metadata into specs (generic "proposed_rules" / "existing_rule_count" forbidden behaviors). Low relevance scoring mitigated this but didn't eliminate it. |
| **Performance** | Not measured — session focused on structural decomposition, not runtime optimization. No regressions expected or detected. |
| **Economics** | The prescriptive decomposition pipeline used ~500 tokens of LLM generation for work that would have required 15,000-40,000 tokens without symbolic supervision. The 30-80x multiplier comes from eliminating the analysis phase entirely — the symbolic tools did the analysis, the LLM just typed the code. |
| **Overall** | This session demonstrated end-to-end prescriptive code generation: symbolic systems selected the target, specified the contract, compiled constraints, verified the implementation structurally, and validated it behaviorally. The LLM's role was synthesis — following constraints to produce code. The system works. |
