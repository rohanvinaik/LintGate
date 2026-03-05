import MutationTheory.Legacy.Phase_6_Exact_Learning

namespace MutationTheory
namespace Phase6Refined

abbrev SpecP {D R : Type} := @Phase6.SpecP D R

/-- Paper reference: Section 5, Regime A inclusion theorem. -/
abbrev regimeAYes := @Phase6.T6_24_regimeAYes

/-- Paper reference: Section 5, Regime B open problem statement. -/
abbrev regimeBOpenProblem := @Phase6.T6_24_regimeBOpenProblem

/-- Paper reference: Section 5, global SpecP separation conjecture. -/
abbrev specPSeparationConjecture {D R : Type} :=
  @Phase6.T6_24_SpecP_separation_conjecture D R

end Phase6Refined
end MutationTheory
