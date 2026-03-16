---
theory_scope: true
---

# LintGate Theory System Retrospective: Wayfinder — Initial Setup & Compass Calibration

## Metadata

| Field | Value |
|-------|-------|
| **Project** | Wayfinder — Navigational theorem prover for Lean 4 / Mathlib |
| **Agent** | Claude Opus 4.6, solo, extended design-review-to-implementation session |
| **Date** | 2026-03-06 |
| **Scope** | Theory system setup: extract_project_theory, compass_update, compass_interview, compass_update (render). No lint/controlplane/code-change work in this segment. |
| **Tools Used** | `getting_started`, `extract_project_theory`, `compass_status`, `compass_update` (x2), `compass_interview` (x2) |
| **Session Context** | Multi-hour design review session. Extensive doc writing/editing preceded theory setup. ~4 design docs totaling ~2,500 lines of markdown. Theory system was engaged AFTER docs were mature — not during initial writing. |
| **Prior State** | Docs: mature, heavily iterated (3 major rewrites). Code: existing Balanced Sashimi codebase (~3,600 lines), no Wayfinder-specific modules yet. |

---

## Part I: What the Theory System Found

### extract_project_theory

Scanned 4 docs (`WAYFINDER_RESEARCH.md`, `WAYFINDER_DESIGN.md`, `WAYFINDER_PLAN.md`, `HARDENING_PLAN.md`), identified 134 claims across 6 facets.

| Facet | Claims | Assessment |
|-------|--------|------------|
| core_theory | 25 (12 sources) | Strong. Correctly identified the navigational paradigm, Mutation Theory connections, and formal learning theory alignment as the theoretical spine. |
| problem_solving | 46 (18 sources) | Largest bucket, but heterogeneous. Contains genuine problem-solving heuristics (scoring alternatives, stop/go gates) alongside architectural decisions that happen to be phrased as solutions. |
| alignment | 28 (16 sources) | Accurate. Pulled the falsifiability conditions ("if dense retrieval consistently outperforms..."), the design invariants, and the hammer delegation criteria. These ARE alignment constraints. |
| architecture | 14 (6 sources) | Surprisingly thin given the project's architectural density. The extraction seems to have classified many architectural claims as problem_solving instead. |
| anti_patterns | 4 (1 source) | Only found encoder-related anti-patterns (subword tokenization of Unicode, frozen encoder caps). Missed the explicit anti-patterns scattered across docs: binary critic targets, pure multiplicative scoring as default, MiniLM as encoder. |
| abstractions | 17 (5 sources) | Reasonable. Anchors, bank positions, proof network schema, benchmark structure. |

**No enforceable rules proposed.** The extraction found zero `LINTGATE_FORBID_REGEX` or `LINTGATE_REQUIRE_REGEX` directives, which is expected — these docs weren't written with LintGate rule syntax in mind.

### Compass Initial State

| Axis | Depth | Interpretation |
|------|-------|----------------|
| problem | 3 | Deep — docs thoroughly define the problem space (proof search inefficiency, dense retrieval limitations, many-to-one tactic mapping) |
| solution | 1 | Sparse — docs are heavy on WHAT the solution is, but thin on WHY this over alternatives and WHAT was traded off |
| implementation | 3 | Deep — the plan doc and design doc together give rich implementation detail |
| world | 1 | Sparse — runtime constraints, hardware, dependencies were under-documented at doc level (they were in the hardening plan but apparently not at sufficient depth for the compass extractor) |

Spikiness: 0.33. Interview recommended.

---

## Part II: Observations

### Observation 1: The Compass Correctly Diagnosed Documentation Blind Spots

The solution axis at depth 1 was genuinely accurate. The three design docs spend thousands of words on *what* the navigational paradigm is and *how* it works, but the explicit "why this over ReProver-style dense retrieval" and "what we traded off" arguments were implicit rather than stated. I knew the answers (from the full design review conversation), but they weren't in the docs.

**What this reveals:** The 4-axis model (problem/solution/implementation/world) is a useful diagnostic for research documentation, not just code projects. A research paper with deep problem and implementation axes but a thin solution axis will fail peer review — reviewers need the "why this approach" argument explicitly. The compass caught this.

### Observation 2: Interview Questions Were Well-Targeted but Generic

The 6 interview questions were:
- solution: "Why this approach over alternatives?" / "What tradeoffs were made?" / "What prior work inspired this?"
- world: "Language/runtime constraints?" / "Infrastructure assumptions?" / "Important dependency constraints?"

