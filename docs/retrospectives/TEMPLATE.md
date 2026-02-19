# LintGate Agent Retrospective: [Project Name] — [Session Type]

<!--
RETROSPECTIVE TEMPLATE v1

== FILE NAMING CONVENTION ==

  {project}-{YYYY-MM-DD}-{tier}{N}-{session-type}.md

Components:
  project        — short project name, lowercase, hyphens (e.g. mneme, iphone-recovery, shortcutforge)
  YYYY-MM-DD     — date of the session
  tierN          — LintGate tier used (tier1, tier2, tier3)
  session-type   — what kind of work (refinement, build, audit, post-impl, hybrid)

Examples:
  mneme-2026-02-19-tier3-refinement.md
  iphone-recovery-2026-02-18-tier2-post-impl.md
  shortcutforge-2026-03-05-tier1-build.md

If the same project has multiple sessions on the same day at the same tier, append a
sequence letter: mneme-2026-02-19-tier3-refinement-b.md

This convention ensures:
  - Files sort chronologically within a project (ls lists them in order)
  - Files sort by project across the directory (all mneme-* group together)
  - The tier and session type are visible without opening the file
  - Multiple runs against the same project are clearly distinct

== INSTRUCTIONS FOR AGENTS ==

- Copy this template and replace all [BRACKETED] placeholders with project-specific content.
- Preserve the section structure (Parts I-VIII + Summary). Skip sections that don't apply,
  but note why they were skipped.
- Each "Observation N" should follow the pattern: describe what happened, then extract what it
  reveals about LintGate or about the agent-tool interaction.
- The Economics section (Part VIII) should use real numbers where available and clearly mark
  estimates. Don't fabricate precision — round numbers with stated assumptions are better than
  exact-looking numbers pulled from thin air.
- The Summary table at the end should be an honest scorecard. Not everything will be "Excellent."
-->

## Metadata

| Field | Value |
|-------|-------|
| **Project** | [Project name — one-line description of what it is] |
| **Agent** | [Model name and version, role (lead/sub-agent/solo), team structure if applicable] |
| **Date** | [YYYY-MM-DD] |
| **Scope** | [What was linted — file count, LOC, language(s)] |
| **LintGate Tier** | [Tier 1/2/3, strict/normal, ControlPlane yes/no] |
| **Session Type** | [Build / Refactoring / Audit / Post-implementation / Hybrid — brief description] |
| **Prior State** | [What state was the codebase in before this session? Working? Broken? New?] |

---

## Part I: Initial Diagnosis

<!-- What did LintGate find on the first run? Include ControlPlane coherence if used. -->

**ControlPlane coherence state: "[state]"** — *"[quoted diagnosis text]"*

[1-2 paragraphs on what the diagnosis meant and whether it was useful as a framing device.]

| Severity | Count | Breakdown |
|----------|-------|-----------|
| Blockers | [N] | [categorized list] |
| Warnings | [N] | [categorized list] |
| Informational | [N] | [brief note] |

### Theory Profile

[What did extract_project_theory find? How many claims across which facets? What was missing?]

---

## Part II: Observations During Refactoring

<!--
Number observations sequentially. Each should follow this structure:
1. What happened (concrete, with code examples where relevant)
2. What it reveals about LintGate or about the agent-tool interaction
3. (Optional) A "Key insight" callout for the most important observations

Aim for 5-10 observations. Fewer is fine if the session was short; more is fine if the
session was rich. Quality over quantity.
-->

### Observation 1: [Title — short, descriptive]

[Description of what happened, with code examples if relevant.]

**What this reveals:** [What does this tell us about LintGate's capabilities, limitations, or the agent's interaction with it?]

### Observation 2: [Title]

[...]

<!-- Continue numbering sequentially. -->

---

## Part III: Fix Patterns and Techniques

<!--
A reusable catalog of the fix patterns applied. This section has value beyond the
individual retrospective — other agents can learn from these patterns.
-->

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| [Pattern name] | [N] | [How it was fixed] | [When this pattern applies] |
| [...] | [...] | [...] | [...] |

---

## Part IV: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | [N] | [N] | [change] |
| Warnings | [N] | [N] | [change] |
| Informational | [N] | [N] | [change] |
| ControlPlane coherence | [state] | [state] | [Improved/Same/Degraded] |
| [Project-specific metrics] | [...] | [...] | [...] |

### Integration Verification

<!--
Did everything still work after refactoring? List what was verified and how.
This is critical for refactoring sessions — changes that break functionality aren't improvements.
-->

| System | Status | Evidence |
|--------|--------|----------|
| [System/feature name] | Pass/Fail | [What was checked] |
| [...] | [...] | [...] |

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| [Phase description] | [N min] | [Brief note] |
| [...] | [...] | [...] |
| **Total** | **[N min]** | |

---

## Part V: Process Assessment

### The LintGate Workflow

<!--
Describe the actual workflow that emerged. What sequence of tool calls did you use?
Was it the sequence you planned, or did it emerge organically?
-->

```
[tool call sequence, e.g.:]
lint_project → lint_get_details → [fixes] → lint_project → lint_fix → controlplane_run
```

