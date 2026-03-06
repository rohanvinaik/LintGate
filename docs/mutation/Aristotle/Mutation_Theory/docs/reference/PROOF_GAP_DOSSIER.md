# Proof Gap Dossier (Theory ↔ Lean)

Date: 2026-03-05  
Scope: canonical repository at `MutationTheory/` in this workspace.

This file is a copy-paste-oriented ledger of the remaining proof gaps.  
Each block includes:
1. current Lean proof state (verbatim snippet),
2. what is still missing vs the paper-level claim,
3. a likely proof architecture to close it.

---

## GAP-01 — T7.17 Sheaf Gluing Is Still Assumption-Passed

### Current proof (as implemented)
File: `MutationTheory/Phase7/SheafCondition.lean`

```lean
theorem T7_17_sheaf_condition
    {Cost : Type}
    [AddCommMonoid Cost]
    [Preorder Cost]
    (κGlobal : Cost)
    (κLocal : Module → Cost)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (_hSheaf : sheafCondition compatible interfaces)
    (hAdditive : κGlobal ≤ localSpecificationCost κLocal modules) :
    κGlobal ≤ localSpecificationCost κLocal modules :=
  hAdditive
```

### Gap vs theory
- Theory claim: sheaf compatibility + local full specification should *derive* global additive bound.
- Current theorem accepts additive inequality as an input hypothesis (`hAdditive`), so gluing is not proved.

### Likely proof architecture
- Define canonical decomposition semantics:
  - local mutant universe per module,
  - interface mutant universe per pair,
  - restriction of tests to interfaces.
- Prove gluing lemma:
  - if each local suite achieves local full SC and interface restrictions are sheaf-compatible,
  - then union suite achieves global full SC.
- Convert gluing to cardinal bound:
  - `κ(P) ≤ |⋃ local_suites| ≤ Σ_i κ(P_i)`.
- Keep failure-form theorem as derived corollary via obstruction costs.

### Copy-paste block
```text
TASK: Close T7.17 by replacing assumption-passing theorem with a derived theorem.

Context:
- File: /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase7/SheafCondition.lean
- Current theorem `T7_17_sheaf_condition` just returns `hAdditive`.
- Theory target: if sheaf condition holds and local modules are fully specified, derive `κ_global ≤ Σ κ_local`.

Implement:
1) Add decomposition primitives sufficient to state local-full-SC and interface compatibility in this file (or a new Phase7/Internal sheaf support file imported here).
2) Prove a gluing theorem from those primitives.
3) Restate T7.17 to depend on gluing assumptions, not pre-supplied additive inequality.
4) Keep backwards-compatible theorem alias if needed, but canonical T7.17 must be derived.

Acceptance:
- `lake build` succeeds.
- Theorem body is nontrivial (not direct `exact` of an input inequality).
- TheoremMap alias `T7_17_sheafCondition` still resolves.
```

---

## GAP-02 — T7.18 Equivalence Still Depends on Bridge Hypotheses

### Current proof (as implemented)
File: `MutationTheory/Phase7/ObstructionClass.lean`

```lean
theorem T7_18_obstruction_zero_iff_sheaf
    (γ : Module → Module → ℕ)
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (hSheafToZero :
      Sheaf.sheafCondition compatible interfaces → ∀ e ∈ interfaces, γ e.1 e.2 = 0)
    (hZeroToSheaf :
      (∀ e ∈ interfaces, γ e.1 e.2 = 0) → Sheaf.sheafCondition compatible interfaces) :
    obstructionClass γ interfaces = 0 ↔ Sheaf.sheafCondition compatible interfaces := by
  constructor
  · intro hObs0
    exact hZeroToSheaf ((T7_18_obstruction_zero_iff γ interfaces).1 hObs0)
  · intro hSheaf
    exact (T7_18_obstruction_zero_iff γ interfaces).2 (hSheafToZero hSheaf)
```

### Gap vs theory
- Theory claim: zero obstruction iff sheaf condition (intrinsic statement).
- Current proof requires both directions as assumptions (`hSheafToZero`, `hZeroToSheaf`), so equivalence is not derived from canonical definitions.

