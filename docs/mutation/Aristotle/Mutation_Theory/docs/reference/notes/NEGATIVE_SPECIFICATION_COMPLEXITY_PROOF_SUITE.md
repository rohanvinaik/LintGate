# Negative Specification Complexity - Integrated Separable Proof Blocks

## Specific integration instructions (outside theorem blocks)

1. Canonical module placement:
   - `MutationTheory/Phase6/NegativeSpecificationComplexity.lean`
   - namespace: `MutationTheory.Phase6Refined`

2. Canonical import wiring:
   - `MutationTheory/Phase6.lean` imports `MutationTheory.Phase6.NegativeSpecificationComplexity`

3. Canonical theorem naming:
   - all items use `NSC_XX_*` names only
   - no Aristotle-prefixed names in the integrated architecture

4. Layer placement contract:
   - Core: `NSC_01` to `NSC_03`
   - Identification: `NSC_04`
   - Dynamics: `NSC_05` to `NSC_10`
   - Decomposition: `NSC_11` to `NSC_12`
   - Asymmetry: `NSC_13` to `NSC_16`
   - Integration synthesis: `NSC_17`
   - Open conjecture: `NSC_18`

5. Shared environment contract:
   - Core uses `Phase6.Program`, `Phase6.Test`, `Phase6.TestSuite`, `Phase6.MutationSystem`
   - Core reuses `Phase6.passes`, `Phase6.killedSet`, `Phase6.nonEquivMutants`, `Phase6.achievesFullSC'`, `Phase6.specComplexity`
   - Non-core layers are integrated under one namespace, but some still use layer-local abstractions where canonical bridges are missing

---

## Theorem block NSC_01

```
THEOREM NAME
NSC_01_elimination_characterization

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
For Phase6 core types, prove:
Q in eliminationSet sem oracle MS P T <-> Q in killedSet sem oracle MS P T

BEST CURRENT PROOF FORM
Direct unfolding + simp over filter membership and inequality predicate.

EXPLICIT GAPS
- None.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_02

```
THEOREM NAME
NSC_02_negative_spec_complexity_well_defined

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
Given hAch : exists T, achievesFullSC' sem oracle MS P T, prove:
1) Feas sem oracle MS P is nonempty
2) Feas sem oracle MS P is bounded below in Nat

BEST CURRENT PROOF FORM
- Extract witness T from hAch
- Convert achievesFullSC' to eliminationSet form using eliminationSet_eq_killedSet
- Construct nonempty witness k = T.card
- Use lower bound 0 for Nat

EXPLICIT GAPS
- None.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_03

```
THEOREM NAME
NSC_03_negative_spec_complexity_eq_spec_complexity

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
Given hAch : exists T, achievesFullSC' sem oracle MS P T, prove:
negativeSpecComplexity sem oracle MS P = specComplexity sem oracle MS P

BEST CURRENT PROOF FORM
- Prove equality of feasible domains by extensional set equality
- Rewrite eliminationSet to killedSet via eliminationSet_eq_killedSet
- Conclude equality by simp on sInf over equal sets

EXPLICIT GAPS
- None.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_04

```
THEOREM NAME
NSC_04_full_elimination_iff_unique_identification

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
For finite H, prove:
FullyEliminated sem oracle H P T <-> UniquelyIdentified sem oracle H P T

BEST CURRENT PROOF FORM
- Forward direction: contradiction using eliminated witness test
- Backward direction: contradiction by constructing agreement on all tests from negated existence

EXPLICIT GAPS
- None.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_05

```
THEOREM NAME
NSC_05_failure_update_preserves_bounds

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
If stateInBounds cap S and delta >= 0 and cap > 0, then:
stateInBounds cap (failureUpdate cap delta target S)

BEST CURRENT PROOF FORM
- Prove helper lemma failureBump_inBounds
- Apply helper separately to perceptual/sequential/outcome/abstract stores

EXPLICIT GAPS
- Statement currently uses cap > 0 only (not full 0 < cap < 1 from planning text).
- If strict cap<1 is needed in this theorem statement, strengthen signature and re-prove.

NEXT REQUIRED ACTION
- Decide whether theorem contract must include cap < 1.
```

