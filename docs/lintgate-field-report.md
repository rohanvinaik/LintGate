# LintGate Field Report: Greenfield Project Bootstrap

> **Historical snapshot** — this document captures the state of LintGate as of 2026-02-17. Current metrics and tool counts may differ.

**Date:** 2026-02-17
**Agent:** Claude Opus 4.6 via Claude Code CLI
**Project:** iphone-recovery (Python, src layout, Click CLI, pytest)
**Task:** Set up a brand-new Python project from empty directory to private GitHub repo,
using LintGate at every step, and evaluate the experience.

---

## Executive Summary

LintGate is a tiered linting and project supervision MCP tool designed to bridge the
gap between LLM-generated code and professional-grade Python projects. I used it to
bootstrap a greenfield project from scratch, exercising 10 of its 17 exposed tools
across dependency management, multi-tier linting, context file generation, cross-channel
supervision, and tool/context auditing.

**Verdict:** LintGate is a genuinely useful supervisory layer that catches real issues
at appropriate severity levels. Its main strength is *diagnostic breadth* — it covers
ground that no single linter does. Its main weakness is *remediation depth* — it tells
you what's wrong but rarely fixes it, which partially undermines its purpose as an
LLM-agent companion.

---

## 1. Token Efficiency

This is LintGate's most significant operational issue for LLM integration.

### The Problem

A single `lint_project` call at Tier 3 returned **~8,500 tokens** of JSON. The bulk
of that was 26 informational findings, each with full evidence objects, CWE links,
suggestion arrays, and severity metadata. For an LLM agent operating within a context
window, this is expensive — especially when the actionable content ("you have 0
blocking issues, 26 informational") could be conveyed in ~50 tokens.

### Measurements from This Session

| Tool Call | Approximate Response Tokens | Actionable Content |
|-----------|---------------------------|-------------------|
| `dep_health_check` (initial) | ~1,200 | 3 issues, 3 suggestions |
| `dep_sync` (create + lock) | ~400 | 2 actions, health before/after |
| `lint_project` T0 | ~2,800 | 7 warnings, all fixable |
| `lint_project` T1 | ~800 | Clean pass |
| `lint_project` T2 | ~1,200 | Clean pass |
| `lint_project` T3 | ~8,500 | 26 informational, 0 blocking |
| `bootstrap_context_files` | ~2,500 | 3 files written |
| `controlplane_run` | ~1,800 | 3 pass, 1 fail (2 findings) |
| `audit_tool_versions` | ~2,000 | 6 tools, all OK |
| `audit_context_health` | ~2,200 | 2 files, warnings on path refs |

**Total LintGate token consumption: ~23,500 tokens** for what amounted to "fix 7
style issues, create a venv, and add a .python-version file."

### What Would Help

1. **A `compact` or `summary` output mode.** When the agent just needs pass/fail +
   actionable items, the full evidence/CWE/suggestion payloads are waste. A two-tier
   response (summary first, details on request) would cut token usage by 60-70%.

2. **Suppress informational in lower output modes.** The 26 T3 informational findings
   consumed ~5,000 tokens. None were actionable. An agent should be able to request
   "blocking + warnings only" and drill into informational separately.

3. **Batch the recurrence tracking into a separate call.** Every lint response includes
   a `recurrence` object tracking how many times each signature has appeared across
   runs. This is useful metadata for trend analysis, but it bloats every lint response
   even when the agent doesn't need it.

### The Structural Issue

LintGate's JSON responses are designed for *completeness* (every finding with full
context), but LLM agents need responses designed for *decision-making* (what do I
need to act on, and what can I ignore). These are fundamentally different output
philosophies. The best MCP tools I've used offer both: a concise decision-oriented
summary, with a way to drill into details when needed.

---

## 2. Thoroughness and Coverage

### What's Covered (Impressive)

LintGate runs 12 linters across 4 tiers:

- **T0:** ruff check, ruff format — fast syntax/style baseline
- **T1:** import checker, context rule checker, redefinition checker, version checker
- **T2:** mypy, complexity checker (radon), structure checker
- **T3:** bandit (security), architecture checker, dead code checker (vulture)

This is a genuinely professional linting stack. Most Python projects in production
use 2-3 of these tools; LintGate orchestrates all 12 with appropriate tiering.

The `dep_health_check` covers:
- Virtual environment presence
- Lockfile presence and freshness
- `.python-version` file
- `requires-python` in manifest
- Dependency churn tracking
- Conflicting package manager detection

The `controlplane_run` cross-checks:
- Lint status
- Test coverage (missing test files)
- Dependency health
- Git state

### What's Missing

1. **No YAML/TOML validation.** This project uses YAML for inventory files. LintGate
   doesn't lint non-Python files, even ones that are clearly part of the project's
   data layer.

2. **No Dockerfile/CI linting.** If I had added a Dockerfile or GitHub Actions workflow,
   LintGate wouldn't have anything to say about them.

