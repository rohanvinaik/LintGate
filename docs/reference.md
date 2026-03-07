# LintGate Reference

Technical reference for LintGate's 81 MCP tools, configuration, and project structure. For the narrative overview, see [README.md](../README.md). For architecture deep dive, see [design.md](design.md).

---

## MCP Tools (74)

> **Source of truth for tool count:** `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools/*.py | wc -l` (target `*.py` to avoid pycache matches)

LintGate operates as both a PostToolUse hook (automatic, fires on every code change) and an MCP server (on-demand tools). The hook requires no interaction. The tools below are called explicitly.

### Onboarding

| Tool              | Purpose                                                                |
| ----------------- | ---------------------------------------------------------------------- |
| `getting_started` | First-call orientation with startup automation (auto config scaffold + auto venv provision + missing-tool diagnostics + next actions) |
| `scaffold_config` | Generate project-specific lintgate.yaml from observed signals |
| `setup_github_quality` | Generate Code Climate/SonarCloud configs, SonarCloud + qlty + security-lite workflows, README badges, .gitignore augmentation |
| `tool_applicability_guide` | Definitive guide on when and how to use each LintGate MCP tool |

### Lint Pipeline

| Tool               | Purpose                                   |
| ------------------ | ----------------------------------------- |
| `lint_files`       | Lint specific files at a tier level       |
| `lint_project`     | Lint all Python files in a project        |
| `lint_get_details` | Drill into a previous lint run by run_id  |
| `lint_fix`         | Auto-fix safe issues (dry_run by default) |
| `lint_status`      | Linter availability, run history, metrics, and missing-tool install hints |

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

### Performance Analysis

| Tool                      | Purpose                                                              |
| ------------------------- | -------------------------------------------------------------------- |
| `inspect_algebra`         | Inspect algebraic property manifest (purity, cacheability, parallelism) |
| `generate_property_tests` | Generate Hypothesis + icontract templates from algebraic properties  |
| `analyze_test_strength`   | Test assertion quality: vulnerability scores, semantic ratios, upgrades |
| `inspect_test_assertions` | Drill into a single test file: every assertion classified by kind/strength |
| `run_property_tests`     | Execute generated property tests for a function and capture counterexamples |

### Bootstrap

| Tool                   | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `bootstrap_tests`      | Trigger test bootstrap pipeline (skeletons, property tests, contracts)  |
| `bootstrap_status`     | Check bootstrap pipeline status, phase, artifacts, errors               |

### Behavioral Supervision

| Tool                   | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `hygiene_check`        | Command-class precondition checks before risky commands                 |
| `constraint_check`     | State known constraints, get coverage gaps/uncertainty/similar failures |
| `prediction_register`  | Register falsifiable predictions checked automatically on next events   |
| `behavior_precheck`    | Deprecated compatibility wrapper over the three orthogonal tools        |
| `global_memory_status` | Cross-session behavioral priors and bias adjustments                    |
| `global_memory_reset`  | Reset global behavior profile                                           |

### Habit Mode

| Tool               | Purpose                                                             |
| ------------------ | ------------------------------------------------------------------- |
| `declare_mode`     | Self-declare "habit" or "standard" mode (immediate, no sustain wait)|
| `habit_status`     | Read-only status: habit score, signals, active files, token state   |
| `habit_compact`    | Trigger compaction NOW, returns structured Habit State Snapshot     |
| `habit_configure`  | Runtime threshold adjustment (session-scoped, clamped to safe ranges)|

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
| `toolchain_health_check` | Check CLI tool availability against the toolchain manifest |
| `telemetry_summary`   | ROI dashboard: quality improvement vs token cost  |

### Compass

| Tool                 | Purpose                                                                   |
| -------------------- | ------------------------------------------------------------------------- |
| `compass_status`     | Show compass axes, depths, gap report, staleness, and cognitive mode      |
| `compass_check`      | Check an action against toward/away/forbidden directives                  |
| `compass_update`     | Re-extract compass from project docs, optionally render context files     |
| `compass_interview`  | Gap-filling interview — returns prioritized questions or applies answers  |
| `compass_reset`      | Scoped state reset (compass/session/project/global), dry-run default      |
| `theory_mode_enter`  | Enter theory exploration mode                                             |
| `theory_mode_freeze` | Freeze compass and exit theory mode to normal                             |
| `setup_hooks`        | Generate .claude/settings.json hook configuration for compass-aware hooks |

### NSIL

| Tool                       | Purpose                                                              |
| -------------------------- | -------------------------------------------------------------------- |
| `nsil_inference_snapshot`  | Point-in-time snapshot of the agent's inference context              |
| `nsil_verify_action`      | Verify an action against behavioral constraints and gate contracts   |
| `nsil_export_training_data`| Export collected alignment and compliance data for model fine-tuning |
| `nsil_benchmark`           | Run NSIL enforcement benchmarks                                     |

### Convergence Analysis

| Tool                      | Purpose                                                              |
| ------------------------- | -------------------------------------------------------------------- |
| `convergence_analyze`     | Multi-lens convergence aggregation on functions and files            |
| `extraction_plan`         | Build stepwise extraction plan for a specific function               |
| `optimization_landscape`  | Project-wide optimization opportunity map                            |

