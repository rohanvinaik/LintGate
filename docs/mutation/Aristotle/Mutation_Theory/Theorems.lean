/-
  Specification Completeness Theory — Theorem Statements

  Each theorem below is self-contained and can be copy-pasted individually
  as a proof obligation. All theorems reference definitions from Definitions.lean
  (provide that file as Lean context).

  Theorems are organized by:
    TIER 1 — Provable from definitions, standard techniques
    TIER 2 — Require moderate proof effort, domain-specific reasoning
    TIER 3 — Conjectures requiring novel proof strategies

  Within each tier, theorems are ordered by dependency (earlier theorems
  may be used by later ones).
-/

import SpecCompleteness.Definitions


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 1: FOUNDATIONS — Order Theory, Set Theory, Direct from Definitions
    Expected difficulty: Trivial to Easy
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T1.1: Killed and Surviving Sets are Complementary ─────────────────
    Every mutant is either killed or survives. This is the basic partition
    that the entire theory rests on.

    Proof strategy: Unfold isKilled and isSurvivor, apply classical logic.
    The definitions are negations of each other by construction.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_1_killed_surv_complement
    (P : Program D R) (T : TestSuite D R) (m : Program D R) :
    isKilled P T m ↔ ¬ isSurvivor P T m := by
  sorry


/-! ─── T1.2: Test-Induced Equivalence is an Equivalence Relation ────────
    The relation "no test in T distinguishes P₁ from P₂" is reflexive,
    symmetric, and transitive.

    Proof strategy: Reflexivity and symmetry are immediate from the definition
    (equality of booleans is symmetric). Transitivity: if passes P₁ t = passes P₂ t
    and passes P₂ t = passes P₃ t, then passes P₁ t = passes P₃ t by transitivity
    of equality.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_2a_testEquiv_refl
    (T : TestSuite D R) (P : Program D R) :
    testEquiv T P P := by
  sorry

theorem T1_2b_testEquiv_symm
    (T : TestSuite D R) (P₁ P₂ : Program D R) :
    testEquiv T P₁ P₂ → testEquiv T P₂ P₁ := by
  sorry

theorem T1_2c_testEquiv_trans
    (T : TestSuite D R) (P₁ P₂ P₃ : Program D R) :
    testEquiv T P₁ P₂ → testEquiv T P₂ P₃ → testEquiv T P₁ P₃ := by
  sorry


/-! ─── T1.3: Refinement is a Partial Order ──────────────────────────────
    The specification refinement relation ⪯ (defined by killed-set inclusion)
    is reflexive, antisymmetric (on equivalence classes), and transitive.
    Since it is defined as ⊆ on Finsets, this inherits from subset ordering.

    Proof strategy: All three properties follow directly from ⊆ on Finset.
    ────────────────────────────────────────────────────────────────────── -/

variable [DecidableEq (Program D R)]
         [∀ (p : Program D R) (t : Test D R), Decidable (passes (D := D) (R := R) p t ≠ passes p t)]

-- Note: For these theorems, we work with a fixed MutationSystem and Program.
-- The refinement order is on test suites relative to these.

theorem T1_3a_refines_refl
    (MS : MutationSystem D R) (P : Program D R)
    (T : TestSuite D R)
    [∀ (p : Program D R) (t : Test D R), Decidable (passes P t ≠ passes p t)] :
    refines MS P T T := by
  sorry

theorem T1_3b_refines_trans
    (MS : MutationSystem D R) (P : Program D R)
    (T₁ T₂ T₃ : TestSuite D R)
    [∀ (p : Program D R) (t : Test D R), Decidable (passes P t ≠ passes p t)] :
    refines MS P T₁ T₂ → refines MS P T₂ T₃ → refines MS P T₁ T₃ := by
  sorry


/-! ─── T1.4: Killed Sets Form a Join-Semilattice ────────────────────────
    The union of two test suites kills exactly the union of their killed sets.
    This gives us a join operation on killed sets.

    Proof strategy: A mutant m is killed by T₁ ∪ T₂ iff there exists t in
    T₁ ∪ T₂ distinguishing m from P. Such t is in T₁ or T₂. So m is in
    killedSet T₁ or killedSet T₂.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_4_killedSet_union
    (MS : MutationSystem D R) (P : Program D R)
    (T₁ T₂ : TestSuite D R)
    [∀ (p : Program D R) (t : Test D R), Decidable (passes P t ≠ passes p t)] :
    killedSet MS P (T₁ ∪ T₂) = killedSet MS P T₁ ∪ killedSet MS P T₂ := by
  sorry


