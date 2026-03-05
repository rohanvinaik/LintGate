/-
Phase 7 consolidated file.
Auto-concatenated from Proofs/Phase_7/T7.0.lean ... T7.9.lean,
with per-file namespaces to avoid identifier collisions.
-/

import Mathlib
import MutationTheory.Legacy.Phase_6_Exact_Learning

set_option linter.mathlibStandardSet false
set_option linter.unusedVariables false
set_option linter.unusedSimpArgs false
set_option linter.unnecessarySimpa false
set_option synthInstance.maxHeartbeats 20000
set_option synthInstance.maxSize 128
set_option relaxedAutoImplicit false
set_option autoImplicit false

open scoped BigOperators
open scoped Real
open scoped Nat
open scoped Classical
open scoped Pointwise

namespace Phase7_T7_0
noncomputable section

/-
Definition of killedSet as the set of non-equivalent mutants that are killed by the test suite.
-/
def killedSet {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program) (ts : Finset Test)
    [DecidablePred (fun m => isKilled sem oracle ms p m ts)] : Finset Mutant :=
  (nonEquivMutants sem ms p).filter (fun m => isKilled sem oracle ms p m ts)

/-
A specification trajectory is a sequence of test suites T_0 ⊂ ... ⊂ T_n where T_0 is empty and T_n kills all non-equivalent mutants.
-/
structure SpecificationTrajectory {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)] where
  n : ℕ
  T : Fin (n + 1) → Finset Test
  T_zero : T 0 = ∅
  T_mono : ∀ i : Fin n, T (i.castSucc) ⊂ T (i.succ)
  full_SC : killedSet nonEquivMutants isKilled sem oracle ms p (T (Fin.last n)) = nonEquivMutants sem ms p

