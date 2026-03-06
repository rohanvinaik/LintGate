import MutationTheory.Core.Definitions
import MutationTheory.Phase7.Internal.Backbone

noncomputable section

/-! Phase 7 greedy convergence and approximation results. -/

namespace MutationTheory
namespace Phase7

/-- **Theorem 3.1** (Proportional progress).
At a greedy step with nonempty survivor set, some test kills at least a
`1/κ` fraction of survivors.
Paper reference: Section 3.2. -/
abbrev greedyProportionalProgress := @Phase7_T7_1.greedy_proportional_progress

/-- Exponential decay of survivor count under proportional progress. -/
abbrev exponentialDecayOfSurvivors := @Phase7_T7_2.exponential_decay_of_survivors

/-- Greedy trajectory stability bound from Section 3. -/
abbrev greedyStabilityBoundFinal := @Phase7_T7_4.greedy_stability_bound_final

/-- Witness set for “full specification reached at trajectory length `n`”. -/
def reachesFullSpecificationAtLength
    {D R : Type}
    (sem : Core.Program D R → D → R)
    (oracle : Core.Test D R → R → Bool)
    (MS : Core.MutationSystem D R)
    (P : Core.Program D R)
    (n : ℕ) : Prop :=
  ∃ T : Core.TestSuite D R, T.card = n ∧ Core.achievesFullSC' sem oracle MS P T

/-- Minimal trajectory length to full specification (exact notion). -/
def minimalConvergenceLength
    {D R : Type}
    (sem : Core.Program D R → D → R)
    (oracle : Core.Test D R → R → Bool)
    (MS : Core.MutationSystem D R)
    (P : Core.Program D R) : ℕ :=
  sInf { n | reachesFullSpecificationAtLength sem oracle MS P n }

/-- **T7.6** Convergence-rate characterization of specification complexity.

`κ` is exactly the minimum trajectory length needed to reach full SC. -/
theorem convergenceRateDeterminesKappa
    {D R : Type}
    (sem : Core.Program D R → D → R)
    (oracle : Core.Test D R → R → Bool)
    (MS : Core.MutationSystem D R)
    (P : Core.Program D R) :
    Core.specComplexity sem oracle MS P =
      minimalConvergenceLength sem oracle MS P := by
  rfl

/-- Harmonic/log greedy upper bound used by T7.6 and T7.7. -/
abbrev convergenceRateGreedyBound := @Phase7_T7_6.T5_12_greedy_approximation_ratio

/-- Greedy set-cover style approximation ratio used by Phase 7. -/
abbrev greedyApproximationRatio := @Phase7_T7_7.T5_12_greedy_approximation_ratio

/-- Base comparison theorem from the internal backbone. -/
abbrev trajectoryComparisonBase := @Phase7_T7_7.T7_7_trajectory_comparison

/-- **T7.7** Greedy-vs-random trajectory comparison with explicit ratio lower bound.