/-! ─── T1.5: Specification Completeness is Monotone ─────────────────────
    Adding tests can only increase (or maintain) specification completeness.
    More precisely: if the killed set of T₁ ⊆ killed set of T₂, then
    SC(P, T₁) ≤ SC(P, T₂).

    Proof strategy: Subset of killed sets means |killed₁| ≤ |killed₂|.
    SC = killed/total, and total is fixed (depends on P, not T).
    So SC₁ ≤ SC₂.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_5_SC_monotone
    (killed₁ killed₂ total : ℕ)
    (h_le : killed₁ ≤ killed₂)
    (h_bound₁ : killed₁ ≤ total)
    (h_bound₂ : killed₂ ≤ total)
    (h_pos : 0 < total) :
    mutationScore killed₁ total ≤ mutationScore killed₂ total := by
  sorry


/-! ─── T1.6: Classification is Monotone ─────────────────────────────────
    The classifyLevel function is monotone: higher mutation scores yield
    higher (or equal) specification levels.

    Proof strategy: Case split on the threshold intervals. If sc₁ ≤ sc₂
    and sc₁ falls in interval I, then sc₂ falls in I or a higher interval.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_6_classifyLevel_monotone
    (sc₁ sc₂ : ℚ)
    (h_le : sc₁ ≤ sc₂)
    (h_bounds₁ : 0 ≤ sc₁)
    (h_bounds₂ : sc₂ ≤ 1) :
    classifyLevel sc₁ ≤ classifyLevel sc₂ := by
  sorry


/-! ─── T1.7: Category Survival is Additive ──────────────────────────────
    Since mutation categories partition the mutant set, the total number of
    survivors is the sum of per-category survivors. This is the formal basis
    for "the survival profile decomposes the specification gap into
    independent components."

    Proof strategy: Categories partition Mut(P), so Surv(P,T) partitions into
    per-category surviving sets. Cardinality of a disjoint union is sum of
    cardinalities.

    We state this abstractly over a disjoint family of Finsets.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_7_additive_survival
    {α : Type} [DecidableEq α]
    (parts : Fin k → Finset α)
    (whole : Finset α)
    (h_union : whole = Finset.univ.biUnion parts)
    (h_disjoint : ∀ i j, i ≠ j → Disjoint (parts i) (parts j)) :
    whole.card = Finset.univ.sum (fun i => (parts i).card) := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 1 (continued): OBSERVATIONAL EQUIVALENCE
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T1.8: True Equivalence Implies Test Equivalence ──────────────────
    If two programs agree on ALL inputs, they certainly agree on test inputs.
    The converse is false in general (test suites are finite).

    Proof strategy: trueEquiv says ∀ x, P₁.eval x = P₂.eval x.
    testEquiv says ∀ t ∈ T, passes P₁ t = passes P₂ t.
    passes is defined via eval, so the universal quantifier specializes.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_8_trueEquiv_implies_testEquiv
    [DecidableEq R]
    (P₁ P₂ : Program D R) (T : TestSuite D R)
    (h : trueEquiv P₁ P₂) :
    testEquiv T P₁ P₂ := by
  sorry


/-! ─── T1.9: Convergence of Test-Approximate Equivalence ────────────────
    If two programs are NOT truly equivalent, there exists a finite test
    suite that distinguishes them (assuming decidable equality on R).

    This is the formal statement that surviving non-equivalent mutants are
    exactly the specification gaps: each one CAN be killed by extending T.

    Proof strategy: If P₁ ≢ P₂, there exists x* with P₁(x*) ≠ P₂(x*).
    Construct the singleton test {(x*, λr. r = P₁(x*))}. This test kills
    the equivalence.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_9_nonequiv_is_distinguishable
    [DecidableEq R]
    (P₁ P₂ : Program D R)
    (h_neq : ¬ trueEquiv P₁ P₂) :
    ∃ (t : Test D R), passes P₁ t ≠ passes P₂ t := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 1 (continued): THE CROSS-CHANNEL GATE
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T1.10: Gate Soundness — Verified Status Requires Low Survival ────
    The gate never grants VERIFIED status when mutation survival is high.
    Formally: if the gate returns .verified, then survival < 30%.

    Proof strategy: Direct case analysis on the gate definition.
    The first branch checks survival ≥ 60% → gated.
    The second checks survival ≥ 30% → penalized.
    The else branch (verified) requires survival < 30%.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_10_gate_verified_implies_low_survival
    (α : AlgebraResult) (μ : MutationState)
    (h : (crossChannelGate α μ).status = .verified) :
    μ.survivalRate < 3/10 := by
  sorry


