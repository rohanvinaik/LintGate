# Claude-Native Loupe and Intent Gates for LintGate

**Source**: Design synthesis from field discussion about minimal tool surfaces, hidden artifact context, advisory-only drift controls, and Prism's narrative-compaction architecture.

**Date**: 2026-04-11

**Scope**: Improvements to the LintGate codebase, explicitly scoped to **Claude Code's hook architecture** as currently used on this machine.

---

## Summary

This design proposes two linked improvements to LintGate:

1. **`loupe`** — a single Claude-facing meta-tool for low-token discovery and surgical artifact drill-down.
2. **Intent gates** — a Claude-native commitment and checkpoint system that turns user intent into enforceable session state instead of advisory prose.

The core claim is structural:

- **Prism** should continue to own **narrative state, telemetry, and compaction framing**.
- **LintGate** should own **project-local tool discovery, artifact navigation, and action governance**.

These are complementary. Prism shapes what enters context. LintGate shapes what exits as state-changing action.

---

## Why This Is Needed

Two failure modes are showing up in real use:

1. **Tool-surface minimization hides useful capability.**
   LintGate already has many strong drill-down surfaces, but they are fragmented:
   `query_analysis`, `controlplane_get_details`, `lint_get_details`, `what_next`,
   `tool_applicability_guide`, and per-tool `analysis_id` envelopes. An agent that does
   not already know which one to call can still get lost.

2. **Behavioral guidance is mostly advisory.**
   LintGate can already advise and sometimes block, but it does not yet persist a
   session-level "the user explicitly told me to do X before Y" contract and enforce it
   at natural checkpoints. That makes drift easy and silent.

The result is a mismatch:

- The codebase already computes more structure than the agent can reliably discover.
- The hook layer already intercepts actions, but it does not yet enforce explicit user
  commitments.

---

## Claude Code Constraints

This design is intentionally limited to what Claude Code's actual hook protocol can do.

### Verified local hook surfaces

From the current local Claude settings at `~/.claude/settings.json`:

- `PostToolUse` is wired for LintGate on `Write|Edit|MultiEdit|Bash`
- `PreToolUse` is wired for LintGate on `Bash`
- `UserPromptSubmit`, `PreCompact`, `Stop`, and `SessionStart` are wired for Prism

### Verified response capabilities in the current code

From the existing hook implementations:

- `PreToolUse` can hard-block by returning:
  ```json
  {"continue": false, "systemMessage": "..."}
  ```
  This already exists in [pre_tool.py](/Users/rohanvinaik/tools/lintgate/lintgate/hooks/pre_tool.py).

- `PostToolUse` can advise and inject compact context via:
  ```json
  {
    "hookSpecificOutput": {
      "hookEventName": "PostToolUse",
      "additionalContext": "..."
    }
  }
  ```
  This already exists in [agent_reporter.py](/Users/rohanvinaik/tools/lintgate/lintgate/agent_reporter.py).

- `PreCompact` can inject `additionalContext`.
  Prism already does this in [hooks.py](/Users/rohanvinaik/tools/Prism/src/prism/hooks.py).

- `UserPromptSubmit` can capture raw user text before tool execution.
  Prism already uses this in [hooks.py](/Users/rohanvinaik/tools/Prism/src/prism/hooks.py)
  and [phase_matcher.py](/Users/rohanvinaik/tools/Prism/src/prism/phase_matcher.py).

### Architectural implication

Claude Code does **not** let LintGate control the model's output token stream directly.
The controllable surfaces are:

- user prompt capture
- pre-tool decision points
- post-tool reporting
- compaction framing

So the design must enforce behavior at **tool and checkpoint boundaries**, not in free
text generation.

---

## Current State in LintGate

LintGate already contains most of the substrate needed for a "Loupe" layer.

### Existing strengths

1. **Disk-first artifact persistence**

   [mcp_tools/_disk_helpers.py](/Users/rohanvinaik/tools/lintgate/mcp_tools/_disk_helpers.py)
   already saves tool outputs into `.lintgate/analysis/<tool>/<id>.json` and classifies
   queryable sections into:

   - `meta`
   - `status`
   - `metrics`
   - `findings`
   - `actions`
   - `config`
   - `data`

