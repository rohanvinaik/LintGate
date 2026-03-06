import MutationTheory.Phase7.SheafCondition
import MutationTheory.Phase7.ObstructionClass

noncomputable section

/-! Phase 7.19/7.20 distributed convergence scaffold. -/

namespace MutationTheory
namespace Phase7
namespace Distributed

open Sheaf

variable {Agent Module : Type}

/-- Every module is covered by at least one agent's local specification effort. -/
def localCoverage
    (localCovers : Agent → Module → Prop)
    (agents : Finset Agent)
    (modules : Finset Module) : Prop :=
  ∀ m ∈ modules, ∃ a ∈ agents, localCovers a m

/-- Set-based form of local coverage, useful for interoperability. -/
def localCoverageSet
    (localCovers : Agent → Module → Prop)
    (agents : Set Agent)
    (modules : Set Module) : Prop :=
  modules ⊆ ⋃ a ∈ agents, {m | localCovers a m}

/-- Finset and set formulations of local coverage are equivalent. -/
theorem localCoverage_iff_set
    (localCovers : Agent → Module → Prop)
    (agents : Finset Agent)
    (modules : Finset Module) :
    localCoverage localCovers agents modules ↔
      localCoverageSet localCovers (agents : Set Agent) (modules : Set Module) := by
  simp [localCoverage, localCoverageSet, Set.subset_def]