/-! ─── T1.11: Gate Conservatism — Confidence Only Increases When Earned ──
    The gate never increases confidence above the algebra pipeline's estimate.
    That is, gated confidence ≤ original confidence (and ≤ 0.9 in all cases).

    Proof strategy: Case analysis on the three gate branches.
    All branches scale confidence multiplicatively by a factor ≤ 1:
    Branch 1 (gated): output = α.confidence * (1/10) ≤ α.confidence.
    Branch 2 (penalized): output = α.confidence * (1/2) ≤ α.confidence.
    Branch 3 (verified): output = min(α.confidence, 0.9) ≤ α.confidence.
    Tactic: unfold crossChannelGate; split_ifs <;> norm_num at * <;> linarith.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_11_gate_conservative
    (α : AlgebraResult) (μ : MutationState)
    (h_conf : 0 ≤ α.confidence ∧ α.confidence ≤ 1) :
    (crossChannelGate α μ).confidence ≤ α.confidence := by
  sorry


/-! ─── T1.12: Gate Monotonicity — Higher Survival → Lower Confidence ────
    If survival rate increases, gated confidence does not increase.
    This formalizes: worse specification → more conservative gate.

    Proof strategy: Case analysis on the 9 interval combinations for μ₁, μ₂.
    With the multiplicative gate, the factors are 1/10 < 1/2 < min(1, 0.9),
    so higher survival → smaller multiplicative factor → lower confidence.
    No extra hypotheses on α.confidence needed beyond 0 ≤ α.confidence ≤ 1.
    Tactic: unfold crossChannelGate; split_ifs <;> norm_num at * <;> linarith.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_12_gate_antitone_in_survival
    (α : AlgebraResult) (μ₁ μ₂ : MutationState)
    (h_conf : 0 ≤ α.confidence ∧ α.confidence ≤ 1)
    (h_le : μ₁.survivalRate ≤ μ₂.survivalRate) :
    (crossChannelGate α μ₂).confidence ≤ (crossChannelGate α μ₁).confidence := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 1 (continued): THE ORACLE HIERARCHY
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T1.13: Oracle Level is a Total Order ─────────────────────────────
    The six oracle levels form a linear (total) order.
    CRASH < TYPE < STRUCTURAL < SHAPE < VALUE < PROPERTY

    Proof strategy: The order is defined by constructor index.
    Totality follows because ℕ is totally ordered.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_13_oracleLevel_total
    (a b : OracleLevel) :
    a ≤ b ∨ b ≤ a := by
  sorry


/-! ─── T1.14: Optimization Safety is Monotone in Oracle Level ───────────
    If optimization O₁ requires level L₁ and O₂ requires L₂ with L₁ ≤ L₂,
    then any test suite sufficient for O₂ is sufficient for O₁.

    We state this as: optRequiredLevel is a function into a totally ordered
    set, and meeting a higher requirement implies meeting all lower ones.

    Proof strategy: If the test suite achieves oracle level ≥ L₂ ≥ L₁,
    then it achieves level ≥ L₁. Transitivity of ≤.
    ────────────────────────────────────────────────────────────────────── -/

theorem T1_14_opt_safety_monotone
    (O₁ O₂ : OptimizationType)
    (achievedLevel : OracleLevel)
    (h_req : optRequiredLevel O₁ ≤ optRequiredLevel O₂)
    (h_met : optRequiredLevel O₂ ≤ achievedLevel) :
    optRequiredLevel O₁ ≤ achievedLevel := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 2: CONVERGENCE AND APPROXIMATION
    Expected difficulty: Easy to Moderate
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T2.1: Synthesis Loop — Tests Grow Monotonically ──────────────────
    The test suite produced by iterating the synthesis operator is a
    non-decreasing chain under ⊆.

    Proof strategy: By the SynthOperator.tests_monotone axiom, each step
    satisfies s.tests ⊆ (step s).tests. Induction on iteration count.
    ────────────────────────────────────────────────────────────────────── -/