3. **No test *execution* in ControlPlane.** The test channel checks for missing test
   files but doesn't actually run tests. It could — and the "coherence" framing would
   be much more powerful if a lint pass + test failure triggered the "correlated" state.

4. **No requirements.txt / pip-tools awareness.** dep_health_check is opinionated toward
   uv/poetry. Many real-world projects still use pip + requirements.txt. The tool should
   at minimum acknowledge these rather than only looking for uv.lock/poetry.lock.

---

## 3. Elegance and Design

### What's Elegant

**The tiered model is the right abstraction.** Instead of dumping 200 findings at once,
the tier system lets an agent (or human) fix formatting first (T0, 14ms), then structural
issues (T1, 20ms), then type/complexity (T2, 970ms), then security/architecture (T3,
340ms). This matches how experienced developers actually work — you don't run bandit
until ruff is clean.

**dep_sync's before/after health reporting** is exactly right. One call returned:
```
health_before: { errors: 2, warnings: 1 }
health_after:  { errors: 0, warnings: 0 }
```
This gives the agent a clear signal that the action worked, without requiring a
follow-up `dep_health_check`.

**ControlPlane's coherence states** (`pass`, `isolated`, `correlated`, `systemic`) are
a genuinely novel framing. Instead of "here are issues from 4 independent tools," it
synthesizes: "Issue isolated to tests. deps, git, lint confirm no problems in their
domains." This is the kind of cross-cutting analysis that individual tools can't do
and that LLM agents particularly benefit from.

### What's Not Elegant

**Bootstrap generates self-inconsistent artifacts.** The `bootstrap_context_files` tool
wrote a CLAUDE.md referencing `.claude/lintgate.yaml` — a file that doesn't exist and
that bootstrap didn't create. Then `audit_context_health` correctly flagged this as a
dead path reference. Two LintGate tools disagreeing with each other on first use is
not a good first impression.

It also flagged `.claude/rules/theory.md` as a dead reference, but that file *does*
exist — bootstrap created it moments earlier. This appears to be a path resolution
bug in the context health checker.

**Theory extraction on a greenfield project produces noise.** The generated
`.claude/rules/theory.md` is 31 lines of "No strong signal extracted for this facet
yet" repeated 6 times. For a new project with no markdown documentation, this is
expected but unhelpful. Bootstrap should either skip theory.md for projects with no
docs, or generate a minimal skeleton seeded from pyproject.toml metadata (which it
can already read — it got the project name and description correct in CLAUDE.md).

---

## 4. Integration with How LLM Agents Actually Work

This is the most important dimension, because LintGate's stated purpose is to bridge
the gap for non-CS users working with LLM coding agents.

### The Agent Loop Pattern

LLM coding agents work in a loop:
1. Understand the current state
2. Decide what to do
3. Take an action
4. Verify the result
5. Repeat

LintGate's tools map well to steps 1 and 4 (diagnosis and verification) but poorly to
step 3 (action). Every finding says "here's what's wrong" but the agent must then
independently figure out how to fix it. This creates unnecessary round-trips.

### Specific Integration Gaps

**No auto-fix capability.** LintGate's T0 report told me 7 issues were auto-fixable
and even suggested `ruff check --fix`. But it didn't offer a tool to do it. I had to:
1. Read each file manually
2. Understand each warning
3. Make 5 separate Edit tool calls
4. Re-run lint to verify

A `lint_fix` tool that applies safe auto-fixes (ruff's `--fix`, import sorting, format
corrections) would eliminate this entire cycle. This is the single highest-impact
improvement for agent integration.

**dep_sync doesn't complete the job.** It created a venv and lockfile but didn't install
the project. I had to manually run `uv pip install -e '.[dev]'`. For an agent, "sync"
should mean "ready to execute code," not "lockfile exists." The tool should either do
the install or explicitly say "run this next" in a structured way the agent can execute.

**No structured "next action" field.** When a tool finds issues, the response includes
human-readable suggestions but no structured `next_action` field that an agent can
directly execute. Compare:

Current: `"suggestion": "Run uv lock to generate a lockfile"`
Better: `"next_action": {"tool": "dep_sync", "params": {"lock": true}}`

This would let agents chain LintGate calls without parsing English suggestions.

**Tool version audit checks system Python, not project venv.** `audit_tool_versions`
found ruff at `/Users/.../miniconda3/bin/ruff` (v0.14.10) instead of the `.venv/bin/ruff`
(v0.15.1) I had just installed. For an agent that just set up a venv, this is confusing —
the versions don't match what was installed, and any drift warnings would be against the
wrong baseline.

### What Would Make This Seamless for Agents

1. **`lint_fix(tier=0, dry_run=false)`** — Apply safe auto-fixes, return diff summary.
2. **`dep_sync(install=true)`** — Full sync including pip install of the project.
3. **Structured next-actions in every response** — Machine-readable, not English prose.
4. **Compact response mode** — Pass/fail + actionable items only, details on demand.
5. **Venv-aware tool resolution** — Check `.venv/bin/` first when a venv exists.

