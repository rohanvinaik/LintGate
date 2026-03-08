# Specification/Mutation System Audit (2026-03-08)

## Scope

This audit evaluates the **current active implementation** (not archived legacy mutation runtime) against:

1. The Specification Complexity paper (`specification_complexity_paper.md`)
2. `docs/mutation/mutation-theory.md`

Goal: identify why analysis can become computationally explosive and what to change to make the system practical, single-file-first, and theory-aligned.

---

## Executive Summary

The project already moved to a symbolic specification system (no active `mutmut` subprocess in runtime channels), but there is a critical discovery/scope mismatch that can still recreate catastrophic behavior:

- `spec_*` MCP tools use a custom project walk that includes `mutants/` artifacts.
- In this repository, `mutants/` contains **504 `.py` files / 49,712,226 lines**, while canonical project discovery is ~168,216 lines.
- This means `spec_*` tools can ingest ~**296x** more code than intended, then feed it into test-effectiveness/source mapping.

So the immediate problem is not only mutation execution; it is **unbounded file discovery + whole-project analysis paths** that do not honor single-file intent.

---

## Root Cause Validation

### Confirmed artifact size

- `mutants/`: `2.2G` on disk
- `archive/mutation_system/`: `29M`

### Confirmed trampolined bloat factors (same module sample)

| File | Clean | `mutants/` | Bloat |
|---|---:|---:|---:|
| `engine.py` | 969 lines | 45,150 lines | 46.6x |
| `prescriptions.py` | 277 lines | 39,829 lines | 143.8x |
| `predictor.py` | 465 lines | 24,980 lines | 53.7x |
| `decomposition.py` | 343 lines | 14,005 lines | 40.8x |

### Discovery inflation in current code paths

- Canonical discovery (`lintgate.discovery.discover_project_files`): **576 files**
- `specification_tools` custom walker: **1043 files**
- Top contributors in custom walker:
  - `mutants`: 504 files
  - `tests`: 258 files
  - `lintgate`: 246 files

Line volume:

- Canonical-discovered files: **168,216 lines**
- `specification_tools` discovery result: **49,869,086 lines**

This is the dominant practical cause of the memory/time explosion.

---

## Current Implementation Reality

### What is already true (good)

- Active runtime mutation subsystem is archived, not registered as active tools.
- Specification channel is symbolic-only (no subprocess, no source mutation, no test execution).
- Composition/risk/gate scaffolding exists in active code.

### What still causes practical failure

1. `spec_*` tools bypass canonical discovery and include mutation artifacts.
2. "Single file" is optional and not enforced.
3. ControlPlane prepass computes shared manifests project-wide, even when event scope is small.
4. No hard memory budget or mutant/artifact guard at the tool entrypoint.

---

## High-Severity Findings

### P0 — `spec_*` tool discovery includes `mutants/` and ignores canonical filters

- `mcp_tools/specification_tools.py:11-23` uses raw `os.walk` and excludes only `archive`/dot dirs.
- Canonical discovery already excludes `mutants` (`lintgate/discovery.py:42-45`).

Impact: symbolic analysis ingests trampolined artifacts and can become explosively expensive.

### P0 — Single-file mode is not truly single-file in `spec_analyze`

- `mcp_tools/specification_tools.py:65-76` discovers all files first, then filters by `file`.

Impact: large tree walk and path set construction still occur before filtering.

### P0 — ControlPlane prepass ignores requested file scope

- `lintgate/controlplane/runtime.py:161-189` always discovers/builds manifests for the full project root.

Impact: even targeted runs incur project-scale symbolic preprocessing.

---

## Medium-Severity Findings (Theory/Model Gaps)

### P1 — Phase classification is not trajectory-based

- `lintgate/specification/predictor.py:268-276` infers phase from static `spec_level` thresholds.

Gap vs paper: Theorem 3.4 requires observing kill dynamics (bulk->tail transition), not static ratio bucketing.

### P1 — Regime classification is heuristic, not symmetry-derived

- `lintgate/specification/predictor.py:119-121` sets Regime B only when `sigma > 20`.

Gap vs paper: Theorem 4.1 ties regime to mutation symmetry group structure.

### P1 — Composition gap uses proxy, not interface mutant count

- `lintgate/specification/composition.py:75-93`
  - `gamma = interface_complexity * (1 - spec_level)`
  - `interface_mutant_count = int(gamma)`

