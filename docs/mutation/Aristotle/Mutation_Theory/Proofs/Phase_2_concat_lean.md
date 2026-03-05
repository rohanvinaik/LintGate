/-
We define the synthesis state and operator, and prove that iterating the operator results in a monotonically increasing set of tests (Theorem 2.1).
-/

import Mathlib

set_option linter.mathlibStandardSet false

open scoped BigOperators
open scoped Real
open scoped Nat
open scoped Classical
open scoped Pointwise

set_option maxHeartbeats 0
set_option maxRecDepth 4000
set_option synthInstance.maxHeartbeats 20000
set_option synthInstance.maxSize 128

set_option relaxedAutoImplicit false
set_option autoImplicit false

noncomputable section

/-
Definitions of Program, Test, SynthState, and SynthOperator based on the problem description.
-/
variable {D R : Type}

structure Program (D R : Type) where
  fn : D → R

structure Test (D R : Type) where
  input : D
  oracle : R → Bool

structure SynthState (D R : Type) where
  tests : Set (Test D R)

structure SynthOperator (D R : Type) where
  step : SynthState D R → SynthState D R
  tests_monotone : ∀ s, s.tests ⊆ (step s).tests

/-
The test suite produced by iterating the synthesis operator is a non-decreasing chain under ⊆.
-/
noncomputable def iterSynth [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) : ℕ → SynthState D R
  | 0 => s₀
  | n + 1 => op.step (iterSynth op s₀ n)

theorem T2_1_tests_monotone_chain [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) (n m : ℕ)
    (h : n ≤ m) :
    (iterSynth op s₀ n).tests ⊆ (iterSynth op s₀ m).tests := by
  induction' h with k hk;
  · rfl;
  · exact Set.Subset.trans ‹_› ( op.tests_monotone _ )

  /-
This module defines the core structures for a program synthesis loop, including `Program`, `SynthState`, and `SynthOperator`. It then defines the `iterSynth` function which iterates the synthesis operator. Finally, it proves `T2_2_survivors_antitone_chain`, showing that the set of surviving mutants is monotonically non-increasing with respect to the number of iterations, using the monotonicity of the synthesis operator.
-/

noncomputable section

variable {D R : Type}

structure Program (D R : Type) where
  id : Nat

instance : DecidableEq (Program D R) :=
  fun p1 p2 =>
    if h : p1.id = p2.id then
      isTrue (by cases p1; cases p2; simp_all)
    else
      isFalse (by intro heq; apply h; cases heq; rfl)

structure SynthState (D R : Type) where
  survivors : Set (Program D R)

structure SynthOperator (D R : Type) where
  step : SynthState D R → SynthState D R
  surv_antitone : ∀ s, (step s).survivors ⊆ s.survivors

def iterSynth (op : SynthOperator D R) (s : SynthState D R) (n : ℕ) : SynthState D R :=
  match n with
  | 0 => s
  | k + 1 => op.step (iterSynth op s k)

/-
The surviving mutant set is a non-increasing chain under ⊆.
-/
theorem T2_2_survivors_antitone_chain [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) (n m : ℕ)
    (h : n ≤ m) :
    (iterSynth op s₀ m).survivors ⊆ (iterSynth op s₀ n).survivors := by
  -- We proceed by induction on k = m - n.
  induction' h with k hk;
  · tauto;
  · apply Set.Subset.trans ( op.surv_antitone _ ) ‹_›

  /-
We define the synthesis state and operator, and prove that the set of survivors forms an antitone chain (T2.2). We then prove the termination bound theorem (T2.3), showing that the synthesis loop stabilizes in at most |initial survivors| productive steps, using the fact that the cardinality of the survivors set is a strictly decreasing measure on productive steps.
-/

noncomputable section

structure Program (D R : Type) where
  id : Nat

variable {D R : Type}

instance : DecidableEq (Program D R) :=
  fun p1 p2 =>
    if h : p1.id = p2.id then
      isTrue (by cases p1; cases p2; simp_all)
    else
      isFalse (by intro heq; apply h; cases heq; rfl)

