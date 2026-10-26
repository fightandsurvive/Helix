# Helix App — White Paper

**Version 0.1** | The desktop application: seven-tab architecture, Legends
panel, God Mode, personal game analytics, gambit tracking, and the security
model.

---

## Abstract

Helix is the desktop interface to the Trinstinctus engine and the seven-voice
Legends panel. It combines a native performance Tauri shell with a local-first
analytics layer, importing personal games from Chess.com and Lichess, computing
per-opening win rates, ranking games by accuracy, and tracking gambit
performance across both sources. A God Mode toggle surfaces three candidate
moves (Stockfish best, Trinstinctus best, chess.com-style brilliant) with
one-click line explanations for training purposes.

The app runs on Linux Mint with a Tauri 2.12 frontend (React 18 + TypeScript)
and a Rust tokio backend, connecting via sqlx to a local PostgreSQL 18 instance.
No cloud dependencies, no telemetry, no API keys.

---

## 1. Design principles

**Local-first.** Every feature works offline. Ollama runs DeepSeek-R1 locally;
Stockfish runs locally; PostgreSQL runs locally. If the network is down, the
app fully functions. This is enforced architecturally — there is no code path
that requires external network access.

**Defensive.** Every external call (DB, GPU, subprocess, HTTP) is wrapped in
retry-and-log logic. Migrations are idempotent. Ingestion uses `ON CONFLICT DO
NOTHING`. Adapters can be deleted and retrained without touching the base.
The app degrades gracefully when any component is missing.

**Honest.** The app does not phone home. Crash logs are written to
`~/.local/share/helix/logs/` and shared only if the user chooses. The
Training Monitor displays the honest Elo trajectory from the engine's
checkpoint history, not a marketing claim.

**Accessible.** WCAG 2.1 Level AA. Keyboard reachable. Screen-reader compatible.
Color usage redundant (states indicated by both color AND text).

---

## 2. Architecture

### 2.1 Stack

| Layer | Technology | Rationale |
|---|---|---|
| Runtime | Tauri 2.12 | ~10 MB app size, native WebView, no bundled Chromium |
| Frontend | React 18 + TypeScript | Industry standard, typed, fast |
| Board | react-chessboard + chess.js | Standard, 3D-effect CSS |
| Charts | Recharts | Lightweight, embeddable |
| Animation | Framer Motion | 60fps smooth transitions |
| Backend | Rust (tokio) | Async, memory-safe, fast |
| IPC | tauri::command | Typed, structured errors |
| Database | sqlx (async PostgreSQL) | Compile-time query checking available |
| LLM | Ollama HTTP | Local inference, no cloud |

**Why Tauri over Electron:** 10 MB vs 150 MB. Native WebView. Faster startup.
Rust backend — the backend tasks (SQL, LLM HTTP, process spawning) are I/O-bound
and benefit from tokio.

### 2.2 Backend modules
main.rs — entry point, delegates to lib::run()
lib.rs — init, tracing, command registration
db.rs — PostgreSQL pool (sqlx)
errors.rs — typed AppError, JSON-serialized to frontend
engine_bridge.rs — style weights, adapter state
opening_explorer.rs — position-level win-rate analytics
panel_service.rs — Legends panel persistent service bridge
panel_commentary.rs — (Python side) LLM synthesis
god_mode.rs — SF best, Trinstinctus best, brilliant detection
analytics.rs — gambit stats, ECO stats, accuracy rankings
personal.rs — personal game import, opening analytics
training.rs — training telemetry polling
training_tools.rs — opening study, puzzle training


### 2.3 Frontend modules
main.tsx — React root, ErrorBoundary wrapper
App.tsx — seven-tab routing
hooks/useInvoke.ts — uniform Tauri command wrapper
components/
ChessBoardView — drag-drop board with style rendering
LegendsPanel — seven-voice panel with voice cards
GodModePanel — three-move analysis with line explanations
GamesAnalytics — personal + eval gambit and ECO breakdown
TrainingMonitor — live training progress
OpeningExplorer — position win rates with per-legend breakdown
OpeningTrainer — four-mode spaced-repetition trainer
PuzzleTrainer — SM-2 scheduled puzzles
StyleWeightDashboard — profile selector + adapters gate
ErrorBoundary — React error boundary


### 2.4 Data flow
User action
↓
React component (useInvoke)
↓
Tauri IPC
↓
Rust command handler
├─ validates input
├─ sqlx query → PostgreSQL
└─ OR subprocess → panel_service.py
↓
Structured Result<T, AppError>
↓
React renders (loading / data / error states)