Gap vs paper: Theorem 3.15/3.16 requires interface mutant accounting, not derived proxy count.

### P1 — Risk model inputs are hardcoded to zero fan-in/fan-out

- `lintgate/specification/ledger.py:112-117` passes `fan_in=0`, `fan_out=0` into risk scoring.

Impact: risk bands underutilize call-graph information already available in system.

### P1 — Function AST resolution can misattribute methods

- `lintgate/specification/ledger.py:273-293` strips to simple name and returns first match.

Impact: class method collisions can bind wrong AST node, skewing sigma/regime/testability.

### P1 — Prescription gap uses covering test count as assertion count

- `lintgate/specification/prescriptions.py:43` uses `len(traceability.covering_tests)`.

Impact: sigma gap logic is distorted when number of tests != number of assertions.

### P2 — Cached ledger loader is unused

- `lintgate/specification/ledger.py:171-183` defines `load_cached_ledger`.
- No active caller found.

Impact: repeated recomputation despite cache structures existing.

---

## Gap Matrix Against Paper Requirements

| Requirement | Current Status | Notes |
|---|---|---|
| Single-file default focus | Missing | Optional file filter exists but default is project walk; prepass remains global |
| Passive symbolic mode | Partial | Implemented in active specification channel; not explicitly mode-managed |
| Active explicit mode | Missing | No active mutation audit path in live tools |
| Monty Hall category pre-filter | Missing (active path) | Exists only in archived mutation code |
| Active hypothesis sampling | Missing (active path) | No 3-5 targeted mutation execution in live path |
| In-process meta-mutant | Missing (active path) | Archived architecture references it, not active runtime |
| Trajectory tracking (killed survivors over i) | Missing | No per-function trajectory state |
| Bulk->tail transition detection | Missing | Static threshold proxy only |
| Greedy convergence sigma estimation | Missing | No set-cover/greedy trajectory estimator |
| Composition gap via interface mutants | Missing | Proxy gamma used instead |
| Regime from symmetry analysis | Missing | `sigma > 20` heuristic |
| Hard resource caps (time/mem/file) | Partial | Time budgets exist; memory/artifact hard guards missing in `spec_*` paths |

---

## Reusable Assets from Archived System

The archived subsystem contains reusable, non-runtime-coupled components worth porting selectively:

- Category taxonomy + relevance matrix:
  - `archive/mutation_system/lintgate/mutation/policy.py`
- Test-impact mapping from coverage DB:
  - `archive/mutation_system/lintgate/mutation/test_impact.py`
- Static category map + prediction scaffolding:
  - `archive/mutation_system/lintgate/mutation/predictor.py`
- Decomposition category->prescription mapping:
  - `archive/mutation_system/lintgate/mutation/decomposition.py`

Do **not** revive mutmut trampoline execution path; reuse symbolic structures only.

---

## Recommended Redesign (Practical + Theory-Aligned)

### Phase 0 (Immediate safety patch)

1. Replace `mcp_tools/specification_tools.py` discovery with canonical discovery.
2. Hard-exclude `mutants/`, `.mutmut*`, and optionally `archive/` from spec tools.
3. Make single-file analysis the default behavior for `spec_*` tools (explicit project-wide flag if needed).
4. Add hard guardrails:
   - max files per run
   - max total lines per run
   - early abort with actionable error when exceeded

### Phase 1 (Passive single-file cartography)

Build a dedicated single-file symbolic analyzer:

- Input: one source file
- Output per function:
  - category map
  - sigma estimate with confidence
  - regime estimate + rationale
  - phase hint (explicitly marked predictive)
  - decomposition signals

No test execution, no source mutation, no subprocess.

### Phase 2 (Trajectory-aware specification metrics)

Add per-function state (predicted/sampled/profiled):

- survivor set size over steps
- greedy kill frontier estimate
- detected phase transition index
- estimated tests remaining (with confidence band)

This is where Theorem 3.2/3.4 ideas become operational.

### Phase 3 (Optional active file audit)

Explicit tool only (never implicit):

- file-scoped, resource-capped
- in-process mutation toggles only
- coverage-based test-impact selection
- greedy category prioritization

Output clearly labels empirical depth (`SAMPLED`/`PROFILED`) vs symbolic (`PREDICTED`).

### Phase 4 (Composition and regime hardening)

