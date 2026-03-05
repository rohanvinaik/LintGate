# Formal Foundations for Specification Completeness Theory

## Mathematical Formalization of Mutation-Guided Decomposition

**Companion to**: Mutation Testing as Specification Completeness (LintGate Theory Document)  
**Purpose**: Precise definitions, theorem statements, and proof strategies suitable for mechanized verification  
**Status**: Theorem statements with proof sketches; formal proofs TBD

---

## 0. Notation and Conventions

We write $\mathbb{B} = \{0, 1\}$ for booleans, $\mathbb{N}$ for naturals, $\mathcal{P}(X)$ for the powerset of $X$. Functions are total unless stated otherwise. We use $\equiv$ for definitional equality and $=$ for provable equality. $|X|$ denotes the cardinality of a set $X$. We write $f : A \to B$ for total functions and $f : A \rightharpoonup B$ for partial functions.

When we write "program" we mean a computable function $f : D \to R$ from some input domain $D$ to some output range $R$, abstracted from its syntactic representation. When we write "mutant" we mean a syntactically distinct program $f' : D \to R$ obtained by applying a mutation operator to the source of $f$.

---

## 1. The Specification Refinement Order

### 1.1 Definitions

**Definition 1.1 (Program and Mutation Space).** Fix a programming language $\mathcal{L}$ and a finite set of mutation operators $\mathcal{O} = \{o_1, \ldots, o_n\}$. For a program $P \in \mathcal{L}$, define:
- $\text{Mut}(P) = \{ P' \in \mathcal{L} : P' = o(P, \ell)$ for some $o \in \mathcal{O}$, site $\ell \in \text{sites}(P, o) \}$ — the set of all first-order mutants of $P$.
- We assume $\text{Mut}(P)$ is finite for any $P$ (guaranteed since programs have finite source and operators target finite sites).

**Definition 1.2 (Test Suite and Observation).** A test suite is a finite set $T = \{t_1, \ldots, t_m\}$ where each $t_i$ is a pair $(x_i, \pi_i)$ of an input $x_i \in D$ and an oracle $\pi_i : R \to \mathbb{B}$ that judges the output. We write $t_i(P) = \pi_i(P(x_i))$. We say $P$ *passes* $T$ if $\forall t \in T,\ t(P) = 1$.

**Definition 1.3 (Test-Induced Equivalence).** Given test suite $T$, define the equivalence relation $\sim_T$ on $\{P\} \cup \text{Mut}(P)$ by:

$$P' \sim_T P'' \iff \forall t \in T,\ t(P') = t(P'')$$

That is, two programs are $T$-equivalent if no test in $T$ distinguishes them.

**Definition 1.4 (Killed and Surviving Mutants).**
- $\text{Killed}(P, T) = \{ m \in \text{Mut}(P) : m \not\sim_T P \}$
- $\text{Surv}(P, T) = \{ m \in \text{Mut}(P) : m \sim_T P \}$
- $\text{Mut}(P) = \text{Killed}(P, T) \sqcup \text{Surv}(P, T)$ (disjoint union)

**Definition 1.5 (Mutation Score).** $\text{MS}(P, T) = |\text{Killed}(P, T)| / |\text{Mut}(P)|$ when $|\text{Mut}(P)| > 0$.

**Definition 1.6 (Specification Completeness).** $\text{SC}(P, T) = \text{MS}(P, T)$. We use the term "specification completeness" to emphasize the reframing: this measures how fully $T$ specifies the behavior of $P$, not "test quality" as an abstract property.

### 1.2 The Refinement Order on Test Suites

**Definition 1.7 (Distinguishing Power).** For test suite $T$ and program $P$, define the *distinguishing power* $\mathcal{D}(T, P) = \text{Killed}(P, T) \subseteq \text{Mut}(P)$.

**Definition 1.8 (Specification Refinement).** For test suites $T_1, T_2$ relative to program $P$, define:

$$T_1 \preceq_P T_2 \iff \mathcal{D}(T_1, P) \subseteq \mathcal{D}(T_2, P)$$

That is, $T_2$ refines $T_1$ if every mutant killed by $T_1$ is also killed by $T_2$.

**Theorem 1.1 (Refinement is a Partial Order).** $(\mathcal{T}_P, \preceq_P)$ is a partial order on the set $\mathcal{T}_P$ of all test suites for $P$ (quotiented by $\mathcal{D}(T_1, P) = \mathcal{D}(T_2, P)$).

*Proof strategy*: Reflexivity and transitivity follow from $\subseteq$ on $\mathcal{P}(\text{Mut}(P))$. Antisymmetry holds after quotienting by distinguishing power. $\square$

**Theorem 1.2 (Specification Refinement is a Bounded Lattice).** The poset $(\mathcal{P}(\text{Mut}(P)), \subseteq)$ of killed-sets is a bounded lattice:

- **Bottom**: $\bot = \emptyset$ (no tests, no mutants killed)  
- **Top**: $\top = \text{Mut}(P) \setminus \text{Equiv}(P)$ where $\text{Equiv}(P)$ is the set of equivalent mutants (see §6)
- **Meet**: $K_1 \wedge K_2 = K_1 \cap K_2$ (mutants killed by both suites)
- **Join**: $K_1 \vee K_2 = K_1 \cup K_2$ (mutants killed by either suite)

*Proof strategy*: This is the powerset lattice restricted to the achievable subsets. The key question is whether $K_1 \cap K_2$ is always achievable. It is: for any $m \in K_1 \cap K_2$, there exist tests $t_1 \in T_1$ and $t_2 \in T_2$ that kill $m$, so $\{t_1\} \cap \{t_2\}$ is non-empty... **Wait — this is wrong.** $K_1 \cap K_2$ contains mutants killed by BOTH suites, but a test in $T_1$ might kill $m$ via oracle $\pi_a$ while a test in $T_2$ kills $m$ via oracle $\pi_b$. The intersection of the killed sets doesn't correspond to the intersection of test suites. 

**Corrected Theorem 1.2a (Join-Semilattice).** The image $\{ \mathcal{D}(T, P) : T \in \mathcal{T}_P \} \subseteq \mathcal{P}(\text{Mut}(P))$ is a join-semilattice under $\subseteq$:

