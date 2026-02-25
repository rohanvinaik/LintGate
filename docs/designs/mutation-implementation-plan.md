# Mutation Testing Implementation Plan

Design document for closing the gaps between LintGate's mutation testing theory
and its current implementation, per GitHub Issue #100.

## Status Summary

| Component | Status | What's There |
|---|---|---|
| State model (`state.py`) | Functional | `CoverageDepth`, `FunctionMutationState`, `MutationStateManager` |
| Policy model (`policy.py`) | Functional | `OperatorRelevanceMatrix`, `RuntimeBudget`, `MutationTelemetry` |
| CI stats (`ci_stats.py`) | Functional | `MutationCIStats`, `load_mutation_hotspots`, AST enrichment |
| Telemetry (`telemetry.py`) | Functional | `TelemetryTargets`, A/B validation |
| Engine (`engine.py`) | Scaffold | Two-tier methods exist but don't parse results or update state |
| CI workflow (`mutation.yml`) | Hardened | Exit code checking, stats validation, artifact upload |
| Cross-channel gate | Missing | `properties.py:240` emits "cacheable" with no mutation check |
| MCP tools | Missing | `mutation_run`, `mutation_sample`, `mutation_gate_check`, `mutation_profile` |
| Background orchestration | Missing | Nothing spawns or schedules mutation work |
| Test-impact mapping | Missing | Engine accepts mapping but nothing builds it |

## Dependency Graph

```
[PR1] State enrichment + engine result parsing
  │
  ├──→ [PR3] Cross-channel gate (mutation gates algebra hints)
  │       │
  │       ├──→ [PR4] MCP tool surface (4 new tools)
  │       │       │
  │       │       └──→ [PR5] Mutation ControlPlane channel
  │       │               │
  │       │               └──→ [PR6] Background orchestration
  │       │
  │       └──→ (TEFF005 elevation, MUTCH004 finding)
  │
  └──→ [PR2] Monty Hall filter wiring
          │
          └──→ [PR7] Inline-on-edit debounce (future)

[PR8] Test-impact mapping (independent, future)
```

**Recommended order:** PR1 → PR3 → PR2 → PR4 → PR5 → PR6 → PR8 → PR7

Rationale: PR3 (cross-channel gate) is the theory's central claim and only needs
PR1. PR2 (Monty Hall) is independent of the gate. PR4 (MCP tools) needs gate
logic to be meaningful.

---

## PR 1: State Enrichment + Engine Result Parsing

**Goal:** Make the engine parse mutmut results and update persistent state.

### 1A: Add `survival_rate` property to `FunctionMutationState`

**File:** `lintgate/mutation/state.py`

Add:
```python
@property
def survival_rate(self) -> float:
    """0.0 = fully specified, 1.0 = no specification."""
    if self.total == 0:
        return 1.0
    return self.survived / self.total
```

Add new fields to distinguish crash-kills from assertion-kills:
```python
killed_by_assertion: int = 0
killed_by_crash: int = 0
```

Crash-kills don't prove specification completeness — only assertion-kills do.

### 1B: Engine result parsing and state update

**File:** `lintgate/mutation/engine.py`

The current engine has three problems:
1. `_execute_mutmut` returns `bool` and discards all results
2. `run_inline_sampling` has a comment: "We don't parse the full results here"
3. Neither method calls `state_manager.update_state()`

Add:
```python
def _parse_mutmut_results(self, paths: List[str]) -> Dict[str, FunctionMutationState]:
    """Parse mutmut results after a run and return per-function state."""
```

Modify `run_inline_sampling` to:
1. Execute mutmut (as before)
2. Parse results via `_parse_mutmut_results`
3. Call `state_manager.update_state()` for each function with `depth=SAMPLED`
4. Call `state_manager.save()`
5. Return `List[FunctionMutationState]` instead of `None`

Same for `run_background_profiling` with `depth=PROFILED`.

**Decision: How to parse mutmut results.** Options:
- (a) Shell out to `mutmut results --all true` and parse the text output — uses the
  same format `load_mutation_hotspots` already handles (`_parse_survivor_line`).
  Note: `mutmut results` only supports `--all` (no `--json` flag).
- (b) Read `.mutmut-cache` SQLite directly — richer per-mutant detail (operator type,
  exact AST node), tighter coupling to mutmut internals.
