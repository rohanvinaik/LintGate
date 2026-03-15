---
theory_scope: false
---

# LintGate Agent Retrospective: Wayfinder — Performance Engineering + Validity Restoration

## Metadata

| Field | Value |
|-------|-------|
| **Project** | Wayfinder — Navigational theorem prover for Lean 4 / Mathlib (242K entity proof network) |
| **Agent** | Claude Opus 4.6 (1M context), solo with background sub-agents for parallelized test writing + lru_cache + premise cache |
| **Date** | 2026-03-14 |
| **Scope** | 110 Python files, ~30K LOC. Performance engineering focused on navigate() hot path (242K entity DB). |
| **LintGate Tier** | Tier 2, normal strictness, ControlPlane enabled (performance + structure + lint channels) |
| **LintGate Version** | Unknown (MCP server) |
| **Session Type** | Hybrid — Validity restoration (Stages 1-3: proof network expansion, domain bank fix, coverage metrics) followed by performance engineering guided by LintGate PERFCH005 + spec_composition |
| **Session Record(s)** | Not captured as JSONL; Claude Code interactive session |
| **Session Continuity** | Continuation from earlier audit session (same day) |
| **Prior State** | 1314 tests passing. Proof network at 78K entities (34.5% premise coverage). Domain bank 98.3% zero. |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state (performance-focused run): "isolated"** — *"Issue isolated to lint. performance, structure confirm no problems in their domains."*

The performance-focused controlplane run was clean on performance and structure channels — both passed with only informational findings. This is because LintGate's performance analysis operates at the **static code level** (purity detection, cacheability hints, cyclomatic complexity) rather than at the **runtime profiling level**. The actual performance bottleneck (SQLite I/O fetching 242K rows per navigate() call) is invisible to static analysis.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 0 | Clean |
| Warnings | 46 | All lint: file-too-long (4), too-many-args (5), cognitive-complexity (2), structural |
| Informational | 55 | PERFCH001 (low purity ratio 11.7%), PERFCH005 (236 cacheable functions), structure (3) |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | Active |
| Lockfile | Fresh (synced earlier this session) |
| .python-version | Present |
| Structure snapshot | STRUCT004 (expected low cohesion in scripts/tests), STRUCT005 (train_* prefix cluster) |

### Theory Profile

Compass updated earlier in session. Solution axis at depth 2 (was 1). No theory pack built.

---

## Part II: Observations During Performance Engineering

### Observation 1: PERFCH005 identifies cacheable functions but doesn't rank by hot-path impact

LintGate found 236 cacheable pure functions. The list included both `bank_score` (called 1.45M times per navigate()) and `GapRecord.to_dict` (called 500 times per analysis run). The finding treats all 236 equally — there's no "this one is called 1M times in the hot loop" annotation.

I had to manually trace the call graph to discover that `bank_score` is inside the `_score_candidates` loop, which is called from `navigate()`, which is the γ=49 hot path. The cacheable finding alone didn't convey this.

**What this reveals:** PERFCH005 would be dramatically more useful if it cross-referenced with call-graph depth and loop membership. A finding like "bank_score is cacheable AND appears inside _score_candidates (called 242K times from navigate)" would have immediately prioritized it over the other 235 functions.

> **Key insight:** Static purity detection is necessary but not sufficient for performance guidance. The missing layer is **call-site frequency analysis** — knowing that a function is pure tells you it *can* be cached; knowing it's called inside a 242K-iteration loop tells you it *should* be cached.

### Observation 2: spec_composition γ analysis correctly identified the hot integration surface

The `spec_composition(module_a="scripts", module_b="src")` call identified `eval_retrieval::nav_retrieve → resolution::resolve` at γ=49 as the highest integration surface. This was genuinely useful — it told me exactly where the architectural boundary was most complex and most exercised.

However, the γ value measures **specification complexity at the boundary**, not runtime cost. γ=49 for resolve() is high because the function has many parameters and decision paths, not because it takes 4 seconds to execute. The γ=81 for `v3_search` is even higher but v3_search hasn't been benchmarked yet.

**What this reveals:** γ is a good proxy for "where complexity accumulates at module boundaries" but doesn't directly predict performance bottlenecks. A complementary metric — actual call count or estimated computational cost — would make it actionable for performance engineering specifically.

