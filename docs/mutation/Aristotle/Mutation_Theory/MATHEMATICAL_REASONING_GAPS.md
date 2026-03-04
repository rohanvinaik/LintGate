# Mathematical Reasoning Gaps (Non-Trivial Only)

## Scope

This document now lists **only non-trivial mathematical/proof-engineering gaps** that remain in `MutationTheory/Full_Corpus_Compile.lean`.

- Mechanical/simple items were cleaned (including the `T1_6_classifyLevel_mono` placeholder and the T2.9 type-annotation regression).
- What remains are deeper proof obligations where the missing work is conceptual (decomposition logic, finite-product/sum identities, and convergence/cardinality chaining), not mere formatting/spec cleanup.

---

## Gap A — `T4_3_lemma6_killedSet_decomposition_under_correctness`

### Full current theorem text (as in file)

```lean
lemma T4_3_lemma6_killedSet_decomposition_under_correctness
    (sem : Program (D₁ × D₂) (R₁ × R₂) → (D₁ × D₂) → (R₁ × R₂))
    (sem₁ : Program D₁ R₁ → D₁ → R₁)
    (sem₂ : Program D₂ R₂ → D₂ → R₂)
    (oracle₁ : Test D₁ R₁ → R₁ → Bool)
    (oracle₂ : Test D₂ R₂ → R₂ → Bool)
    (MS : MutationSystem (D₁ × D₂) (R₁ × R₂))
    (MS₁ : MutationSystem D₁ R₁)
    (MS₂ : MutationSystem D₂ R₂)
    (P : Program (D₁ × D₂) (R₁ × R₂))
    (P₁ : Program D₁ R₁)
    (P₂ : Program D₂ R₂)
    (T : TestSuite (D₁ × D₂) (R₁ × R₂))
    (h_sem_P : ∀ x y, sem P (x, y) = (sem₁ P₁ x, sem₂ P₂ y))
    (h_mutants_decomp : MS.mutants P = (MS₁.mutants P₁).image inj1 ∪ (MS₂.mutants P₂).image inj2)
    (h_sem_inj1 : ∀ m₁ x y, sem (inj1 m₁) (x, y) = (sem₁ m₁ x, sem₂ P₂ y))
    (h_sem_inj2 : ∀ m₂ x y, sem (inj2 m₂) (x, y) = (sem₁ P₁ x, sem₂ m₂ y))
    (h_mutants_inj1 : ∀ m₁, m₁ ∈ MS₁.mutants P₁ → inj1 m₁ ∈ MS.mutants P)
    (h_mutants_inj2 : ∀ m₂, m₂ ∈ MS₂.mutants P₂ → inj2 m₂ ∈ MS.mutants P)
    (h_P1_correct : ∀ t ∈ T, passes sem₁ oracle₁ P₁ (test_project1 t) = true)
    (h_P2_correct : ∀ t ∈ T, passes sem₂ oracle₂ P₂ (test_project2 t) = true) :
    killedSet sem (oracle_product oracle₁ oracle₂) MS P T =
    (killedSet sem₁ oracle₁ MS₁ P₁ (project T).1).image inj1 ∪
    (killedSet sem₂ oracle₂ MS₂ P₂ (project T).2).image inj2 := by
  refine' Finset.Subset.antisymm _ _;
  · exact?;
  · exact Finset.union_subset ( T4_3_lemma4_killedSet_decomposition_superset_left sem sem₁ sem₂ oracle₁ oracle₂ MS MS₁ P P₁ P₂ T h_sem_P h_sem_inj1 h_mutants_inj1 h_P2_correct ) ( T4_3_lemma5_killedSet_decomposition_superset_right sem sem₁ sem₂ oracle₁ oracle₂ MS MS₂ P P₁ P₂ T h_sem_P h_sem_inj2 h_mutants_inj2 h_P1_correct )
```

### Current progress
- Equality is reduced to two inclusions.
- Reverse inclusion is explicit and complete.
- Forward inclusion is still an opaque `exact?`.

### Conceptual context
- This is the key decomposition bridge under correctness assumptions.
- It depends on semantics alignment (`h_sem_*`) and mutant-set decomposition (`h_mutants_*`).

### Exact gap
- Replace the forward inclusion `exact?` with explicit witness-level reasoning: each product killed mutant must map to left or right projected killed set based on `h_mutants_decomp` and distinguishing-test projection.

