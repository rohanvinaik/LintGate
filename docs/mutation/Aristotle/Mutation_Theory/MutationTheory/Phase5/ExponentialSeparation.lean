import MutationTheory.Legacy.Phase_5_Specification_Complexity

namespace MutationTheory
namespace Phase5

/-- Paper reference: Section 2.2 (verification/computation exponential gap). -/
abbrev verificationExponentiallyEasierThanComputation :=
  @_root_.T5_11_Verification_Can_Be_Exponentially_Easier_Than_Computation

/-- Greedy transfer bound used by hardness reductions. -/
abbrev greedyUpperBoundTransfer := @_root_.T5_21_greedy_upper_bound_transfer

/-- Greedy near-optimality ratio on reduction instances. -/
abbrev greedyNearOptimal := @_root_.T5_21_greedy_near_optimal

end Phase5
end MutationTheory