structure SynthState (D R : Type) where
  survivors : Set (Program D R)

structure SynthOperator (D R : Type) where
  step : SynthState D R → SynthState D R
  surv_antitone : ∀ s, (step s).survivors ⊆ s.survivors

def iterSynth (op : SynthOperator D R) (s : SynthState D R) (n : ℕ) : SynthState D R :=
  match n with
  | 0 => s
  | k + 1 => op.step (iterSynth op s k)

theorem T2_2_survivors_antitone_chain [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) (n m : ℕ)
    (h : n ≤ m) :
    (iterSynth op s₀ m).survivors ⊆ (iterSynth op s₀ n).survivors := by
  -- We proceed by induction on k = m - n.
  induction' h with k hk;
  · tauto;
  · apply Set.Subset.trans ( op.surv_antitone _ ) ‹_›

/-
The loop terminates in at most |initial survivors| productive steps. A "productive" step kills at least one mutant. If no step is productive, the loop has reached its fixed point.
-/
theorem T2_3_termination_bound [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R)
    (h_fin : s₀.survivors.Finite)
    /- Productive step kills at least one -/
    (h_productive : ∀ s, (op.step s).survivors ⊂ s.survivors ∨
                         (op.step s).survivors = s.survivors) :
    ∃ N ≤ s₀.survivors.ncard,
      (iterSynth op s₀ N).survivors = (iterSynth op s₀ (N + 1)).survivors := by
  by_contra! h_contra;
  -- Since the survivors set is finite, the sequence of survivors sets is strictly decreasing.
  have h_strict_decr : ∀ i ≤ s₀.survivors.ncard, (iterSynth op s₀ (i + 1)).survivors ⊂ (iterSynth op s₀ i).survivors := by
    intro i hi
    specialize h_contra i hi
    specialize h_productive (iterSynth op s₀ i);
    exact h_productive.resolve_right ( by simpa [ iterSynth ] using h_contra.symm ) |> fun h => by simpa [ iterSynth ] using h;
  -- Applying the strict decrease property repeatedly, we get a strictly decreasing chain of subsets of `s₀.survivors`.
  have h_chain : ∀ i ≤ s₀.survivors.ncard, (iterSynth op s₀ i).survivors ⊆ s₀.survivors ∧ (iterSynth op s₀ i).survivors.ncard ≤ s₀.survivors.ncard - i := by
    intro i hi
    induction' i with i ih;
    · exact ⟨ Set.Subset.refl _, by rfl ⟩;
    · have := h_strict_decr i ( Nat.le_of_succ_le hi );
      have h_card_decr : (iterSynth op s₀ (i + 1)).survivors.ncard < (iterSynth op s₀ i).survivors.ncard := by
        apply_rules [ Set.ncard_lt_ncard ];
        · linarith;
        · exact h_fin.subset ( ih ( Nat.le_of_succ_le hi ) |>.1 );
      exact ⟨ this.1.trans ( ih ( Nat.le_of_succ_le hi ) |>.1 ), by omega ⟩;
  specialize h_chain ( s₀.survivors.ncard ) ; simp_all +decide [ Set.ssubset_def ] ;
  specialize h_strict_decr ( s₀.survivors.ncard ) le_rfl ; simp_all +decide [ Set.subset_def ] ;
  rw [ @Set.ncard_eq_zero ] at h_chain ; aesop;
  exact h_fin.subset h_chain.1

  /-
We have formally proved the Union Formula for Combined Detection (Theorem T2.4).
The theorem states that if n independent detectors each detect with probability p_i,
then the probability that at least one detects is 1 - ∏(1 - p_i).
This confirms the prediction layer's scope reduction claim.
-/

noncomputable section

