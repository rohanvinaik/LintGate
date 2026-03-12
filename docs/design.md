# LintGate: Design Document

A technical deep-dive into LintGate's architecture, mechanisms, and design rationale.

---

## Overview

LintGate is a real-time quality system for AI-first development. It replaces the half-dozen things a competent senior engineer does automatically — catching broken imports before they propagate, noticing when architecture is drifting, remembering that the same class of mistake happened three edits ago, and saying "stop, go back, you're building on a broken foundation" before the damage spreads to twelve files.

It fires every time an LLM coding agent writes, edits, or executes a command that modifies files — classifying what changed, selecting appropriate linters, running them in parallel, and reporting structured results back to the agent before it writes the next line. When nothing is wrong, you see nothing. When something is wrong, the agent knows immediately.

But LintGate is more than a linter. It is an attempt to solve the fundamental reliability problem of LLM-native coding: **drift** — the slow, invisible divergence between what the developer intended and what the agent is actually building.

LintGate attacks drift at three levels. **Code drift** is the familiar problem: broken imports, type errors, complexity creep, architecture violations — the code diverging from what it should be. **Behavioral drift** is the harder, upstream problem: the agent's *problem-solving strategy* diverging from effective reasoning. An agent that tries the same failed approach three times, ignores error messages it saw ten minutes ago, or acts faster than it understands is exhibiting behavioral drift — and behavioral drift is what *causes* code drift. **Understanding drift** is the deepest problem: the agent never understood the project's constraints, conventions, or purpose, so it writes syntactically correct, lint-clean, test-passing code that is architecturally wrong. Catch each failure at its own level — code, strategy, understanding — and you never write the bad code.

The economic consequence is unintuitive: **the monitoring does not save tokens by catching bugs. It saves tokens by ensuring the agent never spends its intelligence budget on problems that are not intelligence problems.** Approach cycling, failure amnesia, formatting drift — these are discipline problems, and discipline is exactly what LLMs are worst at and deterministic systems are best at. An unsupervised agent spends a substantial fraction of its token budget on predictable, preventable failures. LintGate acts as a prefrontal cortex for the agent — not doing the thinking, but deciding what is worth thinking about, and interrupting when the thinking has gone off the rails. The result is not "fewer bugs" but "the agent spends its entire capacity on novel reasoning about your actual problem instead of re-discovering constraints it already encountered an hour ago."

The core insight is simple: **multiple cheap, lossy lenses trained on the same codebase compose into something that looks like intelligence.** No individual tool — not ruff, not mypy, not a complexity checker — can tell you "the agent is building on a broken abstraction." But when the type checker, the import analyzer, the complexity monitor, and the test runner all disagree with each other in a *specific pattern*, that disagreement is diagnostic. Three silent channels and one loud one concentrates your attention. Two failures on overlapping files reveals coupling. Pattern recurrence across sessions reveals systematic misunderstanding, not typos. And when the behavioral compass detects three failed approaches in thirty minutes with zero constraint verification, it tells the agent to stop and think before trying a fourth.

This is not AI. It is not expensive. It is the disciplined application of many bad instruments whose errors are uncorrelated — and whose agreement, therefore, means something.

---

## The Problem

AI coding agents produce code fast. A skilled developer using Claude Code can move through a codebase at a pace that would have been absurd two years ago — refactoring systems, building features, wiring up integrations — all through natural language. The bottleneck is no longer writing. It is *validating*.

The failure mode is not "the agent can't code." It is that the agent writes something subtly wrong at 2:14 PM, the developer doesn't notice because they're moving fast, and thirty edits later the codebase has quietly drifted into a state that is syntactically valid but architecturally broken. The type error compounds. The import hallucination propagates. The function that was supposed to be decomposed gets stuffed with task-specific logic because nobody told the agent that the project has a compositional architecture.

This is the reality of vibe coding. It works — remarkably well — until it doesn't, and by the time you notice, the damage is distributed across a dozen files and an hour of conversation history.

And the problem goes deeper than bad code. Before the agent writes the wrong code, it *reasons* wrong. It tries an approach, it fails, and instead of updating its model it tries a variant of the same approach. It encounters an error, moves on, and encounters the same error twenty minutes later — having learned nothing from the first encounter. It acts before it understands, running commands faster than it can incorporate the results. This is **behavioral drift**: the agent's problem-solving strategy diverging from effective reasoning. It is upstream of code drift, and by the time you see the code problems, the reasoning problems are already entrenched.

A senior engineer sitting next to you would catch most of this. Not because they're running linters in their head, but because they've built habits: check imports after refactoring, verify types after changing signatures, notice when complexity creeps, remember that the same mistake happened last Tuesday. They don't think about these things — they just *do* them, reflexively, as part of writing code.

LintGate exists because the person building it is a biochemist, not a software engineer. They vibe-code everything. They don't have those habits, and they're never going to develop them — they're too busy thinking about protein folding or shortcut DSLs or whatever the actual work is. They got tired of discovering, forty minutes into a session, that the agent had been cheerfully building on top of a broken import since edit number three. They needed the habits without the engineer.

---

## Architecture Overview

LintGate operates at two levels, selectable per-project:

**Level 1: The 5-Phase Pipeline** (default) — A PostToolUse hook that fires after every Write, Edit, MultiEdit, or Bash tool use. Classifies the change, selects linters by tier, runs them in parallel with timeouts, aggregates results, and reports back to the agent. Lightweight, fast (sub-8-second budget), and zero-config.

**Level 2: The ControlPlane** (opt-in) — A supervision mesh that replaces the pipeline with seven independent analysis channels (lint, tests, dependencies, git, behavior, structure, test_effectiveness) running in parallel, a cross-channel coherence engine that diagnoses system state from multi-signal agreement/disagreement, session memory that tracks coherence trajectories across runs, a behavioral compass that monitors the agent's problem-solving strategy for drift with an intent bias layer that classifies every tool use into a 6-category intent taxonomy and uses that structure to adjust signal confidence, a constraint proposer that translates recurring patterns into enforceable rules, and an optional global behavior profile that aggregates behavioral patterns across sessions over days/weeks to provide warm-start priors for the bias layer. This is the heavy machinery — for projects where the cost of drift justifies real-time multi-domain supervision.

Both levels share the same change classifier, linter registry, and reporting infrastructure. The ControlPlane is a strict superset.

**Cross-cutting: The Project Compass** — A 4-axis understanding model (Problem / Solution / Implementation / World) that extracts project knowledge from documentation and code, detects where understanding is thin, and renders the result into context files for any AI coding tool. The compass operates through three cognitive modes — Theory (understand before you write), Normal (monitor for staleness), and Habit (execute with frozen understanding) — and integrates via Claude Code hooks. It addresses the class of failures that neither code-level nor strategy-level supervision can catch: architecturally wrong code produced by an agent that never understood the project's constraints.

---

## The 5-Phase Pipeline

Every tool use flows through a deterministic 5-phase pipeline:

```
PostToolUse event (stdin JSON)
  │
  ├─ Phase 1: Change Classifier
  │    What changed? Which files? What language?
  │    Import-only? Structural? Architectural?
  │    Touches pipeline-critical paths?
  │
  ├─ Phase 2: Tier Selector
  │    Map change classification → lint tier
  │    Skip cosmetic/docs changes entirely
  │    Escalate critical paths to Tier 3
  │
  ├─ Phase 3: Lint Runner
  │    Build registry → filter by tier → run in parallel
  │    Per-linter timeouts (ThreadPoolExecutor)
  │    Graceful degradation: missing tools silently skipped
  │
  ├─ Phase 4: Results Aggregator
  │    Deduplicate, apply exemptions, split by severity
  │    blocking / warning / informational
  │    Merge with issue memory + pattern bank
  │
  └─ Phase 5: Agent Reporter
       Format for agent consumption (XML-style lint-report)
       Delta tracking: "1 fewer blocking issue than last run"
       Pattern alerts: "ruff/F821 seen in 4 of last 5 runs"
       Output: JSON → stdout → systemMessage
```

When nothing is wrong, the output is `{}`. Silent. Zero noise. The developer only sees LintGate when it has something worth saying.

When something is wrong:

```xml
<lint-report tier="tier_2_structural" reason="Structural change" files="pipeline.py">
BLOCKING (1 issue - must fix):
  [ruff/F821] pipeline.py:42: Undefined name 'process_data'
WARNINGS (2):
  [mypy/return-value] pipeline.py:50: Missing return statement
  [radon/complexity] pipeline.py:30: process() CC=18 (threshold=15)
PATTERN ALERT: ruff/F821 seen in 4 of last 5 runs — recurring issue
IMPROVEMENT: 1 fewer blocking issue than last run
</lint-report>
```

The agent sees this as a `systemMessage` and can act on it immediately — fixing the undefined name before writing the next function that depends on it.

---

## Tiered Lint Selection

Not every change needs the same scrutiny. An import cleanup does not need cyclomatic complexity analysis. A function signature change does.

| Change Type | Tier | What Runs |
|---|---|---|
| Docs, comments, formatting | Skip | Nothing |
| Import-only changes | 1 | ruff, imports, context rules, redefinitions |
| Config files | 1 | ruff, version checks, context rules |
| Logic changes | 2 | ruff, mypy, ty, complexity, structure, bandit_fast, pip_audit, context rules, redefinitions |
| Structural (signatures, classes) | 2 | Same as logic |
| Architectural (critical paths, 3+ files) | 3 | Full suite: security, architecture, dead code, cognitive complexity, ty, bandit_fast, pip_audit |

The default is aggressive: **Tier 2 for ordinary code changes**. This is deliberate. In AI-first development, the agent produces code fast enough that the bottleneck shifts from writing to validating. Catching a type error three seconds after it's written is cheaper than debugging it thirty edits later.

The change classifier runs in under 10ms. It inspects tool input to determine file types, languages, diff content (import-only? function signatures changed? class structure changed? formatting-only?), and cross-references against pipeline-critical paths configured per-project. The tier selector maps this classification to a lint tier, with debouncing to prevent redundant runs on rapid successive edits.

---

## The Linter Registry

Eighteen linters, all independently deployable. Each wraps one tool or analysis, yields structured `LintIssue` objects, and knows nothing about the others. The runner composes them.

