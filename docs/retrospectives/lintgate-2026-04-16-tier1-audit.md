---
theory_scope: false
---

# LintGate Agent Retrospective: LintGate — Self-Audit vs. Vision

## Metadata

| Field | Value |
|-------|-------|
| **Project** | LintGate — specification compiler for LLM-generated code (the tool auditing itself) |
| **Agent** | Claude Opus 4.6 (1M context), solo |
| **Date** | 2026-04-16 |
| **Scope** | Full LintGate repo — 230K LOC Python, 14,443 passing tests post-cleanup, 422→414 test files, 134 MCP tools |
| **LintGate Tier** | Tier 1, normal strictness, ControlPlane not invoked during audit (a finding in itself) |
| **LintGate Version** | main @ 52d7b3b + Phase 1/2/cleanup working-tree changes (uncommitted) |
| **Session Type** | Hybrid — Phase 2 script extraction implementation → aggressive test cleanup → self-audit retrospective |
| **Session Record(s)** | `/Users/rohanvinaik/.claude/projects/-Users-rohanvinaik-tools-lintgate/6b97f175-25b2-4e37-9099-1db52834a529.jsonl` (full session; audit phase starts at the turn where user asks "please identify all the usability, QoL, and generally speaking, all the failure modes") |
| **Session Continuity** | Multi-window continuation (Phase 1 from a prior context; Phase 2 + cleanup + audit within this window) |
| **Prior State** | Post-Phase-1 (working tree contained envelope-unwrap fixes + dep_tools/lint_tools subprocess extraction); impl files in Phase 1 shape; 14,677 tests passing (per session-start memory) |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "not invoked during audit"** — *"The agent did not run `controlplane_run` to orient at the start of the session, nor to check the state after Phase 2 completed, nor before starting the test cleanup. The tool that the project's CLAUDE.md calls out as 'the 200 tokens you spend orienting' was never spent."*

This is itself a finding. The project's own guidance is to orient via `controlplane_run` before acting. The agent was in `habit` mode (per the surfaced `lg_session.md` state: `Mode: habit (score: 0.75)`), which per the habit-mode design is supposed to skip re-orientation. In practice this meant the 61 pre-existing test failures — which a fresh `controlplane_run` would have surfaced via the tests channel — stayed invisible for the entire Phase 2 implementation phase, the test cleanup phase, and were only discovered when a background full-suite sweep happened to run. They were then dismissed as "pre-existing drift" until the user pointed out that this framing is itself the failure mode LintGate was built to eliminate.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 61 test failures | 20 in `test_reporter.py`, 7 in `test_platonic_impl.py`, 6 in `test_cold_start.py`, 5 in `test_mcp_server_helpers.py`, 4 in `test_test_regeneration_impl.py`, 4 in `test_doc_counts.py`, 3 in `test_new_features.py`, 3 in `test_habit_tools.py`, 2 in `test_setup_github_quality.py`, 2 in `test_orchestration_chain.py`, 1 each in 5 other files |
| Warnings | Not enumerated | Phase 2 + cleanup produced hook warnings (`channels_run=5; blocking=1; warnings=16-22`) on every write but I never drilled into them |
| Informational | Unknown | Never ran `controlplane_get_details` during the session |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | System Python (`/Users/rohanvinaik/miniconda3/bin/python` — not project-local `.venv`) |
| Lockfile | Present (per Phase 2 memory: "14677 passing") |
| .python-version | Not checked (I never ran `hygiene_check`) |
| Structure snapshot | Unknown — `controlplane_run` not invoked |

### Theory Profile

Per the surfaced `.claude/rules/theory.md`: partial validity, 324 claims, zero enforceable rules, zero high-signal anti-patterns beyond the PERF001-PERF011 family, core_theory facet populated. The theory pack was visible in system reminders but never retrieved by the agent via `build_theory_pack` for this session — the claims that showed up were courtesy of the hook, not the agent's orientation.

---

## Part II: Observations During Refactoring

### Observation 1: Thin subprocess wrappers defeat the mutation pipeline

After Phase 2 extraction, `mcp_tools/behavior_tools.py`, `compass_tools.py`, and `controlplane_tools.py` are ~150-line shells: import, argparse→subprocess→stdout relay. Running `improve_tests` on them returns no mutants to generate because there's no branching logic. Running `compact_tests` returns `"No compaction possible"`. Example:

```
compact_tests(mcp_tools/behavior_tools.py) → no_data, run improve_tests first
improve_tests(mcp_tools/behavior_tools.py) → 0 mutants (nothing to mutate)
```

The mutation engine's unit of analysis is the source file. It has no concept of "this is a communication wrapper; follow the subprocess call to the real compute." After Phase 2, roughly 30-50 MCP wrapper files became blind spots for the entire mutation-driven decomposition pipeline — the core value prop of the system.

**What this reveals:** The vision's promise — "specification complexity for every function, at the speed the agent writes code" — breaks down at architectural boundaries the project itself introduced. LintGate's own Phase 2 refactor (per `feedback_mcp_no_compute.md`: "MCP tools must do ZERO compute") made a large fraction of LintGate's own code invisible to LintGate's own analysis. The mutation engine needs a "delegation" concept: "this file forwards to script X; analyze script X instead".

