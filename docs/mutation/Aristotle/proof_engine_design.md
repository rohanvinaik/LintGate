# Proof Engine Design: MathLinter + LeanDojo Integration

## Executive Summary

A local MCP-based proof engine that combines MathLinter's LSP verification layer
with LeanDojo's ML-powered tactic generation, premise retrieval, and proof search.
Runs on Apple Silicon (Metal) without CUDA. No training in Phase 1.

---

## The Lean-Dojo Ecosystem (What's Available)

| Project | Stars | Key Contribution | Integration Value |
|---------|-------|------------------|-------------------|
| **LeanDojo** (v1) | 772 | Lean tracing + data extraction | Corpus extraction pipeline |
| **LeanDojo-v2** | 44 | End-to-end framework: trace -> train -> prove | Pantograph provers, DynamicDatabase, ExternalProver |
| **ReProver** | 317 | Retrieval-augmented tactic generation (ByT5) | **Tiny models** that run on CPU/Metal: tacgen + retriever |
| **LeanCopilot** | 1233 | Native Lean integration via CTranslate2 FFI | ExternalGenerator protocol (FastAPI) for Lean-side tactics |
| **LeanAgent** | 58 | Lifelong learning with EWC (anti-forgetting) | Retrieval corpus + curriculum difficulty scoring |
| **LeanProgress** | 3 | Proof progress prediction (steps-remaining) | Search guidance signal — prioritize promising branches |
| **LeanMillenniumPrizeProblems** | 44 | Formalized Clay problem statements | Benchmark corpus for hard theorems |

### Key Insight: ReProver Models Are Tiny

The `kaiyuy/leandojo-lean4-tacgen-byt5-small` and retriever models are ByT5-small
(~300M params). They run comfortably on CPU or Metal without quantization. This is
the critical enabler for a local engine — we don't need a GPU for inference.

### Key Insight: LeanCopilot's ExternalGenerator Protocol

LeanCopilot defines a simple FastAPI protocol: POST `/generate` with
`{name, input, prefix}`, returns `{outputs: [{output, score}]}`. Any model server
that implements this interface can be called natively from within Lean proofs.
This means our MCP proof engine can also serve as a LeanCopilot backend.

### Key Insight: LeanProgress Guides Search

LeanProgress predicts "how many tactic steps remain" from a goal state. This is a
reward signal for proof search — instead of blind BFS/DFS, prioritize states that
the progress model scores as closer to completion. Integrated into LeanDojo-v2's
`ExternalProver` server via `LEANPROGRESS_MODEL` env var.

---

## Current MathLinter Architecture

```
MathLinter MCP Server
├── session.py          LeanSession: LSP client per project (lazy init)
├── locator.py          Find theorems by name across project files
├── patcher.py          Buffer-level proof replacement + LSP recheck
├── candidates.py       Tactic candidate generation (strategy-based)
├── safety.py           Classify proof changes (commit_safe vs draft_safe)
├── search/             Loogle + LeanSearch + local symbol search
└── tools/
    ├── theorem_inspect   Statement, proof body, goals, diagnostics
    ├── proof_try         Whole-proof or range-local replacement + verify
    ├── patch_theorem     Replace proof body, LSP recheck
    ├── proof_golf        Try simpler single-tactic proofs
    ├── proof_cleanup     LSP code actions ("Try This" suggestions)
    ├── lemma_search      Multi-backend lemma discovery
    ├── theorem_suggest   Related theorems, corollaries, variants
    └── axiom_trace       Axiom dependency tracking
```

**Strength**: Ground-truth verification via LSP. Every proof is checked by Lean itself.
**Gap**: No ML-powered tactic generation, no proof search, no premise retrieval.

---

