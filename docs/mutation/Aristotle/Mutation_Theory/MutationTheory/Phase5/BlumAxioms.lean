import MutationTheory.Legacy.Phase_5_Specification_Complexity

noncomputable section

namespace MutationTheory
namespace Phase5

/-- Paper reference: Section 2.1, finite-domain Blum axiom witness. -/
abbrev blumAxiom1Finite := @_root_.T5_8_blum_axiom_1

/-- Constructive finite-domain decidability via canonical test suites. -/
abbrev decidableSpecComplexityLeConstructive :=
  @_root_.T5_7b_decidable_specComplexity_le_constructive

/-- Option-B obstruction biconditional schema. -/
abbrev blumAxiom1IffUnderObstruction := @_root_.T5_8b_blum_axiom_1_iff_under_obstruction

end Phase5
end MutationTheory