---

## Gap B — `T4_6_lemmaC_pinsker_cauchy_schwarz`

### Full current theorem text (as in file)

```lean
lemma T4_6_lemmaC_pinsker_cauchy_schwarz {α : Type} [Fintype α] (P Q : MutDistribution α) (hP : is_distribution P) (hQ : is_distribution Q) (hQ_pos : ∀ x, 0 < Q x) :
  (∑ x, |P x - Q x|)^2 ≤ 3 * ∑ x, (P x - Q x)^2 / (P x + 2 * Q x) := by
  have := hP.1;
  -- By Cauchy-Schwarz inequality, we have that for any vectors $v$ and $w$ of equal length, $(∑ i, v i * w i)^2 ≤ (∑ i, v i^2) * (∑ i, w i^2)$.
  have h_cauchy_schwarz : ∀ (v w : α → ℝ), (∑ i, v i * w i)^2 ≤ (∑ i, v i^2) * (∑ i, w i^2) := by
    exact?;
  convert h_cauchy_schwarz ( fun i => |P i - Q i| / Real.sqrt ( P i + 2 * Q i ) ) ( fun i => Real.sqrt ( P i + 2 * Q i ) ) using 1 <;> norm_num [ Real.sq_sqrt ( add_nonneg ( this _ ) ( mul_nonneg zero_le_two ( le_of_lt ( hQ_pos _ ) ) ) ) ];
  · exact congr_arg ( · ^ 2 ) ( Finset.sum_congr rfl fun _ _ => by rw [ div_mul_cancel₀ _ ( ne_of_gt ( Real.sqrt_pos.mpr ( add_pos_of_nonneg_of_pos ( this _ ) ( mul_pos zero_lt_two ( hQ_pos _ ) ) ) ) ) ] );
  · norm_num [ div_pow, Real.sq_sqrt ( add_nonneg ( this _ ) ( mul_nonneg zero_le_two ( le_of_lt ( hQ_pos _ ) ) ) ) ];
    norm_num [ mul_comm, Finset.sum_add_distrib, ← Finset.mul_sum _ _ _, ← Finset.sum_mul, hP.2, hQ.2 ]
```

### Current progress
- Correct weighted-variable setup is in place.
- Positivity side conditions for square roots are handled.
- The core finite Cauchy-Schwarz inequality is still opaque (`exact?`).

### Conceptual context
- This is the geometric heart of the Pinsker chain in this file.
- It connects the L1 term to the weighted quadratic term used by the KL bound.

### Exact gap
- Provide an explicit finite-sum Cauchy-Schwarz theorem invocation/proof term (Mathlib-level), replacing `exact?`.

---

## Gap C — `T4_6_cross_channel_independence`

### Full current theorem text (as in file)

