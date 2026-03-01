---
theory_scope: true
---

# LintGate Agent Retrospective: scripts — Audit + Refactoring

## Metadata

| Field | Value |
|-------|-------|
| **Project** | ~/scripts — CLI tools for home network infrastructure (ASUS router, Marantz, SmartThings, BLE, deception) |
| **Agent** | Claude Opus 4.6, solo agent |
| **Date** | 2026-02-28 |
| **Scope** | 29 files (~10,235 LOC) → 31 files (~10,500 LOC) after extraction, Python + Bash, 17 Python files linted |
| **LintGate Tier** | Tier 2 (structural), normal strictness, ControlPlane enabled |
| **LintGate Version** | Unknown (local MCP install at ~/tools/lintgate) |
| **Session Type** | Audit + Refactoring — first LintGate session on this codebase |
| **Session Record(s)** | /Users/rohanvinaik/.claude/projects/-Users-rohanvinaik-tools-infrastructure-hardware/803c9cdb-16e6-4f87-abdb-5cc0b90b52b3.jsonl |
| **Session Continuity** | Resumed from handoff (continuation of ASUS auth + CAPTCHA OCR session) |
| **Prior State** | Working — scripts actively used for router control, device management, network defense. No prior linting or quality tooling. Organic growth over ~3 days of intensive development. |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "isolated"** — *"Issue isolated to lint. deps, behavior, mutation, structure, tests, performance confirm no problems in their domains."*

The "isolated" coherence state was useful as a framing device: it immediately told me that this codebase has no dependency conflicts, no import cycles, and no structural architecture problems — the issues are purely code quality within individual files. This meant I could focus on per-file refactoring without worrying about cascading cross-module changes.

The 280 lint findings (40 blocking) against 15 Python files averaging ~280 LOC each is high but expected for a codebase that grew organically during a rapid infrastructure buildout. The code was written for correctness and speed-of-development, not maintainability.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 40 | 16 unresolved imports (mitmproxy×5, bleak×8, Crypto.Cipher, iphone_backup_decrypt, pyvesync), 8 cognitive complexity (CC 33-122), 3 file-too-long (1004-1132 LOC), 3 deep nesting (depth 8-11), 3 cyclomatic complexity (CC 31-54, grades E/F), 2 maintainability index (0.0 grade C), 2 unused variables, 1 too-many-statements (111), 1 mypy syntax (external pkg) |
| Warnings | 148 | F541 (f-string no placeholder) ×54, PERF011 ×33, PERF001 (O(n²) membership) ×25, cognitive-complexity ×15, F401 (unused import) ×17, format ×14 |
| Informational | 109 | B603 (subprocess), B105 (hardcoded passwords — expected for router creds), B110 (try/except pass), various |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | Created by LintGate auto-setup (uv venv .venv) |
| Lockfile | Absent — no requirements.txt or pyproject.toml |
| .python-version | Missing |
| Structure snapshot | Cycles: 0, Orphans: 0, Largest module: ingest_devices.py (855 LOC) |

### Theory Profile

Compass extracted 18 claims across 4 axes. Problem axis was strong (depth 3, 15 claims — auto-extracted from project docs and CLAUDE.md). Solution and world axes were sparse until filled via interview (approach rationale, tradeoffs, runtime constraints). Final state: problem=3, solution=2, implementation=1, world=1. Spikiness dropped from 0.5 to 0.17 after interview.

---

## Part II: Observations During Refactoring

### The Three-Phase Pattern

The refactoring naturally fell into three phases, each driven by different LintGate signals:

**Phase 1: Mechanical cleanup (lint_fix)**
Auto-fixed 70 ruff errors (F541 f-string placeholders, F401 unused imports, F841 unused variables) and 10 import sort violations across all 15 Python files. This was pure tooling — zero judgement required. Reduced warnings by 68 in one pass.

**Phase 2: Dependency noise reduction**
Installed optional runtime deps (mitmproxy, bleak, pycryptodome, iphone_backup_decrypt, pyvesync) into the venv. This addressed the category of blocker that *shouldn't have been blocking* — these are conditional imports that only fail at runtime if the dep is actually needed. Post-install, the `ty` linter still couldn't resolve them (likely not finding the venv). 16 blockers remained as noise.

**Phase 3: Structural extraction (manual, guided by lint)**
This was the real work. LintGate identified three concrete extraction targets through the combination of file-too-long, cognitive-complexity, and too-many-statements signals:

1. **asus_auth.py (1004 → 798 LOC)**: Extracted CAPTCHA OCR pipeline into `asus_captcha.py` (245 LOC). The extraction seam was obvious: the CAPTCHA code had zero dependencies on auth state — it was a pure function (gif_bytes → captcha_string). LintGate's file-too-long signal was the trigger, but the actual extraction point came from reading the code.

