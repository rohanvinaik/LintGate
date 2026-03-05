import MutationTheory.Phase7.Internal.Backbone

noncomputable section

/-! Phase-transition analysis for greedy kill trajectories. -/

namespace MutationTheory
namespace Phase7

/-- Per-step kill-rate lower bound for greedy traces. -/
abbrev greedyKillRateLowerBound := @Phase7_T7_3.greedy_kill_rate_lower_bound

/-- Structural properties of the transition step. -/
abbrev transitionStepProperties := @Phase7_T7_3.transition_step_properties

/-- Transition step either hits sequence end or is a genuine `< 2` drop. -/
abbrev transitionStepEqLengthOrDrop := @Phase7_T7_3.transition_step_eq_length_or_drop

/-- Maximal-prefix characterization for the `≥ 2` regime. -/
abbrev transitionStepMaximalPrefix := @Phase7_T7_3.transition_step_maximal_prefix

/-- Main phase-transition theorem (explicit transition-step witness). -/
abbrev phaseTransition := @Phase7_T7_3.phase_transition

end Phase7
end MutationTheory
