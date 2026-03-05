# Specification Completeness Theory: Definitions and Context

## Shared Preamble for Formal Proof Generation

**Purpose**: This document defines all objects, relations, and notation used across the theorem set. It should be provided as context when proving any individual theorem.

---

## 0. Notation and Conventions

- $\mathbb{B} = \{0, 1\}$ for booleans, $\mathbb{N}$ for naturals, $\mathcal{P}(X)$ for the powerset of $X$.
- $\equiv$ for definitional equality, $=$ for provable equality.
- $|X|$ denotes cardinality of finite set $X$.
- $f : A \to B$ for total functions, $f : A \rightharpoonup B$ for partial functions.
- $\bigsqcup$ denotes disjoint union.
- "Program" means a computable function $f : D \to R$ from input domain $D$ to output range $R$, abstracted from syntax.
- "Mutant" means a syntactically distinct program $f' : D \to R$ obtained by applying a mutation operator to the source of $f$.
- All sets are finite unless stated otherwise.

---

## 1. Core Objects

### 1.1 Programs and Mutations

**Definition (Programming Language).** Fix a programming language $\mathcal{L}$ — a countable set of syntactic programs, each denoting a computable function.

**Definition (Mutation Operators).** A finite set $\mathcal{O} = \{o_1, \ldots, o_n\}$ of mutation operators. Each $o_i$ takes a program $P$ and a mutation site $\ell \in \text{sites}(P, o_i)$ and produces a syntactically modified program $o_i(P, \ell)$.

**Definition (Mutation Sites).** For program $P$ and operator $o$, the set $\text{sites}(P, o) \subset \mathbb{N}$ identifies the AST locations where $o$ can be applied. This set is finite for any finite program.

**Definition (Mutant Set).** For program $P$:

$$\text{Mut}(P) = \{ o(P, \ell) : o \in \mathcal{O},\ \ell \in \text{sites}(P, o) \}$$

The set of all first-order mutants. $\text{Mut}(P)$ is finite.

### 1.2 Mutation Categories

**Definition (Mutation Category).** A mutation category $C$ is a subset of mutation operators targeting a specific syntactic/semantic class. The standard category set for a Python-like language is:

$$\mathcal{C} = \{C_\text{arith}, C_\text{cond}, C_\text{str}, C_\text{kw}, C_\text{bound}, C_\text{ret}, C_\text{call}\}$$

**Definition (Category Partition).** The categories partition $\mathcal{O}$:

$$\mathcal{O} = \bigsqcup_{C \in \mathcal{C}} C$$

This induces a partition on $\text{Mut}(P)$:

$$\text{Mut}(P) = \bigsqcup_{C \in \mathcal{C}} \text{Mut}_C(P)$$

where $\text{Mut}_C(P) = \{ o(P, \ell) : o \in C,\ \ell \in \text{sites}(P, o) \}$.

We write $k = |\mathcal{C}|$ for the number of categories.

### 1.3 Tests and Oracles

**Definition (Oracle).** An oracle is a function $\pi : R \to \mathbb{B}$ that judges whether an output is acceptable.

**Definition (Test).** A test is a pair $t = (x, \pi)$ where $x \in D$ is an input and $\pi : R \to \mathbb{B}$ is an oracle. We write $t(P) = \pi(P(x))$.

**Definition (Test Suite).** A test suite is a finite set $T = \{t_1, \ldots, t_m\}$ of tests. We write $\mathcal{T}_P$ for the set of all finite test suites for program $P$.

**Definition (Passing).** Program $P$ *passes* test suite $T$ if $\forall t \in T,\ t(P) = 1$.

### 1.4 Test-Induced Equivalence

**Definition (Test-Induced Equivalence).** Given test suite $T$, the equivalence relation $\sim_T$ on programs is:

$$P_1 \sim_T P_2 \iff \forall t \in T,\ t(P_1) = t(P_2)$$

### 1.5 Killed and Surviving Mutants

**Definition (Killed Mutants).** $\text{Killed}(P, T) = \{ m \in \text{Mut}(P) : m \not\sim_T P \}$

