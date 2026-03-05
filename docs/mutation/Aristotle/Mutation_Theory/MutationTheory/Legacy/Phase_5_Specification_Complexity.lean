/-
# Phase 5: Specification Complexity as a Complexity Measure

This file extends Specification Completeness Theory into complexity theory.
It defines program-level specification complexity κ_μ(P) — the minimum test suite size to
achieve SC = 1 under a fixed mutation policy μ — and investigates function-level
normalizations/complexity-class consequences.

## Phase architecture

### Group K (T5.1–T5.3): basic properties, representation independence, separation from Kolmogorov
### Group L (T5.4–T5.6): communication complexity connection, decomposition-complexity correspondence
### Group M (T5.7–T5.9): Blum axiom investigation
### Group N (T5.10–T5.12): verification boundary, convergence-hardness connection
### Group O (T5.13): parameterized Spec classes SpecP_{μ,ρ}

## Status: theorem statements + partial formal scaffolding.
## See PHASE_5_SPECIFICATION_COMPLEXITY.md for parameterized class framing and proof status.
-/

import Mathlib

set_option linter.mathlibStandardSet false
set_option linter.unusedVariables false
set_option linter.unusedSimpArgs false
set_option linter.unnecessarySimpa false

open scoped BigOperators
open scoped Real
open scoped Nat
open scoped Classical
open scoped Pointwise

set_option maxHeartbeats 4000000
set_option synthInstance.maxHeartbeats 20000
set_option synthInstance.maxSize 128

set_option relaxedAutoImplicit false
set_option autoImplicit false

noncomputable section

/-!
## Shared Definitions (imported from Phase 1–4 kernel)
-/

/-- Programs are represented by opaque indices (`id : Nat`), with semantics
provided externally by `sem : Program D R → D → R`. -/
structure Program (D R : Type) where
  id : Nat
deriving DecidableEq

structure Test (D R : Type) where
  id : Nat
  input : D
deriving DecidableEq

abbrev TestSuite (D R : Type) := Finset (Test D R)

structure MutationSystem (D R : Type) where
  mutants : Program D R → Finset (Program D R)

variable {D R : Type}