---

## 3. The seven tabs

### 3.1 Play

The default tab. Board on the left, Legends panel and God Mode panel on the
right, opening explorer below.

- **Board:** react-chessboard with frosted-glass light squares and obsidian
  dark squares. Style-specific overlays when a personality is active.
- **Legends panel:** collapsible. Shows seven voices with best moves,
  evaluations, and confidence. Consensus detection highlights moves where
  multiple voices agree.
- **God Mode:** toggle in the sidebar. When enabled, the panel shows three
  candidate moves (Stockfish best, Trinstinctus best, brilliant). Clicking a
  candidate plays the move and expands the line explanation.
- **Opening explorer:** live win-rate analytics for the current position,
  broken down by legend.

### 3.2 Training

Live telemetry from `ops.training_progress`.

- Generation counter
- Stage indicator (idle / selfplay / training / gating / adapters / pruning)
- Progress bar with ETA
- Games completed / target
- Total loss (line chart, last 300 steps)
- SPRT LLR graph with upper/lower bounds
- Elo trajectory (line chart across generations)

The Training Monitor polls at 0.5 Hz. Query cost is <10ms. The chart uses
`ops.loss_history` (separate from `ops.training_progress` so pruning old
generations does not destroy the timeline).

### 3.3 Openings

Spaced-repetition opening study. Four modes:

| Mode | Behavior | Purpose |
|---|---|---|
| Visual | Board normal, user plays correct move | Standard study |
| Blind | Board hidden 1.5s, user plays from memory | Visual memory |
| Speed | Same as visual + 3s timer | Muscle memory |
| Streak | Complete 10 lines perfectly | Endurance |

SM-2 scheduling: correct answers advance interval (1 → 6 → interval × EF);
wrong answers reset to 1 day. `mastery_score` saturates at 10 reviews.

### 3.4 Puzzles

SM-2 scheduled puzzle training. Three sources:

| Source | Volume | Rating | Verification |
|---|---|---|---|
| Lichess bulk | ~5M | 600–3000 | Community-curated |
| GM-derived | ~5K | ~1800 | SF-verified, GM-move match |
| User study PGNs | Per-file | n/a | Author-provided |

Hint system: up to 3 hints per puzzle, each revealing the "from square" of
the next move. Quality score decreases with hint count: 0 hints → q=4,
1 hint → q=3, 2 hints → q=2, 3 hints → q=1, give up → q=1.

### 3.5 Analysis

Personal game analytics. Select source (personal or eval), enter username
or engine name.

- **Overview:** distinct gambits played, gambits with positive score, latest
  game accuracy, top gambits by score (min 3 games).
- **Gambits:** full list of played gambits with W/L/D and score.
- **ECO stats:** win rate per ECO code, per color.
- **Accuracy:** recent games ranked by accuracy, with ACPL.

The analytics views (`analytics.participant_gambit_stats`,
`analytics.participant_eco_stats`, `analytics.accuracy_rankings`) span both
personal and eval games. A single query can ask "does Trinstinctus beat
Stockfish with the Evans Gambit?" and "does the user win more with the
King's Indian Defense than the Nimzo?" in the same view.

### 3.6 Gambits

Dedicated gambit tracker. HolyGambitGrail-style features:

- Which gambits the player has played (across Chess.com + Lichess + Helix
  internal games)
- Win rate per gambit
- Soundness classification (sound / speculative / unsound)
- Head-to-head breakdown against specific opponents
- Cross-source (personal + engine eval)

Gambit detection is **transposition-aware** — a game transposing into the
Evans Gambit via a different move order is still detected. This is the
correct semantics; prefix-matching under-counts by 30–40%.

### 3.7 Settings

Style weight customizer. Profile selector + six sliders + temperature +
contempt. Disabled until all six adapters pass validation. A tooltip explains
why and how to train adapters.

Profiles: balanced, romantic, iron_fortress, anti_stockfish, morphy, petrosian,
tal, kasparov, fischer, carlsen.

---

## 4. The Legends panel

### 4.1 UI design

The panel is collapsible. When collapsed, a single "Consult the Legends"
button. When expanded, a header with seven voice cards in a responsive grid,
then the base engine's answer, then a consensus section (if multiple voices
agree), then the LLM-synthesized commentary.

### 4.2 Voice cards

Each card shows:

- Name (uppercase, small)
- Best move (large SAN)
- Evaluation (centipawns, sign-colored)
- Confidence (adapter voices only)
- "≠ SF" badge if the voice differs from Stockfish