**Definition (Surviving Mutants).** $\text{Surv}(P, T) = \{ m \in \text{Mut}(P) : m \sim_T P \}$

**Fact.** $\text{Mut}(P) = \text{Killed}(P, T) \sqcup \text{Surv}(P, T)$.

**Definition (Category-Restricted Survival).** $\text{Surv}_C(P, T) = \text{Surv}(P, T) \cap \text{Mut}_C(P)$ and $\text{Killed}_C(P, T) = \text{Killed}(P, T) \cap \text{Mut}_C(P)$.

### 1.6 Specification Completeness

**Definition (Mutation Score / Specification Completeness).** For $|\text{Mut}(P)| > 0$:

$$\text{SC}(P, T) = \frac{|\text{Killed}(P, T)|}{|\text{Mut}(P)|}$$

We use "specification completeness" (SC) rather than "mutation score" to emphasize the interpretation: this measures how fully $T$ specifies the behavior of $P$.

**Definition (Category Survival Profile).** The survival profile is the vector:

$$\sigma(P, T) = \left(\sigma_1, \ldots, \sigma_k\right) \in [0,1]^k$$

where $\sigma_i = \frac{|\text{Surv}_{C_i}(P,T)|}{|\text{Mut}_{C_i}(P)|}$ when $|\text{Mut}_{C_i}(P)| > 0$, and $\sigma_i = 0$ otherwise.

**Definition (Active Dimension Count).**

$$\text{dim}(P, T) = |\{i : \sigma_i(P, T) > 0 \text{ and } |\text{Mut}_{C_i}(P)| > 0\}|$$

**Definition (Category Weights).** $w_i = \frac{|\text{Mut}_{C_i}(P)|}{|\text{Mut}(P)|}$ for each category $C_i$.

### 1.7 Distinguishing Power

**Definition (Distinguishing Power).** For test suite $T$ and program $P$:

$$\mathcal{D}(T, P) = \text{Killed}(P, T) \subseteq \text{Mut}(P)$$

### 1.8 Specification Refinement

**Definition (Specification Refinement Order).** For test suites $T_1, T_2$ relative to program $P$:

$$T_1 \preceq_P T_2 \iff \mathcal{D}(T_1, P) \subseteq \mathcal{D}(T_2, P)$$

We quotient $\mathcal{T}_P$ by the equivalence $T_1 \equiv_P T_2 \iff \mathcal{D}(T_1, P) = \mathcal{D}(T_2, P)$.

### 1.9 Specification Level Classification

**Definition (Specification Levels).** The set $\mathcal{L} = \{L_0, L_1, L_2, L_3, L_4\}$ with total order $L_0 < L_1 < L_2 < L_3 < L_4$:

| Symbol | Name | SC Range |
|--------|------|----------|
| $L_0$ | UNSPECIFIED | $[0, 0.1)$ |
| $L_1$ | WEAKLY_SPECIFIED | $[0.1, 0.3)$ |
| $L_2$ | PARTIALLY_SPECIFIED | $[0.3, 0.5)$ |
| $L_3$ | MODERATELY_SPECIFIED | $[0.5, 0.8)$ |
| $L_4$ | SPECIFIED | $[0.8, 1.0]$ |

**Definition (Level Function).** $\text{Level} : [0,1] \to \mathcal{L}$ maps SC values to specification levels by the threshold intervals above. $\text{Level}$ is monotone: $a \leq b \implies \text{Level}(a) \leq \text{Level}(b)$.

### 1.10 True Observational Equivalence

**Definition (Extensional Equivalence).** Two programs $P_1, P_2 : D \to R$ are extensionally equivalent, written $P_1 \cong P_2$, if:

$$\forall x \in D,\ P_1(x) = P_2(x)$$

**Definition (Equivalent Mutant).** $m \in \text{Equiv}(P) \iff m \in \text{Mut}(P) \wedge m \cong P$.

**Definition (Achievable Top).** The maximum achievable killed set is $\text{Mut}(P) \setminus \text{Equiv}(P)$.