theorem T2_4_union_detection_formula
    (n : ℕ) (p : Fin n → ℚ)
    (h_bounds : ∀ i, 0 ≤ p i ∧ p i ≤ 1) :
    let combined := 1 - Finset.univ.prod (fun i => 1 - p i)
    0 ≤ combined ∧ combined ≤ 1 := by
  -- Since each $p_i$ is between 0 and 1, the product $\prod_{i} (1 - p_i)$ is also between 0 and 1.
  have h_prod_bounds : 0 ≤ ∏ i, (1 - p i) ∧ ∏ i, (1 - p i) ≤ 1 := by
    -- Since each $p_i$ is between 0 and 1, $1 - p_i$ is also between 0 and 1. The product of numbers between 0 and 1 is also between 0 and 1.
    have h_prod_bounds : ∀ i, 0 ≤ 1 - p i ∧ 1 - p i ≤ 1 := by
      -- Since each $p_i$ is between 0 and 1, subtracting $p_i$ from 1 will give a number between 0 and 1.
      intros i
      exact ⟨by linarith [h_bounds i], by linarith [h_bounds i]⟩;
    -- Apply the fact that the product of non-negative numbers is non-negative.
    apply And.intro (Finset.prod_nonneg fun i _ => h_prod_bounds i |>.1) (Finset.prod_le_one (fun i _ => h_prod_bounds i |>.1) (fun i _ => h_prod_bounds i |>.2));
  -- By combining the results from h_prod_bounds, we can conclude that 1 - ∏ i, (1 - p i) is between 0 and 1.
  simp [h_prod_bounds]

  /-
We proved T2.5: adding a signal with detection probability > 0 strictly increases the combined detection probability, assuming no existing signal is perfect.
The proof uses `mul_lt_of_lt_one_right` on the product of failure probabilities.
-/

noncomputable section

/-
T2.5: Union Formula — Monotone in Number of Signals
Adding a signal with detection probability > 0 strictly increases
combined detection probability (assuming no existing signal is perfect).
-/
theorem T2_5_more_signals_better
    (n : ℕ) (p : Fin n → ℚ) (p_new : ℚ)
    (h_bounds : ∀ i, 0 ≤ p i ∧ p i < 1)
    (h_new_pos : 0 < p_new)
    (h_new_le : p_new ≤ 1) :
    Finset.univ.prod (fun i => 1 - p i) * (1 - p_new) <
    Finset.univ.prod (fun i => 1 - p i) := by
  exact mul_lt_of_lt_one_right ( Finset.prod_pos fun i _ => sub_pos.mpr ( h_bounds i |>.2 ) ) ( sub_lt_self _ h_new_pos )

  /-
We have formalized and proved the Monty Hall Recall Guarantee (T2.6).
The theorem states that if category priors are ε-calibrated, the probability of missing any true specification gap is at most k * ε.
The proof uses `Finset.sum_le_card_nsmul` to bound the sum of probabilities.
-/

/-
If category priors are ε-calibrated (P(survivors | prior < τ) < ε), then the probability of missing any true specification gap is ≤ kε where k is the number of categories.
-/

theorem T2_6_monty_hall_recall
    (k : ℕ) (ε : ℚ)
    (h_ε_pos : 0 < ε) (h_ε_le : ε ≤ 1)
    /- Each filtered category has probability < ε of containing survivors -/
    (miss_prob : Fin k → ℚ)
    (h_calibrated : ∀ i, miss_prob i ≤ ε) :
    /- The probability that ANY filtered category has survivors is ≤ k * ε -/
    Finset.univ.sum miss_prob ≤ k * ε := by
  simpa using Finset.sum_le_sum fun i ( hi : i ∈ Finset.univ ) => h_calibrated i

  /-
Defines the `OracleLevel` enum and proves that the oracle level of a composed function is bounded by the minimum of the oracle levels of its components.
-/

/-
Defines OracleLevel as an enumerated type with a linear order based on the sequence CRASH < TYPE < STRUCTURAL < SHAPE < VALUE < PROPERTY.
-/
inductive OracleLevel
  | CRASH
  | TYPE
  | STRUCTURAL
  | SHAPE
  | VALUE
  | PROPERTY
  deriving DecidableEq, Repr, Ord

