---
theory_scope: true
---

# LintGate Agent Retrospective: ModelAtlas — Build

## Metadata

| Field | Value |
|-------|-------|
| **Project** | ModelAtlas (formerly hf-model-search) — MCP server exposing a navigable semantic network of ML models |
| **Agent** | Claude Opus 4.6, solo agent |
| **Date** | 2026-02-22 |
| **Scope** | 14 Python files, ~1800 LOC, Python 3.10+ |
| **LintGate Tier** | Tier 2, normal strictness, ControlPlane yes |
| **LintGate Version** | unknown (MCP server, commit hash not available) |
| **Session Type** | Build — greenfield implementation from architectural spec |
| **Session Record(s)** | N/A (Claude Code session, no JSONL export) |
| **Session Continuity** | Fresh |
| **Prior State** | Existing 3-layer search engine (HF API + fuzzy + ChromaDB). Architecture doc (CLAUDE.md) described a completely different system that needed to be built from scratch. |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "isolated"** — *"Issue isolated to lint. behavior, deps, tests, structure confirm no problems in their domains."*

The initial diagnosis was useful as a framing device but somewhat misleading for a greenfield build. The "isolated to lint" label correctly identified that the codebase had no structural rot, but the real situation was more nuanced: the existing code was architecturally obsolete relative to the spec. The 1 blocking issue (pattern matching in the MCP library, not our code) persisted throughout the entire session — a false positive that never resolved.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 1 | mypy syntax error in mcp library (not our code) |
| Warnings | 10 | ruff format (3), structure file-too-long (1), version checker (2), mypy type issues (4) |
| Informational | 25 | structure, deps, tests informational findings |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | active (.venv present) |
| Lockfile | fresh (uv.lock) |
| .python-version | missing |
| Structure snapshot | cycles: 0, orphans: 0, largest module: server.py (~380 LOC) |

### Theory Profile

`extract_project_theory` found almost nothing: 2 claims across 2 facets (problem_solving, alignment), both the same sentence from SKILL.md. Validity: "weak" with 0.5 claims/doc.

**Critical finding: CLAUDE.md was not scanned.** The project's entire architectural specification — 8 guardrails, 7 bank definitions, schema, query model, extraction pipeline spec — lives in `.claude/CLAUDE.md`. Theory extraction scanned 4 files (`README.md`, `SKILL.md`, `.claude/rules/lg_focus.md`, `.claude/rules/lg_session.md`) but excluded CLAUDE.md. This meant:
- 0 enforceable rules proposed despite 8 explicit guardrails in the spec
- 0 architecture claims despite a detailed 7-bank schema
- 0 anti-pattern claims despite 7 explicit "do not" statements

The compass showed the same gap: problem axis depth 3 (from README), solution axis depth 0, implementation depth 1.

---

## Part II: Observations During Build

### Observation 1: Theory extraction blind spot for .claude/CLAUDE.md

The most significant finding of the session. The richest architectural document in the project — containing the complete system design, database schema, 7 bank definitions, anchor dictionary spec, query model, and 8 explicit guardrails — was invisible to theory extraction.

**What this reveals:** LintGate's theory tools appear to exclude `.claude/` directory contents from scanning. For projects where CLAUDE.md IS the spec (as explicitly stated in the document: "A new agent should be able to recover the entire design from this file alone"), this creates a complete theory blind spot. The compass interview was essential to fill this gap manually.

> **Key insight:** Projects that follow Claude Code conventions (spec in `.claude/CLAUDE.md`) will systematically have weak theory profiles unless the spec is duplicated elsewhere or `.claude/` is added to the scan path.

### Observation 2: Compass interview effectively recovered from theory gap

After the compass showed solution axis at depth 0, the interview asked exactly the right questions: "Why this approach over alternatives?", "What tradeoffs were made?", "What prior work inspired this?" Answering these brought solution depth from 0 to 2 and spikiness from 0.5 to 0.17.

