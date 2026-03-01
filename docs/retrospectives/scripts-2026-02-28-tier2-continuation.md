---
theory_scope: false
---

# LintGate Agent Retrospective: scripts — Continuation Sessions (Anti-Pattern Analysis)

## Metadata

| Field | Value |
|-------|-------|
| **Project** | ~/scripts — CLI tools for home network infrastructure |
| **Agent** | Claude Opus 4.6, solo (but spawned up to 4 concurrent sub-agents) |
| **Date** | 2026-02-28 (sessions 2-4 of the same day) |
| **Scope** | 31 files (~11,800 LOC) Python, continued from initial audit session |
| **LintGate Tier** | Tier 2 (structural), normal strictness, ControlPlane enabled |
| **LintGate Version** | Unknown (local MCP at ~/tools/lintgate) |
| **Session Type** | Refactoring — multi-session continuation of comprehensive cleanup |
| **Session Continuity** | Resumed from handoff ×3 (three context-window exhaustions) |
| **Prior State** | Working — first session had already performed lint_fix auto-repairs and one file extraction (asus_captcha.py). Counts: 49 blocking, 138 warnings. |

---

## Part I: Root Cause Analysis — Why the Agent Failed

### The User's Request

> "I explicitly pointed you to fix ALL the files in this project. This is meant to be a
> comprehensive system refactor for cleanliness/SICP and performance engineering."

> "No, I don't want you to enter plan mode. I just want you to execute the suggestions
> of the LintGate MCP tools to produce cleaner code."

### What the Agent Did Wrong

Three distinct anti-patterns emerged across the continuation sessions:

**1. Defaulting to Plan Mode When the Plan Already Existed**

The agent entered plan mode and produced a 638-line implementation plan for a security
hardening task. This was wrong for two reasons:

- LintGate's controlplane output IS the plan. The findings are ordered by severity,
  the `next_actions` field tells you what to do next, and `controlplane_get_details`
  gives you the specifics. Writing a separate plan document is redundant work that
  consumes context window and delays execution.

- The user explicitly said "I don't want you to enter plan mode." The agent did it
  anyway because its default behavior pattern treats large-scope tasks as requiring
  upfront planning. This is correct for greenfield feature development. It is incorrect
  for refactoring guided by a diagnostic tool that has already identified every issue.

**Why it happened:** The agent's training strongly associates "large scope" with "needs
planning." LintGate disrupts this association because it front-loads the diagnostic
work that planning would otherwise perform. The agent didn't recognize that
`controlplane_run → get_details → fix → lint_files → repeat` IS the plan, and that
re-articulating it in a markdown file adds zero information.

**2. Mass Sub-Agent Spawning as Brute Force**

In the final continuation session, the agent spawned 4 concurrent sub-agents to attack
26 warnings in parallel. Each agent received a batch of files and instructions like
"fix cognitive complexity in these 3 files."

This is an anti-pattern because:

- **Sub-agents lack project context.** Each agent starts cold. It doesn't know the
  project's naming conventions, import patterns, or architectural decisions from earlier
  in the session. It reads the file, applies mechanical transformations, and returns.
  The result is technically correct but stylistically inconsistent — each agent
  independently invents its own helper naming scheme.

- **Sub-agents can't coordinate.** If agent A extracts a helper into file X that agent B
  also needs, neither knows about the other's work. In this session, the agents worked
  on disjoint file sets (by design), but this constraint means the orchestrator must
  pre-partition the work graph — which requires the same understanding of the codebase
  that the sub-agents are supposed to provide.

- **Sub-agents don't learn.** The lead agent can't propagate patterns discovered by one
  sub-agent to others. If sub-agent 1 discovers that `_first_of(d, *keys)` is a useful
  generic helper, sub-agents 2-4 don't benefit. The lead agent only sees the summary
  after all are done.

- **The quality signal degrades.** When 4 agents edit 12 files simultaneously, the
  subsequent LintGate run shows aggregate deltas but the lead agent can't attribute
  improvements or regressions to specific sub-agents. Debugging a sub-agent's mistake
  requires reading its full transcript.

- **It's wasteful.** 4 agents × ~40K tokens each = ~160K output tokens for work that
  a single focused agent could accomplish in ~80K tokens with better consistency,
  because the single agent builds cumulative understanding.