- (c) Run `mutmut export-cicd-stats` then read `mutmut-cicd-stats.json` — aggregate
  stats only, no per-function breakdown.

**Recommendation:** (a) for both tiers. The existing `load_mutation_hotspots` +
`_enrich_function_names` pipeline in `ci_stats.py` already parses this format and
maps lines to functions via AST. For inline, parse immediately after execution. For
background, the same parser works with larger output. Fall back to (b) only if
per-mutant operator detail is needed for survivor category classification (Phase 2
enhancement — the SQLite schema gives us mutation type per mutant, which the text
output does not).

### Tests

Extend `tests/test_mutation_state.py`:
- `survival_rate` returns 0.0 when survived=0, total=10
- `survival_rate` returns 1.0 when total=0
- Serialization round-trip preserves `killed_by_assertion`/`killed_by_crash`

Extend `tests/test_mutation_engine.py`:
- Mock `_execute_mutmut`, verify `state_manager.update_state` is called
- Verify `FunctionMutationState` objects have correct depth and counts

---

## PR 2: Monty Hall Filter Wiring

**Goal:** Connect `OperatorRelevanceMatrix` to the engine execution path.

### 2A: Function characteristic extraction

**File:** `lintgate/mutation/engine.py`

Add:
```python
def _compute_relevant_categories(
    self,
    file_path: str,
    func_name: str,
    algebra_manifest: Optional[PropertyManifest] = None,
    teff_manifest: Optional[TestEffectivenessManifest] = None,
) -> Set[MutationOperatorCategory]:
    """Layer 1: Exclusionary filtering.

    Uses existing signals to eliminate irrelevant mutation categories.
    """
```

Implementation:
1. Look up function in algebra manifest → `is_pure`, properties
2. Parse file for `branch_count`, `has_strings`, `has_numbers` (AST walk)
3. Call `OperatorRelevanceMatrix.get_prioritized_categories()`
4. Further eliminate categories where teff manifest shows strong existing assertions

### 2B: Extend `OperatorRelevanceMatrix`

**File:** `lintgate/mutation/policy.py`

Add optional parameter to `get_prioritized_categories`:
```python
covered_categories: Optional[Set[MutationOperatorCategory]] = None
```

The current interface returns `Set[MutationOperatorCategory]` (unordered). Categories
in `covered_categories` are **excluded** from the returned set — they represent
categories where existing assertions already provide strong coverage, so mutation
testing in those categories has low expected information gain.

This is a strict include/exclude filter, not a priority ordering. If priority
ordering is needed later (e.g., for budget-constrained sampling that runs
high-value categories first), the return type should change to
`list[MutationOperatorCategory]` in a separate change.

### 2C: Wire filtering into execution

**File:** `lintgate/mutation/engine.py`

Modify `run_inline_sampling` and `run_background_profiling` to accept optional
manifests and call `_compute_relevant_categories` before execution.

**Decision: mutmut doesn't support per-category filtering natively.** Options:
- (a) Post-filter results — classify survivors by category after execution, discard
  irrelevant ones from analysis. **Does not reduce execution cost.** Useful only for
  analysis accuracy (ignore survivors in categories the function doesn't meaningfully
  use). Label telemetry as `analysis_only_filter=true`.
- (b) Use mutmut `pre_mutation` hook to skip irrelevant operators at generation time —
  **actually reduces execution cost.** Requires mapping `OperatorRelevanceMatrix`
  categories to mutmut's internal operator names (the `map_mutmut_type_to_category`
  method already exists for this).
- (c) Build thin custom AST mutation layer — maximum control, largest effort.

**Recommendation:** (b) for Phase 1. The `map_mutmut_type_to_category` mapping
already exists in `policy.py`. Write a `pre_mutation` hook that checks the operator
type against the relevant category set and returns `SKIP` for excluded categories.
This is the only option that actually delivers runtime reduction, which is a core
KPI per `TelemetryTargets.min_runtime_reduction_ratio = 0.50`.

Fall back to (a) only if the `pre_mutation` hook proves unreliable, and in that case
`telemetry.mutants_skipped_policy` must be relabeled as `mutants_excluded_analysis`
to avoid claiming runtime savings that didn't happen.

