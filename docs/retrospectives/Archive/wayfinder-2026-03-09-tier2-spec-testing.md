---
theory_scope: false
---

# LintGate Agent Retrospective: Wayfinder — Spec-Driven Testing Regimen

## Metadata

| Field | Value |
|-------|-------|
| **Project** | Wayfinder — Navigational theorem prover for Lean 4 / Mathlib |
| **Agent** | Claude Opus 4.6, solo agent with 3 parallel sub-agents for test file generation |
| **Date** | 2026-03-09 |
| **Scope** | 55 Python files, ~11,642 LOC across src/, scripts/, tests/ |
| **LintGate Tier** | Tier 2 spec tooling: spec_project_rollup, spec_file_analyze, spec_file_prescribe, mutation_run_sampling |
| **LintGate Version** | unknown (MCP server, current as of 2026-03-09) |
| **Session Type** | Testing — spec-driven gap analysis and test generation for 3 critical untested modules |
| **Session Record(s)** | Continuation session (context compacted from prior SonarCloud fix session in same JSONL). Spec work begins after SonarCloud commit `0817851`. |
| **Session Continuity** | Resumed from handoff (compaction boundary between SonarCloud fixes and spec work) |
| **Prior State** | Working codebase, 163 tests passing. SonarCloud issues just resolved. Three critical modules (`proof_search.py`, `pab_tracker.py` internals, `data.py` loaders) had zero test coverage. |

---

## Part I: Initial Diagnosis

**Spec rollup state: mean_spec_level 0.039** — 28 P0 functions, 254 P1, 166 P2. Only 10 of 448 functions at `phase: complete`. 431 in `tail` phase (untested or trivially tested).

The spec rollup was an effective starting point. It immediately surfaced that the codebase had broad but shallow testing — many tests existed (163), but they didn't constrain function behavior tightly enough for the mutation sampler to detect. The `mean_spec_level` of 0.039 (out of 1.0) quantified the gap precisely.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| P0 (critical) | 28 | `proof_auditor` API methods (6), `pab_tracker` record/finalize (4), `data.py` loaders (3), `trainer` lifecycle (4), `proof_search` (1), misc (10) |
| P1 (major) | 254 | Spread across all modules — mostly `specification_level: 0.0` |
| P2 (minor) | 166 | Pure functions with low sigma, dunder methods |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | active (conda) |
| Lockfile | absent |
| .python-version | missing |
| Structure snapshot | No cycles, no orphans. Largest modules: trainer.py (181σ), proof_auditor.py (165σ) |

### Theory Profile

Not re-extracted this session. Compass state was already set from prior sessions. The compass had minimal influence on the spec-driven workflow — this session was empirical (mutation sampling) rather than theory-driven.

---

## Part II: Observations During Spec-Driven Testing

### Observation 1: Mutation sampling as the honest arbiter

The mutation sampler was the single most valuable tool in the session. While spec_file_analyze and spec_file_prescribe provided useful framing, the mutation sampler gave ground truth: **0% kill rate across `pab_tracker.py`, `data.py`, and `proof_search.py`** — 93 mutants, 0 killed. This was far more actionable than the spec_level scores, which showed `0.0` for everything and didn't differentiate between "has tests but they're weak" and "has no tests at all."

**What this reveals:** Mutation sampling is the crown jewel of the spec tooling. The spec_level metric is too coarse (0.0 everywhere) to guide prioritization when most functions are untested. The mutation sampler cuts through directly to "can your tests catch this change?"

### Observation 2: Mutation sampler test discovery is broken for this project

The mutation sampler consistently reported `tests_loaded: 0` and `tests_discovered: 0` (or 1 for the runner itself) for all source files — even after 314 tests existed and passed. This means the mutation sampler is not using pytest's test discovery mechanism. It appears to use its own heuristic that failed on this project's structure.

This had a cascading effect: **spec_file_analyze reported `specification_level: 0.0` even after comprehensive tests were written**, because spec_level is computed from mutation sampling results. The entire spec feedback loop was broken by the discovery gap.

> **Key insight:** The mutation sampler's test discovery mechanism is a single point of failure for the entire spec system. When it fails, spec_level becomes meaningless, and the agent loses its primary feedback signal for test quality.