The third clause gives a concrete constant-factor form of the paper’s
`Ω(1 / (κ · δ_min · H(n)))` statement. -/
theorem trajectoryComparison
    {D R : Type} [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Phase7_T7_7.Program D R → D → R)
    (oracle : Phase7_T7_7.Test D R → R → Bool)
    (MS : Phase7_T7_7.MutationSystem D R)
    (P : Phase7_T7_7.Program D R)
    (ρ : D → ℝ)
    (h_dist : ∀ d, 0 ≤ ρ d)
    (h_sum : Finset.univ.sum ρ = 1)
    (T_univ : Phase7_T7_7.TestSuite D R)
    (seq : ℕ → Phase7_T7_7.Test D R)
    (h_greedy : Phase7_T7_7.IsGreedySequence_P5 sem oracle MS P T_univ seq)
    (k_greedy : ℕ)
    (h_first_cover : Phase7_T7_7.IsFirstCoverIndex_P5 sem oracle MS P seq k_greedy)
    (T_opt : Phase7_T7_7.TestSuite D R)
    (h_opt : Phase7_T7_7.achievesFullSC' sem oracle MS P T_opt)
    (h_step_decay :
      ∀ i,
        ((Phase7_T7_7.survivorsByPrefix_P5 sem oracle MS P seq (i + 1)).card : ℝ) ≤
          (Phase7_T7_7.survivorsByPrefix_P5 sem oracle MS P seq i).card *
            (1 - 1 / (T_opt.card : ℝ)))
    (n : ℕ)
    (h_n : n = (Phase7_T7_7.nonEquivMutants sem MS P).card)
    (h_n_pos : 0 < n)
    (h_nonempty : (Phase7_T7_7.nonEquivMutants sem MS P).Nonempty)
    (h_pos : 0 < Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)
    (h_k_greedy_pos : 0 < k_greedy)
    (κ : ℕ)
    (h_opt_card_le_κ : T_opt.card ≤ κ) :
    (k_greedy : ℝ) ≤ T_opt.card * (Real.log n + 1) ∧
    Nat.ceil (1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) ≤
      Phase7_T7_7.criticalSampleSize sem oracle MS P ρ ∧
    ((1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) /
      ((κ : ℝ) * (Real.log n + 1)) ≤
      (Phase7_T7_7.criticalSampleSize sem oracle MS P ρ : ℝ) / k_greedy) := by
  rcases trajectoryComparisonBase sem oracle MS P ρ h_dist h_sum
      T_univ seq h_greedy k_greedy h_first_cover T_opt h_opt h_step_decay n h_n h_n_pos
      h_nonempty h_pos h_k_greedy_pos with ⟨h_greedy_bound, h_random_lb, _h_nonneg_ratio⟩
  refine ⟨h_greedy_bound, h_random_lb, ?_⟩

  have hk_pos : 0 < (k_greedy : ℝ) := by
    exact_mod_cast h_k_greedy_pos

  have h_n_ge_one : (1 : ℝ) ≤ n := by
    exact_mod_cast (Nat.succ_le_of_lt h_n_pos)
  have h_log_nonneg : 0 ≤ Real.log n := Real.log_nonneg h_n_ge_one
  have h_log_plus_one_pos : 0 < Real.log n + 1 := by
    linarith

  have h_opt_card_le_κr : (T_opt.card : ℝ) ≤ κ := by
    exact_mod_cast h_opt_card_le_κ

  have h_kgreedy_upper : (k_greedy : ℝ) ≤ (κ : ℝ) * (Real.log n + 1) := by
    refine le_trans h_greedy_bound ?_
    exact mul_le_mul_of_nonneg_right h_opt_card_le_κr (le_of_lt h_log_plus_one_pos)

  have h_inv : (1 / ((κ : ℝ) * (Real.log n + 1))) ≤ 1 / (k_greedy : ℝ) := by
    exact one_div_le_one_div_of_le hk_pos h_kgreedy_upper

  have h_num_le_critical :
      (1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) ≤
      (Phase7_T7_7.criticalSampleSize sem oracle MS P ρ : ℝ) := by
    refine le_trans (Nat.le_ceil _) ?_
    exact_mod_cast h_random_lb

  have h_num_nonneg :
      0 ≤ (1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) := by
    have h_den_pos : 0 < 2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty := by
      nlinarith [h_pos]
    exact le_of_lt (one_div_pos.mpr h_den_pos)

  have h_scaled :
      ((1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) /
        ((κ : ℝ) * (Real.log n + 1))) ≤
      ((1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) /
        (k_greedy : ℝ)) := by
    have h_mul := mul_le_mul_of_nonneg_left h_inv h_num_nonneg
    simpa [div_eq_mul_inv, mul_comm, mul_left_comm, mul_assoc] using h_mul

  have h_from_critical :
      ((1 / (2 * Phase7_T7_7.minDisagreement sem ρ MS P h_nonempty)) /
        (k_greedy : ℝ)) ≤
      (Phase7_T7_7.criticalSampleSize sem oracle MS P ρ : ℝ) / k_greedy := by
    exact div_le_div_of_nonneg_right h_num_le_critical (le_of_lt hk_pos)

  exact le_trans h_scaled h_from_critical

end Phase7
end MutationTheory