2. **Analysis drill-down**

   [mcp_server.py](/Users/rohanvinaik/tools/lintgate/mcp_server.py) already provides:

   - `query_analysis(analysis_id, tool_name, path, section, offset, max_items)`
   - `what_next(path)`

3. **Workflow-specific detail tools**

   LintGate already has direct drill-down tools such as:

   - `controlplane_get_details`
   - `lint_get_details`
   - `query_analysis`

4. **Pre-tool enforcement exists, but narrowly**

   [pre_tool.py](/Users/rohanvinaik/tools/lintgate/lintgate/hooks/pre_tool.py) already:

   - blocks `git push` when quality-gate conditions fail
   - advises on compass alignment
   - emits prescriptive-spec obligation guidance

### Current gaps

1. **Discovery is fragmented.**
   The agent must already know whether to use `query_analysis`, a `*_get_details` tool,
   `what_next`, or raw `Bash`.

2. **Artifact navigation is brittle.**
   `query_analysis` works, but past retrospectives already note path brittleness and
   oversized responses. The envelope exists; the retrieval ergonomics are still weak.

3. **No persistent session commitment ledger.**
   LintGate tracks compliance outcomes and behavior, but it does not yet store a compact
   "the user asked for these tool calls before this checkpoint" contract.

4. **LintGate is not yet participating in `UserPromptSubmit` or `PreCompact` in the
   live local hook wiring.**
   Prism is doing that work today.

---

## Current State in Prism

Prism is important here because it already solves the **Claude-native narrative side** of
the problem.

### Relevant existing capabilities

1. **User prompt capture**

   Prism records the latest prompt text via `UserPromptSubmit` in
   [hooks.py](/Users/rohanvinaik/tools/Prism/src/prism/hooks.py).

2. **Phase state persisted on disk**

   Prism maintains per-session phase state in `~/.claude/prism/phase_state/...` via
   [phase_matcher.py](/Users/rohanvinaik/tools/Prism/src/prism/phase_matcher.py).

3. **Compaction framing**

   Prism injects a compact narrative frame via `PreCompact`.

4. **Compact-first artifact navigation**

   Prism tools write full snapshots to disk and expose `prism_details` as the drill-down
   path in [server.py](/Users/rohanvinaik/tools/Prism/src/prism/server.py) and
   [engine.py](/Users/rohanvinaik/tools/Prism/src/prism/engine.py).

### Design conclusion

Prism should be treated as the reference implementation for:

- prompt capture
- phase/narrative persistence
- compaction framing
- compact-first drill-down ergonomics

LintGate should not reimplement Prism's session analytics stack. It should adopt the same
Claude-native pattern where it improves project-local action and artifact use.

---

## Goals

1. Make LintGate's trimmed surface safe by keeping one always-available discovery tool.
2. Let an agent find the right next tool or the right 1-20 lines of artifact context
   without loading full analysis blobs.
3. Turn explicit user tool-use commitments into session state.
4. Enforce those commitments at natural Claude checkpoints.
5. Keep all new behavior symbolic, local, and cheap.
6. Preserve Claude-native compatibility: no assumptions about controlling raw generation.

---

## Non-Goals

1. Replace Prism.
2. Force every tool call through hard blocking.
3. Introduce LLM inference into supervision.
4. Create a second full artifact store parallel to `.lintgate/analysis`.
5. Depend on Prism for core LintGate correctness.

---

## Proposal A: `loupe`

### Product stance

`loupe` is a **recipe-and-routing tool**, not an executor.

Its job is to answer one of three questions in under ~300 tokens:

1. **What tool should I call?**
2. **Which saved artifact contains the answer?**
3. **What exact retrieval call should I make next?**

### Surface design

Add one new MCP tool:

```text
loupe(
    query: str,
    mode: str = "auto",
    analysis_id: str = "",
    tool_name: str = "",
    current_tool: str = "",
    path: str = "",
    project_root: str = "",
)
```

### Modes

#### `mode="find"`

Purpose: tool discovery.

Examples:

- "Find the tool for surviving VALUE mutants in a file"
- "What should I use to continue after controlplane_run?"

Output shape:

```json
{
  "mode": "find",
  "matched_tools": [
    {
      "tool": "mutation_run_sampling",
      "reason": "Fast sampled mutation profiling by file/function",
      "args_template": {"path": "<project>", "file": "<relative.py>"},
      "confidence": "high"
    }
  ],
  "next_action": {
    "tool": "mutation_run_sampling",
    "args_template": {"path": "<project>", "file": "<relative.py>"}
  }
}
```

#### `mode="extract"`

Purpose: surgical artifact drill-down.

Examples:

- "From the latest mutation run for `test_test_regeneration_impl.py`, show surviving
  SWAP categories"
- "Given this `analysis_id`, how do I pull the blocking findings only?"

Output shape:

```json
{
  "mode": "extract",
  "artifact": {
    "analysis_id": "abc123",
    "tool_name": "mutation_run_sampling",
    "file": "/abs/path/.lintgate/analysis/mutation_run_sampling/abc123.json"
  },
  "recommended_call": {
    "tool": "query_analysis",
    "args": {
      "analysis_id": "abc123",
      "section": "findings"
    }
  },
  "fallback_recipe": "python - <<'PY' ...",
  "expected_output": "List of surviving mutant categories for the selected file"
}
```

#### `mode="next"`

Purpose: pipeline navigation.

Examples:

- "I just ran `spec_file_analyze`; what is the canonical next tool?"
- "After `test_rebuild_plan`, what comes next?"

Output shape:

```json
{
  "mode": "next",
  "current_tool": "spec_file_analyze",
  "recommended_action": {
    "tool": "mutation_run_sampling",
    "reason": "Fast behavioral drill-down after symbolic spec analysis",
    "args_template": {"path": "<project>", "file": "<same-file>"}
  }
}
```

### Why one tool, not three

The problem being solved is **surface minimization**. Adding three new top-level tools
would reproduce the current problem at a higher level. `loupe` should be the single
always-visible escape hatch.

---

## `loupe` Architecture

### 1. Tool catalog

New module family:

- `lintgate/loupe/catalog.py`
- `mcp_tools/loupe_tools.py`

Responsibilities:

- build a queryable catalog of registered MCP tools
- attach:
  - short purpose
  - arg shapes
  - "works well for" hints
  - canonical follow-ups

Primary sources:

- MCP registration metadata
- tool docstrings
- hand-authored overrides for the highest-value tools

This should not parse prose at runtime from `AGENTS.md`. The catalog should be a
machine-first index.

### 2. Artifact registry

New module:

- `lintgate/loupe/artifacts.py`

Responsibilities:

- scan `.lintgate/analysis/**`
- index recent artifacts by:
  - `analysis_id`
  - `tool_name`
  - file path
  - mtime
  - `_sections`
  - inferred artifact kind
- expose "latest artifact by tool" lookup

This is a thin layer over the existing disk-first envelope, not a new storage system.

### 3. Recipe compiler

New module:

- `lintgate/loupe/recipes.py`

Responsibilities:

- choose the cheapest next retrieval primitive

Priority order:

1. direct MCP detail tool (`controlplane_get_details`, `lint_get_details`)
2. `query_analysis`
3. `Read` on a narrow file location
4. `Bash` with generated `jq` / Python / `rg` fallback

Rule:

If the artifact is a LintGate envelope, prefer a LintGate-native retrieval call over raw
shell recipes.

### 4. Envelope enrichment

Extend [mcp_tools/_disk_helpers.py](/Users/rohanvinaik/tools/lintgate/mcp_tools/_disk_helpers.py)
so saved analysis envelopes can optionally include:

```json
"_loupe": {
  "artifact_kind": "mutation_profile",
  "preferred_queries": [
    {"section": "findings"},
    {"path": "counts.blocking"}
  ],
  "producer": "mutation_run_sampling"
}
```

This is metadata only. No breaking schema change is required.

---

## Proposal B: Intent Gates

### Product stance

The enforcement system should not try to police every action. It should enforce **small,
explicit commitments** at **natural checkpoints**.

The correct question is not:

"Can LintGate force the model to think correctly?"

The correct question is:

"Can LintGate make it impossible to silently ignore an explicit user instruction before a
state-changing checkpoint?"

### Core design

Add a small committed-intent ledger to session memory:

