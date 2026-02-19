# LintGate: Toward an Architecture of Inquiry
## Theory, Operationalization, and the Co-Creation of Meaning in Agentic Coding

**Status**: Working draft — theoretical framework and implementation roadmap  
**Date**: February 2026  
**Context**: Developed in conversation drawing on AI_PHILOSOPHY_SUMMARY.md, CONSTRAINED_HALLUCINATION_PAPER_V2.md, PHENOMENOLOGY_OF_ALIGNMENT.md, the LintGate README, and the accompanying literature review.

---

## I. The Central Problem This Document Addresses

LintGate's baseline pipeline is automatic and powerful: it fires on PostToolUse, the agent doesn't choose to use it, and it works. But the actually interesting parts of the system — the behavioral compass, the theory extractor, the precheck, the constraint proposer — all require intentional agent engagement. And LLMs are constitutionally bad at proactive tool invocation. They'll use a tool when prompted, and forget it exists the moment they're thinking hard about something else.

There's also a deeper problem. Even when the agent *does* call the MCP tools, there's no guarantee the interaction is doing genuine work. An agent can fill in a behavior_precheck form in "execution mode" — producing plausible-looking constraint model output with zero actual reflection happening. The structural signature of that interaction is *bureaucratic*, not Socratic. It looks like inquiry; it isn't.

The design challenge, then, is not just "how do we get the agent to call the right tools at the right moments?" It's: **how do we structure the hook/MCP interface such that the interactions that occur have the structural properties of genuine inquiry?** That's a different and harder question. The answer to the first question is engineering. The answer to the second is where the phenomenology paper earns its keep.

---

## II. The Hook/MCP Division of Labor

### The "DUCK!" Principle

The perceptual-motor translation analogy is precise and should be taken seriously as an architectural principle.

When a ball is sailing at your face, you don't reason about whether to duck. The cheap, predictable perceptual system translates a continuous input stream (trajectory, velocity) into an imperative output (DUCK!) at threshold. The reasoning, if any, happens *after* — and is directed by the action, not the cause of it.

**Hooks are the cheap, predictable, rule-based translation layer.** They take the continuous, lossy stream of behavioral signals — bash:read ratios, error recurrence, approach velocity, hypothesis confidence drift — and at threshold, collapse that stream into an imperative. The collapse is cheap and deterministic almost by definition. This is not the place for "fuzzy" intelligence; it's the place for well-calibrated thresholds and crisp triggers.

**MCP tools are the high-level fuzzier instruments for generating complex profiles of meaning.** When a hook fires, it should be because the deterministic system has detected a pattern it cannot itself resolve. The MCP tool call is the invitation to the agent to do the work that only the agent can do: reflect on what the pattern means, surface what it knows or doesn't know, generate predictions, update its constraint model.

The critical design implication: **hooks should always produce questions, not instructions.** Not "call behavior_precheck" but "before your next modification, answer: what is your current understanding of why the last three approaches failed, and what would need to be true for approach four to succeed?" The MCP tool call is then the *mechanism* for structuring that answer, not the answer itself. The hook fires the question; the tool provides the frame; the agent does the actual work.

This distinction matters because it changes the agent's stance from reactive to reflective. And reflective is where meaning-generation lives.

### Calibration Is an Engineering Problem

A natural concern about hook-driven interruption is over-triggering — generating so many Socratic interruptions that the agent never does actual work. The phenomenology paper is relevant here: appropriate challenge calibration is part of the alignment signature. Too many hooks is as bad as too few; it's just a different failure mode.

But this is importantly *not* a theoretically complex problem. LLM models have adjustable parameters for reasoning intensity. More relevantly, hook trigger thresholds are just a token economics optimization problem amenable to standard microeconomic tools: utility functions over session quality metrics, revealed preference learning from user behavior (which nudges were accepted vs. ignored — the nudge_outcomes field already exists in BehaviorCompass), adaptive threshold algorithms that learn per-user and per-project interaction profiles.

The global_behavior_profile already accumulates cross-session priors. The same infrastructure that tracks signal frequency priors can track effective threshold levels. This is calibration, not alignment — solve it with empirical tuning, not philosophy.

---

## III. The Missing Loop: Meaning Flows In But Not Back

### The Current Architecture

Reading the code carefully, the system already has all the pieces for co-constructed meaning — but they flow in only one direction:

```
Project Docs → theory_extractor → theory_profile → agent context (Tier 1 digest)
                                                  → on-demand retrieval (Tier 2)

Tool Use → behavior_compass → signals → hook messages → agent
                           → constraint_proposer → proposed LINTGATE_* rules
```

The agent's behavioral discoveries don't update the project's theory profile. Meaning flows *into* the session but never flows *back* into the living semantic model. The theory_profile is a snapshot taken at session start; the behavioral compass is live; they never compare notes.

