import MutationTheory.Phase7.Internal.T7_16

noncomputable section

/-! Phase 7.15/7.16 composition-gap and interface-mutant layer. -/

namespace MutationTheory
namespace Phase7
namespace CompositionGap

abbrev Program := @Phase7_T7_16.Program
abbrev kills := @Phase7_T7_16.kills
abbrev isTestSuite := @Phase7_T7_16.is_test_suite
abbrev kappa := @Phase7_T7_16.kappa
abbrev gamma := @Phase7_T7_16.gamma
abbrev InterfaceMutants := @Phase7_T7_16.InterfaceMutants

/-- T7.15 nonnegativity of the composition gap `γ`. -/
abbrev gammaNonneg := @Phase7_T7_16.gamma_nonneg

/-- T7.15 zero-gap condition for independent interfaces. -/
abbrev gammaZeroIfIndependent := @Phase7_T7_16.gamma_zero_if_independent

/-- T7.15 gap bounded by total composition complexity. -/
abbrev gammaLeTotal := @Phase7_T7_16.gamma_le_total

/-- T7.15 composition-gap upper bound theorem. -/
abbrev compositionGap := @Phase7_T7_16.composition_gap

/-- T7.16 interface-mutant cardinality upper bound on `γ`. -/
abbrev gammaLeInterfaceMutantsCard := @Phase7_T7_16.gamma_le_interface_mutants_card

end CompositionGap
end Phase7
end MutationTheory
