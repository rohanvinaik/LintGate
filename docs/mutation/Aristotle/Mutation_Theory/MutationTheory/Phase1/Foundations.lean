import MutationTheory.Core.Definitions

/-! Canonical Phase 1 module (foundations). -/

namespace MutationTheory
namespace Phase1

open Core

def isSurvivor
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (P : Program D R)
    (T : TestSuite D R)
    (m : Program D R) : Prop :=
  ∀ t ∈ T, passes sem oracle P t = passes sem oracle m t

def isKilled
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (P : Program D R)
    (T : TestSuite D R)
    (m : Program D R) : Prop :=
  ∃ t ∈ T, passes sem oracle P t ≠ passes sem oracle m t

/-- Paper reference: Phase 1, kill/survive complementarity. -/
theorem killedSurvivorComplement
    {D R : Type}
    (sem : Program D R → D → R)
    (oracle : Test D R → R → Bool)
    (P : Program D R)
    (T : TestSuite D R)
    (m : Program D R) :
    isKilled sem oracle P T m ↔ ¬ isSurvivor sem oracle P T m := by
  unfold isKilled isSurvivor
  constructor
  · intro h hSurv
    rcases h with ⟨t, ht, hneq⟩
    exact hneq (hSurv t ht)
  · intro h
    by_contra hNoKill
    apply h
    intro t ht
    by_contra hneq
    exact hNoKill ⟨t, ht, hneq⟩

inductive OracleLevel
| L0 | L1 | L2 | L3 | L4
deriving Repr, DecidableEq

def OracleLevel.toNat : OracleLevel → Nat
| .L0 => 0
| .L1 => 1
| .L2 => 2
| .L3 => 3
| .L4 => 4

instance : LinearOrder OracleLevel :=
  LinearOrder.lift' OracleLevel.toNat (fun x y => by
    cases x <;> cases y <;> simp [OracleLevel.toNat])

/-- Paper reference: Phase 1, oracle hierarchy totality. -/
theorem oracleLevelTotal (a b : OracleLevel) : a ≤ b ∨ b ≤ a := by
  exact le_total a b

structure OptimizationType where
  requiredLevel : OracleLevel

/-- Paper reference: Phase 1, optimization safety monotonicity. -/
theorem optimizationSafetyMonotone
    (O₁ O₂ : OptimizationType)
    (achievedLevel : OracleLevel) :
    O₁.requiredLevel ≤ O₂.requiredLevel →
    O₂.requiredLevel ≤ achievedLevel →
    O₁.requiredLevel ≤ achievedLevel := by
  intro h12 h2a
  exact le_trans h12 h2a

end Phase1
end MutationTheory
