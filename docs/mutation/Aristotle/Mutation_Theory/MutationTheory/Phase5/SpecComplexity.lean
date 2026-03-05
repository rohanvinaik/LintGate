import MutationTheory.Legacy.Phase_5_Specification_Complexity

noncomputable section

namespace MutationTheory
namespace Phase5

abbrev Program (D R : Type) := _root_.Program D R
abbrev Test (D R : Type) := _root_.Test D R
abbrev TestSuite (D R : Type) := _root_.TestSuite D R
abbrev MutationSystem (D R : Type) := _root_.MutationSystem D R

abbrev nonEquivMutants {D R : Type} := @_root_.nonEquivMutants D R
abbrev achievesFullSC' {D R : Type} := @_root_.achievesFullSC' D R
abbrev specComplexity {D R : Type} := @_root_.specComplexity D R
abbrev OracleFaithful {D R : Type} := @_root_.OracleFaithful D R

/-- Paper reference: Section 2, Theorem 2.2 (finiteness bound). -/
abbrev specComplexityFinite := @_root_.T5_1_spec_complexity_finite

/-- Paper reference: Section 2, representation bound. -/
abbrev representationBound := @_root_.T5_2_representation_bound

end Phase5
end MutationTheory