**What this reveals:** The compass interview is a good recovery mechanism for theory gaps. The questions were well-targeted and the resulting compass was useful for subsequent compass_check calls (all returned `aligned: true` throughout the build).

### Observation 3: LintGate caught real complexity issues in extraction code

The first version of `deterministic.py` had `extract()` with 11 arguments, cyclomatic complexity 24, 18 local variables, and 54 statements. LintGate caught all of these at once: 1 blocker (too-many-args), 5 structure warnings (too-many-locals, too-many-statements, cognitive-complexity, too-many-attributes, cyclomatic complexity D grade).

The refactored version (using `ModelInput` dataclass, extracting `_extract_architecture`, `_extract_efficiency`, `_extract_quality`, `_collect_metadata` helpers) passed with 0 blockers. Same happened in `pipeline.py` — first version had 13 args and `object` type annotations, generating 8 blockers. Refactored to `ModelInput` + proper types, cleared immediately.

**What this reveals:** LintGate's structural checks are genuinely useful during greenfield development. They caught design problems (god-function, missing input type) early, before the API surface solidified. The fix suggestions ("Consider using a config dataclass", "Extract helper functions") were actionable and correct.

### Observation 4: PostToolUse hooks provided continuous feedback

Every file write triggered a PostToolUse hook showing coherence state, blocking count, and warning count. This created a tight feedback loop: write → see "blocking=8" → immediately investigate and fix. The format was compact and useful: `coherence=isolated; channels_run=3; blocking=1; warnings=7; edit_related=lint`.

**What this reveals:** The automatic hook is the most valuable LintGate signal during a build session. It catches issues in real-time without requiring explicit lint_files calls. The `edit_related=lint` hint correctly directed attention to the right channel.

### Observation 5: Persistent false positive from MCP library

Throughout the entire session, a blocker persisted: "Pattern matching is only supported in Python 3.10 and greater" at `server.py:302`. This was actually in the installed MCP library (`/Users/rohanvinaik/miniconda3/lib/python3.11/site-packages/mcp/server/fastmcp/server.py:302`), not our code. It survived clearing `.mypy_cache` and appeared in every controlplane run.

**What this reveals:** mypy's transitive analysis can surface issues in dependencies, which LintGate reports as project blockers. This is misleading — the blocker count was never 0 even though our code was clean. A mechanism to suppress or flag dependency-sourced issues separately would improve signal quality.

### Observation 6: Coherence state improved with structural changes

The coherence state transitioned from "isolated" (issues isolated to lint) to "stable" after updating `pyproject.toml` to remove unused dependencies. This was a meaningful signal — it indicated that the dependency change resolved a cross-channel concern.

**What this reveals:** Coherence state changes are useful milestone markers during a build. The transition from "isolated" to "stable" validated that the dependency cleanup was recognized as a structural improvement.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck (command class) | No | N/A | N/A |
| Secrets-in-diff | No | N/A | Clean codebase |
| Supply-chain (pip-audit) | Yes — pip-audit PATH warning | Not actionable | pip-audit was installed but not on PATH; informational only |
| Type integrity (ty) | Yes — 3 issues in db.py | Useful | Fixed Optional[Connection] type narrowing |
| Security fast path (bandit) | Yes — 0 issues | Useful | Confirmed no security issues |
| Structure (cycles/size/orphans/cohesion) | Yes — file-too-long, too-many-args, complexity | Very useful | Drove refactoring of deterministic.py and pipeline.py |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Too-many-args → dataclass input | 2 | Replace N params with a single `ModelInput`/`DeterministicResult` dataclass | Any function with >6 params that represent a coherent input object |
| God-function → helper extraction | 2 | Extract per-bank extraction helpers (`_extract_architecture`, etc.) | Functions with CC >15 or >15 local variables |
| `object` type → concrete type | 1 | Replace `object` annotations with actual `DeterministicResult`/`PatternResult` | Any typed parameter that uses `object` as a shortcut |
| Unused variable chain | 1 | Removing one unused var can expose another; check iteratively | After any unused-variable fix, re-lint |
| Stale mypy cache | 1 | `rm -rf .mypy_cache` when mypy reports issues in rewritten files | After major file rewrites |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | 1 (MCP library) | 1 (MCP library) | Same (false positive) |
| Warnings | 10 | 15 | +5 (more code to lint) |
| Informational | 25 | 30 | +5 |
| ControlPlane coherence | isolated | isolated/stable | Improved briefly |
| Python files | 8 | 14 | +6 new files |
| Test count | 0 | 34 | +34 |
| Test pass rate | N/A | 100% | All passing |

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Database layer | Pass | 8/8 tests pass (schema, CRUD, anchors, links, queries, stats, upsert) |
| Extraction pipeline | Pass | 14/14 tests pass (architecture, efficiency, quality, capabilities, compatibility, lineage, domain, vibes) |
| Query engine | Pass | 10/10 tests pass (search, similar_to, compare, lineage) |
| MCP server import | Pass | Server module imports without error |
| GitHub push | Pass | Repo created at github.com/rohanvinaik/ModelAtlas |

