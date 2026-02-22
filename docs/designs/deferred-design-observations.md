# Deferred Design Observations — Implementation Designs

**Source**: `docs/retrospectives/lintgate-2026-02-22-tier2-audit.md`
**Date**: 2026-02-22
**Scope**: 10 deferred issues from the Tier 2 production audit that require deeper architectural work beyond the 16 bug fixes already applied.

---

## Issue 24: Habit Dispositions Ignored by Agent

### Problem

The hook outputs disposition messages like `Mode: habit (score: 0.55)` and CLAUDE.md says "check `habit_status`", "call `habit_compact`", "call `declare_mode("habit")`" — but the agent never acts on these. The dispositions exist only in prose; there is no forcing function in the hook output that surfaces them as actionable prompts at the moment they matter.

The root cause is that dispositions are passive: they appear in CLAUDE.md (which the agent may not re-read) and in hook output (which is informational, not imperative). The agent's attention budget is consumed by the current edit-verify cycle, leaving no cognitive surplus for "background" tasks like checking habit state.

### Design

**Approach: Context-triggered disposition injection in hook output**

Instead of relying on the agent reading CLAUDE.md, inject specific disposition prompts directly into hook output when trigger conditions are met. This turns passive dispositions into active, contextual nudges.

#### 1. Define disposition triggers (in `hook_posttooluse.py`)

```python
@dataclass
class DispositionTrigger:
    """A disposition that fires when conditions are met."""
    condition: Callable[[HabitState, TokenTracker, dict], bool]
    message: str  # Short imperative prompt
    tool_hint: str  # MCP tool to call
    cooldown_events: int  # Min events between repeat fires
    priority: int  # Higher = more important (0-3)
```

Define 4 triggers:

| Trigger | Condition | Message | Cooldown |
|---------|-----------|---------|----------|
| compact_pressure | `tracker.context_pressure >= 0.50` and `habit_state.active` | `"⚠ Context pressure {pct}% — call habit_compact() now"` | 5 events |
| habit_enter_suggested | `habit_score >= enter_threshold` and `not active` and `sustained >= 3` | `"Sustained edit pattern detected. Consider: declare_mode('habit')"` | 20 events |
| predict_before_bash | `tool_name == "Bash"` and `prediction_accuracy < 0.5` and `predictions >= 5` | `"Prediction accuracy {acc}% — call constraint_check before Bash"` | 10 events |
| reorient_after_failures | `consecutive_failures >= 3` | `"3+ consecutive failures. Run constraint_check to assess coverage"` | 1 (fires every time) |

#### 2. Injection point

