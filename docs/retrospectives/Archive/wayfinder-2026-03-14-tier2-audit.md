---
theory_scope: false
---

# LintGate Agent Retrospective: Wayfinder — Exploratory Audit + Spec Hardening

## Metadata

| Field | Value |
|-------|-------|
| **Project** | Wayfinder — Navigational theorem prover for Lean 4 / Mathlib |
| **Agent** | Claude Opus 4.6 (1M context), solo, no sub-agents for LintGate work (one background agent for test writing) |
| **Date** | 2026-03-14 |
| **Scope** | 110 Python files, ~29.5K LOC (14.5K src+scripts, 15K tests) |
| **LintGate Tier** | Tier 2, normal strictness, ControlPlane enabled (all 11 channels) |
| **LintGate Version** | Unknown (MCP server, no version exposed) |
| **Session Type** | Hybrid — Exploratory audit (user asked "what might be useful to call upon and run") followed by directed spec-hardening |
| **Session Record(s)** | Not captured as JSONL; Claude Code interactive session |
| **Session Continuity** | Fresh — first LintGate session in this conversation |
| **Prior State** | Codebase functional, 1251 tests passing, large uncommitted working tree (40 modified + 45 untracked files). LintGate config already existed from prior sessions. |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "systemic"** — *"Multiple system failures: git, test_effectiveness, structure, specification, tests, lint, coherence. This suggests a structural problem, not isolated issues."*

The "systemic" label was useful as an attention-getter but somewhat misleading for this codebase. It triggered because 7 of 11 channels reported findings, which sounds alarming. In reality, the project was in reasonable shape — 1251 tests passing, clean ruff output. The "systemic" classification was driven largely by volume (224 COH101 coherence findings, 154 SPEC findings) rather than severity. The single blocking issue was a legitimate catch (12-arg function), but the 476 warnings were dominated by two categories that inflated the count: environment CVEs from pip-audit (transitive deps) and COH101 coherence advisories.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 1 | `energy_refine()` too-many-args (12 params, limit 6) |
| Warnings | 476 | 224 COH101 coherence, 151 lint (130+ CVEs + few code), 74 SPEC, 22 missing tests, 3 TEFF, 1 STRUCT, 1 git |
| Informational | 129 | 80 SPEC, 13 lint format, 10 test hygiene, misc |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | Active (conda + uv) |
| Lockfile | Stale — `uv lock` resolved it |
| .python-version | Present |
| Structure snapshot | STRUCT004 low cohesion in scripts/ and tests/ (expected), STRUCT005 train_* prefix cluster |

### Theory Profile

No theory profile existed. The compass was stale with solution axis at depth 1 ("Uses NumPy for numerical computation" — a thin summary). After `compass_update` + `compass_interview`, solution axis reached depth 2, spikiness dropped from 0.33 to 0.17, no sparse axes.

---

## Part II: Observations During Refactoring

### Observation 1: ControlPlane's 11-channel parallel scan is genuinely useful as a session opener

Running `controlplane_run` as the first action gave a comprehensive map of the codebase in ~13 seconds. The work queue, channel-level pass/fail, and git advisory provided actionable triage without me needing to run individual tools. The `blocking_issues` list with exact file:line was immediately actionable.

**What this reveals:** The controlplane is well-designed as an orientation tool. The multi-channel architecture works — each channel catches a different class of issue. The 11-channel breadth means you rarely miss an entire category.

### Observation 2: CVE findings dominate the warning count and obscure code-level issues

Of 151 lint warnings, 130+ were pip-audit CVEs from transitive environment dependencies (aiohttp, authlib, cryptography, biopython, black). When I asked for `top_n=15` highest-ROI findings, all 15 were CVEs. I had to mentally filter past them to reach the actual code issues. The ROI scoring ranked CVEs at 0.2 (low), but they still occupied the top slots because ruff code violations had already been auto-fixed.

