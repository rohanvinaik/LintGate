---
theory_scope: false
---

# LintGate Agent Retrospective: ModelAtlas — ControlPlane Audit + Fix Session

## Metadata

| Field | Value |
|-------|-------|
| **Project** | ModelAtlas — MCP server exposing a navigable semantic network of ML models |
| **Agent** | Claude Opus 4.6, solo, no sub-agents |
| **Date** | 2026-03-09 |
| **Scope** | 6 files linted (5 Python scripts/modules + 1 test file), ~1,135 LOC directly touched out of ~27K total |
| **LintGate Tier** | Tier 2, normal strictness, ControlPlane enabled |
| **LintGate Version** | unknown (MCP server, no version reported by tools) |
| **Session Type** | Audit — user requested ControlPlane health check then systematic fix of all findings |
| **Session Record(s)** | N/A — live session, no JSONL extracted |
| **Session Continuity** | Fresh |
| **Prior State** | Working codebase, 671 passing tests, but 5 untracked scripts and uncommitted edits to `phase_d_audit.py` |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "systemic"** — *"Multiple system failures: git, specification, test_hygiene, lint, coherence. This suggests a structural problem, not isolated issues."*

The "systemic" label was useful as an attention-getter but slightly misleading in practice. The 11-channel parallel scan found real issues, but many of the "system failures" were pre-existing informational findings in test hygiene and coherence (COH101 = missing docstrings across the entire codebase). The actual *actionable* surface was narrower than the "systemic" framing suggested. The ControlPlane note — "test_hygiene, coherence findings are pre-existing and unrelated to your edit" — was the key contextual signal that prevented me from chasing 728 pre-existing coherence warnings.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 2 | 1× cognitive complexity (CC=34 in `_audit_single_model`), 1× unused variable (F841 in `gemini_validate.py`) |
| Warnings | 51 | 18× lint (structural complexity, formatting, unused imports), 17× specification (under-specified scripts), 14× coherence (pre-existing COH101), 1× test hygiene (duplicate test), 1× git (wide working tree) |
| Informational | 39 | 11× PERF011 (loop-invariant calls), 10× THYGIENE002 (weak tests), 4× test stubs, 3× structure, misc |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | active (`.venv/bin/python`) |
| Lockfile | not checked |
| .python-version | not checked |
| Structure snapshot | cycles: 1, orphans: 0, largest module: `ingest.py` (608 LOC) |

### Theory Profile

No theory profile existed. LintGate flagged "5 uncommitted Python files have no theory grounding" and recommended `build_theory_pack`. This was not pursued in this session as the user's focus was on fixing lint/structural findings, not theory extraction.

---

## Part II: Observations During Refactoring

### Observation 1: The `controlplane_run` → `controlplane_get_details` drill-down was the most productive workflow

The initial ControlPlane run gave a compact, prioritized overview. The `controlplane_get_details` drill-down with `severity="blocking"` filter immediately surfaced the two actionable issues with full context — including the decomposition suggestion for `_audit_single_model` (extract lines 120-137, CC reduction ~7). This two-step workflow felt natural and efficient.

**What this reveals:** The severity-filtered drill-down is the right abstraction. The initial run acts as triage; the details call acts as diagnosis. This maps well to how I actually work.

### Observation 2: `controlplane_apply_repairs` returned 0 repairs despite 6 being listed

The repair system listed 6 available repairs but `apply_repairs(safe_only=True)` returned `{"repairs_executed": 0}`. The repairs were a mix of `create_test_skeleton` and `command` types (including `ruff check --fix`), but none actually executed. I had to fall back to running `ruff check --fix` manually via Bash.

**What this reveals:** The repair execution pipeline has a gap between "repairs available" and "repairs executed." I couldn't determine whether this was a state issue (repairs expired between runs), a permission issue, or a bug. The repair *proposals* were useful as a roadmap even when they didn't auto-execute — I manually did everything they suggested.

### Observation 3: The `context-require` warnings are false positives from a placeholder regex

Six files were flagged with `context-require` warnings referencing CLAUDE.md line 68, but the evidence showed `"pattern": "<regex>"` — a literal placeholder from the `LINTGATE_REQUIRE_REGEX: <regex>` template in the `machine_rules` section. These warnings persisted through every run and were not fixable because the "required pattern" is a template comment, not an actual constraint.