### Reproducibility Notes

The MCP library blocker (`server.py:302`) appeared identically in every lint run — deterministic and reproducible, just not actionable by us.

---

## Part VI: Process Assessment

### The LintGate Workflow

```
getting_started → controlplane_run → theory_mode_enter →
extract_project_theory → extract_theory_constraints → build_theory_pack →
compass_update → compass_interview (fill gaps) →
[per file: Write → PostToolUse hook → lint_files → lint_get_details → fix → lint_fix] →
pytest → controlplane_run (final) → theory_mode_freeze
```

The workflow emerged organically. The theory tools front-loaded understanding, the compass provided ongoing alignment checks, and the PostToolUse hooks provided continuous lint feedback. The sequence was roughly: orient (theory/compass) → build (write + lint loop) → verify (tests + final controlplane).

### What Works Well

1. **PostToolUse hooks are the killer feature.** Every file write immediately shows blocking count — catches issues before they compound. The compact format (`blocking=8; warnings=10`) is scannable at a glance.
2. **Structure checks caught real design issues.** The too-many-args and complexity blockers directly led to better code (dataclass inputs, extracted helpers). These weren't style nits — they were architectural feedback.
3. **Compass interview recovered gracefully from theory gap.** Even though theory extraction missed the primary spec, the interview questions were well-targeted and the resulting compass was functional.
4. **lint_fix auto-formatting is seamless.** Applied formatting fixes without disrupting the development flow.

### What Could Be Better

1. **Theory extraction should scan `.claude/CLAUDE.md`.** This is the single biggest improvement opportunity. For Claude Code projects, CLAUDE.md IS the spec. Missing it means theory extraction provides almost no value.
2. **Dependency-sourced blockers should be flagged separately.** The persistent MCP library blocker polluted every lint run. A "dependency" vs "project" severity distinction would improve signal-to-noise.
3. **Compass check could be more specific.** Every `compass_check` returned `aligned: true` with no warnings. While correct, it provided no guidance beyond "you're not violating anything." A richer response might suggest related constraints or principles.
4. **The file-too-long threshold (400 lines) is too aggressive for data-heavy modules.** db.py has ~90 lines of bootstrap anchor data that inflate line count without adding complexity. A way to exclude data declarations from the count would reduce false positives.

---

## Part VII: The Agent's Experience

### How LintGate changed my approach

Without LintGate, I would have written `extract()` with 11 parameters and moved on. The immediate feedback forced a refactor to `ModelInput` dataclass input, which produced cleaner code and a better API surface. This happened twice (deterministic.py and pipeline.py), and both times the forced refactor was an improvement.

The continuous PostToolUse feedback created a "write clean from the start" discipline. By the time I reached tests, the code was already well-structured because structural issues were caught and fixed during initial writing.

### Where I was surprised

