# LintGate

**Deterministic code quality supervision for AI-assisted development.**

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

AI writes code fast but breaks things. A senior engineer's instincts — check imports after refactoring, notice when complexity creeps, feel when a function does too much — are pattern recognition over structural invariants. Pattern recognition over structural invariants is what symbolic systems do.

118 MCP tools. 18 linters. 6 parallel analysis channels. No LLM inference in the supervision path — symbolic checks on a CPU, at the speed your agent writes code.

| | Without LintGate | With LintGate |
|---|---|---|
| **Creating** | 30% | 55% |
| **Debugging** | 40% | 0% |
| **Verifying** | 30% | 15% |
| **Token overhead** | — | ~2% |

---

## The idea

LintGate encodes those instincts as deterministic checks and runs them on every edit. The hook fires, classifies the change, selects a lint tier, runs 18 linters in parallel. Silent when you're fine. Loud when it matters.

The **ControlPlane** goes deeper: 6 independent channels (lint, tests, deps, git, behavior, structure) report in parallel. A coherence engine computes system state from their agreement — stable, isolated, coupled, systemic, degraded. When three channels converge on the same files, that convergence *is* the diagnosis.

The **behavioral compass** catches something linters can't: reasoning drift. It tracks the agent's approach history, live hypotheses, and prediction accuracy. When the strategy diverges — trying variants of a failed approach instead of updating the mental model — the behavior channel intervenes *before* bad reasoning produces bad code.

All deterministic. All symbolic. No LLM calls in the supervision path.

## What this is not

- **Not another linter.** Ruff checks syntax. LintGate checks whether your *reasoning strategy* is degrading.
- **Not an AI code reviewer.** No LLM judges LLM output. Symbolic checks only.
- **Not Python-specific in concept.** The architecture (lossy lens composition, behavioral drift detection, specification complexity) is language-agnostic. Current implementation targets Python.

## Results

**Self-audit:** LintGate supervised the refactoring of its own codebase — 33,700 lines, 92 files, 6 monolithic modules decomposed. Zero debug spirals. Zero regressions. ~207K tokens vs ~500K estimated unsupervised.

**ShortcutForge (external codebase):** 100 Python files, ~37,500 LOC, built without LintGate. ControlPlane diagnosed "systemic." 46 minutes later:

| | Before | After |
|---|---|---|
| **Blockers** | 132 | 7 (-95%) |
| **Pylint score** | 8.49 | 9.44 |
| **Ruff violations** | 266 | 0 |
| **Test suite** | 477 pass | 477 pass (0 regressions) |

**Small model experiment:** Gemini 2.5 Flash Lite — 0.77B parameters — received 2,639 structured findings from ControlPlane. It correctly identified signal-to-noise problems, six functions needing decomposition, and a `set` vs `tuple` optimization. A sub-billion-parameter model did performance engineering because the findings did the hard work. Structured output shifts the bottleneck from generation to interpretation.

Full session data: [Self-audit](docs/retrospectives/Archive/lintgate-2026-02-20-tier2-audit.md) · [ShortcutForge](docs/retrospectives/Archive/shortcutforge-2026-02-20-tier2-audit.md) · [Small model](docs/retrospectives/Archive/lintgate-2026-02-23-tier2-refactoring-debugging.md) · [All retrospectives](docs/retrospectives/)

## The formalization

The specification system is backed by theory. Mutation score doesn't measure test quality — it measures *specification completeness*. The minimum test suite to fully specify a program is a property of the program itself, not the tests.

This turns out to be the same quantity that five theoretical CS fields discovered independently: teaching dimension, query complexity, identity testing, local testability, and SpecP membership. We proved it in ~10,000 lines of Lean 4. The proofs compile. The five-field identification is either correct or it isn't, and Lean says it is.

Details: [Specification Complexity paper](docs/research.md) · [Design architecture](docs/design.md)

## Quick start

```bash
bash setup.sh            # full suite (ruff, mypy, radon, bandit, vulture, pip-audit)
bash setup.sh --minimal  # just ruff + MCP
```

Auto-detects Claude Code, Cursor, Copilot, Windsurf, Gemini CLI, Cline, Roo Code, Aider, Amazon Q.

**First 5 minutes:**

1. `getting_started(path)` — project onboarding + startup automation
2. `controlplane_run(path)` — full health check across 6 channels (works without config)
3. `controlplane_get_details(run_id, finding_domain="code")` — drill into code findings without dependency noise; the default response also splits code vs environment in its summary
4. `controlplane_apply_repairs(path, run_id=...)` or `lint_fix(path)` — replay persisted safe repairs from that run, or use direct lint autofix
5. `bootstrap_context_files(path, write=True)` — generate persistent project context

## Golden path for agents

The platonic golden path is the primary workflow for auto-improving any Python codebase. It selects targets, profiles mutation survival, generates tests, validates them, and applies — all deterministically.

### Single-file improvement

```
platonic_converge(path, file)  →  follow primary_next_action  →  platonic_apply(path, workflow_id)
```

### Full codebase sweep

```
loop:
  platonic_project(path)           # picks the next highest-value target
  ↓
  follow primary_next_action       # profiles, generates, validates automatically
  ↓
  platonic_continue(path, wf_id)   # step-aware resume (validate-only or snapshot resume)
  ↓
  platonic_apply(path, wf_id)      # apply when state = READY_TO_APPLY or READY_TO_APPLY_WITH_REVIEW
  ↓
  repeat                           # each run picks the next best file
```

