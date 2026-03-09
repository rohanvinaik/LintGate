# LintGate

<!-- lintgate:quality-badges:start -->
[![Tests](https://github.com/rohanvinaik/LintGate/actions/workflows/tests.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/tests.yml)
[![Security](https://github.com/rohanvinaik/LintGate/actions/workflows/security-lite.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/security-lite.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=coverage)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Side-Effect-Free](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/purity.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Algebraic Properties](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/properties.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Mean σ](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/sigma.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Spec Coverage](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/spec-coverage.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Regime](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/regime.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Benchmark](https://github.com/rohanvinaik/LintGate/actions/workflows/benchmark.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/benchmark.yml)
<!-- lintgate:quality-badges:end -->

An MCP server for real-time code quality supervision.

> `--dangerously-skip-permissions`, minus the danger.

---

## The Observation

Ask a senior engineer what makes them effective and they usually won't say "I know more algorithms." They'll describe something closer to instinct — check imports after refactoring, verify types after changing signatures, notice when complexity creeps, feel when a function is doing too much before any metric confirms it. Junior engineers with the same knowledge, the same tools, the same specifications produce measurably worse code. The gap isn't knowledge. It's something harder to name.

The field has always treated this as experience — something you accumulate over years and can't transfer. But if you look at what the instincts actually *are*, they're pattern recognition over structural invariants. "This function is doing too much" is a statement about cyclomatic complexity and cohesion. "Check imports after refactoring" is a dependency graph constraint. "Notice when complexity creeps" is a rate-of-change signal on a well-defined metric.

Pattern recognition over structural invariants is what symbolic systems do.

That's the idea behind LintGate: take the things that make a senior engineer's code better than a junior engineer's code, express them as symbolic checks, and run them automatically on every edit. If it works, the limiting factor in producing quality code stops being years of human experience and becomes compute on a CPU.

---

## What Goes Wrong Without It

Here's what happens when an AI agent writes code unsupervised. It introduces a bad import at edit 5. Nothing flags it. Thirty edits later the codebase is syntactically valid and architecturally broken. The type error compounded. The hallucinated import propagated. A function that should have been decomposed got stuffed with logic because nothing told the agent the project values composition.

But the code problems are downstream of something subtler. Before the agent writes wrong code, it *reasons* wrong. It tries an approach, hits an error, tries a variant of the same approach instead of updating its mental model. It acts before it understands. This is behavioral drift — the reasoning strategy diverging from effective problem-solving. Catch the reasoning failure early enough and you never write the bad code.

A senior engineer catches all of this naturally. LintGate tries to do the same thing with infrastructure.

---

## How It Works

The core idea comes from how good instruments work in science: multiple cheap, lossy measurements whose errors are uncorrelated can compose into something surprisingly reliable. No single linter is smart. Ruff can't judge architecture. Tests can't judge code quality. The dependency checker knows nothing about syntax. But when the type checker, the import analyzer, and the test runner disagree in a specific pattern, the pattern itself is diagnostic. Three silent channels and one loud one concentrates your attention. Two failures on overlapping files reveals coupling.

**The hook** fires on every code change — classifies what changed, selects a lint tier, runs 18 linters in parallel. Silent when you're doing fine. Loud when it matters.

**The ControlPlane** runs 6 independent channels — lint, tests, deps, git, behavior, structure — and a coherence engine computes system state from their agreement: stable, isolated, coupled, systemic, degraded. The state tells you the *character* of your problems, not just the count. When three channels converge on the same files, that convergence is the diagnosis — and across every session so far, it has been the single most useful signal for deciding what to work on first.

**The behavioral compass** tracks the agent's reasoning strategy: live hypotheses, approach history, prediction accuracy. When the strategy drifts, the behavior channel intervenes *before* bad reasoning produces bad code. Deterministic scoring, no LLM calls.

**The algebra pipeline** does formal analysis of the codebase's mathematical structure — function purity, algebraic properties, mutation-backed specification completeness — and connects them to safe optimization opportunities. A surviving mutant is a version of your function that behaves differently and no test notices. Driving toward zero surviving mutants turns out to be the same thing as producing clean, optimizable code. An interesting side effect: the specification analysis converts test-writing from a creative task into an engineering task. "Which behavioral dimensions of this function are unconstrained?" turns out to have a computable answer.

---

## What Happened When We Tried It

### Self-Audit

I pointed LintGate at its own codebase and said: *"Professionalize this codebase."* Three words. What followed: 33,700 lines, 92 Python files, 3 context windows, 6 monolithic modules decomposed into clean components. Every refactoring step passed the linter and test suite on the first run.

| Metric | Supervised | Unsupervised (counterfactual) |
| --- | --- | --- |
| **Output tokens** | ~207,000 | ~450,000–550,000 |
| **Debug spirals** | 0 | 6+ estimated |
| **Regressions** | 0 | 3–6 estimated |
| **Creation : Debugging : Verification** | 55 : 0 : 15 | ~30 : 40 : 30 (typical) |

The debugging phase of software development simply didn't occur. Not because the problems were easy — six modules between 900 and 1,500 lines were each split along behavioral seams — but because the supervision caught structural issues before they could compound.

### At Scale

The self-audit was the first test. Subsequent sessions on LintGate's own codebase — now 636 files, ~70,000 lines — have been more telling. In the largest single session, the agent touched 188 files, added 301 specification tests, and shipped with zero regressions across a 6,994-test suite. The Creation : Debugging : Verification ratio was 85 : 2 : 13. LintGate's own token overhead was 2.1% of the session — and the estimated savings from avoided debug spirals and context pollution was 73–113× that cost.

Two findings surprised me. First, the test suite ran *faster* after adding hundreds of tests, because the module splitting that LintGate's structure channel recommended enabled more granular test collection. Second, every cognitive-complexity reduction the agent performed fell into one of four mechanical patterns (dispatch tables, class extraction, function hoisting, composition decomposition). The intelligence required to *identify* the fix was real. The intelligence required to *apply* it was not. That's exactly the division of labor LintGate is designed around.

### ShortcutForge (External Codebase)

The more interesting test: what happens on a codebase built entirely *without* LintGate?

ShortcutForge is a natural language compiler for Apple Shortcuts — a Lark LALR(1) parser, 615-action catalog, 7-pass static analysis, plist compilation, code signing, LoRA fine-tuning pipeline. 100 Python files, ~37,500 LOC. Built through vibe-coding over a week of intensive development. Working code, passing tests, zero architectural planning.

LintGate's ControlPlane diagnosed it as "systemic." What happened next took 46 minutes.

| Metric | Before | After |
| --- | --- | --- |
| **Blockers** | 132 | 7 (-95%) |
| **Pylint score** | 8.49/10 | 9.44/10 |
| **Ruff violations** | 266 | 0 |
| **High-complexity blocks** | 27 | 10 (-63%) |
| **Test suite** | 477 pass | 477 pass (0 regressions) |

The 7 remaining blockers are irreducible architectural characteristics — cohesive files that happen to be long. Not debt. Shape.

The highest-ROI fix wasn't even a code change: 71 of 132 blockers were `ty` unresolved-import false positives caused by `sys.path` manipulation. Two lines in `pyproject.toml` eliminated them all. Configuration before code.

### The Small Model Experiment

This one surprised me. Gemini 2.5 Flash Lite — 0.77 billion parameters, half the size of GPT-2 — received 2,639 structured performance findings from LintGate's ControlPlane. It correctly identified signal-to-noise problems, the manifest-to-lint bridge gap, six functions that should be decomposed, and a `set` vs `tuple` optimization. Every diagnosis was architecturally sound.

It couldn't write the code — the exact-string-matching requirement for Edit tool calls exceeded its working memory. But Claude Opus 4.6 reviewed its architectural proposals and kept them. A 0.77B model did the performance engineering. A larger model executed it.

What made this possible: Flash Lite scores 34% on LiveCodeBench (code generation) but 0.84 on tool selection quality — in the same range as models 100x its size. The structured findings shifted the task from generation to interpretation. That's a task it could do.

Full session data: [Self-audit](docs/retrospectives/lintgate-2026-02-20-tier2-audit.md) · [ShortcutForge](docs/retrospectives/shortcutforge-2026-02-20-tier2-audit.md) · [Small model experiment](docs/retrospectives/lintgate-2026-02-23-tier2-refactoring-debugging.md) · [Agent retrospectives](docs/retrospectives/)

---

## A Note on Economics

A reasonable question: doesn't all this supervision cost more tokens?

It doesn't, because LintGate's tools are symbolic — they're math running on a CPU, not LLM inference calls. The early self-audit session (33,700 lines, 49% of the codebase touched) cost ~$2.60 in supervision overhead against a $21.28 total — roughly 12%. At scale, that overhead has dropped to around 2% of the session token budget while still eliminating the debugging phase and cutting total output tokens by more than half.

The savings come from what doesn't happen. Every discipline failure an unsupervised agent makes degrades the context window, which degrades all subsequent reasoning. The compounding is the expensive part: a wasted 500 tokens at edit 10 can cost you 5,000 by edit 50. Symbolic checks run in milliseconds and prevent the failures before they enter the context. Across sessions, the pattern has been consistent: low single-digit percentage overhead to recover 50%+ of the token budget.

---

## Bootstrap Progression

LintGate works from zero state and gets better as it accumulates signal:

**Stage 0 — Zero state.** No config, no history. Works immediately with just ruff installed.

**Stage 1 — Theory extraction.** Scans project docs and produces a CLAUDE.md with project-specific dispositions, guardrails, and enforceable rules.

**Stage 2 — Model calibration.** A 5-task behavioral micro-probe profiles the model's coding tendencies by observing what it *does*, not what it *says*.

**Stage 3 — Living context.** Recurring behavioral patterns flow back as patches. The project's self-model evolves across sessions.

Each stage stands alone. Each layer is additive.

---

## Quick Start

```bash
bash setup.sh            # full suite (ruff, mypy, radon, bandit, vulture, pip-audit)
bash setup.sh --minimal  # just ruff + MCP
```

Auto-detects LLM coding agents on your system (Claude Code, Cursor, Copilot, Windsurf, Gemini CLI, Cline, Roo Code, Aider, Amazon Q).

### First 5 Minutes

1. `getting_started(path)` — project onboarding with startup automation.
2. `controlplane_run(path)` — full health check across 6 channels. Works without config.
3. `controlplane_get_details(run_id)` — drill into what matters.
4. `lint_fix(path)` — auto-fix safe issues.
5. `bootstrap_context_files(path, write=True)` — generate persistent project context.

### Ship Pipeline

The agent's job ends at `git push`. Everything after is deterministic.

```bash
python scripts/ship_main.py    # runs local gates → pushes → done
```

After push, the **LintGate GitHub App** (`lintgate[bot]`) creates the PR, monitors CI, approves, squash-merges, and GPG-signs the commit. No polling. No LLM inference spent waiting. The signed merge commit is proof the quality gate was not bypassed.

---

## Habit Mode

Everything above supervises the agent's code. Habit Mode supervises the resource everything else depends on: the context window.

During sustained execution — bulk editing, test marathons, refactoring sweeps — the context fills with stale data that crowds out working state. Habit Mode detects these phases from tool-use signals, tracks token pressure, and when compaction approaches, produces a structured snapshot that turns context loss into context refinement. The deterministic system remembers so the stochastic system doesn't have to.

---

## Research Context

LintGate grew out of a broader research program on relational alignment — the idea that alignment quality is a property of the human-model interaction, not of the model alone. The theoretical foundations are in [docs/research.md](docs/research.md). The design architecture is in [docs/design.md](docs/design.md).

---

*100 MCP tools, configuration reference, and setup details: [docs/reference.md](docs/reference.md)*

---

MIT — Rohan Vinaik
