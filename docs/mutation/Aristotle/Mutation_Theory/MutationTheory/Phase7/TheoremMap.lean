import MutationTheory.Phase7.Trajectories
import MutationTheory.Phase7.GreedyConvergence
import MutationTheory.Phase7.PhaseTransition
import MutationTheory.Phase7.InformationTheory
import MutationTheory.Phase7.FreeEnergy
import MutationTheory.Phase7.Redundancy
import MutationTheory.Phase7.SpectrumEvolution
import MutationTheory.Phase7.RegimeDynamics
import MutationTheory.Phase7.RegimeDecomposition
import MutationTheory.Phase7.CompositionGap

/-!
# Phase 7 Theorem Map

Canonical aliases keyed to the numbering in
`PHASE_7_SPECIFICATION_DYNAMICS.md`.
-/

namespace MutationTheory
namespace Phase7

abbrev T7_0_specificationTrajectory := @SpecificationTrajectory
abbrev T7_0b_killIncrementEqCardSdiff := @killIncrementEqCardSdiff
abbrev T7_0c_survivorSetCard := @survivorSetCard

abbrev T7_1_greedyProportionalProgress := @greedyProportionalProgress
abbrev T7_2_exponentialDecayOfSurvivors := @exponentialDecayOfSurvivors
abbrev T7_3_phaseTransition := @phaseTransition
abbrev T7_4_greedyStabilityBound := @greedyStabilityBoundFinal
abbrev T7_5_lyapunovMonotonicity := @lyapunovMonotonicity
abbrev T7_6_convergenceRateDeterminesKappa := @convergenceRateDeterminesKappa
abbrev T7_7_trajectoryComparison := @trajectoryComparison
abbrev T7_8_entropyMonotonicity := @entropyMonotonicity
abbrev T7_9_greedyInformationContent := @greedyInformationContent

abbrev T7_10_specificationFreeEnergyBound := @FreeEnergy.specificationFreeEnergyBound
abbrev T7_11_redundantIffZeroInformation := @Redundancy.redundantIffZeroInformation
abbrev T7_12_spectrumSizeAntitone := @SpectrumEvolution.spectrumSizeAntitone
abbrev T7_13_dynamicRegimeTransition := @RegimeDynamics.dynamicRegimeTransition
abbrev T7_14_regimeTransition := @RegimeDecomposition.regimeTransition
abbrev T7_15_compositionGap := @CompositionGap.compositionGap
abbrev T7_16_gammaLeInterfaceMutantsCard := @CompositionGap.gammaLeInterfaceMutantsCard

/-!
`T7.17`–`T7.23` (sheaf/distributed/topological frontier in the reference doc)
are not yet formalized as canonical Lean theorems in this repository.
-/

end Phase7
end MutationTheory