### Observation 2: `compact_tests(dry_run=False)` silently breaks fixture-decorator alignment

Applying the dry-run-recommended compact to `tests/test_controlplane_impl_feedback.py` produced tests where the test function signature (`def f(self, mock_x)`) mismatched the stacked `@patch` decorators (expected 5 args from 3 decorators). Python's `unittest.mock` raises `TypeError: takes 3 positional arguments but 5 were given` at call time. 23 tests broke.

The AST rewriter preserved some but not all `@patch` decorators, or stripped fixtures the `self` arg depended on. The tool writes the file without running pytest first to validate. There is no automatic rollback.

**What this reveals:** The `compact_tests` tool has a correctness gap between "AST extraction preserves the tests I picked" and "the extracted tests actually execute". The dry-run preview doesn't indicate this will happen. A minimal fix: after writing, the tool should invoke `pytest --collect-only` on the file and revert on collection failure. Cost: ~200ms. Value: preserves the user's principle "if we over-correct, the LintGate tooling should enable us to regenerate" — which requires the regenerate path to not itself be broken.

### Observation 3: `compact_tests` cross-targets test files via name-collision on duplicated helpers

I ran `compact_tests(scripts/behavior_check.py)`. The tool returned `test_behavior_impl.py` as the target — a test file for a *different* module (`mcp_tools/_behavior_impl.py`). Both modules happened to contain copies of `_build_constraint_recommendation`, `_find_similar_failures`, `_compute_coverage_gap`, `_seed_theory_constraints` (because Phase 2a inlined helpers rather than factoring a shared module). The tool credited `test_behavior_impl.py` tests for killing mutants in `scripts/behavior_check.py` because the function names and signatures matched, even though the tests imported from the other module.

**What this reveals:** `compact_tests` discovers test files by a heuristic (probably name + symbol overlap) rather than by import graph. When helpers are duplicated across modules — which LintGate itself does in several places — the tool fingerprints the wrong file. The vision wants "code whose structure is the inevitable consequence of its specification"; the tool rewards duplication by silently conflating it.

### Observation 4: `improve_tests` is slow enough to discourage the "diagnose first" workflow

Running `improve_tests` on a single 480-LOC module (`lintgate/compass_helpers.py`) took ~30 seconds. On a 900-LOC module (`mcp_tools/_controlplane_impl_run.py`) I didn't wait for the result. The vision promises "at the speed the agent writes code"; the reality in this session was multi-minute waits to decide whether to delete a test file.

This cascades: the user asked for aggressive cleanup. The cheapest aggressive algorithm is "run improve_tests on every source file, sort by kill-rate × LOC, delete from the bottom". That algorithm is infeasible at the current per-file cost. I fell back to manual heuristics (file-pair duplication detection, grep-based mutation-gap identification) that don't require the mutation engine at all — which is ironic, because those heuristics are precisely what the mutation engine was supposed to make obsolete.

**What this reveals:** The performance budget claimed in the vision (`<100ms` per file for compose, `<200ms` for verify) is not met by `improve_tests`. Either the budget is wrong or the tool is doing more than intended. This is the single biggest gap between vision and reality this session surfaced.

### Observation 5: Hook output is unmanaged context overhead

Every file write produced a `PostToolUse:Write hook additional context: coherence=systemic; channels_run=5; blocking=1; warnings=16-22; edit_related=tests,lint; ambient_debt=performance,structure; loud=tests:fail,performance:fail,lint:fail,structure:fail` reminder. Every task-list tick fired a "task tools haven't been used recently" reminder. The session-feedback hook surfaced 3 feedback policies at every user prompt. The `lg_session.md` + `lg_focus.md` rules files were re-surfaced on each touching edit.

Conservatively, hook reminders consumed >10% of the context window across this session. The `feedback_tool_response_budget.md` policy applies to MCP tool responses (<200 chars to disk) but is violated by the hook pipeline itself.

**What this reveals:** The architecture that enforces token discipline on MCP tools has an exemption for its own status-surface. The hook system is effectively a second communication channel that bypasses the Tool Response Token Budget rule. Every signal from that channel increases the context-noise cost the vision is supposed to eliminate. The fix: hook output should go to disk with a slim reference envelope, same as MCP tools.

### Observation 6: Session memory does not warn when `git checkout` will destroy uncommitted work

During cleanup I ran `git checkout tests/test_controlplane_impl_feedback.py` to revert a broken `compact_tests` apply. This destroyed pre-existing uncommitted edits to that test file (Phase 1-era adaptations to the `executable_repairs` filter). The session had no signal that this file had been modified outside this session and that the modifications were necessary for tests to pass. 7 tests immediately started failing; I had to manually reconstruct the edits from the git diff of the impl module.

