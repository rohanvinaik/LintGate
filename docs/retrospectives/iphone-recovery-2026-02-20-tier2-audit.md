---
theory_scope: false
---

# LintGate Agent Retrospective: iphone-recovery — Audit + Implementation

## Metadata

| Field | Value |
|-------|-------|
| **Project** | iphone-recovery — toolkit for recovering activation-locked Apple devices via checkm8 exploit |
| **Agent** | Claude Opus 4.6, solo operator, single context window |
| **Date** | 2026-02-20 |
| **Scope** | 26 Python files, ~2,904 LOC (src/iphone_recovery/ + tests/) |
| **LintGate Tier** | Tier 2, normal strictness, ControlPlane used |
| **LintGate Version** | 0.2.0 |
| **Session Type** | Hybrid — audit then implementation of fixes |
| **Session Record(s)** | `~/.claude/projects/-Users-rohanvinaik-iphone-recovery/552a530f-475c-4001-83ce-b25769682a4f.jsonl` (primary audit, 2026-02-20T13:01 UTC), `~/.claude/projects/-Users-rohanvinaik-iphone-recovery/0e0aac5d-1757-4c59-9d52-35465bc8394c.jsonl` (Part XI config analysis, 2026-02-20T15:30 UTC) |
| **Session Continuity** | Fresh — no prior LintGate context in this session |
| **Prior State** | Working codebase, 82 tests documented (108 discovered at runtime), clean git status on main |

---

## Part I: Initial Diagnosis

**ControlPlane coherence state: "systemic"** — *"Multiple system failures: structure, lint, tests. This suggests a structural problem, not isolated issues."*

The "systemic" label was accurate in characterizing the distribution of findings but arguably over-weighted for this project's actual state. The codebase is functional, tested, and clean on `git status` — the "systemic" diagnosis sounds like an emergency when the reality is more like deferred maintenance. The single blocker is a legitimately complex function (`run_exploit`, CC=31) that orchestrates retry logic across two different exploit tools for different chip architectures — it has inherent complexity that may not decompose cleanly. The "systemic" label was driven by 3 channels reporting non-zero findings (structure, lint, tests), but the test channel's "failures" are merely 2 missing test files, not actual test failures.

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | 1 | cognitive-complexity: `run_exploit` CC=31 (limit: 15) |
| Warnings | 6 | version-missing-executable (pip-audit), 3x PERF001 (O(n²) membership tests), file-too-long (exploit.py, 560 lines), too-many-attributes (RecoveryStage, 14 attrs) |
| Informational | 31 | 15x B603 subprocess calls, 5x B107 hardcoded "alpine" password, 4x too-many-functions, 1x too-many-returns, 1x PERF008, 1x mypy no-untyped-def, 1x STRUCT004 low cohesion (tests), 2x missing_test (cli.py, device.py) |

### Hygiene Baseline

| Signal | Status |
|--------|--------|
| Virtual environment | active (uv) |
| Lockfile | fresh (uv.lock) |
| .python-version | present |
| Structure snapshot | cycles: 0, orphans: 0, largest module: exploit.py (345 LOC) |

### Theory Profile

Theory extraction scanned 7 markdown docs and extracted 134 claims across 5 of 6 facets. The `abstractions` facet was empty — the project has deep observational/process documentation (cognitive-reframing-observations.md, lintgate-token-analysis.md) but no formal abstraction catalog. Core theory, problem_solving, alignment, and anti_patterns were all well-populated. 1 enforceable rule was proposed (forbid `checkra1n` regex) which partially duplicates existing CLAUDE.md LINTGATE_FORBID_REGEX directives — the theory extractor didn't fully recognize the existing machine-enforceable rules already in CLAUDE.md.

---

## Part II: Observations During Audit

### Observation 1: The "systemic" Label Over-Dramatizes a Healthy Codebase

ControlPlane's coherence model labels any project with 3+ failing channels as "systemic." For iphone-recovery, the failing channels were lint (1 genuine blocker), structure (1 informational cohesion note), and tests (2 missing test files). The recommended action — "Step back and review the overall approach before fixing individual issues" — is disproportionate for a project with 82 passing tests, 0 import cycles, 0 orphans, and clean deps/git/behavior channels.

**What this reveals:** The coherence model's thresholds may be too coarse for small, well-tested projects. A project with 1 blocker and 2 missing test files shares the same "systemic" label as one with 20 blockers across multiple categories. A severity-weighted coherence score would be more informative.

### Observation 2: Bandit B603 Findings Are Structurally Unavoidable for This Domain