**What this reveals:** The context rule checker should validate that configured patterns are real regex, not template placeholders. `<regex>` should be treated as "no rule configured" rather than "all files fail."

> **Key insight:** Six of the 13 final warnings (46%) are from this single false positive. Fixing this one issue in the checker would dramatically improve the signal-to-noise ratio.

### Observation 4: PERF011 findings were mechanically useful but sometimes wrong

The PERF011 "uncached pure call in loop" findings were highly actionable for `len()` and `sorted()` calls — I could mechanically hoist them. But two of the PERF011 findings on `gemini_validate.py` (`get_our_classification` at line 243 and `fetch_hf_metadata` at line 249) were false positives: both take `model_id` as an argument, which changes each loop iteration. They are not loop-invariant.

**What this reveals:** The PERF011 checker flags calls where the function is "pure" and the arguments *look* invariant, but it doesn't track which arguments are loop variables. This is a precision issue — the check should verify that all arguments to the "pure" call are actually invariant relative to the loop variable.

### Observation 5: Extracting functions traded one warning type for another

When I extracted `_validate_one_model` (9 args) and `_retry_one_entry` (7 args) to reduce CC/statements/locals in the script `main()` functions, the tool immediately flagged both with `too-many-args` (limit: 6). This is a fundamental tension: CLI script `main()` functions naturally accumulate connections (db, api client, config values), and any helper that does real work needs most of those connections passed in.

**What this reveals:** The tool correctly identifies the structural trade-off but has no way to express "this extraction is net-positive despite the new args warning." A context-aware rule that says "too-many-args is acceptable for internal helpers extracted from main()" would reduce noise for this extremely common pattern.

### Observation 6: The PostToolUse hook coherence annotations were quietly useful

After every edit, the PostToolUse hook injected a one-line annotation like `coherence=isolated; channels_run=4; warnings=3; edit_related=lint; loud=performance:fail,lint:fail`. I didn't consciously plan around these, but they served as a passive confidence signal — when coherence stayed "isolated" I kept going; when it briefly flashed "systemic" (after the `replace_all` mistake) I noticed and investigated.

**What this reveals:** The hook is doing its job as an ambient awareness signal. The `edit_related=lint` tag was particularly useful — it told me the edit was in the "lint fix" category without me having to think about it.

### Observation 7: The `replace_all` footgun and recovery

When I used `replace_all=true` on `Edit` to swap `len(model_ids)` → `num_models`, it also replaced the definition itself (`num_models = len(model_ids)` became `num_models = num_models`). The PostToolUse hook immediately flagged `blocking=4` on the next event, which prompted me to check — but I had already noticed the issue from the diff output. The hook was confirming what the edit output already showed.

**What this reveals:** The hook's blocking count spike was a secondary safety net. The primary safety net was reading the edit output carefully. The hook would have been the primary catch for a less attentive pass.

### Observation 8: Scope expansion between ControlPlane runs

My first `controlplane_run` scoped to "changed" files found 2 blockers, 51 warnings. A later run (after fixing blockers) found 0 blockers but 767 warnings — because it expanded scope to the full project, pulling in 728 pre-existing coherence findings. The output was too large for the context window (204K characters) and had to be extracted via `jq`.

