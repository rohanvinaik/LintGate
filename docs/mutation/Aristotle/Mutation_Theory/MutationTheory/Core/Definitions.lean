import MutationTheory.Legacy.Phase_6_Exact_Learning

/-!
# Core Definitions

This module provides the canonical core objects used across phases.

## Modeling note (`Program.id`)

`Program D R` is an abstract index (`id : Nat`) rather than concrete syntax.
All computational meaning is carried by an external semantics map
`sem : Program D R → D → R`.

This is intentional: the development reasons about concept classes and mutation
neighborhoods extensionally (via semantics), while staying agnostic to concrete
language syntax.
-/

noncomputable section

namespace MutationTheory
namespace Core

abbrev Program (D R : Type) := Phase6.Program D R
abbrev Test (D R : Type) := Phase6.Test D R
abbrev TestSuite (D R : Type) := Phase6.TestSuite D R
abbrev MutationSystem (D R : Type) := Phase6.MutationSystem D R

abbrev passes {D R : Type} := @Phase6.passes D R
abbrev trueEquiv {D R : Type} := @Phase6.trueEquiv D R
abbrev nonEquivMutants {D R : Type} := @Phase6.nonEquivMutants D R
abbrev killedSet {D R : Type} := @Phase6.killedSet D R
abbrev achievesFullSC' {D R : Type} := @Phase6.achievesFullSC' D R
abbrev specComplexity {D R : Type} := @Phase6.specComplexity D R
abbrev OracleFaithful {D R : Type} := @Phase6.OracleFaithful D R
abbrev conceptClass {D R : Type} := @Phase6.conceptClass D R
abbrev isTeachingSet {D R : Type} := @Phase6.isTeachingSet D R
abbrev teachingDimension {D R : Type} := @Phase6.teachingDimension D R

end Core
end MutationTheory
