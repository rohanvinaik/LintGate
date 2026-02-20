# LintGate

**Engineering supervision for AI coding agents — not smarter models, but multiple independent tools whose agreement structure is itself diagnostic.**

> **TL;DR:** Up to 18 linters, tier-selected per code change. Behavioral drift detection that catches reasoning failures upstream of code failures. Multi-channel coherence where the pattern across 6 independent channels narrows your problem space faster than any individual tool. Zero config — works with just ruff.

The core insight, borrowed from how good instruments work: **multiple cheap, lossy lenses whose errors are uncorrelated compose into something that looks like intelligence.** Ruff can't judge architecture. Tests can't judge code quality. The dependency checker knows nothing about syntax. But when the type checker, the import analyzer, and the test runner all disagree in a *specific pattern*, that disagreement is diagnostic. Three silent channels and one loud one concentrates your attention. Two failures on overlapping files reveals coupling. No single tool is "smart"; the agreement pattern is the signal.

---

## The Problem

Vibe-coding works — remarkably well — until it doesn't. And when it fails, it fails silently. The agent introduces a malformed import at edit 5. Nothing flags it because the surrounding code is syntactically valid. Thirty edits later the codebase is syntactically valid and architecturally broken. The type error compounded. The hallucinated import propagated. The function that was supposed to be decomposed got stuffed with task-specific logic because nobody told the agent the project has a compositional architecture.

The code problems, however, are downstream. Before the agent writes the wrong code, it *reasons* wrong. It tries an approach, fails, and instead of updating its model tries a variant of the same approach. It encounters an error, moves on, and encounters the same error twenty minutes later — having learned nothing. It acts before it understands, running commands faster than it can incorporate the results.

This is **behavioral drift**: the agent's reasoning strategy diverging from effective problem-solving. It is upstream of code drift. Catch the reasoning failure early enough and you never write the bad code.

A senior engineer would catch all of this — not by being smarter, but by having instincts. Check imports after refactoring. Verify types after changing signatures. Notice when complexity creeps. Remember that the same mistake happened last Tuesday. These aren't intelligence problems. They're discipline problems. And right now, the only way to get that discipline is to already be (or hire) a senior engineer. That's an access problem, and it's the one LintGate is trying to solve.

---

## What It Does

LintGate operates as a PostToolUse hook (firing after every write, edit, or bash command) and as an MCP server (35 on-demand tools).

### Code Quality Supervision

- **5-phase lint pipeline**: change classification → tier selection → parallel linting (18 linters) → result aggregation → agent-formatted reporting. Silent when nothing is wrong (`{}`). Compact when something is.
- **Tiered linter selection**: T0 (syntax/formatting) → T1 (imports, context rules) → T2 (types, complexity, security, supply-chain) → T3 (architecture, dead code, full security). Default is aggressive: Tier 2 for ordinary code changes, because catching a type error three seconds after it's written is cheaper than debugging it thirty edits later.
- **18 linters**: ruff, mypy, ty, radon, bandit, vulture, pip-audit, plus 11 built-in AST analyzers covering imports, complexity, structure, performance, architecture, dead code, context rules, and redefinitions. Missing tools silently skipped — works out of the box with just ruff.
- **Auto-fix**: `lint_fix` applies safe corrections (formatting, import sorting, simple refactors) without human intervention. Dry-run by default.
- **Professional instinct layer**: command-class hygiene prechecks (venv active before installing? lockfile fresh before publishing? secrets in the staged diff?), supply-chain vulnerability scanning, type integrity verification — the reflexive checks experienced engineers perform that LLM agents skip.

### Behavioral Drift Detection

- **9 detection rules**: approach cycling (3+ failed approaches in 30 min), failure amnesia (same error repeated), brute-force escalation (more approaches than constraints understood), premature action (high bash:read ratio + high failure rate), serial discovery (all constraints discovered reactively), tool repetition, verification debt, stale model, consecutive failures.
- **Intent bias layer**: classifies every tool use into 6 intent categories (inspect, modify, verify, execute, meta, unknown) and uses the pattern to adjust signal confidence. Deterministic — no LLM calls.
- **Behavioral compass**: tracks live hypotheses, approach history, coverage metrics, and action timelines across a session. Co-construction via `constraint_check` — asks the agent to articulate its constraint model, then computes what's missing.

