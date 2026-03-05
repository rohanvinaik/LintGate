# Specification Completeness Theory: Theorem Catalog

## Individually Provable Theorem Statements

**Prerequisite**: All theorems assume the definitions in `DEFINITIONS.md` are loaded as context.

**Organization**: Theorems are grouped by topic, ordered by dependency. Within each group, earlier theorems may be cited by later ones. Each theorem block is self-contained: it states exactly what it needs, what it claims, and a proof strategy.

**Difficulty scale**: ★ trivial, ★★ easy, ★★★ standard, ★★★★ requires care, ★★★★★ research-level

---
---

## GROUP A: Order Structure of Specification Refinement

These theorems establish the algebraic structure of the specification completeness ordering. They depend only on set theory and order theory.

---

### THEOREM A1 — Refinement is a Partial Order

**Difficulty**: ★

**Depends on**: Nothing (foundational)

**Statement**: The specification refinement relation $\preceq_P$ on $\mathcal{T}_P / \equiv_P$ is a partial order. That is, for all test suites $T_1, T_2, T_3 \in \mathcal{T}_P$:

(i) **Reflexivity**: $T \preceq_P T$  
(ii) **Transitivity**: $T_1 \preceq_P T_2 \wedge T_2 \preceq_P T_3 \implies T_1 \preceq_P T_3$  
(iii) **Antisymmetry** (on equivalence classes): $T_1 \preceq_P T_2 \wedge T_2 \preceq_P T_1 \implies \mathcal{D}(T_1, P) = \mathcal{D}(T_2, P)$

**Proof strategy**: Each property reduces to the corresponding property of $\subseteq$ on $\mathcal{P}(\text{Mut}(P))$:
- $\mathcal{D}(T, P) \subseteq \mathcal{D}(T, P)$ ✓
- $A \subseteq B \wedge B \subseteq C \implies A \subseteq C$ ✓
- $A \subseteq B \wedge B \subseteq A \implies A = B$ ✓

---

### THEOREM A2 — Killed-Sets Form a Join-Semilattice

**Difficulty**: ★★

**Depends on**: A1

**Statement**: Let $\mathcal{K}_P = \{ \mathcal{D}(T, P) : T \in \mathcal{T}_P \} \subseteq \mathcal{P}(\text{Mut}(P))$ be the set of achievable killed-sets. Then $(\mathcal{K}_P, \subseteq)$ is a join-semilattice with:

$$\mathcal{D}(T_1, P) \vee \mathcal{D}(T_2, P) = \mathcal{D}(T_1 \cup T_2, P)$$

Concretely: $\mathcal{D}(T_1 \cup T_2, P) = \mathcal{D}(T_1, P) \cup \mathcal{D}(T_2, P)$.

**Proof strategy**: Show both inclusions.

($\supseteq$): If $m \in \mathcal{D}(T_1, P)$, then $\exists t \in T_1$ with $t(m) \neq t(P)$. Since $t \in T_1 \cup T_2$, we have $m \in \mathcal{D}(T_1 \cup T_2, P)$. Symmetric for $T_2$.

($\subseteq$): If $m \in \mathcal{D}(T_1 \cup T_2, P)$, then $\exists t \in T_1 \cup T_2$ with $t(m) \neq t(P)$. Then $t \in T_1$ or $t \in T_2$, so $m \in \mathcal{D}(T_1, P) \cup \mathcal{D}(T_2, P)$.

Join is achieved by the concrete test suite $T_1 \cup T_2$, so the join is in $\mathcal{K}_P$.

---

### THEOREM A3 — Meet is Not Generally Achievable

**Difficulty**: ★★

**Depends on**: A1

**Statement**: The set $\mathcal{K}_P$ is NOT in general closed under intersection. That is, there exist programs $P$ and test suites $T_1, T_2$ such that no test suite $T$ satisfies $\mathcal{D}(T, P) = \mathcal{D}(T_1, P) \cap \mathcal{D}(T_2, P)$.

**Proof strategy (by counterexample)**: Construct a program $P$ with mutants $\{m_1, m_2, m_3\}$ and tests:

- $t_a$ kills $\{m_1, m_2\}$ (cannot separate them — a single input distinguishes both)
- $t_b$ kills $\{m_2, m_3\}$

Let $T_1 = \{t_a\}$, $T_2 = \{t_b\}$. Then $\mathcal{D}(T_1, P) \cap \mathcal{D}(T_2, P) = \{m_2\}$.

For $\mathcal{D}(T, P) = \{m_2\}$ to hold, we need a test suite $T$ that kills $m_2$ but NOT $m_1$ and NOT $m_3$. This requires a test $t^*$ such that $t^*(m_2) \neq t^*(P)$ but $t^*(m_1) = t^*(P)$ and $t^*(m_3) = t^*(P)$.

Whether such a $t^*$ exists depends on $P$. If $m_1$ and $m_2$ are mutations on the same expression (e.g., $m_1$ changes `+` to `-` and $m_2$ changes `+` to `*`), then any input that makes the expression's value matter will likely distinguish BOTH from $P$ — you cannot selectively kill $m_2$ alone.

The concrete construction: Let $P(x) = x + 1$, $m_1(x) = x - 1$, $m_2(x) = x * 1 = x$. Then for $x \neq 0$: $t_x$ kills both $m_1$ and $m_2$. For $x = 0$: $t_0$ kills $m_1$ (since $0 - 1 \neq 0 + 1$) but NOT $m_2$ (since $0 * 1 = 0 \neq 1$... wait, this DOES kill $m_2$). We need $m_2(x) = P(x)$ for our chosen $x$, i.e., $x = x + 1$, which has no solution. So $m_2$ is killed by EVERY input — there is no test that kills $m_2$ without killing $m_1$ because both differ from $P$ on all inputs. The intersection $\{m_2\}$ is not achievable.

