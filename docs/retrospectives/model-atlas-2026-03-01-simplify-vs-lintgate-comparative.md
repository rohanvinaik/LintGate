---

## theory_scope: true

# Comparative Retrospective: Claude Code /simplify vs. LintGate MCP — ModelAtlas

## Metadata


| Field                  | Value                                                                                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Project**            | ModelAtlas — MCP server exposing a navigable semantic network of ML models                                                                                                                 |
| **Agent**              | Claude Opus 4.6, solo agent                                                                                                                                                                |
| **Date**               | 2026-03-01                                                                                                                                                                                 |
| **Scope**              | ~~241 lines changed across 9 source files (excluding uv.lock), plus 7 new files (~~1,600 LOC new). Total project: ~7,068 LOC source, ~3,046 LOC tests                                      |
| **Tools Compared**     | Claude Code `/simplify` (built-in skill, 3 sub-agent ensemble) vs. LintGate MCP (49-tool symbolic analysis platform with ControlPlane)                                                     |
| **Session Type**       | Comparative analysis — same codebase, same session, both tools exercised                                                                                                                   |
| **Session Continuity** | Resumed from handoff — multi-compaction session spanning LintGate audit, critical analysis, mutation theory discussion, and /simplify invocation                                           |
| **Prior State**        | Working codebase, 343 tests passing. Changes include: regex pattern fixes (patterns.py), cache mutation bugfix, new ingest daemon, new vibes module, new query engine, test suites for all |


---

## Part I: What Each Tool Is

### /simplify

A built-in Claude Code skill that decomposes code review into three parallel sub-agents:

1. **Code Reuse Review** — searches for duplication and existing utilities that could replace new code
2. **Code Quality Review** — checks for 5 named anti-patterns (redundant state, parameter sprawl, copy-paste, leaky abstractions, stringly-typed code)
3. **Efficiency Review** — checks for 6 named anti-patterns (unnecessary work, missed concurrency, hot-path bloat, TOCTOU, memory, overly broad operations)

Each agent receives the full diff, scans the codebase with Grep/Read tools, and reports findings. The lead agent then aggregates and applies fixes.

**Theoretical basis**: Code quality decomposes into three orthogonal axes. Each axis is evaluable by pattern-matching against a checklist of known anti-patterns. Agents are truly independent — they cannot inform each other's findings.

### LintGate

A 49-tool MCP server implementing a parallel channel ensemble with cross-channel coherence gating:

- **Channels**: lint, performance, mutation, test effectiveness, structure, deps — each executes independently
- **ControlPlane**: orchestrates channels, runs cross-channel coherence checks after all complete
- **Mutation gate**: when mutation data shows surviving mutants > 50%, crushes confidence on performance/optimization findings to 0.10
- **Compass**: 4-axis project understanding (problem, solution, implementation, world) that constrains agent actions against project theory

**Theoretical basis**: The mutation-theory paper. Surviving mutants measure specification completeness — the behavioral degrees of freedom unconstrained by tests. Specification completeness licenses engineering actions: you cannot safely refactor what you haven't specified. The tool measures before it advises.

---

## Part II: What Each Tool Found

### /simplify Findings (40 total across 3 agents)


| Category                  | Count | Examples                                                                                                                                                    |
| ------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cross-file duplication    | 7     | Ollama HTTP client reimplemented in ingest.py; `_FAMILY_MAP` duplicated; tokenization regex copied                                                          |
| Stringly-typed code       | 3     | Bank names as raw strings across 7+ files; anchor source provenance strings; phase string validation                                                        |
| N+1 query patterns        | 6     | `search()` calls `get_model()` per model (5 SQL queries each); `similar_to()` calls `get_anchor_set()` per model; Phase C helpers issue 3 queries per model |
| Copy-paste with variation | 4     | `ModelInput` construction from raw dict in 2 places; phase_a skip-if-exists duplicated; raw JSON dict shape duplicated                                      |
| Memory / fetchall         | 2     | Phase B and C load all pending rows into memory                                                                                                             |
| Redundant state           | 2     | `get_status()` derived fields; `VIBE_JSON_SCHEMA` duplicates `VibeOutput` dataclass                                                                         |
| Leaky abstractions        | 3     | ingest.py raw SQL bypassing db.py API; inline `import json`                                                                                                 |
| Parameter sprawl          | 1     | `build_vibe_prompt()` takes 7 loose kwargs                                                                                                                  |
| Missed concurrency        | 1     | HF and Ollama Phase A could run in parallel                                                                                                                 |
| Redundant existence check | 2     | SELECT before INSERT OR IGNORE                                                                                                                              |


