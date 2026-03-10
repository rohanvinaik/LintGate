# Specification-Mutation-Performance Orchestration (v1)

## Status Snapshot (March 10, 2026)

This document now reflects implemented behavior in the codebase, not just target design.

## Scope

This orchestration effort covers four linked surfaces:

1. Mutation test discovery reliability for `spec_file_analyze` and `spec_project_rollup`.
2. Performance algebra signal quality (purity, cacheability, parallelism, JIT).
3. Optimization planning UX (`optimization_landscape`, static and dynamic modes).
4. Mutation-to-performance bridge (`mutation_decompose` recommendations).

## Implemented vs Gap Matrix

| Area | Current state | Status |
|---|---|---|
| Mutation test file discovery | Recursive test discovery, broader naming patterns, callable fallback loading, diagnostics on discovery failure | Implemented |
| Mutation result diagnostics | `discovery_failed`, `discovery_diagnostics`, and summary warning in sampling/full runs | Implemented |
| Purity side-effect hardening | Added detection for file/path writes, DB methods, serializer writes, ML load/train calls, external mutation patterns | Implemented |
| Purity confidence quality | Replaced flat purity confidence with evidence-weighted bands | Implemented |
| Cacheability differentiation | New ROI scoring (`cache_scoring.py`) with score bands and factors | Implemented (v1 heuristic) |
| Parallel opportunity detection | New call-site detector (`parallel_detector.py`) | Implemented (v1) |
| JIT opportunity detection | New numeric-kernel detector (`jit_detector.py`) | Implemented (v1) |
| `optimization_landscape` static mode | Added `mode=static`, plus `auto` fallback to static | Implemented |
| `mutation_decompose` performance bridge | Added `performance_unlocks`, predicted unlock classes, richer recommendations | Implemented |
| `spec_project_rollup` test skew control | Added `include_tests=False` default and skipped-test metadata | Implemented |
| Dynamic convergence event wiring | Synthetic mesh events still default to `surface="hook"` in convergence tools | Gap |
| Performance purity evidence into convergence | Adapter still reads `purity_profile` while performance channel emits `pure_function_list` | Gap |
| Static landscape call-site fidelity | Parallel output currently collapses opportunities to callee names only | Gap |
| Static cache hotspot dedupe key | Dedup is function-name-only, can collide across modules/classes | Gap |

## What Is Working Now

### 1) Mutation sampler and spec mapping reliability

- Implemented in:
  - `mcp_tools/_mutation_impl.py`
  - `mcp_tools/_mutation_tools_impl.py`
  - `lintgate/specification/file_analyzer.py`
  - `lintgate/linters/test_effectiveness/source_mapper.py`

- Improvements:
  - Recursive discovery under `tests/` and `test/`.
  - Expanded filename matching (`test_<module>_*.py`, `<module>_test*.py`, characterization test variants).
  - Better class-method test loading for non-`Test*` class names.
  - Explicit diagnostics when no tests are loaded so "no tests discovered" is distinguishable from "function untested."

### 2) Purity and optimization signal quality

- Implemented in:
  - `lintgate/linters/performance_checks/purity.py`
  - `lintgate/linters/performance_checks/cache_scoring.py`
  - `lintgate/linters/performance_checks/parallel_detector.py`
  - `lintgate/linters/performance_checks/jit_detector.py`

- Improvements:
  - Much tighter static side-effect signals.
  - Non-uniform purity confidence (0.95/0.90/0.80/0.65 style bands).
  - First-pass ranking-like performance signals (cache score factors, call-site parallel opportunities, JIT candidates).

### 3) Landscape and decompose output usefulness

- Implemented in:
  - `mcp_tools/convergence_tools.py`
  - `mcp_tools/_mutation_tools_impl.py`

- Improvements:
  - `optimization_landscape(mode="auto|static|dynamic")`.
  - Static fallback when dynamic convergence is empty.
  - Decomposition output now maps mutation survival categories to concrete performance unlocks.

## Open Gaps (Must-Fix)

### Gap A: Dynamic convergence still effectively starved

- In convergence tools, synthetic mesh events are created without `surface="mcp"`.
- Because lint/tests/structure channels gate on `event.surface == "mcp"` (or require classification), dynamic convergence often has sparse/no evidence.
- Dynamic landscape currently returns "No convergence data" in this path.

### Gap B: Purity evidence not consumed by convergence adapters

- Convergence integration adapters still read `purity_profile`.
- Performance channel exports `pure_function_list`.
- Net effect: purity lens evidence is not currently feeding convergence scoring.

### Gap C: Static landscape output loses useful context

- Parallel opportunities are downsampled to `[callee]`, dropping file/line/pattern/confidence from detector output.
- Cache hotspot dedupe uses only function name, risking collisions for same-named methods across modules/classes.

## Updated Plan (Current-State Aligned)

### Execution order rationale

Gap A → Gap B → Gap C. A unblocks dynamic mode entirely. B ensures dynamic convergence consumes purity evidence (without B, fixing A still yields an incomplete pipeline). C improves output quality once the pipeline is semantically correct.

### Phase 1: Reliability Closure (next 1-2 PRs)

**Step 1 — Fix convergence event surface wiring (Gap A).**
- Set `surface="mcp"` when convergence tools synthesize `SupervisionEvent`.
- Ensure `optimization_landscape(mode="dynamic")` yields non-empty convergence on repos with analyzable Python files.
- **Policy**: `surface="mcp"` is the immediate fix. Follow-up issue to decouple convergence channel gating from hook/MCP surface semantics — convergence should not depend on the transport origin of its evidence.