### Multi-Channel Coherence

- **ControlPlane supervision mesh**: 6 independent channels (lint, tests, deps, git, behavior, structure) running in parallel. A coherence engine reads the pattern of agreement and disagreement across channels.
- **5 coherence states**: stable → isolated → coupled → systemic → degraded. Silence is diagnostic — when three channels pass and one fails, the passing channels are actively narrowing your problem space. (The Monty Hall effect applied to system diagnosis.)
- **Trajectory awareness**: session memory tracks coherence state over time. Regressions, persistent failures, and resolutions are distinct signals.

### Theory and Context

- **Theory extraction**: scans all project markdown and builds a 6-facet profile (core theory, problem-solving, alignment, architecture, anti-patterns, key abstractions) plus enforceable rules. Deterministic, no LLM calls.
- **Context file management**: generates, audits, and evolves CLAUDE.md/AGENTS.md. Dead path detection, contradiction checking, staleness monitoring, rule coverage analysis.
- **Living context**: behavioral discoveries flow back as patches to CLAUDE.md managed sections. The project's self-model evolves with each session.

### Cross-Session Learning

- **Global behavior profiles**: aggregate behavioral patterns across sessions to warm-start new sessions. Alpha decay ensures local data always dominates.
- **Model calibration**: 5-task behavioral micro-probe profiles a model's coding tendencies via revealed policy (what it does, not what it says), producing model-specific guardrails injected into context files.
- **Pattern bank**: tracks recurring anti-patterns across runs. The constraint proposer translates persistent patterns into enforceable rules.

---

## Why It Matters

If this works — even partially — the impact is bigger than speedups.

Right now, producing professional-grade software requires professional-grade instincts. You either have them (years of practice) or you hire someone who does. That's a bottleneck, and it means "idea -> professional repo" is gated by access to engineering expertise. LintGate is an attempt to automate that gate open — to turn vibe coding from improvisation into a repeatable engineering process that non-experts can use responsibly.

The calibration data is consistent with this. An unsupervised agent spends roughly 64% of its token budget on discipline problems — not hard problems, *boring* problems. Retrying approaches that already failed. Debugging errors a linter would have caught at write time. Re-reading files it dirtied with formatting drift. In one calibration session, an agent spent ~90 minutes in a death spiral. 83% of the constraints it eventually discovered were predictable from prior failures.


| Metric                                           | Unsupervised | Supervised (LintGate)       |
| ------------------------------------------------ | ------------ | --------------------------- |
| Effective duty cycle (tokens on novel reasoning) | ~36%         | ~78%                        |
| Tokens wasted on discipline failures             | ~64%         | ~22% (deterministic, cheap) |
| Efficiency loss factor                           | ~4x          | ~1.2x                       |


The 18% supervision overhead displaces waste that accumulates at 3-4x the rate. But the efficiency gain isn't the most interesting part. When discipline failures set an artificial ceiling on what an agent can handle, removing them doesn't just make it faster — it lets the agent attempt problems that previously exceeded its effective capacity. The ceiling was never the model's reasoning. It was cumulative drift degrading the working state.

See [docs/design.md](docs/design.md) for calibration data, detailed cost profiles, and scaling analysis.

### Validation: Physician, Heal Thyself

LintGate's strongest evidence comes from auditing its own codebase.

On 2026-02-20, Claude Opus 4.6 was given a single directive: "professionalize this codebase." LintGate's ControlPlane diagnosed its own code as **"systemic"** — multiple structural failures across lint, structure, and tests indicating an architectural problem, not isolated issues. The agent then autonomously executed 6 major module decompositions and 2 complexity reductions across 3 context windows, ~33,700 LOC, with zero human intervention beyond the initial instruction.

Results:

| Metric | Value |
| --- | --- |
| Blockers resolved | 18/20 (90%, remaining 2 are false positives) |
| Regressions introduced | 0 |
| Tests passing at every step | 1,611 |
| Creation:Debugging:Verification ratio | **55:0:15** |
| Total cost | ~$21.28 (~$1.18/blocker) |
| LintGate-specific overhead | 12% of session tokens |