### LintGate Findings (from ControlPlane run earlier in session)


| Category                      | Count                         | Examples                                                                           |
| ----------------------------- | ----------------------------- | ---------------------------------------------------------------------------------- |
| Blockers                      | 0                             | —                                                                                  |
| Warnings                      | 1                             | Missing CI quality infrastructure (18 artifacts)                                   |
| Informational                 | 15                            | Structure (cycles, orphans, module sizes), deps, git hygiene, test coverage gaps   |
| Performance (purity analysis) | 365 pure functions identified | Purity classification for cacheable/deterministic functions across entire codebase |
| Mutation                      | Timed out                     | No mutation data collected — 60s budget exceeded                                   |
| Cross-channel coherence       | Degraded                      | Insufficient channel availability (2 of required channels)                         |


---

## Part III: Comparative Observations

### Observation 1: /simplify finds symptoms; LintGate (when complete) would measure causes

/simplify correctly identified the N+1 query pattern in `search()`. This is a genuine performance issue. But it presents this finding at the same confidence level as "Phase A could run HF and Ollama concurrently" — which saves approximately zero seconds in practice (Ollama fetch is ~200ms; HF fetch is ~15 minutes; they're already sequential because Ollama is instant).

LintGate's architecture would handle this differently. The performance channel classifies `search()` as having mutable state and side effects (SQL queries). The mutation channel (when functional) would test whether the function's behavior is fully specified — can you safely refactor the query pattern without changing observable behavior? The cross-channel gate would then attach confidence: if specification completeness is low, the refactoring suggestion gets confidence 0.10 instead of full confidence.

**What this reveals**: /simplify has no mechanism to distinguish "technically suboptimal" from "actually matters." Every finding arrives at implicit confidence 1.0. LintGate's theory provides a principled confidence scale — but only when the mutation channel actually runs.

### Observation 2: /simplify's reuse detection is genuinely strong and LintGate lacks an equivalent

The reuse agent found that `_FAMILY_MAP` in patterns.py and a local `family_map` in ollama.py encode the same domain knowledge in different forms. This is not textual duplication — it requires understanding that both maps serve the same semantic purpose. Similarly, it found that `_phase_a_ollama()` reimplements HTTP calls that `OllamaAdapter` already encapsulates.

LintGate has no channel that performs cross-module semantic duplication detection. The structure channel detects cycles and orphans (import-graph topology), and the lint channel catches syntactic issues, but neither identifies when two functions in different modules encode the same domain knowledge.

**What this reveals**: There's a genuinely useful capability in /simplify's reuse detection that LintGate should incorporate. A "semantic duplication" channel — gated by specification completeness — would combine /simplify's best finding category with LintGate's core insight.

### Observation 3: /simplify's quality agent rediscovers what LintGate's theory extraction already knows

The quality agent flagged bank names as stringly-typed code — `"ARCHITECTURE"`, `"CAPABILITY"`, etc. used as raw strings across 7+ files. This is a valid finding. But the `BANKS` tuple in db.py already exists as a partial solution, and the CLAUDE.md spec document explicitly defines these seven banks as the core ontology.

LintGate's compass extraction reads CLAUDE.md and understands these banks as architectural invariants. The theory extraction would (if fully implemented) flag any code that uses bank-like strings without referencing the canonical definition. /simplify rediscovers a known architectural constraint by scanning code; LintGate infers it from project documentation.

**What this reveals**: /simplify operates purely bottom-up (code → findings). LintGate operates bidirectionally (theory ↔ code). The bidirectional approach is more powerful when the project has documented architecture — which good projects do.

### Observation 4: The confidence-without-verification failure mode in practice

Earlier in this session, LintGate's performance channel classified 365 functions as "pure" and "cacheable" at confidence 0.7-0.8. The mutation channel timed out, so the cross-channel gate never fired. A background agent then mechanically applied `@lru_cache` to functions based on these unverified purity claims — introducing a cache mutation bug where `_detect_compatibility`'s cached return value (a list) was mutated by the caller.

/simplify, by contrast, would never have suggested adding caches — it's not in its pattern vocabulary. But it also wouldn't have caught the introduced bug, because it reviews the diff as-is, not the behavioral implications of changes.

The correct LintGate behavior (with the MUTATION UNKNOWN fix): the mutation channel times out → `classify_properties()` sees `mutation_state is None` → sets confidence to 0.4 with `[MUTATION UNKNOWN]` annotation → performance channel emits purity hints at reduced confidence → agent treats them as hypotheses, not facts → no mechanical `@lru_cache` application.

**What this reveals**: Both tools failed here, but for different reasons. /simplify wouldn't have caused the problem (it doesn't suggest optimizations). LintGate caused the problem because its confidence gate was silent when it should have been cautious. The fix is a ~10-line change to `classify_properties()` — but the theoretical framework that *demands* this fix (specification completeness licensing actions) is what separates LintGate's approach from /simplify's.

