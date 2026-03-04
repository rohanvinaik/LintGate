/-
  Specification Completeness Theory — Common Definitions

  Formal foundations for the theory that mutation testing measures specification
  completeness, and that specification completeness is a prerequisite for safe
  algebraic optimization of programs.

  This file provides the shared definitions, structures, and axioms used by all
  theorem statements. It is intended as Lean File Context for a proof assistant.

  Dependencies: Mathlib (Order, Data.Finset, Data.Rat, Probability basics)
-/

import Mathlib.Order.Lattice
import Mathlib.Order.BoundedOrder
import Mathlib.Data.Finset.Basic
import Mathlib.Data.Finset.Card
import Mathlib.Data.Finset.Powerset
import Mathlib.Data.Rat.Basic
import Mathlib.Order.Partition.Finpartition
import Mathlib.Tactic

/-! ## 1. Core Types -/

/-- A program is an abstract entity with an input domain and output range.
    We model programs as opaque types with an evaluation function. -/
structure Program (D R : Type) where
  eval : D → R

/-- An oracle judges whether a program's output on a given input is acceptable. -/
structure Oracle (R : Type) where
  judge : R → Bool

/-- A test is a pair of an input and an oracle. -/
structure Test (D R : Type) where
  input : D
  oracle : Oracle R

/-- A test suite is a finite set of tests. -/
abbrev TestSuite (D R : Type) := Finset (Test D R)

/-- A program passes a test if the oracle accepts the program's output on the test input. -/
def passes (P : Program D R) (t : Test D R) : Bool :=
  t.oracle.judge (P.eval t.input)

/-- A program passes a test suite if it passes every test in the suite. -/
def passesAll (P : Program D R) (T : TestSuite D R) : Prop :=
  ∀ t ∈ T, passes P t = true


/-! ## 2. Mutation -/

/-- A mutation operator is a function from programs to finite sets of mutant programs.
    Each mutant is syntactically distinct from the original. -/
structure MutationOperator (D R : Type) where
  apply : Program D R → Finset (Program D R)

/-- A mutation category groups related operators (arithmetic, conditional, string, etc.). -/
structure MutationCategory where
  name : String
  deriving DecidableEq, Hashable

/-- Standard mutation categories for a Python-like language. -/
def cat_arith : MutationCategory := ⟨"arithmetic"⟩
def cat_cond  : MutationCategory := ⟨"conditional"⟩
def cat_str   : MutationCategory := ⟨"string"⟩
def cat_kw    : MutationCategory := ⟨"keyword"⟩
def cat_bound : MutationCategory := ⟨"boundary"⟩
def cat_ret   : MutationCategory := ⟨"return"⟩
def cat_call  : MutationCategory := ⟨"call"⟩

/-- A categorized mutation system assigns each operator to exactly one category
    and produces a finite set of mutants for any program. -/
structure MutationSystem (D R : Type) where
  categories : Finset MutationCategory
  /-- All mutants of a program, tagged by category -/
  mutants : Program D R → Finset (MutationCategory × Program D R)
  /-- Mutants in a specific category -/
  mutantsInCat (P : Program D R) (c : MutationCategory) : Finset (Program D R) :=
    (mutants P |>.filter (fun p => p.1 = c)).image Prod.snd
  /-- All mutants (ignoring category) -/
  allMutants (P : Program D R) : Finset (Program D R) :=
    (mutants P).image Prod.snd
  /-- Categories partition the mutant set -/
  partition_prop : ∀ P : Program D R, ∀ m ∈ allMutants P,
    ∃! c ∈ categories, m ∈ mutantsInCat P c


/-! ## 3. Test-Induced Equivalence and Killed/Surviving Sets -/

/-- Two programs are T-equivalent if no test in T distinguishes them. -/
def testEquiv (T : TestSuite D R) (P₁ P₂ : Program D R) : Prop :=
  ∀ t ∈ T, passes P₁ t = passes P₂ t

/-- A mutant is killed by T (with respect to original P) if some test distinguishes them. -/
def isKilled (P : Program D R) (T : TestSuite D R) (m : Program D R) : Prop :=
  ∃ t ∈ T, passes P t ≠ passes m t

/-- A mutant survives T if no test distinguishes it from P. -/
def isSurvivor (P : Program D R) (T : TestSuite D R) (m : Program D R) : Prop :=
  ∀ t ∈ T, passes P t = passes m t

/-- isKilled and isSurvivor are complementary. -/
theorem killed_or_survives (P : Program D R) (T : TestSuite D R) (m : Program D R) :
    isKilled P T m ↔ ¬ isSurvivor P T m := by
  simp [isKilled, isSurvivor]
  push_neg
  rfl

/-- The killed set: all mutants distinguished from P by T.
    We work with decidable instances for Finset filtering. -/
section KilledSets

variable [DecidableEq (Program D R)]