The number that matters is the zero. In a normal agentic coding session, the overwhelming majority of tokens go to debugging — the write-fail-rewrite-fail-differently loop where context degrades and errors compound. In this session, the agent wrote correct code on every attempt. There was nothing to debug. The entire debugging phase of software development — the most expensive, most failure-prone phase — simply didn't occur.

This is the empirical signature of catching reasoning failures upstream of code failures. The agent never entered the degraded state where debugging becomes necessary, because the supervision infrastructure prevented the drift that normally makes each successive edit worse than the last.

The recursive structure matters: LintGate diagnosed its own codebase as needing fundamental restructuring, then *was the infrastructure that enabled that restructuring to succeed autonomously*. The tool that tells agents how to maintain discipline was itself maintained by the discipline it provides.

One practical implication: CLI flags like `--dangerously-skip-permissions` exist because fully autonomous agent operation has historically been unsafe — one bad decision in minute three compounds into a destroyed codebase by minute thirty. When the supervision infrastructure prevents that compounding, the "dangerously" becomes a historical artifact. Autonomous operation isn't inherently dangerous. Autonomous operation *without feedback loops that prevent drift* is dangerous.

Full session data: [docs/retrospectives/lintgate-2026-02-20-tier2-audit.md](docs/retrospectives/lintgate-2026-02-20-tier2-audit.md)

### Research Context

LintGate is alignment research in product form: design the interaction structure so the human-agent system stays coherent under pressure.

It's an applied instance of a broader research program on *relational engineering* — the working hypothesis that alignment quality is a property of the human-model interaction, not of the model alone. Behavioral drift, session-level degradation, the gap between formally correct outputs and genuinely productive process — these are interaction failures, addressable by deliberately constructing interaction architectures that maintain the conditions for genuine co-construction.

The theory is exploratory and instrumented. We evaluate by operational usefulness: fewer retries per resolved issue, less time in dead-end loops, faster convergence on root causes. Companion papers develop the ontological and phenomenological foundations; LintGate is where they touch the ground.

---

## Quick Start

```bash
# One-command setup: venv, install, hook, MCP config, agent integration
bash setup.sh          # full suite (ruff, mypy, ty, radon, bandit, vulture, pip-audit)
bash setup.sh --minimal  # just ruff + MCP
```

This creates the venv, installs dependencies, configures the PostToolUse hook, registers the MCP server, and auto-detects LLM coding agents on your system (Claude Code, Cursor, Copilot, Windsurf, Gemini CLI, Cline, Roo Code, Aider, Amazon Q).

Or manually:

```bash
cd /path/to/lintgate
uv venv .venv && uv pip install -e ".[dev]"
bash integrate.sh  # detect agents and generate config files
```

Configure the PostToolUse hook in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit|MultiEdit|Bash",
      "hooks": [{
        "type": "command",
        "command": "/path/to/lintgate/.venv/bin/lintgate"
      }]
    }]
  }
}
```

Configure the MCP server in `~/.mcp.json` (or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "lintgate": {
      "command": "/path/to/lintgate/.venv/bin/lintgate-mcp",
      "args": []
    }
  }
}
```

The hook fires automatically on every code change. The MCP server provides 35 on-demand tools. Both use the same venv — always point to the venv binaries, not system Python.

### First 5 Minutes

1. `getting_started(path)` — project-specific onboarding, setup status, next actions.
2. `controlplane_run(path)` — full health check across 6 channels. Works without config.
3. `controlplane_get_details(run_id)` — drill into findings that matter.
4. `lint_fix(path, dry_run=True)` — preview safe fixes, then `dry_run=False` to apply.
5. `bootstrap_context_files(path, write=True)` — generate persistent project context.

### Agent Integration

Every LLM coding agent has its own discovery convention. LintGate uses a single source of truth (`AGENTS.md`) and generates minimal pointer files for each detected agent. Run `bash integrate.sh --list` to see detected agents, or `bash integrate.sh --clean` to remove generated files.

---

## The Bootstrap Progression

LintGate works from zero state and gets better as it accumulates signal:

**Stage 0 — Zero state.** No config, no history, no theory. The lint pipeline fires on every tool use with battle-tested defaults: 7 curated anti-patterns distilled from real sessions. Works immediately with just ruff installed.

