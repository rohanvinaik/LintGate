# Phase 7 Canonical Modules

Canonical entrypoint:
- `MutationTheory/Phase7.lean`

Canonical theorem map (paper numbering):
- `MutationTheory/Phase7/TheoremMap.lean`

Self-contained proof backbones (internal to canonical Phase 7):
- `MutationTheory/Phase7/Internal/Backbone.lean` (`T7.0`–`T7.9` source)
- `MutationTheory/Phase7/Internal/T7_10.lean`
- `MutationTheory/Phase7/Internal/T7_11.lean`
- `MutationTheory/Phase7/Internal/T7_12.lean`
- `MutationTheory/Phase7/Internal/T7_13.lean`
- `MutationTheory/Phase7/Internal/T7_14.lean`
- `MutationTheory/Phase7/Internal/T7_16.lean`

Primary canonical modules:
- `Trajectories.lean` (`T7.0`, `T7.0b`, `T7.0c`, `T7.5`, `T7.8`)
- `GreedyConvergence.lean` (`T7.1`, `T7.2`, `T7.4`, `T7.6`, `T7.7`)
- `PhaseTransition.lean` (`T7.3`)
- `InformationTheory.lean` (`T7.9`)
- `FreeEnergy.lean` (`T7.10`)
- `Redundancy.lean` (`T7.11`)
- `SpectrumEvolution.lean` (`T7.12`)
- `RegimeDynamics.lean` (`T7.13`)
- `RegimeDecomposition.lean` (`T7.14`)
- `CompositionGap.lean` (`T7.15`, `T7.16`)
- `SheafCondition.lean` (`T7.17`)
- `ObstructionClass.lean` (`T7.18`)
- `DistributedConvergence.lean` (`T7.19`, `T7.20`)

Canonical Phase 7 modules import only `MutationTheory/Phase7/*` and do not
depend on `MutationTheory/Legacy/*`.

Greedy approximation internals (`T5.12` / `T7.7`) are expressed via:
- `IsFirstCoverIndex_P5` (first full-cover prefix witness)
- `survivorsByPrefix_P5` (prefix survivor dynamics)
- explicit per-step decay hypotheses on survivor cardinality

`T7.21`–`T7.23` are tracked in the reference markdown but are not yet formalized
as canonical Lean theorems in this repository.