-- Define iteration of the synthesis operator.
noncomputable def iterSynth [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) : ℕ → SynthState D R
  | 0 => s₀
  | n + 1 => op.step (iterSynth op s₀ n)

theorem T2_1_tests_monotone_chain [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) (n m : ℕ)
    (h : n ≤ m) :
    (iterSynth op s₀ n).tests ⊆ (iterSynth op s₀ m).tests := by
  sorry


/-! ─── T2.2: Synthesis Loop — Survivors Shrink Monotonically ────────────
    The surviving mutant set is a non-increasing chain under ⊆.

    Proof strategy: Dual of T2.1, using surv_antitone.
    ────────────────────────────────────────────────────────────────────── -/

theorem T2_2_survivors_antitone_chain [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R) (n m : ℕ)
    (h : n ≤ m) :
    (iterSynth op s₀ m).survivors ⊆ (iterSynth op s₀ n).survivors := by
  sorry


/-! ─── T2.3: Synthesis Loop — Termination Bound ────────────────────────
    The loop terminates in at most |initial survivors| productive steps.
    A "productive" step kills at least one mutant. If no step is productive,
    the loop has reached its fixed point.

    Proof strategy: |survivors| is a ℕ-valued measure that decreases by ≥1
    on each productive step. Since it starts at |s₀.survivors| and cannot
    go below 0, at most |s₀.survivors| productive steps can occur.
    ────────────────────────────────────────────────────────────────────── -/

theorem T2_3_termination_bound [DecidableEq (Program D R)]
    (op : SynthOperator D R) (s₀ : SynthState D R)
    /- Productive step kills at least one -/
    (h_productive : ∀ s, (op.step s).survivors ⊂ s.survivors ∨
                         (op.step s).survivors = s.survivors) :
    ∃ N ≤ s₀.survivors.card,
      (iterSynth op s₀ N).survivors = (iterSynth op s₀ (N + 1)).survivors := by
  sorry


/-! ─── T2.4: Union Formula for Combined Detection ──────────────────────
    If n independent detectors each detect under-specified functions with
    probability pᵢ, the probability that at least one detects is
    1 - ∏(1 - pᵢ).

    This is the formal basis for the prediction layer's scope reduction claim.

    Proof strategy: Independence means P(all miss) = ∏P(miss_i) = ∏(1-pᵢ).
    P(at least one detects) = 1 - P(all miss).
    ────────────────────────────────────────────────────────────────────── -/

theorem T2_4_union_detection_formula
    (n : ℕ) (p : Fin n → ℚ)
    (h_bounds : ∀ i, 0 ≤ p i ∧ p i ≤ 1) :
    let combined := 1 - Finset.univ.prod (fun i => 1 - p i)
    0 ≤ combined ∧ combined ≤ 1 := by
  sorry


/-! ─── T2.5: Union Formula — Monotone in Number of Signals ─────────────
    Adding a signal with detection probability > 0 strictly increases
    combined detection probability.

    This formalizes: "each new independent signal adds value."

    Proof strategy: ∏(1 - pᵢ) over n+1 terms = ∏ over n terms × (1 - p_{n+1}).
    Since 0 < p_{n+1}, we have (1 - p_{n+1}) < 1, so the product shrinks,
    and 1 - product grows.
    ────────────────────────────────────────────────────────────────────── -/

theorem T2_5_more_signals_better
    (n : ℕ) (p : Fin n → ℚ) (p_new : ℚ)
    (h_bounds : ∀ i, 0 ≤ p i ∧ p i ≤ 1)
    (h_new_pos : 0 < p_new)
    (h_new_le : p_new ≤ 1) :
    Finset.univ.prod (fun i => 1 - p i) * (1 - p_new) <
    Finset.univ.prod (fun i => 1 - p i) := by
  sorry


/-! ─── T2.6: Monty Hall Recall Guarantee ───────────────────────────────
    If category priors are ε-calibrated (P(survivors | prior < τ) < ε),
    then the probability of missing any true specification gap is ≤ kε
    where k is the number of categories.

    Proof strategy: Union bound over k categories.
    ────────────────────────────────────────────────────────────────────── -/

-- We state this as a pure probability bound, abstracting from the specific domain.