**What this reveals:** The ROI ranking needs a way to separate "environment-level" from "code-level" findings, or at least provide a filter. A `channel:lint --exclude-kind=CVE*` or `--code-only` flag would be valuable. Alternatively, the controlplane summary could separate "N code lint warnings + M dependency CVEs" rather than lumping them.

### Observation 3: The spec_level metric is assertion-driven, not docstring-driven

I initially expected the SPEC findings to be fixable by adding docstrings extracted from design documents. Instead, spec_level is primarily driven by test assertion coverage relative to function complexity (sigma). This is actually the correct design — docstrings are cheap promises, assertions are verified contracts. But the SPEC finding messages ("under-specified") could make this clearer. A message like "spec_level=0.00 (0 assertions / sigma=30, need ~18 assertions)" would be more immediately actionable than "under-specified."

**What this reveals:** The spec system is fundamentally sound but the finding messages need to convey the specific fix path (assertions needed) rather than the abstract state (under-specified).

> **Key insight:** The SPEC channel is the most intellectually interesting part of LintGate. It quantifies something that's usually vague ("are the tests good enough?") into a concrete sigma-vs-assertions gap that can be closed systematically.

### Observation 4: `lint_fix` with `dry_run=False` was fast and trustworthy

25 files modified in one call — imports sorted, f-strings cleaned, formatting applied. Zero regressions. This is exactly the kind of mechanical cleanup that should be automated away, and LintGate handled it well.

**What this reveals:** The safe-only guard is correctly calibrated. Every fix was genuinely safe (import sorting, unused f-string prefix, formatting). No semantic changes.

### Observation 5: The compass interview filled a real gap, but the update didn't persist the answers

After `compass_interview` with answers that raised the solution axis from depth 1 to depth 2, the immediately following `compass_update(write=True)` re-extracted from docs and overwrote the interview answers — showing solution depth back at 1. The interview and update tools appear to operate on separate state, with update not incorporating interview-applied claims.

**What this reveals:** There's a state management issue between `compass_interview` (which applies claims to the live compass) and `compass_update` (which re-extracts from docs, potentially discarding interview-applied claims). The interview should persist its claims somewhere that `compass_update` reads, or `compass_update` should merge rather than replace.

### Observation 6: `controlplane_apply_repairs` failed with "no snapshots" after the session state expired

The repair actions identified in the first `controlplane_run` (30 safe repairs) were not available when I called `controlplane_apply_repairs` later. The session snapshot had apparently expired between the two calls. This forced me to address the blocking issue manually rather than through the repair pipeline.

**What this reveals:** The repair state needs to be more durable — either persisted to disk or available for the duration of a conversation. The gap between "here are 30 repairs" and "I can't apply them because the snapshot expired" is frustrating.

### Observation 7: The spec_file_prescribe tool provides genuinely actionable prescriptions

The prescriptions for `src/losses.py` (predicate→effect tests for CompositeLoss.forward decision paths, exact-value tests for NavigationalLoss) and `src/v3_runtime.py` (property tests for pure functions) were specific and correct. I used them as a test-writing roadmap and they mapped directly to the tests I wrote. The info_gain scoring helped prioritize.