$$\mathcal{D}(T_1, P) \vee \mathcal{D}(T_2, P) = \mathcal{D}(T_1 \cup T_2, P) = \mathcal{D}(T_1, P) \cup \mathcal{D}(T_2, P)$$

*Proof strategy*: If $m$ is killed by $T_1$ (some test $t \in T_1$ distinguishes $m$ from $P$), then $t \in T_1 \cup T_2$, so $m$ is killed by $T_1 \cup T_2$. Conversely, any mutant killed by $T_1 \cup T_2$ is killed by some $t$ in the union, hence in $T_1$ or $T_2$. $\square$

**Theorem 1.2b (Meet Existence is Not Guaranteed).** The meet $\mathcal{D}(T_1, P) \wedge \mathcal{D}(T_2, P)$ in the image lattice may not correspond to any single test suite. That is, $\mathcal{D}(T_1, P) \cap \mathcal{D}(T_2, P)$ may not equal $\mathcal{D}(T, P)$ for any $T$.

*Proof sketch*: Consider a mutant $m$ killed by $t_1 \in T_1$ via oracle $\pi_1$ and by $t_2 \in T_2$ via oracle $\pi_2$, where $t_1$ and $t_2$ are distinct tests. Then $m \in \mathcal{D}(T_1, P) \cap \mathcal{D}(T_2, P)$. But a test suite $T$ with $\mathcal{D}(T, P) = \mathcal{D}(T_1, P) \cap \mathcal{D}(T_2, P)$ would need to kill exactly those mutants killed by both — and no more. This requires a "selective" suite that can kill $m$ but NOT kill mutants in $\mathcal{D}(T_1, P) \setminus \mathcal{D}(T_2, P)$. Such selectivity is not generally achievable because a single test may kill multiple mutants. $\square$

**Corollary 1.3 (Specification Completeness as Monotone Function).** $\text{SC}(P, \cdot) : \mathcal{T}_P \to [0, 1]$ is monotone with respect to $\preceq_P$:

$$T_1 \preceq_P T_2 \implies \text{SC}(P, T_1) \leq \text{SC}(P, T_2)$$

*Proof*: $\mathcal{D}(T_1, P) \subseteq \mathcal{D}(T_2, P)$ implies $|\mathcal{D}(T_1, P)| \leq |\mathcal{D}(T_2, P)|$. $\square$

### 1.3 The Specification Completeness Lattice on Functions

The more interesting lattice is not over test suites but over *specification states* of functions.

**Definition 1.9 (Specification State).** For program $P$ and test suite $T$, the specification state is the partition $\Pi(P, T)$ of $\text{Mut}(P)$ induced by $\sim_T$ restricted to $\text{Mut}(P)$.

Equivalently: $\Pi(P, T)$ tells you which mutants are distinguishable from each other (and from $P$) under $T$.

**Theorem 1.4 (Partition Lattice).** The set of specification states $\{\Pi(P, T) : T \in \mathcal{T}_P\}$ ordered by partition refinement forms a join-semilattice embedded in the full partition lattice $\text{Part}(\text{Mut}(P))$.

- The full partition lattice $\text{Part}(\text{Mut}(P))$ is a complete lattice (classical result).
- The image under $T \mapsto \Pi(P, T)$ is closed under joins (since $T_1 \cup T_2$ gives the join).
- The image is NOT generally closed under meets (same argument as Theorem 1.2b).

**Remark.** The theory document's informal use of "lattice" should be read as "join-semilattice with bounded top and bottom." This is sufficient for the operational chain: the relevant operations are all join-like (adding tests refines specification), and the lattice axioms needed are just monotonicity and bounded completeness.

### 1.4 Connecting to the Specification Levels

**Definition 1.10 (Specification Level Classification).** Define the classification function $\text{Level} : [0, 1] \to \{$UNSPECIFIED, WEAKLY\_SPECIFIED, PARTIALLY\_SPECIFIED, MODERATELY\_SPECIFIED, SPECIFIED$\}$ by threshold intervals:

| Level | SC Range | Enabled Optimizations |
|-------|----------|----------------------|
| UNSPECIFIED | $[0, 0.1)$ | None — cannot safely change anything |
| WEAKLY_SPECIFIED | $[0.1, 0.3)$ | Type and call-graph reasoning only |
| PARTIALLY_SPECIFIED | $[0.3, 0.5)$ | Purity classification reliable |
| MODERATELY_SPECIFIED | $[0.5, 0.8)$ | Most performance optimizations |
| SPECIFIED | $[0.8, 1.0]$ | Full algebraic optimization |

**Theorem 1.5 (Level is a Chain, hence a Lattice).** The specification levels form a totally ordered set (chain), hence trivially a distributive lattice. The composition $\text{Level} \circ \text{SC}(P, \cdot) : \mathcal{T}_P \to \text{Levels}$ is monotone.

*Proof*: Total order by definition. Monotonicity follows from Corollary 1.3 and the fact that Level is monotone on $[0,1]$. $\square$

---

## 2. Observational Equivalence Under Finite Test Suites

### 2.1 The Approximation Relationship

**Definition 2.1 (True Observational Equivalence).** Two programs $P_1, P_2 : D \to R$ are truly observationally equivalent, written $P_1 \cong P_2$, if:

$$\forall x \in D,\ P_1(x) = P_2(x)$$

(We restrict to extensional equivalence; contextual equivalence in the Morris sense would quantify over all program contexts, which is stronger but undecidable.)

**Definition 2.2 (Test-Approximate Equivalence).** $P_1 \approx_T P_2$ iff $\forall t \in T,\ t(P_1) = t(P_2)$. Note this is weaker than $\cong$ in general.

**Theorem 2.1 (Approximation Hierarchy).**

$$P_1 \cong P_2 \implies P_1 \approx_T P_2 \quad \text{for all } T$$

The converse fails in general. That is, $\approx_T$ is a coarsening of $\cong$.

*Proof*: If $P_1(x) = P_2(x)$ for all $x$, then in particular for all test inputs $x_i$ and oracles $\pi_i$. $\square$

**Theorem 2.2 (Convergence of Approximation).** Define $\mathcal{T}^\infty_P$ as the set of all finite test suites. Then:

$$\bigcap_{T \in \mathcal{T}^\infty_P} \sim_T \;=\; \cong \quad \text{restricted to decidable equality on } R$$