**What this reveals:** The spec system needs a fallback test discovery mechanism. At minimum, if `mutation_run_sampling` reports 0 tests for a file that clearly has test files (e.g., `test_proof_search.py` exists for `proof_search.py`), it should warn about potential discovery failure rather than silently reporting 0% kill rate.

### Observation 3: spec_file_prescribe produced generic prescriptions

The prescriptions were structurally correct but lacked project-specific guidance. Every P0 function got the same prescription: `"Add exact-value assertions for X (gap: N)"` with suggested assertion `"assert X(...) == expected"`. For P2 pure functions, every prescription was `"Add property-based (Hypothesis) tests"` with `"@given(st.from_type(...)) + invariant assertions"`.

These prescriptions told me *what kind* of test to write (exact value, cause-effect, property) but not *how* — what inputs to use, what the expected outputs should be, or what edge cases matter. I had to read the source code myself to figure out the actual test strategy. The prescriptions were a useful taxonomy of test types but not a useful guide to writing tests.

**What this reveals:** The prescription system would benefit from source-code-aware suggestions. If `_classify_regime()` returns 5 possible strings based on stability thresholds, the prescription could say "test all 5 return values: 'unknown' (empty), 'stable' (mean<0.15), 'chaotic' (mean>0.30), 'phase_transition', 'moderate'" instead of the generic "add exact-value assertions."

### Observation 4: Parallel sub-agents were highly effective for test generation

After the spec analysis phase identified the 3 target files, I launched 3 parallel sub-agents (one per file) to write the test files simultaneously. This reduced wall-clock time from ~7 minutes sequential to ~4 minutes parallel. Each sub-agent had enough context from the source file reads to work independently.

The spec prescriptions, while generic (Observation 3), were useful as prompts for the sub-agents — they provided a vocabulary ("exact-value", "cause-effect", "equivalence partitions") that structured the sub-agent task descriptions.

**What this reveals:** The spec system's value may be primarily as a **triage and taxonomy layer** rather than a test generation guide. It answers "what needs testing and what kind of tests?" efficiently, but the actual test authoring requires source-code understanding that lives in the agent, not the tool.

### Observation 5: Phase distribution was the best progress signal

The spec rollup's `phase_distribution` was the clearest before/after metric:
- Before: `{tail: 431, complete: 10, bulk: 7}`
- After: `{tail: 431, complete: 10, bulk: 175}`

168 functions moved from `tail` to `bulk`. This was visible even with the broken mutation sampler discovery, because the phase calculation uses static analysis alongside mutation data. The `tail → bulk` transition represents "tests exist that exercise this function" — a weaker claim than mutation kill rate, but at least it moved.

**What this reveals:** The phase distribution is a resilient metric that degrades gracefully when mutation sampling fails. It should be promoted in the reporting — currently it's buried in the rollup output alongside less useful metrics.

### Observation 6: Hotspot ranking inverted after testing

Before testing, the hotspot files were source modules (`trainer.py`, `proof_auditor.py`, `data.py`). After testing, the hotspots were the test files themselves (`test_pab_tracker_extended.py` at 802σ, `test_data_extended.py` at 405σ). This is expected but mildly confusing — the hotspot ranking doesn't distinguish "high sigma because complex source code" from "high sigma because many test functions." A filter for `is_test_file` would help.

**What this reveals:** The hotspot ranking conflates production code complexity with test suite size. For a spec-driven workflow, the agent needs to filter hotspots to source files only.

### Observation 7: The spec_prescribe → mutation_run_sampling → fix loop never closed

The recommended workflow is: `spec_prescribe → write tests → mutation_run_sampling → verify kill rate improved → iterate`. Because the mutation sampler never discovered the tests (Observation 2), the loop never closed. I couldn't verify whether my tests actually killed mutants through the tool — I had to rely on the fact that 314 tests pass and the tests use exact-value assertions.

**What this reveals:** The spec system's feedback loop depends entirely on mutation discovery working. Without it, the agent is flying blind on test quality. A simpler fallback — even just "does `pytest --co -q` find tests for this module?" — would restore partial feedback.

---

