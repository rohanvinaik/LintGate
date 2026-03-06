# Mutation Theory

This repository is now organized around one canonical Lean surface.

## Canonical Entrypoints (Use These)

- Root library: `MutationTheory.lean`
- Phase entrypoints:
  - `MutationTheory/Phase1.lean`
  - `MutationTheory/Phase2.lean`
  - `MutationTheory/Phase3.lean`
  - `MutationTheory/Phase4.lean`  ← canonical Phase 4 proof entrypoint
  - `MutationTheory/Phase5.lean`
  - `MutationTheory/Phase6.lean`
  - `MutationTheory/Phase7.lean`

Each phase entrypoint imports phase-local canonical modules under:
- `MutationTheory/Phase1/`
- `MutationTheory/Phase2/`
- `MutationTheory/Phase3/`
- `MutationTheory/Phase4/`
- `MutationTheory/Phase5/`
- `MutationTheory/Phase6/`
- `MutationTheory/Phase7/`

Shared core definitions:
- `MutationTheory/Core/Definitions.lean`
- `MutationTheory/Core/BasicProperties.lean`

## Build/Setup Files

- `lakefile.toml`
- `lake-manifest.json`
- `lean-toolchain`
- `Main.lean`
- `.github/workflows/` (CI)

## Internal Legacy Sources (Do Not Use as Entrypoints)

Historical monolithic files used as backing sources are isolated under:
- `MutationTheory/Legacy/`

They are intentionally not the public entrypoint surface.

## Migration Status

- Phase 1: canonical theorem bodies in `MutationTheory/Phase1/Foundations.lean`
- Phase 2: canonical theorem bodies in `MutationTheory/Phase2/Synthesis.lean`
- Phase 3: canonical theorem bodies in `MutationTheory/Phase3/Decomposition.lean`
- Phase 4: partial migration in `MutationTheory/Phase4/CompositionAndInformation.lean`
  (`rewriteLicense`, `survivorExistence`, `fixedPointPartition` migrated; remaining T4 theorems still bridged from `Legacy`)
- Phase 7: canonical interface now includes extended layers from scratch proofs
  (`FreeEnergy`, `Redundancy`, `SpectrumEvolution`, `RegimeDynamics`,
  `RegimeDecomposition`, `CompositionGap`, `SheafCondition`,
  `ObstructionClass`, `DistributedConvergence`) through organized wrappers in
  `MutationTheory/Phase7/*.lean`; theorem-number aliases live in
  `MutationTheory/Phase7/TheoremMap.lean` keyed to
  `PHASE_7_SPECIFICATION_DYNAMICS.md`; proof backbones are now local under
  `MutationTheory/Phase7/Internal/*.lean` (no `Legacy` imports in canonical
  Phase 7 files)

## Scratch Material

All scratch/raw work is outside this repository tree:
- `/Users/rohanvinaik/tools/lintgate/docs/mutation/Aristotle/Mutation_Theory_SCRATCH`