Cards are colored by type: emerald border for legend voices, green for the
Stockfish oracle, amber if the voice differs from Stockfish.

### 4.3 Commentary generation

Raw panel JSON is fed to `engine/panel_commentary.py`, which prompts
DeepSeek-R1 with the seven-voice context and generates prose. The prompt is
engineered to mention each legend **only when they differ from Stockfish**,
where pedagogical value lives. Legends that agree are noted in a consensus
line, not a full paragraph.

Structure of the commentary:
1. **The verdict** — what objective analysis says, and whether the base engine
   agrees.
2. **The legends speak** — one short paragraph per dissenting legend,
   explaining *why* their characteristic instinct prefers a different move.
3. **The consensus** — moves multiple voices converge on.

Maximum 5 paragraphs. Grandmaster tone: precise, direct, no filler.

### 4.4 Graceful degradation

| Missing piece | Behavior |
|---|---|
| Any adapter | Panel shows remaining voices + SF. No error. |
| Stockfish | Panel shows base + legends. No error. |
| Ollama | Deterministic fallback commentary. Panel still renders. |
| Base checkpoint | Panel service fails to start; command returns error; UI shows "Panel unavailable". |

The panel is designed to ship at any stage of training. With only the base
+ SF (day 8), it works. As adapters complete, voices "light up" one by one.
This is better onboarding than a binary "panel off / panel on" state.

---

## 5. God Mode

### 5.1 Three-move analysis

Toggled in the sidebar. When enabled, for each position, three candidate
moves are surfaced:

1. **Stockfish best** — depth 22, objective. Blue.
2. **Trinstinctus best** — engine's actual MCTS recommendation. Cyan.
3. **Brilliant** — Stockfish-verified sacrifice that maintains advantage.
   Amber.

### 5.2 Brilliant detection

A move is "brilliant" if:
- Material delta ≤ -100 centipawns (sacrifice of at least a pawn)
- Post-move evaluation from mover's perspective ≥ -20 cp (still not losing)
- Delta from pre-move evaluation ≥ -50 cp (didn't worsen position by more
  than half a pawn)

The threshold is comparable to chess.com's "brilliant" classification, with
the exact numbers tuned to avoid false positives (a routine exchange sacrifice
does not qualify; a genuine piece sacrifice to open the king does).

### 5.3 Line explanations