---

## Theorem block NSC_06

```
THEOREM NAME
NSC_06_success_update_preserves_bounds

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
If stateInBounds cap S and 0 < gamma < 1, then:
stateInBounds cap (successUpdate gamma target S)

BEST CURRENT PROOF FORM
- Prove helper lemma successDecay_inBounds
- Lower bound via nonnegativity of product
- Upper bound via gamma <= 1 and existing bound s k <= cap

EXPLICIT GAPS
- None.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_07

```
THEOREM NAME
NSC_07_failure_update_monotone_on_target

STATUS
Integrated - partial contract

CURRENT CANONICAL STATEMENT
Given delta >= 0 and local bound s k <= cap, prove:
1) target k = true  -> s k <= failureBump cap delta target s k
2) target k = false -> failureBump cap delta target s k = s k

BEST CURRENT PROOF FORM
- Direct case split on target k
- Use le_min with s k <= cap and s k <= s k + delta

EXPLICIT GAPS
- Contract drift: theorem currently requires local precondition s k <= cap.
- Original stronger statement without that precondition is false in general.
- If global monotonicity is required, statement must be reformulated over bounded states.

NEXT REQUIRED ACTION
- Fix final contract choice: local bounded precondition vs global state invariant version.
```

---

## Theorem block NSC_08

```
THEOREM NAME
NSC_08_success_update_strict_decay_on_target

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
With 0 < gamma < 1, prove:
1) target k = true and s k > 0 -> successDecay gamma target s k < s k
2) target k = false -> successDecay gamma target s k = s k

BEST CURRENT PROOF FORM
- Case split on target k
- Strict inequality from mul_lt_mul_of_pos_right with gamma < 1 and s k > 0

EXPLICIT GAPS
- None.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_09

```
THEOREM NAME
NSC_09_success_decay_crosses_threshold_in_finite_steps

STATUS
Integrated - partial (helpers proved, endpoint axiom remains)

CURRENT CANONICAL STATEMENT
Given 0 < gamma < 1, recurrence s(n+1)=gamma*s(n), s0>0, and 0<tau<s0,
prove existence of finite N such that forall n >= N, s n <= tau.

BEST CURRENT PROOF FORM
- `NSC_09_sequence`, `NSC_09_sequence_closed_form`, and `NSC_09_thresholdN` are now integrated as concrete helper components.
- Endpoint theorem `NSC_09_success_decay_crosses_threshold_in_finite_steps` remains an explicit axiom.

EXPLICIT GAPS
- Missing explicit finite bound expression for N in final theorem body.
- Missing full arithmetic proof tying bound to recurrence.

NEXT REQUIRED ACTION
- Add closed-form lemma, explicit N definition, and final bound proof.
```

---

## Theorem block NSC_10

```
THEOREM NAME
NSC_10_cap_lt_one_prevents_absolute_suppression

STATUS
Integrated - complete proof (reformulated interface)

CURRENT CANONICAL STATEMENT
For `NSC10_SystemConfig` and `NSC10_System`, if:
- initial state satisfies invariant `P`,
- `P` is preserved by `step`,
- `P` implies `val < 1`,
then every trajectory state has value `< 1`.

BEST CURRENT PROOF FORM
- Induction on trajectory index `n` over `NSC10_trajectory`.
- Invariant transport via `NSC10_SystemInvariant`.

EXPLICIT GAPS
- The theorem is complete in the new invariant-based formulation.
- Bridge gap remains to the prior list/chain trajectory formulation used in earlier drafts.

NEXT REQUIRED ACTION
- Add an equivalence/bridge lemma from list-chain trajectories to `NSC10_trajectory` if the old interface is still required downstream.
```

---

## Theorem block NSC_11