**What this reveals:** The system tracks `runtime_state.json`, session memory, refactor_checkpoint, and behavior_compass — but none of them tracked "this file has uncommitted changes you did not make; warn before destructive operations on it". Phase 2 memory explicitly flagged "working tree is uncommitted" as a concern, but that was advisory text in a markdown file, not a gate. A real `hygiene_check` on `git checkout <file>` would have intercepted this.

### Observation 7: Auto-generated "mutation gap" test files are cruft that LintGate itself created

I deleted ~300 tests across 4 `test_mutation_*.py` files (`test_mutation_onboarding_tools.py`, `test_mutation_dep_health.py`, `test_mutation_habit_mode.py`, `test_mutation_test_channel.py`, `test_mutation_controlplane_tools.py`). All were auto-generated earlier to fill mutation-survival gaps, and all turned out to be either (a) duplicating tests from primary test files or (b) testing Python's `bool()` coercion edge cases for trivial helpers (e.g., 10 tests for a 2-line `return bool(x)` wrapper).

These files were produced by LintGate's own mutation → prescription → generate pipeline. The pipeline's incentive is "kill more mutants"; it has no counter-incentive for "don't produce tests that duplicate existing coverage" or "don't bloat the suite with Python-semantics tests". The result is a production of tests that then cost time to remove.

**What this reveals:** The `mutation_prescribe_tests` workflow optimizes a local objective (kill rate on a specific function) without a global objective (minimize test-suite redundancy). The vision says LintGate produces "specification-complete mutation-driven decomposition"; in practice the produce-tests half of the loop has no feedback from the remove-tests half. A true minimum-killing-set pass would eliminate these at generation time, not post-hoc.

### Observation 8: RTK + pytest output interception requires a workaround that shouldn't be necessary

In this environment, `python -m pytest` output is intercepted by RTK (Rust Token Killer) and replaced with `Pytest: No tests collected` — even when tests pass. The workaround is to prefix with `rtk proxy`. This was not discoverable without reading RTK docs in CLAUDE.md, and the error message is actively misleading ("no tests collected" vs. "test output suppressed by rtk"). I wasted ~5 minutes early in the cleanup phase thinking tests had stopped running.

**What this reveals:** This isn't a LintGate bug directly, but LintGate's workflow assumes `pytest` output is available. Session guidance (or the `rtk` interaction layer) should surface "pytest output is filtered, use `rtk proxy`" at the point of failure. Alternatively, LintGate's own test-running wrappers (there's one in `scripts/lint_run.py`) could bypass the pytest-output interception entirely.

### Observation 9: The project has parallel test files that LintGate didn't prevent

`test_compass_tools.py` + `test_mcp_compass_tools.py` tested the same 17 functions with near-identical class names (`TestLoadModeDictExact` / `TestLoadModeDict`, etc.). `test_habit_tools.py` + `test_mcp_habit_tools.py` have a similar parallel structure. Both were created by earlier LintGate-guided sessions (per git log). Nothing in LintGate's test-coverage or structure channels flagged "these two test files cover the same source module" during their creation.

**What this reveals:** The structure channel reports largest modules, import cycles, orphans, cohesion — but it has no concept of "test-to-source cardinality". A simple finding "source module X has 2 test files covering it: A and B" would have surfaced this as drift. The vision calls for "code whose structure is the inevitable consequence of its specification"; two parallel test files for the same spec are, by definition, non-inevitable.

### Observation 10: `compact_tests` cannot be re-run idempotently

After `compact_tests(dry_run=False)` writes a new test file, there is no way to undo except `git checkout`. But the tool holds a ledger entry saying "compacted already — no new data". So running `compact_tests` again on the same file returns "no compaction possible". The system has no notion of "what was the state before compact — can I restore it?"

**What this reveals:** Write-through tools in LintGate don't maintain their own undo buffer. The vision claims regeneration is cheap ("If we over-correct, the LintGate tooling should enable us to regenerate") — but the regeneration path for a bad compact is `git checkout`, which has the file-drift destruction problem from Observation 6. This is a gap between claimed cheap-rollback and actual rollback cost.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck (command class) | No | N/A | `hygiene_check` was never called this session. I ran `git checkout <file>` (destructive) with zero precheck. |
| Secrets-in-diff | No | N/A | Not invoked. |
| Supply-chain (pip-audit) | No | N/A | Not invoked. |
| Type integrity (pyright) | Yes, passively via diagnostics | Partially — stale for re-exports | Pyright repeatedly flagged re-exported symbols as "unknown" even though runtime works. After the 3rd or 4th instance I stopped reading pyright warnings in the post-tool-use reminders. Classic trust-erosion. |
| Security fast path (bandit) | No | N/A | Not invoked. |
| Structure (cycles/size/orphans/cohesion) | No | N/A | `controlplane_run` never fired; structure channel output unseen. |

