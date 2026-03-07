# Negative Specification Complexity - Separable Proof Architecture

## Specific integration instructions (outside theorem blocks)

## A) Code architecture integration

Use this exact layer placement:

1. Core complexity layer:
   - `NSC_01` to `NSC_03`
   - Put these next to existing specification complexity foundations.

2. Identification and exact-learning layer:
   - `NSC_04` and `NSC_17`
   - Put `NSC_04` with exact identification statements.
   - Put `NSC_17` as synthesis theorem at the end of that chapter/section.

3. Dynamics and decomposition layer:
   - `NSC_05` to `NSC_12`
   - Put bounded update dynamics first (`NSC_05` to `NSC_10`), then decomposition/compression (`NSC_11`, `NSC_12`).

4. Asymmetry layer:
   - `NSC_13` to `NSC_16`
   - Put info-theoretic lower bound first, then structured upper/lower bounds, then the gap theorem.

5. Open-problem layer:
   - `NSC_18` only
   - Keep as conjecture; do not import into theorem-only chains.

Recommended import order:
- Core -> Identification -> Dynamics -> Decomposition -> Asymmetry -> Integration
- Conjectures remain isolated.

## B) Paper integration map

Use this exact section-level placement:

1. Add subsection "Negative Specification Complexity (Static Identity)":
   - includes `NSC_01`, `NSC_02`, `NSC_03`.

2. Add subsection "Identification by Exclusion":
   - includes `NSC_04`.

3. Add subsection "Bidirectional Constraint Dynamics":
   - includes `NSC_05` to `NSC_10`.

4. Add subsection "Store Decomposition and Abstraction Trigger":
   - includes `NSC_11`, `NSC_12`.

5. Add subsection "Structured Asymmetry":
   - includes `NSC_13` to `NSC_16`.

6. Add subsection "Specification as Negative Learning (Synthesis)":
   - includes `NSC_17`.

7. Add subsection "General Asymmetry Conjecture":
   - includes `NSC_18` as conjecture only.

## C) Required theorem list

- `NSC_01_elimination_characterization`
- `NSC_02_negative_spec_complexity_well_defined`
- `NSC_03_negative_spec_complexity_eq_spec_complexity`
- `NSC_04_full_elimination_iff_unique_identification`
- `NSC_05_failure_update_preserves_bounds`
- `NSC_06_success_update_preserves_bounds`
- `NSC_07_failure_update_monotone_on_target`
- `NSC_08_success_update_strict_decay_on_target`
- `NSC_09_success_decay_crosses_threshold_in_finite_steps`
- `NSC_10_cap_lt_one_prevents_absolute_suppression`
- `NSC_11_effective_constraint_measure_decomposition`
- `NSC_12_abstraction_trigger_nonincreasing_local_burden`
- `NSC_13_negative_complexity_information_lower_bound`
- `NSC_14_structured_negative_complexity_upper_bound_linear`
- `NSC_15_structured_positive_complexity_lower_bound_exponential`
- `NSC_16_structured_negative_positive_gap`
- `NSC_17_specification_as_negative_learning`
- `NSC_18_general_asymmetry_conjecture`

---

## Theorem block NSC_01

```
THEOREM NAME
NSC_01_elimination_characterization

CONTEXT (SELF-CONTAINED)
- Types D, R.
- Program/Test/TestSuite/MutationSystem objects.
- Existing functions: passes, killedSet.
- Define:
  eliminates sem oracle P t Q := passes sem oracle P t != passes sem oracle Q t
  eliminationSet sem oracle MS P T := (MS.mutants P).filter (fun Q => exists t in T, eliminates sem oracle P t Q)

GOAL
Prove:
Q in eliminationSet sem oracle MS P T <-> Q in killedSet sem oracle MS P T

CONSTRAINTS
- Direct extensional proof only.
- No dependence on any NSC theorem.

QUALITY CRITERIA
- Proof is purely definitional/unfolding and complete.
- No placeholders.
```

---

## Theorem block NSC_02

