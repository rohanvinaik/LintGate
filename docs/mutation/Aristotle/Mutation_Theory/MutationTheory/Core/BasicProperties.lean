import MutationTheory.Core.Definitions

namespace MutationTheory
namespace Core

variable {D R : Type}

/-- The target program is always in its mutation-induced concept class. -/
lemma mem_conceptClass
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R) :
    P ∈ conceptClass sem MS P := by
  classical
  change P ∈ ({P} ∪ MS.mutants P)
  exact Finset.mem_union.mpr (Or.inl (by simp))

/-- Any full-SC suite kills every non-equivalent mutant. -/
lemma fullSC_kills_all
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (P : Program D R)
    (T : TestSuite D R)
    (h : achievesFullSC' sem oracle MS P T) :
    nonEquivMutants sem MS P ⊆ killedSet sem oracle MS P T := h

end Core
end MutationTheory