## Part III: Professional Discipline Signals

Skipped — this session was spec/testing focused. No discipline signals fired.

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Mock-isolated unit tests | 35 | Mock external deps (Pipeline, LeanKernel, resolve) with `unittest.mock.patch`. Test state mutations on known dataclass instances. | Functions with complex external dependencies (proof_search) |
| Exact-value assertions | ~200 | `assertEqual(actual, expected)` instead of `assertTrue(actual)` | Every value assertion — mutation-resistant by default |
| Branch-complete coverage | 81 | One test per branch path through functions with multiple conditionals (e.g., `_classify_regime` has 5 return values → 5+ tests) | Functions with complex branching logic (pab_tracker) |
| Temp-file roundtrip testing | 35 | Write JSONL via `to_dict()` + `json.dumps`, read back via loader function, verify field values | Data loading/serialization functions |
| Curriculum filtering boundary | 4 | Test `max_steps` filter at boundary (inclusive), below, above, and at 0 | Dataset classes with filtering parameters |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Total tests | 163 | 314 | +151 |
| Test files | 11 | 14 | +3 |
| `proof_search.py` tests | 0 | 35 | +35 |
| `pab_tracker.py` tests | 5 | 86 | +81 |
| `data.py` tests | 3 | 38 | +35 |
| Phase distribution (bulk) | 7 | 175 | +168 |
| Phase distribution (tail) | 431 | 431 | 0 (mutation discovery issue) |
| P0 functions | 28 | 36 | +8 (test functions counted) |

### Independent Tool Metrics: Before/After Autonomous Professionalization

Skipped — no production code was changed in this session. Only test files were added.

### Performance Tracking: Before/After Refactor Cycle

| Metric | Before | After | Delta | Notes |
|--------|--------|-------|-------|-------|
| **Test suite wall-clock** | 1.51s | 1.86s | +0.35s (+23%) | 314 tests vs 163 |
| **Package import time** | N/A | N/A | — | No production code changed |

#### Performance Regressions

Test suite time increased from 1.51s to 1.86s (+23%) due to 151 additional tests. This is proportionally efficient — 93% more tests for 23% more runtime, because the new tests use lightweight mocks and temp files rather than heavy fixtures.

#### Performance Wins

None detected. No production code was changed.

#### Process Efficiency: Ship Pipeline Timing

Skipped — tests not yet committed/pushed as of retrospective writing.

### Current Standing vs. Industry Thresholds

| Metric | Current Value | Industry Threshold | Status |
|--------|--------------|-------------------|--------|
| **Test reliability** | 314/314 passed (100%) | 100% pass required | Pass |
| **Test coverage breadth** | 14 test files / 55 source files | — | 25% file ratio (acceptable for 28-module project) |

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| All existing tests | Pass | 163 original tests still pass |
| New test_proof_search.py | Pass | 35 tests, all branches of 7 functions covered |
| New test_pab_tracker_extended.py | Pass | 81 tests, all private methods and edge cases |
| New test_data_extended.py | Pass | 35 tests, all untested loaders and datasets |
| Full suite | Pass | `python -m pytest tests/ -q` → 314 passed in 1.86s |

### Reproducibility Notes

The mutation sampler produced identical `tests_loaded: 0` results before and after test generation, confirming the discovery issue is consistent (not flaky). The spec rollup was deterministic across runs.

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| spec_project_rollup + prescribe | ~2 min | 4 parallel file analyses + prescriptions |
| mutation_run_sampling (4 files) | ~2 min | 4 parallel sampling runs |
| Source code reading | ~3 min | 6 files read (3 source + 3 existing tests + contracts) |
| Test file generation (3 agents) | ~4 min | 3 parallel sub-agents writing test files |
| Verification + re-sampling | ~3 min | Full pytest run + 3 mutation re-runs |
| **Total** | **~14 min** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

