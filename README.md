# LintGate

**Three words. Zero debugging.**

A biochemist kept running into the same problem with AI-generated code. Not complex bugs — *discipline* bugs. The agent would try an approach, fail, and try a variant instead of updating its model. It would hit an error, move on, and hit the same error twenty minutes later. It would act before it understood.

These aren't intelligence failures. They're infrastructure failures. So he built the infrastructure.

Then he pointed it at its own codebase and said: *"Professionalize this codebase."*

---

## The Proof

In a normal agentic coding session, the dominant cost is debugging — the write-fail-rewrite loop where each iteration degrades context and compounds errors. Code generation is cheap. *Correct* code generation is hard.

The autonomous professionalization of LintGate's own codebase — 33,000 lines, 6 major module decompositions, 9 new files — produced this ratio:

**Creation : Debugging : Verification — 55 : 0 : 15**

The zero is not a typo. Across every refactoring step, the agent wrote correct code on the first attempt. Every step passed 1,611 tests. Every step passed the linter. There was nothing to debug. The entire debugging phase of software development — the most expensive, most failure-prone phase — simply didn't occur.

Total human input: three words. Total cost: ~590,000 tokens. Total regressions: zero.

The codebase it refactored was LintGate itself. The tool diagnosed its own code as requiring fundamental restructuring, then *was the infrastructure that enabled that restructuring to succeed*. The tool that teaches agents discipline was itself maintained by the discipline it provides.

[Full session data](docs/retrospectives/lintgate-2026-02-20-tier2-audit.md)

---

## The Problem

Vibe coding works — remarkably well — until it doesn't. And when it fails, it fails silently.

The agent introduces a bad import at edit 5. Nothing flags it. Thirty edits later the codebase is syntactically valid and architecturally broken. The type error compounded. The hallucinated import propagated. The function that should have been decomposed got stuffed with logic because nobody told the agent the project values composition.

But the code problems are downstream. Before the agent writes the wrong code, it *reasons* wrong. It tries an approach, fails, tries a variant instead of updating its model. It encounters an error, moves on, hits the same error twenty minutes later. It acts before it understands.

This is **behavioral drift** — the agent's reasoning strategy diverging from effective problem-solving. It is upstream of code drift. Catch the reasoning failure early enough and you never write the bad code.

A senior engineer catches all of this. Not by being smarter — by having instincts. Check imports after refactoring. Verify types after changing signatures. Notice when complexity creeps. These aren't intelligence problems. They're discipline problems. And right now, the only way to get that discipline is to hire a senior engineer.

That's an access problem. LintGate solves it.

---

## The Insight

This might look like a linter suite. It's not. A linter catches syntax errors after you write them. LintGate prevents the reasoning failures that cause you to write them.

The core mechanism, borrowed from how good instruments work: **multiple cheap, lossy lenses whose errors are uncorrelated compose into something that looks like intelligence.** Ruff can't judge architecture. Tests can't judge code quality. The dependency checker knows nothing about syntax. But when the type checker, the import analyzer, and the test runner all disagree in a *specific pattern*, that disagreement is diagnostic. Three silent channels and one loud one concentrates your attention. Two failures on overlapping files reveals coupling. No single tool is smart. The agreement pattern is the signal.

---

## How It Works

**The hook** fires on every code change. It classifies what changed, selects a lint tier (up to 18 linters across 4 escalating tiers), runs them in parallel, and returns a compact report — or `{}` when nothing is wrong. Silent when you're doing fine. Loud when it matters.

**The ControlPlane** runs 6 independent channels in parallel: lint, tests, deps, git, behavior, structure. A coherence engine reads the pattern of agreement and disagreement across channels and computes a state — stable, isolated, coupled, systemic, degraded. The state tells you the *character* of your problems, not just the count. When three channels pass and one fails, the passing channels are actively narrowing your problem space.

**The behavioral compass** tracks the agent's reasoning strategy in real time: live hypotheses, approach history, intent patterns, coverage metrics. When the strategy drifts — retrying failed approaches, ignoring discovered constraints, acting without verifying — the behavior channel catches it and intervenes *before* the bad reasoning produces bad code. Nine detection rules, deterministic scoring, no LLM calls.

The unsupervised agent spends ~64% of its token budget on discipline problems. LintGate's 18% supervision overhead displaces waste that accumulates at 3-4x the rate:

| Metric | Unsupervised | Supervised |
| --- | --- | --- |
| Effective duty cycle (tokens on novel reasoning) | ~36% | ~78% |
| Tokens wasted on discipline failures | ~64% | ~22% |
| Efficiency loss factor | ~4x | ~1.2x |

But the efficiency gain isn't the most interesting part. When discipline failures set a ceiling on what an agent can handle, removing them doesn't just make it faster — it lets the agent attempt problems that previously exceeded its effective capacity. The ceiling was never the model's reasoning. It was cumulative drift.

