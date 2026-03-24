# LintGate

**A specification compiler for AI-generated code.** Not a linter. A formal system that measures how hard your code is to specify, tells you where to split it, and proves the measurement is mathematically well-posed.

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
[![Mutation Kill Rate](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/mutation-kill-rate.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Tests](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/test-count.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Benchmark](https://github.com/rohanvinaik/LintGate/actions/workflows/benchmark.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/benchmark.yml)
<!-- lintgate:quality-badges:end -->

`118 MCP tools · 18 linters · 6 parallel analysis channels · No LLM in the supervision path`

Built entirely through vibe coding by a biochemist with no formal CS training. The metrics above are computed by the system on its own codebase.

---

## The problem

The dominant cost of AI coding isn't generation. It's the debugging spiral that follows. An agent writes subtly wrong code. You don't notice. Thirty edits later, the codebase has drifted into an architecturally broken state. The agent tries to fix it. Each fix adds misleading context. The context window fills with failed approaches. Generation quality degrades. The session resets.

This isn't an edge case. It is the ordinary experience of using AI agents on complex codebases.

## What LintGate does

LintGate eliminates this failure class by ensuring the conditions for correct code exist *before* generation begins.

**Specification complexity (σ):** For every function, LintGate measures the minimum number of tests needed to fully specify its behavior. Not "how many tests do you have" — "how many do you *need*." σ is a property of the program itself, not of any test suite. Functions with high σ need decomposition, not more tests.

**Mutation profiling:** Five categories of AST mutation (VALUE, SWAP, BOUNDARY, STATE, TYPE) probe which behavioral degrees of freedom your tests actually constrain. Surviving mutants aren't bugs — they're the specification gaps that let bugs in later.

**Prescriptive decomposition:** When σ is too high, mutation survival profiles identify the entangled behavioral dimensions. The system prescribes specific structural decomposition — not "this function is too complex" but "these two concerns are entangled along this dimension, separate them here."

**Symbolic refactoring:** `refactor_move` extracts functions by AST node movement — zero tokens, zero bugs, correct imports. `refactor_extract_method` detects closure variables and refuses when control flow (break/continue) would be corrupted. The refusals are as valuable as the applications.

**ControlPlane:** Six independent channels (lint, tests, deps, git, behavior, structure) report in parallel. A coherence engine computes system state from their agreement. When three channels converge on the same files, that convergence *is* the diagnosis.

All deterministic. All symbolic. Math on a CPU, not tokens through a model.

## The proof

I gave LintGate to a frontier language model and asked it to improve [ModelAtlas](https://github.com/rohanvinaik/ModelAtlas) — a 7,800-line codebase I'd built through vibe coding. Three hours later:

| Metric | Before | After |
|---|---|---|
| **Blockers** | 48 | **0** |
| **Tests** | 783 | **950** |
| **Mutation kill rate** | unmeasured | **100% (824/824)** |
| **MC/DC (DO-178C Level A)** | unverified | **Verified (10/10 conditions)** |
| **Coverage** | — | **96%** |

The model wrote this in its [retrospective](docs/retrospectives/modelatlas-2026-03-23-tier2-hybrid.md):

> *"I am a frontier language model. I had read the file. I had the convergence analysis. I had every advantage. And I made 3 bugs in 15 minutes. The symbolic tool made 0 bugs in 5 seconds."*

> *"The sigma values told me the exact number of tests needed: `_gradient_decay` needs 2, `_bank_score_single` needs 9, `_extract_quality` needs 33. This turned 'improve test quality' from an aspiration into a checklist."*

> *"This is not an incremental improvement over manual refactoring. It is a categorically different operation."*

The ModelAtlas SonarCloud dashboard — 0 bugs, 0 vulnerabilities, triple-A ratings, no god files, CC <40 across the entire codebase — is the LintGate demo. A biochemist's vibe-coded project at aerospace-grade specification.

Full session data: [ModelAtlas retrospective](docs/retrospectives/modelatlas-2026-03-23-tier2-hybrid.md) · [Self-audit](docs/retrospectives/Archive/lintgate-2026-02-20-tier2-audit.md) · [ShortcutForge (-95% blockers)](docs/retrospectives/Archive/shortcutforge-2026-02-20-tier2-audit.md) · [All retrospectives](docs/retrospectives/)

## The theory

Everyone measures test coverage. The question LintGate asks is different: **what's the minimum number of tests needed to fully specify a program's behavior?**

That quantity — specification complexity σ(P,μ) — turns out to be the same number five theoretical CS fields discovered independently: the teaching dimension (computational learning theory), query complexity of identity testing (property testing), the local testability parameter (coding theory), certificate size (combinatorial complexity), and a new parameter governing membership in the complexity class SpecP.

We proved it in ~15,000 lines of fully type-checked Lean 4. σ satisfies the Blum axioms — it sits alongside time and space complexity as a well-posed complexity measure. "How hard is this code to specify?" is formally as meaningful as "how long does it take to run?"

The engineering consequence: mutation pressure reliably forces decomposition into algebraically tractable units. You cannot achieve 100% specification completeness on a tangled function without decomposing it, because tangling is exactly what creates observational equivalence between the original and its mutants.

Details: [Specification Complexity paper](docs/research.md) · [Lean 4 proofs](https://github.com/rohanvinaik/LintGate/tree/main/Mutation_Theory_proofs) · [Design architecture](docs/design.md)

## What this is not

- **Not a linter.** Ruff checks syntax. LintGate checks whether your *specification* is complete.
- **Not an AI code reviewer.** No LLM judges LLM output. Symbolic checks only.
- **Not Python-specific in concept.** The theory is language-agnostic. Current implementation targets Python.

Want just the mutation engine? [Wesker](https://github.com/rohanvinaik/Wesker) — standalone CI action, zero dependencies, 2-minute setup.

## Quick start

```bash
bash setup.sh            # full suite (ruff, mypy, radon, bandit, vulture, pip-audit)
bash setup.sh --minimal  # just ruff + MCP
```

Auto-detects Claude Code, Cursor, Copilot, Windsurf, Gemini CLI, Cline, Roo Code, Aider, Amazon Q.

**First 5 minutes:**

1. `getting_started(path)` — project onboarding
2. `controlplane_run(path)` — full health check across 6 channels
3. `controlplane_apply_repairs(path, run_id=...)` — apply safe fixes
4. `platonic_converge(path, file)` — mutation-profile a file, generate tests, validate kills
5. `platonic_apply(path, workflow_id)` — apply validated improvements

Works from zero state. No config required. Gets better as it accumulates signal — theory extraction, behavioral calibration, living context, prescriptive specs. Each layer is additive.

## Economics

LintGate's tools are symbolic — math on a CPU, not LLM inference. Supervision overhead is ~2% of total tokens while eliminating debugging entirely and cutting total session cost by half.

The savings come from what doesn't happen. Every uncaught discipline failure degrades the context window, which degrades all subsequent reasoning. Symbolic checks prevent failures before they enter the context.

---

Part of a research program on structured navigation through constrained semantic spaces — the same paradigm applied to [ML model discovery](https://github.com/rohanvinaik/ModelAtlas) and [theorem proving](https://github.com/rohanvinaik/Wayfinder).

*Full tool reference, golden path workflows, prescriptive spec system, and configuration: [docs/reference.md](docs/reference.md)*

MIT — Rohan Vinaik
