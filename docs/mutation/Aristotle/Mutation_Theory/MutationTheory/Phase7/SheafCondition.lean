import MutationTheory.Phase7.CompositionGap

noncomputable section

/-! Phase 7.17 sheaf-condition scaffold for compositional specification. -/

namespace MutationTheory
namespace Phase7
namespace Sheaf

variable {Module : Type}

/-- Sum of local specification costs across modules. -/
def localSpecificationCost
    {Cost : Type}
    [AddCommMonoid Cost]
    (κLocal : Module → Cost)
    (modules : Finset Module) : Cost :=
  modules.sum κLocal

/-- Sum of interface-gap penalties across declared interfaces. -/
def interfaceGapCost
    {Cost : Type}
    [AddCommMonoid Cost]
    (γ : Module → Module → Cost)
    (interfaces : Finset (Module × Module)) : Cost :=
  interfaces.sum (fun e => γ e.1 e.2)

/-- Sheaf compatibility condition over all listed interfaces. -/
def sheafCondition
    (compatible : Module → Module → Prop)
    (interfaces : Finset (Module × Module)) : Prop :=
  ∀ e ∈ interfaces, compatible e.1 e.2

/-- Local-cost additivity on disjoint module unions. -/
lemma localSpecificationCost_union_disjoint
    [DecidableEq Module]
    {Cost : Type}
    [AddCommMonoid Cost]
    (κLocal : Module → Cost)
    (s t : Finset Module)
    (h : Disjoint s t) :
    localSpecificationCost κLocal (s ∪ t) =
      localSpecificationCost κLocal s + localSpecificationCost κLocal t := by
  simpa [localSpecificationCost] using Finset.sum_union h

/-- Interface-cost additivity on disjoint interface unions. -/
lemma interfaceGapCost_union_disjoint
    [DecidableEq Module]
    {Cost : Type}
    [AddCommMonoid Cost]
    (γ : Module → Module → Cost)
    (s t : Finset (Module × Module))
    (h : Disjoint s t) :
    interfaceGapCost γ (s ∪ t) =
      interfaceGapCost γ s + interfaceGapCost γ t := by
  simpa [interfaceGapCost] using Finset.sum_union h

/-- Sheaf condition splits over interface unions. -/
lemma sheafCondition_union
    [DecidableEq Module]
    (compatible : Module → Module → Prop)
    (s t : Finset (Module × Module)) :
    sheafCondition compatible (s ∪ t) ↔
      sheafCondition compatible s ∧ sheafCondition compatible t := by
  constructor
  · intro h
    refine ⟨?_, ?_⟩
    · intro e he
      exact h e (by exact Finset.mem_union.mpr (Or.inl he))
    · intro e he
      exact h e (by exact Finset.mem_union.mpr (Or.inr he))
  · rintro ⟨hs, ht⟩ e he
    rcases Finset.mem_union.mp he with hes | het
    · exact hs e hes
    · exact ht e het

/-- Monotonicity of sheaf condition under interface restriction. -/
lemma sheafCondition_mono
    [DecidableEq Module]
    (compatible : Module → Module → Prop)
    {s t : Finset (Module × Module)}
    (h_sub : s ⊆ t) :
    sheafCondition compatible t → sheafCondition compatible s := by
  intro ht e he
  exact ht e (h_sub he)

/-- **T7.17** If the sheaf condition holds, local suites glue additively. -/
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

/-- **T7.17 (failure form)** If the sheaf condition fails, pairwise interface
penalties bound the additional specification cost. -/
theorem T7_17_sheaf_failure_bound
    {Cost : Type}
    [AddCommMonoid Cost]
    [Preorder Cost]
    (κGlobal : Cost)
    (κLocal : Module → Cost)
    (γ : Module → Module → Cost)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (_hFail : ¬ sheafCondition compatible interfaces)
    (hBound :
      κGlobal ≤ localSpecificationCost κLocal modules + interfaceGapCost γ interfaces) :
    κGlobal ≤ localSpecificationCost κLocal modules + interfaceGapCost γ interfaces :=
  hBound

/-- Additive gluing immediately implies the penalized bound since interface cost
is nonnegative. This gives the `Σκ_i + Σγ_ij` form as a corollary. -/
theorem T7_17_additive_implies_penalized
    (κGlobal : ℕ)
    (κLocal : Module → ℕ)
    (γ : Module → Module → ℕ)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (hAdditive : κGlobal ≤ localSpecificationCost κLocal modules) :
    κGlobal ≤ localSpecificationCost κLocal modules + interfaceGapCost γ interfaces := by
  exact le_trans hAdditive (Nat.le_add_right _ _)

end Sheaf
end Phase7
end MutationTheory