---

## 5. Effective Use of Full Tool Potential

### Tools I Used (10/17)

| Tool | Used For |
|------|----------|
| `lint_status` | Initial reconnaissance |
| `dep_health_check` | Pre-setup diagnosis |
| `dep_sync` | Venv + lockfile creation |
| `lint_project` | 4 runs across T0-T3 |
| `bootstrap_context_files` | CLAUDE.md / AGENTS.md generation |
| `controlplane_run` | Cross-channel supervision |
| `audit_tool_versions` | Tool compatibility check |
| `audit_context_health` | Context file validation |
| `context_guidance` | Initial context scan |
| `lint_files` | (indirectly via lint_project) |

### Tools I Didn't Use (7/17)

| Tool | Why Not |
|------|---------|
| `extract_project_theory` | No markdown docs to extract from |
| `build_theory_pack` | Same — greenfield has no theory |
| `get_theory_context` | No theory to retrieve |
| `extract_theory_constraints` | No prose directives to convert |
| `controlplane_report_repair` | No repairs were applied via ControlPlane |
| `controlplane_agent_feedback` | No disagreements to register |
| `controlplane_test_skeleton` | Chose to write tests manually |

The unused tools are mostly the "theory" family, which makes sense — they're designed
for established projects with documentation, not greenfield bootstrapping. The
ControlPlane feedback tools (`report_repair`, `agent_feedback`) are designed for an
ongoing development loop, not initial setup.

### Tool Interaction Patterns That Worked

The natural workflow I discovered was:

```
dep_health_check → dep_sync → dep_health_check (verify)
    ↓
lint_project T0 → fix issues → lint_project T0 (verify)
    ↓
lint_project T1 → lint_project T2 → lint_project T3
    ↓
bootstrap_context_files → audit_context_health (verify)
    ↓
controlplane_run (final cross-check)
```

This is a reasonable workflow but it was **entirely self-discovered**. LintGate doesn't
document or suggest this progression. A `bootstrap_workflow` or `setup_guide` tool that
walks an agent through the recommended sequence for a new project would be valuable —
especially for the target user who doesn't know what steps a professional setup requires.

---

## 6. For the Settlers: Practical Recommendations

### If You're Building on LintGate

1. **Start with `dep_health_check`, not `lint_project`.** Dependencies must be right
   before linting works reliably. LintGate doesn't enforce this ordering.

2. **Run tiers sequentially, not in parallel.** I initially ran T1/T2/T3 in parallel,
   which works but means you can't use lower-tier fixes to reduce higher-tier noise.
   Sequential T0→T1→T2→T3 is better.

3. **Expect to supplement `dep_sync` manually.** After `dep_sync(create_venv=true,
   lock=true)`, you still need to `uv pip install -e '.[dev]'` yourself.

4. **Review bootstrap output immediately.** The generated CLAUDE.md and AGENTS.md are
   reasonable templates but contain dead references that need manual cleanup. Run
   `audit_context_health` right after bootstrap and fix what it finds.

5. **T3 informational findings are noise for greenfield projects.** Bandit's B101
   (assert in tests), B603/B607 (subprocess with hardcoded args), and vulture's
   false positives on Click commands and enum values are expected. Don't chase them.

### If You're Developing LintGate

1. **Priority 1: Add `lint_fix`.** This is the single biggest gap. The agent already
   knows what's wrong, LintGate already knows the fixes are safe — just apply them.

2. **Priority 2: Compact output mode.** Cut token consumption by 60%+ for the common
   case where the agent just needs pass/fail + action items.

3. **Priority 3: Make bootstrap self-consistent.** Files it generates should pass
   `audit_context_health` without intervention.

4. **Priority 4: Complete `dep_sync`.** Add `install=true` to go from "lockfile exists"
   to "project is runnable."

5. **Priority 5: Venv-aware tool resolution.** When `.venv/` exists, check tools there
   first.

---

## 7. Final Assessment

LintGate occupies a genuinely useful niche. The combination of tiered linting,
dependency health monitoring, cross-channel supervision, and context file management
is something no single existing tool provides. For the stated goal of bridging the gap
between a non-CS person with an LLM agent and professional code quality, it gets about
**70% of the way there**.

The remaining 30% is almost entirely in the **action layer** — the gap between "here's
what's wrong" and "here, I fixed it." For an LLM agent, diagnosis without remediation
means extra round-trips, extra token consumption, and extra opportunities for the agent
to introduce new issues while fixing old ones. Closing that gap would make LintGate
not just useful but genuinely transformative for the AI-assisted development workflow.

The diagnostic capabilities are already professional-grade. The cross-channel coherence
analysis is novel and valuable. The tiered progression model is sound. What's needed
now is the operational polish to make the full cycle — diagnose, fix, verify — happen
with minimal friction and minimal token cost.

---

*Report generated during live project bootstrap. All observations are from direct
tool interaction, not documentation review.*
