# Mutation Testing as Specification Completeness: Theory and Architecture

A theory document for LintGate's mutation testing subsystem. Synthesized from design
conversations, CS theory, and analysis of the current implementation.

## 1. The Core Thesis

Mutation score is not a test quality metric. It is a **specification completeness**
metric, and specification completeness is the prerequisite for every other form of
code quality — performance engineering, safe refactoring, algebraic optimization,
and clean decomposition.

A surviving mutant is a program that is **observationally equivalent** to the original
under the existing test suite (Morris 1968, Plotkin, Abramsky). If no test can
distinguish the mutant from the original, the test suite does not fully specify what
the code does. In Sussman's framing (SICP), programs are "precise specifications of
computational processes." A function whose behavior is underdetermined by its tests is
not a precise specification — it is an ambiguous one.

Ambiguous specifications resist optimization, because optimization requires proving
behavioral equivalence, which is impossible when you don't know what behavioral
equivalence *means* for the function in question.

## 2. Why This Matters for Performance Engineering

LintGate's algebra pipeline detects properties that enable classical optimizations:

| Property      | Optimization It Enables                      |
|---------------|----------------------------------------------|
| Purity        | Memoization, CSE, dead code elimination      |
| Commutativity | Reordering for cache locality                |
| Associativity | Parallelization (map-reduce)                 |
| Idempotency   | Caching without invalidation, safe retry     |
| Boundedness   | Overflow-safe, no range checks needed        |

But these properties can only be **proven** for functions whose behavior is fully
specified. The purity analysis says "this function has no side effects, it's
cacheable." The mutation score says "but you don't know what it computes, so caching
it is meaningless."

**Purity without specification completeness is a false optimization signal.**

The cross-channel gate should be:

```
IF function is pure AND mutation_survival > 50%:
    → "This function is pure but underspecified.
       Decompose it until mutation score < 20%
       before applying performance optimizations."
```

This is TEFF005 (pure function, weak tests) elevated from an informational finding
to a **gate** on the performance pipeline.

## 3. The Supra-Signal: Survival Profiles as Decomposition Prescriptions

The individual profile of the survival pattern is a **symbolic articulation** of not
just the fact that code is ambiguous, but *why* it is ambiguous. Three dominant
failure modes emerge from real mutation data:

### 3.1 Insufficient Test Specificity (~59% of survivors)

Tests check `len(result) == 2` instead of `assert result == expected`. The shape is
specified but not the content. In Sussman's terms: the abstraction barrier is leaking.

**Refactoring signal:** Extract the computation that produces the *content* into a
function with its own contract. The shape-checking test stays; the content-checking
test targets the extracted function.

### 3.2 Untested Boundary Conditions (~24% of survivors)

Thresholds like `0.4` mutate to `0.41` and survive because test data is `0.45`. The
semantics of the boundary are unspecified.

**Refactoring signal:** Extract thresholds into named constants with documented
boundary semantics. Each boundary becomes a testable claim.

### 3.3 Single-Path Coverage in Multi-Path Code (~17% of survivors)

Three-level `if/elif/else` chains where only one path is tested. The untested paths
are underdetermined computation.

**Refactoring signal:** Extract each branch into its own function with its own
contract. Cascading conditionals become dispatch over independently testable units.

### 3.4 The Pattern

Each survival category points not to "write more tests" but to "your function is
doing too many things, or doing them in a way that's not independently testable."
The fix is **decomposition**:

| Survival Pattern                | Refactoring Signal                                           |
|---------------------------------|--------------------------------------------------------------|
| String matching survivors       | Extract a vocabulary/pattern registry with its own tests     |
| Threshold survivors             | Extract thresholds into named constants with boundary tests  |
| Cascading conditional survivors | Extract each branch into its own function with its own contract |
| Aggregate survivors             | Make aggregation composable (monoids!) so each step is independently testable |

The act of driving toward 100% mutation kill rate **is** what produces clean,
algebraically tractable code. 100% is not always achievable or desirable — but
exceptions should be **explicit and intentional**, not accidental.

## 4. Specification Completeness as a Lattice

For any function, there is a lattice of specifications from "no tests" (bottom) to
"fully specified" (top, i.e., zero surviving mutations). Each point enables a
different class of optimizations:

| Specification Completeness | What You Can Safely Do                                         |
|----------------------------|----------------------------------------------------------------|
| 100% (zero survivors)      | Full algebraic optimization, mechanical refactoring, verified equivalence transforms |
| ~80% (20% survival)        | Memoization, CSE, most performance optimizations with high confidence |
| ~50% (50% survival)        | Purity classification reliable, but specific properties unverified |
| ~25% (75% survival)        | Can only reason about types and call graph, not values         |
| 0% (no tests)              | Can't safely change anything                                   |

LintGate's purity analysis operates at ~50% specification completeness (proves
absence of side effects). The algebra classification operates at ~80% (proves
structural properties). Neither validates against actual behavior — that requires
the mutation score.

**Mutation score is the missing vertical axis.** It tells you where each function
sits on this lattice, which tells you which optimizations are safe, which tells you
what performance engineering is actually possible.

## 5. Theoretical Foundations and Novelty

### 5.1 Established Pieces (Solid, Disconnected)

- **Mutation testing** — DeMillo, Lipton, Sayward 1978. Competent programmer
  hypothesis, coupling effect, equivalent mutant problem (Budd & Angluin 1982).
