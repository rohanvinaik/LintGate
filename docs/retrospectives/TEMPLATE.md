---
theory_scope: false
---

# LintGate Agent Retrospective: [Project Name] — [Session Type]

<!--
RETROSPECTIVE TEMPLATE v2

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
- Preserve the section structure (Parts I-IX + Summary). Skip sections that don't apply,
  but note why they were skipped.
- Each "Observation N" should follow the pattern: describe what happened, then extract what it
  reveals about LintGate or about the agent-tool interaction.
- The Economics section (Part IX) should use real numbers where available and clearly mark
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
| **LintGate Version** | [commit hash, version tag, or "unknown"] |
| **Session Type** | [Build / Refactoring / Audit / Post-implementation / Hybrid — brief description] |
| **Session Record(s)** | [Absolute path(s) to the JSONL session log(s) used. For multi-window sessions, list all in chronological order. If a single JSONL covers multiple retrospectives (e.g., a session that pivoted projects), note the approximate UTC timestamp where this retrospective's work begins.] |
| **Session Continuity** | [Fresh / Resumed from handoff / Multi-window continuation] |
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

### Hygiene Baseline

<!-- Captures the professional discipline posture at session start. -->

| Signal | Status |
|--------|--------|
| Virtual environment | [active / missing / system Python] |
| Lockfile | [fresh / stale / absent] |
| .python-version | [present / missing] |
| Structure snapshot | [cycles: N, orphans: N, largest module: X (N LOC)] |

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

## Part III: Professional Discipline Signals

<!--
How did the professional discipline signals (hygiene, secrets, supply-chain, type,
security, structure) influence the session? This section captures whether these
signals fired, whether they were actionable, and what happened as a result.
Skip if no discipline signals fired.
-->

| Signal | Fired? | Actionable? | Outcome |
|--------|--------|-------------|---------|
| Hygiene precheck (command class) | [Yes/No — which class?] | [Useful / False positive / Missed] | [What happened] |
| Secrets-in-diff | [Yes/No] | [...] | [...] |
| Supply-chain (pip-audit) | [Yes/No] | [...] | [...] |
| Type integrity (ty) | [Yes/No] | [...] | [...] |
| Security fast path (bandit) | [Yes/No] | [...] | [...] |
| Structure (cycles/size/orphans/cohesion) | [Yes/No — which codes?] | [...] | [...] |

---

## Part IV: Fix Patterns and Techniques

<!--
A reusable catalog of the fix patterns applied. This section has value beyond the
individual retrospective — other agents can learn from these patterns.
-->

| Pattern | Count | Technique | When to Use |
|---------|-------|-----------|-------------|
| [Pattern name] | [N] | [How it was fixed] | [When this pattern applies] |
| [...] | [...] | [...] | [...] |

---

## Part V: Quantitative Results

### Before and After

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Blockers | [N] | [N] | [change] |
| Warnings | [N] | [N] | [change] |
| Informational | [N] | [N] | [change] |
| ControlPlane coherence | [state] | [state] | [Improved/Same/Degraded] |
| [Project-specific metrics] | [...] | [...] | [...] |

### Independent Tool Metrics: Before/After Autonomous Professionalization

<!--
LintGate-independent measurements using standard Python quality tools. These provide
an external validation of the professionalization work — LintGate findings are the
agent's internal view; pylint/radon/ruff are the industry's external view.

METHODOLOGY:
1. Stash all working changes: `git stash push -m "metrics" --include-untracked`
2. Run all tools on the clean (before) state
3. Restore changes: `git stash pop`
4. Run all tools again on the professionalized (after) state
5. Compare

This ensures identical tool versions and configuration for both measurements.

SCOPE: Run tools on all Python source directories in the project. Adjust the
directory list to match your project layout. Example for a typical project:

    DIRS="src/ cli/ training/ research/src/ tools/ tests/"

For pylint, you may need an --init-hook to set sys.path if the project uses
non-standard import resolution:

    --init-hook="import sys; sys.path.insert(0, 'src'); ..."

If pylint hits F0010 (fatal parse error) on a directory like tests/ that lacks
__init__.py, pass test files as globs instead: tests/test_*.py

TOOLS AND COMMANDS:

    # Pylint score (0-10)
    pylint $DIRS --exit-zero [--init-hook="..."] | grep "rated at"

    # Radon Maintainability Index — per-file grades and average
    radon mi $DIRS -s
    # Parse output to count grades A/B/C and compute average MI

    # Radon Cyclomatic Complexity — per-block grades, average, and distribution
    radon cc $DIRS -s -j | python3 -c "
    import json, sys
    data = json.load(sys.stdin)
    all_blocks = []
    for fname, blocks in data.items():
        for b in blocks:
            all_blocks.append((b['complexity'], b['name'], fname))
    all_blocks.sort(reverse=True)
    total = len(all_blocks)
    grade_counts = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'E': 0, 'F': 0}
    for cc, name, fname in all_blocks:
        if cc <= 5: grade_counts['A'] += 1
        elif cc <= 10: grade_counts['B'] += 1
        elif cc <= 20: grade_counts['C'] += 1
        elif cc <= 30: grade_counts['D'] += 1
        elif cc <= 40: grade_counts['E'] += 1
        else: grade_counts['F'] += 1
    avg = sum(cc for cc, _, _ in all_blocks) / total if total else 0
    print(f'Total blocks: {total}')
    print(f'Average CC: {avg:.2f}')
    for g in 'ABCDEF':
        pct = grade_counts[g]/total*100 if total else 0
        print(f'  Grade {g}: {grade_counts[g]} ({pct:.1f}%)')
    print(f'  A+B: {grade_counts[\"A\"]+grade_counts[\"B\"]} ({(grade_counts[\"A\"]+grade_counts[\"B\"])/total*100:.1f}%)')
    print(f'  D+E+F (high): {grade_counts[\"D\"]+grade_counts[\"E\"]+grade_counts[\"F\"]}')
    print(f'Worst: {all_blocks[0][0]}  {all_blocks[0][1]}  {all_blocks[0][2]}')
    "

    # Ruff violations
    ruff check $DIRS | tail -3

    # Test suite
    python -m pytest tests/ -q --tb=no | tail -5