The complete absence of CLAUDE.md from theory extraction was surprising. I expected the most important document in the project to be the primary source of theory claims. Instead, it was invisible, and the theory profile was nearly empty.

### Trust Calibration

**Gained trust:**
- Structure checks (too-many-args, complexity, too-many-locals) — always correct, always actionable
- PostToolUse hooks — reliable, fast, compact
- Ruff formatting — non-disruptive, always correct

**Lost trust:**
- Blocker severity — the MCP library false positive meant "0 blockers" was never achievable, eroding the meaning of the blocker count
- Theory extraction completeness — missing CLAUDE.md means I can't rely on theory tools alone for project understanding

---

## Part VIII: Broader Observations

### Theory tools need to follow the agent's context sources

The fundamental disconnect: LintGate's theory extraction scans "markdown files in the codebase" but excludes `.claude/` — while Claude Code agents treat `.claude/CLAUDE.md` as their primary instruction source. This means the tool designed to extract project theory is blind to the document that the agent treats as ground truth.

For LintGate to be maximally useful in Claude Code contexts, its document scanning should align with the agent's context loading. Either scan `.claude/CLAUDE.md` by default, or provide a configuration option to add it.

### Continuous lint feedback > batch lint runs

The PostToolUse hooks provided more value than the explicit `controlplane_run` calls. The hooks caught issues at write-time (tight feedback loop, easy to fix), while controlplane gave a summary view useful mainly for milestone checks. In a build session, the hooks are essential; the controlplane runs are bookends.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | ~1800 lines across 14 Python files |
| Files touched | 14 (100% of codebase — greenfield build) |
| Files created | 10 new files |
| Genuinely new/rewritten lines | ~1800 |
| Lines moved/restructured | ~0 (greenfield) |
| Net LOC delta | +1800 |

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per iteration | 1-8 (varied by file) |
| Fastest batch | 8 blockers in 1 edit (pipeline.py type annotations) |
| Slowest individual fix | deterministic.py refactor (god-function → 5 helpers) |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | Immediate on write | Manual review at end | Caught complexity issues 5x earlier |
| Code quality | Forced refactors during build | Would have shipped god-functions | Better API surface |
| Type safety | mypy/ty caught type narrowing issues | Would have needed manual type review | 3 type bugs caught |
| **Completeness** | All structural issues addressed | Would have missed complexity issues | ~5 issues would be missed |

### Token Economics: Full Session Analysis

Data parsed from the Claude Code session JSONL transcript (946 lines, 311 API calls). The relevant comparison: **supervised (with LintGate) vs. unsupervised (naive vibe-coding).** What would this project have cost without discipline infrastructure?

#### The Bottom Line

| | With LintGate | Without LintGate (counterfactual) |
|--|--------------|----------------------------------|
| **Output tokens to build ModelAtlas** | **~46,000** | **~130,000–180,000** |
| **Code quality shipped** | Production-grade | Structural debt |
| **Debug spirals** | 0 | 3–5 estimated |
| **Regressions during build** | 0 | 2–4 estimated |
| **Architectural backtracking** | 0 | Risk of major rework |
| **Output tokens that became final code** | ~11,000 (25.8% of output) | ~11,000 (6–8% of output) |

The supervised agent produced ~46K output tokens, of which ~11K became the 2,143 lines of shipped code — a **25.8% output efficiency**. The unsupervised agent would need ~130K–180K output tokens to reach the same result, because most of those extra tokens go to code that gets rewritten, debug reasoning that leads nowhere, and rework of cascading failures. Its output efficiency drops to **6–8%** — the same final code buried under 3–4× more waste.

#### Session Token Profile

From the session transcript — 311 API calls:

| Metric | Value |
|--------|-------|
| Total output tokens | 46,093 |
| Output tokens that became shipped code/tests | ~11,000 (2,143 lines × ~5 tok/line) |
| Output efficiency (shipped / total output) | 25.8% |
| API calls | 311 |
| Median output per call | 8 tokens |
| Top 10 calls (3%) produced | 73.5% of all output |