2. **squish.py (1248 → 316 LOC)**: Split into three files:
   - `squish_scan.py` (497 LOC) — Device model, 5 protocol scanners, cross-referencing, fuzzy matching
   - `squish_recover.py` (287 LOC) — BLE GATT recovery handlers (Aranet4, MOONSIDE, generic)
   - `squish.py` (316 LOC) — Thin CLI dispatcher (argparse + command routing)

   The natural seams were: scanners form a cohesive cluster (all return `list[Device]`), BLE recovery is entirely self-contained, and the CLI dispatch is just routing. LintGate flagged the file-too-long and multiple cognitive-complexity blockers, but the *seam identification* required understanding the code's architecture.

### Key Observation: Lint Signals Tell You WHAT, Not WHERE

LintGate correctly identified that squish.py was too long and had too-complex functions. But the lint output didn't tell me *where to split*. The cognitive-complexity flag on `scan_smartthings` (CC 42) said "this function is too complex" — it didn't say "extract the API fetch logic into `_fetch_st_json()` and the item parsing into `_parse_st_item()`." That seam was visible only after reading the function and noticing two distinct responsibility clusters.

This is the gap between diagnosis and prescription. See Part VIII for thoughts on how mutation testing could bridge it.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck | No — first session | N/A | LintGate auto-scaffolded config + venv |
| Secrets-in-diff | No | N/A | N/A |
| Supply-chain (pip-audit) | Yes — passed | Useful confirmation | No vulnerabilities in installed deps |
| Type integrity (ty) | Yes — 16 unresolved imports | Partially — these are optional runtime deps | Need to install into venv or suppress |
| Security fast path (bandit) | Yes — B105 (hardcoded passwords), B603 (subprocess), B110 (try/except pass) | B105 expected (router creds), B603 expected (curl subprocess), B110 worth reviewing | Domain-expected for infrastructure scripts |
| Structure (cycles/size/orphans/cohesion) | Yes — file-too-long ×3, cognitive-complexity ×8, deep-nesting ×3 | Highly actionable | Primary refactoring targets |

---

## Part IV: Fix Patterns and Techniques

### Pattern A: Mechanical Auto-Fix (lint_fix)
**Trigger:** F541, F401, F841, import sort, ruff format
**Technique:** `lint_fix(dry_run=False)` — zero manual intervention
**Yield:** 70 fixes across 15 files in one pass
**Observation:** This should always be the first step. It's free, fast, and reduces noise so the structural issues become visible.

### Pattern B: Module Extraction for File-Too-Long
**Trigger:** file-too-long (1004, 1248 LOC) + cognitive-complexity clusters
**Technique:** Identify responsibility clusters by reading imports and call graphs. Functions that share no state with the rest of the file are extraction candidates.
**Yield:** asus_auth.py → asus_captcha.py (-206 LOC). squish.py → squish_scan.py + squish_recover.py (-932 LOC).
**Decision rule used:** If a cluster of functions has a clean API boundary (one or two entry points, no shared mutable state with the parent file), extract it.