**Step 2 — Fix purity adapter key mismatch (Gap B).**
- Accept both `purity_profile` and `pure_function_list` in convergence integration adapter.
- Normalize list payload into adapter-ready structure.
- **Policy**: Dual-key support is transitional only. Deprecation window: remove `purity_profile` key acceptance by v1.1 (Phase 2 start). Performance channel is the canonical emitter; adapter aligns to it.

**Step 3 — Improve static landscape fidelity (Gap C).**
- Preserve full parallel call-site records (`file`, `line`, `pattern`, `confidence`, `constraints`).
- Dedupe cache hotspots by `(source_file, function)` not function name alone.

### Phase 2: Ranking and authority quality (v1.1)

1. Promote detector outputs into stable ranked views in `inspect_algebra`.
2. Add authority-aware ranking composition (purity + spec + mutation depth).
3. Add explicit uncertainty flags when evidence is incomplete.
4. Remove deprecated `purity_profile` key from convergence adapter (per Phase 1 Step 2 deprecation window).

### Phase 3: Broader symbolic opportunity set (v2 categories)

1. Add detectors for vectorization, eager->lazy, redundant recomputation, data-structure mismatch, I/O batching, allocation churn, short-circuit ordering, import/init overhead, serialization overhead, constant hoisting.
2. Keep these advisory-first until precision thresholds are met.
3. **Entry gate policy**: Each new detector requires minimum precision >=75% on a curated fixture set of >=50 samples before entering the advisory pipeline. Detectors that fail the gate are not shipped.

## Success and Failure Thresholds

## Reliability thresholds (must pass before closure)

- `optimization_landscape(mode="dynamic")` produces non-empty convergence output on >=90% of eligible projects.
- `optimization_landscape(mode="auto")` produces non-empty output on >=99% of eligible projects.
- Mutation sampling/full runs with discovered test files should report `tests_loaded > 0` for >=95% of source files that have matching test files.

Failure thresholds:

- Dynamic mode empty output >10% on eligible projects is a release blocker.
- Any regression where known test-backed files revert to `tests_loaded=0` without `discovery_failed` diagnostics is a blocker.

## Signal quality thresholds

- Purity false-positive rate on curated side-effect fixtures <=5%.
- Cache hotspot top-10 precision >=70% against benchmark fixtures.
- Parallel/JIT detector precision >=75% before escalating beyond advisory.

Failure thresholds:

- Unsafe recommendation rate (impure function suggested as cache/JIT/parallel) >5% blocks promotion of that recommendation class.

## Performance overhead thresholds (provisional — no benchmark baseline yet)

- p95 incremental runtime overhead of new orchestration logic <=20% versus pre-change baseline for equivalent scope.
- `optimization_landscape(mode="static")` should complete in <=5s on medium repos (~1k Python files) in CI-like environment.
- **Policy**: These targets are provisional until benchmark baselines are established. First priority in Phase 2 is to capture baselines on 3+ repos of varying size, then revise targets if needed.

## Rollback and demotion policy

- If a detector class's precision drops below its threshold for 3 consecutive measurement runs, it is auto-demoted to `suppressed` (not emitted in tool output, still computed for telemetry).
- Demotion is reversible: precision recovery above threshold for 2 consecutive runs restores `advisory` status.
- Unsafe recommendation rate (impure function suggested as cacheable/parallelizable/JIT) >5% immediately blocks that recommendation class from all output until root-caused and fixed. No grace period.

## Validation and Test Coverage

Existing coverage added in this cycle includes:

- `tests/test_mutation_impl_helpers.py`
- `tests/test_source_mapper.py`
- `tests/test_purity_detector.py`
- `tests/test_cache_scoring.py`
- `tests/test_parallel_detector.py`
- `tests/test_jit_detector.py`
- `tests/test_decompose_bridge.py`
- `tests/test_convergence_tools.py`
- `tests/test_file_analyzer.py`
- `tests/test_project_rollup.py`

Regression tests added in `tests/test_gap_regressions.py` (19 tests):

1. ~~Regression test that dynamic convergence path uses `surface="mcp"` and yields evidence.~~ → `TestGapA_SurfaceWiring` (4 tests)
2. ~~Regression test that convergence consumes performance purity metrics (`pure_function_list`).~~ → `TestGapB_PurityAdapterAlignment` (10 tests)
3. ~~Static landscape schema test asserting call-site metadata is preserved for parallel opportunities.~~ → `TestGapC_StaticLandscapeFidelity` (5 tests)
4. ~~Collision test for cache hotspot dedupe across same function names in different modules/classes.~~ → `TestGapC_StaticLandscapeFidelity::test_cache_dedupe_by_source_file_and_function`

## Acceptance Checklist (Updated)

- [x] Mutation discovery diagnostics and broader test mapping implemented.
- [x] Purity side-effect hardening implemented.
- [x] Cache/parallel/JIT detector modules added.
- [x] `optimization_landscape` supports `auto|static|dynamic`.
- [x] `mutation_decompose` emits performance unlock bridge data.
- [x] **(Gap A)** Dynamic convergence path uses `surface="mcp"` and is non-empty on eligible projects.
- [ ] **(Gap A follow-up)** Issue filed to decouple convergence gating from hook/MCP surface semantics.
- [x] **(Gap B)** Convergence purity adapter consumes current performance metrics schema (`pure_function_list`).
- [x] **(Gap B)** Dual-key deprecation window documented — `purity_profile` removed by Phase 2 start.
- [x] **(Gap C)** Static landscape preserves parallel call-site metadata and uses collision-safe cache dedupe.
- [ ] **(Phase 2)** Performance benchmark baselines captured on 3+ repos.
- [ ] **(Phase 3 gate)** Per-detector entry gate enforced (>=75% precision, >=50 sample fixture set).
- [ ] **(Policy)** Rollback/demotion rules implemented for detector precision regression.