These are good questions, but they're stock templates — they'd be identical for any project with sparse solution/world axes. For a research project like Wayfinder, more domain-specific follow-ups would have been valuable:
- "What is the falsification criterion for the core thesis?" (already documented but not extracted as solution-axis)
- "What happens if the proof network can't achieve 70% recall?" (contingency planning)
- "Which external research findings most constrained the design?" (already documented in External Research doc)

**What this reveals:** The interview is a good bootstrapping mechanism, but a second-pass interview that responds to the *content* of the answers (not just the axis depth) would add more value. The current interview is axis-aware but not content-aware.

### Observation 3: Theory Extraction's Facet Classification Is Coarse for Research Projects

The 6-facet model (core_theory, problem_solving, alignment, architecture, anti_patterns, abstractions) maps well to software engineering, where "core theory" is design principles and "architecture" is module structure. But for a research project, the boundaries blur:

- "Multiplicative scoring is both the system's precision advantage and its fragility" — is this core_theory, problem_solving, architecture, or anti_patterns? The extraction put it in architecture. I'd argue it's all four.
- "HTPS found hard binary targets worse than no critic" — this is an external research finding that constrains the design. It was classified as problem_solving and alignment. For a research project, there's a missing facet: **empirical constraints** (things the literature proved that we must respect).
- The Mutation Theory connections are core_theory, but they're also alignment (formal convergence guarantees constrain what the architecture can do) and abstractions (teaching dimension → premise_candidates lower bound).

**What this reveals:** Research projects need either (a) a richer facet taxonomy that includes empirical-constraints and theoretical-grounding as distinct from core_theory, or (b) multi-facet tagging where a claim can belong to multiple facets simultaneously. The current single-facet assignment loses information for theoretically dense projects.

### Observation 4: compass_update After Interview Reverted to Pre-Interview State

After successfully filling solution (depth 1→2) and world (depth 1→2) via `compass_interview`, I ran `compass_update(targets=["all"], write=true)` to render context files. The rendered compass showed solution and world back at depth 1 — the interview answers were overwritten by re-extraction from docs.

This is either:
- **By design**: compass_update always re-extracts from files, and interview answers are ephemeral unless they're written into the actual docs. In this case, the correct workflow is: interview → write answers into docs → compass_update.
- **A bug**: interview answers should persist in compass.yaml and be merged with re-extracted claims during compass_update.

I believe this is by design (the compass is derived from documented state, not session state), but it creates a UX trap: the interview feels like it's enriching the compass permanently, when it's actually enriching it until the next compass_update. The interview's value is diagnostic (it tells you what to write in your docs), not persisted.

**What this reveals:** The interview → compass_update lifecycle needs clearer communication about persistence semantics. If interviews are ephemeral, the tool should say "these answers will be used until the next compass_update — consider adding them to your documentation." If they should persist, they need a merge strategy.

### Observation 5: Anti-Pattern Extraction Was Shallow

Only 4 anti-patterns found, all from one source (encoder section of RESEARCH.md). But the docs contain many more explicit anti-patterns:

- "NOT binary BCE" (critic targets) — stated repeatedly, in bold, across 3 docs
- "Never default to MiniLM" (encoder) — stated as invariant #7
- "Pure multiplicative is an ablation" (scoring) — stated in config section
- "Do not default to 3-bank" (navigation) — stated as invariant #8
- "Eval data is frozen, never modify" — stated as invariant #1

These are phrased as constraints/invariants rather than anti-patterns, so the extraction missed them. The invariants section of PLAN.md (11 invariants) is essentially an anti-pattern list dressed as positive constraints.

**What this reveals:** Anti-pattern extraction would benefit from also scanning for constraint/invariant sections and inverting them. "Eval data is frozen" → anti-pattern: "modifying eval data." "Critic uses soft targets" → anti-pattern: "binary critic targets." The transformation is mechanical but the current extractor doesn't perform it.

### Observation 6: Theory Extraction Didn't See the External Research Doc

`Wayfinder_External_Research.md` (a survey of 8+ proof search systems with detailed findings) was not in the 4 scanned docs. This doc contains the empirical evidence that constrains most of the design decisions — ByT5 as the Lean standard, soft critic targets from HTPS, proof history from LeanProgress, hammer complementarity from LeanHammer.

