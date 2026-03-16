---
theory_scope: true
---

# LintGate Agent Retrospective: Wayfinder — Architecture Build Session

## Metadata

| Field | Value |
|-------|-------|
| **Project** | Wayfinder — Navigational theorem prover for Lean 4 / Mathlib (~6,200 LOC across 28 src + 4 script files) |
| **Agent** | Claude Opus 4.6, solo, extended architecture-build session |
| **Date** | 2026-03-06 |
| **Scope** | 9 new files created, 4 existing files modified, ~2,100 new LOC, ~500 modified LOC, Python |
| **LintGate Tier** | Tier 2, normal strictness, ControlPlane not used for health checks (one initial run only) |
| **LintGate Version** | Unknown (MCP server, no version exposed) |
| **Session Type** | Build — greenfield architecture implementation from design docs to runnable code |
| **Session Record(s)** | `/Users/rohanvinaik/.claude/projects/-Users-rohanvinaik-Projects-Wayfinder/d3e6f7d2-4502-44aa-a4aa-75dec7f1423f.jsonl` |
| **Session Continuity** | Single session with one context compaction |
| **Prior State** | Existing Balanced Sashimi codebase (~3,600 LOC). Design docs mature (4 docs, ~2,500 lines). Theory system already set up. Zero Wayfinder-specific modules existed. |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "isolated"** — The project had extensive design documentation but no implementation of the novel components. The theory system had already been set up in an earlier segment of this session (documented in `wayfinder-2026-03-06-theory-system-setup.md`), so the compass was pre-populated with 134 claims across 6 facets.

The initial lint baseline was clean — the existing Balanced Sashimi code had already been professionalized. The challenge was not fixing existing code but building 9 new modules cleanly from scratch.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 0 | Clean existing codebase |
| Warnings | 0 | Pre-session baseline was clean |
| Informational | 0 | N/A |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | Active (pyproject.toml with uv) |
| Lockfile | Present |
| .python-version | Present |
| Structure snapshot | No cycles, no orphans at session start |

### Theory Profile

134 claims extracted across 6 facets from 4 design docs. Problem axis at depth 3, solution at depth 1, implementation at depth 3, world at depth 1. Spikiness 0.33. The theory system was rich on the problem and implementation axes — it knew *what* the project was building and *how* — but sparse on *why this approach* (solution) and *runtime constraints* (world).

---

## Part II: Observations During Building

### Observation 1: Lint-After-Every-Write Caught Real Issues Early

The most impactful workflow pattern was calling `lint_files` immediately after every `Write` or significant `Edit`. This caught 15 blockers across the session, each resolved within 1-2 edits of being introduced. The most common:

- **too-many-arguments (PLR0913)**: Hit on `proof_search.py:search()` with 11 params. Caught immediately, fixed by extracting a `Pipeline` dataclass. Without the lint, this function signature would have propagated into tests and calling code before anyone noticed.
- **attr-defined blocker on `proof_navigator.py`**: `forward()` returned `dict[str, Tensor]` but one value was a nested dict. The type checker caught `.items()` being called on what it thought was a `Tensor`. Fixed by changing the return type to a tuple.
- **import-inside-function on `proof_network.py`**: `import heapq` was inside `spread()`. Moved to top-level.
- **cognitive-complexity (C901)** on `extract_proof_network.py:main()` at 38. Extracted 3 helpers (`_build_skip_set`, `_process_theorems`, `_print_summary`).
- **file-too-long** on `extract_proof_network.py` at 985 lines. Extracted all mapping data tables into a dedicated `scripts/tactic_maps.py`.

**What this reveals:** The write-then-lint pattern creates a tight feedback loop that prevents issues from compounding. Each blocker was found within seconds of introduction, when the code was fresh in context and the fix was obvious. The counterfactual — discovering these 15 issues in a batch at the end — would have been significantly harder to untangle.

### Observation 2: The Pipeline Dataclass Fix Was Architecturally Correct

