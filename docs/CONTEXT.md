# Helix Project — Continuation Context

Continuing a local-first chess workstation project. Read fully before responding.

## Identity

- **Project**: Helix (desktop app)
- **Engine**: Trinstinctus (neural MCTS — disabled on current hardware, gated)
- **Tutor**: The Legends (7-voice panel — gated)
- **Coach**: Stockfish + DeepSeek-R1 14B via Ollama (active)

## Hardware

- Windows 11, Core i9 13th-gen, 32GB RAM, NVIDIA RTX A2000 12GB
- Python 3.13 venv at `.venv`
- PostgreSQL 18 (port 5432, user `trin`, db `helix_db`, pw `trin_dev`)
- Node 22, npm 10, Rust 1.99 (MSVC), VS 2022 Build Tools (C++ workload installed)
- Tauri 2.12, React 18, Vite 5
- Stockfish 19 at `C:/temp/stockfish/stockfish.exe`
- Ollama running, DeepSeek-R1 14B pulled

## Migrations

14 migrations applied. **Data is loaded:**
- `gm.eco_dict`: 3865 (PK is now (eco_code, pgn))
- `app.gambits`: 1315
- `app.opening_lines`: 3865

**Migration runner has new flags:**
- `--resync` / `--resync-file NAME` — update hash without re-apply (for line-ending/BOM changes)
- `--force-reapply NAME` — delete record so migration re-runs (only if idempotent)
- `--mark-applied NAME` — record as applied without running SQL (for manual psql application)

**Migration 14 was applied manually in psql, then recorded via `--mark-applied`.**
Migrations 01, 03-06, 08-13 are NOT idempotent. If hashes invalidate, use `--resync-file` (content unchanged) not `--force-reapply`.

## Design Decisions (locked)

1. Free closed-source (proprietary freeware)
2. Feature-gated via `.env` (`HELIX_ENGINE_ENABLED`, `HELIX_TRAINING_ENABLED`, `HELIX_COACH_LLM_ENABLED`)
3. Local-first, no telemetry
4. Two-tier SF distillation (depth 18 bulk, depth 24 refine) — deferred until Blackwell hardware
5. NVFP4 with BF16 tail — deferred
6. Sequential opening routing (Morphy 1-8, Petrosian 9-16)
7. Transposition-aware gambit matcher
8. Subprocess cwd = repo root (`repo::find_root()` + `cmd.current_dir`)
9. Capability probe runs from repo root

## Resolved Bugs (this session)

- Board movement (ChessBoardView rewritten — no more AnnotationLayer blocking)
- Coach/god_mode/panel subprocess cwd
- God mode DB query (removed — was querying non-existent column)
- Engine/Training tabs ErrorBoundary (App.tsx uses `<Capability>`)
- Tokio runtime bug in `lib.rs` (uses `tauri::async_runtime::block_on`)
- `common/db.py` import ordering (atexit at bottom)
- ECO URL (`all.tsv` never existed — fetch a-e individually)
- Windows temp path (`tempfile.gettempdir`)
- Personal game 404 (clear error message)
- eco_dict PK was (eco_code) → migration 14, 3865 openings now loaded
- Gambit/opening ID truncation → appended pgn md5 suffix

## Known Issues (remaining)

- **PlayTab hardcodes `"stockfish"`** for SF path — should use `caps.stockfish_path`
- **Arrows (right-drag)** not implemented — only right-click highlight
- **No personal games imported** — needs real Lichess/Chess.com username
- **Accuracy worker not run** — WeaknessFinder empty until `python -m training.accuracy_worker` runs
- **Puzzles not loaded** (requires `zstd` + 450MB download)
- **Git push to remote failing** — SSH port 22 blocked; try port 443 or HTTPS

## File layout (key files)

app/src-tauri/src/
lib.rs — command registration, Tokio setup
capabilities.rs — runtime probe (Python subprocess)
repo.rs — find_root() for subprocess cwd
coach.rs — Stockfish + LLM coach
play.rs — Play vs Stockfish
import.rs — Personal game import
god_mode.rs — Stockfish + brilliant candidates
db.rs — pool + AppState
errors.rs — typed AppError
analytics.rs — gambit/ECO/accuracy analytics
personal.rs — personal game stats
weakness.rs — weakness finder
annotations.rs — board annotations
eval_graph.rs — eval curve data
daily_puzzle.rs — daily puzzle + streaks
panel_service.rs — Legends panel bridge (gated)
training.rs — training monitor (gated)
training_tools.rs — opening/puzzle trainers
engine_bridge.rs — style weights, adapters (gated)
opening_explorer.rs — opening analytics


## TODO for Next Session (priority order)

1. **Wire `caps.stockfish_path` into PlayTab** — currently broken
2. **Import real personal games** and run accuracy worker
3. **Load puzzles** (install zstd, then `python -m ingest.puzzle_loader --source lichess`)
4. **Implement arrow annotations** (right-drag on board)
5. **Fix git push** (SSH port 443 or HTTPS PAT)
6. **Test all tabs end-to-end** with real data
7. First-run wizard for missing dependencies

## Commands Quick Reference

```powershell
# Activate venv
cd C:\Users\rahul\source\repos\helix
.\.venv\Scripts\Activate.ps1

# Run app
cd app; npm run tauri dev

# Rebuild after Rust changes
cd app/src-tauri; cargo check

# Migrations (normal)
python migrations/run_migrations.py

# Migration admin
python migrations/run_migrations.py --resync-file NAME.sql    # line-ending fix
python migrations/run_migrations.py --mark-applied NAME.sql   # applied manually
python migrations/run_migrations.py --force-reapply NAME.sql  # idempotent re-run

# Data import
python -m ingest.eco_loader
python -m ingest.gambit_registry
python -m ingest.opening_lines
python -m ingest.personal_games --platform lichess --username YOUR_HANDLE
python -m ingest.puzzle_loader --source lichess

# Accuracy
python -m training.accuracy_worker
```

## Response Style
Direct, technical, no filler

Full runnable code with comments

Flag honest caveats

One-line fixes preferred

Do NOT re-derive design decisions