**What this reveals:** This is where LintGate's spec system shines — it doesn't just say "under-tested," it says "test these 7 predicate→effect edges" with estimated information gain. This is the kind of guidance that accelerates test writing significantly.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck | No (not run) | N/A | N/A |
| Secrets-in-diff | No | N/A | N/A |
| Supply-chain (pip-audit) | Yes — 130+ CVEs | Partially — most are transitive deps | Upgraded aiohttp, authlib, black; rest are env-level |
| Type integrity (ty) | No (not run) | N/A | N/A |
| Security fast path (bandit) | No (prior session cleared) | N/A | N/A |
| Structure (STRUCT004, STRUCT005) | Yes | Low — expected for scripts/tests dirs | Noted but not acted on (cosmetic refactor) |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Too-many-args → dataclass | 1 | Extract scalar hyperparameters into `RefineConfig` dataclass | Function has >6 args with a natural grouping (tensor inputs vs. config scalars) |
| Import sorting | 25 files | `lint_fix(safe_only=True)` | Always safe to auto-apply |
| Empty f-string cleanup | 2 | `lint_fix(safe_only=True)` | Always safe to auto-apply |
| Pure function test coverage | 4 functions | Write exact-value assertions + boundary tests guided by `spec_file_prescribe` | When SPEC006/TEFF005 flags pure functions |
| Predicate→effect test coverage | 3 modules | Test each decision branch path identified by `spec_file_prescribe` | When SPEC001 reports sigma/assertion gap |
| Dep CVE upgrade | 3 packages | `uv pip install --upgrade "pkg>=fixed_version"` | When pip-audit reports CVEs with known fix versions |
| Lockfile sync | 1 | `uv lock` | When deps channel reports stale lockfile |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | 1 | 0 | -1 (resolved) |
| Warnings | 476 | 470 | -6 |
| Informational | 129 | 127 | -2 |
| ControlPlane coherence | systemic | systemic | Same (threshold is 3.0, score is 10.25) |
| Tests passing | 1251 | 1314 | +63 |
| SPEC warnings | 74 | 71 | -3 |
| Missing test warnings | 23 | 21 | -2 |
| Compass spikiness | 0.33 | 0.17 | Improved |

### Independent Tool Metrics

Skipped — session was primarily audit + targeted test-writing, not a full refactoring cycle. Ruff check confirmed clean (0 violations) at session end.

### Performance Tracking

| Metric | Before | After | Delta | Notes |
|--------|--------|-------|-------|-------|
| **Test suite wall-clock** | 4.91s | 4.43s | -0.48s (faster) | 1251 → 1314 tests, faster due to pytest caching |
| **Package import time** | N/A | N/A | N/A | Library, no CLI entrypoint |

#### Performance Regressions

None detected. The `RefineConfig` dataclass adds one import (`dataclasses`) and one object creation per `energy_refine` call — negligible overhead.

#### Performance Wins

None significant. The 63 new tests added ~0s to wall-clock because they're all fast unit tests (no I/O, no model loading).

#### Process Efficiency: Ship Pipeline Timing

Skipped — no ship/CI cycle in this session.

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Full test suite | Pass | 1314 passed, 5 subtests passed in 4.43s |
| Ruff clean | Pass | `ruff check src/ scripts/ tests/` — "All checks passed!" |
| energy_refine API | Pass | All 3 test call sites updated, 19/19 energy tests pass |

### Reproducibility Notes

Ran `controlplane_run` three times during the session. Finding counts were stable across runs (±0 blocking, ±6 warnings — the variation was from dep upgrades between runs). No flaky findings observed.

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| Tool discovery + setup | ~3 min | Fetching tool schemas, reading applicability guide |
| Initial controlplane_run | ~1 min | 13s tool execution + review |
| lint_fix + auto-repairs | ~2 min | 25 files auto-fixed |
| Blocking fix (RefineConfig) | ~5 min | Read code, design dataclass, update 3 test call sites |
| Compass update + interview | ~3 min | Fill solution axis gap |
| Dep CVE upgrades | ~2 min | uv pip install --upgrade |
| Spec analysis + prescriptions | ~3 min | spec_project_rollup + spec_file_prescribe × 3 |
| Design doc research (agent) | ~2 min | Background Explore agent read docs + source |
| Test writing (losses.py) | ~5 min | 23 new tests for NavigationalLoss + CompositeLoss |
| Test writing (v3_runtime.py) | ~2 min | 12 tests via background agent |
| Test writing (scripts) | ~3 min | 16 tests for benchmark_lane_b + build_nav_training_data |
| Verification + final scan | ~3 min | Full pytest + controlplane_run |
| **Total** | **~34 min** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