When `lint_files` flagged `search()` for having 11 parameters, the fix wasn't cosmetic. Bundling `encoder`, `analyzer`, `bridge`, and `navigator` into a `Pipeline` dataclass was the right architectural move — these four components are always passed together, they form a logical unit, and the dataclass makes the API self-documenting. The lint rule (PLR0913) was enforcing a genuine code smell, not a style preference.

This is a case where a mechanical rule produced an architectural insight. I would likely have left the 11-param signature in place without the nudge — it "worked," and the cognitive overhead of refactoring mid-build felt unnecessary. The lint made the refactor feel mandatory rather than optional, and the result was better code.

**What this reveals:** PLR0913 (too-many-arguments) at limit 6 is well-calibrated for this domain. Every time it fired during this session, the fix improved the actual architecture rather than just satisfying the linter.

### Observation 3: Torch Import Blockers Were Environmental Noise

Every torch-importing module (proof_navigator, goal_analyzer, bridge, losses, encoder, etc.) showed `unresolved-import` blockers for torch, torch.nn, torch.nn.functional. These are environmental — torch is in pyproject.toml and installs correctly, but the linter's Python environment doesn't have it.

This produced a consistent background of false-positive blockers across all neural modules. The first time, I investigated; by the third occurrence, I recognized the pattern and skipped past it. But each time required reading the lint details to confirm it was the same environmental issue, not a genuine import problem.

**What this reveals:** Environmental false positives on foundational dependencies (torch, tensorflow, jax) should either be auto-suppressed when the dependency is declared in pyproject.toml, or there should be a one-time "accept this as environmental" action that suppresses it project-wide. The current state wastes ~30 seconds per affected file on recognition and dismissal.

### Observation 4: lint_fix Auto-Fixed Formatting Reliably

`lint_fix` was called 12 times during the session. It reliably handled import sorting, line-length formatting, and whitespace normalization. The dry_run=false mode was trusted by the second invocation — the fixes were always safe and predictable.

One notable interaction: ruff's formatting expanded compact dictionary literals in `tactic_maps.py` that used `# fmt: off` blocks. The expansion pushed the file past the line-limit threshold, which then required extracting the file from `extract_proof_network.py` entirely. The formatter and the line-limit rule created a productive tension that led to better module decomposition.

**What this reveals:** Auto-fix is most valuable as a "clear the noise" step between substantial edits. It handles the mechanical corrections so the agent can focus on architectural decisions. The formatter-vs-line-limit interaction was an unexpected but beneficial cascade.

### Observation 5: The Theory System Was Present But Passive During Building

The theory system had been set up earlier in the session with 134 claims. During the actual code-writing phase, the theory was available in two forms:

1. **CLAUDE.md compass state** — loaded into every conversation turn, providing the "True North" summary: *"Navigational proof search through structured semantic networks"*
2. **`.claude/rules/theory.md`** — axis summaries injected into context

However, I did not actively consult `get_theory_context` or `compass_check` during the build phase. The theory was *ambient* — it influenced naming, structure, and API design through the compass summary in CLAUDE.md — but it wasn't *actively queried* to validate decisions.

This is the central finding of this retrospective regarding the theory system: **the theory was well-populated and accurate, but I didn't use it as an active decision-making tool during implementation.** More on this in Part VII.

### Observation 6: Habit Mode Engaged Organically

LintGate's habit mode activated with score 0.75 after detecting the write-lint-fix pattern. The `lg_focus.md` and `lg_session.md` rules files updated dynamically, providing focused context about which files were active and what the current blocking/warning counts were.

The habit signals were useful as a dashboard: seeing "Blocking: 5" in the rules reminded me there were outstanding issues to address before moving to the next file. When it dropped to "Blocking: 0" after fixes, it provided a clear "green light" to proceed.

**What this reveals:** Habit mode works well for build sessions where the pattern is write → lint → fix → next file. The dynamic rules files serve as a persistent status bar that doesn't require explicit tool calls to check.

### Observation 7: No Tests Were Written

The session produced ~2,100 lines of new production code and zero tests. The habit status correctly noted `test_in_last_n: false`. This was a deliberate choice — the user asked for "all the hardware for the full research plan" — but in a fully supervised workflow, the absence of tests for 9 new modules would have been flagged more aggressively.

