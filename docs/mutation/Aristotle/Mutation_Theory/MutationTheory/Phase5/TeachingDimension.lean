import MutationTheory.Legacy.Phase_5_Specification_Complexity

noncomputable section

namespace MutationTheory
namespace Phase5

abbrev conceptClass {D R : Type} := @_root_.conceptClass D R
abbrev isTeachingSet {D R : Type} := @_root_.isTeachingSet D R
abbrev teachingDimension {D R : Type} := @_root_.teachingDimension D R

/-- Paper reference: Section 4, TD ≤ κ. -/
abbrev teachingDimensionLeSpecComplexity := @_root_.T5_17_teaching_dimension_lower_bound

/-- Paper reference: Section 4, κ ≤ TD. -/
abbrev specComplexityLeTeachingDimension := @_root_.T5_17_spec_complexity_upper_bound_by_teaching_dimension

/-- Paper reference: Section 4, TD = κ identification. -/
abbrev teachingDimensionEqSpecComplexity := @_root_.T5_17_teaching_dimension_eq_specComplexity

end Phase5
end MutationTheory
