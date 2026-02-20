# LintGate

Real-time quality supervision for AI-generated code — from a biochemist who vibe-codes everything and got tired of watching agents waste their intelligence on discipline problems.

The core insight: **multiple cheap, lossy lenses trained on the same codebase compose into something that looks like intelligence.** No individual tool can tell you "the agent is building on a broken abstraction." But when the type checker, the import analyzer, the complexity monitor, and the test runner all disagree in a specific pattern, that disagreement is diagnostic. This is not AI. It is the disciplined application of many bad instruments whose errors are uncorrelated — and whose agreement, therefore, means something.

LintGate fires every time an LLM coding agent writes, edits, or executes a command that modifies files — classifying what changed, selecting appropriate linters, running them in parallel, and reporting structured results back before the next action. When nothing is wrong, you see nothing. When something is wrong, the agent knows immediately. And when the agent's *reasoning strategy* starts to drift — retrying failed approaches, ignoring discovered constraints, acting faster than it understands — the behavioral compass catches that too.

---

## What It Does

- **5-phase lint pipeline**: change classification → tier selection → parallel linting (15 linters) → result aggregation → agent-formatted reporting
- **ControlPlane supervision mesh**: 5 independent channels (lint, tests, deps, git, behavior) running in parallel with cross-channel coherence analysis
- **Behavioral drift detection**: 9 detection rules (approach cycling, failure amnesia, premature action, brute-force escalation, verification debt, stale model, serial discovery, tool repetition, consecutive failures) with intent-aware bias and signal coordination
- **Theory extraction**: 6 theory facets + enforceable rules extracted from all project markdown — deterministic, no LLM calls
- **Architecture of Inquiry**: 5 opt-in features that close the loop between behavioral detection and theory extraction — theory-grounded signals, falsifiable predictions, coherence checking, living context patches, session readiness gates
- **Cross-session learning**: global behavioral priors that warm-start new sessions, with alpha decay to zero as local data accumulates
- **Model calibration**: per-model behavioral profiling via a 5-question deterministic probe, producing model-specific guardrails and anti-patterns that are injected into context files. Passive EMA refinement across sessions.
- **Anti-drift systems**: issue memory, pattern bank, context rule enforcement
- **Zero-config usability**: works on any Python project with just ruff installed. Every additional tool is additive.
- **Token-efficient**: silent when nothing is wrong (`{}`), compact when something is, delta-only on subsequent runs

## Why It Matters

An unsupervised LLM coding agent spends roughly 64% of its token budget on discipline problems — approach cycling, failure amnesia, formatting drift, re-discovering constraints it already encountered. These are not intelligence problems. They are discipline problems, and discipline is exactly what LLMs are worst at and deterministic systems are best at.

LintGate acts as a prefrontal cortex for the agent: not doing the thinking, but deciding what is worth thinking about, and interrupting when the thinking has gone off the rails. Calibration data from a real 3.5-hour baseline session shows the supervised agent's effective duty cycle (tokens on novel reasoning) at ~78% vs ~36% unsupervised — not because supervision catches more bugs, but because it prevents the agent from ever spending its intelligence budget on problems that are not intelligence problems.

See [docs/design.md](docs/design.md) for the full economic analysis, calibration data, and design philosophy.

---

## Quick Start

```bash
# One-command setup: venv, install, hook, MCP config, agent integration
bash setup.sh          # full suite (ruff, mypy, radon, bandit, vulture)
bash setup.sh --minimal  # just ruff + MCP
```

This does everything: creates the venv, installs dependencies, configures the PostToolUse hook, registers the MCP server, and auto-detects LLM coding agents on your system (Claude Code, Cursor, Copilot, Windsurf, Gemini CLI, Cline, Roo Code, Aider, Amazon Q) to generate config files pointing each to `AGENTS.md`.

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

The hook fires automatically on every code change. The MCP server provides 32 on-demand tools. Both use the same venv — always point to the venv binaries, not system Python.

### Agent Integration

Every LLM coding agent has its own auto-discovery convention. Rather than maintaining duplicate instruction files, LintGate uses a single source of truth (`AGENTS.md`) and generates minimal pointer files for each detected agent. Run `bash integrate.sh --list` to see which agents are detected, or `bash integrate.sh --clean` to remove generated files. See `AGENTS.md` for the full discovery table.

### First 5 Minutes (Cold Start)

1. `getting_started(path)` to get project-specific onboarding, setup state, and next actions.
2. `controlplane_run(path)` for a full health check (works even without config).
3. `controlplane_get_details(run_id)` to inspect findings you care about.
4. `lint_fix(path, dry_run=True)` to preview safe fixes, then `dry_run=False` to apply.
5. `bootstrap_context_files(path, write=True)` to generate persistent project context files.

---

## How It Works

**The hook** fires on Write, Edit, MultiEdit, and Bash tool uses. Phase 1 classifies what changed and how risky it is. Phase 2 selects a lint tier (0-3, escalating strictness). Phase 3 runs selected linters in parallel with timeouts. Phase 4 deduplicates, applies exemptions, and splits findings by severity. Phase 5 formats a compact report for the agent — or `{}` when nothing is wrong.