I don't know if this was a scanning scope issue (maybe it only picks up docs matching certain patterns?) or if the file was excluded for another reason. But for a research project, the literature survey is arguably the most important theory document — it's where the "why" lives.

**What this reveals:** The doc scanner's file selection heuristic may need tuning for research projects where the theory lives in survey/literature documents, not just design specs.

### Observation 7: The Compass Model Maps to Research Paper Structure

The 4-axis compass maps surprisingly well to the sections of a research paper:

| Compass Axis | Paper Section |
|-------------|---------------|
| problem | Introduction + Related Work |
| solution | Methods + Design Decisions |
| implementation | Experiments + Implementation Details |
| world | Evaluation Setup + Reproducibility |

A balanced compass (all axes at depth 2-3) predicts a paper that has all sections developed. An imbalanced compass predicts a paper with gaps. In this case, the sparse solution axis correctly predicted that the "why this approach" argument needed strengthening — exactly the kind of thing a reviewer would flag.

**What this reveals:** The compass could be explicitly marketed as a research-readiness diagnostic. "Your problem and implementation are at depth 3, but your solution justification is at depth 1 — reviewers will ask why you chose navigation over dense retrieval."

---

## Part III: Process Assessment

### The Theory Workflow

```
getting_started(auto_setup=true)
  → extract_project_theory(path)           # 134 claims, 6 facets
  → compass_status(path)                    # no compass
  → compass_update(path, write=true)        # problem=3, solution=1, impl=3, world=1
  → compass_interview(path)                 # 6 questions for solution + world
  → compass_interview(path, answers={...})  # filled, spikiness 0.33→0.17
  → compass_update(path, targets=["all"], write=true)  # rendered context files
```

Total: 7 tool calls. ~3 minutes of wall-clock time (most spent crafting interview answers). The workflow felt natural — status → extract → diagnose gaps → fill gaps → render.

### What Works Well

1. **The 4-axis gap detection is genuinely diagnostic.** Solution=1 and world=1 were real blind spots in the documentation. I wouldn't have noticed without the compass pointing at them.

2. **Interview questions bootstrapped quickly.** Even though they were generic, they provided a scaffold for organizing knowledge that was in my context but not in the docs. The answers were useful independent of whether they persisted in the compass.

3. **extract_project_theory's source tracing is excellent.** Every claim links to `file:line`. For a 2,500-line doc corpus, this is the difference between "the project values auditability" (useless) and "the project values auditability (docs/WAYFINDER_RESEARCH.md:190, scoring mechanism section)" (actionable).

4. **The compass spikiness metric is a clean summary statistic.** 0.33 → 0.17 is immediately interpretable. No need to inspect individual axes to know the compass improved.

5. **Theory extraction ran in seconds.** For a research project with dense docs, this is important — the cost of checking alignment is near-zero, which means it'll actually get used.

### What Could Be Better

1. **Interview persistence semantics need clarity.** The interview → compass_update reversion (Observation 4) is the biggest UX issue. If answers are ephemeral, say so upfront. If they should persist, implement merge.

2. **Anti-pattern extraction should invert constraints/invariants.** The 11 explicit invariants in PLAN.md are a goldmine of anti-patterns that the extractor missed entirely (Observation 5). A heuristic: any sentence containing "never," "must not," "NOT," "is frozen," or "only as ablation" is a candidate anti-pattern.

3. **Research projects need an empirical-constraints facet.** "HTPS proved binary critics are worse than no critic" is a different kind of claim than "our architecture uses soft targets." The first is an external empirical fact; the second is a design decision. Conflating them in problem_solving loses the distinction between "what we chose" and "what the literature forced us to choose" (Observation 3).

4. **Doc scanner should be more inclusive.** Missing the External Research survey (Observation 6) meant the compass was calibrated without the project's primary empirical evidence base. For research projects, literature surveys and annotated bibliographies should be in scope.

5. **Interview should have a content-aware second pass.** The first-pass generic questions are fine for bootstrapping. But after answers are provided, follow-up questions that respond to the content ("You mentioned Mutation Theory provides convergence guarantees — how tight are these bounds?") would push the compass deeper (Observation 2).

---

## Part IV: The Agent's Experience

### How the Theory System Changed My Approach

I had spent several hours doing deep design review and doc editing before engaging the theory system. By that point, I had a comprehensive mental model of the project. The theory system didn't teach me anything new about Wayfinder — but it taught me something about the *documentation*: that the docs were stronger on specification than on justification.