That is, the intersection of all finite-test equivalences recovers true equivalence (for programs with decidable output equality).

*Proof strategy*: If $P_1 \not\cong P_2$, there exists $x^* \in D$ with $P_1(x^*) \neq P_2(x^*)$. The singleton test $T^* = \{(x^*, \lambda r. [r = P_1(x^*)])\}$ kills this equivalence. So $P_1 \not\sim_{T^*} P_2$, hence $P_1 \not\in \bigcap_T \sim_T$. $\square$

**Corollary 2.3.** Every surviving mutant that is NOT truly equivalent (i.e., $m \in \text{Surv}(P, T)$ with $m \not\cong P$) can be killed by some finite test extension. **The surviving-but-inequivalent mutants are exactly the specification gaps.**

### 2.2 The Equivalent Mutant Problem

**Definition 2.3 (Equivalent Mutant).** $m \in \text{Equiv}(P) \iff m \cong P$ (syntactically different but extensionally identical).

**Theorem 2.4 (Equivalent Mutant Detection is Undecidable).** Determining whether a mutant $m$ is equivalent to $P$ (i.e., whether $\forall x \in D, m(x) = P(x)$) is undecidable in general for Turing-complete languages.

*Proof*: Reduction from the halting problem. Given a TM $M$ and input $w$, construct:
- $P(x) = 0$ for all $x$
- $m(x) =$ run $M$ on $w$ for $|x|$ steps; if halts, return 1; else return 0

Then $m \cong P$ iff $M$ does not halt on $w$. $\square$

**Theorem 2.5 (Equivalent Mutant Decidability for Restricted Classes).** For the following restricted function classes, equivalent mutant detection is decidable:

(a) Pure functions over finite domains: decidable by exhaustive testing.  
(b) Functions expressible as straight-line arithmetic programs over bounded integers: decidable by SMT.  
(c) Functions whose output is determined by a finite set of observable properties (the "property basis" — see §5): decidable by checking the basis.

*Proof sketch for (a)*: If $D$ is finite, enumerate all inputs. If $\forall x \in D,\ m(x) = P(x)$, equivalent; otherwise, the distinguishing input $x^*$ exists and constitutes a test. $\square$

**Conjecture 2.6 (Decomposition Reduces Equivalent Mutants).** If $f$ is decomposed into $g_1, \ldots, g_k$ where each $g_i$ operates on a restricted domain, the proportion of equivalent mutants in $\{g_1, \ldots, g_k\}$ collectively is no greater than in $f$, and is strictly less when the decomposition separates previously entangled computation.

*Intuition*: Equivalent mutants arise when syntactic changes produce no semantic effect due to interactions between parts of the function "canceling out" the mutation. Decomposition breaks these interactions, making each component's behavior more directly observable. A mutation to $g_i$ can no longer be "hidden" by computation in $g_j$.

*Status*: Open. Would require a formal model of how syntactic mutation interacts with function composition.

---

## 3. Mutation Categories as a Graded Module

### 3.1 Category Structure

**Definition 3.1 (Mutation Category).** A mutation category $C_i$ is a subset of mutation operators targeting a specific syntactic/semantic class. The standard categories for a Python-like language are:

$$\mathcal{C} = \{C_\text{arith}, C_\text{cond}, C_\text{str}, C_\text{kw}, C_\text{bound}, C_\text{ret}, C_\text{call}\}$$

These partition $\mathcal{O}$: $\mathcal{O} = \bigsqcup_{i} C_i$.

**Definition 3.2 (Category Survival Profile).** For program $P$ and test suite $T$, define:

$$\sigma(P, T) = \left(\frac{|\text{Surv}_{C_1}(P,T)|}{|\text{Mut}_{C_1}(P)|}, \ldots, \frac{|\text{Surv}_{C_k}(P,T)|}{|\text{Mut}_{C_k}(P)|}\right) \in [0,1]^k$$

where $\text{Surv}_{C_i}(P,T) = \text{Surv}(P,T) \cap \text{Mut}_{C_i}(P)$. We use the convention that $0/0 = 0$ (no mutants in a category = fully specified in that category, vacuously).

**Definition 3.3 (Active Dimensions).** The *active dimension count* of $P$ with respect to $T$ is:

$$\text{dim}(P, T) = |\{i : \sigma_i(P, T) > 0 \text{ and } |\text{Mut}_{C_i}(P)| > 0\}|$$

This counts how many independent behavioral dimensions have specification gaps.

### 3.2 Independence of Categories

**Definition 3.4 (Category Independence).** Two categories $C_i, C_j$ are *independent relative to $(P, T)$* if:

$$\text{Surv}_{C_i}(P, T) \cap \text{Surv}_{C_j}(P, T) = \emptyset$$

(they share no surviving mutants — which is automatically true since categories partition $\mathcal{O}$, hence partition $\text{Mut}(P)$).

**Theorem 3.1 (Additive Decomposition of Survival).** Since categories partition the mutation space:

$$|\text{Surv}(P, T)| = \sum_i |\text{Surv}_{C_i}(P, T)|$$

$$\text{SC}(P, T) = \sum_i w_i \cdot (1 - \sigma_i(P, T))$$

where $w_i = |\text{Mut}_{C_i}(P)| / |\text{Mut}(P)|$ are the category weights.

*Proof*: Direct from the partition of $\text{Mut}(P)$. $\square$

**Remark.** This additivity is the formal basis for "surviving categories tell you along which axis to split." Each term in the sum is an independent contribution to the overall specification gap.

### 3.3 The Category Survival Profile as a Diagnostic Vector

**Theorem 3.2 (Profile Distinguishes Structural Pathologies).** Two functions with the same overall mutation score but different profiles have different structural pathologies:

If $\text{SC}(P_1, T_1) = \text{SC}(P_2, T_2)$ but $\sigma(P_1, T_1) \neq \sigma(P_2, T_2)$, then $P_1$ and $P_2$ require different decomposition strategies.

*Proof*: By construction of the category-to-prescription mapping (which is a design choice, not a mathematical necessity — but the mapping is injective: each category profile maps to a unique prescription). $\square$

**Definition 3.5 (Prescription Map).** Define $\Psi : [0,1]^k \to \mathcal{P}(\text{Refactorings})$ mapping survival profiles to sets of recommended decompositions:

| Profile Pattern | Prescription |
|----------------|-------------|
| $\sigma_\text{cond} > \theta$, others $\approx 0$ | Extract branches into independent functions |
| $\sigma_\text{arith} > \theta$, others $\approx 0$ | Extract computation into pure function with boundary tests |
| $\sigma_\text{str} > \theta$, others $\approx 0$ | Extract pattern registry with own contract |
| $\text{dim}(P,T) \geq 2$ | Function mixes concerns; decompose along active axes |

The threshold $\theta$ is a tunable parameter (empirically $\theta \approx 0.3$ for actionable prescriptions).

---

## 4. The Decomposition Conjecture

This is the central theoretical claim. We state it precisely, give the strongest version we believe is true, and identify the weakest version that suffices for the operational chain.

### 4.1 Definitions

**Definition 4.1 (Decomposition).** A *decomposition* of $f : D \to R$ is a tuple $(g_1, \ldots, g_k, h)$ where:
- Each $g_i : D_i \to R_i$ is a *component function*
- $h : R_1 \times \cdots \times R_k \to R$ is a *composition function*
- $f = h \circ (g_1, \ldots, g_k)$ (extensional equality on $D$)
- Each $g_i$ is "simpler" than $f$ in a sense to be defined

**Definition 4.2 (Algebraic Tractability).** A function $g : D \to R$ is *algebraically tractable* if there exists a finite set of equational laws $\mathcal{E} = \{e_1, \ldots, e_n\}$ (where each $e_i$ is a universally quantified equation over the signature of $g$) such that:
- $\mathcal{E}$ completely characterizes $g$'s behavior (any two implementations satisfying $\mathcal{E}$ are extensionally equal)
- $\mathcal{E}$ enables at least one equivalence-preserving optimization (memoization, reordering, parallelization, etc.)

**Remark.** This definition is deliberately broad. The Haskell tradition would restrict to specific equational classes (monoids, functors, etc.). We keep it general because the claim is about the *existence* of equational characterization, not about membership in a specific algebraic variety.

**Definition 4.3 (Specification-Complete).** $f$ is *specification-complete under $T$* if $\text{SC}(f, T) \geq 1 - \epsilon$ for some threshold $\epsilon$ (accounting for equivalent mutants). In the ideal case, $\text{Surv}(f, T) \subseteq \text{Equiv}(f)$.

### 4.2 The Strong Conjecture

**Conjecture 4.1 (Decomposition from Mutation Pressure — Strong Form).** Let $f : D \to R$ be a function with $\text{dim}(f, T) \geq 2$ (multi-category survival). Then there exists a decomposition $(g_1, \ldots, g_k, h)$ of $f$ such that:

(a) $k \leq \text{dim}(f, T)$  
(b) Each $g_i$ has $\text{dim}(g_i, T_i) \leq 1$ for an appropriate test suite $T_i$ derived from $T$  
(c) Each $g_i$ is algebraically tractable  
(d) $h$ is algebraically tractable (typically a simple composition — product, sequence, or dispatch)

### 4.3 The Weak Conjecture (Sufficient for the Operational Chain)

**Conjecture 4.2 (Decomposition from Mutation Pressure — Weak Form).** Let $f$ be a function with $\text{dim}(f, T) \geq 2$. Then there exists a decomposition $(g_1, \ldots, g_k, h)$ such that:

(a) $\sum_i |\text{Mut}(g_i)| + |\text{Mut}(h)| \leq |\text{Mut}(f)|$ (total mutation space does not increase)  
(b) $\text{SC}(h \circ (g_1, \ldots, g_k),\ T_1 \cup \cdots \cup T_k) > \text{SC}(f, T)$ for *some* test suites $T_i$ with $|T_i| \leq c \cdot |\text{Surv}_{C_i}(f, T)|$ for constant $c$  
(c) Each $g_i$ is more amenable to specification-completion than $f$ (fewer tests needed to reach full specification)

**Remark.** The weak form says: decomposition makes the specification gap easier to close, even if it doesn't immediately produce algebraically tractable units. This is the "mutation pressure forces progress" claim — each decomposition step strictly improves the situation.

### 4.4 Provable Special Cases

**Theorem 4.1 (Decomposition for Separable Functions).** Let $f : D_1 \times D_2 \to R$ be a function where $f(x_1, x_2) = h(g_1(x_1), g_2(x_2))$ for some $g_1, g_2, h$ (i.e., $f$ is *separable* — its inputs decompose into independent groups). Then:

(a) $\text{Mut}(f) \supseteq \text{Mut}(g_1) \sqcup \text{Mut}(g_2) \sqcup \text{Mut}(h)$  
(b) A mutant $m \in \text{Mut}(g_1)$ can be killed by a test that varies only $x_1$  
(c) $\text{dim}(f, T) \geq 2$ implies at least one of $g_1, g_2$ has specification gaps  
(d) Decomposing $f$ into $g_1, g_2, h$ makes each component independently testable

*Proof strategy*: (a) follows from the fact that mutation sites in the source of $g_1$ become mutation sites in $f$. (b) follows from separability — the output of $g_1$ depends only on $x_1$. (c) is direct. (d) is the key operational claim: independence of $g_1, g_2$ means tests for $g_1$ need not consider $g_2$'s behavior, reducing the test space combinatorially. $\square$

**Theorem 4.2 (Decomposition for Dispatching Functions).** Let $f$ contain a conditional dispatch:

```
f(x) = if p(x) then g_1(x) else g_2(x)
```

If $\sigma_\text{cond}(f, T) > 0$ (conditional mutants survive), then:

(a) At least one branch ($g_1$ or $g_2$) is not independently tested  
(b) Extracting $g_1, g_2$ as independent functions with their own test suites eliminates the conditional survivors  
(c) The specification gap in $f$ decomposes into independent specification gaps in the branches

*Proof strategy*: A conditional mutation (e.g., flipping the predicate) survives iff the test suite doesn't exercise both branches with inputs where the branch outcome matters. Extracting branches makes each independently testable; any conditional mutant of $f$ corresponds to a "wrong dispatch" that is caught by testing $g_1(x)$ and $g_2(x)$ independently. $\square$

### 4.5 The Entanglement Obstruction

