## Helix

A neural MCTS chess engine and multi-voice coaching workstation.
Local-first, self-hosted, no cloud dependencies.

**Trinstinctus** is the engine. **The Legends** are the seven-voice tutor
(Morphy, Petrosian, Tal, Kasparov, Fischer, Carlsen, Stockfish). Helix is
the application that binds them together with opening analytics, puzzle
training, personal game review, and gambit tracking.

---

## What's here

- **Trinstinctus** — Transformer-based AlphaZero-family engine.
  NVIDIA Blackwell NVFP4 training, Gumbel-AlphaZero root selection, six-piece
  Syzygy at every leaf, Mixture-of-Experts FFN. Supervised bootstrap on 13.2M
  GM games → two-tier Stockfish distillation → continuous self-play RL.
- **The Legends** — Seven-voice tutor. Six LoRA personality adapters plus
  Stockfish as objective oracle. Persistent analysis service; ~600ms per panel.
  Graceful degradation when any voice is missing.
- **Helix app** — Tauri 2.12 + React 18. Seven tabs: play, training, openings,
  puzzles, analysis, gambits, settings. Personal game import from Chess.com
  and Lichess. Opening analytics, gambit detection, accuracy ranking.
- **God Mode** — Toggle on the play board. Highlights Stockfish best,
  Trinstinctus best, and Stockfish-verified brilliant moves with one-click
  line explanation.

---

## Quick start

```bash
git clone git@github.com: <USER_NAME>/helix.git
cd helix
```
# 1. System deps (Ubuntu 24.04)
```bash scripts/setup.sh```

# 2. Configure
```bash cp .env.example .env && $EDITOR .env```

# 3. Migrate
```bash python migrations/run_migrations.py```

# 4. Download data
```bash python ingest/eco_loader.py```
```bash python ingest/bulk_loader.py ~/pgn/master --source lumbra_gb```
```bash python ingest/bulk_loader.py ~/pgn/legends --source pgn_mentor```
```bash python ingest/opening_lines.py```
```bash bash scripts/download_syzygy.sh ~/syzygy 345```

# 5. Train
```
bash python -m training.train_supervised --epochs 4```
bash python -m training.distill --positions 2000000 --depth 18```
bash python -m training.distill_train --tsv /tmp/sf_distill_18.tsv --epochs 2
bash python -m training.distill --positions 100000 --depth 24
bash python -m training.distill_train --tsv /tmp/sf_distill_24.tsv --epochs 1 \
    --base checkpoints/distilled_bulk.pt --out checkpoints/distilled.pt
```

# 6. Train personality adapters (after base converges)
```
for legend in morphy fischer petrosian kasparov tal carlsen; do
  python -m training.train_adapters --base checkpoints/distilled.pt --legend $legend
done
bash python -m training.validate_adapters --base checkpoints/distilled.pt
```

# 7. Launch daemons
```
sudo systemctl enable --now helix-orchestrator@$USER
sudo systemctl enable --now helix-accuracy-worker@$USER
```

# 8. Launch app
```
cd app && npm install && npm run tauri dev
```

## Architecture
┌──────────────────────────────────────────────────────────────────┐
│  Helix app (Tauri 2.12 + React 18)                               │
│    Play · Training · Openings · Puzzles · Analysis · Gambits     │
└──────────────────────────┬───────────────────────────────────────┘
                           │ tauri::command IPC
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Rust backend (tokio + sqlx)                                     │
│    panel_service · analytics · personal · god_mode · training    │
└──────────────────────────┬───────────────────────────────────────┘
                           │ SQL + JSON-lines subprocess
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  PostgreSQL 18 (io_uring async I/O)                              │
│    gm · selfplay · eval · engine_state · ops · app · analytics   │
└──────────────────────────┬───────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┬─────────────────┐
        ▼                  ▼                  ▼                 ▼
  ┌──────────┐      ┌─────────────┐    ┌──────────┐      ┌──────────┐
  │ engine/  │      │ training/   │    │ ingest/  │      │ scripts/ │
  │ MCTS+NN  │      │ PyTorch     │    │ PGN load │      │ setup    │
  └────┬─────┘      └──────┬──────┘    └──────────┘      └──────────┘
       │                   │
       └────────┬──────────┘
                ▼
       ┌─────────────────┐    ┌───────────────┐
       │ Blackwell 72GB  │    │ DeepSeek-R1   │
       │ NVFP4 training  │    │ (Ollama)      │
       └─────────────────┘    └───────────────┘

## Training Pipeline
Stage 1  Supervised bootstrap        13.2M GM games              ~2900 Elo
Stage 2  Distillation (two-tier)     SF depth 18 + depth 24      ~3050-3150 Elo
Stage 3  Self-play RL                MCTS-generated targets      continuous
Stage 3a SPRT gates                  Stockfish evaluation        gates each gen
▶ Freeze                             Plateau detection           base ~3450-3550 Elo
Stage 4  Six LoRA adapters           Per-legend PGNs             personality only
Stage 4a Adapter validation          Held-out move-match         gate to panel
Stage 5  Sequential phase router     Runtime config              blended play

The base engine is the priority. Adapters are trained after freeze and do not
affect the base. If adapter training fails, the engine still ships at full
strength.

## Hardware Reference
|Component|	Spec|
|---------|-----|
|CPU|	AMD Ryzen 9 9950X3D (dual-die 3D V-Cache)
|Display GPU|	NVIDIA RTX A2000 12GB
|Compute GPU|	NVIDIA RTX PRO 5000 Blackwell 72GB, NVFP4 tensor
|RAM|	128GB DDR5-5400 (2×64GB, dual-channel)
|Storage|	Gen5 NVMe SSD
|OS|	Linux Mint (bare-metal) + Windows 11 VM (A2000 passthrough)
Minimum viable: any CUDA GPU with ≥24GB VRAM. Throughput will be lower;
the pipeline runs, the training will be slower.

## Honest Capabilities
What Helix beats today (as of blueprint lock):

Every human player in history (aggregate)

Every MCTS engine at or below Lc0 T79 in equal-compute play

Stockfish 17 single-thread at slow time controls (45–55% score, target by month 9–12)

What Helix does not beat:

Stockfish 17 at 16+ threads in open tournaments. No open-source project
on a single workstation has closed this gap since 2022.

Timeline (solo Blackwell, no cloud):

Day 8: ~2900 Elo (supervised complete)

Day 14: ~3050–3150 Elo (distillation complete)

Month 6: ~3400 Elo (Lc0 T79 parity)

Month 9–12: ~3450–3550 Elo (SF single-thread parity, stage 6 optional)

The white papers state the honest trajectory in detail. No claims are made
that cannot be verified against the training logs in engine_state.checkpoints
and eval.elo_history.

## Reproducibility
Every training script sets seeds via engine.reproducibility.seed_everything.
Every checkpoint is stored in engine_state.checkpoints with SHA256, parent
checkpoint ID, training step, phase, and config JSON. A run can be traced
forward and backward through the database alone.

Data corpora are tracked as manifests in data/manifests/ with SHA256 and
source URLs. If a corpus is lost, the manifest documents exactly where to
re-download and what to verify.

## Contributing
See .github/workflows/ci.yml for required checks:

ruff check and ruff format

pytest tests/

npx tsc --noEmit

cargo check and cargo clippy

Commits follow Conventional Commits. Pre-commit hooks enforce this. Every PR
must pass CI on main.

## License
MIT.
