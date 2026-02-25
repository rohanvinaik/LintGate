# Mutation Testing as Specification Completeness: Theory, Architecture, and Implementation

A theory and implementation document for LintGate's mutation testing subsystem. Covers the
core thesis, its connection to performance engineering, the current implementation state,
identified gaps in the end-to-end chain, and the path to a fully-wired symbolic refactoring
engine.

---

## 1. The Core Thesis

Mutation score is not a test quality metric. It is a **specification completeness** metric,
and specification completeness is the prerequisite for every other form of code quality —
performance engineering, safe refactoring, algebraic optimization, and clean decomposition.

A surviving mutant is a program that is **observationally equivalent** to the original under
the existing test suite (Morris 1968, Plotkin, Abramsky). If no test can distinguish the
mutant from the original, the test suite does not fully specify what the code does. In
Sussman's framing (SICP), programs are "precise specifications of computational processes."
A function whose behavior is underdetermined by its tests is not a precise specification —
it is an ambiguous one.

Ambiguous specifications resist optimization, because optimization requires proving
behavioral equivalence, which is impossible when you don't know what behavioral equivalence
*means* for the function in question.

---

## 2. Why This Matters for Performance Engineering

LintGate's algebra pipeline detects properties that enable classical optimizations:

| Property      | Optimization It Enables                      |
|---------------|----------------------------------------------|
| Purity        | Memoization, CSE, dead code elimination      |
| Commutativity | Reordering for cache locality                |
| Associativity | Parallelization (map-reduce)                 |
| Idempotency   | Caching without invalidation, safe retry     |
| Boundedness   | Overflow-safe, no range checks needed        |
| Monotonicity  | Sorted-output guarantees, binary search eligibility |

But these properties can only be **proven** for functions whose behavior is fully
specified. The purity analysis says "this function has no side effects, it's cacheable."
The mutation score says "but you don't know what it computes, so caching it is
meaningless."

**Purity without specification completeness is a false optimization signal.**

The cross-channel gate enforces this:

```
IF function is pure AND mutation_survival > 60%:
    → confidence = 0.10, [MUTATION GATED]
    → "This function is pure but underspecified.
       Decompose it until mutation score < 20%
       before applying performance optimizations."
```

---

## 3. The Operational Chain

The central claim, which does not appear to be articulated as a unified theory anywhere
in the existing literature:

```
mutation pressure
  → surviving mutants categorized by type
    → specification gap map (which behavioral dimensions are unconstrained)
      → forced decomposition (tangled functions resist full specification)
        → algebraically tractable units (pure, bounded, commutative, etc.)
          → safe optimization hints (cacheable, parallelizable, JIT-eligible)
```

Each step is **symbolic and deterministic** — AST analysis, mutmut execution, category
classification, prescription logic, algebraic property detection. No LLM inference
required. The entire chain runs on local hardware as cheap symbolic computation.

### 3.1 Why Decomposition Is Forced

You cannot fully specify a tangled function because the test surface is combinatorially
large. A function that does conditional dispatch, arithmetic computation, string
formatting, and state management has behavioral dimensions that interact
multiplicatively. Writing tests that pin down every behavioral degree of freedom requires
O(n^k) test cases where n is the number of values per dimension and k is the number of
independent concerns.

Decomposition makes specification tractable by isolating each concern. A pure function
that does only arithmetic has O(n) behavioral dimensions. Each subfuntion can be
independently specified. The act of driving toward zero surviving mutants *is* what
produces clean, optimizable code.

### 3.2 Why This Produces Algebraically Tractable Units

Fully specified functions naturally exhibit algebraic properties because the
decomposition process strips away entanglement. A function isolated to a single concern
tends to be:

- **Pure** (no side effects from other concerns)
- **Bounded** (single domain, predictable range)
- **Monotonic** (simple input→output relationship)
- **Idempotent**, **commutative**, or **associative** when the concern is inherently so

These properties are not imposed — they are *revealed* by decomposition. The mutation
profile is the map that tells you where to cut.