## Proposed Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     MCP Interface Layer                       │
│  (Claude-facing — all existing MathLinter tools preserved)   │
│                                                              │
│  NEW TOOLS:                                                  │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────────┐ │
│  │ lean_prove       │ │ lean_suggest │ │ lean_retrieve     │ │
│  │ (proof search)   │ │ (ML tactics) │ │ (premise select)  │ │
│  └────────┬────────┘ └──────┬───────┘ └────────┬──────────┘ │
│           │                 │                   │            │
├───────────┼─────────────────┼───────────────────┼────────────┤
│           │     Orchestration Layer              │            │
│           │                                      │            │
│  ┌────────▼──────────────────────────────────────▼────────┐  │
│  │              ProofSearchOrchestrator                    │  │
│  │                                                        │  │
│  │  1. Get goal state (Pantograph or LSP)                 │  │
│  │  2. Generate tactic candidates:                        │  │
│  │     a. ReProver ByT5 tacgen (local, fast)              │  │
│  │     b. ReProver retrieval-augmented tacgen              │  │
│  │     c. MathLinter candidates.py (strategy-based)       │  │
│  │     d. Claude reasoning (fallback, via MCP caller)     │  │
│  │  3. Score candidates (LeanProgress steps-remaining)    │  │
│  │  4. Verify top-K via Pantograph goal_start/goal_tactic │  │
│  │  5. If search succeeds → verify full proof via LSP     │  │
│  │  6. Return tactic trace + verification status          │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                    Runtime Layer                              │
│                                                              │
│  ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │  Pantograph      │  │ Model Server │  │ MathLinter LSP  │ │
│  │  (PyPantograph)  │  │ (FastAPI)    │  │ (leanclient)    │ │
│  │                  │  │              │  │                  │ │
│  │  Goal-state RPC  │  │ ReProver:    │  │ File-level       │ │
│  │  Tactic apply    │  │  - tacgen    │  │ diagnostics      │ │
│  │  Search tree     │  │  - retriever │  │ Buffer patching  │ │
│  │                  │  │ Progress:    │  │ Code actions     │ │
│  │                  │  │  - scorer    │  │                  │ │
│  └─────────────────┘  └──────────────┘  └─────────────────┘ │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                     Data Layer                                │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Premise Corpus (per-project)                           │ │
│  │  - Extracted via LeanDojo tracing pipeline              │ │
│  │  - Indexed with ReProver retriever embeddings           │ │
│  │  - Cached at ~/.mathlinter/corpora/<project_hash>/      │ │
│  │                                                         │ │
│  │  Proof Log (append-only)                                │ │
│  │  - Every verified proof attempt: (goal, tactics, result)│ │
│  │  - Future SFT training data                             │ │
│  │  - Stored at <project>/.mathlinter/proof_log.jsonl      │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## New MCP Tools

### `lean_prove` — Automated proof search

```
lean_prove(name, file_path?, max_steps=100, max_time_s=120, strategy="best_first")
```

Runs proof search on a theorem (identified by name or as a `sorry`).
Uses Pantograph for goal-state manipulation, ML models for tactic generation,
LeanProgress for search guidance. Returns the tactic trace if a proof is found,
or a partial trace with the closest state reached.

**Search strategies**:
- `best_first`: LeanProgress-scored priority queue (default)
- `depth_first`: Greedy, fast for easy goals
- `beam`: Parallel beam search with width K

**Verification**: Found proofs are double-checked via MathLinter's LSP path
(`lean_proof_try`) before being reported as verified.

### `lean_suggest_tactics` — ML-powered tactic suggestions

```
lean_suggest_tactics(goal_state, num_suggestions=8, use_retrieval=true)
```

Given a goal state string, returns ranked tactic suggestions from the local
ReProver models. If `use_retrieval=true`, retrieves relevant premises first
and uses the retrieval-augmented tacgen model.

Does NOT verify the tactics — use `lean_proof_try` to verify.

### `lean_retrieve_premises` — Premise selection

```
lean_retrieve_premises(goal_state, num_premises=10)
```

Embeds the goal state with the ReProver retriever encoder, computes cosine
similarity against the project premise corpus, returns top-K premises with
their similarity scores and full signatures.

Complements `lean_lemma_search` (which uses Loogle/LeanSearch) with
embedding-based retrieval from the local project corpus.

### `lean_trace_project` — Extract premise corpus

```
lean_trace_project(path?, build_deps=false)
```

Runs LeanDojo's data extraction pipeline on the project. Extracts theorem
statements, proof states, premises, and builds the retriever corpus index.
Cached — re-running on an unchanged project is a no-op.

### `lean_search_status` — Proof search diagnostics

```
lean_search_status()
```

Returns: model server health, corpus status (traced/stale/missing),
Pantograph connection status, proof log stats.

---

## Metal/CPU Runtime Strategy

### Model Serving (no CUDA required)

The ReProver models are ByT5-small (~300M params). Three options for Metal:

**Option A: MLX (recommended for Apple Silicon)**
```python
# mlx-lm can serve encoder-decoder models
# Convert HF checkpoint → MLX format
mlx_lm.convert --hf-path kaiyuy/leandojo-lean4-tacgen-byt5-small
```
Pros: Native Metal acceleration, fast inference.
Cons: Need to verify ByT5 (byte-level T5) compatibility with mlx-lm.