### 1.11 Oracle Hierarchy

**Definition (Oracle Levels).** The set $\mathcal{O}_L = \{$CRASH, TYPE, STRUCTURAL, SHAPE, VALUE, PROPERTY$\}$ with total order:

$$\text{CRASH} < \text{TYPE} < \text{STRUCTURAL} < \text{SHAPE} < \text{VALUE} < \text{PROPERTY}$$

**Definition (Oracle Level of a Test).** Each test $t = (x, \pi)$ has an oracle level $\text{olevel}(t) \in \mathcal{O}_L$ determined by what $\pi$ checks:

| Level | Oracle behavior |
|-------|----------------|
| CRASH | $\pi(r) = [r \neq \bot]$ (no crash) |
| TYPE | $\pi(r) = [\text{type}(r) = \tau]$ |
| STRUCTURAL | $\pi(r) = [\text{struct}(r) = s]$ (e.g., length) |
| SHAPE | $\pi(r) = [\text{shape}(r) = s]$ (e.g., element types) |
| VALUE | $\pi(r) = [r = v]$ for specific $v$ |
| PROPERTY | $\pi$ checks a universally quantified law |

**Definition (Kill Strength).** When test $t$ kills mutant $m$ (i.e., $t(m) \neq t(P)$), the kill strength is $\text{olevel}(t)$.

**Definition (Specification Strength Vector).** For function $f$ and test suite $T$:

$$\text{str}(f, T) = (s_\text{crash}, s_\text{type}, s_\text{struct}, s_\text{shape}, s_\text{value}, s_\text{prop}) \in [0,1]^6$$

where $s_\ell = |\{m \in \text{Killed}(f,T) : \text{max kill strength of } m = \ell\}| / |\text{Mut}(f)|$.

### 1.12 Function Decomposition

**Definition (Decomposition).** A decomposition of $f : D \to R$ is a tuple $(g_1, \ldots, g_n, h)$ where:
- Each $g_i : D_i \to R_i$ is a component function
- $h : R_1 \times \cdots \times R_n \to R$ is a composition function
- $f = h \circ (g_1, \ldots, g_n)$ extensionally on $D$

**Definition (Separable Function).** $f : D_1 \times D_2 \to R$ is separable if $f(x_1, x_2) = h(g_1(x_1), g_2(x_2))$ for some $g_1, g_2, h$.

**Definition (Dispatching Function).** $f$ is dispatching if $f(x) = \text{if } p(x) \text{ then } g_1(x) \text{ else } g_2(x)$ for some predicate $p$ and branches $g_1, g_2$.

### 1.13 Cross-Channel Gate

**Definition (Algebra Result).** A pair $\alpha = (\text{property}, \text{conf})$ where $\text{property}$ is an algebraic property name and $\text{conf} \in [0,1]$.

**Definition (Mutation State).** $\mu = (\text{survival}, \text{depth})$ where $\text{survival} \in [0,1]$ is the survival rate.

**Definition (Gate Function).** $G : \text{AlgebraResult} \times \text{MutationState} \to \text{GatedResult}$:

$G(\alpha, \mu) = \begin{cases}
(\alpha.\text{property},\ \min(\alpha.\text{conf},\ 0.90),\ \text{VERIFIED}) & \text{if } \mu.\text{survival} < 0.30 \\
(\alpha.\text{property},\ \alpha.\text{conf} \times 0.50,\ \text{PENALIZED}) & \text{if } 0.30 \leq \mu.\text{survival} < 0.60 \\
(\alpha.\text{property},\ \alpha.\text{conf} \times 0.10,\ \text{GATED}) & \text{if } \mu.\text{survival} \geq 0.60
\end{cases}$

**Design note (multiplicative gate).** All three branches scale confidence relative to $\alpha.\text{conf}$. The gated branch uses $\alpha.\text{conf} \times 0.10$ rather than a constant $0.10$. This guarantees two formal properties: (1) the gate never *increases* confidence above the input (conservatism — T1.11), and (2) confidence is antitone in survival rate without requiring extra hypotheses on $\alpha.\text{conf}$ (monotonicity — T1.12). A constant floor of $0.10$ would violate conservatism when $\alpha.\text{conf} < 0.10$.