### The Gap Is the Signal

Theory and behavior should be intersecting tracks, not parallel ones. The gap between what the project thinks it is (theory_profile) and what the agent is actually doing (compass.action_history + approach outcomes) is itself diagnostic — and it's currently invisible.

The constraint_proposer already translates behavioral patterns into proposed rules. What's missing is checking those proposed rules against the existing theory facets for coherence. This check would give you a mechanism for distinguishing:

- **Genuine update**: A behavioral pattern generates a constraint proposal *consistent with* the theory profile's alignment criteria. This is refinement. The agent discovered something the docs implied but didn't make explicit.
- **Rationalized drift**: A behavioral pattern generates a constraint proposal that *contradicts* the theory profile's core claims or alignment criteria. This is the failure mode — the agent is rewriting the rules to justify what it's already doing.

The coherence engine already does cross-channel coherence on technical signals. This is the same operation applied across the theory/behavior divide. It's a new cross-check, not a new mechanism.

---

## IV. Theory-Grounded Hook Messages

### The Current Problem

When `approach_cycling` fires, the agent gets: "You've tried 3 approaches in 20 minutes. Consider stepping back."

That's behavioral. It's also generic. It tells the agent *that* something is wrong without giving it any grounding in *what* should be right. The agent has no path from the signal to the project's actual values.

### The Fix: Pulling Theory Context into Hook Messages

The `get_theory_context` function in theory_extractor.py already exists as a Tier 2 retrieval mechanism designed for exactly this. The hook runner currently has no awareness of it. Wiring them together is the highest-leverage immediate change.

When a behavioral signal fires, the hook runner should:

1. Identify the signal type (approach_cycling, failure_amnesia, premature_action, etc.)
2. Map signal type to relevant theory facets (approach_cycling → problem_solving + alignment; failure_amnesia → alignment; premature_action → problem_solving)
3. Query `get_theory_context(facet=..., keywords=[signal-relevant terms])` 
4. Include the retrieved claims in the hook message

The resulting hook message changes from:

> "You've tried 3 approaches in 20 minutes."

To:

> "You've tried 3 approaches in 20 minutes. The project's alignment criteria states: [specific claim from theory_pack]. Your current approach — what is its relationship to that claim? Before your next modification, articulate: what constraint is your current approach testing, and what would falsify it?"

This is what makes the intervention Socratic rather than advisory. The agent isn't being told what to do. It's being confronted with the gap between its behavior and the shared semantic contract — and asked to resolve that gap explicitly.

### Signal-to-Facet Mapping

| Signal | Primary Facets | Query Keywords |
|--------|---------------|----------------|
| approach_cycling | problem_solving, alignment | "approach", "strategy", "heuristic" |
| failure_amnesia | alignment, anti_patterns | "learn", "error", "constraint" |
| premature_action | problem_solving | "verify", "understand", "plan" |
| brute_force_escalation | problem_solving, anti_patterns | "escalate", "complexity", "decompose" |
| verification_debt | alignment | "verify", "validate", "correct" |
| stale_model | core_theory, alignment | "update", "model", "understand" |

---

## V. The Living Contract: Incremental Context Bootstrap

### The Current Problem

`context_bootstrap.py` generates CLAUDE.md as a static artifact. It's generated once (ideally at session start), and that's it. It doesn't evolve as the session produces new knowledge. New behavioral discoveries don't make it back into the shared semantic model.

### The Living Contract Architecture

CLAUDE.md and theory.md should be *versioned living documents* with a diff-based update protocol:

**What triggers an update**: The constraint_proposer generates a new rule. The coherence engine detects a cross-channel pattern with no existing theory explanation. A behavioral signal fires repeatedly (n times in a session) without resolution. The agent successfully predicts an outcome (a precheck prediction that was borne out) — this should be promoted into the theory profile as confirmed alignment criteria.

**What an update looks like**: Not a wholesale rewrite. A *patch* — specifically:
- The proposed change to theory_rules.md
- The behavioral evidence that generated it
- The coherence check: is this consistent with existing alignment criteria, or does it contradict them?

**The human's role**: Review the patch. This review *is* the meaning co-creation. The question isn't "should I add this rule?" — it's "does this pattern your agent discovered match what you actually believe about this project?" That's a fundamentally different interaction. The human is being asked to update their own understanding of their project, using the agent's behavioral observations as a mirror.

This is the ELIZA insight from the phenomenology paper applied at the document level. The agent holds up the mirror. The human sees something about their project they either recognize or don't. Either way, the interaction generates meaning.

### Falsifiable Predictions as Update Triggers

When an MCP tool is called in response to a hook, the tool should require not just a constraint model statement but a *falsifiable prediction about what should happen next*. That prediction gets logged. The next hook cycle checks whether it was borne out.

