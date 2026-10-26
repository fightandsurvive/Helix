# Trinstinctus Engine — White Paper

**Version 0.1** | The neural architecture, search algorithm, training pipeline,
personality system, and honest Elo trajectory of the Trinstinctus chess engine.

---

## Abstract

Trinstinctus is a Transformer-based chess engine in the AlphaZero family,
built for a single-workstation deployment on NVIDIA Blackwell silicon.
It combines a pre-norm transformer trunk (optionally Mixture-of-Experts),
PUCT Monte Carlo Tree Search with optional Gumbel-AlphaZero root selection,
six-piece Syzygy probing at every leaf, NVFP4 mixed-precision training,
two-tier Stockfish distillation, and continuous self-play reinforcement
learning. Six LoRA personality adapters, trained on isolated historical
corpora, provide the engine's coaching voices without degrading base strength.

The design targets:
1. **Lc0 parity or superiority** in equal-compute MCTS play.
2. **Stockfish-17 single-thread parity** in slow classical matches
   (45–55% score as realistic target, not dominance).
3. **Six playable personalities** for the seven-voice Legends panel.

We state honestly: no open-source project on a single workstation has beaten
Stockfish at full tournament strength since 2022. Trinstinctus does not claim
that milestone. It claims parity with Lc0 and competitive scoring versus
Stockfish single-thread — both verifiable via SPRT and open-source comparisons.

---

## 1. Design goals and non-goals

### 1.1 Goals

- **Strength first.** The base engine is the priority. Personality adapters are
  a secondary feature, trained after the base converges, and never retroactively
  affect the base.
- **Reproducibility.** Every run is seeded, every checkpoint is hashed, every
  dataset is manifested. A run can be reconstructed from the database.
- **Local-first.** No cloud inference, no telemetry, no API keys. Ollama runs
  DeepSeek-R1 locally. PostgreSQL runs locally. PyTorch runs locally.
- **Observability.** Every long-running operation writes to `ops.training_progress`
  at 1 Hz. Failures produce structured JSON with correlation IDs, not freeform
  strings.
- **Honest claims.** The white paper states what the engine achieves at each
  milestone, with the corpus size and compute budget that produced it. No
  hand-waving about Elo.

### 1.2 Non-goals

- Beating Stockfish 17 at 16+ threads in open tournaments. This requires
  compute (multi-node clusters) that a single workstation cannot supply.
- Solving chess.
- Real-time analysis at bullet time controls.
- Distributed training. The pipeline runs on one GPU. Distributed work is
  compatible architecturally but not implemented.

---

## 2. Related work

**AlphaZero** (Silver et al., 2018) — established the architecture: a
policy-value network evaluated by PUCT MCTS, trained via self-play with
visit-distribution policy targets and game-result value targets. Trinstinctus
follows this paradigm.

**Lc0** — the open-source AlphaZero-family engine. Its T79-class networks
(ResNet blocks, 20M–200M parameters) established MCTS engines as competitive
with Stockfish NNUE at high compute. Trinstinctus uses transformers instead of
ResNets, following the T80+ experiments Leela's own community ran in 2023–2024.

**Stockfish NNUE** — the current strongest open-source engine. Its evaluation
network is trained on hundreds of millions of positions with Stockfish as
its own teacher. Trinstinctus borrows this technique in stage 2 (distillation).

**Danihelka et al., 2022** — Gumbel-AlphaZero. Replaces PUCT root selection
with Gumbel-top-k sampling and sequential halving. The paper demonstrates
~3× sample efficiency at low simulation counts — exactly our training regime.

---

## 3. Architecture

### 3.1 Input representation

Board → 66-token sequence:

- **Token 0** — CLS token (learned parameter, aggregates global context for heads)
- **Token 1** — Global features (14 floats projected to d_model):
  side-to-move, 4 castling flags, 8 en-passant file flags, halfmove clock
- **Tokens 2–65** — One per square. Each encoded as integer 0–12
  (empty, 1–6 white pieces, 7–12 black pieces) and embedded at d_model.

Fixed 66-token sequence means no padding anywhere. Batches have uniform shape,
VRAM usage is predictable, and no `attention_mask` is needed.