---

## 4. Survival Profiles as Decomposition Prescriptions

The individual profile of the survival pattern is a **symbolic articulation** of not
just the fact that code is ambiguous, but *why* it is ambiguous. Three dominant failure
modes emerge from real mutation data:

### 4.1 Insufficient Test Specificity (~59% of survivors)

Tests check `len(result) == 2` instead of `assert result == expected`. The shape is
specified but not the content.

**Refactoring signal:** Extract the computation that produces the *content* into a
function with its own contract.

### 4.2 Untested Boundary Conditions (~24% of survivors)

Thresholds like `0.4` mutate to `0.41` and survive because test data is `0.45`.

**Refactoring signal:** Extract thresholds into named constants with documented
boundary semantics. Each boundary becomes a testable claim.

### 4.3 Single-Path Coverage in Multi-Path Code (~17% of survivors)

Three-level `if/elif/else` chains where only one path is tested.

**Refactoring signal:** Extract each branch into its own function with its own contract.
Cascading conditionals become dispatch over independently testable units.

### 4.4 The Category→Decomposition Mapping

| Survival Pattern                | Decomposition Axis                                           |
|---------------------------------|--------------------------------------------------------------|
| String matching survivors       | Extract a vocabulary/pattern registry with its own tests     |
| Threshold survivors             | Extract thresholds into named constants with boundary tests  |
| Cascading conditional survivors | Extract each branch into its own function with own contract  |
| Aggregate survivors             | Make aggregation composable (monoids) for independent testing |
| Multi-category survivors (3+)   | Function does too many things — decompose along category axes |

---

## 5. Specification Completeness as a Lattice

For any function, there is a lattice from "no tests" (bottom) to "fully specified" (top,
zero surviving mutations). Each point enables a different class of optimizations:

| Specification Completeness | What You Can Safely Do                                         |
|----------------------------|----------------------------------------------------------------|
| 100% (zero survivors)      | Full algebraic optimization, verified equivalence transforms   |
| ~80% (20% survival)        | Memoization, CSE, most performance optimizations               |
| ~50% (50% survival)        | Purity classification reliable, properties unverified          |
| ~25% (75% survival)        | Can only reason about types and call graph, not values         |
| 0% (no tests)              | Can't safely change anything                                   |

**Mutation score is the missing vertical axis** that tells you where each function sits on
this lattice and therefore which optimizations are actually safe.

---

## 6. Theoretical Foundations

### 6.1 Established Pieces

- **Mutation testing** — DeMillo, Lipton, Sayward 1978. Competent programmer hypothesis,
  coupling effect, equivalent mutant problem (Budd & Angluin 1982).
- **Observational equivalence** — Morris 1968, the denotational semantics tradition.
- **Algebraic specification** — ADJ group (Goguen, Thatcher, Wagner, Wright, 1970s–80s).
- **Property-based testing** — QuickCheck (Claessen & Hughes 2000). Algebraic laws are
  practically testable.
- **Sussman's SICP** — Programs as specifications for computational processes, abstraction
  barriers, the "what" vs "how" distinction.

### 6.2 Novel Synthesis

The operational chain — **mutation pressure → forced decomposition → algebraic properties →
mechanizable optimization** — is not articulated as a unified theory in the literature.
Related traditions each capture part of it:

- Haskell's equational reasoning starts from purity and algebraic laws, not from mutation
  testing as the discovery mechanism.
- Okasaki's Purely Functional Data Structures shows algebraic properties enabling
  optimization — again from the algebraic side, not the mutation side.
- Design by Contract treats contracts as partial specifications but makes no connection
  to mutation testing as a completeness measure.
- Abstract interpretation approximates semantics statically but doesn't produce the
  decomposition pressure mutation testing creates.

### 6.3 The Key Conjecture

Mutation pressure **reliably forces decomposition into algebraically tractable units**.