### What Works Well

<!--
3-5 specific things LintGate did well in this session. Be concrete — "good diagnostics"
is too vague; "ControlPlane's 'systemic' label was more actionable than the raw issue count"
is specific and useful.
-->

1. [...]
2. [...]
3. [...]

### What Could Be Better

<!--
3-5 specific improvement suggestions. These should be actionable and tied to specific
experiences in this session, not generic wishlists.
-->

1. [...]
2. [...]
3. [...]

---

## Part VI: The Agent's Experience

<!--
First-person introspective section. How did LintGate change the agent's behavior,
decision-making, or approach? This is the section that distinguishes a retrospective
from a test report.

Suggested subsections (use whichever apply):
- How the tool changed my approach
- Where I was surprised
- What I would do differently next time
- How it affected my relationship with sub-agents (if applicable)
-->

### [Subsection title]

[...]

---

## Part VII: Broader Observations

<!--
Patterns that generalize beyond this specific project. What does this session reveal
about LintGate as a tool category, about agent-tool interaction, or about the nature
of code quality work?
-->

### [Observation title]

[...]

---

## Part VIII: Economics

<!--
The rough math on the session. Use real numbers where available, clearly mark estimates.
This section makes retrospectives comparable across projects and sessions.
-->

### Session Scope

| Metric | Value |
|--------|-------|
| Total project LOC | [N lines across N files] |
| Files touched | [N (X% of codebase)] |
| Files created | [N] |
| Genuinely new/rewritten lines | [~N] |
| Lines moved/restructured | [~N] |
| Net LOC delta | [+/- N] |

### Time Allocation

| Activity | Time | % | Category |
|----------|------|---|----------|
| [Activity] | [N min] | [N%] | Diagnosis / Creation / Debugging / Verification |
| [...] | [...] | [...] | [...] |
| **Total** | **[N min]** | **100%** | |

**Creation:Debugging:Verification ratio — [N:N:N]**

[1-2 sentences interpreting the ratio. What does it say about the session's efficiency?]

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per hour | [N] |
| Fastest batch | [N blockers in N min — what pattern?] |
| Slowest individual fix | [N min — what was hard?] |
| Lines reviewed per minute | [~N] |

### Token Cost Estimate

<!--
Estimate if exact counts unavailable. State assumptions. Break down by component
so the LintGate-specific overhead is visible.
-->

| Component | Input Tokens | Output Tokens | Notes |
|-----------|-------------|---------------|-------|
| File reads | [~N] | — | [N files, N lines] |
| LintGate tool calls | [~N] | [~N] | [which tools, how many calls] |
| Edit tool calls | [~N] | [~N] | [N edits] |
| Reasoning overhead | [~N] | [~N] | |
| **Total** | **[~N]** | **[~N]** | |

<!--
Adjust pricing to the model used. Current reference rates:
- Opus 4: $15/M input, $75/M output
- Sonnet 4: $3/M input, $15/M output
- Haiku 4.5: $0.80/M input, $4/M output
-->

| Component | Cost |
|-----------|------|
| Input tokens | ~$[N] |
| Output tokens | ~$[N] |
| **Total session cost** | **~$[N]** |
| LintGate-specific overhead | ~$[N] ([N%] of total) |

### Cost Per Blocker

| Metric | Value |
|--------|-------|
| Total cost / blockers | ~$[N] per blocker |
| Total time / blockers | ~[N] min per blocker |

### Counterfactual: Without LintGate

<!--
What would the same task have looked like without the tool? Estimate time, completeness,
and what would have been missed. Be honest — some sessions might show minimal LintGate
benefit if the codebase was already clean.
-->

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | [description] | [description] | [time saved/lost] |
| [Dimension] | [...] | [...] | [...] |
| **Total estimated time** | [N min] | [N min] | **[N]x slower** |
| **Completeness** | [%] | [%] | [what was missed] |

### Return on Investment

| Metric | Value |
|--------|-------|
| LintGate overhead (time) | [N min] |
| LintGate overhead (tokens/cost) | [~N tokens / ~$N] |
| Time saved vs. manual approach | [~N min] |
| Issues that would have been missed | [list] |
| **Time ROI** | [~Nx return] |
| **Token ROI** | [~Nx return] |

---

## Summary

<!--
One-row-per-dimension scorecard. Be honest — rate each dimension based on the evidence
from this specific session, not on what LintGate could theoretically do.
-->

| Dimension | Assessment |
|-----------|-----------|
| **Diagnosis quality** | [1-2 sentence assessment] |
| **Fix guidance** | [1-2 sentence assessment] |
| **Workflow integration** | [1-2 sentence assessment] |
| **Regression detection** | [1-2 sentence assessment] |
| **Structural insight** | [1-2 sentence assessment] |
| **Theory/documentation** | [1-2 sentence assessment] |
| **Auto-fix** | [1-2 sentence assessment] |
| **Noise level** | [1-2 sentence assessment] |
| **Economics** | [1-2 sentence assessment — was the tool worth the overhead?] |
| **Overall** | [2-3 sentence summary] |