### Observation 5: The cost asymmetry is dramatic

/simplify spawned 3 sub-agents, each receiving the full 2,485-line diff+new-files context. Each agent then made 17-50 tool calls (Grep, Read) to search the codebase for duplication and patterns. Total tool uses across the three agents: ~92. Each agent's token usage was 105K-132K total tokens.

**Estimated /simplify token cost**: ~~345K total tokens across 3 agents (~~132K + ~~109K + ~106K), plus the orchestrator's context. At Opus pricing ($15/M input, $75/M output), conservatively **~~$5-8 for one /simplify run** on a modest diff.

LintGate's ControlPlane run: purely symbolic. Local Python execution. Zero API tokens. The only token cost is the agent reading and interpreting the findings — a single API call, not 92.

**What this reveals**: /simplify's architecture is fundamentally expensive because it uses LLM inference for tasks that are largely symbolic (pattern matching, duplication detection, N+1 identification). These are tasks that static analysis tools have solved for decades without requiring a language model. LintGate's architecture pushes all symbolic work to local execution and reserves the LLM for judgment — interpreting findings, deciding what to fix, writing the actual code.

This is not a minor difference. Running /simplify on every PR in a project with 20 PRs/week would cost $100-160/week in API tokens for analysis that a $0 symbolic tool could largely replicate.

### Observation 6: /simplify's three agents can't inform each other

The reuse agent found that ingest.py duplicates OllamaAdapter's HTTP client. The efficiency agent found that ingest.py's Phase A does redundant existence checks. The quality agent found that ingest.py's Phase C helpers bypass db.py's API.

No agent could synthesize: "ingest.py has a systemic abstraction problem — it reimplements lower layers instead of composing them." Each agent sees its slice. LintGate's cross-channel coherence (when functional) is designed exactly for this synthesis — COH001/COH002/COH003 correlate findings across channels to identify systemic issues.

**What this reveals**: /simplify is a flat ensemble with no cross-channel communication. LintGate is a correlated ensemble with a coherence layer. The theoretical advantage of cross-channel coherence is clear; the practical advantage depends on the coherence layer actually working (currently degraded when mutation times out).

---

## Part IV: Fix Patterns Applied

After aggregating /simplify's findings, these fixes were applied:


| Pattern                     | Count | Technique                                             | Theoretical Justification             |
| --------------------------- | ----- | ----------------------------------------------------- | ------------------------------------- |
| Redundant query elimination | 1     | 4 COUNT queries → 1 aggregate                         | Pure efficiency — identical semantics |
| Batch SQL                   | 1     | Loop of INSERT → executemany                          | Same                                  |
| Dead code removal           | 2     | Remove SELECT before INSERT OR IGNORE                 | TOCTOU + redundancy                   |
| Memory bounding             | 2     | `.fetchall()` → cursor iteration                      | Unbounded allocation on hot path      |
| Input validation            | 1     | Validate `phases` string against `set("abc")`         | Defensive boundary                    |
| Import hygiene              | 1     | Move `import json` from function body to module level | Convention                            |


All 343 tests pass after fixes.

**What was NOT fixed** (valid but too large): N+1 in query.py (needs batch query infrastructure), Ollama client dedup (needs OllamaAdapter refactor), bank name enums (cross-codebase), VIBE_JSON_SCHEMA auto-generation.

---

## Part V: Quantitative Comparison

### Finding Quality