### 1.14 Prediction Layer (Orthogonal Lens)

**Definition (Latent Variable).** $\Sigma \in \mathcal{L}$ is the true specification level of a function — the latent variable not directly observed.

**Definition (Signal Channels).** Observable signals $X_1, \ldots, X_n$ where:
- $X_1 = \text{semantic\_ratio}(f)$ — assertion quality
- $X_2 = \text{weakness\_taxonomy}(f)$ — weakness classification
- $X_3 = \text{num\_categories}(f)$ — behavioral dimensionality
- $X_4 = \text{purity}(f)$ — purity classification

Each $X_i$ is a noisy observation of $\Sigma$.

**Definition (Conditional Independence).** Signals $X_i, X_j$ are conditionally independent given $\Sigma$ if:

$$P(X_i, X_j \mid \Sigma) = P(X_i \mid \Sigma) \cdot P(X_j \mid \Sigma)$$

### 1.15 Synthesis Loop

**Definition (Test Generator).** A function $\text{gen} : \mathcal{P}(\text{Mut}(P)) \to \mathcal{P}(\text{Tests})$ that produces candidate tests using surviving mutants as fitness signal.

**Definition (Synthesis Operator).** $\mathcal{S} : \mathcal{P}(\text{Tests}) \times \mathcal{P}(\text{Mut}(P)) \to \mathcal{P}(\text{Tests}) \times \mathcal{P}(\text{Mut}(P))$:

$$\mathcal{S}(T, M) = (T \cup \text{gen}(M),\ M \setminus \text{Killed}(P, T \cup \text{gen}(M)))$$

**Definition (Complete Generator).** $\text{gen}$ is complete if for every non-equivalent mutant $m \notin \text{Equiv}(P)$, there exists $n$ such that $m \in \text{Killed}(P, T_n)$ in the sequence generated by iterated application of $\mathcal{S}$.

### 1.16 Monty Hall Filtering

**Definition (Category Prior).** For category $C_i$ and function $f$, given signal observations:

$$p_i(f) = P(\text{Surv}_{C_i}(f, T) \neq \emptyset \mid X_1(f), \ldots, X_n(f))$$

**Definition (Filtered Mutation Set).** Given threshold $\tau \in [0,1]$:

$$\text{Mut}_\tau(f) = \bigcup_{i\ :\ p_i(f) > \tau} \text{Mut}_{C_i}(f)$$

**Definition (Calibrated Prior).** The priors are $\epsilon$-calibrated if:

$$P(\text{Surv}_{C_i}(f, T) \neq \emptyset \mid p_i(f) < \tau) < \epsilon$$

---

## 2. Assumed Properties

The following are taken as axioms within this framework:

**A1 (Finite Mutation Space).** For any program $P \in \mathcal{L}$, $\text{Mut}(P)$ is finite.

**A2 (Category Partition).** The categories $\mathcal{C}$ partition $\mathcal{O}$, inducing a partition on $\text{Mut}(P)$.

**A3 (Test Determinism).** For any test $t$ and program $P$, $t(P)$ is deterministic (same input always produces same output).

**A4 (Oracle Monotonicity).** If oracle $\pi_1$ is at level $\ell_1$ and $\pi_2$ at $\ell_2$ with $\ell_1 < \ell_2$, then for any programs $P_1 \neq P_2$: if $\pi_1$ distinguishes them, $\pi_2$ also distinguishes them. (Higher oracles are strictly more discriminating.)

**A5 (Mutation Operator Soundness).** Each mutation operator $o \in \mathcal{O}$ produces a syntactically valid program: $o(P, \ell) \in \mathcal{L}$.

**A6 (Level Monotonicity).** The Level function is monotone: $a \leq b \implies \text{Level}(a) \leq \text{Level}(b)$.