**The ControlPlane** runs 5 independent channels in parallel — lint (wraps the pipeline), tests (discovery + execution), deps (lockfile, venv, churn), git (state analysis), and behavior (9 detection rules for agent reasoning drift). A coherence engine reads the pattern across silent and loud channels. Session memory accumulates findings across runs. A constraint proposer turns recurring patterns into enforceable rules.

**The Architecture of Inquiry** closes the loop. Theory-grounded signals attach project-specific theory claims to behavioral findings ("3 approaches attempted in 20min" becomes "3 approaches attempted in 20min. Theory: Constraint maps become architectures of possibility."). Falsifiable predictions create a feedback loop the agent can't fake — predict an exit code, system checks it, accuracy modulates future signal confidence. Living context patches flow behavioral discoveries back into CLAUDE.md's managed sections, making the project's self-model evolve with each session. All five features are opt-in via `controlplane.inquiry.*` config.

---

## The Bootstrap Process

LintGate is designed to be useful from zero state — no project history, no theory, no model calibration — and to become progressively more effective as it accumulates signal. This is the progression from a cold start to full cognitive integration:

**Stage 0 — Zero state.** The agent has never seen this project. No CLAUDE.md, no theory profile, no calibration. LintGate fires on every tool use via the PostToolUse hook using battle-tested defaults: 7 curated anti-patterns distilled from field experience (approach cycling, serial constraint discovery, failure amnesia, root-cause clustering, bounded checklists, layered signal composition, performance anti-patterns) and 4 facet fallbacks covering core theory, problem-solving, alignment, and architecture. These are not placeholders — they are the lessons LintGate learned from hundreds of real sessions, and they provide useful guardrails even before the system knows anything about the specific project.

**Stage 1 — Theory extraction.** `bootstrap_context_files` scans all markdown in the project and extracts a theory profile: 6 facets (core theory, problem-solving, alignment, architecture, anti-patterns, key abstractions) plus enforceable rules. This produces a CLAUDE.md with project-specific dispositions, guardrails, and managed sections for living context. Once theory exists, the zero-state defaults are replaced by project-grounded content — but only where the project has enough signal. Facets without claims fall back to the battle-tested defaults rather than empty placeholders.

**Stage 2 — Model calibration.** `model_profile_probe_start` presents 5 multiple-choice questions that reveal the model's behavioral tendencies. The agent answers, `model_profile_probe_submit` scores the responses deterministically into a signal risk vector, derives model-specific anti-patterns and guardrail dispositions, and persists the profile at `~/.lintgate/model_profiles.json`. The profile is keyed by canonical `provider:model` format (e.g., `anthropic:claude-opus-4`) and applies only when the model key matches exactly. On subsequent calls to `bootstrap_context_files(model_id='...')`, the system injects model-biased guardrails into CLAUDE.md — so a model with high approach-cycling risk gets explicit guardrails about running `behavior_precheck` before a 3rd attempt.

**Stage 3 — Living context.** As the agent works, the ControlPlane's behavior channel detects patterns. Recurring signals generate constraint proposals. Accepted constraints become `context_patch` entries that flow back into CLAUDE.md's managed sections. The project's self-model evolves with each session. Passive telemetry refinement (EMA alpha=0.15) nudges the model profile toward observed behavior — the probe anchors the profile, and real-world signal fires adjust it over time.

Each stage is independently useful. A project at Stage 0 still gets the hook pipeline, the ControlPlane mesh, and sensible defaults. Stage 1 adds project grounding. Stage 2 adds model awareness. Stage 3 adds cross-session evolution. You can stop at any stage and the system works — each layer is additive.

---

## MCP Tools (32)

Source of truth: `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools | wc -l`

### Onboarding

| Tool | Purpose |
|------|---------|
| `getting_started` | First-call orientation: setup status, essential workflow, and next actions |

### Lint Pipeline

| Tool | Purpose |
|------|---------|
| `lint_files` | Lint specific files at a tier level |
| `lint_project` | Lint all Python files in a project |
| `lint_get_details` | Drill into a previous lint run by run_id |
| `lint_fix` | Auto-fix safe issues (dry_run by default) |
| `lint_status` | Linter availability, run history, metrics |

### ControlPlane

| Tool | Purpose |
|------|---------|
| `controlplane_run` | Full supervision mesh (lint + tests + deps + git + behavior) |
| `controlplane_get_details` | Drill into a ControlPlane run by run_id |
| `controlplane_status` | Config and channel status |
| `controlplane_apply_repairs` | Execute proposed repair actions |
| `controlplane_report_repair` | Report repair outcome |
| `controlplane_agent_feedback` | Accept/reject findings and constraint proposals |
| `controlplane_test_skeleton` | Generate pytest skeleton from AST analysis |

### Behavioral Supervision

