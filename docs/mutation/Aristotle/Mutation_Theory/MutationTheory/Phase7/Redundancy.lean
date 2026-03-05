import MutationTheory.Phase7.Internal.T7_11

noncomputable section

/-! Phase 7.11 redundancy-detection layer. -/

namespace MutationTheory
namespace Phase7
namespace Redundancy

abbrev isFullSC := @Phase7_T7_11.Phase7_T7_9.is_full_SC
abbrev survivors := @Phase7_T7_11.Phase7_T7_9.survivors
abbrev killedMutants := @Phase7_T7_11.Phase7_T7_9.killed_mutants
abbrev specificationRedundant := @Phase7_T7_11.Phase7_T7_9.specification_redundant

abbrev isGreedyTrajectory := @Phase7_T7_11.Phase7_T7_9.is_greedy_trajectory
abbrev redundancyCount := @Phase7_T7_11.Phase7_T7_9.redundancy_count
abbrev redundancyRatio := @Phase7_T7_11.Phase7_T7_9.redundancy_ratio

/-- T7.11 redundancy iff zero information gain. -/
abbrev redundantIffZeroInformation := @Phase7_T7_11.Phase7_T7_9.redundant_iff_zero_information

/-- T7.11 greedy trajectories have zero redundancy ratio. -/
abbrev greedyRedundancyRatioZero := @Phase7_T7_11.Phase7_T7_9.greedy_redundancy_ratio_zero

/-- T7.11 full coverage implies any additional test is redundant. -/
abbrev redundantOfFullCoverage := @Phase7_T7_11.Phase7_T7_9.redundant_of_full_coverage

/-- T7.11 after full coverage, subsequent tests are redundant. -/
abbrev redundantAfterFullCoverage := @Phase7_T7_11.Phase7_T7_9.redundant_after_full_coverage

/-- T7.11 lower bound on redundant-test count after full coverage. -/
abbrev redundancyCountLowerBound := @Phase7_T7_11.Phase7_T7_9.redundancy_count_lower_bound

/-- T7.11 asymptotic lower bound on redundancy ratio. -/
abbrev redundancyRatioLimitBound := @Phase7_T7_11.Phase7_T7_9.redundancy_ratio_limit_bound

abbrev achievesFullCoverageInfinite := @Phase7_T7_11.Phase7_T7_9.achieves_full_coverage_infinite

/-- T7.11 eventual redundancy ratio limit under full-coverage trajectories. -/
abbrev redundancyRatioLimitEqOne := @Phase7_T7_11.Phase7_T7_9.redundancy_ratio_limit_eq_one

end Redundancy
end Phase7
end MutationTheory
