# Helix Project — Continuation Context

I am continuing a large project from a previous chat. This document is the
complete context. Read it fully before responding. Do not ask clarifying
questions about anything covered here.

## Project Identity

- **Project name**: Helix (the application)
- **Engine name**: Trinstinctus (the neural MCTS engine core)
- **Tutor name**: The Legends (a 7-voice analysis panel: 6 LoRA personality
  adapters — Morphy, Petrosian, Tal, Kasparov, Fischer, Carlsen — plus
  Stockfish as the objective oracle)
- **Goal**: An enterprise-grade, local-first chess engine + coaching
  workstation. Target: beat Lc0, compete with Stockfish 17 single-thread
  (45-55% score is the realistic target; dominance over SF is not).

## Hardware

- AMD Ryzen 9 9950X3D (dual-die 3D V-Cache)
- NVIDIA RTX A2000 12GB (display, passthrough to Windows VM)
- NVIDIA RTX PRO 5000 Blackwell 72GB (headless compute, NVFP4)
- 128GB DDR5-5400 (2×64GB)
- Linux Mint host + Windows 11 QEMU/KVM VM (A2000 passthrough)

## Stack (pinned)

- Python 3.15
- PyTorch 2.14 (NVGEMM, CUTLASS-SM100)
- torchao 0.18 (NVFP4 dense-linear training — prototype API)
- PostgreSQL 18 (io_uring async I/O)
- psycopg3 (not psycopg2)
- Tauri 2.12 + React 18 + Rust 1.90 (MSRV)
- python-chess 1.11.2
- DeepSeek-R1 32B via local Ollama (for The Legends commentary)

## Training Pipeline (locked)

    Stage 1  Supervised bootstrap         13.2M GM games              ~2900 Elo
    Stage 2a Distillation bulk            SF depth 18, 2M positions   ~3050-3100
    Stage 2b Distillation refinement      SF depth 24, 100K positions ~3100-3150
    Stage 3  Self-play RL                 MCTS-generated targets      continuous
    Stage 3a SPRT gates                   Stockfish evaluation        gates each gen
    ▶ Freeze                             Plateau detection           ~3450-3550 Elo
    Stage 4  Six LoRA adapters            Per-legend PGNs             personality only
    Stage 4a Adapter validation           Held-out move-match gates the panel
    Stage 5  Sequential phase router      Runtime config              blended play
    Stage 6  Learned adaptive router      (deferred, optional)

**Freeze trigger** is plateau detection, NOT a fixed Elo. Freeze when any of:
- 3 consecutive SPRT rejects
- Elo window < 15 over 5 generations
- Absolute Elo ≥ 3500

## Key Design Decisions (do not re-litigate)

1. **Sequential opening routing** — Morphy plies 1-8 (development race),
   Petrosian plies 9-16 (consolidation). Sequential, not 50/50 blend,
   because the styles are opposites.

2. **Two-tier distillation** — Depth 18 for the 2M bulk corpus (4-6 days),
   depth 24 for the 100K refinement corpus (1-2 days). Depth 22 for 2M
   would take a month; the tiered approach gets the same quality with
   bounded time.

3. **NVFP4 with BF16 tail** — Last 15% of transformer blocks stay in BF16
   (TorchTitan recipe). torch.compile required for competitive throughput.

4. **Gumbel-AlphaZero is OPT-IN** — Default is PUCT. Gumbel must be SPRT-
   verified at 400 sims before enabling in production. The paper claims ~3×
   sample efficiency; validate empirically.

5. **Syzygy multi-path auto-detect** — 3-4-5 / 6 / 7 auto-discovered at
   startup. Max probe pieces scales with installed set. Midgame probing
   was considered and RULED OUT — no proven benefit for 10+ piece positions.

6. **LoRA rank scales with dataset size** — Morphy r=4 (400 games), Carlsen
   r=32 (4000+ games). Full fine-tuning would catastrophically overfit
   Morphy; LoRA is the only viable approach.

7. **Adapters trained AFTER freeze, in isolation, then validated** — The
   panel does not enable until all 6 adapters pass their held-out
   move-match thresholds. Partial adapter sets degrade gracefully (panel
   shows base + SF only).

8. **Adapters do not affect the base** — The base is frozen at plateau.
   The training loop for stages 4+ operates on the frozen base. If adapter
   training fails, the engine still ships at full strength.

9. **Transposition-aware gambit matcher** — Match by position key
   (FEN minus halfmove/fullmove), not prefix. Prefix-matching under-counts
   by 30-40%.

10. **Self-play retention** — 12 generations kept in full, generations
    13-20 sampled to 1% (regression corpus), 21+ dropped. Enforced by
    selfplay.prune_old_generations.

11. **Panel service is a persistent subprocess** — Loads base + adapters +
    SF once (~10s), answers over stdin/stdout JSON lines (~350-600ms per
    request). Per-request subprocess would be unusable.

12. **Analytics unified across personal + eval sources** — Same tables,
    same views. "How does Trinstinctus do vs SF with the Evans Gambit?"
    and "how does the user do with the KID?" are the same query shape.

13. **Accuracy worker uses N+1 SF calls, not 2N** — One eval per position,
    loss = e_i + e_{i+1}_opp. Halves the SF cost.

## Current State

The complete project was delivered across 4 batches:

- **Batch 1**: Config (settings, logging), common (db, retry), 11 migrations
- **Batch 2**: Engine (16 files — encoders, model, MCTS, Gumbel, LoRA, FP4,
  Syzygy, phase router, gambit matcher, panel service, UCI engine)
- **Batch 3**: Training (15 files) + Ingest (8 files)
- **Batch 4**: Tauri app (Rust + TypeScript + config), tests, scripts

**Total: ~95 files, ~9000 lines.** Full manifest is in the git repo at
`docs/MANIFEST.md`. White papers at `docs/ENGINE_WHITEPAPER.md` and
`docs/APP_WHITEPAPER.md`.

The code is at: [REPO URL — fill in]

## What I Need Next

[FILL IN: the specific task for this session — e.g., "the panel service
is failing to load adapters when X happens", or "help me wire the UCI
subprocess into analyze_position", or "add feature Y"]

## Response Style

- Direct, technical, no filler.
- If a design decision here needs revisiting, say so explicitly — do not
  silently deviate.
- Deliver complete, runnable code with comments explaining *why*.
- Flag honest caveats: "this doesn't work yet", "this is a placeholder",
  "this won't scale past X".
- Do not re-derive the design. The decisions above are locked. Focus on
  implementation, debugging, and refinement.