**Note**: The specific counterexample requires careful construction. The key structural point is that individual tests are coarse instruments — killing one mutant at a site typically kills others at the same site as a side effect, making selective killing impossible.

---

### THEOREM A4 — Specification Completeness is Monotone

**Difficulty**: ★

**Depends on**: A1

**Statement**: $\text{SC}(P, \cdot) : (\mathcal{T}_P / \equiv_P,\ \preceq_P) \to ([0,1],\ \leq)$ is monotone:

$$T_1 \preceq_P T_2 \implies \text{SC}(P, T_1) \leq \text{SC}(P, T_2)$$

**Proof**: $T_1 \preceq_P T_2$ means $\mathcal{D}(T_1, P) \subseteq \mathcal{D}(T_2, P)$. Since these are finite sets, $|\mathcal{D}(T_1, P)| \leq |\mathcal{D}(T_2, P)|$. Both are divided by the same $|\text{Mut}(P)|$. $\square$

---

### THEOREM A5 — Level Classification is Monotone

**Difficulty**: ★

**Depends on**: A4, Axiom A6

**Statement**: The composition $\text{Level} \circ \text{SC}(P, \cdot) : \mathcal{T}_P \to \mathcal{L}$ is monotone:

$$T_1 \preceq_P T_2 \implies \text{Level}(\text{SC}(P, T_1)) \leq \text{Level}(\text{SC}(P, T_2))$$

**Proof**: Composition of monotone functions (A4 and A6) is monotone. $\square$

---

### THEOREM A6 — Specification Levels Form a Distributive Lattice

**Difficulty**: ★

**Depends on**: Nothing

**Statement**: $(\mathcal{L}, \leq)$ is a finite totally ordered set, hence a distributive lattice with:
- Meet: $L_i \wedge L_j = \min(L_i, L_j)$
- Join: $L_i \vee L_j = \max(L_i, L_j)$
- Bottom: $L_0$
- Top: $L_4$

**Proof**: Every finite total order is a distributive lattice (standard result). $\square$

---

### THEOREM A7 — Partition Lattice Embedding

**Difficulty**: ★★

**Depends on**: A2

**Statement**: For program $P$ and test suite $T$, let $\Pi(P, T)$ be the partition of $\text{Mut}(P)$ induced by $\sim_T$ (restricted to $\text{Mut}(P)$). The map $T \mapsto \Pi(P, T)$ embeds the specification refinement order into the partition lattice $\text{Part}(\text{Mut}(P))$, and the image is closed under joins:

$$\Pi(P, T_1 \cup T_2) = \Pi(P, T_1) \vee \Pi(P, T_2) \text{ in } \text{Part}(\text{Mut}(P))$$

where $\vee$ in the partition lattice is the finest partition coarser than both (i.e., the join of the two partitions).

**Proof strategy**: Adding tests can only make previously-equivalent mutants distinguishable (finer partition). The join of partitions in the partition lattice is exactly what happens when you combine distinguishing capabilities. The image is closed under joins because $T_1 \cup T_2$ is a valid test suite.

---
---

## GROUP B: Observational Equivalence and Approximation

These theorems connect test-based equivalence to true extensional equivalence. They clarify when and how finite testing approximates semantic truth.

---

### THEOREM B1 — Test Equivalence Coarsens True Equivalence

**Difficulty**: ★

**Depends on**: Nothing

**Statement**: For any test suite $T$:

$$P_1 \cong P_2 \implies P_1 \sim_T P_2$$

The converse fails in general.

**Proof**: If $\forall x \in D,\ P_1(x) = P_2(x)$, then for any test $t = (x_i, \pi_i)$: $\pi_i(P_1(x_i)) = \pi_i(P_2(x_i))$. Hence $t(P_1) = t(P_2)$ for all $t \in T$.

Converse failure: Let $P_1(x) = x$, $P_2(x) = x$ for $x \neq 42$ and $P_2(42) = 0$. Let $T = \{(0, \lambda r.[r=0])\}$. Then $P_1 \sim_T P_2$ but $P_1 \not\cong P_2$. $\square$

---

### THEOREM B2 — Convergence of Finite Test Approximation

**Difficulty**: ★★

**Depends on**: B1

**Statement**: Let $\mathcal{T}^\infty_P$ be the set of all finite test suites for $P$. Assume output equality on $R$ is decidable. Then for programs $P_1, P_2 : D \to R$:

$$\left(\forall T \in \mathcal{T}^\infty_P,\ P_1 \sim_T P_2\right) \iff P_1 \cong P_2$$

**Proof**: ($\Leftarrow$) is Theorem B1. ($\Rightarrow$) by contrapositive: if $P_1 \not\cong P_2$, then $\exists x^* \in D$ with $P_1(x^*) \neq P_2(x^*)$. Since equality on $R$ is decidable, define $\pi^*(r) = [r = P_1(x^*)]$. Then $t^* = (x^*, \pi^*)$ satisfies $t^*(P_1) = 1 \neq 0 = t^*(P_2)$. So $P_1 \not\sim_{\{t^*\}} P_2$. $\square$

---

### THEOREM B3 — Surviving Non-Equivalent Mutants Are Specification Gaps

**Difficulty**: ★

**Depends on**: B2

**Statement (Corollary of B2)**: Every surviving mutant that is NOT truly equivalent can be killed by some finite test extension:

$$m \in \text{Surv}(P, T) \wedge m \notin \text{Equiv}(P) \implies \exists T' \supseteq T \text{ such that } m \in \text{Killed}(P, T')$$

**Proof**: If $m \not\cong P$, then by the contrapositive direction of B2, there exists a singleton test $\{t^*\}$ that distinguishes them. Set $T' = T \cup \{t^*\}$. $\square$

---

### THEOREM B4 — Equivalent Mutant Detection is Undecidable