---

## The Bootstrap Progression

LintGate works from zero state and gets better as it accumulates signal:

**Stage 0 — Zero state.** No config, no history, no theory. Works immediately with just ruff installed.

**Stage 1 — Theory extraction.** Scans project documentation and produces a CLAUDE.md with project-specific dispositions, guardrails, and enforceable rules.

**Stage 2 — Model calibration.** A 5-task behavioral micro-probe profiles the model's coding tendencies by observing what it *does*, not what it *says*. Model-specific guardrails.

**Stage 3 — Living context.** Recurring behavioral patterns flow back as patches to CLAUDE.md managed sections. The project's self-model evolves across sessions.

Each stage stands alone. Stop at any stage and the system works — each layer is additive.

---

## Quick Start

```bash
# One-command setup
bash setup.sh            # full suite (ruff, mypy, radon, bandit, vulture, pip-audit)
bash setup.sh --minimal  # just ruff + MCP
```

This creates the venv, installs dependencies, configures the PostToolUse hook, registers the MCP server, and auto-detects LLM coding agents on your system (Claude Code, Cursor, Copilot, Windsurf, Gemini CLI, Cline, Roo Code, Aider, Amazon Q).

### First 5 Minutes

1. `getting_started(path)` — project-specific onboarding and next actions.
2. `controlplane_run(path)` — full health check across 6 channels. Works without config.
3. `controlplane_get_details(run_id)` — drill into what matters.
4. `lint_fix(path)` — auto-fix safe issues.
5. `bootstrap_context_files(path, write=True)` — generate persistent project context.

For manual setup, hook/MCP configuration, and agent integration details, see [docs/reference.md](docs/reference.md).

---

## Validation

Independent tool validation of the LintGate codebase (92 Python files, ~24K LOC). All measurements taken with standard open-source tools — no LintGate involvement in scoring.

### Before/After: Autonomous Professionalization

**Total human input to produce the "After" column: one sentence.** *"Professionalize this codebase."* No corrections. No guidance. No steering.

| Metric | Before | After | Delta |
| --- | --- | --- | --- |
| **Pylint score** | 9.38/10 | 9.42/10 | +0.04 |
| **Radon maintainability (avg MI)** | 57.1 | 58.4 | +1.3 |
| **Files at MI grade A** | 88/92 (96%) | 90/92 (98%) | +2 |
| **Files at MI grade C or below** | 1 | 0 | **eliminated** |
| **Radon avg cyclomatic complexity** | 5.59 | 5.31 | -5.0% |
| **High-complexity blocks (D+)** | 15 | 8 | **-47%** |
| **Very high complexity (F grade)** | 3 | 1 | **-67%** |
| **Worst single function CC** | 82 | 54 | -34% |
| **Ruff violations** | 3 | 4 | +1 (all cosmetic) |
| **Test suite** | 1,611 pass | 1,611 pass | 0 regressions |

### Current Standing vs. Industry Thresholds

| Metric | LintGate | Professional Threshold | Assessment |
| --- | --- | --- | --- |
| Pylint | **9.42/10** | >9.0 (excellent) | Excellent |
| Maintainability Index | **98% grade A** | >80% grade A (healthy) | Excellent |
| Avg cyclomatic complexity | **5.31** | <10 (acceptable), <5 (ideal) | Good — near ideal |
| Function grades A+B | **89%** | >75% (maintainable) | Excellent |
| High-complexity blocks | **1.1%** | <5% (acceptable) | Excellent |
| Test reliability | **0 regressions** through 6 major refactors | — | Verified |

This codebase was built almost entirely through vibe coding — natural language directives to an LLM agent, minimal manual code. The professionalization pass was itself fully autonomous. The external scores are not the result of manual engineering discipline. They are the result of automated supervision making manual discipline unnecessary.

---

## Research Context

LintGate is alignment research in product form: design the interaction structure so the human-agent system stays coherent under pressure.

It's an applied instance of *relational engineering* — the working hypothesis that alignment quality is a property of the human-model interaction, not of the model alone. Behavioral drift, session-level degradation, the gap between formally correct outputs and genuinely productive process — these are interaction failures, addressable by deliberately constructing interaction architectures that maintain the conditions for co-construction.

The theory is exploratory and instrumented. We evaluate by operational usefulness: fewer retries per resolved issue, less time in dead-end loops, faster convergence on root causes. [docs/design.md](docs/design.md) has the full architecture, calibration data, and design philosophy.

---

*35 MCP tools, configuration reference, project structure, and setup details: [docs/reference.md](docs/reference.md)*

*Full architecture and design philosophy: [docs/design.md](docs/design.md)*

---

MIT — Rohan Vinaik