```json
{
  "committed_intent": {
    "source_prompt": "use the tooling before push",
    "must_call": ["spec_file_analyze", "test_rebuild_plan", "test_rebuild_generate"],
    "before_checkpoint": "git_push",
    "created_at": 1712860000.0,
    "status": "active"
  },
  "observed_calls": ["controlplane_run", "spec_file_analyze"],
  "drift_score": 2,
  "overrides": []
}
```

---

## Intent Gate Architecture

### 1. `UserPromptSubmit` capture inside LintGate

Add a new LintGate hook entrypoint for `UserPromptSubmit`.

New file:

- `lintgate/hooks/user_prompt.py`

Responsibilities:

- capture raw prompt text
- extract explicit commitments using symbolic heuristics
- persist committed intent to session memory

This mirrors Prism's prompt capture pattern but stores action commitments rather than
narrative text.

### 2. Commitment extraction

New module:

- `lintgate/intent/commit.py`

Responsibilities:

- detect:
  - named tools
  - phrases like "use the tooling"
  - "before push", "before commit", "then ship", "run X first"
  - golden-path references such as "use platonic", "run the mutation pipeline"

This should be conservative. It is better to miss weak intent than to hard-block on bad
parsing.

### 3. Observed call ledger

Extend session memory update paths so MCP and hook-visible tool calls append to
`observed_calls`.

This can build on the existing compliance/session memory plumbing rather than introducing
a second event store.

### 4. Checkpoint enforcement in `PreToolUse`

Extend [pre_tool.py](/Users/rohanvinaik/tools/lintgate/lintgate/hooks/pre_tool.py).

Checkpoint types:

- `git_push` — hard block
- `git_commit` — advisory by default, optional hard block
- first structural write after explicit "analyze first" prompt — advisory first

Example block:

```json
{
  "continue": false,
  "systemMessage": "[IntentGate] BLOCKED: session commitment requires spec_file_analyze, test_rebuild_plan, test_rebuild_generate before git push. Missing: test_rebuild_plan, test_rebuild_generate. Run them now or call renegotiate_intent with rationale."
}
```

### 5. Explicit override path

Add a new MCP tool:

```text
renegotiate_intent(path: str, rationale: str, drop_tools: list[str] = [])
```

Purpose:

- explicitly record why the agent is deviating
- clear or weaken active commitments
- leave a forensics trail

This is required to avoid dead-end blocking.

### 6. Post-tool drift escalation

Add a soft drift counter updated on `PostToolUse`.

Rules:

- ignoring advisories increments `drift_score`
- satisfying commitments decrements it
- exceeding a threshold promotes the next checkpoint from advisory to blocking

This preserves the current light-touch feel during normal work while giving advisory
signals future teeth.

---

## Division of Labor: LintGate vs Prism

### Prism owns

- narrative phase state
- prompt text for compaction framing
- session analytics
- compact-first historical drill-down

### LintGate owns

- project-local artifact discovery
- tool selection over its own MCP surface
- committed intent extraction
- checkpoint gating over state-changing actions

### Optional bridge

LintGate may optionally read Prism state if present, but only as enrichment:

- current work phase can help `loupe` rank suggestions
- recent compaction boundaries can help intent-gate messaging

This must remain optional and fail-open.

---

## Hook Topology Changes

### Current live topology

From `~/.claude/settings.json`:

- LintGate:
  - `PostToolUse`: `Write|Edit|MultiEdit|Bash`
  - `PreToolUse`: `Bash`
- Prism:
  - `PostToolUse`
  - `SessionStart`
  - `PreCompact`
  - `Stop`
  - `UserPromptSubmit`

### Proposed LintGate topology

Update LintGate hook generation and recommended setup so LintGate also registers:

- `UserPromptSubmit`
- `PreCompact`
- `PreToolUse` matcher expanded to `Write|Edit|MultiEdit|Bash`

Rationale:

- `UserPromptSubmit` is needed for commitment capture
- `PreCompact` is needed to surface committed intent in compaction context
- `Write|Edit|MultiEdit` pre-tool visibility is needed for "analyze before edit" advisory
  flows

LintGate should still fail-open when these hooks are absent.

---

## Claude-Native Behavior Model

This design is explicitly shaped around Claude Code's actual affordances:

### Attention shaping

Use:

- `UserPromptSubmit`
- `PreCompact`
- `PostToolUse.additionalContext`

For:

- preserving the task story
- surfacing the active commitment
- pointing to the next minimal retrieval action

### Action shaping

Use:

- `PreToolUse continue=false`

For:

- checkpoint enforcement only

This keeps hard blocking narrow and predictable.

---

## Implementation Plan

### Phase 1: Loupe foundation

Files:

- `mcp_tools/loupe_tools.py`
- `lintgate/loupe/catalog.py`
- `lintgate/loupe/artifacts.py`
- `lintgate/loupe/recipes.py`

Changes:

- add `loupe`
- scan existing LintGate analysis envelopes
- prefer `query_analysis` and existing detail tools

### Phase 2: Query ergonomics hardening

Files:

- [mcp_server.py](/Users/rohanvinaik/tools/lintgate/mcp_server.py)
- [mcp_tools/_disk_helpers.py](/Users/rohanvinaik/tools/lintgate/mcp_tools/_disk_helpers.py)

Changes:

- add better manifest hints
- improve list pagination and error hints in `query_analysis`
- add optional `_loupe` metadata in envelopes

### Phase 3: Intent ledger

Files:

- `lintgate/hooks/user_prompt.py`
- `lintgate/intent/commit.py`
- session-memory modules

Changes:

- capture commitments
- persist `must_call` and checkpoint target

### Phase 4: Checkpoint gating

Files:

- [pre_tool.py](/Users/rohanvinaik/tools/lintgate/lintgate/hooks/pre_tool.py)
- `mcp_tools/intent_tools.py`

Changes:

- enforce missing commitments on `git push`
- add `renegotiate_intent`
- expand advisory gating to write/edit entrypoints

### Phase 5: Optional Prism enrichment

Files:

- `lintgate/loupe/prism_bridge.py`

Changes:

- read Prism phase state if available
- enrich ranking only

---

## Test Plan

### Loupe

1. `test_loupe_find_mutation_tool`
2. `test_loupe_extract_prefers_query_analysis_for_envelope`
3. `test_loupe_extract_falls_back_to_shell_recipe_for_non_envelope_artifact`
4. `test_loupe_next_returns_canonical_followup`
5. `test_loupe_output_stays_bounded`

### Intent gates

1. `test_user_prompt_creates_commitment_for_named_tools`
2. `test_git_push_blocked_when_required_tools_missing`
3. `test_git_push_allowed_when_commitment_satisfied`
4. `test_renegotiate_intent_clears_block`
5. `test_write_checkpoint_only_advises_by_default`
6. `test_missing_user_prompt_hook_fails_open`

### Claude-hook integration

1. `test_setup_hooks_includes_user_prompt_for_lintgate`
2. `test_setup_hooks_expands_pretool_matcher`
3. `test_precompact_context_includes_committed_intent_when_present`

---

## Acceptance Criteria

1. From a cold start, an agent can discover the correct LintGate tool for a task in one
   `loupe` call.
2. From a saved `analysis_id`, an agent can retrieve the relevant subsection without
   opening the whole artifact.
3. A session that explicitly commits to required tools before `git push` cannot silently
   skip them.
4. Deviation is possible, but only through an explicit recorded override.
5. The design works within Claude Code's existing hook contract without assuming access
   to hidden model internals.

---

## Open Questions

1. Should `renegotiate_intent` be a new dedicated tool, or should this be folded into
   `controlplane_agent_feedback`?
2. Should `loupe` return Claude built-in tool recipes (`Read`, `Bash`) in addition to MCP
   calls, or only MCP-native next actions?
3. Should the first release restrict hard gating to `git push`, or also support
   `git commit` behind config?
4. Should LintGate read Prism phase state directly, or should Prism expose a tiny bridge
   file similar to its existing health bridge?

---

## Recommendation

Implement this in the following order:

1. `loupe`
2. `query_analysis` hardening
3. `UserPromptSubmit` commitment capture
4. `git push` intent gate
5. explicit override tool

That order matters. Better discovery and artifact retrieval reduce drift immediately.
Checkpoint enforcement should come only after the system can reliably tell the agent what
to do next.