### Decision tree

| Situation | Tool | What it does |
|-----------|------|-------------|
| "Improve this codebase" | `platonic_project(path)` | Auto-selects highest-value untested file |
| "Improve this file" | `platonic_converge(path, file)` | Profiles mutations, generates tests, validates |
| Workflow interrupted | `platonic_continue(path, workflow_id)` | Resumes from persisted state, including validate-only and profiling snapshot resume |
| State = `READY_TO_APPLY` or `READY_TO_APPLY_WITH_REVIEW` | `platonic_apply(path, workflow_id)` | Previews or applies validated staged tests to the live suite |
| "Sweep the whole project" | `platonic_sweep(path)` | Scheduler-driven multi-file sweep with budget + cross-session persistence |
| Want manual control | `mutation_run_sampling` → `mutation_prescribe_tests` → `mutation_validate_tests` | Low-level mutation pipeline |

### What happens inside

Each `platonic_converge` run:
1. **Profiles** the file with mutation sampling (VALUE, SWAP, BOUNDARY, STATE, TYPE categories)
2. **Routes** functions by survival profile — high survival → generate tests, low → skip
3. **Generates** tests using typed input synthesis, field-enumeration assertions, round-trip pair detection, and oracle-light properties
4. **Validates** generated tests by re-profiling — confirms new tests actually kill targeted mutants
5. **Persists** workflow state so it can be resumed or applied later

## Prescriptive specifications

LintGate normally operates *retrospectively*: code is written, then analyzed. The Prescriptive Spec system flips this to *prospective*: before code is written, a behavioral contract is composed from project theory and compass directives.

### How it works

```
prescriptive_spec_compose(path, target)     # theory + compass → behavioral contract
  ↓
prescriptive_spec_compile(path, target)     # contract → test skeletons + generation constraints
  ↓
[write code guided by generation_prompt]    # LLM sees MUST/MUST NOT constraints
  ↓
prescriptive_spec_verify(path, file)        # refinement check: sigma convergence + mutation kill expectations
```

Each spec is a **typed Predicate IR** — not strings. Predicates are normalizable, comparable, and checked against the function AST at ControlPlane time (PSPEC001). The `CUSTOM` escape hatch handles predicates that require semantic understanding.

### Three problem-class backends

| Backend | Compiles to |
|---------|-------------|
| **Pure** | Hypothesis property skeletons, exact-value assertions, mutation kill expectations |
| **Stateful** | Init/Next/Invariant transition tests, state variable initialization checks |
| **Distributed** | Protocol conformance tests, message sequence monitors |

### Hook integration (8 points)

- **PostToolUse**: advisory when editing files with specs
- **PreToolUse**: obligation + generation constraint guidance before writes
- **UserPromptSubmit**: `PSpec: N specs (X% covered)` in primer
- **PreCompact**: prescriptive state preserved in compaction capsule
- **Theory freeze**: auto-composes specs for high-confidence targets
- **ControlPlane**: PSPEC001 (AST invariant violation), PSPEC002 (sigma divergence), PSPEC003 (missing spec)
- **Living context**: `prescriptive_rules` managed section in CLAUDE.md
- **Compass alignment**: `check_alignment_with_specs()` checks actions against forbidden behaviors

### Config

```yaml
controlplane:
  prescriptive_spec:
    enabled: true
    auto_compose_on_freeze: true
    emit_advisory_on_write: true
    sigma_divergence_threshold: 2.0
```

## Bootstrap progression

Works from zero state. Gets better as it accumulates signal.

**Stage 0** — No config, no history. Works immediately with just ruff.
**Stage 1** — Theory extraction. Scans project docs, produces context with dispositions and enforceable rules.
**Stage 2** — Model calibration. Behavioral micro-probe profiles the model's coding tendencies.
**Stage 3** — Living context. Recurring patterns flow back as patches. The project's self-model evolves.
**Stage 4** — Prescriptive specs. Behavioral contracts composed from theory, enforced via AST checks, refined through mutation verification.

Each stage stands alone. Each layer is additive.

## Ship pipeline

```bash
python scripts/ship_main.py    # local gates → push → done
```

The **LintGate GitHub App** creates the PR, monitors CI, approves, squash-merges, and GPG-signs the commit. No polling. No LLM inference spent waiting. The signed merge commit is proof the quality gate was not bypassed.

## Economics

LintGate's tools are symbolic — math on a CPU, not LLM inference. The self-audit cost ~$2.60 in supervision against $21.28 total (~12%). At scale, overhead drops to ~2% while eliminating debugging and cutting total tokens by half.

The savings come from what doesn't happen. Every uncaught discipline failure degrades the context window, which degrades all subsequent reasoning. A wasted 500 tokens at edit 10 costs 5,000 by edit 50. Symbolic checks prevent failures before they enter the context.

---

Part of a research program on structured navigation through constrained semantic spaces — the same paradigm applied to [ML model discovery](https://github.com/rohanvinaik/ModelAtlas) and [theorem proving](https://github.com/rohanvinaik/Wayfinder).

*118 MCP tools, configuration reference, and setup details: [docs/reference.md](docs/reference.md)*

MIT — Rohan Vinaik