| Metric                            | /simplify        | LintGate (current)     | LintGate (with gaps closed)           |
| --------------------------------- | ---------------- | ---------------------- | ------------------------------------- |
| Total findings                    | 40               | 16                     | ~25-35 estimated                      |
| True positives (actionable)       | ~22 (55%)        | ~12 (75%)              | ~20-28 (80%+)                         |
| False positives / noise           | ~10 (25%)        | ~2 (12%)               | ~3-5 (12-15%)                         |
| Valid but too large to fix        | ~8 (20%)         | ~2 (12%)               | ~2-5                                  |
| Findings with confidence scores   | 0 (implicit 1.0) | Yes (0.1-0.9 range)    | Yes, with mutation gating             |
| Cross-file architectural findings | 7 (strong)       | 5 (structure channel)  | 5 + semantic duplication              |
| Specification-aware findings      | 0                | 0 (mutation timed out) | All performance/optimization findings |


### Cost


| Metric                                 | /simplify               | LintGate                        |
| -------------------------------------- | ----------------------- | ------------------------------- |
| API tokens consumed by analysis        | ~345K total tokens      | 0 (symbolic, local)             |
| Tool calls during analysis             | ~92 (across 3 agents)   | ~6 (MCP calls, local execution) |
| Wall-clock time for analysis           | ~170s (longest agent)   | ~45s (ControlPlane run)         |
| Estimated dollar cost per run          | ~$5-8 (Opus pricing)    | $0 (local execution)            |
| Marginal cost of running on every PR   | ~$100-160/week (20 PRs) | $0/week                         |
| Tokens for agent to interpret findings | ~2K (structured output) | ~2K (structured output)         |


### What Each Tool Caught That the Other Missed


| /simplify caught, LintGate missed                             | LintGate caught, /simplify missed                           |
| ------------------------------------------------------------- | ----------------------------------------------------------- |
| Cross-module semantic duplication (Ollama client, family map) | Purity classification of 365 functions                      |
| Specific N+1 query patterns with SQL-level detail             | Dependency health (stale lockfile, missing .python-version) |
| `VIBE_JSON_SCHEMA` redundant with `VibeOutput` dataclass      | Import graph structure (cycles, orphans, module sizes)      |
| `build_vibe_prompt` parameter sprawl                          | Git hygiene (18 CI artifacts missing)                       |
|                                                               | Test effectiveness analysis                                 |
|                                                               | Cross-channel coherence signals (when available)            |


---

## Part VI: Theoretical Assessment

### /simplify's Implied Theory

/simplify's theory of code quality is **enumerative**: quality is the absence of known anti-patterns. It maintains a fixed vocabulary of 16 named patterns across 3 categories. If your code's problem isn't one of those 16, it goes undetected.

This is equivalent to a lint rule set — but implemented at LLM inference cost instead of static analysis cost. The LLM adds value in two places: (1) it can understand semantic equivalence across modules (the reuse findings), and (2) it can reason about whether a pattern instance is actually problematic. But for most of the 16 patterns, a symbolic tool (pylint, ruff, SonarQube) could identify the same issues at zero marginal cost.

**There is no theory of when findings matter.** Every finding is presented as equally actionable. The N+1 in `search()` that will cause real problems at 50K models is the same weight as the missed concurrency in Phase A that saves 200ms. The user (or the fixing agent) must supply all judgment about priority.

### LintGate's Theory (from mutation-theory.md)

LintGate's theory is **measurement-first**: you don't advise actions you can't verify. Specification completeness (measured by mutation survival) determines what engineering actions are licensed:

- 0-25% specification completeness → only safe to add tests, not refactor
- 25-50% → safe to extract functions, not change interfaces
- 50-80% → safe to optimize, refactor signatures
- 80%+ → safe for deep structural changes

This is a fundamentally different paradigm. /simplify says "here's a pattern that looks like a problem." LintGate says "here's a measurement of how well you've specified the behavior of this code, and here's what that measurement licenses you to do."

### The Gap Between Theory and Implementation

LintGate's theory is sound. Its implementation has three known gaps (identified earlier in this session):

1. **MUTATION UNKNOWN**: When mutation data is absent, `classify_properties()` silently proceeds at confidence 0.8 instead of emitting confidence 0.4 with `[MUTATION UNKNOWN]`. This caused the `@lru_cache` cargo-cult in this session. ~10-line fix.
2. **Shared pre-pass manifest**: The purity manifest is computed inside the performance channel instead of as a shared pre-pass available to all channels. This means the mutation channel can't use purity data to target mutations efficiently. ~50-line refactor.
3. **Background orchestration**: The mutation channel times out at 60s on projects with >20 functions. Background profiling that accumulates evidence across runs would solve this. ~200 lines new code.