```lean
theorem T4_6_cross_channel_independence {n : ℕ} (P : MutDistribution (Fin n → Bool))
    (hP : is_distribution P) (hP_pos : ∀ x, 0 < P x) :
    combined_detection P ≥ independent_detection P - Real.sqrt (2 * mutual_information P) := by
  have h_combined : |∑ x ∈ Finset.univ.filter (fun x => x = fun _ => false), P x - ∑ x ∈ Finset.univ.filter (fun x => x = fun _ => false), ∏ i, marginal P i (x i)| ≤ (1 / 2) * ∑ x, |P x - ∏ i, marginal P i (x i)| := by
    convert T4_6_lemmaE_prob_diff_le_half_l1 P ( product_of_marginals P ) hP ?_ ?_ using 1;
    refine' ⟨ _, _ ⟩;
    · exact fun x => Finset.prod_nonneg fun i _ => Finset.sum_nonneg fun _ _ => le_of_lt ( hP_pos _ );
    · -- By definition of product_of_marginals, we can expand the sum.
      have h_expand : ∑ x : Fin n → Bool, ∏ i : Fin n, marginal P i (x i) = ∏ i : Fin n, ∑ x : Bool, marginal P i x := by
        exact?;
      convert h_expand using 1;
      rw [ Finset.prod_eq_one ] ; intros ; simp +decide [ marginal ] ; ring;
      rw [ ← hP.2, ← Finset.sum_union ] ; congr ; ext ; by_cases h : ( ‹Fin n› : Fin n → Bool ) ‹_› = Bool.true <;> aesop;
      exact Finset.disjoint_filter.mpr ( by aesop );
  have h_combined : ∑ x, |P x - ∏ i, marginal P i (x i)| ≤ Real.sqrt (2 * kl_divergence P (fun x => ∏ i, marginal P i (x i))) := by
    apply_rules [ T4_6_lemmaD_pinsker_inequality ];
    · constructor <;> norm_num [ Finset.prod_eq_zero_iff, hP_pos ];
      · exact fun x => Finset.prod_nonneg fun i _ => Finset.sum_nonneg fun _ _ => le_of_lt ( hP_pos _ );
      · -- By definition of marginal, we know that $\sum_{x} \prod_{i} \text{marginal}(P_i)(x_i) = 1$.
        have h_marginal_sum : ∀ i, ∑ x : Bool, marginal P i x = 1 := by
          intro i
          have h_marginal_sum : ∑ x : Bool, marginal P i x = ∑ x : Fin n → Bool, P x := by
            unfold marginal; simp +decide [ Finset.sum_filter ] ;
            simpa only [ ← Finset.sum_add_distrib ] using Finset.sum_congr rfl fun x _ => by cases x i <;> simp +decide [ * ] ;
          rw [h_marginal_sum]
          exact hP.2;
        have h_prod_sum : ∑ x : Fin n → Bool, ∏ i, marginal P i (x i) = ∏ i, ∑ x : Bool, marginal P i x := by
          exact?;
        aesop;
    · intro x; exact Finset.prod_pos fun i _ => lt_of_lt_of_le ( hP_pos ( fun j => if j = i then x i else if x j then Bool.true else Bool.false ) ) ( Finset.single_le_sum ( fun a _ => le_of_lt ( hP_pos a ) ) ( by aesop ) ) ;
  unfold combined_detection independent_detection mutual_information;
  unfold product_of_marginals; norm_num [ Finset.sum_filter ] at *; linarith! [ abs_le.mp ‹_› ] ;
```

### Current progress
- The high-level reduction strategy is strong and mostly explicit.
- Two critical normalization/factorization identities remain opaque (`h_expand`, `h_prod_sum`).

### Conceptual context
- This theorem is the practical “cross-channel dependence correction” result.
- It links event-difference bounds (Lemma E), Pinsker bound (Lemma D), and product-of-marginals normalization.

### Exact gap
- Supply explicit finite product/sum factorization over `Fin n → Bool` (twice), replacing both `exact?` terms.

---

## Gap D — `T4_7_lemmaH_geometric_decay`

### Full current theorem text (as in file)

```lean
lemma T4_7_lemmaH_geometric_decay
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T_univ : TestSuite D R) (seq : ℕ → Test D R)
    (h_greedy : IsGreedySequence sem oracle MS P T_univ seq) (k : ℕ)
    (n : ℕ) (h_n : n = (distinctKillSets sem oracle MS P T_univ).card) (h_n_pos : 0 < n) :
    ((total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq (k + 1)).card : ℚ) ≤
    ((total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq k).card : ℚ) * (1 - 1 / n) := by
  -- By definition of $covered$, we have $covered sem oracle MS P seq (k + 1) = covered sem oracle MS P seq k ∪ killedSet sem oracle MS P {seq k}$.
  have h_covered : covered sem oracle MS P seq (k + 1) = covered sem oracle MS P seq k ∪ killedSet sem oracle MS P {seq k} := by
    unfold covered; ext; simp +decide [ Finset.range_add_one ] ;
    grind +ring;
  -- By definition of $SC$, we have $SC k = \frac{|covered_k|}{|total_killable|}$.
  have h_SC : (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq (k + 1)) = (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq k) \ (killedSet sem oracle MS P {seq k} \ covered sem oracle MS P seq k) := by
    grind;
  -- By Lemma G, the gain at step k is ≥ |uncovered_k|/n.
  have h_gain : ((killedSet sem oracle MS P {seq k} \ covered sem oracle MS P seq k).card : ℚ) ≥ ((total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq k).card : ℚ) / n := by
    exact?;
  rw [ h_SC, Finset.card_sdiff ];
  rw [ mul_sub, mul_one, Nat.cast_sub ];
  · gcongr;
    convert h_gain.le using 1 <;> norm_num [ mul_div ];
    · exact?;
    · congr! 1;
      ext; simp [killedSet, covered, total_killable];
      exact fun h₁ h₂ h₃ => ⟨ ⟨ h₁, seq k, h_greedy k |>.1, h₂ ⟩, h₃ ⟩;
  · exact Finset.card_le_card fun x hx => by aesop;
```