Track separately:
- `mutants_skipped_execution` — skipped before running (real runtime savings)
- `mutants_excluded_analysis` — excluded after running (analysis accuracy only)

### Tests

- `_compute_relevant_categories` returns correct categories for pure math function
- `_compute_relevant_categories` returns correct categories for string-heavy function
- Irrelevant categories are counted in `telemetry.mutants_skipped_policy`
- Backward compat: `None` manifests work (no filtering)

---

## PR 3: Cross-Channel Gate

**Goal:** Mutation score gates optimization hints. This is the theory's central claim.

### 3A: Add mutation-aware gating to `classify_properties`

**File:** `lintgate/linters/performance_checks/properties.py`

Currently line 240:
```python
hints: list[str] = ["cacheable"]
```

Change signature:
```python
def classify_properties(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    purity: PurityResult,
    mutation_state: FunctionMutationState | None = None,
) -> FunctionProperties:
```

Add gating after hint assembly. The gate incorporates **both** survival rate and
signal quality (depth + confidence), not survival alone. Low-quality signals
produce advisory annotations rather than hard suppression to avoid false gating:

```python
if mutation_state is not None and mutation_state.depth != CoverageDepth.NONE:
    survival = mutation_state.survival_rate
    is_authoritative = (
        mutation_state.depth == CoverageDepth.PROFILED
        or (mutation_state.depth == CoverageDepth.SAMPLED
            and mutation_state.confidence == ConfidenceLevel.HIGH)
    )

    if is_authoritative:
        # Hard gate: sufficient evidence to suppress hints
        if survival > 0.5:
            hints = []
            gate_reason = f"gated: survival_rate={survival:.0%}, depth={mutation_state.depth.value}"
        elif survival > 0.2:
            hints = [h for h in hints if h in ("cacheable",)]
            gate_reason = f"partial: survival_rate={survival:.0%}, depth={mutation_state.depth.value}"
        else:
            gate_reason = None
    else:
        # Advisory only: sampled/low-confidence signal annotates but does not suppress
        if survival > 0.3:
            gate_reason = (
                f"advisory: survival_rate={survival:.0%}, "
                f"depth={mutation_state.depth.value}, "
                f"confidence={mutation_state.confidence.value} "
                f"(run mutation_run for authoritative gating)"
            )
        else:
            gate_reason = None
        # hints are NOT modified — advisory signals never suppress
```

Gate policy summary:

| Depth | Confidence | Survival > 50% | Survival 20–50% | Survival < 20% |
|---|---|---|---|---|
| PROFILED | any | Hard gate: all hints suppressed | Hard gate: keep cacheable only | Pass |
| SAMPLED | HIGH | Hard gate: all hints suppressed | Hard gate: keep cacheable only | Pass |
| SAMPLED | MEDIUM/LOW | Advisory annotation only | Advisory annotation only | Pass |
| NONE | any | Hints pass through (no data) | Hints pass through | Pass |

When `mutation_state is None` (no data), hints pass through ungated. No data ≠ bad
data. This preserves backward compatibility.

Add `gate_reason: str | None = None` to `FunctionProperties`.

### 3B: Wire mutation state into manifest builder

**File:** `lintgate/linters/performance_checks/manifest.py`

Modify `build_manifest` and `_scan_file` to accept optional `MutationStateManager`.
When classifying pure functions, look up mutation state and pass to
`classify_properties`.

### 3C: MUTCH004 finding in performance channel

**File:** `lintgate/channels/performance_channel.py`

Add new finding generator:
```python
def _mutch004_underspecified_hints(
    manifest: PropertyManifest,
    mutation_state_manager: Optional[MutationStateManager],
    project_root: str,
) -> list[LintIssue]:
    """Pure functions where optimization hints lack specification evidence."""
```

Severity: `"informational"` in Phase 1 (audit-only, per CLAUDE.md).

### 3D: Elevate TEFF005

**File:** `lintgate/channels/test_effectiveness_channel.py`

When mutation state is available, annotate the TEFF005 message with gate status.
The annotation reflects the same depth/confidence policy as the gate itself:

- **Authoritative signal** (PROFILED, or SAMPLED+HIGH): message states hints are GATED.
- **Advisory signal** (SAMPLED+MEDIUM/LOW): message states hints are flagged but not
  suppressed, and recommends `mutation_run` for authoritative data.
- **No mutation data**: existing TEFF005 message unchanged.