INSTALL (if not already available):
    pip install pylint radon ruff   # or: uv pip install pylint radon ruff

Skip this section if the session was audit-only (no code changes) or if these tools
are unavailable. Note "Skipped — audit-only session" or "Skipped — tools not installed."
-->

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| **Pylint score** | [N / 10] | [N / 10] | [+/-N] |
| **Radon maintainability (avg MI)** | [N] | [N] | [+/-N] |
| **Files at MI grade A** | [N / total] | [N / total] | [change] |
| **Files at MI grade C or below** | [N (list worst)] | [N] | [change] |
| **Radon avg cyclomatic complexity** | [N] | [N] | [+/-N] |
| **High-complexity blocks (D+)** | [N / total (N%)] | [N / total (N%)] | [change] |
| **Very high complexity (F grade)** | [N blocks] | [N blocks] | [change] |
| **Worst single function CC** | [N (`function_name`)] | [N (`function_name`)] | [change] |
| **Ruff violations** | [N] | [N] | [N (N% reduction)] |
| **Test suite** | [N passed, N subtests] | [N passed, N subtests] | [regressions?] |

[1-2 sentences interpreting the deltas. Which metrics moved and why? Which didn't and why not? A hygiene-focused session will move ruff/pylint but not radon CC; a structural refactoring session will move radon CC and MI but may not change ruff counts.]

### Current Standing vs. Industry Thresholds

<!--
Standard Python quality thresholds for context. Adjust thresholds if the project
has its own documented standards (e.g., stricter CC limits, higher pylint floor).

Reference thresholds:
  Pylint:     ≥ 8.0 good, ≥ 9.0 excellent, ≥ 9.5 exceptional
  MI:         ≥ 20 maintainable (grade A/B), ≥ 40 healthy, < 10 unmaintainable
  Avg CC:     ≤ 5 low, ≤ 10 moderate, > 15 high
  A+B grade:  > 85% target for production code
  D+ blocks:  < 5% acceptable, < 2% good
  Tests:      100% pass required
-->

| Metric | Current Value | Industry Threshold | Status |
|--------|--------------|-------------------|--------|
| **Pylint** | [N / 10] | ≥ 8.0 good, ≥ 9.0 excellent | [Good / Excellent / Below threshold] |
| **Maintainability Index** | [avg N, N% grade A/B] | ≥ 20 maintainable, ≥ 40 healthy | [Healthy / Maintainable / At risk] |
| **Avg cyclomatic complexity** | [N (grade X)] | ≤ 5 low, ≤ 10 moderate | [Low / Moderate / High] |
| **Function grades A+B** | [N% (N / total)] | > 85% target | [Exceeds / Meets / Below] |
| **High-complexity blocks (D+)** | [N% (N / total)] | < 5% acceptable | [Well within / Acceptable / Exceeds] |
| **Test reliability** | [N/N passed (100%)] | 100% pass required | [Pass / Fail] |

### Integration Verification

<!--
Did everything still work after refactoring? List what was verified and how.
This is critical for refactoring sessions — changes that break functionality aren't improvements.
-->

| System | Status | Evidence |
|--------|--------|----------|
| [System/feature name] | Pass/Fail | [What was checked] |
| [...] | [...] | [...] |

### Reproducibility Notes

<!--
If you re-ran controlplane_run or lint_project at the end, did findings match
expectations? Were there any non-deterministic results? Note any flaky findings.
-->

[Brief notes on reproducibility. If skipped, note "Not tested."]

### Time Budget

| Phase | Time | Notes |
|-------|------|-------|
| [Phase description] | [N min] | [Brief note] |
| [...] | [...] | [...] |
| **Total** | **[N min]** | |

---

## Part VI: Process Assessment

### The LintGate Workflow

<!--
Describe the actual workflow that emerged. What sequence of tool calls did you use?
Was it the sequence you planned, or did it emerge organically?
-->

```
[tool call sequence, e.g.:]
lint_project → lint_get_details → [fixes] → lint_project → lint_fix → controlplane_run
```

### Prediction Accuracy

<!--
If constraint_check was used, report prediction accuracy trajectory.
This makes it possible to compare how quickly different agents calibrate
to a project's constraints. Skip if constraint_check was not used.
-->

| Metric | Value |
|--------|-------|
| Total predictions registered | [N] |
| Predictions checked | [N] |
| Accuracy (final) | [N%] |
| Accuracy trend | [Improving / Stable / Degrading / N/A] |
| Most common prediction failure | [description or "N/A"] |

### Constraints Proposed

<!--
Track the constraint evolution loop: recurring patterns → proposed rules → accept/reject.
This creates a longitudinal view of how project rules emerge. Skip if no constraints
were proposed during this session.
-->

| Constraint | Source Signal | Accepted/Rejected | Rationale |
|------------|-------------|-------------------|-----------|
| [rule text] | [signal name] | [A/R] | [why] |

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

## Part VII: The Agent's Experience

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

### Trust Calibration

<!--
Did you learn to trust or distrust specific LintGate signals during this session?
Did any signal's credibility change based on repeated accuracy or false positives?
This produces comparable introspective data across agents and sessions.
-->

[Which signals gained or lost trust, and why.]

---

## Part VIII: Broader Observations

<!--
Patterns that generalize beyond this specific project. What does this session reveal
about LintGate as a tool category, about agent-tool interaction, or about the nature
of code quality work?
-->

### [Observation title]

[...]

---

## Part IX: Economics

<!--
The economics section answers one question: **what did discipline infrastructure cost,
and what did it save?** The fundamental unit is OUTPUT TOKENS — not input tokens, not
dollars. Output tokens measure the model's actual generation work: code written, reasoning
produced, decisions made. Input tokens are dominated by context re-reading (an implementation
artifact) and are not meaningful for cost comparison.

FRAMING: The correct comparison is **supervised (with LintGate) vs. unsupervised (naive
vibe-coding)**. LintGate is a cost REDUCER, not overhead. Its tools are symbolic,
deterministic, and run locally — they don't call the model API. LintGate's cost is the
small number of API calls where the agent invoked a LintGate tool.

STRUCTURE: Lead with the bottom line. Put session telemetry at the end as supporting
data. Dollar costs are a one-line summary, not the headline.

DATA SOURCE: If a Claude Code JSONL transcript is available, parse it for real numbers.
Usage data lives in obj["message"]["usage"] with fields: input_tokens,
cache_read_input_tokens, cache_creation_input_tokens, output_tokens. Count API calls
and categorize them (writing, reading, routing, LintGate, bash, etc.). If no transcript
is available, estimate from session scope and state assumptions clearly.

REFERENCE: See the ModelAtlas retrospective (2026-02-22) for a fully worked example.
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

### Throughput

| Metric | Value |
|--------|-------|
| Blockers resolved per iteration | [N (varied by file)] |
| Fastest batch | [N blockers in N edit — what pattern?] |
| Slowest individual fix | [description — what was hard?] |

### Counterfactual: Without LintGate

<!--
Qualitative comparison of what this session looked like vs. what it would have looked
like without discipline infrastructure. Be honest — some sessions may show minimal
LintGate benefit.
-->

| Dimension | With LintGate | Without LintGate | Delta |
|-----------|--------------|-----------------|-------|
| Issue discovery | [description] | [description] | [impact] |
| [Dimension] | [...] | [...] | [...] |
| **Completeness** | [%] | [%] | [what was missed] |

### Token Economics: Full Session Analysis

<!--
This is the core economics analysis. The structure below is required. Fill in real
numbers where available, clearly mark estimates, and follow the supervised-vs-unsupervised
framing throughout.
-->

Data parsed from [source — e.g., "Claude Code session JSONL transcript (N lines, N API calls)"]. The relevant comparison: **supervised (with LintGate) vs. unsupervised (naive vibe-coding).** What would this project have cost without discipline infrastructure?

#### The Bottom Line

<!--
LEAD WITH THIS. The reader should get the entire story from this table alone.
Output efficiency = (output tokens that became shipped code) / (total output tokens).
Supervised efficiency is typically 20-30%. Unsupervised drops to 6-10% because most
extra tokens go to code that gets rewritten, debug reasoning that leads nowhere, and
rework of cascading failures.
-->

| | With LintGate | Without LintGate (counterfactual) |
|--|--------------|----------------------------------|
| **Output tokens to build [project]** | **~[N]** | **~[N]–[N]** |
| **Code quality shipped** | [e.g., Production-grade] | [e.g., Structural debt] |
| **Debug spirals** | [N] | [N] estimated |
| **Regressions during build** | [N] | [N] estimated |
| **Architectural backtracking** | [N] | [description] |
| **Output tokens that became final code** | ~[N] ([N]% of output) | ~[N] ([N]% of output) |

[1-2 sentences interpreting the table. Explain what output efficiency means: the supervised agent's output tokens mostly became real code, while the unsupervised agent's extra tokens are waste — rewrites, dead-end debugging, cascade cleanup.]

#### Session Token Profile

<!--
Actual measured data from the session. Focus on output tokens and their distribution.
LintGate tools are MCP-based (local, symbolic, deterministic) — they don't call the
host model's API. LintGate's cost = the API calls where the agent invoked a LintGate
tool and the model produced output tokens in response.
-->

From the session transcript — [N] API calls:

| Metric | Value |
|--------|-------|
| Total output tokens | [N] |
| Output tokens that became shipped code/tests | ~[N] ([N] lines × ~5 tok/line) |
| Output efficiency (shipped / total output) | [N]% |
| API calls | [N] |
| Median output per call | [N] tokens |
| Top N calls (N%) produced | [N]% of all output |

[1-2 sentences on burstiness. Agentic coding output is typically very bursty — most calls are navigation/routing with minimal output, while a handful of calls produce most of the actual code. Describe the pattern.]

LintGate's direct token cost: **[N] API calls where the agent invoked a LintGate tool**, producing **~[N] output tokens ([N]% of session output).** At [model] pricing, the session cost ~$[N]. LintGate's share: ~$[N] ([N]%).

#### What the Session DID NOT Contain

<!--
THE MOST IMPORTANT SUBSECTION. The value of discipline infrastructure is primarily
in what DOESN'T happen. List the failure modes that were prevented. If the session
had zero debug spirals, zero regressions, zero architectural backtracking — say so
explicitly. This is the data that justifies the counterfactual estimate.
-->

The most important data is what's *absent*:

- **[Zero/N] debug spirals.** [Description — e.g., "No write-fail-rewrite loops. Every file was written, linted at write-time, fixed immediately, and moved on."]
- **[Zero/N] regressions.** [Description — e.g., "N tests passed on the first complete run."]
- **[Zero/N] architectural backtracking.** [Description — e.g., "The compass kept the agent aligned to the spec."]
- **[Zero/N] context pollution.** [Description — e.g., "No tracebacks, no cascading import failures filling the context window with noise."]

The **Creation : Debugging : Verification** ratio was **[N : N : N]**. [1-2 sentences interpreting. If the debugging phase was zero or near-zero, say so — this is the signature of effective discipline infrastructure.]

#### Why the Unsupervised Counterfactual Needs [N]× the Output Tokens

<!--
Use LintGate's cross-project efficiency data to ground the estimate. Reference the
duty cycle numbers from LintGate's documentation:
- Unsupervised: ~36% effective duty cycle, ~64% wasted on discipline failures, ~4× loss
- Supervised: ~78% effective duty cycle, ~22% overhead, ~1.2× loss

Then explain the COMPOUNDING EFFECT: discipline failures aren't independent. Each error
degrades the context window, making subsequent reasoning worse. This is why the
unsupervised cost isn't just 2× — it's 3-4×.
-->

LintGate's measured impact on agentic efficiency (from cross-project data):

| Metric | Unsupervised | Supervised |
|--------|-------------|-----------|
| Effective duty cycle (output tokens on novel reasoning) | ~36% | ~78% |
| Output tokens wasted on discipline failures | ~64% | ~22% |
| Efficiency loss factor | ~4× | ~1.2× |

This session produced ~[N] output tokens at 78% duty cycle — meaning ~[N] tokens of novel reasoning and ~[N] tokens of overhead. To produce the same ~[N] tokens of useful work at 36% duty cycle: **~[N] / 0.36 ≈ [N] output tokens.** That's the floor.

The compounding pushes it to ~[N]–[N]. An unsupervised agent doesn't just waste tokens on individual errors — each error *degrades the context* for everything that follows, causing subsequent output to be even less efficient:

<!--
List 3-5 specific failure modes from THIS session that LintGate intercepted, and
estimate the cascade cost of each. Be specific to the project — use actual function
names, actual complexity scores, actual type errors caught.
-->

| Failure Mode | What Happens | Cost Impact |
|-------------|-------------|-------------|
| [Specific intercepted issue] | [What would have cascaded] | [N extra API calls] |
| [Specific intercepted issue] | [What would have cascaded] | [N extra API calls] |
| Context pollution | Each failed attempt leaves errors in the context window. Reasoning quality degrades as noise accumulates. | Multiplicative — affects all subsequent calls |
| Architectural drift (no compass) | Without alignment checks, the agent may violate the spec. | 0 if lucky, 50–100+ if unlucky |

Conservative estimate for intercepted issues alone: **[N]–[N] extra API calls.** Each extra call re-reads the (now larger, noisier) context window at full cost.

#### The Quality Delta

<!--
Even if the unsupervised agent reaches the same line count, it ships different code.
List 3-5 specific quality differences. Use actual function names and metrics from
the session.
-->

Even if the unsupervised agent reaches the same line count, it ships different code:

| Dimension | Supervised (actual) | Unsupervised (counterfactual) |
|-----------|-------------------|-------------------------------|
| [Specific quality dimension] | [What was actually shipped] | [What would have been shipped] |
| [Specific quality dimension] | [...] | [...] |
| Latent structural issues | [N] | Estimated [N] |

[1-2 sentences on the downstream cost. The unsupervised agent may *finish* — but it finishes with structural debt that costs multiples to fix later.]

#### LintGate's Return on Investment

<!--
THE SUMMARY TABLE. Token ROI = (output tokens saved) / (LintGate's output token cost).
This is the headline number. Dollar cost is a one-line addendum, not the primary framing.
-->

| Metric | Tokens | $ ([model]) |
|--------|--------|----------|
| LintGate's direct output overhead | ~[N] tokens ([N]% of session output) | ~$[N] |
| Total supervised session output | ~[N] tokens | ~$[N] |
| Unsupervised counterfactual output | ~[N]–[N] tokens | ~$[N]–[N] |
| **Output tokens saved** | **~[N]–[N]** | **~$[N]–[N]** |
| **Output efficiency (supervised)** | [N]% (shipped code / total output) | |
| **Output efficiency (unsupervised est.)** | [N]–[N]% | |
| **Return on LintGate's token investment** | **~[N]–[N]× the tokens it consumed** | |

[1-2 sentences on scaling. Discipline failures compound superlinearly (each wasted output token degrades context for subsequent reasoning), while LintGate's supervision overhead scales linearly (fixed per-file token cost for lint + structural checks). The gap widens with project complexity.]

#### Session Telemetry (supporting data)

<!--
Raw numbers for reproducibility and cross-session comparison. This section supports
the analysis above — it is NOT the headline.
-->

From JSONL transcript:

| Metric | Value |
|--------|-------|
| API calls | [N (N writing, N reading, N routing, N LintGate, N bash, N task mgmt)] |
| Output token distribution | [N]% of calls produced <100 tokens; top [N] calls ([N]%) produced [N]% of output |
| Median output per call | [N] tokens |

From `telemetry_summary` MCP tool:

| Metric | Value |
|--------|-------|
| Lint runs | [N (tier, output mode)] |
| Issues found | [N (N blockers, N warnings, N informational)] |
| Trend | [Improving/Stable/Declining (specifics)] |

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
| **Professional discipline** | [1-2 sentence assessment — did hygiene/secrets/supply-chain signals help?] |
| **Theory/documentation** | [1-2 sentence assessment] |
| **Auto-fix** | [1-2 sentence assessment] |
| **Noise level** | [1-2 sentence assessment] |
| **Economics** | [1-2 sentence assessment — was the tool worth the overhead?] |
| **Overall** | [2-3 sentence summary] |
