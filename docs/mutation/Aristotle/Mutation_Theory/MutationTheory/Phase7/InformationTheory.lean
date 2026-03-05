import MutationTheory.Phase7.Internal.Backbone

noncomputable section

/-! Information-theoretic view of specification trajectories. -/

namespace MutationTheory
namespace Phase7

/-- Greedy per-step information lower bound. -/
abbrev greedyInformationContent := @Phase7_T7_9.greedy_information_content

/-- Finset-form greedy information lower bound. -/
abbrev greedyInformationGainLowerBound := @Phase7_T7_9.greedy_information_gain_lower_bound_final

/-- Information content is nonnegative for nonexpanding survivor sets. -/
abbrev informationContentNonneg := @Phase7_T7_9.information_content_nonneg

/-- Telescoping log-sum identity used by total information bounds. -/
abbrev sumLogbTelescope := @Phase7_T7_9.sum_logb_telescope

/-- Total information needed to reduce survivor count from `n` to `1`. -/
abbrev totalInformationContent := @Phase7_T7_9.total_information_content

/-- Zero information iff no survivor reduction. -/
abbrev informationContentZeroIff := @Phase7_T7_9.information_content_zero_iff

end Phase7
end MutationTheory
