# LintGate Agent Tool Reference

> **Tool count**: 83 MCP tools. Source of truth: `grep -Rho '@mcp.tool()' mcp_server.py mcp_tools/*.py | wc -l`

## Ship Pipeline

Your job ends at `git push`. After push, the LintGate GitHub App (`lintgate[bot]`) handles everything:

- Creates/updates a PR and adds itself as required reviewer
- Monitors CI checks against `gate_contract.yaml`
- All green → approves, squash-merges, GPG-signs the commit
- Classifies any failures via GitHub Models (free tier) and creates structured issues
- Mechanical findings get `auto-fix` labels — local deterministic tools handle them without LLM inference
- Structural findings get `needs-reasoning` labels with theory context for your next session

Before pushing, run the local gate stack: `python scripts/ship_main.py` (or `--preflight` for dry-run).

## Tool Index by Domain

### Onboarding & Orientation

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `getting_started` | Project setup, first-session orientation | Session start, new project |
| `tool_applicability_guide` | Cadence/trigger/anti-pattern guidance for all tools | When unsure which tool to call |
| `scaffold_config` | Generate `lintgate.yaml` from observed signals | After first `controlplane_run` |
| `setup_github_quality` | GitHub badges, CI workflows, quality infrastructure | Project setup |
| `setup_hooks` | Generate `.claude/settings.json` hook config | Project setup |

### Lint Workflow

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `lint_files` | Lint specific files at a tier level | After editing Python files |
| `lint_project` | Lint all Python files in a project | Session start, before committing |
| `lint_get_details` | Drill into a previous run by `run_id` | After `lint_files`/`lint_project` |
| `lint_fix` | Auto-fix safe lint issues (ruff) | After lint reports fixable issues |
| `lint_status` | Linters, run history, context, version audits, metrics | Orientation |
| `audit_tool_versions` | Check linter version compatibility | When tools seem broken |
| `toolchain_health_check` | Check CLI tool availability | Session start, missing tools |

### ControlPlane (Multi-Channel Analysis)

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `controlplane_run` | Run 6-channel project health check | Session start, after significant changes |
| `controlplane_get_details` | Drill into a ControlPlane run | After `controlplane_run` |
| `controlplane_status` | Show ControlPlane config and status | Orientation |
| `controlplane_test_skeleton` | Generate pytest skeleton for a source file | When adding tests |
| `controlplane_apply_repairs` | Execute proposed repair actions | After reviewing repairs |
| `controlplane_report_repair` | Report repair outcome (applied/ignored/rejected) | After applying or skipping repair |
| `controlplane_agent_feedback` | Record disagreements or accept/reject constraints | When findings seem wrong |

### Behavior & Predictions

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `hygiene_check` | Command-class preconditions | Before install, commit, publish |
| `constraint_check` | Check action against constraint ledger | Before attempting new approach |
| `prediction_register` | Register falsifiable prediction | Before any Bash command |
| `behavior_precheck` | *(Deprecated)* Combined hygiene+constraint+prediction | Use individual tools instead |
| `global_memory_status` | Cross-session behavioral analysis | Review learned patterns |
| `global_memory_reset` | Reset global behavior profile | After major workflow changes |

### Habit Mode (Context Window Management)

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `declare_mode` | Enter/exit habit mode | Sustained refactoring or execution work |
| `habit_status` | Token pressure, habit score, signals | Before compaction decisions |
| `habit_compact` | Trigger compaction, return state snapshot | Approaching context limits |
| `habit_configure` | Adjust thresholds for current session | Session tuning |

### Theory & Context

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `build_theory_pack` | Compact summary of project principles | Session start |
| `extract_project_theory` | Full theory extraction from markdown | Deep principle understanding |
| `get_theory_context` | Look up specific principles by topic | During design decisions |
| `extract_theory_constraints` | Extract enforceable rules from prose | After CLAUDE.md changes |
| `bootstrap_context_files` | Generate CLAUDE.md/AGENTS.md from docs | First session, doc changes |
| `context_guidance` | Summarize context guidance and rules | Quick reference |
| `context_patch_review` | Review pending CLAUDE.md patches | After behavioral discoveries |
| `context_patch_apply` | Apply pending patches to CLAUDE.md | After reviewing patches |
| `audit_context_health` | Audit CLAUDE.md quality | Periodic maintenance |