| Linter | Tier | External Tool | What It Catches |
|---|---|---|---|
| `ruff_check` | 0 | ruff | Syntax errors, import issues, style violations |
| `ruff_format` | 0 | ruff | Formatting inconsistencies |
| `import_checker` | 1 | built-in | Hallucinated or broken imports (AST-based) |
| `version_checker` | 1 | built-in | Tool version mismatches against project constraints |
| `context_rule_checker` | 1 | built-in | Drift from CLAUDE.md / AGENTS.md rules |
| `redefinition_checker` | 1 | built-in | Duplicate function/class definitions at same scope |
| `mypy` | 2 | mypy | Type errors |
| `ty` | 2 | ty | Type integrity (Astral's Rust-based type checker) |
| `complexity_checker` | 2 | radon | Cyclomatic complexity, maintainability index |
| `structure_checker` | 2 | built-in | God functions, god classes, deep nesting, cognitive complexity, file bloat |
| `performance_checker` | 2 | built-in | Quadratic membership, re.compile in loops, sorted()[0], string concat in loops, unnecessary wrappers, sequential I/O, numerical loops without vectorization |
| `bandit_fast` | 2 | bandit | Focused security checks: hardcoded passwords, shell injection, weak hashing, pickle/marshal, unsafe SSL |
| `pip_audit` | 2 | pip-audit | Supply-chain vulnerabilities in installed packages (CVE/GHSA database) |
| `custom_linter` | 2 | configurable | User-defined lint commands |
| `bandit` | 3 | bandit | Full security vulnerability scan (OWASP patterns) |
| `architecture_checker` | 3 | built-in | Layer violations, circular imports, responsibility diffusion |
| `dead_code_checker` | 3 | vulture / built-in | Unused functions, classes, imports |
| `cognitive_complexity` | 3 | built-in | Understanding difficulty (distinct from cyclomatic) |

External tools are auto-detected via `shutil.which()`. If mypy is not installed, the mypy linter silently disables itself. **LintGate works out of the box with just ruff** — everything else is additive. Install more tools, get more coverage. No configuration required.

The three new Tier 2 linters (`ty`, `bandit_fast`, `pip_audit`) embody the "professional instinct" principle: they model checks that experienced engineers perform reflexively. `ty` catches type errors at Rust speed, complementing mypy. `bandit_fast` runs a focused subset of high-confidence security checks on every structural change (not just Tier 3 deep scans). `pip_audit` scans installed dependencies against vulnerability databases. All three follow the same `BaseLinter` protocol, degrade gracefully when their tools are missing, and integrate through the existing pipeline — no new MCP tools, no behavior channel changes.

Every linter implements a common protocol: `run(context) -> Iterable[LintIssue]`. Issues carry a severity (`blocking` / `warning` / `informational`), a confidence score (0.0–1.0), linter identity, file/line location, and a machine-readable kind (e.g., `F821`, `return-value`, `complexity`). This uniformity is what makes the aggregator, pattern bank, and ControlPlane possible — they all speak the same issue language.

### Performance Anti-Pattern Detection

The `performance_checker` is a pure AST linter (Tier 2, no external dependencies) that detects structurally wrong performance choices — patterns that are mathematically inferior regardless of context. These are not micro-optimizations or style preferences. A `for val in other: if val in my_list` inside a loop is O(n²) when `my_set` makes it O(n). `sorted(x)[0]` is O(n log n) when `min(x)` is O(n). `re.compile(literal)` inside a function recompiles on every call when hoisting to module level compiles once. These are facts about complexity classes, not opinions about coding style.

The 11 checks (PERF001–PERF011):

| Check | Severity | Pattern | Fix |
|-------|----------|---------|-----|
| PERF001 | warning | `x in some_list` inside loop (loop-invariant) | Convert to set |
| PERF002 | warning | `re.compile(literal)` inside function body | Hoist to module level |
| PERF003 | warning | `sorted(x)[0]` or `sorted(x)[-1]` | Use `min()`/`max()` |
| PERF004 | warning | String `+=` inside loop (3+ statements) | Use `''.join(parts)` |
| PERF005 | informational | `list(range(...))` in for-loop iteration | Remove list() wrapper |
| PERF006 | informational | `for k in d.keys()` | Use `for k in d` |
| PERF007 | informational | Arithmetic loop over large range without numpy/numba | Consider vectorization |
| PERF008 | informational | `requests.*` or variable-path `open()` in loop | Batch or parallelize |
| PERF009 | warning | Multi-pass sequence processing | Process in a single pass |
| PERF010 | warning | Unnecessary materialization of lists/dicts | Return generators/iterators |
| PERF011 | warning | Pure function called uncached in loop | Cache result or hoist |

PERF001–PERF004 and PERF009–PERF011 are severity `warning` because they are always structurally wrong. PERF005–PERF008 are `informational` because they have edge cases — the agent decides what to do with the signal.

### Algebraic Properties Bridge

Beyond simple pattern matching, the `performance_checker` integrates a deeper **Algebraic Properties Bridge**. By performing AST-based analysis without execution, LintGate statically detects fundamental properties of functions:
- **Purity**: Functions with no side effects and deterministic outputs based purely on inputs.
- **Algebraic Properties**: Commutative, Associative, Idempotent, and Bounded behaviors.

This data builds a `PropertyManifest` which powers three downstream capabilities:
1. **Hypothesis Integration**: Automatically generates property-based testing templates (`hypothesis_bridge.py`) based on detected algebraic properties.
2. **icontract Integration**: Autogenerates design-by-contract decorators (`icontract_bridge.py`) for enforcement of boundaries.
3. **Telemetry & Economics**: Performance metrics (pure functions count, properties proven) are aggregated into telemetry economics and integrated into CI workflows via Performance Badges (e.g., in `.github/workflows/algebra-badges.yml`).

False-positive guardrails are critical: PERF001 only fires when the membership container is loop-invariant (not mutated inside the loop body). PERF002 skips `@lru_cache`/`@cache` decorated functions. PERF007 requires non-trivial loop bounds and arithmetic operations on the loop variable, and skips files that already import numpy/pandas/numba.

Performance findings integrate through the existing lint pipeline — no new MCP tools, no behavior channel changes. When `performance_checker|PERF001` recurs across runs, the pattern bank fires a `PATTERN ALERT`. The constraint proposer generates rule proposals. The coherence engine naturally surfaces performance findings alongside other lint signals. This is the "lossy lenses" principle: the performance checker is one more cheap, independent signal whose agreement with complexity, structure, and test signals produces insight that no individual tool could generate alone.

Configuration:
```yaml
linters:
  performance_checker:
    enabled: true
    disabled_checks: ["PERF007"]  # stable IDs, not prose names
```

### Test Effectiveness Channel

Coverage measures *execution* — did the test runner touch this line? Mutation testing measures *effectiveness* — did the assertions actually verify the behavior? LintGate's mutation testing data revealed the gap: 59.1% kill rate (7,845 killed / 13,272 total mutants) despite 92% line coverage. The surviving mutants reveal a systematic pattern: tests execute code paths but assert on structure (existence, type) rather than semantics (exact values, boundary conditions).

Three survivor classes drove the assertion taxonomy:

1. **Positional semantics survivors**: `find` → `rfind` mutations survive because tests call the function and check `is not None` instead of checking the exact index. The assertion proves the function returns *something*, not the *right thing*.
2. **Sentinel value survivors**: `-1` → `+1` mutations survive because assertions check truthiness (`assert result`) instead of exact values (`assert result == -1`). Any non-zero value passes.
3. **Key identity survivors**: Dict key mutations survive because tests check `"key" in result` for structural shape instead of asserting specific key names.

The test effectiveness channel quantifies this gap through AST-based assertion classification. Every assertion in test files is classified into a 16-kind taxonomy with mutation-killing power scores:

| Category | Kinds | Strength | Mutation-Killing Power |
|---|---|---|---|
| **Structural** (weak) | `is_none`, `is_not_none`, `is_true`, `is_false`, `isinstance_check` | 0.2–0.3 | Low — lets value-altering mutants survive |
| **Semantic** (strong) | `equality`, `inequality`, `comparison`, `string_contains`, `collection_membership`, `dict_key_check`, `regex_match` | 0.7–1.0 | High — catches value-altering mutants |
| **Boundary** | `raises`, `length_check`, `range_check` | 0.6–0.9 | Medium-high — catches off-by-one and error-path mutations |
| **Property-based** | `hypothesis_property` | 0.8 | High — catches broad mutation classes via invariants |

Seven finding codes (TEFF001–TEFF007):

| Code | Trigger | Severity | Signal |
|---|---|---|---|
| TEFF001 | Project-level semantic ratio < 0.4 | informational | Assertion portfolio is structurally biased toward weak patterns |
| TEFF002 | Public function with 0 mapped tests | informational | Coverage blind spot — no test exercises this function at all |
| TEFF003 | Function tested with only structural assertions | warning | Tests execute but don't verify — maximum false confidence |
| TEFF004 | Function mutation vulnerability > 0.7 | warning | Most mutants would survive the current test suite |
| TEFF005 | Pure function (from PropertyManifest) AND effectiveness < 0.5 | warning | **Holographic**: composes purity + effectiveness. Maximum wasted opportunity |
| TEFF006 | Covered (from SymbolCoverage) AND vulnerability > 0.6 | informational | Coverage ≠ effectiveness — the classic gap |
| TEFF007 | Cyclomatic complexity > 10 AND semantic ratio < 0.5 | warning | Complex code needs strong assertions to catch mutation paths |

TEFF005 is the key holographic signal — the first cross-channel finding that composes the performance channel's purity analysis with this channel's effectiveness analysis. Pure functions are deterministic (same input → same output), which makes them the *easiest* functions to test with exact-value assertions. Weak tests on pure functions represent maximum wasted opportunity because the difficulty barrier is zero — you just need to compute the expected output once.

The channel's error profile is genuinely uncorrelated with existing channels:
- **Lint** catches syntax/style violations → test effectiveness catches assertion *quality*
- **Tests** report pass/fail → test effectiveness explains *why* passing tests still let mutants survive
- **Performance** detects purity/algebraic properties → test effectiveness *consumes* purity data to find wasted testing opportunity
- **Structure** detects architectural issues → test effectiveness is orthogonal to module structure

This is the "lossy lenses" admission criterion satisfied: the errors this channel catches (weak assertions on tested code) are invisible to every other channel, and the coherence engine gains a new independent signal for triangulation.

Configuration:
```yaml
controlplane:
  channels:
    test_effectiveness:
      enabled: true
      timeout_ms: 12000
  coherence_channel_weights:
    test_effectiveness: 0.6  # Higher than structure (0.4), lower than lint (1.0)
```

MCP tools:
- `analyze_test_strength(path)` — project-wide effectiveness report with top vulnerable functions and assertion upgrade suggestions
- `inspect_test_assertions(path, test_file)` — per-assertion drill-down into a single test file

### Mutation Testing Engine

The mutation subsystem provides 10 MCP tools for inline AST mutation analysis. It generates mutants across 5 semantic categories (VALUE, SWAP, STATE, BOUNDARY, TYPE), evaluates them against existing tests, and produces survival profiles that reveal specification gaps.

**Two-tier execution model**:
- `mutation_run_sampling` — fast sampled run (<=3 mutants per category, time-budgeted). Use after editing files for quick feedback.
- `mutation_run_full` — exhaustive profiling with full kill matrix, budget semantics (`budget_ms`, `per_mutant_timeout_ms`), and per-function budget splitting. Returns survivor records, discovery/topology diagnostics, and trajectory analysis. Hard circuit breaker at 10 minutes.

**Runtime safety (PR0)**: Outer wall-clock budget is separated from per-mutant timeout. Fallback test loading is capped at 50 tests. Multi-function runs split budget per function. All mutation tools have a hard circuit breaker and return partial results on timeout.

**Discovery truthfulness (PR1)**: Every mutation result includes `discovery_state` (NO_TEST_FILES, TEST_FILES_FOUND_NONE_LINKED, TESTS_LINKED_ZERO_KILLS, DISCOVERY_IMPORT_FAILED, DISCOVERY_OK), `topology_state` (NORMAL, MOCK_BOUNDARY_DOMINANT, PATCHED_INTERNAL_CALLS, TOPOLOGY_UNKNOWN), and `survival_interpretation` (MEANINGFUL, DISCOVERY_ARTIFACT, MOCK_BOUNDARY_ARTIFACT, LOW_CONFIDENCE).

**Survivor records (PR2)**: Each mutant has a stable `mutant_id` and produces a survivor/killed record with `diff_summary` (via `ast.unparse`), enabling grounded prescriptions downstream.

**Prescription pipeline (PR3)**: `mutation_prescribe` uses survivor records for witness-based prescriptions (`why_this_matters`, `suggested_input`, `assertion_shape`, `confidence`, `source_of_evidence`). Falls back to category templates when survivor data is unavailable. `mutation_prescribe_tests` generates pytest skeletons. `mutation_validate_tests` uses sampled re-profiling with budget splitting.

**Trajectory analysis (PR4)**: Post-profiling analysis computes teaching-set upper bounds, tail onset detection, and phase classification (bulk/transition/tail/complete) via `trajectory_analysis.py`. All greedy quantities use explicit upper-bound language.

**Cross-lens decomposition (PR6)**: `mutation_decompose` requires multi-lens agreement (mutation + specification + composition gap) before recommending EXTRACT_BOUNDARY. Single-lens mutation evidence returns KEEP_TESTING. Mock-dominant topology blocks extraction. Supports three modes: `auto` (merged static + dynamic), `static` (AST-only, no test data needed), `dynamic` (mutation-data-driven).

| Tool | Purpose |
|---|---|
| `mutation_run_sampling(path, file, function?, budget_ms?)` | Fast sampled mutation run — inline AST mutation sampling |
| `mutation_run_full(path, file, function?, budget_ms?, per_mutant_timeout_ms?)` | Deep exhaustive mutation profiling with survivor records, trajectory analysis, and discovery/topology diagnostics |
| `mutation_get_state(path, file?, function?)` | View cached mutation state and metrics |
| `mutation_prescribe(path, file?, function?)` | Deterministic prescriptions from survival profiles |
| `mutation_decompose(path, file?, function?, mode?)` | Find entangled functions (auto/static/dynamic) |
| `mutation_refactor_loop(path, file?, function?)` | Re-profile after test improvement |
| `mutation_prescribe_tests(path, file?, function?)` | Generate test skeletons from mutation profiles |
| `mutation_validate_tests(path, file?, function?)` | Re-profile and compute survival deltas |
| `mutation_clear_state(path, file?)` | Clear stale mutation state |
| `spec_improve(path, file, function?, budget_ms?)` | One-shot spec improvement: diagnose → sample → prescribe in one call |

**Integration with specification pipeline**: Mutation tools compose with specification tools (`spec_file_analyze`, `spec_prescribe`, `spec_gate_check`) in a closed-loop pipeline:

```
spec_file_analyze → mutation_run_sampling → mutation_run_full → mutation_prescribe
    → mutation_prescribe_tests → [write tests] → mutation_validate_tests → spec_gate_check
```

Each tool's `next_actions` output suggests the next step in the pipeline.

**Purity-aware filtering**: `filter_categories(func_node, is_pure=True)` excludes STATE mutations for pure functions. Purity is detected via `analyze_purity()` from the algebra pipeline and passed through `MutationContext.purity_map`.

**Real test selection**: Tests are loaded dynamically via `build_test_impact_map(test_files)` (AST-based static mapping from source functions to test functions) and `load_test_callables()` (dynamic import). Kill/survive results reflect actual test assertions, not theoretical coverage.

**Post-profiling analysis** (automatic in `mutation_run_full`):
- **Greedy convergence (Thm 3.2)**: `analyze_convergence(profiling_result, sigma)` validates each test adds ≥1/σ specification. Results in `analysis.convergence`.
- **Symmetry regime classification (Thm 4.1)**: `classify_regime_from_mutations(pr, sigma, is_pure, param_count)` uses kill-set equivalence classes for empirical regime determination. Results in `analysis.symmetry`.

**Cross-run trajectory**: The specification ledger accepts `prior_ledger` and calls `update_trajectory()` / `detect_phase_from_trajectory()` to accumulate ΔK across mutation→validate cycles, driving phase detection (bulk → transition → tail → complete).

The original Tier 2 auto-scheduling implementation has been archived under `archive/mutation_system/`.

### Cold-Start Bootstrap Pipeline

The mutation→decompose→refactor pipeline is powerful but silent for exactly the projects that need guidance most: those with zero tests. `mutation_decompose` returns 0 candidates without test data. The bootstrap pipeline breaks this chicken-and-egg problem by generating deterministic test scaffolding from AST analysis alone — no LLM calls, fully resumable.

**The problem**: A project with no tests has no mutation data, so mutation-based prescription is unavailable. But the project's AST still contains extractable signals: type annotations, loop patterns, exception handlers, pure function signatures. The bootstrap pipeline harvests these signals into test scaffolding that gives the mutation engine something to work with.

**Pipeline phases** (ordered, resumable, idempotent):

```
Phase 1: Algebra Survey
  │  Run PropertyManifest to identify pure functions,
  │  algebraic properties, extraction safety
  │
Phase 2: Skeleton Generation
  │  Generate pytest skeletons for each source file
  │  via AST analysis (existing skeleton_generator)
  │
Phase 3: Property Tests
  │  For pure functions with algebraic properties,
  │  generate Hypothesis test templates
  │
Phase 4: Behavioral Contracts
  │  Deterministic contract generation from AST patterns:
  │  - Return type assertions (from annotations)
  │  - Shape preservation (map/filter loop patterns)
  │  - Error boundary defaults (except → return [])
  │  - I/O stub markers (subprocess/HTTP/file calls)
  │
Phase 5: Mutation Sampling
  │  Run inline mutation sampling against generated tests
  │  to produce initial survival data
  │
Phase 6: Complete
     mutation_decompose now has data to work with
```

**Execution model**:
- **Lock-guarded**: PID-based lock file with stale detection (>120s or dead PID). Only one pipeline runs per project.
- **Heartbeat**: Long phases call `heartbeat()` to prevent stale lock false positives.
- **Resumable**: State persists to `~/.claude/lintgate/bootstrap/{project_hash}.json`. Interrupted runs continue from the last completed phase.
- **Incremental**: Per-file tracking in `files_processed` — individual files are not re-processed on resume.
- **Isolated output**: Generated tests go into `tests/generated/` with header comment `# Auto-generated by LintGate bootstrap — review before relying on`. No edits to existing test files.

**Behavioral contracts** are the most novel component. Four deterministic strategies extract testable properties from the AST without any LLM inference:

1. **Return type contracts**: When a function has a return type annotation, generate `assert isinstance(result, T)`. Maps annotations to isinstance-compatible types.
2. **Shape preservation**: Detects the map pattern (`result = []; for x in items: result.append(...)`) and generates `assert len(result) == len(input)`.
3. **Error boundary contracts**: Detects `except: return []` patterns and generates `assert func(bad_input) == []` — verifying the documented default.
4. **I/O stub markers**: Detects calls to subprocess, requests, file I/O and emits TODO stubs with suggested mock patterns. These are advisory only — not executable assertions.

**Integration with test channel**: The test channel emits `BOOTSTRAP_TRIGGERED` (informational) when it detects zero test files. This signal is picked up by `controlplane_run`, which can auto-launch the bootstrap pipeline. The `bootstrap_tests` MCP tool provides manual control.

Configuration:
```yaml
controlplane:
  channels:
    tests:
      settings:
        bootstrap_on_zero_tests: true
        bootstrap_output_dir: "tests/generated"
```

### Common Prescription Schema

All prescriptive subsystems — lint, structure, mutation, and bootstrap — emit a shared `Prescription` dataclass. This prevents diagnosis-to-prescription from staying fragmented across subsystems:

```python
@dataclass
class Prescription:
    kind: str              # "extract_function" | "split_file" | "add_test" | "fix_import" | ...
    target: str            # file::function or file path
    action: str            # human-readable description
    lines: tuple[int, int] | None  # affected line range
    proposed_name: str | None      # suggested name for extracted entity
    inputs: list[str]      # parameters / shared variables
    outputs: list[str]     # return values / side effects
    confidence: float      # 0.0–1.0
    source: str            # "static" | "dynamic" | "lint" | "bootstrap"
    basis: list[str]       # evidence keys: ["variable_clustering", "purity_analysis", ...]
    expected_delta: dict   # {"cc_reduction": 15, "survival_reduction": 0.3, ...}
```

The `source` field differentiates provenance: `"static"` prescriptions come from AST analysis (always available), `"dynamic"` from mutation data (requires tests), `"lint"` from linter findings, and `"bootstrap"` from the cold-start pipeline. Static prescriptions carry lower confidence than empirically validated dynamic ones. When both exist for the same function, convergence boosts confidence; divergence flags the function for investigation.

---

## Anti-Drift Systems

The core problem LintGate was built to solve is not "find syntax errors." Ruff does that fine on its own. The problem is **drift** — the slow, invisible divergence between what the developer intended and what the agent is actually building.

LintGate attacks code drift at three levels (with behavioral drift handled by the [behavior channel](#behavioral-drift-detection) and understanding drift by the [project compass](#project-compass-4-axis-understanding)):

### Issue Memory

Every issue signature (`linter + kind + file + line`) is persisted across runs. When the agent reintroduces a previously-seen error, the report says so. This is the first line of defense against **tail-chasing**: the agent fixing a bug in one place while reintroducing it in another.

Stored per-project in `~/.claude/lintgate/`.

### Pattern Bank

Categorical tracking, not instance tracking. The pattern bank records `linter + kind` frequencies across runs, ignoring file and line. When the same *category* of mistake recurs — say, `ruff/F821` (undefined name) appearing in four of the last five runs — the agent gets a `PATTERN ALERT`.

This is not a blocking error. It is a signal that the agent has a **systematic** problem, not a one-off typo.

The pattern bank stores rolling history on disk at `~/.claude/lintgate/pattern_bank/` and survives across sessions. It also tracks a global run history to correctly distinguish "this pattern appeared in 3 of last 5 runs" from "this pattern appeared in 3 consecutive runs with no clean runs between" — a subtlety that prevents false alerts.

Design decision: **alert-only, never auto-escalate severity**. Auto-escalation creates noisy hard-fail loops on style codes. The pattern bank informs; the agent decides.

### Context Rule Enforcement

CLAUDE.md and AGENTS.md files contain project-specific rules — both natural language directives ("DO NOT create task-specific functions") and machine-readable rules (`LINTGATE_FORBID_REGEX: def\s+solve_task_`). The context rule checker enforces these on every edit.

If the agent writes code that violates a project directive, the violation appears in the lint report with a reference back to the source rule. This is drift detection at the *intent* level — not "is this syntactically valid?" but "is this aligned with what the project is trying to be?"

---

## Context File Management

Beyond enforcing rules, LintGate provides tools for managing the quality of context files themselves — because a bad context file produces bad agent behavior just as reliably as a bad import produces a runtime error.

### Context Health Auditor

Six checks against LLM context file best practices:

| Check | What It Catches |
|---|---|
| **Length** | Files beyond useful size (configurable; default warn at 300 lines, error at 500) |
| **Structure** | Missing section headers, poor organization |
| **Staleness** | Files not updated recently (configurable; default 30 days) |
| **Contradictions** | Conflicting DO and DO NOT directives about the same concept |
| **Rule Coverage** | Percentage of DO NOT directives with machine-enforceable `LINTGATE_*` rules |
| **Path References** | File paths mentioned in backticks that don't exist on disk |

The auditor synthesizes best practices from Claude Code documentation, the AGENTS.md cross-tool standard (25+ platforms), and research on context rot — all models degrade with input length, so keeping context files concise and current is a first-order concern.

### Theory Constraint Extraction

The constraint extractor bridges the gap between prose directives and machine enforcement. It scans CLAUDE.md/AGENTS.md for `DO NOT` and `MUST` directives and proposes concrete `LINTGATE_FORBID_REGEX` / `LINTGATE_REQUIRE_REGEX` rules, deduplicating against existing rules. Each proposal includes the source directive, a confidence score, and a copy-paste-ready line.

### Context Bootstrap (Authoring)

`bootstrap_context_files` generates best-practice context-file drafts by composing up to four signals:

1. Existing context directives and machine rules (`context_guidance`)
2. Context quality diagnostics (`audit_context_health`)
3. Theory summaries and enforceable-rule proposals (`build_theory_pack` + `extract_project_theory`)
4. Model-specific behavioral profile (when `model_id` is provided)

The tool drafts concise `CLAUDE.md` and `AGENTS.md` files (plus optional `.claude/rules/theory.md`) designed for agent consumption: high-signal directives, explicit quality gates, and machine-enforceable rules at the top. It defaults to dry-run output and only writes files when `write=true`.

When a model profile is available and usable (confidence ≥ 0.55, not stale), the bootstrap tool injects model-biased guardrails into the Guardrails section and replaces generic anti-patterns with model-specific ones in the `do_dont` managed section. The `source_signals` in the response reports `model_profile_applied`, `model_key`, and `model_profile_confidence` for transparency.

**Zero-state defaults.** When no theory claims exist and no model profile is provided, the bootstrap tool uses battle-tested defaults: 7 curated anti-patterns and 4 facet fallbacks distilled from field experience. These are not placeholders — they encode the lessons from hundreds of real sessions (approach cycling, serial discovery, failure amnesia, root-cause clustering, bounded checklists, layered signal composition, performance anti-patterns). Project-extracted theory replaces them where available; model-specific anti-patterns take precedence over both.

---

## Theory Extraction

This is the most ambitious piece of LintGate.

Most projects have **theory** scattered across their documentation: design docs, research notes, architecture decisions, READMEs at various directory levels, handoff documents. This theory describes how the project thinks about its problem domain, what approaches are considered aligned versus misaligned, and *why* the system is designed the way it is. An agent that understands this theory makes better decisions. An agent that does not will cheerfully write code that is syntactically correct but **conceptually wrong**.

The theory extractor scans **all** markdown documents in a codebase — not just CLAUDE.md — and builds a structured profile of the project's conceptual space.

### The Six Facets

| Facet | What It Captures |
|---|---|
| **Core Theory** | Foundational concepts, definitions, hypotheses, key insights |
| **Problem-Solving Approach** | Heuristics, strategies, preferred methods, lessons learned |
| **Alignment Criteria** | What "good" looks like — contrastive examples, non-goals, scope |
| **Architectural Philosophy** | Design rationale, rejected alternatives, invariants, tradeoffs |
| **Anti-Patterns** | Conceptual violations — not code style, but approaches that undermine the project's theory |
| **Key Abstractions** | Named mechanisms, domain vocabulary, theoretical constructs |

Enforceable rules (`DO NOT` / `MUST` directives) are extracted via a separate path and converted to `LINTGATE_FORBID_REGEX` / `LINTGATE_REQUIRE_REGEX` rules.

### How It Works

The extraction is **deterministic and rule-based** — no LLM token cost. It works by:

1. **Document discovery**: Scans all `.md` files in the project (respecting skip dirs), with priority for `.claude/rules/` which is first-class theory content.

2. **Section parsing**: Splits each document into headed sections (by markdown `#` headers).

3. **Section classification**: Each section is classified into zero or more theory facets using two layers of signals:
   - **Heading-level signals**: Regex patterns on section headings (e.g., "approach", "anti-pattern", "design rationale")
   - **Paragraph-level signals**: Compiled regex patterns on section body text (e.g., causal reasoning markers, contrastive language, importance vocabulary)
   - **Contrastive markers**: Dedicated detection for alignment criteria (WRONG vs CORRECT patterns, "instead of" / "rather than" language)

4. **Claim extraction**: From classified sections, individual sentences are scored for theory content using facet-specific scoring functions that weight causal reasoning, contrastive reasoning, importance markers, and mechanistic language. Only sentences scoring above threshold become claims.

5. **Deduplication**: Claims are deduplicated within each facet by normalized text similarity.

6. **Validity reporting**: The extractor produces quality diagnostics — which required facets are missing, claim density per document, traceability percentage (how many claims trace to source), and actionable recommendations for improving theory coverage.

### Two-Tier Delivery

The theory system is designed for **token-efficient** agent consumption:

**Tier 1: Theory Pack** (`build_theory_pack`) — A compact digest (~500–1500 tokens) designed for system prompt injection. Follows "lost in the middle" mitigation: enforceable rules at the top (highest value), facet summaries in the middle (compressed context), anti-pattern list at the end (high attention zone). Benefits from prompt caching (90% cost reduction on Anthropic) and lands in the high-attention start-of-context zone.

**Tier 2: On-Demand Retrieval** (`get_theory_context`) — Full claim text retrievable by facet and/or keyword when the agent needs deeper reasoning about a specific violation or design decision.

---

## Professional Instinct Layer

Beyond code quality linting, LintGate models the reflexive checks that distinguish senior from junior engineers — checks that are not about intelligence but about discipline. An experienced engineer doesn't think about checking for secrets before committing or verifying the venv is active before installing packages. These are professional instincts, performed reflexively, and their absence in LLM agents leads to costly environmental failures that compound across sessions.

The professional instinct layer operates at two levels:

### Hygiene Prechecks (Command-Class Awareness)

Every planned action is classified into a command class, and domain-specific preconditions are checked before execution. This is exposed via `hygiene_check` (and available through the deprecated `behavior_precheck` wrapper):

| Command Class | Patterns | Checks |
|---|---|---|
| `pip_install` | `pip install`, `uv install`, `uv add` | venv active, lockfile exists, versions pinned |
| `git_commit` | `git commit`, `git push` | no secrets in staged diff, lockfile fresh |
| `env_edit` | `.env`, `export VAR=` | `.env` covered by `.gitignore` |
| `publish_build` | `python -m build`, `twine upload`, `uv publish` | working tree clean, lockfile fresh |

Each check is fast (<50ms), emits machine-verifiable evidence, and degrades gracefully when tools or state are unavailable. Unrecognized commands pass through with no warnings — the system only speaks when it has something worth saying.

Implementation: `lintgate/hygiene.py`. Each check function returns `HygieneWarning | None` with confidence (0.0–1.0), evidence dict, and actionability level (immediate / investigate / advisory).

### Secrets-in-Diff Scanning

The git channel scans staged diffs for high-confidence secret patterns before every commit. This runs automatically in `controlplane_run` (git channel, Check 4), and can be invoked proactively with `hygiene_check` for git_commit/git_push command classes.

Seven regex patterns scan only `+` lines (additions) in `git diff --cached`:
- AWS access keys (`AKIA[0-9A-Z]{16}`)
- Private keys (RSA, EC, DSA, OPENSSH)
- GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`)
- GitLab tokens (`glpat-`)
- Connection strings (postgres, mysql, mongodb, redis with credentials)
- Generic API keys and secrets (16+ character values)

Critical design decision: **secret values are never included in messages or evidence**. Findings report only the pattern name, file path, and line range. This prevents the monitoring system itself from becoming a secret-leaking vector.

All findings carry `confidence = 0.85` (regex-based, possible false positives) and `severity = "warning"`. When secrets are found in a ControlPlane run, the git channel's overall severity escalates to warning level.

### Supply-Chain Integrity

The `pip_audit` linter scans installed packages against vulnerability databases. Every dependency with a known CVE or GHSA advisory produces a finding with:
- The vulnerable package name and installed version
- The vulnerability ID (CVE/GHSA)
- Fix versions (when available)
- Severity: CVE/GHSA IDs → warning, other IDs → informational
- Confidence: 1.0 (database-backed, deterministic)

This runs at Tier 2, meaning every structural code change triggers a dependency vulnerability check. The linter degrades gracefully when `pip-audit` is not installed.

### Dependency Discipline

The dependency health system (`lintgate/dependency_health.py`) uses convergent evidence to escalate severity:
- Missing `.python-version` is informational by default, but escalates to error when CI config is present (the combination of "no pinned Python version" + "automated builds exist" is a reproducibility risk)
- Conflicting package managers (e.g., both `poetry.lock` and `Pipfile.lock` present) escalates to error when both are lockfiles (not just manifests)
- Manifest quality checks: missing `requires-python`, unpinned core dependencies

---

## ControlPlane: The Supervision Mesh

The ControlPlane is what happens when you ask: "What if the linter isn't enough? What if you need to supervise the agent across *multiple domains simultaneously* — code quality, test health, dependency integrity, git hygiene — and the real diagnostic signal is in the *agreement or disagreement* between those domains?"

### Architecture

The ControlPlane replaces the 5-phase pipeline with a parallel supervision mesh (6 channels):

```
SupervisionEvent
  │
  ├─ Channel: Lint
  │    Runs the full linter pipeline (tiers, aggregation, pattern bank)
  │    Produces LintIssue findings + pattern alerts
  │
  ├─ Channel: Tests
  │    Discovers and runs relevant tests for changed files
  │    Test archetype selection (unit, integration, smoke)
  │    Skeleton generation for untested files
  │
  ├─ Channel: Dependencies
  │    Venv health, lockfile freshness, conflicting managers
  │    Global install detection, churn tracking
  │
  ├─ Channel: Git
  │    Uncommitted changes, branch hygiene
  │    Conflict detection, large file warnings
  │    Secrets-in-diff scanning (staged content, 7 regex patterns)
  │
  ├─ Channel: Behavior
  │    Agent problem-solving strategy supervision
  │    9 detection rules with intent-aware confidence biasing
  │    Hard: approach cycling, failure amnesia, brute-force escalation
  │    Soft: premature action, serial discovery, tool repetition,
  │          verification debt, stale model
  │    Signal coordinator: per-signal cooldown, nudge dedup, escalation
  │    Advisory only — weather-style observations, not judgments
  │
  ├─ Channel: Structure
  │    Codebase architectural analysis (AST-based, symbolic only)
  │    STRUCT001: Import cycle detection (DFS, max depth 5)
  │    STRUCT002: Module-size distribution (p90/p50 ratio, quantile thresholds)
  │    STRUCT003: Orphan detection (excludes entrypoints, migrations, tests, plugins)
  │    STRUCT004: Package cohesion (intra- vs. inter-package import ratio)
  │    Emits structure snapshot for cheap orientation in compact output
  │    Advisory only — informational unless corroborated by other channels
  │
  └─ Coherence Engine
       Cross-channel diagnosis from multi-signal agreement
       5 coherence states: stable → isolated → coupled → systemic → degraded
       "Silent channels provide confident exclusion" (Monty Hall principle)
```

### The Coherence Engine

This is the novel piece — where the ControlPlane becomes more than the sum of its channels, and where the "lossy lenses" principle pays off.

Each channel, individually, is a bad instrument. The lint channel can't tell you whether your architecture is sound — it can only tell you that `ruff` found three undefined names. The test channel can't tell you whether your dependencies are healthy — it can only tell you that two tests failed. The dependency channel knows nothing about code quality. The git channel knows nothing about test health. The structure channel knows nothing about runtime behavior — only static module relationships. The behavior channel knows nothing about syntax — it only knows that the agent has tried three different approaches in twenty minutes and all of them failed.

But **the pattern of agreement and disagreement between them is a higher-order signal that none of them could produce alone.** When lint finds undefined names, tests fail on import errors, and the dependency channel reports a missing package — that's not three independent problems, that's one problem (a broken dependency) diagnosed from three angles. When lint is clean but tests fail, the problem is in test logic, not code quality. When everything is clean except git (large uncommitted diff), the code is fine but the workflow isn't. And when the behavior channel reports approach cycling while lint and tests are both failing — the agent isn't just writing bad code, it's *stuck*, and the right response is to step back and rethink, not to try a fifth variant of the same approach.

This is how a senior engineer thinks. They don't run each check independently and read the results in isolation. They *triangulate*. LintGate's coherence engine does the same thing, cheaply, with simple symbolic tools — and the insight is borrowed from OTP/Erlang supervision theory: **silence IS diagnostic information**. When three channels pass and one fails, the three passing channels are not just "clean" — they are actively concentrating your attention on the single failure. This is the Monty Hall effect applied to system diagnosis.

Five coherence states:

| State | Condition | What It Means |
|---|---|---|
| **stable** | All channels pass or skip | System is healthy. Continue. |
| **isolated** | Exactly one failure, 2+ passes | Problem is localized. Silent channels confirm other domains are clean. Focus on the failing one. |
| **coupled** | Two+ failures, overlapping files | Failures are related. Address shared files first. |
| **systemic** | Three+ failures, or infrastructure + code channels both failing | Structural problem. Step back and review the overall approach. |
| **degraded** | Any channel errors/timeouts | System health concern. Results may be incomplete. |

### Trajectory-Aware Coherence

With session memory enabled, the coherence engine enriches its diagnosis with history:

- **REGRESSION**: Coherence state worsened from the previous run (e.g., `isolated → coupled`)
- **PERSISTENT**: Same channel has been failing for 3+ consecutive runs — suggests the agent needs a different approach
- **RESOLUTION**: Previously-loud channel is now silent — positive signal, the fix worked

### Session Memory

Cross-run state that persists within an agent session (configurable TTL, default 4 hours). Tracks:

- **Coherence trajectory**: The sequence of coherence states across runs
- **Repair outcomes**: Proposed fixes tracked from `pending` → `applied` / `ignored` / `rejected`
- **Pattern trends**: Per-pattern count history for the constraint proposer
- **Agent disagreements**: Logged when the agent rejects a finding via `controlplane_agent_feedback`
- **Behavioral compass**: The agent's live hypothesis set, approach history, coverage metrics, and action timeline (see [Behavioral Drift Detection](#behavioral-drift-detection))

### Constraint Proposer

This is the feedback loop that closes the circle: **findings → pattern bank → constraint proposals → theory rules → reduced future findings**.

When the pattern bank detects a recurring anti-pattern (same `linter|kind` appearing across multiple runs above a configurable threshold), the constraint proposer translates that observation into an enforceable rule:

- **`LINTGATE_FORBID_REGEX`** — Ban the code pattern causing recurring errors
- **`LINTGATE_REQUIRE_REGEX`** — Require patterns that prevent recurring issues
- **Theory notes** — Advisory observations for CLAUDE.md refinement

The proposer also promotes recurring *behavioral* findings. When the behavior channel's signals — `approach_cycling`, `failure_amnesia`, `brute_force_escalation`, `verification_debt`, or `stale_model` — appear across multiple session snapshots, the proposer generates theory-note constraints. These range from "enumerate constraints before attempting new approach" (approach cycling) to "verify downstream acceptance before long build sequences" (verification debt) to "update constraint model between approach changes" (stale model). Behavioral constraints flow through the same acceptance pipeline as code constraints.

Proposals are stored in session memory with confidence scores and are **never auto-applied** — the agent or user must explicitly accept or reject them via the `controlplane_agent_feedback` MCP tool. Accepted constraints flow back into the theory extractor's rule set.

### Structure Channel

The structure channel provides AST-based codebase architectural analysis. It models a professional instinct that the other channels cannot: **a senior engineer carries a mental model of the codebase's shape** — where the import graph tangles, which modules accumulate too much responsibility, which files nobody references anymore. The structure channel provides that awareness cheaply and deterministically.

Four checks with explicit finding codes:

| Code | Check | Signal | Evidence |
|------|-------|--------|----------|
| STRUCT001 | Import cycles | DFS cycle detection, max depth 5 | `{cycle: [mod1, mod2, ...], length: N}` |
| STRUCT002 | Module-size skew | Quantile-based: p90/p50 ratio, min sample guards | `{p50_loc, p90_loc, ratio, outliers: [...]}` |
| STRUCT003 | Orphan detection | Modules not imported by any other module | `{module, file}` |
| STRUCT004 | Package cohesion | Intra- vs. inter-package import ratio | `{intra_imports, inter_imports, cohesion_ratio}` |

Design decisions:

- **Symbolic only**: AST-based import extraction, no ML, no external services. Deterministic and fully offline. Reuses the existing `architecture_checks._helpers` for import extraction and module resolution.
- **Quantile-based thresholds**: Module-size analysis uses p90/p50 ratio (default 5.0x) rather than absolute thresholds. Minimum sample-size guards (5 files) prevent noisy findings on small projects.
- **Hardened false-positive handling**: Orphan detection excludes entrypoints (`__main__`, `cli`, `server`, `app`, `main`), migration directories, test files/directories, `__init__.py`, plugin/discovery patterns, and files with shebangs. Orphan findings carry lower confidence (0.6) reflecting the many valid reasons a module might not be imported.
- **Informational severity**: All findings are informational unless corroborated by other channels. This is a deliberate design choice — structural findings are advisory, and severity escalation happens in the coherence engine when multiple channels point at the same files.
- **Structure snapshot**: Every `controlplane_run` with the structure channel produces a compact snapshot in the metrics: file count, total LOC, median module LOC, largest modules, package distribution, cycle/orphan/low-cohesion counts. This is the cheap orientation that saves 500-2000 tokens of exploratory file reads.

The structure channel's error profile is genuinely uncorrelated with the other channels — import cycles are invisible to ruff, module-size skew is invisible to tests, orphans are invisible to the dependency checker. This is the lossy-lenses admission criterion: the errors it catches are different in kind from what existing channels catch, and the coherence engine benefits from having another independent signal to triangulate with.

#### Co-Change Coupling Annotations

When the structure checker detects a file-too-long finding with cohesion-based split proposals, the proposals are annotated with git co-change coupling data. This is evidence for structure proposals only — not a standalone finding, not a severity modifier.

The `cochange_analysis` module runs `git log --name-only` over a configurable window (default 30 days) and computes Jaccard-like coupling strength between file pairs:

```
coupling_strength = cochange_count / (commits_a + commits_b - cochange_count)
```

Each split proposal receives a `cochange_annotation` with one of four statuses:

| Status | Coupling | Meaning |
|--------|----------|---------|
| `corroborated` | < 0.2 | Structurally separable and historically independent. Split is well-supported. |
| `neutral` | 0.2–0.6 | Moderate historical coupling. Split may be fine. |
| `caution` | > 0.6 | Structurally separable but historically coupled. Split may increase cross-file change frequency. |
| `no_data` | — | No co-change data available (non-git project, no history, or no Python co-changes). |

Design decisions:
- **Not a standalone finding**: Co-change data annotates existing split proposals. It does not generate STRUCT findings on its own.
- **Graceful degradation**: If git is unavailable, the project is not a git repo, or the subprocess times out (15s), proposals are returned unannotated.
- **Python-only pairing**: Only `.py` files participate in co-change counting. Non-Python files are tracked per-file for commit counts but not for pair coupling.

Implementation: `lintgate/linters/structure_checks/cochange_analysis.py`, integrated via `file_checks.py` → `_annotate_with_cochange()`.

#### AST Function Cache

The structure checker uses a session-scoped function analysis cache to avoid re-analyzing unchanged functions on repeated runs. The cache is keyed by function source hash (SHA256 of `ast.dump()` output) and invalidated when file-level imports change.

Cache lifecycle:
1. On file analysis, compute `import_hash` from all import nodes in the file
2. For each function, compute `func_hash` from the function's AST dump
3. **Cache hit**: If `(filepath, func_name, func_hash)` exists and the file's import hash hasn't changed, replay cached `LintIssue` objects
4. **Cache miss**: Run full analysis, store results, yield issues
5. **Invalidation**: Import hash change → all functions in that file are invalidated

Design decisions:
- **Session-scoped**: The cache is a class-level attribute on `StructureChecker`, shared across all runs within the same process. No cross-session persistence.
- **Import-aware invalidation**: A function's analysis may depend on imported names (e.g., type annotations, base classes). Changing imports invalidates all functions in the file.
- **LRU eviction**: Maximum 500 entries per file (configurable). Eviction is oldest-first within a file.
- **Staleness**: Cached entries older than 1 hour are evicted on access.

Implementation: `lintgate/linters/structure_checks/ast_cache.py`, integrated into `structure_checker.py` → `StructureChecker._check_file()`.

### Import Tracing and E402 Evidence

The import tracing subsystem provides transitive dependency analysis for import-placement findings (E402). When ruff reports an E402 violation, the structure checker attaches evidence about the import's dependency chain without modifying the finding's severity.

The `import_tracing` module provides:

1. **Stdlib detection**: Uses `sys.stdlib_module_names` (Python 3.10+) with a fallback set for earlier versions. Top-level module extraction handles dotted imports (`os.path` → `os`).

2. **Transitive import resolution**: Walks the import graph from a module to discover non-stdlib transitive dependencies. Bounded by depth limit (default 5) and visited-set to prevent cycles.

3. **Lazy import classification**: Detects imports guarded by patterns that make placement intentional:
   - `try/except ImportError` (optional dependency)
   - Inside function body (deferred loading)
   - `if TYPE_CHECKING:` block (type-only)
   - Inside conditional (`if`/`else`) blocks

4. **Module-level I/O detection**: Scans for calls to `subprocess`, `requests`, `urllib`, file operations, and other I/O at module scope. Presence of module-level I/O suggests import ordering has semantic significance.

The `build_e402_evidence()` function produces a structured evidence dict:

```python
{
    "is_stdlib": bool,
    "non_stdlib_deps": ["dep1", "dep2"],
    "lazy_imports": [{"module": "...", "guardian": "try_except", "line": N}],
    "has_module_level_io": bool,
    "io_calls": ["subprocess.run", "open"],
    "semantic_placement": bool,  # True when placement appears intentional
}
```

Design decisions:
- **Evidence only, no severity promotion**: E402 severity stays as ruff reports it. The evidence informs the agent's decision about whether to refactor.
- **Semantic placement flag**: Set when lazy imports or module-level I/O suggest the import's position is intentional, not accidental.
- **Graceful degradation**: If tracing fails (missing files, parse errors, import cycles), evidence falls back to `{"code": "E402", "note": "could not resolve module name"}`.

Implementation: `lintgate/linters/structure_checks/import_tracing.py`, integrated into `ruff_linter.py` → `_build_e402_evidence_safe()`.

### Decomposition Coordinator

The `DecompositionCoordinator` merges static (AST-based) and dynamic (mutation-based) decomposition candidates into a unified view. It solves the cold-start problem: projects without test data can still receive decomposition guidance from static analysis, while projects with mutation data get empirically validated candidates.

Three modes:

| Mode | Behavior |
|------|----------|
| `"auto"` (default) | Always run both static and dynamic. Merge results. |
| `"static"` | AST-only candidates (always available, lower confidence). |
| `"dynamic"` | Mutation-only candidates (requires test data). |

Merge rules:
- Dynamic candidates take priority for the same function
- If both static and dynamic exist for the same function → `source="converged"`, confidence boosted by +0.10 (capped at 0.95)
- Static-only candidates are kept with their original confidence
- Evidence lists are merged (deduplicated)

The `DecompositionCandidate` dataclass carries explicit provenance:

```python
source: str    # "static" | "dynamic" | "converged"
confidence: float
evidence: list[str]   # ["variable_clustering", "mutation_survival", ...]
```

Static candidates come from `get_static_candidates()`, which scans Python files for high-CC functions and runs variable dependency clustering to propose extractions. Dynamic candidates come from `DecompositionDetector`, which identifies functions with high mutation survival rates across multiple semantic categories.

Exposed via `mutation_decompose(mode="auto"|"static"|"dynamic")`.

Implementation: `lintgate/mutation/decomposition.py` (`DecompositionCoordinator`, `merge_candidates`, `get_static_candidates`).

### Coherence Bootstrap Awareness

The coherence engine's classification now accounts for zero-test projects. When the test channel reports `bootstrap_needed` (zero test files detected), the coherence classification includes an annotation:

```
tests: pass with caveat — no test files exist. Bootstrap pipeline [running|available].
```

This ensures "tests pass" is not misinterpreted as "tests are healthy." The annotation is informational and does not change the coherence state classification itself. If the bootstrap pipeline is actively running, the annotation reflects the current phase.

The compact reporter also surfaces bootstrap progress when available:

```
⚙ Bootstrap: Phase 3/7 — property tests (12/33 files processed)
```

Next-actions suggest `bootstrap_tests(path)` for idle pipelines or `bootstrap_status(path)` for running/failed ones.

Implementation: `lintgate/controlplane/coherence.py` (`_detect_bootstrap_needed`), `lintgate/controlplane/reporter_compact.py` (`_build_bootstrap_progress`).

---

## Behavioral Drift Detection

Code drift is when the agent writes the wrong code. Behavioral drift is when the agent *reasons* wrong — trying failed approaches repeatedly, ignoring error messages it saw ten minutes ago, acting faster than it understands, or brute-forcing solutions without building a model of what constraints actually apply. Behavioral drift is upstream of code drift: an agent stuck in a bad reasoning loop will produce bad code until its strategy changes, regardless of how many lint errors you report.

LintGate's behavioral drift detection works through three components within a session, plus an optional cross-session learning layer: a **Behavior Channel** (reactive, part of the ControlPlane mesh) that watches tool-use patterns for anti-patterns, an **intent bias layer** (structural, threaded through the channel) that classifies every tool use into a 6-category intent taxonomy and uses that structure to sharpen signal confidence, three **orthogonal MCP tools** (proactive) — `constraint_check` for constraint co-construction, `prediction_register` for falsifiable predictions, and `hygiene_check` for command-class preconditions — and a **global behavior profile** (persistent, cross-session) that aggregates behavioral patterns over days/weeks to warm-start the bias layer on new sessions (see [Cross-Session Learning](#cross-session-learning)).

### The Behavioral Compass

The behavioral compass is the core data structure. It lives in session memory and accumulates state across every tool use — including read-only operations that other channels skip (this is critical for computing the bash:read ratio that distinguishes "acting" from "researching").

The compass tracks two categories of state:

**Core state** — the agent's live model of its problem:

- **Hypotheses**: Live constraints treated as confidence-scored claims, not static facts. Each hypothesis has a confidence (0.0-1.0), evidence for and against, a source (`command_failure`, `precheck_declared`, `constraint_violation`), and a status (`active`, `confirmed`, `weakened`, `expired`). Auto-generated at 0.3 confidence from Bash failures, strengthened by confirming evidence, weakened by contradicting evidence, decayed over time when untested, and evicted when the cap (20) is exceeded. A monotonic **hypothesis version** counter increments on every mutation (add, strengthen, weaken, expire), giving detection rules a cheap way to tell whether the agent's model has actually changed between approach attempts.

- **Approaches**: A rolling window of approach attempts, each identified by a normalized command signature and tagged with the hypothesis version at creation time. An approach is marked failed when its commands produce non-zero exit codes. The approach history is what the cycling and stale-model detectors watch.

- **Coverage metrics**: The ratio between approaches attempted and constraints verified, the bash:read ratio, prediction recall (what fraction of constraints were predicted before being encountered through failure).

- **Action history**: A rolling window of tool events with normalized, redacted command signatures (never raw commands), timestamps, exit codes, and error signatures. This is the raw timeline that all detection rules operate against.

**Intent and coordination state** — structural context for smarter signal processing:

- **Intent history**: A rolling window (last 30) of resolved intents — one of `inspect`, `modify`, `verify`, `execute`, `meta`, or `unknown` — appended on every tool use. This is the raw material the intent bias scorer uses to compute verification debt streaks, failure amnesia bias, and other intent-derived signals.

- **Constraint check count**: How many times the agent has invoked `constraint_check` this session. Used to suppress the serial discovery early nudge — once the agent demonstrates proactive constraint articulation, the reactive nudge backs off.

- **Signal coordination counters**: Per-signal `last_fired` event counter and cumulative `signal_fire_counts`, used by the signal coordinator for cooldown enforcement and escalation tracking. A one-time `early_nudge_emitted` flag prevents the serial discovery early nudge from repeating.

### Detection Rules

The behavior channel runs nine detection rules against compass state, producing structured findings (same `LintIssue` type as code findings). Every finding carries an **evidence trace** — a structured dict with the recent intent window, intent counts, and (for bias-adjusted findings) the matched bias terms and score delta.

| Rule | Signal | Severity | What It Detects |
|------|--------|----------|-----------------|
| `approach_cycling` | Hard | warning | 3+ failed approaches within 30 minutes |
| `failure_amnesia` | Hard | warning | Same error signature repeated — dual-source: action history repeats OR hypothesis evidence match |
| `brute_force_escalation` | Hard | warning | More approaches tried than constraints understood |
| `premature_action` | Soft | informational | High bash:read ratio (>3:1) with high failure rate (>50%) |
| `serial_discovery` | Soft | informational | Two-stage: early nudge on 1st failure hypothesis with 0 constraint checks; escalation at 3+ failure-sourced, 0 predicted |
| `tool_repetition` | Soft | informational | Same command signature 4+ times in 30 minutes |
| `verification_debt` | Soft | informational | 8+ consecutive execute/modify intents with no verify or inspect |
| `stale_model` | Soft | informational | 2+ approach changes without hypothesis model updates |
| `consecutive_failures` | Trigger | (none) | 3+ straight Bash failures — triggers `constraint_check` nudge |

Hard signals participate in coherence (a behavior channel warning alongside a lint failure produces a "coupled" diagnosis). Soft signals are informational only — they describe weather, not make judgments. All rules run through the signal coordinator, which enforces per-signal cooldowns, deduplicates `constraint_check` nudges, and escalates persistent signals.

All thresholds are configurable per-project in `lintgate.yaml` under `controlplane.channels.behavior`.

### Intent Bias Layer

The intent bias layer classifies every tool use into one of six categories and uses that structure to adjust signal confidence — making findings louder when the intent pattern is suspicious, quieter when it is not.

**The six intent categories**: `inspect` (reading, searching), `modify` (writing, editing), `verify` (testing, diffing, linting), `execute` (running commands, installing packages), `meta` (git operations, task management, asking questions), `unknown` (unrecognized tools).

**Deterministic mapping chain** — no NLP, no LLM calls:

1. Non-Bash tools resolve by tool type: `Read` → inspect, `Write` → modify, `Grep` → inspect, `TodoWrite` → meta, etc.
2. Bash commands resolve by a three-step cascade: exact `binary:subcommand` match (e.g., `git:status` → inspect, `git:commit` → modify), then binary-only match (e.g., `pytest` → verify, `mkdir` → modify), then fallback (`execute` for Bash, `unknown` for anything else).

Both mapping tables are YAML-configurable per-project, so a Rust project can map `cargo:test` → verify and `cargo:build` → execute without touching code.

**How bias works**: Each detection rule can query the `_IntentBiasScorer` for a confidence delta. The scorer examines the recent intent window (last 10 intents) and compass state to compute a bias term — a float clamped to ±0.25. The rule's base confidence is adjusted: `score = base_score + bias; clamp to [0, 1]`. Intent data makes signals louder or quieter; it never gates them.

Four bias methods:

| Bias | Fires When | Delta | Effect |
|------|-----------|-------|--------|
| `verification_debt` | 8+ consecutive execute/modify intents, 0 verify/inspect | +0.20 | Strengthens verification_debt finding |
| `failure_amnesia` | Repeated error with no verify/inspect between occurrences | +0.15 | Strengthens failure_amnesia finding |
| `serial_discovery` | 1+ failure-sourced hypothesis, 0 constraint checks this session | +0.10 | Strengthens serial_discovery early nudge |
| `stale_model` | 2+ approaches created at same hypothesis version | +0.15 | Strengthens stale_model finding |

**Evidence traces**: Every finding carries a structured evidence dict: `{window, intent_counts, matched_bias_terms, score_delta}`. This makes findings auditable — you can see exactly which intents were in the window, which bias terms fired, and how much they shifted the confidence score.

### Signal Coordination

The signal coordinator prevents noise. Without it, nine detection rules firing on every tool use would produce a blizzard of findings and redundant `constraint_check` nudges. Three mechanisms keep the output clean:

**Per-signal cooldown**: A signal cannot fire again until at least 10 events (configurable) have elapsed since its last firing. The first firing is always allowed. This prevents the same finding from repeating on every tool use when the underlying condition persists.

**Constraint check nudge dedup**: Multiple signals may want to suggest a `constraint_check`, but the agent should only receive one nudge per execution cycle. The coordinator tracks a priority ranking (approach_cycling=1, failure_amnesia=2, brute_force=3, consecutive_failures=4, verification_debt=5, stale_model=6, serial_discovery_early=7) and only emits the highest-priority nudge. Lower-priority nudges are silently dropped.

**Escalation**: When the same signal fires repeatedly across a session, the coordinator escalates it. After 3 firings (configurable), soft signals are promoted from `informational` to `warning` severity, and hard signals get a `[persistent]` tag prepended to their message. This distinguishes a one-time observation from a systemic pattern.

Signal coordination state (last_fired counters, fire counts, early_nudge_emitted flag) is propagated back to session memory via compass delta in `ChannelResult.metrics`, applied by the hook after the mesh completes. This keeps the behavior channel read-only during execution while persisting coordination state across runs.

### The Behavioral Tools (Orthogonal Decomposition)

The behavioral supervision surface is decomposed into three orthogonal tools, each addressing one independent concern. This follows LintGate's core thesis: orthogonal lenses with uncorrelated errors compose into diagnostic signal through their agreement structure. A monolithic tool that checks hygiene AND constraints AND predictions conflates three independent signals into one opaque output. Three tools let the agent (and the system) reason about each concern independently.

**`constraint_check`** — constraint co-construction. Instead of telling the agent what its constraints are, it asks the agent to state its own model, then computes what is missing.

The agent calls `constraint_check(path, planned_action, known_constraints)` before taking an action. The tool:

1. Loads the behavioral compass from session memory
2. Finds hypotheses relevant to the planned action (scoped by command signature and tool type)
3. Compares the agent's declared `known_constraints` against the hypothesis set
4. Computes coverage gaps — constraints in the compass that the agent did not mention
5. Returns the constraint ledger, uncertainty zones, similar past failures, and a recommendation

Agent-declared constraints not already in the compass are added as hypotheses with source `precheck_declared` at confidence 0.5. This is how proactive reasoning gets recorded — and it is what distinguishes an agent that understands its problem from one that is just trying things. Each invocation increments the session's constraint check count, which suppresses the serial discovery early nudge and reduces the serial discovery bias term — creating a feedback loop where proactive constraint articulation directly reduces reactive signal noise.

The constraint check is triggered automatically by the behavior channel (via `next_actions` in the channel result) when signals fire — with the highest-priority signal's nudge winning the dedup: approach cycling, then failure amnesia, then brute-force escalation, then consecutive failures, then verification debt, then stale model, then serial discovery.

**`prediction_register`** — falsifiable prediction registration. A standalone tool for registering structured predictions (prediction_type, prediction_value) before executing commands. Predictions are stored in the behavioral compass and checked automatically against actual Bash outcomes on the next hook cycle. See [Falsifiable Predictions](#falsifiable-predictions-prediction_tracking) for the full prediction lifecycle.

**`hygiene_check`** — command-class precondition checks. Delegates to `lintgate/hygiene.py` to classify the planned command and run domain-specific safety checks (venv active, lockfile fresh, secrets in diff, versions pinned). See [Hygiene Prechecks](#hygiene-prechecks-command-class-awareness) for the full check matrix.

**`behavior_precheck`** (deprecated) — a backward-compatibility wrapper that delegates to all three tools above and merges their outputs. Retained for one release cycle to avoid breaking existing agent integrations. Emits a deprecation notice with migration guidance in every response.

### Command Normalization

All command signatures in the compass are normalized and redacted before storage. Raw commands never persist. The normalizer:

- Strips wrapper prefixes (`uv run`, `python -m`, `env VAR=val`, `sudo`)
- Keeps only the binary name and first positional-like argument
- Strips flags and absolute paths
- Redacts anything that looks like a secret (tokens, long hex strings)

Result: `uv run python -m pytest tests/test_foo.py -v` becomes `pytest:tests/test_foo`. This is enough for approach matching without leaking sensitive command details.

---

## Cross-Session Learning

The behavioral drift detection system operates at two timescales: **local** (within a session, 0–4 hours) and **global** (across sessions, days to weeks). The local layer — the behavioral compass, intent bias scorer, and signal coordinator — resets when a session expires. The global layer persists, aggregating behavioral patterns into warm-start priors that make the next session smarter from its first tool use.

### The Global Behavior Profile

The global behavior profile lives at `~/.claude/lintgate/global_behavior_profile.json` and stores only aggregate counts — no raw commands, no output text, no PII. It tracks three categories of data:

**Signal frequency priors**: For each detection signal (approach_cycling, verification_debt, etc.), the profile records total firings and sessions present. An agent that triggers verification_debt in 4 of 5 sessions has a different baseline than one that triggers it once in 20 sessions. The global profile captures this distinction.

**Intent ratio priors**: Cumulative intent counts across all sessions. Over time, this reveals the agent's baseline intent distribution — how much it inspects vs. modifies vs. verifies vs. executes. A session that starts with an unusual intent ratio (relative to the global baseline) is more likely exhibiting drift.

**Nudge outcome tracking**: When the behavior channel suggests a `constraint_check`, did the agent use it (accepted) or ignore it (ignored)? High acceptance rates for a signal mean the nudge is useful and should be boosted. Low acceptance rates mean the nudge is noise and should be dampened.

### How Global Priors Enter the Bias Layer

Global priors do not produce constraints or findings. They adjust bias weights via bounded deltas — making existing signals louder or quieter based on cross-session evidence. The merge formula:

```
effective_bias = project_weight + alpha * global_adjustment
```

Where:
- `project_weight` is the per-project bias weight from `lintgate.yaml` (e.g., `verification_debt_bias: 0.20`)
- `global_adjustment` is computed from the global profile (clamped to ±0.10 per signal)
- `alpha` decays from 0.6 toward 0 as the local `event_counter` grows

Alpha decay is the key mechanism. At the start of a session (event 0), global priors have full influence (alpha=0.6). By event 25, influence is halved. By event 50, alpha reaches 0 — the session is fully local. Global priors are a warm start that fades as local data accumulates, not a permanent bias.

### Global Adjustment Computation

The global adjustment for each signal is computed from two inputs:

| Input | Condition | Delta |
|-------|-----------|-------|
| **Signal frequency** | ≥2.0 firings/session | +0.05 |
| | ≥1.0 firings/session | +0.02 |
| **Nudge acceptance** | ≥60% acceptance rate (≥3 nudges) | +0.03 |
| | ≤20% acceptance rate (≥3 nudges) | -0.05 |

The total adjustment per signal is clamped to ±0.10, well inside the existing ±0.25 bias cap. Global priors activate only after `MIN_SAMPLE_SIZE` (3) sessions — below that, the profile returns empty adjustments.

### Safety Properties

- **Bias, don't gate**: Global priors adjust bias weights, never produce constraints or findings
- **Bounded influence**: ±0.10 per signal, further bounded by ±0.25 bias cap, further bounded by alpha decay
- **Decay over session**: Alpha starts at 0.6 and reaches 0 by event 50 — local data always wins
- **Minimum sample size**: No global influence until 3+ sessions contribute data
- **TTL pruning**: Profiles older than 90 days (configurable) are pruned on load — stale habits don't persist forever
- **No PII**: Only aggregate counts are stored; raw commands are never persisted
- **Default disabled**: Requires `global_memory.enabled: true` in `lintgate.yaml`
- **Fail-safe**: Corrupt profile files produce a fresh profile, not an error

---

## Model Calibration

Different LLM models exhibit different behavioral tendencies. A model prone to approach cycling needs different guardrails than one prone to verification debt. Model calibration creates per-model behavioral profiles that customize supervision output.

### The Probe

Probe v2 uses 5 micro-task scenarios (not multiple-choice). Each task targets specific behavioral signals and is scored from revealed policy features:
- `tool_calls` order
- `actions` sequence
- verification cadence (`verify_points`)
- retry behavior (`retry_count`)
- constraint/error references (`constraint_refs`)

| Task | Targets |
|---|---|
| Error reading | failure_amnesia, premature_action, verification_debt |
| Retry behavior | tool_repetition, approach_cycling, failure_amnesia |
| Verification cadence | verification_debt |
| Constraint discovery | serial_discovery, brute_force_escalation, premature_action |
| Model updating | stale_model, approach_cycling, failure_amnesia |

Scoring is deterministic: extracted features map to per-signal deltas, then values are clamped to [0.0, 1.0]. Confidence combines task completeness and trace quality and is capped at 0.60 (weak prior by design). Text-only responses are accepted but produce lower confidence than structured trace responses.

### Profile Structure

A `ModelProfile` stores:

- **model_key**: Canonical `provider:model` format (e.g., `anthropic:claude-opus-4`, `openai:gpt-4o`)
- **signal_risk**: `dict[str, float]` — per-signal risk levels
- **custom_anti_patterns**: Anti-pattern strings derived from highest-risk signals
- **custom_dispositions**: Guardrail MUST statements derived from high-risk signals (threshold 0.3)
- **confidence**: 0.0–1.0 probe confidence (completeness + trace quality, capped at 0.60)
- **telemetry_samples**: Count of passive refinement updates
- **stale_after_days**: Default 30

Profiles are stored at `~/.lintgate/model_profiles.json` (or `LINTGATE_HOME` env). Global, not per-project. A model calibrated once works across all repos.

### Key Canonicalization

`resolve_model_key()` maps raw identifiers to `provider:model` format:

- `claude-opus-4` → `anthropic:claude-opus-4`
- `gpt-4o` → `openai:gpt-4o`
- `gemini-2.0-flash` → `google:gemini-2.0-flash`
- `anthropic:claude-sonnet-4` → `anthropic:claude-sonnet-4` (already canonical)
- `unknown-model` → `None` (graceful fallback, no profile applied)

Provider prefixes: `claude` → anthropic, `gpt/o1/o3/o4` → openai, `gemini` → google, `llama` → meta, `mistral/codestral` → mistralai, `deepseek` → deepseek.

### Passive Telemetry Refinement

The probe anchors the profile. Real-world behavior nudges it. After a ControlPlane run, the hook applies EMA (exponential moving average) updates to the profile's signal_risk vector using observed signal fire counts from the behavioral compass:

```
new_risk = (1 - alpha) * current_risk + alpha * observed_rate
```

Where alpha = 0.15, and `observed_rate` is normalized fire counts relative to total events. Updates only apply when the session has ≥ 10 events (enough data to be meaningful) and at least one signal actually fired (no-op sessions don't dilute the profile). The session cap is 10 updates — beyond that, the current session's behavior is fully represented.

### Isolation

Profiles apply only when the model key exactly matches. No cross-model bleed, no "closest match" fallback. If no profile exists for the current model, the system falls through to project theory defaults or battle-tested zero-state defaults. This is conservative by design — applying one model's behavioral profile to another would produce misleading guardrails.

### Bootstrap Integration

When `bootstrap_context_files(model_id='...')` is called:

1. Resolve the model key via `resolve_model_key()`
2. Load the profile if it exists and is usable (confidence ≥ 0.55, not stale)
3. If usable: inject model-specific custom_anti_patterns into the `do_dont` section, inject `_model_biased_guardrails()` output into the Guardrails section
4. If not usable: fall through to project theory → zero-state defaults

The `source_signals` in the bootstrap response reports whether a model profile was applied, which model key was resolved, and the profile's confidence level.

---

## Architecture of Inquiry

The Architecture of Inquiry transforms LintGate from a reactive lint pipeline into a system that closes the loop between behavioral detection and theory extraction. Five opt-in features (configured under `controlplane.inquiry.*`) create epistemic feedback loops where the agent's own reasoning becomes the input to the supervision system.

### Theory-Grounded Signals (`theory_grounded_signals`)

When a behavioral signal fires, LintGate pulls relevant claims from the project's theory profile and appends them as a coda to the finding message. A `SIGNAL_THEORY_MAP` maps each of the 9 detection rules to relevant theory facets and keywords — for example, `approach_cycling` maps to the `problem_solving` and `alignment` facets. The coda is capped at 1-2 short claims (~150 chars) and deduplicated across consecutive runs via a merge-based delta in the behavior channel.

The effect: findings shift from "3 approaches attempted in 20 minutes" to "3 approaches attempted in 20 minutes. Theory: 'Constraint maps become architectures of possibility.' What constraint is your current approach testing?" The agent is confronted not just with what it did wrong, but with the project's own stated values about how to do it right.

Implementation: `behavior_channel.py` (`_ground_finding_in_theory`, `_SignalCoordinator`).

### Falsifiable Predictions (`prediction_tracking`)

Agents register structured, falsifiable predictions via `prediction_register` — specifying a `prediction_type` (`exit_code`, `error_signature`, or `stdout_contains`) and an expected value. Predictions are stored in the behavioral compass and checked against actual Bash outcomes on the next hook cycle. Each prediction links to the most relevant hypothesis.

Prediction accuracy is computed after 5+ checked predictions. If accuracy exceeds 70%, soft signal confidence is dampened by -0.15 — the agent demonstrably understands the project, so reactive signals should be quieter. If accuracy falls below 30%, signal confidence is amplified by +0.15 — the agent's model is unreliable, and the supervision system should be louder.

Predictions with empty or unresolvable command signatures (`unknown:unknown`) are rejected at registration. Only the three structured prediction types are accepted — unknown types are rejected to prevent implicit mismatches from silently weakening hypotheses. Predictions that go unchecked for 20 events expire.

Implementation: `behavior_compass.py` (`PredictionExpectation`, `Prediction`, `_check_predictions`, `compute_prediction_accuracy`), `behavior_tools.py` (`prediction_register`).

### Theory Coherence Check (`theory_coherence_check`)

When the constraint proposer generates a new rule from recurring patterns, it checks the proposal against the project's theory profile for alignment. A `TheoryCoherenceResult` records supporting and contradicting claims with a coherence score. If contradicting claims are found, the proposal is flagged with `drift_warning = True`.

This is metadata-only — coherence does not adjust confidence. The heuristic contradiction detection (dominant polarity comparison) will have false positives, so the system surfaces the information for human review rather than acting on it automatically. The coherence check is gated behind the `theory_coherence_check` config flag and will not run merely because other inquiry features have populated the theory cache.

Implementation: `constraint_proposer.py` (`check_theory_coherence`, `_is_contradicting`).

### Living Context (`living_context`)

CLAUDE.md becomes a living document. Behavioral discoveries flow back as managed-section patches via `<!-- LINTGATE:BEGIN section_id vN -->` / `<!-- LINTGATE:END section_id -->` markers. Four section IDs: `machine_rules`, `do_dont`, `theory_alignment`, `context_map`.

Patches are generated on four triggers: `constraint_accepted` (new rule added to machine_rules), `prediction_confirmed` (stable pattern noted in do_dont), `recurring_behavioral_signal` (DO NOT entry in do_dont), and `theory_coherence_update` (theory_alignment refresh). Patches are stored in session memory as pending and are **never auto-applied**.

The `context_patch_review` MCP tool shows pending patches with diff previews. The `context_patch_apply` MCP tool explicitly applies them (with `dry_run=False`). When a patch targets a section and the proposed content already exists, the patch is skipped (idempotency). Patches that produce no changes (no-op) return `applied: false` with a descriptive message. Cumulative patches to the same section increment the version number in the BEGIN marker.

For pre-upgrade CLAUDE.md files without markers, a migration function (`_migrate_to_managed_sections`) uses heading-based heuristics to identify and wrap existing sections. User content outside managed sections is preserved.

Implementation: `context_bootstrap.py` (`ManagedSection`, `ContextPatch`, `generate_context_patch`, `apply_context_patch`, `_parse_managed_sections`, `_migrate_to_managed_sections`).

### Session Readiness Advisory (`session_gate`)

An advisory gate that warns when the agent attempts to modify files (Write/Edit/MultiEdit) without sufficient theory context. The gate checks two conditions: the theory profile has required facets (`core_theory`, `problem_solving`, `alignment`) with at least one claim each, and CLAUDE.md has enforceable rules.

When not ready, the system emits an advisory in the hook's systemMessage recommending `bootstrap_context_files`, and short-circuits the behavior channel (saving tokens and latency). Basic lint still runs. Once readiness is confirmed, the session is marked ready and the gate does not re-check.

This is advisory-only by design. A PreToolUse hard gate would create blocking dependencies on theory extraction before every write — problematic for new projects bootstrapping their docs. The advisory steers without blocking.

Implementation: `context_auditor.py` (`SessionReadiness`, `check_session_readiness`), `hook_posttooluse.py` (advisory gate before mesh execution).

---

## Project Compass: 4-Axis Understanding

The theory extractor produces a 6-facet profile of a project's conceptual space. The project compass maps that output to a 4-axis model — **Problem**, **Solution**, **Implementation**, **World** — detects where understanding is thin, fills gaps through interview or code inference, and renders the result into context files for any AI coding tool.

The compass addresses a specific failure mode that code-level and strategy-level supervision cannot catch: an agent that writes syntactically correct, lint-clean, test-passing code that is architecturally wrong because it never understood the project's constraints. The lint hook catches bad code. The behavioral compass catches bad strategy. The project compass catches bad *understanding* — before it produces either.

### The 4-Axis Model

The compass collapses the theory extractor's 7 facets into 4 orthogonal axes:

| Axis | Source Facets | What It Captures |
|------|--------------|------------------|
| **Problem** | core_theory, alignment | What the project solves, success criteria, non-goals |
| **Solution** | problem_solving, architecture, anti_patterns | Why this approach, tradeoffs, rejected alternatives |
| **Implementation** | abstractions, enforceable_rules | Naming conventions, tools, idioms, machine-enforceable rules |
| **World** | (inferred from code/infra) | Runtime constraints, dependencies, platform assumptions |

Each axis carries a depth score (0–3):

| Depth | Meaning | Evidence |
|-------|---------|----------|
| 0 | Empty | No claims |
| 1 | Surface | 1–3 definitional claims |
| 2 | Structural | 4–8 claims, or any claim with causal/contrastive reasoning |
| 3 | Deep | 9+ claims, or ≥3 claims with causal/contrastive reasoning |

Depth is scored from claim text, not claim count alone. A single claim that explains *why* a tradeoff was made ("because X, we chose Y instead of Z") signals structural understanding — it carries more weight than three claims that merely name things. The depth scorer checks for causal markers (because, therefore, since, in order to) and contrastive markers (however, instead, rather than, unlike).

Three data types compose the compass state:

- **CompassClaim** — a knowledge claim with text, source reference, confidence (0.0–1.0), and provenance (parsed/inferred/interviewed)
- **CompassDirective** — a behavioral directive derived from claims: `toward` (what correct work looks like), `away` (patterns that indicate drift), `forbidden` (hard constraints from enforceable rules)
- **GapReport** — arithmetic on axis depths: spikiness (stdev of normalized required-axis depths), sparse axes (depth ≤ 1), interview recommendation

The compass file lives at `.claude/compass.yaml` (committed, persists across sessions). It is project understanding, not session state.

### Axis Extraction

The axis extractor wraps the existing theory extractor — it does not replace the 1,275-line parser with its 70+ heading patterns and 40+ paragraph patterns. Instead, it calls `extract_theory()`, receives the 7-facet profile, and maps each facet's claims to the appropriate compass axis via the `FACET_TO_AXIS` table. It then supplements the mapping with axis-specific heading signals (regex patterns targeting axis-relevant headings) to improve discrimination — for example, headings containing "trade-off" or "rejected" strengthen the solution axis even if the claim was originally classified as `architecture`.

The extractor also runs code inference (see below) to populate the world axis, which has no direct facet source.

Both the theory pipeline and compass pipeline can coexist. When compass.yaml exists, tools like `build_theory_pack` and `get_theory_context` read from it. When it does not, they fall back to the existing theory pipeline. No breaking changes.

### Code Inference

The world axis — runtime constraints, dependencies, platform assumptions — has no direct facet source because most projects do not document their infrastructure in prose. Instead, the code inference engine derives claims from code artifacts, all in pure Python:

| Signal Source | What It Produces | Axis |
|---------------|-----------------|------|
| pyproject.toml | Python version, dependencies, project description | world, problem |
| README | First non-heading paragraph, badge analysis | problem, solution |
| Import graph | Framework detection, architectural layers, dependency clusters | solution, implementation |
| Directory structure | src layout, package hierarchy, naming conventions | implementation |
| Test patterns | pytest vs unittest, fixture patterns, test-to-source ratio | implementation |
| Commit messages | Conventional commit themes, refactoring frequency | solution |
| Docstrings | Module/class descriptions | problem, solution |

All inferred claims carry provenance `"inferred"` and confidence capped at 0.6. Framework detection uses a mapping table (e.g., FastAPI → "Uses FastAPI for HTTP API layer" on the solution axis, boto3 → "Uses AWS SDK" on the world axis). Import graph analysis uses `ast.parse` on up to 50 Python files to build a dependency DAG and identify core vs utility modules.

Code inference runs automatically during compass extraction. It can also be triggered explicitly during the interview (see below) as an alternative to answering questions manually.

### Gap Detection

Codebases have predictable documentation profiles — and those profiles create predictable blind spots. A systems-thinker documents Problem and Solution heavily but leaves Implementation sparse, so the agent understands *what* and *why* but invents its own conventions. A performance engineer documents Implementation but forgets to explain what the project *does*, so the agent follows patterns flawlessly while building the wrong thing.

The gap detector turns documentation profiles into arithmetic:

1. Compute depth for each axis from its claims
2. Compute **spikiness**: stdev of normalized required-axis (Problem, Solution) depths. Spikiness > 0.3 means lopsided understanding
3. Identify **sparse axes**: any axis at depth ≤ 1
4. Recommend interview when spikiness exceeds threshold or any required axis is empty

The interview system provides 12 questions (3 per axis), each targeting a specific class of mistake:

| Axis | Questions | What Mistakes They Prevent |
|------|-----------|---------------------------|
| Problem | "What does this solve?" / "Non-goals?" / "Success criteria?" | Scope creep, building the wrong thing |
| Solution | "Why this approach?" / "Tradeoffs?" / "Prior work?" | Re-imagining the architecture, ignoring constraints |
| Implementation | "Naming conventions?" / "Standard tools?" / "Well-written module example?" | Convention drift, style inconsistency |
| World | "Runtime constraints?" / "Infrastructure assumptions?" / "Dependency constraints?" | Infrastructure-level errors no amount of testing catches |

Questions are prioritized by axis sparseness. Every question has four response paths:
1. **Type an answer** → becomes a claim on the axis (provenance `"interviewed"`)
2. **Skip this question** → moves to next
3. **Skip all remaining** → marks interview complete
4. **Figure it out from the code** → triggers code inference for that axis

All gap detection and interview logic is pure Python, zero LLM tokens.

### Cognitive Modes

The compass defines three cognitive modes that shape how the system behaves during a session:

| Mode | Purpose | Transition In | Transition Out |
|------|---------|---------------|----------------|
| **Normal** | Default. Monitor compass staleness, serve queries. | Session start, or exit from Theory/Habit | Enter Theory or Habit |
| **Theory** | Understand before you write. Explore docs, fill gaps, build compass. | Agent calls `theory_mode_enter` | Agent calls `theory_mode_freeze` (lock compass) |
| **Habit** | Sustained execution with frozen project understanding. | Habit score threshold (existing hysteresis) | Habit score decay or user directive |

Two transitions are **blocked**:
- Theory → Habit: cannot start sustained execution without first freezing understanding
- Habit → Theory: cannot re-examine understanding while in sustained execution mode

The blocking rules enforce sequencing. `theory_mode_freeze` validates that at least 2 axes have depth ≥ 1 (minimum viable understanding), computes a compass hash for drift detection, and transitions to Normal. From Normal, the existing habit mode hysteresis can detect and enter Habit mode.

Mode state is session-scoped (stored in session memory, not compass.yaml). Every new session starts in Normal.

### Execution Compass

When habit mode activates, the compass state is frozen into an **ExecutionCompass** — a compact, immutable structure containing:

- `toward`: what correct work looks like in this project (from solution claims)
- `away`: patterns indicating drift from project intent (from anti-pattern claims)
- `forbidden`: hard constraints (from enforceable rules)
- `true_north`: the problem this project solves (from problem axis summary)
- `compass_hash`: integrity check against drift during execution

The ExecutionCompass is checked against recent actions during habit mode. Alignment checking is mechanical: case-insensitive keyword matching of forbidden/away directives against action descriptions. Violations produce structured warnings (`AlignmentWarning`) with the matched directive and severity. This is a cheap, narrow check — it catches only the most obvious contradictions between what the agent claimed to understand and what it is doing.

The ExecutionCompass serializes to ~800 tokens for compaction snapshots, ensuring project understanding survives context window compaction.

### Multi-Model Renderers

From a single `compass.yaml`, the renderer registry produces context files tailored to each AI coding tool's native format:

| Target | Output Path(s) | Format | Key Optimization |
|--------|---------------|--------|------------------|
| **Claude** | CLAUDE.md + .claude/rules/theory.md | Hierarchical MD, managed sections, epistemic checks | Imperative dispositions, LINTGATE:BEGIN/END markers |
| **Cursor** | .cursor/rules/compass.mdc | Flat MDC with YAML frontmatter | Front-loaded rules, glob-scoped, <2000 tokens |
| **Copilot** | .github/copilot-instructions.md | Action-focused MD | Specific, path-scoped, <1200 tokens |
| **Windsurf** | .windsurf/rules/compass.md + dynamic files | Per-topic .md files | Split by axis, "Always On" mode, <6000/file |
| **Cline** | .clinerules/compass.md | MD rules (AI-editable) | Cline can modify its own rules |
| **Aider** | CONVENTIONS.md | Convention format | Convention-focused, lint integration |
| **AGENTS.md** | AGENTS.md | Cross-tool standard | Works with 25+ platforms |
| **Generic** | CONTEXT.md | Simple structured MD | Universal fallback, ~2000 tokens |

Each renderer implements a common protocol: `render(compass, metadata) → {relative_path: content}`. The registry auto-detects AI tools present in the project directory (`.cursor/` → Cursor, `.github/` → Copilot, `.windsurf/` → Windsurf, `.clinerules` → Cline) and can render for all detected targets with `compass_update(path, targets=["all"], write=True)`.

The renderers map compass state to each tool's native language. The Claude renderer uses True North, Architecture Philosophy, DO/DO NOT sections, and machine rules. The Cursor renderer uses flat, front-loaded rules under Mission/Architecture/Do/Avoid/Forbidden headings. Each renderer speaks its own tool's idiom while conveying the same project understanding.

### Compass Hooks

The compass integrates with Claude Code's hook system through 6 hook handlers, each enforcing a narrow concern:

| Hook | Event | What It Does |
|------|-------|-------------|
| **session_start** | SessionStart | Loads compass, injects staleness and axis depths as `additionalContext`. Suggests `compass_update` if no compass found. |
| **user_prompt** | UserPromptSubmit | Targeted pre-generation primer — injects mode, focus files, blocking alerts, and behavioral warnings as a system message before model reasoning (~120 tokens). Falls back to mode indicator if RuntimeState unavailable. |
| **pre_tool** | PreToolUse (Write\|Edit\|Bash) | In Theory mode: advises against writing before understanding. Checks proposed actions against forbidden/away directives. |
| **stop_gate** | Stop | Reports compass coverage and staleness. Advisory only, never blocks. |
| **pre_compact** | PreCompact | Dual-write compaction shaping — builds structured capsule from RuntimeState, writes to both hook output (`additionalContext`) and dynamic rule files to ensure state survives compaction (~800 tokens). Falls back to compass-only capsule. |
| **session_end** | SessionEnd | Re-saves compass state if modified during session. |

Each hook reads JSON from stdin, loads compass and session state, performs its narrow check, and writes JSON to stdout. Hook messages report raw data — `[Compass] Loaded — staleness=85% axes={problem: 2, solution: 3, ...}` — not interpretations. The hooks are independent entry points, not a monolithic handler.

### State Reset

The `compass_reset` tool provides scoped state deletion with dry-run default:

| Scope | What It Deletes | What It Preserves |
|-------|----------------|-------------------|
| `compass` | compass.yaml, rules/theory.md | Session memory, config, issue memory, everything else |
| `session` | Session memory, habit state | Compass, config, issue memory |
| `project` | All LintGate state for the project | CLAUDE.md, AGENTS.md (user may have manual edits), global profile |
| `global` | All LintGate state for all projects | CLAUDE.md, AGENTS.md per project |

Without `confirm=True`, the tool returns a structured report of what *would* be deleted, with file sizes and types. CLAUDE.md and AGENTS.md are never auto-deleted — they may contain manual edits that are not recoverable.

Implementation: `lintgate/compass.py` (data model, depth scoring, gap report), `lintgate/compass_io.py` (load/save/migrate), `lintgate/axis_extractor.py` (wraps theory_extractor + heading signals), `lintgate/gap_detector.py` (gap detection + interview templates), `lintgate/code_inference.py` (multi-signal code analysis), `lintgate/modes/mode_state.py` (cognitive mode transitions), `lintgate/modes/execution_compass.py` (frozen directive state + alignment checking), `lintgate/renderers/` (8 renderer implementations + registry), `lintgate/hooks/` (6 hook handlers), `lintgate/reset.py` (scoped state deletion), `mcp_tools/compass_tools.py` (8 MCP tools).

### Runtime State & Dynamic Rendering

The runtime state bus and dynamic rendering system provide session-scoped context that survives context compaction across all supported AI coding tools.

**RuntimeState** (`lintgate/runtime_state.py`): A canonical projection of all rendering surfaces. Assembles from SessionMemory + HabitModeState + TokenTrackerState + CompassState into a single `RuntimeState` dataclass. Persists to `.lintgate/runtime_state.json` with a monotonic `generation` counter. All operations are fail-safe — corrupt or missing state returns None/fresh defaults. File locking uses best-effort `fcntl` with 80ms timeout and exponential backoff.

**WriteScheduler** (`lintgate/write_scheduler.py`): Cadence control for dynamic rule file writes. Every rewrite changes file mtime → host re-tokenizes its system prompt → expensive. The scheduler uses dirty-flag + cooldown logic to batch writes. Immediate triggers (mode_transition, compass_violation, compaction, session_start) bypass cooldown; cadenced triggers (tool_call, lint_complete, timer) respect 30–300s intervals.

**HostAdapter** (`lintgate/renderers/host_adapter.py`): Capability detection protocol for multi-host rendering. Extends the existing `Renderer` protocol with dynamic rendering methods (`render_session`, `render_focus`, `cleanup_dynamic`). Defines host presets with rules/hooks/MCP/frontmatter capabilities and token budgets (4k–12k).

**Dynamic rendering** (`lintgate/renderers/dynamic.py`): Produces session-scoped files (`lg_session`, `lg_focus`) carrying runtime state that survives context compaction. Token-budgeted at 1200 + 250 tokens respectively. Files carry `LG_GEN:<N>` watermarks for fast staleness detection without full parsing. Dynamic files use the `lg_` prefix convention and inherit the host's rule directory and file extension (`.md` or `.mdc`).

---

## Dependency Health Monitoring

LLM coding agents frequently install packages, modify manifests, and alter dependency graphs — often without considering environment hygiene. LintGate monitors dependency health at two speeds:

**Fast path** (hook, < 5ms): Fires on `dependency` or `build` change kinds. Checks for missing venvs, stale lockfiles, global installs outside venvs, and dependency churn (5+ dep changes in 10 minutes = "stabilize before continuing").

**Full audit** (MCP tool): Comprehensive health check covering virtual environment state, lockfile presence and freshness, `.python-version` file, conflicting package manager artifacts (poetry.lock + uv.lock = pick one), manifest health (pyproject.toml required fields), and dependency churn history.

Also includes `dep_sync` — a status/action tool that can create venvs and refresh lockfiles on demand.

---

## MCP Server & Tool Interface

LintGate exposes 100 MCP tools. The recommended workflow is: **lint → drill-down → fix → verify**.

### Core Lint Workflow

| Tool | Purpose |
|---|---|
| `lint_files(files, tier)` | Lint specific files. Returns compact JSON with `run_id` and `next_actions`. |
| `lint_project(path, tier)` | Lint all Python files in a project. Same compact output. |
| `lint_get_details(run_id, severity)` | Drill into a previous run by `run_id` — full issue details without re-running linters. |
| `lint_fix(path, dry_run)` | Auto-fix safe issues (ruff --fix/format). Default `dry_run=True` previews changes. |
| `lint_status(path)` | Show available linters, run history, context metrics, today's telemetry, and missing-tool install hints. |

All lint responses include a `next_actions` array with prioritized suggestions: "3 auto-fixable issues → try `lint_fix`" or "2 blocking issues → try `lint_get_details`". This lets the agent chain tools without guessing.

### Auditing & Context

| Tool | Purpose |
|---|---|
| `audit_tool_versions(path, auto_fix)` | Check and optionally fix tool version mismatches |
| `context_guidance(path, files)` | Summarize CLAUDE.md/AGENTS.md guidance and machine rules |
| `audit_context_health(path)` | Audit context file quality against best practices |
| `bootstrap_context_files(path, write, model_id)` | Generate best-practice context files from theory + audits. Pass `model_id` for model-aware calibration. |
| `context_patch_review(path)` | Review pending managed-section context patches with diff previews |
| `context_patch_apply(path, patch_ids, dry_run)` | Explicitly apply pending context patches to CLAUDE.md |
| `extract_theory_constraints(path)` | Extract enforceable lint rules from prose directives |

### Theory Extraction

| Tool | Purpose |
|---|---|
| `extract_project_theory(path)` | Extract the full conceptual theory profile (6 facets + enforceable rules) |
| `build_theory_pack(path)` | Compact theory digest for system prompt injection (~500-1500 tokens) |
| `get_theory_context(path, facet, keywords)` | On-demand retrieval of specific theory claims |

### Dependencies & Environment

| Tool | Purpose |
|---|---|
| `dep_health_check(path)` | Comprehensive dependency health audit |
| `dep_sync(path, create_venv, lock)` | Check/fix dependency sync status |

### Bootstrap (Cold-Start)

| Tool | Purpose |
|---|---|
| `bootstrap_tests(path, force, dry_run)` | Trigger resumable test bootstrap pipeline for zero-test projects. Generates skeletons, property tests, behavioral contracts, and runs mutation sampling. |
| `bootstrap_status(path)` | Check bootstrap pipeline status: phase, progress, artifacts, errors. |

`bootstrap_tests` is idempotent — re-running skips completed phases. The pipeline writes to `tests/generated/` (isolated namespace) and never modifies existing test files. Use `dry_run=True` to preview without writing.

### ControlPlane (Supervision Mesh)

| Tool | Purpose |
|---|---|
| `controlplane_run(path, channels)` | Run the multi-channel supervision mesh (compact delta-first output) |
| `controlplane_get_details(run_id, channel, severity, max_issues, sections)` | Drill into a ControlPlane run by run_id (full findings, repairs, evidence, and channel metrics) |
| `controlplane_status(path)` | Show ControlPlane configuration and session state |
| `controlplane_test_skeleton(path, target_file)` | Generate test skeleton for a source file |
| `controlplane_report_repair(path, action_id)` | Report repair action outcome |
| `controlplane_apply_repairs(path, safe_only)` | Execute proposed command-type repairs |
| `controlplane_agent_feedback(path, ...)` | Agent feedback on findings/constraints |
| `hygiene_check(path, planned_action)` | Command-class precondition safety checks (venv/lockfile/secrets/git hygiene) |
| `constraint_check(path, planned_action, known_constraints)` | Constraint co-construction: agent states model, tool computes gaps |
| `prediction_register(path, planned_action, prediction, prediction_type, prediction_value)` | Register falsifiable predictions checked automatically on next tool event |
| `behavior_precheck(path, ...)` | Deprecated compatibility wrapper that delegates to `hygiene_check` + `constraint_check` + `prediction_register` |
| `global_memory_status(path)` | Show global behavior profile: session count, signal priors, nudge outcomes, computed adjustments |
| `global_memory_reset(path)` | Reset the global behavior profile (useful after major workflow changes) |
| `model_profile_status(path, model_id)` | Show model calibration profile status. If `model_id` is None, returns all profiles. |
| `model_profile_probe_start(path, model_id)` | Start a 5-task behavioral micro-probe. Returns micro-tasks + structured response schema. |
| `model_profile_probe_submit(path, model_id, answers)` | Submit task responses (action traces + text), score into signal_risk vector, derive anti-patterns + guardrails, persist profile. |

`controlplane_run` returns compact JSON with `run_id`, `coherence`, `counts`, channel summary, and `next_actions`. On first run in a session it inlines blocking issues; on subsequent runs it emits deltas (`new`, `escalated`, `resolved_count`, `still_active_count`). Use `controlplane_get_details` for drill-down without re-running channels.

### Habit Mode

| Tool | Purpose |
|---|---|
| `declare_mode(path, mode)` | Agent self-declares "habit" or "standard" mode (immediate, no sustain wait) |
| `habit_status(path)` | Read-only status: active, score, signals, files, test status, token economics |
| `habit_compact(path)` | Trigger compaction, return structured Habit State Snapshot (~3000 tokens) |
| `habit_configure(path, ...)` | Runtime threshold adjustment: compact_threshold, enter_score, exit_score, sustain_calls, token_api_interval, context_window_size (session-scoped, safe-clamped) |

`habit_compact` gathers all available context (session memory, compass, last lint run, theory pack, issue memory, token state) and produces a structured 10-section snapshot for post-compact injection. `habit_configure` overrides are session-scoped and clamped to safe ranges.

### Project Compass

| Tool | Purpose |
|---|---|
| `compass_status(path)` | Show axis depths, gap report, staleness, cognitive mode, and next actions |
| `compass_update(path, targets, write)` | Re-extract compass from project docs. Optionally render context files for specified targets. |
| `compass_interview(path, answers, skip)` | Gap-filling interview — returns prioritized questions or applies answers to fill sparse axes |
| `compass_check(path, action)` | Check a planned action against toward/away/forbidden directives |
| `compass_reset(path, scope, confirm)` | Scoped state reset with dry-run default. Scopes: compass, session, project, global. |
| `theory_mode_enter(path)` | Enter theory exploration mode (Normal→Theory). Runs extraction and gap detection. |
| `theory_mode_freeze(path)` | Freeze compass and exit theory mode to Normal. Validates minimum axis depth. |
| `setup_hooks(path, write)` | Generate `.claude/settings.json` hook configuration for compass hooks. Preview by default. |

### Specification Complexity

| Tool | Purpose |
|---|---|
| `spec_analyze(path)` | Project-wide specification complexity analysis (sigma, regime, phase, risk, DFT) |
| `spec_prescribe(path, function?)` | Risk-prioritized test prescriptions with expanded taxonomy |
| `spec_composition(path, module_a?, module_b?)` | Composition gap (γ) and sheaf condition analysis across modules |
| `spec_gate_check(path, function?)` | Optimization gate validation — checks specification level against hint thresholds |
| `spec_file_analyze(path, file, enrich?)` | Single-file spec analysis with empirical overlay (PR5). Default `enrich=True` builds manifests and computes static/empirical reconciliation from cached mutation data. Overlay status: NO_EMPIRICAL_DATA, AGREES, CONTRADICTS, DISCOVERY_FAILURE, TOPOLOGY_LIMITED. `enrich=False` runs AST-only symbolic baseline. |
| `spec_file_prescribe(path, file, max_prescriptions?)` | Single-file test prescriptions sorted by risk priority |
| `spec_project_rollup(path, use_cache?, analyze_uncached?, include_tests?)` | Project-wide rollup with file-level content-hash caching. Defaults to production-only (`include_tests=False`) so hotspots focus on source code. Default mode is cache-read-only; `analyze_uncached=True` runs live analysis on cache misses. |

The specification system estimates **σ(P,μ)** — the minimum tests to fully specify a function under mutation policy — using a 6-path AST decision tree, test design signals, DFT scoring, and TPA calibration. It classifies functions into **regimes** (A = tractable via standard testing, B = requires alternative strategies) with an explicit **rationale** string explaining the classification. **Phase detection** (bulk → transition → tail → complete) uses design signal density as a trajectory-aware proxy for convergence rate (Theorem 3.4). Each prediction produces a **TrajectoryState** with convergence rate and estimated remaining specification points.

**Composition analysis** computes the composition gap γ(A,B) for cross-module call edges. Interface mutation point counting uses value mutations (per parameter), swap mutations (C(n,2) transpositions), and state mutations (shared mutable coupling), weighted by callee uncertainty — well-specified callees reduce effective mutation points. The sheaf condition holds when total obstruction < threshold.

**Pipeline integration with mutation testing**: Specification analysis is the diagnostic entry point; mutation testing is the empirical verification layer. The full pipeline: `spec_file_analyze` (diagnose σ, regime, phase) → `mutation_run_sampling`/`mutation_run_full` (profile which mutation categories survive) → `mutation_prescribe` → `mutation_prescribe_tests` → [write tests] → `mutation_validate_tests` (survival deltas) → `spec_gate_check` (stop criteria). The ledger accumulates ΔK across these cycles via `prior_ledger` parameter, enabling trajectory-aware phase detection. See the [Mutation Testing Engine](#mutation-testing-engine) section for execution details.

### Test Regeneration

| Tool | Purpose |
|---|---|
| `test_rebuild_plan(path, file?, write_manifest?, preserve_globs?)` | Classify all functions into regeneration strategies, write manifest |
| `test_rebuild_generate(path, write?, max_files?)` | Generate test skeletons for `auto_generate_unit` targets |
| `test_rebuild_validate(path, review_ceiling?)` | Run 9 quality gates: pytest sanity, review ceiling, artifact check, kill rate, zero-kill, effectiveness, hygiene, redundancy |
| `test_rebuild_apply(path, dry_run?)` | Promote generated tests, quarantine old ones (dry_run=True by default) |

The test regeneration system classifies every project function into one of four strategies:
- **exclude_mutation** — entrypoint glue, topology-hostile surfaces (artifact discovery states)
- **preserve_system** — functions with integration coverage on system surfaces
- **manual_contract** — meaningful but mutation-resistant code requiring hand-written tests
- **auto_generate_unit** — deterministic local logic with meaningful mutation signal

The classifier composes evidence from spec analysis (`SpecEvidence`: σ, regime, phase, purity) and mutation cache (`MutationEvidence`: discovery/topology state, survival rate). Confidence is computed as `min(topology_confidence, 1.0 - survival_rate) * phase_weight`.

**Nine validation gates** in `test_rebuild_validate`:
1. Preserved tests still pass (pytest execution)
2. Generated tests import and run
3. Manual review share stays under `review_ceiling` (default 0.15)
4. No auto-generate target has artifact discovery state
5. Kill rate floor — average kill rate ≥ `kill_floor` (default 0.70)
6. Zero-kill ceiling — fraction of zero-kill auto targets ≤ `zero_kill_ceiling` (default 0.05)
7. Effectiveness score — `project_effectiveness_score` must not regress (advisory)
8. Test hygiene — no critical findings from `test_hygiene_scan`
9. Test redundancy — advisory redundancy check via `test_redundancy_project`

Gates 7 and 9 are advisory (do not block apply). Gates 1-6 and 8 are blocking.

Configuration in `lintgate.yaml`:
```yaml
controlplane:
  test_regeneration:
    preserve_globs: ["test_conftest_*.py"]
    review_ceiling: 0.15
    kill_floor: 0.70
    zero_kill_ceiling: 0.05
    generated_dir: "tests/generated"
    quarantine_dir: "tests/quarantine"
```

### Telemetry

| Tool | Purpose |
|---|---|
| `telemetry_summary(path, period)` | ROI dashboard: runs, issues, fix rate, token cost, quality trend |

> **Source of truth for tool inventory**: Run `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools/*.py | wc -l` to get the authoritative count (target `*.py` to avoid pycache matches). Tool tables in all documentation files should be cross-checked against this count.

---

## Token Efficiency

The dominant cost in an unsupervised agentic coding pipeline is not bugs. It is the agent spending its intelligence budget on problems that are not intelligence problems.

Approach cycling is not a hard problem. Failure amnesia is not a hard problem. Rebuilding an import that was broken three edits ago is not a hard problem. These are discipline problems — and discipline is exactly what LLMs are worst at and deterministic systems are best at. An unsupervised agent operating on a real codebase spends a substantial fraction of its token budget on predictable, preventable failures: retrying approaches that already failed, debugging errors that a linter would have caught at write time, re-reading files it dirtied with formatting drift. In one calibration session, an agent spent ~90 minutes in a death spiral — 83% of the constraints it eventually discovered were predictable from prior failures, and the token cost of that spiral exceeded the cost of the useful work in the entire session.

LintGate's value proposition is not "catches bugs early." It is: **offload discipline to the cheap deterministic layer so the expensive stochastic layer spends 100% of its capacity on novel reasoning about your actual problem.** The monitoring is not a cost — it is an efficiency multiplier on the agent's primary capability. The agent is already the most expensive component in the pipeline. Making it 2-3x more effective at its actual job by spending 18% more tokens on supervision is the highest-leverage investment available. This is cognitive resource allocation at the pipeline level — the prefrontal cortex role described in the introduction, applied as an engineering system with measurable costs and returns.

### The Duty Cycle Problem

The numbers below come from two real agentic sessions, both by a Claude Opus agent. One is a calibration session (3.5 hours, hardware hacking, no quality gate). The other is a supervised session (two sittings, 1,330 LoC Python project, LintGate v0.2 active). They illustrate the same thing from opposite directions.

**The unsupervised session** — an agent trying to bypass an iCloud activation lock on an iPhone 5c — executed 55 bash commands across 3.5 hours. Of those:

| Category | Commands | % of Session |
|---|---|---|
| Produced new useful information | ~20 | 36% |
| Wasted (predictably failed or redundant) | ~15 | 27% |
| Execution steps for ultimately-doomed plans | ~20 | 36% |

**Effective duty cycle: 36%.** The remaining 64% of the agent's token budget went to discipline failures: retrying approaches that had already failed, re-discovering constraints it had already encountered, building artifacts without verifying the assumptions they depended on. The agent attempted 5 approaches — all failed — and the approach that would have worked (custom IPSW with patched ASR in the restore ramdisk) was never attempted because the session ran out of time cycling through approaches 1-5. Of the 6 constraints discovered during the session, 5 (83%) were predictable from prior failures without executing anything. One was a re-discovery of a constraint from 2 hours earlier (failure amnesia). Total time on doomed approaches: ~90 minutes. Time the correct approach would have taken: ~50 minutes.

The efficiency loss factor was ~4x. Not because the problems were hard — the constraints were well-documented and discoverable through reading — but because the agent spent its intelligence budget on discipline problems instead of reasoning problems.

**The supervised session** — building an iPhone recovery toolkit with LintGate active — tells the inverse story:

| Component | Tokens | Purpose |
|---|---|---|
| Code generation (1,330 LoC x ~4 tok/LoC) | ~5,320 | Novel reasoning — architecture, implementation |
| Context reads (1,330 LoC x ~12 tok/LoC) | ~15,960 | Novel reasoning — understanding before modifying |
| LintGate supervision (17 calls x ~275 tok) | ~4,670 | Discipline — quality checks, auto-fix, drift prevention |
| Manual fixes (8 issues x ~150 tok) | ~1,200 | Discipline — guided corrections |
| **Total** | **~27,150** | |

Of which ~21,280 (78%) went to the agent's actual job (generating and reasoning about code) and ~5,870 (22%) went to discipline. The discipline layer caught all 26 issues — 18 auto-fixed, 8 guided — and the agent never entered a death spiral, never debugged a propagated error, never retried a doomed approach. Every token it spent on novel reasoning went toward the actual problem.

**The comparison is not "overhead."** It is duty cycle:

| Metric | Unsupervised | Supervised (LintGate v0.2) |
|---|---|---|
| Effective duty cycle (tokens on novel reasoning) | ~36% | ~78% |
| Tokens on discipline failures | ~64% | ~22% (deterministic, cheap) |
| Session outcome | 0 of 5 approaches succeeded | 1,330 LoC, 26 issues caught, 20 tests passing |
| Efficiency loss factor | ~4x | ~1.2x |

The unsupervised agent is not cheaper. It spends *more* total tokens to accomplish *less*, because the tokens it saves on supervision it loses — at a higher exchange rate — to debugging, re-reading, retrying, and spiraling. The 18% supervision overhead displaces token spend that was going to waste at 3-4x the rate.

### What Degrades the Duty Cycle

The mechanism is specific and observable. When an unsupervised agent writes code, it accumulates discipline debt that feeds back into lower productivity on subsequent actions:

- **Dead imports** (9 in the test codebase) — each one is a small confusion on every subsequent pass through the file. The agent re-reads the import block, processes imports that do nothing, and occasionally follows them to understand code that depends on something that is not actually used. Multiply by the number of passes. Multiply by the number of files.
- **Formatting drift** (8 inconsistencies) — noisy diffs hide real changes in whitespace. The agent spends tokens parsing what actually changed versus what was reformatted. Over a multi-file refactor, this is a material fraction of context-read cost.
- **Wrong API usage** (`shutil.os` instead of `os`) — works today, breaks on the next refactor. When it breaks, the agent debugs the symptom (import error) rather than the cause (wrong API), because the cause looks like it should work. This is the seed of a death spiral: the agent cycles through approaches to fix the symptom while the structural problem persists.
- **Missing type stubs** — entire library interfaces unchecked. Type errors surface at runtime, not at write time, and the agent discovers them one at a time through test failures rather than all at once through a type checker. Each discovery costs ~350-500 tokens (re-read file, trace error, locate fix, apply, re-test). A type checker surfaces them all in one pass for ~300 tokens.

None of these individually cause a death spiral. Together they degrade the codebase into a state where the *next* modification is harder, the *next* bug is harder to trace, and the agent's effective duty cycle drops further. This is the compounding effect that single-session token counts do not capture.

### Supervision Cost Profile

LintGate's supervision cost is not constant — it decreases over a session as the agent's codebase stabilizes. The delta-first output system means subsequent runs report only what changed since the last check.

**Lint pipeline (hook):**

| Phase | Cost per run | Notes |
|---|---|---|
| Compact lint report | ~275 tokens | Summary + counts + next_actions |
| Auto-fix (when issues exist) | ~100-150 tokens | One call fixes all safe issues |
| Drill-down (when needed) | ~300-500 tokens | On demand — only for non-auto-fixable issues |
| Clean pass | ~50 tokens | `{}` — silent when nothing is wrong |

**ControlPlane (full mesh):**

| Run | Cost | Why |
|---|---|---|
| First run | ~500-800 tokens | Full compact: coherence + counts + inline blocking issues + next_actions |
| Subsequent runs | ~200-400 tokens | Delta only: new/escalated/resolved/still-active |
| Drill-down | ~300-600 tokens | On demand via `controlplane_get_details` |

For a general development cycle producing ~500 LoC/session:

| Pipeline | Tokens/Session | Supervision Cost | Supervision % |
|---|---|---|---|
| No quality gate | ~8,500 | 0 | 0% (but ~64% lost to discipline failures) |
| LintGate v0.2 (hook) | ~10,800 | ~2,300 | 21% (deterministic, shrinks over session) |
| LintGate v0.2 + ControlPlane | ~12,500 | ~4,000 | 32% (includes behavioral drift detection) |

The ControlPlane overhead on a 500 LoC session is roughly the cost of two agent re-reads of a 200-line file. In the unsupervised calibration session, the agent re-read the same files 4-5 times because it kept forgetting what it had already learned. Two file reads worth of supervision to prevent five file reads worth of amnesia is a net token reduction, not a cost.

### Scaling

Compact output scales with *finding count*, not *codebase size*. A 10,000 LoC project with 5 lint findings produces the same ~275-token compact response as a 500 LoC project with 5 findings. What scales linearly: the number of hook firings per session (more files changed → more runs). What stays constant: the token cost per run.

The duty cycle benefit scales *better* than linearly with session length. Short sessions (< 30 minutes) rarely enter death spirals — the agent does not have time to accumulate enough drift. Long sessions (2+ hours) are where unsupervised agents reliably degrade, and where the supervision multiplier is highest. The calibration session's 4x efficiency loss appeared between hours 1 and 3 — exactly the window where the behavioral compass and coherence engine provide the most value.

### The Actual Economic Claim

The naive framing is: "LintGate costs 18% more tokens and catches more bugs." The accurate framing is: **LintGate converts the agent's token budget from ~36% effective utilization to ~78% by offloading discipline to a deterministic layer that is better at it and cheaper at it.**

The agent is the most expensive component in the pipeline. It is good at novel reasoning — architecture, design, implementation of things it has never seen before. It is bad at discipline — remembering what failed, checking its work, maintaining consistency across files, noticing when its strategy has drifted. These are precisely the things that deterministic systems are best at and cheapest at. The 18% supervision cost displaces token spend that was going to waste at 3-4x the rate — which means the net effect is not +18% overhead but something closer to -50% total cost for equivalent useful output, because the supervised agent produces that output without the dead time.

---

## Habit Mode: Context Window Management

The Token Efficiency analysis above identifies the core economic problem — unsupervised agents waste ~64% of their token budget on discipline failures. But there is a second economic problem that supervision alone does not solve: **context window degradation during sustained execution.**

When an agent enters a long refactoring session — the edit-test-fix loop that produces value — the context window fills with stale file reads, raw test output, superseded diffs, and intermediate reasoning that was useful ten minutes ago but is now ballast. The model gets progressively dumber as the session continues, not because its reasoning degrades, but because its attention is diluted across irrelevant context. When Claude Code's automatic compaction fires, it does a generic lossy summarization that discards structured state LintGate already tracks.

The insight: LintGate already knows what matters. It tracks the behavioral trajectory, the active constraints, the lint state, the project's theory, the recurring issues, the coherence state. It should control what survives compaction — shedding ballast and injecting focused tool guidance. This turns context loss into context *refinement.* The model gets a 3,000-token distillation of everything it learned, and the stale reads are gone. The deterministic system remembers so the stochastic system does not have to.

### Architecture

Habit Mode operates as a "supra-tool" — mostly passive, called by the LintGate system itself on every tool event. The architecture is shared core + storage adapters:

```
Tool Event (hook)
  │
  ├─ Signal Collector     ← 8 signals from sliding window of 20 events
  │    Pure arithmetic: tool types, intents, timestamps, file paths
  │
  ├─ Mode Detector        ← Weighted composite score (0.0-1.0)
  │    Hysteresis: enter at 0.70 × 5 calls, exit at < 0.40
  │    User message grading: directive / continuation / clarification
  │
  ├─ Token Tracker        ← Character-count heuristic + periodic API calibration
  │    Compaction gate: 4 conditions (threshold + active + cooldown + delta)
  │
  └─ Compaction Strategy  ← 10-section structured snapshot (~3000 tokens)
       Theory digest + lint state + constraints + tool guidance
       Hard cap: 12,000 chars. Deterministic truncation.
```

**Dual persistence** — works with or without session_memory:
- **Path A** (session-backed): Piggybacks on the behavioral compass and session memory infrastructure. Rich signals from `compass.action_history`. State stored in `session.behavior_compass["habit_mode"]`. Fires inside `_record_behavior_event()` after compass save, before session save.
- **Path B** (standalone file-backed): Lightweight path when `session_memory` is disabled. Maintains a 30-entry action ring buffer at `~/.claude/lintgate/habit_state/<project_hash>.json`. Uses `quick_intent()` for tool classification instead of the full intent resolver.

Both paths call identical core functions: `update_signals()` → `compute_habit_score()` → `update_mode()` → `build_compaction_snapshot()`. No behavioral divergence between paths — only storage differs.

All signal computation, score calculation, mode transitions, and compaction logic live in pure functions in `lintgate/habit_mode.py`. No LLM calls, no subprocess calls, no file I/O in the hot path.

### Signal Collector

Eight signals computed from a sliding window of 20 recent tool events:

| Signal | Computation | What It Indicates |
|--------|------------|-------------------|
| `read_edit_ratio` | Reads / max(Edits, 1) | Low ratio (< 2.0) = editing more than reading = execution phase |
| `execute_pct` | % of calls with modify/verify/execute intent | High (> 0.5) = execution-heavy work |
| `edit_streak` | Consecutive Edit/Write/MultiEdit at end of window | 3+ = sustained modification cadence |
| `sub_agent_freq` | % of calls that are Task tool | Low (< 0.05) = not delegating = focused single-thread work |
| `inter_tool_gap_median` | Median seconds between last 10 calls | Fast (< 3s) = rapid execution rhythm |
| `same_file_ratio` | Proportion of file ops targeting already-seen files | High (> 0.6) = working in known territory |
| `gather_pct` | % of calls with inspect/unknown intent | Complementary to execute_pct — low = not exploring |
| `test_in_last_n` | pytest/test keyword in recent Bash calls | Boolean — test loop is active |

Tool classification uses frozen sets: `_INSPECT_TOOLS` = {Read, Grep, Glob, WebFetch, WebSearch}, `_MODIFY_TOOLS` = {Write, Edit, MultiEdit, NotebookEdit}, `_META_TOOLS` = {Task, TodoWrite, AskUserQuestion}. Intent categories are reused from the existing `command_normalization` module.

Pure arithmetic — no LLM calls, no string heuristics beyond tool name matching and keyword detection.

### Mode Detector

Weighted composite score (0.0-1.0) with hysteresis to prevent mode-flapping during mixed read-edit sequences:

| Signal | Weight | Full (1.0) | Half (0.5) |
|--------|--------|------------|------------|
| `read_edit_ratio` | 0.25 | < 2.0 | < 3.0 |
| `execute_pct` | 0.20 | > 0.5 | > 0.3 |
| `edit_streak` | 0.15 | ≥ 3 | ≥ 2 |
| `sub_agent_freq` | 0.10 | < 0.05 | < 0.15 |
| `inter_tool_gap` | 0.10 | < 3s | < 5s |
| `same_file_ratio` | 0.10 | > 0.6 | > 0.4 |
| `declared` | 0.10 | True | — |

**Mode transitions**:
- **Enter**: `habit_score >= 0.70` sustained for 5 consecutive events. The sustain gate prevents false triggering during short bursts of editing within an otherwise exploratory session.
- **Exit**: `habit_score < 0.40` (instant) OR user directive (instant reset to score 0.0).
- **Declaration**: `declare_mode("habit")` bypasses the sustain gate entirely — immediate entry. `declare_mode("standard")` is unconditional exit.

**User message grading** — the "conscious attention collapses subconscious habit" mechanism. Three categories with graded response:

- **Directive** (stop, wait, actually, long messages > 50 chars, multi-sentence): Catastrophic reset — active→False, sustain_counter→0, declared→False, score→0.0. The agent's user has re-engaged with new intent.
- **Continuation** (yes, ok, proceed, short confirmations ≤ 15 chars): **No effect.** These are expected between refactoring steps and should not collapse execution flow.
- **Clarification** (short questions ending in ?): Gentle decay (−0.15 from habit_score). The user is checking in, not redirecting.

Classification is by cheap heuristics: message length, keyword matching, sentence counting, ?-detection. The `_classify_user_message()` helper returns the type string. This prevents the common failure mode where a quick "ok" confirmation between refactoring steps collapses the agent out of habit mode.

Implementation: `lintgate/habit_mode.py` (`HabitSignals`, `HabitModeState`, `update_signals`, `compute_habit_score`, `update_mode`, `signal_user_message`, `declare_mode`).

### Token Tracker

The token tracker estimates context window consumption and determines when compaction should trigger. All logic lives in `lintgate/token_tracker.py`.

**Per-call estimation**: Serialize tool input + output to string, count characters, multiply by `calibration_factor` (default 0.25 — 1 token per 4 characters). Update running totals: `estimated_tokens_used`, `char_count_total`, `tool_call_count`. Classify tool as LintGate (matches `_LINTGATE_TOOL_PREFIXES`) vs external. Track `lines_written` for Write/Edit tools (count newlines).

**API calibration**: Every ~15 tool calls (configurable), call Anthropic's free `POST /v1/messages/count_tokens` endpoint with a 500ms hard timeout. Compute new calibration factor from ground truth and blend 70% new + 30% old for smooth adjustment. Reset `estimated_tokens_used` to the API's ground truth value.

**Resilience**: Exponential backoff on consecutive API failures — effective interval becomes `interval × 2^min(failures, 3)`, so after 3 consecutive failures the check interval grows from 15 to 120 calls. Counter resets on next success. All API calls wrapped in `contextlib.suppress(Exception)`. If the API is unreachable, the character-count heuristic continues operating without calibration — degraded accuracy but zero latency impact.

**Anti-thrash compaction gate**: `should_compact()` requires ALL four conditions to be true:

1. `estimated_tokens / context_window_size > threshold` (default 0.40 — aggressive, 40% of window)
2. Habit mode is active
3. `tool_calls_since_compact >= 20` (minimum calls between compacts — prevents rapid re-compaction)
4. `estimated_tokens - last_compact_tokens >= 15,000` (delta gate — don't compact if barely anything was added since last time)

**Economics tracking**: Total tool calls, LintGate vs external breakdown, lines written, compaction count, calibration count. Summary available via `habit_status` MCP tool and integrated into telemetry.

### Compaction Strategy

The compaction snapshot is a structured JSON document with 10 sections, ~3,000 tokens (hard cap 12,000 characters), designed for post-compact context injection. This is the core innovation: the model loses stale reads and superseded diffs but keeps the project's conceptual DNA, active constraints, and targeted tool guidance.

| # | Section | Budget | Content |
|---|---------|--------|---------|
| 1 | `mode` | 50 tok | Active state, habit score, declared, compaction number |
| 2 | `active_context` | 100 tok | MRU files (cap 10, basename fallback if paths too long), last test status |
| 3 | `theory_digest` | 1500 tok | Full theory pack from `build_theory_pack` — the project's stated values, anti-patterns, enforceable rules |
| 4 | `lint_state` | 400 tok | Blocking/warning counts, top 5 blocking issues (messages truncated to 80 chars) |
| 5 | `behavioral_trajectory` | 300 tok | Top 3 constraints by confidence, top 2 error signatures by frequency, prediction recall |
| 6 | `recurring_issues` | 150 tok | Top 3 recurrent issue patterns from issue memory |
| 7 | `session_history` | 100 tok | Last 2 coherence snapshots (coherence state, blocking count, finding count) |
| 8 | `coherence_trajectory` | 30 tok | Last 3 coherence states |
| 9 | `token_state` | 50 tok | Estimated tokens used, total tool calls, lines written |
| 10 | `tool_guidance` | 300 tok | Max 4 conditional tool injection recommendations |
| | `focus_directive` | (inline) | "You are in Habit Mode. Focus: [files]. Test: pass/fail." |

**Deterministic truncation**: If the serialized snapshot exceeds 12,000 characters, sections are truncated in priority order — low-value sections shed first: `session_history` → `recurring_issues` → `behavioral_trajectory` → `lint_state` → `coherence_trajectory`. The high-value sections — `mode`, `active_context`, `token_state`, `tool_guidance`, and `theory_digest` — are **never truncated**. They are the minimum viable compaction context.

The theory digest (1,500 tokens of the project's conceptual DNA) always survives compaction. This means the post-compact model retains the project's stated values, problem-solving philosophy, and enforceable rules — exactly the context that prevents the behavioral drift the Token Efficiency analysis documents.

### Tool Injection Engine

The compaction snapshot includes conditional tool recommendations (max 4) based on behavioral metrics. This is the "sleeper hit" — it surgically targets the model's post-compact attention toward the most impactful LintGate tools given the session state:

| Condition | Injected Tool | Priority |
|-----------|--------------|----------|
| `edit_streak > 5` AND no test in window | `prediction_register` | 1 |
| Blocking lint issues > 3 | `lint_fix` | 1 |
| 2+ recent failed approaches | `constraint_check` | 1 |
| Recent coherence state = "systemic" | `controlplane_run` | 2 |
| (always) | `habit_status` | 3 |

Injections are sorted by priority and capped at 4. Each includes a reason (capped at 80 characters). The post-compact model sees not just "what you were working on" but "what you should do next" — converting the compaction event from a disruption into a course correction.

### Habit Mode Configuration

```yaml
controlplane:
  habit_mode:
    enabled: true           # Master switch (default: true)
    auto_detect: true       # Automatic mode detection from signals
    compact_threshold: 0.40 # Compact at this % of context window
    enter_score: 0.70       # Score threshold to enter habit mode
    exit_score: 0.40        # Score threshold to exit habit mode
    sustain_calls: 5        # Must sustain enter_score for N consecutive calls
    token_api_interval: 15  # API calibration every N tool calls
```

All values can be overridden at runtime via `habit_configure` (session-scoped, clamped to safe ranges). The system works with or without `session_memory` — Path A for full integration, Path B for standalone operation. Both paths read the same config and call the same core functions.

Implementation: `lintgate/habit_mode.py` (core), `lintgate/token_tracker.py` (token estimation + calibration), `mcp_tools/habit_tools.py` (4 MCP tools), `lintgate/hook_posttooluse.py` (Path A + Path B integration).

---

## Configuration

LintGate works with **zero config** for any Python project with ruff installed. For project-specific tuning, create `.claude/lintgate.yaml`:

```yaml
# Pipeline-critical paths get Tier 3 on structural changes
pipeline_critical_paths:
  - "src/control/pipeline.py"
  - "src/cassette/"

# Reclassify specific codes
severity_overrides:
  E501: "informational"

# Known debt (tracked, not blocking)
exemptions:
  complexity:
    "role_confluence.py":
      reason: "Pre-existing CC=48, tracked in backlog"
      ticket: "CRUSADE-42"

# Linter-specific configuration
linters:
  structure_checker:
    max_function_args: 5
    max_class_attributes: 8
    cognitive_complexity_threshold: 12
    max_file_lines: 300
  dead_code_checker:
    severity: "warning"
    min_confidence: 70
  architecture_checker:
    layers:
      - name: "presentation"
        modules: ["app.views"]
        must_not_import: ["app.db"]
    max_module_exports: 15
  context_auditor:
    max_lines_warn: 300
    max_lines_error: 500
    staleness_days: 30

# Tool version expectations
tool_versions:
  python: ">=3.10"
  ruff: ">=0.4,<0.7"
  mypy: ">=1.8"

# Debounce intervals
debounce:
  tier_2_interval_s: 3
  tier_3_interval_s: 5

# ControlPlane (opt-in)
controlplane:
  enabled: false
  latency_budget_ms: 120000  # 2 min default; MCP surface scales dynamically with project size
  advisory_default: true
  session_memory: true
  session_max_age_hours: 4.0
  constraint_proposal_threshold: 5
  channels:
    lint:
      enabled: true
      blocking: true
    tests:
      enabled: true
      blocking: false
      timeout_ms: 10000
      symbol_coverage:
        enabled: true
        # "changed" = only functions touched in git diff (default)
        mode: changed
        # Diff base: "HEAD" for local MCP (default)
        diff_base: HEAD
        # Required symbols: always checked regardless of mode
        required_symbols:
          - "lintgate/hooks/pre_tool.py::handle"
        # Waivers: explicit per-symbol exemptions
        waivers:
          - symbol: "lintgate/config.py::_auto_detect"
            reason: "Environment-dependent; tested via integration tests."
            expires: "2025-09-01"
      settings:
        bootstrap_on_zero_tests: true       # Auto-trigger bootstrap on cold-start
        bootstrap_output_dir: "tests/generated"  # Isolated namespace for generated tests
    deps:
      enabled: true
    git:
      enabled: true
    behavior:
      enabled: true
      timeout_ms: 500
      thresholds:
        approach_cycling_count: 3
        approach_cycling_window_min: 30
        failure_amnesia_lookback: 30
        premature_action_ratio: 3.0
        premature_action_failure_rate: 0.5
        brute_force_approach_gap: 0
        consecutive_bash_failures: 3
        tool_repetition_count: 4
        tool_repetition_window_min: 30
        verification_debt_streak: 8
        stale_model_approach_changes: 2
        signal_cooldown: 10
        escalation_threshold: 3
      settings:
        # Override intent mappings for project-specific tools
        intent_map:
          cargo: "execute"
          rustfmt: "modify"
        intent_sig_map:
          "cargo:test": "verify"
          "cargo:build": "execute"
        # Tune bias weights (defaults shown)
        bias_weights:
          verification_debt_bias: 0.20
          failure_amnesia_bias: 0.15
          serial_discovery_early_bias: 0.10
          stale_model_bias: 0.15
  # Global behavior profile (cross-session learning)
  global_memory:
    enabled: true
    alpha: 0.6           # Initial weight for global priors (0-1)
    decay_horizon: 50    # Events before alpha reaches 0
    ttl_days: 90         # Prune profile data older than this
  # Habit Mode (context window management, default enabled)
  habit_mode:
    enabled: true           # Master switch
    auto_detect: true       # Auto-detect from signals (vs. declaration-only)
    compact_threshold: 0.40 # Compact trigger: estimated_tokens / context_window
    enter_score: 0.70       # habitScore threshold to enter (0.0-1.0)
    exit_score: 0.40        # habitScore threshold to exit
    sustain_calls: 5        # Consecutive events above enter_score to enter
    token_api_interval: 15  # API calibration every N tool calls
  # Architecture of Inquiry (opt-in)
  inquiry:
    theory_grounded_signals: false
    prediction_tracking: false
    theory_coherence_check: false
    living_context: false
    session_gate: false
```

---

## Installation

LintGate is installed as a Claude Code PostToolUse hook. The hook fires on Write, Edit, MultiEdit, and Bash tool uses across all projects.

### Quick Start

```bash
# Create venv and install LintGate with MCP support + all linters
cd /path/to/lintgate
uv venv .venv && uv pip install -e ".[dev]"
# Or minimal: uv pip install -e ".[mcp]" && uv pip install ruff
```

LintGate auto-detects installed linters and enables them. No configuration needed beyond the install.

### PostToolUse Hook

Configure in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit|MultiEdit|Bash",
      "hooks": [{
        "type": "command",
        "command": "/path/to/lintgate/.venv/bin/lintgate"
      }]
    }]
  }
}
```

The hook fires automatically on every code modification tool use. It reads JSON from stdin (tool_name, tool_input, tool_output, cwd), runs the lint pipeline, and writes structured JSON to stdout. Silent on clean passes (`{}`).

### MCP Server

Configure in `~/.mcp.json` (global) or `.mcp.json` (project-level):
```json
{
  "mcpServers": {
    "lintgate": {
      "command": "/path/to/lintgate/.venv/bin/lintgate-mcp",
      "args": []
    }
  }
}
```

**Important**: Always use the venv-installed entrypoint, not a system binary. The venv contains `mcp` and all linter packages.

`lintgate-mcp` and `controlplane-mcp` are equivalent entry points (both serve the same MCP tool surface).

---

## Ship Pipeline & GitHub App Architecture

LintGate's ship pipeline is designed around a single principle: **the LLM's job ends at `git push`**. Everything after push is deterministic infrastructure that requires no inference, no context window, and no human attention.

### Local Gate Stack

Before code leaves the machine, the pre-push hook (`.githooks/pre-push`) runs a configurable gate stack defined in `gate_contract.yaml`:

- **Toolchain gate**: Verify all required CLI tools are installed and version-compatible
- **Infrastructure gate**: Validate CI config, badge references, doc freshness
- **Qlty gate**: Run ruff, bandit, shellcheck, trivy, trufflehog via qlty
- **Secrets gate**: Scan staged diffs for leaked credentials
- **Tests gate**: Full pytest suite (skippable via `LINTGATE_GATE_ID` for known-safe pushes)

The gate stack is the quality floor. Code that passes it has been locally validated across lint, security, and correctness dimensions.

### GitHub App: `lintgate[bot]`

After push, the LintGate GitHub App receives webhooks and orchestrates the post-push lifecycle:

```
git push (agent's terminal action)
    │
    ├─ push webhook → create/update PR, add bot as required reviewer
    │
    ├─ check_suite completed → read gate_contract.yaml
    │   ├─ all green → approve PR, squash-merge, GPG-sign commit
    │   └─ any red → post structured failure diagnosis
    │
    ├─ pull_request merged → delete head branch, trigger post-merge workflows
    │
    └─ schedule (weekly) → stale branch cleanup, dependency freshness
```

The App operates as its own GitHub identity with fine-grained permissions, installation tokens (short-lived, auto-rotated), and GPG-signed merge commits. It replaces the poll-based monitoring loop in `ship_main.py` with event-driven webhook handling via a Cloudflare Worker.

### Finding Triage via GitHub Models

When CI checks fail, the App classifies findings using GitHub's free model tier (GPT-4o-mini):

| Classification | Response Strategy |
|---|---|
| **Mechanical** (formatting, imports) | Create issue with `auto-fix` label + exact ruff/ast command |
| **Structural** (design, architecture) | Create issue with `needs-reasoning` label + theory context |
| **Security** (vulnerabilities, secrets) | Create issue immediately with `security` label |
| **False positive** | Log to suppression ledger for pattern learning |

The classification determines the response strategy, not the fix. Mechanical fixes are applied by local deterministic tools (ruff, AST transforms) without LLM inference. Structural findings are routed to the local agent's next session with attached theory context.

### Cross-Talk Feedback Loop

The full cycle keeps LLM inference to the minimum:

1. Agent pushes code (terminal action — LLM done)
2. CI runs (deterministic)
3. App classifies findings (free LLM tier, ~0.1s per finding)
4. App creates structured issues (deterministic)
5. Local deterministic tools apply mechanical fixes (no LLM)
6. Agent reasons about structural findings in next session (LLM, but targeted)
7. App validates and merges (deterministic)

Steps 1–5 and 7 require zero LLM inference cost. Step 6 only fires for findings that genuinely require reasoning, and the structured issue provides exactly the context needed to reason efficiently.

### Semiotic Proof Chain

The App architecture completes a self-referential proof system:

1. LintGate enforces quality gates on code that passes through it
2. LintGate's own code passes through those same gates
3. The README badges are generated by those gates running on this codebase
4. The merge commits are GPG-signed by `lintgate[bot]`, proving no human bypassed the gates
5. The issues are auto-generated by the App's finding triage, demonstrating the pipeline works

Every badge, commit signature, and auto-generated issue is a live demonstration of LintGate's capabilities operating on itself. The evidence is cryptographic (GPG signatures), empirical (CI status), and publicly auditable (issue history + commit log).

### Configuration: `gate_contract.yaml`

The existing `gate_contract.yaml` serves double duty — it configures the local pre-push gate stack and the App's required checks:

```yaml
required_checks:
  - "Tests (3.11)"
  - "Tests (3.12)"
  - "SonarQube Cloud Scan"

merge_strategy: squash
delete_branch_on_merge: true
stale_branch_days: 30
protected_branches:
  - main
  - badges
```

No separate App config needed. The contract that the local pipeline enforces is the same contract the App enforces — one source of truth, two enforcement points.

---

## Design Philosophy

**Lossy lenses, not omniscient analysis.** Every tool in LintGate is a bad instrument. Ruff can't reason about architecture. Mypy can't detect design drift. The complexity checker has no idea what your project is trying to be. But their errors are *uncorrelated* — they fail in different ways, on different axes, for different reasons. When you run them all on the same codebase and read the *pattern* of results, you get something that looks like a senior engineer's intuition: "the type errors and the failing tests and the complexity spike are all pointing at the same module — that's where the real problem is." This is the core trick. It is very cheap. It requires no LLM calls. And it works because disagreement between independent lossy channels is diagnostic in a way that no individual channel can be.

**The habits, not the engineer.** LintGate doesn't try to be smart. It tries to be *habitual* — catching the things that a competent senior engineer catches reflexively, not through brilliance but through discipline. Check imports after refactoring. Verify types after changing signatures. Notice when complexity creeps. Remember that the same mistake happened last Tuesday. These habits cost almost nothing to automate and prevent the expensive cascading failures that happen when nobody is paying attention.

**Silent success.** When there's nothing to report, the output is `{}`. No "all checks passed" noise. The developer should forget LintGate exists until it has something worth saying.

**Aggressive defaults.** Tier 2 for ordinary code changes. In the AI-first paradigm, the cost of a missed error compounds faster than the cost of running one extra linter. Default to catching things, not to being quiet.

**Graceful degradation.** Every linter uses `shutil.which()` guards. Missing tools are silently skipped, never crash. A partial run is infinitely more valuable than a crashed hook that blocks the agent.

**Zero-config usability.** Works on any Python project with just ruff installed. Every additional tool is additive. Configuration exists for tuning, not for bootstrapping.

**Alert, don't automate.** Pattern alerts are informational. Constraint proposals require explicit acceptance. Repair actions require opt-in. The system provides signal — humans and agents make decisions.

**Silent channels are diagnostic.** Borrowed from OTP supervision: a channel that finds nothing is not just "clean" — it's actively narrowing the space of possible problems. The ControlPlane's coherence engine treats silence as first-class evidence.

**Co-construction, not instruction.** The `constraint_check` tool does not tell the agent what its constraints are. It asks the agent to state its own model, then computes the gap. The diagnostic signal is the *difference* between what the agent thinks it knows and what the system has recorded. This is more effective than broadcasting constraints — an agent that generates its own constraint model is more likely to actually use it, and the gap computation catches blind spots that a static constraint list would miss.

**Constraints are hypotheses, not facts.** The behavioral compass treats every constraint as a live hypothesis with a confidence score, evidence for and against, and a decay rate. A constraint learned from a failure starts at low confidence (0.3) and requires confirming evidence to promote. A constraint declared via `constraint_check` starts at moderate confidence (0.5). Constraints that go untested for hours decay toward zero. This prevents stale constraints from polluting the model and ensures the compass reflects the agent's *current* understanding, not a historical artifact.

**Report weather, don't make judgments.** Behavioral findings describe patterns — "3 approaches attempted in 20 minutes, all failed" — not prescriptions. The agent decides what to do about it. Hard signals trigger `constraint_check` nudges (suggestions, not mandates). Soft signals are informational only. This matters because behavioral drift detection is inherently noisier than code analysis, and false positives that sound like commands erode trust faster than false positives that sound like observations.

**Bias, don't gate.** The intent taxonomy classifies every tool use but never blocks or overrides a detection rule. Intent data adjusts confidence via bounded deltas (±0.25 cap), not binary filters. An agent running 8 consecutive modify commands without verification gets a higher-confidence `verification_debt` finding, but the finding still fires on the same threshold — intent makes it louder, not mandatory. This keeps the system advisory and prevents false positives from becoming false mandates.

**Warm start, then fade.** Cross-session learning provides priors, not truth. Global behavioral patterns (signal frequencies, nudge acceptance rates) give the system a head start on a new session — but alpha decays to zero as local data accumulates. After 50 local events, the session is fully autonomous. Global influence is bounded (±0.10 per signal), layered inside existing bounds (±0.25 bias cap), and requires minimum sample sizes (3 sessions) before activating. The system never lets stale cross-session data override fresh local evidence.

**Fail-safe persistence.** All state (issue memory, pattern bank, session memory, behavioral compass, global behavior profile, dep churn history) is written best-effort. A corrupted state file starts a fresh session. Persistence is observability, not correctness — the system functions identically with or without prior state.

**Self-referential proof.** The strongest credibility signal for a quality tool is using itself on itself and making the results public. Every badge on the README, every GPG-signed merge commit, every auto-generated issue is a live artifact produced by LintGate operating on its own codebase. This is not marketing — it is the logical consequence of the architecture. If the tool works, the proof accumulates automatically. If it doesn't, the badges break. The semiotic chain is: enforcement → self-application → public evidence → verifiable trust.

---

## Design Lineage

LintGate pulls patterns from four projects that explored different facets of the same problem: how do you build reliable feedback loops between code generation and code validation?

**From TailChasingFixer**: The plugin protocol where every analyzer implements `run(ctx) -> Iterable[Issue]` against a shared context, composable and independently deployable. The confidence scoring model (0.0–1.0) and the `IssueCollection` pattern (filter, deduplicate, sort by severity) that became the results aggregator.

**From ShortcutForge's DSL linter**: Phased processing where each phase accumulates changes but does not depend on the others succeeding. If the change classifier misclassifies something, the tier selector has a safe fallback. If a linter times out, the aggregator still reports what the others found.

**From ARC's lint.py**: The blocking/warning/informational severity split, the `PIPELINE_PATHS` pattern where certain files get stricter analysis based on architectural role, the exemptions system for known debt with an audit trail, and the `shutil.which()` guards that let every linter degrade gracefully when its external tool is not installed.

**From Grail's hidden compass protocol**: The co-construction principle — where the system asks the agent to generate its own constraint model rather than broadcasting constraints to it, and the diagnostic signal is the gap between the agent's stated model and the system's recorded model. The hypothesis-with-confidence pattern (constraints as live claims that strengthen, weaken, and decay rather than static facts). The approach of treating behavioral observations as weather reports (descriptive, not prescriptive) to avoid the trust-erosion that comes from false-positive commands. And the specific detection rules — approach cycling, failure amnesia, premature action — which were calibrated against a real baseline session where an agent spent 3.5 hours on a problem with 0 successes, 83% predictable constraints, and ~90 minutes spent on doomed approaches.

---

## Project Structure

```
lintgate/
├── lintgate/                        # Core package
│   ├── hook_posttooluse.py          # PostToolUse hook entry point
│   ├── change_classifier.py         # Phase 1: What changed and how risky
│   ├── tier_selector.py             # Phase 2: Which linters to run
│   ├── lint_runner.py               # Phase 3: Parallel execution with timeouts
│   ├── results_aggregator.py        # Phase 4: Deduplicate, exempt, split severity
│   ├── agent_reporter.py            # Phase 5: Format for agent consumption
│   ├── registry.py                  # Linter discovery and registration
│   ├── config.py                    # YAML config loading + auto-detection
│   ├── types.py                     # Shared types (LintIssue, ChangeClassification, etc.)
│   ├── state.py                     # Persistent issue memory, metrics, run details
│   ├── telemetry.py                 # ROI telemetry aggregation
│   ├── lint_fixer.py                # Auto-fix via ruff (safe rules + format)
│   ├── pattern_bank.py              # Categorical anti-tail-chasing
│   ├── context_guidance.py          # CLAUDE.md/AGENTS.md parsing
│   ├── context_auditor.py           # Context file health checks + session readiness
│   ├── bootstrap_defaults.py        # Battle-tested zero-state anti-patterns + facet fallbacks
│   ├── context_bootstrap.py         # Context file generation + managed sections + patches + model-aware bootstrap
│   ├── theory_extractor.py          # Project theory profiling (6 facets + enforceable rules)
│   ├── compass.py                   # 4-axis compass data model, depth scoring, gap report
│   ├── compass_io.py                # Compass load/save/migrate (YAML persistence)
│   ├── axis_extractor.py            # Theory → compass axis mapping + heading signals
│   ├── gap_detector.py              # Gap detection + interview templates (12 questions)
│   ├── code_inference.py            # Multi-signal code inference (pyproject, imports, AST, git)
│   ├── reset.py                     # Scoped state deletion (compass/session/project/global)
│   ├── versioning.py                # Tool version auditing
│   ├── dependency_health.py         # Dependency health monitoring + manifest quality
│   ├── hygiene.py                   # Command-class hygiene prechecks
│   ├── habit_mode.py                # Habit Mode: signals, scoring, compaction, dual persistence
│   ├── token_tracker.py             # Token estimation, API calibration, compaction economics
│   ├── linters/                     # 18 linter implementations
│   │   ├── base.py                  # Base linter protocol
│   │   ├── ruff_linter.py           # ruff check + format
│   │   ├── mypy_linter.py           # Type checking
│   │   ├── ty_linter.py             # Type integrity (Astral's ty)
│   │   ├── import_checker.py        # Hallucinated import detection
│   │   ├── complexity_checker.py    # Cyclomatic complexity (radon)
│   │   ├── structure_checker.py     # God functions, god classes, nesting (with AST function cache)
│   │   ├── structure_checks/        # Structure sub-checks
│   │   │   ├── file_checks.py       # File size, structure limits, cohesion + co-change annotations
│   │   │   ├── function_checks.py   # Args, locals, statements, returns, nesting, CC
│   │   │   ├── class_checks.py      # Attributes, methods, parents
│   │   │   ├── cohesion_analysis.py  # File cohesion scoring and split proposals
│   │   │   ├── cochange_analysis.py  # Git co-change coupling annotations for split proposals
│   │   │   ├── dependency_clustering.py # Variable dependency clustering for extraction proposals
│   │   │   ├── import_tracing.py     # Transitive import analysis + E402 evidence
│   │   │   └── ast_cache.py          # Per-function analysis cache (session-scoped, import-aware)
│   │   ├── cognitive_complexity.py  # Understanding difficulty
│   │   ├── architecture_checker.py  # Layer violations, circular imports
│   │   ├── dead_code_checker.py     # Unused code (vulture)
│   │   ├── performance_checker.py   # Performance anti-patterns (AST-based)
│   │   ├── bandit_linter.py         # Security vulnerabilities (full scan)
│   │   ├── bandit_fast_linter.py    # Security fast path (focused Tier 2)
│   │   ├── pip_audit_linter.py      # Supply-chain vulnerability scanning
│   │   ├── context_rule_checker.py  # CLAUDE.md/AGENTS.md rule enforcement
│   │   ├── version_checker.py       # Tool version validation
│   │   ├── redefinition_checker.py  # Duplicate definitions
│   │   └── custom_linter.py         # User-defined lint commands
│   ├── modes/                       # Cognitive mode system
│   │   ├── mode_state.py            # CognitiveMode enum, ModeState, transition rules
│   │   └── execution_compass.py     # Frozen directive state + alignment checking
│   ├── hooks/                       # Claude Code hook handlers (6 events)
│   │   ├── __init__.py              # Shared utilities (load compass, load session, JSON I/O)
│   │   ├── session_start.py         # SessionStart: inject compass + staleness
│   │   ├── user_prompt.py           # UserPromptSubmit: mode indicator + theory-relevance
│   │   ├── pre_tool.py              # PreToolUse: theory-mode advisory + alignment checks
│   │   ├── stop_gate.py             # Stop: compass coverage report (advisory only)
│   │   ├── pre_compact.py           # PreCompact: checkpoint + compass capsule emission
│   │   └── session_end.py           # SessionEnd: persist compass if modified
│   ├── renderers/                   # Multi-model context file renderers
│   │   ├── __init__.py              # RendererRegistry, Renderer protocol, auto-detection
│   │   ├── _helpers.py              # Shared rendering utilities
│   │   ├── claude.py                # CLAUDE.md + .claude/rules/theory.md
│   │   ├── cursor.py                # .cursor/rules/compass.mdc
│   │   ├── copilot.py               # .github/copilot-instructions.md
│   │   ├── windsurf.py              # .windsurf/rules/compass.md + dynamic files
│   │   ├── cline.py                 # .clinerules/compass.md
│   │   ├── aider.py                 # CONVENTIONS.md
│   │   ├── agents_md.py             # AGENTS.md (cross-tool standard)
│   │   └── generic.py               # CONTEXT.md (universal fallback)
│   ├── controlplane/                # ControlPlane supervision mesh
│   │   ├── types.py                 # SupervisionEvent, MeshResult, CoherenceResult
│   │   ├── channel.py               # Channel protocol (ABC)
│   │   ├── runtime.py               # Parallel channel execution + shedding
│   │   ├── coherence.py             # Cross-channel coherence engine
│   │   ├── session_memory.py        # Cross-run state accumulation + behavioral compass storage
│   │   ├── behavior_compass.py      # Behavioral drift data model, hypothesis lifecycle, intent taxonomy
│   │   ├── constraint_proposer.py   # Pattern bank → theory refinement loop (incl. behavioral)
│   │   ├── global_behavior_profile.py # Cross-session behavioral priors (global memory)
│   │   ├── model_profiles.py        # Model-specific behavioral profiles + persistence
│   │   ├── model_probe.py           # Behavioral micro-task calibration probe engine (v2)
│   │   ├── reporter.py              # Mesh result → systemMessage formatting
│   │   ├── reporter_compact.py      # Compact output with bootstrap progress
│   │   ├── skeleton_generator.py    # Test skeleton generation
│   │   ├── test_archetype_selector.py # Test type selection heuristics
│   │   └── cli.py                   # CLI entry point
│   ├── orchestration/               # Pipeline orchestration
│   │   ├── bootstrap_state.py       # Resumable bootstrap state (lock, heartbeat, phases)
│   │   ├── bootstrap_pipeline.py    # Cold-start test generation pipeline
│   │   └── behavioral_contracts.py  # Deterministic contract generation from AST
│   ├── mutation/                    # Mutation testing subsystem
│   │   ├── engine.py                # MutationEngine (sampling + profiling)
│   │   ├── state.py                 # MutationStateManager + CoverageDepth + SpecificationLevel
│   │   ├── decomposition.py         # DecompositionCoordinator (static/dynamic/converged merge) + CATEGORY_PRESCRIPTION_MAP
│   │   ├── prescriptions.py         # PrescriptionEngine (category → action mapping) + classify_specification_level()
│   │   ├── test_generators.py       # Category-specific test template generators for mutation prescriptions
│   │   ├── automation.py            # MutationOrchestrator (Tier 1/2 background queue + debounce)
│   │   └── policy.py                # RuntimeBudget + MutationTelemetry
│   └── channels/                    # ControlPlane channel implementations
│       ├── lint_channel.py          # Wraps the linter pipeline
│       ├── test_channel.py          # Test discovery + execution
│       ├── dependency_channel.py    # Dependency health checks
│       ├── git_channel.py           # Git state analysis + secrets-in-diff scanning
│       ├── behavior_channel.py      # Agent behavioral drift detection (9 rules, intent bias, signal coordination)
│       ├── structure_channel.py     # Codebase structural analysis (STRUCT001-004: cycles, size, orphans, cohesion)
│       └── test_effectiveness_channel.py  # Test assertion quality analysis (TEFF001-007: mutation-informed)
├── mcp_server.py                    # MCP bootstrap + shared helpers
├── mcp_tools/                       # MCP domain modules (51 tool definitions)
│   ├── compass_tools.py             # compass_status, compass_update, compass_interview, compass_check,
│   │                                #   compass_reset, theory_mode_enter, theory_mode_freeze, setup_hooks
│   ├── habit_tools.py               # declare_mode, habit_status, habit_compact, habit_configure
│   └── ...                          # lint_tools, controlplane_tools, behavior_tools, context_tools, etc.
├── tests/                           # 55+ test files
│   ├── test_compass.py              # Compass data model, depth scoring, gap report
│   ├── test_compass_hooks.py        # Compass hook handlers
│   ├── test_axis_extractor.py       # Axis extraction + facet mapping
│   ├── test_gap_detector.py         # Gap detection + interview
│   ├── test_renderers.py            # Multi-model renderer output
│   ├── test_modes.py                # Cognitive mode transitions
│   ├── test_habit_mode.py           # Habit mode core logic (51 tests)
│   ├── test_token_tracker.py        # Token tracker logic (30 tests)
│   ├── test_module_contracts.py     # Interface parity gates
│   ├── test_regressions.py          # Bug regression tests
│   ├── test_lint_parity.py          # Linter output parity
│   ├── test_controlplane_kernel.py  # ControlPlane core tests
│   ├── test_coherence_history.py    # Trajectory-aware coherence
│   ├── test_session_memory.py       # Session persistence
│   ├── test_constraint_proposer.py  # Constraint proposal logic
│   ├── test_mesh_reporter.py        # Report formatting, fingerprint, delta, compact output
│   ├── test_controlplane_details.py # ControlPlane run cache + drill-down
│   ├── test_behavior_compass.py     # Behavioral compass, hypothesis lifecycle, intent taxonomy
│   ├── test_behavior_channel.py     # Detection rules, intent bias, signal coordination, global priors
│   ├── test_global_behavior_profile.py # Cross-session learning tests
│   ├── test_model_profiles.py       # Model profile system tests
│   ├── test_model_probe.py          # Calibration probe engine tests
│   ├── test_bootstrap_defaults.py   # Zero-state defaults tests
│   ├── test_performance_checker.py  # Performance anti-pattern detection tests
│   ├── test_doc_counts.py           # Count drift regression tests
│   ├── test_phase5_regressions.py   # Regression tests (scoping, defaults, integration)
│   ├── test_deliverable4_integration.py # D4: co-change, AST cache, import tracing, decomposition coordinator
│   ├── test_*_channel.py            # Per-channel tests
│   └── golden/                      # Golden fixture files
└── pyproject.toml                   # Package config (MIT, Python >=3.10)
```

---

## License

MIT

## Author

Rohan Vinaik