- **Observational equivalence** — Morris 1968, the denotational semantics tradition.
- **Algebraic specification** — ADJ group (Goguen, Thatcher, Wagner, Wright, 1970s–80s).
  Algebraic properties as the right unit for specifying abstract data types.
- **Property-based testing** — QuickCheck (Claessen & Hughes 2000). Algebraic laws
  are practically testable.
- **Sussman's SICP** — Programs as specifications for computational processes,
  abstraction barriers, the "what" vs "how" distinction.

### 5.2 Partially Explored → Actively Explored by Related Groups

The reframing from "test quality" to "specification completeness" exists in pockets
(Gordon Fraser, Andrea Arcuri, the PITest community) but is not the dominant framing.
Mainstream mutation testing literature treats surviving mutants as a measure of test
suite adequacy — how good are the tests? — not as a statement about the function's
contract.

However, recent work from three independent groups converges on the same reframing:

- **Xu et al. (Waterloo)** — FAST (IEEE S&P 2023) explicitly treats mutation testing
  as a specification completeness audit for formally verified systems. "Not All Move
  Specifications Are Created Equal" (LangSec 2024) formalizes the quality taxonomy
  that maps to our specification lattice. The Agentic Specification Generator
  (ASE 2025) automates specification synthesis using mutation feedback.
- **Lévai & McMinn (Sheffield)** — mutest-rs demonstrates compiler-integrated mutation
  with static dispatch tables and non-conflicting mutation batching (ICST 2023,
  TOSEM 2026). Their cost model formalization (66.4% runtime reduction from batching)
  validates Section 6's execution architecture.
- **Vikram & Padhye (CMU)** — mu2 (ISSTA 2023) uses mutation kill count as a fuzzer
  fitness function, with dead mutant retirement and cartography-then-refine execution.
  This is the mu2 pattern that validates Section 7's two-tier orchestration.

### 5.3 Novel Synthesis

The operational chain — **mutation pressure -> forced decomposition -> algebraic
properties -> mechanizable optimization** — does not appear to be articulated as a
unified theory anywhere in the literature, though recent work independently validates
individual links in the chain. Related traditions each get part of it:

- **Haskell's equational reasoning** (SPJ's "Playing by the Rules") demonstrates
  algebraic properties enabling automated optimization — but starts from purity and
  algebraic laws, not from mutation testing as the discovery mechanism.
- **Okasaki's Purely Functional Data Structures** shows algebraic properties enabling
  mechanical optimization — again starting from the algebraic side.
- **Design by Contract** (Meyer, Eiffel) treats contracts as partial specifications
  but makes no connection to mutation testing as a completeness measure.
- **Formal methods** (B, Z, TLA+) achieve full specification at enormous cost and
  don't interact with the "fuzzy" creative layer.
- **Abstract interpretation** (Cousot & Cousot) approximates semantics statically
  but doesn't produce the decomposition pressure mutation testing creates.

What LintGate uniquely contributes is not any single link but the **operational chain
connecting them**: prediction (static signals → specification level) → targeted
execution (Monty Hall filtering → in-process mutation) → decomposition prescription
(survival categories → architectural moves) → test synthesis (mu2 fitness → minimal
pinning sets) → verification (re-profile → calibrate) → optimization gating
(specification completeness → safe algebraic transforms). FAST validates the
measurement theory, mu2 validates the synthesis loop, Sheffield validates the
execution architecture — but the end-to-end chain from static prediction through
decomposition to optimization gating is novel.

### 5.4 The Key Conjecture

Mutation pressure **reliably forces decomposition into algebraically tractable units**.

This is plausible — you can't achieve 100% kill rate on a tangled function without
decomposing it, because tangling is exactly what creates observational equivalence
between the original and mutants. But there is no formal proof, and counterexamples
may exist (functions that are fully specified by tests but not algebraically
tractable).

### 5.5 The Pareto Claim

The thesis produces a **Pareto improvement on both sides** of the engineering/creative
divide:

1. Mutation testing identifies where specification is incomplete.
2. The category of surviving mutants prescribes specific decompositions.
3. Those decompositions naturally produce algebraically tractable units.
4. Those units are amenable to automated symbolic optimization (the "super-linter").
5. Everything not captured by those units is, by construction, the genuinely
   creative/representational work.
6. Both sides are strictly better: engineering is rigorous and mechanizable; the
   creative layer is freed from carrying implicit engineering constraints.

### 5.6 On Perfection and Pragmatism

The conjecture above hedges: "no formal proof, and counterexamples may exist." This
invites the objection: *if you can't prove it universally, what's the point?*

But this is the wrong frame. Even literal *linters* have rules you apply to ignore
some of their recommendations. Nobody demands that `pylint` be provably correct about
every finding before they'll use it. The value isn't in universal truth — it's in
actionable signal at the point of decision.

This is not an attempt to build a universal theory of computer science. It is a
**tool**. One that you can point at a 100,000 LoC spaghetti monster and have it pull
out one method and say "this could benefit from a refactor and JIT optimization."
That is *absurdly* useful, even if not "provably" true in every case.

The specification completeness lattice doesn't need to be a perfect ordering. It needs
to be a *better* ordering than what exists today, which is: nothing. No mainstream
tool connects mutation survival profiles to decomposition prescriptions to optimization
eligibility. The bar is not "formally verified" — the bar is "more useful than flying
blind," and the empirical results (Section 8) clear that bar by a wide margin.