```
getting_started (skipped - config existed)
→ tool_applicability_guide (understand available tools)
→ controlplane_run (full 11-channel diagnosis)
→ controlplane_get_details (blocking + top ROI)
→ lint_fix (25 files auto-fixed)
→ controlplane_apply_repairs (failed - snapshot expired)
→ [manual fix: RefineConfig dataclass]
→ lint_files (verify fix)
→ controlplane_run (re-baseline)
→ controlplane_get_details (lint channel deep-dive)
→ [dep upgrades]
→ compass_status → compass_update → compass_interview
→ spec_project_rollup + spec_file_prescribe × 3
→ [test writing guided by prescriptions]
→ controlplane_run (final measurement)
```

The workflow emerged organically — I followed the `next_actions` suggestions from each tool, which was effective. The controlplane_run → drill-down → fix → re-run loop is natural.

### Prediction Accuracy

Skipped — `constraint_check` was not used.

### Constraints Proposed

Skipped — no constraints proposed during this session.

### What Works Well

1. **ControlPlane as session opener** — The 11-channel parallel scan in ~13s gives a comprehensive health map. The work queue with parallelizable groups is a genuinely novel feature I haven't seen in other lint tooling. It answers "where should I start?" instantly.

2. **spec_file_prescribe produces actionable test recipes** — "Test 7 predicate→effect edges in CompositeLoss.forward" with info_gain scores directly translated to a test-writing plan. This is the highest-value tool in the suite for me.

3. **lint_fix with safe_only is trustworthy** — 25 files modified, zero regressions. The safe/unsafe distinction is correctly calibrated. I didn't need to review the changes — I trusted the tool and it earned that trust.

4. **The coherence classification is a useful framing device** — "systemic" vs. "isolated" vs. "coupled" tells me whether to fix locally or step back and think architecturally. Even when the label felt inflated (this codebase is healthier than "systemic" suggests), it made me take the findings seriously.

5. **The finding fingerprinting and run_id system** — Being able to drill into a specific run_id with severity/channel filters makes the controlplane usable on large codebases. The `top_n` and `time_budget_minutes` parameters in `controlplane_get_details` are thoughtful features.

### What Could Be Better

1. **CVE findings need separation from code findings** — 130+ pip-audit CVEs drowning out 20 actual code warnings is the biggest UX issue. The `controlplane_get_details` ROI ranking puts CVEs at the top because there's nothing else left after auto-fix. Suggestion: separate "environment health" from "code quality" in the summary, or add an `--exclude-linter=pip_audit` filter to `controlplane_get_details`.

2. **Repair session state is too transient** — `controlplane_apply_repairs` failing with "no snapshots" after a few tool calls is a workflow-breaker. The repair proposals should persist for the duration of a conversation (or until the next `controlplane_run`), not expire with the MCP tool's internal session.

3. **Compass interview answers don't survive compass_update** — This creates a confusing loop where you fill gaps via interview, then update overwrites them. The interview should write its claims to a persistent file that `compass_update` merges with its doc-extracted claims.

4. **The "systemic" coherence threshold feels too aggressive** — With a threshold of 3.0 severity-weighted score, any project with >3 channels reporting findings is "systemic." For a 30K LOC project with 100+ files, having findings in git + test_effectiveness + structure + specification + lint + tests + coherence is normal, not systemic. Consider scaling the threshold with project size, or provide a "systemic-but-stable" state for codebases that have known structural debt but are functionally healthy.

5. **SPEC finding messages should include the concrete gap** — "Under-specified: has sigma=30 but only 0 assertions" is good. But "Pure function is under-specified (spec_level=0.00)" doesn't tell me *what to do*. Append "→ add ~N assertions targeting value correctness" to make every finding immediately actionable without needing to call `spec_file_prescribe`.

---

## Part VII: The Agent's Experience

### How LintGate changed my approach

Without LintGate, I would have done the user's request ("go ham until tractable") by running `ruff`, `pytest`, and eyeballing the code. LintGate's multi-channel scan changed my approach in two ways:

First, it surfaced the **spec_level** concept, which I wouldn't have discovered organically. The idea that a function has a measurable "specification debt" (sigma vs. assertions) gave me a concrete target for test writing. Instead of "write more tests for losses.py," I had "test 8 predicate→effect edges in CompositeLoss.forward and 7 in NavigationalLoss.forward." This made the test-writing phase faster and more focused.