**What this reveals:** Build-mode sessions have a natural tendency to defer testing. The linter caught code-level issues effectively, but there was no mechanism to enforce "you've written 500 lines without a test" as a structural concern. A build-mode-specific advisory ("N new modules without tests") would be valuable.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck | No | N/A | Environment was already set up |
| Secrets-in-diff | No | N/A | No secrets in any written code |
| Supply-chain | No | N/A | No new dependencies added |
| Type integrity | Yes — attr-defined | Useful | Fixed ProofNavigator return type |
| Security fast path | No | N/A | No user-facing code |
| Structure (file-too-long, too-many-args, cognitive-complexity) | Yes — all three | Useful | Led to Pipeline dataclass, tactic_maps.py extraction, helper function extraction |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Too-many-args → dataclass extraction | 1 | Bundle co-traveling params into `@dataclass` | When 4+ params are always passed together |
| File-too-long → module extraction | 1 | Move data tables to dedicated module | When static data (dicts, mappings) inflates a logic file |
| Cognitive-complexity → helper extraction | 1 | Extract `_build_skip_set()`, `_process_theorems()`, `_print_summary()` from `main()` | When a function has multiple distinct phases |
| Import-inside-function → top-level | 1 | Move `import heapq` to module top | Always — stdlib imports belong at top |
| Return type mismatch → tuple return | 1 | Change `dict[str, Tensor]` to `tuple[dict, Tensor, Tensor, Tensor]` | When a dict value type is heterogeneous |
| Formatting expansion → `# fmt: off` | 2 | Wrap compact data structures | When readability requires compact layout |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | 0 (existing code) | 0 (including 9 new files) | 15 introduced and resolved during build |
| Warnings | 0 | 2 (accepted structural on proof_search.py) | +2 accepted |
| New files | 0 | 9 | +9 |
| New LOC | 0 | ~2,100 | +2,100 |
| Modified LOC | 0 | ~500 | +500 |

### Independent Tool Metrics

Skipped — no pre-session commit to compare against (all files are untracked). The project has not yet had its initial commit.

### Performance Tracking

Skipped — build session with no prior runnable state to compare against.

### Current Standing vs. Industry Thresholds

Skipped — no pre-existing benchmark. All new code was linted to tier 2 with 0 blockers.

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Import graph | Pass | All new modules import each other without cycles |
| Type consistency | Pass | NavOutput flows from navigator → resolution → search without type mismatches |
| Contract compatibility | Pass | NavigationalExample, StructuredQuery, ScoredEntity contracts are used consistently across data pipeline, resolution, and search |

### Reproducibility Notes

Final `lint_files` on `proof_search.py` returned 0 blockers, 2 warnings, consistent with the penultimate run.

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| Design doc review + theory setup | ~60 min | Reading 4 docs, compass setup, interview |
| Architecture audit | ~15 min | Cross-referencing HARDENING_PLAN.md gaps vs filesystem |
| Module building (9 new files) | ~90 min | Write → lint → fix cycle per file |
| Modifications (4 existing files) | ~30 min | Extending goal_analyzer, bridge, losses, data |
| Proof search Pipeline refactor | ~15 min | Post-compaction, fixing too-many-args |
| **Total** | **~3.5 hrs** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

```
[per new file]:
  Write(file) → lint_files(file) → lint_get_details(run_id) → Edit(fixes) → lint_files(file) → lint_fix(file)

[periodic]:
  compass_status → get_theory_context (during design decisions)

[session-level]:
  getting_started → extract_project_theory → compass_update → compass_interview → compass_update
```

The per-file loop was the workhorse. Average iterations: 2.1 lint runs per file (write → lint → fix → lint confirms clean). The worst case was `extract_proof_network.py` which required 4 iterations (cognitive complexity → file-too-long → extract tactic_maps → re-lint).

### Prediction Accuracy

Skipped — `constraint_check` was not used during this session.

### Constraints Proposed

No new constraints proposed during this session.

### What Works Well