15 of 31 informational findings are B603 (subprocess without shell=True). This project exists specifically to shell out to USB exploit tools (gaster, iPwnder32, irecovery). These are not security vulnerabilities — they are the project's core purpose. Similarly, the 5 B107 "hardcoded password" findings for `alpine` are the well-known default iOS ramdisk SSH password, not a secret.

**What this reveals:** LintGate's bandit integration lacks domain-awareness for tool-orchestration projects. A suppression mechanism (per-project or per-file) for known-intentional patterns would reduce noise significantly. 20 of 31 informational findings (~65%) are expected and non-actionable for this project.

### Observation 3: The Performance Checker Found Genuine Issues

The 3 PERF001 warnings (O(n²) membership tests in loops) in boot.py:127, device.py:78, and recovery.py:181 are legitimate performance observations. While the data sets are small enough that the quadratic behavior doesn't matter in practice, the suggestions ("convert to a set before the loop") are correct and specific.

**What this reveals:** The performance checker operates at a useful granularity — it doesn't just say "this is slow," it identifies the specific container and suggests the exact fix. Even for cases where the performance impact is negligible, the pattern recognition is valuable for code quality.

### Observation 4: Recurrence Tracking Reveals Persistent Technical Debt

The pattern alerts showed that `file-too-long` on exploit.py has been flagged 25 times across runs, `cognitive-complexity` on `run_exploit` 4 times, and `too-many-functions` across 4 files 31 times. This recurrence data transforms individual findings into a narrative: these aren't new issues, they're persistent ones that previous sessions chose not to address.

**What this reveals:** Recurrence tracking is one of LintGate's most distinctive features. It converts point-in-time lint results into longitudinal data. A finding flagged once is noise; a finding flagged 25 times is technical debt with a history. This is information that no single lint run can provide.

> **Key insight:** The recurrence data creates an implicit priority system. The exploit.py file-too-long finding (25 occurrences) is clearly the most persistently deferred issue in the codebase, even though it's only a "warning" by severity.

### Observation 5: Theory Extraction is Rich but Theory-to-Rule Pipeline Has Gaps

The theory extractor found 134 claims, including detailed observations about anti-patterns like "serial constraint discovery" and "approach cycling." But it proposed only 1 enforceable rule (`forbid checkra1n`) despite CLAUDE.md already containing 3 `LINTGATE_FORBID_REGEX` and 1 `LINTGATE_REQUIRE_REGEX` directives. The extractor detected 0 existing rules even though 4 are present.

**What this reveals:** The theory extractor reads documentation broadly but the rule-detection pipeline may use a stricter format match than what's actually in CLAUDE.md. The existing `# LINTGATE_FORBID_REGEX:` comments use a format that the context_rule_checker apparently handles but the theory extractor's enforceable_rules scanner doesn't fully reconcile.

### Observation 6: The Tool Ran 10 Linters in 4.1 Seconds

The full ControlPlane run across 6 channels and 10 linters completed in 4.1 seconds, with lint taking 757ms and tests (checking for missing test files and impacted tests) taking 4.1 seconds. The structure check took 50ms. For a 2,892-LOC project, this is fast enough to be unobtrusive.

**What this reveals:** LintGate's performance is well within the "run after every edit" threshold. The tool would not be a friction source even in tight iteration loops.

### Observation 7: All Three PERF001 Warnings Are False Positives

Upon implementing fixes, I verified the 3 PERF001 warnings:
- **boot.py:127** — `name.lower() in actual_files` where `actual_files` is a `dict` comprehension (`{f.name.lower(): f for ...}`). Dict key lookup is O(1), not O(n). The performance checker classified this as "list membership" but it's a dict.
- **device.py:78** — `": " in line` where `line` is a `str` from `splitlines()`. This is a substring search on a string, not a membership test on a collection.
- **recovery.py:181** — Same pattern as device.py. String substring check, not list membership.

**What this reveals:** The PERF001 checker uses heuristic AST analysis that doesn't distinguish between `in` on a dict (O(1)), `in` on a string (substring search), and `in` on a list (O(n)). All three cases were confidently flagged at 0.85 confidence. This is a genuine gap in the performance checker's type inference.