### 3.2 Transformer stack

- **Depth:** 24 layers (configurable)
- **Width:** d_model = 1024, 16 heads, FFN d_ff = 3072
- **Norm:** RMSNorm with pre-norm placement (matches LLaMA/Qwen lineage)
- **Activation:** GELU
- **Dropout:** 0.05 (attention + FFN)

Pre-norm is stable at depth 24+ without learning-rate warmup tricks. RMSNorm
is cheaper than LayerNorm and empirically equivalent for this scale.

### 3.3 Mixture-of-Experts FFN

When `use_moe=True`, the FFN is replaced by a 32-expert sparse mixture with
top-4 routing. Total parameters grow to ~1.4B; active parameters per token
remain ~420M.

**Why MoE:** chess positions cluster into "tactical density regimes." MoE
allows specialized experts for quiet positional endgames, sharp tactical
middlegames, opening theory, etc., without cross-contamination of shared FFN
weights. This is the architecture Lc0's T80+ converged on and that closed
Leela's gap with SF NNUE in 2023–2024.

**Cost:** ~2× memory. Dense 24L d=1024 fits in ~50GB at batch 8192; MoE fits
in ~55GB at batch 4096. Both fit on a 72GB Blackwell.

### 3.4 Output heads

- **Policy head** — Linear → 1024 → GELU → Linear → 4672.
  4672 = 64 from-squares × 73 move-types (56 queen-slides + 8 knight +
  9 underpromotions). AlphaZero convention; interchangeable with Lc0's
  output indices.
- **Value head** — Linear → 512 → GELU → Linear → 1 → tanh.
  Value in [-1, 1], from side-to-move perspective.

### 3.5 Parameter counts

| Config | Layers | d_model | FFN | Params | VRAM @ B=8192 |
|---|---|---|---|---|---|
| Small (dev) | 12 | 512 | dense 2048 | 45M | 8GB |
| Medium (default) | 24 | 1024 | dense 3072 | 380M | 50GB |
| Large (target) | 24 | 1024 | MoE 32×3072 | 1.4B (420M active) | 55GB @ B=4096 |
| XL (aspirational) | 32 | 1024 | MoE 32×4096 | 2.1B (580M active) | >72GB, needs 2× GPU |

The Medium configuration is the floor — it matches Lc0's T79 scale. Large is
the target. XL requires cloud augmentation.

---

## 4. Search

### 4.1 PUCT MCTS