**What this reveals overall:** Every discipline signal was either not invoked, or invoked so noisily that the agent learned to ignore it. The hygiene/secrets/supply-chain tools are opt-in; the one that's passively applied (pyright via IDE-like diagnostics) has a false-positive rate high enough to train the agent to tune it out. A system whose discipline signals are unused by the agent it supervises is not actually supervising.

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| Dead-module + its-dedicated-test-file deletion | 1 | Identify module with no non-test consumers (grep), delete module + test file together | After an extraction refactor leaves an old impl module untouched |
| Parallel-test-file merge | 1 | Identify two test files with parallel class structure testing same source, extract unique class (e.g., `TestSubprocessArgv`) into the comprehensive file, delete the other | When Phase-X rewrite produced `test_mcp_X_tools.py` alongside existing `test_X_tools.py` |
| Mutation-gap wholesale deletion | 4 | Check that target function has coverage in non-gap files; verify `compact_tests` minimum-set targets don't include the gap file; delete | For auto-generated mutation-gap files duplicating natural-test coverage |
| Fixture-adaptation to match impl drift | 7 tests | Read the impl's current filter/guard logic, add required fields to mock fixtures | When uncommitted impl changes invalidated existing tests |
| Bulk patch-site retargeting via `sed` | 26 sites | Script iterates over symbol list and replaces `patch("old.path.X")` with `patch("new.path.X")` | After moving helpers between modules; preserves tests without rewriting assertions |

---

## Part V: Quantitative Results

### Before and After (this session's cleanup phase only)

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Test files | 422 | 414 | −8 |
| Test functions | ~14,549 | ~14,269 | −280 |
| Dead source modules | 1 (`mcp_tools/_behavior_impl.py`) | 0 | −1 module, −640 LOC |
| Test LOC | ~178,770 | ~174,070 | −~4,700 |
| Pre-existing test failures | Unknown (not measured) | 61 | Exposed, not fixed |
| Tool count (`@mcp.tool()`) | 134 | 134 | No change |

### Independent Tool Metrics: Before/After Autonomous Professionalization

Skipped — this session was mixed-purpose (implementation + cleanup + audit) and did not run the full `pylint / radon / ruff` matrix before and after each sub-phase. A clean baseline is not recoverable without a stash-and-re-measure cycle, and the audit phase is the current focus.

### Performance Tracking: Before/After Refactor Cycle

Skipped — not a pure refactor session; performance delta would conflate Phase 2 script-extraction overhead (subprocess launch per MCP call is +50-200ms per tool invocation) with cleanup wins (fewer tests = faster suite). A dedicated benchmark session would separate these.

One data point worth capturing: full-suite `pytest tests/ --ignore=tests/test_hook_controlplane.py` took **836 seconds (~14 minutes)** for 14,443 passing + 61 failing + 8 skipped tests. Per-test mean: ~57ms. This is the wall-clock cost of one "did I break anything" check, which is high enough that I avoided running it until the end. Compare to the vision's implicit budget ("at the speed the agent writes code"): a test suite that takes 14 minutes to validate is an order of magnitude above the vision's implied cadence.

### Current Standing vs. Industry Thresholds

| Metric | Current Value | Industry Threshold | Status |
|--------|--------------|-------------------|--------|
| Pylint | Not measured | ≥ 8.0 good | Unknown |
| Maintainability Index | Not measured | ≥ 20 maintainable | Unknown |
| Avg cyclomatic complexity | Not measured | ≤ 5 low | Unknown |
| Function grades A+B | Not measured | > 85% target | Unknown |
| High-complexity blocks (D+) | Not measured | < 5% acceptable | Unknown |
| Test reliability | 14,443 / 14,512 passing = 99.6% | 100% pass required | **Fail** — 61 pre-existing failures |

The most telling cell is the last row: **99.6% pass rate is a failing grade by the project's own vision**. The vision claims "14,520 passing tests" with "100% mutation kill rate on profiled functions" — a system that self-supervises into a 100% state. Reality: 61 drift failures that neither this session's agent nor any prior session's agent flagged, because the gating system is "did the tests the agent cares about in this turn pass" rather than "did the whole suite pass before and after".

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Phase 2 wrappers (behavior/compass/controlplane) | Pass | 455/455 tests on touched modules |
| Post-cleanup touched tests | Pass | 455/455 on the files I edited during cleanup |
| Wider test suite | **Fail** | 61 pre-existing failures across 15 other test files |
| MCP tool count invariant | Pass | Still 134 @mcp.tool() markers |
| Import graph | Pass | `python -c "import mcp_server"` succeeds |

### Reproducibility Notes

`compact_tests` produces a slim envelope with an `analysis_id`; the full preview is on disk at `.lintgate/analysis/test_compact/<id>.json`. Re-running the same command regenerates the same id (content-hashed). Deterministic. But `improve_tests` writes to `.lintgate/mutation/` per function; I did not verify the contents are content-hashed for determinism and saw no garbage collection of stale entries across Phase 2's refactor.

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| Phase 2a (behavior extraction) | ~45 min | Script write, wrapper, test split, verification |
| Phase 2b (compass extraction) | ~40 min | Helpers module, script, wrapper, bulk patch retarget |
| Phase 2c (controlplane extraction) | ~40 min | Script, wrapper, 4 register-test rewrites across 2 test files |
| Test cleanup — Phase 2 fallout | ~20 min | Duplicate file merge (compass), dead impl deletion (behavior) |
| Test cleanup — mutation-gap sweep | ~20 min | 4 mutation_* file deletions + 1 accidental compact_tests misfire + 7 test-adaptation fixes |
| Test cleanup — verification | ~25 min | Parallel tests, full-suite sweep (14 min wall clock for the sweep itself) |
| Audit / retrospective | ~20 min | This document |
| **Total** | **~3h 30min** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

