import MutationTheory.Phase7.Internal.T7_14

noncomputable section

/-! Phase 7.14 regime-transition/decomposition interface layer. -/

namespace MutationTheory
namespace Phase7
namespace RegimeDecomposition

abbrev supportsRegimeA := @Phase7_T7_14.supports_Regime_A
abbrev forcesRegimeB := @Phase7_T7_14.forces_Regime_B
abbrev separatedMutants := @Phase7_T7_14.separated_mutants
abbrev iAB := @Phase7_T7_14.i_AB

/-- T7.14 regime transition determines decomposition point. -/
abbrev regimeTransition := @Phase7_T7_14.T7_14_Regime_Transition

end RegimeDecomposition
end Phase7
end MutationTheory