```
THEOREM NAME
NSC_02_negative_spec_complexity_well_defined

CONTEXT (SELF-CONTAINED)
- Types D, R.
- Program/Test/TestSuite/MutationSystem objects.
- Existing functions: passes, killedSet, nonEquivMutants, achievesFullSC'.
- Define:
  eliminates sem oracle P t Q := passes sem oracle P t != passes sem oracle Q t
  eliminationSet sem oracle MS P T := (MS.mutants P).filter (fun Q => exists t in T, eliminates sem oracle P t Q)
- Define feasible set:
  Feas := {k | exists T : TestSuite D R, T.card = k and nonEquivMutants sem MS P subset eliminationSet sem oracle MS P T}
- Define:
  negativeSpecComplexity sem oracle MS P := sInf Feas

GOAL
Under hypothesis hAch : exists T : TestSuite D R, achievesFullSC' sem oracle MS P T, prove:
1) Feas is nonempty
2) Feas is bounded below in Nat

CONSTRAINTS
- Must explicitly construct witnesses.
- Must not rely on implicit behavior of sInf on empty sets.

QUALITY CRITERIA
- Nonempty and lower-bound arguments are explicit and machine-checkable.
- No placeholders.
```

---

## Theorem block NSC_03

```
THEOREM NAME
NSC_03_negative_spec_complexity_eq_spec_complexity

CONTEXT (SELF-CONTAINED)
- Types D, R.
- Program/Test/TestSuite/MutationSystem objects.
- Existing functions: passes, nonEquivMutants, achievesFullSC', specComplexity.
- Define:
  eliminates sem oracle P t Q := passes sem oracle P t != passes sem oracle Q t
  eliminationSet sem oracle MS P T := (MS.mutants P).filter (fun Q => exists t in T, eliminates sem oracle P t Q)
- Define feasible set:
  Feas := {k | exists T : TestSuite D R, T.card = k and nonEquivMutants sem MS P subset eliminationSet sem oracle MS P T}
- Define:
  negativeSpecComplexity sem oracle MS P := sInf Feas
- Hypothesis hAch : exists T : TestSuite D R, achievesFullSC' sem oracle MS P T.

GOAL
Prove:
negativeSpecComplexity sem oracle MS P = specComplexity sem oracle MS P

CONSTRAINTS
- Show equality of minimization domains (feasible test-suite sets).
- Then conclude equality of infima.

QUALITY CRITERIA
- One exact equality theorem.
- No placeholders.
```

---

## Theorem block NSC_04

```
THEOREM NAME
NSC_04_full_elimination_iff_unique_identification

CONTEXT (SELF-CONTAINED)
- Types D, R; Program/Test/TestSuite.
- sem, oracle.
- Finite candidate class H : Finset (Program D R), with P in H.
- Define:
  FullyEliminated(T) := forall Q in H, Q != P -> exists t in T, passes sem oracle P t != passes sem oracle Q t
  UniquelyIdentified(T) := forall Q in H, (forall t in T, passes sem oracle P t = passes sem oracle Q t) -> Q = P

GOAL
Prove:
FullyEliminated(T) <-> UniquelyIdentified(T)

CONSTRAINTS
- Fully finite, constructive reasoning.
- No dependence on NSC_01..NSC_03.

QUALITY CRITERIA
- Direct iff proof.
- No placeholders.
```

---

## Theorem block NSC_05

```
THEOREM NAME
NSC_05_failure_update_preserves_bounds

CONTEXT (SELF-CONTAINED)
- Key type K.
- Strength := Real.
- Parameters cap, gamma with 0 < cap, cap < 1, 0 < gamma, gamma < 1.
- State with four stores: perceptual, sequential, outcome, abstract : K -> Strength.
- inBounds(s) := forall k, 0 <= s k and s k <= cap
- stateInBounds(S) := all four stores satisfy inBounds
- failureBump(delta, target, s)(k) := if target k then min cap (s k + delta) else s k
- failureUpdate applies failureBump to all four stores.

GOAL
If stateInBounds(S) and delta >= 0, prove stateInBounds(failureUpdate(delta, target, S)).

CONSTRAINTS
- Must prove both lower and upper bounds.

QUALITY CRITERIA
- Complete arithmetic proof.
- No placeholders.
```

---

## Theorem block NSC_06