### Observation 3: The real bottleneck was invisible to all LintGate tools

After implementing all LintGate-suggested optimizations, I profiled `navigate()` and found:

| Stage | Time | % |
|-------|------|---|
| SQLite: fetch anchor sets | 1658ms | 40% |
| SQLite: fetch positions | 1066ms | 26% |
| SQLite: fetch names | 373ms | 9% |
| NumPy bank scoring | 233ms | 6% |

**75% of the time is SQLite I/O** — fetching 242K rows of positions, anchors, and names from disk on every navigate() call. No LintGate tool flagged this because:

1. The `_batch_get_positions`, `_batch_get_anchor_sets`, `_batch_get_names` functions are just thin SQL wrappers — they have low sigma, low complexity, and no interesting static properties.
2. The fact that they're called with 242K-element `candidate_ids` lists is a runtime property, not a static one.
3. The fact that the same data is re-fetched on every navigate() call is a caching opportunity, but LintGate's cacheability analysis only covers pure functions, not "impure functions whose inputs don't change between calls."

**What this reveals:** LintGate needs a **data-flow caching analysis** layer — something that detects "this function reads from a stable data source (DB) with inputs that don't change between calls, so the result could be cached." This is different from pure-function caching (where the guarantee is mathematical) but equally important for performance.

### Observation 4: The attempted SQL pre-filter broke the scoring contract

I tried to optimize `_get_candidates()` by excluding entities with wrong-sign bank positions in SQL (reducing 242K candidates to ~10K). This failed because `bank_score()` gives non-zero scores to misaligned entities via `_MISSING_BANK_SCORE`. The pre-filter was sound algorithmically but violated a scoring invariant that wasn't documented or tested.

**What this reveals:** LintGate's mutation testing would have caught this — `_compute_bank_score` has no mutation tests (spec_level=0.0), so I couldn't verify that my optimization preserved the scoring contract. If I'd run `mutation_run_full` on `_compute_bank_score` before optimizing, I would have seen the invariant that wrong-sign entities still get non-zero scores.

> **Key insight:** Performance optimizations are refactorings that preserve behavior. Mutation testing is the right tool to verify preservation — but only if the function is already mutation-tested. The spec_prescribe tool should flag "you're about to optimize a function with 0% mutation coverage" as a pre-condition warning.

### Observation 5: The premise embedding cache was the biggest win but wasn't suggested

The single most impactful optimization (premise embedding disk cache, saving 20+ minutes per run) wasn't suggested by any LintGate tool. It came from understanding the eval_retrieval workflow — the same 242K premise names get encoded through the same frozen model on every run.

LintGate's PERFCH005 flagged `_encode_all_premises` as having an "optimization hint" but the hint was empty (`[]`). The function isn't pure (it has side effects — reads from DB), so it didn't get the "cacheable" hint. But its *result* is deterministic given the DB content and model weights, which makes it cacheable to disk.

**What this reveals:** LintGate needs a concept of **result-deterministic functions** — functions that aren't formally pure (they do I/O) but whose output is deterministic given stable inputs. `_encode_all_premises(conn, modules)` always returns the same embeddings for the same DB + model. A "deterministic I/O" hint would have flagged this for disk caching.

### Observation 6: Mutation testing on the hot path was the most actionable pre-optimization check

When I used `mutation_run_sampling` on `_resolve_step_directions` (the training data pipeline function), it found 0% kill rate — all 7 mutants survived. This was the most actionable finding of the session: it told me that my new domain override logic had zero test coverage, meaning any bug in the training signal would silently corrupt 321K examples.

The mutation prescriptions (BOUNDARY, SWAP, VALUE categories) directly translated to the 8 tests I wrote, which brought kill rate to 100%.

