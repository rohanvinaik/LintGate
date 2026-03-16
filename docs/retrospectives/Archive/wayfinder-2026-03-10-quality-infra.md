# Wayfinder Quality Infrastructure Audit — 2026-03-10

## Objective
Systematically explore LintGate MCP tools and apply all actionable improvements to professionalize the Wayfinder codebase.

## Tools Used (14 total)

| Tool | Purpose | Result |
|------|---------|--------|
| `controlplane_run` | Full project health check (strict, 11 channels) | 2 blocking, 327 warnings, 75 info |
| `controlplane_get_details` | Drill into findings by ROI | Identified formatting fixes, missing tests, quality infra gaps |
| `tool_applicability_guide` | Understand cadence/triggers for each tool | Mapped tool usage patterns |
| `lint_fix` | Auto-fix safe lint issues | 15 files reformatted |
| `dep_health_check` | Dependency audit | 1 issue: stale lockfile (fixed with `uv lock`) |
| `setup_github_quality` | Preview quality infrastructure artifacts | Generated configs for selective deployment |
| `spec_project_rollup` | Specification health overview | Mean spec level 0.06, 250/296 functions in regime A |
| `spec_prescribe` | Test prescriptions by risk priority | 828 prescriptions, mostly P0 exact-value gaps |
| `mutation_run_sampling` | Mutation testing on 4 key files | Mixed results (see below) |
| `analyze_test_strength` | Test assertion quality scoring | Effectiveness 0.235, identified 10 assertion upgrades |
| `inspect_test_assertions` | Per-test assertion classification | Found 7 genuinely weak tests across 3 files |
| `generate_property_tests` | Hypothesis property test generation | 2 candidates (low value for this codebase) |
| `convergence_analyze` | Cross-channel pattern detection | No EXTRACT signals, all "investigate" |
| `contract_audit` | Internal channel contract health | PASS — all contracts healthy |

## Mutation Testing Results

| File | Functions | Kill Rate | Notes |
|------|-----------|-----------|-------|
| `lowering.py` | 12 | 3/12 functions at 0% survival | Public API well-tested; internal `_lower_*` helpers survive |
| `resolution.py` | 3 | 2/3 at 0% survival | `build_query`, `_combine_candidates` strong; `resolve` weak |
| `nav_contracts.py` | 5 | 0/5 at 0% survival | Tests exist but mutations survive — subtle value mutations |
| `proof_search.py` | 7 | 0/7 (discovery failed) | Import failures prevented test loading |

## Changes Made

### Quality Infrastructure (new files)
- `.codeclimate.yml` — Code Climate config (complexity, duplication thresholds)
- `.gitleaks.toml` — Secret detection baseline config
- `SECURITY.md` — Responsible disclosure policy
- `.github/dependabot.yml` — Automated dependency updates (pip + GitHub Actions)
- `.github/workflows/security-lite.yml` — Bandit SAST + gitleaks on push/PR

### Code Quality
- **15 test files reformatted** via `lint_fix` (ruff format)
- **uv.lock synced** with pyproject.toml
- **.gitignore updated** — added `.qlty/`, `.coverage`, `coverage.xml`, `.scannerwork/`
- **README.md** — added Security Rating + Security Lite badges

### Test Assertion Strengthening (7 genuinely weak tests upgraded)
- `test_verification.py`: `test_no_sorry` and `test_sorry_not_substring` — added tactic_count and tactics equality checks
- `test_data_datasets.py`: 5 tests upgraded from isinstance/assertIsNone-only to include value assertions
  - `test_getitem_returns_ood_prompt` — added field equality
  - `test_path_attribute` (x2) — added path.exists() check
  - `test_getitem_returns_negative_bank_entry` — added theorem_id check
  - `test_negative_is_none_when_absent` — added positive field verification
  - `test_getitem_returns_navigational_example` — added theorem_id check

### Deliberately NOT deployed
- `tests.yml` workflow — conflicts with existing `ci.yml`
- `sonarcloud.yml` workflow — already handled in `ci.yml`
- `.coveragerc` — generated for lintgate, not Wayfinder
- `qlty.yml` / `.qlty/` — adds tool dependency not yet opted into
- `quality-infra-gate.yml` — unnecessary gate overhead
- `clusterfuzzlite`, `codeql`, `pypi-publish` — overkill for current project stage

## Key Findings

1. **Test effectiveness is low (0.235)** — 140/197 production functions untested. Most are in torch-dependent modules excluded from CI coverage, so this is by design.
2. **Spec coverage is very low (mean 0.06)** — 250/296 functions in regime A. The 828 prescriptions suggest massive room for property-based and exact-value testing, but most targets are in integration-test-only modules.
3. **Contract audit is clean** — internal channel wiring is healthy.
4. **Convergence analysis shows no structural decomposition needed** — codebase architecture is stable.
5. **Mutation testing reveals internal helper functions survive** — `_lower_*` functions in lowering.py and `resolve()` in resolution.py could use targeted test strengthening.

## Recommendations for Future Sessions

1. **Mutation-guided test hardening**: Run `mutation_run_full` on `lowering.py` and `resolution.py` to identify exactly which mutations survive and write targeted tests.
2. **Periodic `controlplane_run`**: Run at session start with `time_budget_minutes=30` for focused ROI fixes.
3. **`spec_prescribe` with file filter**: Target specific files rather than project-wide to get actionable prescriptions.