```
THEOREM NAME
NSC_06_success_update_preserves_bounds

CONTEXT (SELF-CONTAINED)
- Key type K.
- Strength := Real.
- Parameters cap, gamma with 0 < cap, cap < 1, 0 < gamma, gamma < 1.
- State with four stores: perceptual, sequential, outcome, abstract : K -> Strength.
- inBounds(s) := forall k, 0 <= s k and s k <= cap
- stateInBounds(S) := all four stores satisfy inBounds
- successDecay(target, s)(k) := if target k then gamma * s k else s k
- successUpdate applies successDecay to all four stores.

GOAL
If stateInBounds(S), prove stateInBounds(successUpdate(target, S)).

CONSTRAINTS
- Use only 0 < gamma < 1 and existing bounds.

QUALITY CRITERIA
- Complete bound-preservation proof.
- No placeholders.
```

---

## Theorem block NSC_07

```
THEOREM NAME
NSC_07_failure_update_monotone_on_target

CONTEXT (SELF-CONTAINED)
- Key type K.
- Strength := Real.
- Parameter cap with 0 < cap and cap < 1.
- Parameter delta with delta >= 0.
- target : K -> Bool.
- s : K -> Strength.
- Define:
  failureBump(delta, target, s)(k) := if target k then min cap (s k + delta) else s k

GOAL
Prove for all s, k:
1) target k = true  -> s k <= failureBump(delta, target, s)(k)
2) target k = false -> failureBump(delta, target, s)(k) = s k

CONSTRAINTS
- Explicit case split on target k.

QUALITY CRITERIA
- Proof is concise and complete.
- No placeholders.
```

---

## Theorem block NSC_08

```
THEOREM NAME
NSC_08_success_update_strict_decay_on_target

CONTEXT (SELF-CONTAINED)
- Key type K.
- Strength := Real.
- 0 < gamma < 1.
- target : K -> Bool.
- s : K -> Strength.
- Define:
  successDecay(target, s)(k) := if target k then gamma * s k else s k

GOAL
Prove for all s, k:
1) target k = true and s k > 0  -> successDecay(target, s)(k) < s k
2) target k = false             -> successDecay(target, s)(k) = s k

CONSTRAINTS
- Strict inequality must be explicit.

QUALITY CRITERIA
- Complete strict-decay proof.
- No placeholders.
```

---

## Theorem block NSC_09

```
THEOREM NAME
NSC_09_success_decay_crosses_threshold_in_finite_steps

CONTEXT (SELF-CONTAINED)
- 0 < gamma < 1.
- Sequence s_0 > 0 and s_(n+1) = gamma * s_n.
- Threshold tau with 0 < tau < s_0.

GOAL
Prove existence of finite N such that for all n >= N, s_n <= tau.

CONSTRAINTS
- Must provide explicit finite bound expression for N.

QUALITY CRITERIA
- Geometric decay argument is explicit and complete.
- No placeholders.
```

---

## Theorem block NSC_10

```
THEOREM NAME
NSC_10_cap_lt_one_prevents_absolute_suppression

CONTEXT (SELF-CONTAINED)
- cap with 0 < cap < 1.
- Any finite update trajectory from an initial in-bounds state.
- Assumption: each update preserves in-bounds.

GOAL
Prove every store value along the trajectory is strictly < 1.

CONSTRAINTS
- Trajectory-level invariant reasoning.

QUALITY CRITERIA
- Complete global invariant proof.
- No placeholders.
```

---

## Theorem block NSC_11

```
THEOREM NAME
NSC_11_effective_constraint_measure_decomposition

CONTEXT (SELF-CONTAINED)
- Finite key universe.
- Store measures M_p, M_s, M_o, M_a.
- Global measure M_total.
- Choose one counting regime explicitly:
  Regime A (exact decomposition) OR Regime B (subadditive bound).

GOAL
Prove either:
1) M_total = M_p + M_s + M_o + M_a
or
2) M_total <= M_p + M_s + M_o + M_a

CONSTRAINTS
- Must state regime assumptions explicitly.

QUALITY CRITERIA
- Counting semantics are unambiguous.
- No placeholders.
```

---

## Theorem block NSC_12