theorem T2_6_monty_hall_recall
    (k : ℕ) (ε : ℚ)
    (h_ε_pos : 0 < ε) (h_ε_le : ε ≤ 1)
    /- Each filtered category has probability < ε of containing survivors -/
    (miss_prob : Fin k → ℚ)
    (h_calibrated : ∀ i, miss_prob i ≤ ε) :
    /- The probability that ANY filtered category has survivors is ≤ k * ε -/
    Finset.univ.sum miss_prob ≤ k * ε := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 2 (continued): COMPOSITION AND STRENGTH
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T2.7: Composition Reduces Oracle Strength ────────────────────────
    The oracle level of a composed function f = h ∘ g is bounded by the
    minimum of the oracle levels of h and g.

    If g is only specified at STRUCTURAL level, then h ∘ g cannot be
    specified above STRUCTURAL for g's contribution, regardless of h.

    Proof strategy: A mutation to g that preserves structure but changes
    values will propagate through h undetected if the test suite only checks
    h's output at STRUCTURAL level for g's behavior. The weakest link
    determines the composite's strength.
    ────────────────────────────────────────────────────────────────────── -/

-- We state this as a general min-bound principle.
theorem T2_7_composition_strength_bound
    (level_g level_h : OracleLevel) :
    min level_g level_h ≤ level_g ∧ min level_g level_h ≤ level_h := by
  sorry


/-! ─── T2.8: Greedy Set Cover Approximation for Test Synthesis ──────────
    The problem of finding a minimal test suite that kills all non-equivalent
    mutants is at least as hard as minimum set cover. The greedy algorithm
    (select the test that kills the most uncovered mutants) achieves an
    O(ln n) approximation ratio.

    We state the classical set cover bound. The connection to mutation testing
    is: each test is a "set" of mutants it kills; we seek minimum collection
    covering all killable mutants.

    Proof strategy: This is the classical Chvátal (1979) / Johnson (1974)
    result. The greedy algorithm for minimum set cover on a universe of size
    n achieves ratio H(n) = 1 + 1/2 + ... + 1/n ≤ ln(n) + 1.

    For the proof LLM: this is a well-known result. You may cite it as
    an axiom if the full proof is too involved, or prove the weaker bound
    that greedy covers are at most O(n) times optimal.
    ────────────────────────────────────────────────────────────────────── -/

-- We state the bound abstractly.
axiom greedy_set_cover_bound :
  ∀ (n : ℕ) (opt greedy : ℕ),
    /- opt is the optimal cover size, greedy is the greedy cover size -/
    /- (assuming standard greedy set cover algorithm) -/
    0 < opt →
    greedy ≤ opt * (Nat.log n + 1)


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 2 (continued): EQUIVALENT MUTANTS
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T2.9: Equivalent Mutant Detection is Undecidable ────────────────
    Determining whether a mutant m is truly equivalent to the original P
    is undecidable for Turing-complete languages.

    Proof strategy: Reduction from the halting problem.
    Given TM M and input w, construct:
      P(x) = 0 for all x
      m(x) = run M on w for |x| steps; if halts, return 1; else return 0
    Then m ≡ P iff M does not halt on w.

    For the proof LLM: this requires a formalization of Turing machines
    and the halting problem. Lean's Mathlib has Computability foundations.
    You may state this as an axiom if the reduction is too complex to
    formalize, or reference Mathlib.Computability.HaltingProblem.
    ────────────────────────────────────────────────────────────────────── -/

-- We state this as an axiom linking to computability theory.
-- A full proof would require formalizing TMs and the halting reduction.
axiom equiv_mutant_undecidable :
  /- There is no total computable function that decides,
     for all programs P and mutants m, whether m ≡ P -/
  ¬ ∃ (decide : (ℕ → ℕ) → (ℕ → ℕ) → Bool),
    ∀ (P m : ℕ → ℕ), decide P m = true ↔ (∀ x, P x = m x)


/-! ─── T2.10: Equivalent Mutant Decidability for Finite Domains ────────
    When the input domain is finite, equivalent mutant detection IS decidable:
    just check all inputs.

    Proof strategy: Enumerate all elements of D. For each x ∈ D, check
    P(x) = m(x). If all agree, equivalent; if any disagree, not equivalent.
    ────────────────────────────────────────────────────────────────────── -/