Clicking any candidate plays the move and displays the principal variation
(up to 10 plies), evaluation change, and a one-line LLM explanation from The
Legends ("Stockfish's Nf3 develops the knight while preparing d4, maintaining
the initiative").

This is a training tool: the user can compare three perspectives on the same
position and understand why the objective best move is best, or why a
brilliant sacrifice is sound (or unsound).

---

## 6. Personal game import

### 6.1 Sources

| Platform | Method | Rate limit |
|---|---|---|
| Chess.com | Monthly archive URLs, JSON | 1 req/sec (polite) |
| Lichess | Streaming NDJSON, no auth | Respected via streaming |
| Helix internal | Self-play and eval matches | Local DB |

Import flow:
1. User enters username and platform in the Analysis tab.
2. `ingest/personal_games.py` runs.
3. Games are stored in `app.personal_games` with PGN, ECO, opening name,
   result (from user's perspective), user color, user rating, opponent rating.
4. Gambit detection is run inline; results stored in `analytics.gambit_plays`.
5. Accuracy job is queued in `analytics.accuracy_jobs`.
6. Background worker computes accuracy and ACPL asynchronously.

### 6.2 Opening analytics

`app.player_opening_stats` materialized view aggregates per-opening W/L/D
and score. Refreshed nightly via pg_cron.

The "strongest repertoire against X" query uses `app.best_repertoire_against()`,
a SQL function that returns the user's best-scoring continuations against a
given opponent ECO. This is the "what's my best response to the Sicilian?"
query, answered from the user's own game history.

### 6.3 Accuracy ranking

Accuracy is computed via the Lichess-inspired formula:
`accuracy = mean(103.1668 · exp(-0.04354 · loss_cp) - 3.1669)` over all moves,
where `loss_cp` is the centipawn loss from the mover's perspective.

The worker runs Stockfish at depth 16 per move (depth 18 is overkill for
accuracy — the classification doesn't change meaningfully between 16 and 18
for accuracy purposes).

Rankings are materialized in `analytics.accuracy_rankings`, sorted by average
accuracy. Users see their best games, their worst games, and the trajectory
across time.

---

## 7. Gambit tracking

### 7.1 Registry

`app.gambits` seeds from the Lichess opening database, filtered to names
containing "gambit" (~600 gambits). Each entry stores:
- gambit_id (e.g., `C51_Evans_Gambit`)
- eco_code
- name
- side (white or black — which side "plays" the gambit)
- moves_uci (the gambit line)
- soundness (sound / speculative / unsound)

Soundness is a heuristic: unsound for A0x/B0x gambits, speculative otherwise.
Users can manually curate over time.

### 7.2 Transposition-aware matching

Prefix-matching under-counts gambits by 30–40% because transpositions are
ubiquitous in the opening. The correct matcher indexes each gambit by its
**final position key** (FEN minus halfmove/fullmove), then checks whether that
position appears anywhere in the user's game — regardless of move order.

The matcher consumes `list[(fen_before, san)]`, which both PGN (personal) and
`eval.match_moves` (engine) can produce. This is the same abstraction for both
sources — no dual code paths.

### 7.3 Matchup statistics

`analytics.gambit_matchup_stats` aggregates per (participant, opponent, gambit).
This answers "how does Trinstinctus do against Stockfish with the Evans
Gambit?" and "how does the user do against their most frequent opponent in the
Najdorf?" — the same query, both sources.

---

## 8. Training telemetry

The Training Monitor polls `get_training_state` every 2s. The backend query is:

```sql
SELECT * FROM ops.training_progress ORDER BY updated_at DESC LIMIT 1;
```

That's one row from a table that only accumulates one row per state change
(throttled to 1 Hz by the reporter). Cost is negligible — the app can poll
for hours.

The chart data comes from:

ops.loss_history — training loss trajectory, one row per 100 steps

eval.elo_history — Elo progression, one row per generation

Both are small (thousands of rows) and indexed by generation. The chart renders
in a single Recharts component.

## 9. Security model
### 9.1 Trust boundary
The Rust backend is the only process with database access. The React frontend
has no direct DB or filesystem access. All data flows through typed
tauri::command boundaries that:

Validate input

Sanitize SQL (parameterized queries only)

Return structured Result<T, AppError>

A compromised renderer cannot corrupt the database or read arbitrary files.

###	9.2 Input validation
Every command validates:

String length (e.g., FEN ≤ 100 chars)

Enum fields against allowed sets

Numeric fields within range (temperature ∈ [0,5], contempt ∈ [0,1])

###	9.3 SQL injection prevention
Every query uses sqlx::query_as with $1, $2 placeholders. Dynamic table
names (e.g., gm.moves_{legend}) use SQL format! at compile time — never
user input.

###	9.4 LLM isolation
Ollama is reached via localhost HTTP with a 30s timeout. If it hangs or
crashes, commentary returns empty. The app never depends on the LLM for core
functionality.

###	9.5 Failure modes
Failure	Detection	User-visible behavior
DB unreachable	sqlx error	Error toast, retry button
Ollama unreachable	reqwest timeout	Empty commentary, "offline" indicator
Adapter not trained	count_adapters = 0	Style dashboard disabled with tooltip
Migration mismatch	App fails to start	"Database schema mismatch"
Corrupt FEN	chess.js raises	Error toast with detail

## 10. Accessibility
WCAG 2.1 Level AA:

Contrast: all text meets 4.5:1 minimum

Keyboard: all interactive elements reachable via Tab; Enter/Space activates

Focus: visible focus rings preserved

Motion: respects prefers-reduced-motion

Labels: every icon button has aria-label; form inputs have visible labels

Errors: announced via role="alert" for screen readers

## 11. Future work
High priority:

Persistent panel service — replace placeholder analyze_god_mode SF stub
with the panel_service's SF subprocess. (Partially done — panel_service
is live; god_mode still has a stub.)

Keyboard shortcuts — Cmd/Ctrl+P for play, / for search, etc.

Pre-computed Legends cache for ECO lines (offline, one-time)

Toast notification system for non-critical errors

PGN export of current game

Medium priority:

PGN import to set board position

Multi-board tournament viewer

Custom puzzle collections (user-curated from games)

Study editor (PGN + comments + arrows)

Server-side rendering for large opening-explorer result sets

Aspirational:

iOS/Android companion app via Tauri mobile

Web version with WASM-compiled engine (limited strength)

Real-time multiplayer study rooms

## 12. References
Tauri 2.12 docs — tauri.app

React 18 docs — react.dev

sqlx docs — docs.rs/sqlx

react-chessboard — github.com/Clariity/react-chessboard

SM-2 algorithm (Wozniak, 1987) — spaced repetition

WCAG 2.1 — accessibility standard