**Stage 1 — Theory extraction.** `bootstrap_context_files` scans all project markdown and produces a CLAUDE.md with project-specific dispositions, guardrails, and enforceable rules. Where project signal is insufficient, zero-state defaults persist.

**Stage 2 — Model calibration.** A 5-task behavioral micro-probe profiles the model's coding tendencies by observing what it does (tool call order, verification cadence, constraint references), not what it says. The resulting signal risk vector produces model-specific guardrails — so a model prone to approach cycling gets explicit constraints about articulating its constraint model before a third attempt. Weak prior capped at 0.60 confidence; decays fast as real telemetry arrives.

**Stage 3 — Living context.** The ControlPlane detects recurring behavioral patterns, proposes constraints, and flows accepted constraints back into CLAUDE.md managed sections. The project's self-model evolves across sessions.

Each stage stands on its own. Stop at any stage and the system works — each layer is additive.

---

## How It Works

**The hook** fires on Write, Edit, MultiEdit, and Bash tool uses. Phase 1 classifies what changed and how risky it is. Phase 2 selects a lint tier (0-3, escalating strictness). Phase 3 runs linters in parallel with timeouts. Phase 4 deduplicates, applies exemptions, and splits findings by severity. Phase 5 formats a compact report — or `{}` when nothing is wrong.

**The ControlPlane** runs 6 independent channels in parallel. The coherence engine reads the pattern across silent and loud channels — a god-module flagged by both lint *and* structure is more likely a real problem than either signal alone. Session memory accumulates findings across runs. A constraint proposer turns recurring patterns into enforceable rules.

**The behavioral compass** tracks the agent's problem-solving strategy: live hypotheses, approach attempts, intent patterns, coverage metrics. When the strategy drifts — retrying failed approaches, ignoring discovered constraints, acting without verifying — the behavior channel catches it and intervenes before the bad reasoning produces bad code.

**The Architecture of Inquiry** closes the loop. Theory-grounded signals attach project-specific theory claims to behavioral findings. Falsifiable predictions create a feedback loop the agent can't fake — predict an exit code, system checks it, accuracy modulates future confidence. Living context patches flow behavioral discoveries back into CLAUDE.md. All five features are opt-in.

**Example flow (realistic):** Agent edits a module and introduces a bad import plus a too-large function. The hook runs a tier-selected lint pass, flags unresolved import + complexity signal, and returns targeted next actions. `controlplane_run` then confirms whether this is isolated or coupled with tests/structure. The agent applies `lint_fix` for safe edits, makes one focused manual fix, and re-runs checks instead of wandering through five speculative rewrites.

---

## MCP Tools (35)

> **Source of truth for tool count:** `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools | wc -l`

### Onboarding


| Tool              | Purpose                                                                |
| ----------------- | ---------------------------------------------------------------------- |
| `getting_started` | First-call orientation: setup status, essential workflow, next actions |


### Lint Pipeline


| Tool               | Purpose                                   |
| ------------------ | ----------------------------------------- |
| `lint_files`       | Lint specific files at a tier level       |
| `lint_project`     | Lint all Python files in a project        |
| `lint_get_details` | Drill into a previous lint run by run_id  |
| `lint_fix`         | Auto-fix safe issues (dry_run by default) |
| `lint_status`      | Linter availability, run history, metrics |


### ControlPlane


| Tool                          | Purpose                                                                  |
| ----------------------------- | ------------------------------------------------------------------------ |
| `controlplane_run`            | Full supervision mesh (lint + tests + deps + git + behavior + structure) |
| `controlplane_get_details`    | Drill into a ControlPlane run by run_id                                  |
| `controlplane_status`         | Config and channel status                                                |
| `controlplane_apply_repairs`  | Execute proposed repair actions                                          |
| `controlplane_report_repair`  | Report repair outcome                                                    |
| `controlplane_agent_feedback` | Accept/reject findings and constraint proposals                          |
| `controlplane_test_skeleton`  | Generate pytest skeleton from AST analysis                               |


### Behavioral Supervision


