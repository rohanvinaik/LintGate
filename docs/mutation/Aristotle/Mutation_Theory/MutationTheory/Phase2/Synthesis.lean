import MutationTheory.Core.Definitions
import MutationTheory.Phase1.Foundations

/-! Canonical Phase 2 module (iterative synthesis dynamics). -/

namespace MutationTheory
namespace Phase2

open Core

structure SynthState (D R : Type) where
  tests : TestSuite D R
  survivors : Finset (Program D R)

structure SynthOperator (D R : Type) where
  step : SynthState D R → SynthState D R
  tests_monotone : ∀ s, s.tests ⊆ (step s).tests
  surv_antitone : ∀ s, (step s).survivors ⊆ s.survivors

def iterSynth {D R : Type}
    (op : SynthOperator D R)
    (s₀ : SynthState D R)
    (n : Nat) : SynthState D R :=
  match n with
  | 0 => s₀
  | k + 1 => op.step (iterSynth op s₀ k)

/-- Paper reference: Phase 2, monotone test-growth chain. -/
theorem testsMonotoneChain
    {D R : Type}
    (op : SynthOperator D R)
    (s₀ : SynthState D R)
    (n m : Nat)
    (h : n ≤ m) :
    (iterSynth op s₀ n).tests ⊆ (iterSynth op s₀ m).tests := by
  induction' h with k hk ih
  · intro t ht
    exact ht
  · exact Set.Subset.trans ih (op.tests_monotone _)

/-- Paper reference: Phase 2, antitone survivor chain. -/
theorem survivorsAntitoneChain
    {D R : Type}
    (op : SynthOperator D R)
    (s₀ : SynthState D R)
    (n m : Nat)
    (h : n ≤ m) :
    (iterSynth op s₀ m).survivors ⊆ (iterSynth op s₀ n).survivors := by
  induction' h with k hk ih
  · intro x hx
    exact hx
  · exact Set.Subset.trans (op.surv_antitone _) ih

/-- Paper reference: Phase 2, finite-step survivor stabilization bound. -/
theorem terminationBound
    {D R : Type}
    (op : SynthOperator D R)
    (s₀ : SynthState D R) :
    ∃ N, N ≤ s₀.survivors.card ∧
      (iterSynth op s₀ (N + 1)).survivors = (iterSynth op s₀ N).survivors := by
  by_contra h
  push_neg at h
  have h_card_decr :
      ∀ n ≤ s₀.survivors.card,
        (iterSynth op s₀ n).survivors.card >
          (iterSynth op s₀ (n + 1)).survivors.card := by
    intro n hn
    specialize h n hn
    have h_ssub :
        (iterSynth op s₀ (n + 1)).survivors ⊂ (iterSynth op s₀ n).survivors :=
      lt_of_le_of_ne (op.surv_antitone _) h
    exact Finset.card_lt_card h_ssub
  have h_card_decr_seq :
      ∀ n ≤ s₀.survivors.card,
        (iterSynth op s₀ n).survivors.card ≤ s₀.survivors.card - n := by
    intro n hn
    induction n with
    | zero =>
        simp [iterSynth]
    | succ n ih =>
        exact Nat.le_sub_one_of_lt
          (lt_of_lt_of_le
            (h_card_decr n (Nat.le_of_succ_le hn))
            (ih (Nat.le_of_succ_le hn)))
  specialize h_card_decr_seq s₀.survivors.card le_rfl
  specialize h_card_decr s₀.survivors.card le_rfl
  omega

/-- Paper reference: Phase 2, composition strength bound. -/
theorem compositionStrengthBound
    (level_g level_h : Phase1.OracleLevel) :
    min level_g level_h ≤ level_g ∧ min level_g level_h ≤ level_h := by
  exact ⟨min_le_left _ _, min_le_right _ _⟩

end Phase2
end MutationTheory