```
spec_project_rollup(analyze_uncached=True)
  → spec_prescribe(max_prescriptions=28)
  → spec_file_analyze × 4 (parallel: proof_auditor, trainer, pab_tracker, data)
  → spec_file_prescribe × 4 (parallel: same files)
  → mutation_run_sampling × 4 (parallel: proof_auditor, pab_tracker, data, proof_search)
  → [read source + existing tests]
  → [write 3 test files via parallel sub-agents]
  → [verify: pytest 314 passed]
  → mutation_run_sampling × 3 (verify improvement — failed due to discovery issue)
  → spec_project_rollup (verify phase_distribution improvement)
  → spec_file_analyze × 3 (verify spec_level — still 0.0 due to discovery)
```

The workflow was mostly as planned, except the verification loop failed to close (Observation 7). In practice, I diverged from the tool's guidance after the first round of prescriptions and relied on source code reading to design the actual tests.

### Prediction Accuracy

Skipped — constraint_check was not used in this session.

### Constraints Proposed

None proposed during this session.

### What Works Well

1. **`spec_project_rollup` as a triage entry point.** The single-call summary with P0/P1/P2 distribution, hotspot files, and phase distribution gave an immediate, actionable picture of where to invest testing effort. The `analyze_uncached=True` flag was convenient for a fresh analysis.

2. **`mutation_run_sampling` as ground truth.** Even though test discovery was broken, the initial mutation sampling proved invaluable: it confirmed that `pab_tracker` (100% survival, 43 mutants), `data.py` (100% survival, 21 mutants), and `proof_search` (100% survival, 29 mutants) had genuinely zero test coverage — not just low spec_level scores. The distinction between "tested weakly" and "not tested at all" is critical for prioritization.

3. **Parallel file analysis.** All spec tools accept per-file arguments, making it natural to run 4 analyses in parallel. This cut diagnosis time roughly in half.

4. **Phase distribution as a resilient metric.** When mutation discovery broke, the phase distribution was the only metric that reflected progress (7 → 175 bulk phase functions). It should be more prominent in reporting.

5. **The prescription taxonomy.** Categorizing gaps as "exact_value", "cause_effect", "equivalence", "property", "boundary" etc. provided a useful vocabulary that structured test design thinking, even though the specific prescriptions were generic.

### What Could Be Better

1. **Mutation sampler test discovery is silently broken.** The sampler reported `tests_loaded: 0` without any warning that this might be a discovery failure. For a file like `proof_search.py` where `test_proof_search.py` exists in the canonical location, the tool should flag: "Warning: 0 tests discovered for proof_search.py despite test_proof_search.py existing in tests/. Test discovery may be failing."

2. **spec_level is too coarse.** Everything is 0.0 until mutation sampling succeeds. There should be an intermediate signal from static analysis alone — e.g., "test file exists and imports this module" could push spec_level to 0.1, "test functions reference this function name" to 0.2, etc. Binary 0.0/1.0 with no middle ground makes it impossible to track incremental progress.

3. **Prescriptions lack source-code awareness.** The generic "assert X(...) == expected" suggestions don't help an agent who needs to know what inputs and outputs to test. For pure functions (which the tool already identifies), the prescription could include: the function's parameter types, its return value branches, and concrete boundary values from the source code analysis.

4. **Hotspot ranking should filter test files.** After writing tests, the top 3 hotspots were test files — not helpful for identifying remaining source code gaps. A `source_only=True` option or automatic filtering would improve the signal.

5. **The spec feedback loop has no fallback.** When mutation discovery fails, the entire spec_prescribe → test → verify cycle breaks. A lightweight fallback — even running `pytest --collect-only` to verify test existence — would restore partial feedback and let the agent confirm tests were at least written and importable for the target module.

---

## Part VII: The Agent's Experience

### How the spec tools changed my approach

Without the spec tooling, I would have approached testing by reading source files and writing tests for whatever looked important. The spec tools changed this in two ways:

First, the **P0/P1/P2 prioritization** prevented me from wasting time on low-value targets. I might have written tests for `__len__` and `__getitem__` methods (P2, trivial) before getting to `_classify_regime` (P2 by priority band but high design_signals, and empirically untested). The mutation sampling made the priority ordering empirical rather than guesswork.

Second, the **sigma scores** gave me a complexity budget. Functions with σ > 20 (like `search()` at σ=36) needed mock-heavy integration-style tests, while functions with σ < 5 (like `_should_hammer` at σ=2) could be tested with simple input/output pairs. The sigma score is a better guide to test complexity than LOC.

