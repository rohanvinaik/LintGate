# LintGate Agent Tool Reference

> **Tool count**: 111 MCP tools. Source of truth: `grep -Rho '@mcp.tool()' mcp_server.py mcp_tools/*.py | wc -l`

## Golden Path (Auto-Improve)

The platonic golden path auto-improves any Python codebase toward the platonic ideal. It is the **default workflow** when a user asks to "improve tests", "increase coverage", or "sweep the codebase".

**Codebase sweep**: `platonic_project(path)` → follow `primary_next_action` → `platonic_apply` → repeat.
**Single file**: `platonic_converge(path, file)` → follow `primary_next_action` → `platonic_apply`.

Each iteration: profiles mutation survival → generates typed tests with field-enumeration assertions → validates tests kill targeted mutants → persists for apply. The test generation pipeline uses typed input synthesis (resolves dataclass annotations into constructible values), shared factory generation (emits `_issue()` helpers when types repeat), round-trip pair detection (`to_dict`/`from_dict`), and post-generation validation (strips broken tests before writing to disk).

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
| `controlplane_get_work_queue` | Get dependency-ordered work queue from a cached run | When resuming work without re-running |

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
| `platonic_project` | Golden-path entrypoint that selects the first deterministic high-value target | Default repo-wide improvement workflow |
| `convergence_analyze` | Multi-lens convergence aggregation on functions/files | After controlplane_run identifies decomposition candidates |
| `extraction_plan` | Build stepwise extraction plan for a specific function | After convergence_analyze shows EXTRACT actionability |
| `optimization_landscape` | Project-wide optimization opportunity map | Strategic view of codebase optimization potential |
| `platonic_converge` | File-scoped platonic workflow: profile, route, generate, validate, persist | Improve one known module toward the platonic ideal |
| `platonic_continue` | Resume a persisted platonic workflow by `workflow_id` | Follow the returned `primary_next_action` |
| `platonic_apply` | Apply a validated platonic workflow (dry-run default) | Only after `READY_TO_APPLY` |
| `platonic_sweep` | Scheduler-driven multi-file specification sweep with budget controls | After initial quality work, to systematically close specification gaps |

### Specification Complexity

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `spec_analyze` | Specification complexity analysis (sigma, regime, phase, risk, DFT) | Understanding function specification state |
| `spec_prescribe` | Risk-prioritized test prescriptions with expanded taxonomy | After spec_analyze reveals under-specified functions |
| `spec_composition` | Composition gap and sheaf condition analysis across modules | Understanding cross-module specification dependencies |
| `spec_gate_check` | Optimization gate validation with stop criteria | Checking if optimization hints are backed by specification |
| `spec_file_analyze` | Single-file spec analysis. `enrich=True` (default) builds manifests; `enrich=False` runs AST-only symbolic baseline. Returns regime rationale, trajectory state, and empirical overlay (static/empirical reconciliation: AGREES, CONTRADICTS, DISCOVERY_FAILURE, TOPOLOGY_LIMITED, NO_EMPIRICAL_DATA). | Interactive per-file spec analysis |
| `spec_file_prescribe` | Single-file test prescriptions with risk prioritization | After spec_file_analyze shows under-specified functions |
| `spec_project_rollup` | Project-wide specification rollup with file-level caching. Defaults to production-only (`include_tests=False`) so hotspots focus on source code; set `include_tests=True` to include test files. Cache mode remains read-only by default; `analyze_uncached=True` for live analysis. | Getting project-wide spec health overview |