/-- Assuming decidable test passage, the killed set is computable. -/
def killedSet (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R)
    [∀ (p : Program D R) (t : Test D R), Decidable (passes P t ≠ passes p t)] :
    Finset (Program D R) :=
  MS.allMutants P |>.filter (fun m => ∃ t ∈ T, passes P t ≠ passes m t)

/-- The surviving set is the complement within all mutants. -/
def survSet (MS : MutationSystem D R) (P : Program D R) (T : TestSuite D R)
    [∀ (p : Program D R) (t : Test D R), Decidable (passes P t ≠ passes p t)] :
    Finset (Program D R) :=
  MS.allMutants P |>.filter (fun m => ∀ t ∈ T, passes P t = passes m t)

end KilledSets


/-! ## 4. Specification Completeness -/

/-- Mutation score as a rational number (killed / total).
    Returns 1 if there are no mutants (vacuously fully specified). -/
noncomputable def mutationScore (killed total : ℕ) : ℚ :=
  if total = 0 then 1 else (killed : ℚ) / (total : ℚ)

/-- Specification completeness equals mutation score. The renaming is intentional:
    it emphasizes the reframing from "test quality" to "specification completeness." -/
abbrev specCompleteness := mutationScore


/-! ## 5. The Refinement Order on Test Suites -/

/-- The distinguishing power of a test suite is its killed set.
    We define the refinement order via set inclusion of killed sets.
    T₁ ⪯ T₂ iff every mutant killed by T₁ is also killed by T₂. -/
structure SpecRefinement (D R : Type) [DecidableEq (Program D R)] where
  MS : MutationSystem D R
  P : Program D R

/-- T₁ refines to T₂ if the killed set of T₁ is a subset of the killed set of T₂. -/
def refines [DecidableEq (Program D R)]
    (MS : MutationSystem D R) (P : Program D R)
    (T₁ T₂ : TestSuite D R)
    [∀ (p : Program D R) (t : Test D R), Decidable (passes P t ≠ passes p t)] :
    Prop :=
  killedSet MS P T₁ ⊆ killedSet MS P T₂


/-! ## 6. Specification Level Classification -/

/-- The specification levels form a totally ordered enumeration. -/
inductive SpecLevel where
  | unspecified        -- SC ∈ [0, 0.1)
  | weaklySpecified    -- SC ∈ [0.1, 0.3)
  | partiallySpecified -- SC ∈ [0.3, 0.5)
  | moderatelySpecified-- SC ∈ [0.5, 0.8)
  | specified          -- SC ∈ [0.8, 1.0]
  deriving DecidableEq, Repr

/-- SpecLevel has a natural linear order. -/
instance : LE SpecLevel where
  le a b := a.toCtorIdx ≤ b.toCtorIdx

instance : LT SpecLevel where
  lt a b := a.toCtorIdx < b.toCtorIdx

/-- Classify a mutation score into a specification level. -/
noncomputable def classifyLevel (sc : ℚ) : SpecLevel :=
  if sc < 1/10 then .unspecified
  else if sc < 3/10 then .weaklySpecified
  else if sc < 1/2 then .partiallySpecified
  else if sc < 4/5 then .moderatelySpecified
  else .specified


/-! ## 7. Category Survival Profiles -/

/-- A survival profile maps each category to its per-category survival rate. -/
def SurvivalProfile := MutationCategory → ℚ

/-- Active dimension count: number of categories with nonzero survival
    and at least one mutant. -/
noncomputable def activeDimensions
    (cats : Finset MutationCategory)
    (profile : SurvivalProfile)
    (hasMutants : MutationCategory → Bool) : ℕ :=
  (cats.filter (fun c => hasMutants c && profile c > 0)).card


/-! ## 8. True Observational Equivalence -/

/-- Two programs are truly (extensionally) equivalent if they agree on all inputs. -/
def trueEquiv (P₁ P₂ : Program D R) [DecidableEq R] : Prop :=
  ∀ x : D, P₁.eval x = P₂.eval x

/-- A mutant is equivalent if it is truly equivalent to the original. -/
def isEquivMutant (P m : Program D R) [DecidableEq R] : Prop :=
  trueEquiv P m


/-! ## 9. Oracle Hierarchy -/

/-- Oracle levels form a total order representing assertion strength. -/
inductive OracleLevel where
  | crash       -- Program doesn't crash
  | type_       -- Output has correct type
  | structural  -- Output has correct structure (e.g., length)
  | shape       -- Output has correct shape/format
  | value       -- Output has correct value
  | property    -- Output satisfies an algebraic law
  deriving DecidableEq, Repr

instance : LE OracleLevel where
  le a b := a.toCtorIdx ≤ b.toCtorIdx

instance : LT OracleLevel where
  lt a b := a.toCtorIdx < b.toCtorIdx

/-- Each oracle has an associated level. -/
structure LeveledOracle (R : Type) extends Oracle R where
  level : OracleLevel