```
# Authoritative:
'func_name' is mathematically pure but underspecified
(mutation survival 74%, depth=profiled). Optimization hints are GATED
until specification improves.

# Advisory:
'func_name' is mathematically pure but underspecified
(mutation survival 74%, depth=sampled, confidence=low).
Run mutation_run for authoritative gating.
```

### Tests

New `tests/test_mutation_gate.py`:

Hard gate cases (PROFILED or SAMPLED+HIGH):
- Pure function with no mutation data → all hints pass through
- Pure function with survival_rate=0.0, depth=PROFILED → all hints pass
- Pure function with survival_rate=0.3, depth=PROFILED → parallelizable gated, cacheable passes
- Pure function with survival_rate=0.6, depth=PROFILED → all hints gated
- Pure function with survival_rate=0.6, depth=SAMPLED, confidence=HIGH → all hints gated

Advisory cases (SAMPLED+MEDIUM/LOW):
- Pure function with survival_rate=0.6, depth=SAMPLED, confidence=LOW → hints preserved, gate_reason starts with "advisory:"
- Pure function with survival_rate=0.4, depth=SAMPLED, confidence=MEDIUM → hints preserved, gate_reason is advisory
- Pure function with survival_rate=0.1, depth=SAMPLED, confidence=LOW → no gate_reason (low survival)

Channel integration:
- MUTCH004 emitted for underspecified pure function with authoritative signal
- MUTCH004 NOT emitted when no mutation data exists
- MUTCH004 advisory (not blocking) when signal is sampled+low confidence
- Existing `test_performance_channel.py` still passes (backward compat)

---

## PR 4: MCP Tool Surface

**Goal:** Create the four missing tools referenced in CLAUDE.md.

### 4A: New `mcp_tools/mutation_tools.py`

Following `register(mcp, helpers)` pattern from existing tool modules.

**`mutation_sample(path, file_filter?, function_filter?, max_functions=5)`**

Run signal-guided inline sampling (2–5 seconds). Uses Monty Hall filter.
Returns per-function state with `coverage_depth="sampled"`.

*When to use:* Quick spec completeness check after editing. When TEFF005/MUTCH004
flags appear.

**`mutation_run(path, file_filter?, function_filter?)`**

Exhaustive background profiling (minutes). Full operator coverage with
test-impact selection when available. Returns `coverage_depth="profiled"`,
`is_gateable=True`.

*When to use:* Definitive spec completeness data for optimization decisions.

**`mutation_gate_check(path, function?)`**

Check which optimization hints are backed by specification evidence.
Returns per-function breakdown: `eligible` / `gated` / `partial` / `no_data`.

*When to use:* Before applying performance optimizations. When MUTCH004 appears.

**`mutation_profile(path, function?)`**

View survival profiles. Shows survival_rate, coverage_depth, killed/survived/total,
survivor categories, specification completeness level.

*When to use:* Understand WHY a function is underspecified. Survivor categories
prescribe specific decomposition strategies.

### 4B: Registration

Add `mutation_tools` to `mcp_tools/__init__.py` module list.

### 4C: Documentation

Update tool counts in `README.md`, `docs/agent/AGENTS.md`, `docs/design.md`.
Verify with `grep -Rho '@mcp.tool()' mcp_server.py mcp_tools/*.py | wc -l`.

### Tests

New `tests/test_mutation_tools.py`:
- Schema contract tests (output includes expected keys)
- `mutation_gate_check` returns correct classification
- `mutation_profile` returns correct survival data
- Graceful degradation when mutmut not installed

---

## PR 5: Mutation ControlPlane Channel

**Goal:** `controlplane_run` includes mutation-based specification findings.

### 5A: New `lintgate/channels/mutation_channel.py`

```python
class MutationChannel:
    name = "mutation"
    timeout_ms = 8000
    blocking_capable = False  # Advisory in Phase 1
```

Finding codes:
| Code | Finding | Severity |
|---|---|---|
| MUTCH001 | Project-level specification completeness summary | informational |
| MUTCH002 | Function with high survival + tests present | warning |
| MUTCH003 | Function with no mutation data (blind spot) | informational |
| MUTCH004 | Pure function with ungated optimization hints | informational |
| MUTCH005 | Stale mutation data (code_hash mismatch) | informational |
| MUTCH006 | Sampled-depth signal (directional, not gateable) | informational |