def passes (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (P : Program D R) (t : Test D R) : Bool :=
  oracle t (sem P t.input)

def isKilled (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (P : Program D R) (T : TestSuite D R) (m : Program D R) :=
  ∃ t ∈ T, passes sem oracle P t ≠ passes sem oracle m t

def isSurvivor (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (P : Program D R) (T : TestSuite D R) (m : Program D R) :=
  ∀ t ∈ T, passes sem oracle P t = passes sem oracle m t

def trueEquiv (sem : Program D R → D → R) (P₁ P₂ : Program D R) :=
  ∀ x, sem P₁ x = sem P₂ x

def killedSet (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) :=
  (MS.mutants P).filter (fun m => ∃ t ∈ T, passes sem oracle P t ≠ passes sem oracle m t)

/-!
## Phase 5 Definitions
-/

/-
Notation convention in this Lean file:
- `MS : MutationSystem D R` plays the role of mutation policy μ.
- `specComplexity sem oracle MS P` is program-level κ_μ(P).
- Function-level normalization κ^ρ_μ(f) and class-level SpecP_{μ,ρ} are handled in the
  prose layer; this file provides the core program-level lemmas used by that layer.
-/

/-- The set of non-equivalent mutants: those in Mut(P) that are not truly equivalent to P. -/
def nonEquivMutants (sem : Program D R → D → R) (MS : MutationSystem D R) (P : Program D R) : Finset (Program D R) :=
  (MS.mutants P).filter (fun m => ¬ trueEquiv sem P m)

/-- A test suite achieves full specification completeness when it kills every non-equivalent mutant. -/
def achievesFullSC (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Prop :=
  killedSet sem oracle MS P T = nonEquivMutants sem MS P ∪ (killedSet sem oracle MS P T ∩ (MS.mutants P).filter (fun m => trueEquiv sem P m))
  -- Equivalently: every non-equivalent mutant is killed.

/-- Simplified: full SC means every non-equivalent mutant is killed. -/
def achievesFullSC' (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Prop :=
  nonEquivMutants sem MS P ⊆ killedSet sem oracle MS P T

/-- Canonical equivalence between the legacy set-equation definition and the
subset-style definition used throughout the development. -/
theorem achievesFullSC_iff_achievesFullSC'
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) :
    achievesFullSC sem oracle MS P T ↔ achievesFullSC' sem oracle MS P T := by
  constructor
  · intro h m hm
    unfold achievesFullSC at h
    rw [h]
    exact Finset.mem_union.mpr (Or.inl hm)
  · intro h
    unfold achievesFullSC
    refine Finset.Subset.antisymm ?_ ?_
    · intro m hm
      have hm_mut : m ∈ MS.mutants P := (Finset.mem_filter.mp hm).1
      by_cases h_eq : trueEquiv sem P m
      · refine Finset.mem_union.mpr (Or.inr ?_)
        refine Finset.mem_inter.mpr ⟨hm, ?_⟩
        exact Finset.mem_filter.mpr ⟨hm_mut, h_eq⟩
      · exact Finset.mem_union.mpr (Or.inl (Finset.mem_filter.mpr ⟨hm_mut, h_eq⟩))
    · intro m hm
      rcases Finset.mem_union.mp hm with h_ne | h_eq
      · exact h h_ne
      · exact (Finset.mem_inter.mp h_eq).1

/-- Specification complexity: minimum test suite size to achieve full SC. -/
def specComplexity (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) : ℕ :=
  sInf { k | ∃ T : TestSuite D R, T.card = k ∧ achievesFullSC' sem oracle MS P T }

/-- The set of test suites achieving full SC. -/
def fullSCSuites (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) : Set (TestSuite D R) :=
  { T | achievesFullSC' sem oracle MS P T }

/-- Oracle faithfulness: the oracle detects every semantic difference.
    If P and m produce different outputs on a test's input, the oracle distinguishes them. -/
def OracleFaithful (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (P : Program D R) : Prop :=
  ∀ (t : Test D R) (m : Program D R),
    sem P t.input ≠ sem m t.input → passes sem oracle P t ≠ passes sem oracle m t

/-! ## ProperTest Framework (T5.11)

ProperTest uses a (input, predicate) pair instead of the abstract Test/oracle pattern.
This enables concrete witness constructions where the test predicate is explicit. -/

def ProperTest (D R : Type) := D × (R → Bool)

abbrev ProperTestSuite (D R : Type) := List (ProperTest D R)

def kills {D R : Type} (sem : Program D R → D → R) (t : ProperTest D R) (p : Program D R) : Bool :=
  let (x, pred) := t
  ¬(pred (sem p x))

def ProperAchievesFullSC {D R : Type} (sem : Program D R → D → R) (MS : MutationSystem D R) (P : Program D R) (T : ProperTestSuite D R) : Prop :=
  ∀ m ∈ MS.mutants P, ∃ t ∈ T, kills sem t m

noncomputable def ProperSpecComplexity {D R : Type} (sem : Program D R → D → R) (MS : MutationSystem D R) (P : Program D R) : Nat :=
  sInf { n | ∃ T : ProperTestSuite D R, T.length = n ∧ ProperAchievesFullSC sem MS P T }

/-! ## Circuit Complexity Theory (T5.11)

Captures the Shannon bound: for every n, there exists a Boolean function on n inputs
whose circuit complexity is at least 2^n / (3n). -/

structure ComplexityTheory where
  circuit_complexity : {n : Nat} → ((Fin n → Bool) → Bool) → Nat
  shannon_bound : ∀ n, ∃ f : (Fin n → Bool) → Bool, circuit_complexity f ≥ (2^n) / (3 * n)

/-- Verification Circuit Complexity (VCC) is defined as the specification complexity. -/
def VCC {D R : Type} (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) : Nat :=
  specComplexity sem oracle MS P

/-!
## GROUP K: Basic Properties of Specification Complexity
-/

/-
T5.1: Specification Complexity is Finite for Finite Domains.
If D is finite and R has decidable equality, then κ(f) ≤ |nonEquivMutants|.
One test per non-equivalent mutant always suffices.
-/
theorem T5_1_spec_complexity_finite
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    -- For each non-equivalent mutant, there exists a distinguishing test:
    (h_distinguishable : ∀ m ∈ nonEquivMutants sem MS P, ∃ t : Test D R, passes sem oracle P t ≠ passes sem oracle m t) :
    ∃ T : TestSuite D R, T.card ≤ (nonEquivMutants sem MS P).card ∧ achievesFullSC' sem oracle MS P T := by
  let S : Finset (Program D R) := nonEquivMutants sem MS P
  let T : TestSuite D R :=
    S.attach.image (fun msub => Classical.choose (h_distinguishable msub.1 msub.2))
  refine ⟨T, ?_, ?_⟩
  · have h_card_attach : T.card ≤ S.attach.card := by
      unfold T
      simpa using (Finset.card_image_le
        (s := S.attach)
        (f := fun msub : {m // m ∈ S} => Classical.choose (h_distinguishable msub.1 msub.2)))
    calc
      T.card ≤ S.attach.card := h_card_attach
      _ = S.card := by simp
      _ = (nonEquivMutants sem MS P).card := rfl
  · unfold achievesFullSC'
    intro m hm
    have hmS : m ∈ S := hm
    let t0 : Test D R := Classical.choose (h_distinguishable m hmS)
    have ht0_in : t0 ∈ T := by
      unfold T
      refine Finset.mem_image.mpr ?_
      refine ⟨⟨m, hmS⟩, by simp, rfl⟩
    have ht0_kills : passes sem oracle P t0 ≠ passes sem oracle m t0 :=
      Classical.choose_spec (h_distinguishable m hmS)
    have hm_mem_mutants : m ∈ MS.mutants P := by
      exact (Finset.mem_filter.mp hm).1
    unfold killedSet
    exact Finset.mem_filter.mpr ⟨hm_mem_mutants, ⟨t0, ht0_in, ht0_kills⟩⟩

/-
T5.2 (Conjecture): Representation Independence.
For two programs computing the same function, specification complexities are within constant factors.
We state the weaker version: if P₁ and P₂ are truly equivalent, their spec complexities
are related by the mutant set sizes.

Fix note: original h_phi_kills assumed achievesFullSC' for P₁ (wrong direction — proves
P₂ ≤ P₁, not P₁ ≤ P₂). Replaced with h_phi_equiv (behavioral equivalence of corresponding
mutants), following the is_SC_1_transfer pattern from T5.2_partial.lean.
-/
theorem T5_2_representation_bound
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P₁ P₂ : Program D R)
    (h_equiv : trueEquiv sem P₁ P₂)
    -- Hypothesis: there is a mutation-preserving map between the two programs' mutant sets
    (phi : Program D R → Program D R)
    (h_phi_mutants : ∀ m ∈ MS.mutants P₁, phi m ∈ MS.mutants P₂)
    (h_phi_preserves : ∀ m ∈ MS.mutants P₁, trueEquiv sem P₁ m ↔ trueEquiv sem P₂ (phi m))
    -- phi maps each mutant to a behaviorally identical program in the other representation
    (h_phi_equiv : ∀ m ∈ MS.mutants P₁, trueEquiv sem m (phi m))
    -- Guard against sInf ∅ = 0 edge case: need at least one achieving suite for P₂
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS P₂ T) :
    specComplexity sem oracle MS P₁ ≤ specComplexity sem oracle MS P₂ := by
  -- Strategy: show { k | ... P₂ } ⊆ { k | ... P₁ }, so sInf P₁ ≤ sInf P₂.
  unfold specComplexity
  apply csInf_le_csInf
  · -- BddBelow: every set of ℕ is bounded below by 0
    exact ⟨0, fun _ _ => Nat.zero_le _⟩
  · -- Nonempty: the P₂ achievable-size set is nonempty
    obtain ⟨T₀, hT₀⟩ := h_achievable
    exact ⟨T₀.card, T₀, rfl, hT₀⟩
  · -- Subset: every achieving size for P₂ is also achieving for P₁
    intro k ⟨T, hT_card, hT_sc⟩
    refine ⟨T, hT_card, ?_⟩
    -- Show achievesFullSC' P₁ T: every non-equiv P₁-mutant is killed by T.
    intro m hm_nonequiv
    -- m ∈ nonEquivMutants P₁: m ∈ MS.mutants P₁ and ¬ trueEquiv P₁ m
    have hm_mut : m ∈ MS.mutants P₁ := (Finset.mem_filter.mp hm_nonequiv).1
    have hm_ne : ¬ trueEquiv sem P₁ m := (Finset.mem_filter.mp hm_nonequiv).2
    -- phi(m) ∈ nonEquivMutants P₂
    have hphi_mut : phi m ∈ MS.mutants P₂ := h_phi_mutants m hm_mut
    have hphi_ne : ¬ trueEquiv sem P₂ (phi m) :=
      fun h => hm_ne ((h_phi_preserves m hm_mut).mpr h)
    have hphi_nonequiv : phi m ∈ nonEquivMutants sem MS P₂ :=
      Finset.mem_filter.mpr ⟨hphi_mut, hphi_ne⟩
    -- Full SC for P₂: phi(m) ∈ killedSet P₂ T
    have hphi_killed : phi m ∈ killedSet sem oracle MS P₂ T := hT_sc hphi_nonequiv
    -- Extract kill witness: ∃ t ∈ T, passes P₂ t ≠ passes (phi m) t
    rw [killedSet, Finset.mem_filter] at hphi_killed
    obtain ⟨_, ⟨t, ht_mem, ht_kill⟩⟩ := hphi_killed
    -- Transfer kill back to m using h_equiv and h_phi_equiv:
    -- passes P₁ t = passes P₂ t (by h_equiv)
    -- passes m t = passes (phi m) t (by h_phi_equiv)
    -- So passes P₁ t ≠ passes m t.
    have h_p1_eq_p2 : passes sem oracle P₁ t = passes sem oracle P₂ t := by
      unfold passes; congr 1; exact h_equiv t.input
    have h_m_eq_phi : passes sem oracle m t = passes sem oracle (phi m) t := by
      unfold passes; congr 1; exact h_phi_equiv m hm_mut t.input
    -- m ∈ killedSet P₁ T
    unfold killedSet
    exact Finset.mem_filter.mpr ⟨hm_mut, ⟨t, ht_mem, by rw [h_p1_eq_p2, h_m_eq_phi]; exact ht_kill⟩⟩

/-
T5.3 (refactored): Separation from Kolmogorov complexity.

In this file's framework, we encode the Kolmogorov side via abstract predicates/hypotheses
and prove the structural specification-complexity side directly.
-/

/-- Structural strong-b lemma: if one test already achieves full SC, then specification complexity is at most 1. -/
theorem T5_3b_singleton_suite_bound
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (h_one_test : ∃ t0 : Test D R, achievesFullSC' sem oracle MS P ({t0} : TestSuite D R)) :
    specComplexity sem oracle MS P ≤ 1 := by
  rcases h_one_test with ⟨t0, ht0⟩
  unfold specComplexity
  refine Nat.sInf_le ?_
  refine ⟨({t0} : TestSuite D R), ?_, ht0⟩
  simp

/-- Conditional low-K/high-κ direction (part a): kept assumption-driven in Lean.
`LowK` and `HighKappa` abstract the asymptotic claims from the prose theorem. -/
theorem T5_3a_low_K_high_kappa_conditional
    (LowK : Program D R → Prop)
    (HighKappa : Program D R → Prop)
    (P : Program D R)
    (h_lowK : LowK P)
    (h_highKappa : HighKappa P) :
    LowK P ∧ HighKappa P := by
  exact ⟨h_lowK, h_highKappa⟩

/-- Conditional packaging theorem for T5.3 in this corpus:
assumed part (a) witness + structural part (b) witness with one-test full-SC. -/
theorem T5_3_conditional_refactor
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (LowK : Program D R → Prop)
    (HighKappa : Program D R → Prop)
    (HighK : Program D R → Prop)
    (hA : ∃ P : Program D R, LowK P ∧ HighKappa P)
    (hB : ∃ Q : Program D R,
      HighK Q ∧ (∃ t0 : Test D R, achievesFullSC' sem oracle MS Q ({t0} : TestSuite D R))) :
    (∃ P : Program D R, LowK P ∧ HighKappa P) ∧
    (∃ Q : Program D R, HighK Q ∧ specComplexity sem oracle MS Q ≤ 1) := by
  rcases hA with ⟨P, hP⟩
  rcases hB with ⟨Q, hQK, hQtest⟩
  refine ⟨⟨P, hP⟩, ?_⟩
  refine ⟨Q, hQK, ?_⟩
  exact T5_3b_singleton_suite_bound sem oracle MS Q hQtest

/-!
## Legacy Compatibility (ordered insertion: T5.4, T5.5a)

These declarations are kept for compatibility with earlier corpus-style statements,
and are placed here to preserve increasing theorem-number flow.
-/

namespace Phase5

variable {D R : Type}

def semKilled
    (sem : Program D R → D → R)
    (P M : Program D R)
    (t : Test D R) : Prop :=
  sem P t.input ≠ sem M t.input

def killsMutantSem
    (sem : Program D R → D → R)
    (P M : Program D R)
    (T : TestSuite D R) : Prop :=
  ∃ t ∈ T, semKilled sem P M t

def SC_is_1_sem
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R)
    (T : TestSuite D R) : Prop :=
  ∀ M ∈ MS.mutants P, ¬ trueEquiv sem P M → killsMutantSem sem P M T

noncomputable def kappa
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R) : WithTop ℕ :=
  sInf { n : WithTop ℕ | ∃ T : TestSuite D R, ↑T.card = n ∧ SC_is_1_sem sem MS P T }

noncomputable def Mut_size
    (MS : MutationSystem D R)
    (P : Program D R) : ℕ :=
  (MS.mutants P).card

theorem T5_4_specification_complexity_lower_bound
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R)
    (R_epsilon : (D → R) → Real → Real)
    (epsilon : Real)
    (h_epsilon : 0 < epsilon ∧ epsilon < 1/2)
    (h_mut_nontrivial : 1 < Mut_size MS P)
    (h_diff : ∃ M ∈ MS.mutants P, ¬ trueEquiv sem P M) :
    ∃ C : Real, C > 0 ∧
      ∀ (k : ℕ), kappa sem MS P = k →
      (k : Real) ≥ R_epsilon (sem P) epsilon / (C * Real.log (Mut_size MS P : Real)) := by
  by_cases h : kappa sem MS P = ⊤
  · aesop
  · by_cases h_kappa_zero : kappa sem MS P = 0
    · simp_all +decide [kappa]
      contrapose! h_kappa_zero
      refine' ne_of_gt (lt_of_lt_of_le zero_lt_one <| le_csInf _ _)
      · exact ⟨_, ⟨h.choose_spec.choose, h.choose_spec.choose_spec.1, h.choose_spec.choose_spec.2.1⟩⟩
      · rintro _ ⟨T, rfl, hT⟩
        exact Nat.one_le_cast.mpr (Finset.card_pos.mpr <| by
          exact Exists.elim h_diff fun M hM => by
            obtain ⟨t, ht, _⟩ := hT M hM.1 hM.2
            exact ⟨t, ht⟩)
    · obtain ⟨k, hk_top⟩ : ∃ k : ℕ, (k : WithTop ℕ) = kappa sem MS P :=
        WithTop.ne_top_iff_exists.mp h
      have hk_eq : kappa sem MS P = k := by
        simpa using hk_top.symm
      have hk_pos : 1 ≤ k := by
        have hk_ne_zero : k ≠ 0 := by
          intro hk0
          apply h_kappa_zero
          simpa [hk_eq, hk0]
        exact Nat.succ_le_of_lt (Nat.pos_of_ne_zero hk_ne_zero)
      have hk : kappa sem MS P = k ∧ 1 ≤ k := ⟨hk_eq, hk_pos⟩
      use (|R_epsilon (sem P) epsilon| + 1) / (k * Real.log (Mut_size MS P))
      field_simp
      refine ⟨?_, ?_⟩
      · have h_num_pos : 0 < |R_epsilon (sem P) epsilon| + 1 := by
          nlinarith [abs_nonneg (R_epsilon (sem P) epsilon)]
        have h_den_pos : 0 < Real.log (Mut_size MS P : Real) := by
          exact Real.log_pos (Nat.one_lt_cast.mpr h_mut_nontrivial)
        have h_div_pos : 0 < (|R_epsilon (sem P) epsilon| + 1) / Real.log (Mut_size MS P : Real) := by
          exact div_pos h_num_pos h_den_pos
        nlinarith
      · intro n hn
        rw [mul_div_right_comm]
        rw [mul_div_cancel_right₀ _ (ne_of_gt (Real.log_pos (Nat.one_lt_cast.mpr h_mut_nontrivial)))]
        cases abs_cases (R_epsilon (sem P) epsilon) <;>
          nlinarith [show (k : Real) = n by exact_mod_cast hk.1.symm.trans hn]

def FunctionIrreducibleBool (f : Bool × Bool → Bool) : Prop :=
  ∀ g₁ g₂ : Bool → Bool, ∀ h : Bool × Bool → Bool,
    (¬ Function.Injective g₁ ∨ ¬ Function.Injective g₂) →
    (fun p => h (g₁ p.1, g₂ p.2)) ≠ f

theorem irreducible_functions_exist_bool :
  ∃ f : Bool × Bool → Bool, FunctionIrreducibleBool f := by
  simp_all +decide [FunctionIrreducibleBool, funext_iff, Fin.forall_fin_two]

def sliceA_bool (f : Bool × Bool → Bool) (b₀ : Bool) : Bool → Bool :=
  fun a => f (a, b₀)

def sliceB_bool (f : Bool × Bool → Bool) (a₀ : Bool) : Bool → Bool :=
  fun b => f (a₀, b)

structure MutationContextBool where
  mutants_f : Set (Bool × Bool → Bool)
  mutants_A : Set (Bool → Bool)
  mutants_B : Set (Bool → Bool)

def IsUniversalBool (ctx : MutationContextBool) : Prop :=
  ctx.mutants_f = Set.univ ∧ ctx.mutants_A = Set.univ ∧ ctx.mutants_B = Set.univ

def kappa_univ_card {A : Type} [Fintype A] : ℤ :=
  Fintype.card A

def InteractionInformation_univ
    (f : Bool × Bool → Bool)
    (b₀ a₀ : Bool)
    (ctx : MutationContextBool) : ℤ :=
  kappa_univ_card (A := Bool × Bool) -
    kappa_univ_card (A := Bool) -
    kappa_univ_card (A := Bool)

theorem T5_5a_refutation_universal_context :
    ∃ (f : Bool × Bool → Bool) (b₀ a₀ : Bool) (ctx : MutationContextBool),
      IsUniversalBool ctx ∧
      FunctionIrreducibleBool f ∧
      InteractionInformation_univ f b₀ a₀ ctx = 0 := by
  rcases irreducible_functions_exist_bool with ⟨f, hf⟩
  refine ⟨f, true, true, ⟨Set.univ, Set.univ, Set.univ⟩, ?_⟩
  refine ⟨by simp [IsUniversalBool], ?_, ?_⟩
  · exact hf
  · norm_num [InteractionInformation_univ, kappa_univ_card]

end Phase5

/-- Top-level alias for the legacy-compatible T5.4 lower-bound statement. -/
theorem T5_4_specification_complexity_lower_bound_compat
    (sem : Program D R → D → R)
    (MS : MutationSystem D R)
    (P : Program D R)
    (R_epsilon : (D → R) → Real → Real)
    (epsilon : Real)
    (h_epsilon : 0 < epsilon ∧ epsilon < 1/2)
    (h_mut_nontrivial : 1 < Phase5.Mut_size MS P)
    (h_diff : ∃ M ∈ MS.mutants P, ¬ trueEquiv sem P M) :
    ∃ C : Real, C > 0 ∧
      ∀ (k : ℕ), Phase5.kappa sem MS P = k →
      (k : Real) ≥ R_epsilon (sem P) epsilon / (C * Real.log (Phase5.Mut_size MS P : Real)) := by
  exact Phase5.T5_4_specification_complexity_lower_bound sem MS P R_epsilon epsilon h_epsilon h_mut_nontrivial h_diff

/-- Top-level alias for the legacy-compatible T5.5a refutation witness. -/
theorem T5_5a_refutation_universal_context_compat :
    ∃ (f : Bool × Bool → Bool) (b₀ a₀ : Bool) (ctx : Phase5.MutationContextBool),
      Phase5.IsUniversalBool ctx ∧
      Phase5.FunctionIrreducibleBool f ∧
      Phase5.InteractionInformation_univ f b₀ a₀ ctx = 0 := by
  exact Phase5.T5_5a_refutation_universal_context

/-!
## GROUP L: Communication Complexity Connection
-/

/-
T5.5: Irreducibility Implies Positive Interaction Information.
If f is irreducible (in the sense of T3.4), then testing the slices independently
does not suffice to achieve full SC.

Fix note (three bugs in original):
1. h_TA_slice / h_TB_slice were vacuously true (∃ a, t.input.1 = a holds by ⟨_, rfl⟩).
   Replaced with meaningful slice constraint: T_A fixes the second coordinate to a constant
   b₀ (tests A by holding B constant), T_B fixes the first coordinate to a₀.
2. h_irreducible only gave existential agreement (∃ b per row), not at test points.
   The mutant must agree with P along entire axis slices for the argument to transfer.
3. The mutant m was independent of T_A, T_B. Now h_irreducible is universally quantified
   over axes, so the adversarial mutant can hide along whichever axes the tests probe.
-/
theorem T5_5_irreducible_positive_interaction
    {A B C : Type} [DecidableEq A] [DecidableEq B] [DecidableEq C]
    (sem : Program (A × B) C → A × B → C)
    (oracle : Test (A × B) C → C → Bool)
    (MS : MutationSystem (A × B) C)
    (P : Program (A × B) C)
    -- Integration irreducibility: for any choice of test axes a₀, b₀, there is a
    -- non-equivalent mutant that agrees with P along the entire b₀-column and a₀-row.
    -- The mutant hides on exactly the axes any slice test suite must probe.
    (h_irreducible : ∀ a₀ : A, ∀ b₀ : B, ∃ m ∈ nonEquivMutants sem MS P,
      (∀ a : A, sem P (a, b₀) = sem m (a, b₀)) ∧
      (∀ b : B, sem P (a₀, b) = sem m (a₀, b)))
    -- Slice test suites: T_A tests A by fixing B to constant b₀, T_B fixes A to a₀
    (T_A : TestSuite (A × B) C)
    (T_B : TestSuite (A × B) C)
    (h_TA_slice : ∃ b₀ : B, ∀ t ∈ T_A, t.input.2 = b₀)
    (h_TB_slice : ∃ a₀ : A, ∀ t ∈ T_B, t.input.1 = a₀) :
    ¬ achievesFullSC' sem oracle MS P (T_A ∪ T_B) := by
  -- Extract the specific axes from slice constraints
  obtain ⟨b₀, hb₀⟩ := h_TA_slice
  obtain ⟨a₀, ha₀⟩ := h_TB_slice
  -- Get the integration mutant hiding along these axes
  obtain ⟨m, hm_ne, h_agree_col, h_agree_row⟩ := h_irreducible a₀ b₀
  -- Assume full SC and derive contradiction
  intro h_sc
  -- m ∈ nonEquivMutants, so m ∈ killedSet (T_A ∪ T_B)
  have hm_killed := h_sc hm_ne
  rw [killedSet, Finset.mem_filter] at hm_killed
  obtain ⟨_, t, ht_mem, ht_kill⟩ := hm_killed
  -- Case split: t ∈ T_A or t ∈ T_B
  rw [Finset.mem_union] at ht_mem
  rcases ht_mem with ht_A | ht_B
  · -- t ∈ T_A: second coordinate is b₀, so sem P and sem m agree at t.input
    exact absurd (show passes sem oracle P t = passes sem oracle m t by
      unfold passes; congr 1
      rw [show t.input = (t.input.1, b₀) from Prod.ext rfl (hb₀ t ht_A)]
      exact h_agree_col t.input.1) ht_kill
  · -- t ∈ T_B: first coordinate is a₀, so sem P and sem m agree at t.input
    exact absurd (show passes sem oracle P t = passes sem oracle m t by
      unfold passes; congr 1
      rw [show t.input = (a₀, t.input.2) from Prod.ext (ha₀ t ht_B) rfl]
      exact h_agree_row t.input.2) ht_kill

/-- Alias naming this theorem explicitly as the structured-context T5.5b direction. -/
theorem T5_5b_irreducible_positive_interaction_structured
    {A B C : Type} [DecidableEq A] [DecidableEq B] [DecidableEq C]
    (sem : Program (A × B) C → A × B → C)
    (oracle : Test (A × B) C → C → Bool)
    (MS : MutationSystem (A × B) C)
    (P : Program (A × B) C)
    (h_irreducible : ∀ a₀ : A, ∀ b₀ : B, ∃ m ∈ nonEquivMutants sem MS P,
      (∀ a : A, sem P (a, b₀) = sem m (a, b₀)) ∧
      (∀ b : B, sem P (a₀, b) = sem m (a₀, b)))
    (T_A : TestSuite (A × B) C)
    (T_B : TestSuite (A × B) C)
    (h_TA_slice : ∃ b₀ : B, ∀ t ∈ T_A, t.input.2 = b₀)
    (h_TB_slice : ∃ a₀ : A, ∀ t ∈ T_B, t.input.1 = a₀) :
    ¬ achievesFullSC' sem oracle MS P (T_A ∪ T_B) := by
  exact T5_5_irreducible_positive_interaction sem oracle MS P h_irreducible T_A T_B h_TA_slice h_TB_slice

/-!
## T5.5b Information-Theoretic Proof (Aristotle)

Namespace isolating T5.5b's semantic type ecosystem (`Program { fn }`, reordered `Test`)
from Phase 5's top-level types. Contains the constructive proof that irreducibility
implies strictly positive mutual information of the detection distribution.

Co-authored-by: Aristotle (Harmonic) <aristotle-harmonic@harmonic.fun>
-/

namespace T5_5b_IT

-- T5.5b uses semantic Program { fn } and reordered Test { input, id }
structure Program (D R : Type) where
  fn : D → R

structure Test (D R : Type) where
  input : D
  id : ℕ
deriving DecidableEq, Repr

abbrev TestSuite (D R : Type) := Finset (Test D R)

structure MutationSystem (D R : Type) where
  mutants : Program D R → Finset (Program D R)

variable {D R : Type}
variable (sem : Program D R → D → R)
variable (oracle : Test D R → R → Bool)

def passes (P : Program D R) (t : Test D R) : Bool :=
  oracle t (sem P t.input)

def killedSet (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Finset (Program D R) :=
  (MS.mutants P).filter (fun m => ∃ t ∈ T, ¬ passes sem oracle m t)

/-! ### Information-Theory Infrastructure -/

def MutDistribution (α : Type) := α → ℝ

def is_distribution {α : Type} [Fintype α] (P : MutDistribution α) : Prop :=
  (∀ x, 0 ≤ P x) ∧ (∑ x, P x = 1)

noncomputable def marginal {n : ℕ} (P : MutDistribution (Fin n → Bool)) (i : Fin n) (b : Bool) : ℝ :=
  ∑ x ∈ Finset.univ.filter (fun v => v i = b), P x

noncomputable def product_of_marginals {n : ℕ} (P : MutDistribution (Fin n → Bool)) : MutDistribution (Fin n → Bool) :=
  fun x => ∏ i, marginal P i (x i)

noncomputable def kl_divergence {α : Type} [Fintype α] (P Q : MutDistribution α) : ℝ :=
  ∑ x, P x * Real.log (P x / Q x)

noncomputable def mutual_information {n : ℕ} (P : MutDistribution (Fin n → Bool)) : ℝ :=
  kl_divergence P (product_of_marginals P)

/-! ### Irreducibility -/

def IrreducibleFunction {D1 D2 R : Type} (f : Program (D1 × D2) R) : Prop :=
  ∀ (R1 R2 : Type) (g1 : Program D1 R1) (g2 : Program D2 R2) (h : Program (R1 × R2) R),
    f.fn ≠ fun p => h.fn (g1.fn p.1, g2.fn p.2)

def ProperlyIrreducible {D1 D2 R : Type} (f : Program (D1 × D2) R) : Prop :=
  ∀ (R1 R2 : Type) (g1 : Program D1 R1) (g2 : Program D2 R2) (h : Program (R1 × R2) R),
    (¬ Function.Injective g1.fn ∨ ¬ Function.Injective g2.fn) →
    f.fn ≠ fun p => h.fn (g1.fn p.1, g2.fn p.2)

/-! ### Structured Contexts -/

structure StructuredContext (D1 D2 R : Type) where
  MS : MutationSystem (D1 × D2) R
  sem : Program (D1 × D2) R → (D1 × D2) → R
  oracle : Test (D1 × D2) R → R → Bool
  is_locality_preserving : Prop
  is_component_respecting : Prop
  is_non_universal : Prop

structure StructuredContextWithSplit (D1 D2 R : Type) where
  MS : MutationSystem (D1 × D2) R
  sem : Program (D1 × D2) R → (D1 × D2) → R
  oracle : Test (D1 × D2) R → R → Bool
  local_mutants : Finset (Program (D1 × D2) R)
  cross_mutants : Finset (Program (D1 × D2) R)
  h_partition : MS.mutants = (fun f => local_mutants ∪ cross_mutants)
  h_disjoint : Disjoint local_mutants cross_mutants

def toStructuredContext {D1 D2 R : Type} (M : StructuredContextWithSplit D1 D2 R) : StructuredContext D1 D2 R :=
  { MS := M.MS,
    sem := M.sem,
    oracle := M.oracle,
    is_locality_preserving := True,
    is_component_respecting := True,
    is_non_universal := True }

/-! ### Detection Machinery -/

def detection_vector_scalar {D1 D2 R : Type} (M : StructuredContext D1 D2 R) (T1 T2 : TestSuite (D1 × D2) R) (m : Program (D1 × D2) R) : Fin 2 → Bool :=
  fun i =>
    if i = 0 then
      ∃ t ∈ T1, ¬ passes M.sem M.oracle m t
    else
      ∃ t ∈ T2, ¬ passes M.sem M.oracle m t

noncomputable def distribution_of_scalar {D1 D2 R : Type} (M : StructuredContext D1 D2 R) (f : Program (D1 × D2) R) (T1 T2 : TestSuite (D1 × D2) R) : MutDistribution (Fin 2 → Bool) :=
  fun v =>
    let mutants := M.MS.mutants f
    let matching := mutants.filter (fun m => detection_vector_scalar M T1 T2 m = v)
    matching.card / mutants.card

noncomputable def InteractionInformationScalar {D1 D2 R : Type} (M : StructuredContext D1 D2 R) (f : Program (D1 × D2) R) (T1 T2 : TestSuite (D1 × D2) R) : ℝ :=
  mutual_information (distribution_of_scalar M f T1 T2)

/-! ### Slicing -/

def is_slice_suite {D1 D2 R : Type} [Fintype D1] [Fintype D2] (T : TestSuite (D1 × D2) R) (T1 : TestSuite (D1 × D2) R) (T2 : TestSuite (D1 × D2) R) : Prop :=
  (∃ y0 : D2, T1 = Finset.univ.image (fun x => { input := (x, y0), id := 0 })) ∧
  (∃ x0 : D1, T2 = Finset.univ.image (fun y => { input := (x0, y), id := 0 })) ∧
  T = T1 ∪ T2

def is_slice_complete_struct {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContext D1 D2 R)
    (f : Program (D1 × D2) R)
    (T : TestSuite (D1 × D2) R)
    (T1 T2 : TestSuite (D1 × D2) R) : Prop :=
  is_slice_suite T T1 T2 ∧
  (∀ m ∈ M.MS.mutants f, (∃ t ∈ T1, M.sem m t.input ≠ M.sem f t.input) → (∃ t ∈ T1, ¬ passes M.sem M.oracle m t)) ∧
  (∀ m ∈ M.MS.mutants f, (∃ t ∈ T2, M.sem m t.input ≠ M.sem f t.input) → (∃ t ∈ T2, ¬ passes M.sem M.oracle m t))

/-! ### Counting Infrastructure -/

def counts_profile {D1 D2 R : Type} [Fintype D1] [Fintype D2] (M : StructuredContextWithSplit D1 D2 R) (T1 T2 : TestSuite (D1 × D2) R) (mutants : Finset (Program (D1 × D2) R)) : (Fin 2 → Bool) → ℕ :=
  fun v =>
    (mutants.filter (fun m => detection_vector_scalar (toStructuredContext M) T1 T2 m = v)).card

theorem counts_profile_add
    {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContextWithSplit D1 D2 R)
    (T1 T2 : TestSuite (D1 × D2) R)
    (A B : Finset (Program (D1 × D2) R))
    (h_disjoint : Disjoint A B)
    (v : Fin 2 → Bool) :
    counts_profile M T1 T2 (A ∪ B) v = counts_profile M T1 T2 A v + counts_profile M T1 T2 B v := by
  unfold counts_profile
  rw [Finset.filter_union, Finset.card_union_of_disjoint]
  exact Finset.disjoint_filter_filter h_disjoint

theorem counts_profile_decomposition
    {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContextWithSplit D1 D2 R)
    (f : Program (D1 × D2) R)
    (T1 T2 : TestSuite (D1 × D2) R)
    (h_cross_hidden : ∀ m ∈ M.cross_mutants, detection_vector_scalar (toStructuredContext M) T1 T2 m = (fun _ => false)) :
    let N00 := counts_profile M T1 T2 (M.MS.mutants f) (fun _ => false)
    let N11 := counts_profile M T1 T2 (M.MS.mutants f) (fun _ => true)
    let N10 := counts_profile M T1 T2 (M.MS.mutants f) (fun i => if i = 0 then true else false)
    let N01 := counts_profile M T1 T2 (M.MS.mutants f) (fun i => if i = 1 then true else false)
    let n00 := counts_profile M T1 T2 M.local_mutants (fun _ => false)
    let n11 := counts_profile M T1 T2 M.local_mutants (fun _ => true)
    let n10 := counts_profile M T1 T2 M.local_mutants (fun i => if i = 0 then true else false)
    let n01 := counts_profile M T1 T2 M.local_mutants (fun i => if i = 1 then true else false)
    N00 = n00 + M.cross_mutants.card ∧
    N11 = n11 ∧
    N10 = n10 ∧
    N01 = n01 := by
      have h_split_counts : ∀ v : Fin 2 → Bool, counts_profile M T1 T2 (M.MS.mutants f) v = counts_profile M T1 T2 M.local_mutants v + counts_profile M T1 T2 M.cross_mutants v := by
        intro v
        rw [← counts_profile_add]
        · rw [ M.h_partition ]
        · exact M.h_disjoint
      simp_all +decide [ counts_profile ]
      exact congr_arg Finset.card ( Finset.filter_true_of_mem fun m hm => h_cross_hidden m hm )

/-! ### Bridge Lemmas -/

theorem T5_5b_lemma_algebra (n00 n01 n10 n11 NC : ℕ)
    (h_indep : n00 * n11 = n10 * n01)
    (h_NC_pos : NC > 0)
    (h_n11_pos : n11 > 0) :
    (n00 + NC) * n11 ≠ n10 * n01 := by
      rw [←h_indep]
      linarith [mul_pos h_NC_pos h_n11_pos]

theorem prob_eq_count_div_total
    {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContextWithSplit D1 D2 R)
    (f : Program (D1 × D2) R)
    (T1 T2 : TestSuite (D1 × D2) R)
    (h_mutants_nonempty : (M.MS.mutants f).Nonempty)
    (v : Fin 2 → Bool) :
    distribution_of_scalar (toStructuredContext M) f T1 T2 v =
    (counts_profile M T1 T2 (M.MS.mutants f) v : ℝ) / (M.MS.mutants f).card := by
  unfold distribution_of_scalar counts_profile
  simp
  rfl

theorem cross_product_counts_iff_probs_v5
    {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContextWithSplit D1 D2 R)
    (f : Program (D1 × D2) R)
    (T1 T2 : TestSuite (D1 × D2) R)
    (h_mutants_nonempty : (M.MS.mutants f).Nonempty) :
    let N00 := counts_profile M T1 T2 (M.MS.mutants f) (fun _ => false)
    let N11 := counts_profile M T1 T2 (M.MS.mutants f) (fun _ => true)
    let N10 := counts_profile M T1 T2 (M.MS.mutants f) (fun i => if i = 0 then true else false)
    let N01 := counts_profile M T1 T2 (M.MS.mutants f) (fun i => if i = 1 then true else false)
    let p00 := distribution_of_scalar (toStructuredContext M) f T1 T2 (fun _ => false)
    let p11 := distribution_of_scalar (toStructuredContext M) f T1 T2 (fun _ => true)
    let p10 := distribution_of_scalar (toStructuredContext M) f T1 T2 (fun i => if i = 0 then true else false)
    let p01 := distribution_of_scalar (toStructuredContext M) f T1 T2 (fun i => if i = 1 then true else false)
    (p00 * p11 = p10 * p01) ↔ (N00 * N11 : ℝ) = (N10 * N01 : ℝ) := by
  intros N00 N11 N10 N01 p00 p11 p10 p01
  have h_card_pos : 0 < (M.MS.mutants f).card := Finset.card_pos.mpr h_mutants_nonempty
  have h_card_ne_zero : ((M.MS.mutants f).card : ℝ) ≠ 0 := by
    rw [Nat.cast_ne_zero]
    exact h_card_pos.ne'
  dsimp only [p00, p11, p10, p01, N00, N11, N10, N01]
  simp only [prob_eq_count_div_total M f T1 T2 h_mutants_nonempty]
  field_simp [h_card_ne_zero]

/-! ### Major Lemmas -/

theorem independence_iff_cross_product_eq
    (P : MutDistribution (Fin 2 → Bool))
    (hP : is_distribution P) :
    mutual_information P = 0 ↔
    let p00 := P (fun i => if i = 0 then false else false)
    let p11 := P (fun i => if i = 0 then true else true)
    let p10 := P (fun i => if i = 0 then true else false)
    let p01 := P (fun i => if i = 0 then false else true)
    p00 * p11 = p10 * p01 := by
      constructor;
      · intro h_mutual_zero
        have h_prod : ∀ (x : Fin 2 → Bool), P x = (∏ i, marginal P i (x i)) := by
          contrapose! h_mutual_zero;
          obtain ⟨x, hx⟩ : ∃ x : Fin 2 → Bool, P x ≠ (∏ i, marginal P i (x i)) := h_mutual_zero
          have h_kl_pos : ∑ x, P x * Real.log (P x / (∏ i, marginal P i (x i))) > 0 := by
            have h_kl_pos : ∀ x, P x > 0 → P x ≠ (∏ i, marginal P i (x i)) → P x * Real.log (P x / (∏ i, marginal P i (x i))) > P x - (∏ i, marginal P i (x i)) := by
              intros x hx_pos hx_neq
              have h_log_pos : Real.log (P x / (∏ i, marginal P i (x i))) > 1 - (∏ i, marginal P i (x i)) / P x := by
                have h_log_pos : ∀ y > 0, y ≠ 1 → Real.log y > 1 - 1 / y := by
                  exact fun y hy hy' => by have := Real.log_lt_sub_one_of_pos ( inv_pos.mpr hy ) ( by aesop ) ; norm_num at * ; linarith;
                convert h_log_pos ( P x / ∏ i, marginal P i ( x i ) ) ( div_pos hx_pos ( Finset.prod_pos fun i _ => ?_ ) ) ( div_ne_one_of_ne hx_neq ) using 1 ; norm_num [ div_eq_mul_inv ];
                refine' lt_of_lt_of_le _ ( Finset.single_le_sum ( fun a _ => hP.1 a ) ( Finset.mem_filter.mpr ⟨ Finset.mem_univ x, rfl ⟩ ) ) ; aesop;
              nlinarith [ mul_div_cancel₀ ( ∏ i, marginal P i ( x i ) ) hx_pos.ne' ];
            have h_kl_pos : ∑ x, P x * Real.log (P x / (∏ i, marginal P i (x i))) > ∑ x, (P x - (∏ i, marginal P i (x i))) := by
              refine' Finset.sum_lt_sum _ _;
              · intro x hx; by_cases hx' : P x > 0 <;> by_cases hx'' : P x = ∏ i, marginal P i ( x i ) <;> simp_all +decide ;
                · linarith [ h_kl_pos x hx' hx'' ];
                · norm_num [ show P x = 0 by linarith [ hP.1 x ] ] at *;
                  exact mul_nonneg ( Finset.sum_nonneg fun _ _ => hP.1 _ ) ( Finset.sum_nonneg fun _ _ => hP.1 _ );
              · by_cases h_pos : P x > 0;
                · exact ⟨ x, Finset.mem_univ _, h_kl_pos x h_pos hx ⟩;
                · cases lt_or_gt_of_ne hx <;> simp_all +decide [ Fin.prod_univ_two ];
                  · use x; simp_all +decide [ marginal ] ;
                    rw [ show P x = 0 by linarith [ hP.1 x ] ] ; norm_num;
                    linarith [ hP.1 x ];
                  · linarith [ show 0 ≤ marginal P 0 ( x 0 ) * marginal P 1 ( x 1 ) by exact mul_nonneg ( Finset.sum_nonneg fun _ _ => hP.1 _ ) ( Finset.sum_nonneg fun _ _ => hP.1 _ ) ];
            convert h_kl_pos using 1 ; norm_num [ Finset.sum_sub_distrib, hP.2 ];
            unfold marginal; norm_num [ Finset.sum_filter, Finset.sum_product ] ; ring_nf;
            rw [ show ( Finset.univ : Finset ( Fin 2 → Bool ) ) = { fun i => if i = 0 then Bool.true else Bool.true, fun i => if i = 0 then Bool.true else Bool.false, fun i => if i = 0 then Bool.false else Bool.true, fun i => if i = 0 then Bool.false else Bool.false } by decide ] ; simp +decide [ Finset.sum ] ; ring_nf!;
            have := hP.2; rw [ show ( Finset.univ : Finset ( Fin 2 → Bool ) ) = { fun i => if i = 0 then Bool.true else Bool.true, fun i => if i = 0 then Bool.true else Bool.false, fun i => if i = 0 then Bool.false else Bool.true, fun i => if i = 0 then Bool.false else Bool.false } by decide ] at this; simp +decide [ Finset.sum ] at this; nlinarith!;
          exact ne_of_gt h_kl_pos;
        simp +decide [ h_prod, Fin.prod_univ_two ] ; ring!;
      · intro h_eq
        have h_prod : ∀ x : Fin 2 → Bool, P x = (∏ i, marginal P i (x i)) := by
          intro x
          have h_prod : P x = (∑ y ∈ Finset.univ.filter (fun v => v 0 = x 0), P y) * (∑ y ∈ Finset.univ.filter (fun v => v 1 = x 1), P y) / (∑ y ∈ Finset.univ, P y) := by
            simp_all +decide [ Finset.sum_filter, Finset.sum_range_succ ];
            rw [ show ( Finset.univ : Finset ( Fin 2 → Bool ) ) = { fun _ => Bool.true, fun _ => Bool.false, fun i => if i = 0 then Bool.true else Bool.false, fun i => if i = 0 then Bool.false else Bool.true } by decide ] ; simp +decide [ Finset.sum ] ; ring_nf;
            cases x0 : x 0 <;> cases x1 : x 1 <;> simp +decide [ x0, x1 ] <;> ring_nf;
            · rw [ show x = fun _ => Bool.false from funext fun i => by fin_cases i <;> assumption ] ; ring_nf;
              by_cases h : ( P fun _ => Bool.false ) + P ( fun i => Decidable.decide ( i = 0 ) ) + P ( fun _ => Bool.true ) + P ( fun i => !Decidable.decide ( i = 0 ) ) = 0 <;> simp_all +decide [ sq, mul_assoc, mul_comm, mul_left_comm ];
              · linarith [ hP.1 ( fun _ => Bool.false ), hP.1 ( fun i => Decidable.decide ( i = 0 ) ), hP.1 ( fun _ => Bool.true ), hP.1 ( fun i => !Decidable.decide ( i = 0 ) ) ];
              · field_simp [h];
                linarith;
            · field_simp;
              rw [ eq_div_iff ];
              · rw [ show x = fun i => if i = 0 then Bool.false else Bool.true from by ext i; fin_cases i <;> tauto ] ; ring_nf;
                rw [ show ( fun i : Fin 2 => if i = 0 then Bool.false else Bool.true ) = fun i : Fin 2 => !Decidable.decide ( i = 0 ) by decide ] ; ring_nf!;
                linarith;
              · have := hP.2; rw [ show ( Finset.univ : Finset ( Fin 2 → Bool ) ) = { fun _ => Bool.true, fun _ => Bool.false, fun i => if i = 0 then Bool.true else Bool.false, fun i => if i = 0 then Bool.false else Bool.true } by decide ] at this; simp +decide [ Finset.sum ] at this; linarith;
            · field_simp;
              rw [ eq_div_iff ];
              · rw [ show x = fun i => if i = 0 then Bool.true else Bool.false by ext i; fin_cases i <;> tauto ] ; ring_nf!;
                linarith!;
              · have := hP.2; rw [ show ( Finset.univ : Finset ( Fin 2 → Bool ) ) = { fun _ => Bool.true, fun _ => Bool.false, fun i => if i = 0 then Bool.true else Bool.false, fun i => if i = 0 then Bool.false else Bool.true } by decide ] at this; simp +decide [ Finset.sum ] at this; linarith;
            · rw [ show x = fun _ => Bool.true from by ext i; fin_cases i <;> assumption ] ; ring_nf;
              field_simp;
              rw [ eq_div_iff ] <;> ring_nf;
              · linarith;
              · have := hP.2; rw [ show ( Finset.univ : Finset ( Fin 2 → Bool ) ) = { fun _ => Bool.true, fun _ => Bool.false, fun i => if i = 0 then Bool.true else Bool.false, fun i => if i = 0 then Bool.false else Bool.true } by decide ] at this; simp +decide [ Finset.sum ] at this; nlinarith;
          simp_all +decide [ Finset.prod_apply, marginal ];
          rw [ hP.2, div_one ];
        unfold mutual_information kl_divergence product_of_marginals; simp +decide [ Fin.prod_univ_two, h_prod ] ;

theorem mutual_information_nonneg {n : ℕ} (P : MutDistribution (Fin n → Bool)) (hP : is_distribution P) :
  mutual_information P ≥ 0 := by
    have h_jensen : ∑ x, P x * Real.log (product_of_marginals P x / P x) ≤ 0 := by
      have h_log_ineq : ∀ x, P x * Real.log (product_of_marginals P x / P x) ≤ P x * (product_of_marginals P x / P x - 1) := by
        intro x; cases eq_or_ne ( P x ) 0 <;> simp_all +decide [ Real.log_le_iff_le_exp ] ;
        by_cases h : product_of_marginals P x / P x = 0 <;> simp_all +decide [ Real.log_le_iff_le_exp ];
        · unfold product_of_marginals at h; simp_all +decide [ Finset.prod_eq_zero_iff, marginal ] ;
          obtain ⟨ a, ha ⟩ := h; rw [ Finset.sum_eq_zero_iff_of_nonneg fun _ _ => hP.1 _ ] at ha; aesop;
        · exact mul_le_mul_of_nonneg_left ( Real.log_le_sub_one_of_pos ( div_pos ( lt_of_le_of_ne ( show 0 ≤ product_of_marginals P x from Finset.prod_nonneg fun _ _ => Finset.sum_nonneg fun _ _ => le_trans ( by norm_num ) ( hP.1 _ ) ) ( Ne.symm h ) ) ( lt_of_le_of_ne ( show 0 ≤ P x from hP.1 _ ) ( Ne.symm ‹_› ) ) ) ) ( hP.1 _ );
      refine le_trans ( Finset.sum_le_sum fun x _ => h_log_ineq x ) ?_;
      suffices h_simplify : ∑ x, product_of_marginals P x ≤ ∑ x, P x by
        simp_all +decide [ mul_sub, mul_div_cancel₀, Finset.sum_sub_distrib, mul_one, Finset.sum_mul _ _ _, hP.2 ];
        refine' le_trans _ h_simplify;
        refine' Finset.sum_le_sum fun x _ => _;
        by_cases hx : P x = 0 <;> simp_all +decide [ mul_div_cancel₀ ];
        exact Finset.prod_nonneg fun _ _ => Finset.sum_nonneg fun _ _ => hP.1 _;
      have h_fubini : ∑ x : Fin n → Bool, ∏ i : Fin n, marginal P i (x i) = ∏ i : Fin n, ∑ b : Bool, marginal P i b := by
        exact Eq.symm (Fintype.prod_sum (marginal P))
      have h_marginal_sum : ∀ i : Fin n, ∑ b : Bool, marginal P i b = 1 := by
        intro i
        have h_marginal_sum : ∑ b : Bool, marginal P i b = ∑ x : Fin n → Bool, P x := by
          unfold marginal; simp +decide [ Finset.sum_filter ] ;
          simpa only [ ← Finset.sum_add_distrib ] using Finset.sum_congr rfl fun x _ => by cases x i <;> simp +decide [ * ] ;
        exact h_marginal_sum.trans hP.2;
      convert h_fubini.le using 1 ; norm_num [ h_marginal_sum, hP.2 ];
      exact Eq.symm ( Finset.prod_eq_one fun i _ => by simpa [ Finset.sum_add_distrib ] using h_marginal_sum i );
    unfold mutual_information;
    unfold kl_divergence; simp_all +decide [ Real.log_div, mul_comm ] ;
    rw [ Finset.sum_congr rfl fun x hx => by rw [ show P x / product_of_marginals P x = ( product_of_marginals P x / P x ) ⁻¹ from by rw [ inv_div ], Real.log_inv ] ] ; norm_num ; linarith;

theorem distribution_of_scalar_is_distribution
    {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContextWithSplit D1 D2 R)
    (f : Program (D1 × D2) R)
    (T1 T2 : TestSuite (D1 × D2) R)
    (h_mutants_nonempty : (M.MS.mutants f).Nonempty) :
    is_distribution (distribution_of_scalar (toStructuredContext M) f T1 T2) := by
      have h_nonneg : ∀ v, 0 ≤ distribution_of_scalar (toStructuredContext M) f T1 T2 v := by
        exact fun v => div_nonneg ( Nat.cast_nonneg _ ) ( Nat.cast_nonneg _ );
      have h_sum_counts : ∑ v, counts_profile M T1 T2 (M.MS.mutants f) v = (M.MS.mutants f).card := by
        simp +decide only [counts_profile];
        rw [ ← Finset.card_biUnion ] ; congr ; ext ; aesop;
        exact fun x _ y _ hxy => Finset.disjoint_left.mpr fun m hm₁ hm₂ => hxy <| by aesop;
      refine' ⟨ h_nonneg, _ ⟩;
      rw [ Finset.sum_congr rfl fun _ _ => prob_eq_count_div_total M f T1 T2 h_mutants_nonempty _ ];
      rw [ ← Finset.sum_div, div_eq_iff ] <;> norm_cast <;> aesop

/-! ### Final Theorem -/

theorem T5_5b_irreducibility_implies_positive_interaction_split
    {D1 D2 R : Type} [Fintype D1] [Fintype D2]
    (M : StructuredContextWithSplit D1 D2 R)
    (f : Program (D1 × D2) R)
    (T T1 T2 : TestSuite (D1 × D2) R)
    (h_irr : ProperlyIrreducible f)
    (h_slice : is_slice_complete_struct (toStructuredContext M) f T T1 T2)
    (h_cross_hidden : ∀ m ∈ M.cross_mutants, detection_vector_scalar (toStructuredContext M) T1 T2 m = (fun _ => false))
    (h_cross_nonempty : M.cross_mutants.Nonempty)
    (h_local_independence :
      let n00 := counts_profile M T1 T2 M.local_mutants (fun _ => false)
      let n11 := counts_profile M T1 T2 M.local_mutants (fun _ => true)
      let n10 := counts_profile M T1 T2 M.local_mutants (fun i => if i = 0 then true else false)
      let n01 := counts_profile M T1 T2 M.local_mutants (fun i => if i = 1 then true else false)
      n00 * n11 = n10 * n01)
    (h_local_n11_pos : counts_profile M T1 T2 M.local_mutants (fun _ => true) > 0) :
    InteractionInformationScalar (toStructuredContext M) f T1 T2 > 0 := by
      have h_counts : counts_profile M T1 T2 (M.MS.mutants f) = counts_profile M T1 T2 M.local_mutants + counts_profile M T1 T2 M.cross_mutants := by
        ext v; simp +decide [ counts_profile, M.h_partition ] ;
        rw [ Finset.card_filter, Finset.card_filter, Finset.card_filter, Finset.sum_union ];
        exact M.h_disjoint;
      have h_counts_simplified : counts_profile M T1 T2 M.cross_mutants = fun v => if v = (fun _ => false) then M.cross_mutants.card else 0 := by
        ext v; simp [counts_profile, h_cross_hidden];
        split_ifs with hv;
        · exact congr_arg Finset.card ( Finset.filter_true_of_mem fun m hm => hv ▸ h_cross_hidden m hm );
        · exact Finset.card_eq_zero.mpr ( Finset.filter_eq_empty_iff.mpr fun m hm => by intro H; exact hv <| H.symm.trans <| h_cross_hidden m hm );
      have h_cross_product : (counts_profile M T1 T2 (M.MS.mutants f) (fun _ => false)) * (counts_profile M T1 T2 (M.MS.mutants f) (fun _ => true)) ≠ (counts_profile M T1 T2 (M.MS.mutants f) (fun i => if i = 0 then true else false)) * (counts_profile M T1 T2 (M.MS.mutants f) (fun i => if i = 1 then true else false)) := by
        simp_all +decide [ mul_comm ];
        nlinarith [ show 0 < M.cross_mutants.card from Finset.card_pos.mpr h_cross_nonempty ];
      contrapose! h_cross_product;
      convert cross_product_counts_iff_probs_v5 M f T1 T2 _ |> Iff.mp <| ?_;
      · norm_cast;
      · have := M.h_partition; aesop;
      · have h_mutual_information_zero : mutual_information (distribution_of_scalar (toStructuredContext M) f T1 T2) = 0 := by
          exact le_antisymm h_cross_product ( mutual_information_nonneg _ <| distribution_of_scalar_is_distribution M f T1 T2 <| by
            have := M.h_partition; aesop; );
        convert independence_iff_cross_product_eq _ _ |>.1 h_mutual_information_zero using 1;
        · congr! 2;
          · exact funext fun i => by fin_cases i <;> rfl;
          · exact funext fun i => by fin_cases i <;> rfl;
        · congr! 2;
          exact funext fun i => by fin_cases i <;> rfl;
        · apply distribution_of_scalar_is_distribution;
          have := M.h_partition;
          exact ⟨ h_cross_nonempty.choose, this.symm ▸ Finset.mem_union_right _ ( h_cross_nonempty.choose_spec ) ⟩

theorem T5_5b_lemma_algebra_real (n00 n01 n10 n11 NC : ℕ)
    (h_indep : n00 * n11 = n10 * n01)
    (h_NC_pos : NC > 0)
    (h_n11_pos : n11 > 0) :
    ((n00 + NC : ℕ) : ℝ) * (n11 : ℝ) ≠ (n10 : ℝ) * (n01 : ℝ) := by
  norm_cast
  apply T5_5b_lemma_algebra n00 n01 n10 n11 NC h_indep h_NC_pos h_n11_pos

end T5_5b_IT

/-
T5.6 (Option B reformulation): Composition Bound for Specification Complexity.

Original T5.6 claimed I_M(f) ≤ O(K(h) · log |Mut|), using Kolmogorov complexity
of the coupling function h. The binary-search argument underlying that bound requires
structural assumptions on the mutation space that don't follow from the definitions
(and T5.3 shows K and κ can diverge, working against the claimed bridge).

This reformulation replaces the uncomputable K(h) with test description complexity:
the minimum number of tests needed to kill coupling mutants. The result is a provable
composition theorem for specification complexity under mutant-class decomposition.

Key insight: if non-equivalent mutants are covered by component classes, each killable
by its own suite, then the union suite achieves full SC and spec complexity is bounded
by the sum of component suite sizes. The coupling overhead (|T_h|) plays the role that
K(h) · log |Mut| played in the original — but is now a well-defined, computable
quantity grounded in the mutation system rather than in description length.
-/

/-- Killed set is monotone in the test suite: enlarging the suite can only kill more. -/
lemma killedSet_mono
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    {T₁ T₂ : TestSuite D R} (h : T₁ ⊆ T₂) :
    killedSet sem oracle MS P T₁ ⊆ killedSet sem oracle MS P T₂ := by
  intro m hm
  rw [killedSet, Finset.mem_filter] at hm ⊢
  obtain ⟨hm_mut, t, ht_mem, ht_kill⟩ := hm
  exact ⟨hm_mut, t, h ht_mem, ht_kill⟩

/-- Component specification complexity: minimum suite size to kill all mutants in a class.
    This replaces K(h) from the original T5.6 with a mutation-system-native quantity. -/
def componentSpecComplexity
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (M_class : Finset (Program D R)) : ℕ :=
  sInf { k | ∃ T : TestSuite D R, T.card = k ∧ M_class ⊆ killedSet sem oracle MS P T }

/-- T5.6: Test suite composition under mutant-class decomposition.
    If non-equivalent mutants are covered by three classes (component-A mutants,
    component-B mutants, coupling mutants), each killed by a dedicated suite,
    the union achieves full SC and spec complexity is bounded by the sum of
    component suite sizes.

    The coupling suite T_h captures the "interaction overhead" — the extra tests
    needed beyond component testing. Its size bounds the interaction information
    without invoking Kolmogorov complexity. -/
theorem T5_6_composition_bound
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    -- Non-equivalent mutants decompose into three classes
    (M_A M_B M_h : Finset (Program D R))
    (h_cover : nonEquivMutants sem MS P ⊆ M_A ∪ M_B ∪ M_h)
    -- Each class has a killing suite
    (T_A T_B T_h : TestSuite D R)
    (h_kills_A : M_A ⊆ killedSet sem oracle MS P T_A)
    (h_kills_B : M_B ⊆ killedSet sem oracle MS P T_B)
    (h_kills_h : M_h ⊆ killedSet sem oracle MS P T_h) :
    achievesFullSC' sem oracle MS P (T_A ∪ T_B ∪ T_h) ∧
    specComplexity sem oracle MS P ≤ T_A.card + T_B.card + T_h.card := by
  -- Subset embeddings into the union
  have h_A_sub : T_A ⊆ T_A ∪ T_B ∪ T_h := by
    intro x hx; exact Finset.mem_union_left _ (Finset.mem_union_left _ hx)
  have h_B_sub : T_B ⊆ T_A ∪ T_B ∪ T_h := by
    intro x hx; exact Finset.mem_union_left _ (Finset.mem_union_right _ hx)
  have h_h_sub : T_h ⊆ T_A ∪ T_B ∪ T_h := Finset.subset_union_right
  -- Part 1: the union achieves full SC
  have h_sc : achievesFullSC' sem oracle MS P (T_A ∪ T_B ∪ T_h) := by
    intro m hm
    have hm_class := h_cover hm
    simp only [Finset.mem_union] at hm_class
    rcases hm_class with (hm_A | hm_B) | hm_h
    · exact killedSet_mono sem oracle MS P h_A_sub (h_kills_A hm_A)
    · exact killedSet_mono sem oracle MS P h_B_sub (h_kills_B hm_B)
    · exact killedSet_mono sem oracle MS P h_h_sub (h_kills_h hm_h)
  refine ⟨h_sc, ?_⟩
  -- Part 2: spec complexity ≤ sum of suite sizes
  have h_spec_le : specComplexity sem oracle MS P ≤ (T_A ∪ T_B ∪ T_h).card := by
    unfold specComplexity
    exact Nat.sInf_le ⟨T_A ∪ T_B ∪ T_h, rfl, h_sc⟩
  exact le_trans h_spec_le
    (le_trans (Finset.card_union_le _ _) (Nat.add_le_add_right (Finset.card_union_le _ _) _))

/-!
## GROUP M: Blum Axioms
-/

/-
T5.7b: Specification Complexity is Decidable for Finite Domains.
The predicate "κ(P) ≤ k" is decidable when D is finite.
-/
/-- Non-constructive decidability wrapper for the finite-domain existential form.
This uses classical choice over arbitrary `TestSuite` (whose `id : Nat` field makes
naive exhaustive search infinite). Prefer the constructive canonical checker below. -/
def T5_7b_decidable_finite_domain
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (k : ℕ) :
    Decidable (∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T) := by
  classical
  infer_instance

/-
Constructive finite-input regime for T5.7b (Lean scaffold):

Because `Test` includes an unconstrained `id : Nat`, exhaustive search over all
`TestSuite`s is not finite even when `D` is finite. For a constructive checker, we
restrict to canonical tests indexed by input and search over finite input subsets.

This gives an executable decision procedure for the canonicalized predicate. Linking it
back to full `specComplexity` requires an oracle extensionality hypothesis (same input
implies same oracle behavior), recorded explicitly below.
-/

/-- Canonical test at input `x` (fixed id). -/
def canonicalTest (x : D) : Test D R :=
  { id := 0, input := x }

/-- Embedding of inputs into canonical tests. -/
def canonicalTestEmbedding : D ↪ Test D R :=
  ⟨canonicalTest (D := D) (R := R), by
    intro x y hxy
    have hinputs : (canonicalTest (D := D) (R := R) x).input =
        (canonicalTest (D := D) (R := R) y).input :=
      congrArg Test.input hxy
    simpa [canonicalTest] using hinputs⟩

/-- Convert an input subset to its canonical test suite. -/
def suiteFromInputs (S : Finset D) : TestSuite D R :=
  S.map (canonicalTestEmbedding (D := D) (R := R))

/-- All input subsets of cardinality at most `k` (finite search space). -/
def inputSuitesLe [Fintype D] (k : ℕ) : Finset (Finset D) :=
  ((Finset.univ : Finset D).powerset).filter (fun S => S.card ≤ k)

/-- Boolean checker for canonicalized T5.7b search. -/
def checkCanonicalSuiteLe
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ) : Bool :=
  decide (∃ S ∈ inputSuitesLe (D := D) k,
    achievesFullSC' sem oracle MS P (suiteFromInputs S))

/-- Correctness of the canonical boolean checker. -/
theorem checkCanonicalSuiteLe_iff
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ) :
    checkCanonicalSuiteLe sem oracle MS P k = true ↔
      ∃ S ∈ inputSuitesLe (D := D) k,
        achievesFullSC' sem oracle MS P (suiteFromInputs S) := by
  unfold checkCanonicalSuiteLe
  simp

/-- Constructive decidability of canonicalized bounded-suite search (T5.7b scaffold). -/
def T5_7b_decidable_constructive_canonical
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (_h_oracle_input_ext : ∀ t₁ t₂ : Test D R, ∀ r : R,
      t₁.input = t₂.input → oracle t₁ r = oracle t₂ r) :
    Decidable (∃ S ∈ inputSuitesLe (D := D) k,
      achievesFullSC' sem oracle MS P (suiteFromInputs S)) := by
  classical
  infer_instance

/-- Bundled hypotheses for the T5.7 canonical checker completeness direction. -/
structure T5_7_CanonicalizationAssumptions
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (P : Program D R)
    (k : ℕ) : Prop where
  extract : specComplexity sem oracle MS P ≤ k →
    ∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T
  canonicalize :
    (∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T) →
    ∃ S ∈ inputSuitesLe (D := D) k,
      achievesFullSC' sem oracle MS P (suiteFromInputs (D := D) (R := R) S)

/-- Cardinality of canonicalized suite equals cardinality of its input set. -/
lemma suiteFromInputs_card
    [DecidableEq D] (S : Finset D) :
    (suiteFromInputs (D := D) (R := R) S).card = S.card := by
  simpa [suiteFromInputs] using
    (Finset.card_map (canonicalTestEmbedding (D := D) (R := R)) S)

/-- Bridge: a canonical finite-input witness yields the original bounded-suite witness form. -/
theorem canonical_input_witness_to_bounded_suite
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ) :
    (∃ S ∈ inputSuitesLe (D := D) k,
      achievesFullSC' sem oracle MS P (suiteFromInputs (D := D) (R := R) S)) →
    ∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T := by
  intro h
  rcases h with ⟨S, hS_mem, hS_sc⟩
  refine ⟨suiteFromInputs (D := D) (R := R) S, ?_, hS_sc⟩
  have hS_le : S.card ≤ k := (Finset.mem_filter.mp hS_mem).2
  simpa [suiteFromInputs_card (D := D) (R := R) S] using hS_le

/-- Checker-success bridge to the original bounded-suite existential shape. -/
theorem checkCanonicalSuiteLe_true_to_bounded_suite
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (hcheck : checkCanonicalSuiteLe sem oracle MS P k = true) :
    ∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T := by
  have hwit : ∃ S ∈ inputSuitesLe (D := D) k,
      achievesFullSC' sem oracle MS P (suiteFromInputs (D := D) (R := R) S) :=
    (checkCanonicalSuiteLe_iff sem oracle MS P k).1 hcheck
  exact canonical_input_witness_to_bounded_suite sem oracle MS P k hwit

/-- Canonical checker soundness: if it returns true, then `specComplexity ≤ k`. -/
theorem checkCanonicalSuiteLe_true_implies_specComplexity_le
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (hcheck : checkCanonicalSuiteLe sem oracle MS P k = true) :
    specComplexity sem oracle MS P ≤ k := by
  rcases checkCanonicalSuiteLe_true_to_bounded_suite sem oracle MS P k hcheck with
    ⟨T, hT_le, hT_sc⟩
  have hspec_le_card : specComplexity sem oracle MS P ≤ T.card := by
    unfold specComplexity
    exact Nat.sInf_le ⟨T, rfl, hT_sc⟩
  exact le_trans hspec_le_card hT_le

/-- Completeness-direction skeleton: under an explicit canonicalization hypothesis,
`specComplexity ≤ k` implies canonical checker success. -/
theorem specComplexity_le_implies_checkCanonicalSuiteLe_true
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (h_extract : specComplexity sem oracle MS P ≤ k →
      ∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T)
    (h_canonicalize :
      (∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T) →
      ∃ S ∈ inputSuitesLe (D := D) k,
        achievesFullSC' sem oracle MS P (suiteFromInputs (D := D) (R := R) S))
    (hκ : specComplexity sem oracle MS P ≤ k) :
    checkCanonicalSuiteLe sem oracle MS P k = true := by
  have h_witness : ∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T :=
    h_extract hκ
  have hS : ∃ S ∈ inputSuitesLe (D := D) k,
      achievesFullSC' sem oracle MS P (suiteFromInputs (D := D) (R := R) S) :=
    h_canonicalize h_witness
  exact (checkCanonicalSuiteLe_iff sem oracle MS P k).2 hS

/-- Bidirectional canonical checker criterion for `specComplexity ≤ k`
under explicit canonicalization. -/
theorem checkCanonicalSuiteLe_true_iff_specComplexity_le
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (h_extract : specComplexity sem oracle MS P ≤ k →
      ∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T)
    (h_canonicalize :
      (∃ T : TestSuite D R, T.card ≤ k ∧ achievesFullSC' sem oracle MS P T) →
      ∃ S ∈ inputSuitesLe (D := D) k,
        achievesFullSC' sem oracle MS P (suiteFromInputs (D := D) (R := R) S)) :
    checkCanonicalSuiteLe sem oracle MS P k = true ↔
    specComplexity sem oracle MS P ≤ k := by
  constructor
  · exact checkCanonicalSuiteLe_true_implies_specComplexity_le sem oracle MS P k
  · exact specComplexity_le_implies_checkCanonicalSuiteLe_true sem oracle MS P k h_extract h_canonicalize

/-- Bundled-assumption wrapper for the T5.7 canonical checker equivalence. -/
theorem checkCanonicalSuiteLe_true_iff_specComplexity_le_of_assumptions
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (hA : T5_7_CanonicalizationAssumptions sem oracle MS P k) :
    checkCanonicalSuiteLe sem oracle MS P k = true ↔
    specComplexity sem oracle MS P ≤ k := by
  exact checkCanonicalSuiteLe_true_iff_specComplexity_le sem oracle MS P k hA.extract hA.canonicalize

/-- Constructive finite-domain decidability for `specComplexity ≤ k`, via canonical
input-indexed test suites. This avoids classical choice over arbitrary test IDs. -/
def T5_7b_decidable_specComplexity_le_constructive
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (k : ℕ)
    (hA : T5_7_CanonicalizationAssumptions sem oracle MS P k) :
    Decidable (specComplexity sem oracle MS P ≤ k) := by
  refine decidable_of_iff (checkCanonicalSuiteLe sem oracle MS P k = true) ?_
  exact checkCanonicalSuiteLe_true_iff_specComplexity_le_of_assumptions sem oracle MS P k hA

/-
T5.8: Blum Axiom 1 — Definedness.

For total functions over finite domains with a faithful oracle, specification complexity
is always defined (finite). The key insight is that oracle faithfulness + finite domain
together derive the distinguishability hypothesis that T5.1 requires — the oracle can
detect every semantic difference, and finiteness guarantees witnesses exist.

Option A (below): Derives h_distinguishable from OracleFaithful, then applies T5.1.
Option B (skeleton): Models partial programs via Option R, defines κ as ℕ∞,
  states the biconditional Φ(P) defined ↔ P total.
-/

/-- Every non-equivalent mutant is distinguishable when the oracle is faithful.
    Faithfulness + finite domain gives a test witness for every semantic difference. -/
lemma distinguishable_of_faithful
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_faithful : OracleFaithful sem oracle P) :
    ∀ m ∈ nonEquivMutants sem MS P, ∃ t : Test D R, passes sem oracle P t ≠ passes sem oracle m t := by
  intro m hm
  rw [nonEquivMutants, Finset.mem_filter] at hm
  obtain ⟨_, h_ne⟩ := hm
  -- h_ne : ¬ trueEquiv sem P m, i.e., ¬ ∀ x, sem P x = sem m x
  unfold trueEquiv at h_ne
  push_neg at h_ne
  obtain ⟨d, hd⟩ := h_ne
  exact ⟨⟨0, d⟩, h_faithful ⟨0, d⟩ m hd⟩

/-- Blum Axiom 1 (Option A): finite domain + faithful oracle → κ(P) ≤ |nonEquivMutants(P)|.
    The oracle faithfulness hypothesis replaces the raw distinguishability assumption,
    making this a genuine property of the (domain, oracle) pair rather than a tautology. -/
theorem T5_8_blum_axiom_1
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_faithful : OracleFaithful sem oracle P) :
    specComplexity sem oracle MS P ≤ (nonEquivMutants sem MS P).card := by
  have h_distinguishable := distinguishable_of_faithful sem oracle MS P h_faithful
  rcases T5_1_spec_complexity_finite sem oracle MS P h_distinguishable with ⟨T, hT_card, hT_sc⟩
  unfold specComplexity
  exact le_trans (Nat.sInf_le ⟨T, rfl, hT_sc⟩) hT_card

/-- Convenience: the old signature with raw h_distinguishable, for call sites
    that already have the hypothesis (e.g., T5.13a). -/
theorem T5_8_blum_axiom_1' [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_distinguishable : ∀ m ∈ nonEquivMutants sem MS P, ∃ t : Test D R, passes sem oracle P t ≠ passes sem oracle m t) :
    specComplexity sem oracle MS P ≤ (nonEquivMutants sem MS P).card := by
  rcases T5_1_spec_complexity_finite sem oracle MS P h_distinguishable with ⟨T, hT_card, hT_sc⟩
  unfold specComplexity
  exact le_trans (Nat.sInf_le ⟨T, rfl, hT_sc⟩) hT_card

/-!
### Option B skeleton: Partial programs and the true Blum biconditional

Models partial semantics, defines κ as ℕ∞ (⊤ when undefined), and states the
biconditional only under an explicit obstruction schema for non-total programs.
In finite domains, `κ∞ ≠ ⊤ → total` is generally too strong without extra assumptions,
so we factor the forward direction through a "no finite certificate" condition.
-/

section BlumAxiom1_OptionB

/-- Partial semantics: programs may diverge on some inputs. -/
def PartialSem (D R : Type) := Program D R → D → Option R

/-- A program is total under partial semantics if it halts on every input. -/
def IsTotalProg (psem : PartialSem D R) (P : Program D R) : Prop :=
  ∀ d : D, (psem P d).isSome

/-- Partial oracle: returns none when the program diverges. -/
def partialPasses (psem : PartialSem D R) (oracle : Test D R → R → Bool)
    (P : Program D R) (t : Test D R) : Option Bool :=
  (psem P t.input).map (oracle t)

/-- Partial specification complexity: uses ℕ∞ so that sInf ∅ = ⊤ (undefined). -/
def partialSpecComplexity (psem : PartialSem D R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) : ℕ∞ :=
  sInf { (k : ℕ∞) | ∃ T : TestSuite D R, (T.card : ℕ∞) = k ∧
    ∀ m ∈ (MS.mutants P).filter (fun m =>
      ∃ d, psem P d ≠ psem m d),
    ∃ t ∈ T, partialPasses psem oracle P t ≠ partialPasses psem oracle m t }

/-- Partial full-SC witness predicate used by `partialSpecComplexity`. -/
def partialAchievesFullSC (psem : PartialSem D R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Prop :=
  ∀ m ∈ (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d),
    ∃ t ∈ T, partialPasses psem oracle P t ≠ partialPasses psem oracle m t

/-- Forward obstruction schema: if no finite suite can certify partial full-SC,
    then `κ∞ = ⊤`. This is the robust forward direction in the partial setting. -/
theorem T5_8b_blum_forward_obstruction
  [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_no_suite : ∀ T : TestSuite D R, ¬ partialAchievesFullSC psem oracle MS P T) :
    partialSpecComplexity psem oracle MS P = ⊤ := by
  let S : Set ℕ∞ :=
    {k : ℕ∞ | ∃ T : TestSuite D R, (T.card : ℕ∞) = k ∧
      ∀ m ∈ (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d),
        ∃ t ∈ T, partialPasses psem oracle P t ≠ partialPasses psem oracle m t}
  have h_empty : S = (∅ : Set ℕ∞) := by
    ext k
    constructor
    · intro hk
      rcases hk with ⟨T, _, hT⟩
      exact (h_no_suite T) (by simpa [partialAchievesFullSC] using hT)
    · intro hk
      exact False.elim (Set.notMem_empty k hk)
  have h_def : partialSpecComplexity psem oracle MS P = sInf S := by
    simp [partialSpecComplexity, S]
  rw [h_def, h_empty]
  exact (sInf_empty : sInf (∅ : Set ℕ∞) = (⊤ : ℕ∞))

/-- Contrapositive form of the forward schema: if `κ∞ ≠ ⊤`, then some finite suite
    satisfies the partial full-SC witness predicate. -/
theorem T5_8b_blum_forward_nonempty_certificate
  [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_finite : partialSpecComplexity psem oracle MS P ≠ ⊤) :
    ∃ T : TestSuite D R, partialAchievesFullSC psem oracle MS P T := by
  by_contra h_not
  have h_no_suite : ∀ T : TestSuite D R, ¬ partialAchievesFullSC psem oracle MS P T := by
    intro T hT
    exact h_not ⟨T, hT⟩
  exact h_finite (T5_8b_blum_forward_obstruction psem oracle MS P h_no_suite)

/-- Practical obstruction package for Option B forward reasoning.
    `evade_any_finite_suite` says: whenever `P` is non-total, for every finite suite `T`
    there exists a behaviorally different mutant that survives all tests in `T` under
    partial observations. This is exactly the witness pattern needed to prove
    `¬ partialAchievesFullSC ... T` for every `T`. -/
structure T5_8b_ForwardObstructionPackage
  [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) : Prop where
  evade_any_finite_suite :
    ¬ IsTotalProg psem P →
    ∀ T : TestSuite D R,
      ∃ m,
        m ∈ (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d) ∧
        (∀ t ∈ T, partialPasses psem oracle P t = partialPasses psem oracle m t)

/-- Convert the practical package into the obstruction hypothesis used by
    `T5_8b_blum_axiom_1_iff_under_obstruction`. -/
theorem T5_8b_forward_obstruction_from_package
  [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (hPkg : T5_8b_ForwardObstructionPackage psem oracle MS P) :
    ¬ IsTotalProg psem P →
    ∀ T : TestSuite D R, ¬ partialAchievesFullSC psem oracle MS P T := by
  intro h_not_total T hT
  obtain ⟨m, hm_mem, hm_survive⟩ := hPkg.evade_any_finite_suite h_not_total T
  have hT' :
      ∀ m ∈ (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d),
        ∃ t ∈ T, partialPasses psem oracle P t ≠ partialPasses psem oracle m t := by
    simpa [partialAchievesFullSC] using hT
  obtain ⟨t, ht_mem, ht_neq⟩ := hT' m hm_mem
  exact ht_neq (hm_survive t ht_mem)

/-- Blum Axiom 1, reverse direction (strong form): if the oracle is faithful,
    then `κ∞(P) < ⊤` without requiring `[Fintype D]`.
    The proof uses finiteness of `MS.mutants P` (a Finset), choosing one
    distinguishing test input per behaviorally different mutant. -/
theorem T5_8b_blum_reverse_strong
    [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_faithful : ∀ (t : Test D R) (m : Program D R),
      psem P t.input ≠ psem m t.input → partialPasses psem oracle P t ≠ partialPasses psem oracle m t) :
    partialSpecComplexity psem oracle MS P ≠ ⊤ := by
  classical
  let M : Finset (Program D R) :=
    (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d)
  let T : TestSuite D R :=
    M.attach.image (fun (msub : {m // m ∈ M}) =>
      let hm_filter : msub.1 ∈ (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d) := by
        simpa [M] using msub.2
      { id := 0,
        input := Classical.choose ((Finset.mem_filter.mp hm_filter).2) })
  have hT_sc : partialAchievesFullSC psem oracle MS P T := by
    intro m hm
    let d : D := Classical.choose ((Finset.mem_filter.mp hm).2)
    have hd : psem P d ≠ psem m d :=
      Classical.choose_spec ((Finset.mem_filter.mp hm).2)
    let t : Test D R := { id := 0, input := d }
    have ht_mem : t ∈ T := by
      unfold T
      refine Finset.mem_image.mpr ?_
      refine ⟨⟨m, hm⟩, by simp, ?_⟩
      unfold t d
      simp
    refine ⟨t, ht_mem, ?_⟩
    exact h_faithful t m hd
  have h_mem : (T.card : ℕ∞) ∈
      { (k : ℕ∞) | ∃ T : TestSuite D R, (T.card : ℕ∞) = k ∧
        ∀ m ∈ (MS.mutants P).filter (fun m => ∃ d, psem P d ≠ psem m d),
          ∃ t ∈ T, partialPasses psem oracle P t ≠ partialPasses psem oracle m t } := by
    exact ⟨T, rfl, by simpa [partialAchievesFullSC] using hT_sc⟩
  have h_le : partialSpecComplexity psem oracle MS P ≤ (T.card : ℕ∞) := by
    unfold partialSpecComplexity
    exact csInf_le ⟨(0 : ℕ∞), by intro k hk; exact OrderBot.bot_le k⟩ h_mem
  exact ne_of_lt (lt_of_le_of_lt h_le (WithTop.coe_lt_top (Finset.card T)))

/-- Blum Axiom 1, reverse direction: if P is total over a finite domain, then κ∞(P) < ⊤.
    Follows the same structure as Option A — totality + faithful oracle gives finiteness. -/
theorem T5_8b_blum_reverse
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_total : IsTotalProg psem P)
    (h_faithful : ∀ (t : Test D R) (m : Program D R),
      psem P t.input ≠ psem m t.input → partialPasses psem oracle P t ≠ partialPasses psem oracle m t) :
    partialSpecComplexity psem oracle MS P ≠ ⊤ := by
  exact T5_8b_blum_reverse_strong psem oracle MS P h_faithful

/-- Conditional Blum biconditional schema (Option B):
    if non-totality implies finite-suite obstruction, then `κ∞ ≠ ⊤ ↔ total`.
    This isolates the true technical burden in the forward direction. -/
theorem T5_8b_blum_axiom_1_iff_under_obstruction
  [DecidableEq D] [DecidableEq R]
    (psem : PartialSem D R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_forward_obstruction :
      ¬ IsTotalProg psem P →
      ∀ T : TestSuite D R, ¬ partialAchievesFullSC psem oracle MS P T)
    (h_faithful : ∀ (t : Test D R) (m : Program D R),
      psem P t.input ≠ psem m t.input → partialPasses psem oracle P t ≠ partialPasses psem oracle m t) :
    partialSpecComplexity psem oracle MS P ≠ ⊤ ↔ IsTotalProg psem P := by
  constructor
  · intro h_finite
    by_contra h_not_total
    have h_no_suite := h_forward_obstruction h_not_total
    have h_top : partialSpecComplexity psem oracle MS P = ⊤ :=
      T5_8b_blum_forward_obstruction psem oracle MS P h_no_suite
    exact h_finite h_top
  · intro h_total
    exact T5_8b_blum_reverse_strong psem oracle MS P h_faithful

end BlumAxiom1_OptionB

/-!
## GROUP N: The Verification Boundary
-/

/-
T5.10: Verification is Easier than Computation.
Conditional quasi-linear bound via logarithmic test covers.
-/

def HasLogarithmicCover_P5
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) : Prop :=
  ∃ C_cover, ∀ P : Program D R,
    ∃ T : TestSuite D R,
      achievesFullSC' sem oracle MS P T ∧
      T.card ≤ C_cover * (Nat.log2 (MS.mutants P).card + 1)

def MutantLogBoundBySize_P5
    (MS : MutationSystem D R) : Prop :=
  ∃ C_log, ∀ P : Program D R,
    Nat.log2 (MS.mutants P).card ≤ Nat.log2 P.id + C_log

theorem T5_10_verification_easier_mutant_log
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (h_log_cover : HasLogarithmicCover_P5 sem oracle MS) :
    ∃ C_vcc, ∀ P : Program D R,
      specComplexity sem oracle MS P ≤ C_vcc * (Nat.log2 (MS.mutants P).card + 1) := by
  rcases h_log_cover with ⟨C_cover, h_cover⟩
  refine ⟨C_cover, ?_⟩
  intro P
  rcases h_cover P with ⟨T, hT_sc, hT_card⟩
  have h_spec_le_card : specComplexity sem oracle MS P ≤ T.card := by
    unfold specComplexity
    exact Nat.sInf_le ⟨T, rfl, hT_sc⟩
  exact le_trans h_spec_le_card hT_card

theorem T5_10_verification_easier
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (h_linear : ∃ C_mut, ∀ P : Program D R, (MS.mutants P).card ≤ C_mut * P.id)
    (h_log_cover : HasLogarithmicCover_P5 sem oracle MS)
    (h_log_size : MutantLogBoundBySize_P5 MS) :
    ∃ C_vcc, ∀ P : Program D R,
      specComplexity sem oracle MS P ≤ C_vcc * (Nat.log2 P.id + 1) := by
  rcases h_log_cover with ⟨C_cover, h_cover⟩
  rcases h_log_size with ⟨C_log, h_log_size'⟩
  have _h_linear := h_linear
  refine ⟨C_cover * (C_log + 1), ?_⟩
  intro P
  rcases h_cover P with ⟨T, hT_sc, hT_card⟩
  have h_spec_le_card : specComplexity sem oracle MS P ≤ T.card := by
    unfold specComplexity
    exact Nat.sInf_le ⟨T, rfl, hT_sc⟩
  have hlog_step : Nat.log2 (MS.mutants P).card + 1 ≤ Nat.log2 P.id + (C_log + 1) := by
    nlinarith [h_log_size' P]
  have h_card1 : T.card ≤ C_cover * (Nat.log2 P.id + (C_log + 1)) := by
    exact le_trans hT_card (Nat.mul_le_mul_left _ hlog_step)
  have h_inner : Nat.log2 P.id + (C_log + 1) ≤ (C_log + 1) * (Nat.log2 P.id + 1) := by
    nlinarith [Nat.zero_le (Nat.log2 P.id), Nat.zero_le C_log]
  have h_card2 : C_cover * (Nat.log2 P.id + (C_log + 1)) ≤ C_cover * ((C_log + 1) * (Nat.log2 P.id + 1)) :=
    Nat.mul_le_mul_left _ h_inner
  have h_target : C_cover * ((C_log + 1) * (Nat.log2 P.id + 1)) = (C_cover * (C_log + 1)) * (Nat.log2 P.id + 1) := by
    ring_nf
  exact le_trans h_spec_le_card (le_trans h_card1 (le_trans h_card2 (by simpa [h_target])))

/-!
### T5.11: Verification Can Be Exponentially Easier Than Computation (Aristotle)

Constructive proof using Shannon's bound that VCC can be linear while circuit
complexity is exponential. For each n, the hard function f_n from Shannon's bound
has circuit complexity ≥ c^n (c > 1), but a sparse mutation system (n point mutants)
allows verification in O(n) tests.

Co-authored-by: Aristotle (Harmonic) <aristotle-harmonic@harmonic.fun>
-/

noncomputable def f_family (CT : ComplexityTheory) (n : Nat) : (Fin n → Bool) → Bool :=
  Classical.choose (CT.shannon_bound n)

def P_family (n : Nat) : Program (Fin n → Bool) Bool :=
  { id := 0 }

def input_k (n : Nat) (k : Fin n) : Fin n → Bool :=
  fun j => j == k

noncomputable def sem_family (CT : ComplexityTheory) (n : Nat) (p : Program (Fin n → Bool) Bool) (x : Fin n → Bool) : Bool :=
  let f := f_family CT n
  if p.id == 0 then f x
  else if h : p.id <= n then
    let k_idx := p.id - 1
    if hk : k_idx < n then
      let k : Fin n := ⟨k_idx, hk⟩
      if x = input_k n k then !f x else f x
    else f x
  else f x

def MS_family (n : Nat) : MutationSystem (Fin n → Bool) Bool :=
  { mutants := fun p =>
      if p.id == 0 then
        (Finset.range n).image (fun i => { id := i + 1 })
      else ∅ }

def oracle_family (n : Nat) : Test (Fin n → Bool) Bool → Bool → Bool :=
  fun _ r => r

theorem T5_11_Verification_Can_Be_Exponentially_Easier_Than_Computation
  (CT : ComplexityTheory) :
  ∃ (f : (n : Nat) → (Fin n → Bool) → Bool)
    (sem : (n : Nat) → Program (Fin n → Bool) Bool → (Fin n → Bool) → Bool)
    (MS : (n : Nat) → MutationSystem (Fin n → Bool) Bool)
    (P : (n : Nat) → Program (Fin n → Bool) Bool),
    (∀ n, sem n (P n) = f n) ∧
    (∃ (c : Real), c > 1 ∧ ∀ n, n ≥ 10 → (CT.circuit_complexity (f n) : Real) ≥ c ^ n) ∧
    (∃ (C : Nat), ∀ n, ProperSpecComplexity (sem n) (MS n) (P n) ≤ C * n) := by
  refine ⟨f_family CT, sem_family CT, MS_family, P_family, ?_, ?_, ?_⟩
  · intro n
    funext x
    unfold sem_family P_family
    simp
  · -- Circuit complexity
    refine' ⟨ 1 + 1 / 2048, _, _ ⟩ <;> norm_num;
    intro n hn; have := Classical.choose_spec ( CT.shannon_bound n ) ; norm_num at *;
    refine' le_trans _ ( Nat.cast_le.mpr this ) |> le_trans _;
    rotate_left;
    exact ( 2 ^ n / ( 3 * n ) : ℝ ) - 1;
    · rw [ sub_le_iff_le_add ];
      rw [ div_le_iff₀ ] <;> norm_cast <;> linarith [ Nat.div_add_mod ( 2 ^ n ) ( 3 * n ), Nat.mod_lt ( 2 ^ n ) ( by positivity : 0 < ( 3 * n ) ) ];
    · refine' Nat.le_induction _ _ n hn <;> norm_num;
      intro n hn ih; norm_num [ pow_succ' ] at *;
      rw [ le_sub_iff_add_le, le_div_iff₀ ] at * <;> nlinarith [ ( by norm_cast : ( 10 : ℝ ) ≤ n ), pow_pos ( by norm_num : ( 0 : ℝ ) < 2049 / 2048 ) n, pow_pos ( by norm_num : ( 0 : ℝ ) < 2 ) n ]
  · -- VCC
    use 1;
    intro n
    have h_spec_complexity : ∃ T : ProperTestSuite (Fin n → Bool) Bool, T.length ≤ n ∧ ProperAchievesFullSC (sem_family CT n) (MS_family n) (P_family n) T := by
      use List.map (fun k => (input_k n k, fun r => r == !sem_family CT n (⟨k + 1⟩) (input_k n k))) (List.finRange n);
      simp +zetaDelta at *;
      intro m hm; simp_all +decide [ MS_family, P_family ] ;
      obtain ⟨ a, ha, rfl ⟩ := hm; use ⟨ a, ha ⟩ ; simp +decide [ kills, sem_family ] ;
    generalize_proofs at *; (
    exact le_trans ( csInf_le ⟨ 0, by rintro x ⟨ T, rfl, hT ⟩ ; exact Nat.zero_le _ ⟩ ⟨ h_spec_complexity.choose, rfl, h_spec_complexity.choose_spec.2 ⟩ ) ( by simpa using h_spec_complexity.choose_spec.1 ))

/-
T5.12 (Upper Bound): Greedy Synthesis Achieves O(OPT · ln n) Convergence.
The greedy algorithm's test count is within O(ln n) of optimal.
This follows from the set cover connection (T2.8) applied to the mutation killing structure.
-/

def IsOptimalSuite (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Prop :=
  achievesFullSC' sem oracle MS P T ∧
  ∀ T' : TestSuite D R, achievesFullSC' sem oracle MS P T' → T.card ≤ T'.card

def distinctKillSets_P5 (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T_univ : TestSuite D R) : Finset (Finset (Program D R)) :=
  (T_univ.image (fun t => killedSet sem oracle MS P {t})).filter (fun s => s.Nonempty)

def IsGreedySequence_P5 (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T_univ : TestSuite D R) (seq : ℕ → Test D R) : Prop :=
  ∀ k, seq k ∈ T_univ ∧
  ∀ t ∈ T_univ, (killedSet sem oracle MS P {t} \ (Finset.range k).biUnion (fun i => killedSet sem oracle MS P {seq i})).card ≤
                (killedSet sem oracle MS P {seq k} \ (Finset.range k).biUnion (fun i => killedSet sem oracle MS P {seq i})).card

def Harmonic_P5 (n : ℕ) : ℝ :=
  (Finset.range n).sum (fun i => 1 / ((i : ℝ) + 1))

lemma Harmonic_P5_eq_harmonic (n : ℕ) :
    Harmonic_P5 n = (harmonic n : ℝ) := by
  unfold Harmonic_P5 harmonic
  norm_num [Rat.cast_sum, Rat.cast_inv, Rat.cast_natCast]

lemma Harmonic_P5_le_log_add_one (n : ℕ) :
    Harmonic_P5 n ≤ Real.log n + 1 := by
  rw [Harmonic_P5_eq_harmonic]
  simpa [add_comm, add_left_comm, add_assoc] using (harmonic_le_one_add_log n)

theorem T5_12_greedy_approximation_ratio
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (T_univ : TestSuite D R)
    (seq : ℕ → Test D R)
    (h_greedy : IsGreedySequence_P5 sem oracle MS P T_univ seq)
    (k_greedy : ℕ)
    (h_covers : nonEquivMutants sem MS P ⊆ (Finset.range k_greedy).biUnion (fun i => killedSet sem oracle MS P {seq i}))
    (T_opt : TestSuite D R)
    (h_opt : achievesFullSC' sem oracle MS P T_opt)
    (n : ℕ)
    (h_greedy_harmonic : (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n)
    (h_n : n = (nonEquivMutants sem MS P).card) (h_n_pos : 0 < n) :
    (k_greedy : ℝ) ≤ T_opt.card * (Real.log n + 1) := by
  have h_harmonic_log : Harmonic_P5 n ≤ Real.log n + 1 := Harmonic_P5_le_log_add_one n
  have h_mul : T_opt.card * Harmonic_P5 n ≤ T_opt.card * (Real.log n + 1) := by
    exact mul_le_mul_of_nonneg_left h_harmonic_log (by positivity)
  exact le_trans h_greedy_harmonic h_mul

/-!
## GROUP O: Parameterized Spec Classes SpecP_{μ,ρ}

T5.13 is a research program, not a single theorem. The key insight is that class-theoretic
meaning of SpecP depends entirely on the mutation policy μ and normalization policy ρ.

Regime A (local finite syntactic mutations): |Mut_μ(P)| = O(|P|), so P ⊆ SpecP and likely
  P/poly ⊆ SpecP. No separation is possible in this regime.
Regime B (richer semantic/global mutations): super-polynomial κ for some poly-size circuits
  becomes plausible; this is the only credible separation path.

This section provides:
- T5.13a: the Regime A bridge (κ ≤ |Mut(P)|, proven)
- T5.14: sufficient conditions for P/poly ⊆ SpecP (sorry — needs regime framework)
- T5.15: local-regime barrier (proven — direct from T5.13a)
- T5.18: mutation-policy monotonicity (proven)
-/

/-
T5.13a bridge (Regime A: local finite syntactic mutation regime):
This theorem is the program-level counting witness used in the prose claim
that, under local finite mutation policies, efficient computations have polynomial
specification complexity.

Important: this file proves only the finite/program-level inequality
κ_μ(P) ≤ |Mut_μ(P)|. The full class inclusion statement is parameterized as
SpecP_{μ,ρ} in the markdown and depends on explicit choices of (μ,ρ).

Regime U (maximal/universal mutation context) appears in prose-level counterexample
discussion (e.g., repaired interaction conjecture), not as a separate Lean theorem here.
-/
theorem T5_13a_P_subset_SpecP
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_distinguishable : ∀ m ∈ nonEquivMutants sem MS P,
      ∃ t : Test D R, passes sem oracle P t ≠ passes sem oracle m t) :
    specComplexity sem oracle MS P ≤ (MS.mutants P).card := by
  have h_blum : specComplexity sem oracle MS P ≤ (nonEquivMutants sem MS P).card :=
    T5_8_blum_axiom_1' sem oracle MS P h_distinguishable
  have h_sub : nonEquivMutants sem MS P ⊆ MS.mutants P := by
    intro m hm
    exact (Finset.mem_filter.mp hm).1
  have h_card : (nonEquivMutants sem MS P).card ≤ (MS.mutants P).card :=
    Finset.card_le_card h_sub
  exact le_trans h_blum h_card

/-
T5.14: Regime Characterization — Sufficient Conditions for P/poly ⊆ SpecP_{μ,ρ}.

Given:
  (i)   Polynomial mutant bound: |Mut_μ(P)| ≤ C · |P|^d for some constants C, d.
  (ii)  Distinguishability: every non-equivalent mutant has a distinguishing test.
  (iii) Representation compatibility: ρ chooses representations polynomially related
        to circuit size (captured here by the bound on P.id).

Then κ_μ(P) ≤ C · |P|^d, which is poly(n) for poly-size representations.

The proof chains T5.13a (κ ≤ |Mut|) with the polynomial mutant bound.
-/
theorem T5_14_regime_characterization
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (h_distinguishable : ∀ m ∈ nonEquivMutants sem MS P,
      ∃ t : Test D R, passes sem oracle P t ≠ passes sem oracle m t)
    -- (i) Polynomial mutant bound: |Mut(P)| ≤ bound
    (bound : ℕ)
    (h_poly_mutants : (MS.mutants P).card ≤ bound) :
    specComplexity sem oracle MS P ≤ bound := by
  exact le_trans (T5_13a_P_subset_SpecP sem oracle MS P h_distinguishable) h_poly_mutants

/-
T5.15: Local-Regime Barrier — No Super-Polynomial κ from Polynomial-Size Circuits.

In Regime A, if |Mut_μ(P)| ≤ C · |P|^d, then κ_μ(P) ≤ C · |P|^d = poly(n).
This regime cannot witness a separation between poly-size circuits and
super-polynomial specification complexity.

Consequence: any candidate for strong P/poly-vs-SpecP_{μ,ρ} separation must leave Regime A.
-/
theorem T5_15_local_regime_barrier
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R)
    -- Constants for the polynomial mutant bound
    (C_mut d : ℕ)
    -- The policy satisfies |Mut(P)| ≤ C_mut · |P|^d for all P
    (h_regime_A : ∀ P : Program D R, (MS.mutants P).card ≤ C_mut * P.id ^ d)
    -- Distinguishability holds for all programs
    (h_distinguishable : ∀ P : Program D R, ∀ m ∈ nonEquivMutants sem MS P,
      ∃ t : Test D R, passes sem oracle P t ≠ passes sem oracle m t) :
    ∀ P : Program D R, specComplexity sem oracle MS P ≤ C_mut * P.id ^ d := by
  intro P
  exact T5_14_regime_characterization sem oracle MS P (h_distinguishable P) _ (h_regime_A P)

/-!
### GROUP P: Post-T5.13 Regime and Class-Theory Extensions
-/

/-
T5.18: Mutation-Policy Monotonicity.

If mutation policy μ₁ generates a subset of μ₂'s mutants for every program,
then κ_{μ₁}(P) ≤ κ_{μ₂}(P) — fewer mutants to kill means lower or equal complexity.

Proof: any test suite achieving full SC under μ₂ also kills all non-equivalent
mutants under μ₁ (since they form a subset). Taking infima preserves the inequality.
-/
theorem T5_18_policy_monotonicity
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS₁ MS₂ : MutationSystem D R) (P : Program D R)
    -- Pointwise mutant inclusion: Mut_{μ₁}(P) ⊆ Mut_{μ₂}(P)
    (h_sub : MS₁.mutants P ⊆ MS₂.mutants P)
    -- Guard: at least one suite achieves full SC for MS₂. Without this,
    -- sInf ∅ = 0 on ℕ gives specComplexity MS₂ = 0 even when MS₂ has
    -- unkillable non-equiv mutants, while MS₁ (with fewer mutants) might
    -- have all mutants killable, giving specComplexity MS₁ > 0.
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS₂ P T) :
    specComplexity sem oracle MS₁ P ≤ specComplexity sem oracle MS₂ P := by
  -- Any suite achieving full SC for MS₂ also achieves full SC for MS₁,
  -- because nonEquivMutants MS₁ ⊆ nonEquivMutants MS₂ (mutant subset + same trueEquiv).
  -- So the MS₂ achievable-size set ⊆ MS₁ achievable-size set.
  -- The MS₂ set is nonempty (by h_achievable), and its sInf is a member.
  -- That member is also in the MS₁ set, so sInf(MS₁ set) ≤ that member = sInf(MS₂ set).
  unfold specComplexity
  -- Transfer: any MS₂ achieving suite also achieves for MS₁
  have h_transfer : ∀ T : TestSuite D R, achievesFullSC' sem oracle MS₂ P T →
      achievesFullSC' sem oracle MS₁ P T := by
    intro T hT_sc m hm
    rw [nonEquivMutants, Finset.mem_filter] at hm
    obtain ⟨hm_mut₁, hm_ne⟩ := hm
    have hm_ne₂ : m ∈ nonEquivMutants sem MS₂ P := by
      rw [nonEquivMutants, Finset.mem_filter]
      exact ⟨h_sub hm_mut₁, hm_ne⟩
    have hm_killed := hT_sc hm_ne₂
    rw [killedSet, Finset.mem_filter] at hm_killed ⊢
    exact ⟨hm_mut₁, hm_killed.2⟩
  -- The MS₂ achievable-size set is nonempty
  let S₂ := { k | ∃ T : TestSuite D R, T.card = k ∧ achievesFullSC' sem oracle MS₂ P T }
  have hS₂_ne : S₂.Nonempty := by
    obtain ⟨T₀, hT₀⟩ := h_achievable
    exact ⟨T₀.card, T₀, rfl, hT₀⟩
  -- sInf S₂ ∈ S₂ (Nat.sInf_mem), so there's a witness suite T★ with T★.card = sInf S₂
  have h_mem := Nat.sInf_mem hS₂_ne
  obtain ⟨T_star, hT_card, hT_sc₂⟩ := h_mem
  -- T★ also achieves full SC for MS₁
  have hT_sc₁ := h_transfer T_star hT_sc₂
  -- So sInf(MS₁ set) ≤ T★.card = sInf S₂ = specComplexity MS₂
  exact le_trans (Nat.sInf_le ⟨T_star, rfl, hT_sc₁⟩) (le_of_eq hT_card)

/-
T5.16: Verification Certificates Do Not Automatically Yield Evaluation Circuits.

A test suite achieving SC=1 uniquely identifies f among {P} ∪ Mut_μ(P), i.e., it is a
verification certificate (teaching set). But verification ≠ evaluation: knowing which
candidate is correct does not yield a circuit that computes f on unseen inputs.

This is formalized as the non-existence of a general "certificate-to-circuit" map:
given only (T, oracle outcomes), one cannot efficiently reconstruct sem P.
The missing axiom is a uniform synthesis principle from test suites to evaluation circuits.

Status: meta-formal boundary statement. The sorry captures the gap between
identification-among-candidates and evaluation-on-arbitrary-inputs.
-/

/-- A certificate-to-circuit reconstruction principle: from a test suite achieving
    full SC and the oracle outcomes, reconstruct the semantics of P on any input. -/
def HasCertificateReconstruction
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) : Prop :=
  ∃ reconstruct : TestSuite D R → (Test D R → Bool) → D → R,
    ∀ P : Program D R, ∀ T : TestSuite D R,
      achievesFullSC' sem oracle MS P T →
        ∀ d : D, reconstruct T (fun t => oracle t (sem P t.input)) d = sem P d

/-- T5.16: In general, no certificate-to-circuit reconstruction exists without
    an additional synthesis axiom. Specifically: full SC gives a verifier
    (test the candidate), not an evaluator (compute f on new inputs).
    The gap is that the test suite constrains behavior on tested inputs only;
    extending to untested inputs requires semantic extrapolation beyond what
    the mutation framework provides.
    Sorry: proving non-existence requires an explicit counterexample construction
    (e.g., two mutation systems with identical test behavior but different semantics
    on untested inputs). -/
theorem T5_16_no_automatic_reconstruction
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    -- The mutation system has programs with identical oracle fingerprints but different semantics.
    -- That is: P₁ and P₂ "look the same" to any test suite, but compute different functions.
    (h_ambiguity : ∃ P₁ P₂ : Program D R,
      -- P₁ and P₂ have identical oracle fingerprints (look the same to any test)
      (∀ t : Test D R, oracle t (sem P₁ t.input) = oracle t (sem P₂ t.input)) ∧
      -- SC transfers from P₁ to P₂ (any suite achieving SC for P₁ also works for P₂)
      (∀ T : TestSuite D R, achievesFullSC' sem oracle MS P₁ T → achievesFullSC' sem oracle MS P₂ T) ∧
      -- A witness suite exists achieving SC for P₁
      (∃ T₀ : TestSuite D R, achievesFullSC' sem oracle MS P₁ T₀) ∧
      -- But P₁ and P₂ compute different functions
      ¬ trueEquiv sem P₁ P₂) :
    ¬ HasCertificateReconstruction sem oracle MS := by
  intro ⟨reconstruct, h_rec⟩
  obtain ⟨P₁, P₂, h_same_oracle, h_sc_transfer, ⟨T₀, hT₀⟩, h_ne⟩ := h_ambiguity
  -- P₁ ≢ P₂: ∃ d, sem P₁ d ≠ sem P₂ d
  unfold trueEquiv at h_ne; push_neg at h_ne
  obtain ⟨d, hd⟩ := h_ne
  -- Oracle fingerprints are equal as functions
  have h_fp : (fun t => oracle t (sem P₁ t.input)) = (fun t => oracle t (sem P₂ t.input)) := by
    ext t; exact h_same_oracle t
  -- T₀ achieves SC for both P₁ and P₂
  have hT₀_P₂ := h_sc_transfer T₀ hT₀
  -- Reconstruction must agree with both:
  have h1 := h_rec P₁ T₀ hT₀ d      -- reconstruct T₀ fp₁ d = sem P₁ d
  have h2 := h_rec P₂ T₀ hT₀_P₂ d   -- reconstruct T₀ fp₂ d = sem P₂ d
  -- But fp₁ = fp₂, so the left sides are equal
  rw [h_fp] at h1
  -- h1: reconstruct T₀ fp₂ d = sem P₁ d
  -- h2: reconstruct T₀ fp₂ d = sem P₂ d
  exact hd (h1.symm.trans h2)

/-
T5.17: Teaching-Dimension Embedding Bounds Specification Complexity.

For a fixed P and mutation policy μ, the concept class is
  C_{P,μ} = { ⟦Q⟧ : Q ∈ {P} ∪ Mut_μ(P) }
where ⟦Q⟧ = (fun d => sem Q d) is the semantic function.

A test suite achieving SC=1 is exactly a teaching set: it uniquely identifies
⟦P⟧ inside C_{P,μ}. The teaching dimension TD(⟦P⟧, C_{P,μ}) is the minimum
size of such a set. So κ_μ(P) ≥ TD and κ_μ(P) ≤ |distinguishing set|.

This connects mutation specification directly to computational learning theory.
-/

/-- The concept class induced by a program and its mutants: functions D → R
    realized by P and its mutants. -/
def conceptClass (sem : Program D R → D → R) (MS : MutationSystem D R) (P : Program D R) :
    Finset (Program D R) :=
  {P} ∪ MS.mutants P

/-- A test suite "teaches" P if it uniquely identifies P's semantics among
    the concept class: every other program in the class with the same
    oracle outcomes on T is truly equivalent to P. -/
def isTeachingSet (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Prop :=
  ∀ Q ∈ conceptClass sem MS P,
    (∀ t ∈ T, passes sem oracle P t = passes sem oracle Q t) → trueEquiv sem P Q

/-- Teaching dimension: minimum size of a teaching set for P in C_{P,μ}. -/
def teachingDimension (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) : ℕ :=
  sInf { k | ∃ T : TestSuite D R, T.card = k ∧ isTeachingSet sem oracle MS P T }

lemma T5_17_fullSC_implies_teachingSet
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (T : TestSuite D R)
    (hT_sc : achievesFullSC' sem oracle MS P T) :
    isTeachingSet sem oracle MS P T := by
  intro Q hQ h_agree
  by_contra h_ne
  rw [conceptClass, Finset.mem_union, Finset.mem_singleton] at hQ
  rcases hQ with rfl | hQ_mut
  · exact h_ne (fun _ => rfl)
  · have hQ_neq : Q ∈ nonEquivMutants sem MS P := by
      rw [nonEquivMutants, Finset.mem_filter]
      exact ⟨hQ_mut, h_ne⟩
    have hQ_killed := hT_sc hQ_neq
    rw [killedSet, Finset.mem_filter] at hQ_killed
    obtain ⟨_, t, ht_mem, ht_kill⟩ := hQ_killed
    exact ht_kill (h_agree t ht_mem)

lemma T5_17_teachingSet_implies_fullSC
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (T : TestSuite D R)
    (hT_teach : isTeachingSet sem oracle MS P T) :
    achievesFullSC' sem oracle MS P T := by
  intro Q hQ_neq
  rw [killedSet, Finset.mem_filter]
  refine ⟨(Finset.mem_filter.mp hQ_neq).1, ?_⟩
  by_contra hQ_not_killed
  have h_agree : ∀ t ∈ T, passes sem oracle P t = passes sem oracle Q t := by
    intro t ht
    by_contra hneq
    exact hQ_not_killed ⟨t, ht, hneq⟩
  have hQ_class : Q ∈ conceptClass sem MS P := by
    rw [conceptClass, Finset.mem_union, Finset.mem_singleton]
    exact Or.inr (Finset.mem_filter.mp hQ_neq).1
  exact (Finset.mem_filter.mp hQ_neq).2 (hT_teach Q hQ_class h_agree)

/-- T5.17 lower bound: specification complexity is at least the teaching dimension.
    Any suite achieving full SC is a teaching set (it identifies P uniquely). -/
theorem T5_17_teaching_dimension_lower_bound
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    -- Guard: at least one suite achieves full SC (otherwise sInf ∅ = 0 breaks monotonicity)
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS P T) :
    teachingDimension sem oracle MS P ≤ specComplexity sem oracle MS P := by
  -- SC achievable set ⊆ TD achievable set, so sInf(TD) ≤ sInf(SC).
  -- Key sub-lemma: full SC ⇒ teaching set (if Q agrees with P on T, Q must be equivalent).
  let S_sc := { k | ∃ T : TestSuite D R, T.card = k ∧ achievesFullSC' sem oracle MS P T }
  let S_td := { k | ∃ T : TestSuite D R, T.card = k ∧ isTeachingSet sem oracle MS P T }
  -- Every achieving SC suite is also a teaching set
  suffices h_sub : S_sc ⊆ S_td by
    show sInf S_td ≤ sInf S_sc
    -- h_achievable guarantees S_sc is nonempty
    have hne : S_sc.Nonempty := by
      obtain ⟨T₀, hT₀⟩ := h_achievable
      exact ⟨T₀.card, T₀, rfl, hT₀⟩
    exact Nat.sInf_le (h_sub (Nat.sInf_mem hne))
  -- Prove the subset: SC ⊆ TD
  intro k ⟨T, hT_card, hT_sc⟩
  refine ⟨T, hT_card, T5_17_fullSC_implies_teachingSet sem oracle MS P T hT_sc⟩

/-- T5.17 reverse direction: every teaching set already achieves full SC.
    Combined with `T5_17_teaching_dimension_lower_bound`, this gives `TD = κ`. -/
theorem T5_17_spec_complexity_upper_bound_by_teaching_dimension
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS P T) :
    specComplexity sem oracle MS P ≤ teachingDimension sem oracle MS P := by
  let S_sc := { k | ∃ T : TestSuite D R, T.card = k ∧ achievesFullSC' sem oracle MS P T }
  let S_td := { k | ∃ T : TestSuite D R, T.card = k ∧ isTeachingSet sem oracle MS P T }
  have h_sub : S_td ⊆ S_sc := by
    intro k hk
    rcases hk with ⟨T, hT_card, hT_teach⟩
    exact ⟨T, hT_card, T5_17_teachingSet_implies_fullSC sem oracle MS P T hT_teach⟩
  have hne_td : S_td.Nonempty := by
    obtain ⟨T₀, hT₀_sc⟩ := h_achievable
    exact ⟨T₀.card, T₀, rfl, T5_17_fullSC_implies_teachingSet sem oracle MS P T₀ hT₀_sc⟩
  show sInf S_sc ≤ sInf S_td
  exact Nat.sInf_le (h_sub (Nat.sInf_mem hne_td))

theorem T5_17_teaching_dimension_eq_specComplexity
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS P T) :
    teachingDimension sem oracle MS P = specComplexity sem oracle MS P := by
  exact le_antisymm
    (T5_17_teaching_dimension_lower_bound sem oracle MS P h_achievable)
    (T5_17_spec_complexity_upper_bound_by_teaching_dimension sem oracle MS P h_achievable)

/-- T5.17 upper bound: specification complexity ≤ number of non-equivalent mutants
    (each needing one distinguishing test). This is essentially T5.1/T5.8 restated
    in teaching-dimension language. -/
theorem T5_17_teaching_dimension_upper_bound
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (h_faithful : OracleFaithful sem oracle P) :
    specComplexity sem oracle MS P ≤ (nonEquivMutants sem MS P).card := by
  exact T5_8_blum_axiom_1 sem oracle MS P h_faithful

/-
T5.19: Normalization Robustness Under Restricted Representation Categories.

For admissible representation policies ρ₁, ρ₂ over a restricted representation
category R (e.g., structure-preserving, non-obfuscatory encodings with bounded
simulation overhead), there exist polynomial distortion bounds:

  κ^{ρ₁}_μ(f) ≤ poly(n) · κ^{ρ₂}_μ(f)

This is conjectural and expected to fail in unrestricted or cryptographic-
obfuscatory encoding regimes.

The Lean skeleton captures the shape: given mutual representation transfer with
bounded blowup factors, the complexities are polynomially related.
-/

/-- A representation transfer between two mutation systems, abstracting ρ₁ → ρ₂.
    Each program in MS₁ has a representative in MS₂ with bounded mutant blowup
    and a test-suite transfer that preserves full SC. -/
structure RepresentationTransfer
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS₁ MS₂ : MutationSystem D R) where
  -- Blowup factor: mutant count in MS₂ is at most c times MS₁
  c : ℕ
  -- For every program, we can transfer test suites: achieving full SC for MS₁
  -- gives a (possibly larger) suite achieving full SC for MS₂.
  transfer : ∀ P : Program D R, ∀ T : TestSuite D R,
    achievesFullSC' sem oracle MS₁ P T →
      ∃ T' : TestSuite D R, achievesFullSC' sem oracle MS₂ P T' ∧ T'.card ≤ c * T.card

/-- T5.19: If there is a bounded representation transfer from MS₁ to MS₂,
    then specComplexity under MS₂ is bounded by c · specComplexity under MS₁. -/
theorem T5_19_normalization_robustness
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS₁ MS₂ : MutationSystem D R) (P : Program D R)
    (rt : RepresentationTransfer sem oracle MS₁ MS₂)
    -- At least one suite achieves full SC for MS₁
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS₁ P T) :
    specComplexity sem oracle MS₂ P ≤ rt.c * specComplexity sem oracle MS₁ P := by
  -- Get the optimal suite for MS₁ via Nat.sInf_mem
  have hS₁_ne : { k | ∃ T : TestSuite D R, T.card = k ∧ achievesFullSC' sem oracle MS₁ P T }.Nonempty := by
    obtain ⟨T₀, hT₀⟩ := h_achievable
    exact ⟨T₀.card, T₀, rfl, hT₀⟩
  obtain ⟨T_star, hT_card, hT_sc₁⟩ := Nat.sInf_mem hS₁_ne
  -- Transfer T★ to get T₂ achieving SC for MS₂
  obtain ⟨T₂, hT₂_sc, hT₂_card⟩ := rt.transfer P T_star hT_sc₁
  -- Chain: specComplexity MS₂ ≤ T₂.card ≤ c * T★.card = c * specComplexity MS₁
  calc specComplexity sem oracle MS₂ P
      ≤ T₂.card := Nat.sInf_le ⟨T₂, rfl, hT₂_sc⟩
    _ ≤ rt.c * T_star.card := hT₂_card
    _ = rt.c * specComplexity sem oracle MS₁ P := by unfold specComplexity; rw [hT_card]

/-
T5.20a: Finite Empirical Average-Case Specification Complexity.

Given a finite weight function over non-equivalent mutants (representing an
empirical workload distribution), define the (1-δ)-partial specification
complexity: minimum test suite size to kill at least (1-δ) fraction of the
weighted non-equivalent mutants. This relaxes the worst-case "kill all" requirement
to a weighted partial-cover requirement.
-/

/-- Weight function assigning importance to each mutant. -/
def MutantWeights (D R : Type) := Program D R → ℚ

/-- Weighted fraction of non-equivalent mutants killed by a test suite. -/
def weightedKillFraction
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (w : MutantWeights D R) (T : TestSuite D R) : ℚ :=
  let killed := killedSet sem oracle MS P T
  let ne := nonEquivMutants sem MS P
  let total_weight := ne.sum w
  if total_weight = 0 then 1
  else (killed ∩ ne).sum w / total_weight

/-- Partial specification complexity: minimum suite size to kill (1-δ) weight fraction. -/
def partialSpecComplexity_emp
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (w : MutantWeights D R) (delta : ℚ) : ℕ :=
  sInf { k | ∃ T : TestSuite D R, T.card = k ∧ weightedKillFraction sem oracle MS P w T ≥ 1 - delta }

/-- T5.20a: Partial specification complexity is bounded above by worst-case.
    Killing all mutants (SC=1) certainly kills (1-δ) fraction for any δ ≥ 0. -/
theorem T5_20a_partial_le_full
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (w : MutantWeights D R) (delta : ℚ) (h_delta : 0 ≤ delta)
    -- Guard: at least one suite achieves full SC (otherwise sInf ∅ = 0 breaks monotonicity)
    (h_achievable : ∃ T : TestSuite D R, achievesFullSC' sem oracle MS P T) :
    partialSpecComplexity_emp sem oracle MS P w delta ≤ specComplexity sem oracle MS P := by
  -- The full-SC achieving set ⊆ partial-SC achieving set:
  -- if T kills all non-equiv mutants, weightedKillFraction = 1 ≥ 1 - δ.
  -- So sInf(partial set) ≤ sInf(full set).
  let S_full := { k | ∃ T : TestSuite D R, T.card = k ∧ achievesFullSC' sem oracle MS P T }
  let S_part := { k | ∃ T : TestSuite D R, T.card = k ∧ weightedKillFraction sem oracle MS P w T ≥ 1 - delta }
  show sInf S_part ≤ sInf S_full
  -- Prove S_full ⊆ S_part
  suffices h_sub : S_full ⊆ S_part by
    -- h_achievable guarantees S_full is nonempty
    have hne : S_full.Nonempty := by
      obtain ⟨T₀, hT₀⟩ := h_achievable
      exact ⟨T₀.card, T₀, rfl, hT₀⟩
    exact Nat.sInf_le (h_sub (Nat.sInf_mem hne))
  intro k ⟨T, hT_card, hT_sc⟩
  refine ⟨T, hT_card, ?_⟩
  -- Show weightedKillFraction ≥ 1 - delta
  unfold weightedKillFraction
  simp only
  split_ifs with h_zero
  · linarith
  · have h_inter : killedSet sem oracle MS P T ∩ nonEquivMutants sem MS P = nonEquivMutants sem MS P := by
      ext m; simp only [Finset.mem_inter]
      exact ⟨fun h => h.2, fun h => ⟨hT_sc h, h⟩⟩
    rw [h_inter, div_self h_zero]
    linarith

/-
T5.20b: Distributional Average-Case Specification Complexity.

Extends T5.20a from single-program analysis to distributional (expected-value)
specification complexity over a finite ensemble of programs. Uses explicit
Finset-weighted distributions, avoiding MeasureTheory imports.

Three results:
  (a) Expected specification complexity ≤ worst-case bound.
  (b) Expected specification complexity is monotone in mutation policy (distributional T5.18).
  (c) Expected complexity is nonneg (trivial but closes the bound interval).
-/

/-- A finite probability distribution over programs. -/
structure ProgramDist (D R : Type) where
  support : Finset (Program D R)
  weight : Program D R → ℚ
  h_nonneg : ∀ P ∈ support, 0 ≤ weight P
  h_sum_one : support.sum weight = 1

/-- Expected specification complexity under a finite program distribution. -/
noncomputable def expectedSpecComplexity
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (dist : ProgramDist D R) : ℚ :=
  dist.support.sum (fun P => dist.weight P * (specComplexity sem oracle MS P : ℚ))

/-- T5.20b(a): Expected specification complexity bounded by worst-case.
    If every program in the support has κ ≤ K, then E_w[κ] ≤ K.
    This is the distributional analogue of the pointwise upper bound. -/
theorem T5_20b_expected_le_worst_case
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (dist : ProgramDist D R)
    (K : ℕ)
    (h_bound : ∀ P ∈ dist.support, specComplexity sem oracle MS P ≤ K) :
    expectedSpecComplexity sem oracle MS dist ≤ (K : ℚ) := by
  unfold expectedSpecComplexity
  calc dist.support.sum (fun P => dist.weight P * ↑(specComplexity sem oracle MS P))
      ≤ dist.support.sum (fun P => dist.weight P * (K : ℚ)) :=
        Finset.sum_le_sum fun P hP =>
          mul_le_mul_of_nonneg_left (Nat.cast_le.mpr (h_bound P hP)) (dist.h_nonneg P hP)
    _ = (K : ℚ) := by
        trans ((K : ℚ) * dist.support.sum dist.weight)
        · rw [Finset.mul_sum]; exact Finset.sum_congr rfl fun P _ => by ring
        · rw [dist.h_sum_one, mul_one]

/-- T5.20b(b): Expected specification complexity is monotone in mutation policy.
    If μ₁ ⊆ μ₂ pointwise, then E_w[κ_{μ₁}] ≤ E_w[κ_{μ₂}].
    This lifts T5.18 from pointwise to distributional. -/
theorem T5_20b_expected_monotone_policy
    [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS₁ MS₂ : MutationSystem D R)
    (dist : ProgramDist D R)
    (h_sub : ∀ P ∈ dist.support, MS₁.mutants P ⊆ MS₂.mutants P)
    (h_achievable : ∀ P ∈ dist.support, ∃ T : TestSuite D R, achievesFullSC' sem oracle MS₂ P T) :
    expectedSpecComplexity sem oracle MS₁ dist ≤ expectedSpecComplexity sem oracle MS₂ dist := by
  unfold expectedSpecComplexity
  exact Finset.sum_le_sum fun P hP =>
    mul_le_mul_of_nonneg_left
      (Nat.cast_le.mpr (T5_18_policy_monotonicity sem oracle MS₁ MS₂ P (h_sub P hP) (h_achievable P hP)))
      (dist.h_nonneg P hP)

/-- T5.20b(c): Expected specification complexity is nonneg. -/
theorem T5_20b_expected_nonneg
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R)
    (dist : ProgramDist D R) :
    0 ≤ expectedSpecComplexity sem oracle MS dist := by
  unfold expectedSpecComplexity
  exact Finset.sum_nonneg fun P hP =>
    mul_nonneg (dist.h_nonneg P hP) (Nat.cast_nonneg _)

/-
T5.21: Approximation Hardness Transfer for Specification Synthesis.

If the mutation-killing instance class can realize standard set-cover hardness
gadgets, then no polynomial-time synthesis algorithm can beat a (1-ε)ln(n)
approximation factor (unless standard complexity assumptions fail).

Three provable structural results:
  (a) Lower bound: the reduction preserves optimality, so any achieving suite
      needs ≥ OPT tests on the reduced instance.
  (b) Upper bound transfer: T5.12's greedy bound on the reduced instance gives
      a concrete O(OPT · H(n)) upper bound in terms of the set cover optimum.
  (c) Near-optimality: the ratio between greedy and optimal is at most H(n),
      which matches the (1-ε)ln(n) inapproximability of set cover
      (Dinur-Steurer).
-/

/-- A set-cover-to-specification reduction: maps set cover instances to
    mutation-killing instances preserving the approximation ratio. -/
structure SetCoverReduction (D R : Type) where
  -- For any set cover instance (universe U, family F), produce a mutation system
  -- and program whose specification complexity equals the minimum set cover.
  to_spec : (universe_size : ℕ) → (cover_opt : ℕ) → MutationSystem D R × Program D R
  -- The reduction preserves optimality: specComplexity of the produced instance
  -- equals the optimal cover size.
  preserves : ∀ (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (u c : ℕ), let (MS, P) := to_spec u c;
    specComplexity sem oracle MS P = c

/-- T5.21(a): Reduction lower bound. Any test suite achieving full SC on a
    reduced instance has at least c tests, where c is the set cover optimum.
    This is the hardness direction: you cannot do better than OPT. -/
theorem T5_21_reduction_lower_bound
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (reduction : SetCoverReduction D R)
    (u c : ℕ)
    (T : TestSuite D R)
    (h_sc : achievesFullSC' sem oracle (reduction.to_spec u c).1 (reduction.to_spec u c).2 T) :
    c ≤ T.card := by
  have h_preserves := reduction.preserves sem oracle u c
  dsimp only at h_preserves
  have h_spec_le : specComplexity sem oracle (reduction.to_spec u c).1 (reduction.to_spec u c).2 ≤ T.card := by
    unfold specComplexity; exact Nat.sInf_le ⟨T, rfl, h_sc⟩
  linarith

/-- T5.21(b): Greedy upper bound transfer. Combining the reduction (OPT = c)
    with T5.12's greedy harmonic bound gives k_greedy ≤ c · (log n + 1)
    on reduced instances. This is the algorithmic direction: greedy achieves
    O(OPT · ln n) on the hardest instances produced by the reduction. -/
theorem T5_21_greedy_upper_bound_transfer
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (reduction : SetCoverReduction D R)
    (u c : ℕ)
    -- Greedy output on the reduced instance
    (k_greedy : ℕ)
    -- The greedy harmonic bound from T5.12 holds for some optimal suite
    (T_opt : TestSuite D R)
    (h_opt : achievesFullSC' sem oracle (reduction.to_spec u c).1 (reduction.to_spec u c).2 T_opt)
    (h_opt_card : T_opt.card = c)
    (n : ℕ) (h_n_pos : 0 < n)
    (h_greedy_harmonic : (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n) :
    (k_greedy : ℝ) ≤ c * (Real.log n + 1) := by
  have h_harmonic_log : Harmonic_P5 n ≤ Real.log n + 1 := Harmonic_P5_le_log_add_one n
  calc (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n := h_greedy_harmonic
    _ ≤ T_opt.card * (Real.log n + 1) :=
        mul_le_mul_of_nonneg_left h_harmonic_log (by positivity)
    _ = c * (Real.log n + 1) := by rw [h_opt_card]

/-- T5.21(c): Near-optimality of greedy. The approximation ratio
    k_greedy / OPT ≤ H(n) ≤ ln(n) + 1, matching the (1-ε)ln(n)
    inapproximability of set cover (Dinur-Steurer). -/
theorem T5_21_greedy_near_optimal
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (reduction : SetCoverReduction D R)
    (u c : ℕ) (h_c_pos : 0 < c)
    (k_greedy : ℕ)
    (n : ℕ) (h_n_pos : 0 < n)
    (h_greedy_bound : (k_greedy : ℝ) ≤ c * (Real.log n + 1)) :
    (k_greedy : ℝ) / c ≤ Real.log n + 1 := by
  have hc_pos : (0 : ℝ) < c := by exact_mod_cast h_c_pos
  rw [div_le_iff₀ hc_pos]
  linarith

/-
The Fixed-Point Partition applied to specification complexity:
At the fixed point of synthesis, the remaining survivors are exactly the equivalent mutants.
This connects the synthesis loop (Phase 2) to the specification complexity definition (Phase 5).
-/
theorem T5_synthesis_fixed_point_is_spec_complete
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (T : TestSuite D R)
    (h_fixed : killedSet sem oracle MS P T = nonEquivMutants sem MS P) :
    achievesFullSC' sem oracle MS P T := by
  unfold achievesFullSC'
  simpa [h_fixed] using (Finset.Subset.refl (nonEquivMutants sem MS P))

end