**Difficulty**: ★★★

**Depends on**: Nothing (uses computability theory)

**Statement**: For a Turing-complete language $\mathcal{L}$, the problem "given $P$ and $m \in \text{Mut}(P)$, is $m \cong P$?" is undecidable.

**Proof**: Reduction from the halting problem. Given Turing machine $M$ and input $w$, construct:
- $P(x) = 0$ for all $x$
- $m(x) =$ simulate $M$ on $w$ for $|x|$ steps; if $M$ halts within $|x|$ steps, return 1; else return 0.

Then $m \cong P$ iff $M$ does not halt on $w$. If we could decide $m \cong P$, we could decide halting. Contradiction. $\square$

---

### THEOREM B5 — Equivalent Mutant Decidability for Finite Domains

**Difficulty**: ★★

**Depends on**: Nothing

**Statement**: For pure functions $f : D \to R$ where $D$ is finite, equivalent mutant detection is decidable: there exists an algorithm that, given $P$ and $m$, determines whether $m \cong P$ in $O(|D|)$ time.

**Proof**: Enumerate all $x \in D$. Compute $P(x)$ and $m(x)$. If any differ, $m \not\cong P$ (and $x$ is a killing test input). If all agree, $m \cong P$. Terminates because $D$ is finite. $\square$

---
---

## GROUP C: Additive Structure of Mutation Categories

These theorems formalize how the category partition decomposes specification completeness into independent components.

---

### THEOREM C1 — Additive Decomposition of Survival

**Difficulty**: ★

**Depends on**: Axiom A2 (category partition)

**Statement**: Since categories partition $\text{Mut}(P)$:

$$|\text{Surv}(P, T)| = \sum_{i=1}^{k} |\text{Surv}_{C_i}(P, T)|$$

$$\text{SC}(P, T) = \sum_{i=1}^{k} w_i \cdot (1 - \sigma_i(P, T))$$

where $w_i = |\text{Mut}_{C_i}(P)| / |\text{Mut}(P)|$.

**Proof**: The first equation follows from $\text{Surv}(P, T) = \bigsqcup_i \text{Surv}_{C_i}(P, T)$ (disjoint union from A2). For the second:

$$\text{SC} = \frac{|\text{Killed}|}{|\text{Mut}|} = \frac{\sum_i |\text{Killed}_{C_i}|}{|\text{Mut}|} = \sum_i \frac{|\text{Mut}_{C_i}|}{|\text{Mut}|} \cdot \frac{|\text{Killed}_{C_i}|}{|\text{Mut}_{C_i}|} = \sum_i w_i(1 - \sigma_i)$$

$\square$

---

### THEOREM C2 — Profile Determines Diagnostic Category

**Difficulty**: ★★

**Depends on**: C1

**Statement**: For any two functions $f_1, f_2$ with $\text{SC}(f_1, T_1) = \text{SC}(f_2, T_2)$ but $\sigma(f_1, T_1) \neq \sigma(f_2, T_2)$, the functions differ in which behavioral dimensions are underspecified.

Formally: there exists an index $j$ such that $\sigma_j(f_1, T_1) \neq \sigma_j(f_2, T_2)$, meaning $f_1$ and $f_2$ have different specification gaps in category $C_j$.

**Proof**: Direct from the definition of $\sigma$: if $\sigma(f_1, T_1) \neq \sigma(f_2, T_2)$ as vectors, they differ in at least one component. $\square$

**Remark**: This is trivially true from definitions but operationally important: it justifies using the *profile* rather than the *scalar* SC for diagnostic purposes.

---

### THEOREM C3 — Multi-Dimensional Survival Implies Mixed Concerns

**Difficulty**: ★★

**Depends on**: C1

**Statement**: If $\text{dim}(f, T) \geq 2$, then $f$ has surviving mutants in at least two syntactically distinct categories. Equivalently, the test suite $T$ fails to specify $f$'s behavior along at least two independent syntactic axes.

**Proof**: $\text{dim}(f, T) \geq 2$ means $|\{i : \sigma_i > 0 \wedge |\text{Mut}_{C_i}| > 0\}| \geq 2$. So there exist distinct $C_j, C_k$ with non-empty surviving mutants in each. Since $C_j \cap C_k = \emptyset$ (partition), these are syntactically independent. $\square$

---
---

## GROUP D: Decomposition Theory

These theorems address when and how multi-dimensional survival profiles can be resolved by decomposing functions. Contains both provable special cases and open conjectures.

---

### THEOREM D1 — Decomposition for Separable Functions

**Difficulty**: ★★★

**Depends on**: C1, C3

**Statement**: Let $f : D_1 \times D_2 \to R$ be separable, i.e., $f(x_1, x_2) = h(g_1(x_1), g_2(x_2))$. Then:

(a) $\text{Mut}(f) \supseteq \text{Mut}(g_1) \sqcup \text{Mut}(g_2) \sqcup \text{Mut}(h)$ (mutation sites in components appear in the composite)

(b) For any $m \in \text{Mut}(g_1)$ with $m \not\cong g_1$: there exists a test $t = ((x_1, x_2), \pi)$ that kills $m$-embedded-in-$f$, where $\pi$ depends only on $g_1(x_1)$ (not on $x_2$). Specifically, $x_2$ can be held fixed at any value.

(c) If $\text{dim}(f, T) \geq 2$ with surviving mutants in the syntactic regions corresponding to $g_1$ and $g_2$ respectively, then the specification gaps in $g_1$ and $g_2$ are independently closable: tests for $g_1$ need not consider $g_2$'s behavior and vice versa.

**Proof sketch**:

(a): Any mutation site in the source of $g_1$ is a mutation site in $f$ (since $g_1$'s source appears within $f$'s source). The disjointness follows from $g_1, g_2, h$ operating on disjoint source regions.