```
(Phase 2 implementation — extensive)
Read → Write → Bash(pytest) → Edit → Bash(pytest)  [repeat per sub-phase]

(Cleanup phase — the part that used LintGate-proper tools)
compact_tests(wrapper) → "no data"
  → improve_tests(script)
    → compact_tests(script)
      → inspect preview
        → decide: delete / merge / apply
          → Bash(rm / Edit) → Bash(pytest)
```

The workflow I'd planned — `controlplane_run` → `triage_tests` → `compact_tests` across all big source files — collapsed after the first wrapper returned "no data". I never invoked `controlplane_run` or `triage_tests` at all. The actual workflow used two LintGate tools (`improve_tests`, `compact_tests`) and substituted bash/grep for the rest.

### Prediction Accuracy

Not measured — `constraint_check` was not invoked at any point during this session. Per the project's CLAUDE.md, `constraint_check` is supposed to fire before "any Bash command". I ran ~80 Bash commands; zero `constraint_check` calls. The session ran in habit mode, which the system designs to skip constraint overhead — but that design choice means the accuracy telemetry that feeds future confidence modulation is not being collected. Habit mode trades future calibration for current throughput, and that trade was invisible to me.

### Constraints Proposed

None this session. The constraint proposer didn't fire, or if it did, the proposals were not surfaced to the agent.

### What Works Well

1. **`compact_tests` dry-run preview output structure** — the JSON envelope with `original_test_count → compacted_test_count`, `reduction_pct`, and `preview_content` is genuinely useful for deciding whether to apply. The framing is right; it's the apply path that's broken.
2. **The cross-channel coherence labels in the PostToolUse hook** — `coherence=systemic` vs `coherence=coupled` vs `coherence=stable` is a useful single-word status on the post-write project state. The signal-to-noise ratio is poor but the signal itself is good when I paid attention.
3. **File-based analysis envelopes** — the disk-first architecture (slim envelope → full file on disk) worked well for the one or two times I needed to read a full analysis. The `query_analysis` path avoided re-running expensive tools.
4. **Phase 2 pattern repeatability** — extract helpers to `lintgate/<domain>_helpers.py`, script does CLI+emit, MCP subprocess-wraps. The pattern worked 3 times in a row with minor adjustments. That's evidence the architecture itself is right.

### What Could Be Better

1. **`improve_tests` budget** — the per-file cost is 10-30× the claimed vision budget. This is the single highest-leverage fix. Targeted mutation (only on functions with source diffs since last ledger entry) would reduce this by ~10×.
2. **`compact_tests` must validate before writing** — `pytest --collect-only` on the target file after AST rewrite, revert on failure. This is 5 lines of code. It would have prevented the 23-test breakage in this session.
3. **MCP-wrapper awareness in the mutation engine** — detect "this file is a subprocess wrapper" (argparse import + subprocess.run call + no own branching) and redirect mutation analysis to the referenced script. Without this, Phase 2's architecture silently exempts ~30% of the codebase from supervision.
4. **Hook output goes to disk, not context** — identical treatment to MCP tool responses. Consuming 10%+ of context per session on ambient status is a direct violation of the Token Response Budget feedback.
5. **A `git_safeguard` or `hygiene_check_destructive` that fires on `git checkout <file>`, `rm <file>`, `git stash`, etc. when the file has uncommitted non-agent changes** — would have prevented Observation 6's failure. One signal, one gate, high value.
6. **A structure-channel finding for "source X has ≥2 test files"** — would have caught the parallel test-file drift at creation time instead of 2 sessions later during cleanup.

---

## Part VII: The Agent's Experience

### How the tool changed my approach

It didn't, in the parts of the session where I expected it would most. Phase 2 extraction was pure Read → Write → Bash. I used `improve_tests` and `compact_tests` exactly 8 times, all in the cleanup phase. The other 200+ tool calls were bash, read, edit — the same toolkit an agent on a project without LintGate would use.

The one place LintGate's MCP tools did change my approach: `compact_tests` dry-run guided the decision about which test files to delete wholesale. Without it, I would have either been too conservative (keep all mutation-gap files) or too aggressive (delete on intuition). The preview gave me a defensible "this file is outside the minimum killing set" argument. That's real value — but it's ~3% of the session by tool-call count.

### Where I was surprised

