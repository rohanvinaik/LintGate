import MutationTheory.Phase7.Internal.T7_10

noncomputable section

/-! Phase 7.10 free-energy and information-gain layer. -/

namespace MutationTheory
namespace Phase7
namespace FreeEnergy

abbrev informationContent := @Phase7_T7_10.information_content
abbrev informationGain := @Phase7_T7_10.information_gain

/-- T7.10 greedy per-step information lower bound. -/
abbrev greedyInformationContent := @Phase7_T7_10.greedy_information_content

/-- T7.10 finite-set form of the greedy information lower bound. -/
abbrev greedyInformationGainLowerBound := @Phase7_T7_10.greedy_information_gain_lower_bound_final

/-- T7.10 information content is nonnegative for nonexpanding survivor sets. -/
abbrev informationContentNonneg := @Phase7_T7_10.information_content_nonneg

/-- T7.10 telescoping log identity. -/
abbrev sumLogbTelescope := @Phase7_T7_10.sum_logb_telescope

/-- T7.10 total information needed to reduce survivors from `n` to `1`. -/
abbrev totalInformationContent := @Phase7_T7_10.total_information_content

/-- T7.10 zero information iff no survivor reduction. -/
abbrev informationContentZeroIff := @Phase7_T7_10.information_content_zero_iff

abbrev specificationFreeEnergy := @Phase7_T7_10.specification_free_energy

/-- T7.10 specification free-energy descent bound. -/
abbrev specificationFreeEnergyBound := @Phase7_T7_10.specification_free_energy_bound

abbrev specFreeEnergy := @Phase7_T7_10.spec_free_energy
abbrev specFreeEnergyBound := @Phase7_T7_10.spec_free_energy_bound

end FreeEnergy
end Phase7
end MutationTheory