(b): Since $m \not\cong g_1$, $\exists x_1^*$ with $m(x_1^*) \neq g_1(x_1^*)$. Fix any $x_2^0 \in D_2$. Then $f_m(x_1^*, x_2^0) = h(m(x_1^*), g_2(x_2^0)) \neq h(g_1(x_1^*), g_2(x_2^0)) = f(x_1^*, x_2^0)$ (assuming $h$ is injective in its first argument on the relevant values — this is where the proof needs care; the general statement requires $h$ to "propagate" the difference).

(c): Independence follows from (b): to kill mutants in $g_1$, vary $x_1$ with $x_2$ fixed. To kill mutants in $g_2$, vary $x_2$ with $x_1$ fixed. The test sets are independent. $\square$

**Caveat on (b)**: The statement assumes $h$ doesn't "absorb" the difference between $m(x_1^*)$ and $g_1(x_1^*)$. This fails if $h$ is, e.g., a constant function. Formally, we need: $h(a, b) \neq h(a', b)$ when $a \neq a'$ for the relevant values. This is the non-degeneracy condition on the composition.

---

### THEOREM D2 — Decomposition for Dispatching Functions

**Difficulty**: ★★★

**Depends on**: C1

**Statement**: Let $f(x) = \text{if } p(x) \text{ then } g_1(x) \text{ else } g_2(x)$. If conditional mutants survive (i.e., $\sigma_\text{cond}(f, T) > 0$), then:

(a) The test suite $T$ does not exercise both branches with inputs where the branch outcome matters for the oracle. Formally: there exists a branch (say $g_1$) such that either no test has an input satisfying $p(x)$, or the oracle $\pi$ for such tests does not depend on the specific value of $g_1(x)$.

(b) Extracting $g_1, g_2$ as independent functions eliminates conditional survivors: every conditional mutation of $f$ (flipping or modifying $p$) that survived under $T$ is killed by a test suite $T'$ containing tests for $g_1$ and $g_2$ with value-level oracles, where $|T'| \leq |\text{Surv}_\text{cond}(f, T)| + 2$.

(c) The total test count required to specify $f$ decreases: $|T_\text{decomposed}| \leq |T_\text{monolithic}|$ where $T_\text{monolithic}$ is a minimal test suite killing all non-equivalent mutants of $f$ and $T_\text{decomposed} = T_{g_1} \cup T_{g_2} \cup T_p$ (separate suites for each component).

**Proof sketch**:

(a): A conditional mutant changes the predicate $p$ to $p'$. If this mutant survives, then for all test inputs $x_i$: either $p(x_i) = p'(x_i)$ (the mutation doesn't affect the branch taken), or $p(x_i) \neq p'(x_i)$ but $g_1(x_i) = g_2(x_i)$ (both branches produce the same output), or the oracle doesn't check the output value precisely enough to notice.

(b): With $g_1, g_2$ extracted: a conditional mutation routes input $x$ to the wrong branch. A value-level test for $g_1$ (asserting $g_1(x) = v_1$) and for $g_2$ (asserting $g_2(x) = v_2$) on an input where $v_1 \neq v_2$ will catch the mis-routing. We need at least one such input per conditional mutation site. Since conditional mutations affect the predicate, and the predicate typically has $O(1)$ mutation sites, $O(1)$ additional tests suffice.

(c): The monolithic suite must cover all combinations of (branch × behavior). The decomposed suite covers each independently: $|T_p| + |T_{g_1}| + |T_{g_2}| \leq |T_p| \cdot |T_{g_1}| + |T_p| \cdot |T_{g_2}|$ in the worst case for the monolithic approach. The savings come from eliminating the cross-product.

---

### THEOREM D3 — Entanglement Creates Multiplicative Specification Surface

**Difficulty**: ★★★★

**Depends on**: C1, C3

**Statement**: Let $f$ have $\text{dim}(f, T) = d$ with surviving mutant counts $n_1, \ldots, n_d$ in the $d$ active categories. If the categories are *entangled* in $f$ (meaning the behaviors they govern interact within $f$'s computation), then:

(a) A complete specification of $f$ (killing all non-equivalent mutants) requires, in the worst case, $\Omega(\prod_{i=1}^d n_i)$ distinct tests — the product of the per-category surviving mutant counts.

(b) If $f$ is decomposed into components $g_1, \ldots, g_d$ such that each $g_i$ contains the computation governed by category $C_i$ alone, then complete specification requires at most $O(\sum_{i=1}^d n_i)$ tests — the sum.

(c) The ratio of multiplicative to additive test counts is $\frac{\prod n_i}{\sum n_i}$, which grows exponentially in $d$ for $n_i > 1$.

**Proof sketch**:

(a): The interaction between categories means that the behavior of a category-$i$ mutation may depend on the state produced by category-$j$ code. To verify all combinations, we need the cross product of inputs exercising each category.

(b): After decomposition, each $g_i$ is independently testable. The tests for $g_i$ need not consider $g_j$'s behavior ($i \neq j$), because the decomposition has separated the concerns.

(c): Arithmetic. For $d = 2$ with $n_1 = n_2 = n$: $\frac{n^2}{2n} = n/2$. For $d = 3$: $\frac{n^3}{3n} = n^2/3$. Exponential in $d$. $\square$

---

### CONJECTURE D4 — Weak Decomposition from Mutation Pressure

**Difficulty**: ★★★★★ (Open)

**Depends on**: D1, D2, D3

**Statement**: Let $f$ be a function with $\text{dim}(f, T) \geq 2$. Then there exists a decomposition $(g_1, \ldots, g_n, h)$ of $f$ such that:

(a) $\sum_i |\text{Mut}(g_i)| + |\text{Mut}(h)| \leq |\text{Mut}(f)|$ (mutation space does not increase)

(b) $\text{SC}(h \circ (g_1, \ldots, g_n),\ T') > \text{SC}(f, T)$ for some $T' \supseteq T$ with $|T' \setminus T| \leq c \cdot \sum_i |\text{Surv}_{C_i}(f, T)|$ for constant $c$

(c) $\max_i \text{dim}(g_i, T_i) < \text{dim}(f, T)$ for appropriate test suites $T_i$

**Status**: Open. Provable for the special cases of separable functions (D1) and dispatching functions (D2). The general case requires showing that multi-dimensional survival always implies decomposability, which may fail for irreducibly entangled computations (see Counter-Conjecture D6).

**Attack strategy**: Prove (a) by showing mutation operators act locally (a mutation to source region of $g_i$ becomes a mutation of $g_i$). Prove (b) using D3 — the multiplicative-to-additive reduction means fewer tests are needed. Prove (c) by showing each component inherits at most one category from $f$.

---

### CONJECTURE D5 — Strong Decomposition Conjecture

**Difficulty**: ★★★★★ (Open, likely false in full generality)

**Depends on**: D4

**Statement**: Under the conditions of D4, additionally:
- Each $g_i$ is algebraically tractable (admits a finite equational specification enabling at least one equivalence-preserving optimization)
- $h$ is algebraically tractable

**Status**: Open. Believed to hold for the "SICP-style" code class (pure computation of definite values) but fail for "fuzzy" code (representational/creative functions where the interaction IS the computation). See D6 for the obstruction.

---

### CONJECTURE D6 — Existence of Irreducible Functions

**Difficulty**: ★★★★ (Counterexample construction)

**Depends on**: Nothing

**Statement**: There exist functions $f$ such that:

(a) $\text{dim}(f, T) \geq 2$ for any reasonable test suite $T$

(b) For every decomposition $(g_1, \ldots, g_n, h)$ of $f$, at least one $g_i$ or $h$ has $\text{dim} \geq 2$

(c) $f$ is fully specifiable (is not inherently ambiguous — $\text{Equiv}(f)$ does not account for all survivors)

**Candidate counterexamples**:
- Newton's method: couples arithmetic (iteration) and conditional (convergence check) inherently
- ReLU layer: $\text{relu}(Wx + b)$ couples linear algebra and conditional thresholding
- Parser combinators: couple string matching and conditional branching essentially

**Significance**: If such functions exist (likely), they bound the decomposition conjecture. They correspond to the "fuzzy" category — code whose multi-category structure IS the computation. The practical claim becomes: mutation-guided decomposition separates the decomposable parts from these irreducible cores.

---
---

## GROUP E: Information Theory of the Prediction Layer

These theorems formalize the Orthogonal Lens result — why combining independent static signals achieves high scope reduction.

---

### THEOREM E1 — Naive Bayes Posterior

**Difficulty**: ★★

**Depends on**: Nothing (probability theory)

**Statement**: Under the conditional independence assumption (signals $X_1, \ldots, X_n$ are conditionally independent given $\Sigma$), the posterior is:

$$P(\Sigma = \sigma \mid X_1 = x_1, \ldots, X_n = x_n) = \frac{P(\Sigma = \sigma) \prod_{i=1}^n P(X_i = x_i \mid \Sigma = \sigma)}{\sum_{\sigma'} P(\Sigma = \sigma') \prod_{i=1}^n P(X_i = x_i \mid \Sigma = \sigma')}$$

**Proof**: Bayes' theorem: $P(\Sigma \mid \mathbf{X}) \propto P(\mathbf{X} \mid \Sigma) P(\Sigma)$. Conditional independence: $P(\mathbf{X} \mid \Sigma) = \prod_i P(X_i \mid \Sigma)$. Normalization gives the denominator. $\square$

---

### THEOREM E2 — Union Formula for Independent Detection

**Difficulty**: ★★

**Depends on**: Nothing (probability theory)

**Statement**: If each signal $X_i$ independently detects underspecified functions with true positive rate $p_i$, and the signals are mutually independent, then:

$$P(\text{at least one signal detects}) = 1 - \prod_{i=1}^n (1 - p_i)$$

For $n = 4$ signals with $p_i = 0.6$: $1 - 0.4^4 = 0.9744$.

**Proof**: $P(\text{all miss}) = \prod_i P(X_i \text{ misses}) = \prod_i (1 - p_i)$ by independence. Complement gives detection probability. $\square$

---

### THEOREM E3 — Mutual Information Test for Independence

**Difficulty**: ★★★

**Depends on**: E1

**Statement**: The conditional independence assumption can be validated by computing pairwise conditional mutual information:

$$I(X_i; X_j \mid \Sigma) = \sum_{\sigma, x_i, x_j} P(x_i, x_j, \sigma) \log \frac{P(x_i, x_j \mid \sigma)}{P(x_i \mid \sigma) P(x_j \mid \sigma)}$$

The following hold:
(a) $I(X_i; X_j \mid \Sigma) \geq 0$ with equality iff $X_i \perp X_j \mid \Sigma$
(b) If $I(X_i; X_j \mid \Sigma) < \epsilon$ for all pairs and small $\epsilon$, the Naive Bayes model's error is bounded by a function of $\epsilon$

**Proof**: (a) is a classical information theory result (non-negativity of conditional MI, with equality iff conditional independence). (b) requires bounding the KL divergence between the true joint $P(X_1, \ldots, X_n \mid \Sigma)$ and the Naive Bayes approximation $\prod_i P(X_i \mid \Sigma)$ in terms of the pairwise MI terms — this uses the interaction information decomposition. $\square$

---

### THEOREM E4 — Marginal Value of Next Signal

**Difficulty**: ★★★

**Depends on**: E1, E3

**Statement**: Given $n$ existing signals, the information gain from adding signal $X_{n+1}$ is:

$$\Delta I = I(X_{n+1}; \Sigma \mid X_1, \ldots, X_n)$$

Under conditional independence, this simplifies to:

$$\Delta I = I(X_{n+1}; \Sigma)$$

The optimal next signal maximizes $I(X_{n+1}; \Sigma)$ subject to $I(X_{n+1}; X_i \mid \Sigma) \approx 0$ for all existing $i$.

**Proof**: Chain rule: $I(X_1, \ldots, X_{n+1}; \Sigma) = I(X_1, \ldots, X_n; \Sigma) + I(X_{n+1}; \Sigma \mid X_1, \ldots, X_n)$. Under conditional independence, $I(X_{n+1}; \Sigma \mid X_1, \ldots, X_n) = I(X_{n+1}; \Sigma)$ because knowing $X_1, \ldots, X_n$ provides no additional information about $X_{n+1}$ beyond what $\Sigma$ provides. $\square$

---
---

## GROUP F: Oracle Hierarchy and Optimization Gating

These theorems formalize the relationship between oracle strength, specification quality, and optimization safety.

---

### THEOREM F1 — Oracle Level Monotonicity Under Kill

**Difficulty**: ★★

**Depends on**: Axiom A4

**Statement**: If oracle $\pi_1$ at level $\ell_1$ kills mutant $m$ (i.e., $\pi_1(m(x)) \neq \pi_1(P(x))$), and $\pi_2$ is at level $\ell_2 > \ell_1$, then $\pi_2$ also kills $m$ on the same input (i.e., $\pi_2(m(x)) \neq \pi_2(P(x))$).

**Proof**: Direct from Axiom A4 (higher oracles are strictly more discriminating). $\square$

**Remark**: This axiom is actually a modeling choice that holds for the standard oracle levels (a VALUE check distinguishes anything a TYPE check distinguishes) but may fail for exotic oracle definitions. The theorem is trivial given A4; the work is in justifying A4 for the specific oracle taxonomy.

---

### THEOREM F2 — Optimization Prerequisite Soundness

**Difficulty**: ★★

**Depends on**: F1

**Statement**: Define the optimization prerequisite function $\text{req} : \text{Optimizations} \to \mathcal{O}_L$ mapping each optimization to its minimum required oracle level. Then for any optimization $O$:

If $\text{SC}_{\geq \ell}(f, T) \geq \theta$ (where $\text{SC}_{\geq \ell}$ counts only kills at oracle level $\geq \ell = \text{req}(O)$), then applying $O$ is safe (behavior-preserving under the tested specification).

Contrapositively: if $\text{SC}_{\geq \ell}(f, T) < \theta$, the system cannot guarantee that $O$ preserves the function's behavior on untested inputs.

**Proof sketch**: An optimization $O$ is safe if the algebraic property it exploits holds for $f$. The property is verified by kills at oracle level $\geq \text{req}(O)$. If sufficient fraction of mutants are killed at this level, the property is verified with high confidence. If not, the property may not hold and $O$ is unsafe. $\square$

---

### THEOREM F3 — Composition Reduces Specification Strength

**Difficulty**: ★★★

**Depends on**: F1

**Statement**: For composed function $f = h \circ g$, the effective oracle level for $f$ is bounded:

$$\text{eff\_level}(f) \leq \min(\text{eff\_level}(h), \text{eff\_level}(g))$$

where $\text{eff\_level}(f) = \max\{\ell : \text{SC}_{\geq \ell}(f, T) \geq \theta\}$.

**Proof sketch**: A mutation to $g$ that preserves structure but changes values (surviving at VALUE level) propagates through $h$. If $g$ is only specified at STRUCTURAL level, then $f$'s specification of the $g$-component is at most STRUCTURAL, regardless of how well $h$ is specified.

The key step: let $m_g$ be a mutant of $g$ surviving at level $\ell_g$ (not killed by any oracle at level $> \ell_g$). Then $h \circ m_g$ is a mutant of $f$ that also survives at level $\ell_g$, because the oracle for $f$ at level $> \ell_g$ would need to detect the difference $m_g(x) \neq g(x)$, which is exactly what the oracle at level $> \ell_g$ for $g$ fails to do, and $h$ cannot amplify oracle level (it can only propagate or absorb differences). $\square$

---
---

## GROUP G: Cross-Channel Gate Correctness

These theorems verify the correctness properties of the gate that combines algebra analysis with mutation analysis.

---

### THEOREM G1 — Gate Soundness

**Difficulty**: ★★

**Depends on**: Gate function definition (§1.13), F2

**Statement**: If the algebra pipeline correctly identifies property $\alpha$ and the mutation engine correctly measures survival rate $\mu.\text{survival}$, then:

$$G(\alpha, \mu).\text{status} = \text{VERIFIED} \implies \text{SC}(f, T) \geq 0.70$$

That is, the gate never labels an optimization as VERIFIED unless specification completeness exceeds 70%.

**Proof**: By the definition of $G$: VERIFIED requires $\mu.\text{survival} < 0.30$. Since $\text{SC} = 1 - \mu.\text{survival}$, $\text{SC} > 0.70$. $\square$

---

### THEOREM G2 — Gate Conservatism (No False Positives)

**Difficulty**: ★★

**Depends on**: G1

**Statement**: Assume:
- (S1) The mutation engine is sound: $\text{Surv}(f, T)$ contains only mutants genuinely indistinguishable from $f$ under $T$.
- (S2) The algebra pipeline has no false positives: if it reports property $\alpha$, then $f$ genuinely has property $\alpha$ (modulo the specification gap).

Then the gate has no false positives: if $G(\alpha, \mu).\text{status} = \text{VERIFIED}$, then applying the optimization licensed by $\alpha$ is safe with confidence $\geq 0.90 \cdot P(\alpha \text{ holds})$.

**Proof**: The gate outputs $\text{conf} = \min(\alpha.\text{conf}, 0.90)$ in the VERIFIED case. Combined with (S2), this confidence reflects the algebra pipeline's confidence that the property holds, capped at 0.90. The mutation verification ($\text{survival} < 0.30$) ensures the property claim is backed by behavioral evidence.

The gate can produce false negatives (GATED on a function that is actually safe to optimize) but not false positives: it only increases confidence when specification completeness is high, and only decreases confidence when survival is high. Since unsafe optimization corresponds to acting on insufficient specification, and the gate monotonically reduces confidence with increasing survival, the false-positive rate is zero (assuming S1 and S2). $\square$

---

### THEOREM G3 — Gate Monotonicity

**Difficulty**: ★

**Depends on**: Gate function definition

**Statement**: The output confidence of $G$ is monotonically decreasing in survival rate:

$$\mu_1.\text{survival} \leq \mu_2.\text{survival} \implies G(\alpha, \mu_1).\text{conf} \geq G(\alpha, \mu_2).\text{conf}$$

**Proof**: Case analysis on the three threshold intervals. Within each interval, confidence is either constant (VERIFIED: $\min(\alpha.\text{conf}, 0.90)$; GATED: $0.10$) or linearly decreasing (PENALIZED: $\alpha.\text{conf} \times 0.50$). At boundaries, confidence decreases: VERIFIED → PENALIZED drops from $\min(c, 0.90)$ to $c \times 0.50 \leq 0.50 \leq 0.90$; PENALIZED → GATED drops from $c \times 0.50$ to $0.10$. $\square$

---
---

## GROUP H: Convergence of the Synthesis Loop

These theorems establish that the test synthesis feedback loop terminates and approximates optimality.

---

### THEOREM H1 — Monotone Convergence of Synthesis

**Difficulty**: ★★

**Depends on**: Synthesis operator definition (§1.15)

**Statement**: The sequence $(T_n, M_n)$ defined by $(T_0, M_0) = (T, \text{Surv}(P, T))$ and $(T_{n+1}, M_{n+1}) = \mathcal{S}(T_n, M_n)$ satisfies:

(a) $T_0 \subseteq T_1 \subseteq T_2 \subseteq \cdots$ (test suites grow)

(b) $M_0 \supseteq M_1 \supseteq M_2 \supseteq \cdots$ (surviving sets shrink)

(c) The sequence converges in at most $|M_0|$ productive steps: if $|M_n| = |M_{n+1}|$ then $M_n = M_{n+1}$ and the sequence has stabilized.

**Proof**:
(a): $T_{n+1} = T_n \cup \text{gen}(M_n) \supseteq T_n$ by set union.

(b): $M_{n+1} = M_n \setminus \text{Killed}(P, T_{n+1}) \subseteq M_n$ since we only remove elements.

(c): $|M_n|$ is a non-negative integer. If $|M_{n+1}| < |M_n|$, at least one mutant was killed. If $|M_{n+1}| = |M_n|$, no new mutants were killed ($\text{gen}(M_n)$ produced no useful tests), and $M_{n+1} = M_n$. The sequence of $|M_n|$ values is non-increasing and bounded below by 0, so it stabilizes in at most $|M_0| \leq |\text{Mut}(P)|$ productive steps. $\square$

---

### THEOREM H2 — Fixed Point Characterization

**Difficulty**: ★★★

**Depends on**: H1

**Statement**: The fixed point $(T^*, M^*)$ of the synthesis loop satisfies:

$$M^* = \{m \in \text{Surv}(P, T) : \text{no test generable by } \text{gen} \text{ kills } m\}$$

Furthermore:

(a) If $\text{gen}$ is complete (Definition §1.15), then $M^* \subseteq \text{Equiv}(P)$ — the only surviving mutants at the fixed point are equivalent mutants.

(b) If $\text{gen}$ is incomplete, then $M^* = \text{Equiv}(P) \cup \text{Unkillable}_\text{gen}(P)$ where $\text{Unkillable}_\text{gen}$ are non-equivalent mutants that the generator cannot reach.

**Proof sketch**:

$M^*$ is a fixed point means $\mathcal{S}(T^*, M^*) = (T^*, M^*)$, i.e., $\text{gen}(M^*)$ kills no additional mutants in $M^*$. So every $m \in M^*$ survives all generable tests.

(a): If $\text{gen}$ is complete, then for any $m \notin \text{Equiv}(P)$, eventually some generated test kills $m$. At the fixed point, all such $m$ have been killed. Only equivalent mutants remain.

(b): If $\text{gen}$ is not complete, some non-equivalent mutants may be unreachable. $\square$

---

### THEOREM H3 — Greedy Set Cover Approximation

**Difficulty**: ★★★ (classical result applied)

**Depends on**: H1

**Statement**: The minimum set cover problem — find the smallest test suite $T^*$ that kills all non-equivalent mutants — is NP-hard. The greedy algorithm (at each step, add the test that kills the most uncovered mutants) achieves an approximation ratio of $H(n) = O(\ln n)$, where $n = |\text{Mut}(P) \setminus \text{Equiv}(P)|$.

**Proof**: The problem maps exactly to minimum set cover: universe = non-equivalent mutants, sets = $\{m : t \text{ kills } m\}$ for each possible test $t$. NP-hardness and the greedy approximation ratio are classical (Chvátal 1979, Feige 1998 for the inapproximability). $\square$

**Corollary H4**: The mu2-style fitness function (prioritize inputs killing more mutants) implements the greedy set cover heuristic. The resulting test suite is within an $O(\ln n)$ factor of optimal size.

---
---

## GROUP I: Monty Hall Filtering

These theorems formalize the pre-execution scope reduction layer.

---

### THEOREM I1 — Scope Reduction Bound

**Difficulty**: ★★

**Depends on**: Definitions §1.16

**Statement**: If the category priors assign $p_i(f) \leq \tau$ to $j$ categories (out of $k$ total), then the filtered mutation set satisfies:

$$|\text{Mut}_\tau(f)| \leq |\text{Mut}(f)| \cdot \frac{k - j}{k}$$

assuming uniform distribution of mutations across categories. In the non-uniform case:

$$|\text{Mut}_\tau(f)| = \sum_{i : p_i(f) > \tau} |\text{Mut}_{C_i}(f)|$$

**Proof**: Direct from definition of $\text{Mut}_\tau(f)$: we sum mutant counts only over categories with prior exceeding $\tau$. The uniform bound follows by setting $|\text{Mut}_{C_i}(f)| = |\text{Mut}(f)|/k$ for all $i$. $\square$

---

### THEOREM I2 — Recall Guarantee Under Calibrated Priors

**Difficulty**: ★★

**Depends on**: I1

**Statement**: If the priors are $\epsilon$-calibrated, then the probability of a false negative (filtering out a category that actually has survivors) is bounded:

Per-category: $P(\text{Surv}_{C_i}(f, T) \neq \emptyset \mid p_i(f) < \tau) < \epsilon$

Overall (union bound): $P(\exists i : \text{Surv}_{C_i}(f, T) \neq \emptyset \wedge p_i(f) < \tau) < k\epsilon$

**Proof**: The per-category bound is the definition of $\epsilon$-calibration. The union bound over $k$ categories gives the overall guarantee. $\square$

---
---

## GROUP J: Property-Based Testing and Weighted Specification

These theorems address the interaction between property-based tests and the specification completeness metric.

---

### THEOREM J1 — Property Kills Dominate Value Kills

**Difficulty**: ★★

**Depends on**: Oracle hierarchy definitions

**Statement**: Let $t_\text{prop}$ be a PROPERTY-level test that verifies $\forall a, b: f(a,b) = f(b,a)$, and let $t_\text{val}$ be a VALUE-level test on specific input $(a_0, b_0)$. If both kill the same mutant $m$:

(a) $t_\text{prop}$ provides strictly more specification information: it proves $m$ violates a universal law (all inputs), while $t_\text{val}$ proves $m$ disagrees on one specific input.

(b) Formally: the set of mutants killed by $t_\text{prop}$ for a given property is a superset of those killed by finitely many value tests on randomly chosen inputs, with probability 1 as the number of random inputs grows.

**Proof sketch**: (a) A universal property test checks $f(a,b) = f(b,a)$ for sampled $(a,b)$. Any mutant that breaks commutativity will be caught with high probability. A value test checks $f(a_0, b_0) = v_0$ — this catches mutants that change the output on $(a_0, b_0)$ but misses those that preserve this specific output while breaking the general property. (b) By sampling coverage arguments. $\square$

---

### THEOREM J2 — Kill Rate is Non-Monotone in Oracle Strength

**Difficulty**: ★★

**Depends on**: J1

**Statement**: There exist test suites $T_\text{prop}, T_\text{val}$ such that:

$$\text{oracle\_level}(T_\text{prop}) > \text{oracle\_level}(T_\text{val})$$
$$|\text{Killed}(f, T_\text{prop})| < |\text{Killed}(f, T_\text{val})|$$

yet $T_\text{prop}$ provides stronger specification.

**Proof by construction**: Let $f(a,b) = a + b$. Let $T_\text{val}$ be 50 unit tests: $\{(a_i, b_i, a_i + b_i) : i = 1..50\}$. Let $T_\text{prop}$ be 5 commutativity tests: $\{((a_i, b_i), \lambda r.[r = f(b_i, a_i)]) : i = 1..5\}$.

$T_\text{val}$ kills every mutant that changes $f$'s output on any of 50 specific inputs — potentially many mutants.

$T_\text{prop}$ kills only mutants that break commutativity on 5 inputs — potentially fewer mutants.

But $T_\text{prop}$ proves commutativity (a universal property), while $T_\text{val}$ proves 50 point properties. Commutativity enables reordering optimizations; 50 point values do not. $\square$

---

### THEOREM J3 — Weighted Specification Completeness

**Difficulty**: ★★

**Depends on**: J1, J2

**Statement**: Define weighted specification completeness:

$$\text{SC}_w(f, T) = \frac{\sum_{m \in \text{Killed}(f,T)} w(\text{max\_kill\_level}(m, T))}{\sum_{m \in \text{Mut}(f)} w_{\max}}$$

where $w : \mathcal{O}_L \to \mathbb{R}^+$ with $w(\text{PROPERTY}) > w(\text{VALUE}) > \cdots > w(\text{CRASH})$ and $w_{\max} = w(\text{PROPERTY})$.

Then $\text{SC}_w$ is:

(a) Monotone: $T_1 \preceq_P T_2 \implies \text{SC}_w(f, T_1) \leq \text{SC}_w(f, T_2)$

(b) A refinement of $\text{SC}$: for uniform weights ($w \equiv 1$), $\text{SC}_w = \text{SC}$

(c) Strictly more informative than $\text{SC}$: there exist $(f, T_1, T_2)$ with $\text{SC}(f, T_1) = \text{SC}(f, T_2)$ but $\text{SC}_w(f, T_1) \neq \text{SC}_w(f, T_2)$

**Proof**:

(a): Adding tests can only increase kills (or keep them the same). Each new kill adds $w(\ell) > 0$ to the numerator.

(b): If $w \equiv 1$, $\text{SC}_w = |\text{Killed}| \cdot 1 / (|\text{Mut}| \cdot 1) = \text{SC}$.

(c): Two suites killing the same number of mutants but at different oracle levels will have the same $\text{SC}$ but different $\text{SC}_w$. $\square$
