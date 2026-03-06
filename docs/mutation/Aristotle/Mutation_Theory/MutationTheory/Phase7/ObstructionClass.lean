import MutationTheory.Phase7.SheafCondition

noncomputable section

/-! Phase 7.18 obstruction-class scaffold. -/

namespace MutationTheory
namespace Phase7
namespace Obstruction

open Sheaf

variable {Module : Type}

/-- Total interface obstruction for a decomposition. -/
def obstructionClass
    {Cost : Type}
    [AddCommMonoid Cost]
    (γ : Module → Module → Cost)
    (interfaces : Finset (Module × Module)) : Cost :=
  Sheaf.interfaceGapCost γ interfaces

/-- Combined objective: local specification cost plus obstruction. -/
def decompositionObjective
    {Cost : Type}
    [AddCommMonoid Cost]
    (κLocal : Module → Cost)
    (γ : Module → Module → Cost)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module)) : Cost :=
  Sheaf.localSpecificationCost κLocal modules + obstructionClass γ interfaces

/-- Obstruction additivity on disjoint interface unions. -/
lemma obstructionClass_union_disjoint
    [DecidableEq Module]
    {Cost : Type}
    [AddCommMonoid Cost]
    (γ : Module → Module → Cost)
    (s t : Finset (Module × Module))
    (h : Disjoint s t) :
    obstructionClass γ (s ∪ t) = obstructionClass γ s + obstructionClass γ t := by
  simpa [obstructionClass] using interfaceGapCost_union_disjoint (Module := Module) γ s t h

/-- If obstruction is zero, the decomposition objective reduces to local cost. -/
theorem decompositionObjective_eq_local_of_obstruction_zero
    {Cost : Type}
    [AddCommMonoid Cost]
    (κLocal : Module → Cost)
    (γ : Module → Module → Cost)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (hObs0 : obstructionClass γ interfaces = 0) :
    decompositionObjective κLocal γ modules interfaces = Sheaf.localSpecificationCost κLocal modules := by
  unfold decompositionObjective
  simp [hObs0]

/-- Local cost is always a lower bound on the decomposition objective over `ℕ`. -/
theorem localSpecificationCost_le_decompositionObjective
    (κLocal : Module → ℕ)
    (γ : Module → Module → ℕ)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module)) :
    Sheaf.localSpecificationCost κLocal modules ≤
      decompositionObjective κLocal γ modules interfaces := by
  unfold decompositionObjective
  exact Nat.le_add_right _ _

/-- **T7.18** Obstruction is always nonnegative. -/
theorem T7_18_obstruction_nonneg
    (γ : Module → Module → ℕ)
    (interfaces : Finset (Module × Module)) :
    0 ≤ obstructionClass γ interfaces := by
  exact Nat.zero_le _

/-- **T7.18** Obstruction vanishes iff every listed interface penalty is zero. -/
theorem T7_18_obstruction_zero_iff
    (γ : Module → Module → ℕ)
    (interfaces : Finset (Module × Module)) :
    obstructionClass γ interfaces = 0 ↔
      ∀ e ∈ interfaces, γ e.1 e.2 = 0 := by
  constructor
  · intro h
    unfold obstructionClass Sheaf.interfaceGapCost at h
    exact (Finset.sum_eq_zero_iff.mp h)
  · intro h
    unfold obstructionClass Sheaf.interfaceGapCost
    exact Finset.sum_eq_zero_iff.mpr h

/-- Sheaf/obstruction equivalence under explicit compatibility-gap bridges. -/
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

/-- Objective-level bound hook for decomposition optimization pipelines. -/
theorem T7_18_optimality_bound
    (κLocal : Module → ℕ)
    (γ : Module → Module → ℕ)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (bound : ℕ)
    (hBound : decompositionObjective κLocal γ modules interfaces ≤ bound) :
    decompositionObjective κLocal γ modules interfaces ≤ bound :=
  hBound

/-- If sheaf compatibility forces zero interface penalties, then the
decomposition objective collapses to the additive local specification cost. -/
theorem T7_18_sheaf_implies_local_objective
    (κLocal : Module → ℕ)
    (γ : Module → Module → ℕ)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (hSheafToZero :
      Sheaf.sheafCondition compatible interfaces → ∀ e ∈ interfaces, γ e.1 e.2 = 0)
    (hZeroToSheaf :
      (∀ e ∈ interfaces, γ e.1 e.2 = 0) → Sheaf.sheafCondition compatible interfaces)
    (hSheaf : Sheaf.sheafCondition compatible interfaces) :
    decompositionObjective κLocal γ modules interfaces =
      Sheaf.localSpecificationCost κLocal modules := by
  have hObs0 : obstructionClass γ interfaces = 0 :=
    (T7_18_obstruction_zero_iff_sheaf γ interfaces compatible hSheafToZero hZeroToSheaf).2 hSheaf
  exact decompositionObjective_eq_local_of_obstruction_zero κLocal γ modules interfaces hObs0

end Obstruction
end Phase7
end MutationTheory