Linters work because they're right often enough to be worth the occasional `# noqa`.
This system works the same way. When it says "this function has 93% mutation survival
across 3 categories — decompose it," it might occasionally be wrong about the best
decomposition axis. But it's never wrong that the function is underspecified. And
knowing *that* — knowing where the specification gaps are, at per-function granularity,
with categorical breakdown — is the kind of signal that changes how you write code.

### 5.7 Research Grounding — Connecting Three Traditions

Three independent research traditions converge on pieces of the theory articulated
here. None connects all the pieces; their synthesis is what makes LintGate's approach
novel. But each provides formal backing for claims that were initially empirical.

**Sussman (MIT) — Programs as Specifications.** SICP's framing that programs are
"precise specifications of computational processes" is the philosophical foundation.
Sussman's "abstraction barrier" maps directly to the decomposition prescribed by
multi-category survival: when a function has survivors in string, conditional, AND
arithmetic categories, it has violated an abstraction barrier by mixing concerns. His
distinction between "what" (declarative knowledge) and "how" (imperative knowledge)
maps to the SICP/fuzzy classification from the ModelAtlas retrospective (Part IV):
SICP-style code is fully specifiable because it computes a definite "what"; fuzzy code
has an irreducible "how" that resists full specification because the "right answer" is
a design decision.

