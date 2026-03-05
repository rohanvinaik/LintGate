import MutationTheory.Core.Definitions
import MutationTheory.Phase1.Foundations
import MutationTheory.Phase2.Synthesis
import MutationTheory.Legacy.Full_Corpus_Compile

/-! Canonical Phase 4 module (composition and information-theoretic phase). -/

namespace MutationTheory
namespace Phase4

open Core

def testEquiv
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (T : TestSuite D R)
    (P₁ P₂ : Program D R) : Prop :=
  ∀ t ∈ T, passes sem oracle P₁ t = passes sem oracle P₂ t

/-- Paper reference: Phase 4, rewrite license (killed set = mutant set). -/
theorem rewriteLicense
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (P : Program D R)
    (T : TestSuite D R)
    (h_killed : killedSet sem oracle MS P T = MS.mutants P) :
    ∀ m ∈ MS.mutants P, ¬ testEquiv sem oracle T P m := by
  intro m hmMut hEq
  have hmKilled : m ∈ killedSet sem oracle MS P T := by
    simpa [h_killed] using hmMut
  rcases Finset.mem_filter.mp hmKilled with ⟨_, ⟨t, ht, hneq⟩⟩
  exact hneq (hEq t ht)

/-- Paper reference: Phase 4, existence of an actual survivor when not all mutants are killed. -/
theorem survivorExistence
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (P : Program D R)
    (T : TestSuite D R)
    (h_not_killed : killedSet sem oracle MS P T ≠ MS.mutants P) :
    ∃ m ∈ MS.mutants P, Phase1.isSurvivor sem oracle P T m := by
  have h_exists_not_killed : ∃ m ∈ MS.mutants P, m ∉ killedSet sem oracle MS P T := by
    by_contra h_none
    apply h_not_killed
    apply Finset.Subset.antisymm
    · exact Finset.filter_subset _ _
    · intro m hmMut
      by_contra hmNotKilled
      exact h_none ⟨m, hmMut, hmNotKilled⟩
  rcases h_exists_not_killed with ⟨m, hmMut, hmNotKilled⟩
  refine ⟨m, hmMut, ?_⟩
  have h_no_dist : ¬ ∃ t ∈ T, passes sem oracle P t ≠ passes sem oracle m t := by
    intro hDist
    exact hmNotKilled (Finset.mem_filter.mpr ⟨hmMut, hDist⟩)
  intro t ht
  by_contra hneq
  exact h_no_dist ⟨t, ht, hneq⟩

def KilledMutants
    {D R : Type}
    (_sem : Program D R → D → R)
    (_oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (P : Program D R)
    (s : Phase2.SynthState D R) : Set (Program D R) :=
  {m | m ∈ MS.mutants P ∧ m ∉ s.survivors}

def EquivalentMutants
    {D R : Type}
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R)
    (s : Phase2.SynthState D R) : Set (Program D R) :=
  {m | m ∈ MS.mutants P ∧ m ∈ s.survivors ∧ trueEquiv sem P m}

def DistinguishableSurvivors
    {D R : Type}
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R)
    (s : Phase2.SynthState D R) : Set (Program D R) :=
  {m | m ∈ MS.mutants P ∧ m ∈ s.survivors ∧ ¬ trueEquiv sem P m}

/-- Paper reference: Phase 4, fixed-point partition of the mutant space. -/
theorem fixedPointPartition
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (P : Program D R)
    (s : Phase2.SynthState D R) :
    (KilledMutants sem oracle MS P s ∪
      EquivalentMutants sem MS P s ∪
      DistinguishableSurvivors sem MS P s = {m | m ∈ MS.mutants P}) ∧
    Disjoint (KilledMutants sem oracle MS P s) (EquivalentMutants sem MS P s) ∧
    Disjoint (KilledMutants sem oracle MS P s) (DistinguishableSurvivors sem MS P s) ∧
    Disjoint (EquivalentMutants sem MS P s) (DistinguishableSurvivors sem MS P s) := by
  have h_union :
      KilledMutants sem oracle MS P s ∪
        EquivalentMutants sem MS P s ∪
        DistinguishableSurvivors sem MS P s =
      {m | m ∈ MS.mutants P} := by
    ext m
    by_cases hm_surv : m ∈ s.survivors <;> by_cases hm_eq : trueEquiv sem P m <;>
      simp [KilledMutants, EquivalentMutants, DistinguishableSurvivors, hm_surv, hm_eq]
  refine ⟨h_union, ?_, ?_, ?_⟩
  · refine Set.disjoint_left.2 ?_
    intro m hmK hmE
    exact hmK.2 hmE.2.1
  · refine Set.disjoint_left.2 ?_
    intro m hmK hmD
    exact hmK.2 hmD.2.1
  · refine Set.disjoint_left.2 ?_
    intro m hmE hmD
    exact hmD.2.2 hmE.2.2

abbrev decompositionSpecificationAdjunction := @LegacyFull.T4_3_decomposition_specification_adjunction
abbrev oracleHierarchyInformationOrdering := @LegacyFull.T4_4_oracle_hierarchy_information_ordering
abbrev idempotency := @LegacyFull.T4_5_idempotency
abbrev crossChannelIndependence := @LegacyFull.T4_6_cross_channel_independence
abbrev greedySynthesisConvergence := @LegacyFull.T4_7_greedy_synthesis_convergence
abbrev capstoneCertification := @LegacyFull.T4_capstone_certification

end Phase4
end MutationTheory