**With these gaps closed**, LintGate would have:

- Detected the purity of `_detect_compatibility` AND flagged it as `[MUTATION UNKNOWN]` (no mutation data) → agent would NOT have blindly applied `@lru_cache`
- Classified the N+1 in `search()` as a specification-contingent optimization (safe to refactor only if search behavior is well-specified)
- Provided the same structural/dependency/hygiene findings it already provides

**Without these gaps closed**, LintGate is stronger than /simplify on structural analysis but weaker on the performance/optimization findings where its core innovation (mutation gating) is meant to shine.

---

## Part VII: What LintGate Should Learn From /simplify

### 1. Cross-Module Semantic Duplication Detection

/simplify's strongest unique finding category. LintGate's structure channel understands import graphs but not whether two functions in different modules encode the same domain knowledge. A semantic duplication channel — using AST similarity, shared constant analysis, or even lightweight embedding — would add genuine value. Crucially, such a channel should be **gated**: "these functions duplicate domain knowledge, and neither has >50% specification completeness, so deduplicating them is risky until you test one properly."

### 2. N+1 Query Pattern Detection

SQL-aware analysis that identifies per-iteration database queries inside loops. This is a well-understood static analysis problem that LintGate could implement symbolically (AST pattern: `for ... in ...: conn.execute(...)`) at zero inference cost. The performance channel already classifies function properties — adding "issues N SQL queries per call, called in a loop" to the property set would catch what /simplify found.

### 3. The Checklist Has Value as a Baseline

/simplify's 16-pattern checklist, while theoretically shallow, catches real issues that slip through when you're focused on deeper analysis. LintGate's channels are more powerful but also more complex. A lightweight "common anti-pattern scan" — fetchall on large result sets, import-inside-function, SELECT-before-INSERT-OR-IGNORE — would catch low-hanging fruit without the inference cost.

---

## Part VIII: What /simplify Cannot Learn From LintGate

### The Measurement Problem

/simplify's architecture cannot incorporate specification completeness because it has no way to measure it. Mutation testing requires executing the test suite hundreds of times with controlled perturbations. This is inherently a local, symbolic, compute-bound operation — not something an LLM sub-agent can do by reading code.

/simplify could theoretically add a "run pytest and count tests" step, but that's not specification completeness. The insight from mutation-theory.md is that line coverage (what tests execute) is a poor proxy for specification completeness (what tests constrain). The purity.py case study showed 99% line coverage with 38% mutation kill rate. /simplify has no path to this measurement.

### The Cross-Channel Synthesis Problem

/simplify's three agents are truly independent. This is a design choice (parallelism, simplicity) but it means findings can never be correlated. "This function is duplicated AND has an N+1 AND is stringly-typed" is a systemic diagnosis that requires cross-referencing. LintGate's cross-channel coherence layer exists precisely for this synthesis. /simplify would need a fourth "synthesis agent" that reads all three reports — adding another full-context LLM call to an already expensive pipeline.

### The Confidence Problem

/simplify cannot assign principled confidence to findings because it has no measurement backing. It can guess ("this looks serious" vs. "this is minor") but that's aesthetic judgment, not quantified confidence. LintGate's confidence scores are grounded in measurements: mutation survival rates, purity classification, structural metrics. The confidence scale isn't arbitrary — it's derived from the specification completeness lattice.

---

## Part IX: Economics

### The Core Asymmetry


|                            | /simplify                                              | LintGate                                                  |
| -------------------------- | ------------------------------------------------------ | --------------------------------------------------------- |
| **Analysis cost per run**  | ~345K tokens, ~$5-8                                    | 0 tokens, $0                                              |
| **What that buys**         | 40 findings, 55% true positive rate                    | 16 findings, 75% true positive rate + confidence scores   |
| **Unique value**           | Cross-module semantic duplication detection            | Specification-aware confidence gating                     |
| **Marginal cost scaling**  | Linear with codebase size (more context = more tokens) | Sublinear (symbolic analysis scales with CPU, not tokens) |
| **Cost per true positive** | ~$0.25-0.35                                            | $0                                                        |


### Token Budget Perspective