This is plausible — you can't achieve 100% kill rate on a tangled function without
decomposing it, because tangling is exactly what creates observational equivalence between
the original and mutants. But there is no formal proof, and counterexamples may exist
(functions that are fully specified by tests but not algebraically tractable).

### 6.4 On Perfection and Pragmatism

The conjecture hedges: "no formal proof, and counterexamples may exist." This invites
the objection: *if you can't prove it universally, what's the point?*

But this is the wrong frame. Even linters have rules you sometimes `# noqa`. Nobody
demands pylint be provably correct about every finding before they'll use it. The value
isn't in universal truth — it's in actionable signal at the point of decision.

The specification completeness lattice doesn't need to be a perfect ordering. It needs
to be a *better* ordering than what exists today, which is: nothing. No mainstream tool
connects mutation survival profiles to decomposition prescriptions to optimization
eligibility. The bar is not "formally verified" — the bar is "more useful than flying
blind," and the empirical results (Section 8) clear that bar by a wide margin.

---

## 7. Efficient Execution: The Monty Hall Architecture

Mutation testing is expensive. If it is a first-order signal, hyper-optimizing the process
is essential.

### 7.1 Layer 1: Exclusionary Filtering (The Monty Hall Layer)

Use existing LintGate signals to eliminate irrelevant mutation categories **before
generating a single mutant**:

| Signal Source              | What It Eliminates                                    |
|----------------------------|-------------------------------------------------------|
| Purity analysis            | PATH mutations on branchless pure functions            |
| Assertion classification   | Categories already covered by strong value assertions  |
| Algebraic property manifest| Reorder mutations on commutative ops (equivalent suspects) |
| Complexity analysis        | Don't bother with simple accessors that have strong tests |

### 7.2 Layer 2: Active Hypothesis Testing

From the reduced candidate set, generate a small sample (3–5 mutations per category).
If any survive, the prior was right — confirmed specification gap. If none survive, either
well-specified or prior was wrong. Broader testing only triggers on "prior was wrong."

Budget: 3–5 targeted mutations × surviving categories × candidate functions. For a typical
function where filtering eliminates 4 of 7 categories: 9–15 mutations instead of 50–200.

### 7.3 Layer 3: Execution Optimization

- **Caching:** Same source + same operator = same mutant.
- **Parallelization:** Each mutant is independent.
- **Test-impact selection:** Map functions to covering tests. 15 mutations × 3 tests >>
  15 mutations × full suite.

---

## 8. Two-Tier Progressive Execution

### 8.1 Inline Tier (On Edit, 2–5 Seconds)

Triggered on file edit. Per function:

1. Read existing signals.
2. Compute category priors (Monty Hall filter).
3. Generate 3–5 targeted mutations in high-prior categories.
4. Run against only covering tests.
5. Save with `coverage_depth=SAMPLED`.

### 8.2 Background Tier (Progressive)

Background process works through the full manifest, function by function, ordered by
priority:

1. **No data** — highest priority.
2. **Confirmed gaps** — inline sampling found survivors.
3. **Stale data** — code_hash mismatches.
4. **Everything else** — refresh/confirm.

Larger sample budgets. Incremental state persistence.

---

## 9. Empirical Validation

On 2026-02-25, the pipeline was tested end-to-end against
`lintgate/linters/performance_checks/purity.py` — a 124-line module with 99% line
coverage. mutmut v3 generated 288 mutants.

### 9.1 The Cross-Channel Gate in Action

`_get_parameter_count` — a pure function detected as cacheable by the algebra pipeline:

| Signal | Value | Interpretation |
|---|---|---|
| Purity detector | `is_pure=True, conf=0.80` | No side effects detected |
| Algebra pipeline | `hints=["cacheable"]` | Safe to memoize |
| Mutation testing | `survival_rate=0.75` (12/16 survived) | Tests specify only 25% of behavior |
| **Gate verdict** | `conf=0.10, [MUTATION GATED]` | Purity valid but semantics unverified |