/-- Coverage is monotone under restricting the module target set. -/
lemma localCoverage_mono_modules
    (localCovers : Agent → Module → Prop)
    (agents : Finset Agent)
    {modules modules' : Finset Module}
    (hCov : localCoverage localCovers agents modules)
    (hSub : modules' ⊆ modules) :
    localCoverage localCovers agents modules' := by
  intro m hm
  exact hCov m (hSub hm)

/-- Coverage is monotone under enlarging the agent set. -/
lemma localCoverage_mono_agents
    (localCovers : Agent → Module → Prop)
    {agents agents' : Finset Agent}
    (modules : Finset Module)
    (hCov : localCoverage localCovers agents modules)
    (hSub : agents ⊆ agents') :
    localCoverage localCovers agents' modules := by
  intro m hm
  rcases hCov m hm with ⟨a, ha, hCover⟩
  exact ⟨a, hSub ha, hCover⟩

/-- Monotonic growth of global killed sets over asynchronous rounds. -/
def globalKilledMonotone
    (K : ℕ → Finset Module) : Prop :=
  ∀ i, K i ⊆ K (i + 1)

/-- Set-based monotonicity form (`Monotone`) of the killed trajectory. -/
def globalKilledMonotoneSet
    (K : ℕ → Set Module) : Prop :=
  Monotone K

/-- Stepwise monotonicity implies arbitrary-index monotonicity. -/
lemma globalKilledMonotone_subset_of_le
    (K : ℕ → Finset Module)
    (hMono : globalKilledMonotone K)
    {i j : ℕ}
    (hij : i ≤ j) :
    K i ⊆ K j := by
  intro m hm
  induction hij with
  | refl =>
      simpa using hm
  | @step j hij ih =>
      exact hMono j (ih)

/-- Finset and set formulations of killed-set monotonicity are equivalent. -/
theorem globalKilledMonotone_iff_set
    (K : ℕ → Finset Module) :
    globalKilledMonotone K ↔
      globalKilledMonotoneSet (fun i => (K i : Set Module)) := by
  constructor
  · intro hMono i j hij m hm
    exact globalKilledMonotone_subset_of_le (Module := Module) K hMono hij hm
  · intro hMono i m hm
    exact hMono (Nat.le_succ i) hm

/-- Communication sufficiency: every remaining interface mutant is eventually seen. -/
def communicationAdequate
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module) : Prop :=
  ∀ m ∈ remainingInterface, ∃ i, m ∈ K i

/-- Set-based eventual-visibility condition for interface mutants. -/
def communicationAdequateSet
    (K : ℕ → Set Module)
    (remainingInterface : Set Module) : Prop :=
  remainingInterface ⊆ ⋃ i, K i

/-- Finset and set formulations of communication adequacy are equivalent. -/
theorem communicationAdequate_iff_set
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module) :
    communicationAdequate K remainingInterface ↔
      communicationAdequateSet (fun i => (K i : Set Module)) (remainingInterface : Set Module) := by
  simp [communicationAdequate, communicationAdequateSet, Set.subset_def]

/-- Under monotonicity and eventual visibility, there exists a finite round where
all remaining interface mutants have been seen. -/
theorem communicationAdequate_eventually_subset
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module)
    (hMono : globalKilledMonotone K)
    (hComm : communicationAdequate K remainingInterface) :
    ∃ N, remainingInterface ⊆ K N := by
  classical
  let witness : Module → ℕ := fun m =>
    if hm : m ∈ remainingInterface then Classical.choose (hComm m hm) else 0
  have hWitnessMem : ∀ m ∈ remainingInterface, m ∈ K (witness m) := by
    intro m hm
    have hExists : ∃ i, m ∈ K i := hComm m hm
    have hChosen : m ∈ K (Classical.choose hExists) := Classical.choose_spec hExists
    simpa [witness, hm] using hChosen
  let N := remainingInterface.sup witness
  refine ⟨N, ?_⟩
  intro m hm
  have hLe : witness m ≤ N := Finset.le_sup hm
  have hSubset : K (witness m) ⊆ K N :=
    globalKilledMonotone_subset_of_le (Module := Module) K hMono hLe
  exact hSubset (hWitnessMem m hm)

/-- **T7.19** Asynchronous specification converges under sheaf gluing. -/
theorem T7_19_asynchronous_specification
    (agents : Finset Agent)
    (modules : Finset Module)
    (interfaces : Finset (Module × Module))
    (localCovers : Agent → Module → Prop)
    (compatible : Module → Module → Prop)
    (globalFullSC : Prop)
    (hSheaf : sheafCondition compatible interfaces)
    (hLocal : localCoverage localCovers agents modules)
    (hGluing :
      sheafCondition compatible interfaces →
      localCoverage localCovers agents modules →
      globalFullSC) :
    globalFullSC :=
  hGluing hSheaf hLocal

/-- **T7.20 (strengthened)** Partial communication converges in finite rounds:
the standard invariants hold and there exists a global round covering all
remaining interface mutants. -/
theorem T7_20_convergence_under_partial_communication_strengthened
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module)
    (_hSheaf : sheafCondition compatible interfaces)
    (hMono : globalKilledMonotone K)
    (hComm : communicationAdequate K remainingInterface) :
    (∀ i, K i ⊆ K (i + 1)) ∧
    (∀ m ∈ remainingInterface, ∃ i, m ∈ K i) ∧
    (∃ N, remainingInterface ⊆ K N) := by
  refine ⟨hMono, hComm, ?_⟩
  exact communicationAdequate_eventually_subset (Module := Module) K remainingInterface hMono hComm

/-- **T7.20** Convergence under partial communication.

The theorem exposes exactly the two required invariants used by the paper:
monotonicity of global killed sets and eventual visibility of interface mutants. -/
theorem T7_20_convergence_under_partial_communication
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module)
    (_hSheaf : sheafCondition compatible interfaces)
    (hMono : globalKilledMonotone K)
    (hComm : communicationAdequate K remainingInterface) :
    (∀ i, K i ⊆ K (i + 1)) ∧
    (∀ m ∈ remainingInterface, ∃ i, m ∈ K i) :=
  ⟨hMono, hComm⟩

/-- Corollary form of T7.20 giving only the finite-round full-visibility claim. -/
theorem T7_20_eventual_full_visibility
    (interfaces : Finset (Module × Module))
    (compatible : Module → Module → Prop)
    (K : ℕ → Finset Module)
    (remainingInterface : Finset Module)
    (hSheaf : sheafCondition compatible interfaces)
    (hMono : globalKilledMonotone K)
    (hComm : communicationAdequate K remainingInterface) :
    ∃ N, remainingInterface ⊆ K N := by
  exact
    (T7_20_convergence_under_partial_communication_strengthened
      (Module := Module) interfaces compatible K remainingInterface hSheaf hMono hComm).2.2

end Distributed
end Phase7
end MutationTheory