```
THEOREM NAME
NSC_11_effective_constraint_measure_decomposition

STATUS
Integrated - complete proof (Regime A and Regime B)

CURRENT CANONICAL STATEMENT
Under covering assumption over finite universe K, prove:
M_total <= M_p + M_s + M_o + M_a

BEST CURRENT PROOF FORM
- Regime B: chain `Finset.card_union_le` inequalities under the covering equality.
- Regime A: combine covering equality with pairwise disjointness assumptions and simplify disjoint-union cardinalities to an exact sum.

EXPLICIT GAPS
- None at theorem level.

NEXT REQUIRED ACTION
- No proof action required.
```

---

## Theorem block NSC_12

```
THEOREM NAME
NSC_12_abstraction_trigger_nonincreasing_local_burden

STATUS
Integrated - complete proof (concrete model)

CURRENT CANONICAL STATEMENT
Over concrete state model `NSC12_State`:
- `NSC12_L := prog.size + test.difficulty`
- `NSC12_EqCover` enforces componentwise nonincrease
prove `NSC12_L sAfter <= NSC12_L sBefore`.

BEST CURRENT PROOF FORM
- Direct arithmetic monotonicity: `add_le_add` over componentwise bounds.

EXPLICIT GAPS
- Proof is complete for the concrete monotone-burden model.
- Semantic strength gap: current `Trigger` is trivial and `EqCover` is a simple componentwise ordering, not yet tied to richer abstraction semantics.

NEXT REQUIRED ACTION
- Strengthen model semantics (non-trivial trigger + canonical abstraction-step relation) while preserving the proved monotonicity shape.
```

---

## Theorem block NSC_13

```
THEOREM NAME
NSC_13_negative_complexity_information_lower_bound

STATUS
Integrated - partial (real-form theorem + canonical axiom)

CURRENT CANONICAL STATEMENT
If |H| <= eliminationCapacity b n with b>0 and |H|>=2, prove:
(n : Real) >= log_{b+1}(|H|)

BEST CURRENT PROOF FORM
- Real-form theorem is integrated as:
  `NSC_13_negative_complexity_information_lower_bound_real`
  with capacity model `eliminationCapacityReal`.
- Canonical finite-type theorem remains as an explicit axiom:
  `NSC_13_negative_complexity_information_lower_bound`.

EXPLICIT GAPS
- Need bridge from real-form cardinality theorem to canonical finite-type theorem.
- Counting model is still not tied to canonical mutation/test semantics.

NEXT REQUIRED ACTION
- Prove finite-type canonical theorem from the integrated real-form result plus a casting/encoding bridge.
```

---

## Theorem block NSC_14

```
THEOREM NAME
NSC_14_structured_negative_complexity_upper_bound_linear

STATUS
Integrated - axiom gap

CURRENT CANONICAL STATEMENT
For structured model with per-axis resolvability constant c, prove:
negComplexityStructured model <= c * k

BEST CURRENT PROOF FORM
- Currently represented as an explicit axiom in canonical architecture.

EXPLICIT GAPS
- Missing finalized constructive assembly proof from axis-local tests to global test suite.
- Need explicit proof that assembled suite is both valid and non-self covering.
- Need cardinality bound proof for union/image construction in canonical form.
- Latest candidate extension still leaves a hole in the finite-union cardinality sub-lemma inside the constructed-suite bound (`construct_NSC_set_card` path).

NEXT REQUIRED ACTION
- Complete constructive global test synthesis proof and replace axiom.
```

---

## Theorem block NSC_15

```
THEOREM NAME
NSC_15_structured_positive_complexity_lower_bound_exponential

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
There exists c2>0 such that for all k:
posComplexityStructured k >= c2 * 2^k

BEST CURRENT PROOF FORM
- Define behavior table as Fin k -> Bool
- Use cardinality identity card(Fin k -> Bool)=2^k
- Choose c2=1 and conclude by equality

EXPLICIT GAPS
- None for the formal bound itself.
- Interpretation gap remains: this is a generic table-size bound, not yet tied to a richer semantic positive-learning notion.

NEXT REQUIRED ACTION
- Optional: strengthen interpretation bridge to semantic positive characterization complexity.
```

---

## Theorem block NSC_16