**What this reveals:** Mutation testing is LintGate's strongest prescriptive tool. The category-level prescriptions ("add boundary-value tests", "add parameter-order tests") are specific enough to act on immediately, and the kill rate provides a clear success metric. This is where LintGate adds the most value — not "you have 236 cacheable functions" but "this specific function has 7 surviving mutants in these 3 categories."

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck | No | N/A | N/A |
| Secrets-in-diff | No | N/A | N/A |
| Supply-chain (pip-audit) | Yes (earlier) | Partially | aiohttp/authlib upgraded |
| Type integrity | No | N/A | N/A |
| Security fast path (bandit) | No | N/A | N/A |
| Structure (STRUCT004/005) | Yes | Low | Expected for scripts/tests dirs |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| NumPy vectorization | 1 | Replace Python loop with array ops | Inner loop over fixed-size per-entity data (6 banks) |
| lru_cache on pure functions | 3 | `@functools.lru_cache(maxsize=N)` | Pure function with bounded input domain (63 unique pairs) |
| Entity ID set caching | 1 | Module-level dict cache with clear_caches() | Same SQL query repeated across many function calls |
| Premise embedding disk cache | 1 | `torch.save`/`torch.load` with SHA256 invalidation key | Expensive deterministic computation (20 min → 2 sec) |
| DB index addition | 2 | `CREATE INDEX` on entity_type, provenance | Full table scan on frequently filtered column |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | 0 | 0 | Same |
| Warnings | ~500 | ~500 | Same (perf work, not lint work) |
| Tests | 1314 | 1354 | +40 |
| Test suite wall-clock | 7.9s | 5.5s | **-30%** |
| navigate() warm (242K DB) | ~5.0s est. | 4.2s | **-16%** |
| eval_retrieval encode step | 20+ min | 2 sec (cached) | **-99.8%** |
| bank_score throughput | ~2M calls/sec est. | 7.7M calls/sec | **~4x** |
| _resolve_step_directions mutation kill | 0% | 100% | **Fixed** |

### Performance Tracking

| Metric | Before | After | Delta | Notes |
|--------|--------|-------|-------|-------|
| **Test suite wall-clock** | 7.9s | 5.5s | -2.4s (-30%) | 1354 tests, vectorized scoring helps even on small test DBs |
| **navigate() cold** | ~6.5s | 5.8s | -0.7s (-11%) | Entity ID cache eliminates re-query |
| **navigate() warm** | ~5.0s | 4.2s | -0.8s (-16%) | Vectorized bank scoring + lru_cache |
| **Premise encode** | 20+ min | 2 sec | -20 min (-99.8%) | Disk cache with SHA256 invalidation |

#### Performance Regressions

None detected.

#### Performance Wins

1. **Premise embedding cache** — 20 min → 2 sec (the dominant win)
2. **Test suite speedup** — 7.9s → 5.5s from vectorized scoring
3. **Entity ID cache** — ~200ms savings per navigate() call × 1000 calls = ~200 sec per eval run

#### What the profiling revealed that LintGate didn't

The true performance bottleneck is **SQLite I/O** (75% of navigate() time):
- `_batch_get_anchor_sets`: 1658ms (40%) — fetches 242K rows from entity_anchors
- `_batch_get_positions`: 1066ms (26%) — fetches 242K rows from entity_positions
- `_batch_get_names`: 373ms (9%) — fetches 242K rows from entities

The optimization opportunity still on the table: **pre-load the entire DB into NumPy arrays at startup** (one-time 3-second cost) would reduce navigate() from 4.2s to ~0.5s — an 8x improvement. LintGate didn't suggest this because it requires runtime profiling, not static analysis.

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Full test suite | Pass | 1354 passed, 5 subtests passed in 5.5s |
| Ruff clean | Pass | All checks passed |
| navigate() correctness | Pass | 176 proof_network + resolution + anchor_gap tests pass |
| Scoring contract | Pass | Vectorized scoring produces identical results to Python loop |

### Reproducibility Notes

Test suite timing varied ±0.3s across 3 runs (5.44s, 5.59s, 5.50s). Navigate() timing varied ±300ms across 5 warm runs (4084-4417ms). All consistent within noise.

---

## Part VI: Process Assessment

### The LintGate Workflow

```
controlplane_run(channels=performance,structure,lint)
→ controlplane_get_details(channel=performance) → PERFCH005 (236 cacheable)
→ spec_composition(module_a=scripts, module_b=src) → γ=49 on resolve()
→ spec_file_analyze(proof_network.py) → bank_score σ=8, pure, cacheable
→ spec_file_analyze(eval_retrieval.py) → _encode_all_premises σ=20, not pure
→ mutation_run_sampling(_resolve_step_directions) → 0% kill rate!
→ mutation_prescribe → BOUNDARY, SWAP, VALUE prescriptions
→ [write tests] → mutation_run_sampling → 100% kill rate
→ [implement optimizations]
→ [manual profiling reveals SQLite I/O bottleneck]
```