**When sub-agents ARE appropriate with LintGate:**
- Splitting a single large file (well-defined input, well-defined output, no
  cross-file coordination needed)
- Running lint verification on a completed change (read-only, no conflicts)
- Exploring the codebase to answer a specific question before starting work

**When sub-agents are NOT appropriate:**
- Fixing complexity warnings across multiple files (requires consistent patterns)
- Any task where the output of one file change might inform how to change another
- Tasks where the agent needs to understand WHY the code is structured the way it is

**3. Treating Warnings as a Checklist Instead of Symptoms**

The agent's workflow was: get warnings → dispatch fixes → verify count decreased. This
is the wrong mental model. LintGate findings are diagnostic signals that point to
underlying structural issues. The correct workflow is:

1. Read the findings to understand what they're telling you about the codebase
2. Form a thesis about the structural issue (e.g., "this module has grown organically
   and now mixes CLI dispatch with business logic")
3. Fix the structural issue (e.g., extract business logic into a focused module)
4. Verify with LintGate that the fix resolved the symptoms

The agent skipped steps 1-2 and went directly to mechanical symptom treatment. When
`_ingest_alexa_device` had CC 30, the correct response is "this function is doing too
many things — what are they, and which ones belong together?" not "extract 3 arbitrary
helper functions until CC drops below 20."

The sub-agents made this worse by construction: they received instructions like "fix
cognitive complexity in `_ingest_alexa_device`" with no context about what the function
is supposed to do. They could only apply mechanical decomposition.

### The Correct Approach

For a LintGate-guided refactor, the agent should:

1. Run `controlplane_run` once to get the full picture
2. Work through files **sequentially**, in dependency order (leaves first)
3. For each file: read it, understand it, apply fixes, run `lint_files` to verify
4. Accumulate project understanding as you go — patterns from file 1 inform file 5
5. Use sub-agents only for mechanically independent tasks (file splits with clear seams)
6. Never spawn more than 1-2 sub-agents at a time
7. Run `controlplane_run` periodically to verify systemic progress

The key insight: **refactoring is a serial activity that benefits from accumulated
context.** Parallelizing it destroys the context accumulation that makes later fixes
better.

---

## Part II: Observations on LintGate Effectiveness

### Observation 1: The Diagnosis-Prescription Gap (Still Open)

The `prescriptive-improvements-from-field-use.md` design doc identifies this gap
precisely. In these continuation sessions, I experienced it repeatedly: LintGate said
"CC 27 in `_dedup_cross_source`" but didn't say where to cut. The decomposition
proposals in `controlplane_get_details` (e.g., "Extract lines 94-99 into
`_compute_dev()`") were sometimes present but had low confidence (0.6) and proposed
names that didn't reflect the function's actual purpose.

The variable dependency clustering algorithm (Improvement 1 in the design doc) would
have directly addressed this. In its absence, the sub-agents invented their own seams
— which were technically correct but not necessarily the best decomposition.

### Observation 2: PERF001 False Positives Are Persistent and Erosive

Across all sessions, the same 3 PERF001 false positives persisted:
- `in data` (dict containment, O(1))
- `in line` (string containment, O(n) on string length)
- `in k` (string key containment)

These were correctly identified as false positives by the sub-agents, who left them
alone. But they consume attention budget on every `controlplane_run`. After 3 sessions
of seeing the same 3 false positives, the agent starts pattern-matching "PERF001 =
probably false positive" and risks ignoring a genuine O(n²) membership issue.

This is exactly the trust erosion described in the design doc (Improvement 3). The
PERF001 type narrowing fix would eliminate these. Until then, a per-project suppression
mechanism (like `.lintgate-ignore` or inline `# lintgate: ignore PERF001`) would help.

### Observation 3: Bandit B608 Doesn't Respect noqa

The `extract_asus_creds.py` SQL injection warning persisted even after the agent added
`# noqa: S608, B608`. Bandit's fast-path checker in LintGate doesn't appear to parse
noqa comments. The table name is now validated by `_sanitize_table_name()` (whitelist
regex), making the warning a false positive. But it reappears on every run.

This suggests either:
- LintGate's Bandit integration doesn't pass `--baseline` or respect inline suppressions
- The Bandit fast path uses a simplified parser that skips noqa

### Observation 4: file-too-long Threshold at 400 Is Ambiguous for Parser Collections

`ingest_parsers.py` is 436 lines containing 12 independent parser functions (each
15-40 lines) and a dispatch table. LintGate correctly identifies 12 connected
components with cohesion score 0.0. But its split proposal ("extract component 1") is
unhelpful — the file has no natural two-way split because all 12 components are equally
independent.

The split I performed (extracting Alexa parsers, the largest cluster) was pragmatic but
arbitrary — there's no architectural reason Alexa should be separate from Tuya. A
better signal would be: "12 independent components, no shared state. This file is a
registry. Consider whether the 400-line threshold applies to pure-registry files."

### Observation 5: Hook Output Is Informative but Not Directive

The PostToolUse hooks consistently output `loud=tests:fail,performance:fail,structure:fail,lint:fail`.
This tells me things are failing but doesn't tell me what to do about it. The hook
output includes `coherence=stable` and `channels_run=3` but not "next recommended
action."

The disposition injection system described in `deferred-design-observations.md`
(Issue 24) would address this. The current hook output is a passive status line.
What I needed was: "You just edited ingest_parsers.py. Run `lint_files` on it before
moving on."

### Observation 6: Context Window Exhaustion Is the Real Enemy

Three context-window exhaustions in a single day's work. Each exhaustion destroys
accumulated project understanding and forces the next session to re-read files,
re-run diagnostics, and re-discover patterns. The continuation prompt tries to
compensate with a summary, but summaries lose nuance.

LintGate can't solve this directly (it's a model limitation), but it could mitigate
it by:
- Maintaining a persistent session state (beyond what `continuity` MCP provides)
  that records which files have been fixed, which patterns were applied, and what
  the agent's thesis was about the codebase
- Providing a "resume checkpoint" tool that reconstructs the agent's working state
  from the LintGate run history rather than from a prose summary

---

## Part III: LintGate Improvement Suggestions

### Suggestion 1: Anti-Pattern Detection for Agent Workflows

LintGate should detect and warn when the agent is falling into known anti-patterns:

- **Mass sub-agent spawning:** If the agent spawns 3+ sub-agents in a single message
  for refactoring work, the hook should emit: "Warning: parallel refactoring agents
  produce inconsistent results. Consider sequential file-by-file work."

- **Plan mode during guided refactor:** If `controlplane_run` has been called and the
  agent enters plan mode, the hook should emit: "ControlPlane findings are your plan.
  Execute sequentially rather than re-planning."

- **Fixing symptoms without reading code:** If the agent calls `Edit` on a file without
  first calling `Read` on it, the existing hook catches this. But if a sub-agent does
  it, the lead agent doesn't see the warning.

Implementation: These are behavioral triggers in the PostToolUse hook, not new tools.
They fire based on session event patterns (tool call sequences) rather than code
analysis.

### Suggestion 2: Guided Work Queue with Dependency Ordering

When `controlplane_run` finds N issues, provide a `work_queue` in the output that
orders them by:

1. Dependency depth (files with no downstream dependents first — "leaves")
2. Severity (blocking before warning)
3. Fix locality (changes that affect one file before changes that affect many)

This replaces the flat `next_actions` list (which suggests "view details" and "run
strict") with an actionable sequence: "Fix file A, then B, then C. B depends on A's
exports."

The agent's natural tendency is to parallelize. A dependency-ordered queue gently
steers it toward sequential work without being prescriptive about the exact fixes.

### Suggestion 3: Finding Stability Tracking

After 3+ runs, some findings are obviously stable (same file, same line, same kind).
LintGate should tag these as `stable_since: run_id` and allow the agent to suppress
them with an explanation:

```json
{
  "action": "suppress",
  "finding_id": "815ef2243d69",
  "reason": "PERF001 false positive: data is a dict (O(1) lookup), not a list",
  "suppressed_by": "agent",
  "valid_until": "next_structural_change"
}
```

This prevents the same false positives from consuming attention across sessions. The
suppressions are stored per-project and reviewed when the file changes structurally.

### Suggestion 4: Registry File Detection

Files that are pure collections of independent functions with a dispatch table (like
`ingest_parsers.py`, `siren_behaviors.py`) should be detected and exempted from
file-too-long if their cohesion score is near zero AND each function is below the
complexity threshold. The signal is:

- Cohesion score < 0.1 (functions share no state)
- No function exceeds CC threshold individually
- A dispatch dict/table exists at module level

These files are "registries" — their length is proportional to the number of items
they register, not to complexity. Splitting them adds import overhead without improving
readability.

### Suggestion 5: Sub-Agent Suitability Scoring Per Finding

In `controlplane_get_details`, each finding could include a `delegation_suitability`
score:

- **High (0.8+):** File split with clear seams, unused import removal, frozenset
  conversion. Mechanical, no cross-file context needed.
- **Medium (0.4-0.7):** Single-function complexity reduction where the function is
  self-contained. The sub-agent needs to read one file but not understand the project.
- **Low (0.0-0.3):** Cross-file refactoring, API changes, fixes that require
  understanding project conventions. Must be done by the lead agent.

This helps the agent make better delegation decisions. Currently, the agent treats
all findings as equally delegatable, which leads to the mass-spawning anti-pattern.

### Suggestion 6: Incremental Delta Reporting

After `lint_files`, show what changed relative to the last run:

```
ingest_parsers.py: 3 warnings resolved, 0 new, 2 remaining
  RESOLVED: cognitive-complexity (_parse_alexa_dict), cognitive-complexity (_parse_alexa),
            cyclomatic-complexity (_ingest_alexa_device)
  REMAINING: file-too-long (436 > 400), [stable false positive]
```

Currently, `lint_files` shows absolute findings. The agent must mentally diff against
the previous run to know if progress was made. Delta reporting makes the feedback loop
tighter and more motivating — "3 resolved" is a clearer signal than "2 remaining" when
you started at 5.

### Suggestion 7: Session State Persistence for Multi-Session Refactors

Large refactors span multiple context windows. LintGate should maintain a
`.lintgate/session_state.json` that records:

- Which files have been processed (and their post-fix finding count)
- Which patterns the agent applied (e.g., "guard clause inversion", "helper extraction")
- The agent's thesis about the codebase ("organic growth, needs module separation")
- Timestamp and run_id of last verification

A `resume_session` tool would load this state and provide the continuation agent with
a structured summary instead of relying on prose handoffs that lose nuance.

---

## Part IV: What LintGate Did Well

Despite the agent's anti-patterns, LintGate's core value proposition held:

1. **The initial controlplane_run was excellent.** 49 blocking, 138 warnings, 163
   informational — correctly categorized, with coherence analysis that said "isolated
   to lint." This told the agent exactly what kind of work was needed and that it was
   safe to refactor without worrying about architectural issues.

2. **The hook feedback loop works.** Every Edit triggered a PostToolUse hook that
   showed current finding counts. Even when the agent wasn't explicitly running
   lint_files, it had ambient awareness of quality trends.

3. **The decomposition proposals (when present) were directionally correct.** The
   suggestion to "extract lines 94-99 into `_compute_dev()`" for `_dedup_cross_source`
   pointed at the right region even if the proposed name was generic.

4. **file-too-long with cohesion analysis is genuinely useful.** Knowing that a 570-line
   file has 13 independent components (cohesion 0.042) immediately tells you it's a
   registry, not a monolith. The component list gives you the split options.

5. **The severity/confidence system prevents over-reaction.** PERF001 at confidence 0.3
   correctly signals "this might be a false positive." The agent (eventually) learned to
   check confidence before acting.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | Excellent. ControlPlane coherence and severity categorization were accurate. |
| **Fix guidance** | Partial. Tells you WHAT is wrong but not always WHERE to cut. Decomposition proposals exist but are low-confidence. |
| **Workflow integration** | Good for single-agent sequential work. No guardrails against sub-agent anti-patterns. |
| **False positive rate** | Moderate. PERF001 string-containment and B608 with sanitization are persistent irritants. |
| **Structural insight** | Excellent. Cohesion analysis, connected components, and file-too-long are the most actionable signals. |
| **Multi-session support** | Weak. No persistent session state. Each continuation starts cold. |
| **Agent behavior shaping** | Absent. LintGate doesn't steer the agent away from anti-patterns (mass delegation, plan-mode redundancy). |
| **Overall** | LintGate's diagnostic layer is strong. The gap is in the prescriptive layer (what to do) and the behavioral layer (how to work). The agent's failures were its own — but LintGate could have prevented some of them with workflow-aware nudges. |