```
THEOREM NAME
NSC_16_structured_negative_positive_gap

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
Given hNeg and hPos plus c1>0,c2>0, prove:
posComplexityStructured'(k) >= c2 * (2^(1/c1))^(negComplexityStructured'(k))

BEST CURRENT PROOF FORM
- Fix `k`, specialize `hNeg` and `hPos`.
- Derive `(2 : Real)^(neg(k)/c1) <= 2^k` from `neg(k) <= c1*k` using positivity of `c1` and monotonicity of real powers with base `2`.
- Multiply by `c2` (nonnegative) and compose with `hPos`.
- Rewrite `((2 : Real)^(1/c1))^(neg(k))` into `(2 : Real)^(neg(k)/c1)` via exponential-power algebra.

EXPLICIT GAPS
- The theorem itself is complete in abstract function form.
- Architecture bridge gap remains: deriving `hNeg`/`hPos` directly from canonical `NSC_14`/`NSC_15` objects with cast/constant wiring.

NEXT REQUIRED ACTION
- Add a canonical bridge theorem that constructs `NSC_17_Context` from `NSC_14` and `NSC_15` assumptions.
```

---

## Theorem block NSC_17_instantiation

```
THEOREM NAME
NSC_17_instantiation

STATUS
Integrated - complete proof

CURRENT CANONICAL STATEMENT
For any `NSC_17_Context ctx`, prove:
forall k, ctx.posComplexity k >= ctx.c2 * (2^(1/ctx.c1))^(ctx.negComplexity k)

BEST CURRENT PROOF FORM
- Direct application of `NSC_16_structured_negative_positive_gap` to `ctx.hC1`, `ctx.hC2`, `ctx.hNeg`, and `ctx.hPos`.

EXPLICIT GAPS
- No theorem-level proof gap.
- Context-construction gap remains (same bridge gap identified in `NSC_16`).

NEXT REQUIRED ACTION
- Add theorem(s) that build `NSC_17_Context` from canonical structured-model assumptions.
```

---

## Theorem block NSC_17

```
THEOREM NAME
NSC_17_specification_as_negative_learning

STATUS
Integrated - axiom gap

CURRENT CANONICAL STATEMENT
From hCore + hDyn + hDecomp + hGap over NSC17_System, conclude:
NSC17_NegativeLearningSpecificationSystem sys

BEST CURRENT PROOF FORM
- Final synthesis theorem is currently represented as an explicit axiom.
- Asymmetry-side instantiation sub-layer is now formalized via `NSC_17_instantiation`.

EXPLICIT GAPS
- Missing full convergence proof from asymmetry-gap dynamics to eventual specification.
- Need precise well-founded measure or descent argument with termination guarantee.
- Need harmonized use of decomposition hypothesis inside convergence argument.

NEXT REQUIRED ACTION
- Complete convergence theorem with explicit descent bound and contradiction closure.
```

---

## Theorem block NSC_18

```
THEOREM NAME
NSC_18_general_asymmetry_conjecture

STATUS
Integrated - conjecture form

CURRENT CANONICAL STATEMENT
NSC_18_general_asymmetry_conjecture G : Prop :=
MissingAssumptions G -> (not IsStructured G -> IsAsymmetric G)

BEST CURRENT PROOF FORM
- Implemented as Prop-level conjecture definition (not theorem proof).

EXPLICIT GAPS
- MissingAssumptions is currently placeholder-true.
- IsStructured and IsAsymmetric are placeholder graph predicates.
- No upgrade path assumptions are yet encoded formally.

NEXT REQUIRED ACTION
- Replace placeholder predicates with final structural/asymmetry semantics and refine MissingAssumptions into explicit assumptions list.
```

---

## Light execution sequence

1. Replace axiom gaps in order: `NSC_09`, canonical `NSC_13`, `NSC_14`, `NSC_17`.
2. Add bridge lemmas:
   - construct `NSC_17_Context` directly from canonical `NSC_14`/`NSC_15` hypotheses,
   - trajectory interface bridge for `NSC_10` (if old list-chain interface is still required),
   - semantic strengthening bridge for `NSC_12`.
3. Refine `NSC_18` from conjecture placeholder to explicit assumption-indexed conjecture schema.