### Likely proof architecture
- Define `compatible` canonically from `γ` (e.g. zero-penalty compatibility predicate).
- Prove:
  - `sheafCondition compatible interfaces ↔ ∀ e∈interfaces, γ(e)=0`.
- Compose with existing `obstructionClass = 0 ↔ all interface penalties zero`.
- Remove bridge assumptions from canonical theorem.

### Copy-paste block
```text
TASK: Make T7.18 intrinsic by removing bridge hypotheses.

Context:
- File: /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase7/ObstructionClass.lean
- Current `T7_18_obstruction_zero_iff_sheaf` takes `hSheafToZero` and `hZeroToSheaf`.
- Need theorem derived from definitions.

Implement:
1) Introduce canonical compatibility-from-gap predicate (or equivalent canonical bridge lemma).
2) Prove equivalence between sheaf condition and zero-per-interface gaps directly.
3) Refactor `T7_18_obstruction_zero_iff_sheaf` to no longer require bridge assumptions.
4) Update `T7_18_sheaf_implies_local_objective` accordingly.

Acceptance:
- No assumption arguments equivalent to `hSheafToZero`/`hZeroToSheaf` remain in canonical T7.18 theorem.
- `lake build` succeeds.
```

---

## GAP-03 — T7.19 Still Assumes a Gluing Lemma

### Current proof (as implemented)
File: `MutationTheory/Phase7/DistributedConvergence.lean`

```lean
theorem T7_19_asynchronous_specification
    (agents : Finset Agent)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (localCovers : Agent → Module → Prop)
    (compatible : Module → Module → Prop)
    (globalFullSC : Prop)
    (hSheaf : sheafCondition compatible interfaces)
    (hLocal : localCoverage localCovers agents modules)
    (hGluing :
      sheafCondition compatible interfaces →
      localCoverage localCovers agents modules →
      globalFullSC) :
    globalFullSC :=
  hGluing hSheaf hLocal
```

### Gap vs theory
- Theory claim: asynchronous local completeness + sheaf compatibility implies global completeness.
- Current theorem is a wrapper around an assumed implication (`hGluing`).

### Likely proof architecture
- Introduce local per-agent test suites over time and global suite as union.
- Define “agent i achieves local full SC for module i”.
- Prove:
  - local-complete coverage kills all local mutants,
  - sheaf compatibility kills interface mutants,
  - therefore global full SC.
- Then T7.19 can be a derived theorem without `hGluing` hypothesis.

### Copy-paste block
```text
TASK: Derive T7.19 from explicit asynchronous/local completeness assumptions.

Context:
- File: /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase7/DistributedConvergence.lean
- Current theorem includes `hGluing` as an assumption.
- Desired statement: no externally supplied gluing implication.

Implement:
1) Add explicit model of local suites and global union suite.
2) State/define local full-SC and interface mutant coverage assumptions.
3) Prove asynchronous convergence theorem directly from those assumptions and sheaf condition.
4) Keep current wrapper as compatibility theorem if desired, but not as canonical T7.19 result.

Acceptance:
- Canonical `T7_19_asynchronous_specification` no longer depends on a raw `hGluing` function hypothesis.
- `lake build` succeeds.
```

---

## GAP-04 — T7.20 Missing the “No-Sheaf ⇒ Need Interface Tests” Branch

### Current proof (as implemented)
File: `MutationTheory/Phase7/DistributedConvergence.lean`

```lean
theorem T7_20_convergence_under_partial_communication
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module)
    (_hSheaf : sheafCondition compatible interfaces)
    (hMono : globalKilledMonotone K)
    (hComm : communicationAdequate K remainingInterface) :
    (∀ i, K i ⊆ K (i + 1)) ∧
    (∀ m ∈ remainingInterface, ∃ i, m ∈ K i) :=
  ⟨hMono, hComm⟩
```

```lean
theorem T7_20_convergence_under_partial_communication_strengthened
    ...
    (hMono : globalKilledMonotone K)
    (hComm : communicationAdequate K remainingInterface) :
    ... ∧ (∃ N, remainingInterface ⊆ K N) := by
  ...
```