The workflow was productive but the most impactful insight (SQLite I/O) came from manual `time.perf_counter()` profiling, not from LintGate. The mutation testing loop was the most satisfying LintGate interaction — clear input, clear output, clear success metric.

### What Works Well

1. **Mutation testing prescriptions are immediately actionable.** "Add boundary-value tests for BOUNDARY category (1 surviving)" with specific category breakdowns directly translates to test code. The kill rate metric is unambiguous — 0% bad, 100% good.

2. **spec_composition γ analysis identifies real architectural hotspots.** The `resolve()` → `navigate()` path at γ=49 was genuinely the right place to look. The γ metric combines specification complexity with integration surface area in a way that highlights where bugs and performance issues accumulate.

3. **spec_file_analyze correctly classifies purity.** `bank_score` flagged as pure + cacheable, `_encode_all_premises` correctly flagged as impure — this classification was accurate and useful for deciding which functions to `@lru_cache`.

4. **PostToolUse hooks provide useful ambient context.** The `coherence=isolated; channels_run=4; warnings=87; edit_related=lint` annotations after each edit tell me whether my change affected quality signals without requiring a full re-scan. The `blocking=1` alerts were particularly useful — they made me fix the energy_refine too-many-args immediately.

5. **The controlplane's channel architecture is genuinely useful for scoped analysis.** Running `channels=performance,structure,lint` gave me exactly the information I needed without the noise of CVE findings and test effectiveness scores.

### What Could Be Better

1. **PERFCH005 needs call-site frequency analysis.** "236 cacheable functions" is noise. "3 cacheable functions in the navigate() hot loop, called 1.45M times" is actionable. Cross-reference purity detection with call-graph depth and loop membership to rank by estimated impact.

2. **No runtime profiling integration.** LintGate's performance analysis is entirely static. The actual bottleneck (SQLite I/O fetching 242K rows) is invisible because `_batch_get_positions` has low sigma, low complexity, and no interesting static properties. A simple `@profile` annotation or timing decorator that LintGate could analyze post-run would bridge this gap.

3. **Missing "result-deterministic" function concept.** `_encode_all_premises` produces deterministic output from stable inputs (DB + frozen model) but isn't pure (it does I/O). LintGate's purity detection correctly excludes it from cacheability hints, but a weaker property — "deterministic given stable inputs" — would have flagged it for disk caching, which was the 20-minute win.

4. **Pre-optimization mutation coverage should be a gate.** When I tried to optimize `_get_candidates()` with SQL pre-filtering, I broke the scoring contract because `_compute_bank_score` had 0% mutation coverage. LintGate should warn: "You're about to modify a function that calls `_compute_bank_score` (0% mutation kill rate). Run `mutation_run_sampling` first to establish a behavioral baseline."

5. **The PostToolUse hook output is too terse for performance context.** `loud=performance:fail` doesn't tell me what failed or why. If the performance channel had found something relevant to my edit, I'd want to see it inline, not have to make another tool call to find out.

---

## Part VII: The Agent's Experience

### How LintGate changed my approach to performance engineering

Without LintGate, I would have started with `time.perf_counter()` profiling and found the SQLite bottleneck directly. LintGate redirected me to work on cacheability and vectorization first, which produced real improvements (30% test suite speedup, 16% navigate() improvement) but missed the 75% SQLite I/O opportunity.

This isn't entirely a criticism — the static-analysis-first approach ensures correctness (mutation testing, spec coverage) before optimization, which is the right order. I just wish the tools had said "we can see cacheability and purity; we cannot see I/O costs — profile before concluding."

### Where I was surprised

The `spec_composition` tool was more useful than expected. I initially thought it was a theoretical metric, but the γ=49 finding directly pointed me to the right function to profile. The composition gap analysis is genuinely novel — I haven't seen another tool that quantifies integration surface complexity across module boundaries.

### What I would do differently next time

1. **Profile first, then use LintGate to verify.** The manual profiling revealed the SQLite bottleneck in 2 minutes. LintGate's analysis took longer and found a different (smaller) bottleneck. For performance engineering specifically, `time.perf_counter()` > static analysis > spec composition, in that order.

2. **Run mutation_run_sampling before any optimization.** I learned this the hard way when the SQL pre-filter broke the scoring contract. Establishing baseline kill rates before refactoring is essential.