### Mutation Testing

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `mutation_run_sampling` | Fast sampled mutation run | After editing specific files |
| `mutation_run_full` | Deep exhaustive mutation profiling with budget semantics (`budget_ms`, `per_mutant_timeout_ms`). Returns survivor records, trajectory analysis, and discovery/topology diagnostics. | Verify test quality of component; also auto-scheduled by ControlPlane when `tier2_auto_schedule: true` |
| `mutation_get_state` | Current mutation state and metrics | Review previous runs |
| `mutation_prescribe` | Grounded prescriptions from survivor records with witness generation (per-mutant `why_this_matters`, `suggested_input`, `assertion_shape`, `confidence`). Falls back to category templates when survivor data unavailable. | After mutation run |
| `mutation_decompose` | Cross-lens decomposition guidance (mode: auto/static/dynamic). Requires multi-lens agreement (mutation + specification + composition) for EXTRACT_BOUNDARY; single-lens mutation returns KEEP_TESTING. Mock-dominant topology blocks extraction. | Refactoring decisions, cold-start projects |
| `mutation_refactor_loop` | Re-profile after test improvement | Close the test improvement loop |
| `mutation_prescribe_tests` | Generate targeted test skeletons from mutation profiles | After `mutation_prescribe` identifies surviving categories |
| `mutation_validate_tests` | Sampled re-profiling with per-category survival deltas and budget splitting (`budget_ms`). Safe for routine feedback loop use. | After writing prescribed tests |
| `mutation_clear_state` | Clear mutation state | Code has drifted significantly |
| `spec_improve` | One-shot spec improvement: diagnose → profile → prescribe in one call. Returns prioritized action plan with next test for each under-specified function. | Quick consolidated feedback on a file without running the full pipeline manually |

## Orchestration: Spec → Mutation → Convergence Pipeline

The specification, mutation, and convergence tools form a closed-loop pipeline. Each tool's `next_actions` output suggests the next step. Follow this sequence to systematically close specification gaps:

```
spec_file_analyze ──→ mutation_run_sampling ──→ mutation_run_full
       │                      │                        │
  (σ, regime,            (per-category          (full kill matrix +
   phase, DFT)           kill/survive)          convergence analysis +
       │                      │                symmetry classification)
       │                      │                        │
       ▼                      ▼                        ▼
spec_prescribe         mutation_prescribe ──→ mutation_prescribe_tests
  (prioritized            (category →              (pytest skeletons)
   test recs)              action map)                    │
                                                          ▼
                                                   [Write tests]
                                                          │
                                                          ▼
                                              mutation_validate_tests
                                                (survival deltas)
                                                          │
                                                          ▼
                                                  spec_gate_check
                                               (stop criteria met?)
```

**Quick feedback loop** (after editing): `spec_file_analyze` → `mutation_run_sampling` → `mutation_prescribe`

**Full verification loop** (before shipping): `spec_file_analyze` → `mutation_run_full` → `mutation_prescribe` → `mutation_prescribe_tests` → [write tests] → `mutation_validate_tests` → `spec_gate_check`

**Cross-module composition**: `spec_composition` computes composition gaps γ(A,B) weighted by callee uncertainty. Use after individual files pass gate checks to verify cross-module specification integrity.

**Key internals**: Mutation tools auto-detect purity (pure functions skip STATE mutations), load real tests via test-impact mapping, and accumulate trajectory (ΔK) across runs for phase detection. `mutation_run_full` automatically runs greedy convergence analysis (Thm 3.2) and symmetry-based regime classification (Thm 4.1) — results appear in the `analysis` field.

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
| `test_hygiene_scan` | Scan for stubs, weak assertions, duplicates | After editing tests or during health checks |
| `test_triage` | Rank untested functions by spec priority | Cold-start: decide what to test first |
| `test_infer_inputs` | Infer inputs from call sites and type hints | Before writing tests for untested functions |
| `test_characterize` | Generate characterization tests | Lock in current behavior before refactoring |
| `test_characterize_mark` | Mark characterization test maturity | After reviewing or mutation-validating tests |
| `test_redundancy_project` | Project-wide kill-matrix redundancy analysis | After exhaustive mutation profiling |
| `test_rebuild_plan` | Classify functions into regen strategies, build manifest | First step of test regeneration |
| `test_rebuild_generate` | Generate test skeletons for auto targets | After test_rebuild_plan |
| `test_rebuild_validate` | Validate generated tests against quality gates | After test_rebuild_generate with write=True |
| `test_rebuild_apply` | Promote generated, quarantine old (dry_run default) | After test_rebuild_validate passes |

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