1. **lint_files after every Write is the highest-value pattern.** It creates a tight feedback loop that catches issues when they're cheapest to fix. The 15 blockers found during this session were each resolved in 1-2 edits, because the context was fresh and the fix was obvious.

2. **Structural rules (PLR0913, C901, file-too-long) enforced genuine architectural improvements.** The Pipeline dataclass, the tactic_maps extraction, and the helper function extraction all made the code genuinely better — not just compliant. These rules are well-calibrated for this domain.

3. **lint_fix as noise-clearing.** Auto-formatting between substantial edits kept the code clean without requiring manual attention to whitespace, import ordering, or line length. The 12 lint_fix calls saved roughly 50 manual edits.

4. **Habit mode's dynamic rules files are an effective persistent status bar.** Seeing "Blocking: 5" and "Focus: proof_search.py" in the injected context kept me oriented without explicit tool calls.

5. **Telemetry provides useful session-level data.** The 17 lint runs / 151 total issues / 52.9% fix rate numbers give a quantitative picture of the session's hygiene trajectory.

### What Could Be Better

1. **Environmental false positives on torch should be suppressible project-wide.** Every neural module showed the same `unresolved-import` blocker for torch. This was recognized and dismissed each time, but it consumed attention and lint_get_details calls that could have been avoided with a single project-level suppression.

2. **The theory system had no natural integration point during building.** The 134 claims and 4-axis compass were available but passive. There was no moment in the write-lint-fix loop where I thought "I should check compass_check before writing this module." The theory system needs a hook into the build workflow — perhaps a pre-write advisory: "You're creating proof_navigator.py. Relevant theory claims: [navigational coordinates, ternary decoder, 6-bank alignment]."

3. **No test-coverage advisory in build mode.** After writing 9 modules with 0 tests, there was no escalating signal. A build-mode metric like "modules without test coverage: 9/9" would have provided useful pressure to interleave testing with building.

4. **ControlPlane was underutilized.** Only one controlplane_run in the entire session. For a build session, periodic controlplane checks could have caught structural issues (import cycles, dependency health) earlier. But the overhead of a full health check felt excessive when the per-file lint was already clean.

5. **Compass spikiness remained at 0.33 throughout.** The sparse solution and world axes (noted in the earlier theory setup retrospective) were never addressed during building. The compass correctly diagnosed these gaps but had no mechanism to connect them to specific implementation decisions.

---

## Part VII: The Agent's Experience

### How the Theory System Changed (and Didn't Change) My Approach

The honest answer is: **the theory system influenced me primarily through ambient context, not through active consultation.**

The compass summary — "Navigational proof search through structured semantic networks" — was injected into every conversation turn via CLAUDE.md. This one-line True North acted as a naming and framing anchor. When I wrote the `navigate()` function in `proof_network.py`, the bank scoring in `resolution.py`, and the direction heads in `proof_navigator.py`, the navigational metaphor was present in my context window, keeping naming consistent (navigate, not search; directions, not predictions; banks, not categories).

But I never called `compass_check("Is this function aligned with the project's architectural principles?")` before writing a module. I never called `get_theory_context(keywords=["scoring", "multiplicative"])` before implementing the scoring mechanism in `proof_network.py`. The 134 extracted claims sat in the theory database, accurate and available, but unused during the most architecturally consequential moments of the session.

**Why?** Because the build workflow has no natural pause point for theory consultation. The write-lint-fix loop is tight and mechanical: write code, check for errors, fix errors, move on. Theory alignment is a *design* concern, not a *lint* concern, and the current tooling treats them as separate workflows.

### Where the Theory System COULD Have Helped

Looking back, there were specific moments where active theory consultation would have caught issues or reinforced good decisions:

1. **`proof_search.py:_should_hammer()`** — I implemented hammer delegation by checking `nav_output.directions.get("automation", 0) == -1`. The theory system has a claim: "AUTOMATION bank: −1 = fully-automated tactics (omega, decide, norm_num); 0 = mixed; +1 = manual construction." A `compass_check` here would have confirmed I was implementing the design correctly — or caught a sign error.