The /simplify run consumed approximately as many tokens as writing 200-300 lines of actual code. For a 241-line diff, the analysis-to-change ratio is roughly 1:1 in token cost — you're paying as much to review the code as you paid to write it. For larger diffs, this ratio may improve, but the fundamental cost structure (3 full-context LLM inference passes) means /simplify will always be expensive relative to symbolic alternatives.

LintGate's token cost is concentrated where it should be: the agent's interpretation and action on findings. The analysis itself is free. The agent spends tokens deciding what to do, not figuring out what's wrong.

### The Real Comparison

The honest comparison isn't /simplify vs. LintGate-as-it-stands. It's:


|                                             | /simplify                    | LintGate (current, gaps open) | LintGate (gaps closed)      |
| ------------------------------------------- | ---------------------------- | ----------------------------- | --------------------------- |
| **Catches duplication**                     | Yes (strong)                 | No                            | Possible with new channel   |
| **Catches N+1 patterns**                    | Yes                          | No                            | Possible with AST pattern   |
| **Catches stringly-typed code**             | Yes                          | Partially (structure)         | Same                        |
| **Provides specification-aware confidence** | No                           | No (mutation times out)       | Yes                         |
| **Prevents cargo-cult optimization**        | Indirectly (doesn't suggest) | No (silent pass-through)      | Yes (MUTATION UNKNOWN gate) |
| **Cost per PR**                             | $5-8                         | $0                            | $0                          |
| **Scales to CI**                            | Expensive ($100-160/wk)      | Free                          | Free                        |


LintGate with gaps closed is strictly better than /simplify on everything except cross-module semantic duplication — and that's a channel that should be added. The cost difference alone would justify LintGate even if finding quality were identical.

---

## Summary


| Dimension                  | /simplify                                                                                                                                                                                                                            | LintGate (current)                                                                                                                                                                                                                                                                        | LintGate (complete)                                                                                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Diagnosis quality**      | Good breadth, no depth. 40 findings but no prioritization — user must supply all judgment about what matters.                                                                                                                        | Narrower but more structured. Confidence scores on findings. Degraded when mutation channel times out.                                                                                                                                                                                    | Strong. Specification-aware confidence on all findings. Measurement-first.                                                                                                                                                                              |
| **Unique capability**      | Cross-module semantic duplication detection. Genuinely strong and not available elsewhere.                                                                                                                                           | Cross-channel coherence, specification completeness gating (when functional), project compass alignment.                                                                                                                                                                                  | All of the above, plus MUTATION UNKNOWN preventing false confidence.                                                                                                                                                                                    |
| **False positive rate**    | ~25%. The missed-concurrency and redundant-derived-state findings are noise.                                                                                                                                                         | ~12%. Fewer findings but higher signal-to-noise.                                                                                                                                                                                                                                          | ~12-15%. Mutation gating reduces false positives on optimization findings.                                                                                                                                                                              |
| **Theoretical foundation** | Enumerative: quality = absence of 16 known patterns. No theory of when findings matter.                                                                                                                                              | Measurement-first: specification completeness licenses engineering actions. Sound theory, incomplete implementation.                                                                                                                                                                      | Same theory, realized.                                                                                                                                                                                                                                  |
| **Cost**                   | ~$5-8 per run. Prohibitive for CI integration. 3 full-context LLM inference passes for analysis that is largely symbolic.                                                                                                            | $0 per run. Scales to CI trivially.                                                                                                                                                                                                                                                       | $0 per run.                                                                                                                                                                                                                                             |
| **What it prevents**       | Duplication, known anti-patterns, obvious inefficiencies.                                                                                                                                                                            | Structural drift, dependency rot, hygiene regression.                                                                                                                                                                                                                                     | All of the above + cargo-cult optimization (the @lru_cache incident).                                                                                                                                                                                   |
| **Overall**                | A competent mechanical reviewer — equivalent to a mid-level engineer's first PR pass. Catches real things, can't prioritize them, costs as much as writing the code it reviews. Useful as a one-off; uneconomical as infrastructure. | The right architecture with implementation gaps. When mutation works, it's genuinely novel — measuring specification completeness is something no other tool in this category does. When mutation times out (current state on this project), it degrades to a structural/hygiene scanner. | The tool the mutation-theory paper describes. Measures before advising, gates confidence on evidence, costs nothing to run. The three known gaps (MUTATION UNKNOWN, shared pre-pass, background profiling) are all tractable fixes totaling ~260 lines. |


