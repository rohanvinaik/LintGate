import MutationTheory.Phase7.Internal.T7_12

noncomputable section

/-! Phase 7.12 local-disagreement spectrum evolution layer. -/

namespace MutationTheory
namespace Phase7
namespace SpectrumEvolution

abbrev MutationContext := @Phase7_T7_12.MutationContext
abbrev killed := @Phase7_T7_12.killed
abbrev survivors := @Phase7_T7_12.survivors
abbrev achievesFullSC' := @Phase7_T7_12.achievesFullSC'

/-- T7.12 Lyapunov monotonicity for killed/survivor sets. -/
abbrev lyapunovMonotonicity := @Phase7_T7_12.lyapunov_monotonicity

abbrev disagreementProb := @Phase7_T7_12.disagreementProb
abbrev localDisagreementSpectrum := @Phase7_T7_12.localDisagreementSpectrum

/-- T7.12 spectrum cardinality antitone under test-suite growth. -/
abbrev spectrumSizeAntitone := @Phase7_T7_12.spectrum_size_antitone

abbrev spectrumMax := @Phase7_T7_12.spectrumMax

/-- T7.12 spectrum maximum antitone under test-suite growth. -/
abbrev spectrumMaxAntitone := @Phase7_T7_12.spectrum_max_antitone

abbrev spectrumMin := @Phase7_T7_12.spectrumMin

/-- T7.12 spectrum minimum monotone under test-suite growth. -/
abbrev spectrumMinMonotone := @Phase7_T7_12.spectrum_min_monotone

abbrev spectrumMean := @Phase7_T7_12.spectrumMean

end SpectrumEvolution
end Phase7
end MutationTheory