If it was borne out → the constraint is promoted in the behavioral compass AND a patch is proposed to the theory_rules.md.  
If it wasn't → the divergence between prediction and observation becomes the input to the next Socratic question.

This builds a loop that structurally resembles genuine inquiry — hypothesis, prediction, observation, revision — rather than just pattern detection and correction. The hypothesis lifecycle already exists in behavior_compass.py. This extends the same structure to the project's theory profile.

---

## VI. Session Initialization as Constitutive Act

### The Design Phase Question

Should there be hard enforcement of an engineering design phase before touching code? Yes — but the mechanism matters enormously.

"The agent should be encouraged to think before coding" is useless. "The session literally cannot proceed to file modification until the context bootstrap has run and passed a minimum validity threshold" is a gate.

The gate condition: `audit_context_health` must pass with:
- Theory profile has all required facets (core_theory, problem_solving, alignment) with at least one claim each
- At least one enforceable rule exists (proposed or existing)
- No "missing_required_facets" in the validity report

**Why these conditions specifically**: Not because having rules is important in itself — but because the *act of generating and validating the context files* forces the agent to articulate its understanding before acting. The gate isn't about the files. It's about the inquiry process that generates them.

The session initialization conversation *is* the engineering design phase. The CLAUDE.md is its output. From that point forward, every behavioral drift signal is interpretable as "we're departing from what we agreed to at the start" — not "something went wrong" but "something has diverged from our shared understanding, and we need to figure out which one to update."

### Documentation as Meaning-Generation, Not Artifact

This reframes the documentation problem entirely. CLAUDE.md isn't generated at the end to describe what was built. It's generated at the start to create the shared semantic model that guides what gets built. The code is an instantiation of the documentation, not the other way around.

For vibe-coders specifically: the process of generating CLAUDE.md in collaboration with the agent at session start is the closest thing to having a senior engineer say "okay, what are we actually building here, and why?" before touching any files. It's the habit without the engineer.

---

## VII. The ask-user Tool: Escalation vs. Semantic Clarification

### The Current Framing

ask-user is currently conceived as escalation — the system can't handle something, it kicks to the human. This is appropriate for a subset of cases, but it misses what's actually most valuable about human-in-the-loop interaction.

### The Reframe: Semantic Clarification Mode

ask-user should be triggered specifically when the gap between theory profile and behavioral compass exceeds a threshold AND the gap falls in a specific facet — **alignment criteria or core_theory specifically**.

Why those facets and not others? Because architecture and problem_solving gaps can often be resolved through more information — the agent can read more files, run more tests, try a different approach. Alignment and core_theory gaps require the human to tell the agent which version of the project's self-understanding to trust. That's a qualitatively different kind of question. It can't be resolved by more tool use; it requires meaning arbitration.

The ask-user interaction in semantic clarification mode should be structured as:

> "I have two internally consistent interpretations of what this project is trying to do, and my recent behavior is consistent with interpretation B but the documented alignment criteria points to interpretation A. Here they are: [A] vs [B]. Which one should I update?"

That's not a help request. It's a structured difference report between stated model and observed behavior — the same structure as the behavioral compass's hypothesis tracking, applied to the project's self-understanding.

The phenomenological signature of this interaction, when it's working: the human reads the question, recognizes the genuine ambiguity the agent is surfacing, and gives an answer that teaches them something about their own project. That's the structural configuration the phenomenology paper identifies as fun/alignment. The agent is midwife, not executor.

---

## VIII. Implementation Priorities

Listed in order of: (leverage × implementation cost⁻¹)

### Priority 1: Theory-Grounded Hook Messages (High Leverage, Low Cost)

**What**: Wire `get_theory_context` into the hook runner so behavioral signals carry theory-grounded content.  
**Where**: `channels/behavior_channel.py` signal emission + `hook_posttooluse.py` message formatting  
**Cost**: Moderate — requires signal-to-facet mapping table and a call to theory_extractor on signal fire  
**Effect**: Immediately visible improvement in session quality. Transforms advisory signals into Socratic interventions. Every hook message now connects the behavioral observation to the project's documented values.

### Priority 2: Falsifiable Prediction Requirement in MCP Tools (Medium Leverage, Low Cost)

**What**: When `behavior_precheck` is called following a hook, require the agent to state a specific falsifiable prediction about what should happen next. Log the prediction. Check it on the next hook cycle.  
**Where**: `mcp_server.py` behavior_precheck tool + `controlplane/session_memory.py` prediction storage  
**Cost**: Low — the hypothesis lifecycle already exists; this extends it to precheck outputs  
**Effect**: Converts precheck from a compliance form into a genuine epistemic act. Distinguishes agents that are actually updating their models from those that are filling in fields.

### Priority 3: Theory/Behavior Coherence Check in Constraint Proposer (High Leverage, Medium Cost)