| Tool                   | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `hygiene_check`        | Command-class precondition checks before risky commands                 |
| `constraint_check`     | State known constraints, get coverage gaps/uncertainty/similar failures |
| `prediction_register`  | Register falsifiable predictions checked automatically on next events   |
| `behavior_precheck`    | Deprecated compatibility wrapper over the three orthogonal tools        |
| `global_memory_status` | Cross-session behavioral priors and bias adjustments                    |
| `global_memory_reset`  | Reset global behavior profile                                           |


### Model Calibration


| Tool                         | Purpose                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| `model_profile_status`       | Show calibration profile for a model or all models             |
| `model_profile_probe_start`  | Start 5-task behavioral micro-probe (revealed policy)          |
| `model_profile_probe_submit` | Submit task responses, score into signal_risk vector, persist  |


### Theory & Context


| Tool                         | Purpose                                            |
| ---------------------------- | -------------------------------------------------- |
| `build_theory_pack`          | Token-efficient theory digest (~500-1500 tokens)   |
| `get_theory_context`         | Deep retrieval of specific theory claims           |
| `extract_project_theory`     | Full theory profile (6 facets + enforceable rules) |
| `extract_theory_constraints` | DO NOT / MUST directives → proposed lint rules     |
| `context_guidance`           | Machine-usable rules and context signals           |
| `audit_context_health`       | CLAUDE.md/AGENTS.md quality audit                  |
| `bootstrap_context_files`    | Generate context files from theory + signals       |
| `context_patch_review`       | Show pending living-context patches                |
| `context_patch_apply`        | Apply patches to CLAUDE.md managed sections        |


### Dependencies & Telemetry


| Tool                  | Purpose                                           |
| --------------------- | ------------------------------------------------- |
| `dep_health_check`    | Comprehensive dependency health audit             |
| `dep_sync`            | Check sync status, optionally create venv or lock |
| `audit_tool_versions` | Compare installed versions against requirements   |
| `telemetry_summary`   | ROI dashboard: quality improvement vs token cost  |


---

## Configuration

LintGate works with **zero config** for any Python project with ruff installed. For project-specific tuning, create `.claude/lintgate.yaml`:

```yaml
# Tier escalation for critical paths
pipeline_critical_paths:
  - "src/control/pipeline.py"

# ControlPlane (opt-in)
controlplane:
  enabled: true
  channels:
    behavior:
      enabled: true
      thresholds:
        approach_cycling_count: 3
        failure_amnesia_lookback: 30

  # Architecture of Inquiry (opt-in)
  inquiry:
    theory_grounded_signals: true
    prediction_tracking: true
    theory_coherence_check: true
    living_context: true
    session_gate: true
```

See [docs/design.md](docs/design.md) for the full configuration reference, calibration data, token economics, and design philosophy.

---

## Project Structure

```
lintgate/
├── lintgate/                        # Core package
│   ├── hook_posttooluse.py          # PostToolUse hook entry point
│   ├── change_classifier.py         # Phase 1: What changed and how risky
│   ├── tier_selector.py             # Phase 2: Which linters to run
│   ├── lint_runner.py               # Phase 3: Parallel execution with timeouts
│   ├── results_aggregator.py        # Phase 4: Deduplicate, exempt, split severity
│   ├── agent_reporter.py            # Phase 5: Format for agent consumption
│   ├── linters/                     # 18 linter implementations
│   ├── controlplane/                # Supervision mesh + behavioral compass
│   └── channels/                    # 6 independent analysis channels
├── mcp_server.py                    # MCP bootstrap
├── mcp_tools/                       # 35 MCP tool definitions
├── tests/                           # 1500+ tests
├── docs/
│   └── design.md                    # Full architecture + economics + philosophy
├── AGENTS.md                        # Universal agent entry point
├── setup.sh                         # One-command install + configure
├── integrate.sh                     # Agent detection + config generation
└── pyproject.toml                   # MIT, Python >=3.10
```

---

## Design Deep Dive

[docs/design.md](docs/design.md) has the full architecture: the 5-phase pipeline, the 18-linter registry, the coherence engine, behavioral drift detection (9 rules, intent taxonomy, signal coordination), the Architecture of Inquiry, cross-session learning, model calibration, token economics with calibration data, and the design philosophy that ties it together.

---

## License

MIT

## Author

Rohan Vinaik