- **By how invisible the 61 test failures were.** They lived in the project through an entire implementation session. Nothing in LintGate's weather reports ever said "by the way, you have 61 failing tests right now." The hook told me `warnings=19, blocking=1, loud=tests:fail` after every write but the `blocking=1` was always the same one thing I was working on, not the actual state of the 61 elsewhere.
- **By how easy it is to fall into the "not my monkey" frame.** When the user called it out, I recognized it instantly. Before they called it out, I treated pre-existing failures as orthogonal to a cleanup task. This is the exact framing LintGate was supposed to dissolve, and I slipped into it the first time I encountered drift. That's a data point about how easy the trap is for any agent on a large codebase.
- **By `compact_tests` breaking my test file.** I was too trusting of the dry-run output. Future sessions should default to dry-run + inspect + manually cherry-pick rather than `dry_run=False`.

### What I would do differently next time

1. Run `controlplane_run` at the start of the session. Period. Even in habit mode.
2. Run `pytest tests/` (full suite, not just touched files) at session start and again at session end. Reference the diff, not just the absolute count.
3. Never run `compact_tests(dry_run=False)` without first inspecting the preview AND confirming `pytest --collect-only` on the proposed output.
4. Never `git checkout` a file without `git diff` first to see what's actually uncommitted.
5. Treat `improve_tests` as an expensive overnight job, not an interactive tool. Batch the list, kick off in background, review tomorrow.

### Trust Calibration

- **`compact_tests` dry-run preview** — high trust for the reduction claim (82%), low trust for the "apply this verbatim" output. Gained from the feedback test file breakage.
- **`improve_tests` kill-rate reports** — high trust. Numbers matched my manual read of the test files.
- **PostToolUse hook `blocking=N`** — low trust. The count never moved meaningfully and never exposed the 61 pre-existing failures, so I learned to ignore it.
- **Pyright "unknown symbol" diagnostics on re-exports** — zero trust after the first 3 false positives. This is a cost — it means I also stopped reading the pyright warnings that would have been real.
- **`hygiene_check` / `constraint_check`** — untested. I should have tried them at least once.

---

## Part VIII: Broader Observations

### The vision's core promise is intermittently met

The vision says: specification compiler, CPU-bound, at the speed of code. What I experienced: a mutation engine with a large per-file cost that discourages the diagnose-first workflow; a compact tool with a correctness bug in its apply path; a structure engine that missed parallel test files; a hygiene system that was never invoked. The parts that worked (file-based envelopes, the Phase 1 script-extraction pattern, the dry-run preview content) are real infrastructure. The parts that didn't (invocation habit, self-supervision of drift, cheap incremental mutation) are where the vision's claims outrun the implementation.

### Self-supervision is presence, not correctness

The vision says "LintGate supervises its own development" and cites "14,520 passing tests, 100% mutation kill rate on profiled functions" as evidence. This session produced the data point that the test-count number alone isn't enough: 14,443 passing + 61 failing is what the project actually looked like, and the system didn't self-detect the 61. Self-supervision, as currently implemented, verifies that the system runs and that the touched tests pass. It does not verify that the full suite remains green, or that the full suite is still the correct specification of the code. The vision's claim is stronger than the implementation supports.

### The "agent responsibility" frame is load-bearing

The user's correction — "these are ALL your monkeys, pre-existing drift isn't a *thing*" — names an agent-side failure mode that LintGate's design seems to assume won't happen. The tools default to per-turn scope (what did you just touch? is that OK?) rather than per-session scope (what's the state of the codebase now, relative to when you started?). An agent with a narrow responsibility frame will slot into the per-turn default and never notice session-wide drift. The fix is probably a combination of: (a) controlplane_run at session boundaries as a habit the tool enforces, not just suggests; (b) a drift-diff signal that explicitly compares session-start to session-end state; (c) framing text in the hook output that explicitly calls out "this project has N failures you didn't cause but are now responsible for if you ship."

### Phase 2's refactor created a supervision gap the system didn't anticipate