### Pattern C: Function Decomposition for Cognitive Complexity
**Trigger:** cognitive-complexity > 15 (scan_smartthings CC 42)
**Technique:** Identify distinct logical steps within the function. Each step that can take inputs and produce outputs independently becomes a helper.
**Yield:** `scan_smartthings` → `_fetch_st_json()` + `_parse_st_item()` + slim `scan_smartthings()`. CC 42 → CC ~8.
**Decision rule used:** "Each function should be understandable in one mental pass" (LintGate's own suggestion). When a function has two loops or two try/except blocks doing different things, that's a seam.

### Pattern D: Accepted/Deferred Findings
**Trigger:** Unresolved imports for optional deps, external package syntax errors
**Technique:** Accept as noise. These are correct behavior — conditional imports that only fail at runtime if the dep is missing.
**Yield:** None — these are not bugs. 16 "blockers" that should be warnings or informational.

---

## Part V: Quantitative Results

### Before and After

| Metric | Before (run 1) | After lint_fix (run 2) | After extraction (run 3) | Net Delta |
|--------|----------------|------------------------|--------------------------|-----------|
| Blockers | 40 | 37 | 32 | **-8 (-20%)** |
| Warnings | 148 | 80 | 80 | **-68 (-46%)** |
| Informational | 109 | 95 | 93 | **-16 (-15%)** |
| Total | 297 | 212 | 205 | **-92 (-31%)** |
| ControlPlane coherence | isolated | isolated | isolated | unchanged |
| Python files | 15 | 15 | 17 | +2 (extractions) |
| Largest file | squish.py (1248) | squish.py (1248) | squish_scan.py (497) | **-751 LOC** |

### Blocker Breakdown (Final State: 32)

| Kind | Count | Notes |
|------|-------|-------|
| Unresolved imports | 16 | Optional deps — ty linter can't find venv packages. Domain noise. |
| Cognitive complexity | 6 | Remaining monsters: build_unified.py (CC 48, 91), ingest_devices.py (CC 65, 89), extract_asus_creds.py (CC 122) |
| External package syntax | 3 | bleak/backends/client.py — not our code |
| Cyclomatic complexity | 3 | ingest_devices.py (CC 54, 31), build_unified.py (CC 52) |
| Deep nesting | 2 | extract_asus_creds.py (depth 8, 11) |
| File too long | 1 | squish_scan.py at 497 (limit 400) — marginal, could split further |
| Maintainability index | 1 | extract_asus_creds.py (MI 0.0, grade C) |

### What Moved vs What Didn't

**Moved:** File-too-long (3→1), unused variables (2→0), format warnings (14→0), f-string warnings (54→0), unused imports (17→0). These were all mechanical or structural — addressable without understanding the code's purpose.

**Didn't move:** Cognitive complexity in build_unified.py, ingest_devices.py, extract_asus_creds.py. These are the "hard" monsters — complex parsing logic that requires domain understanding to refactor. Also: all 16 unresolved imports persist because the ty linter's venv detection doesn't work.

---

## Part VI: Process Assessment

### The LintGate Workflow

```
getting_started → theory_mode_enter → compass_update → compass_interview →
theory_mode_freeze → controlplane_run → controlplane_get_details →
[refactoring] → lint_fix → controlplane_run → retrospective
```

### What Works Well

1. **ControlPlane coherence framing** — "isolated" immediately told me the problem was localized to lint, not architectural. Saved time that would have been spent investigating cross-module issues.
2. **Theory mode / compass** — Forcing me to articulate the project's solution rationale and world constraints before diving into code was valuable. The interview questions were well-targeted to the sparse axes.
3. **Blocker categorization** — The breakdown by kind (cognitive-complexity, file-too-long, deep-nesting) with specific line numbers made triage immediate. I could see the worst offenders without reading code.
4. **Pattern alerts (recurring issues)** — Flagging that F541 (54 instances), PERF011 (33), and PERF001 (25) are recurring across runs is more useful than raw counts.
5. **Post-edit hooks** — The automatic lint-on-edit feedback loop (coherence/blocking/warnings in the hook output) was the most valuable real-time signal. After every write/edit, I could see whether the change helped or hurt without running a full scan.

### What Could Be Better

1. **Unresolved imports for optional deps** — 16 of 40 initial blockers are unresolved imports for packages that are intentionally optional (bleak, mitmproxy, pyvesync). These scripts use conditional imports and only fail if the dep is actually needed at runtime. A way to mark deps as optional would reduce noise.
2. **External package findings** — 3 blockers are syntax errors in `bleak/backends/client.py` (an installed package, not our code). LintGate should exclude site-packages from analysis.
3. **`mutation_decompose` requires tests** — I tried `mutation_decompose` on squish.py hoping for seam detection. It returned 0 candidates because there are no tests. The mutation system is currently test-dependent, but structural decomposition signals should work without tests. See Part VIII.
4. **PERF001 false positives on string membership** — 25+ warnings about "O(n²) membership test" for `in line` and `in svc` checks where `line` and `svc` are strings, not lists. String `in` is O(n) already and idiomatic — these are false positives.
5. **No "refactor prescription" tool** — LintGate identifies *what* is wrong (CC 42, file too long) but doesn't suggest *where to split*. The agent still has to read the code and identify seams manually. See Part VIII for how this could be addressed.

---

## Part VII: The Agent's Experience

### What I Spent Time On

Roughly:
- 10% — LintGate setup (getting_started, theory, compass interview)
- 15% — Analyzing findings (controlplane_run, get_details, understanding blockers)
- 15% — Mechanical fixes (lint_fix, installing deps, auto-formatting)
- 50% — Structural extraction (reading code, identifying seams, writing new files, updating imports)
- 10% — This retrospective

The 50% structural extraction phase is where the real work happened, and it was the phase where LintGate provided the *least* guidance. LintGate told me squish.py was too long and had complex functions. I had to figure out that the scanners form a natural module, the BLE recovery handlers form another, and the CLI dispatch is the thin spine.

### The Edit Hook Was The Most Useful Signal

The `PostToolUse:Edit/Write` hook that reports `coherence=isolated; blocking=3; warnings=10` after every file change was more useful than the full controlplane runs. It gave me immediate feedback: "that extraction dropped blocking from 5 to 3" or "that edit introduced 2 new warnings." This is the tightest feedback loop in the system and should be emphasized in the workflow.

### Token Efficiency

The full controlplane_run takes ~2 seconds and ~200 tokens per invocation. The edit hooks are essentially free (reuse cached state). For a project this size, the economics are favorable — the alternative (manually running ruff, radon, mypy, bandit, ty separately) would take far more human time.

---

## Part VIII: Broader Observations

### Refactor Signals: Function → File → Project

LintGate currently provides signals at two levels: individual findings (per-function CC, per-file LOC) and project-wide coherence. There's a missing middle layer — **inter-function and inter-file relationship signals** that would tell the agent *how* to refactor, not just *that* something needs refactoring.

**Function-level signals (currently present):**
- Cognitive complexity, cyclomatic complexity (measures difficulty)
- Deep nesting (measures structural density)
- Too many locals/parameters (measures overloading)

**Function-level signals (missing):**
- **Responsibility cluster detection:** "Functions A, B, C in this file share state X but not Y. Functions D, E share state Y but not X. These are two natural modules." Could be computed from variable sharing / call graph analysis within a file.
- **Pure function identification:** LintGate tracks pure function count (33/171 = 19.3%) but doesn't expose *which* functions are pure. Pure functions are the easiest extraction targets — they have no side effects, so moving them can't break anything.
- **Call fan-in/fan-out per function:** A function called by many others is a utility. A function that calls many others is an orchestrator. Utilities are safe to extract; orchestrators define the file's structure.

**File-level signals (missing):**
- **Cohesion score:** How related are the functions within a file? A file with 5 functions that all call each other is cohesive. A file with 3 clusters of functions that never interact is ripe for splitting. This is computable from the intra-file call graph.
- **Import fan-in:** How many other files import from this one? High fan-in = foundational module (careful when splitting). Zero fan-in = leaf module (safe to split, merge, or delete).
- **CLI vs logic mixing score:** Does this file mix argparse/CLI dispatch with business logic? If so, it's a splitting candidate. Detectable by checking whether a file has both `argparse` imports and significant non-CLI function definitions.

**Project-level signals (partially present):**
- Cycle detection (present, found 0 — good)
- File-too-long counts (present, but doesn't suggest *which* functions to move)
- **Missing: "These N files should be a package" detection** — When files share a prefix (squish.py, squish_scan.py, squish_recover.py) and have import relationships, suggest creating a package directory.
- **Missing: Duplicate pattern detection** — "These 5 files all have the same config loading pattern (expand path, read JSON, handle missing). Extract to shared utility."

### Mutation Testing as a Refactor Signal

The `mutation_decompose` tool currently requires tests to evaluate mutation survival rates. When I tried it on squish.py (no tests), it returned 0 candidates. This is a fundamental limitation for the common case: projects being linted for the first time rarely have tests.

**How mutation testing *could* drive refactoring without tests:**

The core insight of mutation testing is: "if I change this code, does anything notice?" In the testing context, "notice" means "a test fails." But there's a testless analog:

1. **Static reachability mutation:** Instead of running mutated code, statically analyze whether a mutation in function F would be observable from function G (i.e., does G depend on F's output, directly or transitively?). Functions whose mutations are only observable from a single caller are tightly coupled to that caller — they belong together. Functions whose mutations are observable from many callers are shared utilities — they belong in a shared module.

2. **Import-graph mutation:** "If I remove function F from file X, which other files break?" This is computable without running code — just trace the import graph. Functions that can be removed without breaking other files are internal implementation details. Functions that break many files are the API surface. This immediately identifies extraction boundaries.

3. **Survival rate as coupling proxy:** Even without tests, you can define "survival" as "does the function's type signature change?" or "does the function's return value's *shape* change?" A function with many mutations that don't change its observable interface is loosely coupled and safe to move. A function where most mutations change the interface is tightly coupled to its callers.

4. **Decomposition = low-coupling seam detection:** The `mutation_decompose` tool could operate on *static coupling* instead of *test-observed coupling*. Given a function with CC > 15:
   - Identify its internal variable dependency graph
   - Find the minimum cut that separates two clusters
   - Propose: "Extract lines 10-30 as helper function — they share only variable `x` with the rest"
   - This is essentially Tarjan's algorithm applied to variable lifetimes within a function

**Concrete enhancement proposal:**

```
mutation_decompose(path, file, mode="static")  # New mode: no tests needed
```

Returns:
```json
{
  "candidates": [
    {
      "function": "scan_smartthings",
      "file": "squish_scan.py",
      "cc": 42,
      "proposed_splits": [
        {
          "name": "_fetch_st_json",
          "lines": [215, 248],
          "shared_vars": ["SMARTTHINGS_SH", "SCRIPTS"],
          "reason": "Independent I/O block — async fetch with no dependency on downstream parsing"
        },
        {
          "name": "_parse_st_item",
          "lines": [250, 290],
          "shared_vars": [],
          "reason": "Pure function — takes dict, returns Device. Zero shared state."
        }
      ],
      "residual_cc": 8
    }
  ]
}
```

This would have saved me the 50% of time I spent reading code to identify seams. The tool already has the AST parsing infrastructure (it computes CC) — extending it to analyze variable sharing within functions is a natural next step.

### Pre-Emptive Test Generation: Making Mutation Signals Available From Day One

The static decomposition mode above is the "no tests" fallback. But there's a stronger path: **generate a comprehensive test suite automatically on the first ControlPlane run**, so that `mutation_decompose` has real survival data from the very beginning of a LintGate engagement.

LintGate already has every piece of this pipeline — they just aren't chained together automatically:

| Tool | What it does | Exists? |
|------|-------------|---------|
| `controlplane_run` (tests channel) | Detects "no tests" state | Yes |
| `inspect_algebra` | Identifies pure functions + algebraic properties | Yes |
| `controlplane_test_skeleton` | Generates pytest stubs per source file | Yes |
| `generate_property_tests` | Generates Hypothesis property tests for pure functions | Yes |
| `mutation_run_sampling` | Computes survival rates | Yes |
| `mutation_decompose` | Identifies decomposition candidates from survival data | Yes |

**The missing piece is orchestration.** These tools exist as independent manual steps. The proposal is a single background pipeline that fires automatically:

#### Phase 1: First ControlPlane Run (automatic trigger)

```
controlplane_run(path)
  → tests channel reports: "0 test files, 0 coverage"
  → triggers: BACKGROUND_TEST_BOOTSTRAP pipeline
```

The key word is *background*. The agent continues its lint/refactor work normally. The test generation pipeline runs in parallel, and its outputs become available incrementally — the agent gets notified as each file's tests are ready, and `mutation_decompose` gains resolution progressively.

#### Phase 2: Background Test Bootstrap Pipeline

**Step 1 — Algebraic survey (fast, ~1s):**
`inspect_algebra(path)` → identifies all pure functions, their properties (idempotent, bounded, commutative), and their type signatures. This is the easy harvest — pure functions are testable without mocking.

**Step 2 — Skeleton generation (fast, ~2s per file):**
`controlplane_test_skeleton(path, target_file)` for every source file. Produces pytest files with correct imports, fixture setup, and stub functions named after each source function. These skeletons are *runnable* (they pass trivially) but have no meaningful assertions yet.

**Step 3 — Property test generation for pure functions (fast, ~3s):**
`generate_property_tests(path)` for all pure functions identified in step 1. These are *real tests* — Hypothesis generates random inputs and checks algebraic properties hold. For this project, that's 33 pure functions out of 171 total — 19.3% of the codebase gets meaningful tests immediately with zero human input.

**Step 4 — Behavioral contract generation for impure functions (the hard part):**
This is the step that doesn't fully exist yet. For impure functions (I/O, subprocess, state mutation), you can't use property testing. But you can generate *structural behavioral contracts*:

- **Return type assertions:** "This function claims to return `list[Device]`. Assert `isinstance(result, list)` and `all(isinstance(d, Device) for d in result)`." Derivable from type annotations or AST inference.
- **Shape preservation assertions:** "This function takes a list of N items and returns a list. Assert `len(result) <= len(input)`" (for filters) or `len(result) == len(input)` (for transforms). Derivable from the function's loop structure.
- **Error boundary assertions:** "This function has a try/except that returns `[]` on failure. Assert that it returns `[]` when given a nonexistent path, and returns `list[Device]` when given valid input." Derivable from exception handlers in the AST.
- **Mock-based I/O contracts:** "This function calls `subprocess.run(["curl", ...])`. Generate a test that mocks subprocess.run, feeds it a fixture response, and asserts the function parses it into the expected output shape." Derivable from subprocess/HTTP calls in the AST.

An LLM agent (the same Claude model running LintGate) is uniquely positioned for step 4. It can read the function, understand what it *does* semantically, and generate a test that exercises the meaningful behavior — not just "does it run without crashing" but "does it actually parse the ASUS client list into Device objects with the right fields." This is where the model's language understanding creates tests that static analysis alone cannot.

**Step 5 — Mutation sampling (slow, ~30-60s):**
`mutation_run_sampling(path)` against the newly generated tests. Even with imperfect tests, mutation sampling reveals which functions have high survival rates (= poorly tested = the mutations don't get caught). High survival → the function is either undertested or so entangled that mutations propagate without being observable.

**Step 6 — Decompose signals now available:**
`mutation_decompose(path)` now has real survival data. Functions with high survival across multiple mutation categories are the decomposition candidates. The agent gets a ranked list: "These 5 functions need structural decomposition. Here's where to cut."

#### Phase 3: Incremental Test Maintenance (on every edit)

The edit hooks already fire on every Write/Edit. Extend them:

```
PostToolUse:Write hook
  → detect: new function added to file X
  → background: generate test for new function (property if pure, behavioral if impure)
  → background: mutation_run_sampling on changed file only (~5s)
  → update decompose signals for changed file
```

This means `mutation_decompose` stays current as the codebase evolves. When the agent extracts `scan_smartthings` into helpers, the mutation signals update to reflect the new structure. The refactoring feedback loop closes in near-real-time.

#### Why This Matters: The Cold Start Problem

The fundamental issue I hit in this session: `mutation_decompose` returned 0 candidates because there were no tests. The 18 "safe repairs" offered by ControlPlane were all test skeletons — but those skeletons have no assertions, so they wouldn't help mutation testing either. I had to do the structural refactoring entirely by reading code and manually identifying seams.

If the pipeline above had run automatically on my first `controlplane_run`:
- 33 pure functions would have gotten Hypothesis property tests (~0 human effort)
- ~100 impure functions would have gotten behavioral contract tests (LLM-generated, ~0 human effort)
- `mutation_run_sampling` would have identified which functions have high mutation survival
- `mutation_decompose` would have returned actual candidates with survival data
- I would have gotten: "scan_smartthings has 85% mutation survival. 60% of surviving mutations are in the API fetch block (lines 215-248). Proposed split: extract fetch logic into helper. Expected survival drop: 85% → 40%."

That's the difference between "this function is complex (CC 42)" and "here's exactly where to cut and what the improvement will be." The former is a lint finding. The latter is a refactoring prescription.

### Cross-Environment Synchronization: When Lint Findings Are Runtime Bugs

The most surprising outcome of this session: a **lint warning (E402: module import not at top of file) turned out to be the root cause of a runtime failure** on a remote machine. This reveals a class of bugs that LintGate could catch proactively if its tools understood multi-environment deployment as a first-class concern.

#### What Happened

`asus_auth.py` had `import asus_captcha` at line 322 (mid-file, after ~300 lines of auth logic), instead of at the top with the other imports. LintGate flagged this as E402 — a style warning. In practice, it caused a cascading import failure:

- On the development machine (M1 Max, Homebrew Python 3.13, PIL in site-packages): everything worked fine. Python resolves imports lazily, so the mid-file `import asus_captcha` happened to work because PIL was on the default path.
- On the deployment machine (macpro, Apple Command Line Tools Python 3.9, PIL in `~/Library/Python/3.9/lib/python/site-packages/`): the import chain `asus_auth.py → asus_captcha.solve() → _preprocess() → from PIL import Image` failed with `No module named 'PIL'`.

The paradox: standalone `python3 -c "from PIL import Image"` worked on macpro. Standalone `python3 -c "import asus_captcha"` worked on macpro. But the full call chain through asus_auth.py triggered the failure. Moving the import to the top of the file (fixing E402) resolved the issue completely — CAPTCHA OCR login succeeded on the first attempt after the fix.

The exact mechanism is likely an import ordering subtlety: when `import asus_captcha` happens at line 322 (deep in the module's execution), the Python import machinery's state (specifically `sys.path` snapshot, or the parent module's `__path__`) differs from what it is at module-load time. At the top of the file, all imports resolve with the same fresh `sys.path`. Mid-file, the path may have been altered by prior code, or the import lock may interact differently with the lazy PIL import inside `_preprocess()`.

#### The Broader Pattern: Cross-Environment Deployment Bugs

This is a *class* of bugs, not a one-off. Projects that are developed on one machine and deployed to others via `scp` (as this project is) face several synchronization hazards:

1. **Python version mismatch** — Development on 3.13, deployment on 3.9. Features, import behavior, and `sys.path` resolution differ.
2. **Package location divergence** — Homebrew puts packages in `/opt/homebrew/lib/`, Apple CLT puts them in `~/Library/Python/3.9/lib/python/site-packages/`. Same package, different path, different resolution behavior.
3. **PATH differences in SSH sessions** — Non-interactive SSH sessions on macOS have `PATH=/usr/bin:/bin:/usr/sbin:/sbin`. Homebrew's `/usr/local/bin` (or `/opt/homebrew/bin`) is absent. This is why `tesseract` wasn't found until we added `_find_tesseract()` with explicit path probing.
4. **Import ordering sensitivity** — Mid-file imports (E402) work on one environment but fail on another because the import machinery's state at that point in execution differs between Python versions or system configurations.
5. **Lazy imports hiding failures** — `from PIL import Image` inside `_preprocess()` (called only during CAPTCHA solving) means the import failure is invisible until the specific code path is exercised. Static linting sees "asus_captcha imports fine" and misses that `_preprocess()`'s internal import will fail at runtime.

#### How LintGate Could Catch These

**Tool 1: `sync_check` — Cross-Environment Import Verification**

A new tool (or extension of `controlplane_run`) that simulates import resolution under multiple Python environments:

```
sync_check(
    path="/my/project",
    environments=[
        {"python": "3.9", "site_packages": ["PIL", "bleak"]},
        {"python": "3.13", "site_packages": ["PIL", "bleak", "mitmproxy"]},
    ]
)
```

This wouldn't need to actually run on the remote machines. It would:
- Parse all import statements (top-level AND deferred/lazy imports inside functions)
- Build a full import dependency graph including *transitive* imports (asus_auth → asus_captcha → PIL)
- Flag imports that are:
  - **Positionally sensitive:** Mid-file (E402) AND transitively import packages not in stdlib. These are the dangerous ones — they work in some environments but not others.
  - **Lazily deferred:** Inside function bodies, meaning they're invisible until runtime. Combined with E402, this creates the exact bug we hit.
  - **PATH-dependent:** Imports that resolve differently based on `sys.path` ordering (e.g., a local `json.py` shadowing the stdlib `json`).

**Tool 2: Promote E402 to Blocking When Transitive Imports Include Non-Stdlib Packages**

Currently E402 is a style warning. But the insight from this incident is: **E402 + non-stdlib transitive import = potential runtime failure on environments with different `sys.path` resolution**. The rule:

```
IF import is mid-file (E402)
AND the imported module transitively imports non-stdlib packages (PIL, bleak, etc.)
THEN severity = blocking (not warning)
WITH message: "Mid-file import of '{module}' transitively depends on '{non_stdlib_dep}'.
              Import ordering sensitivity may cause runtime failures on environments
              with different sys.path resolution. Move to top of file."
```

This single rule would have caught the PIL bug as a *blocker* instead of a warning, and the fix ("move import to top of file") is trivially prescriptive.

**Tool 3: `deploy_lint` — Pre-Deployment Environment Diff**

For projects deployed via `scp` (no CI/CD, no Docker, no requirements.txt), a tool that compares the development and deployment environments:

```
deploy_lint(
    source="localhost",
    target="macpro",
    files=["asus_auth.py", "asus_captcha.py"]
)
```

Output:
```json
{
  "python_version_mismatch": {"source": "3.13.1", "target": "3.9.6"},
  "missing_packages": [],
  "path_divergence": {
    "tesseract": {"source": "/opt/homebrew/bin/tesseract", "target": "/usr/local/bin/tesseract"},
    "PIL": {"source": "/opt/homebrew/lib/python3.13/site-packages/PIL", "target": "~/Library/Python/3.9/lib/python/site-packages/PIL"}
  },
  "e402_risk": [
    {
      "file": "asus_auth.py",
      "line": 322,
      "import": "asus_captcha",
      "transitive_non_stdlib": ["PIL", "tesseract (subprocess)"],
      "risk": "high — mid-file import with non-stdlib transitive deps, Python version mismatch"
    }
  ]
}
```

This is particularly valuable for the "scripts on bare metal" pattern (no virtualenvs, no containers, no package managers on remote). The user's codebase is deployed to 3 machines via `scp`, each with different Python versions and package layouts. This is a common pattern in home lab / infrastructure automation projects — exactly the kind of project LintGate targets.

**Tool 4: Extend Edit Hooks to Flag Cross-Environment Risk**

The post-edit hooks already report `coherence` and `blocking`. Extend them to include a deployment risk signal when the project has known remote targets:

```
PostToolUse:Edit hook
  → coherence=stable; blocking=0; warnings=7
  → deploy_risk=high (E402 with non-stdlib transitive: asus_captcha→PIL)
```

The `deploy_risk` signal would fire only when the edit involves imports, `sys.path` manipulation, or subprocess calls to binaries that vary across environments. It's a lightweight check (AST-only, no network) that catches the class of "works on my machine" bugs before deployment.

#### Why This Matters Beyond This Project

The E402 → PIL failure is a microcosm of a much larger problem: **static linting operates on the development environment's assumptions, but code runs on deployment environments that differ in subtle ways**. Docker and virtualenvs solve this for web applications, but for infrastructure scripts, CLI tools, and home lab automation — exactly the kind of projects that benefit most from LintGate — there's no containerization layer. The code is copied to bare metal and run with whatever Python happens to be installed.

LintGate is uniquely positioned to address this because it already has:
- Full AST parsing (can trace import chains)
- Multi-linter orchestration (can correlate E402 with type resolution failures)
- Edit hooks (can catch problems in real-time)
- ControlPlane coherence model (can promote warnings to blockers based on cross-channel evidence)

The missing piece is treating "this code will be run on a different machine" as a first-class concern, not an afterthought. The four tools above would catch the entire class of problems we spent debugging via SSH trial-and-error.

#### Cost Estimate

For this project (17 Python files, 171 functions):
- Steps 1-3: ~30 seconds, ~500 tokens (existing tools, mechanical)
- Step 4 (LLM behavioral contracts): ~2-5 minutes, ~10,000-20,000 tokens (one-time)
- Step 5 (mutation sampling): ~60 seconds (mutmut, no token cost)
- Incremental updates: ~5 seconds per edit (mutation on changed file only)

Total bootstrap cost: ~20,000 tokens and ~3-5 minutes of background time. For a project that will consume 100,000+ tokens in a refactoring session anyway, that's a 20% surcharge that pays for itself immediately by eliminating the "read code to find seams" phase.

The key insight: **the test suite is not the goal — it's the substrate.** The tests exist to make mutation analysis possible, which makes decomposition signals possible, which makes refactoring prescriptive instead of diagnostic. The tests are an intermediate artifact in service of the mutation → decompose → refactor pipeline.

---

## Part IX: Economics

### Session Telemetry

From `telemetry_summary(period="all")`:

| Metric | Value |
|--------|-------|
| ControlPlane runs | 3 |
| Lint file runs | 3 (on new extracted files) |
| Total issues found across runs | 41 (lint_files) + 280 (CP run 1) + 212 (CP run 2) + 205 (CP run 3) |
| Avg controlplane duration | ~2100ms |
| Avg lint_files duration | 774ms |
| Token estimate per run | ~200 |
| Total LintGate token cost | ~600 (lint_files) + ~600 (controlplane) = ~1200 tokens |
| Pure functions detected | 33 / 171 (19.3% purity ratio) |
| Performance issues | 15 across 11 analysis runs |

### Cost-Benefit

- **Agent time spent on LintGate interactions:** ~25% of session (setup, analysis, fix, re-scan)
- **Blockers eliminated:** 8 (40→32), with 16 of the remaining 32 being false positives (optional dep imports)
- **Warnings eliminated:** 68 (148→80) — almost entirely mechanical (lint_fix)
- **Structural improvement:** Largest file 1248→497 LOC, total files 15→17 (better factored)
- **Net code change:** +2 new files, ~500 lines of new/reorganized code, ~1000 lines removed from bloated files

The ROI is positive. The mechanical fixes (lint_fix) alone saved significant manual effort. The structural signals (file-too-long, CC) correctly identified the three files that needed splitting. The false positive rate on blockers (16/40 = 40%) is high and should be addressed.

---

## Summary

LintGate's ControlPlane + lint pipeline correctly diagnosed a rapidly-grown infrastructure codebase: "isolated" coherence (no architectural problems, only per-file quality issues), 40 blockers concentrated in 3 oversized files, 148 warnings mostly mechanical.

Refactoring reduced total findings by 31% (297→205). The biggest wins were mechanical (lint_fix cleared 68 warnings instantly) and structural (squish.py 1248→316 LOC via extraction into 2 focused modules). The remaining blockers are either false positives (16 unresolved optional imports) or hard complexity monsters requiring domain knowledge (CC 48-122 in data processing files).

**Key insight for LintGate development:** The tool excels at diagnosis (what is wrong, how severe) but lacks prescription (where to split, what to extract). Three paths to close this gap:

1. **Static decomposition mode** — `mutation_decompose(mode="static")` that analyzes variable coupling within functions to identify seams without any test infrastructure. Immediate value, zero prerequisites.

2. **Pre-emptive test bootstrap** — On the first ControlPlane run, automatically generate a full test suite in the background (property tests for pure functions via `inspect_algebra` + `generate_property_tests`, LLM-generated behavioral contracts for impure functions). This makes `mutation_decompose` fully operational from session start, turning it from a test-quality tool into a refactoring advisor. Cost: ~20K tokens and ~3 minutes of background time — a 20% surcharge that eliminates the "read code to find seams" phase entirely. The test suite is not the goal; it's the substrate that makes mutation → decompose → refactor prescriptive instead of diagnostic.

3. **Cross-environment synchronization** — An E402 warning (mid-file import) turned out to be the root cause of a runtime PIL import failure on a remote deployment machine. LintGate should treat E402 as *blocking* when the mid-file import transitively depends on non-stdlib packages — this single rule would have caught the bug immediately. Beyond that, a `sync_check` or `deploy_lint` tool that traces transitive import chains under multiple Python versions and `sys.path` configurations would catch the entire class of "works on my machine" bugs that plague bare-metal script deployment. See Part VIII for the full four-tool proposal.
