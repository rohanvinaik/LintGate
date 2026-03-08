# Specification Engine Implementation Plan

Design document for closing the remaining gaps between LintGate's specification
complexity theory and its implementation. Supersedes the mutation-focused
`mutation-implementation-plan.md` for the execution layer — the original mutmut
approach was abandoned after it caused ~200GB RAM usage via its trampoline
architecture. This plan builds a lightweight, AST-based specification engine.

## Current State (2026-03-08)

### Implemented (Symbolic Layer)

| Component | Status | Location |
|---|---|---|
| 6-path σ decision tree | Complete | `specification/predictor.py` |
| Multi-factor regime classification with rationale | Complete | `specification/predictor.py:_classify_regime` |
| Trajectory-aware phase detection | Complete | `specification/predictor.py:_detect_phase` |
| TrajectoryState (ΔK, convergence_rate, estimated_remaining) | Complete | `specification/types.py`, `predictor.py:_build_trajectory` |
| DFT scoring (statefulness, side effects, hidden deps) | Complete | `specification/predictor.py:compute_dft_score` |
| Test design signal extraction (BVA, equivalence, decision rules) | Complete | `specification/test_design_signals.py` |
| TPA calibration | Complete | `specification/tpa_calibration.py` |
| Risk model with fan-in/fan-out | Complete | `specification/risk_model.py` |
| Composition gap γ with interface mutation counting | Complete | `specification/composition.py` |
| Callee uncertainty weighting on interface mutations | Complete | `specification/composition.py:_count_interface_mutation_points` |
| Sheaf condition checker | Complete | `specification/composition.py:analyze_composition` |
| Symbolic-only file analyzer (enrich=False) | Complete | `specification/file_analyzer.py:_do_analyze_symbolic` |
| Project rollup with cache-read-only default | Complete | `specification/project_rollup.py` |
| Specification channel (ControlPlane) | Complete | `channels/specification_channel.py` |
| 7 MCP tools (spec_*) | Complete | `mcp_tools/specification_tools.py` |
| Prescriptions (6 categories, risk-prioritized) | Complete | `specification/prescriptions.py` |
| Optimization gate (stop criteria) | Complete | `specification/optimization_gate.py` |

### Missing (Execution Layer)