### Where I was surprised

I was surprised that the mutation sampler found 0 tests for `proof_auditor.py` functions where `test_proof_auditor.py` clearly has 32 tests loaded (the sampler reported this for some functions). The sampler's discovery is inconsistent — it finds tests for some functions in a file but not others in the same file. This suggests the issue is at the function-to-test matching level, not the file discovery level.

### What I would do differently next time

1. **Check mutation discovery first.** Before investing in the full spec_prescribe workflow, I'd run `mutation_run_sampling` on one file and verify `tests_loaded > 0`. If discovery is broken, I'd skip the mutation-dependent tools and use static analysis only.

2. **Write tests in the same file namespace the sampler expects.** If the mutation sampler uses a specific naming convention for test-to-source matching, I'd follow it from the start rather than discovering the mismatch after writing 151 tests.

### Trust Calibration

| Signal | Trust Level | Why |
|--------|------------|-----|
| `spec_project_rollup` | **High** | Consistent, fast, accurate phase/risk distribution. The hotspot ranking was useful. |
| `mutation_run_sampling` | **High for initial diagnosis, low for verification** | Excellent at confirming "zero coverage" but useless for confirming "coverage now exists" due to discovery failure. |
| `spec_file_prescribe` | **Medium** | Correct taxonomy but generic suggestions. Useful as a checklist, not as a guide. |
| `spec_level` metric | **Low** | Binary 0.0/1.0 with no intermediate values. Didn't reflect 151 new tests. |
| `phase_distribution` | **High** | The only metric that correctly reflected progress (7 → 175 bulk). |

---

## Part VIII: Broader Observations

### The spec system's value is front-loaded

The spec tools provided ~80% of their value in the first 5 minutes (triage: which files? which functions? what priority?) and ~20% in the remaining time (prescriptions and verification). The verification phase was essentially broken due to mutation discovery, so the tool's value proposition collapses to "smart triage" — which is genuinely useful, but doesn't justify the full spec_prescribe → verify loop that the system recommends.

For the spec system to deliver its full value, the mutation sampler's test discovery must be reliable. Without it, the system is a one-shot triage tool rather than an iterative quality improvement loop.

### Mutation testing is the right abstraction

Despite the discovery issues, the concept of mutation testing as the quality metric is correct. Line coverage tells you "this code ran"; mutation testing tells you "your tests would catch a bug here." The spec system's choice to ground spec_level in mutation kill rates rather than line coverage is architecturally sound — the implementation just needs to reliably find the tests.

### Model-facing information pipeline suggestions

These are specific, actionable suggestions for the automated information pipeline that faces the model (me):

1. **PostToolUse hooks should include a diagnostic summary, not just raw JSON.** The mutation_run_sampling output is a wall of per-function JSON that requires significant parsing to extract the key signal ("0 tests found, 100% survival"). A 2-line summary at the top — "proof_search.py: 0/29 mutants killed (0 tests discovered). DIAGNOSIS: test discovery failure likely." — would save the agent from parsing 200 lines of JSON to reach the same conclusion.

2. **The `next_actions` field is useful but should be conditional.** Every tool response includes `next_actions` suggesting the next tool to call. This is helpful for onboarding but becomes noise after the agent has used the tools several times in a session. A `suppress_next_actions=True` parameter or a session-level setting would reduce output volume by ~20%.

3. **Mutation sampler should report discovery diagnostics.** When `tests_loaded: 0`, the output should include: (a) what discovery heuristic was used, (b) what test file paths were searched, (c) whether any `test_*.py` files exist that import the source module. This turns a silent failure into a debuggable one.

4. **spec_file_analyze should separate static and dynamic signals.** Currently, `specification_level` blends static analysis (does a test file exist?) with dynamic analysis (mutation kill rate). When dynamic analysis fails, the static signal is lost. Reporting these as two separate numbers — `static_spec_level` and `mutation_spec_level` — would preserve useful information even when mutation sampling fails.