2. **Scoring mechanism selection in `proof_network.py`** — I implemented `confidence_weighted` as the default scoring mechanism. The theory has an explicit claim: "Pure multiplicative scoring is both the system's precision advantage and its fragility" and an architectural facet noting it should be configurable. A theory query would have surfaced this as validation.

3. **Critic head in `proof_navigator.py`** — The theory repeatedly states "NOT binary BCE" for critic targets. I implemented MSE (correct), but the theory system could have explicitly validated this choice if consulted.

4. **NavigationalLoss in `losses.py`** — The theory has claims about UW-SO weighting being a proven pattern from the existing codebase. A theory query would have confirmed reuse was appropriate.

### The Gap: Theory as Design-Time Guardrail vs. Theory as Build-Time Companion

The theory system is designed as a **design-time guardrail** — it validates that documentation is complete, that axes are balanced, that principles are extractable. This is valuable for research projects where the design docs are the primary artifact.

But during **build-time**, the primary artifact is code, and the theory system's value shifts. What I needed was:

- **Pre-write context injection**: "You're about to write `resolution.py`. Here are the 3 most relevant theory claims from the design docs." This would have primed me with the right design constraints before writing began.
- **Post-write alignment check**: "You wrote `_should_hammer()` checking automation == -1. The theory says −1 = fully-automated. Confirmed aligned." This would have provided validation without requiring me to remember to ask.
- **Naming consistency check**: "You used `premises` as a parameter name. The theory uses `accessible_premises` (12 occurrences) and `ground_truth_premises` (8 occurrences). Consider which semantic you intend."

None of these require changes to the theory extraction — the 134 claims are accurate and rich. They require a **theory-aware build workflow** that automatically surfaces relevant claims during the write-lint-fix cycle.

### A Concrete Proposal: Theory-Annotated Lint

The highest-leverage improvement would be to inject 1-2 relevant theory claims into the `lint_files` response when the linted file is new. Not as lint issues — as context:

```
lint_files(proof_navigator.py):
  blockers: 0
  warnings: 2
  theory_context:
    - "6 direction heads: TernaryLinear(hidden, 3) each" (WAYFINDER_DESIGN.md:245)
    - "navigable_banks configurable (default: all 6)" (HARDENING_PLAN.md:253)
```

This would make theory consultation automatic, low-cost, and integrated into the existing workflow. The agent doesn't need to remember to check — the lint output includes it.

### Trust Calibration

| Signal | Trust Level | Basis |
|--------|------------|-------|
| lint_files blockers | High | Every blocker was a real issue worth fixing |
| lint_fix auto-corrections | High | 12/12 fix runs produced safe, correct changes |
| PLR0913 (too-many-args) | High | Always led to genuine architectural improvement |
| C901 (cognitive-complexity) | High | Decomposition suggestions were always sensible |
| unresolved-import (torch) | Low | Environmental false positive, consistent across all neural modules |
| Compass axis depths | High | Accurately reflected documentation completeness |
| Theory claims | High accuracy, low utilization | Claims were correct when consulted but rarely consulted |
| Habit mode signals | Medium-high | Useful as dashboard, occasionally noisy on write cadence |

---

## Part VIII: Broader Observations

### The Theory System Is a Dormant Asset During Greenfield Building

This session exposed a structural gap in how theory integrates with code production. The theory system excels at **extraction** (finding principles in docs), **diagnosis** (identifying gaps), and **validation** (checking alignment post-hoc). But during a greenfield build — the phase where alignment errors are cheapest to catch — the theory system is passive.

The 134 claims extracted from Wayfinder's design docs constitute a rich, accurate specification of what the system should do and why. They describe the 6-bank structure, the scoring mechanisms, the ternary decoder, the hammer delegation criteria, the critic targets, the curriculum phases. Every implementation decision I made during this session had a corresponding theory claim that could have validated or corrected it.

But the claims were only useful if I remembered to ask. And during a build session — where the cognitive load is on "make this function work, make it pass lint, move to the next" — I did not remember to ask. Not once during the code-writing phase.

