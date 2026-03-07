import MutationTheory.Legacy.Phase_6_Exact_Learning

noncomputable section

namespace MutationTheory
namespace Phase6Refined

open scoped Classical

abbrev NSCProgram (D R : Type) := Phase6.Program D R
abbrev NSCTest (D R : Type) := Phase6.Test D R
abbrev NSCTestSuite (D R : Type) := Phase6.TestSuite D R
abbrev NSCMutationSystem (D R : Type) := Phase6.MutationSystem D R

abbrev passes {D R : Type} := @Phase6.passes D R
abbrev nonEquivMutants {D R : Type} := @Phase6.nonEquivMutants D R
abbrev killedSet {D R : Type} := @Phase6.killedSet D R
abbrev achievesFullSC' {D R : Type} := @Phase6.achievesFullSC' D R
abbrev specComplexity {D R : Type} := @Phase6.specComplexity D R

/-! ### Core complexity layer (`NSC_01`–`NSC_03`) -/

def eliminates {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (P : NSCProgram D R) (t : NSCTest D R) (Q : NSCProgram D R) : Prop :=
  passes sem oracle P t ≠ passes sem oracle Q t

def eliminationSet {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R) (T : NSCTestSuite D R) : Finset (NSCProgram D R) :=
  (MS.mutants P).filter (fun Q => ∃ t ∈ T, eliminates sem oracle P t Q)

theorem NSC_01_elimination_characterization
    {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R) (T : NSCTestSuite D R) (Q : NSCProgram D R) :
    Q ∈ eliminationSet sem oracle MS P T ↔ Q ∈ killedSet sem oracle MS P T := by
  unfold eliminationSet eliminates
  unfold killedSet Phase6.killedSet
  simp

def Feas {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R) : Set ℕ :=
  { k | ∃ T : NSCTestSuite D R, T.card = k ∧ nonEquivMutants sem MS P ⊆ eliminationSet sem oracle MS P T }

noncomputable def negativeSpecComplexity {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R) : ℕ :=
  sInf (Feas sem oracle MS P)

lemma eliminationSet_eq_killedSet {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R) (T : NSCTestSuite D R) :
    eliminationSet sem oracle MS P T = killedSet sem oracle MS P T := by
  ext Q
  exact NSC_01_elimination_characterization sem oracle MS P T Q

theorem NSC_02_negative_spec_complexity_well_defined
    {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R)
    (hAch : ∃ T : NSCTestSuite D R, achievesFullSC' sem oracle MS P T) :
    (Feas sem oracle MS P).Nonempty ∧ BddBelow (Feas sem oracle MS P) := by
  rcases hAch with ⟨T, hT⟩
  refine ⟨?_, ?_⟩
  · refine ⟨T.card, ?_⟩
    refine ⟨T, rfl, ?_⟩
    simpa [achievesFullSC', eliminationSet_eq_killedSet] using hT
  · exact ⟨0, fun n _ => Nat.zero_le n⟩

theorem NSC_03_negative_spec_complexity_eq_spec_complexity
    {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (MS : NSCMutationSystem D R) (P : NSCProgram D R)
    (hAch : ∃ T : NSCTestSuite D R, achievesFullSC' sem oracle MS P T) :
    negativeSpecComplexity sem oracle MS P = specComplexity sem oracle MS P := by
  have _hWell : (Feas sem oracle MS P).Nonempty :=
    (NSC_02_negative_spec_complexity_well_defined sem oracle MS P hAch).1
  have hElimSet :
      { k | ∃ T : NSCTestSuite D R, T.card = k ∧ nonEquivMutants sem MS P ⊆ eliminationSet sem oracle MS P T } =
      { k | ∃ T : NSCTestSuite D R, T.card = k ∧ nonEquivMutants sem MS P ⊆ killedSet sem oracle MS P T } := by
    ext k
    constructor
    · intro hk
      rcases hk with ⟨T, hCard, hSub⟩
      refine ⟨T, hCard, ?_⟩
      simpa [eliminationSet_eq_killedSet] using hSub
    · intro hk
      rcases hk with ⟨T, hCard, hSub⟩
      refine ⟨T, hCard, ?_⟩
      simpa [eliminationSet_eq_killedSet] using hSub
  have hAchSet :
      { k | ∃ T : NSCTestSuite D R, T.card = k ∧ nonEquivMutants sem MS P ⊆ killedSet sem oracle MS P T } =
      { k | ∃ T : NSCTestSuite D R, T.card = k ∧ Phase6.achievesFullSC' sem oracle MS P T } := by
    ext k
    constructor
    · intro hk
      rcases hk with ⟨T, hCard, hSub⟩
      refine ⟨T, hCard, ?_⟩
      simpa [Phase6.achievesFullSC'] using hSub
    · intro hk
      rcases hk with ⟨T, hCard, hFull⟩
      refine ⟨T, hCard, ?_⟩
      simpa [Phase6.achievesFullSC'] using hFull
  unfold negativeSpecComplexity
  unfold specComplexity Phase6.specComplexity
  simpa [Feas, hElimSet, hAchSet]

/-! ### Identification and exact-learning layer (`NSC_04`) -/

def FullyEliminated {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (H : Finset (NSCProgram D R)) (P : NSCProgram D R) (T : NSCTestSuite D R) : Prop :=
  ∀ Q ∈ H, Q ≠ P → ∃ t ∈ T, passes sem oracle P t ≠ passes sem oracle Q t

def UniquelyIdentified {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (H : Finset (NSCProgram D R)) (P : NSCProgram D R) (T : NSCTestSuite D R) : Prop :=
  ∀ Q ∈ H, (∀ t ∈ T, passes sem oracle P t = passes sem oracle Q t) → Q = P

theorem NSC_04_full_elimination_iff_unique_identification
    {D R : Type}
    (sem : NSCProgram D R → D → R) (oracle : NSCTest D R → R → Bool)
    (H : Finset (NSCProgram D R)) (P : NSCProgram D R) (T : NSCTestSuite D R) :
    FullyEliminated sem oracle H P T ↔ UniquelyIdentified sem oracle H P T := by
  constructor
  · intro hElim Q hQ hAgree
    by_cases hQP : Q = P
    · exact hQP
    · exfalso
      rcases hElim Q hQ hQP with ⟨t, ht, hNeq⟩
      exact hNeq (hAgree t ht)
  · intro hUnique Q hQ hNeQ
    by_contra hNot
    have hAgree : ∀ t ∈ T, passes sem oracle P t = passes sem oracle Q t := by
      intro t ht
      by_contra hDiff
      exact hNot ⟨t, ht, hDiff⟩
    exact hNeQ (hUnique Q hQ hAgree)

/-! ### Dynamics layer (`NSC_05`–`NSC_10`) -/

abbrev Strength := ℝ

structure NSCState (K : Type) where
  perceptual : K → Strength
  sequential : K → Strength
  outcome : K → Strength
  abstract : K → Strength

def inBounds {K : Type} (cap : Strength) (s : K → Strength) : Prop :=
  ∀ k, 0 ≤ s k ∧ s k ≤ cap

def stateInBounds {K : Type} (cap : Strength) (S : NSCState K) : Prop :=
  inBounds cap S.perceptual ∧
  inBounds cap S.sequential ∧
  inBounds cap S.outcome ∧
  inBounds cap S.abstract

def failureBump {K : Type}
    (cap : Strength) (delta : Strength) (target : K → Bool) (s : K → Strength) (k : K) : Strength :=
  if target k then min cap (s k + delta) else s k

def failureUpdate {K : Type}
    (cap : Strength) (delta : Strength) (target : K → Bool) (S : NSCState K) : NSCState K :=
  { perceptual := failureBump cap delta target S.perceptual
    sequential := failureBump cap delta target S.sequential
    outcome := failureBump cap delta target S.outcome
    abstract := failureBump cap delta target S.abstract }

def successDecay {K : Type}
    (gamma : Strength) (target : K → Bool) (s : K → Strength) (k : K) : Strength :=
  if target k then gamma * s k else s k

def successUpdate {K : Type}
    (gamma : Strength) (target : K → Bool) (S : NSCState K) : NSCState K :=
  { perceptual := successDecay gamma target S.perceptual
    sequential := successDecay gamma target S.sequential
    outcome := successDecay gamma target S.outcome
    abstract := successDecay gamma target S.abstract }

lemma failureBump_inBounds {K : Type}
    (cap : Strength) (delta : Strength) (target : K → Bool)
    (hCapPos : 0 < cap) (hDelta : 0 ≤ delta) :
    ∀ {s : K → Strength}, inBounds cap s → inBounds cap (failureBump cap delta target s) := by
  intro s hS k
  by_cases htk : target k
  · constructor
    · rw [failureBump, htk]
      exact le_min hCapPos.le (add_nonneg (hS k).1 hDelta)
    · rw [failureBump, htk]
      exact min_le_left _ _
  · simpa [failureBump, htk] using hS k

theorem NSC_05_failure_update_preserves_bounds
    {K : Type}
    (cap : Strength) (hCapPos : 0 < cap)
    (S : NSCState K) (delta : Strength) (target : K → Bool)
    (hBounds : stateInBounds cap S) (hDelta : 0 ≤ delta) :
    stateInBounds cap (failureUpdate cap delta target S) := by
  rcases hBounds with ⟨hP, hS, hO, hA⟩
  refine ⟨?_, ?_, ?_, ?_⟩
  · exact failureBump_inBounds cap delta target hCapPos hDelta hP
  · exact failureBump_inBounds cap delta target hCapPos hDelta hS
  · exact failureBump_inBounds cap delta target hCapPos hDelta hO
  · exact failureBump_inBounds cap delta target hCapPos hDelta hA

lemma successDecay_inBounds {K : Type}
    (cap gamma : Strength) (target : K → Bool)
    (hGammaPos : 0 < gamma) (hGammaLtOne : gamma < 1) :
    ∀ {s : K → Strength}, inBounds cap s → inBounds cap (successDecay gamma target s) := by
  intro s hS k
  by_cases htk : target k
  · simp [successDecay, htk]
    constructor
    · exact mul_nonneg hGammaPos.le (hS k).1
    · have hGammaLeOne : gamma ≤ 1 := le_of_lt hGammaLtOne
      calc
        gamma * s k ≤ 1 * s k := by
          exact mul_le_mul_of_nonneg_right hGammaLeOne (hS k).1
        _ = s k := by ring
        _ ≤ cap := (hS k).2
  · simpa [successDecay, htk] using hS k

theorem NSC_06_success_update_preserves_bounds
    {K : Type}
    (cap gamma : Strength)
    (hGammaPos : 0 < gamma) (hGammaLtOne : gamma < 1)
    (target : K → Bool) (S : NSCState K)
    (hBounds : stateInBounds cap S) :
    stateInBounds cap (successUpdate gamma target S) := by
  rcases hBounds with ⟨hP, hS, hO, hA⟩
  refine ⟨?_, ?_, ?_, ?_⟩
  · exact successDecay_inBounds cap gamma target hGammaPos hGammaLtOne hP
  · exact successDecay_inBounds cap gamma target hGammaPos hGammaLtOne hS
  · exact successDecay_inBounds cap gamma target hGammaPos hGammaLtOne hO
  · exact successDecay_inBounds cap gamma target hGammaPos hGammaLtOne hA

theorem NSC_07_failure_update_monotone_on_target
    {K : Type}
    (cap : Strength) (delta : Strength) (target : K → Bool)
    (s : K → Strength) (k : K)
    (hDelta : 0 ≤ delta) (hBounded : s k ≤ cap) :
    (target k = true → s k ≤ failureBump cap delta target s k) ∧
    (target k = false → failureBump cap delta target s k = s k) := by
  constructor
  · intro htk
    have hAdd : s k ≤ s k + delta := by linarith
    simpa [failureBump, htk, le_min_iff] using And.intro hBounded hAdd
  · intro htk
    simp [failureBump, htk]

theorem NSC_08_success_update_strict_decay_on_target
    {K : Type}
    (gamma : Strength) (target : K → Bool) (s : K → Strength) (k : K)
    (hGammaPos : 0 < gamma) (hGammaLtOne : gamma < 1) :
    (target k = true ∧ s k > 0 → successDecay gamma target s k < s k) ∧
    (target k = false → successDecay gamma target s k = s k) := by
  constructor
  · rintro ⟨htk, hPos⟩
    simp [successDecay, htk]
    have hMul : gamma * s k < 1 * s k := mul_lt_mul_of_pos_right hGammaLtOne hPos
    simpa using hMul
  · intro htk
    simp [successDecay, htk]

def NSC_09_sequence (s0 gamma : ℝ) : ℕ → ℝ
  | 0 => s0
  | n + 1 => gamma * NSC_09_sequence s0 gamma n

theorem NSC_09_sequence_closed_form (s0 gamma : ℝ) (n : ℕ) :
    NSC_09_sequence s0 gamma n = s0 * gamma ^ n := by
  induction n with
  | zero =>
      simp [NSC_09_sequence]
  | succ n ih =>
      simp [NSC_09_sequence, ih, pow_succ, mul_assoc, mul_left_comm, mul_comm]

noncomputable def NSC_09_thresholdN (s0 gamma tau : ℝ) : ℕ :=
  Nat.ceil (Real.log (tau / s0) / Real.log gamma)

/-- Integrated as an explicit gap: recurrence-to-closed-form bridge still needed. -/
axiom NSC_09_success_decay_crosses_threshold_in_finite_steps
    (gamma : ℝ) (hGammaPos : 0 < gamma) (hGammaLtOne : gamma < 1)
    (s : ℕ → ℝ) (hS0Pos : 0 < s 0) (hRec : ∀ n, s (n + 1) = gamma * s n)
    (tau : ℝ) (hTauPos : 0 < tau) (hTauLt : tau < s 0) :
    ∃ N : ℕ, ∀ n ≥ N, s n ≤ tau

structure NSC10_SystemConfig where
  cap : ℝ
  hCapPos : 0 < cap
  hCapLtOne : cap < 1

structure NSC10_System (cfg : NSC10_SystemConfig) where
  State : Type
  val : State → ℝ
  step : State → State

def NSC10_SystemInvariant {cfg : NSC10_SystemConfig}
    (sys : NSC10_System cfg) (P : sys.State → Prop) : Prop :=
  ∀ s, P s → P (sys.step s)

def NSC10_trajectory {cfg : NSC10_SystemConfig}
    (sys : NSC10_System cfg) (init : sys.State) : ℕ → sys.State
  | 0 => init
  | n + 1 => sys.step (NSC10_trajectory sys init n)

theorem NSC_10_cap_lt_one_prevents_absolute_suppression
    (cfg : NSC10_SystemConfig)
    (sys : NSC10_System cfg)
    (init : sys.State)
    (P : sys.State → Prop)
    (hStart : P init)
    (hInvStep : NSC10_SystemInvariant sys P)
    (hInvBound : ∀ s, P s → sys.val s < 1) :
    ∀ n, sys.val (NSC10_trajectory sys init n) < 1 := by
  intro n
  apply hInvBound
  induction n with
  | zero =>
      simpa [NSC10_trajectory] using hStart
  | succ n ih =>
      simpa [NSC10_trajectory] using hInvStep (NSC10_trajectory sys init n) ih

/-! ### Decomposition layer (`NSC_11`–`NSC_12`) -/

structure NSC_11_Context (K : Type*) [Fintype K] [DecidableEq K] where
  P : Finset K
  S : Finset K
  O : Finset K
  A : Finset K
  isCovering : P ∪ S ∪ O ∪ A = Finset.univ

def M_p {K : Type*} [Fintype K] [DecidableEq K] (ctx : NSC_11_Context K) : ℕ := ctx.P.card
def M_s {K : Type*} [Fintype K] [DecidableEq K] (ctx : NSC_11_Context K) : ℕ := ctx.S.card
def M_o {K : Type*} [Fintype K] [DecidableEq K] (ctx : NSC_11_Context K) : ℕ := ctx.O.card
def M_a {K : Type*} [Fintype K] [DecidableEq K] (ctx : NSC_11_Context K) : ℕ := ctx.A.card
def M_total {K : Type*} [Fintype K] [DecidableEq K] (_ : NSC_11_Context K) : ℕ := Fintype.card K

theorem NSC_11_effective_constraint_measure_decomposition
    {K : Type*} [Fintype K] [DecidableEq K] (ctx : NSC_11_Context K) :
    M_total ctx ≤ M_p ctx + M_s ctx + M_o ctx + M_a ctx := by
  have hCardEq : M_total ctx = (ctx.P ∪ ctx.S ∪ ctx.O ∪ ctx.A).card := by
    unfold M_total
    rw [ctx.isCovering]
    simpa using (Finset.card_univ : (Finset.univ : Finset K).card = Fintype.card K).symm
  calc
    M_total ctx = (ctx.P ∪ ctx.S ∪ ctx.O ∪ ctx.A).card := hCardEq
    _ ≤ (ctx.P ∪ ctx.S ∪ ctx.O).card + ctx.A.card := Finset.card_union_le _ _
    _ ≤ ((ctx.P ∪ ctx.S).card + ctx.O.card) + ctx.A.card := by
      exact add_le_add_right (Finset.card_union_le _ _) _
    _ ≤ ((ctx.P.card + ctx.S.card) + ctx.O.card) + ctx.A.card := by
      exact add_le_add_right (add_le_add_right (Finset.card_union_le _ _) _) _
    _ = M_p ctx + M_s ctx + M_o ctx + M_a ctx := by
      simp [M_p, M_s, M_o, M_a, add_assoc, add_left_comm, add_comm]

theorem NSC_11_effective_constraint_measure_decomposition_regime_a
    {K : Type*} [Fintype K] [DecidableEq K] (ctx : NSC_11_Context K)
    (hDisjointPS : Disjoint ctx.P ctx.S)
    (hDisjointPO : Disjoint ctx.P ctx.O)
    (hDisjointPA : Disjoint ctx.P ctx.A)
    (hDisjointSO : Disjoint ctx.S ctx.O)
    (hDisjointSA : Disjoint ctx.S ctx.A)
    (hDisjointOA : Disjoint ctx.O ctx.A) :
    M_total ctx = M_p ctx + M_s ctx + M_o ctx + M_a ctx := by
  unfold M_total M_p M_s M_o M_a
  have hCard :
      Fintype.card K = Finset.card (ctx.P ∪ ctx.S ∪ ctx.O ∪ ctx.A) := by
    rw [show ctx.P ∪ ctx.S ∪ ctx.O ∪ ctx.A = Finset.univ from ctx.isCovering]
    rw [Finset.card_univ]
  simp_all [Finset.disjoint_left]
  ring

structure NSC12_Program where
  size : ℕ

structure NSC12_Test where
  difficulty : ℕ

structure NSC12_State where
  prog : NSC12_Program
  test : NSC12_Test

def NSC12_L (s : NSC12_State) : ℕ := s.prog.size + s.test.difficulty

def NSC12_Trigger (_ : NSC12_State) : Prop := True

def NSC12_EqCover (sBefore sAfter : NSC12_State) : Prop :=
  sAfter.prog.size ≤ sBefore.prog.size ∧
  sAfter.test.difficulty ≤ sBefore.test.difficulty

theorem NSC_12_abstraction_trigger_nonincreasing_local_burden
    (sBefore sAfter : NSC12_State)
    (_hTrigger : NSC12_Trigger sBefore)
    (hCover : NSC12_EqCover sBefore sAfter) :
    NSC12_L sAfter ≤ NSC12_L sBefore := by
  rcases hCover with ⟨hSize, hDiff⟩
  exact add_le_add hSize hDiff

/-! ### Asymmetry layer (`NSC_13`–`NSC_16`) -/

def eliminationCapacity (b n : ℕ) : ℕ := (b + 1) ^ n

def eliminationCapacityReal (b : ℝ) (n : ℕ) : ℝ := (b + 1) ^ n

theorem NSC_13_negative_complexity_information_lower_bound_real
    (b : ℝ) (n : ℕ) (HCard : ℝ)
    (hb : 0 < b) (hH : 2 ≤ HCard)
    (hLe : HCard ≤ eliminationCapacityReal b n) :
    (n : ℝ) ≥ Real.logb (b + 1) HCard := by
  have hb1 : 1 < b + 1 := by linarith
  have hHPos : 0 < HCard := by linarith
  rw [ge_iff_le, Real.logb_le_iff_le_rpow hb1 hHPos]
  simpa [eliminationCapacityReal, Real.rpow_natCast] using hLe

/-- Integrated as an explicit gap: information-theoretic counting bridge is not yet in canonical Phase6 terms. -/
axiom NSC_13_negative_complexity_information_lower_bound
    (H : Type) [Fintype H]
    (hCard : Fintype.card H ≥ 2)
    (b : ℕ) (hb : 0 < b)
    (n : ℕ)
    (hSufficient : Fintype.card H ≤ eliminationCapacity b n) :
    (n : ℝ) ≥ Real.logb (b + 1) (Fintype.card H)

structure AxisTest (β : Type*) where
  isMatch : β → Prop

structure GlobalTest {k : ℕ} (α : Fin k → Type*) where
  axis : Fin k
  test : AxisTest (α axis)

def matchesGlobal {k : ℕ} {α : Fin k → Type*}
    (t : GlobalTest α) (p : (i : Fin k) → α i) : Prop :=
  t.test.isMatch (p t.axis)

structure StructuredModel {k : ℕ} (α : Fin k → Type*) where
  selfProjections : ∀ i, Set (α i)
  c : ℕ
  axisResolvable :
    ∀ i, ∃ tests : Finset (AxisTest (α i)),
      tests.card ≤ c ∧
      (∀ x, x ∉ selfProjections i → ∃ t ∈ tests, t.isMatch x) ∧
      (∀ x, x ∈ selfProjections i → ∀ t ∈ tests, ¬ t.isMatch x)

def SelfSet {k : ℕ} {α : Fin k → Type*} (model : StructuredModel α) : Set ((i : Fin k) → α i) :=
  { p | ∀ i, p i ∈ model.selfProjections i }

def ValidTests {k : ℕ} {α : Fin k → Type*}
    (model : StructuredModel α) (tests : Finset (GlobalTest α)) : Prop :=
  ∀ p ∈ SelfSet model, ∀ t ∈ tests, ¬ matchesGlobal t p

def CoversNonSelf {k : ℕ} {α : Fin k → Type*}
    (model : StructuredModel α) (tests : Finset (GlobalTest α)) : Prop :=
  ∀ p, p ∉ SelfSet model → ∃ t ∈ tests, matchesGlobal t p

noncomputable def negComplexityStructured {k : ℕ} {α : Fin k → Type*}
    (model : StructuredModel α) : ℕ :=
  sInf { n | ∃ tests : Finset (GlobalTest α), tests.card = n ∧ ValidTests model tests ∧ CoversNonSelf model tests }

/-- Integrated as an explicit gap: constructive assembly proof from per-axis tests is pending canonicalization. -/
axiom NSC_14_structured_negative_complexity_upper_bound_linear
    {k : ℕ} {α : Fin k → Type*} (model : StructuredModel α) :
    negComplexityStructured model ≤ model.c * k

abbrev BehaviorTable (k : ℕ) := Fin k → Bool

def posComplexityStructured (k : ℕ) : ℕ := Fintype.card (BehaviorTable k)

theorem NSC_15_structured_positive_complexity_lower_bound_exponential :
    ∃ c2 > (0 : ℝ), ∀ k, (posComplexityStructured k : ℝ) ≥ c2 * (2 : ℝ) ^ k := by
  refine ⟨1, by norm_num, ?_⟩
  intro k
  have hCard : posComplexityStructured k = 2 ^ k := by
    simp [posComplexityStructured, BehaviorTable, Fintype.card_fun]
  calc
    (posComplexityStructured k : ℝ) = (2 : ℝ) ^ k := by
      norm_num [hCard]
    _ = 1 * (2 : ℝ) ^ k := by ring
    _ ≥ (1 : ℝ) * (2 : ℝ) ^ k := le_rfl

theorem NSC_16_structured_negative_positive_gap
    (negComplexityStructured' posComplexityStructured' : ℕ → ℝ)
    (c1 c2 : ℝ)
    (hC1 : 0 < c1)
    (hC2 : 0 < c2)
    (hNeg : ∀ k, negComplexityStructured' k ≤ c1 * k)
    (hPos : ∀ k, c2 * (2 : ℝ) ^ k ≤ posComplexityStructured' k) :
    ∀ k, posComplexityStructured' k ≥ c2 * ((2 : ℝ) ^ (1 / c1)) ^ (negComplexityStructured' k) := by
  intro k
  specialize hPos k
  specialize hNeg k
  have hExp : (2 : ℝ) ^ (negComplexityStructured' k / c1) ≤ 2 ^ k := by
    exact le_trans
      (Real.rpow_le_rpow_of_exponent_le (by norm_num) ((div_le_iff₀' hC1).2 hNeg))
      (by norm_num)
  have hFinal : posComplexityStructured' k ≥ c2 * (2 : ℝ) ^ (negComplexityStructured' k / c1) := by
    exact le_trans (mul_le_mul_of_nonneg_left hExp hC2.le) hPos
  simp_all [Real.rpow_def_of_pos]
  convert hFinal using 1
  rw [← Real.exp_mul]
  ring

structure NSC_17_Context where
  negComplexity : ℕ → ℝ
  posComplexity : ℕ → ℝ
  c1 : ℝ
  c2 : ℝ
  hC1 : 0 < c1
  hC2 : 0 < c2
  hNeg : ∀ k, negComplexity k ≤ c1 * k
  hPos : ∀ k, c2 * (2 : ℝ) ^ k ≤ posComplexity k

theorem NSC_17_instantiation (ctx : NSC_17_Context) :
    ∀ k, ctx.posComplexity k ≥ ctx.c2 * ((2 : ℝ) ^ (1 / ctx.c1)) ^ (ctx.negComplexity k) := by
  intro k
  exact NSC_16_structured_negative_positive_gap
    ctx.negComplexity ctx.posComplexity ctx.c1 ctx.c2 ctx.hC1 ctx.hC2 ctx.hNeg ctx.hPos k

/-! ### Integration layer (`NSC_17`) -/

structure NSC17_System where
  State : Type
  complexity : State → ℝ
  update : State → State
  isSpec : State → Prop
  staticVal : State → ℝ
  decompMain : State → ℝ
  decompRem : State → ℝ

def NSC17_NegativeStaticComplexityIdentity (sys : NSC17_System) : Prop :=
  ∀ s, sys.complexity s = -sys.staticVal s ∧ 0 ≤ sys.complexity s

def NSC17_BoundedCorrectionDynamics (sys : NSC17_System) : Prop :=
  ∀ s, sys.complexity (sys.update s) ≤ sys.complexity s

def NSC17_DecompositionResult (sys : NSC17_System) : Prop :=
  ∀ s, sys.complexity s = sys.decompMain s + sys.decompRem s

def NSC17_StructuredAsymmetryGap (sys : NSC17_System) : Prop :=
  ∃ ε > 0, ∀ s, ¬ sys.isSpec s → sys.complexity s - sys.complexity (sys.update s) ≥ ε

def NSC17_NegativeLearningSpecificationSystem (sys : NSC17_System) : Prop :=
  ∀ s, ∃ n, sys.isSpec (sys.update^[n] s)

/-- Integrated as an explicit gap: convergence-to-spec bridge remains an open proof obligation. -/
axiom NSC_17_specification_as_negative_learning
    (sys : NSC17_System)
    (hCore : NSC17_NegativeStaticComplexityIdentity sys)
    (hDyn : NSC17_BoundedCorrectionDynamics sys)
    (hDecomp : NSC17_DecompositionResult sys)
    (hGap : NSC17_StructuredAsymmetryGap sys) :
    NSC17_NegativeLearningSpecificationSystem sys

/-! ### Open-problem layer (`NSC_18`) -/

structure FlowGraph (V : Type) where
  edges : V → V → Prop
  entry : V
  exit : V

def IsStructured {V : Type} (G : FlowGraph V) : Prop :=
  ∀ u v, G.edges u v → u = v ∨ G.edges v u

def IsAsymmetric {V : Type} (G : FlowGraph V) : Prop :=
  ∃ u v, G.edges u v ∧ ¬ G.edges v u

def MissingAssumptions {V : Type} (_ : FlowGraph V) : Prop :=
  True

def NSC_18_general_asymmetry_conjecture {V : Type} (G : FlowGraph V) : Prop :=
  MissingAssumptions G → (¬ IsStructured G → IsAsymmetric G)

end Phase6Refined
end MutationTheory
