# LintGate: Prescriptive Improvements from Field Use

**Source:** Two-session audit of `~/scripts` (30 files, ~10K LOC, Python CLI tools for
home network infrastructure). Agent: Claude Opus 4.6. Date: 2026-02-28.

**Companion:** `docs/retrospectives/scripts-2026-02-28-tier2-audit.md` — full session
narrative with before/after metrics.

---

## Why These Changes

LintGate's architecture is sound. The multi-channel ControlPlane, the coherence model,
and the edit hooks form a genuinely novel feedback loop. But after two sessions of heavy
use, a consistent pattern emerged: **LintGate excels at diagnosis but lacks prescription.**

It tells you *what* is wrong ("CC 42", "file too long", "E402") but not *what to do about
it* ("extract lines 215-248 into `_fetch_st_json()` — they share only variable `cache`
with the rest"). The agent spends ~50% of refactoring time reading code to find seams that
are computable from the AST. Meanwhile, the mutation system — LintGate's most ambitious
tool — sits idle because it requires tests, and first-time codebases don't have them.

A second pattern: LintGate treats the development machine as the only environment. An
E402 warning (mid-file import) turned out to be a runtime failure on a deployment machine
with a different Python version and different `sys.path`. Static linting that doesn't
account for deployment divergence misses an entire class of bugs.

The improvements below address three gaps:
1. **Diagnosis → Prescription** — Tell the agent WHERE to cut, not just THAT something is complex
2. **Cold start → Day-one signals** — Make mutation/decompose useful before tests exist
3. **Single-environment → Multi-environment** — Catch "works on my machine" import bugs

Every improvement is designed to be **symbolic, local, and cheap** — AST graph algorithms,
pattern matching, and data plumbing rather than LLM inference. The only exception is
behavioral contract generation (Improvement 8, Step 4), which genuinely requires language
understanding.

---

## Improvement 1: Variable Dependency Clustering Within Functions

**What:** Given a function with CC > threshold, compute the internal variable dependency
graph and propose extraction points.

**Where it lands:**
- `lintgate/linters/structure_checks/function_checks.py` (analysis)
- `lintgate/mutation/decomposition.py` (output format, new `mode="static"`)
- `mcp_tools/mutation_tools.py` (expose via `mutation_decompose(mode="static")`)

**Algorithm:**
1. Parse function body into an AST
2. Build a directed graph: nodes = statements (or statement groups), edges = variable
   read/write dependencies ("statement B reads variable X that statement A writes")
3. Find connected components or compute minimum cut (Tarjan's / Kernighan-Lin)
4. Each component is a candidate helper function
5. Shared variables across the cut become the helper's parameters
6. Compute residual CC for the parent function after extraction

**Output:**
```json
{
  "function": "scan_smartthings",
  "file": "squish_scan.py",
  "cc": 42,
  "proposed_splits": [
    {
      "name": "_fetch_st_json",
      "lines": [215, 248],
      "shared_vars": ["SMARTTHINGS_SH"],
      "coupling_score": 0.12,
      "reason": "Independent I/O block — async fetch, no dependency on downstream parsing"
    },
    {
      "name": "_parse_st_item",
      "lines": [250, 290],
      "shared_vars": [],
      "coupling_score": 0.0,
      "reason": "Pure transform — takes dict, returns Device. Zero shared state."
    }
  ],
  "residual_cc": 8
}
```

**Complexity:** O(n) in statement count per function. Microseconds per function.

**Tests to validate:**
- `test_variable_clustering_two_clusters`: Function with two independent blocks sharing
  no variables → proposes split at the boundary
- `test_variable_clustering_single_bridge`: Function with two blocks connected by a single
  shared variable → proposes split, reports shared var as parameter
- `test_variable_clustering_tightly_coupled`: Function where all statements share state →
  no split proposed (coupling_score too high)
- `test_residual_cc_calculation`: After proposed extraction, residual CC matches expected
- `test_real_world_scan_smartthings`: Feed the actual `scan_smartthings` AST → proposed
  splits match the manual extraction that was performed (ground truth from this session)

**Why:** This was the single biggest time sink in the session. 50% of refactoring time
was spent reading code to find seams. The information needed to identify those seams was
in the AST the entire time — LintGate already parses it for CC computation. Extending
to variable dependency analysis is a natural incremental step.

---

## Improvement 2: Optional Import Pattern Recognition

**What:** Recognize the `try: import X; except ImportError` pattern and downgrade
unresolved import findings from blocking to informational.

**Where it lands:**
- `lintgate/linters/import_checker.py` (or whichever linter produces unresolved import
  findings — likely `ty` integration or `mypy_linter.py`)
- New AST pattern matcher in `lintgate/linters/structure_checks/` if needed

**Pattern to detect:**
```python
# Pattern 1: try/except at module level
try:
    from bleak import BleakScanner
except ImportError:
    BleakScanner = None  # or: pass, or: HAS_BLEAK = False

# Pattern 2: guarded import inside function
def scan_ble():
    try:
        from bleak import BleakScanner
    except ImportError:
        return []
```

**Detection:** AST walk. For each `ImportFrom` or `Import` node, check if it's inside
a `Try` node whose `handlers` include `ExceptHandler(type=Name('ImportError'))`. If so,
tag the import as optional.

**Severity mapping:**
- Unresolved import inside try/except(ImportError) → informational ("optional dep, not
  installed in analysis environment")
- Unresolved import NOT inside try/except → blocking (current behavior, correct)

**Tests to validate:**
- `test_optional_import_try_except`: `try: import bleak; except ImportError: pass` →
  finding severity = informational
- `test_required_import_bare`: `import bleak` (no try/except) → finding severity =
  blocking
- `test_optional_import_in_function`: Guarded import inside function body → informational
- `test_optional_import_wrong_exception`: `try: import X; except ValueError: pass` →
  still blocking (wrong exception type)
- `test_optional_import_with_fallback`: `try: import X; except ImportError: X = None` →
  informational, with note "fallback value: None"

**Why:** 16 of 40 initial blockers (40%) were false positives from this exact pattern.
Every scanner in the project uses `try: from bleak import BleakScanner; except
ImportError: return []`. These are correct, intentional, and should not block. The false
positive rate erodes trust in the blocker category — if 40% of "blockers" aren't real,
agents learn to ignore the severity signal.

---

## Improvement 3: PERF001 Type Narrowing

**What:** Suppress PERF001 ("O(n²) membership test in loop") when the container is a
string, not a list/set.

**Where it lands:**
- `lintgate/linters/performance_checks/perf001_*.py`

**Current behavior:** Fires on any `x in container` inside a loop, regardless of type.

**Problem:** `if "value" in some_string` is O(n) substring search, not O(n) per-element
membership. The check conflates list membership (where converting to set helps) with
string containment (where it doesn't). 25+ false positives in this project from patterns
like `if "<Power>" in line`.

**Fix:** Before emitting PERF001, check if the container variable has a known type:
1. **From type annotations:** `line: str` → suppress
2. **From assignment inference:** `line = stdout.splitlines()[i]` → str (splitlines
   returns list[str], indexing returns str) → suppress
3. **From mypy output:** If mypy is in the linter pipeline (it is), its type information
   is already computed. Consume it.
4. **From literal context:** `if "substr" in "literal string"` → suppress (both operands
   are str)

Fallback: if type cannot be determined, emit the warning but at reduced confidence
(currently 0.5 → drop to 0.3).

**Tests to validate:**
- `test_perf001_list_in_loop`: `for x in items: if x in big_list` → fires (correct)
- `test_perf001_string_in_loop`: `for line in lines: if "tag" in line` → suppressed
  (line is str from splitlines)
- `test_perf001_annotated_str`: `line: str; if "x" in line` → suppressed
- `test_perf001_unknown_type`: `if x in container` (no type info) → fires at confidence
  0.3
- `test_perf001_set_already`: `s = set(items); if x in s` → suppressed (already a set)

**Why:** False positives in performance warnings are worse than missing true positives.
A developer who sees 25 "O(n²)" warnings that are all string containment checks will
start ignoring the performance channel entirely. The type information to suppress these
already exists in the pipeline.

---

## Improvement 4: E402 Severity Promotion for Non-Stdlib Transitive Imports

**What:** Escalate E402 (module import not at top of file) from warning to blocking when
the mid-file import transitively depends on non-stdlib packages.

**Where it lands:**
- `lintgate/linters/ruff_linter.py` (post-processing of ruff E402 findings)
- New analysis in `lintgate/linters/structure_checks/` to compute transitive import deps

**Rule:**
```
IF import is mid-file (E402)
AND the imported module transitively imports non-stdlib packages
THEN severity = blocking
WITH message: "Mid-file import of '{module}' transitively depends on
'{non_stdlib_dep}'. Import ordering sensitivity may cause runtime failures
on environments with different sys.path resolution. Move to top of file."
```

**Algorithm:**
1. For each E402 finding, identify the imported module
2. If the module is a local file, parse its imports recursively
3. Collect the full transitive import set
4. Check each against a stdlib module list (available from `sys.stdlib_module_names`
   on 3.10+ or from the `stdlib-list` package for older versions)
5. If any transitive import is non-stdlib → promote to blocking

**Incident that motivates this:** `import asus_captcha` at line 322 of `asus_auth.py`.
`asus_captcha` imports PIL (non-stdlib) inside a function body. On macpro (Python 3.9,
different `sys.path`), the import chain failed at runtime. Moving the import to top of
file fixed it. The E402 warning was the signal, but at "warning" severity it was lost
in a sea of 80 other warnings.

**Tests to validate:**
- `test_e402_stdlib_only`: Mid-file `import json` → stays warning (stdlib, no risk)
- `test_e402_direct_non_stdlib`: Mid-file `import PIL` → promoted to blocking
- `test_e402_transitive_non_stdlib`: Mid-file `import my_module` where `my_module`
  imports PIL → promoted to blocking
- `test_e402_local_only`: Mid-file `import my_helper` where `my_helper` only imports
  stdlib → stays warning
- `test_e402_conditional_import_inside_function`: Mid-file import of module that has
  `from PIL import Image` inside a function body → promoted (lazy import still creates
  transitive dependency)

**Why:** This single rule would have caught the PIL runtime failure as a blocker on the
first lint run, before any deployment. The fix ("move to top of file") is trivially
prescriptive. Cost: one recursive AST walk per E402 finding.

---

## Improvement 5: File Cohesion Score and Split Proposals

**What:** For files flagged as file-too-long, compute an intra-file cohesion score and
propose how to split.

**Where it lands:**
- `lintgate/linters/structure_checks/file_checks.py` (cohesion analysis)
- `lintgate/channels/structure_channel.py` (file-level structural signals)

**Algorithm:**
1. Parse the file's AST
2. Build an intra-file call graph: nodes = top-level functions/classes, edges = "function
   A calls function B"
3. Also track shared module-level variables (globals read/written by each function)
4. Compute connected components in this graph
5. Each component is a candidate module extraction
6. Score cohesion as: `edges_within_components / total_possible_edges` (1.0 = perfectly
   cohesive, 0.0 = no internal relationships)
7. For files with cohesion < 0.5 AND file-too-long, propose split along component
   boundaries

**Additional heuristics:**
- **CLI vs logic detection:** If a file imports `argparse` AND has >5 non-CLI functions,
  flag "mixed CLI/logic — extract dispatch from business logic"
- **Prefix clustering:** If files share a prefix (`squish.py`, `squish_scan.py`,
  `squish_recover.py`) and have import relationships, suggest package creation
- **Import fan-in:** Report how many other files import from this one. High fan-in =
  careful splitting. Zero fan-in = safe leaf module.

**Output (added to file-too-long finding):**
```json
{
  "kind": "file-too-long",
  "file": "squish.py",
  "lines": 1248,
  "cohesion": 0.31,
  "components": [
    {"functions": ["scan_ble", "scan_mdns", "scan_asus", "scan_marantz", "scan_smartthings", "cross_reference"], "label": "scanners"},
    {"functions": ["recover_aranet4", "recover_moonside", "recover_generic", "gatt_enumerate"], "label": "ble_recovery"},
    {"functions": ["cmd_scan", "cmd_control", "cmd_status", "cmd_harden", "main"], "label": "cli_dispatch"}
  ],
  "proposed_modules": ["squish_scan.py (scanners)", "squish_recover.py (ble_recovery)"],
  "import_fan_in": 0,
  "has_argparse": true,
  "suggestion": "File has 3 disconnected function clusters. Extract scanners and BLE recovery into separate modules, leaving CLI dispatch as the spine."
}
```

**Tests to validate:**
- `test_cohesion_single_cluster`: File where all functions call each other → cohesion
  ~1.0, no split proposed
- `test_cohesion_two_clusters`: File with two independent groups → cohesion ~0.5,
  two components identified
- `test_cohesion_three_clusters`: File with three groups (matches the squish.py case) →
  cohesion ~0.31, three components, split proposed
- `test_cli_detection`: File with `import argparse` + 10 business functions → "mixed
  CLI/logic" flag
- `test_fan_in_high`: File imported by 5 others → fan_in=5, suggestion includes "careful
  splitting"
- `test_fan_in_zero`: File imported by nothing → fan_in=0, "safe leaf module"

**Why:** `file-too-long` is currently a dead-end finding. It tells you the file is too
big but not how to split it. The agent reads the entire file to identify seams. The
intra-file call graph — which LintGate already has the AST infrastructure to compute —
reveals those seams algorithmically.

---

## Improvement 6: Expose Pure Function Identity in `inspect_algebra`

**What:** The algebra manifest tracks purity ratio (33/171 = 19.3%) but the current
output doesn't clearly expose *which* functions are pure vs impure. Make this explicit
and flag pure functions as safe extraction targets.

**Where it lands:**
- `lintgate/channels/performance_channel.py` (where PropertyManifest is built)
- `lintgate/linters/performance_checks/manifest.py`
- `mcp_tools/performance_tools.py` (`inspect_algebra` output format)

**Enhancement:** Add to each function's algebraic profile:
```json
{
  "function": "_parse_st_item",
  "pure": true,
  "extraction_safety": "safe — no side effects, no shared state, no I/O",
  "callers": ["scan_smartthings"],
  "caller_count": 1
}
```

And to the summary:
```json
{
  "pure_functions_by_file": {
    "squish_scan.py": ["_parse_st_item", "normalize_mac", "name_similarity", "proto_tag"],
    "asus_captcha.py": ["_correct_ocr_chars", "_add_text_to_votes", "_tally_votes"]
  },
  "safe_to_extract": ["_parse_st_item", "normalize_mac", "_correct_ocr_chars"],
  "unsafe_to_extract": ["scan_ble", "http_request"]
}
```

**Tests to validate:**
- `test_pure_function_detected`: Function with no I/O, no globals, no side effects →
  pure=true
- `test_impure_function_detected`: Function that calls subprocess.run → pure=false
- `test_extraction_safety_pure`: Pure function → extraction_safety="safe"
- `test_extraction_safety_shared_state`: Function that reads a module-level dict →
  extraction_safety="needs module-level state"
- `test_caller_count`: Function called by 3 others → caller_count=3

**Why:** Pure functions are the lowest-risk extraction targets. When you're splitting a
file, move the pure functions first — they can't break anything. Knowing *which* functions
are pure (not just how many) directly informs the extraction order.

---

## Improvement 7: Git Co-Change Coupling for Coherence

**What:** Add a git history dimension to the coherence model: files that change together
frequently are coupled, and that coupling should inform structural analysis.

**Where it lands:**
- `lintgate/channels/git_channel.py` (already exists — extend)
- `lintgate/controlplane/coherence.py` (consume git coupling signal)

**Algorithm:**
1. `git log --name-only --pretty=format:'' --since=30.days` → list of change sets
2. For each pair of files, count co-change frequency (how often they appear in the same
   commit)
3. Normalize: `co_change(A,B) = commits_with_both / commits_with_either`
4. Files with high co-change coupling that are in different modules → "these should be
   together"
5. Files with low co-change coupling that are in the same module → "these could be split"

**Integration with coherence:**
- If the structure channel says "these files form disconnected clusters" AND git says
  "these clusters never change together" → stronger confidence in split proposal
- If the structure channel proposes a split BUT git says "these files always change
  together" → weaken the proposal (they're coupled in practice even if not by call graph)

**Output (added to structure channel):**
```json
{
  "co_change_clusters": [
    {"files": ["asus_auth.py", "asus_captcha.py"], "coupling": 0.85},
    {"files": ["squish.py", "squish_scan.py"], "coupling": 0.92}
  ],
  "surprising_coupling": [
    {"files": ["marantz.sh", "squish_scan.py"], "coupling": 0.60, "note": "no import relationship but frequently co-changed"}
  ]
}
```

**Tests to validate:**
- `test_cochange_high`: Two files in 8/10 same commits → coupling=0.8
- `test_cochange_zero`: Two files never in same commit → coupling=0.0
- `test_cochange_no_git`: Non-git project → gracefully skip, no error
- `test_coherence_integration`: Structure proposes split + git confirms low coupling →
  coherence confidence increased
- `test_coherence_conflict`: Structure proposes split + git shows high coupling →
  coherence notes "structural clusters diverge from change patterns"

**Why:** The coherence model currently runs on a point-in-time snapshot. Git history is
free, local, and captures *behavioral* coupling — which files developers actually treat
as a unit. This is exactly the signal mutation testing tries to capture through test
observation, but git history is available immediately, without generating or running any
tests.

---

## Improvement 8: Pre-Emptive Test Bootstrap on First ControlPlane Run

**What:** When ControlPlane's test channel detects "0 test files, 0 coverage," automatically
trigger a background pipeline that generates a full test suite, then makes mutation/decompose
signals available.

**Where it lands:**
- `lintgate/controlplane/runtime.py` (trigger logic)
- `lintgate/orchestration/workflows.py` (new `test_bootstrap_workflow`)
- Chains existing tools: `inspect_algebra` → `controlplane_test_skeleton` →
  `generate_property_tests` → `mutation_run_sampling` → `mutation_decompose`

**Pipeline (all steps use existing tools except Step 4):**

| Step | Tool | What it does | LLM needed? | Time |
|------|------|-------------|-------------|------|
| 1 | `inspect_algebra` | Identify pure functions + algebraic properties | No | ~1s |
| 2 | `controlplane_test_skeleton` | Generate pytest stubs per source file | No | ~2s/file |
| 3 | `generate_property_tests` | Hypothesis property tests for pure functions | No | ~3s |
| 4 | *new* behavioral contracts | Return type + shape + error boundary assertions for impure functions | **Yes** (LLM) | ~2-5min |
| 5 | `mutation_run_sampling` | Compute survival rates against generated tests | No | ~30-60s |
| 6 | `mutation_decompose` | Identify decomposition candidates from survival data | No | ~1s |

Step 4 is the only LLM-dependent part. Three of its four sub-strategies are symbolic:
- **Return type assertions:** Derive from type annotations or AST inference. If function
  declares `-> list[Device]`, generate `assert isinstance(result, list)`. No LLM.
- **Shape preservation assertions:** Derive from loop structure. If function has
  `for item in input: result.append(transform(item))`, generate
  `assert len(result) == len(input)`. No LLM.
- **Error boundary assertions:** Derive from exception handlers. If function has
  `except: return []`, generate `assert func(bad_input) == []`. No LLM.
- **Mock-based I/O contracts:** This genuinely requires understanding what the function
  *does*. "This function calls curl to fetch XML, parses it, extracts `<Power>` tags."
  An LLM generates the mock fixture and meaningful assertions. **This is the one place
  where LLM inference earns its cost.**

**The pipeline runs in the background.** The agent continues lint/refactor work normally.
As tests are generated, `mutation_decompose` gains resolution incrementally.

**Tests to validate:**
- `test_bootstrap_triggers_on_zero_tests`: ControlPlane with 0 test files → bootstrap
  pipeline starts
- `test_bootstrap_skips_with_existing_tests`: ControlPlane with 5 test files → no
  bootstrap
- `test_property_tests_generated_for_pure`: Pure function identified by inspect_algebra →
  Hypothesis test generated
- `test_behavioral_contract_return_type`: Function with `-> list[str]` annotation →
  `assert isinstance(result, list)` generated
- `test_behavioral_contract_error_boundary`: Function with `except: return []` →
  `assert func(bad) == []` generated
- `test_mutation_decompose_has_data_after_bootstrap`: After bootstrap completes,
  `mutation_decompose` returns >0 candidates (was returning 0 before)
- `test_incremental_on_edit`: New function added → test generated for it within next
  edit hook cycle

**Why:** `mutation_decompose` returned 0 candidates in this session because there were
no tests. This is the cold start problem — the tool designed to guide refactoring is
silent for exactly the projects that need guidance most. The pipeline above makes it
operational from session start. Cost: ~20K tokens for the LLM step (Step 4), the rest
is symbolic. For a refactoring session that will consume 100K+ tokens anyway, this is a
20% surcharge that eliminates the manual "read code to find seams" phase.

---

## Improvement 9: Incremental Edit Hook Analysis

**What:** Cache per-function AST analysis and only re-analyze functions whose source
changed, instead of re-running the full linter pipeline on every edit.

**Where it lands:**
- `lintgate/hook_posttooluse.py` (main entry point)
- `lintgate/state.py` (per-function analysis cache)
- `lintgate/linters/structure_checker.py` (accept function-level invalidation)

**Current behavior:** Edit hooks run `channels_run=3` (three full channel evaluations)
after every Write/Edit. For a 798-line file, this means re-parsing and re-analyzing ~30
functions even if only 1 changed.

**Proposed behavior:**
1. Cache per-function AST analysis results keyed by function source hash
2. On edit, identify which functions were modified (diff the AST, or hash each function
   body)
3. Re-analyze only modified functions
4. Merge updated results into the cached state
5. Re-compute file-level and project-level metrics from the merged cache

**Invalidation strategy:**
- Function source hash changes → re-analyze that function
- File-level import changes → re-analyze all functions in that file (imports affect type
  resolution)
- New function added or function deleted → re-analyze file-level metrics (cohesion, LOC)

**Tests to validate:**
- `test_cache_hit_unchanged_function`: Edit line 50 → function at line 200 is NOT
  re-analyzed (cache hit)
- `test_cache_miss_changed_function`: Edit function body → that function IS re-analyzed
- `test_import_change_invalidates_file`: Change an import statement → all functions
  re-analyzed
- `test_new_function_updates_file_metrics`: Add function → file-level LOC/cohesion
  recalculated
- `test_hook_latency_reduction`: Measure wall-clock time. Editing 1 function in a
  30-function file should be ~30x faster than full re-analysis.

**Why:** The edit hooks are LintGate's most valuable real-time signal. Making them faster
means tighter feedback loops. The 8-second timeout in `hook_posttooluse.py` is generous —
with incremental analysis, hooks should complete in <1 second even for large files.

---

## Improvement 10: External Package Exclusion

**What:** Exclude installed packages (site-packages) from analysis. Currently, LintGate
reports syntax errors in `bleak/backends/client.py` — an installed third-party package,
not user code.

**Where it lands:**
- `lintgate/lint_runner.py` (file filtering)
- `lintgate/config.py` (default exclusion patterns)

**Rule:** If a file's resolved path contains `/site-packages/`, `/dist-packages/`, or
matches the project's virtual environment path, exclude it from all linters.

**Tests to validate:**
- `test_exclude_site_packages`: File in `.venv/lib/python3.9/site-packages/bleak/` →
  excluded
- `test_include_project_files`: File in project root → included
- `test_exclude_dist_packages`: File in `/usr/lib/python3/dist-packages/` → excluded
- `test_include_symlinked_local`: Local file symlinked into venv → included (resolve
  symlinks before checking)

**Why:** 3 blockers in the session were syntax errors in `bleak/backends/client.py`.
These are not our code. Reporting them as blockers is noise that erodes trust. This is a
simple path-prefix check with zero analysis cost.

---

## Improvement 11: Cross-Environment Import Tracing (`sync_check`)

**What:** A new tool (or ControlPlane channel) that traces the full transitive import
graph — including lazy imports inside function bodies — and flags environment-sensitive
import chains.

**Where it lands:**
- New linter: `lintgate/linters/sync_checker.py`
- Or new channel: `lintgate/channels/sync_channel.py`
- MCP tool: `mcp_tools/lint_tools.py` (add `sync_check` tool)

**Analysis (all AST-based, no network, no remote execution):**
1. Walk every `.py` file in the project
2. Collect ALL import statements — top-level, mid-file, and inside function bodies
3. For each import, resolve whether it's stdlib, local, or third-party
4. Build the full transitive import graph (A imports B, B imports C → A transitively
   depends on C)
5. Flag chains where:
   - A top-level import depends transitively on a lazy import inside a function body
     (invisible until runtime)
   - A mid-file import (E402) depends transitively on non-stdlib packages
   - A `subprocess.Popen`/`subprocess.run` call references a binary by bare name
     (PATH-dependent)

**Output:**
```json
{
  "import_chains": [
    {
      "root": "asus_auth.py:322",
      "chain": ["asus_captcha", "PIL.Image (lazy, inside _preprocess)"],
      "risk": "E402 + lazy non-stdlib transitive",
      "fix": "Move 'import asus_captcha' to top of asus_auth.py"
    }
  ],
  "path_dependent_binaries": [
    {
      "file": "asus_captcha.py:153",
      "binary": "tesseract",
      "note": "Resolved via shutil.which — may differ across environments"
    }
  ]
}
```

**Tests to validate:**
- `test_transitive_non_stdlib`: A imports B, B imports PIL → A has transitive non-stdlib
  dep on PIL
- `test_lazy_import_detected`: `from PIL import Image` inside function body → marked as
  lazy
- `test_e402_plus_lazy_chain`: Mid-file import of module with lazy non-stdlib import →
  high risk
- `test_subprocess_bare_binary`: `subprocess.run(["tesseract", ...])` → flagged as
  PATH-dependent
- `test_subprocess_absolute_path`: `subprocess.run(["/usr/local/bin/tesseract", ...])` →
  not flagged (absolute path)
- `test_stdlib_only_chain`: A imports B, B imports json → no risk (all stdlib)

**Why:** The E402 → PIL failure cost ~45 minutes of SSH debugging. The information needed
to prevent it — "asus_auth.py mid-file imports asus_captcha, which lazily imports PIL
inside a function" — is entirely derivable from the AST. No remote execution, no LLM, no
network. Just recursive AST parsing and a stdlib module list.

---

## Summary: Cost and Compute Profile

| # | Improvement | Compute basis | LLM needed? | Estimated dev effort |
|---|------------|--------------|-------------|---------------------|
| 1 | Variable dependency clustering | AST graph algorithms (Tarjan's) | No | Medium |
| 2 | Optional import recognition | AST pattern matching | No | Small |
| 3 | PERF001 type narrowing | Type annotation + mypy data plumbing | No | Small |
| 4 | E402 severity promotion | Recursive AST import tracing + stdlib list | No | Small |
| 5 | File cohesion + split proposals | Intra-file call graph, connected components | No | Medium |
| 6 | Expose pure function identity | Extend existing PropertyManifest | No | Small |
| 7 | Git co-change coupling | `git log` parsing, pairwise counting | No | Medium |
| 8 | Pre-emptive test bootstrap | Pipeline orchestration + LLM contracts | **Step 4 only** | Large |
| 9 | Incremental edit hooks | Per-function source hashing + cache | No | Medium |
| 10 | External package exclusion | Path-prefix check | No | Trivial |
| 11 | Cross-environment import tracing | Recursive AST walk + stdlib list | No | Medium |

**10 of 11 improvements are fully symbolic.** They use AST parsing, graph algorithms,
pattern matching, type information plumbing, git log parsing, and path checks. Zero LLM
tokens. The one exception (Improvement 8, Step 4 — behavioral contract generation for
impure functions) is the only place where language understanding genuinely adds value that
static analysis cannot provide.

**Recommended implementation order** (highest impact, lowest effort first):
1. **Improvement 2** (optional import recognition) — trivial, eliminates 40% of false-positive blockers
2. **Improvement 10** (external package exclusion) — trivial, eliminates noise from site-packages
3. **Improvement 3** (PERF001 type narrowing) — small, eliminates 25+ false-positive warnings
4. **Improvement 4** (E402 severity promotion) — small, catches a real bug class
5. **Improvement 6** (pure function identity) — small, extends existing infrastructure
6. **Improvement 5** (file cohesion + split proposals) — medium, replaces 50% of manual seam-finding
7. **Improvement 1** (variable dependency clustering) — medium, the big prescription win
8. **Improvement 11** (cross-environment import tracing) — medium, new analysis capability
9. **Improvement 9** (incremental edit hooks) — medium, performance optimization
10. **Improvement 7** (git co-change coupling) — medium, enriches coherence model
11. **Improvement 8** (pre-emptive test bootstrap) — large, solves cold start problem
