import MutationTheory.Legacy.Phase_6_Exact_Learning

namespace MutationTheory
namespace Phase6Refined

/-- Paper reference: Section 4, codeword-dependent testability twist. -/
abbrev codingTwistHolds := @Phase6.T6_19_20_twistHolds

/-- Bridge object for transporting T6.19/20 semantics into Phase6 core types. -/
abbrev ProgramBridge19
    {Prog₁₉ Inp₁₉ Out₁₉ : Type}
    (sem₁₉ : Prog₁₉ → Inp₁₉ → Out₁₉)
    (μ₁₉ : Prog₁₉ → Set Prog₁₉) :=
  @Phase6.T6_19_20_ProgramBridge Prog₁₉ Inp₁₉ Out₁₉ sem₁₉ μ₁₉

/-- Bridge object for transporting T6.21 Boolean-function semantics into Phase6. -/
abbrev ProgramBridge21 (n : Nat) := Phase6.T6_21_ProgramBridge n

end Phase6Refined
end MutationTheory