### 5B: Register in channel registry

Wire into `_build_channel_registry()` in `controlplane_tools.py` and `cli.py`.

### Tests

- MUTCH004 emitted for pure function with high survival
- MUTCH003 emitted for function with no data
- Channel returns `status="skip"` when no mutation state exists

---

## PR 6: Background Orchestration

**Goal:** ControlPlane triggers progressive background mutation work.

### 6A: Priority queue builder

**File:** `lintgate/mutation/engine.py`

```python
def build_priority_queue(self, project_root, python_files):
    """Priority: no_data > confirmed_gaps > stale > refresh."""
```

### 6B: Integration

After `controlplane_run` mesh completes, if mutation data is stale or missing,
add `next_actions` entries suggesting `mutation_sample` or `mutation_run`.

**Decision:** Spawn actual background process vs. suggest tool calls?

**Recommendation:** Keep as explicit MCP tool calls for Phase 1. The `mutation_run`
tool can accept a `background: bool` parameter that returns immediately with a
run_id and writes results incrementally. True background spawning is Phase 2.

---

## PR 7: Inline-on-Edit Trigger (Future)

Debounced mutation sampling in PostToolUse hook after Write/Edit operations.
30-second per-file cooldown. Only when ControlPlane is enabled.

**Risk:** Adds latency to PostToolUse hook (8s timeout). Inline sampling takes
2–5 seconds. Tight budget. Run as post-mesh step, not in critical path.

---

## PR 8: Test-Impact Mapping (Future)

**File:** New `lintgate/mutation/test_impact.py`

Phase 1: Static import analysis (parse test imports, map to source files).
Phase 2: `coverage.py` JSON integration when available.

This is the efficiency unlock for background profiling. Without it, every mutation
runs the full test suite.

---

## Risks and Decisions

### mutmut in MCP Context

MCP is request-response. `mutation_sample` (2–5s) is fine. `mutation_run` (minutes)
blocks the event loop. Use `subprocess.Popen` with non-blocking I/O and return a
run_id. Poll via `mutation_profile(run_id=...)`.

### mutmut Not Installed

Not a core dependency (dev extras only). Every tool and channel must check
availability early: `{"error": "mutmut not installed", "install_hint": "..."}`.

### State File Contention

Concurrent inline + background writes could corrupt the JSON state file. Add file
locking (`fcntl.flock`) to `save()` and `load()`. Alternative: SQLite-backed state.

### Backward Compatibility

`classify_properties` signature change: make `mutation_state` keyword-only with
`None` default. All existing callers work unchanged until explicitly wired.

### Documentation Drift

Adding 4 MCP tools changes the tool count. CLAUDE.md MUST directive requires
updating `AGENTS.md`, `README.md`, `docs/design.md` in every tool-adding PR.

---

## Issue #100 Checklist Coverage

| Checklist Item | Covered By |
|---|---|
| 1. Theory→system contract | PR 3 (gate logic encodes the hypothesis) |
| 2. Operator relevance matrix | PR 2 (Monty Hall filter wiring) |
| 3. Two-tier execution model | PR 1 (engine result parsing) |
| 4. Runtime budget policy | Already functional in `policy.py` |
| 5. Coverage-depth state model | Already functional in `state.py` + PR 1 enrichment |
| 6. Test-impact selection | PR 8 (future) |
| 7. Equivalent/suspect denominator | Already in `ci_stats.py` |
| 8. CI integrity vs quality split | Already in `mutation.yml` + `parse_stats_for_ci` |
| 9. MCP schema contract | PR 4 (schema tests) |
| 10. Hotspot artifact format | Already in `ci_stats.py` `_normalize_hotspot` |
| 11. Quantitative acceptance metrics | Partial — `TelemetryTargets` defines thresholds but `validate_mutation_policy.py` is mocked; no empirical baseline-vs-filtered runs yet. Requires PR 2 (Monty Hall filter) producing real filtered runs before targets can be validated against actual data. |
| 12. Baseline A/B validation | Partial — `evaluate_telemetry_against_targets` implements the comparison logic but has never run against real baseline/filtered pairs. Wire into CI after PR 2 produces real filtered execution data. Until then, treat as structural scaffold, not empirical validation. |