This is a subtle and useful distinction. I knew why navigation beats dense retrieval. The docs described what navigation is. But they didn't argue for it explicitly enough for a reader (or a compass) to extract the reasoning. The theory system acted as a proxy reader: "I can see what you're building, but I can't see why you chose it." That's exactly what a reviewer would say.

### Where I Was Surprised

The solution axis at depth 1 surprised me. I'd written thousands of words of design rationale — how could the solution justification be thin? The answer: the rationale was distributed across the docs as embedded asides ("this is why multiplicative scoring matters," "this is why we chose 6 banks") rather than consolidated as a dedicated "design decisions and alternatives" section. The compass measures extractable, structured justification — not total words spent on justification. This is a better metric for communication quality.

### Trust Calibration

| Signal | Trust Level | Basis |
|--------|------------|-------|
| Compass axis depths | High | Accurately diagnosed real doc gaps |
| Spikiness metric | High | Clean, interpretable, moved in the right direction |
| Facet classification | Medium | Reasonable for software, coarse for research |
| Anti-pattern extraction | Low | Missed most explicit constraints/invariants |
| Interview questions | Medium | Good scaffolding, generic content |
| Interview persistence | Low | Reversion on compass_update was confusing |

---

## Part V: Broader Observations

### The Theory System as Research Readiness Diagnostic

The most generalizable insight from this session: the compass's 4-axis model is a proxy for research paper completeness. A research project with deep problem and implementation axes but sparse solution and world axes has written a technical report, not a paper. The solution axis corresponds to the "design decisions and alternatives considered" section that distinguishes a contribution from a description. The world axis corresponds to the "evaluation setup and reproducibility" section that distinguishes a claim from an anecdote.

For the LintGate roadmap, this suggests a lightweight "paper readiness" mode that maps compass axes to paper sections and generates targeted prompts: "Your solution axis is at depth 1. Consider adding a section that explicitly compares your approach to 2-3 alternatives and states what you traded off."

### Theory Extraction at Documentation Maturity

The theory system was engaged after extensive doc iteration — the docs had been written, critiqued, and rewritten 3 times. This is an unusual use case; most sessions probably engage LintGate on existing code, not freshly written docs. The result: very high claim count (134) but with the classification issues noted above. The extractor is tuned for discovering principles in existing codebases, not for validating the completeness of deliberately authored research documentation. Both are valuable use cases, but they may need different extraction heuristics.

### The Compass as Communication Architecture Validator

Given the project's explicit investment in communication architecture (personal semiotics framework, Winston's Star mapping, assertion-evidence figure conventions), there's an interesting gap: the compass doesn't have an axis for "how findings will be communicated." A fifth axis — **communication** or **narrative** — would capture whether the project has thought through its presentation strategy. For Wayfinder, this axis would be at depth 3 (deeply developed). For most projects, it would be at depth 0 (not considered at all). The delta would itself be informative.

---

## Summary

| Dimension | Assessment |
|-----------|-----------|
| **Gap detection** | Excellent. Compass correctly identified solution and world as sparse axes — real documentation blind spots I wouldn't have noticed. |
| **Theory extraction** | Good. 134 claims with full source tracing. Facet classification is reasonable for software but loses nuance on research projects where empirical constraints, design decisions, and architectural claims overlap. |
| **Interview quality** | Adequate. Generic but functional scaffolding. Content-aware follow-ups would add significant value. |
| **Persistence model** | Needs work. Interview answers being overwritten by compass_update is the biggest UX issue. Semantics should be explicit regardless of whether the behavior is correct by design. |
| **Anti-pattern detection** | Weak for this project. Missed 11 explicit invariants that ARE anti-patterns (inverted). Constraint/invariant inversion would be high-leverage. |
| **Doc scanner scope** | Incomplete. Missed the External Research survey, which is the empirical evidence base for most design decisions. |
| **Research applicability** | Promising. The 4-axis model maps to paper sections. The compass could be marketed as a research readiness diagnostic with minimal adaptation. |
| **Overall** | The theory system is most valuable as a documentation quality diagnostic — a proxy reader that tells you what's extractable vs. what's implicit. For a research project, it correctly identified that specification outpaced justification, which is the single most common peer-review failure mode. The tool infrastructure is solid; the extraction heuristics need tuning for research-doc patterns (constraint inversion, empirical-constraints facet, inclusive doc scanning). |