3. **Use the premise cache pattern proactively.** For any function that takes > 1 second and reads from stable data, add disk caching with a content-hash invalidation key. Don't wait for LintGate to suggest it — it won't, because it can't detect "deterministic I/O."

### Trust Calibration

| Signal | Trust | Reason |
|--------|-------|--------|
| mutation_run_sampling | **High** | 0% → 100% kill rate is unambiguous. The category prescriptions translated directly to tests. |
| spec_composition γ | **Medium-high** | Correctly identified the hot integration surface, but γ measures complexity not cost. |
| PERFCH005 cacheable | **Low-medium** | Directionally correct (bank_score IS cacheable and HOT) but buried in 235 other functions. No ranking by impact. |
| spec_file_analyze purity | **High** | Correct classification in every case tested. |
| PostToolUse hooks | **Medium** | Useful ambient signal but too terse for performance context. |
| Performance channel overall | **Low** | Only found static properties (purity ratio, cacheability). Missed the actual bottleneck (I/O). |

---

## Part VIII: Broader Observations

### Static Analysis vs. Runtime Profiling: Complementary, Not Substitutable

LintGate's performance tools operate at the static-analysis level: purity detection, cacheability hints, complexity metrics, composition gaps. These are necessary for *correctness-preserving* optimizations (you need to know a function is pure before caching it). But they're insufficient for *identifying* what to optimize.

The 75%/6% split in this session (SQLite I/O vs. Python scoring) demonstrates the gap clearly. The Python scoring loop was the target of all three LintGate-guided optimizations (lru_cache, vectorization, entity ID cache). Together they improved scoring from ~800ms to ~233ms — a 3.4x improvement on 6% of the total cost. The SQLite I/O at 75% was untouched.

A runtime profiling integration would close this gap. Concretely:

1. **Timing annotations**: LintGate could emit `@lintgate_profile` decorators on functions identified by spec_composition γ analysis. After one profiled run, the timing data would feed back into the analysis.

2. **I/O detection**: Static analysis CAN detect "this function calls `conn.execute()` with a variable-length `IN (?)` clause" — that's a SQL N+1 pattern. LintGate could flag `_batch_get_positions(conn, entity_ids)` as "I/O cost scales linearly with `len(entity_ids)`."

