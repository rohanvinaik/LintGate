import MutationTheory.Phase7.Internal.T7_12

noncomputable section

namespace Phase7_T7_13

abbrev MutationContext := Phase7_T7_12.MutationContext
abbrev spectrumMin := @Phase7_T7_12.spectrumMin

/-- Effective specification regime: `A` (polynomially visible survivors) or
`B` (hard tail with tiny disagreement mass). -/
inductive Regime
| A
| B
deriving DecidableEq, Repr

/-- Regime classifier from the local disagreement spectrum minimum.

`threshold` is the phase boundary (typically `1 / poly(|P|)`). -/
def currentRegime
    {D R : Type} [Fintype D] [DecidableEq R]
    (ctx : MutationContext D R)
    (T : Set D)
    (ρ : D → ℝ)
    (h_finite : ctx.MS.Finite)
    (threshold : ℝ) : Regime :=
  if spectrumMin ctx T ρ h_finite ≥ (threshold : WithTop ℝ) then Regime.A else Regime.B

/-- A trajectory is an increasing sequence of test sets. -/
structure SpecificationTrajectory (D : Type) where
  tests : ℕ → Set D
  mono : ∀ i, tests i ⊆ tests (i + 1)
  empty_start : tests 0 = ∅

/-- First index where the trajectory enters Regime `B` (if any). -/
noncomputable def transitionPoint
    {D R : Type} [Fintype D] [DecidableEq R]
    (ctx : MutationContext D R)
    (traj : SpecificationTrajectory D)
    (ρ : D → ℝ)
    (h_finite : ctx.MS.Finite)
    (threshold : ℝ) : ℕ :=
  sInf { i | currentRegime ctx (traj.tests i) ρ h_finite threshold = Regime.B }

/-- **T7.13** Dynamic regime transition.

Before the first `B` index we remain in `A`; at the transition index (when it is
witnessed as `B`) the regime is `B`. -/
theorem T7_13_dynamic_regime_transition
    {D R : Type} [Fintype D] [DecidableEq R]
    (ctx : MutationContext D R)
    (traj : SpecificationTrajectory D)
    (ρ : D → ℝ)
    (h_finite : ctx.MS.Finite)
    (threshold : ℝ)
    (h_start_A : currentRegime ctx (traj.tests 0) ρ h_finite threshold = Regime.A) :
    let i_AB := transitionPoint ctx traj ρ h_finite threshold
    currentRegime ctx (traj.tests 0) ρ h_finite threshold = Regime.A ∧
    (∀ i < i_AB, currentRegime ctx (traj.tests i) ρ h_finite threshold = Regime.A) ∧
    (∀ i, i = i_AB →
      i_AB ∈ { k | currentRegime ctx (traj.tests k) ρ h_finite threshold = Regime.B } →
      currentRegime ctx (traj.tests i) ρ h_finite threshold = Regime.B) := by
  dsimp [transitionPoint]
  refine ⟨h_start_A, ?_, ?_⟩
  · intro i hi
    by_contra h_not_A
    have h_is_B : currentRegime ctx (traj.tests i) ρ h_finite threshold = Regime.B := by
      cases h_reg : currentRegime ctx (traj.tests i) ρ h_finite threshold <;>
        simp [h_reg] at h_not_A ⊢
    have h_iab_le_i : sInf {k | currentRegime ctx (traj.tests k) ρ h_finite threshold = Regime.B} ≤ i :=
      Nat.sInf_le (by simpa using h_is_B)
    exact (not_le_of_gt hi) h_iab_le_i
  · intro i hi hi_mem
    simpa [hi] using hi_mem

/-- Backward-compatible name from the scratch corpus. -/
theorem T7_13_dynamic_regime_transition_v2
    {D R : Type} [Fintype D] [DecidableEq R]
    (ctx : MutationContext D R)
    (traj : SpecificationTrajectory D)
    (ρ : D → ℝ)
    (h_finite : ctx.MS.Finite)
    (threshold : ℝ)
    (h_start_A : currentRegime ctx (traj.tests 0) ρ h_finite threshold = Regime.A) :
    let i_AB := transitionPoint ctx traj ρ h_finite threshold
    currentRegime ctx (traj.tests 0) ρ h_finite threshold = Regime.A ∧
    (∀ i < i_AB, currentRegime ctx (traj.tests i) ρ h_finite threshold = Regime.A) ∧
    (∀ i, i = i_AB →
      i_AB ∈ { k | currentRegime ctx (traj.tests k) ρ h_finite threshold = Regime.B } →
      currentRegime ctx (traj.tests i) ρ h_finite threshold = Regime.B) := by
  simpa using
    T7_13_dynamic_regime_transition ctx traj ρ h_finite threshold h_start_A

end Phase7_T7_13