**Xu et al. (Waterloo) — Specification Completeness as Measurable Property.** FAST
(Ji & Xu, IEEE S&P 2023) mutates CODE and checks if SPEC catches it — exactly the
operation LintGate performs with test suites as implicit specifications. FAST
formalizes "specification blind spots" as gaps where mutated code still conforms to the
spec. Each blind spot is a degree of freedom (Section 8.3's "negative space"). "Not
All Move Specifications Are Created Equal" (Xu, LangSec 2024) provides the taxonomy
for specification quality that maps to our specification lattice (Section 4): some
specifications constrain behavior meaningfully (value assertions → high kill rate),
others are tautological (structural assertions → low kill rate despite passing tests).
eBPF SpecCheck (Lyu, Payer, Xu et al., SOSP 2025) formalizes the oracle taxonomy:
crash oracles (our `killed_by_crash`) prove crash-freedom but not behavioral
correctness; specification oracles (our `killed_by_assertion`) prove the function
computes the right output. This validates and extends the `specification_strength`
metric in `FunctionMutationState`. FuzzSlice (Xu, ICSE 2024) demonstrates static
analysis validated by targeted execution — the same pattern as our prediction layer
(static signals → specification level) followed by mutation verification.

**Lévai & McMinn (Sheffield) — Efficient Execution Architecture.** mutest-rs
demonstrates compiler-integrated mutation via a static dispatch table: all mutations
compiled into one binary, selected at runtime by a global handle. This is the
architectural pattern for the in-process meta-mutant engine (Issue #250). Their
non-conflicting mutation batching formalization (ICST 2023, TOSEM 2026) proves that
mutations in functions with disjoint call graphs can execute simultaneously, achieving
66.4% runtime reduction. This validates Section 6.3's parallelization claim and
provides the mathematical cost model. Their safety precondition — mutations conflict
iff their target functions share test execution paths — maps to our convergence
engine's `LensKind.CALL_GRAPH` evidence.

**Vikram & Padhye (CMU) — Mutation as Fitness Function.** mu2 (ISSTA 2023) inverts the
standard relationship: instead of running tests against mutants, it uses mutation kill
count as a fitness function for a greybox fuzzer. Inputs that kill more mutants are
prioritized. The cartography phase (map mutation sites once, amortize cost) IS the
Monty Hall filtering from Section 6.1. The dead mutant retirement (killed mutants
excluded from future runs, surviving set shrinks monotonically) IS the progressive
convergence from Section 7.3. mu2 validates the self-perpetuating cycle from the
ModelAtlas retrospective Part IV: mutation data guides input generation, which kills
mutants, which updates the mutation data.

**The Synthesis.** Sussman says programs ARE specifications. Xu shows specifications
have measurable completeness and quality taxonomies. Sheffield shows how to measure
efficiently. Padhye shows how to close the loop between measurement and synthesis.
DeMillo provides the measurement mechanism (mutant survival under the competent
programmer hypothesis). LintGate's contribution: the operational chain that makes this
useful for working engineers — prediction → targeted execution → decomposition
prescription → test synthesis → verification → calibration → optimization gating.

### 5.8 The Orthogonal Lens Theorem

The prediction layer achieves 94.8% scope reduction on LintGate itself: of 8,118
functions, only 425 need mutation verification. This is achieved by combining four
static signals — `semantic_ratio` from test effectiveness, `weakness_taxonomy` from
test effectiveness, AST category count from local analysis, and `purity` from the
algebra pipeline — through a 6-path deterministic decision tree.

**Why does this work?** The signals are independently computed by different channels
with different methodologies. No single signal achieves more than ~60% accuracy. Yet
their combination achieves 94.8%. The explanation lies in the orthogonality of the
measurement axes.

Each channel measures a different dimension of Xu's specification quality:

| Channel | Dimension Measured | Independence Basis |
|---------|-------------------|-------------------|
| Test effectiveness (`semantic_ratio`) | Assertion quality — do tests check values? | Operates on test AST, not source |
| Test effectiveness (`weakness_taxonomy`) | Weakness classification — structural vs. semantic | Derived from assertion taxonomy |
| AST analysis (`num_categories`) | Behavioral dimensionality — how many concerns? | Operates on source AST only |
| Algebra (`purity`) | Structural property — side effects? | Operates on data flow analysis |

The convergence engine's probability union formula — `1 - Π(1 - confidence_i)` —
assumes independence. If three lenses each have 0.6 confidence, the union gives 0.936.
The empirical 94.8% is consistent with 3-4 independent signals at moderate individual
accuracy.

The information-theoretic framing: if the signals are truly independent observations
of the same latent variable (specification completeness), each adds fresh information.
The total information is the sum of individual contributions. This is the Naive Bayes
assumption applied to specification prediction — and like Naive Bayes in practice, it
works well even when the independence assumption is only approximately true.

This has practical implications for extending the prediction layer: the highest-value
new signal is one that is maximally independent of existing signals (measures a
dimension none of the current channels capture). Candidates include `fan_in` (measures
how many callers depend on the function — orthogonal to what the function does
internally) and `cochange` frequency (measures coupling — orthogonal to static
properties). See Issue #258 for the full exploration plan.

## 6. Efficient Execution: The Monty Hall Architecture

Mutation testing is expensive. If it is a first-order signal, hyper-optimizing the
process is essential. The architecture has three layers.

### 6.1 Layer 1: Exclusionary Filtering (The Monty Hall Layer)

Use existing LintGate signals to eliminate irrelevant mutation categories **before
generating a single mutant**:

| Signal Source              | What It Eliminates                                    |
|----------------------------|-------------------------------------------------------|
| Purity analysis            | PATH mutations on branchless pure functions            |
| Assertion classification   | Categories already covered by strong value assertions  |
| Algebraic property manifest| Reorder mutations on commutative operations (equivalent suspects) |
| Complexity analysis        | Don't bother with simple accessors that have strong tests |

For each function, compute the prior probability of each `SurvivorCategory` producing
actual survivors. Only generate mutations where the prior exceeds a threshold.

### 6.2 Layer 2: Active Hypothesis Testing

From the reduced candidate set, generate a small sample (3–5 mutations per category).
If any survive, the prior was right — we have a confirmed specification gap in that
category. If none survive, either the function is well-specified in that dimension or
the prior was wrong. Fallback to broader testing triggers only on "prior was wrong,"
which should be rare.

**Budget:** 3–5 targeted mutations x surviving categories x candidate functions. For a
typical function where filtering eliminates 4 of 7 categories, that's 9–15 mutations
instead of 50–200. **Order of magnitude reduction.**

### 6.3 Layer 3: Execution Optimization

The finite set of mutation operators (~15–20 distinct AST transformations) enables:

- **Caching:** Same source function + same operator = same mutant. Cache mutant
  generation.
- **Parallelization:** Each mutant is independent. Distribute across cores.
- **Test-impact selection:** Map functions to their covering tests (built once,
  invalidated on test file changes). 15 mutations x 3 relevant tests each >> 15
  mutations x full test suite.

### 6.4 Research-Backed Execution Architecture

The three execution optimization layers above (Sections 6.1–6.3) map directly to
techniques validated by recent systems research. The combined cost model transforms
mutation testing from prohibitively expensive to routine.

**In-process meta-mutant (mutest-rs pattern).** Replace the mutmut subprocess with
an in-process mutator. For each function, generate all mutant ASTs using Python's `ast`
module, construct a dispatch table indexed by `(category, mutation_id)`, and select the
active mutation via a module-level toggle. This eliminates the 0.39s subprocess startup
per invocation and the source file backup/restore dance. All transforms happen
in-memory — no disk writes, no trampoline artifacts, no corruption risk. The dispatch
table approach is proven by mutest-rs (Lévai & McMinn, Sheffield) and mu2's
`MutationClassLoader` (Vikram & Padhye, CMU). See Issue #250.

**Non-conflicting mutation batching (Sheffield pattern).** Two mutations are
non-conflicting if their target functions are disjoint in the call graph within a
configured depth. Non-conflicting mutations can be activated simultaneously — one test
run produces multiple data points. Lévai & McMinn (ICST 2023, TOSEM 2026) report 66.4%
runtime reduction from batching alone. Implementation requires: (1) a function-level
conflict graph built from test coverage data or static call graph analysis, (2) a
greedy graph coloring algorithm to find maximal independent sets, (3) failure
attribution when a batch test run fails (binary search or per-assertion tracking). See
Issue #253.

**Coverage-guided test scoping (Mull pattern).** For each mutation, run only the tests
that cover the mutated function, not the full suite. `coverage.py` already provides
per-context line coverage in its `.coverage` SQLite database. The existing
`test_impact.py` module parses this data at file granularity; extending to function
granularity (mapping source lines to function boundaries via AST) yields precise
per-mutation test selection. See Issue #260.

**Combined cost model.** The current cost is:

```
O(functions × mutants_per_function × subprocess_startup × full_test_suite)
```

With all three optimizations:

```
O(batches × covering_tests_per_batch × in_process_toggle)
```

Where `batches = ceil(mutants / batch_size)` with batch_size typically 3–10 for
well-structured code, and `covering_tests` is typically 2–5 per function vs. the full
suite. Conservative estimate: 50–200x total cost reduction, making continuous mutation
profiling practical as a background operation.

## 7. Orchestration: Two-Tier Progressive Execution

### 7.1 Inline Tier (On Edit, 2–5 Seconds)

Triggered on file creation, significant edits, or any ControlPlane run. Per function:

1. Read existing signals for the function.
2. Compute category priors (Monty Hall filter).
3. Generate 3–5 targeted mutations in high-prior categories.
4. Run against only the covering tests (test-impact selection).
5. Save results to manifest immediately with `coverage_depth=SAMPLED`.

A few mutation tests per method, a couple of seconds MAX per file. Directionally
correct specification completeness signal within seconds of every edit.

### 7.2 Background Tier (Progressive, From First ControlPlane Run)

A background process works through the full manifest, function by function, ordered
by priority:

1. **No data** — functions with no mutation state (flying blind, highest priority).
2. **Confirmed gaps** — functions where inline sampling found survivors (need full
   profile characterization).
3. **Stale data** — functions with code_hash mismatches.
4. **Everything else** — refresh/confirm.

Uses the same Monty Hall filtering with larger sample budgets. Saves results
incrementally — every finished function updates the on-disk cache. The manifest is
always growing.

### 7.3 The Convergence Property

By the time a user has:

1. Run `controlplane_run` (spawns background mutation process)
2. Written/edited several files (inline sampling on each)
3. Done theory mode exploration, performance engineering, etc.

...the background process has had 15–30 minutes of wall-clock time. With Monty Hall
filtering reducing each function to 10–20 mutations, and test-impact selection
reducing each mutation to 2–3 test runs, a 100-function project is fully profiled
in that window.

### 7.4 Two-Mode Orchestration — Passive Accumulator + Active Audit

The two-tier execution model (Sections 7.1–7.2) implies two operationally distinct
modes that should be explicitly separated rather than blurred.

**Passive mode (background accumulator).** Starts automatically on `controlplane_run`.
Symbolic-only — no source modification, no subprocess spawning, no test execution.
Operations: read existing state from the specification ledger, run the predictor
(`predict_specification_level`) to classify all functions, build category maps from AST
analysis, emit findings to the ControlPlane, enrich convergence data. This mode builds
a "symbolic mutation profile" — everything knowable about specification completeness
without executing mutations. It should feel invisible: one worker, one core, running
until the ledger is populated or the session ends.

This maps to mu2's cartography phase (Vikram & Padhye, ISSTA 2023): map all mutation
sites once, cheaply, as infrastructure for subsequent intensive analysis. The
cartography IS the passive mode; the exploitation loop IS the active mode.

**Active mode (intensive audit).** Triggered explicitly via MCP tools
(`mutation_run_sampling`, `mutation_run_full`) or via `declare_mode("mutation_audit")`.
Multi-worker, execution-intensive, informed by passive mode's accumulated data. Uses
in-process mutation (Section 6.4), mutation batching, and coverage-guided test scoping
for throughput. Produces authoritative data (`coverage_depth=PROFILED`) that supersedes
passive predictions. May block for minutes.

**Mode transition.** Configurable in `.claude/lintgate.yaml`:
`mutation.mode: passive | active | auto`. The `auto` default runs passive during
ControlPlane runs and active when MCP tools are explicitly invoked. Mode-aware
scheduling: passive mode prioritizes prediction refresh and staleness detection; active
mode uses the convergence-informed priority queue (TANGLED > UNSPECIFIED >
CONFIRMED_GAP > NEARLY_SPECIFIED > STALE).

**The passive mode output** is a new coverage depth level: `PREDICTED` (below
`SAMPLED`). Findings clearly distinguish predicted vs. sampled vs. profiled data. The
specification ledger (Issue #251) stores both passive predictions and active
verification results, with source-hash invalidation triggering re-prediction on code
changes. See Issue #254 for the full design.

## 8. Empirical Validation

On 2026-02-25, the full pipeline was tested end-to-end against
`lintgate/linters/performance_checks/purity.py` — a 124-line module with 99% line
coverage from the test suite. mutmut v3 generated 288 mutants. Results:

### 8.1 The Cross-Channel Gate in Action

`_get_parameter_count` — a pure function detected as cacheable by the algebra pipeline:

| Signal | Value | Interpretation |
|---|---|---|
| Purity detector | `is_pure=True, conf=0.80` | No side effects detected |
| Algebra pipeline | `hints=["cacheable"]` | Safe to memoize |
| Mutation testing | `survival_rate=0.75` (12/16 survived) | Tests specify only 25% of behavior |
| **Gate verdict** | `conf=0.10, [MUTATION GATED]` | Purity valid but semantics unverified |

Without the gate, an agent sees a pure cacheable function at 80% confidence and applies
`@lru_cache`. The gate says: you know it has no side effects, but you don't know *what
it computes*. The surviving mutants change the return value and no test notices. **Caching
wrong output is worse than recomputing correct output.**

This is the Sussman distinction made operational: purity is a structural property,
specification completeness is a semantic one. You need both to license optimization.

### 8.2 Per-Function Specification Profiles

Same file, same test suite, radically different specification completeness per function:

| Function | Kill Rate | Survival | Lattice Position |
|---|---|---|---|
| `visit_Yield` / `visit_YieldFrom` | 0% | 100% (no tests) | Bottom — unspecified |
| `visit_Assign` | 7% (2/28) | 93% | Weakly specified |
| `_get_parameter_count` | 25% (4/16) | 75% | Partially specified |
| `_check_mutable_defaults` | 33% (7/21) | 67% | Partially specified |
| `__init__` | 53% (9/17) | 47% | Moderately specified |
| `analyze_purity` | 55% (51/94) | 45% | Moderately specified |
| `visit_Call` | 59% (26/44) | 41% | Moderately specified |

The project-level kill rate (38%) hides all of this. Per-function granularity is what
lets the gate make surgical decisions instead of blunt ones. A function at 7% and a
function at 59% in the same file require completely different interventions.

### 8.3 Surviving Mutants as Negative Space

The most important finding: **surviving mutants are not just a score — they are a
constructive specification of the function's unverified behavioral degrees of freedom.**

Take `visit_Assign` — 93% survival, 26 of 28 mutants invisible to the test suite.
Those 26 surviving mutants are not an abstract number. Each one is a *specific
alteration* to the function's logic that produces different output and no test notices.
Taken together, they form a constructive specification of everything the tests *don't
require* the function to do. This is the **negative space**.

The positive space — what the tests *do* require — is the 2 killed mutants. That is the
entire behavioral contract enforced by the test suite for `visit_Assign`.

The "platonic ideal" version of a function is one with zero surviving mutants — every
behavioral degree of freedom is pinned down by at least one test. The surviving mutants
are the unpinned degrees of freedom. The function could wobble in those dimensions and
nobody would know. Each test you write pins one more degree of freedom. Each refactoring
that reduces the function's dimensionality makes full specification tractable.

### 8.4 Mutation Categories as Prescriptive Diagnostics

The *category* of surviving mutants makes the negative space actionable:

| Surviving Category | What It Means | Prescription |
|---|---|---|
| Conditional mutations survive | Branch logic unverified | Add tests for each branch outcome |
| Arithmetic mutations survive | Numeric computation unverified | Add boundary/value tests |
| String mutations survive | String handling unverified | Add format/content tests |
| Keyword mutations survive | Control flow unverified | Test break/continue/return paths |
| Multiple categories survive | Function has too many concerns | **Decompose along category axes** |

When a function has high survival across *multiple* categories, the problem isn't
testing — it's **decomposition**. The function is doing too many things, and the test
surface area required to specify all of them is combinatorially large. The mutation
profile tells you "split me up." The surviving categories tell you *along which axis*
to split.

This is why the theory calls it specification completeness and not test coverage. Coverage
tells you which lines executed. Mutation survival tells you which *behaviors* are
unconstrained. The difference is the difference between "this code ran" and "this code
does what we think it does."

### 8.5 Key Observations from the Run

1. **99% line coverage ≠ specification completeness.** `purity.py` has 99% line coverage
   but only 38% of mutants are killed. Lines executing is not behaviors being verified.

2. **The gate fires precisely where it should.** `_get_parameter_count` is pure and
   cacheable, but the gate crushes confidence from 0.80 to 0.10 because tests don't
   verify what it computes. This is the exact false signal the theory predicts.

3. **The specification lattice maps to real data.** The observed kill rates (0%, 7%, 25%,
   33%, 53%, 55%, 59%) span the lattice naturally. No function in this sample reaches the
   80%+ threshold where optimization hints would pass the gate with high confidence.

4. **mutmut v3's "no tests" category is large** (36/288 = 12.5%). The Monty Hall
   pre-filter would eliminate many of these by not generating mutants in categories
   irrelevant to the function's structural profile.

## 9. Current Implementation vs. Theory Alignment

### 9.1 What Exists

The `lintgate/mutation/` package contains five backend modules and six MCP tools:

| Module          | Purpose                               | Status             |
|-----------------|---------------------------------------|--------------------|
| `engine.py`     | Two-tier execution (inline/background)| **Operational** (mutmut v3) |
| `state.py`      | Per-function persistent state         | Functional         |
| `policy.py`     | Operator relevance, runtime budgets   | Functional         |
| `ci_stats.py`   | CI result parsing, hotspot loading    | Functional         |
| `telemetry.py`  | A/B validation targets                | Functional         |

Six MCP tools are exposed:

- `mutation_run_sampling` — Tier 1 inline sampling via MCP.
- `mutation_run_full` — Tier 2 background profiling via MCP.
- `mutation_get_state` — View per-function mutation state (survival, depth, confidence).
- `mutation_clear_state` — Reset mutation state for re-profiling.
- `analyze_test_strength` — Integrates mutation CI stats with assertion effectiveness.
- `inspect_test_assertions` — Classifies assertions with mutation-killing strength scores.

The cross-channel gate is operational in `classify_properties()` — when mutation state
shows >60% survival, confidence on optimization hints is crushed to 0.10. This is
surfaced through `inspect_algebra` with `mutation_gate` evidence annotations.

### 9.2 Closed Gaps (as of 2026-02-25)

**Gap 1 (CLOSED): Live mutation execution from MCP tools.** Four new MCP tools
(`mutation_run_sampling`, `mutation_run_full`, `mutation_get_state`,
`mutation_clear_state`) wire the engine to the agent's interactive loop. The engine
handles mutmut v3's config-driven CLI (temporary pyproject.toml scoping) and parses
v3's mangled output format (`.x_funcname`, `.xǁClassǁmethod`).

**Gap 3 (CLOSED): Cross-channel gate.** `classify_properties()` in
`performance_checks/properties.py` checks mutation state and modulates confidence:
>60% survival → `conf=0.10 [MUTATION GATED]`, 30-60% → `conf*=0.5 [MUTATION PENALIZED]`,
<30% → `conf=max(0.9) [MUTATION VERIFIED]`. The `inspect_algebra` MCP tool surfaces
gate evidence. Empirically validated: `_get_parameter_count` gated from 0.80 to 0.10.

### 9.3 Open Gaps

**Gap 2: Monty Hall filtering not in execution path.** `OperatorRelevanceMatrix` exists
and the engine passes `relevant_categories` through, but mutmut v3 doesn't support
per-category mutation selection. The filtering would need to happen post-generation
(discard irrelevant mutants before test execution) or pre-generation (custom mutant
generator).

**Gap 4: No background orchestration.** The engine has `run_background_profiling` but
nothing spawns it automatically. No ControlPlane integration triggers background
mutation work.

**Gap 5: No test-impact mapping.** `run_background_profiling` accepts a `test_mapping`
parameter but nothing builds this mapping. mutmut v3 has its own coverage-based test
selection (via `coverage.py`), which partially addresses this but requires a fresh stats
collection pass per config change.

**Gap 6: No inline-on-edit trigger.** No hook, no debounce logic. The MCP tools exist
for manual invocation but automatic inline sampling is not wired.

**Gap 7: Static vs. dynamic strength calibration.** The `STRENGTH_MAP` remains static.

### 9.4 What's Solid

- **State model is right.** `CoverageDepth` (NONE/SAMPLED/PROFILED), code/test hash
  freshness, per-function granularity — aligns with the two-tier progressive architecture.
- **Policy model is right.** `OperatorRelevanceMatrix` is the Monty Hall filter seed.
  `RuntimeBudget` has correct constraints. `MutationTelemetry` tracks the economics.
- **CI integration is right.** Stats parsing, badge generation, hotspot enrichment.
- **The gate works.** Empirically validated on real mutation data. The confidence
  modulation is correctly wired through the manifest builder to the MCP tools.
- **mutmut v3 integration works.** Config scoping, v3 output parsing with demangling
  (module-level and class methods), stats collection, per-function aggregation.

## 10. Implementation Path

Ordered by payoff-to-effort ratio. Phases 2 and 3 are complete.

### Phase 1: Wire the Monty Hall Filter

Connect `OperatorRelevanceMatrix` to the engine's execution path. Before generating
mutations for a function, query the algebra manifest for purity/properties and the
test effectiveness manifest for existing assertion coverage. Skip irrelevant
categories. This is the single highest-leverage change — it makes every subsequent
mutation run dramatically cheaper.

### Phase 2: Close the Cross-Channel Gate — DONE

The gate is operational in `classify_properties()`. When the algebra pipeline emits
"cacheable" for a function, the manifest builder checks mutation state. >60% survival
crushes confidence to 0.10, 30-60% halves it, <30% boosts to 0.90. Empirically
validated on `purity.py::_get_parameter_count` (0.80 → 0.10, gated at 75% survival).

### Phase 3: Expose MCP Tools — DONE

Four MCP tools operational: `mutation_run_sampling` (inline Tier 1),
`mutation_run_full` (background Tier 2), `mutation_get_state` (read state),
`mutation_clear_state` (reset). `inspect_algebra` surfaces `mutation_gate` evidence
annotations with gated confidence values.

### Phase 4: Build Test-Impact Mapping

Instrument a test run to record which functions each test calls. Store as a manifest.
Invalidate on test file changes. This is the efficiency unlock that makes background
profiling tractable. (Note: mutmut v3 has its own `tests_by_mangled_function_name`
mapping via coverage instrumentation, which partially addresses this.)

### Phase 5: Background Orchestration

Add a ControlPlane integration that spawns progressive background mutation work on
`controlplane_run`. Priority queue: no-data > confirmed-gaps > stale > refresh.
Incremental state persistence. Telemetry tracking of coverage progress.

### Phase 6: Inline-on-Edit Debounce

Hook into the supervision event loop. On file modification events, debounce (30s
cooldown per file), then run inline sampling for changed functions. Save with
`coverage_depth=SAMPLED`.

### Phase 7: Dynamic Strength Calibration

Periodically compare actual mutation kill rates per assertion kind against the static
`STRENGTH_MAP`. Adjust weights. This closes the loop between the fast static
approximation and the ground truth.

## 11. The Next Frontier: Mutation-Guided Refactoring as a Symbolic System

The empirical validation (Section 8) reveals something beyond the original thesis:
surviving mutants don't just measure specification gaps — they **prescribe** how to
close them. The mutation profile is a map of the function's unverified behavioral
degrees of freedom: its negative space.

This implies a genuinely novel tool: a **symbolic refactoring engine** that reads the
negative space of mutation results and emits targeted, actionable prescriptions — not
"write more tests" but "this function's conditional logic is unverified; decompose the
dispatch into independently testable units" or "this function's arithmetic is
unverified; extract the computation into a pure function with boundary tests."

The surviving mutant categories are the decomposition axes. The surviving mutant counts
are the priority weights. The surviving mutant locations are the surgical targets.
Together, they form a symbolic specification of the delta between the code as it is and
the code as it should be.

This is outlined as a full implementation proposal in GitHub Issue #102.

### 11.1 Mutation-Guided Test Synthesis

The symbolic refactoring engine prescribes decomposition. But the other half of the
cycle — generating the tests that pin each degree of freedom — also has research-backed
architecture.

**mu2's fitness function applied to test generation.** mu2 (Vikram & Padhye, ISSTA
2023) uses mutation kill count as a fitness function for a greybox fuzzer: inputs that
kill more mutants are prioritized, inputs that kill none are discarded. Applied to test
synthesis, this means: instead of generating test TEMPLATES and hoping they kill
mutants, generate test INPUTS guided by which mutants they kill. The mutation engine
becomes the oracle for the test generator. See Issue #255.

**Minimal pinning sets (FAST pattern).** FAST (Ji & Xu, IEEE S&P 2023) formalizes
"specification blind spots" — gaps where mutated code conforms to the original spec.
Each blind spot is a degree of freedom. The minimal fix is one assertion per blind
spot, not one test class per mutation category. Five arithmetic mutants that all change
the same `+` operator represent ONE degree of freedom, not five separate test needs. A
single parametrized test with the right input kills all five. The test generator should
search for high-kill-count inputs (set cover optimization), not enumerate per-mutant
templates. See Issue #252.

**Specification oracle taxonomy (eBPF SpecCheck pattern).** Not all kills are equal.
eBPF SpecCheck (Lyu, Payer, Xu et al., SOSP 2025) distinguishes crash oracles from
specification oracles. A crash-kill proves crash-freedom; an assertion-kill proves
behavioral correctness. But assertion-kills vary in strength: `assert result is not
None` (structural) is weaker than `assert result == expected` (value). The kill
taxonomy extends the current binary `killed_by_assertion`/`killed_by_crash` to a
multi-level classification: crash → type → structural → shape → value → property. Each
level proves a different property, and the specification strength becomes a vector
rather than a scalar. This enables more precise gating: caching requires value-level
specification (must verify the cached value), while parallelization requires
property-level specification (must verify associativity/commutativity). See Issue #256.

**The synthesis loop.** These three patterns combine into a closed-loop test synthesis
engine:

1. Profile function → identify surviving mutants by category
2. Group survivors by behavioral equivalence (same AST node = same degree of freedom)
3. For each degree of freedom, generate candidate inputs using mu2 fitness
4. Score candidates by kill count; keep high-scorers, discard zeros
5. Classify kills by oracle level (crash/structural/value/property)
6. Retire killed mutants; repeat on survivors until budget exhausted or all pinned
7. Output: minimal pinning set with per-input kill attribution and oracle classification

The loop is self-improving: each successfully pinned degree of freedom validates the
category→input mapping and refines it for future synthesis.

## 12. Related Systems

LintGate's mutation testing subsystem sits at the intersection of four active research
programs. Each addresses a different aspect of the same fundamental problem:
understanding and improving specification completeness.

### 12.1 FAST — Mutation as Specification Audit for Verified Systems

FAST (Ji & Xu, IEEE S&P 2023) applies mutation testing to formally verified systems
(seL4, Compcert). It mutates code and checks whether the formal specification catches
the mutation. LintGate does the same thing for informally verified systems — test
suites as implicit specifications. Where FAST checks satisfiability of formal specs,
LintGate checks assertion-kills in test suites. The measurement theory is identical;
the specification formalism differs.

### 12.2 eBPF SpecCheck — Oracle Taxonomy and Specification Quality

eBPF SpecCheck (Lyu, Payer, Xu et al., SOSP 2025) formalizes the distinction between
crash oracles and specification oracles for the eBPF verifier. LintGate extends this
taxonomy to mainstream Python testing: the `killed_by_assertion` vs `killed_by_crash`
distinction maps directly to specification oracle vs crash oracle. The multi-level kill
taxonomy (Issue #256) is the full operationalization of SpecCheck's oracle hierarchy
for general-purpose code.

### 12.3 mu2 — Mutation-Guided Fuzzing

mu2 (Vikram & Padhye, ISSTA 2023) uses mutation kill count as a fuzzer fitness
function. LintGate's test synthesis (Issue #255) is mu2 applied to specification
completeness: instead of fuzzing for bugs, "fuzz" for test inputs that pin behavioral
degrees of freedom. mu2's cartography-then-exploit architecture maps to LintGate's
passive-then-active orchestration (Section 7.4). mu2's dead mutant retirement maps to
the specification ledger's temporal tracking (Issue #251).

### 12.4 mutest-rs — Compiler-Integrated Efficient Mutation

mutest-rs (Lévai & McMinn, Sheffield) demonstrates that mutation testing can be made
efficient enough for routine use through compiler integration and batching. LintGate's
in-process meta-mutant (Issue #250) follows mutest-rs's dispatch table architecture
adapted to Python's runtime. The non-conflicting mutation batching (Issue #253) follows
Sheffield's formalization with call-graph-based conflict detection. Sheffield provides
the execution efficiency; LintGate adds the specification-level interpretation and
decomposition prescription that Sheffield's work does not address.

### 12.5 LintGate's Unique Position

What distinguishes LintGate from these systems is the **end-to-end operational chain**:

```
Static prediction (94.8% scope reduction)
    → Targeted mutation execution (in-process, batched, test-scoped)
    → Specification level classification (UNSPECIFIED → TANGLED → ... → SPECIFIED)
    → Decomposition prescription (survival categories → architectural moves)
    → Test synthesis (mu2 fitness → minimal pinning sets)
    → Verification (re-profile → calibrate predictions)
    → Optimization gating (specification completeness → safe algebraic transforms)
```

No existing system connects prediction through execution through decomposition through
synthesis through verification through optimization. FAST measures but doesn't
prescribe. mu2 synthesizes but doesn't decompose. mutest-rs executes efficiently but
doesn't interpret. LintGate's contribution is the chain itself — making mutation
testing not just a measurement tool but a specification convergence engine.
