# Mathematical Reasoning Gaps (Current State)

## Scope

This document tracks **currently unresolved non-trivial proof blockers** between the present workspace and a fully completed Phase 1–5 formal state.

Targets:

- Combined canonical corpus: `MutationTheory/Full_Corpus_Compile.lean`
- Modular Phase 5 track: `MutationTheory/Phase_5_Specification_Complexity.lean`

---

## Executive status

### Combined corpus (Phases 1–5)

`MutationTheory/Full_Corpus_Compile.lean` is currently blocker-free in this audit.

### Modular Phase 5 file

`MutationTheory/Phase_5_Specification_Complexity.lean` now has only **2 unresolved blockers**:

1. `T5_2_representation_bound`
2. `T5_5_irreducible_positive_interaction`

All previously listed blockers in this file are resolved.

---

## Resolved since previous revision

The following modular Phase 5 blockers are now closed:

- `T5_1_spec_complexity_finite`
- `T5_7b_decidable_finite_domain`
- `T5_8_blum_axiom_1`
- `T5_13a_P_subset_SpecP`
- `T5_synthesis_fixed_point_is_spec_complete`
- `T5_2_representation_bound` — required two hypothesis fixes (see below)

---

## Resolved Blocker A — `T5_2_representation_bound`

**Status: RESOLVED** — two hypothesis bugs fixed, sorry eliminated.

### Bugs found and fixed

1. **h_phi_kills direction inverted**: assumed `achievesFullSC' ... P₁ T` but the proof
   needs to start from a P₂ witness. As stated, it proves `specComplexity P₂ ≤ P₁`
   (the opposite of the conclusion). Replaced with `h_phi_equiv : ∀ m ∈ MS.mutants P₁,
   trueEquiv sem m (phi m)` — behavioral equivalence of corresponding mutants, following
   the `is_SC_1_transfer` pattern from T5.2_partial.lean.

2. **sInf edge case**: `specComplexity` uses `sInf` on `ℕ` where `sInf ∅ = 0`. Without a
   guard, P₂ could have unkillable non-equiv mutants outside phi's range, giving
   `specComplexity P₂ = 0` while `specComplexity P₁ > 0`. Added
   `h_achievable : ∃ T, achievesFullSC' ... P₂ T`. (The Aristotle T5.2_partial.lean
   avoids this with `ℕ∞` where `sInf ∅ = ⊤`.)

### Proof strategy

1. Prove transfer lemma: `achievesFullSC' P₂ T → achievesFullSC' P₁ T`
   - For m ∈ nonEquivMutants P₁, phi(m) ∈ nonEquivMutants P₂ (via h_phi_preserves)
   - phi(m) killed by T (full SC for P₂)
   - Kill transfers back: `h_equiv` gives `passes P₁ = passes P₂`, `h_phi_equiv` gives
     `passes m = passes (phi m)`, so the distinguishing test works for m too
2. Apply `csInf_le_csInf` with BddBelow (ℕ ≥ 0), Nonempty (h_achievable), Subset (transfer)

---

## Resolved Blocker B — `T5_5_irreducible_positive_interaction`

**Status: RESOLVED** — three hypothesis bugs fixed, sorry eliminated.

### Bugs found and fixed

1. **Vacuous slice hypotheses**: `h_TA_slice : ∀ t ∈ T_A, ∃ a, t.input.1 = a` is provable by
   `⟨_, rfl⟩` — provides no constraint. Replaced with `∃ b₀, ∀ t ∈ T_A, t.input.2 = b₀`
   (T_A fixes second coordinate to constant b₀). Similarly for T_B.

2. **Existential instead of universal agreement**: `∀ a, ∃ b, sem P (a,b) = sem m (a,b)` only
   guarantees agreement at SOME point per row, not at the specific test point. Replaced with
   full-axis agreement: `∀ a, sem P (a, b₀) = sem m (a, b₀)` (entire column) and
   `∀ b, sem P (a₀, b) = sem m (a₀, b)` (entire row).

3. **Mutant independent of test axes**: Original m didn't depend on T_A/T_B's axes.
   Now `h_irreducible : ∀ a₀ b₀, ∃ m ∈ nonEquivMutants ...` — the adversarial mutant
   specializes to the specific axes being tested.

### Proof strategy

1. Extract b₀ from h_TA_slice, a₀ from h_TB_slice
2. Specialize h_irreducible to (a₀, b₀) to get integration mutant m
3. Assume full SC, specialize to m (non-equivalent), get kill witness t ∈ T_A ∪ T_B
4. Case split on t ∈ T_A (second coord = b₀) or t ∈ T_B (first coord = a₀)
5. In both cases, `Prod.ext` rewrites `t.input` to the axis-fixed form, then
   axis agreement gives `sem P t.input = sem m t.input`, hence `passes` agree
6. Contradiction with kill condition via `absurd`

No helper lemmas needed — `passes` equality follows directly from `sem` equality by `congr 1`.

---

## Updated closure plan

1. ~~Solve `T5_2_representation_bound`~~ — **DONE** (hypothesis fixes + proof).
2. ~~Solve `T5_5_irreducible_positive_interaction`~~ — **DONE** (hypothesis fixes + proof).

Modular Phase 5 has reached parity with the combined corpus proof-completeness level.