### Gap vs theory
- Positive branch is now formalized (monotonicity/eventual visibility/finite round).
- Missing theoremized negative branch from paper:
  - if sheaf fails and nobody generates interface tests, some interface mutants remain un-killed.
- Also missing schedule-order invariance statement as a named theorem.

### Likely proof architecture
- Define explicit event/schedule semantics for communication order.
- Prove union-of-tests invariance under schedule permutations.
- Define interface-invisible mutants under local-only testing.
- Prove impossibility lemma under `¬sheafCondition` + no interface-test capability.

### Copy-paste block
```text
TASK: Complete T7.20 by formalizing the failure/necessity branch and order invariance.

Context:
- File: /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase7/DistributedConvergence.lean
- Positive convergence branch exists (including finite-round theorem).
- Missing: formal theorem that without sheaf, convergence requires interface-test generation.

Implement:
1) Add theorem: communication-order permutation does not affect eventual global killed set (given same multiset/union of emitted tests).
2) Add theorem: under `¬sheafCondition`, if all agents are restricted to local-only tests with no interface generator, then full interface kill cannot be reached.
3) Derive necessity corollary matching paper text: interface tests are required in no-sheaf regime.

Acceptance:
- New named theorem(s) cover paper’s item (3) in T7.20.
- Existing strengthened positive theorem remains intact.
- `lake build` succeeds.
```

---

## GAP-05 — T7.21–T7.23 Topological Frontier Not Formalized

### Current proof state (explicit note)
File: `MutationTheory/Phase7/TheoremMap.lean`

```lean
/-!
`T7.21`–`T7.23` (topological frontier in the reference doc)
are not yet formalized as canonical Lean theorems in this repository.
-/
```

### Gap vs theory
- Paper claims a frontier section with mutation neighborhood complex / homological complexity / Euler characteristic relations.
- Canonical Lean has no formal definitions/theorems yet for this layer.

### Likely proof architecture
- Stage 1 (formalized, non-conjectural):
  - define finite mutation neighborhood hypergraph/complex,
  - define simplex killability relation,
  - prove basic combinatorial lemmas relating cover/hitting numbers to κ-style bounds.
- Stage 2 (conjectural):
  - add `conjecture` declarations for Betti/Euler relations,
  - isolate all hard topology assumptions behind explicit interfaces.

### Copy-paste block
```text
TASK: Add canonical Phase7 topological frontier scaffolding (T7.21–T7.23).

Context:
- File to extend: /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase7/
- TheoremMap explicitly states T7.21–T7.23 are absent.

Implement:
1) New module (suggested): `Phase7/TopologicalFrontier.lean`.
2) Define mutation neighborhood complex data structure over finite mutants/tests.
3) Prove at least one nontrivial combinatorial theorem linking killability complexes to κ bounds (T7.21 core).
4) Add clearly labeled conjectural statements for T7.22/T7.23 if full proofs remain open.
5) Wire aliases in `Phase7/TheoremMap.lean`.

Acceptance:
- Canonical module exists and builds.
- TheoremMap no longer says “not yet formalized” for definitions and at least one theorem in this block.
```

---

## GAP-06 — Greedy Harmonic Bound Is Hypothesis-Passed, Not Derived

### Current proof (as implemented)
File: `MutationTheory/Phase7/Internal/Backbone.lean`

```lean
theorem T5_12_greedy_approximation_ratio
    ...
    (h_greedy_harmonic : (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n)
    ... :
    (k_greedy : ℝ) ≤ T_opt.card * (Real.log n + 1) := by
  ...
  exact le_trans h_greedy_harmonic h_mul
```

T7.7 downstream also consumes this assumption:

```lean
theorem T7_7_trajectory_comparison
    ...
    (h_greedy_harmonic : (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n)
    ... := by
  ...
```

### Gap vs theory
- Theory claims classical Chvátal-style guarantee.
- Current canonical theorem proves only the `H(n) ≤ log n + 1` conversion, assuming the core harmonic inequality as input.