Without the gate: agent sees pure cacheable at 80% confidence, applies `@lru_cache`.
With the gate: you know it has no side effects, but you don't know *what it computes*.
Caching wrong output is worse than recomputing correct output.

### 9.2 Per-Function Specification Profiles

Same file, same test suite, radically different specification completeness:

| Function | Kill Rate | Survival | Lattice Position |
|---|---|---|---|
| `visit_Yield` / `visit_YieldFrom` | 0% | 100% | Bottom — unspecified |
| `visit_Assign` | 7% | 93% | Weakly specified |
| `_get_parameter_count` | 25% | 75% | Partially specified |
| `_check_mutable_defaults` | 33% | 67% | Partially specified |
| `__init__` | 53% | 47% | Moderately specified |
| `analyze_purity` | 55% | 45% | Moderately specified |
| `visit_Call` | 59% | 41% | Moderately specified |

The project-level kill rate (38%) hides all of this. Per-function granularity is what
lets the gate make surgical decisions instead of blunt ones.

### 9.3 Key Observations

1. **99% line coverage ≠ specification completeness.** Lines executing is not behaviors
   being verified.
2. **The gate fires precisely where it should.** `_get_parameter_count` gated from 0.80
   to 0.10 because tests don't verify what it computes.
3. **The specification lattice maps to real data.** The observed kill rates span the
   lattice naturally. No function reaches the 80%+ gate-passing threshold.
4. **mutmut v3's "no tests" category is large** (12.5%). The Monty Hall pre-filter would
   eliminate many of these.

---

## 10. Implementation Status

### 10.1 What's Built and Working

**The algebra pipeline** (bottom of the chain) is solid:

- **Purity detection** (`purity.py`): AST-based, catches global writes, IO calls,
  mutations, impure calls. `PurityResult` with confidence and side-effect evidence.
- **Property classification** (`properties.py`): For pure functions, detects bounded
  (clamp patterns, bool returns), monotonic (single-expression + monotonic ops),
  idempotent (type casts, abs), commutative/associative (+, *, &, |, ^). All AST heuristics.
- **Optimization hints**: Pure → "cacheable". Idempotent → "safe-to-retry",
  "cache-without-invalidation". Associative → "parallelizable", "map-reduce-compatible".
  Bounded → "overflow-safe".

**The cross-channel gate** is wired and real. In `classify_properties()`:
- Survival > 60% → confidence capped at 0.10, `[MUTATION GATED]`
- Survival 30-60% → confidence halved, `[MUTATION PENALIZED]`
- Survival < 30% → confidence boosted to 0.90, `[MUTATION VERIFIED]`

**The mutation engine** executes and parses. Two-tier model works: inline sampling with
budget constraints, background profiling with test-impact selection. Category extraction
via mutmut+libcst (preferred) with AST fallback. Results parse into
`FunctionMutationState` with `survived_by_category`.

**The prescription engine** is deterministic. `PrescriptionEngine.diagnose()` maps
survival profiles to recommendations: >50% across 3+ categories → `DECOMPOSE_FUNCTION`.
Single category dominance → `ADD_TEST_CASE` or `ADD_BOUNDS_CHECK`.

**The decomposition detector** works. `DecompositionDetector.get_candidates()` uses
50% survival + 3+ categories to flag entangled functions. `MUTCH007` surfaces this
through ControlPlane.

### 10.2 Modules and Tools

| Module              | Purpose                                  | Status         |
|---------------------|------------------------------------------|----------------|
| `engine.py`         | Two-tier execution (inline/background)   | Operational    |
| `state.py`          | Per-function persistent state            | Operational    |
| `policy.py`         | Operator relevance, runtime budgets      | Operational    |
| `prescriptions.py`  | Deterministic diagnosis + prescriptions  | Operational    |
| `decomposition.py`  | Entanglement detection                   | Operational    |
| `ci_stats.py`       | CI result parsing, hotspot loading       | Operational    |
| `telemetry.py`      | A/B validation targets                   | Operational    |
| `automation.py`     | Background orchestration scaffolding     | Partial        |