| Tool | Purpose |
|------|---------|
| `behavior_precheck` | State constraints + predictions before Bash commands |
| `global_memory_status` | Cross-session behavioral priors and bias adjustments |
| `global_memory_reset` | Reset global behavior profile |

### Model Calibration

| Tool | Purpose |
|------|---------|
| `model_profile_status` | Show calibration profile status for a model or all models |
| `model_profile_probe_start` | Start a 5-question behavioral calibration probe |
| `model_profile_probe_submit` | Submit probe answers, score into signal_risk vector, persist profile |

### Theory & Context

| Tool | Purpose |
|------|---------|
| `build_theory_pack` | Token-efficient theory digest (Tier 1: ~500-1500 tokens) |
| `get_theory_context` | Deep retrieval of specific theory claims |
| `extract_project_theory` | Full theory profile (6 facets + enforceable rules) |
| `extract_theory_constraints` | DO NOT / MUST directives → proposed lint rules |
| `context_guidance` | Machine-usable rules and context signals |
| `audit_context_health` | CLAUDE.md/AGENTS.md quality audit |
| `bootstrap_context_files` | Generate context files from theory + signals. Pass `model_id` for model-aware calibration. |
| `context_patch_review` | Show pending living-context patches |
| `context_patch_apply` | Apply patches to CLAUDE.md managed sections |

### Dependencies & Telemetry

| Tool | Purpose |
|------|---------|
| `dep_health_check` | Comprehensive dependency health audit |
| `dep_sync` | Check sync status, optionally create venv or lock |
| `audit_tool_versions` | Compare installed versions against requirements |
| `telemetry_summary` | ROI dashboard: quality improvement vs token cost |

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

See [docs/design.md](docs/design.md) for the full configuration reference.

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
│   ├── registry.py                  # Linter discovery and registration
│   ├── config.py                    # YAML config loading + auto-detection
│   ├── types.py                     # Shared types (LintIssue, ChangeClassification, etc.)
│   ├── state.py                     # Persistent issue memory, metrics, run details
│   ├── telemetry.py                 # ROI telemetry aggregation
│   ├── lint_fixer.py                # Auto-fix via ruff (safe rules + format)
│   ├── pattern_bank.py              # Categorical anti-tail-chasing
│   ├── context_guidance.py          # CLAUDE.md/AGENTS.md parsing
│   ├── context_auditor.py           # Context file health checks + session readiness
│   ├── bootstrap_defaults.py         # Battle-tested zero-state anti-patterns + facet fallbacks
│   ├── context_bootstrap.py         # Context file generation + managed sections + patches
│   ├── theory_extractor.py          # Project theory profiling (6 facets + enforceable rules)
│   ├── versioning.py                # Tool version auditing
│   ├── dependency_health.py         # Dependency health monitoring
│   ├── linters/                     # 15 linter implementations
│   ├── controlplane/                # ControlPlane supervision mesh
│   │   ├── types.py                 # SupervisionEvent, MeshResult, InquiryConfig
│   │   ├── behavior_compass.py      # Hypothesis lifecycle, predictions, intent taxonomy
│   │   ├── constraint_proposer.py   # Pattern → rule proposals + theory coherence
│   │   ├── session_memory.py        # Cross-run state + theory profile cache
│   │   ├── global_behavior_profile.py # Cross-session priors (alpha decay)
│   │   ├── model_profiles.py        # Model-specific behavioral profiles + persistence
│   │   ├── model_probe.py           # 5-question deterministic calibration probe
│   │   └── ...                      # channel.py, runtime.py, coherence.py, reporter.py, etc.
│   └── channels/                    # ControlPlane channel implementations
│       ├── behavior_channel.py      # 9 detection rules + theory grounding
│       └── ...                      # lint, test, dependency, git channels
├── mcp_server.py                    # MCP bootstrap + shared helpers
├── mcp_tools/                       # MCP domain modules (32 tool definitions)
├── tests/                           # 890+ tests
├── docs/                            # Design deep dive, field reports
│   └── design.md                    # Full architecture + calibration + philosophy
├── AGENTS.md                        # Universal agent entry point (single source of truth)
├── SKILL.md                         # MCP onboarding card
├── setup.sh                         # One-command install + configure
├── integrate.sh                     # Agent detection + config generation
└── pyproject.toml                   # MIT, Python >=3.10
```

---

## Design Deep Dive

See [docs/design.md](docs/design.md) for:
- The 5-phase pipeline in detail (with ASCII diagram)
- Tiered lint selection and the 15-linter registry
- ControlPlane architecture: coherence engine, session memory, constraint proposer
- Behavioral drift detection: 9 rules, intent taxonomy, signal coordination
- Architecture of Inquiry: theory grounding, predictions, coherence, living context
- Cross-session learning: global profiles, alpha decay, safety properties
- Model calibration: per-model behavioral profiling, bootstrap progression, EMA refinement
- Token economics: calibration data, duty cycle analysis, scaling properties
- Design philosophy: 13 principles
- Design lineage: 4 precursor projects

---

## License

MIT

## Author

Rohan Vinaik