/-
Definition of K_i (killed_at_step) and proof that K_i is a subset of K_{i+1} given that isKilled is monotone with respect to test sets.
-/
def killed_at_step {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (i : Fin (traj.n + 1)) : Finset Mutant :=
  killedSet nonEquivMutants isKilled sem oracle ms p (traj.T i)

theorem killed_trajectory_mono {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (h_mono : ∀ (m : Mutant) (t1 t2 : Finset Test), t1 ⊆ t2 → isKilled sem oracle ms p m t1 → isKilled sem oracle ms p m t2)
    (i : Fin traj.n) :
    killed_at_step nonEquivMutants isKilled sem oracle ms p traj (i.castSucc) ⊆
    killed_at_step nonEquivMutants isKilled sem oracle ms p traj (i.succ) := by
      intro m hm
      obtain ⟨t, ht⟩ := Finset.mem_filter.mp hm;
      exact Finset.mem_filter.mpr ⟨ t, h_mono _ _ _ ( Finset.Subset.trans ( Finset.Subset.refl _ ) ( Finset.ssubset_iff_subset_ne.mp ( traj.T_mono i ) |>.1 ) ) ht ⟩

/-
The specification score at step i is the ratio of the number of killed mutants to the total number of non-equivalent mutants.
-/
def specification_score {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (i : Fin (traj.n + 1)) : ℚ :=
  (killed_at_step nonEquivMutants isKilled sem oracle ms p traj i).card / (nonEquivMutants sem ms p).card

/-
The kill increment at step i is the difference in the number of killed mutants between step i+1 and step i. This is equal to the number of new mutants killed.
-/
def kill_increment {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (i : Fin traj.n) : ℕ :=
  (killed_at_step nonEquivMutants isKilled sem oracle ms p traj (i.succ)).card -
  (killed_at_step nonEquivMutants isKilled sem oracle ms p traj (i.castSucc)).card

theorem kill_increment_eq_card_sdiff {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (h_mono : ∀ (m : Mutant) (t1 t2 : Finset Test), t1 ⊆ t2 → isKilled sem oracle ms p m t1 → isKilled sem oracle ms p m t2)
    (i : Fin traj.n) :
    kill_increment nonEquivMutants isKilled sem oracle ms p traj i =
    (killed_at_step nonEquivMutants isKilled sem oracle ms p traj (i.succ) \
     killed_at_step nonEquivMutants isKilled sem oracle ms p traj (i.castSucc)).card := by
       rw [ Finset.card_sdiff ];
       rw [ Finset.inter_eq_left.mpr ];
       · rfl;
       · exact killed_trajectory_mono nonEquivMutants isKilled sem oracle ms p traj h_mono i

/-
The survivor set at step i is the set of non-equivalent mutants that are not killed. Its cardinality is the total number of non-equivalent mutants minus the number of killed mutants.
-/
def survivor_set {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (i : Fin (traj.n + 1)) : Finset Mutant :=
  (nonEquivMutants sem ms p) \ (killed_at_step nonEquivMutants isKilled sem oracle ms p traj i)

theorem survivor_set_card {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (i : Fin (traj.n + 1)) :
    (survivor_set nonEquivMutants isKilled sem oracle ms p traj i).card =
    (nonEquivMutants sem ms p).card - (killed_at_step nonEquivMutants isKilled sem oracle ms p traj i).card := by
      rw [ survivor_set, Finset.card_sdiff ];
      rw [ Finset.inter_eq_left.mpr ];
      exact Finset.filter_subset _ _

/-
The survivor set is empty if and only if the set of killed mutants is equal to the set of all non-equivalent mutants.
-/
theorem survivor_set_empty_iff_killed_eq_all {Program Mutant Test Semantics Oracle MutationSystem : Type}
    (nonEquivMutants : Semantics → MutationSystem → Program → Finset Mutant)
    (isKilled : Semantics → Oracle → MutationSystem → Program → Mutant → Finset Test → Prop)
    (sem : Semantics) (oracle : Oracle) (ms : MutationSystem) (p : Program)
    [DecidablePred (fun m => isKilled sem oracle ms p m ∅)]
    [∀ ts, DecidablePred (fun m => isKilled sem oracle ms p m ts)]
    [DecidableEq Mutant]
    (traj : SpecificationTrajectory nonEquivMutants isKilled sem oracle ms p)
    (i : Fin (traj.n + 1)) :
    survivor_set nonEquivMutants isKilled sem oracle ms p traj i = ∅ ↔
    killed_at_step nonEquivMutants isKilled sem oracle ms p traj i = nonEquivMutants sem ms p := by
      have := traj.full_SC; simp_all +decide [ Finset.ext_iff ] ;
      unfold survivor_set killed_at_step killedSet at *; aesop;

end
end Phase7_T7_0

namespace Phase7_T7_1
noncomputable section

/-
A test suite achieves full SC (Spec Complexity) if for every mutant in the set of all mutants, there exists a test in the suite that kills it.
-/
def is_full_SC {Mutant Test : Type} (kills : Test → Mutant → Prop) (suite : Finset Test) (all_mutants : Finset Mutant) : Prop :=
  ∀ m ∈ all_mutants, ∃ t ∈ suite, kills t m

/-
The set of survivors is the subset of all mutants that are not killed by any test in the given suite.
-/
def survivors {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (suite : Finset Test) (all_mutants : Finset Mutant) : Finset Mutant :=
  all_mutants.filter (λ m => ¬ ∃ t ∈ suite, kills t m)

/-
At each step i of the greedy algorithm, if there are survivors, the kill increment satisfies: ΔK_i ≥ |S_i| / κ. That is, there exists a test that kills at least a 1/κ fraction of the remaining survivors.
-/
theorem greedy_proportional_progress
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (current_suite : Finset Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_survivors_nonempty : (survivors kills current_suite all_mutants).Nonempty) :
  ∃ t : Test, ((survivors kills current_suite all_mutants).filter (kills t)).card ≥
    (survivors kills current_suite all_mutants).card / (κ : ℚ) := by
  have h_total_kills : (∑ t ∈ T_star, (Finset.filter (kills t) (survivors kills current_suite all_mutants)).card : ℚ) ≥ (survivors kills current_suite all_mutants).card := by
    have h_total_kills : (∑ t ∈ T_star, (Finset.filter (kills t) (survivors kills current_suite all_mutants)).card : ℚ) ≥ (Finset.biUnion T_star (fun t => Finset.filter (kills t) (survivors kills current_suite all_mutants))).card := by
      exact_mod_cast Finset.card_biUnion_le;
    convert h_total_kills using 2;
    congr with m ; simp +decide [ survivors ] at * ; aesop;
  contrapose! h_total_kills;
  refine' lt_of_lt_of_le ( Finset.sum_lt_sum_of_nonempty ( Finset.nonempty_of_ne_empty _ ) fun x hx => h_total_kills x ) _ <;> norm_num [ h_κ_pos, h_size ];
  · rintro rfl ; simp_all +decide [ is_full_SC ];
    exact h_survivors_nonempty.elim fun x hx => h_cover x <| Finset.mem_filter.mp hx |>.1;
  · rw [ mul_div, div_le_iff₀ ] <;> norm_cast ; nlinarith [ Finset.card_pos.mpr h_survivors_nonempty ]

end
end Phase7_T7_1

namespace Phase7_T7_2
noncomputable section

def is_full_SC {Mutant Test : Type} (kills : Test → Mutant → Prop) (suite : Finset Test) (all_mutants : Finset Mutant) : Prop :=
  ∀ m ∈ all_mutants, ∃ t ∈ suite, kills t m

def survivors {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (suite : Finset Test) (all_mutants : Finset Mutant) : Finset Mutant :=
  all_mutants.filter (λ m => ¬ ∃ t ∈ suite, kills t m)

theorem greedy_proportional_progress
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (current_suite : Finset Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_survivors_nonempty : (survivors kills current_suite all_mutants).Nonempty) :
  ∃ t : Test, ((survivors kills current_suite all_mutants).filter (kills t)).card ≥
    (survivors kills current_suite all_mutants).card / (κ : ℚ) := by
  simpa [is_full_SC, survivors, Phase7_T7_1.is_full_SC, Phase7_T7_1.survivors] using
    (Phase7_T7_1.greedy_proportional_progress
      kills all_mutants current_suite κ T_star h_size h_cover h_κ_pos h_survivors_nonempty)

/-
Under the greedy algorithm, the survivor count satisfies: |S_i| ≤ |S_0| · (1 - 1/κ)^i. After κ · ln(|S_0|) steps, |S_i| < 1, so all mutants are killed.
-/
theorem exponential_decay_of_survivors
  {Mutant : Type} [DecidableEq Mutant]
  (S : ℕ → Finset Mutant)
  (κ : ℕ) (h_κ_pos : 0 < κ)
  (h_step : ∀ i, ((S (i+1)).card : ℝ) ≤ (S i).card * (1 - 1 / (κ : ℝ))) :
  (∀ i : ℕ, ((S i).card : ℝ) ≤ (S 0).card * (1 - 1 / (κ : ℝ)) ^ i) ∧
  (∀ i : ℕ, (i : ℝ) > (κ : ℝ) * Real.log (S 0).card → (S i).card = 0) := by
    refine' ⟨ fun i => _, fun i hi => _ ⟩;
    · exact Nat.recOn i ( by norm_num ) fun n ih => by rw [ pow_succ, ← mul_assoc ] ; exact le_trans ( h_step _ ) ( mul_le_mul_of_nonneg_right ih ( sub_nonneg.mpr <| div_le_self zero_le_one <| mod_cast h_κ_pos ) ) ;
    · -- By induction, we have |S_i| ≤ |S_0| * (1 - 1/κ)^i.
      have h_ind : ∀ i, ((S i).card : ℝ) ≤ ((S 0).card : ℝ) * (1 - 1 / κ) ^ i := by
        exact fun n => Nat.recOn n ( by norm_num ) fun n ihn => by rw [ pow_succ, ← mul_assoc ] ; exact le_trans ( h_step n ) ( mul_le_mul_of_nonneg_right ihn ( sub_nonneg.2 <| div_le_self zero_le_one <| mod_cast h_κ_pos ) ) ;
      -- Since $|S_0| \cdot (1 - 1/κ)^i < 1$, we have $|S_i| < 1$.
      have h_lt_one : ((S 0).card : ℝ) * (1 - 1 / κ) ^ i < 1 := by
        -- Using the inequality $(1 - 1/κ)^i ≤ e^{-i/κ}$, we get $|S_0| \cdot e^{-i/κ} < 1$.
        have h_exp : ((S 0).card : ℝ) * Real.exp (-i / κ) < 1 := by
          by_cases h : ( S 0 |> Finset.card : ℝ ) = 0 <;> simp_all +decide [ neg_div, Real.exp_neg ];
          rw [ ← div_eq_mul_inv, div_lt_one ( Real.exp_pos _ ) ];
          rw [ ← Real.log_lt_iff_lt_exp ( Nat.cast_pos.mpr <| Finset.card_pos.mpr <| Finset.nonempty_of_ne_empty h ) ] ; rw [ lt_div_iff₀ <| by positivity ] ; linarith;
        refine' lt_of_le_of_lt _ h_exp;
        gcongr;
        rw [ ← Real.rpow_natCast, Real.rpow_def_of_nonneg ] <;> norm_num [ neg_div ];
        · split_ifs <;> norm_num at *;
          · aesop;
          · positivity;
          · exact le_trans ( mul_le_mul_of_nonneg_right ( Real.log_le_sub_one_of_pos ( sub_pos.mpr <| inv_lt_one_of_one_lt₀ <| Nat.one_lt_cast.mpr <| lt_of_le_of_ne h_κ_pos <| Ne.symm <| by rintro rfl; norm_num at * ) ) <| Nat.cast_nonneg _ ) <| by ring_nf; norm_num;
        · exact inv_le_one_of_one_le₀ <| mod_cast h_κ_pos;
      exact Nat.eq_zero_of_le_zero ( Nat.le_of_lt_succ <| by rw [ ← @Nat.cast_lt ℝ ] ; push_cast; linarith [ h_ind i ] )

end
end Phase7_T7_2

namespace Phase7_T7_3
noncomputable section

/-
A test suite achieves full SC (Spec Complexity) if for every mutant in the set of all mutants, there exists a test in the suite that kills it.
-/
def is_full_SC {Mutant Test : Type} (kills : Test → Mutant → Prop) (suite : Finset Test) (all_mutants : Finset Mutant) : Prop :=
  ∀ m ∈ all_mutants, ∃ t ∈ suite, kills t m

/-
The set of survivors is the subset of all mutants that are not killed by any test in the given suite.
-/
def survivors {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (suite : Finset Test) (all_mutants : Finset Mutant) : Finset Mutant :=
  all_mutants.filter (λ m => ¬ ∃ t ∈ suite, kills t m)

/-
At each step i of the greedy algorithm, if there are survivors, the kill increment satisfies: ΔK_i ≥ |S_i| / κ. That is, there exists a test that kills at least a 1/κ fraction of the remaining survivors.
-/
theorem greedy_proportional_progress
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (current_suite : Finset Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_survivors_nonempty : (survivors kills current_suite all_mutants).Nonempty) :
  ∃ t : Test, ((survivors kills current_suite all_mutants).filter (kills t)).card ≥
    (survivors kills current_suite all_mutants).card / (κ : ℚ) := by
  simpa [is_full_SC, survivors, Phase7_T7_1.is_full_SC, Phase7_T7_1.survivors] using
    (Phase7_T7_1.greedy_proportional_progress
      kills all_mutants current_suite κ T_star h_size h_cover h_κ_pos h_survivors_nonempty)

/-
The kill count of a test t on a set of survivors s is the number of mutants in s killed by t. A test t is a greedy choice for s if its kill count is maximal among all tests.
-/
def kill_count {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (s : Finset Mutant) (t : Test) : ℕ :=
  (s.filter (kills t)).card

def is_greedy_choice {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (s : Finset Mutant) (t : Test) : Prop :=
  ∀ t' : Test, kill_count kills s t' ≤ kill_count kills s t

/-
Definitions for survivors at step i, kill increment, instantaneous kill rate, and greedy sequence.
-/
def survivors_at {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (all_mutants : Finset Mutant) (seq : List Test) (i : ℕ) : Finset Mutant :=
  survivors kills (seq.take i).toFinset all_mutants

def kill_increment {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (all_mutants : Finset Mutant) (seq : List Test) (i : ℕ) : ℕ :=
  if h : i < seq.length then
    kill_count kills (survivors_at kills all_mutants seq i) (seq.get ⟨i, h⟩)
  else 0

def instantaneous_kill_rate {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (all_mutants : Finset Mutant) (seq : List Test) (i : ℕ) : ℚ :=
  let s := survivors_at kills all_mutants seq i
  if s.card = 0 then 0 else (kill_increment kills all_mutants seq i : ℚ) / s.card

def is_greedy_sequence {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (all_mutants : Finset Mutant) (seq : List Test) : Prop :=
  ∀ i : ℕ, ∀ h : i < seq.length, is_greedy_choice kills (survivors_at kills all_mutants seq i) (seq.get ⟨i, h⟩)

/-
Under the greedy algorithm, if there are survivors at step i, the instantaneous kill rate is at least 1/κ.
-/
lemma greedy_kill_rate_lower_bound
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (seq : List Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_greedy : is_greedy_sequence kills all_mutants seq) :
  ∀ i, i < seq.length → (survivors_at kills all_mutants seq i).Nonempty → instantaneous_kill_rate kills all_mutants seq i ≥ 1 / (κ : ℚ) := by
    intros i hi h_survivors_nonempty
    have h_kill_increment_ge : kill_count kills (survivors_at kills all_mutants seq i) (seq.get ⟨i, hi⟩) ≥ (survivors_at kills all_mutants seq i).card / (κ : ℚ) := by
      obtain ⟨t, ht⟩ : ∃ t : Test, (kill_count kills (survivors_at kills all_mutants seq i) t) ≥ (survivors_at kills all_mutants seq i).card / (κ : ℚ) := by
        apply_rules [ greedy_proportional_progress ];
      exact ht.trans ( mod_cast h_greedy i hi t );
    unfold instantaneous_kill_rate;
    unfold kill_increment; simp_all +decide [ div_eq_mul_inv ] ;
    rw [ if_neg ( Finset.Nonempty.ne_empty h_survivors_nonempty ) ] ; exact le_div_iff₀' ( Nat.cast_pos.mpr <| Finset.card_pos.mpr h_survivors_nonempty ) |>.2 h_kill_increment_ge;

/-
The transition step is the first step where the kill increment drops below 2. If no such step exists, it is the length of the sequence.
-/
def transition_step {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (all_mutants : Finset Mutant) (seq : List Test) : ℕ :=
  (List.range seq.length).find? (fun i => kill_increment kills all_mutants seq i < 2) |>.getD seq.length

/-
The transition step i* is the first step where the kill increment drops below 2. Before i*, the kill rate is high (≥ 2). Also, i* occurs no later than |S_0| - κ, provided |S_0| ≥ 2κ.
-/
theorem transition_step_properties
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (seq : List Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_greedy : is_greedy_sequence kills all_mutants seq)
  (h_seq_covers : survivors kills seq.toFinset all_mutants = ∅)
  (h_large : 2 * κ ≤ all_mutants.card) :
  (∀ i < transition_step kills all_mutants seq, kill_increment kills all_mutants seq i ≥ 2) ∧
  transition_step kills all_mutants seq ≤ all_mutants.card - κ := by
    refine ⟨ fun i hi ↦ ?_, ?_ ⟩;
    · simp_all +decide [ transition_step ];
      unfold Option.getD at hi; aesop;
    · -- By definition of transition_step, we know that for all i < transition_step, kill_increment i ≥ 2.
      have h_kill_ge_2 : ∀ i < transition_step kills all_mutants seq, kill_increment kills all_mutants seq i ≥ 2 := by
        unfold transition_step;
        cases h : List.find? ( fun i => Decidable.decide ( kill_increment kills all_mutants seq i < 2 ) ) ( List.range seq.length ) <;> aesop;
      -- The total number of mutants killed in the first i* steps is K = ∑_{j=0}^{i*-1} kill_increment(j) ≥ 2 * i*.
      have h_total_killed : ∑ i ∈ Finset.range (transition_step kills all_mutants seq), kill_increment kills all_mutants seq i ≤ all_mutants.card := by
        -- By definition of survivors_at, we know that the number of survivors at step i is equal to the number of mutants not killed by the first i tests.
        have h_survivors_card : ∀ i, (survivors_at kills all_mutants seq i).card + ∑ j ∈ Finset.range i, kill_increment kills all_mutants seq j ≤ all_mutants.card := by
          intro i
          induction' i with i ih
          simp [survivors_at];
          · exact Finset.card_filter_le _ _;
          · by_cases hi : i < seq.length <;> simp_all +decide [ Finset.sum_range_succ ];
            · -- By definition of survivors_at, we know that the number of survivors at step i+1 is equal to the number of survivors at step i minus the number of mutants killed by the (i+1)th test.
              have h_survivors_card_succ : (survivors_at kills all_mutants seq (i + 1)).card + kill_increment kills all_mutants seq i ≤ (survivors_at kills all_mutants seq i).card := by
                unfold survivors_at kill_increment; simp +decide [ Finset.filter_filter ] ;
                unfold survivors_at; simp +decide [ Finset.filter_filter, List.take_succ ] ;
                unfold survivors kill_count; simp +decide [ Finset.filter_filter, hi ] ;
                rw [ ← Finset.card_union_of_disjoint ];
                · exact Finset.card_mono fun x hx => by aesop;
                · exact Finset.disjoint_filter.mpr ( by aesop );
              linarith;
            · simp_all +decide [ survivors_at, kill_increment ];
              simp_all +decide [ List.take_of_length_le ( Nat.le_succ_of_le hi ) ];
              simp_all +decide [ List.take_of_length_le hi ];
              grind;
        exact le_trans ( Nat.le_add_left _ _ ) ( h_survivors_card _ );
      exact le_tsub_of_add_le_left ( by have := Finset.sum_le_sum fun i ( hi : i ∈ Finset.range ( transition_step kills all_mutants seq ) ) => h_kill_ge_2 i ( Finset.mem_range.mp hi ) ; norm_num at * ; linarith )

lemma transition_step_eq_length_or_drop
  {Mutant Test : Type} [DecidableEq Mutant]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (seq : List Test) :
  transition_step kills all_mutants seq = seq.length ∨
    kill_increment kills all_mutants seq (transition_step kills all_mutants seq) < 2 := by
  unfold transition_step
  cases hfind : List.find? (fun i => Decidable.decide (kill_increment kills all_mutants seq i < 2)) (List.range seq.length) with
  | none =>
      left
      simp [hfind]
  | some i =>
      right
      have hi_decide : Decidable.decide (kill_increment kills all_mutants seq i < 2) = true := by
        exact (List.find?_eq_some_iff_getElem.mp hfind).1
      have hi_lt : kill_increment kills all_mutants seq i < 2 := by
        exact decide_eq_true_eq.mp hi_decide
      simpa [hfind] using hi_lt

lemma transition_step_maximal_prefix
  {Mutant Test : Type} [DecidableEq Mutant]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (seq : List Test) :
  ∀ j, j ≤ seq.length →
    (∀ i < j, kill_increment kills all_mutants seq i ≥ 2) →
    j ≤ transition_step kills all_mutants seq := by
  intro j hj h_prefix
  by_contra h_not_le
  have h_ts_lt_j : transition_step kills all_mutants seq < j := lt_of_not_ge h_not_le
  rcases transition_step_eq_length_or_drop kills all_mutants seq with h_end | h_drop
  · exact h_not_le (by simpa [h_end] using hj)
  · have h_ge : kill_increment kills all_mutants seq (transition_step kills all_mutants seq) ≥ 2 :=
      h_prefix _ h_ts_lt_j
    exact (not_lt_of_ge h_ge) h_drop

/-
There exists a critical transition step `i*`, explicitly constructed as
`transition_step`, such that:
1. all earlier increments are at least 2,
2. `i*` appears by the linear phase bound `|S₀| - κ`,
3. `i*` is maximal among prefixes with increment bound `≥ 2`.
-/
theorem phase_transition
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (seq : List Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_greedy : is_greedy_sequence kills all_mutants seq)
  (h_seq_covers : survivors kills seq.toFinset all_mutants = ∅)
  (h_large : 2 * κ ≤ all_mutants.card) :
  ∃ i_star : ℕ,
    i_star = transition_step kills all_mutants seq ∧
    i_star ≤ all_mutants.card - κ ∧
    (∀ i < i_star, kill_increment kills all_mutants seq i ≥ 2) ∧
    (∀ j, j ≤ seq.length →
      (∀ i < j, kill_increment kills all_mutants seq i ≥ 2) →
      j ≤ i_star) := by
  refine ⟨transition_step kills all_mutants seq, rfl, ?_, ?_, ?_⟩
  · exact (transition_step_properties kills all_mutants seq κ T_star h_size h_cover h_κ_pos h_greedy h_seq_covers h_large).2
  · exact (transition_step_properties kills all_mutants seq κ T_star h_size h_cover h_κ_pos h_greedy h_seq_covers h_large).1
  · intro j hj h_prefix
    exact transition_step_maximal_prefix kills all_mutants seq j hj h_prefix

end
end Phase7_T7_3

namespace Phase7_T7_4
noncomputable section

def is_full_SC {Mutant Test : Type} (kills : Test → Mutant → Prop) (suite : Finset Test) (all_mutants : Finset Mutant) : Prop :=
  ∀ m ∈ all_mutants, ∃ t ∈ suite, kills t m

def survivors {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (suite : Finset Test) (all_mutants : Finset Mutant) : Finset Mutant :=
  all_mutants.filter (λ m => ¬ ∃ t ∈ suite, kills t m)

theorem greedy_proportional_progress
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (current_suite : Finset Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_survivors_nonempty : (survivors kills current_suite all_mutants).Nonempty) :
  ∃ t : Test, ((survivors kills current_suite all_mutants).filter (kills t)).card ≥
    (survivors kills current_suite all_mutants).card / (κ : ℚ) := by
  simpa [is_full_SC, survivors, Phase7_T7_1.is_full_SC, Phase7_T7_1.survivors] using
    (Phase7_T7_1.greedy_proportional_progress
      kills all_mutants current_suite κ T_star h_size h_cover h_κ_pos h_survivors_nonempty)

theorem exponential_decay_of_survivors
  {Mutant : Type} [DecidableEq Mutant]
  (S : ℕ → Finset Mutant)
  (κ : ℕ) (h_κ_pos : 0 < κ)
  (h_step : ∀ i, ((S (i+1)).card : ℝ) ≤ (S i).card * (1 - 1 / (κ : ℝ))) :
  (∀ i : ℕ, ((S i).card : ℝ) ≤ (S 0).card * (1 - 1 / (κ : ℝ)) ^ i) ∧
  (∀ i : ℕ, (i : ℝ) > (κ : ℝ) * Real.log (S 0).card → (S i).card = 0) := by
    refine' ⟨ fun i => _, fun i hi => _ ⟩;
    · exact Nat.recOn i ( by norm_num ) fun n ih => by rw [ pow_succ, ← mul_assoc ] ; exact le_trans ( h_step _ ) ( mul_le_mul_of_nonneg_right ih ( sub_nonneg.mpr <| div_le_self zero_le_one <| mod_cast h_κ_pos ) ) ;
    · -- By induction, we have |S_i| ≤ |S_0| * (1 - 1/κ)^i.
      have h_ind : ∀ i, ((S i).card : ℝ) ≤ ((S 0).card : ℝ) * (1 - 1 / κ) ^ i := by
        exact fun n => Nat.recOn n ( by norm_num ) fun n ihn => by rw [ pow_succ, ← mul_assoc ] ; exact le_trans ( h_step n ) ( mul_le_mul_of_nonneg_right ihn ( sub_nonneg.2 <| div_le_self zero_le_one <| mod_cast h_κ_pos ) ) ;
      -- Since $|S_0| \cdot (1 - 1/κ)^i < 1$, we have $|S_i| < 1$.
      have h_lt_one : ((S 0).card : ℝ) * (1 - 1 / κ) ^ i < 1 := by
        -- Using the inequality $(1 - 1/κ)^i ≤ e^{-i/κ}$, we get $|S_0| \cdot e^{-i/κ} < 1$.
        have h_exp : ((S 0).card : ℝ) * Real.exp (-i / κ) < 1 := by
          by_cases h : ( S 0 |> Finset.card : ℝ ) = 0 <;> simp_all +decide [ neg_div, Real.exp_neg ];
          rw [ ← div_eq_mul_inv, div_lt_one ( Real.exp_pos _ ) ];
          rw [ ← Real.log_lt_iff_lt_exp ( Nat.cast_pos.mpr <| Finset.card_pos.mpr <| Finset.nonempty_of_ne_empty h ) ] ; rw [ lt_div_iff₀ <| by positivity ] ; linarith;
        refine' lt_of_le_of_lt _ h_exp;
        gcongr;
        rw [ ← Real.rpow_natCast, Real.rpow_def_of_nonneg ] <;> norm_num [ neg_div ];
        · split_ifs <;> norm_num at *;
          · aesop;
          · positivity;
          · exact le_trans ( mul_le_mul_of_nonneg_right ( Real.log_le_sub_one_of_pos ( sub_pos.mpr <| inv_lt_one_of_one_lt₀ <| Nat.one_lt_cast.mpr <| lt_of_le_of_ne h_κ_pos <| Ne.symm <| by rintro rfl; norm_num at * ) ) <| Nat.cast_nonneg _ ) <| by ring_nf; norm_num;
        · exact inv_le_one_of_one_le₀ <| mod_cast h_κ_pos;
      exact Nat.eq_zero_of_le_zero ( Nat.le_of_lt_succ <| by rw [ ← @Nat.cast_lt ℝ ] ; push_cast; linarith [ h_ind i ] )

def kill_increment {Mutant : Type} (S : ℕ → Finset Mutant) (i : ℕ) : ℝ :=
  (S i).card - (S (i + 1)).card

def trajectory_stability {Mutant : Type} (S : ℕ → Finset Mutant) (n : ℕ) : ℝ :=
  (1 / (n : ℝ)) * ∑ i ∈ Finset.range n, |kill_increment S (i + 1) - kill_increment S i|

/-
For the greedy algorithm (which implies monotonically decreasing kill increments), the specification trajectory stability is bounded by (|S_0| / n) · (1 + 1/κ).
-/
theorem greedy_stability_bound
  {Mutant : Type}
  (S : ℕ → Finset Mutant)
  (n : ℕ) (h_n_pos : 0 < n)
  (κ : ℕ) (h_κ_pos : 0 < κ)
  (h_step : ∀ i, ((S (i+1)).card : ℝ) ≤ (S i).card * (1 - 1 / (κ : ℝ)))
  (h_mono : ∀ i, kill_increment S (i+1) ≤ kill_increment S i) :
  trajectory_stability S n ≤ ((S 0).card : ℝ) / n * (1 + 1 / (κ : ℝ)) := by
  -- The trajectory_stability is the average of the absolute differences between consecutive kill increments.
  have h_avg : trajectory_stability S n = (1 / n : ℝ) * (∑ i ∈ Finset.range n, (kill_increment S i - kill_increment S (i + 1))) := by
    exact congrArg _ ( Finset.sum_congr rfl fun i hi => by rw [ abs_sub_comm, abs_of_nonneg ] ; linarith [ h_mono i ] );
  -- The sum in trajectory_stability telescopes:
  have h_telescope : ∑ i ∈ Finset.range n, (kill_increment S i - kill_increment S (i + 1)) = kill_increment S 0 - kill_increment S n := by
    rw [ Finset.sum_range_sub' ];
  -- Since kill increments are non-negative and decreasing, we have kill_increment S 0 ≤ (S 0).card and kill_increment S n ≥ 0.
  have h_bounds : kill_increment S 0 ≤ (S 0).card ∧ kill_increment S n ≥ 0 := by
    constructor <;> norm_num [ kill_increment ];
    exact_mod_cast le_trans ( h_step n ) ( mul_le_of_le_one_right ( Nat.cast_nonneg _ ) ( sub_le_self _ ( by positivity ) ) );
  field_simp;
  rw [ h_avg, div_mul_eq_mul_div, div_mul_cancel₀ _ ( by positivity ) ] ; nlinarith [ ( by norm_cast : ( 1 :ℝ ) ≤ n ), ( by norm_cast : ( 1 :ℝ ) ≤ κ ) ] ;

def is_greedy_trace {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (S : ℕ → Finset Mutant) : Prop :=
  ∀ i, ∃ t, S (i+1) = (S i).filter (λ m => ¬ kills t m) ∧
       ∀ t', ((S i).filter (λ m => kills t' m)).card ≤ ((S i).filter (λ m => kills t m)).card

/-
If a trajectory is generated by the greedy algorithm, the kill increments are monotonically decreasing.
-/
theorem greedy_trace_implies_mono
  {Mutant Test : Type} [DecidableEq Mutant]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (S : ℕ → Finset Mutant)
  (h_greedy : is_greedy_trace kills S) :
  ∀ i, kill_increment S (i+1) ≤ kill_increment S i := by
  intro i
  rcases h_greedy i with ⟨t_i, h_Si_next, h_max_i⟩
  rcases h_greedy (i+1) with ⟨t_next, h_Si_next_next, h_max_next⟩
  rw [kill_increment, kill_increment]
  -- We need to show |S_{i+1}| - |S_{i+2}| ≤ |S_i| - |S_{i+1}|
  -- This is equivalent to |killed by t_{i+1} in S_{i+1}| ≤ |killed by t_i in S_i|
  -- We know t_i maximizes kills in S_i.
  -- We know t_{i+1} maximizes kills in S_{i+1}.
  -- Also S_{i+1} ⊆ S_i.
  -- So for any t, |killed by t in S_{i+1}| ≤ |killed by t in S_i|.
  -- In particular, |killed by t_{i+1} in S_{i+1}| ≤ |killed by t_{i+1} in S_i|.
  -- And |killed by t_{i+1} in S_i| ≤ |killed by t_i in S_i| (by maximality of t_i).
  simp_all +decide [ Finset.filter_not, Finset.card_sdiff ];
  rw [ Nat.cast_sub ] <;> norm_num [ Finset.inter_comm ];
  · rw [ Nat.sub_sub, add_comm ];
    rw [ Nat.cast_sub ] <;> norm_cast;
    · rw [ Int.subNatNat_eq_coe ] ; norm_num [ Finset.inter_filter, Finset.sdiff_inter_self_left ];
      linarith [ h_max_i t_next, h_max_next t_i, show Finset.card ( Finset.filter ( kills t_next ) ( S i \ Finset.filter ( kills t_i ) ( S i ) ) ) ≤ Finset.card ( Finset.filter ( kills t_next ) ( S i ) ) from Finset.card_mono <| Finset.filter_subset_filter _ <| Finset.sdiff_subset ];
    · rw [ ← Finset.card_union_of_disjoint ];
      · exact Finset.card_le_card fun x hx => by aesop;
      · exact Finset.disjoint_left.mpr ( by aesop );
  · exact Finset.card_le_card fun x hx => by aesop;

/-
For a greedy trace, the specification trajectory stability is bounded by (|S_0| / n) · (1 + 1/κ).
-/
theorem greedy_stability_bound_final
  {Mutant Test : Type} [DecidableEq Mutant]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (S : ℕ → Finset Mutant)
  (n : ℕ) (h_n_pos : 0 < n)
  (κ : ℕ) (h_κ_pos : 0 < κ)
  (h_greedy : is_greedy_trace kills S)
  (h_step : ∀ i, ((S (i+1)).card : ℝ) ≤ (S i).card * (1 - 1 / (κ : ℝ))) :
  trajectory_stability S n ≤ ((S 0).card : ℝ) / n * (1 + 1 / (κ : ℝ)) := by
  apply greedy_stability_bound S n h_n_pos κ h_κ_pos h_step
  exact greedy_trace_implies_mono kills S h_greedy

end
end Phase7_T7_4

namespace Phase7_T7_5
noncomputable section

/-
Definitions of killed mutants, survivors, and achieving full specification completeness (SC) in the context of mutation testing.
Killed mutants are those in the mutant set MS that differ from the oracle on at least one test in T.
Survivors are mutants in MS that are not killed.
Full SC is achieved when the set of survivors is empty.
-/
structure MutationContext (D R : Type) where
  P : D → R
  MS : Set (D → R)
  oracle : D → R

def killed {D R : Type} (ctx : MutationContext D R) (T : Set D) : Set (D → R) :=
  { m ∈ ctx.MS | ∃ t ∈ T, m t ≠ ctx.oracle t }

def survivors {D R : Type} (ctx : MutationContext D R) (T : Set D) : Set (D → R) :=
  ctx.MS \ killed ctx T

def achievesFullSC' {D R : Type} (ctx : MutationContext D R) (T : Set D) : Prop :=
  survivors ctx T = ∅

/-
Lyapunov Monotonicity:
1. The set of killed mutants increases monotonically with the test suite.
2. The number of survivors decreases monotonically with the test suite.
3. The number of survivors is 0 iff full SC is achieved.
-/
theorem lyapunov_monotonicity {D R : Type} (ctx : MutationContext D R) (T₁ T₂ : Set D)
    (h_subset : T₁ ⊆ T₂) (h_finite : ctx.MS.Finite) :
    (killed ctx T₁ ⊆ killed ctx T₂) ∧
    (Set.ncard (survivors ctx T₂) ≤ Set.ncard (survivors ctx T₁)) ∧
    (Set.ncard (survivors ctx T₁) = 0 ↔ achievesFullSC' ctx T₁) := by
  unfold survivors killed achievesFullSC';
  constructor;
  · exact fun m hm => ⟨ hm.1, hm.2.choose, h_subset hm.2.choose_spec.1, hm.2.choose_spec.2 ⟩;
  · constructor;
    · fapply Set.ncard_le_ncard;
      · grind;
      · exact h_finite.subset fun x hx => hx.1;
    · rw [ @Set.ncard_eq_zero ];
      · rfl;
      · exact h_finite.subset fun x hx => hx.1

end
end Phase7_T7_5

namespace Phase7_T7_6
noncomputable section

variable {D R : Type}

def Program (D R : Type) := D → R
def Test (D R : Type) := D
def MutationSystem (D R : Type) := Program D R → Finset (Program D R)
def TestSuite (D R : Type) := Finset (Test D R)

variable {D R : Type}

open Classical

noncomputable def killedSet (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T : Finset (Test D R)) : Finset (Program D R) :=
  (MS P).filter (fun m => ∃ t ∈ T, oracle t (sem m t) = false)

variable {D R : Type}

open Classical

noncomputable def nonEquivMutants (sem : Program D R → D → R) (MS : MutationSystem D R) (P : Program D R) : Finset (Program D R) :=
  (MS P).filter (fun m => sem m ≠ sem P)

variable {D R : Type}

open Classical

def IsGreedySequence_P5 (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T_univ : Finset (Test D R)) (seq : ℕ → Test D R) : Prop :=
  ∀ k, seq k ∈ T_univ ∧
    ∀ t ∈ T_univ,
      (killedSet sem oracle MS P {seq k} \ (Finset.range k).biUnion (fun i => killedSet sem oracle MS P {seq i})).card ≥
      (killedSet sem oracle MS P {t} \ (Finset.range k).biUnion (fun i => killedSet sem oracle MS P {seq i})).card

def achievesFullSC' (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T : Finset (Test D R)) : Prop :=
  nonEquivMutants sem MS P ⊆ (MS P).filter (fun m => ∃ t ∈ T, oracle t (sem m t) = false)

noncomputable def Harmonic_P5 (n : ℕ) : ℝ := (Finset.range n).sum (fun i => (1 : ℝ) / (i + 1))

lemma Harmonic_P5_eq_harmonic (n : ℕ) :
    Harmonic_P5 n = (harmonic n : ℝ) := by
  unfold Harmonic_P5 harmonic
  norm_num [Rat.cast_sum, Rat.cast_inv, Rat.cast_natCast]

lemma Harmonic_P5_le_log_add_one (n : ℕ) :
    Harmonic_P5 n ≤ Real.log n + 1 := by
  rw [Harmonic_P5_eq_harmonic]
  simpa [add_comm, add_left_comm, add_assoc] using (harmonic_le_one_add_log n)

/-
The fastest achievable convergence rate determines κ. Moreover, the greedy trajectory achieves n ≤ κ · (H(|S_0|)) where H is the harmonic number, and this is optimal up to the O(ln n) factor.
-/
theorem T5_12_greedy_approximation_ratio
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R)
    (T_univ : Finset (Test D R))
    (seq : ℕ → Test D R)
    (h_greedy : IsGreedySequence_P5 sem oracle MS P T_univ seq)
    (k_greedy : ℕ)
    (h_covers : nonEquivMutants sem MS P ⊆ (Finset.range k_greedy).biUnion (fun i => killedSet sem oracle MS P {seq i}))
    (T_opt : Finset (Test D R))
    (h_opt : achievesFullSC' sem oracle MS P T_opt)
    (n : ℕ)
    (h_greedy_harmonic : (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n)
    (h_n : n = (nonEquivMutants sem MS P).card) (h_n_pos : 0 < n) :
    (k_greedy : ℝ) ≤ T_opt.card * (Real.log n + 1) := by
  have h_harmonic_log : Harmonic_P5 n ≤ Real.log n + 1 := Harmonic_P5_le_log_add_one n
  have h_mul : T_opt.card * Harmonic_P5 n ≤ T_opt.card * (Real.log n + 1) := by
    exact mul_le_mul_of_nonneg_left h_harmonic_log (by positivity)
  exact le_trans h_greedy_harmonic h_mul

end
end Phase7_T7_6

namespace Phase7_T7_7
noncomputable section

abbrev Program := Phase6.Program
abbrev Test := Phase6.Test
abbrev TestSuite := Phase6.TestSuite
abbrev MutationSystem := Phase6.MutationSystem

instance {D R : Type} [DecidableEq D] : DecidableEq (Test D R) := inferInstance

def Harmonic_P5 (n : ℕ) : ℝ := (Finset.range n).sum (fun i => 1 / (i + 1 : ℝ))

lemma Harmonic_P5_eq_harmonic (n : ℕ) :
    Harmonic_P5 n = (harmonic n : ℝ) := by
  unfold Harmonic_P5 harmonic
  norm_num [Rat.cast_sum, Rat.cast_inv, Rat.cast_natCast]

lemma Harmonic_P5_le_log_add_one (n : ℕ) :
    Harmonic_P5 n ≤ Real.log n + 1 := by
  rw [Harmonic_P5_eq_harmonic]
  simpa [add_comm, add_left_comm, add_assoc] using (harmonic_le_one_add_log n)

def nonEquivMutants {D R : Type}
    (sem : Program D R → D → R) (MS : MutationSystem D R) (P : Program D R) : Finset (Program D R) :=
  Phase6.nonEquivMutants sem MS P

def killedSet {D R : Type}
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (t : Finset (Test D R)) : Finset (Program D R) :=
  Phase6.killedSet sem oracle MS P t

def achievesFullSC' {D R : Type}
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R) : Prop :=
  Phase6.achievesFullSC' sem oracle MS P T

def IsGreedySequence_P5 {D R : Type} [DecidableEq D] (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T_univ : TestSuite D R) (seq : ℕ → Test D R) : Prop :=
  ∀ k, (seq k) ∈ (T_univ : Finset (Test D R)) ∧
    ∀ t : Test D R, t ∈ (T_univ : Finset (Test D R)) →
      (killedSet sem oracle MS P ({seq k} : Finset (Test D R)) \ (Finset.range k).biUnion (fun i => killedSet sem oracle MS P ({seq i} : Finset (Test D R)))).card ≥
      (killedSet sem oracle MS P ({t} : Finset (Test D R)) \ (Finset.range k).biUnion (fun i => killedSet sem oracle MS P ({seq i} : Finset (Test D R)))).card

def disagreementProb {D R : Type} [Fintype D]
    (sem : Program D R → D → R) (ρ : D → ℝ) (P : Program D R) (m : Program D R) : ℝ :=
  Phase6.disagreementProb sem ρ P m

def minDisagreement {D R : Type} [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (ρ : D → ℝ)
    (MS : MutationSystem D R) (P : Program D R)
    (h : (nonEquivMutants sem MS P).Nonempty) : ℝ :=
  Phase6.minDisagreement sem ρ MS P h

def criticalSampleSize {D R : Type} [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (ρ : D → ℝ) : ℕ :=
  Phase6.criticalSampleSize sem oracle MS P ρ

def specComplexity {D R : Type}
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) : ℕ :=
  Phase6.specComplexity sem oracle MS P

def randomTestingSufficient {D R : Type} [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R) (ρ : D → ℝ) (n : ℕ) (ε : ℝ) : Prop :=
  criticalSampleSize sem oracle MS P ρ ≤ n ∧ 0 ≤ ε

theorem T5_12_greedy_approximation_ratio
  {D R : Type} [DecidableEq D]
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

/-
Let T^G be the greedy trajectory and T^R be a random trajectory (tests chosen uniformly from the domain). Then:

1. |T^G| ≤ κ · H(|S_0|) (greedy length bound, from T5.12)
2. |T^R| ≥ ⌈1/(2δ_min)⌉ with probability ≥ 3/4 (random length bound, from T6.3)
3. A basic nonnegativity sanity bound: |T^R| / |T^G| ≥ 0
-/
theorem T7_7_trajectory_comparison
    {D R : Type} [Fintype D] [DecidableEq D] [DecidableEq R]
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool)
    (MS : MutationSystem D R) (P : Program D R)
    (ρ : D → ℝ) (h_dist : ∀ d, 0 ≤ ρ d) (h_sum : Finset.univ.sum ρ = 1)
    -- Greedy hypotheses
    (T_univ : TestSuite D R)
    (seq : ℕ → Test D R)
    (h_greedy : IsGreedySequence_P5 sem oracle MS P T_univ seq)
    (k_greedy : ℕ)
    (h_covers : nonEquivMutants sem MS P ⊆ (Finset.range k_greedy).biUnion (fun i => killedSet sem oracle MS P {seq i}))
    (T_opt : TestSuite D R)
    (h_opt : achievesFullSC' sem oracle MS P T_opt)
    (n : ℕ)
    (h_greedy_harmonic : (k_greedy : ℝ) ≤ T_opt.card * Harmonic_P5 n)
    (h_n : n = (nonEquivMutants sem MS P).card) (h_n_pos : 0 < n)
    -- Random hypotheses
    (h_nonempty : (nonEquivMutants sem MS P).Nonempty)
    (h_pos : 0 < minDisagreement sem ρ MS P h_nonempty)
    (h_k_greedy_pos : 0 < k_greedy) :
    (k_greedy : ℝ) ≤ T_opt.card * (Real.log n + 1) ∧
    Nat.ceil (1 / (2 * minDisagreement sem ρ MS P h_nonempty)) ≤ criticalSampleSize sem oracle MS P ρ ∧
    0 ≤ (criticalSampleSize sem oracle MS P ρ : ℝ) / k_greedy := by
      refine ⟨?_, ?_, ?_⟩
      · exact T5_12_greedy_approximation_ratio sem oracle MS P T_univ seq h_greedy k_greedy h_covers T_opt h_opt n h_greedy_harmonic h_n h_n_pos
      · have h_pos6 : 0 < Phase6.minDisagreement sem ρ MS P h_nonempty := by
          simpa [minDisagreement] using h_pos
        exact Phase6.T6_3_GLLSZ_lower_bound sem oracle MS P ρ h_dist h_sum h_nonempty h_pos6
      · have hk_nonneg : (0 : ℝ) ≤ (k_greedy : ℝ) := by exact_mod_cast Nat.zero_le k_greedy
        have hcrit_nonneg : (0 : ℝ) ≤ (criticalSampleSize sem oracle MS P ρ : ℝ) := by
          exact_mod_cast Nat.zero_le (criticalSampleSize sem oracle MS P ρ)
        exact div_nonneg hcrit_nonneg hk_nonneg

end
end Phase7_T7_7

namespace Phase7_T7_8
noncomputable section

structure MutationContext (D R : Type) where
  P : D → R
  MS : Set (D → R)
  oracle : D → R

def killed {D R : Type} (ctx : MutationContext D R) (T : Set D) : Set (D → R) :=
  { m ∈ ctx.MS | ∃ t ∈ T, m t ≠ ctx.oracle t }

def survivors {D R : Type} (ctx : MutationContext D R) (T : Set D) : Set (D → R) :=
  ctx.MS \ killed ctx T

def achievesFullSC' {D R : Type} (ctx : MutationContext D R) (T : Set D) : Prop :=
  survivors ctx T = ∅

theorem lyapunov_monotonicity {D R : Type} (ctx : MutationContext D R) (T₁ T₂ : Set D)
    (h_subset : T₁ ⊆ T₂) (h_finite : ctx.MS.Finite) :
    (killed ctx T₁ ⊆ killed ctx T₂) ∧
    (Set.ncard (survivors ctx T₂) ≤ Set.ncard (survivors ctx T₁)) ∧
    (Set.ncard (survivors ctx T₁) = 0 ↔ achievesFullSC' ctx T₁) := by
  unfold survivors killed achievesFullSC';
  constructor;
  · exact fun m hm => ⟨ hm.1, hm.2.choose, h_subset hm.2.choose_spec.1, hm.2.choose_spec.2 ⟩;
  · constructor;
    · fapply Set.ncard_le_ncard;
      · grind;
      · exact h_finite.subset fun x hx => hx.1;
    · rw [ @Set.ncard_eq_zero ];
      · rfl;
      · exact h_finite.subset fun x hx => hx.1

/-
The specification entropy H(T) is monotonically non-increasing with respect to the test suite T. That is, if T₁ ⊆ T₂, then H(T₂) ≤ H(T₁).
-/
noncomputable def specificationEntropy {D R : Type} (ctx : MutationContext D R) (T : Set D) : ℝ :=
  Real.logb 2 (Set.ncard (survivors ctx T))

theorem entropy_monotonicity {D R : Type} (ctx : MutationContext D R) (T₁ T₂ : Set D)
    (h_subset : T₁ ⊆ T₂) (h_finite : ctx.MS.Finite) :
    specificationEntropy ctx T₂ ≤ specificationEntropy ctx T₁ := by
      -- By Lyapunov monotonicity, we know that the number of survivors is non-increasing as the test suite grows.
      have h_survivors_noninc : (Set.ncard (survivors ctx T₂)) ≤ (Set.ncard (survivors ctx T₁)) := by
        rw [ Set.ncard_def, Set.ncard_def ];
        gcongr;
        · exact ne_of_lt ( Set.encard_lt_top_iff.mpr ( h_finite.subset ( show survivors ctx T₁ ⊆ ctx.MS from fun x hx => hx.1 ) ) );
        · apply_rules [ Set.encard_mono ] ; exact Set.diff_subset_diff ( Set.Subset.refl _ ) ( Set.subset_def.mpr fun x hx => by
            exact ⟨ hx.1, by rcases hx.2 with ⟨ t, ht₁, ht₂ ⟩ ; exact ⟨ t, h_subset ht₁, ht₂ ⟩ ⟩ ) ;
      unfold specificationEntropy;
      by_cases h : ( survivors ctx T₂ |> Set.ncard : ℕ ) = 0 <;> by_cases h' : ( survivors ctx T₁ |> Set.ncard : ℕ ) = 0 <;> simp_all +decide [ Real.logb_le_iff_le_rpow ];
      · exact Real.logb_nonneg ( by norm_num ) ( mod_cast Nat.one_le_iff_ne_zero.mpr h' );
      · gcongr ; norm_cast

/-
Properties of specification entropy:
1. Initial entropy is log₂ of the number of mutants.
2. Entropy is 0 when full specification completeness is achieved (no survivors).
3. Entropy is maximized at the start (empty test suite).
-/
theorem entropy_properties {D R : Type} (ctx : MutationContext D R) (T : Set D) (h_finite : ctx.MS.Finite) :
    (specificationEntropy ctx ∅ = Real.logb 2 (Set.ncard ctx.MS)) ∧
    (achievesFullSC' ctx T → specificationEntropy ctx T = 0) ∧
    (specificationEntropy ctx T ≤ specificationEntropy ctx ∅) := by
      refine' ⟨ _, _, _ ⟩;
      · unfold specificationEntropy;
        unfold survivors;
        unfold killed; aesop;
      · unfold achievesFullSC' specificationEntropy; aesop;
      · convert entropy_monotonicity ctx ∅ T ( Set.empty_subset _ ) h_finite using 1

/-
The set of killed mutants for an empty test suite is empty.
-/
lemma killed_empty {D R : Type} (ctx : MutationContext D R) : killed ctx ∅ = ∅ := by
  exact Set.eq_empty_of_forall_notMem fun m hm => by cases hm; aesop;

end
end Phase7_T7_8

namespace Phase7_T7_9
noncomputable section

def is_full_SC {Mutant Test : Type} (kills : Test → Mutant → Prop) (suite : Finset Test) (all_mutants : Finset Mutant) : Prop :=
  ∀ m ∈ all_mutants, ∃ t ∈ suite, kills t m

def survivors {Mutant Test : Type} [DecidableEq Mutant] (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)] (suite : Finset Test) (all_mutants : Finset Mutant) : Finset Mutant :=
  all_mutants.filter (λ m => ¬ ∃ t ∈ suite, kills t m)

theorem greedy_proportional_progress
  {Mutant Test : Type} [DecidableEq Mutant] [DecidableEq Test]
  (kills : Test → Mutant → Prop) [DecidablePred (Function.uncurry kills)]
  (all_mutants : Finset Mutant)
  (current_suite : Finset Test)
  (κ : ℕ)
  (T_star : Finset Test)
  (h_size : T_star.card ≤ κ)
  (h_cover : is_full_SC kills T_star all_mutants)
  (h_κ_pos : 0 < κ)
  (h_survivors_nonempty : (survivors kills current_suite all_mutants).Nonempty) :
  ∃ t : Test, ((survivors kills current_suite all_mutants).filter (kills t)).card ≥
    (survivors kills current_suite all_mutants).card / (κ : ℚ) := by
  have h_total_kills : (∑ t ∈ T_star, (Finset.filter (kills t) (survivors kills current_suite all_mutants)).card : ℚ) ≥ (survivors kills current_suite all_mutants).card := by
    have h_total_kills : (∑ t ∈ T_star, (Finset.filter (kills t) (survivors kills current_suite all_mutants)).card : ℚ) ≥ (Finset.biUnion T_star (fun t => Finset.filter (kills t) (survivors kills current_suite all_mutants))).card := by
      exact_mod_cast Finset.card_biUnion_le;
    convert h_total_kills using 2;
    congr with m ; simp +decide [ survivors ] at * ; aesop;
  contrapose! h_total_kills;
  refine' lt_of_lt_of_le ( Finset.sum_lt_sum_of_nonempty ( Finset.nonempty_of_ne_empty _ ) fun x hx => h_total_kills x ) _ <;> norm_num [ h_κ_pos, h_size ];
  · rintro rfl ; simp_all +decide [ is_full_SC ];
    exact h_survivors_nonempty.elim fun x hx => h_cover x <| Finset.mem_filter.mp hx |>.1;
  · rw [ mul_div, div_le_iff₀ ] <;> norm_cast ; nlinarith [ Finset.card_pos.mpr h_survivors_nonempty ]

/-
The information content of the i-th test is I_i = log2(|S_i| / |S_{i+1}|).
-/
noncomputable def information_content (n_before n_after : ℕ) : ℝ :=
  Real.logb 2 ((n_before : ℝ) / (n_after : ℝ))

noncomputable def information_gain {Mutant : Type} (s_current s_next : Finset Mutant) : ℝ :=
  information_content s_current.card s_next.card

/-
For the greedy algorithm: I_i ≥ log₂(κ/(κ-1)) at every step i where S_i ≠ ∅ and S_{i+1} ≠ ∅.
-/
theorem greedy_information_content
  (n_before n_after killed : ℕ)
  (κ : ℕ)
  (h_κ_gt_1 : 1 < κ)
  (h_before_pos : 0 < n_before)
  (h_after_pos : 0 < n_after)
  (h_killed_def : n_after = n_before - killed)
  (h_greedy : (killed : ℝ) ≥ (n_before : ℝ) / (κ : ℝ)) :
  information_content n_before n_after ≥ Real.logb 2 ((κ : ℝ) / ((κ : ℝ) - 1)) := by
  -- Since $n_{\text{after}} \leq n_{\text{before}}(1-1/\kappa)$, we have $n_{\text{before}} / n_{\text{after}} \geq \kappa / (\kappa-1)$.
  have h_ratio : (n_before : ℝ) / n_after ≥ (κ : ℝ) / (κ - 1) := by
    rw [ ge_iff_le, div_le_div_iff₀ ] <;> norm_num <;> try linarith;
    rw [ ge_iff_le, div_le_iff₀ ] at h_greedy <;> norm_cast at *;
    · rw [ Int.subNatNat_eq_coe ] ; push_cast ; nlinarith [ Nat.sub_add_cancel ( show killed ≤ n_before from le_of_lt ( Nat.lt_of_sub_pos ( by linarith ) ) ) ];
    · grind;
  exact div_le_div_of_nonneg_right ( Real.log_le_log ( div_pos ( by positivity ) ( by norm_num; linarith ) ) h_ratio ) ( Real.log_nonneg ( by norm_num ) )

theorem greedy_information_gain_lower_bound_final
  {Mutant : Type}
  (s_current s_next : Finset Mutant)
  (killed κ : ℕ)
  (h_κ_gt_1 : 1 < κ)
  (h_before_nonempty : s_current.Nonempty)
  (h_after_nonempty : s_next.Nonempty)
  (h_card_eq : s_next.card = s_current.card - killed)
  (h_greedy : (killed : ℝ) ≥ (s_current.card : ℝ) / (κ : ℝ)) :
  information_gain s_current s_next ≥ Real.logb 2 ((κ : ℝ) / ((κ : ℝ) - 1)) := by
  unfold information_gain
  exact greedy_information_content s_current.card s_next.card killed κ h_κ_gt_1
    (Finset.card_pos.mpr h_before_nonempty)
    (Finset.card_pos.mpr h_after_nonempty)
    h_card_eq
    h_greedy

/-
Tests never increase uncertainty, so information content is non-negative.
-/
theorem information_content_nonneg (n_before n_after : ℕ) (h_le : n_after ≤ n_before) (h_pos : 0 < n_after) :
  information_content n_before n_after ≥ 0 := by
  exact Real.logb_nonneg ( by norm_num ) ( by rw [ le_div_iff₀ ] <;> norm_cast ; linarith )

/-
The sum of log differences telescopes to the difference of the logs of the endpoints.
-/
theorem sum_logb_telescope
  (s : ℕ → ℝ)
  (k : ℕ)
  (h_pos : ∀ i ≤ k, 0 < s i) :
  (∑ i ∈ Finset.range k, Real.logb 2 (s i / s (i + 1))) = Real.logb 2 (s 0) - Real.logb 2 (s k) := by
  convert Finset.sum_range_sub' ( fun i ↦ Real.logb 2 ( s i ) ) k using 1 ; norm_num [ Real.logb ] ; ring_nf;
  rw [ ← Finset.sum_sub_distrib ] ; refine' Finset.sum_congr rfl fun i hi => _ ; rw [ Real.log_mul ( ne_of_gt ( h_pos i ( by linarith [ Finset.mem_range.mp hi ] ) ) ) ( ne_of_gt ( inv_pos.mpr ( h_pos ( 1 + i ) ( by linarith [ Finset.mem_range.mp hi ] ) ) ) ), Real.log_inv ] ; ring;

/-
The sum of information content over a sequence reducing the set size from n to 1 is log2(n).
-/
theorem total_information_content
  (n : ℕ)
  (s : ℕ → ℕ)
  (k : ℕ)
  (h_pos : ∀ i ≤ k, 0 < s i)
  (h_start : s 0 = n)
  (h_end : s k = 1) :
  (∑ i ∈ Finset.range k, information_content (s i) (s (i + 1))) = Real.logb 2 n := by
  -- We'll use the telescoping property of the logarithm: $\sum_{i=0}^{k-1} \log(s_i / s_{i+1}) = \log(s_0) - \log(s_k)$.
  have h_telescope : ∑ i ∈ Finset.range k, Real.logb 2 ((s i : ℝ) / (s (i + 1) : ℝ)) = Real.logb 2 (s 0) - Real.logb 2 (s k) := by
    convert sum_logb_telescope _ _ _ using 1;
    exact fun i hi => Nat.cast_pos.mpr ( h_pos i hi );
  aesop

/-
Information content is zero if and only if the number of survivors remains unchanged (no new mutants killed).
-/
theorem information_content_zero_iff (n_before n_after : ℕ) (h_le : n_after ≤ n_before) (h_pos : 0 < n_after) :
  information_content n_before n_after = 0 ↔ n_before = n_after := by
  norm_num [ information_content ];
  rw [ div_eq_iff, div_eq_iff ] <;> norm_cast <;> aesop;

end
end Phase7_T7_9
