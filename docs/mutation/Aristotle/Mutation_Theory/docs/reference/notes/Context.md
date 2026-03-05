# Specification Completeness Theory — Domain Context and Proof Hints

## What This Theory Is About

This is a formalization of the theory that **mutation testing measures specification completeness** — how fully a test suite specifies a program's behavior — and that specification completeness is a prerequisite for safe program optimization.

### The Core Idea

A mutation is a small syntactic change to a program (e.g., changing `+` to `-`, or `>` to `>=`). A mutant is the resulting modified program. If no test in the test suite can distinguish the mutant from the original, the mutant "survives." A surviving mutant means: there's a behavioral degree of freedom that the tests don't pin down.

**Mutation score** = (killed mutants) / (total mutants). We reframe this as **specification completeness**: it measures how fully the test suite specifies the program's behavior.

### Key Structures

1. **Programs** are functions from input domain D to output range R.
2. **Test suites** are finite sets of (input, oracle) pairs.
3. **Mutation operators** produce syntactically modified programs.
4. **Mutation categories** group operators by type: arithmetic, conditional, string, etc.
5. **The refinement order**: test suite T₂ refines T₁ if it kills every mutant T₁ kills (and possibly more).
6. **The cross-channel gate**: a function that combines an algebra pipeline's property classification with mutation data to gate optimizations.
7. **Oracle levels**: a hierarchy of assertion strength from CRASH (weakest) to PROPERTY (strongest).
8. **The synthesis loop**: iteratively generate tests guided by surviving mutants.

---

## Proof Hints by Theorem

### T1.1: Killed/Surviving Complement
`isKilled` says ∃ t, passes P t ≠ passes m t. `isSurvivor` says ∀ t, passes P t = passes m t. These are negations of each other. Use `push_neg` to convert between them.

### T1.2: Test Equivalence is Equivalence
`testEquiv T P₁ P₂` says ∀ t ∈ T, passes P₁ t = passes P₂ t. Reflexivity: passes P t = passes P t (eq.refl). Symmetry: swap sides of the equality. Transitivity: chain the equalities via P₂.

### T1.3: Refinement Partial Order
`refines` is defined as `killedSet T₁ ⊆ killedSet T₂`. This inherits reflexivity (A ⊆ A), transitivity (A ⊆ B → B ⊆ C → A ⊆ C) from Finset.Subset.

### T1.4: Killed Set Union
The key insight: `killedSet MS P (T₁ ∪ T₂)` filters mutants m where ∃ t ∈ T₁ ∪ T₂ with passes P t ≠ passes m t. Since t ∈ T₁ ∪ T₂ iff t ∈ T₁ or t ∈ T₂, this is equivalent to m being in killedSet T₁ or killedSet T₂. Use `Finset.mem_union` and `Finset.filter_union` or `ext` + `simp`.

### T1.5: SC Monotone
`mutationScore killed total = killed / total` when total > 0. If killed₁ ≤ killed₂ and total is the same, then killed₁/total ≤ killed₂/total. Use `Rat.div_le_div_right` or equivalent.

### T1.6: classifyLevel Monotone
Case split on which interval sc₁ falls into. Since sc₁ ≤ sc₂, sc₂ is in the same or a higher interval. The SpecLevel order is by constructor index. Work through the if-then-else branches.

### T1.7: Additive Survival
This is the standard result that cardinality of a disjoint union equals the sum of cardinalities. Use `Finset.card_biUnion` with the disjointness hypothesis.

### T1.8: True Equiv → Test Equiv
`trueEquiv P₁ P₂` gives ∀ x, P₁.eval x = P₂.eval x. `passes P t = t.oracle.judge (P.eval t.input)`. Since P₁.eval t.input = P₂.eval t.input, the oracle gets the same input, so passes gives the same result. Use `congr_arg`.

### T1.9: Non-Equiv is Distinguishable
`¬ trueEquiv P₁ P₂` means ∃ x*, P₁.eval x* ≠ P₂.eval x*. Construct a test with input = x* and oracle = λ r, r = P₁.eval x*. Then passes P₁ t = true and passes P₂ t = false. Use `use` to exhibit the test.

### T1.10: Gate Verified → Low Survival
The gate definition is an if-then-else chain. `status = .verified` only in the else branch, which requires survival < 3/10 (since both earlier branches check survival ≥ thresholds). Use `simp [crossChannelGate]` and case split on the survival thresholds.

### T1.11: Gate Conservative
Three cases:
- Gated: confidence = 1/10 ≤ α.confidence (since 0 ≤ α.confidence by hypothesis)
- Penalized: confidence = α.confidence * 1/2 ≤ α.confidence (multiply by factor ≤ 1)
- Verified: confidence = min(α.confidence, 9/10) ≤ α.confidence (min is ≤ both arguments)

### T1.12: Gate Antitone in Survival
If μ₁.survival ≤ μ₂.survival, then μ₂ is in the same or a worse gate band. Work through the 9 combinations of (band₁, band₂) where band₁ ≤ band₂. In each case, the output confidence for μ₂ is ≤ the output for μ₁.

### T1.13: Oracle Level Total Order
OracleLevel is an inductive type with 6 constructors. The LE instance uses `toCtorIdx` which maps to {0,1,2,3,4,5}. Totality follows from totality of ℕ. Use `cases a <;> cases b <;> simp [LE.le, ...]` or `omega`.

### T1.14: Optimization Safety Monotone
This is just transitivity of ≤. If optRequiredLevel O₁ ≤ optRequiredLevel O₂ ≤ achievedLevel, then optRequiredLevel O₁ ≤ achievedLevel. Use `le_trans`.