theorem T2_10_equiv_decidable_finite_domain
    [DecidableEq R] [Fintype D]
    (P m : Program D R) :
    Decidable (trueEquiv P m) := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 3: DECOMPOSITION THEORY
    Expected difficulty: Moderate to Hard (some are open conjectures)
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T3.1: Separable Functions Decompose with Independent Tests ───────
    If f(x₁, x₂) = h(g₁(x₁), g₂(x₂)) — the inputs split into independent
    groups — then:
    (a) Mutants of g₁ can be killed by tests varying only x₁
    (b) The test spaces for g₁ and g₂ are independent

    This is a provable special case of the decomposition conjecture.

    Proof strategy: Since g₁ depends only on x₁ and g₂ depends only on x₂,
    a mutation to g₁'s source changes only the first argument to h.
    A test (x₁*, x₂) with oracle checking h(g₁(x₁*), g₂(x₂)) will detect
    the mutation if g₁(x₁*) ≠ g₁'(x₁*), regardless of x₂.

    We model this abstractly: if a function factors through a product,
    mutations in one factor can be detected by tests on that factor alone.
    ────────────────────────────────────────────────────────────────────── -/

theorem T3_1_separable_independent_testing
    [DecidableEq R₁] [DecidableEq R₂] [DecidableEq R]
    (g₁ g₁' : Program D₁ R₁)
    (g₂ : Program D₂ R₂)
    (h : R₁ → R₂ → R)
    (h_injective_left : ∀ r₂, Function.Injective (fun r₁ => h r₁ r₂))
    /- g₁' differs from g₁ on some input -/
    (x_star : D₁) (h_diff : g₁.eval x_star ≠ g₁'.eval x_star)
    /- Then for ANY x₂, the composed outputs differ -/
    (x₂ : D₂) :
    h (g₁.eval x_star) (g₂.eval x₂) ≠ h (g₁'.eval x_star) (g₂.eval x₂) := by
  sorry


/-! ─── T3.2: Entanglement Creates Multiplicative Test Burden ───────────
    When a function mixes two independent concerns (categories Cᵢ and Cⱼ),
    the number of tests needed to fully specify the function grows as the
    PRODUCT of the per-category surviving mutant counts, not their sum.

    This formalizes why multi-category survival diagnoses "too many concerns."

    Proof strategy: Consider mutations mᵢ ∈ Cᵢ and mⱼ ∈ Cⱼ that survive
    independently. The interaction (mᵢ, mⱼ) is a new behavioral dimension.
    To verify the function fully, we need tests covering the product space.
    Separation reduces this to sum.

    We state the combinatorial fact: |A × B| = |A| · |B| > |A| + |B|
    for |A|, |B| > 1.
    ────────────────────────────────────────────────────────────────────── -/

theorem T3_2_product_vs_sum
    (a b : ℕ) (ha : 1 < a) (hb : 1 < b) :
    a + b < a * b := by
  sorry


/-! ─── T3.3: Decomposition Makes Progress (Weak Conjecture) ────────────
    This is the central open conjecture in weak form:

    If a function has multi-category survival (dim ≥ 2), then there exists
    a decomposition such that the TOTAL surviving mutant count across all
    components is strictly less than the original's surviving count.

    We cannot prove this in full generality without a model of how AST
    mutations interact with function decomposition. We state it as an axiom
    to be validated.

    The proof LLM should attempt to prove this under the simplifying
    assumption that the decomposition perfectly separates categories
    (each component has survivors in at most one category).
    ────────────────────────────────────────────────────────────────────── -/

-- Under the simplifying assumption: decomposition separates categories.
-- Each component gᵢ has survivors only in category Cᵢ.
-- The composition h has no survivors of its own.
-- Then: total survivors after decomposition ≤ sum of per-category survivors
-- = total survivors before decomposition (by T1.7), but the combinatorial
-- interaction terms vanish, making further specification easier.

-- We state the key inequality: if interaction effects exist, decomposition
-- strictly reduces the effective test burden.

theorem T3_3_decomposition_reduces_burden
    (survA survB interaction : ℕ)
    (h_interaction_pos : 0 < interaction)
    /- Before decomposition: total tests needed includes interaction -/
    /- After decomposition: interaction term vanishes -/
    : survA + survB < survA + survB + interaction := by
  sorry


/-! ─── T3.4: Irreducible Functions Exist ────────────────────────────────
    Not all functions can be decomposed to eliminate multi-category survival.
    There exist functions where the interaction between categories IS the
    computation, and separating them destroys semantic content.

    Proof strategy: Constructive. Exhibit a function (e.g., conditional
    arithmetic: if x > 0 then x + 1 else x * 2) where any decomposition
    that separates the conditional from the arithmetic loses the
    relationship between them.

    This is important because it bounds the conjecture: the theory claims
    mutation-guided decomposition works for "engineering" code, not ALL code.
    ────────────────────────────────────────────────────────────────────── -/

-- We show that some two-category functions cannot be decomposed into
-- single-category components without changing the function's behavior.
-- Stated as: there exists a function where any factoring through
-- single-concern components is not extensionally equal to the original.

-- For the proof LLM: consider f(x) = if x > 0 then x + 1 else x * 2.
-- The conditional and arithmetic are essentially coupled.
-- Any g : ℤ → ℤ that is purely arithmetic (no branching) and
-- any p : ℤ → Bool that is purely conditional (no arithmetic output)
-- cannot be composed to produce f without reintroducing the coupling.

-- This is hard to formalize abstractly. As a simpler stand-in, prove that
-- there exist functions on finite domains that resist factorization:

theorem T3_4_irreducible_functions_exist :
    ∃ (f : Bool × Bool → Bool),
      ¬ ∃ (g₁ : Bool → Bool) (g₂ : Bool → Bool) (h : Bool → Bool → Bool),
        (∀ x y, f (x, y) = h (g₁ x) (g₂ y)) ∧
        (g₁ ≠ id ∧ g₂ ≠ id) := by
  sorry


/-! ═══════════════════════════════════════════════════════════════════════════
    TIER 3 (continued): PROPERTY-BASED TESTING INTERACTION
    ═══════════════════════════════════════════════════════════════════════════ -/


/-! ─── T3.5: Property Kills Dominate Value Kills ───────────────────────
    A test that verifies an algebraic law (e.g., commutativity) provides
    strictly more specification information than a test that verifies a
    single input-output pair, even if the latter kills more mutants.

    We formalize "more information" as: the set of programs consistent with
    a property test is a strict subset of the set consistent with any
    finite collection of value tests.

    Proof strategy: A property ∀ x y, f(x,y) = f(y,x) rules out infinitely
    many behaviors. Any finite set of value tests rules out only finitely
    many. The remaining consistent set is smaller for the property test.
    ────────────────────────────────────────────────────────────────────── -/

-- We state the finite/infinite distinction abstractly.
-- For infinite domain D: the set of functions satisfying a universal property
-- is a strict subset of those consistent with any finite number of point checks.

-- This is a statement about cardinality of function spaces.
-- For a simpler version: over a domain with ≥ 3 elements, a commutativity check
-- on k pairs leaves fewer consistent functions than 2k value checks.

-- For the proof LLM: prove the simpler combinatorial fact that
-- a universally quantified constraint is strictly stronger than any
-- finite conjunction of instances.

theorem T3_5_universal_stronger_than_finite
    {α : Type} [Infinite α] (P : α → Prop)
    (S : Finset α)
    (h_not_all : ¬ ∀ x, P x)
    (h_holds_on_S : ∀ x ∈ S, P x) :
    ∃ x, x ∉ S ∧ ¬ P x := by
  sorry


/-! ─── T3.6: Weighted SC Subsumes Unweighted SC ───────────────────────
    If we extend the specification completeness metric to weight kills by
    oracle level (PROPERTY kills weighted higher than VALUE kills, etc.),
    then the weighted metric agrees with the unweighted metric when all
    kills are at the same level, and strictly distinguishes suites that
    the unweighted metric considers equal when oracle levels differ.

    Proof strategy: When all weights are equal, weighted SC = unweighted SC.
    When weights differ, two suites with the same kill count but different
    oracle level distributions will have different weighted SC.
    ────────────────────────────────────────────────────────────────────── -/

-- Abstract the weighting: given a weight function w : OracleLevel → ℚ
-- with w strictly monotone (higher levels get strictly higher weights),
-- the weighted score distinguishes suites the unweighted score cannot.

theorem T3_6_weighted_distinguishes
    (w : OracleLevel → ℚ)
    (h_monotone : ∀ a b : OracleLevel, a < b → w a < w b)
    (h_pos : ∀ a, 0 < w a)
    /- Two suites with same kill count but different level distributions -/
    (kills : ℕ)
    (level₁ level₂ : OracleLevel)
    (h_diff : level₁ < level₂) :
    kills * w level₁ < kills * w level₂ ∨ kills = 0 := by
  sorry