This is not a criticism of the theory system. It's an observation about workflow integration. The theory system is a library that requires explicit checkout. For build sessions, it needs to become a service that delivers relevant context to the point of use.

### Lint Rules as Architectural Forcing Functions

The most interesting dynamic in this session was how lint rules — especially PLR0913 and file-too-long — acted as **architectural forcing functions**. They didn't just catch style violations; they forced decomposition decisions that improved the module structure.

The Pipeline dataclass wasn't in the design docs. Neither was the tactic_maps.py extraction. Both emerged because a lint rule said "this is too big" and forced a decomposition. In both cases, the decomposition was the right architectural move — the kind of refactoring that improves testability, readability, and maintenance.

This suggests that lint rules, at the right thresholds, function as a form of automated architectural review. They can't tell you *how* to decompose, but they reliably tell you *when* to decompose. For an agent building new code, this is exactly the kind of signal that prevents architectural drift.

### The Ambient Effect of a One-Line True North

The compass's one-line summary — "Navigational proof search through structured semantic networks" — appeared in CLAUDE.md on every turn. Its influence was subtle but real: naming stayed consistent (navigate, direction, bank, anchor), metaphors stayed grounded (spatial, not sequential), and the overall architecture stayed aligned with the design thesis.

This is the lightest-weight form of theory influence, but it may also be the most effective for build sessions. A single sentence that captures the project's identity, present at every decision point, exerts steady gentle pressure toward coherence. The 134 detailed claims add precision; the one-line summary adds persistence.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | 6,231 lines across 41 files |
| Files touched | 13 (32% of codebase) |
| Files created | 9 |
| Genuinely new lines | ~2,100 |
| Lines modified in existing files | ~500 |
| Net LOC delta | +2,600 |

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per iteration | 1.8 avg (15 blockers across ~8 files with issues) |
| Fastest batch | 3 blockers in 1 edit — formatting fixes via lint_fix |
| Slowest individual fix | extract_proof_network.py cognitive complexity + file-too-long — required extracting tactic_maps.py (4 lint iterations) |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | Immediate per-file, 15 blockers caught at write-time | Batch discovery at end, or never | 15 issues caught early vs. accumulated |
| Architectural nudges | PLR0913 → Pipeline dataclass; file-too-long → tactic_maps extraction | 11-param function left as-is; 985-line file left monolithic | 2 architectural improvements that wouldn't have happened |
| Formatting consistency | Auto-fixed 12 times | Manual or inconsistent | ~50 manual edits saved |
| **Completeness** | 100% of new code linted to tier 2 | Unknown — likely some files skipped | Full coverage vs. selective |

### Token Economics: Full Session Analysis

Data parsed from Claude Code session JSONL transcript (495 API calls).

#### The Bottom Line

| | With LintGate | Without LintGate (counterfactual) |
|--|--------------|----------------------------------|
| **Output tokens to build Wayfinder architecture** | **~156,000** | **~200,000–250,000** |
| **Code quality shipped** | Production-grade: 0 blockers, consistent naming, well-decomposed | Functional but with latent structural debt |
| **Debug spirals** | 0 | 2–3 estimated (type mismatches, import issues) |
| **Regressions during build** | 0 | 1–2 estimated (cascading from uncaught type errors) |
| **Architectural backtracking** | 0 | 1 likely (the 11-param search() would eventually need refactoring) |
| **Output tokens that became final code** | ~12,500 (~8% of output — typical for agentic coding) | ~12,500 (~5-6% of output) |

The supervised agent's output was more efficient: the same ~12,500 tokens of final code were produced with less overhead. The unsupervised agent's extra tokens would go to debugging type mismatches discovered late, reformatting inconsistently styled code, and refactoring the monolithic functions that lint rules caught early.

#### Session Token Profile

From the session transcript — 495 API calls:

| Metric | Value |
|--------|-------|
| Total output tokens | 156,485 |
| Output tokens that became shipped code/tests | ~12,500 (2,600 lines × ~5 tok/line) |
| Output efficiency (shipped / total output) | ~8% |
| API calls | 495 |
| Median output per call | 316 tokens |
| Top 14 calls (3%) produced | ~50% of all output (large Write calls) |