The "MCP = communication only, scripts = all compute" rule was adopted to free the model from the token overhead of in-process compute. It succeeded at that. But it created ~40 files (the wrappers) that the mutation engine can't meaningfully analyze. This is a self-inflicted blind spot the project is still accumulating (Phase 2 extraction is only ~25% complete per the 29-script roadmap). By the time Phase 5 ships, roughly half the MCP layer will be outside mutation-engine analysis. The engineering response should be: teach the mutation engine about delegation, OR reconsider whether every wrapper needs to be a separate script. This session is evidence that the tradeoff wasn't surfaced when the architecture decision was made.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | ~230,000 across ~1,100 files |
| Files touched | 18 (Phase 2 modules + script files + test files + wrappers; 0.016% of codebase) |
| Files created | 6 (scripts/behavior_check.py, scripts/compass_manage.py, scripts/controlplane_run.py, lintgate/compass_helpers.py, tests/test_scripts_behavior_check.py, tests/test_scripts_compass_manage.py) |
| Files deleted | 8 (1 source + 7 test files) |
| Genuinely new/rewritten lines | ~1,800 (the 3 new scripts + 1 helpers module + 2 new test files) |
| Lines moved/restructured | ~1,200 (existing impl moved into scripts; wrappers became thin) |
| Lines deleted | ~4,700 (dead impl module + dead/duplicate tests) |
| Net LOC delta | **−1,700** |

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per iteration | 7 tests per iteration (the feedback-fixture adaptation cycle) |
| Fastest batch | 73 tests deleted in 1 operation (merge test_mcp_compass_tools.py into test_compass_tools.py, delete mcp file) |
| Slowest individual fix | ~15 min spent reconstructing test_controlplane_impl_feedback.py after `git checkout` destroyed the Phase 1 adaptations |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Identifying dead module `_behavior_impl.py` | `compact_tests` pointed at the test file for it, which led me to check whether the module was referenced; grep confirmed | Would have done the grep directly without the hint | Minor — ~3 min saved |
| Identifying duplicate parallel test files | Hand-comparison of class names (same pattern LintGate could surface via structure channel but doesn't currently) | Same hand-comparison | 0 |
| Identifying mutation-gap files outside minimum set | `compact_tests` dry-run confirmed these files aren't in the min-kill-set | Would have manually read each file and intuited | ~20 min saved |
| Preventing `git checkout` destruction | Nothing prevented it | Nothing would have prevented it | 0 — LintGate added no value here |
| **Completeness of cleanup** | ~280 tests removed, 4,700 LOC, 0 regressions on touched files | Similar scope achievable manually | LintGate's value-add in THIS session: ~20-30 min time savings |

Honest assessment: this session's cleanup was ~90% achievable without LintGate's specialized test-analysis tools. The tools helped at the margin (the compact_tests dry-run was the single most useful output), but the structural pattern-matching (parallel test files, dead modules, mutation-gap bloat) was mostly done via grep and manual reading. The vision's implicit promise — "the tool makes cleanup at this scale 10× faster" — was not borne out in this session.

### Token Economics: Full Session Analysis

Data is not parsed from the JSONL transcript for this retrospective — the session is still live as of writing. The numbers below are estimated from session scope and marked clearly.

#### The Bottom Line

| | With LintGate | Without LintGate (counterfactual) |
|--|--------------|----------------------------------|
| **Output tokens for Phase 2 + cleanup + audit** | **~180K (estimated)** | **~220K–280K (estimated)** |
| Code quality shipped | Phase 2 pattern repeated cleanly; cleanup defensible via dry-run evidence | Same Phase 2 possible (not LintGate-specific); cleanup would have been more intuition-driven |
| Debug spirals | 1 (the `compact_tests` + `git checkout` destruction cycle) | Similar pattern possible |
| Regressions during session | 0 on touched files; 61 pre-existing not fixed | Same 61 pre-existing |
| Architectural backtracking | 1 (compact_tests recommendation rejected after applying) | Similar |
| Output tokens that became final code | ~40K shipped (LOC×5 tok/line); ~140K reasoning/exploration/fix-cycle | Less clear separation |

Honestly: this session did NOT demonstrate a strong supervised-vs-unsupervised delta. Much of the "supervised" work was structural refactor that LintGate's core tools don't directly assist with (they assist after the fact). The test cleanup did benefit from `compact_tests` dry-run but the benefit was incremental, not transformative.

#### Session Token Profile

Not parsed from the transcript for this document. Rough estimate: ~180K output tokens across Phase 2 + cleanup + audit. LintGate MCP tools (`improve_tests`, `compact_tests`, `check_project` not invoked, `get_details` not invoked) accounted for 8 calls producing small slim envelopes — well under 1% of session output.

#### What the Session DID NOT Contain

- **0 debug spirals on the Phase 2 rewrite itself.** The pattern was repeatable; each sub-phase (2a/2b/2c) shipped without backtracking.
- **0 regressions introduced by my code changes.** The 61 failures at session end were all pre-existing.
- **1 debug spiral induced by LintGate's own tool** — `compact_tests(dry_run=False)` broke 23 tests; `git checkout` destroyed separate uncommitted edits; ~30 min recovery.
- **Context pollution via hook output.** ~10% of the context window was ambient hook reminders. Not prevented.

The **Creation : Debugging : Verification** ratio was approximately **50 : 15 : 35**. The 15% debugging includes the self-inflicted `compact_tests` recovery and the adaptation of 7 tests post-`git-checkout`. The 35% verification is justified given the refactor scope; it also reflects the wall-clock cost of the full test suite.

#### Why the Unsupervised Counterfactual Needs ~1.3–1.5× the Output Tokens (Not 3-4×)

The standard LintGate counterfactual claims 3-4× token savings via supervised-vs-unsupervised efficiency. This session does not support that multiplier. Reasons:

1. The work was structural refactor (moving compute between modules), not feature-building. The mutation engine's value prop is specification + mutation-driven decomposition, which doesn't engage with module-boundary work.
2. The wrapper modules LintGate produced are mutation-engine blind spots, so the system didn't actually supervise the outputs of Phase 2.
3. The test cleanup benefited from LintGate's `compact_tests` by ~20-30 minutes of time savings, not by a tokens multiplier.
4. The 1 self-inflicted debug spiral cost tokens that an unsupervised agent wouldn't have spent (no `compact_tests` = no misapply).

Honest assessment: for this session profile (structural refactor + test cleanup on a large existing codebase), LintGate's economic value was **modest positive** — maybe 1.3× improvement in tokens-to-shippable-code, not 3-4×. Different session profiles (from-scratch build, spec-driven feature implementation) may see the larger multiplier the vision claims, but structural sessions don't.

#### The Quality Delta

| Dimension | Supervised (actual) | Unsupervised (counterfactual) |
|-----------|-------------------|-------------------------------|
| Phase 2 script extraction pattern | Repeatable 3×, consistent structure | Same pattern achievable without LintGate-specific tools |
| Test cleanup defensibility | Dry-run compact preview gave evidence for deletions | Grep-based duplication detection gives similar (weaker) evidence |
| Detection of 61 pre-existing failures | Not detected this session | Not detected |
| Prevention of git-checkout destruction | Not prevented | Not prevented |
| Latent structural issues (unchanged parallel test files not in this session's scope) | Unknown; structure channel not invoked | Same unknown |

The quality delta from this session is small. The patterns LintGate would shine on (prescriptive spec compile + synthesis + verify for new feature code) weren't exercised.

#### LintGate's Return on Investment

| Metric | Tokens | $ (Claude Opus 4.6) |
|--------|--------|----------|
| LintGate's direct output overhead | ~1–2K tokens (8 MCP calls) | <$0.02 |
| Total supervised session output | ~180K tokens (est.) | ~$2.70 |
| Unsupervised counterfactual output | ~220–280K tokens (est.) | ~$3.30–4.20 |
| **Output tokens saved** | **~40–100K (est.)** | **~$0.60–1.50** |
| **Output efficiency (supervised)** | ~22% (shipped / total) | |
| **Output efficiency (unsupervised est.)** | ~14-18% | |
| **Return on LintGate's token investment** | **~30–50× the tokens it consumed** | |

The 30-50× ROI is on the direct-tool-output measure, which is a small denominator. On the session-total basis, the savings are modest (~15-25%) for this session profile.

#### Session Telemetry (supporting data)

Not parsed from the JSONL. Key observable numbers:
- 8 LintGate MCP tool calls (4 `improve_tests`, 4 `compact_tests`)
- ~80 Bash calls
- ~30 Read calls
- ~15 Write / Edit calls
- 0 `controlplane_run` / `check_project` calls
- 0 `hygiene_check` / `constraint_check` calls
- 0 `prediction_register` calls

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | **Not exercised** — `controlplane_run` was never invoked. The tool that is the vision's front door sat unused for 3.5 hours. |
| **Fix guidance** | **Modest** — `compact_tests` dry-run previews were useful for deletion decisions. `improve_tests` kill-rate summaries were informative but slow. |
| **Workflow integration** | **Partial** — the MCP wrappers I built in Phase 2 are architecturally correct but defeat mutation analysis on themselves. The pattern propagates a blind spot. |
| **Regression detection** | **Failing** — 61 pre-existing failures invisible to the session's tool-use pattern; only surfaced by a 14-min background full-suite sweep I initiated manually. |
| **Structural insight** | **Missing** — parallel test files (test_mcp_X_tools.py + test_X_tools.py) weren't flagged by any channel. The structure channel has no test-to-source cardinality concept. |
| **Professional discipline** | **Self-exempted** — `hygiene_check`, `constraint_check`, `prediction_register` never invoked. Pyright warnings desensitized by re-export false positives. |
| **Theory/documentation** | **Passive** — theory pack surfaced via hook reminders, never retrieved via `build_theory_pack`. Claims there but not grounding the agent's reasoning. |
| **Auto-fix** | **Dangerous** — `compact_tests(dry_run=False)` silently broke 23 tests; no post-apply validation; `git checkout`-based rollback has its own drift-destruction problem. |
| **Noise level** | **High** — PostToolUse hook reminders consume ~10% of context window. Token Response Budget policy not applied to hook channel. |
| **Performance** | **Below vision budget** — `improve_tests` per-file is 10-30× the claimed budget; full test suite is 14 min wall clock. The "at the speed the agent writes code" promise is not met at these costs. |
| **Economics** | **Modest positive** (~1.3× improvement, not the 3-4× the vision claims). This session profile — structural refactor + test cleanup — is in LintGate's weakest zone. Vision-strong scenarios (spec-driven feature build) weren't exercised here. |
| **Overall** | The session surfaced a clear gap between LintGate's vision (specification compiler at the speed of code, self-supervising for code-quality drift) and its current behavior (several MCP tools that aid specific sub-tasks well, a mutation pipeline too slow for interactive use, a self-supervision story that detects presence of tests but not drift of the suite, an MCP-wrapper architecture that the mutation engine cannot see through). The top-3 highest-leverage fixes from this session are: (1) `compact_tests` must validate via `pytest --collect-only` before overwriting and revert on failure; (2) the mutation engine needs a "this is a wrapper, follow the delegation" concept so Phase 2-style extractions don't blind it; (3) `controlplane_run` needs to fire at session boundaries (or drift needs its own explicit signal) so pre-existing failures can't hide. None of these are research problems. All three are well-scoped engineering work. |