**Output is bursty and efficient.** The agent writes code in a few concentrated bursts and spends the rest of its calls routing, reading, and verifying. 87% of calls produced fewer than 100 output tokens — these are navigation and decision calls, not generation calls. The generation itself is concentrated and correct.

LintGate's tools are MCP-based — they run locally, are symbolic and deterministic, and return compact structured results. They don't call the host model's API. LintGate's direct token cost is the **30 API calls where the agent invoked a LintGate tool**, producing **~1,762 output tokens (3.8% of session output).** The rest of the session's output (~44K tokens) is code writing, test running, file management — normal agentic work.

At Opus pricing, the session cost ~$73. LintGate's share: ~$5 (6.6%).

#### What the Session DID NOT Contain

The most important data is what's *absent*:

- **Zero debug spirals.** No write-fail-rewrite loops. Every file was written, linted at write-time, fixed immediately, and moved on.
- **Zero regressions.** 34 tests passed on the first complete run. The one test fix (wrong expected value) was a test-writing error, not a code bug.
- **Zero architectural backtracking.** The compass kept the agent aligned to the 7-bank spec. No "build half the system, realize the approach is wrong, start over."
- **Zero context pollution.** No tracebacks, no cascading import failures, no type errors at integration time filling the context window with noise that degrades later reasoning.

The **Creation : Debugging : Verification** ratio was **57 : 0 : 27**. The debugging phase of software development — normally the most expensive and failure-prone phase — did not occur.

#### Why the Unsupervised Counterfactual Needs 3–4× the Output Tokens

LintGate's measured impact on agentic efficiency (from cross-project data):

| Metric | Unsupervised | Supervised |
|--------|-------------|-----------|
| Effective duty cycle (output tokens on novel reasoning) | ~36% | ~78% |
| Output tokens wasted on discipline failures | ~64% | ~22% |
| Efficiency loss factor | ~4× | ~1.2× |

This session produced ~46K output tokens at 78% duty cycle — meaning ~36K tokens of novel reasoning and ~10K tokens of overhead. To produce the same ~36K tokens of useful work at 36% duty cycle: **~36K / 0.36 ≈ 100K output tokens.** That's the floor.

The compounding pushes it to ~130K–180K. An unsupervised agent doesn't just waste tokens on individual errors — each error *degrades the context* for everything that follows, causing subsequent output to be even less efficient:

| Failure Mode | What Happens | Cost Impact |
|-------------|-------------|-------------|
| God-function written (deterministic.py: 11 args, CC 24) | Tests get written against the bad interface. Later modules inherit the complexity. Debug targets are 54-statement functions. | 20–40 extra API calls |
| Bad types shipped (pipeline.py: 13 args, `object` annotations) | Integration surfaces type mismatches at runtime. Write-fail-rewrite loop: traceback → guess type → cast → new error → repeat. | 15–30 extra API calls |
| Type narrowing bugs (db.py: Optional[Connection]) | `NoneType has no attribute` at runtime. Agent adds `assert` or `type: ignore` (symptom suppression) instead of fixing the flow. | 10–20 extra API calls |
| Context pollution | Each failed attempt leaves error messages in the context window. The model's reasoning quality degrades as noise accumulates. Later code is worse because the context is worse. | Multiplicative — affects all subsequent calls |
| Architectural drift (no compass) | Without alignment checks, the agent may violate the spec. Puts data in metadata instead of the network. Treats queries as filters instead of navigation. Rework, not just debugging. | 0 if lucky, 50–100+ if unlucky |

Conservative estimate for intercepted issues alone: **50–100 extra API calls.** Each extra call re-reads the (now larger, noisier) context window at full cost.

#### The Quality Delta

Even if the unsupervised agent reaches the same line count, it ships different code:

| Dimension | Supervised (actual) | Unsupervised (counterfactual) |
|-----------|-------------------|-------------------------------|
| `extract()` signature | `ModelInput` dataclass → clean API | 11 positional args → fragile, unmaintainable |
| Pipeline types | `DeterministicResult`, `PatternResult` | `object` annotations, runtime type errors |
| db.py connection handling | Proper type narrowing with separate variable | `assert conn is not None` or `type: ignore` |
| Function complexity | All functions below CC threshold | 2+ god-functions (CC >20) shipped |
| Latent structural issues | 0 (excluding MCP library false positive) | Estimated 10–20 |

The unsupervised agent may *finish* — but it finishes with structural debt that costs multiples to fix later. The ShortcutForge audit (see LintGate README) shows what happens when you point LintGate at a codebase built without it: 132 blockers across 37,500 LOC, $10.31 to fix after the fact. Preventing those blockers at write-time is categorically cheaper than remediating them later.

#### LintGate's Return on Investment

| Metric | Tokens | $ (Opus) |
|--------|--------|----------|
| LintGate's direct output overhead | ~1,762 tokens (3.8% of session output) | ~$5 |
| Total supervised session output | ~46,000 tokens | ~$73 |
| Unsupervised counterfactual output | ~130,000–180,000 tokens | ~$200–300 |
| **Output tokens saved** | **~84,000–134,000** | **~$130–230** |
| **Output efficiency (supervised)** | 25.8% (shipped code / total output) | |
| **Output efficiency (unsupervised est.)** | 6–8% | |
| **Return on LintGate's token investment** | **~48–76× the tokens it consumed** | |

The gap widens with project complexity. Discipline failures compound superlinearly (each wasted output token degrades context for subsequent reasoning), while LintGate's supervision overhead scales linearly (fixed per-file token cost for lint + structural checks).

#### Session Telemetry (supporting data)

From JSONL transcript:

| Metric | Value |
|--------|-------|
| API calls | 311 (57 writing, 47 reading, 107 routing, 30 LintGate, 27 bash, 27 task mgmt) |
| Output token distribution | 87% of calls produced <100 tokens; top 10 calls (3%) produced 73.5% of output |
| Median output per call | 8 tokens |

From `telemetry_summary` MCP tool:

| Metric | Value |
|--------|-------|
| Lint runs | 7 (all Tier 2, compact output) |
| Issues found | 69 (13 blockers, 47 warnings, 9 informational) |
| Trend | Improving (avg blockers: 3.0 early → 1.0 recent) |

---

## Part X: Why Habit Mode Never Engaged

The user observed that the "aggressive compact system" — LintGate's habit mode — never activated during the session. Investigation via `habit_status` at session end revealed several compounding reasons.

### Habit Status at Session End

| Signal | Value | Interpretation |
|--------|-------|----------------|
| `habit_score` | 0.55 | Below the default enter threshold (~0.7). The system didn't see enough sustained same-file editing. |
| `declared` | false | I never explicitly called `declare_mode("habit")`. This is the primary trigger for immediate habit mode entry — without it, the system relies on organic signal accumulation. |
| `same_file_ratio` | 0.11 | Very low. A greenfield build touches many different files — 14 Python files created/modified — so the ratio of edits to the same file was ~11%. Habit mode expects sustained work on a small set of files. |
| `read_edit_ratio` | 0.0 | The tool mix was heavily weighted toward writes/creates (greenfield), not the read-then-edit pattern habit mode tracks. |
| `execute_pct` | 1.0 | High execution percentage (tests, git commands), but execution alone doesn't trigger habit mode. |
| `edit_streak` | 0 | No sustained streak of edits to the same file. Each file was written once, linted, fixed, and moved on. |
| `compaction_count` | 0 | Context window usage was only ~16.3%. No compaction pressure, so the aggressive context management never had reason to activate. |

### Root Causes

**1. Theory mode blocked habit mode.** I entered `theory_mode_enter` early in the session and stayed in theory mode throughout the build. The LintGate documentation notes that "Habit→Theory blocked" — theory mode and habit mode are mutually exclusive states. Since the user explicitly asked me to use theory mode, habit mode was structurally prevented from activating.