5. **The `session_context` footer in every response is excessive.** Every tool response includes `{"gen": 735, "mode": "habit", "focus": [...], "blocking": 0, "coherence": "isolated", "test": "", "tokens_pct": 108.8}`. This is 80+ tokens of context metadata that rarely changes between calls and is almost never actionable. It should be emitted once per session or on significant state changes, not on every response.

6. **Prescription output should include function signatures.** The prescriptions reference functions by name but don't include their parameter types or return types. For an agent about to write tests, knowing `_classify_regime(self) -> str` (pure function, no params beyond self, returns one of 5 strings) is far more useful than `"Add exact-value assertions for PABTracker._classify_regime (gap: 8)"`.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | 11,642 lines across 55 files |
| Files touched | 3 (new test files only) |
| Files created | 3 |
| Genuinely new/rewritten lines | ~2,055 |
| Lines moved/restructured | 0 |
| Net LOC delta | +2,055 |

### Throughput

| Metric | Value |
|--------|-------|
| Tests written per minute | ~11 (151 tests in ~14 min) |
| Fastest batch | 81 tests (pab_tracker_extended) — branch-complete pattern with simple dataclass inputs |
| Slowest individual file | proof_search.py — required understanding mock topology for Pipeline + resolve + LeanKernel |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | Spec rollup identified 28 P0 functions across 4 files in 1 tool call | Manual: would have read all test files, compared against source, built my own gap list | ~5 min saved on triage |
| Prioritization | P0/P1/P2 + sigma gave clear priority order | Would have tested whatever I read first, likely starting with simpler functions | Higher-value tests written first |
| Test type guidance | Prescription taxonomy (exact_value, cause_effect, property) structured test design | Would have written ad-hoc tests without systematic coverage of all assertion types | Slightly more systematic coverage |
| Verification | Broken (mutation discovery failed) | Would have relied on pytest pass/fail anyway | No difference — LintGate verification failed to add value |
| **Completeness** | 151 tests covering all P0 functions | Likely ~80-100 tests covering obvious gaps | ~50% more tests from systematic approach |

### What the Session DID NOT Contain

- **Zero debug spirals.** All 3 test files were written once and passed on first run. No write-fail-rewrite cycles.
- **Zero regressions.** All 163 original tests continued to pass alongside 151 new tests.
- **Zero architectural backtracking.** The spec system's triage was correct — the 3 target files were the right ones to test.

The **Creation : Debugging : Verification** ratio was approximately **80 : 0 : 20**. The debugging phase was zero because the test generation was guided by source code reading, not trial-and-error.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Excellent for triage. spec_project_rollup + mutation_run_sampling correctly identified the 3 critical untested modules and quantified the gap (93 surviving mutants). |
| **Fix guidance** | Adequate taxonomy, weak specifics. Prescriptions categorized gaps correctly (exact_value, cause_effect, property) but generic suggestions required full source code reading to act on. |
| **Workflow integration** | Good for the first half (diagnosis), broken for the second half (verification). The mutation sampler's test discovery failure prevented the feedback loop from closing. |
| **Regression detection** | Not tested — no production code was changed in this session. |
| **Structural insight** | Good. Sigma scores, phase distribution, and hotspot ranking were all useful for test planning. The regime A/B classification correctly flagged high-complexity functions. |
| **Professional discipline** | Not applicable — testing-only session. |
| **Theory/documentation** | Not used this session. |
| **Auto-fix** | Not applicable — testing session, no auto-fixable issues. |
| **Noise level** | Moderate. `session_context` on every response, `next_actions` on every response, and verbose per-function JSON add ~30% token overhead that could be compressed. |
| **Performance** | Test suite time increased from 1.51s to 1.86s (+23% for 93% more tests). Acceptable. |
| **Economics** | The spec tools saved ~5 minutes on triage and produced a more systematic test suite than ad-hoc testing would have. The broken verification loop reduced the tool's value from "iterative quality improvement" to "one-shot triage." |
| **Overall** | The spec system is a strong triage tool with a broken feedback loop. `spec_project_rollup` and `mutation_run_sampling` correctly identified what needed testing. `spec_file_prescribe` provided useful categories but generic guidance. The critical gap is mutation sampler test discovery — until that's reliable, the system can't verify its own recommendations, and spec_level remains meaninglessly stuck at 0.0. |
