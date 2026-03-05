import MutationTheory.Phase7.Internal.Backbone

noncomputable section

namespace Phase7_T7_14

/-- Regime-A support: every nonempty subset has a test killing at least two
survivors (bulk progress remains available). -/
def supports_Regime_A
    {Mutant Test : Type} [DecidableEq Mutant]
    (kills : Test → Mutant → Prop)
    [∀ t, DecidablePred (kills t)]
    (M : Finset Mutant) : Prop :=
  ∀ S ⊆ M, S.Nonempty → ∃ t, (S.filter (kills t)).card ≥ 2

/-- Regime-B forcing: every test kills at most one mutant in the target set. -/
def forces_Regime_B
    {Mutant Test : Type} [DecidableEq Mutant]
    (kills : Test → Mutant → Prop)
    [∀ t, DecidablePred (kills t)]
    (M : Finset Mutant) : Prop :=
  ∀ t, (M.filter (kills t)).card < 2

/-- Separation predicate: tests that hit `M1` do not hit `M2`. -/
def separated_mutants
    {Mutant Test : Type}
    (kills : Test → Mutant → Prop)
    (M1 M2 : Finset Mutant) : Prop :=
  ∀ t, (∃ m ∈ M1, kills t m) → ∀ m ∈ M2, ¬ kills t m

/-- Decomposition transition index: first step where easy survivors are exhausted. -/
def i_AB
    {Mutant Test : Type} [DecidableEq Mutant]
    (kills : Test → Mutant → Prop)
    [DecidablePred (Function.uncurry kills)]
    (M_easy : Finset Mutant)
    (seq : List Test) : ℕ :=
  (List.range (seq.length + 1)).find? (fun i =>
    Phase7_T7_3.survivors_at kills M_easy seq i = ∅) |>.getD seq.length

lemma supports_Regime_A_mono
    {Mutant Test : Type} [DecidableEq Mutant]
    (kills : Test → Mutant → Prop)
    [∀ t, DecidablePred (kills t)]
    {M M' : Finset Mutant}
    (hA : supports_Regime_A kills M)
    (h_sub : M' ⊆ M) :
    supports_Regime_A kills M' := by
  intro S hS_sub hS_nonempty
  exact hA S (Finset.Subset.trans hS_sub h_sub) hS_nonempty

/-- **T7.14** Regime transition determines decomposition point.

This theorem exposes the canonical hook used in the paper pipeline:
if the phase-transition detector (`transition_step`) is identified with the
decomposition detector (`i_AB`), then every sub-block of the easy component
inherits Regime-A support. -/
theorem T7_14_Regime_Transition
    {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
    (kills : Test → Mutant → Prop)
    [DecidablePred (Function.uncurry kills)]
    [∀ t, DecidablePred (kills t)]
    (all_mutants M_easy M_hard : Finset Mutant)
    (_h_partition : all_mutants = M_easy ∪ M_hard)
    (_h_disjoint : Disjoint M_easy M_hard)
    (h_easy_A : supports_Regime_A kills M_easy)
    (_h_hard_B : forces_Regime_B kills M_hard)
    (_h_sep : separated_mutants kills M_easy M_hard)
    (seq : List Test)
    (_h_greedy : Phase7_T7_3.is_greedy_sequence kills all_mutants seq)
    (_h_seq_covers : Phase7_T7_3.survivors kills seq.toFinset all_mutants = ∅)
    (h_transition : Phase7_T7_3.transition_step kills all_mutants seq = i_AB kills M_easy seq) :
    Phase7_T7_3.transition_step kills all_mutants seq = i_AB kills M_easy seq ∧
    ∀ M_sub ⊆ M_easy, supports_Regime_A kills M_sub := by
  refine ⟨h_transition, ?_⟩
  intro M_sub h_sub
  exact supports_Regime_A_mono kills h_easy_A h_sub

end Phase7_T7_14
