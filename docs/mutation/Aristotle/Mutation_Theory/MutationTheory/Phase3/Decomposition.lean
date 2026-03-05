import Mathlib

/-! Canonical Phase 3 module (decomposition and complexity structure). -/

namespace MutationTheory
namespace Phase3

/-- Paper reference: Phase 3, separable independent testing lemma. -/
theorem separableIndependentTesting
    {D₁ D₂ R₁ R₂ R : Type}
    (g₁ g₁' : D₁ → R₁)
    (g₂ : D₂ → R₂)
    (h : R₁ → R₂ → R)
    (x_star : D₁)
    (x₂ : D₂)
    (h_diff : g₁ x_star ≠ g₁' x_star)
    (h_inj : ∀ (r₁ r₁' : R₁) (r₂ : R₂), h r₁ r₂ = h r₁' r₂ → r₁ = r₁') :
    h (g₁ x_star) (g₂ x₂) ≠ h (g₁' x_star) (g₂ x₂) := by
  intro hEq
  exact h_diff (h_inj _ _ _ hEq)

/-- Paper reference: Phase 3, product-vs-sum inequality for factors > 1. -/
theorem productVsSum
    (a b : Nat)
    (ha : 1 < a)
    (hb : 1 < b) :
    a + b ≤ a * b := by
  nlinarith

/-- Paper reference: Phase 3, positive interaction strictly increases burden. -/
theorem decompositionReducesBurden
    (survA survB interaction : ℚ)
    (h_interaction : 0 < interaction) :
    survA + survB < survA + survB + interaction := by
  linarith

/-- Paper reference: Phase 3, universal quantification is stronger than finite coverage. -/
theorem universalStrongerThanFinite
    {α : Type}
    (P : α → Prop)
    (S : Set α)
    (_h_finite : S.Finite)
    (h_holds_on_S : ∀ x ∈ S, P x)
    (h_not_universal : ¬ ∀ x, P x) :
    ∃ x ∉ S, ¬ P x := by
  rcases not_forall.mp h_not_universal with ⟨x, hxNP⟩
  by_cases hxS : x ∈ S
  · exact False.elim (hxNP (h_holds_on_S x hxS))
  · exact ⟨x, hxS, hxNP⟩

end Phase3
end MutationTheory