3. **Data-flow caching analysis**: For functions that read from a "stable" source (same DB connection, same table, data doesn't change between calls), suggest result caching even if the function isn't pure.

### Prescriptive Tools: Where LintGate Is Strongest

The mutation testing loop — `mutation_run_sampling` → `mutation_prescribe` → write tests → re-run — is LintGate's highest-value workflow. It provides:
- **Clear diagnosis**: "7 mutants survive in 3 categories"
- **Specific prescriptions**: "Add boundary-value tests at boundary-1, boundary, boundary+1"
- **Verifiable outcome**: "Kill rate: 0% → 100%"

This is qualitatively different from lint warnings ("file too long") or spec findings ("under-specified"). The mutation tools convert a vague quality concern into a concrete, measurable, completable task. Every other LintGate tool should aspire to this level of prescriptive specificity.

### What Would Have Identified the SQLite I/O Bottleneck

For LintGate to have caught the SQLite bottleneck, it would need:

1. **SQL pattern detection in Python AST**: Identify `conn.execute(f"SELECT ... WHERE entity_id IN ({placeholders})", entity_ids)` as a pattern where cost scales with `len(entity_ids)`. Flag it as "O(N) I/O where N = candidate count."

2. **Data volume estimation**: From the DB schema and `init_db()`, estimate row counts. Cross-reference with `_get_candidates()` to estimate "this function returns ~242K IDs, which then drive 3 batch queries each fetching 242K rows."

3. **Caching opportunity for stable I/O**: Detect that `_batch_get_positions` is called multiple times with the same DB connection and that the underlying data doesn't change between calls. Suggest module-level caching analogous to `_entity_id_cache`.

4. **Memory-vs-disk tradeoff hint**: "Loading 242K × 6 positions into RAM costs ~12MB but saves 1066ms per navigate() call. At 1000 calls per eval, this is a 1066-second savings for a 12MB memory cost." This kind of cost/benefit calculation is computable from static analysis + schema inspection.

---

## Part IX: Economics

Skipped — no JSONL transcript available. Qualitative assessment: the LintGate tools added ~30 minutes of overhead (tool calls, interpreting results, acting on findings) and produced ~2 hours of measurable performance improvement (premise cache alone saves 20 min per eval run, entity ID cache saves ~3 min per eval run). Net positive ROI on the first eval run.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Mixed. PERFCH005 correctly identified cacheable functions but didn't rank by impact. spec_composition correctly found the hot integration surface. Neither found the actual bottleneck (SQLite I/O). |
| **Fix guidance** | Excellent for mutation testing (specific, actionable, verifiable). Adequate for cacheability (directionally correct but noisy). Missing for I/O optimization (blind spot). |
| **Workflow integration** | Good. The PostToolUse hooks provided useful ambient context. The controlplane channel filtering (performance,structure,lint) was effective for scoped analysis. |
| **Prescriptive tools** | Mutation testing is the standout: clear diagnosis → specific prescription → verifiable outcome. spec_file_prescribe is also good but less immediately actionable for performance work. |
| **Performance engineering** | Incomplete. Static analysis found the secondary bottleneck (Python scoring loop, 6% of cost) and missed the primary one (SQLite I/O, 75% of cost). The tools need runtime profiling, SQL pattern detection, and data-flow caching analysis to be useful for real performance engineering. |
| **Code generation guidance** | Strong for test generation (mutation prescriptions → tests). Not applicable for performance optimizations (no "here's the vectorized code" suggestions). |
| **PostToolUse hooks** | Useful but too terse. `loud=performance:fail` should include the specific finding, not just the channel name. Best when they surface blocking issues immediately after an edit. |
| **Noise level** | Moderate for performance. 236 cacheable functions is too many to act on without ranking. The earlier session's 130+ CVEs drowning code findings remains the bigger noise problem. |
| **Overall** | LintGate is excellent at what it does (static analysis, mutation testing, spec coverage) but has a genuine blind spot for I/O-bound and data-volume-driven performance issues. The mutation testing workflow is the gold standard for prescriptive tooling — other LintGate tools should aspire to its specificity. For performance engineering specifically, manual profiling remains essential; LintGate's contribution is ensuring the optimizations you write are correct (via mutation testing) rather than identifying what to optimize. |

### Specific Suggestions for the Model-Facing Information Pipeline

1. **PERFCH005 should rank cacheable functions by estimated call frequency.** Cross-reference with call graph: functions inside loops, inside other hot functions (by γ), or called from navigate()/search() get higher priority. Output: "bank_score: cacheable, called ~1.45M times from _score_candidates (loop over candidate_ids)" vs "GapRecord.to_dict: cacheable, called ~500 times from run_analysis."

2. **Add a PERFCH006: "I/O in hot loop" detector.** Detect `conn.execute()` calls inside functions called from high-γ paths, where the query has variable-length parameters (IN clauses). Flag: "_batch_get_positions does O(N) SQL I/O with N=len(candidate_ids). Consider pre-loading to module-level cache."

3. **Add a "result-deterministic" function property.** Beyond pure (no side effects) and impure, add "deterministic I/O" — functions that read from stable sources and produce the same output for the same source state. Flag these for disk caching with content-hash invalidation.

4. **Make PostToolUse hooks surface the specific finding.** Instead of `loud=performance:fail`, emit `loud=performance:fail(PERFCH005:bank_score_cacheable)`. The agent can decide whether to act without making another tool call.

5. **Add a pre-optimization mutation gate.** When the agent is about to edit a function, check its mutation coverage. If 0%, emit: "WARNING: _compute_bank_score has 0% mutation kill rate. Run mutation_run_sampling before optimizing to establish a behavioral baseline." This prevents the broken-pre-filter scenario.

6. **Add a "data volume estimation" step to the performance channel.** From DB schema inspection (table row counts, index usage), estimate the cost of batch queries. Flag: "entity_positions has 1.45M rows. _batch_get_positions fetches ~242K rows per call. At 1000 calls per eval, this is 242M row fetches — consider module-level caching."

7. **Integrate timing data when available.** If the agent has run `time.perf_counter()` profiling (which it often does), allow it to feed the results back to LintGate for cross-referencing with static analysis. A `report_timing(function, elapsed_ms, call_count)` tool would let LintGate's next analysis incorporate runtime data.