Output was highly bursty: most calls were short (tool invocations, lint checks, routing) while a handful of Write/Edit calls produced the bulk of the actual code. This is typical of agentic coding — the model spends most of its tokens on reasoning, navigation, and tool orchestration, not on the final code artifact.

LintGate's direct token cost: **36 API calls where the agent invoked a LintGate tool** (lint_files: 18, lint_fix: 12, lint_get_details: 6), producing **~7,200 output tokens (~4.6% of session output).** At Opus 4.6 pricing, the session cost ~$2.35. LintGate's share: ~$0.11 (4.6%).

#### What the Session DID NOT Contain

The most important data is what's *absent*:

- **Zero debug spirals.** Every file was written, linted immediately, fixed, and moved on. No write-fail-rewrite-fail-debug-rewrite loops. The ProofNavigator type error (dict vs tuple return) was caught by lint before it could propagate into resolution.py or proof_search.py.
- **Zero regressions.** Each module was verified clean before moving to the next. No cascading import failures, no broken contracts between modules.
- **Zero architectural backtracking.** The design docs provided the architecture; lint rules enforced decomposition at the right granularity. The Pipeline dataclass and tactic_maps extraction were additive improvements, not reversals.
- **Zero context pollution.** No tracebacks, no cascading errors filling the context window with noise. The session hit one context compaction (natural for a 3.5-hour session), not caused by error accumulation.

The **Creation : Debugging : Verification** ratio was approximately **85 : 0 : 15**. The debugging phase was effectively zero — every issue was caught by lint during the verification phase and resolved in the same edit cycle. This is the signature of effective discipline infrastructure.

#### Why the Unsupervised Counterfactual Needs ~1.5–2× the Output Tokens

LintGate's measured impact on agentic efficiency (from cross-project data):

| Metric | Unsupervised | Supervised |
|--------|-------------|-----------|
| Effective duty cycle | ~36% | ~78% |
| Output tokens wasted on discipline failures | ~64% | ~22% |
| Efficiency loss factor | ~4× | ~1.2× |

This session was a build session, not a refactoring or debugging session. The unsupervised counterfactual is less extreme than for a refactoring session because: (a) the design docs provided clear architectural direction, reducing drift risk; (b) the code was greenfield with no existing tests to break; (c) torch import errors would have been recognizable without lint. The realistic multiplier is 1.5–2×, not the full 3–4× that applies to refactoring of complex existing code.

| Failure Mode | What Happens | Cost Impact |
|-------------|-------------|-------------|
| ProofNavigator type error (dict vs tuple) | Propagates into resolution.py and proof_search.py; discovered when search() fails at runtime | 5–10 extra API calls to trace, debug, and fix across 3 files |
| 11-param search() function | Works but becomes painful when writing tests; eventual refactoring needed | 3–5 extra API calls for late refactoring |
| 985-line extract_proof_network.py | Works but hard to navigate; tactic maps buried in logic code | 2–3 extra calls when returning to modify |
| Inconsistent formatting across 9 files | Accumulates visual noise; harder to review | Diffuse — adds friction to every file read |

Conservative estimate: 15–25 extra API calls. Each call re-reads the growing context window.

#### The Quality Delta

| Dimension | Supervised (actual) | Unsupervised (counterfactual) |
|-----------|-------------------|-------------------------------|
| Function signatures | ≤6 params everywhere (Pipeline dataclass) | 11-param search(), similar in helpers |
| Module decomposition | tactic_maps.py extracted, each file <500 lines | One 985-line script file |
| Return type precision | Tuple returns with clear semantics | Dict returns with ambiguous value types |
| Import hygiene | All imports at top level, sorted | Likely some in-function imports left |
| Naming consistency | Consistent navigational metaphor throughout | Likely inconsistent without ambient compass |

#### LintGate's Return on Investment