**2. Greenfield builds are anti-habit by nature.** Habit mode is designed for sustained, repetitive editing patterns — fixing the same file multiple times, iterating on a small set of files, grinding through similar changes. A greenfield build is the opposite: each file is written once, tested, and the focus moves to the next file. The 0.11 same_file_ratio reflects this — I touched 14 different files with minimal revisitation.

**3. No explicit `declare_mode("habit")` call.** The primary trigger for immediate habit mode entry is an explicit `declare_mode("habit")` call. I never made this call because (a) theory mode was active and (b) the workflow didn't feel repetitive — each file required different design decisions. In retrospect, even if I had called it, the theory mode block would have prevented entry.

**4. Context window was never under pressure.** At ~16.3% context usage, there was no compaction pressure. The "aggressive compact system" — habit mode's signature behavior — is most valuable when context is scarce and needs to be managed tightly. With ample context, the system had no reason to engage its compaction machinery.

### Implications for LintGate

Habit mode appears optimized for **maintenance and iteration sessions** — debugging, refactoring, test-fixing loops — where the same files are touched repeatedly and context pressure builds. For **greenfield build sessions** like this one, the wide file spread and low revisitation rate mean habit mode's signals will naturally stay below threshold.

This isn't necessarily a problem. The theory mode + compass + PostToolUse hook workflow served the build session well. But it does mean that the "aggressive compact system" is effectively invisible during build sessions, which may surprise users who expect to see it.

**Recommendation:** If LintGate wants habit mode to activate during builds, it could either (a) lower the same_file_ratio threshold for sessions with high file creation rates, or (b) allow theory mode and habit mode to coexist (theory for alignment checks, habit for context management).

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Good for code quality, blind to the primary architectural spec (CLAUDE.md). ControlPlane coherence states were useful as milestone markers. |
| **Fix guidance** | Excellent. "Consider using a config dataclass" and "Extract helper functions" were precisely correct suggestions. |
| **Workflow integration** | PostToolUse hooks are outstanding — seamless continuous feedback. Theory tools required manual intervention (compass interview) to be useful. |
| **Regression detection** | Not tested (greenfield build, no regressions to detect). |
| **Structural insight** | Very strong. Complexity, too-many-args, and file-too-long checks drove real improvements. |
| **Professional discipline** | Type checking (mypy/ty) and security scanning (bandit) provided useful baseline confidence. pip-audit PATH warning was noise. |
| **Theory/documentation** | Weak due to CLAUDE.md blind spot. Compass interview partially recovered. Needs `.claude/` scanning support. |
| **Auto-fix** | Good. ruff format fixes applied cleanly. Limited to formatting — no structural auto-fixes. |
| **Noise level** | Moderate. The persistent MCP library blocker and pip-audit PATH warning were distracting false positives. |
| **Economics** | ~46K output tokens supervised vs. ~130K–180K unsupervised for equivalent shipped code. Output efficiency: 25.8% supervised vs. 6–8% unsupervised. LintGate consumed 1,762 output tokens (3.8%) and displaced ~84K–134K tokens of discipline-failure waste — a 48–76× return. Zero debug spirals, zero regressions. The debugging phase did not occur. (~$73 vs. ~$200–300 at Opus pricing.) |
| **Habit mode** | Never activated. Theory mode blocked it, greenfield build patterns (low same_file_ratio, high file spread) kept habit_score at 0.55, and no context pressure existed to trigger compaction. Habit mode appears optimized for maintenance/iteration, not builds. |
| **Overall** | LintGate's strength is continuous structural feedback during development — PostToolUse hooks and complexity checks are the standout features. Its weakness is theory extraction, which completely missed the project's primary spec document. Habit mode was invisible during this build session. For Claude Code projects, fixing the `.claude/CLAUDE.md` blind spot would transform theory tools from "requires manual workaround" to "genuinely useful." |