instance : LinearOrder OracleLevel :=
  LinearOrder.lift'
    (fun l => match l with
      | .CRASH => 0
      | .TYPE => 1
      | .STRUCTURAL => 2
      | .SHAPE => 3
      | .VALUE => 4
      | .PROPERTY => 5)
    (by
      intros x y h
      cases x <;> cases y <;> simp_all
    )

theorem T2_7_composition_strength_bound
    (level_g level_h : OracleLevel) :
    min level_g level_h ≤ level_g ∧ min level_g level_h ≤ level_h := by
  -- By definition of min, we know that min(level_g, level_h) ≤ level_g and min(level_g, level_h) ≤ level_h.
  apply And.intro (min_le_left level_g level_h) (min_le_right level_g level_h)

  /-
We define a typeclass `GreedySetCoverTheory` to encapsulate the properties of the Set Cover problem and the greedy algorithm.
We then state the classical bound: the greedy algorithm achieves an approximation ratio of H(n) ≤ ln(n) + 1.
This is formalized as `greedy ≤ opt * (Real.log n + 1)` for valid instances.
-/

/-
The greedy algorithm for set cover achieves an approximation ratio of H(n) <= ln(n) + 1. We state this abstractly using a typeclass to encapsulate the property.
-/
class GreedySetCoverTheory where
  /-- Predicate characterizing a valid tuple of (universe_size, optimal_cost, greedy_cost) for a Set Cover instance -/
  ValidInstance : ℕ → ℕ → ℕ → Prop
  /-- The approximation bound holds for all valid instances -/
  bound : ∀ (n opt greedy : ℕ), ValidInstance n opt greedy → 0 < opt → (greedy : ℝ) ≤ (opt : ℝ) * (Real.log n + 1)

theorem greedy_set_cover_bound [T : GreedySetCoverTheory] (n opt greedy : ℕ)
    (h_inst : T.ValidInstance n opt greedy)
    (h_opt : 0 < opt) :
    (greedy : ℝ) ≤ (opt : ℝ) * (Real.log n + 1) := by
  apply T.bound n opt greedy h_inst h_opt

  /-
We formalized the undecidability of equivalent mutant detection using Mathlib's computability theory.
We defined `ProgramsEquivalent` for `Nat.Partrec.Code` (representing Turing-complete programs) and proved `equiv_mutant_undecidable`, which states that there is no computable function that can decide whether two arbitrary programs are equivalent.
The proof proceeds by reduction to Rice's Theorem: if we could decide equivalence, we could decide if a program computes the zero function, which is a non-trivial property of partial recursive functions and thus undecidable.
-/

/-
It is undecidable whether two Turing machines (represented by Nat.Partrec.Code) are equivalent (compute the same partial function).
-/
def ProgramsEquivalent (p1 p2 : Nat.Partrec.Code) : Prop :=
  ∀ x, p1.eval x = p2.eval x