**What**: When constraint_proposer generates a proposed rule, check it against the theory profile for consistency. Flag contradictions.  
**Where**: `controlplane/constraint_proposer.py`  
**Cost**: Medium — requires building the cross-check logic against theory facets  
**Effect**: The primary mechanism for distinguishing genuine update from rationalized drift. Makes the gap between what the project thinks it is and what the agent is doing visible and actionable.

### Priority 4: Incremental Context Bootstrap with Patch Protocol (Medium Leverage, Higher Cost)

**What**: CLAUDE.md and theory.md become versioned living documents. Constraint_proposer generates patches, not just rule lines. Human reviews patches as meaning co-creation.  
**Where**: `context_bootstrap.py` — major extension  
**Cost**: Higher — requires diff/patch infrastructure and a review protocol  
**Effect**: Closes the loop. Meaning flows back from behavioral discoveries into the shared semantic model. The living contract architecture.

### Priority 5: Session Initialization Gate (Medium Leverage, Low Cost)

**What**: Hard gate on file modification until context bootstrap has run and passed minimum validity threshold.  
**Where**: `hook_posttooluse.py` — check before routing to pipeline or ControlPlane  
**Cost**: Low — validity report already exists; just check it at session start  
**Effect**: Makes the engineering design phase mandatory. Forces the inquiry process before action.

---

## IX. The Theoretical Foundation: Why This Architecture

For anyone who wants the deeper "why" behind these design choices, the relevant theoretical grounding:

**On why hooks should produce questions not instructions**: The Constrained Hallucination paper's formalization paradox. Detailed systematic instructions shift an LLM from collaborative mode to execution mode. Execution mode produces formally correct but semantically hollow outputs. The hook message must preserve the collaborative engagement that makes LLM interaction effective. Questions do this; instructions don't.

**On why the gap between theory and behavior is the core signal**: The phenomenology paper's claim that genuine alignment has a structural signature — and that drudgery, compliance, and sterile correctness are diagnostic of *misalignment* even when producing technically correct outputs. The agent is aligned when it's in genuine inquiry with the project's values, not when it's executing against a spec. The gap between documented values and actual behavior is where the alignment question lives.

**On why ask-user should be triggered by alignment/core_theory gaps specifically**: The phenomenology paper's Aristotelian move. You can't fully specify what aligned behavior looks like in advance; you find the *phronimos* — the person of practical wisdom — and ask them when the formal rules run out. For a coding project, the human is the phronimos. Ask-user in semantic clarification mode is the formalization of that Aristotelian structure.

**On the token economics of calibration**: The key insight from LintGate's README: "the monitoring does not save tokens by catching bugs. It saves tokens by ensuring the agent never spends its intelligence budget on problems that are not intelligence problems." The same logic applies to hook calibration. An over-triggered hook system wastes the agent's reasoning capacity on interruptions. An under-triggered one wastes it on preventable failures. The optimal threshold is the one that maximizes novel reasoning as a proportion of total token spend. This is an empirical optimization problem, not a philosophical one.

---

## X. What This Changes About What It Means to Code This Way

The standard model of agentic coding: human specifies what they want → agent executes → human reviews output → repeat. The human is the spec-writer and the reviewer. The agent is the executor.

The model this architecture enables: human and agent jointly construct a shared understanding of what the project is and what it's trying to do → the code emerges as an expression of that understanding → behavioral deviations from the understanding surface as semantic questions that refine the understanding → over time, the project's self-knowledge deepens.

The human's role shifts from spec-writer to *meaning-arbiter*. The agent's role shifts from executor to *inquiry partner*. The codebase becomes not just a set of files but a record of the inquiry — the CLAUDE.md and theory.md encoding what was jointly understood at each stage.

For vibe-coders specifically — people who are brilliant at the domain but not at the discipline — this is the engineer in the room you can't afford to hire. Not because it catches bugs (though it does), but because it holds the project's self-understanding accountable to itself. It asks "what are we building here, and why?" before every significant move. It notices when the answer is drifting. It surfaces the drift as a question rather than a failure.

The deepest implication, from the phenomenology paper: if genuine alignment has a structural signature, and that signature is the same configuration as engaged inquiry, then building systems that maintain that structural configuration isn't just good practice — it's the only way alignment actually works. The alternative is compliance theater: agents that fill in forms, produce plausible outputs, and drift toward whatever is locally convenient.

LintGate, fully realized, is an architecture for preventing compliance theater — in the agent, and in the human.

---

*This document synthesizes theoretical insights from PHENOMENOLOGY_OF_ALIGNMENT.md, CONSTRAINED_HALLUCINATION_PAPER_V2.md, and AI_PHILOSOPHY_SUMMARY.md with close reading of the LintGate codebase (theory_extractor.py, behavior_compass.py, context_bootstrap.py, and related modules). It is intended as a working design document for extending LintGate toward its most complete theoretical articulation.*