```
THEOREM NAME
NSC_12_abstraction_trigger_nonincreasing_local_burden

CONTEXT (SELF-CONTAINED)
- Local burden metric L.
- Abstraction trigger predicate Trigger.
- Equivalent coverage predicate EqCover.
- Before/after states S_before, S_after where abstraction is added.

GOAL
If Trigger(S_before) and EqCover(S_before, S_after), prove:
L(S_after) <= L(S_before)

CONSTRAINTS
- Weak inequality only.
- Do not claim strict decrease without extra assumptions.

QUALITY CRITERIA
- Complete monotonicity proof.
- No placeholders.
```

---

## Theorem block NSC_13

```
THEOREM NAME
NSC_13_negative_complexity_information_lower_bound

CONTEXT (SELF-CONTAINED)
- Finite hypothesis class H with |H| >= 2.
- Per-test elimination capacity b > 0 (explicitly defined).
- n tests can eliminate at most (b+1)^n distinguishable candidate patterns (or equivalent explicit capacity model).

GOAL
Derive a logarithmic lower bound on required negative tests in terms of |H| and b.

CONSTRAINTS
- Pure counting/combinatorial argument.

QUALITY CRITERIA
- Every counting step explicit.
- No placeholders.
```

---

## Theorem block NSC_14

```
THEOREM NAME
NSC_14_structured_negative_complexity_upper_bound_linear

CONTEXT (SELF-CONTAINED)
- Structured model with k independent behavioral axes.
- Axis-separable negative tests.
- Each axis resolvable by at most c tests (constant c).

GOAL
Prove existence of c1 such that:
negComplexityStructured(k) <= c1 * k

CONSTRAINTS
- Constructive strategy required.

QUALITY CRITERIA
- Upper bound constant made explicit.
- No placeholders.
```

---

## Theorem block NSC_15

```
THEOREM NAME
NSC_15_structured_positive_complexity_lower_bound_exponential

CONTEXT (SELF-CONTAINED)
- Structured model with k independent behavioral axes.
- Positive characterization requires resolving full behavior table over axis combinations.

GOAL
Prove existence of c2 > 0 such that:
posComplexityStructured(k) >= c2 * 2^k

CONSTRAINTS
- Explicit counting argument over behavior-table size.

QUALITY CRITERIA
- Lower bound is explicit and complete.
- No placeholders.
```

---

## Theorem block NSC_16

```
THEOREM NAME
NSC_16_structured_negative_positive_gap

CONTEXT (SELF-CONTAINED)
- Local hypotheses:
  hNeg: negComplexityStructured(k) <= c1 * k
  hPos: c2 * 2^k <= posComplexityStructured(k)
  with c1 > 0, c2 > 0

GOAL
Conclude structured exponential gap between negative and positive complexity growth.

CONSTRAINTS
- Must be hypothesis-driven synthesis.

QUALITY CRITERIA
- Conclusion explicitly tied to assumptions.
- No placeholders.
```

---

## Theorem block NSC_17

```
THEOREM NAME
NSC_17_specification_as_negative_learning

CONTEXT (SELF-CONTAINED)
- Local hypotheses:
  hCore   : negative/static complexity identity holds
  hDyn    : boundedness and correction dynamics hold
  hDecomp : decomposition/compression result holds
  hGap    : structured asymmetry gap holds

GOAL
Prove a synthesis theorem stating that, under hCore + hDyn + hDecomp + hGap,
the framework is formally a negative-learning specification system.

CONSTRAINTS
- Must not claim anything outside hypotheses.

QUALITY CRITERIA
- Clean synthesis theorem with explicit assumptions.
- No placeholders.
```

---

## Theorem block NSC_18

```
THEOREM NAME
NSC_18_general_asymmetry_conjecture

CONTEXT (SELF-CONTAINED)
- General non-structured program classes.

GOAL
State the broad asymmetry claim as a conjecture.

CONSTRAINTS
- Must be marked conjecture, not theorem.
- Must list missing assumptions required for upgrade to theorem status.

QUALITY CRITERIA
- Precise conjecture statement with explicit open conditions.
```

---

## Light process sketch

1. Prove NSC_01 to NSC_04.
2. Prove NSC_05 to NSC_10.
3. Prove NSC_11 and NSC_12.
4. Prove NSC_13 to NSC_16.
5. Prove NSC_17.
6. Add NSC_18 as conjecture with missing assumptions.
