# LintGate

<!-- lintgate:quality-badges:start -->
[![Tests](https://github.com/rohanvinaik/LintGate/actions/workflows/tests.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/tests.yml)
[![Security](https://github.com/rohanvinaik/LintGate/actions/workflows/security-lite.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/security-lite.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=coverage)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_LintGate&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_LintGate)
[![Mutation Score](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/mutation.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/mutation.yml)
[![Side-Effect-Free](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/purity.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Algebraic Properties](https://raw.githubusercontent.com/rohanvinaik/LintGate/badges/.github/badges/properties.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/algebra-badges.yml)
[![Benchmark](https://github.com/rohanvinaik/LintGate/actions/workflows/benchmark.yml/badge.svg)](https://github.com/rohanvinaik/LintGate/actions/workflows/benchmark.yml)
<!-- lintgate:quality-badges:end -->

An MCP server for real-time code quality supervision — built entirely through vibe coding by a biochemist with no formal CS training.

> `--dangerously-skip-permissions`, minus the danger.

A biochemist kept running into the same problem with AI-generated code. Not complex bugs — *discipline* bugs. The agent would act before it understood. It would hit an error, move on, and hit the same error twenty minutes later.

These aren't intelligence failures. They're infrastructure failures. So he built the infrastructure.

Then he pointed it at its own codebase and said: *"Professionalize this codebase."*

And it did. Three words. Zero debugging.

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

The downstream consequence is stranger than the mechanism: **when discipline is infrastructure, the conceptual burden of hard engineering problems drops below the burden of typing.** Structured deterministic findings — file paths, line numbers, severity codes, check IDs — shift the task from "understand code + reason + write code" to "read structured findings + propose actions." That means the bottleneck for using these tools is not intelligence. It's dexterity.

---

## The Deeper Signal

Clean code and well-tested code are usually treated as separate virtues. They're not — they're the same property viewed from different angles.

A surviving mutant is a version of your function that behaves differently and no test notices. If no test can tell the difference, the test suite doesn't fully specify what the function does. In Sussman's framing (SICP): the function is an ambiguous specification of a computational process. And ambiguous specifications resist optimization, because you can't prove two things are equivalent when you haven't defined what equivalence means.

But the surviving mutants aren't just a score. They're a **map**. Each one is a specific behavioral degree of freedom that isn't pinned down — the function could wobble in that dimension and nobody would know. The *category* of surviving mutants (conditional, arithmetic, string, keyword) tells you *why* the function is underspecified. And that tells you exactly what to do about it: which tests to write, or — when multiple categories survive — which axis to decompose along. The mutation profile is a constructive specification of the negative space between the code as it is and the code as it should be.

This creates an operational chain that doesn't appear elsewhere in the literature: **mutation pressure → specification gap → forced decomposition → algebraically tractable units → safe optimization**. You can't fully specify a tangled function — the test surface is combinatorially large. Decomposition makes specification tractable. Fully specified functions naturally exhibit purity, cacheability, parallelizability. The act of driving toward zero surviving mutants *is* what produces clean, optimizable code.

LintGate makes this concrete. Point it at a module with 99% line coverage and the algebra pipeline says `_get_parameter_count` is pure and cacheable. Then the mutation gate fires: 75% of mutants survive. The tests execute the code but don't verify what it computes. Confidence drops from 80% to 10%, and the gate tells you *why* — 12 surviving mutants across conditional and arithmetic categories — which is the surgical target for the 2-3 tests that would make the optimization safe. ([Full theory](docs/mutation-theory.md))

---

## How It Works

**The hook** fires on every code change. It classifies what changed, selects a lint tier (up to 18 linters across 4 escalating tiers), runs them in parallel, and returns a compact report — or `{}` when nothing is wrong. Silent when you're doing fine. Loud when it matters.

**The ControlPlane** runs 6 independent channels in parallel: lint, tests, deps, git, behavior, structure. A coherence engine reads the pattern of agreement and disagreement across channels and computes a state — stable, isolated, coupled, systemic, degraded. The state tells you the *character* of your problems, not just the count. When three channels pass and one fails, the passing channels are actively narrowing your problem space.

**The behavioral compass** tracks the agent's reasoning strategy in real time: live hypotheses, approach history, intent patterns, coverage metrics. When the strategy drifts — retrying failed approaches, ignoring discovered constraints, acting without verifying — the behavior channel catches it and intervenes *before* the bad reasoning produces bad code. Nine detection rules, deterministic scoring, no LLM calls.

**The Algebraic Properties Bridge** performs formal analysis of your codebase's mathematical structure. It detects function purity (side-effect isolation), classifies algebraic properties (idempotency, commutativity, associativity), and identifies optimization opportunities like trivially safe parallelization or zero-invalidation caching. It bridges structural analysis with formal verification.

---

## The Economics

The fundamental unit is **output tokens** — the model's actual generation work: code written, reasoning produced, decisions made. Input tokens are dominated by context re-reading and aren't meaningful for cost comparison. LintGate's tools are symbolic, deterministic, and run locally — they don't call the model API. Its cost is the small number of API calls where the agent invoked a LintGate tool.

### The Bottom Line

From a fully instrumented session — LintGate professionalized its own codebase (33,700 lines, 92 Python files, 3 context windows, human input: three words):

| | With LintGate | Without LintGate (counterfactual) |
| --- | --- | --- |
| **Output tokens to ship** | **~207,000** | **~450,000–550,000** |
| **Debug spirals** | 0 | 6+ estimated (one per decomposition) |
| **Regressions** | 0 | 3–6 estimated |
| **Test suite (1,611 tests)** | Green at every step | Batch-verified at end |
| **Creation : Debugging : Verification** | 55 : 0 : 15 | ~30 : 40 : 30 (typical) |

The supervised agent produced ~207K output tokens and touched 49% of the codebase — 36 files modified, 9 created, 6 monolithic modules decomposed into clean, independently testable components. Every refactoring step passed the linter. Every step passed the test suite. The entire debugging phase of software development simply didn't occur.

The zero in the debugging column is not a typo. Six modules between 900 and 1,500 lines were each split along behavioral seams, producing 2–3 clean extraction modules with backward-compatible re-exports. All 1,611 tests passed on the first run after every split. The unsupervised agent would need 2–3× the output tokens because each failed decomposition pollutes the context window, degrading all subsequent reasoning.

### Why the Gap Compounds

Discipline failures don't add — they multiply. Each wasted output token degrades the context for everything that follows, causing subsequent output to be even less efficient:

| Metric | Unsupervised | Supervised |
| --- | --- | --- |
| Effective duty cycle (output tokens on novel reasoning) | ~36% | ~78% |
| Output tokens wasted on discipline failures | ~64% | ~22% |

LintGate consumed ~$2.60 of the $21.28 session — 12% of total cost — and returned the entire debugging phase as savings.

### External Validation: ShortcutForge

Auditing your own code is table stakes. The question is: what happens when you point LintGate at a codebase built *without* it?

ShortcutForge is a natural language compiler for Apple Shortcuts — a Lark LALR(1) parser, 615-action catalog with validation, 7-pass static analysis, plist compilation, code signing, LoRA fine-tuning pipeline, and a distillation loop. 100 Python files, ~37,500 LOC. Built through vibe-coding over a week of intensive development. Working code, passing tests, zero architectural planning.

LintGate's ControlPlane diagnosed it as "systemic" — 132 blockers, 253 warnings, 151 informational findings across all 6 channels. What happened next took 46 minutes.

| Metric | Before | After | Delta |
| --- | --- | --- | --- |
| **Blockers** | 132 | 7 | **-95%** |
| **Pylint score** | 8.49/10 | 9.44/10 | **+0.95** (crossed "excellent") |
| **Ruff violations** | 266 | 0 | **-100%** |
| **High-complexity blocks (D+)** | 27 | 10 | **-63%** |
| **Very high complexity (F grade)** | 5 | 1 | **-80%** |
| **Worst single function CC** | 95 | 79 | -17% |
| **Python files** | 91 | 57 | restructured |
| **Test suite** | 477 pass | 477 pass | 0 regressions |

The 7 remaining blockers are irreducible architectural characteristics — 5 cohesive files that happen to be long, 2 data classes that genuinely need many fields. Not debt. Just shape.

Three things stand out:

**The highest-ROI fix was not a code change.** 71 of 132 blockers were `ty` unresolved-import false positives caused by `sys.path` manipulation. Adding two lines to `pyproject.toml` eliminated them all. Configuration before code.

**The maintainability index broke.** Not "decreased" — the metric stopped being comparable. The file count changed from 91 to 57 because the codebase was *restructured*. Fewer files, each scoring better than the originals. The denominator of the measurement changed because the shape of the codebase changed.

**The largest god-function was decomposed into 15 methods with zero regressions.** `Orchestrator.generate()` — 620 lines, CC=95, 231 statements — was split along phase boundaries into a clean pipeline tree. All 25 orchestrator tests passed on the first run.

Full session data: [ModelAtlas build](docs/retrospectives/hf-model-search-2026-02-22-tier2-build.md) · [LintGate self-audit](docs/retrospectives/lintgate-2026-02-20-tier2-audit.md) · [ShortcutForge audit](docs/retrospectives/shortcutforge-2026-02-20-tier2-audit.md)

### The Capability Inversion: 0.77B Parameters Did Performance Engineering

We pointed LintGate's performance tools at its own codebase and told the smallest model we could find to optimize it. Gemini 2.5 Flash Lite — **0.77 billion parameters**, half the size of GPT-2 — running against a professional Python codebase with the full ControlPlane stack.

It received 2,639 structured PERFCH005 findings and correctly identified:

- Signal-to-noise problem: per-function findings should be collapsed into top-N summaries
- The manifest-to-lint bridge gap: purity analysis results weren't flowing to the lint checkers
- Six monolithic functions that should be decomposed into focused helpers
- A `set` vs `tuple` optimization for O(1) membership checks

Every diagnosis was correct. Every proposed decomposition was architecturally sound. Claude Opus 4.6 — a model with 1,000x+ the parameters — reviewed the architectural proposals and kept them: *"The manifest decomposition is genuinely good — I want to keep the helper extraction."*

Flash Lite produced **zero bytes of working code**. It couldn't construct valid Edit tool calls — the exact-string-matching requirement exceeded its 0.77B working memory. It entered a behavioral loop, restating its plan 9 times, each restatement coherent and slightly rephrased. It eventually pivoted to providing code snippets for "manual application" — the correct degradation strategy for a model that knows its hands don't work.

Gemini 2.5 Flash (7B parameters) then executed Flash Lite's designs. The composite system — 0.77B for diagnosis, 7B for execution, deterministic infrastructure for validation — produced performance engineering that would challenge senior developers.

| Model | Parameters | Diagnosis | Architecture | Execution | Behavioral Control |
| --- | --- | --- | --- | --- | --- |
| Flash Lite | 0.77B | Correct | Correct | Failed (0 bytes) | Looped |
| Flash | 7B | Correct | Correct | Succeeded | Stable |
| Opus 4.6 | 1T+ | Reviewed and validated | Kept the designs | Restored gutted files | Full editorial control |

**Why this happened**: Flash Lite scores 34% on LiveCodeBench (code generation) but **0.84 on tool selection quality** — in the same range as models 100x its size. LintGate's structured findings shifted the task from code generation (Flash Lite's weakness) to structured interpretation and action proposal (its strength). The conceptual burden of performance engineering was lower than the burden of typing.

This is not a parlor trick. It's the design thesis in action: when you offload discipline to deterministic infrastructure, the intelligence required for hard problems drops dramatically. The bottleneck was never reasoning. It was discipline. And discipline is infrastructure, not cognition.

Full session data: [Small model experiment](docs/retrospectives/lintgate-2026-02-23-tier2-refactoring-debugging.md)

---

## The Bootstrap Progression

LintGate works from zero state and gets better as it accumulates signal:

**Stage 0 — Zero state.** No config, no history, no theory. Works immediately with just ruff installed.

**Stage 1 — Theory extraction.** Scans project documentation and produces a CLAUDE.md and AGENTS.md with project-specific dispositions, guardrails, and enforceable rules.

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

1. `getting_started(path)` — project-specific onboarding with startup automation (auto config scaffold + auto venv provision + missing-tool diagnostics/remediation).
2. `controlplane_run(path)` — full health check across 6 channels. Works without config.
3. `controlplane_get_details(run_id)` — drill into what matters.
4. `lint_fix(path)` — auto-fix safe issues.
5. `bootstrap_context_files(path, write=True)` — generate persistent project context.

For manual setup, hook/MCP configuration, and agent integration details, see [docs/reference.md](docs/reference.md).

### Automated Ship To Main

Use the centralized ship pipeline to move from local changes to green on `main` with one command:

```bash
python scripts/ship_main.py
```

What it does:

1. Runs the strict local gate stack (`.githooks/pre-push`)
2. Pushes your branch
3. Creates/updates a PR to `main`
4. Watches required checks from branch protection plus `gate_contract.yaml`
5. Merges only when every required check is green

Useful flags:

```bash
python scripts/ship_main.py --no-merge          # stop after checks go green
python scripts/ship_main.py --prune-merged      # prune merged local side branches after merge
python scripts/ship_main.py --wait-seconds 30   # slower polling
python scripts/ship_main.py --preflight         # run strict local gate stacks without git mutations
```

---

## One More Thing

Everything above supervises the agent's *code*. Habit Mode supervises the resource that everything else depends on: the **context window**.

During sustained execution — bulk editing, test marathons, refactoring sweeps — the context fills with stale data that crowds out working state. Habit Mode detects these phases from tool-use signals, tracks token pressure via a calibrated estimator, and when compaction approaches, produces a structured snapshot that turns context loss into context refinement. The deterministic system remembers so the stochastic system doesn't have to.

This is a complete theory of alignment in its own right. [Full architecture and design philosophy →](docs/design.md)

---

## Research Context

LintGate is alignment research in product form: design the interaction structure so the human-agent system stays coherent under pressure.

It's an applied instance of *relational engineering* — the working hypothesis that alignment quality is a property of the human-model interaction, not of the model alone. Behavioral drift, session-level degradation, the gap between formally correct outputs and genuinely productive process — these are interaction failures, addressable by deliberately constructing interaction architectures that maintain the conditions for co-construction.

The theory is exploratory and instrumented. We evaluate by operational usefulness: fewer retries per resolved issue, less time in dead-end loops, faster convergence on root causes. The theoretical foundations — three papers on constrained hallucination, phenomenological alignment, and relational ontology — are mapped in [docs/research.md](docs/research.md).

---

*68 MCP tools, configuration reference, project structure, and setup details: [docs/reference.md](docs/reference.md)*

*Research foundations and theoretical lineage: [docs/research.md](docs/research.md)*

---

MIT — Rohan Vinaik