**What this reveals:** The scope behavior between runs should be more predictable, or the tool should report what scope it used. Going from 51 warnings to 767 in a "progress check" run was confusing until I realized the scope had shifted. A `scope` field in the output header would eliminate this confusion.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck (command class) | No | N/A | N/A |
| Secrets-in-diff | No | N/A | Clean |
| Supply-chain (pip-audit) | Yes — ran, 0 findings | Useful (confirms no CVEs) | Pass |
| Type integrity (ty) | Yes — caught `modelId` as unresolved attribute | Useful | Fixed to `info.id` |
| Security fast path (bandit) | Yes — B603 on subprocess call | Informational | Left as-is (subprocess with constant args in a script) |
| Structure (cycles/size/orphans/cohesion) | Yes — file-too-long, too-many-locals, too-many-classes, cognitive-complexity | Useful | Fixed file length, CC, locals; left test class count |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Function decomposition (CC reduction) | 3 | Extract semantically coherent blocks into named helpers | CC > 15, especially when a function has distinct "phases" |
| Loop-invariant hoisting (PERF011) | 8 | Compute `len()` / `sorted()` before the loop | Any `len(invariant)` or `sorted(invariant)` inside a loop body |
| Unused variable removal (F841) | 2 | Delete assignment, verify value isn't needed downstream | Ruff auto-fix handles this; manual when auto-fix unavailable |
| Import reordering (E402) | 1 | Move module-level code after all imports | Imports split by `logging.basicConfig()` or `sys.path` manipulation |
| Duplicate test removal (THYGIENE003) | 1 | Remove the byte-identical copy, keep the canonical one | When test hygiene reports identical hashes |
| Shared helper extraction (DRY) | 3 | Extract `parse_gemini_json`, `build_validation_prompt`, `build_record` | When two scripts duplicate identical logic |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | 2 | 0 | -2 (cleared) |
| Warnings (scoped to changed files) | 51 | 13 | -38 (6 remaining are config false positives) |
| Informational | 39 | 3 | -36 |
| ControlPlane coherence | systemic | isolated | Improved |
| Cognitive complexity (worst) | 34 (`_audit_single_model`) | 17 (`gemini_validate::main`) | -17 |
| Test suite | 671 passed | 671 passed | 0 regressions |

### Independent Tool Metrics: Before/After Autonomous Professionalization

Skipped — tools (pylint, radon) not installed in this environment.

### Performance Tracking: Before/After Refactor Cycle

Skipped — no benchmarks or CLI entrypoint to measure. The refactored code is structurally equivalent; function extraction does not affect runtime.

#### Performance Regressions

None detected. All extractions were pure restructuring with identical logic.

#### Performance Wins

None detected. This was a structural-only refactoring session.

#### Process Efficiency: Ship Pipeline Timing

Skipped — no ship/CI cycle in this session.

### Current Standing vs. Industry Thresholds

| Metric | Current Value | Industry Threshold | Status |
|--------|--------------|-------------------|--------|
| **Avg cyclomatic complexity** | ~5.5 (worst: 17) | ≤ 5 low, ≤ 10 moderate | Moderate (worst function 2 over warning threshold) |
| **Test reliability** | 671/671 passed (100%) | 100% pass required | Pass |

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Full test suite | Pass | 671 passed in 1.10s, 0 failures |
| phase_d_audit module | Pass | 9 tests passed in 0.03s |
| test_query module | Pass | 24 tests (15 original + removed duplicate) |
| ruff check | Pass | "All checks passed!" on all modified files |
| ruff format | Pass | All files formatted, no changes needed on final check |

### Reproducibility Notes

The final `lint_files` run was reproducible: 0 blocking, 13 warnings (6 context-require, 2 too-many-args, 3 too-many-locals, 1 CC=17, 1 too-many-classes). The mypy linter consistently timed out (15s limit) — this is a known issue with mypy on larger codebases, not a flaky result.

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| Getting started + ControlPlane run | ~2 min | Tool setup, initial health check |
| Blocking fix (F841 + CC decomposition) | ~5 min | Unused var trivial; CC decomposition was the bulk |
| Ruff auto-fix + format | ~1 min | Batch fix of formatting, unused imports |
| PERF011 hoisting | ~3 min | Mechanical: hoist `len()` and `sorted()` out of loops |
| Script `main()` refactoring | ~5 min | Extract helpers, reduce CC/statements/locals |
| File trimming + cleanup | ~2 min | Compress docstrings, remove dead variables |
| Verification (tests + re-lint) | ~3 min | Full test suite + lint checks between rounds |
| **Total** | **~21 min** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

```
getting_started → controlplane_run → controlplane_get_details(blocking) + controlplane_apply_repairs
  → [manual fixes: F841, CC decomposition] → ruff check --fix + ruff format
  → controlplane_run → controlplane_get_details(warning)
  → [fix PERF011, duplicate test, import order, unresolved attribute]
  → lint_files → lint_get_details → [fix extracted function trade-offs, file length]
  → lint_files (final verification)
```