### Likely proof architecture
- Build internal set-cover proof in Lean:
  - define uncovered survivor count sequence,
  - prove per-step multiplicative decrease from greedy maximality and optimal set size,
  - derive harmonic charging argument,
  - conclude `k_greedy ≤ k_opt * H(n)`.
- Refactor theorem signatures to remove `h_greedy_harmonic` parameter.

### Copy-paste block
```text
TASK: Prove the harmonic greedy bound in Lean and remove hypothesis-passing.

Context:
- File: /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase7/Internal/Backbone.lean
- Current T5.12 theorem assumes `h_greedy_harmonic`.
- T7.7 consumes the same assumption.

Implement:
1) Add full proof of `k_greedy ≤ k_opt * Harmonic_P5 n` from greedy/set-cover premises.
2) Replace `h_greedy_harmonic` assumptions in both T5.12 and dependent T7.7 theorem(s).
3) Keep conversion to `log n + 1` as corollary.
4) Ensure no theorem in canonical Phase7 requires external harmonic-bound hypothesis.

Acceptance:
- `rg \"h_greedy_harmonic\"` only appears in proof internals/comments, not theorem parameters.
- `lake build` succeeds.
```

---

## GAP-07 — Phase 6 Canonical Surface Still Depends on Legacy Monolith

### Current proof state (as implemented)
Files:
- `MutationTheory/Phase6/ExactLearning.lean`
- `MutationTheory/Phase6/CodingTheory.lean`
- `MutationTheory/Phase6/SpecP.lean`

Each currently imports legacy:

```lean
import MutationTheory.Legacy.Phase_6_Exact_Learning
```

Canonical files are mostly aliases:

```lean
abbrev codingTwistHolds := @Phase6.T6_19_20_twistHolds
abbrev ProgramBridge19 ... := @Phase6.T6_19_20_ProgramBridge ...
abbrev ProgramBridge21 (n : Nat) := Phase6.T6_21_ProgramBridge n
```

Legacy does define bridge structures, e.g.:

```lean
structure T6_19_20_ProgramBridge where ...
structure T6_21_ProgramBridge (n : Nat) where ...
```

### Gap vs theory
- The canonical Phase 6 surface is not independent/self-contained yet.
- Type bridge infrastructure exists but remains anchored in legacy file organization.

### Likely proof architecture
- Introduce `Phase6/Internal/*` canonicalized modules for:
  - exact learning core,
  - codeword testability bridge,
  - Boolean-function bridge + SpecP.
- Move or re-prove theorems into canonical namespace.
- Replace Phase6 top-level alias wrappers with direct canonical definitions/theorems.
- Keep legacy only as archival, not imported by canonical build path.

### Copy-paste block
```text
TASK: Remove canonical Phase6 dependency on Legacy monolith and finalize bridge integration.

Context:
- Files currently importing legacy:
  - /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase6/ExactLearning.lean
  - /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase6/CodingTheory.lean
  - /Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory/MutationTheory/Phase6/SpecP.lean
- Need canonical self-contained Phase6 modules.

Implement:
1) Create canonical internal modules under `MutationTheory/Phase6/Internal/` and migrate required definitions/theorems.
2) Keep bridge structures (T6.19/20 and T6.21) in canonical Phase6 namespace.
3) Replace top-level Phase6 files with imports from canonical internal modules (no `MutationTheory.Legacy.*` imports).
4) Ensure Phase7 imports continue working without referencing legacy.

Acceptance:
- `rg \"import MutationTheory.Legacy.Phase_6_Exact_Learning\" MutationTheory/Phase6` returns no matches.
- `lake build` succeeds.
```

---

## Priority Order (recommended)

1. GAP-06 (greedy harmonic proof)  
2. GAP-01 (real T7.17 gluing theorem)  
3. GAP-02 (intrinsic T7.18 equivalence)  
4. GAP-03 + GAP-04 (distributed completeness + no-sheaf necessity)  
5. GAP-07 (Phase6 de-legacy + bridge finalization)  
6. GAP-05 (frontier topology formalization scaffolding)

This order closes core mathematical claims first, then canonical architecture, then frontier expansion.