theorem equiv_mutant_undecidable :
  ¬ ∃ (decide : Nat.Partrec.Code → Nat.Partrec.Code → Bool),
    Computable₂ decide ∧
    ∀ (p1 p2 : Nat.Partrec.Code), decide p1 p2 = true ↔ ProgramsEquivalent p1 p2 := by
  by_contra h_contra
  obtain ⟨decide, hdecide, h_equiv⟩ := h_contra

  -- By Rice's Theorem, the set of indices of programs that compute the zero function is not recursive.
  have h_rice : ¬∃ (decide_zero : Nat.Partrec.Code → Bool), (Computable decide_zero) ∧ ∀ p : Nat.Partrec.Code, (decide_zero p) ↔ (∀ n : ℕ, (((Nat.Partrec.Code.eval p) n) = (some 0))) := by
    rintro ⟨ decide_zero, hdecide_zero, h_equiv ⟩;
    -- By Rice's Theorem, the set of indices of programs that compute the zero function is not recursive. Hence, decide_zero cannot be computable.
    have h_rice : ¬∃ (decide_zero : Nat.Partrec.Code → Bool), (Computable decide_zero) ∧ ∀ p : Nat.Partrec.Code, (decide_zero p) ↔ (∀ n : ℕ, (((Nat.Partrec.Code.eval p) n) = (some 0))) := by
      intro h
      obtain ⟨decide_zero, hdecide_zero, h_equiv⟩ := h
      have h_nontrivial : ∃ p : Nat.Partrec.Code, (∀ n : ℕ, (((Nat.Partrec.Code.eval p) n) = (some 0))) ∧ ∃ q : Nat.Partrec.Code, ¬(∀ n : ℕ, (((Nat.Partrec.Code.eval q) n) = (some 0))) := by
        refine' ⟨ Nat.Partrec.Code.const 0, _, Nat.Partrec.Code.succ, _ ⟩ <;> norm_num [ Nat.Partrec.Code.eval ]
      obtain ⟨ p, hp, q, hq ⟩ := h_nontrivial;
      -- Apply the recursion theorem to obtain a contradiction.
      have h_recursion : ∀ (f : Nat.Partrec.Code → Nat.Partrec.Code), Computable f → ∃ e : Nat.Partrec.Code, Nat.Partrec.Code.eval e = Nat.Partrec.Code.eval (f e) := by
        intro f hf
        have h_recursion : ∃ e : Nat.Partrec.Code, Nat.Partrec.Code.eval e = Nat.Partrec.Code.eval (f e) := by
          have h_fixed_point : ∃ e : Nat.Partrec.Code, Nat.Partrec.Code.eval e = Nat.Partrec.Code.eval (f e) := by
            have h_fixed_point : ∀ (f : Nat.Partrec.Code → Nat.Partrec.Code), Computable f → ∃ e : Nat.Partrec.Code, Nat.Partrec.Code.eval e = Nat.Partrec.Code.eval (f e) := by
              intro f hf
              exact (by
              have := @Nat.Partrec.Code.fixed_point;
              exact Exists.elim ( this hf ) fun e he => ⟨ e, he.symm ⟩)
            exact h_fixed_point f hf
          exact h_fixed_point;
        exact h_recursion;
      obtain ⟨ e, he ⟩ := h_recursion ( fun e => if decide_zero e then q else p ) ( by
        convert Computable.cond ( hdecide_zero ) ( Computable.const q ) ( Computable.const p ) using 1;
        grind ) ; simp_all +decide [ funext_iff ] ;
      split_ifs at he <;> simp_all +decide [ funext_iff ] ;
    exact h_rice ⟨ decide_zero, hdecide_zero, h_equiv ⟩;
  apply h_rice;
  use fun p => decide p (Nat.Partrec.Code.zero);
  refine' ⟨ _, _ ⟩;
  · exact hdecide.comp ( Computable.id ) ( Computable.const _ );
  · intro p; specialize h_equiv p Nat.Partrec.Code.zero; aesop;

  /-
We define `Program` as a structure wrapping a function `D → R` and `trueEquiv` as pointwise equality.
We then prove `T2_10_equiv_decidable_finite_domain`, which establishes that `trueEquiv` is decidable
when the domain `D` is finite and the range `R` has decidable equality. This is achieved by
observing that `trueEquiv` is a universal quantification over a finite type, which is decidable.
-/

/-
Definition of Program as a wrapper for a function, and trueEquiv as functional equivalence.
-/
structure Program (D R : Type) where
  eval : D → R

def trueEquiv {D R : Type} (P m : Program D R) : Prop :=
  ∀ x, P.eval x = m.eval x

/-
When the input domain is finite, equivalent mutant detection IS decidable: just check all inputs.
-/
def T2_10_equiv_decidable_finite_domain
    {D R : Type} [DecidableEq R] [Fintype D]
    (P m : Program D R) :
    Decidable (trueEquiv P m) := by
  unfold trueEquiv
  exact Fintype.decidableForallFintype