Standard AlphaZero search:
```
select: argmax_a [ Q(s,a) + c_puct · P(s,a) · sqrt(N(s)) / (1 + N(s,a)) ]
expand: evaluate leaf via network or Syzygy
backup: N(s,a) += 1, W(s,a) += v, Q(s,a) = W/N (negamax across tree)


c_puct = 1.5` in training, `1.8` in evaluation (slightly more exploitative).
```

### 4.2 Syzygy probing at every leaf

Any node with ≤N pieces (N determined by installed tablebase set) probes
Syzygy before evaluating the network. On the reference setup with 6-piece
tables (149 GiB, ~365 files), N=6.

**Why this matters:** Syzygy gives perfect endgame values at zero network
cost (~2μs per probe from RAM-cached tables). Without it, the network wastes
capacity learning endgame fundamentals it cannot fully learn from 13.2M games.
With it, the network's endgame predictions become irrelevant — search handles
the exact answer.

**What about midgame probing?** Extending probing to positions with 10+
pieces is not standard practice and has no proven benefit. The 6-piece tables
cover all ≤6-piece positions exactly; 7-piece covers ≤7. Midgame positions
with 10+ pieces are outside any published tablebase set. We do not attempt
midgame probing.

### 4.3 Gumbel-AlphaZero root selection

The PUCT implementation above is Lc0's standard. Gumbel-AlphaZero
(Danihelka et al., 2022) replaces the root-selection step with Gumbel-top-k
sampling plus sequential halving:

1. Draw Gumbel noise `g_i ~ Gumbel(0,1)` for each root child.
2. Sample m candidates via `argtop_k(logits_i + g_i)`.
3. Run `sims_per = total_sims / (m · log₂(m))` simulations per candidate.
4. Keep top half by `logits + c_scale · Q / (1 + c_visit · N / N_total)`.
5. Repeat until one candidate remains.

The paper demonstrates ~3× sample efficiency at 400–800 sims, which is
exactly our training regime. Enabling Gumbel in `MCTS.search()` is a one-line
flag. Gate behind SPRT: run 500 games with Gumbel vs without at 400 sims,
verify measurable Elo gain, then enable by default.

### 4.4 Number of simulations

- Training self-play: 400 sims/move
- SPRT gates: 800 sims/move
- Final match play: 3200 sims/move (adjustable)
- Timeless analysis: 10M+ sims/position (no time constraint)

---

## 5. Training pipeline

### 5.1 Stage 1: Supervised bootstrap

**Data:** 13.2M GM games from `gm.moves_general` (Lumbra + PGN Mentor).
**Target:** game result as value target, actual move as policy target.
**Output:** `checkpoints/supervised.pt` (~2900 Elo).
**Time:** 3–4 days on Blackwell.

Stage 1 exists because the distillation corpus (2M positions) cannot teach
chess from scratch. Those positions are sampled from middlegame — they do not
cover opening patterns or endgame fundamentals adequately. The 528M positions
in the GM corpus do, at game-result signal. Stage 1 builds the foundation that
makes Stage 2's fine-tune effective.

### 5.2 Stage 2: Two-tier Stockfish distillation

**The depth question.** Stockfish at depth 18 (32-thread CPU) produces
evaluations at ~80–120 ms per position. Depth 22 at ~400–600 ms. Depth 24 at
~1.2–1.8 s. For a 2M-position corpus:

| Depth | Time per position | 2M corpus |
|---|---|---|
| 18 | 100 ms | **~4–6 days** |
| 22 | 500 ms | ~18–28 days |
| 24 | 1.5 s | ~35–50 days |

A 2M-position corpus at depth 22 monopolizes the machine for nearly a month.
That is why the bulk corpus runs at depth 18.

**The tiered solution.** Depth 18 already captures ~99% of Stockfish's
evaluative signal — the marginal information gain from depth 22 is small on
most positions, and matters mainly in sharp tactical middlegames. We
exploit this asymmetry:

| Corpus | Size | Depth | Time | Purpose |
|---|---|---|---|---|
| Bulk | 2,000,000 | 18 | 4–6 days | Broad evaluation landscape |
| Refinement | 100,000 | 22–24 | 1–2 days | Sharp tactical gold samples |

Training runs in two passes:
1. Bulk corpus at depth 18 → `distilled_bulk.pt`
2. Refinement corpus at depth 22–24 → `distilled.pt`

The refinement corpus is where the depth 22–24 gold samples actually earn
their cost. On the high-value positions — sharp middlegames with multiple
piece tensions — depth 22 differs meaningfully from depth 18. On the other
98% of positions, it does not.

**Training details.** Value target = `tanh(sf_eval_cp / 400)`. Policy target =
SF's best move (hard label). Loss = `CE(policy, sf_best_move) + MSE(value,
sf_eval_normalized)`. Learning rate 1e-4, 2–3 epochs over the corpus.

### 5.3 Stage 3: Self-play reinforcement learning

Per generation:
1. **Play N games** against self, storing MCTS visit distributions
   (400 sims/move, Dirichlet noise ε=0.25, α=0.3) and game outcomes.
2. **Train** on the accumulated buffer: cross-entropy on visit distribution +
   MSE on game result.
3. **Persist** to `selfplay.games_gen_N` and `selfplay.positions_gen_N`.

Training target for value is the **negamax-adjusted game result** (side-to-move
wins → +1, loses → -1, draw → 0). Training target for policy is the visit
distribution at the root — not a hard label, but a distribution reflecting
where MCTS actually explored.

Buffer size per generation: 500K–2M positions. Old generations (>12 back) are
pruned to prevent training against stale policy targets.

### 5.4 SPRT gates

Between generations, run a Wald SPRT against Stockfish at reduced strength.
Standard configuration:

- H0: new engine score ≤ 50% vs SF-skill-10 (reject)
- H1: new engine score ≥ 52% vs SF-skill-10 (accept)
- α = β = 0.05

Typical stop: 100–250 games. Saves 50–80% of evaluation compute vs fixed
1000-game matches. Rejected generations do not promote their checkpoint; the
next generation starts from the previous accepted checkpoint.

### 5.5 Freeze criteria

The base is frozen when **any** of the following is true:

1. **3 consecutive SPRT rejects.** The model has stopped improving.
2. **Elo window < 15 over 5 generations.** Plateau detection.
3. **Absolute Elo ≥ 3500.** Goal reached.

Once frozen, the base is not modified further. Stages 4–5 operate on the
frozen base.

**Why freeze at plateau, not at 3k?** The 3k Elo target is the distillation
ceiling (end of Stage 2). Freezing at 3k means freezing before RL has
contributed meaningfully. RL pushes the base from 3k to 3450–3550. The freeze
must wait for RL to converge.

### 5.6 NVFP4 mixed precision

On Blackwell (SM100+), training linear layers in NVFP4 via torchao 0.18
delivers ~1.6–2.2× throughput vs BF16, after `torch.compile` warmup. The last
15% of transformer blocks remain in BF16 (TorchTitan recipe) because the final
layers are numerically sensitive. Linear dimensions must be divisible by 128
— our configuration satisfies this automatically (d_model=1024, d_ff=3072,
heads at 512/1024).

Fallback to BF16 is transparent if Blackwell or torchao is unavailable.
`engine/fp4.py` isolates the API surface so torchao upgrades only touch one
module.

---

## 6. Personality adapters

### 6.1 Why LoRA, not full fine-tunes

Data reality:

| Legend | Games in PGN Mentor |
|---|---|
| Morphy | ~400 |
| Fischer | ~950 |
| Petrosian | ~2,000 |
| Kasparov | ~2,500 |
| Tal | ~2,800 |
| Carlsen | ~4,000+ |

Fine-tuning a 380M-parameter network on 400 Morphy games is a 10⁶:1
parameter-to-data ratio. Catastrophic overfitting.

LoRA adds low-rank decomposition ΔW = BA · (α/r) to frozen weights. With
r=4, only ~3M trainable params — matching the Morphy corpus size. With
r=32, ~24M params — matching Carlsen's corpus.

### 6.2 Rank scaling

| Legend | Rank | Trainable params | Learning rate |
|---|---|---|---|
| Morphy | 4 | ~3M | 5e-5 |
| Fischer | 8 | ~6M | 1e-4 |
| Petrosian | 8 | ~6M | 1e-4 |
| Kasparov | 16 | ~12M | 1e-4 |
| Tal | 16 | ~12M | 1e-4 |
| Carlsen | 32 | ~24M | 2e-4 |

Adapter storage: ~20 MB per adapter at r=16. Six adapters = 120 MB vs
~9 GB for six full checkpoints.

### 6.3 Adapter validation gate

An adapter is valid if its held-out move-match exceeds a per-legend threshold:

| Legend | Threshold |
|---|---|
| Morphy | 45% |
| Petrosian | 45% |
| Tal | 50% |
| Kasparov | 50% |
| Fischer | 50% |
| Carlsen | 50% |

Below threshold = undertrained. Above 75% = overfit. Target window 50–65%.

**The panel does not enable until all six adapters pass.** Training the panel
with an incomplete or undertrained adapter set produces misleading commentary
("Morphy recommends Nf3" when the Morphy adapter is actually a partially-trained
noise generator). The validation gate enforces this.

### 6.4 Sequential phase routing

For gameplay style blending (secondary feature), the router applies different
adapter weights by game phase:

Plies 1–8: 50% base + 50% Morphy (development race)
Plies 9–16: 50% base + 50% Petrosian (consolidation)
Plies 17+: 40% base + 20% Tal + 20% Kasparov + 20% Fischer
Endgame (<12 pieces): 50% base + 25% Fischer + 25% Carlsen


**Why sequential (not simultaneous) for opening:** Morphy and Petrosian are
opposites. Morphy races pieces forward; Petrosian prevents counterplay. A
simultaneous 50/50 blend dilutes both into a weak compromise. Sequential
routing captures what strong players actually do: race first, then consolidate.

**Strength cost:** adapters intentionally trade strength for style:

| Configuration | Elo vs base |
|---|---|
| 100% base (balanced) | 0 (peak) |
| Base + 1 adapter 50/50 | -30 to -60 |
| Base + phase-routed | -40 to -80 |

This is expected. Users choose "balanced" for max strength, specific profiles
for personality play.

---

## 7. The Legends panel

### 7.1 Seven voices

The panel shows seven analyses of the current position:

- **Base engine** — Trinstinctus's actual MCTS recommendation (600 sims).
  This is the engine's answer, not a voice.
- **Six legends** — Morphy, Petrosian, Tal, Kasparov, Fischer, Carlsen.
  Each adapter's top policy move, one forward pass per adapter (~30ms total).
- **Stockfish** — objective oracle at depth 22. The reference all others are
  compared against.

### 7.2 Persistent service architecture

Loading seven models takes ~10 seconds. Per-request subprocess would make the
panel unusable. Instead, `engine/panel_service.py` runs as a long-lived
process, loading all models once at startup and answering analysis requests
over stdin/stdout JSON lines.

- **Startup cost:** ~10s.
- **Per-request cost:** ~350–600ms (Stockfish at depth 22 dominates).
- **Graceful startup:** if an adapter is missing, the panel shows the voices
  it has (down to base + SF alone). No crash, no error state.
- **Restart after retraining:** the service must be restarted to pick up new
  adapters. The Rust backend spawns it lazily on first panel request, so
  restarting the app is sufficient.

### 7.3 LLM synthesis

The panel's raw JSON is fed to DeepSeek-R1 (local Ollama) for prose
synthesis. The prompt is engineered to mention each legend **only when they
differ from Stockfish**, which is where pedagogical value lives. Legends
who agree with the objective analysis are noted in a consensus line, not
given their own paragraph.

**Graceful degradation:** if Ollama is unreachable, commentary falls back to a
deterministic summary. The panel still renders. Users who want the tutor
experience enable Ollama; those who do not get a fast panel without the 30W
LLM overhead.

---

## 8. Hardware reference

| Component | Spec | Reason |
|---|---|---|
| CPU | AMD Ryzen 9 9950X3D (dual-die 3D V-Cache) | PGN ingestion (528M inserts) and self-play orchestration are CPU-bound; L3 cache hits dominate |
| Display GPU | NVIDIA RTX A2000 12GB | Passed through to Windows 11 VM for chess GUIs (Fritz, etc.) |
| Compute GPU | NVIDIA RTX PRO 5000 Blackwell 72GB | Headless Linux-side training; NVFP4 tensor cores; 72GB allows 1.4B MoE with batch 4096 |
| RAM | 128GB DDR5-5400 (2×64GB) | PostgreSQL cache + batch prefetch; dual-channel integrity preserved |
| Storage | Gen5 NVMe SSD (ext4) | PostgreSQL random IOPS; checkpoint read/write |
| OS | Linux Mint (bare-metal) + Windows 11 VM (QEMU/KVM) | Native Linux training; Windows chess GUIs via A2000 passthrough |

**Graceful degradation:** any single CUDA GPU with ≥24GB VRAM runs the full
pipeline. Smaller GPUs use the dense 380M configuration instead of MoE. CPU-only
PyTorch works but is ~10× slower and not recommended for production.

---

## 9. Honest Elo trajectory

Solo Blackwell (reference hardware, no cloud):

| Milestone | Time | Elo | Verification |
|---|---|---|---|
| Supervised bootstrap | Day 8 | ~2900 | vs SF skill-5 |
| Distillation (depth 18 bulk) | Day 13 | ~3050 | vs SF skill-10 |
| Distillation (depth 24 refine) | Day 14 | ~3100–3150 | SPRT vs SF skill-10 |
| Self-play gen 10 | Month 2 | ~3200 | SPRT vs SF skill-15 |
| Self-play gen 30 | Month 4 | ~3320 | SPRT vs SF thread-4 |
| Lc0 T79 parity | Month 6 | ~3400 | head-to-head |
| SF single-thread parity | Month 9–12 | ~3450–3550 | 45–55% vs SF-17 thread-1 |

With cloud augmentation (16× H100 for 30 days, ~$15K):

| Milestone | Time | Elo |
|---|---|---|
| Distillation + initial RL | Day 30 | ~3350–3450 |
| Continued RL | Month 3 | ~3500–3600 |

**The ceiling:** no open-source single-workstation project has beaten SF-17 at
16+ threads. The realistic win-condition is:
- **Beat Lc0 T79** at equal compute (achievable by month 6)
- **45–55% vs SF-17 single-thread** at classical time control (achievable by
  month 9–12)
- **Match SF-17 skill-15** in rapid time control (achievable by month 3)

Dominance over Stockfish at full strength is not a realistic claim for any
open-source project on single workstation hardware.

---

## 10. Reproducibility and observability

### 10.1 Checkpoint lineage

Every checkpoint is stored in `engine_state.checkpoints` with:
- SHA256 content hash
- Parent checkpoint ID (lineage chain)
- Generation, training step, phase
- Config JSON (hyperparameters, seed, git commit)

### 10.2 Structured logging

Every module uses `structlog` with ISO-8601 UTC timestamps, module names, and
contextual fields (generation, step, Elo). JSON in production; text in dev.
A Grafana/Loki instance can filter by `generation=42` across the entire
pipeline.

### 10.3 Progress telemetry

`ops.training_progress` is written at 1 Hz by the orchestrator. Fields include
stage, progress, loss, games completed, SPRT LLR/status, Elo estimate + stderr,
and ETA. The Helix Training Monitor polls this at 0.5 Hz.

### 10.4 Data manifests

Corpora are tracked in `data/manifests/*.json` with SHA256, size, source URL,
and ingest commit. A corpus can be verified against its manifest without
re-downloading.

---

## 11. Failure modes and recovery

| Failure | Detection | Recovery |
|---|---|---|
| Orchestrator crash | systemd restart | Resumes from last checkpoint |
| Mid-generation crash | Partial selfplay.games_gen_N | `--resume` skips already-played games |
| Adapter training OOM | torch error logged | Continue to next adapter |
| Syzygy file missing | FileNotFoundError | Fatal — user must provide TBs |
| NVFP4 conversion fails | Logged warning | Falls back to BF16 transparently |
| DB connection lost | retry decorator | 3 retries with backoff |
| SPRT timeout | status='timeout' | Orchestrator logs and continues |
| Ollama unreachable | reqwest timeout | Empty commentary, "offline" indicator |

---

## 12. Future work

**High priority:**
1. **Gumbel-AlphaZero enablement** — SPRT-verify the 3× sample efficiency
   claim, then enable by default.
2. **`--resume` for self-play** — crash recovery without recompute.
3. **Multi-GPU training** — if the user adds a second Blackwell.
4. **NVFP4 for MoE layers** — currently dense only; MoE NVFP4 is a torchao
   roadmap item.

**Medium priority:**
5. **Learned phase router** — Stage 6. Requires freeze-then-train pattern;
   deferred until hardcoded router is validated.
6. **Cross-engine opening book extraction** — mine SF-17 wins for opening
   book augmentation.
7. **7-piece Syzygy** — requires dedicated 20TB NVMe; not for reference
   hardware.

**Aspirational:**
8. **Full self-play from scratch** — only feasible with cloud compute.
9. **Multi-iteration MoE with learned routing** — research direction.

---

## 13. References

- Silver et al., "A general reinforcement learning algorithm that masters
  chess, shogi, and Go through self-play," *Science*, 2018.
- Lc0 project, github.com/LeelaChessZero/lc0.
- Stockfish NNUE, stockfishchess.org.
- Danihelka et al., "Policy improvement by planning with Gumbel," ICLR 2022.
- Hu et al., "LoRA: Low-Rank Adaptation of Large Language Models," 2021.
- NVIDIA, "Pretraining Large Language Models with NVFP4," 2025.