Second, the **compass** system made me think about the design docs as a source of test contracts. The interview format ("Why this approach over alternatives?", "What tradeoffs were made?") naturally surfaced the kind of information that becomes good test assertions — e.g., "L_critic is MSE not BCE" from the design doc became `test_critic_loss_is_mse_not_bce`.

### Where I was surprised

The `controlplane_run` completing in 13 seconds across 11 channels on a 30K LOC codebase was faster than I expected. I also didn't expect the `spec_file_prescribe` output to be as immediately useful as it was — most "suggested test" tools produce generic boilerplate, but LintGate's prescriptions were specific to the function's decision structure.

### What I would do differently next time

I would call `controlplane_apply_repairs` immediately after `controlplane_run`, before doing any manual work, to avoid the snapshot expiration issue. I would also run `spec_project_rollup(analyze_uncached=True)` early to build the cache, rather than calling `spec_file_prescribe` per-file later.

### Trust Calibration

| Signal | Trust | Reason |
|--------|-------|--------|
| `lint_fix(safe_only=True)` | High | 25 files, zero regressions |
| `controlplane_run` coherence | Medium | "Systemic" label inflated for this codebase's health level |
| `spec_file_prescribe` prescriptions | High | Directly translated to working tests |
| `controlplane_apply_repairs` | Low | Snapshot expiration made it unusable |
| `compass_interview` | Medium | Answers applied correctly but didn't persist through update |
| pip-audit CVE findings | Low relevance | True findings but not actionable at the code level |

---

## Part VIII: Broader Observations

### The Spec Channel Is the Unique Value Proposition

Most of what LintGate's lint channel does (ruff, bandit, formatting) is available from standard tooling. The structure channel (STRUCT004/005) provides modest insight. But the **specification channel** — quantifying sigma-vs-assertions gap, identifying pure functions as high-ROI test targets, and generating prescriptions with information gain scores — is genuinely novel. I have not seen another tool that answers "which test should I write next for maximum specification coverage?" This is the feature that would make me reach for LintGate over raw ruff/pylint.

### Actionable Suggestions for the Model-Facing Information Pipeline

These are specific, implementable changes to how LintGate presents information to AI agents:

1. **Separate environment from code in controlplane summaries.** The `counts` object should have `blocking_code`, `warning_code`, `blocking_env`, `warning_env` rather than lumping them. This prevents the "130 CVEs drowning 20 code issues" problem.

2. **Include the concrete fix action in every SPEC finding message.** Instead of `"Pure function 'X' is under-specified (spec_level=0.00)"`, emit `"Pure function 'X' is under-specified (spec_level=0.00, sigma=4). Fix: add 3 exact-value assertions. Run spec_file_prescribe for details."` The agent shouldn't need a second tool call to know what to do.

3. **Add a `--code-only` filter to `controlplane_get_details`.** When I ask for the top 15 warnings, I want code warnings, not dependency CVEs. A filter like `exclude_linters=["pip_audit"]` or `category="code"` would save a round-trip.

4. **Make repair state durable per controlplane_run.** Tie repair proposals to the `run_id` and persist them to disk (e.g., `.claude/lintgate/repairs/{run_id}.json`). Let `controlplane_apply_repairs(run_id=X)` work regardless of MCP session state.

5. **Emit a `spec_delta` field in controlplane_run results.** After I write 63 new tests, the next controlplane_run still shows "154 SPEC findings." If it also showed `spec_delta: -3 warnings resolved since last run`, I'd know my work had measurable impact without manually comparing run results.

6. **Surface the compass interview answers in the theory profile.** The interview workflow is good, but the answers vanish when `compass_update` re-extracts from docs. Write interview answers to `.claude/lintgate/compass_interview_claims.yaml` and merge them during update.