In `_format_report()` (the function that builds the hook's stdout), append a `dispositions` section after the lint/behavior report:

```python
def _inject_dispositions(
    habit_state: HabitState,
    tracker: TokenTracker,
    compass_data: dict,
    tool_name: str,
    session_data: dict,
) -> list[dict[str, str]]:
    """Evaluate triggers and return fired dispositions."""
    fired = []
    cooldowns = session_data.get("_disposition_cooldowns", {})
    event_counter = session_data.get("event_counter", 0)

    for trigger in _DISPOSITION_TRIGGERS:
        last_fire = cooldowns.get(trigger.name, 0)
        if event_counter - last_fire < trigger.cooldown_events:
            continue
        if trigger.condition(habit_state, tracker, compass_data):
            fired.append({
                "disposition": trigger.message,
                "tool_hint": trigger.tool_hint,
                "priority": trigger.priority,
            })
            cooldowns[trigger.name] = event_counter

    session_data["_disposition_cooldowns"] = cooldowns
    return fired
```

#### 3. Output format

```json
{
  "dispositions": [
    {
      "disposition": "⚠ Context pressure 62% — call habit_compact() now",
      "tool_hint": "habit_compact",
      "priority": 3
    }
  ]
}
```

Priority 3 dispositions use the `stopSequence` hook response mechanism (if available) to interrupt the agent's flow. Priority 0-2 are advisory and included in the `result` field.

#### 4. Files changed

| File | Change |
|------|--------|
| `lintgate/hook_posttooluse.py` | Add `DispositionTrigger` dataclass, `_DISPOSITION_TRIGGERS` list, `_inject_dispositions()`, wire into `_format_report()` |
| `lintgate/habit_mode.py` | Expose `HabitState.consecutive_failures` counter (new field) |
| `lintgate/token_tracker.py` | Expose `context_pressure` property (already exists as computation) |

**Complexity**: Medium — 4 trigger definitions, 1 injection function, wiring into existing format pipeline.

**Risk**: Disposition fatigue. Mitigate with cooldowns and a `dispositions_enabled` config flag (default: true) in `lintgate.yaml` under `controlplane.habit_mode`.

---

## Issue 25: Decomposition Creates New Blockers

### Problem

When extracting helper functions to reduce cyclomatic complexity, the refactored functions often need many parameters (e.g., `_try_habit_compaction` has 9 args, `_post_process_session` has 8 args). The coherence engine and lint pipeline see `too-many-args` as a new blocker, creating a net-zero or negative outcome from the refactoring.

This is a **structural tradeoff**, not a bug: CC goes down, but parameter count goes up. The system should recognize this tradeoff pattern and adjust its severity classification accordingly.

### Design

**Approach: Refactoring-tradeoff detection in the coherence engine**

#### 1. Add a `RefactoringTradeoff` detector to coherence history analysis

```python
@dataclass
class RefactoringTradeoff:
    """Detected tradeoff where one metric improved while another regressed."""
    improved_metric: str       # e.g., "cyclomatic_complexity"
    improved_delta: float      # e.g., -5 (went down)
    regressed_metric: str      # e.g., "too_many_args"
    regressed_delta: float     # e.g., +3 (went up)
    net_assessment: str        # "net_positive", "net_neutral", "net_negative"
    affected_files: list[str]
```

#### 2. Detection logic in `coherence.py:compute_coherence_with_history()`

After the existing REGRESSION/IMPROVEMENT detection, add tradeoff detection:

```python
def _detect_refactoring_tradeoffs(
    current_findings: list[LintIssue],
    prev_findings: list[LintIssue],
) -> list[RefactoringTradeoff]:
    """Detect cases where complexity decreased but args/locals increased."""
    tradeoffs = []

    # Known tradeoff pairs
    TRADEOFF_PAIRS = [
        ("cyclomatic_complexity", "too_many_args"),
        ("cyclomatic_complexity", "too_many_locals"),
        ("cognitive_complexity", "too_many_args"),
        ("file_too_long", "too_many_functions"),
    ]

    for improved_kind, regressed_kind in TRADEOFF_PAIRS:
        prev_improved = [f for f in prev_findings if f.kind == improved_kind]
        curr_improved = [f for f in current_findings if f.kind == improved_kind]
        prev_regressed = [f for f in prev_findings if f.kind == regressed_kind]
        curr_regressed = [f for f in current_findings if f.kind == regressed_kind]

        # Check: did improved_kind go DOWN and regressed_kind go UP?
        improved_delta = len(curr_improved) - len(prev_improved)
        regressed_delta = len(curr_regressed) - len(prev_regressed)

        if improved_delta < 0 and regressed_delta > 0:
            # Find affected files (intersection of changed files)
            improved_files = {f.file for f in prev_improved} - {f.file for f in curr_improved}
            regressed_files = {f.file for f in curr_regressed} - {f.file for f in prev_regressed}
            overlap = improved_files & regressed_files

            # Net assessment: complexity reduction is worth more than arg count
            WEIGHTS = {
                "cyclomatic_complexity": 2.0,
                "cognitive_complexity": 2.0,
                "file_too_long": 1.5,
                "too_many_args": 1.0,
                "too_many_locals": 0.8,
                "too_many_functions": 0.5,
            }
            benefit = abs(improved_delta) * WEIGHTS.get(improved_kind, 1.0)
            cost = abs(regressed_delta) * WEIGHTS.get(regressed_kind, 1.0)

            if benefit > cost * 1.5:
                assessment = "net_positive"
            elif benefit > cost * 0.8:
                assessment = "net_neutral"
            else:
                assessment = "net_negative"

            tradeoffs.append(RefactoringTradeoff(
                improved_metric=improved_kind,
                improved_delta=improved_delta,
                regressed_metric=regressed_kind,
                regressed_delta=regressed_delta,
                net_assessment=assessment,
                affected_files=sorted(overlap)[:5],
            ))

    return tradeoffs
```

#### 3. Severity modulation

When a `net_positive` or `net_neutral` tradeoff is detected, downgrade the `too-many-args` findings on affected files from `blocking` to `warning` and add an annotation:

```python
if tradeoff.net_assessment in ("net_positive", "net_neutral"):
    for finding in current_findings:
        if (finding.kind == tradeoff.regressed_kind
                and finding.file in tradeoff.affected_files
                and finding.severity == "blocking"):
            finding.severity = "warning"
            finding.evidence["tradeoff_detected"] = True
            finding.suggestions.insert(0,
                f"This is a refactoring tradeoff: {tradeoff.improved_metric} "
                f"improved by {abs(tradeoff.improved_delta)}. Consider a "
                f"context object or builder pattern to reduce parameters."
            )
```

#### 4. Coherence annotation

Add to coherence output:

```python
if tradeoffs:
    annotations.append(
        f"TRADEOFF: {len(tradeoffs)} refactoring tradeoff(s) detected "
        f"— net assessment: {', '.join(t.net_assessment for t in tradeoffs)}"
    )
```

#### 5. Files changed

| File | Change |
|------|--------|
| `lintgate/controlplane/coherence.py` | Add `RefactoringTradeoff` dataclass, `_detect_refactoring_tradeoffs()`, wire into `compute_coherence_with_history()` |
| `lintgate/controlplane/types.py` | Add `tradeoffs: list[dict]` to `CoherenceResult` |

**Complexity**: Medium — detection logic is straightforward; the tricky part is getting the weighting right. Start with the weights above and tune from observed data.

**Risk**: False positive tradeoff detection when unrelated files change simultaneously. Mitigate by requiring file overlap between improved and regressed findings.

---

## Issue 26: STRUCT003 Increases with Module Extraction

### Problem

When you extract a module from a monolith (reducing `file_too_long`), the new module starts as an orphan from STRUCT003's perspective because nothing explicitly imports it yet. The current fix (Step 4d) adds plugin directory exclusions, but that's a heuristic — it doesn't detect actual re-export patterns.

### Design

**Approach: Import graph enhancement with re-export detection**

#### 1. Detect re-exports in `__init__.py`

Currently, `_build_import_graph()` only tracks `import X` and `from X import Y` as edges. It doesn't recognize that `__init__.py` re-exporting a module makes that module "used." Add re-export detection:

```python
def _detect_reexports(
    init_file: str,
    project_root: str,
) -> set[str]:
    """Detect modules re-exported by __init__.py.

    Patterns detected:
    1. `from .submodule import *` (wildcard re-export)
    2. `from .submodule import ClassA, ClassB` (explicit re-export)
    3. `__all__ = ["submodule", ...]` (explicit public API)
    4. `import .submodule as submodule` (alias re-export)
    """
    reexported: set[str] = set()

    try:
        with open(init_file) as f:
            tree = ast.parse(f.read(), filename=init_file)
    except (SyntaxError, OSError):
        return reexported

    package_dir = os.path.dirname(init_file)

    for node in ast.walk(tree):
        # from .submodule import * or from .submodule import X
        if isinstance(node, ast.ImportFrom) and node.level > 0 and node.module:
            sub_path = os.path.join(package_dir, node.module.replace(".", os.sep))
            if os.path.exists(sub_path + ".py") or os.path.isdir(sub_path):
                reexported.add(node.module)

        # __all__ = [...]
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                reexported.add(elt.value)

    return reexported
```

#### 2. Integrate into `_check_orphans()`

Before declaring a module orphan, check if it's re-exported by its parent `__init__.py`:

```python
def _check_orphans(py_files, import_graph, file_map, project_root, extra_exclude_dirs=None):
    # ... existing code ...

    # Pre-compute re-exports from all __init__.py files
    reexports_by_package: dict[str, set[str]] = {}
    for filepath in py_files:
        if os.path.basename(filepath) == "__init__.py":
            pkg_dir = os.path.dirname(filepath)
            reexports_by_package[pkg_dir] = _detect_reexports(filepath, project_root)

    for module, filepath in file_map.items():
        if module in all_imported:
            continue

        # NEW: Check if module is re-exported by parent __init__.py
        parent_dir = os.path.dirname(filepath)
        stem = os.path.basename(filepath).replace(".py", "")
        parent_reexports = reexports_by_package.get(parent_dir, set())
        if stem in parent_reexports or module.rsplit(".", 1)[-1] in parent_reexports:
            continue  # Re-exported = not orphan

        if _is_orphan_excluded(filepath, module, project_root, extra_exclude_dirs):
            continue
        # ... rest of orphan reporting ...
```

#### 3. Evidence enrichment

For orphans that remain after re-export checking, add evidence about what the re-export scan found:

```python
evidence={
    "code": "STRUCT003",
    "module": module,
    "file": relpath,
    "parent_reexports_checked": bool(parent_reexports),
    "not_in_reexports": True,
}
```

#### 4. Files changed

| File | Change |
|------|--------|
| `lintgate/channels/structure_channel.py` | Add `_detect_reexports()`, integrate into `_check_orphans()` pre-loop |

**Complexity**: Small — single function addition + 5-line integration.

**Risk**: Performance — parsing every `__init__.py` adds time. Mitigate by caching results in the existing `file_map` traversal. For most projects, `__init__.py` count is <50.

---

## Issue 9: Dead Path References Lack Integration Context

### Problem

The context auditor's `check_path_references()` flags paths in CLAUDE.md that don't exist on disk. But some paths reference things created by build scripts (e.g., `integrate.sh`, `setup.py install`, `npm run build`). The auditor doesn't know about generated artifacts.

### Design

**Approach: Build-artifact awareness via `.claude/lintgate.yaml` config + heuristic detection**

#### 1. Config-based generated path patterns

Add to `lintgate.yaml`:

```yaml
context_auditor:
  generated_path_patterns:
    - "dist/*"
    - "build/*"
    - ".coverage"
    - "htmlcov/*"
    - "*.egg-info/*"
    - ".claude/lintgate/state/*"
```

#### 2. Script-output detection

Scan for common build scripts and infer their output paths:

```python
_BUILD_SCRIPT_OUTPUTS: dict[str, list[str]] = {
    "integrate.sh": [],  # Generic — user should configure
    "setup.py": ["*.egg-info", "dist", "build"],
    "Makefile": ["build", "dist", "out"],
    "webpack.config.js": ["dist", "bundle"],
    "tsconfig.json": ["dist", "out", "lib"],
    "pyproject.toml": ["dist", "*.egg-info"],
}

def _detect_generated_patterns(project_root: str) -> list[str]:
    """Detect likely generated path patterns from build scripts."""
    patterns = []
    for script, outputs in _BUILD_SCRIPT_OUTPUTS.items():
        if os.path.exists(os.path.join(project_root, script)):
            patterns.extend(outputs)
    return patterns
```

#### 3. Integration into `find_dead_paths()`

```python
def find_dead_paths(
    path_refs: list[str],
    project_root: str,
    generated_patterns: list[str] | None = None,
) -> list[str]:
    """Check which path references don't exist on disk."""
    dead: list[str] = []
    gen_patterns = generated_patterns or []

    for ref in path_refs:
        # ... existing checks ...

        # NEW: skip refs matching generated patterns
        if _matches_generated_pattern(ref, gen_patterns):
            continue

        # ... rest of dead path check ...
    return dead

def _matches_generated_pattern(ref: str, patterns: list[str]) -> bool:
    """Check if a path reference matches a known generated pattern."""
    import fnmatch
    for pattern in patterns:
        if fnmatch.fnmatch(ref, pattern) or fnmatch.fnmatch(ref.lstrip("./"), pattern):
            return True
    return False
```

#### 4. Evidence for remaining dead paths

Add provenance to the finding:

```python
{
    "check": "path_references",
    "status": "warning",
    "dead_paths": dead_paths,
    "generated_patterns_checked": len(gen_patterns),
    "hint": "Add patterns to lintgate.yaml context_auditor.generated_path_patterns to suppress known generated paths",
}
```

#### 5. Files changed

| File | Change |
|------|--------|
| `lintgate/context_auditor_checks.py` | Add `_detect_generated_patterns()`, `_matches_generated_pattern()`, update `find_dead_paths()` signature |
| `lintgate/config.py` | Parse `context_auditor.generated_path_patterns` from config |

**Complexity**: Small — pattern matching with fallback heuristics.

---

## Issue 11: `getting_started` Recreates State Dirs

### Problem

`getting_started()` auto-creates `.claude/lintgate.yaml` and provisions a venv every time it's called, even when these already exist. For first-time setup this is correct, but for subsequent calls it can overwrite customizations (though currently it checks `os.path.exists(config_path)` for config).

The real issue is that state directories under `.claude/lintgate/` are also created by other tools, and `getting_started()` touches them in ways that confuse the state management.

### Design

**Approach: Add `--reset` flag with idempotent default + setup report diffing**

#### 1. Make `getting_started()` fully idempotent

The function already guards config creation with `if auto_setup and not os.path.exists(config_path)`. Extend this pattern to all setup actions:

```python
def getting_started(
    path: str,
    auto_setup: bool = True,
    auto_install_optional_linters: bool = True,
    reset: bool = False,  # NEW: force-recreate everything
) -> str:
    """..."""
    project_root = helpers["_validate_project_root"](path)
    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")

    if reset:
        # Document what will be reset
        reset_actions = _reset_project_state(project_root)
        startup_actions.extend(reset_actions)
```

#### 2. `_reset_project_state()` function

```python
def _reset_project_state(project_root: str) -> list[dict[str, Any]]:
    """Reset LintGate state for the project (config preserved)."""
    actions = []
    state_dir = os.path.join(project_root, ".claude", "lintgate")

    # Clear state subdirectories (NOT config)
    for subdir in ("state", "runs", "sessions"):
        target = os.path.join(state_dir, subdir)
        if os.path.isdir(target):
            shutil.rmtree(target)
            actions.append({"action": "reset_dir", "path": target})

    # Clear global state for this project
    from lintgate.state import _project_hash
    project_hash = _project_hash(project_root)
    global_state = Path.home() / ".claude" / "lintgate" / "habit_state"
    for f in global_state.glob(f"{project_hash}*"):
        f.unlink(missing_ok=True)
        actions.append({"action": "reset_file", "path": str(f)})

    return actions
```

#### 3. Setup report diffing

Show what `getting_started` changed vs what was already set up:

```python
# In the return output, add:
"setup_diff": {
    "config": "already_existed" if config_existed_before else "created",
    "venv": venv_setup.get("status"),
    "tools_installed": [a["tool"] for a in install_attempts if a.get("success")],
    "tools_already_present": [t["tool"] for t in tool_gaps_before.get("present_tools", [])],
    "state_dirs": "reset" if reset else "preserved",
}
```

#### 4. Files changed

| File | Change |
|------|--------|
| `mcp_tools/onboarding_tools.py` | Add `reset` parameter, `_reset_project_state()` function, `setup_diff` output |

**Complexity**: Small — mostly gating logic and a cleanup function.

---

## Issue 14: `global_memory_status` Naming Confusion

### Problem

`global_memory_status` sounds like it shows global state, but it actually shows the **project-scoped** behavior profile (learned patterns, signal priors, nudge outcomes). Users expect it to show something global across all projects. The term "global memory" in the codebase means "cross-session memory for this project" — the word "global" refers to temporal scope (all sessions), not spatial scope (all projects).

### Design

**Approach: Add `scope` field to output + rename in next major version**

#### Phase 1 (Non-breaking): Add scope clarification

```python
@mcp.tool()
def global_memory_status(path: str) -> str:
    """Show cross-session behavioral analysis status.

    Returns session count, learned patterns, calibration settings,
    and computed bias adjustments from accumulated behavioral data.

    NOTE: "Global" refers to cross-session scope (all sessions for THIS project),
    not cross-project scope. Each project has its own behavior profile.

    Args:
        path: Project root path.
    """
    # ... existing implementation ...

    output: dict[str, Any] = {
        "scope": "project",  # NEW: clarify scope
        "scope_note": "Cross-session memory for this project (not cross-project)",
        "project_root": project_root,
        "enabled": cp_config.global_memory_enabled,
        # ... rest unchanged ...
    }
```

#### Phase 2 (Next major version): Rename tool

Register a new tool `behavior_profile_status` and deprecate `global_memory_status`:

```python
@mcp.tool()
def behavior_profile_status(path: str) -> str:
    """Show cross-session behavioral analysis for this project.
    ...
    """
    return _impl_memory_status(path)

@mcp.tool()
def global_memory_status(path: str) -> str:
    """[DEPRECATED] Use behavior_profile_status instead.
    ...
    """
    result = json.loads(_impl_memory_status(path))
    result["_deprecated"] = "Use behavior_profile_status instead"
    return helpers["_json_dumps"](result)
```

Similarly rename `global_memory_reset` → `behavior_profile_reset`.

#### 3. Config rename (backward compatible)

```yaml
controlplane:
  # New name:
  behavior_profile:
    enabled: true
    alpha: 0.6
  # Old name still accepted:
  # global_memory:
  #   enabled: true
```

In `config.py`:

```python
# Accept both old and new config keys
profile_raw = cp_raw.get("behavior_profile", cp_raw.get("global_memory", {}))
```

#### 4. Files changed

| File | Change |
|------|--------|
| `mcp_tools/behavior_tools.py` | Phase 1: add `scope` field. Phase 2: register `behavior_profile_status`, deprecate old |
| `lintgate/config.py` | Accept `behavior_profile` key with `global_memory` fallback |
| `AGENTS.md` | Update tool reference |
| `docs/design.md` | Update terminology |

**Complexity**: Small (Phase 1), Medium (Phase 2 — tool renaming needs MCP contract management).

---

## Issue 16: Model Profile Confidence Decay

### Problem

`apply_telemetry_update()` in `model_profiles.py` updates `signal_risk` via EMA from observed behavior, but it never decays `confidence`. A profile calibrated 30 days ago still shows `confidence: 0.82` even though the model may have been updated by its provider. The `is_stale()` check uses a hard cutoff (`stale_after_days`), but there's no gradual confidence degradation.

Additionally, the telemetry pipeline is only connected in the hook layer (line 1161-1186 of `hook_posttooluse.py`), which requires `session_memory` to be on and `signal_fire_counts` to be populated. The passive path (session_memory off) never feeds telemetry.

### Design

**Approach: Exponential confidence decay + passive telemetry connection**

#### 1. Confidence decay function

Add to `model_profiles.py`:

```python
def apply_confidence_decay(profile: ModelProfile) -> None:
    """Decay confidence based on time since last update.

    Uses exponential decay with a half-life of 15 days:
    confidence *= 2^(-days_since_update / half_life)

    This ensures:
    - 15 days: ~50% of original confidence
    - 30 days: ~25% of original confidence
    - 45 days: ~12.5%
    """
    HALF_LIFE_DAYS = 15.0

    if profile.confidence <= 0:
        return

    days_since = (time.time() - profile.updated_at) / 86400
    if days_since < 1.0:
        return  # No decay within 24 hours

    decay_factor = 2.0 ** (-days_since / HALF_LIFE_DAYS)
    profile.confidence = max(0.0, round(profile.confidence * decay_factor, 4))
```

#### 2. Apply decay on read

In `get_profile()`:

```python
def get_profile(model_key: str) -> ModelProfile | None:
    """Get a specific model's profile, applying confidence decay."""
    store = load_profiles()
    canonical = resolve_model_key(model_key)
    if canonical is None:
        return None
    profile = store.profiles.get(canonical)
    if profile is not None:
        apply_confidence_decay(profile)
        # Persist decayed value so it's consistent
        save_profiles(store)
    return profile
```

#### 3. Connect passive telemetry path

In `hook_posttooluse.py`, the Path B (lightweight) section currently does not feed model telemetry. Add a lightweight telemetry aggregation:

```python
def _record_habit_event_lightweight(cp_config, cwd, tool_name, tool_input, tool_output):
    """Path B: Lightweight habit mode tracking..."""
    # ... existing code ...

    # NEW: Lightweight signal fire counting for model telemetry
    state_data = _load_standalone_state(cwd)
    signal_fires = state_data.get("signal_fire_counts", {})
    event_counter = state_data.get("event_counter", 0)

    # Track basic signals even without full compass
    if tool_name == "Bash":
        exit_code = _extract_exit_code(tool_output)
        if exit_code and exit_code != 0:
            signal_fires["command_failure"] = signal_fires.get("command_failure", 0) + 1
    if _is_approach_cycling(action_ring):
        signal_fires["approach_cycling"] = signal_fires.get("approach_cycling", 0) + 1

    state_data["signal_fire_counts"] = signal_fires
    # Telemetry update at session boundaries (every 50 events)
    if event_counter > 0 and event_counter % 50 == 0 and signal_fires:
        _apply_lightweight_telemetry(signal_fires, event_counter, input_data)
        state_data["signal_fire_counts"] = {}  # Reset after apply
```

#### 4. `model_profile_status` output enhancement

Show the decayed confidence and time-to-stale:

```python
output = {
    "model_key": profile.model_key,
    "confidence": profile.confidence,
    "confidence_raw": original_confidence,  # Before decay
    "confidence_decay_applied": original_confidence != profile.confidence,
    "days_since_update": round((time.time() - profile.updated_at) / 86400, 1),
    "days_to_stale": max(0, profile.stale_after_days - days_since),
    "telemetry_samples": profile.telemetry_samples,
    "signal_risk": profile.signal_risk,
}
```

#### 5. Files changed

| File | Change |
|------|--------|
| `lintgate/controlplane/model_profiles.py` | Add `apply_confidence_decay()`, integrate into `get_profile()` |
| `lintgate/hook_posttooluse.py` | Add lightweight signal counting to Path B, `_apply_lightweight_telemetry()` |
| `mcp_tools/model_profile_tools.py` | Add decay info to `model_profile_status` output |

**Complexity**: Medium — decay math is simple, but connecting the passive telemetry path requires careful state management in the lightweight flow.

---

## Issue 2: Coherence Overstates Severity

### Problem

The coherence engine classifies states as `stable → isolated → coupled → systemic → degraded`. When multiple channels fail, it jumps to `coupled` or `systemic` even when:
- All failures are pre-existing ambient debt (not caused by recent edits)
- The channels that "fail" only have informational findings
- The failure count is inflated by structural issues that are known and tracked

Step 3d (IMPROVEMENT annotation) partially addressed this, and severity-weighted demotion helps (info-only channels are demoted). But the core classification still treats 2 failing channels as `coupled` regardless of whether those failures are ambient or edit-related.

### Design

**Approach: Channel weighting + trajectory-aware classification**

#### 1. Channel weight configuration

Add to `lintgate.yaml`:

```yaml
controlplane:
  coherence:
    channel_weights:
      lint: 1.0       # Full weight — lint failures directly affect code quality
      tests: 1.0      # Full weight — test failures are always significant
      git: 0.8        # High weight — git hygiene matters but isn't blocking
      deps: 0.6       # Medium weight — dep issues are often ambient
      structure: 0.4   # Lower weight — structural findings are long-term
      behavior: 0.3    # Lowest weight — behavioral signals are advisory
```

#### 2. Weighted failure count

Replace the current `len(failed)` threshold with a weighted sum:

```python
def _weighted_failure_count(
    failed: list[ChannelResult],
    weights: dict[str, float],
) -> float:
    """Compute weighted failure count for threshold comparison."""
    total = 0.0
    for r in failed:
        w = weights.get(r.channel, 0.5)
        # Scale by finding severity composition
        blocker_ratio = _blocker_ratio(r)
        total += w * (0.3 + 0.7 * blocker_ratio)  # Min 30% of weight
    return total
```

#### 3. Trajectory-aware thresholds

Instead of hard thresholds (2 failures = coupled, 3 = systemic), use trajectory:

```python
# Current: systemic if len(failed) >= 3
# New: systemic only if weighted_count >= 2.0 AND trend is not improving

weighted = _weighted_failure_count(failed, weights)

if weighted >= 2.5:
    # Unambiguously systemic
    return _classify_systemic(...)
elif weighted >= 1.5:
    # Check trajectory before classifying
    if trajectory == "improving":
        # Downgrade to coupled — system is healing
        return _classify_coupled_with_trajectory(...)
    else:
        return _classify_systemic(...)
```

#### 4. Edit-scope integration into base classification

Currently, edit-scope overlay runs after base classification. Move relevant edit-scope logic earlier:

```python
def _compute_base_coherence(
    channel_results, *, severity_weighted=False, files_changed=None,
    channel_weights=None,
):
    """Compute base coherence with optional edit-scope awareness."""
    # ... existing partition logic ...

    # NEW: If files_changed provided, classify findings as edit-related vs ambient
    if files_changed:
        for r in failed:
            edit_count = sum(1 for f in r.findings if _is_edit_related(f, files_changed))
            r.evidence["edit_related_count"] = edit_count
            r.evidence["ambient_count"] = len(r.findings) - edit_count

    # Use edit-related findings for failure severity, not total count
    if files_changed:
        actionable_failed = [
            r for r in failed
            if r.evidence.get("edit_related_count", len(r.findings)) > 0
        ]
        ambient_only_failed = [r for r in failed if r not in actionable_failed]
        # Demote ambient-only failures for classification purposes
        for r in ambient_only_failed:
            passed.append(r)
        failed = actionable_failed
```

#### 5. Files changed

| File | Change |
|------|--------|
| `lintgate/controlplane/coherence.py` | Add `_weighted_failure_count()`, move edit-scope into base, trajectory integration |
| `lintgate/controlplane/types.py` | Add `channel_weights: dict[str, float]` to `ControlPlaneConfig` |
| `lintgate/config.py` | Parse `coherence.channel_weights` from config |

**Complexity**: Large — this touches the core classification logic. Requires careful testing to avoid breaking existing coherence contracts.

**Risk**: Understating severity. Mitigate by keeping the unweighted classification available as a `raw_state` field alongside the weighted `state`.

---

## Issue 21: Hook Output Noise During Bulk Work

### Problem

During bulk editing (e.g., refactoring 15 files), the PostToolUse hook fires on every Write/Edit/Bash. The output is repetitive: `loud=tests:fail,lint:fail` appears 15 times because tests "fail" due to missing test files and lint "fails" due to pre-existing issues. Neither finding is related to the current edit.

The agent either ignores the output (bad — misses real issues) or pauses to investigate each one (wasteful).

### Design

**Approach: `hook_verbosity` config with three levels**

#### 1. Config

```yaml
controlplane:
  hook_verbosity: "pulse"  # silent | pulse | full
  # Or auto-detect:
  # hook_verbosity: "auto"  # Switches to pulse during habit mode
```

| Level | Behavior | When |
|-------|----------|------|
| `silent` | Suppress all hook output (still run linters internally for state tracking) | Manual opt-in for known-clean bulk work |
| `pulse` | Emit once every N events (configurable, default 5). Show delta since last pulse. | Habit mode auto-switch, or explicit config |
| `full` | Current behavior — every event gets full output | Default; non-habit mode |

#### 2. Implementation

In `hook_posttooluse.py`, add a gate before `_format_report()`:

```python
def _should_emit_output(
    cp_config,
    session_data: dict,
    habit_state: HabitState | None,
) -> tuple[bool, str]:
    """Determine whether to emit hook output.

    Returns (should_emit, reason).
    """
    verbosity = _resolve_verbosity(cp_config, habit_state)

    if verbosity == "silent":
        return False, "silent mode"

    if verbosity == "pulse":
        event_counter = session_data.get("event_counter", 0)
        pulse_interval = getattr(cp_config, "hook_pulse_interval", 5)
        last_pulse = session_data.get("_last_pulse_event", 0)
        if event_counter - last_pulse < pulse_interval:
            return False, f"pulse: {pulse_interval - (event_counter - last_pulse)} events until next"
        session_data["_last_pulse_event"] = event_counter
        return True, "pulse: emitting"

    return True, "full verbosity"


def _resolve_verbosity(cp_config, habit_state: HabitState | None) -> str:
    """Resolve effective verbosity, with auto-detection."""
    configured = getattr(cp_config, "hook_verbosity", "full")
    if configured == "auto":
        if habit_state and habit_state.active:
            return "pulse"
        return "full"
    return configured
```

#### 3. Pulse output format

When in pulse mode, the output is a compact delta:

```json
{
  "pulse": true,
  "events_since_last": 5,
  "delta": {
    "new_blockers": 0,
    "resolved_blockers": 1,
    "files_edited": ["foo.py", "bar.py"],
    "habit_score": 0.55,
    "context_pressure": "23%"
  },
  "next_full_at": "event #45 or on blocker change"
}
```

#### 4. Force-emit on significant changes

Even in pulse/silent mode, force-emit on:
- New blocking finding (severity escalation)
- Test failure (test channel goes from pass → fail)
- Secret detected in diff
- Disposition triggers (from Issue 24 design)

```python
def _should_force_emit(
    current_results: dict,
    previous_results: dict,
) -> bool:
    """Force emission regardless of verbosity."""
    # New blockers
    curr_blockers = current_results.get("blocking_count", 0)
    prev_blockers = previous_results.get("blocking_count", 0)
    if curr_blockers > prev_blockers:
        return True

    # Test regression
    if (current_results.get("test_status") == "fail"
            and previous_results.get("test_status") == "pass"):
        return True

    # Secret detected
    if current_results.get("secrets_found"):
        return True

    return False
```

#### 5. Files changed

| File | Change |
|------|--------|
| `lintgate/hook_posttooluse.py` | Add `_should_emit_output()`, `_resolve_verbosity()`, `_should_force_emit()`, pulse format, gate before `_format_report()` |
| `lintgate/controlplane/types.py` | Add `hook_verbosity: str` and `hook_pulse_interval: int` to `ControlPlaneConfig` |
| `lintgate/config.py` | Parse `hook_verbosity` and `hook_pulse_interval` |

**Complexity**: Medium — the verbosity gate is straightforward, but pulse-mode delta computation requires tracking previous results.

---

## Issue 4: B105 False Positives on Checkmarks

### Problem

Bandit's B105 (`hardcoded_password_string`) flags Unicode checkmark `✓` and the string `'50'` as "Possible hardcoded password." The existing `severity_overrides` in `lintgate.yaml` already downgrades B105 to `informational`, but the findings still appear in output and consume attention.

### Design

**Approach: Post-processing filter with configurable allowlist**

#### 1. Bandit result post-filter

Add a filtering step to `BanditFastLinter.run()` that strips known false-positive patterns:

```python
# In bandit_fast_linter.py

# Known B105 false positive patterns
_B105_ALLOWLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r"[✓✔✗✘☑☐●○]"),  # Unicode symbols
    re.compile(r"^'?\d{1,3}'?$"),  # Short numeric strings like '50'
    re.compile(r"^'?(true|false|yes|no|on|off|ok|none|null)'?$", re.IGNORECASE),
    re.compile(r"^'?[*#\-=.]+\s*'?$"),  # Separator strings
]

def _is_b105_false_positive(issue_text: str) -> bool:
    """Check if a B105 finding matches known false-positive patterns."""
    # Extract the flagged value from "Possible hardcoded password: 'VALUE'"
    match = re.search(r"['\"]([^'\"]+)['\"]", issue_text)
    if not match:
        return False
    value = match.group(1)

    # Check against allowlist
    for pattern in _B105_ALLOWLIST_PATTERNS:
        if pattern.search(value):
            return True

    # Very short values (1-2 chars) are almost always false positives
    if len(value) <= 2:
        return True

    return False
```

#### 2. Integration into the linter run loop

```python
def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
    # ... existing code ...

    for item in results:
        test_id = item.get("test_id", "")
        # NEW: Filter B105 false positives
        if test_id == "B105" and _is_b105_false_positive(item.get("issue_text", "")):
            continue

        yield LintIssue(...)
```

#### 3. Configurable extension via `lintgate.yaml`

```yaml
quality_policy:
  security:
    tolerated_false_positives:
      - rule: "B105"
        scope: "value_pattern"
        pattern: "✓"
        reason: "Unicode checkmark used in CLI formatting"
      - rule: "B105"
        scope: "value_pattern"
        pattern: "^\\d{1,3}$"
        reason: "Short numeric strings are not passwords"
```

The existing `ToleratedFalsePositive` type supports `rule`, `file`, `scope`, and `reason`. Extend `scope` to support `"value_pattern"` which applies pattern matching to the flagged value.

#### 4. Files changed

| File | Change |
|------|--------|
| `lintgate/linters/bandit_fast_linter.py` | Add `_B105_ALLOWLIST_PATTERNS`, `_is_b105_false_positive()`, filter in `run()` |
| `lintgate/linters/bandit_linter.py` | Same filter for the full bandit path |
| `lintgate/types.py` | Document `value_pattern` scope in `ToleratedFalsePositive` |
| `lintgate/config.py` | Parse `value_pattern` scope from config |

**Complexity**: Small — regex matching with a static allowlist.

**Risk**: Filtering real password findings. Mitigate by keeping the allowlist tight (only obvious non-password patterns) and logging filtered findings at debug level.

---

## Implementation Priority

| Priority | Issue | Complexity | Impact | Rationale |
|----------|-------|-----------|--------|-----------|
| 1 | Issue 21 (hook verbosity) | Medium | High | Directly affects agent productivity during bulk work |
| 2 | Issue 24 (disposition enforcement) | Medium | High | Closes the intent-action gap for habit mode |
| 3 | Issue 2 (coherence weighting) | Large | High | Core classification accuracy affects all ControlPlane users |
| 4 | Issue 25 (tradeoff detection) | Medium | Medium | Prevents false regression signals during refactoring |
| 5 | Issue 26 (re-export detection) | Small | Medium | Reduces STRUCT003 noise for well-structured projects |
| 6 | Issue 16 (confidence decay) | Medium | Medium | Ensures model profiles don't become stale silently |
| 7 | Issue 4 (B105 filter) | Small | Low | Already downgraded; this is polish |
| 8 | Issue 14 (naming) | Small | Low | UX clarity; can ship in any release |
| 9 | Issue 9 (build artifacts) | Small | Low | Affects projects with build scripts referencing CLAUDE.md |
| 10 | Issue 11 (reset flag) | Small | Low | Convenience feature for power users |

---

## Dependencies Between Designs

```
Issue 24 (dispositions) ──depends-on──▶ Issue 21 (verbosity)
  └─ Disposition injection needs to respect verbosity levels.
     Priority 3 dispositions force-emit even in pulse mode.

Issue 2 (weighting) ──depends-on──▶ Issue 25 (tradeoffs)
  └─ Tradeoff detection feeds into coherence classification.
     Implement weighting first, then tradeoff as an enrichment.

Issue 16 (decay) ──independent
Issue 26 (re-export) ──independent
Issue 4 (B105) ──independent
Issue 14 (naming) ──independent
Issue 9 (build artifacts) ──independent
Issue 11 (reset) ──independent
```

Recommended implementation order: 21 → 24 → 2 → 25 → 26 → 16 → 4 → 14 → 9 → 11