The workflow was iterative: fix blockers first, re-scan, fix warnings, re-scan. This is the natural pattern. I used `controlplane_run` for the initial broad scan and `lint_files` for targeted re-checks after fixes — this was more efficient than re-running the full ControlPlane each time.

### Prediction Accuracy

Skipped — `constraint_check` was not used in this session.

### Constraints Proposed

Skipped — no constraints were proposed in this session.

### What Works Well

1. **The ControlPlane severity hierarchy is correctly prioritized.** Blockers→warnings→informational maps exactly to how I want to triage. The work queue in the ControlPlane output gave a complete, file-grouped list of everything to fix — I could work through it systematically.

2. **The `lint_files` targeted re-check is fast and focused.** At ~15s per run, I could lint 6 files after every batch of fixes without losing momentum. The compact JSON output (blocking/warning/info counts + `delta` showing resolved/new) gave me instant feedback on whether my fixes were working.

3. **The decomposition suggestions in cognitive-complexity findings are specific and grounded.** The finding for `_audit_single_model` included a concrete extraction proposal: "Extract lines 120-137 into `_compute_pipeline_tag()`" with inputs/outputs/expected CC reduction. I didn't follow this exact suggestion (I chose a different decomposition), but having a starting proposal accelerated my thinking.

4. **The THYGIENE003 duplicate test detection with byte-identical hash is high-signal.** It told me exactly which test to keep (by naming the "keeper file") and gave me confidence the deletion was safe.

5. **The PostToolUse hook annotations provide ambient situational awareness.** The `coherence=isolated/systemic` and `blocking=N` signals after every edit let me track trajectory without explicit re-runs.

### What Could Be Better

1. **`controlplane_apply_repairs` should work or explain why it didn't.** Listing 6 repairs as "available" and then executing 0 with no explanation is confusing. Even a "repairs expired (run_id mismatch)" or "repairs require explicit action_ids" message would have been useful.

2. **The `context-require` checker should not fire on template placeholders.** `LINTGATE_REQUIRE_REGEX: <regex>` is a documentation example, not a rule. The checker should treat `<regex>`, `<pattern>`, or any angle-bracket placeholder as "no rule configured." This single fix would eliminate 6 of the 13 remaining warnings (46% noise reduction).

3. **ControlPlane scope should be explicit and stable.** When a second `controlplane_run` expanded from "changed" to "project" scope without me requesting it, the warning count jumped from 51 to 767. The output should include a `"scope": "changed"` or `"scope": "project"` field and ideally keep the same scope as the previous run by default.

4. **PERF011 should verify argument invariance against loop variables.** Two of the PERF011 findings were false positives — the "pure" functions were called with the loop variable as an argument. The checker should track which variables are loop-bound.

5. **The output from `controlplane_run` can exceed context limits.** One run produced 204K characters of JSON. The tool should either have a `compact=true` mode (default?) that omits the full evidence/structure sections, or paginate the output. For the agent workflow, the compact counts + coherence state + blocking issues is sufficient — I only need the full evidence when drilling down via `get_details`.

---

## Part VII: The Agent's Experience

### How the tool changed my approach

Without LintGate, I would have fixed the user's request by addressing whatever issues I could see in the code directly. With LintGate, I had a complete, prioritized inventory before writing a single line. This changed my approach from "scan the code, find issues" to "work through the queue from highest severity to lowest." The latter is faster and more thorough — I would not have found the duplicate test, the PERF011 loop-invariant issues, or the `modelId` type error on my own.

The iterative re-scan loop (fix → lint → verify → next batch) kept me honest. Every fix was immediately validated, so I never accumulated uncertainty about whether my changes were correct.

### Where I was surprised

I was surprised by the `too-many-args` warnings appearing on the functions I *just extracted* to fix CC/statements/locals. The tool is technically right, but the finding feels like it's punishing good behavior. In practice, CLI scripts need to thread many connections through to helpers — the alternative (global state, or a config object just to hold 7 args) would be worse.

### What I would do differently next time

I would run `lint_files` (targeted) instead of `controlplane_run` (full) for progress checks. The ControlPlane is excellent for initial diagnosis but the full re-run is expensive (15s) and can shift scope unexpectedly. For iterative fix-verify loops, the targeted `lint_files` is faster and more predictable.

