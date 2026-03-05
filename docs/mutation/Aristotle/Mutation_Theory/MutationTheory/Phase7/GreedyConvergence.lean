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

/-- T7.6 convergence-rate characterization via harmonic greedy bound. -/
abbrev convergenceRateDeterminesKappa := @Phase7_T7_6.T5_12_greedy_approximation_ratio

/-- Greedy set-cover style approximation ratio used by Phase 7. -/
abbrev greedyApproximationRatio := @Phase7_T7_7.T5_12_greedy_approximation_ratio

/-- Greedy-vs-random trajectory comparison theorem (Section 3/4 bridge). -/
abbrev trajectoryComparison := @Phase7_T7_7.T7_7_trajectory_comparison

end Phase7
end MutationTheory