### T2.1-T2.2: Synthesis Monotonicity
Induction on the difference m - n. Base case: n = m, trivial (⊆ refl). Step: use the SynthOperator's monotonicity axiom plus the inductive hypothesis and transitivity of ⊆.

### T2.3: Termination Bound
The survivors form a descending chain of Finsets (by T2.2). The chain stabilizes in at most |s₀.survivors| steps because Finset.card is a ℕ-valued measure that decreases by ≥1 on each strict step. Use well-founded induction on Finset.card.

### T2.4-T2.5: Detection Formula
Standard probability theory. For T2.5, show that multiplying by (1 - p_new) with 0 < p_new makes the product strictly smaller. Use `mul_lt_of_lt_one_right` or similar.

### T2.6: Monty Hall Recall
Sum of k terms each ≤ ε is ≤ k * ε. Use `Finset.sum_le_card_nsmul` or similar.

### T2.9: Equivalent Mutant Undecidable
This requires a reduction from the halting problem. The key construction: given a Turing machine M and input w, construct P(x) = 0 always, and m(x) = 1 if M halts on w within |x| steps, 0 otherwise. Then m ≡ P iff M doesn't halt on w. If we could decide m ≡ P, we could decide the halting problem.

This is stated as an axiom because the full formalization requires Turing machine infrastructure. The proof LLM may accept this as given or attempt the reduction using Mathlib.Computability.

### T2.10: Equiv Decidable for Finite Domains
With `[Fintype D]` and `[DecidableEq R]`, we can enumerate all x ∈ D and check P.eval x = m.eval x. Use `Fintype.decidableForallFintype`.

### T3.1: Separable Independent Testing
The hypothesis `h_injective_left` says h is injective in its first argument (holding the second fixed). Since g₁(x*) ≠ g₁'(x*) and h(·, g₂(x₂)) is injective, h(g₁(x*), g₂(x₂)) ≠ h(g₁'(x*), g₂(x₂)). Use `Function.Injective.ne`.

### T3.2: Product vs Sum
For a, b > 1: a*b = a*b, and a+b < a*b when a,b > 1. Proof: a*b - (a+b) = (a-1)*(b-1) - 1 ≥ 0 when a,b ≥ 2. Actually: a*b - a - b = a*(b-1) - b = (a-1)*(b-1) - 1. For a,b ≥ 2: (a-1)*(b-1) ≥ 1, so a*b ≥ a + b + 1 > a + b. Use `nlinarith` or `omega`.

### T3.3: Decomposition Reduces Burden
This is simply n < n + k for k > 0. Use `Nat.lt_add_of_pos_right`.

### T3.4: Irreducible Functions Exist
Exhibit XOR: f(x, y) = x ≠ y (or equivalently, XOR). Show that no factorization f(x,y) = h(g₁(x), g₂(y)) works when g₁, g₂ are both non-identity. XOR is a well-known example of a function that doesn't factor through independent components.

Actually, this needs care. XOR DOES factor: h(a,b) = a ≠ b, g₁ = id, g₂ = id. The constraint is g₁ ≠ id AND g₂ ≠ id. With Bool → Bool, the non-identity functions are `not` and the constant functions. Check all cases.

### T3.5: Universal Stronger than Finite
Since α is infinite and S is finite, there exists x ∉ S (Infinite means there are infinitely many elements, so Finset cannot contain all of them). Since ¬ ∀ x, P x, there exists some x₀ with ¬ P x₀. If x₀ ∉ S, we're done. If x₀ ∈ S, that contradicts h_holds_on_S... wait, no. We need ∃ x ∉ S ∧ ¬ P x. This requires that the counterexample to P is outside S. This isn't guaranteed by the hypotheses as stated — we might need to strengthen the hypothesis.

Better approach: since ¬ ∀ x, P x and S is finite while α is infinite, there are infinitely many elements not in S. Unless P holds for all of them, we're done. If P holds for all elements outside S, then ∀ x, P x (since P holds on S by hypothesis and outside S by this assumption), contradiction. Use `Set.Infinite.exists_not_injOn` or the pigeonhole principle.

### T3.6: Weighted Distinguishes
If kills > 0 and level₁ < level₂, then w(level₁) < w(level₂) by h_monotone. So kills * w(level₁) < kills * w(level₂) by `Nat.mul_lt_mul_left` (or the rational version). The kills = 0 case is the escape hatch.

---

## Connection to the Broader Theory

These theorems formalize a theory document about software engineering tools. The key practical claims are:

1. **Mutation score reframes as specification completeness** (T1.1-T1.7)
2. **The refinement order is well-behaved** (T1.3-T1.5): adding tests improves specification monotonically
3. **The cross-channel gate is sound and conservative** (T1.10-T1.12): it never approves unsafe optimizations
4. **The synthesis loop converges** (T2.1-T2.3): iterative test generation terminates
5. **The prediction layer's efficiency is justified** (T2.4-T2.6): independent signals combine multiplicatively
6. **Decomposition helps** (T3.1-T3.3): separating concerns reduces the specification burden
7. **But not everything decomposes** (T3.4): irreducible functions exist, bounding the theory
8. **Property tests are strictly stronger than value tests** (T3.5-T3.6): the oracle hierarchy matters

The theorems are ordered so that earlier results can be used as lemmas for later ones. The Tier 1 theorems are all provable from definitions using standard Lean/Mathlib tactics. The Tier 2 theorems require slightly more work (induction, probability). The Tier 3 theorems involve open mathematical questions where partial results are still valuable.