### Project Compass (4-Axis Understanding)

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `compass_status` | Show axes, depths, gaps, staleness | Orientation |
| `compass_check` | Check action against toward/away/forbidden | Before architectural changes |
| `compass_update` | Re-extract compass from project docs | After doc changes |
| `compass_interview` | Gap-filling interview | When compass has gaps |
| `compass_reset` | Scoped state reset | After major pivots |
| `theory_mode_enter` | Enter theory exploration mode | Deep design exploration |
| `theory_mode_freeze` | Freeze compass and exit theory mode | Done exploring |

### Convergence Analysis

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `convergence_analyze` | Multi-lens convergence aggregation on functions/files | After controlplane_run identifies decomposition candidates |
| `extraction_plan` | Build stepwise extraction plan for a specific function | After convergence_analyze shows EXTRACT actionability |
| `optimization_landscape` | Project-wide optimization opportunity map | Strategic view of codebase optimization potential |

### Specification Complexity

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `spec_analyze` | Specification complexity analysis (sigma, regime, phase, risk, DFT) | Understanding function specification state |
| `spec_prescribe` | Risk-prioritized test prescriptions with expanded taxonomy | After spec_analyze reveals under-specified functions |
| `spec_composition` | Composition gap and sheaf condition analysis across modules | Understanding cross-module specification dependencies |
| `spec_gate_check` | Optimization gate validation with stop criteria | Checking if optimization hints are backed by specification |
| `spec_file_analyze` | Single-file specification analysis (fast, resource-bounded) | Interactive per-file spec analysis |
| `spec_file_prescribe` | Single-file test prescriptions with risk prioritization | After spec_file_analyze shows under-specified functions |

### Mutation Testing

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `mutation_run_sampling` | Fast sampled mutation run | After editing specific files |
| `mutation_run_full` | Deep exhaustive mutation profiling (Tier 2) | Verify test quality of component; also auto-scheduled by ControlPlane when `tier2_auto_schedule: true` |
| `mutation_get_state` | Current mutation state and metrics | Review previous runs |
| `mutation_prescribe` | Deterministic prescriptions from profiles | After mutation run |
| `mutation_decompose` | Find entangled functions (mode: auto/static/dynamic) | Refactoring decisions, cold-start projects |
| `mutation_refactor_loop` | Re-profile after test improvement | Close the test improvement loop |
| `mutation_prescribe_tests` | Generate targeted test skeletons from mutation profiles | After `mutation_prescribe` identifies surviving categories |
| `mutation_validate_tests` | Re-profile and compute per-category survival deltas | After writing prescribed tests |
| `mutation_clear_state` | Clear mutation state | Code has drifted significantly |

### Bootstrap (Cold-Start)

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `bootstrap_tests` | Generate test skeletons, property tests, contracts | Zero-test projects |
| `bootstrap_status` | Check bootstrap pipeline phase and artifacts | Monitor bootstrap progress |

### Test Effectiveness (TEFF)

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `analyze_test_strength` | Project-wide assertion quality analysis | Assess test suite health |
| `inspect_test_assertions` | Per-assertion drill-down into test file | Investigate weak test files |

### Performance & Algebra

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `inspect_algebra` | View algebraic property manifest | Understanding function purity |
| `generate_property_tests` | Generate Hypothesis test templates | After finding pure functions |

### Dependencies

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `dep_health_check` | Comprehensive dependency audit | Session start, after installs |
| `dep_sync` | Check sync status, create venv/lockfile | Environment setup |

### Model Calibration

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `model_profile_status` | Show calibration profiles | Check model tendencies |
| `model_profile_probe_start` | Start behavioral micro-tasks | First session with new model |
| `model_profile_probe_submit` | Submit probe answers | After completing micro-tasks |

### NSIL (Native Symbolic Integration Layer)

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `nsil_inference_snapshot` | Current inference context snapshot | Local LLM state injection |
| `nsil_verify_action` | Dry-run action verification | Before risky actions |
| `nsil_export_training_data` | Export alignment data for fine-tuning | Training data collection |
| `nsil_benchmark` | Run safety enforcement benchmarks | Validate safety coverage |

### GitHub Project Organization

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `project_organize_audit` | Audit GitHub labels, milestones, templates, wiki | Project setup, organization review |
| `project_organize_apply` | Create missing labels/milestones (dry-run default) | After audit identifies gaps |
| `project_wiki_sync` | Sync theory/compass/design to wiki pages (dry-run default) | Publishing project knowledge to wiki |
| `project_wiki_read` | Read a GitHub wiki page | Wiki content for theory extraction |

### Telemetry

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `telemetry_summary` | ROI dashboard: quality vs token cost | Review session economics |
