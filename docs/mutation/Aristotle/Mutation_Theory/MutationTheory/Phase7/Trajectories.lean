import MutationTheory.Phase7.Internal.Backbone

noncomputable section

/-! Phase 7 trajectory primitives and monotonicity interface. -/

namespace MutationTheory
namespace Phase7

abbrev SpecificationTrajectory
    (Program Mutant Test Semantics Oracle MutationSystem : Type) :=
  @Phase7_T7_0.SpecificationTrajectory Program Mutant Test Semantics Oracle MutationSystem

abbrev MutationContext (D R : Type) := Phase7_T7_5.MutationContext D R

/-- Paper reference: Section 3, trajectory monotonicity. -/
abbrev killedTrajectoryMono := @Phase7_T7_0.killed_trajectory_mono

/-- Paper reference: Section 3, incremental-kill decomposition. -/
abbrev killIncrementEqCardSdiff := @Phase7_T7_0.kill_increment_eq_card_sdiff

/-- Paper reference: Section 3, survivor cardinality identity. -/
abbrev survivorSetCard := @Phase7_T7_0.survivor_set_card

/-- Paper reference: Section 3, fixed-point criterion (`survivors = ∅`). -/
abbrev survivorSetEmptyIffKilledEqAll := @Phase7_T7_0.survivor_set_empty_iff_killed_eq_all

/-- Paper reference: Section 3.3, Lyapunov monotonicity. -/
abbrev lyapunovMonotonicity := @Phase7_T7_5.lyapunov_monotonicity

/-- Paper reference: Section 3.3, entropy monotonicity. -/
abbrev entropyMonotonicity := @Phase7_T7_8.entropy_monotonicity

/-- Paper reference: Section 3.3, entropy endpoint properties. -/
abbrev entropyProperties := @Phase7_T7_8.entropy_properties

end Phase7
end MutationTheory
