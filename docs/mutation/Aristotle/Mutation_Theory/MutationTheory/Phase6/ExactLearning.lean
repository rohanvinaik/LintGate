import MutationTheory.Legacy.Phase_6_Exact_Learning

namespace MutationTheory
namespace Phase6Refined

abbrev Program (D R : Type) := Phase6.Program D R
abbrev Test (D R : Type) := Phase6.Test D R
abbrev TestSuite (D R : Type) := Phase6.TestSuite D R
abbrev MutationSystem (D R : Type) := Phase6.MutationSystem D R

/-- Paper reference: Section 4, T6.7. -/
abbrev greedyNearOptimal := @Phase6.T6_7_greedy_near_optimal

/-- Paper reference: Section 4, T6.8 (`TD = κ`). -/
abbrev teachingDimensionEqQueryComplexity := @Phase6.T6_8_TD_eq_query_complexity

end Phase6Refined
end MutationTheory