MCP tools:

- `mutation_run_sampling` — Tier 1 inline sampling.
- `mutation_run_full` — Tier 2 background profiling.
- `mutation_get_state` — View per-function state.
- `mutation_clear_state` — Reset state for re-profiling.
- `mutation_prescribe` — Deterministic prescriptions from profiles.
- `mutation_decompose` — Identify entangled decomposition candidates.
- `mutation_refactor_loop` — Re-profile + delta measurement after changes.
- `analyze_test_strength` — Assertion quality + mutation context.
- `inspect_test_assertions` — Per-assertion classification.

### 10.3 The Connective Tissue Gaps

The skeleton of the chain is built. Every major component exists and works individually.
What's missing is the **connective tissue** between components that would allow the chain
to operate end-to-end without human intervention at the boundaries.

**Gap A: Location-unaware survivor profiles.**
`FunctionMutationState.survived_by_category` is `dict[str, int]` — aggregate counts per
category (e.g., `{"conditional": 4, "arithmetic": 2}`). To prescribe specific
decompositions, we need to know *where* in the function the conditional survivors cluster
vs. the arithmetic survivors. The `survivor_sites` field (line numbers, operator details)
described in #102's target schema does not exist yet.

Without location data, the system can say "decompose this function" but not "extract
lines 15-30 (the arithmetic core) into a pure helper and lines 31-50 (the conditional
dispatch) into a separate function."

**Gap B: Prescription→decomposition axis bridge.**
The decomposition detector says THAT a function should be decomposed (MUTCH007), and the
prescription engine says what category of work to do (ADD_TEST_CASE,
DECOMPOSE_FUNCTION). But neither maps surviving categories to specific decomposition axes
within the function. The theory says "the *category* of surviving mutants tells you *why*
the function is underspecified and therefore *where* to cut." The implementation says
"3+ categories survive, so decompose" — it knows to cut but not where.

**Gap C: Prescription→test-skeleton bridge.**
`mutation_prescribe` produces prescriptions. `controlplane_test_skeleton` produces test
stubs. No adapter converts a prescription saying "add boundary check for conditional
category" into a test skeleton specifically targeting that category. The two systems exist
side by side without a wire between them.

**Gap D: Monty Hall filter partially stubbed.**
The engine's `_collect_file_categories()` computes relevant categories from the algebra
manifest (purity, structural properties). The `covered_categories` input from
test-effectiveness is stubbed (line 298 in engine.py: `pass`). The filter works from
the "what's relevant to this function" side but not from the "what's already
well-tested" side, meaning it can't subtract categories with strong existing assertions.

**Gap E: Background orchestration is manual.**
`mutation_refactor_loop` exists as an MCP tool but each step (profile → prescribe →
act → re-profile → measure delta) requires explicit tool invocation. There is no
orchestrated loop that runs the full cycle autonomously.

**Gap F: No inline-on-edit trigger.**
The MCP tools exist for manual invocation. Automatic inline sampling on file
modification is not wired. The `automation.py` module has a global orchestrator
scaffold but no hook integration.

### 10.4 What This Means in Practice

For any function today, the system can:

1. ✅ Detect purity and classify algebraic properties
2. ✅ Run mutation testing and produce per-function survival rates with category breakdown
3. ✅ Gate optimization hints based on mutation survival
4. ✅ Fire MUTCH007 when a function has high multi-category survival
5. ✅ Produce a prescription saying "decompose" or "add tests"
6. ❌ Tell you *which lines* to extract along *which axis*
7. ❌ Generate a test skeleton targeting the specific surviving category
8. ❌ Automatically re-verify after decomposition to confirm tractability improvement

The system is approximately **60% of the way** to the fully autonomous chain. The
remaining 40% is the connective tissue described in Gaps A–F.

---

## 11. The Path to End-to-End: Closing the Connective Tissue

The work to complete the chain, ordered by dependency and payoff:

### Phase 1: Location-Aware Survivor Profiles (Closes Gap A)