**Option B: CTranslate2 (what LeanCopilot uses)**
```python
# CTranslate2 has CPU + Metal backends, optimized for T5
import ctranslate2
generator = ctranslate2.Translator("ct2-model-path", device="auto")
```
Pros: Battle-tested (LeanCopilot uses this exact path), CPU fallback.
Cons: Requires model conversion to CT2 format.

**Option C: Raw HuggingFace + MPS**
```python
import torch
device = torch.device("mps")  # Apple Metal
model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)
```
Pros: Zero conversion, works today.
Cons: Slower than MLX/CT2, higher memory.

**Recommendation**: Start with Option C (zero setup), migrate to Option B
(CTranslate2) once working, since LeanCopilot's pre-converted models are
already available on HuggingFace (`ct2-leandojo-lean4-tacgen-byt5-small`).

### Pantograph

PyPantograph is pure Python + subprocess (talks to Lean via RPC).
No GPU dependency. Works on macOS as-is.

### LeanProgress

The progress model is a fine-tuned LLM that predicts steps-remaining.
For Phase 1, skip this (use uniform scoring). Add in Phase 2 with a
small quantized model or by calling Claude as the scorer.

---

## LeanCopilot Compatibility

Our model server can double as a LeanCopilot ExternalGenerator backend.
The protocol is trivial:

```python
@app.post("/generate")
async def generate(req: GeneratorRequest) -> GeneratorResponse:
    """LeanCopilot-compatible endpoint."""
    outputs = reprover_tacgen.generate(req.input, prefix=req.prefix)
    return GeneratorResponse(outputs=[
        Generation(output=t, score=s) for t, s in outputs
    ])
```

This means users can also use the proof engine directly from Lean's editor
via `suggest_tactics` / `search_proof`, not just through Claude.

---

## Phased Implementation

### Phase 1: Core Search Loop (scope of first PR)

1. **Install LeanDojo-v2 + PyPantograph** as dependencies
2. **Model server**: FastAPI process serving ReProver tacgen + retriever
   on MPS/CPU, LeanCopilot-compatible `/generate` endpoint
3. **`lean_suggest_tactics`** tool: goal state → ranked tactics
4. **`lean_retrieve_premises`** tool: goal state → similar premises
5. **`lean_prove`** tool: best-first search using Pantograph
   - Tacgen candidates from ReProver
   - Pantograph for goal-state transitions
   - LSP double-check via existing `lean_proof_try`
6. **Proof logging**: append every verified search to proof_log.jsonl

### Phase 2: Corpus + Progress

7. **`lean_trace_project`**: LeanDojo tracing pipeline integration
8. **Premise corpus indexing**: per-project, cached, auto-refreshed
9. **LeanProgress integration**: steps-remaining scoring for search guidance
10. **Search strategy tuning**: beam width, backtracking policy

### Phase 3: Training Loop (future)

11. **SFT from proof logs**: use LeanDojo-v2's `SFTTrainer` on collected
    (goal_state, tactic) pairs from successful proofs
12. **Retrieval fine-tuning**: specialize the retriever on the project's
    premise corpus using `RetrievalTrainer`
13. **GRPO**: reinforcement learning from proof search outcomes
14. **LeanAgent lifelong learning**: EWC to prevent forgetting as the
    model specializes to new repos

### Phase 4: Ecosystem Integration

15. **LeanCopilot lakefile integration**: users add our server as an
    ExternalGenerator in their lakefile.lean
16. **Millennium Problems benchmark**: evaluate search on LeanMillenniumPrizeProblems
17. **Multi-model ensemble**: combine ReProver (fast/local) with
    DeepSeek-Prover-V2 (powerful/API) via ExternalProver

---

## Data Flow