### Specification Complexity

| Tool                | Purpose                                                                  |
| ------------------- | ------------------------------------------------------------------------ |
| `spec_analyze`      | Specification complexity analysis (sigma, regime, phase, risk, DFT)      |
| `spec_prescribe`    | Risk-prioritized test prescriptions with expanded taxonomy               |
| `spec_composition`  | Composition gap and sheaf condition analysis across modules              |
| `spec_gate_check`   | Optimization gate validation with stop criteria                          |

### Refactor Checkpointing

| Tool                  | Purpose                                                                |
| --------------------- | ---------------------------------------------------------------------- |
| `refactor_checkpoint` | Record progress on a file during a refactoring session                 |
| `refactor_resume`     | Load refactor state and provide structured summary for session resumption |
| `refactor_thesis`     | Record or update the agent's structural thesis about the codebase      |

### GitHub Project Organization

| Tool                       | Purpose                                                         |
| -------------------------- | --------------------------------------------------------------- |
| `project_organize_audit`   | Audit GitHub labels, milestones, templates, wiki status         |
| `project_organize_apply`   | Create missing labels and milestones (dry-run by default)       |
| `project_wiki_sync`        | Sync theory/compass/design data to GitHub wiki pages            |
| `project_wiki_read`        | Read a GitHub wiki page (local clone or remote)                 |
| `wiki_materialize`         | Generate wiki pages locally from manifest (dry-run or write)    |
| `wiki_publish_pages`       | Publish wiki pages as a static GitHub Pages site                |
| `wiki_check_links`         | Check link integrity across all materialized wiki pages         |
| `wiki_status`              | Show wiki freshness state — stale/fresh/missing page counts     |

### Contract Auditing

| Tool                       | Purpose                                                         |
| -------------------------- | --------------------------------------------------------------- |
| `contract_audit`           | Audit channel metric contract health (wiring, sheaf, size)      |

---

## Configuration

LintGate works with **zero config** for any Python project with ruff installed. For project-specific tuning, create `.claude/lintgate.yaml`:

```yaml
# Quality policy — single source of truth for CI and LintGate gates
quality_policy:
  coverage:
    global_threshold: 80          # coverage telemetry target (non-blocking)
    diff_threshold: 80            # diff-coverage telemetry target (non-blocking)
    source_packages:              # --cov=<pkg> (measure code, not tests)
      - lintgate
      - mcp_tools
  security:
    tolerated_false_positives:    # Encoded in sonar-project.properties too
      - rule: "pythonsecurity:S2083"
        file: "lintgate/reset.py"
        reason: "Read-modify-writeback of same local file"

# Optional: align CI coverage scope with integration-heavy repos
# via .coveragerc (for example, omit generated test fixture packages).

# Tier escalation for critical paths
pipeline_critical_paths:
  - "src/control/pipeline.py"

# ControlPlane (opt-in)
controlplane:
  enabled: true
  channels:
    tests:
      symbol_coverage:
        enabled: true             # primary pass/fail gate (changed symbols)
    behavior:
      enabled: true
      thresholds:
        approach_cycling_count: 3
        failure_amnesia_lookback: 30

  # Habit Mode — context window management (opt-in)
  habit_mode:
    enabled: true
    compact_threshold: 0.40        # Compact at 40% of context window
    enter_score: 0.70              # habitScore to enter habit mode
    exit_score: 0.40               # habitScore to exit habit mode
    sustain_calls: 5               # Must sustain enter_score for N calls
    token_api_interval: 15         # API calibration every N tool calls

  # Architecture of Inquiry (opt-in)
  inquiry:
    theory_grounded_signals: true
    prediction_tracking: true
    theory_coherence_check: true
    living_context: true
    session_gate: true
```

See [design.md](design.md) for the full configuration reference, calibration data, and design philosophy.

---

## Feature Detail

### Code Quality Supervision

- **5-phase lint pipeline**: change classification → tier selection → parallel linting (18 linters) → result aggregation → agent-formatted reporting. Silent when nothing is wrong (`{}`). Compact when something is.
- **Tiered linter selection**: T0 (syntax/formatting) → T1 (imports, context rules) → T2 (types, complexity, security, supply-chain) → T3 (architecture, dead code, full security). Default: Tier 2 for ordinary changes.
- **18 linters**: ruff, mypy, ty, radon, bandit, vulture, pip-audit, plus 11 built-in AST analyzers. Missing tools silently skipped — works out of the box with just ruff.
- **Auto-fix**: `lint_fix` applies safe corrections (formatting, import sorting, simple refactors) without human intervention. Dry-run by default.
- **Professional instinct layer**: command-class hygiene prechecks (venv active before installing? lockfile fresh before publishing? secrets in the staged diff?), supply-chain vulnerability scanning, type integrity verification.

### System-Mutation Pre-Execution Guard (Threat Model)