> **Key insight:** 3 of 5 remaining warnings are false positives from a single checker. Combined with the RecoveryStage "too-many-attributes" false positive (it's an Enum, not a class), 4 of 5 post-fix warnings are non-actionable.

### Observation 8: The "too-many-attributes" Warning on RecoveryStage Is an Enum, Not a Class

LintGate's structure checker flags `RecoveryStage` (an `Enum` with 14 members) as having "14 attributes (limit: 10)" and suggests "this class has too many responsibilities." But enum members aren't attributes in the responsibility sense — they're values. A 14-member enum representing workflow stages is perfectly reasonable. The checker doesn't distinguish between `Enum` members and `dataclass`/class instance attributes.

**What this reveals:** The structure checker's attribute counter applies a blanket threshold to all class-like constructs without considering the class's metaclass (Enum, dataclass, etc.). Enums should be excluded from this check, or the threshold should be different.

### Observation 9: CC Reduction via Helper Extraction Worked Cleanly

Extracting three helpers from `run_exploit` (CC=31):
1. `_dispatch_exploit(tool, chip)` — tool dispatch (removes an if/else branch)
2. `_log_tool_output(log, tool, proc)` — output processing (removes the nested for/if/if)
3. `_check_exploit_success(proc, output)` — success verification (removes two conditional returns)

The result: `run_exploit` dropped from ~70 lines with CC=31 to ~40 lines, well under the CC=15 limit. Zero test failures after the change. The helpers are independently testable and reusable.

**What this reveals:** For functions with CC driven by multiple concerns in a loop body, extracting each concern into a named helper is the canonical fix. The naming itself improves readability: `_check_exploit_success(proc, output)` communicates intent more clearly than the inline `verify_pwned_state()` + fallback check it replaced.

### Observation 10: Module Decomposition Required Consumer Updates, Not Re-exports

The initial approach (bottom-of-file re-exports from bypass.py) triggered 7 E402 violations from ruff. The re-export imports had to be at the bottom to avoid a circular import (bypass.py imports ExploitResult from exploit.py), but ruff flags any import after non-import code.

Rather than suppressing with `# noqa: E402`, I updated all 5 consumer files to import directly from bypass.py. In a 26-file codebase with known consumers, direct import updates are cleaner than a re-export shim. The re-export pattern from the LintGate self-audit retrospective works better for larger codebases where consumer discovery is harder.

**What this reveals:** The right backward-compatibility strategy is codebase-size-dependent. For small projects, update consumers directly. For large projects, re-export and migrate incrementally.

---

## Part III: Professional Discipline Signals

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck | No | N/A | Clean environment from start |
| Secrets-in-diff | No | N/A | No diffs to check (audit only) |
| Supply-chain (pip-audit) | Skipped | N/A | pip-audit executable missing from PATH (warning flagged) |
| Type integrity (ty) | Skipped | N/A | ty not available |
| Security fast path (bandit) | Yes — 20 findings | Low actionability | All B603/B107 findings are domain-expected |
| Structure (file-too-long, CC, functions, attrs) | Yes — primary signal | Moderate | exploit.py and recovery.py are the structural hotspots |

---

## Part IV: Fix Patterns and Techniques

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| CC reduction via helper extraction | 1 (applied) | Extracted `_dispatch_exploit`, `_log_tool_output`, `_check_exploit_success` from `run_exploit` | CC > 15 in functions with multiple concerns in a loop |
| Module decomposition | 1 (applied) | Split exploit.py into exploit.py (253 lines) + bypass.py (334 lines) along exploit/SSH seam | File > 400 lines with natural domain boundary |
| Consumer import updates | 5 (applied) | Updated recovery.py, mcp_server.py, cli.py, test_exploit.py, and ruff auto-fixed mcp_server.py | After module split, when codebase is small enough to enumerate consumers |
| Auto-fix (ruff) | 1 (applied) | `ruff check --fix` for import sorting in mcp_server.py | After adding new import sources |
| False positive identification | 4 (documented) | Verified PERF001 (3 findings) and too-many-attributes (1 finding) as false positives | When checker confidence is < 1.0 and findings seem implausible |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | 1 | 0 | -1 (CC blocker resolved) |
| Warnings | 6 | 5 | -1 (file-too-long resolved) |
| Informational | 31 | 32 | +1 (new: missing tests for bypass.py) |
| ControlPlane coherence | systemic | systemic | Same (still driven by test/structure info-level findings) |
| Tests passing | 108 | 108 | 0 (zero regressions) |

### Files Changed

| Category | Count |
|----------|-------|
| Files modified | 6 (exploit.py, recovery.py, mcp_server.py, cli.py, test_exploit.py, pyproject.toml—via ruff) |
| Files created | 1 (bypass.py) |
| Net LOC delta | +12 (2,904 from 2,892 — 3 new helpers + module overhead) |

### Integration Verification

| System | Status | Evidence |
|--------|--------|----------|
| Full test suite | Pass | 108 passed, 0 failed (uv run pytest tests/ -q) |
| Ruff lint | Pass | 0 errors (uv run ruff check .) |
| ControlPlane | Pass | 0 blockers, all 6 channels completed |
| Import compatibility | Pass | All consumers updated to import from bypass.py directly |

### Reproducibility Notes

Both ControlPlane runs (before and after) were deterministic. The blocker resolution (CC=31 → under 15) and file-too-long resolution (560 lines → 253 lines) were confirmed by the second run. No flaky findings observed.

---

## Part VI: Process Assessment

### The LintGate Workflow

```
getting_started(path) → controlplane_run(path) →
  controlplane_get_details(run_id, severity=blocking) →
  controlplane_get_details(run_id, severity=warning) →
  controlplane_get_details(run_id, severity=informational) →
  extract_project_theory(path) →
  lint_status(path)
```

This was a pure read-only audit — the workflow was entirely diagnostic. The tool call sequence was logical and natural: orient, scan, drill down by severity, extract theory, check status.

### Prediction Accuracy

constraint_check was not used — this was an exploratory audit, not an implementation session.

### Constraints Proposed

No new constraints proposed during this session. The theory extractor's proposed `checkra1n` forbid rule overlaps with existing CLAUDE.md directives.

### What Works Well

1. **ControlPlane as a single entry point is excellent UX.** One tool call (`controlplane_run`) produced a complete 6-channel health assessment with severity counts, coherence diagnosis, and suggested next actions. No need to figure out which linters to run or in what order.
2. **Drill-down by severity is the right information architecture.** `controlplane_get_details` with severity filters let me look at the 1 blocker, then the 6 warnings, then all 31 informational findings separately. This prevents information overload while preserving access to full detail.
3. **Recurrence tracking adds a dimension that no other linting tool provides.** Seeing that exploit.py's file-too-long has been flagged 25 times across sessions tells a story that a single lint run never could. This is genuinely novel and useful.
4. **The `next_actions` suggestions in every response are well-calibrated.** After `controlplane_run`, the tool suggested drilling into blockers first, then applying safe repairs, then looking at warnings — exactly the right priority order.
5. **Theory extraction on 7 docs in a single call is impressive breadth.** 134 claims across 5 facets, with source traceability, from a single tool invocation.

### What Could Be Better

1. **The "systemic" coherence label needs severity weighting.** A project with 1 blocker and 2 missing test files shouldn't share the same urgency label as one with 20 blockers. Consider "localized" or "minor" states for projects with few, contained issues.
2. **PERF001 needs type inference.** All 3 PERF001 warnings were false positives — the checker flagged `in` on a dict (O(1) key lookup), `in` on a string (substring search), and another string substring check. It assumes all `in` targets are lists. Even basic AST heuristics (is the target a dict comprehension? is it a `str.splitlines()` result?) would eliminate these.
3. **The structure checker should exclude Enums from the "too-many-attributes" check.** An Enum with 14 workflow stage members is not a class with 14 responsibilities. Checking `issubclass(cls, Enum)` or detecting `(Enum)` in the class definition would fix this.
4. **Bandit B603/B107 findings need a suppression mechanism for tool-orchestration projects.** 20 of 32 informational findings are domain-expected subprocess calls and the well-known `alpine` SSH password. A project-level `known_safe_patterns` config would dramatically improve signal-to-noise.
5. **The theory extractor should reconcile its proposed rules against existing CLAUDE.md LINTGATE_ directives.** It proposed a `checkra1n` forbid rule that's already present in CLAUDE.md's machine-enforceable rules section, and reported 0 existing rules when 4 exist.

---

## Part VII: The Agent's Experience

### How the Tool Changed My Approach

Without LintGate, my audit of this codebase would have been: read a few files, run ruff, run pytest, glance at structure. LintGate compressed that into a single tool call that produced a structured, severity-ranked, channel-separated diagnostic. More importantly, it surfaced things I wouldn't have checked at all: recurrence patterns, theory profile completeness, test coverage gaps for specific modules, and performance anti-patterns.

During the implementation phase, the diagnostic-fix-verify loop (`controlplane_run` → fix → `controlplane_run`) was the right workflow. The second run confirmed the blocker was resolved and the file-too-long warning was gone, giving me definitive closure on the changes.

### Where I Was Surprised

**Recurrence tracking** remained the most surprising feature even after implementation. Seeing the exploit.py file-too-long recurrence at 20+ across sessions gave me a sense of its history as persistent deferred debt, now finally resolved.

**The false positive rate at the warning level surprised me negatively.** Going in, I assumed all 6 warnings would be actionable. After investigation, only 2 of 6 were real (CC blocker + file-too-long). The other 4 (3 PERF001 + 1 too-many-attributes on an Enum) were false positives. That's a 67% false-positive rate at the warning level, which is high enough to erode trust.

**The module decomposition worked perfectly on the first attempt.** Zero test failures after splitting 560 lines into two modules and updating 5 consumer files. The seam between exploit logic and SSH/bypass operations was clean and obvious.

**The test count discrepancy (82 documented vs 108 discovered)** is itself a signal. CLAUDE.md says "82 tests" but `pytest tests/ -q` found 108. This suggests tests were added in prior sessions without updating the project metadata — exactly the kind of documentation drift that LintGate's theory extractor could flag.

### What I Would Do Differently Next Time

1. Start with `lint_status` before `controlplane_run` to understand the linter inventory.
2. Verify PERF001 findings against actual types before attempting fixes — I nearly "fixed" correct code.
3. Check if Enum classes are flagged before accepting "too-many-attributes" warnings.
4. For module splits in small codebases, go directly to consumer updates rather than re-exports.

### Trust Calibration

**Gained trust in:** The cognitive-complexity checker (CC=31 was real and decomposed cleanly) and file-too-long detector (exploit.py genuinely needed splitting). Both findings led to meaningful improvements.

**Lost trust in:** The PERF001 performance checker. All 3 findings were false positives due to missing type awareness. The checker confidently (0.85) suggested fixes that would have been wrong (converting a dict to a set? converting a string to a set?). Confident wrong suggestions are worse than missed findings.

**Lost trust in:** The "too-many-attributes" check for Enum classes. 14 enum members is not 14 responsibilities.

**Moderate trust in:** The coherence model. It correctly stayed at "systemic" throughout because test/structure channels still had info-level findings, but the label's severity doesn't match the actual project health (0 blockers, all tests passing).

**Low trust in:** Bandit findings for this project domain. Unchanged from audit — all B603/B107 are structurally unavoidable.

---

## Part VIII: Broader Observations

### LintGate as an Orientation Tool for Fresh Agents

The most valuable use case I experienced was *orientation*. As a fresh agent encountering this codebase for the first time in this session, LintGate's ControlPlane gave me a structural understanding in seconds that would have taken multiple file reads and manual tool runs to assemble. I immediately knew: exploit.py is the largest and most complex module, recovery.py has a possibly over-packed dataclass, the test suite has gaps for cli.py and device.py, and the project has strong documentation but no formal abstraction catalog.

This "orientation acceleration" may be LintGate's highest-value use case for agentic workflows. Fresh context windows — whether from session resets, compaction, or new agent spawns — start with zero codebase knowledge. A single ControlPlane run provides structured knowledge that persists as context even when the tool isn't called again.

### The Noise-to-Signal Ratio Is Domain-Dependent

For a typical web application or library, LintGate's bandit integration would be high-signal — subprocess calls and hardcoded passwords would be genuine concerns. For a hardware exploit toolkit that exists to shell out to USB tools with known default passwords, the same findings are pure noise. This suggests that LintGate's value proposition varies significantly by project domain, and that per-project noise tuning is important for sustained use.

---

## Part IX: Economics

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | 2,904 lines across 26 files (from 2,892 across 25) |
| Files touched | 6 modified |
| Files created | 1 (bypass.py) |
| Genuinely new/rewritten lines | ~50 (3 helper functions + bypass.py module header + display name map) |
| Lines moved/restructured | ~310 (SSH/bypass code from exploit.py to bypass.py) |
| Net LOC delta | +12 |

### Time Allocation

| Activity | Approximate % | Category |
|----------|---------------|----------|
| Reading template and prior retrospective | 10% | Orientation |
| Running LintGate tools (8 calls) | 15% | Diagnosis |
| Reading source files | 15% | Diagnosis |
| Implementing CC fix + module split | 25% | Creation |
| Fixing test failures + lint errors | 10% | Debugging |
| Running validation (ruff + pytest + controlplane) | 10% | Verification |
| Writing/updating retrospective | 15% | Documentation |
| **Total** | **100%** | |

### LintGate Tool Calls

| Tool | Input Tokens (est.) | Output Tokens (est.) | Notes |
|------|---------------------|----------------------|-------|
| getting_started | ~200 | ~2,000 | Orientation + workflow |
| controlplane_run | ~200 | ~3,000 | Full 6-channel scan |
| controlplane_get_details (blocking) | ~200 | ~8,000 | Full evidence dump |
| controlplane_get_details (warning) | ~200 | ~8,000 | Includes recurrence data |
| controlplane_get_details (informational) | ~200 | ~12,000 | 31 findings with evidence |
| extract_project_theory | ~200 | ~15,000 | 134 claims from 7 docs |
| lint_status | ~200 | ~5,000 | Linter inventory + metrics |
| **Total LintGate overhead** | **~1,400** | **~53,000** | **~54,400 tokens** |

At Claude Opus 4.6 pricing ($15/M input, $75/M output):

| Component | Cost |
|-----------|------|
| LintGate input tokens (~1.4K) | ~$0.02 |
| LintGate output tokens (~53K) | ~$3.98 |
| **LintGate-specific cost** | **~$4.00** |

### Counterfactual: Without LintGate

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | 1 controlplane_run → complete 6-channel picture in 4.1s | Manual: run ruff, pytest, mypy separately, manually inspect structure | ~10 min saved on diagnosis |
| Recurrence data | 25 prior occurrences of file-too-long visible | No history — every run is a fresh snapshot | Qualitatively different insight |
| Theory profile | 134 claims extracted, coverage gaps identified | Would need to manually read 7 docs | ~20 min saved |
| Orientation speed | Immediate structural understanding of codebase | Multiple file reads + manual inference | Significant for fresh agents |

### Return on Investment

| Metric | Value |
|--------|-------|
| LintGate overhead (tokens) | ~54,400 tokens |
| LintGate overhead (cost) | ~$4.00 |
| Time saved vs. manual audit | ~30 min (conservative) |
| Unique insights not available otherwise | Recurrence tracking, theory profile, coherence diagnosis |
| **Verdict** | Worth the overhead for orientation; ROI increases with repeated use (recurrence data accumulates) |

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Good. The CC=31 blocker was a legitimate finding that decomposed cleanly. However, 4 of 5 remaining warnings are false positives (3 PERF001 + 1 Enum misclassification). |
| **Fix guidance** | Mixed. CC suggestions ("extract helper functions") were correct but generic. PERF001 suggestions ("convert to set") were confident but wrong for dicts and strings. |
| **Workflow integration** | Excellent. `controlplane_run` → `controlplane_get_details` → implement → `controlplane_run` (verify) is a clean diagnostic-fix-verify loop. |
| **Regression detection** | Strong. Post-fix controlplane_run confirmed blocker resolved and file-too-long warning gone. 108 tests passed. |
| **Structural insight** | Good. File-too-long and cognitive-complexity pointed to real concerns. But too-many-attributes on an Enum and PERF001 on dicts/strings are noise. |
| **Professional discipline** | Mixed. Clean channels (deps, git, behavior) were genuinely clean. Bandit B603/B107 are domain noise. |
| **Theory/documentation** | Good. 134 claims across 5 facets with source traceability. Rule reconciliation gap persists. |
| **Auto-fix** | Minimal. ruff `--fix` handled import sorting. LintGate's safe repairs (test skeletons) were available but not applied this session. |
| **Noise level** | High. Post-fix: 4 of 5 warnings are false positives. Of 32 informational, ~20 are domain-expected bandit. True signal-to-noise ratio is roughly 1:4 at the warning level. |
| **Economics** | Good. Resolved the only blocker and reduced file size below threshold with zero regressions. ControlPlane's verify loop caught the resolution cleanly. |
| **Overall** | LintGate excels at orientation and diagnostic framing but has a significant false-positive problem at the warning level for this project. The single blocker was real and the fix was straightforward. The module decomposition guidance was implicit (file-too-long + CC both pointed at exploit.py) which is useful. The PERF001 checker needs type-awareness, and the structure checker needs Enum-awareness. The recurrence tracking and coherence framing remain the standout features. |

---

## Part X: Pipeline Execution — LintGate in the Field

### Context

After the code audit and fixes in Parts I–IX, we ran the full recovery pipeline on a real device: **iPhone 4 (GSM)** — iPhone3,1, CPID 0x8930, A4 chip, running iOS 7.1.2 (11D257), activation-locked. Two phones were connected simultaneously (iPhone 4 + iPhone SE 1st gen).

### Observation 11: LintGate's `constraint_check` Is Not a Pipeline Orchestrator

The user explicitly requested: *"Use LintGate to guide you through this"* and *"It's not about code quality. LintGate should also guide you through the process of running the pipeline."* This revealed a gap between user expectation and tool capability. `constraint_check` verified code constraints (img3 format validation, correct file paths) but could not guide operational decisions like "which USB device is targeted" or "is iBSS needed before iBEC for this chip."

**What this reveals:** Users may project orchestration capabilities onto a tool that provides diagnostic framing. LintGate's strength is code health, not runtime process management. A future "pipeline constraint" mode that encodes operational sequences (exploit → boot → bypass) would bridge this gap.

### Observation 12: Live Testing Exposed a Bug That Static Analysis Could Not

`boot_32bit()` had three compounding bugs:
1. **Missing iBSS send for A4** — iPwnder32 `-p` only sends the exploit payload for s5l8930x (A4). For s5l895Xx (A6/A6X), it auto-sends a pwned iBSS. The code assumed all 32-bit chips got iBSS from iPwnder32.
2. **Missing `getenv ramdisk-delay`** — Legacy-iOS-Kit issues this command before `ramdisk` to ensure timing alignment. Our code skipped it.
3. **Missing Recovery mode wait** — Legacy-iOS-Kit's `device_find_mode Recovery` polls for up to 4 seconds after iBEC. Our code had a fixed 2-second sleep.

None of these would be caught by any linter — they're semantic correctness issues in a hardware interaction protocol. The bugs were found by comparing against Legacy-iOS-Kit's `restore.sh` (the reference implementation) after repeated boot failures on real hardware.

**What this reveals:** For hardware-interfacing code, integration tests against real devices (or faithful simulators) are the only path to correctness. LintGate can catch structural issues, but protocol correctness requires runtime validation.

### Observation 13: The Fix Was Clean and Immediately Testable

After identifying the three bugs, the fix to `boot_32bit()` was:
- Added `chip` parameter (default `"A6"` for backward compatibility)
- Added iBSS send for `chip == "A4"` with 2s sleep
- Added `_wait_for_recovery_mode()` helper (polls irecovery for 4s)
- Added `getenv ramdisk-delay` before `ramdisk` command
- Fixed timings: sleep 3 after iBEC (was 2), sleep 2 after ramdisk (was 1)

Test count went from 20 to 24 in test_boot.py (112 total, all passing). The fix was validated by lint + tests in under 30 seconds. LintGate's prior CC reduction of `run_exploit` (Part IX) kept the exploit module clean enough that adding the boot fix didn't create new structural concerns.

### Observation 14: Multiple Connected Devices Create Silent Targeting Errors

With both iPhone 4 and iPhone SE connected, Legacy-iOS-Kit's `restore.sh --sshrd` targeted the iPhone SE (A9 → gaster) instead of the iPhone 4 (A4 → iPwnder32). irecovery and gaster both silently pick up the first USB device they find. This is not a LintGate concern per se, but our pipeline code (`detect_device()`) should handle multi-device disambiguation — currently it returns whichever device irecovery finds first.

### Pipeline Execution Summary

| Step | Tool | Result |
|------|------|--------|
| DFU detection | irecovery -q | iPhone3,1 in DFU, CPID 0x8930 |
| Exploit | iPwnder32 -p (limera1n) | Success — pwned DFU confirmed |
| Ramdisk build | xpwntool + iBoot32Patcher + hfsplus | Built from iOS 6.1.3 IPSW (10B329) firmware keys |
| Boot (our code) | boot_32bit() — multiple attempts | **Failed** — missing iBSS, ramdisk-delay, Recovery wait |
| Boot (Legacy-iOS-Kit) | restore.sh --sshrd | **Succeeded** — correct boot chain |
| SSH access | iproxy 6414 22 + ssh root@localhost | Connected to ramdisk shell |
| Activation bypass | `mv Setup.app Setup.app.bak` + reboot | **Success** — device boots to home screen |
| Final state | iOS 7.1.2 bypassed | Usable device, activation screen removed |

### Updated Trust Calibration

**Gained trust in:** LintGate for pre-flight code health. The CC reduction in Part IX meant `exploit.py` was clean and well-structured when we needed to debug boot failures — the module boundaries made it obvious that the bug was in boot.py, not exploit.py.

**Confirmed gap:** LintGate cannot guide operational pipelines. The user's expectation that it could orchestrate the exploit→boot→bypass sequence was reasonable but unsupported. `constraint_check` verified code constraints, not runtime protocol correctness.

**New insight:** The strongest LintGate signal during pipeline execution was *absence of noise*. After the Part II fixes, zero blockers and clean lint meant I could focus entirely on the hardware interaction bug without wondering if code quality issues were contributing to failures.

---

## Part XI: The Missing Config File — Post-Session Analysis

*Added 2026-02-20, later session (Claude Opus 4.6, different context window)*

### The Problem

When a new session opened on iphone-recovery, `getting_started()` reported `config_found: false` — no `.claude/lintgate.yaml` existed despite the previous session running 545+ LintGate tool calls, completing a full audit, implementing fixes, splitting modules, and writing this retrospective. The config was never created.

### Root Cause Analysis

The gap is architectural, not a single agent mistake. Three factors compounded:

**1. No tool creates the config.** LintGate has 35 MCP tools. None of them create or scaffold `lintgate.yaml`. The `getting_started` tool *reports* the config is missing and provides a `setup_hint` with a minimal snippet (`controlplane: enabled: true`), but it's a diagnostic message, not an action. `bootstrap_context_files` generates `CLAUDE.md` and `AGENTS.md` — not `lintgate.yaml`. `integrate.sh` handles hook/MCP setup — not project-level config. There is no `init`, `create_config`, or `scaffold` tool in the 35-tool inventory.

**2. The setup hint is passive and easily buried.** The hint appears as a nested field inside `getting_started`'s JSON response: `config_state: "no_config"` with a `setup_hint` string. In a session focused on audit and code fixes, this informational signal competes with actionable findings (blockers, warnings, coherence state). The previous session's agent correctly noted the missing config in its orientation but then moved to `controlplane_run` — which works without config — and never circled back. The hint was acknowledged and forgotten.

**3. The "works without config" design undermines config creation urgency.** LintGate's zero-config philosophy means everything functions on defaults. The previous session ran a full ControlPlane audit, theory extraction, and implementation cycle without a config file. The config's absence caused no errors, no degraded functionality, no warnings during operation — only a one-time note during `getting_started`. This is good UX for onboarding (low friction) but bad for config adoption (no forcing function).

### What the Config Should Have Contained

Based on the retrospective's own observations, the previous session had all the information needed to generate a proper config:

| Observation | Config Implication | Section Written |
|---|---|---|
| Obs. 2: B603/B107 are domain noise (20 of 32 findings) | `severity_overrides: {B603: informational, B107: informational}` | Part II |
| Obs. 9: CC reduction targeted exploit.py | `pipeline_critical_paths` should include exploit.py, boot.py | Part IV |
| Obs. 12: boot.py had compounding runtime bugs | boot.py and recovery.py are critical paths | Part X |
| Obs. 1: "systemic" label over-dramatized | Behavior thresholds could be tuned | Part II |
| General: Theory extraction found 134 claims | Inquiry features (theory_grounded_signals, living_context) should be enabled | Part VI |

The session *documented* all the insights that should have informed the config but never *materialized* them into the config file. The retrospective became a dead-letter — valuable analysis that didn't flow back into tooling.

### The Broader Pattern: Diagnostic-Rich, Action-Poor Handoff

This is a variant of what the retrospective itself identified in Observation 1 (the "systemic" label) — LintGate is excellent at producing structured diagnostics but has a gap in translating diagnostics into persistent configuration. The information flows one way:

```
Project → LintGate (diagnosis) → Agent (understanding) → Retrospective (documentation)
                                                          ↑ dead end
                                                          Config file never written
```

The missing link is `Retrospective → Config`. The previous agent wrote 426 lines of analysis and never wrote 20 lines of YAML.

### Recommendations

1. **LintGate should offer a `scaffold_config` tool** that generates a project-specific `lintgate.yaml` from observed signals (domain-specific suppressions, critical paths from file-too-long/CC findings, channel configuration). This is the `bootstrap_context_files` equivalent for the config file.

2. **`getting_started` should escalate "no config" from hint to suggested action** — e.g., returning a `suggested_config` block in `next_actions` with a ready-to-write YAML blob, not just a setup hint string.

3. **`controlplane_run` should note config absence in its summary** — not just `getting_started`. If the agent skips `getting_started` and goes straight to `controlplane_run` (a reasonable shortcut for repeat sessions), the config gap is invisible.

4. **Retrospectives should have a "config changes" section** in the template that forces the author to either write config updates or explicitly document "no config changes needed." This creates a structural forcing function against the diagnostic-rich/action-poor pattern.

### Config Created This Session

```yaml
pipeline_critical_paths:
  - "src/iphone_recovery/exploit.py"
  - "src/iphone_recovery/boot.py"
  - "src/iphone_recovery/recovery.py"
  - "src/iphone_recovery/bypass.py"

severity_overrides:
  B603: informational
  B107: informational

controlplane:
  enabled: true
  channels:
    behavior:
      enabled: true
      thresholds:
        approach_cycling_count: 3
        failure_amnesia_lookback: 30
  inquiry:
    theory_grounded_signals: true
    prediction_tracking: true
    theory_coherence_check: true
    living_context: true
    session_gate: true
```

All settings derived directly from observations documented in this retrospective's Parts II, IV, and X.