**Definition 4.4 (Entangled Function).** $f$ is *entangled at category $(C_i, C_j)$* if there exist AST nodes $n_i \in C_i$ and $n_j \in C_j$ such that:
- Mutating $n_i$ alone survives
- Mutating $n_j$ alone survives
- BUT the combined effect of both mutations is caught by $T$

This means the behaviors are correlated in a way that single-category testing misses but multi-category testing catches.

**Theorem 4.3 (Entanglement Forces Decomposition).** If $f$ is entangled at $(C_i, C_j)$, then full specification of $f$ requires tests that exercise the *interaction* between the $C_i$ and $C_j$ behaviors. The number of such interaction tests is $O(|\text{Surv}_{C_i}| \times |\text{Surv}_{C_j}|)$ in the worst case.

Decomposing $f$ to separate the $C_i$ and $C_j$ concerns eliminates the interaction term, reducing the required test count from $O(n \times m)$ to $O(n + m)$.

*Proof sketch*: The interaction tests must cover the product space of $C_i \times C_j$ behaviors. Separation makes the spaces independent, reducing the product to a sum. This is the combinatorial argument for why tangled functions resist specification. $\square$

**Corollary 4.4 (Tangling IS the Specification Gap).** A function with $\text{dim}(f, T) \geq 2$ has a combinatorially large specification surface. Decomposition reduces the surface from multiplicative to additive. This is why multi-category survival diagnoses "doing too many things."

### 4.6 Where the Conjecture Fails

**Counter-Conjecture 4.5 (Irreducible Interaction).** There exist functions where the interaction between categories IS the computation, and decomposition destroys the semantic content. Examples:

(a) **Numerical methods**: Newton's method combines arithmetic and conditional logic (convergence check) in an inherently coupled way. Separating them destroys the algorithm.  
(b) **Neural network layers**: $\text{relu}(\mathbf{W}x + \mathbf{b})$ couples arithmetic (linear transform) and conditional (relu threshold) essentially.  
(c) **Parsing combinators**: The composition of parsing rules involves conditional branching (match/fail) and string manipulation (consume input) in a way that IS the parser's semantics.

**Theorem 4.6 (Characterization of Irreducible Functions).** A function $f$ is *irreducible with respect to mutation-guided decomposition* if:

(a) $\text{dim}(f, T) \geq 2$ (multi-category survival)  
(b) For every decomposition $(g_1, \ldots, g_k, h)$, at least one $g_i$ or $h$ has $\text{dim} \geq 2$  
(c) The full specification of $f$ is achievable (it is not irreducibly underspecified)

Such functions exist. They correspond to the "fuzzy" category in the LintGate taxonomy: code where the interaction between concerns is the creative/representational content, not an engineering artifact.

**Remark.** The practical claim is NOT that all functions decompose. It is that mutation-guided decomposition reliably separates the *decomposable* parts (engineering) from the *irreducible* parts (creative), and that this separation is itself a Pareto improvement.

---

## 5. The Orthogonal Lens Theorem

### 5.1 Information-Theoretic Setup

**Definition 5.1 (Specification Completeness as Latent Variable).** Let $\Sigma \in \{$UNSPECIFIED, ..., SPECIFIED$\}$ be the true specification level of a function $f$. This is the latent variable. We do not observe $\Sigma$ directly; we observe signals $X_1, \ldots, X_n$ from different analysis channels.

**Definition 5.2 (Signal Channels).** In the current system:
- $X_1 = \text{semantic\_ratio}(f)$ — assertion quality from test effectiveness analysis
- $X_2 = \text{weakness\_taxonomy}(f)$ — weakness classification
- $X_3 = \text{num\_categories}(f)$ — AST category count (behavioral dimensionality)
- $X_4 = \text{purity}(f)$ — pure/impure classification

Each $X_i$ is a noisy observation of $\Sigma$.

**Definition 5.3 (Conditional Independence).** Signals $X_i$ and $X_j$ are *conditionally independent given $\Sigma$* if:

$$P(X_i, X_j \mid \Sigma) = P(X_i \mid \Sigma) \cdot P(X_j \mid \Sigma)$$

This means that once you know the true specification level, knowing $X_i$ tells you nothing additional about $X_j$.

### 5.2 The Naive Bayes Model

**Theorem 5.1 (Naive Bayes Prediction).** Under the conditional independence assumption (Def 5.3), the posterior probability of specification level given all signals is:

$$P(\Sigma \mid X_1, \ldots, X_n) \propto P(\Sigma) \prod_{i=1}^n P(X_i \mid \Sigma)$$

*Proof*: Direct application of Bayes' theorem with the conditional independence factorization. $\square$

**Theorem 5.2 (Union Formula for Scope Reduction).** If each signal $X_i$ independently identifies under-specified functions with probability $p_i$ (true positive rate) and the signals are independent, then the probability that ALL signals miss an under-specified function is:

$$P(\text{all miss}) = \prod_{i=1}^n (1 - p_i)$$

Hence the combined detection rate is:

$$P(\text{at least one detects}) = 1 - \prod_{i=1}^n (1 - p_i)$$

For $n = 4$ signals each with $p_i = 0.6$: $1 - 0.4^4 = 1 - 0.0256 = 0.9744$.

*Proof*: Direct from independence and inclusion-exclusion. $\square$

**Remark.** The observed 94.8% is consistent with 3-4 signals at $p_i \in [0.55, 0.65]$.

### 5.3 Testing the Independence Assumption

**Theorem 5.3 (Mutual Information Test).** The conditional independence assumption can be validated empirically by computing:

$$I(X_i; X_j \mid \Sigma) = \sum_{\sigma, x_i, x_j} P(x_i, x_j, \sigma) \log \frac{P(x_i, x_j \mid \sigma)}{P(x_i \mid \sigma) P(x_j \mid \sigma)}$$

If $I(X_i; X_j \mid \Sigma) \approx 0$ for all pairs $(i, j)$, independence holds. If significantly positive, the Naive Bayes model overestimates combined detection power.

**Concrete Test Protocol.** On the LintGate codebase:
1. Run full mutation profiling on all functions to obtain ground-truth $\Sigma$
2. Compute all four signal values $X_1, \ldots, X_4$ for each function
3. Bin functions by true $\Sigma$ (using mutation score thresholds)
4. Within each bin, compute pairwise mutual information $I(X_i; X_j \mid \Sigma = \sigma)$
5. If all pairwise MIs are $< 0.1$ bits, independence is well-supported