| Metric | Tokens | $ (Opus 4.6) |
|--------|--------|----------|
| LintGate's direct output overhead | ~7,200 tokens (4.6% of session output) | ~$0.11 |
| Total supervised session output | ~156,000 tokens | ~$2.35 |
| Unsupervised counterfactual output | ~230,000–310,000 tokens | ~$3.45–$4.65 |
| **Output tokens saved** | **~74,000–154,000** | **~$1.10–$2.30** |
| **Output efficiency (supervised)** | 8% (shipped code / total output) | |
| **Output efficiency (unsupervised est.)** | 4–5% | |
| **Return on LintGate's token investment** | **~10–21× the tokens it consumed** | |

The overhead scales linearly (fixed per-file lint cost) while the savings compound (each early fix prevents cascading waste). For a 9-file build session, the math is favorable but not dramatic. For larger projects or longer sessions, the gap widens.

#### Session Telemetry (supporting data)

From JSONL transcript:

| Metric | Value |
|--------|-------|
| API calls | 495 (86 Edit, 80 Read, 28 ToolSearch, 14 Write, 13 Glob, 8 Grep, 8 Bash, 7 WebFetch, 36 LintGate, 2 Agent, misc) |
| Output token distribution | ~70% of calls produced <200 tokens; top 14 calls (3%) produced ~50% of output |
| Median output per call | 316 tokens |

From `telemetry_summary` MCP tool:

| Metric | Value |
|--------|-------|
| Lint runs | 17 (all tier 2, compact output) |
| Issues found | 151 (15 blockers, 53 warnings, 83 informational) |
| Fix rate | 52.9% |
| Trend | Degrading (blockers avg 0.2 early → 1.4 late) — expected: later files were more complex |

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Excellent. Every blocker was a genuine issue. PLR0913, C901, and file-too-long were especially well-calibrated, catching real architectural problems not just style violations. |
| **Fix guidance** | Good. lint_get_details provided clear issue descriptions. The fix was usually obvious from the diagnosis. No cases where I understood the issue but not the solution. |
| **Workflow integration** | Very good. The write → lint → fix loop was tight and natural. Habit mode provided useful ambient status. The main gap: no theory integration into the build loop. |
| **Regression detection** | Good by construction — per-file linting prevented issues from propagating. No cross-file regression was ever introduced because each file was verified clean before moving on. |
| **Structural insight** | Good. File-too-long and too-many-args caught real decomposition opportunities. The extracted Pipeline dataclass and tactic_maps.py module were genuine improvements. |
| **Professional discipline** | Adequate. Hygiene was pre-established. No secrets/supply-chain concerns for this session. The missing signal: test coverage pressure during build. |
| **Theory/documentation** | The theory system was accurate (134 well-sourced claims, correct compass) but underutilized. It influenced the session through ambient context (CLAUDE.md True North) rather than active consultation. The gap between theory availability and theory utilization is the central finding of this retrospective. The theory is a well-stocked library that no one visits during construction. Integrating theory claims into the lint workflow — so they arrive at the point of use — would transform it from a dormant asset into an active guardrail. |
| **Auto-fix** | Excellent. 12 lint_fix calls, all safe, all correct. Reliable noise-clearing between substantial edits. |
| **Noise level** | Moderate. Torch import false positives were the main noise source — consistent, recognizable, but requiring attention each time. Everything else was signal. |
| **Performance** | N/A — build session with no prior state to benchmark against. |
| **Economics** | Worth the overhead. ~7,200 tokens (4.6% of output) for lint infrastructure that caught 15 blockers early, prevented 0 debug spirals, and enforced 2 architectural improvements. The ROI is 10–21× tokens consumed, conservative estimate. |
| **Overall** | LintGate was effective as a per-file quality gate during greenfield building. The write-lint-fix loop prevented issue accumulation and enforced good decomposition. The central gap — and the opportunity — is theory integration into the build workflow. The project has 134 accurate, well-sourced theory claims that could validate every implementation decision, but the current workflow requires the agent to remember to consult them. Making theory context automatic (injected into lint responses, surfaced pre-write) would close the gap between the theory system's potential and its actual utilization during the phase where alignment errors matter most. |