- Compute composition gap with explicit interface mutation accounting.
- Upgrade regime classification from raw sigma threshold to symmetry-informed proxies.
- Feed real fan-in/fan-out into risk model.

---

## Suggested Tool Surface

### Passive (default)

- `spec_file_analyze(path, file)`
- `spec_file_prescribe(path, file, function?)`

### Active (explicit)

- `spec_file_sample(path, file, function?, budget?)`
- `spec_file_profile(path, file, function?, budget?)`

### Optional project aggregator

- `spec_project_rollup(path)` should aggregate cached file-level outputs only, not execute fresh whole-project scans.

---

## Success Criteria

1. Running spec analysis on this repo with `mutants/` present does not ingest `mutants/` files.
2. Default spec analysis executes on one file only unless project mode is explicitly requested.
3. Peak memory remains bounded by configured limits.
4. Passive outputs are clearly predictive; active outputs are clearly empirical.
5. Composition and regime metrics have transparent, inspectable derivations.

---

## Bottom Line

The project has the right theoretical direction, but current operational behavior still permits catastrophic scale amplification through discovery/scope mismatches. The first practical win is to enforce **single-file, canonical-discovery, resource-capped passive analysis** as the default. Then add explicit active audits with in-process execution and test-impact scoping.

---

## Extension: Platonic Testing Regimen + Symbolic Refactor Synthesis

Your proposed extension is a natural next step and can be implemented directly on top of the specification-complexity framing.

### 1) "Platonic ideal" testing regimen (minimal, exact, typed)

Define the target as:

- **Per-function objective**: construct a minimal distinguishing test basis approximating `sigma(P, mu)`
- **Global objective**: aggregate function bases into a project plan with no redundant tests

Practical implementation should output:

- exact **test count target** per function (lower/upper bound)
- exact **test type mix** per function
  - exact-value
  - boundary
  - equivalence partition
  - decision table / cause-effect
  - property/metamorphic
- stop condition: "no additional information gain above epsilon"

#### Operational algorithm (single-file first)

1. Build symbolic mutation-dimension basis from AST + category map.
2. Build required assertion dimensions from surviving/predicted categories.
3. Build candidate test archetypes (typed templates).
4. Solve weighted set-cover (or ILP if small):
   - minimize test count
   - maximize distinguishability/information gain
   - enforce per-risk constraints (P0 stricter)
5. Emit:
   - minimal plan
   - proof artifacts (covered dimensions, uncovered residuals)
   - confidence of optimality

This gives a working approximation of the "exactly required" test plan while staying computationally tractable.

### 2) Symbolic improvement engine (from findings -> refactor strategy)

Use mutation/specification outputs as a structural signal for codebase transformations.

#### Pattern families to detect

- repeated function shapes with near-identical logic
- same survivor categories recurring across modules
- pure + underspecified + high call frequency clusters
- high composition-gap edges with repeated interface patterns

#### Action synthesis examples

- repeated pure arithmetic kernels -> vectorize/JIT candidates
- repeated pure deterministic transforms -> shared memo/cache layer
- high fan-out + Regime-B-like patterns -> extract strategy/dispatch objects
- repeated boundary-survivor motifs -> centralize constraints/constants
- repeated string-grammar survivors -> vocabulary/regex registry extraction

#### Gating rules (important)

- performance actions only when spec confidence meets threshold
- decomposition actions prioritized when transition-to-tail is early
- JIT/parallelization suggestions require purity + stable interfaces + sufficient specification level

### 3) MCP + ControlPlane integration

Add two explicit tools and one synthetic channel feed:

- `spec_file_minimal_test_plan(path, file, function?)`
- `spec_refactor_synthesis(path, file|project, mode="symbolic")`

ControlPlane integration:

- synthesize findings into coherence layer as advisory actions
- emit structured `next_actions` with confidence and expected ROI
- keep execution separate: suggestions are symbolic by default; application remains explicit

### 4) Success metrics for this extension

1. Test plan compression ratio (naive tests / minimal plan tests)
2. Distinguishability coverage (% mutation dimensions covered by plan)
3. Refactor suggestion precision (accepted/applied suggestions)
4. Performance lift for accepted transformations
5. Regression safety (no increase in escaped defects)

This extension makes the system not just a diagnosis engine, but a **specification convergence and codebase optimization planner** grounded in symbolic evidence.