### 5.4 The Value of the Next Signal

**Theorem 5.4 (Marginal Value of Additional Signal).** Given $n$ existing signals, the marginal value of an $(n+1)$th signal $X_{n+1}$ for scope reduction is maximized when:

$$I(X_{n+1}; \Sigma \mid X_1, \ldots, X_n)$$

is maximized. Under the Naive Bayes model, this simplifies to maximizing $I(X_{n+1}; \Sigma)$ (since the signals are conditionally independent, knowing the existing signals doesn't change the value of the new one).

*Proof*: Chain rule of mutual information plus conditional independence. $\square$

**Corollary 5.5.** The optimal next signal to add to the prediction layer is the one with the highest mutual information with specification completeness that is maximally *un*correlated with existing signals. Candidates:
- $\text{fan\_in}(f)$: measures caller-side dependency (orthogonal to internal structure)
- $\text{cochange\_freq}(f)$: measures coupling (orthogonal to static properties)

---

## 6. The Oracle Hierarchy

### 6.1 Definitions

**Definition 6.1 (Oracle Level).** An oracle $\pi : R \to \mathbb{B}$ has one of the following levels, forming a total order:

$$\text{CRASH} < \text{TYPE} < \text{STRUCTURAL} < \text{SHAPE} < \text{VALUE} < \text{PROPERTY}$$

| Level | What it checks | Example |
|-------|---------------|---------|
| CRASH | Program doesn't crash | `try: f(x)` (no assertion) |
| TYPE | Output has correct type | `assert isinstance(result, list)` |
| STRUCTURAL | Output has correct structure | `assert len(result) == 2` |
| SHAPE | Output has correct shape/format | `assert all(isinstance(x, int) for x in result)` |
| VALUE | Output has correct value | `assert result == [3, 7]` |
| PROPERTY | Output satisfies algebraic law | `assert f(a, b) == f(b, a)` (commutativity) |

**Definition 6.2 (Oracle Strength of a Kill).** When test $t$ kills mutant $m$, the *kill strength* is the oracle level of $t$'s oracle $\pi$. A VALUE kill is strictly stronger than a STRUCTURAL kill.

**Definition 6.3 (Specification Strength Vector).** The specification strength of $f$ under $T$ is:

$$\text{str}(f, T) = (\text{kills}_\text{crash}, \text{kills}_\text{type}, \text{kills}_\text{struct}, \text{kills}_\text{shape}, \text{kills}_\text{value}, \text{kills}_\text{prop})$$

normalized by $|\text{Mut}(f)|$.

### 6.2 Strength-Dependent Optimization Gating

**Theorem 6.1 (Optimization Prerequisites).** Each optimization type requires a minimum oracle level for safety:

| Optimization | Minimum Oracle Level | Justification |
|-------------|---------------------|---------------|
| Crash guard elimination | CRASH | Know it doesn't crash |
| Type specialization | TYPE | Know the output type |
| Container pre-allocation | STRUCTURAL | Know the output structure |
| Memoization/caching | VALUE | Must verify cached value is correct |
| Parallelization | PROPERTY | Must verify associativity/commutativity |

**Theorem 6.2 (Strength Monotonicity).** If optimization $O_1$ requires oracle level $L_1$ and $O_2$ requires $L_2$ with $L_1 < L_2$, then any test suite sufficient for $O_2$ is sufficient for $O_1$.

*Proof*: Since $L_1 < L_2$ and kills at level $L_2$ imply kills at all lower levels (a VALUE oracle detects anything a STRUCTURAL oracle detects), a suite with VALUE-level kills has at least as many kills as one with only STRUCTURAL kills. $\square$

**Remark.** This is the formalization of "purity without specification completeness is a false optimization signal." Purity lives at the STRUCTURAL level. Caching requires VALUE level. The cross-channel gate enforces this.

### 6.3 Strength Under Composition

**Theorem 6.3 (Composition Reduces Strength).** For $f = h \circ g$:

$$\text{oracle\_level}(f) \leq \min(\text{oracle\_level}(h), \text{oracle\_level}(g))$$

The specification strength of a composition is bounded by its weakest component.

*Proof sketch*: If $g$ is only specified at STRUCTURAL level, then $f = h \circ g$ cannot be specified above STRUCTURAL for the $g$-component of its behavior, regardless of how well $h$ is specified. A mutation to $g$ that preserves structure but changes values will propagate through $h$ undetected. $\square$

**Corollary 6.4 (Weakest Link Principle for Optimization).** An optimization applied to $f = h \circ g$ is safe only if BOTH $h$ and $g$ meet the oracle level prerequisite. This motivates bottom-up specification: specify leaf functions first, then compositions.

---

## 7. Convergence of the Synthesis Loop

### 7.1 Setup

**Definition 7.1 (Synthesis Operator).** Define $\mathcal{S} : \mathcal{P}(\text{Tests}) \times \mathcal{P}(\text{Mut}(P)) \to \mathcal{P}(\text{Tests}) \times \mathcal{P}(\text{Mut}(P))$ by:

$$\mathcal{S}(T, M) = (T \cup \text{gen}(M), M \setminus \text{killed}(T \cup \text{gen}(M)))$$

where $\text{gen}(M)$ generates new test inputs using mutation survival as fitness (mu2 pattern), and $\text{killed}(T')$ computes the set of mutants killed by $T'$.

### 7.2 Convergence

**Theorem 7.1 (Monotone Convergence).** The sequence $(T_0, M_0), (T_1, M_1), \ldots$ defined by $(T_{n+1}, M_{n+1}) = \mathcal{S}(T_n, M_n)$ satisfies:

(a) $T_0 \subseteq T_1 \subseteq T_2 \subseteq \cdots$ (test suite grows monotonically)  
(b) $M_0 \supseteq M_1 \supseteq M_2 \supseteq \cdots$ (surviving mutant set shrinks monotonically)  
(c) Both sequences converge in at most $|\text{Mut}(P)|$ steps (since each step either kills at least one mutant or terminates)

*Proof*: (a) $T_{n+1} \supseteq T_n$ by construction. (b) $M_{n+1} = M_n \setminus \text{killed}(\cdots) \subseteq M_n$. (c) $|M_n|$ is a non-negative integer that decreases by at least 1 at each productive step. If $|M_n| = |M_{n+1}|$, no new mutants were killed and the loop should terminate. $\square$

**Theorem 7.2 (Fixed Point Characterization).** The fixed point $(T^*, M^*)$ satisfies:

$$M^* = \{m \in \text{Mut}(P) : \text{gen}(M^*) \text{ cannot kill } m\}$$

If the test generator is *complete* (can generate a killing test for any non-equivalent mutant), then $M^* = \text{Equiv}(P)$.

If the test generator is *incomplete*, then $M^* \supseteq \text{Equiv}(P)$ and $M^* \setminus \text{Equiv}(P)$ represents mutants that are killable in principle but not by the generator.

### 7.3 Approximation Ratio

**Theorem 7.3 (Greedy Set Cover Approximation).** The problem of finding a minimal test suite that kills all non-equivalent mutants is equivalent to minimum set cover (each test kills a set of mutants; cover all killable mutants). The greedy algorithm (select the test that kills the most uncovered mutants) achieves an $O(\ln n)$ approximation ratio, where $n = |\text{Mut}(P) \setminus \text{Equiv}(P)|$.

*Proof*: Classical result on greedy set cover (Chvátal 1979). $\square$

**Corollary 7.4.** The mu2-style fitness function (prioritize inputs that kill more mutants) implements the greedy set cover heuristic. The resulting test suite is within a logarithmic factor of optimal.

---

## 8. The Cross-Channel Gate: Formal Specification

### 8.1 Gate Logic

**Definition 8.1 (Gate Function).** The cross-channel gate $G : \text{AlgebraResult} \times \text{MutationState} \to \text{GatedResult}$ is defined by:

$$G(\alpha, \mu) = \begin{cases}
(\alpha.\text{property}, \min(\alpha.\text{conf}, 0.90), \text{VERIFIED}) & \text{if } \mu.\text{survival} < 0.30 \\
(\alpha.\text{property}, \alpha.\text{conf} \times 0.50, \text{PENALIZED}) & \text{if } 0.30 \leq \mu.\text{survival} < 0.60 \\
(\alpha.\text{property}, 0.10, \text{GATED}) & \text{if } \mu.\text{survival} \geq 0.60
\end{cases}$$

### 8.2 Correctness Properties

**Theorem 8.1 (Gate Soundness).** If the algebra pipeline correctly identifies a property (purity, commutativity, etc.) and the mutation score correctly measures specification completeness, then the gate never approves an optimization that the specification level does not support.

*Formal statement*: Let $\alpha$ be a true algebraic property of $f$, and let $O(\alpha)$ be the optimization it enables. Let $L(O)$ be the minimum specification level required for $O$. Then:

$$G(\alpha, \mu).\text{status} = \text{VERIFIED} \implies \text{SC}(f, T) \geq L(O)$$

*Proof*: By definition, VERIFIED requires $\mu.\text{survival} < 0.30$, so $\text{SC} > 0.70 \geq L(O)$ for standard optimizations. $\square$

**Theorem 8.2 (Gate Conservatism).** The gate may reject safe optimizations (false negatives) but never approves unsafe ones (no false positives), assuming:
- The mutation engine is sound (non-killed mutants are genuinely indistinguishable under $T$)
- The algebra pipeline has no false positives for the property itself

*Proof sketch*: The gate can only increase confidence when mutation survival is low (specification is high). It can only decrease confidence when survival is high. Since unsafe optimizations correspond to applying a transform when specification is insufficient, and the gate reduces confidence monotonically with survival, it errs on the side of rejection. $\square$

---

## 9. The Monty Hall Filtering Theorem

### 9.1 Setup

The Monty Hall layer eliminates mutation categories before generating mutants, using existing static signals.

**Definition 9.1 (Category Prior).** For each category $C_i$ and function $f$, the prior probability of survivors is:

$$p_i(f) = P(\text{Surv}_{C_i}(f, T) \neq \emptyset \mid X_1(f), \ldots, X_n(f))$$

computed from the static signals.

**Definition 9.2 (Filtered Mutation Set).** Given threshold $\tau$:

$$\text{Mut}_\tau(f) = \bigcup_{i : p_i(f) > \tau} \text{Mut}_{C_i}(f)$$

### 9.2 Properties

**Theorem 9.1 (Scope Reduction Bound).** If the category priors are calibrated (i.e., $P(\text{survivors} \mid p_i < \tau) < \delta$ for small $\delta$), then:

$$\frac{|\text{Mut}_\tau(f)|}{|\text{Mut}(f)|} \leq \frac{|\{i : p_i(f) > \tau\}|}{k}$$

where $k = |\mathcal{C}|$ is the number of categories. With typical parameters ($\tau = 0.3$, 4 of 7 categories eliminated), this gives $\approx 43\%$ of mutations retained.

**Theorem 9.2 (Recall Guarantee).** If the priors are $\epsilon$-calibrated, then the probability of missing a true specification gap (filtering out a category with actual survivors) is bounded by $\epsilon$ per category, and by $k\epsilon$ overall (union bound).

*Proof*: Calibration means $P(\text{survivors} \mid p_i < \tau) < \epsilon$. For $k$ categories, the probability that ANY filtered category has survivors is $\leq k\epsilon$ by union bound. $\square$

**Remark.** This formalizes why the Monty Hall metaphor is apt: each filtered category is like Monty revealing a door with no prize. The elimination is informed (based on existing signals), not random, so it concentrates the remaining probability mass on the categories where survivors are likely.

---

## 10. Property-Based Testing and the Oracle Hierarchy

### 10.1 The Wrinkle

**Observation.** Property-based tests (e.g., QuickCheck/Hypothesis) test algebraic laws directly. A test `assert f(a, b) == f(b, a)` for commutativity may kill FEWER individual mutants than a comprehensive unit test suite, but proves a STRONGER property.

**Definition 10.1 (Property Kill vs. Value Kill).** A *property kill* kills a mutant by demonstrating that the mutant violates an algebraic law $\ell$. A *value kill* kills a mutant by demonstrating a specific input-output disagreement.

**Theorem 10.1 (Property Kills Dominate Value Kills).** A single property test at PROPERTY level that kills mutant $m$ provides strictly more specification information than a VALUE-level test killing the same $m$, because:

(a) The property test proves $m$ violates a universal law (all inputs)  
(b) The value test proves $m$ disagrees on a specific input  
(c) (a) implies (b) but not vice versa

**Theorem 10.2 (Non-Monotonicity of Kill Rate Under Property Testing).** It is possible that:

$$|T_\text{prop}| < |T_\text{value}|, \quad \text{kills}(T_\text{prop}) < \text{kills}(T_\text{value}), \quad \text{yet } T_\text{prop} \text{ provides stronger specification}$$

That is, kill rate alone does not capture specification strength when property tests are involved.

*Proof by example*: A commutativity test `f(a,b) == f(b,a)` for 5 random inputs kills only mutants that break commutativity for those 5 inputs. A suite of 50 unit tests with specific expected values kills more mutants total. But the commutativity test proves a universal property; the unit tests prove 50 point properties. $\square$

**Corollary 10.3 (Weighted Specification Completeness).** The specification completeness metric should be extended to:

$$\text{SC}_w(f, T) = \frac{\sum_{m \in \text{Killed}} w(\text{oracle\_level}(m, T))}{\sum_{m \in \text{Mut}(f)} w_\text{max}}$$

where $w : \text{OracleLevel} \to \mathbb{R}^+$ is a weighting function with $w(\text{PROPERTY}) > w(\text{VALUE}) > \cdots > w(\text{CRASH})$.

**Remark.** This addresses the gap in the core theory. The specification lattice should not be indexed purely by kill count but by *weighted* kill count reflecting oracle strength. This makes property-based tests properly valued: a single property test may contribute more specification completeness than 10 value tests.

---

## 11. Summary of Proof Obligations

### Theorems (Provable from Definitions)

| ID | Statement | Proof Strategy | Difficulty |
|----|-----------|---------------|------------|
| 1.1 | Refinement is a partial order | Set theory | Trivial |
| 1.2a | Killed-sets form a join-semilattice | Set theory | Easy |
| 1.2b | Meet not guaranteed in image | Counterexample | Easy |
| 1.3 | SC is monotone | Order theory | Trivial |
| 1.4 | Partition lattice embedding | Classical | Easy |
| 2.1 | Approximation hierarchy | Direct | Trivial |
| 2.2 | Convergence of approximation | Constructive | Easy |
| 2.4 | Equiv. mutant undecidable | Halting reduction | Standard |
| 3.1 | Additive survival decomposition | Partition counting | Trivial |
| 5.1 | Naive Bayes prediction | Bayes theorem | Standard |
| 5.2 | Union formula | Probability | Easy |
| 7.1 | Monotone convergence | Induction on |M| | Easy |
| 7.3 | Greedy set cover bound | Classical (Chvátal) | Standard |
| 8.1 | Gate soundness | Case analysis | Easy |
| 8.2 | Gate conservatism | Monotonicity | Easy |

### Conjectures (Require New Proof Techniques)

| ID | Statement | Status | Priority |
|----|-----------|--------|----------|
| 2.6 | Decomposition reduces equiv. mutants | Open | Medium |
| 4.1 | Strong decomposition conjecture | Open; likely false in full generality | High |
| 4.2 | Weak decomposition conjecture | Open; plausible | **Highest** |
| 4.3 | Entanglement forces decomposition | Partially proved (Thm 4.3) | High |
| 4.6 | Characterization of irreducible functions | Open | Medium |

### Empirical Validation Needed

| ID | Claim | Method |
|----|-------|--------|
| 5.3 | Signal independence | Mutual information on LintGate data |
| 5.4 | Marginal value of fan_in signal | Ablation study |
| 9.2 | Monty Hall recall guarantee | Calibration study on false negatives |
| 10.2 | Property tests vs. value tests | Comparative kill analysis |

---

## 12. Recommended Formalization Path

### Phase 1: Foundations (Lean 4 / Coq)

Formalize Definitions 1.1-1.10, Theorems 1.1-1.5. These are straightforward order theory and set theory. The partition lattice embedding (1.4) connects to existing mathlib results.

### Phase 2: The Gate Correctness

Formalize the gate function (Def 8.1) and prove soundness (8.1) and conservatism (8.2). This is case analysis on threshold values — mechanical but important for trust in the operational system.

### Phase 3: Convergence Theory

Formalize the synthesis loop (§7) and prove convergence. The key theorem (7.1) is induction on a finite decreasing sequence. The set cover connection (7.3) links to classical combinatorics.

### Phase 4: The Decomposition Conjecture

Attack Conjecture 4.2 (weak form) via the separable (4.1) and dispatching (4.2) special cases. These are the tractable sub-problems. If both prove out, attempt the general case or characterize the obstruction class (Counter-Conjecture 4.5).

### Phase 5: Information Theory

Compute mutual information (Thm 5.3 protocol) on real data. If independence holds, formalize the Naive Bayes model. If not, develop the correct graphical model.

---

## Appendix A: Connection to Existing Formal Libraries

| Concept | Lean 4 / Mathlib | Coq / MathComp |
|---------|-----------------|----------------|
| Partial orders | `Mathlib.Order.PartialOrder` | `order.v` |
| Lattices | `Mathlib.Order.Lattice` | `lattice.v` |
| Partition lattice | `Mathlib.Order.Partition` | Custom |
| Set cover | Custom | Custom |
| Mutual information | Custom | Custom |
| Halting problem | `Mathlib.Computability` | `undecidability/` |
| Bayes theorem | `Mathlib.Probability.Bayes` | Custom |

## Appendix B: Key Symbols

| Symbol | Meaning |
|--------|---------|
| $P$ | Program under test |
| $T$ | Test suite |
| $\text{Mut}(P)$ | Set of all first-order mutants |
| $\sim_T$ | Test-induced equivalence |
| $\text{SC}(P,T)$ | Specification completeness (= mutation score) |
| $\sigma(P,T)$ | Category survival profile vector |
| $\text{dim}(P,T)$ | Active dimension count |
| $\mathcal{D}(T,P)$ | Distinguishing power (= killed set) |
| $\preceq_P$ | Specification refinement order |
| $\text{Equiv}(P)$ | Set of equivalent mutants |
| $\text{str}(f,T)$ | Specification strength vector |
| $G(\alpha, \mu)$ | Cross-channel gate function |