```
User: "prove T5_12_composition_gap"
         │
         ▼
  lean_prove(name="T5_12_composition_gap")
         │
         ▼
  ┌─ Locate theorem (MathLinter locator) ─────────────────┐
  │  file: Phase7/CompositionGap.lean                      │
  │  statement: theorem T5_12_composition_gap : ...        │
  │  proof: sorry                                          │
  └────────────────────────────────────────────────────────┘
         │
         ▼
  ┌─ Initialize Pantograph server for project ─────────────┐
  │  goal_state = server.goal_start(theorem_type)          │
  │  goals: ["⊢ ∀ (M : MutationSystem) ..."]              │
  └────────────────────────────────────────────────────────┘
         │
         ▼
  ┌─ Search Loop ─────────────────────────────────────────┐
  │                                                        │
  │  while steps < max_steps:                              │
  │    1. current_goal = pick_goal(search_queue)            │
  │                                                        │
  │    2. candidates = []                                   │
  │       candidates += reprover_tacgen(goal)      # ML    │
  │       candidates += reprover_rag(goal, premises) # RAG │
  │       candidates += mathlinter_candidates(goal)  # rule│
  │                                                        │
  │    3. for tactic in rank(candidates):                   │
  │         result = server.goal_tactic(state, tactic)     │
  │         if result.is_solved:                           │
  │           → verify_via_lsp(tactic_trace)               │
  │           → return ProofFound(trace)                   │
  │         elif result.has_new_goals:                     │
  │           → push new states to search_queue            │
  │                                                        │
  │  return ProofNotFound(best_partial_trace)              │
  └────────────────────────────────────────────────────────┘
         │
         ▼
  ┌─ LSP Verification ────────────────────────────────────┐
  │  lean_proof_try(name, new_proof=tactic_trace,          │
  │                 apply=false)                            │
  │  → diagnostics: [] (clean = verified)                  │
  └────────────────────────────────────────────────────────┘
         │
         ▼
  ┌─ Proof Log ───────────────────────────────────────────┐
  │  proof_log.jsonl ← {theorem, tactics, steps, time,    │
  │                      verified: true, model: "reprover"}│
  └────────────────────────────────────────────────────────┘
```

---

## File Layout (proposed additions to MathLinter)

```
mathlinter/
├── tools/
│   ├── prove.py              # lean_prove MCP tool
│   ├── suggest_tactics.py    # lean_suggest_tactics MCP tool
│   ├── retrieve_premises.py  # lean_retrieve_premises MCP tool
│   ├── trace_project.py      # lean_trace_project MCP tool
│   └── search_status.py      # lean_search_status MCP tool
├── engine/
│   ├── search.py             # ProofSearchOrchestrator
│   ├── pantograph_session.py # Pantograph server lifecycle
│   ├── model_server.py       # FastAPI server for ReProver models
│   ├── tactic_generator.py   # Wrapper: tacgen model → tactic list
│   ├── premise_retriever.py  # Wrapper: retriever model → premise list
│   ├── progress_scorer.py    # LeanProgress integration (Phase 2)
│   └── proof_log.py          # Append-only JSONL logger
├── corpus/
│   ├── tracer.py             # LeanDojo tracing pipeline wrapper
│   ├── indexer.py            # Build/cache retriever corpus index
│   └── cache.py              # Per-project corpus cache management
└── compat/
    └── copilot_server.py     # LeanCopilot ExternalGenerator compat
```

---

## Dependencies (added to MathLinter)

```toml
[project.optional-dependencies]
engine = [
    "pantograph",                          # Lean RPC
    "torch>=2.0",                          # Model inference (MPS backend)
    "transformers>=4.20",                  # ReProver models (ByT5)
    "fastapi>=0.100",                      # Model server
    "uvicorn>=0.20",                       # ASGI server
    "lean-dojo-v2",                        # Tracing + database (Phase 2)
]
```

Phase 1 core (search + suggest + retrieve) needs only:
`pantograph`, `torch`, `transformers`, `fastapi`, `uvicorn`.

LeanDojo-v2 proper (with its heavy deps like DeepSpeed, Ray, PyTorch Lightning)
is only needed for Phase 2 tracing and Phase 3 training.

---

## Open Questions

1. **ByT5 on MLX**: Does mlx-lm support encoder-decoder T5 variants?
   If not, CTranslate2 is the fallback. Need to test.

2. **Pantograph project compatibility**: PyPantograph needs the project
   built with specific Lean versions. How does this interact with
   MathLinter's LSP session (which uses `lake serve`)?

3. **Model server lifecycle**: Run as a subprocess managed by MathLinter's
   MCP server? Or as a standalone daemon? Subprocess is simpler but
   adds startup latency. Daemon needs lifecycle management.

4. **Corpus staleness**: When a Lean file changes, the premise corpus
   index is stale. Incremental re-indexing vs full retrace tradeoff.

5. **DeepSeek-Prover-V2 integration**: For hard theorems, ReProver may
   not suffice. Adding an `ExternalProver` that calls DeepSeek via API
   is straightforward but adds latency + cost. Worth it for Phase 2?