The `lintgate-pre` executable acts as a `PreToolUse` hook (specifically tuned for Claude Code) to intercept system-level commands that escape the project directory context.
- **Catches**: Global package installations (`brew install`, `pip install <pkg>`, `npm install -g`), system directory modifications (`/etc/`, `/usr/local/`, `~/Library/LaunchAgents`), shell configuration mutations (`~/.zshrc`), direct web execution (`curl | sh`), and privilege escalation (`sudo`).
- **Misses**: Scoped package manager scripts that execute arbitrary underlying code, obfuscated bash strings, explicit binary downloads to the project directory that are later executed, and `node_modules` post-install hooks.
- **Evasion & Override Limits**: Users can explicitly bypass the guard by appending ` # lintgate-override` to the exact command. This is not a formal sandbox—it is a behavioral guardrail designed against hallucinated prompt injection and out-of-bounds agent operations, not determined malicious actors with interactive shell access.

### Behavioral Drift Detection

- **9 detection rules**: approach cycling, failure amnesia, brute-force escalation, premature action, serial discovery, tool repetition, verification debt, stale model, consecutive failures.
- **Intent bias layer**: classifies every tool use into 6 intent categories and uses the pattern to adjust signal confidence. Deterministic — no LLM calls.
- **Behavioral compass**: tracks live hypotheses, approach history, coverage metrics, and action timelines. Co-construction via `constraint_check` — asks the agent to articulate its constraint model, then computes what's missing.

### Multi-Channel Coherence

- **6 independent channels**: lint, tests, deps, git, behavior, structure — running in parallel.
- **5 coherence states**: stable → isolated → coupled → systemic → degraded. Silence is diagnostic — when three channels pass and one fails, the passing channels narrow your problem space.
- **Trajectory awareness**: session memory tracks coherence state over time. Regressions, persistent failures, and resolutions are distinct signals.

### Theory and Context

- **Theory extraction**: scans project markdown and builds a 6-facet profile plus enforceable rules. Deterministic, no LLM calls.
- **Context file management**: generates, audits, and evolves CLAUDE.md/AGENTS.md. Dead path detection, contradiction checking, staleness monitoring.
- **Living context**: behavioral discoveries flow back as patches to CLAUDE.md managed sections. The project's self-model evolves with each session.

### Cross-Session Learning

- **Global behavior profiles**: aggregate patterns across sessions to warm-start new sessions. Alpha decay ensures local data dominates.
- **Model calibration**: 5-task behavioral micro-probe profiles model tendencies via revealed policy, producing model-specific guardrails.
- **Pattern bank**: tracks recurring anti-patterns. The constraint proposer translates persistent patterns into enforceable rules.

### Habit Mode

- **Context window management**: detects sustained execution phases (editing, testing, refactoring) and produces structured compaction snapshots that turn context loss into context refinement. Compaction ≠ loss — the deterministic system remembers so the stochastic system doesn't have to.
- **Dual persistence**: Path A (session-backed via ControlPlane state) for integrated sessions; Path B (standalone file-backed) for hook-only environments. Same core functions, different storage backends.
- **Signal-driven mode detection**: 8 signals from a sliding window of 20 tool events — read_edit_ratio, execute_pct, edit_streak, sub_agent_freq, inter_tool_gap_median, same_file_ratio, gather_pct, test_in_last_n — weighted into a composite habitScore with hysteresis (enter at 0.70×5, exit at 0.40).
- **Token tracker**: per-call char-count estimation (factor 0.25) with periodic Anthropic API calibration (free `count_tokens` endpoint, 500ms timeout, exponential backoff, 70/30 blend). Anti-thrash compaction gate prevents compaction storms.

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
│   ├── habit_mode.py                # Habit Mode: signal collection, mode detection, compaction
│   ├── token_tracker.py             # Token estimation, API calibration, economics
│   ├── linters/                     # 18 linter implementations
│   ├── controlplane/                # Supervision mesh + behavioral compass
│   └── channels/                    # 6 independent analysis channels
├── mcp_server.py                    # MCP bootstrap
├── mcp_tools/                       # 73 MCP tool definitions (incl. convergence_tools.py, habit_tools.py, compass_tools.py, bootstrap_tools.py)
├── tests/                           # 2,180+ tests
├── docs/
│   ├── design.md                    # Full architecture + economics + philosophy
│   └── reference.md                 # This file
├── AGENTS.md                        # Universal agent entry point
├── setup.sh                         # One-command install + configure
├── integrate.sh                     # Agent detection + config generation
└── pyproject.toml                   # MIT, Python >=3.10
```

---

## Setup Detail

### PostToolUse Hook

Configure in `~/.claude/settings.json`:

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

### MCP Server

Configure in `~/.mcp.json` (or project-level `.mcp.json`):

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

The hook fires automatically on every code change. The MCP server provides 82 on-demand tools. Both use the same venv — always point to the venv binaries, not system Python.

### Agent Integration

Every LLM coding agent has its own discovery convention. LintGate uses a single source of truth (`AGENTS.md`) and generates minimal pointer files for each detected agent. Run `bash integrate.sh --list` to see detected agents, or `bash integrate.sh --clean` to remove generated files.