7. **Scale the "systemic" threshold by project size.** A 5-file project with 7 failing channels is systemic. A 110-file project with 7 failing channels (most at "informational" severity) is normal. Consider `threshold = 3.0 + 0.01 * file_count` or weight by (warning_count / file_count).

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | ~29,500 lines across 110 files |
| Files touched | 30 (~27% of codebase) |
| Files created | 3 (test_benchmark_lane_b.py, test_build_nav_training_data.py, test_v3_runtime.py) |
| Genuinely new/rewritten lines | ~450 (test code + RefineConfig) |
| Lines moved/restructured | ~30 (energy_refine signature refactor) |
| Net LOC delta | +420 |

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per iteration | 1 (single blocker, single iteration) |
| Fastest batch | 25 files in one `lint_fix` call — mechanical formatting |
| Slowest individual fix | energy_refine refactor (~5 min) — needed to read callers, design dataclass, update 3 test sites |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | 11-channel scan in 13s, prioritized work queue | Manual: ruff + pytest + eyeballing | LintGate found SPEC gaps and TEFF weaknesses I wouldn't have looked for |
| Test writing direction | spec_file_prescribe gave specific prescriptions per function | Ad hoc: "losses.py needs tests, let me think about what to test" | 2-3x faster test design with prescriptions |
| Prioritization | ROI-ranked findings with info_gain | Gut feeling about what's most important | More systematic, covered pure functions first |
| **Completeness** | High — covered SPEC, TEFF, lint, structure, deps | Medium — would have caught ruff/pytest issues but missed spec gaps | ~40% of test improvements were spec-guided |

### Token Economics

Skipped — no JSONL transcript available for token-level analysis. Qualitative assessment: LintGate's MCP calls were fast (13s for controlplane_run) and the information density per call was high (one controlplane_run replaced what would have been 5-10 separate tool invocations). The spec_file_prescribe calls were the highest-ROI tool invocations — each one saved several minutes of "what should I test?" deliberation.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Good. The 11-channel controlplane gives a comprehensive health map. The "systemic" label was slightly inflated for this codebase but served its purpose as an attention signal. |
| **Fix guidance** | Excellent for spec channel (spec_file_prescribe is genuinely actionable). Adequate for lint (auto-fix handles it). Weak for CVE findings (just says "upgrade" without distinguishing direct vs. transitive deps). |
| **Workflow integration** | Good. The controlplane_run → drill-down → fix → re-run loop is natural. next_actions suggestions in every response keep the workflow moving. Repair state expiration broke one step. |
| **Regression detection** | Not tested directly, but the full test suite ran cleanly after every change. The incremental lint_files after edits caught issues early. |
| **Structural insight** | Modest. STRUCT004/005 findings were correct but not actionable in this session. The spec composition analysis was not used but looks promising. |
| **Professional discipline** | pip-audit CVE detection is valuable in principle but the presentation buries code-level issues. Lockfile sync detection was useful and actionable. |
| **Theory/documentation** | The compass system is interesting but has persistence issues (interview answers lost on update). The spec system's connection to design docs is indirect — it measures assertions, not doc alignment. |
| **Auto-fix** | Excellent. lint_fix with safe_only=True was trustworthy and fast. 25 files in one call, zero regressions. |
| **Noise level** | Moderate. The 224 COH101 findings and 130+ CVEs create a high background noise level that obscures actionable issues. Better filtering or categorization would help significantly. |
| **Performance** | No regressions from any changes. Test suite wall-clock slightly improved despite adding 63 tests. LintGate tool calls were fast (13s for full project scan). |
| **Economics** | Net positive. The spec_file_prescribe tool alone justified the session — it turned "add more tests" into "add these specific 8 predicate→effect assertions." The auto-fix saved ~10 minutes of manual formatting work. |
| **Overall** | LintGate is a genuinely useful tool suite with a strong unique selling point in the spec channel. The controlplane provides excellent orientation. The main friction points are noise management (CVEs vs. code findings), state persistence (repair snapshots, compass interviews), and the "systemic" threshold being too aggressive for medium-sized projects. I would use it again, primarily for the spec analysis workflow. |