### Current progress
- Set-theoretic decomposition (`h_covered`, `h_SC`) is explicit.
- The key gain lower bound and one conversion side-goal are still opaque (`exact?`).

### Conceptual context
- This is the quantitative engine for greedy convergence.
- It must convert local greedy gain into global geometric decay of uncovered mutants.

### Exact gap
- Replace the two `exact?` obligations with explicit:
  1. a direct invocation/instantiation of `T4_7_lemmaG_pigeonhole_gain`, and
  2. an explicit cast/algebraic side-proof in the `convert` block.

---

## Gap E — `T4_7_greedy_synthesis_convergence` (nontrivial inequality-shape mismatch)

### Full current theorem text (as in file)

```lean
theorem T4_7_greedy_synthesis_convergence
    (sem : Program D R → D → R) (oracle : Test D R → R → Bool) (MS : MutationSystem D R) (P : Program D R) (T_univ : TestSuite D R) (seq : ℕ → Test D R)
    (h_greedy : IsGreedySequence sem oracle MS P T_univ seq)
    (n : ℕ) (h_n : n = (distinctKillSets sem oracle MS P T_univ).card) (h_n_pos : 0 < n) :
    ∀ k, ((covered sem oracle MS P seq k).card : ℝ) ≥ (total_killable sem oracle MS P T_univ).card * (1 - (1 - 1 / n) ^ k) := by
  -- Apply Lemma H: |uncovered_{k+1}| ≤ |uncovered_k| * (1-1/n).
  have h_uncovered : ∀ k, (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq (k + 1)).card ≤ (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq k).card * (1 - 1 / n : ℝ) := by
    intro k
    have h_uncovered : (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq (k + 1)).card ≤ (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq k).card * (1 - 1 / n : ℝ) := by
      have := T4_7_lemmaH_geometric_decay sem oracle MS P T_univ seq h_greedy k n h_n h_n_pos
      convert this using 1 ; norm_num [ Rat.divInt_eq_div ] ; ring;
      field_simp;
      norm_cast
    exact h_uncovered;
  have h_uncovered_induction : ∀ k, (total_killable sem oracle MS P T_univ \ covered sem oracle MS P seq k).card ≤ (total_killable sem oracle MS P T_univ).card * (1 - 1 / (n : ℝ)) ^ k := by
    intro k
    induction' k with k ih;
    · norm_num [ covered ];
    · simpa only [ pow_succ, mul_assoc ] using le_trans ( h_uncovered k ) ( mul_le_mul_of_nonneg_right ih ( sub_nonneg.2 <| div_le_self zero_le_one <| mod_cast h_n_pos ) );
  intro k; specialize h_uncovered_induction k; simp_all +decide [ mul_sub, sub_mul ] ;
  refine le_trans ?_ ( add_le_add_left h_uncovered_induction _ );
  norm_cast; rw [ ← Finset.card_union_of_disjoint ( Finset.disjoint_sdiff ) ] ; exact Finset.card_le_card fun x hx => by by_cases hx' : x ∈ covered sem oracle MS P seq k <;> aesop;
```

### Current progress
- Induction scaffold for uncovered-set geometric decay is explicit.
- Transfer from `T4_7_lemmaH_geometric_decay` is explicit.
- Final combination step has an inequality-shape mismatch (`le_trans` + `add_le_add_left` composition).

### Conceptual context
- This theorem is the final convergence-rate statement used by the capstone theorem.
- It combines set-cardinality decomposition with real-valued geometric decay bounds.

### Exact gap
- Rework the final inequality assembly so both sides are in the same algebraic normal form before `le_trans`; this is a structural proof-architecture fix, not a syntax/spec tweak.

---

## Summary

- Remaining opaque closures (`exact?`): **6**
- Additional nontrivial blocker without `exact?`: **1** (`T4_7_greedy_synthesis_convergence` inequality-shape mismatch)
- Remaining gaps are all non-trivial and concentrated in:
  - decomposition transfer (`T4_3`),
  - Pinsker/cross-channel normalization (`T4_6`),
  - greedy geometric convergence (`T4_7`).

These are the parts best suited for manual theorem-hardening by a human prover author (or a deliberate deep refactor pass), rather than quick mechanical cleanup.