/-- A leveled test carries oracle level information. -/
structure LeveledTest (D R : Type) where
  input : D
  oracle : LeveledOracle R


/-! ## 10. Optimization Types and Prerequisites -/

/-- Named optimization types that the algebra pipeline may recommend. -/
inductive OptimizationType where
  | crashGuardElim    -- Remove unnecessary try/except
  | typeSpecialization-- Specialize to known output type
  | preallocation     -- Pre-allocate containers
  | memoization       -- Cache return values
  | parallelization   -- Reorder / parallelize via algebraic laws
  deriving DecidableEq, Repr

/-- Each optimization requires a minimum oracle level for safety. -/
def optRequiredLevel : OptimizationType → OracleLevel
  | .crashGuardElim     => .crash
  | .typeSpecialization => .type_
  | .preallocation      => .structural
  | .memoization        => .value
  | .parallelization    => .property


/-! ## 11. The Cross-Channel Gate -/

/-- Gate status after combining algebra and mutation signals. -/
inductive GateStatus where
  | verified   -- Mutation confirms: safe to optimize
  | penalized  -- Mutation cautions: reduced confidence
  | gated      -- Mutation blocks: specification too incomplete
  deriving DecidableEq, Repr

/-- The algebra pipeline's output for a function. -/
structure AlgebraResult where
  property : OptimizationType
  confidence : ℚ  -- ∈ [0, 1]

/-- The mutation pipeline's output for a function. -/
structure MutationState where
  survivalRate : ℚ  -- ∈ [0, 1]

/-- The gated result after combining both signals. -/
structure GatedResult where
  property : OptimizationType
  confidence : ℚ
  status : GateStatus

/-- The cross-channel gate function.
    High survival → gate (block optimization).
    Medium survival → penalize (reduce confidence).
    Low survival → verify (approve optimization). -/
noncomputable def crossChannelGate (α : AlgebraResult) (μ : MutationState) : GatedResult :=
  if μ.survivalRate ≥ 3/5 then
    { property := α.property, confidence := 1/10, status := .gated }
  else if μ.survivalRate ≥ 3/10 then
    { property := α.property, confidence := α.confidence * 1/2, status := .penalized }
  else
    { property := α.property, confidence := min α.confidence (9/10), status := .verified }


/-! ## 12. Decomposition -/

/-- A decomposition of a program f into components g₁,...,gₖ composed by h.
    We model this abstractly: f = h ∘ (g₁,...,gₖ). -/
structure Decomposition (D R : Type) where
  /-- Number of components -/
  k : ℕ
  /-- Intermediate types (one per component) -/
  intermediateDomain : Fin k → Type
  intermediateRange : Fin k → Type
  /-- Component functions -/
  components : (i : Fin k) → Program (intermediateDomain i) (intermediateRange i)
  /-- Composition function -/
  compose : Program (∀ i, intermediateRange i) R
  /-- The original function, for stating the correctness property -/
  original : Program D R


/-! ## 13. Probability Model for Prediction Layer -/

/-- A signal is a function from programs to a rational-valued observation. -/
def Signal (D R : Type) := Program D R → ℚ

/-- A set of independent signals for the Naive Bayes prediction model. -/
structure PredictionModel (D R : Type) where
  signals : Fin n → Signal D R
  /-- Individual detection probability per signal -/
  detectionProb : Fin n → ℚ
  /-- All probabilities are in [0, 1] -/
  prob_bounds : ∀ i, 0 ≤ detectionProb i ∧ detectionProb i ≤ 1

/-- Combined detection probability under independence assumption. -/
noncomputable def combinedDetection (model : PredictionModel D R) : ℚ :=
  1 - Finset.univ.prod (fun i => 1 - model.detectionProb i)


/-! ## 14. Synthesis Loop -/

/-- State of the test synthesis loop: a test suite and surviving mutant set. -/
structure SynthState (D R : Type) [DecidableEq (Program D R)] where
  tests : TestSuite D R
  survivors : Finset (Program D R)

/-- The synthesis operator takes the current state and produces a new state
    with (weakly) more tests and (weakly) fewer survivors. -/
structure SynthOperator (D R : Type) [DecidableEq (Program D R)] where
  step : SynthState D R → SynthState D R
  /-- Tests only grow -/
  tests_monotone : ∀ s, s.tests ⊆ (step s).tests
  /-- Survivors only shrink -/
  surv_antitone : ∀ s, (step s).survivors ⊆ s.survivors


/-! ## 15. Monty Hall Filtering -/

/-- A category prior assigns a probability of survivors to each category,
    conditioned on static signals. -/
def CategoryPrior := MutationCategory → ℚ

/-- The filtered mutation set retains only categories above a threshold. -/
noncomputable def filteredCategories
    (prior : CategoryPrior)
    (cats : Finset MutationCategory)
    (threshold : ℚ) : Finset MutationCategory :=
  cats.filter (fun c => prior c > threshold)