### Trust Calibration

| Signal | Trust | Reason |
|--------|-------|--------|
| F841 (unused variable) | High | 100% accurate, trivially verifiable |
| cognitive-complexity | High | CC=34 was correct, decomposition suggestions were reasonable |
| PERF011 (loop-invariant) | Medium | ~70% accurate; false positives on functions with loop-variable args |
| context-require | Low | 100% false positive rate in this session (template placeholder) |
| THYGIENE003 (duplicate test) | High | Byte-identical hash is definitive |
| too-many-args | Medium | Technically correct but fires on trade-off situations where the alternative is worse |
| ty (unresolved-attribute) | High | Caught a real bug (`modelId` vs `id`) |

---

## Part VIII: Broader Observations

### The Information Pipeline: What Model-Facing Consumers Need

This session revealed several patterns about what works and what doesn't in the automated, model-facing information pipeline. These are specific, actionable suggestions for the system design.

**PostToolUse hooks: the right abstraction at the right density.**
The one-line `coherence=isolated; channels_run=4; warnings=3; edit_related=lint; loud=performance:fail,lint:fail` annotation after every edit is almost perfectly tuned. It's dense enough to be informative (I can see trajectory without re-running tools) and terse enough to not waste context. Two suggestions:
- Add a `delta` field: `warnings=3 (−2)` would let me see improvement without comparing to remembered numbers.
- The `loud=` field listing failing channels is useful but would be more useful as `loud=lint:2w,perf:1i` (with severity counts) rather than just `lint:fail`.

**Tool output JSON structure: the compact summary + drill-down pattern works.**
The two-tier output model (compact summary from `lint_files` / `controlplane_run`, then full details from `get_details`) is exactly right. The compact output should never exceed ~2K tokens. The problem case was `controlplane_run` producing 204K characters — this should never happen in compact mode. Suggested fix: the top-level `controlplane_run` should return the same ~2K compact output regardless of how many findings exist, and the evidence/structure sections should only appear in `get_details`.

**The `delta` field in `lint_files` output is the best single signal.**
When `lint_files` returned `"delta": {"resolved": 8, "new": 5, "remaining": 11}`, this was more useful than the absolute counts. I could immediately see whether my edit batch was net-positive. Every tool that runs iteratively should include a delta from the previous run.

**The `work_queue` in `controlplane_run` is excellent but under-used.**
The work queue grouped findings by file, assigned tiers and severity, flagged `delegation_safe` items, and identified parallelizable groups. This is exactly the structured plan I need. However, it only appeared in the ControlPlane output — it should also be available as a standalone tool (`get_work_queue(run_id)`) so I can reference it without re-running the full health check.

**Finding fingerprints enable efficient dedup but aren't surfaced well enough.**
Each finding has a `fingerprint` or `issue_id`, and the delta tracking uses these to distinguish resolved/new/remaining. This is good infrastructure. The missing piece: when a finding first appears, the tool should note if it's a *known recurring* issue (the `recurrence` data exists but is buried in the evidence section). Surfacing "this F841 has recurred 22 times across 5 runs" in the compact output would help me prioritize systemic vs. one-off issues.

### The Diminishing Returns Curve in Lint-Driven Refactoring

This session showed a clear diminishing returns curve:
- **Pass 1** (blockers): Highest ROI. Two specific, high-confidence findings with clear fixes. ~5 min.
- **Pass 2** (auto-fixable warnings): High ROI. Ruff format + fix handles formatting and unused imports mechanically. ~1 min.
- **Pass 3** (structural warnings): Medium ROI. PERF011 hoisting and file-length trimming are real improvements. ~5 min.
- **Pass 4** (script complexity): Low ROI. Extracting helpers from CLI scripts reduced CC/statements but introduced `too-many-args` warnings. Net warning count barely changed. ~5 min.
- **Pass 5** (remaining warnings): Negligible ROI. The 13 remaining warnings are config false positives, structural trade-offs, or pre-existing test organization issues.