Extend `FunctionMutationState` with:

```python
@dataclass
class SurvivorSite:
    line: int
    operator: str        # e.g., "+" → "-"
    category: str        # "arithmetic", "conditional", etc.
    status: str          # "survived", "killed", "timeout"
    mutant_id: str       # For traceability back to mutmut output

survived_sites: list[SurvivorSite] = field(default_factory=list)
```

Modify `_parse_mutmut_results()` to extract per-mutant line numbers from mutmut's cache
or structured output. The AST-based category mapper already knows which node generates
which mutant — extending it to preserve line numbers is a natural increment.

### Phase 2: Category→Axis Decomposition Mapping (Closes Gap B)

With location-aware survivor data, cluster surviving mutants spatially within each
function:

1. Group survivors by category.
2. For each category, compute the line-range span (contiguous regions).
3. Identify category-dominated regions: ranges where >70% of survivors share a category.
4. These regions are the decomposition axes — extractable into standalone functions.

Output: a `DecompositionPlan` with named axes, line ranges, and dominant categories.
This is deterministic and AST-based — no LLM needed.

### Phase 3: Prescription→Skeleton Bridge (Closes Gap C)

Wire `mutation_prescribe` output to `controlplane_test_skeleton`:

- A prescription saying `ADD_TEST_CASE` for category `conditional` generates test stubs
  that exercise each branch outcome of the function.
- A prescription saying `ADD_BOUNDS_CHECK` for category `arithmetic` generates property-
  based test stubs (Hypothesis strategies) targeting numeric edge cases.
- A prescription saying `DECOMPOSE_FUNCTION` generates test stubs for each proposed
  subfuction from the decomposition plan.

The adapter maps `(prescription_category, survivor_category)` → test template selection.

### Phase 4: Complete the Monty Hall Filter (Closes Gap D)

Wire `TestEffectivenessManifest` assertion classifications into the engine's
`_compute_relevant_categories()`:

- For each function, query which assertion categories already have strong coverage.
- Subtract those from the relevant mutation categories.
- This avoids generating mutations in dimensions that are already well-specified.

### Phase 5: Automated Re-Verification Loop (Closes Gap E)

Extend `mutation_refactor_loop` to orchestrate the full cycle:

```
profile(function) → prescribe(profile) → [agent acts on prescription]
  → re-profile(function) → compute_delta(before, after) → report
```

The loop should be invocable as a single MCP tool call that handles the before/after
measurement automatically. The "[agent acts on prescription]" step remains external
(the agent writes the code), but everything else is symbolic.

### Phase 6: Inline-on-Edit Trigger (Closes Gap F)

Wire the mutation engine into the PostToolUse hook pipeline:

- On file modification events, debounce (30s cooldown per file).
- Run inline sampling for changed functions.
- Save with `coverage_depth=SAMPLED`.
- Surface stale/degraded findings immediately in the next ControlPlane report.

### Phase 7: Threshold Calibration + Confidence Model (Future)

Replace static gate thresholds with calibrated policy from observed data:

- Collect per-repository survival distributions over time.
- Compute repository-specific warning/blocking thresholds relative to the observed mean
  (the existing `CalibratedPolicy` in `policy.py` has the skeleton but uses simple
  average-based heuristics).
- Incorporate **equivalent-mutant uncertainty**: survival rates >80% may reflect
  equivalent mutants (mutations producing semantically identical code), not specification
  gaps. Confidence should be penalized when survival is extremely high.
- Emit calibration metadata in diagnostics so gate decisions are reproducible and auditable.
- Acceptance: gate decision reproducibility and false-positive/false-negative rates are
  measurable and within defined bounds.

### Signal Quality Tiering (Design Consideration)

The current `CoverageDepth` has three levels: `none | sampled | profiled`. A finer model
would split sampled into two based on confidence:

```
none → sampled_low → sampled_high → profiled
```

This enables a three-tier gate policy:

| Signal Quality    | Gate Behavior |
|-------------------|---------------|
| `profiled`        | Authoritative — gate verdict is final |
| `sampled_high`    | Near-authoritative — trusted, but full profiling recommended |
| `sampled_low`     | Advisory — directional signal only, do not gate on it |

This prevents low-confidence samples from blocking optimization hints while allowing
high-confidence samples to serve as near-authoritative signals.

### Acceptance Metrics Taxonomy

Four categories for evaluating the mutation chain's operational quality:

| Category | Metrics |
|----------|---------|
| **Integrity** | Valid mutation run rate, parse success rate, state write/read consistency |
| **Efficiency** | Runtime reduction vs baseline, mutants executed per function, fallback frequency |
| **Signal quality** | Score degradation vs baseline, gate precision/recall proxy, advisory→authoritative convergence |
| **Actionability** | Prescription acceptance/use rate, median time from finding to improved kill-rate, fraction of high-survival functions with decomposition/test action generated |

The "actionability" category is the ultimate measure — if prescriptions aren't leading
to improved kill rates, the chain isn't working regardless of how precise the signals are.

---

## 12. Self-Hosting Proof-of-Concept

The ultimate validation: point the fully-wired mutation chain at a real god-module
in the LintGate codebase and verify that the symbolic system produces decomposition
guidance that a cheap execution model can follow.

**Target: `mcp_tools/quality_helpers.py`** (2000+ LOC, CC=196, 48 code smells).

Expected behavior of the fully-wired chain:

1. Mutation engine profiles each function → per-function survival rates with
   category+location breakdown.
2. MUTCH007 fires on entangled functions (high multi-category survival).
3. Prescription engine emits `DECOMPOSE_FUNCTION` with decomposition plan: specific
   axes (line ranges, dominant categories) for each entangled function.
4. Test skeleton generator produces targeted test stubs for each proposed subfuction.
5. A cheap execution model (Factory Droid on MiniMax M2.5) reads the decomposition
   plan and test stubs, executes the mechanical extraction.
6. Re-verification loop confirms specification completeness improved for each
   extracted function.

This would demonstrate the full thesis: mutation pressure → specification gap →
forced decomposition → tractable units → safe optimization, all driven by symbolic
computation with only the mechanical typing delegated to an LLM.

**#99 (decompose quality_helpers.py) becomes the validation target, not a separate
work item.** If the mutation chain works, #99 is solved by the tool. If it doesn't,
#99's failure modes tell us exactly which connective tissue gaps still need closing.

---

## 13. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Overfitting tests to mutants | Emphasize contract-level assertions, not implementation snapshots |
| Equivalent mutant noise inflates recommendations | Explicit uncertainty fields, confidence penalties for >80% survival |
| Runtime blowups in large repos | Hard budgets, queue prioritization, strict fallback telemetry |
| State corruption under concurrent runs | File locking via `filelock`, transactional state updates |
| Schema churn breaks MCP consumers | `schema_version` field, additive changes only |
| Location extraction fails for complex AST patterns | Graceful fallback to aggregate-only profiles, never block on missing locations |
| Decomposition axes are wrong | Re-verification loop catches regressions; decomposition is advisory, not automatic |

---

## 14. Related Issues

- **#114** — Close the connective tissue. The active implementation issue for Phases 1-6
  of Section 11, plus the self-hosting proof-of-concept. Includes ported unique content
  from #102 (threshold calibration, signal quality tiering, acceptance metrics taxonomy).
- **#102** — Mutation-Guided Symbolic Refactoring Engine (CLOSED). Original master
  engineering issue. Phases 0-1 were completed. Remaining unique content ported to #114.
- **#99** — Decompose quality_helpers.py. The target for the self-hosting
  proof-of-concept. Becomes a validation case for the fully-wired chain.
- **#100** — Mutation CI + MCP loop hardening. Infrastructure correctness that must hold
  for the mutation chain to produce reliable data.
- **#96** — Split-brain postmortem. Includes the mutation state path unification that is
  a prerequisite for reliable cross-channel gate operation.
