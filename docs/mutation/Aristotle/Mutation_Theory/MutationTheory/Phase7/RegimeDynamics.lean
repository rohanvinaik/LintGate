import MutationTheory.Phase7.Internal.T7_13

noncomputable section

/-! Phase 7.13 dynamic regime-transition layer. -/

namespace MutationTheory
namespace Phase7
namespace RegimeDynamics

abbrev Regime := Phase7_T7_13.Regime
abbrev currentRegime := @Phase7_T7_13.currentRegime
abbrev SpecificationTrajectory := @Phase7_T7_13.SpecificationTrajectory
abbrev transitionPoint := @Phase7_T7_13.transitionPoint

/-- T7.13 dynamic regime transition theorem. -/
abbrev dynamicRegimeTransition := @Phase7_T7_13.T7_13_dynamic_regime_transition

/-- T7.13 renamed variant in the source corpus. -/
abbrev dynamicRegimeTransitionV2 := @Phase7_T7_13.T7_13_dynamic_regime_transition_v2

end RegimeDynamics
end Phase7
end MutationTheory