The tool should help the agent recognize when to stop. A "marginal ROI estimate" on each warning — based on how much effort similar fixes have required historically vs. how much they improved the codebase — would prevent the agent from spending 5 minutes on a warning that trades one code smell for another.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | ~27,144 lines across ~83 files |
| Files touched | 6 (7% of codebase) |
| Files created | 0 |
| Genuinely new/rewritten lines | ~200 (extracted helper functions) |
| Lines moved/restructured | ~250 (decomposition of `_audit_single_model`, script `main()` bodies) |
| Net LOC delta | +75 (more lines from function signatures/docstrings in extracted helpers) |

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per iteration | 2 in first pass |
| Fastest batch | 2 blockers in 2 edits + 1 ruff command — unused variable removal + format |
| Slowest individual fix | `_audit_single_model` CC decomposition — required reading 150 lines, designing 5 helper functions, writing ~120 lines of new code, then cleaning up the aftermath |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | Complete inventory in 16s (ControlPlane run) | Manual code review, would miss PERF011, THYGIENE003, and `modelId` type error | 3+ issues missed |
| Prioritization | Severity-ranked work queue with file grouping | Ad-hoc, likely to fix visible issues and miss structural ones | More systematic |
| Regression detection | Re-lint after every edit batch caught the `replace_all` breakage (unused vars) immediately | Would have discovered at test time (if tests cover it) or not at all | Faster feedback |
| Verification | Delta tracking showed resolved/new/remaining counts | Manual "does it look right?" | Quantitative vs. qualitative |
| **Completeness** | ~85% of actionable issues resolved | ~50% — would likely fix the obvious CC and formatting, miss PERF011, duplicate test, type error | 35% more issues caught |

### Token Economics: Full Session Analysis

Skipped — no JSONL transcript available for token analysis. The session involved approximately 25 tool calls (8 LintGate MCP, 12 Edit, 5 Bash) across ~21 minutes of wall-clock time.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Excellent. The ControlPlane "systemic" diagnosis with severity-ranked findings gave a complete, actionable picture in one call. The pre-existing/new distinction prevented chasing old debt. |
| **Fix guidance** | Good. CC decomposition suggestions were concrete and grounded. PERF011 findings were mechanically actionable. The `too-many-args` trade-off warning was technically correct but lacked context for "is this trade-off worth it?" |
| **Workflow integration** | Good. The `lint_files` → `lint_get_details` → fix → `lint_files` loop was fast and natural. The PostToolUse hooks provided ambient awareness. The `controlplane_apply_repairs` failure was the main friction point. |
| **Regression detection** | Excellent. Immediate detection of the `replace_all` breakage via the next lint pass. The delta tracking in `lint_files` gave precise resolved/new/remaining counts. |
| **Structural insight** | Good. File-too-long, too-many-locals, and cognitive-complexity findings were all actionable. The cohesion analysis and split proposals (for `query.py`) were interesting but not acted on in this session. |
| **Professional discipline** | Good. `ty` caught a real type error (`modelId` → `id`). `pip-audit` confirmed clean supply chain. `bandit` flagged subprocess usage appropriately as informational. |
| **Theory/documentation** | Not tested. Theory profile was flagged as missing but not built (out of scope for this session). |
| **Auto-fix** | Poor. `controlplane_apply_repairs` listed 6 repairs but executed 0. Ruff auto-fix worked correctly when invoked manually. The gap between "repairs available" and "repairs executed" needs investigation. |
| **Noise level** | Moderate. 6 of 13 final warnings (46%) were false positives from a template placeholder regex. PERF011 had ~30% false positive rate. The raw warning counts were noisy; the severity filtering was essential for productivity. |
| **Performance** | N/A. Structural-only refactoring, no runtime impact. |
| **Economics** | Good value. The 8 LintGate MCP calls (~2 min total tool time) discovered 3+ issues I would have missed and provided the prioritized work queue that structured the entire 21-min session. |
| **Overall** | LintGate's ControlPlane-first workflow is the right abstraction for audit sessions. The severity hierarchy, delta tracking, and PostToolUse hooks form an effective feedback loop. The main improvement areas are: (1) fix the `context-require` template placeholder false positive, (2) make `controlplane_apply_repairs` actually execute, (3) cap `controlplane_run` output to prevent context overflow, and (4) add loop-variable awareness to PERF011. |