| Component | Theory Section | Priority | Description |
|---|---|---|---|
| AST mutation engine | §6.4 | P0 | In-process meta-mutant dispatch — generate and evaluate mutants without subprocess spawning |
| Monty Hall filtering | §6.1 | P0 | Exclude irrelevant mutation categories before generation using OperatorRelevanceMatrix |
| Active hypothesis testing | §6.2 | P1 | Signal-guided sampling: 3–5 representative mutants per category |
| Passive/active modes | §7 | P1 | Two-tier: passive (symbolic-only, current), active (mutation execution) |
| ΔK trajectory tracking | Thm 3.4 | P1 | Append spec_level deltas across runs, detect phase transitions empirically |
| Greedy convergence | Thm 3.2 | P1 | Each test adds ≥1/σ specification; verify proportional progress |
| Symmetry regime classification | Thm 4.1 | P1 | Regime from mutation symmetry group structure (replaces heuristic σ>20) |
| Test-impact mapping | §6.3 | P2 | Static import analysis + coverage DB for test selection |
| Cross-channel mutation gate | §5 | P2 | Mutation score gates optimization hints (the theory's central claim) |
| Background orchestration | §7 | P2 | Priority-queue scheduler for automatic background mutation work |

---

## Architecture: Why Not mutmut

The original plan (see `mutation-implementation-plan.md`) used mutmut as the
mutation execution backend. This was abandoned because:

1. **Memory**: mutmut's trampoline approach copies the entire test environment per
   mutant. On a 500-file project this reached ~200GB RAM.
2. **Granularity**: mutmut operates at line/expression level, not at the semantic
   category level the theory requires (value mutations, swap mutations, state
   coupling mutations).
3. **Speed**: Even the "inline sampling" mode (2–5s) was too slow for PostToolUse
   hooks where the budget is <8 seconds total.
4. **Control**: The theory's Monty Hall filter needs to exclude categories *before*
   generation. mutmut's `pre_mutation` hook is a post-hoc filter, not a generator
   control.

The replacement is an **in-process AST mutation engine** that:
- Generates mutants by AST rewriting (no subprocess spawning)
- Operates at the semantic category level (value, swap, state, boundary, type)
- Evaluates mutants by running targeted tests in the same process
- Respects a per-function time budget (500ms inline, 30s background)

---

## PR 5: AST Mutation Engine

**Goal:** Lightweight in-process mutation engine that generates and evaluates
mutants at the semantic category level.

### Module: `lintgate/specification/mutation_engine.py`

```
MutationEngine
├── generate_mutants(func_node, categories) → list[Mutant]
├── evaluate_mutant(mutant, test_func) → MutantResult
├── run_function_sampling(file, func_key, budget_ms) → SamplingResult
└── run_function_profiling(file, func_key) → ProfilingResult

Mutant
├── category: MutationCategory (VALUE, SWAP, STATE, BOUNDARY, TYPE)
├── original_node: ast.AST
├── mutated_node: ast.AST
├── description: str

MutantResult
├── killed: bool
├── killed_by: "assertion" | "crash" | "timeout" | None
├── test_name: str | None
```

### 5A: Mutant Generation (AST Rewriting)

**Category → AST Transform Mapping:**

| Category | Transform | AST Target |
|---|---|---|
| VALUE | Replace constant with boundary value (0, -1, MAX, empty string) | `ast.Constant` |
| VALUE | Negate boolean | `ast.Constant(value=bool)` |
| SWAP | Transpose two parameters in a call | `ast.Call.args` |
| BOUNDARY | Off-by-one on comparisons (`<` → `<=`, `>=` → `>`) | `ast.Compare` |
| BOUNDARY | Replace `range(n)` with `range(n-1)` or `range(n+1)` | `ast.Call(func=range)` |
| STATE | Remove `self.x = ...` assignment | `ast.Assign` to `self.*` |
| STATE | Replace `return x` with `return None` | `ast.Return` |
| TYPE | Replace `isinstance(x, T)` with `True` | `ast.Call(func=isinstance)` |

Each transform produces a new AST tree with the mutation applied. The engine
compiles the mutated tree to bytecode via `compile()` and `exec()` — no file
I/O, no subprocess.

### 5B: Mutant Evaluation

For each mutant:
1. Compile the mutated module AST
2. Import the mutated module into a sandboxed namespace
3. Run relevant test functions against the mutated namespace
4. Classify result: killed_by_assertion, killed_by_crash, survived, timeout

**Sandboxing:** The mutated module replaces only the target function in the
namespace. Other module-level state is preserved from the original. This prevents
mutation side effects from propagating.

**Test selection:** Without test-impact mapping (PR 8), run all test functions
that reference the target function name (same heuristic as
`ledger.py:_build_test_coverage_map`).

### 5C: Sampling Mode (Inline)

`run_function_sampling(file, func_key, budget_ms=500)`:
1. Parse file, find function node
2. Determine relevant categories (Monty Hall filter, PR 6)
3. Generate ≤3 mutants per category (representative sampling)
4. Evaluate mutants within time budget
5. Return `SamplingResult` with per-category kill/survive counts

This is the "active hypothesis testing" from §6.2. Each sampled mutant tests
a specific hypothesis: "does the test suite distinguish this behavioral
dimension?"

### 5D: Profiling Mode (Background)

`run_function_profiling(file, func_key)`:
1. Generate exhaustive mutants across all relevant categories
2. Evaluate all mutants (no time budget, but with per-mutant timeout of 5s)
3. Return `ProfilingResult` with full survival profile
4. Result is `coverage_depth=PROFILED`, `is_gateable=True`

### Tests

- Generate VALUE mutants for `def add(a, b): return a + b`
- Generate SWAP mutants for 2-param and 3-param functions
- Generate BOUNDARY mutants for comparison operators
- Sampling respects time budget (mock time)
- Killed mutants correctly classified as assertion vs crash
- Zero-param functions produce no SWAP mutants

---

## PR 6: Monty Hall Filtering

**Goal:** Exclude irrelevant mutation categories before generation.

### Module: `lintgate/specification/mutation_filter.py`

Port the `OperatorRelevanceMatrix` concept from archived code but implement
it against the new category taxonomy (VALUE, SWAP, STATE, BOUNDARY, TYPE)
instead of mutmut's operator types.

```python
def filter_categories(
    func_node: ast.FunctionDef,
    is_pure: bool,
    design_signals: TestDesignSignals,
) -> set[MutationCategory]:
    """Layer 1: Exclusionary filtering (§6.1).

    Returns the set of categories relevant to this function.
    Categories where the function has no structural support are excluded.
    """
```

**Filtering rules:**

| Condition | Excluded Categories |
|---|---|
| 0 parameters | SWAP |
| 1 parameter | SWAP |
| No comparisons in body | BOUNDARY |
| No `self.*` assignments, no global/nonlocal | STATE |
| No `isinstance` calls | TYPE |
| Pure function (no side effects) | STATE |

This is the "Monty Hall" insight: if a function has no comparisons, boundary
mutants cannot survive (there's nothing to mutate), so generating them wastes
budget. The filter reveals which "doors" have no prize.

### Tests

- Pure function with 2 params, no comparisons → {VALUE, SWAP}
- Stateful method with comparisons → {VALUE, SWAP, STATE, BOUNDARY}
- Zero-param function → {VALUE, BOUNDARY, STATE, TYPE} minus whatever else
- isinstance-free function excludes TYPE

---

## PR 7: Passive/Active Modes + MCP Tools

**Goal:** Two operational modes with explicit MCP tool surface.

### Mode Architecture

```
Passive Mode (current default):
  spec_file_analyze(enrich=False) → symbolic σ estimate
  spec_file_analyze(enrich=True)  → manifest-enriched σ estimate
  Both are predictive — no mutation execution.

Active Mode (new):
  spec_mutation_sample(path, file, function?) → inline sampling (500ms budget)
  spec_mutation_profile(path, file, function?) → full profiling (background)
  spec_mutation_gate(path, function?) → check if hints are backed by mutation evidence
```

### New MCP Tools (3)

**`spec_mutation_sample`**: Run inline AST mutation sampling for a function or
file. Returns per-category kill/survive counts, coverage_depth=SAMPLED.
Budget: 500ms per function.

**`spec_mutation_profile`**: Run exhaustive mutation profiling. Returns full
survival profile, coverage_depth=PROFILED, is_gateable=True. Slower (seconds
per function).

**`spec_mutation_gate`**: Check which optimization hints are backed by mutation
evidence. Returns per-function: eligible/gated/partial/no_data. This is the
theory's central claim (§5): mutation score gates optimization hints.

### Integration with Existing Tools

`spec_file_analyze` gains an optional `mode` parameter:
- `mode="passive"` (default): current symbolic analysis
- `mode="active"`: runs symbolic + inline mutation sampling

The active mode result includes everything the passive mode returns, plus:
- `mutation_survival_rate` per function
- `mutation_categories_tested` count
- `specification_confidence` (higher when mutation data confirms symbolic estimate)

### Tests

- spec_mutation_sample returns results within budget
- spec_mutation_gate returns "no_data" when no profiling has been done
- spec_mutation_gate returns "gated" when survival > 50%
- Active mode produces strictly more data than passive mode

---

## PR 8: ΔK Trajectory Tracking

**Goal:** Empirical phase transition detection via specification level deltas
across successive measurements.

### Changes

**`specification/types.py` — TrajectoryState enrichment:**

The `TrajectoryState` already has `delta_k: list[float]`. This PR populates it
across runs by loading the previous trajectory from the cached ledger and
appending the new delta.

```python
def update_trajectory(
    current: TrajectoryState,
    new_spec_level: float,
    previous_spec_level: float,
) -> TrajectoryState:
    """Append ΔK and detect phase transitions (Thm 3.4)."""
    delta = new_spec_level - previous_spec_level
    new_delta_k = current.delta_k + [delta]

    # Detect transition: sustained decrease in ΔK magnitude
    transition_idx = _detect_transition_point(new_delta_k)

    # Update convergence rate: EMA of recent deltas
    recent = new_delta_k[-5:] if len(new_delta_k) >= 5 else new_delta_k
    convergence_rate = sum(abs(d) for d in recent) / len(recent)

    return TrajectoryState(
        delta_k=new_delta_k,
        transition_index=transition_idx,
        estimated_remaining=current.estimated_remaining,
        convergence_rate=round(convergence_rate, 4),
    )
```

**Phase transition detection:** When the moving average of |ΔK| drops below
a threshold (e.g., 0.02) for 3+ consecutive measurements, the function has
entered the tail phase — diminishing returns on additional testing.

### Integration

The specification channel and project rollup load previous trajectories from
cached ledgers and call `update_trajectory` when new measurements arrive. This
enables the system to say "this function has been in bulk phase for 5 runs
with convergence_rate=0.15 — 3 more runs should reach transition" vs "this
function has been in tail for 8 runs — further testing has diminishing returns."

### Tests

- Trajectory with monotonically decreasing ΔK detects transition
- Trajectory with flat ΔK stays in bulk
- Convergence rate computed from recent window
- Empty trajectory (first run) returns defaults

---

## PR 9: Test-Impact Mapping

**Goal:** Skip irrelevant tests during mutation evaluation.

### Module: `lintgate/specification/test_impact.py`

Phase 1 (static): Parse test file imports, map test functions to source
functions they reference. Reuse `ledger.py:_build_test_coverage_map` and extend
it to return `dict[str, list[tuple[str, str]]]` (function → list of
(test_file, test_func) pairs).

Phase 2 (future): Integrate `coverage.py` JSON output when available for
precise line-level test→source mapping.

### Impact

Without test-impact mapping, the mutation engine runs all tests for every
mutant. With it, only tests that reference the mutated function run — typically
10-20x faster for large test suites.

---

## PR 10: Cross-Channel Mutation Gate

**Goal:** The theory's central claim — mutation score gates optimization hints.

### Gate Logic

When mutation profiling data is available for a pure function:
- survival_rate > 50% → all optimization hints suppressed
- survival_rate 20–50% → only "cacheable" survives, others gated
- survival_rate < 20% → all hints pass

This reuses the gate policy from `mutation-implementation-plan.md` PR 3, but
wired to the new AST mutation engine instead of mutmut.

### Finding Code

MUTCH004 in the performance channel: "Pure function with ungated optimization
hints — mutation survival {rate}%."

---

## PR 11: Greedy Convergence Verification

**Goal:** Validate Theorem 3.2 — each test should add ≥1/σ specification
coverage. This turns the theoretical bound into an observable, enforceable
property of the test suite.

### Theory (Thm 3.2)

For a function with specification complexity σ, any test that kills at least
one previously-surviving mutant adds specification coverage ≥ 1/σ. This gives
a greedy bound: σ tests suffice to fully specify the function. If a test kills
zero new mutants, it adds zero specification — it is redundant with respect to
the existing suite.

### Module: `lintgate/specification/greedy_convergence.py`

```python
@dataclass
class ConvergenceStep:
    test_name: str
    new_kills: int          # mutants killed by this test that survived all prior tests
    delta_spec: float       # new_kills / total_mutants (empirical Δ specification)
    cumulative_spec: float  # running total specification coverage
    meets_bound: bool       # delta_spec >= 1/sigma

@dataclass
class ConvergenceResult:
    function_key: str
    sigma: int
    steps: list[ConvergenceStep]
    redundant_tests: list[str]          # tests that killed zero new mutants
    convergence_efficiency: float       # actual_steps_to_full / sigma
    greedy_bound_violations: int        # steps where delta_spec < 1/sigma but new_kills > 0
    is_fully_specified: bool            # cumulative_spec >= 1.0

def analyze_convergence(
    profiling_result: ProfilingResult,
    sigma: int,
    test_ordering: list[str] | None = None,
) -> ConvergenceResult:
    """Analyze test suite convergence against the greedy bound.

    If test_ordering is None, tests are ordered by kill count (greedy-optimal).
    This gives the best-case convergence. Passing the actual test execution
    order reveals how far the real suite deviates from optimal.
    """
```

### Algorithm

1. Start with all mutants in the "surviving" set
2. For each test (in specified or greedy-optimal order):
   a. Count how many surviving mutants this test kills → `new_kills`
   b. `delta_spec = new_kills / total_mutants`
   c. Check `delta_spec >= 1/sigma` (the greedy bound)
   d. Remove killed mutants from surviving set
   e. Record step
3. Tests that kill zero new mutants → redundant
4. `convergence_efficiency = steps_to_full / sigma` (1.0 = optimal, >1.0 = suboptimal)

### Integration Points

- **Prescription enrichment**: `specification/prescriptions.py` gains a
  `convergence_prescription` category. When `convergence_efficiency > 1.5`,
  the prescription says "test suite has X redundant tests and converges Y%
  slower than optimal — consider consolidating."

- **ΔK trajectory** (PR 8): Each convergence step's `delta_spec` feeds into
  the trajectory's `delta_k` list, enabling empirical phase detection from
  test-by-test convergence data (not just run-to-run deltas).

- **MCP tool**: `spec_convergence_analyze(path, file, function?)` — requires
  profiling data from PR 5. Returns `ConvergenceResult` with redundancy
  analysis and efficiency metric.

### Finding Code

SPECCH008: "Test suite converges at {efficiency}x the greedy bound — {n}
redundant tests detected." Fires when `convergence_efficiency > 1.5` and
`redundant_tests` is non-empty.

### Tests

- 3-mutant function with 3 tests each killing 1 → efficiency=1.0, no violations
- 3-mutant function with 5 tests, 2 redundant → efficiency reported, redundant list populated
- Greedy-optimal ordering produces efficiency ≤ 1.0
- Zero-sigma function (no mutants) returns trivially fully_specified
- Bound violation detected when a test kills mutants but delta_spec < 1/sigma
  (possible with clustered kills across non-independent categories)

---

## PR 12: Symmetry Regime Classification

**Goal:** Replace the heuristic regime classifier (`sigma > 20 → B`) with
a symmetry-group-derived classification using actual mutation data (Thm 4.1).

### Theory (Thm 4.1)

The mutation symmetry group G(f) of a function f is the set of input
permutations under which the function's mutation behavior is invariant.
Functions with large symmetry groups have redundant mutation categories —
their specification complexity is lower than the raw σ estimate suggests.
Conversely, functions with trivial symmetry groups (|G| = 1) have maximum
specification complexity for their parameter count.

The regime classification should use:
- **|G| relative to parameter count**: High |G|/n! → A (symmetry reduces effective σ)
- **Category independence**: If mutation categories produce non-overlapping
  kill sets, the function has independent behavioral dimensions → harder to
  specify → pushes toward B
- **Kill-set overlap structure**: Shared kills across categories indicate
  redundancy → easier to specify → pushes toward A

### Module: `lintgate/specification/symmetry_classifier.py`

```python
@dataclass
class SymmetryAnalysis:
    function_key: str
    parameter_count: int
    symmetry_group_size: int          # |G(f)|
    max_possible_symmetry: int        # n! for n params
    symmetry_ratio: float             # |G| / n!
    category_independence: float      # 0.0 (fully overlapping) to 1.0 (fully independent)
    effective_sigma: int              # sigma adjusted by symmetry
    regime: str                       # "A" or "B"
    regime_rationale: str             # human-readable explanation
    data_source: str                  # "mutation" (from engine data) or "symbolic" (fallback)

def classify_regime_from_mutations(
    profiling_result: ProfilingResult,
    sigma: int,
    is_pure: bool,
) -> SymmetryAnalysis:
    """Regime classification using mutation data (Thm 4.1).

    Requires profiling data from PR 5. Falls back to symbolic heuristic
    when mutation data is unavailable.
    """

def _compute_symmetry_group_size(
    profiling_result: ProfilingResult,
) -> int:
    """Estimate |G(f)| from mutation kill patterns.

    Two mutations m1, m2 are symmetry-equivalent if they produce identical
    kill sets (the same tests kill both). The symmetry group size is the
    number of equivalence classes.

    This is an approximation — true symmetry group computation requires
    solving the graph isomorphism problem on the mutation-test bipartite
    graph, which is intractable. The equivalence-class approximation is
    conservative (underestimates |G|, so never incorrectly classifies B→A).
    """

def _compute_category_independence(
    profiling_result: ProfilingResult,
) -> float:
    """Measure independence between mutation categories.

    For each pair of categories, compute the Jaccard distance of their
    kill sets. Category independence is the mean pairwise Jaccard distance.

    0.0 = all categories killed by the same tests (fully redundant)
    1.0 = each category killed by a unique test set (fully independent)
    """
```

### Regime Decision Logic

```python
def _decide_regime(
    symmetry_ratio: float,
    category_independence: float,
    sigma: int,
    is_pure: bool,
) -> tuple[str, str]:
    # Pure functions are always tractable
    if is_pure:
        return "A", "pure function: specification scales linearly"

    # High symmetry reduces effective complexity
    if symmetry_ratio > 0.3:
        effective = int(sigma * (1 - symmetry_ratio))
        return "A", (f"high symmetry ratio ({symmetry_ratio:.2f}) reduces "
                     f"effective sigma from {sigma} to ~{effective}")

    # Low independence means categories are redundant — easier than sigma suggests
    if category_independence < 0.3:
        return "A", (f"low category independence ({category_independence:.2f}) "
                     f"indicates redundant mutation dimensions")

    # High sigma + high independence + low symmetry = genuinely hard
    if sigma > 12 and category_independence > 0.7:
        return "B", (f"sigma={sigma} with high category independence "
                     f"({category_independence:.2f}) and low symmetry "
                     f"({symmetry_ratio:.2f}): genuinely complex specification surface")

    return "A", f"sigma={sigma} within tractable range (symmetry-adjusted)"
```

### Integration with Existing Regime Classifier

The current `_classify_regime` in `predictor.py` uses the symbolic heuristic
(`sigma > 20`). After PR 12:

1. `predictor.py:_classify_regime` remains the **symbolic fallback** — used
   when no mutation data is available (passive mode).
2. `symmetry_classifier.py:classify_regime_from_mutations` is the **primary
   classifier** — used when profiling data exists.
3. The `PredictionResult` gains a `regime_data_source: str` field ("symbolic"
   or "mutation") so consumers know which classifier produced the regime.
4. The `spec_file_analyze(mode="active")` flow (PR 7) automatically uses the
   symmetry classifier when profiling data is available.

### Fallback Behavior

When mutation data is unavailable:
- `classify_regime_from_mutations` returns `data_source="symbolic"` and
  delegates to the existing `_classify_regime` heuristic
- No behavioral change for passive-mode users
- The symmetry classifier only fires when profiling data exists

### Tests

- Pure function → always A regardless of mutation data
- Function with all kills from same test set → high symmetry, low independence → A
- Function with each category killed by unique tests → low symmetry, high independence → B candidate
- Symmetry group size estimation: 3 mutations with identical kill sets → size=1 equivalence class
- Category independence: 2 categories with Jaccard distance 1.0 → independence=1.0
- Fallback to symbolic when ProfilingResult is None
- regime_data_source correctly set to "mutation" or "symbolic"

---

## PR 13: Background Orchestration & Scheduling

**Goal:** Automatic scheduling of mutation work via a priority queue, enabling
progressive background profiling without explicit user intervention.

### Problem

The mutation engine (PR 5) provides sampling (500ms) and profiling (30s) per
function, but the user must explicitly call `spec_mutation_sample` or
`spec_mutation_profile` for each function. For a 200-function project, this
is impractical. The system needs to automatically schedule mutation work based
on priority signals and available budget.

### Module: `lintgate/specification/scheduler.py`

```python
@dataclass
class SchedulerConfig:
    max_concurrent: int = 1           # sequential by default (safe)
    batch_size: int = 10              # functions per batch
    budget_per_batch_s: float = 30.0  # wall-clock seconds per batch
    cooldown_s: float = 60.0          # minimum gap between batches
    auto_promote: bool = True         # auto-promote sampled→profiled when idle

class MutationScheduler:
    """Priority-queue scheduler for background mutation work.

    Functions are prioritized by a composite score:
    - Higher σ → higher priority (more to learn)
    - Higher risk_score → higher priority (more impact if wrong)
    - No existing mutation data → higher priority than stale data
    - Recently edited files → higher priority (freshness)
    - Pure functions with optimization hints → highest priority (gate candidates)
    """

    def __init__(self, config: SchedulerConfig): ...

    def enqueue_file(self, file_path: str, functions: list[FunctionSpecification]) -> int:
        """Add functions from a file to the priority queue. Returns count enqueued."""

    def enqueue_project(self, project_path: str) -> int:
        """Scan project, enqueue all functions by priority. Returns count."""

    def next_batch(self) -> list[ScheduledItem]:
        """Pop the next batch of functions to process."""

    def report_result(self, item: ScheduledItem, result: SamplingResult | ProfilingResult):
        """Record result, potentially promote to profiling tier."""

    def status(self) -> SchedulerStatus:
        """Current queue depth, completed count, estimated remaining time."""
```

### Priority Score

```python
def _compute_priority(func_spec: FunctionSpecification) -> float:
    score = 0.0

    # Sigma: higher = more specification surface to explore
    score += min(func_spec.sigma / 30.0, 1.0) * 40

    # Risk: higher impact functions first
    if func_spec.risk_score:
        score += func_spec.risk_score * 30

    # No existing data: cold-start premium
    if func_spec.coverage_depth == "none":
        score += 20
    elif func_spec.coverage_depth == "sampled":
        score += 5  # promote to profiled

    # Gate candidates: pure + has optimization hints
    if func_spec.is_pure and func_spec.optimization_hints:
        score += 10

    return score
```

### Auto-Promote Logic

When `auto_promote=True` and a function has `coverage_depth="sampled"`:
1. If sampling survival_rate > 20% → promote to full profiling queue
2. If sampling survival_rate == 0% → skip profiling (already fully specified)
3. If sampling was inconclusive (budget exhausted before all categories) → promote

This creates a two-tier pipeline: fast sampling for triage, full profiling
only for functions that need it.

### Trigger Points

The scheduler integrates at three points:

1. **Post-`controlplane_run`**: After ControlPlane completes, enqueue any
   functions flagged by MUTCH004 (pure + ungated hints) at highest priority.
   Enqueue remaining functions from the specification channel at normal priority.

2. **Post-edit (debounced)**: When a file is edited, re-enqueue its functions
   with a freshness boost. Existing stale results for those functions are
   marked `needs_refresh`.

3. **Explicit `mutation_schedule_project`**: New MCP tool that triggers a
   full project enqueue + begins processing. Returns scheduler status.

### New MCP Tools (2)

**`mutation_schedule_project(path, budget_minutes?)`**: Enqueue all project
functions and begin background processing. Default budget: 5 minutes.
Returns initial scheduler status with queue depth and estimated completion.

**`mutation_scheduler_status(path)`**: Current scheduler state — queue depth,
completed count, functions by coverage_depth tier, estimated remaining time.

### Batching Strategy

Functions are processed in batches of `batch_size` (default 10):
1. Pop `batch_size` items from the priority queue
2. For each: run sampling (500ms) or profiling (30s) depending on tier
3. Record results, trigger auto-promote if applicable
4. Wait `cooldown_s` before next batch (prevents CPU monopolization)
5. Respect `budget_per_batch_s` — if a batch exceeds this, defer remaining
   items back to queue

### Persistence

Scheduler state is persisted to `.lintgate/scheduler_state.json`:
- Queue contents (function keys + priority scores)
- Completed items with timestamps
- Auto-promote decisions

This enables resumption across sessions — a partially-completed project
profile picks up where it left off.

### Tests

- Priority scoring: pure function with hints > high-sigma function > low-sigma function
- Auto-promote: sampled with survival > 20% promoted, survival == 0% skipped
- Batch respects budget (mock time): 10 items with 500ms each = 5s < 30s budget
- Cooldown enforced between batches
- Project enqueue populates queue from spec analysis results
- Persistence: save + load round-trips queue state
- Empty project: enqueue returns 0, no processing triggered

---

## Dependency Graph

```
[PR 5] AST Mutation Engine (core execution)
  │
  ├──→ [PR 6] Monty Hall Filtering
  │       │
  │       └──→ [PR 7] Passive/Active Modes + MCP Tools
  │               │
  │               ├──→ [PR 8] ΔK Trajectory Tracking
  │               │       │
  │               │       └──→ [PR 11] Greedy Convergence Verification
  │               │
  │               ├──→ [PR 10] Cross-Channel Gate
  │               │
  │               └──→ [PR 12] Symmetry Regime Classification
  │
  ├──→ [PR 9] Test-Impact Mapping (independent, enhances perf)
  │
  └──→ [PR 13] Background Orchestration & Scheduling
              (depends on PR 5 engine + PR 7 tools; enhances all)
```

**Recommended order:** PR5 → PR6 → PR7 → PR8 → PR9 → PR10 → PR11 → PR12 → PR13

Rationale: PR5 (engine) is the foundation everything depends on. PR6 (Monty
Hall) makes the engine practical by reducing mutant count. PR7 (modes + tools)
gives the user access. PR8 (trajectory) builds on accumulated run data. PR9
(test-impact) is a pure performance enhancement. PR10 (gate) is the theory's
payoff but needs reliable mutation data first. PR11 (greedy convergence) needs
profiling data to validate the bound. PR12 (symmetry) needs profiling data to
compute kill-set overlap. PR13 (scheduling) orchestrates everything — it should
be last so it can schedule all the analysis types that precede it.

---

## Design Decisions

### Why AST-Based, Not subprocess?

The theory requires semantic-category-level control over mutation. AST rewriting
gives us:
- Category-aware generation (VALUE, SWAP, STATE, BOUNDARY, TYPE)
- Pre-generation filtering (Monty Hall layer)
- In-process evaluation (no fork/exec overhead)
- Per-function time budgets (500ms inline)

The tradeoff is that AST mutation is less "realistic" than subprocess mutation
(the real module's global state, import side effects, etc. are not fully
replicated). For specification complexity measurement — which asks "how many
independent behavioral dimensions does this function expose?" — this is
acceptable. We're measuring the function's specification surface, not its
integration behavior.

### Why Not Revive Archived Code?

The `archive/mutation_system/` contains ~2000 lines of functional mutation
code built around mutmut. Reusable assets:
- `policy.py:OperatorRelevanceMatrix` — category taxonomy (port concepts, not code)
- `decomposition.py` — survivor category → refactoring action mapping (reusable as-is)
- `state.py` — persistence schema (adapt for new engine)

Not reusable: `engine.py` (mutmut-coupled), `ci_stats.py` (mutmut output format).

### Resource Budgets

| Mode | Per-Function Budget | Total Budget | Use Case |
|---|---|---|---|
| Symbolic (passive) | <50ms | <5s for 100 functions | Default, every run |
| Inline sampling (active) | 500ms | <8s for 15 functions | PostToolUse hook |
| Background profiling | 30s | Minutes for full project | Explicit tool call |

### Why Greedy Convergence as a Separate PR?

Greedy convergence (Thm 3.2) could be folded into PR 8 (trajectory tracking),
since both concern specification progress. They are separated because:

1. **Data dependency**: Convergence analysis needs per-test kill data from
   profiling results. Trajectory tracking needs only per-run spec_level deltas.
   PR 8 can ship without profiling; PR 11 cannot.
2. **Scope**: PR 8 is a data pipeline (append deltas, detect transitions).
   PR 11 is an analysis tool (evaluate test suite efficiency against a
   theoretical bound). Mixing them would produce a PR too large to review.
3. **Value standalone**: Convergence efficiency and redundant-test detection
   are independently useful even without trajectory tracking.

### Why Symmetry Classification Needs Mutation Data

The current symbolic regime classifier uses `sigma > 20` as a proxy for
"genuinely hard to specify." This works for cold-start (no mutation data)
but produces false B classifications for functions with high parameter counts
but strong symmetry (e.g., `max(a, b, c)` has σ=6 but |G|=6, making it
trivially specifiable). The symmetry classifier replaces the proxy with the
actual structural property the theory identifies (Thm 4.1), but requires
kill-set data that only exists after mutation profiling. The symbolic
classifier is preserved as the fallback for passive mode.

### Why Background Scheduling is Last

The scheduler (PR 13) is the orchestration layer that ties everything together.
It needs to know about: mutation categories (PR 5), filtering (PR 6), sampling
vs profiling tiers (PR 7), convergence efficiency (PR 11), and symmetry
classification (PR 12). Building it last means it can schedule all analysis
types with full knowledge of their resource profiles and data dependencies.

### Fail-Closed Guardrails

Same guardrails as the symbolic layer:
- File budget: 500 files max
- Line budget: 500K lines max
- Per-function timeout: hard kill at 2× budget
- Memory: mutant ASTs are discarded after evaluation (no accumulation)
- Scheduler: hard budget cap prevents runaway background work
- Auto-promote: fires when sampling survival > 20% or when sampling was inconclusive (budget exhausted); skipped when survival == 0% or on timeout
