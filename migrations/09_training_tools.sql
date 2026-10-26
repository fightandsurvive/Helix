-- ══════════════════════════════════════════════════════════════════════
-- Migration 09 — Opening study and puzzle training tools.
--
-- Both use SM-2 spaced repetition (supervisor: training/spaced_repetition.py
-- and the Rust implementations in training_tools.rs — the two MUST produce
-- identical scheduling so a line reviewed in the app keeps its schedule
-- when reviewed from the CLI).
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS app;

-- ── Opening study ────────────────────────────────────────────────────
CREATE TABLE app.opening_lines (
    line_id    TEXT PRIMARY KEY,
    eco_code   CHAR(3) NOT NULL,
    name       TEXT NOT NULL,
    moves_uci  TEXT NOT NULL,
    moves_san  TEXT[] NOT NULL,
    fens       TEXT[] NOT NULL,
    side       TEXT CHECK (side IN ('white','black','both')) DEFAULT 'both',
    difficulty SMALLINT DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON app.opening_lines (eco_code);

CREATE TABLE app.opening_study (
    line_id       TEXT PRIMARY KEY REFERENCES app.opening_lines(line_id) ON DELETE CASCADE,
    ef            REAL NOT NULL DEFAULT 2.5,
    interval_days INT NOT NULL DEFAULT 1,
    repetitions   INT NOT NULL DEFAULT 0,
    due_at        TIMESTAMPTZ DEFAULT NOW(),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_reviewed TIMESTAMPTZ,
    mastery_score REAL DEFAULT 0.0
);
CREATE INDEX ON app.opening_study (due_at);

CREATE TABLE app.opening_attempts (
    attempt_id    BIGSERIAL PRIMARY KEY,
    line_id       TEXT NOT NULL REFERENCES app.opening_lines(line_id),
    mode          TEXT NOT NULL CHECK (mode IN ('visual','blind','speed','streak')),
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    correct_moves INT NOT NULL,
    total_moves   INT NOT NULL,
    time_ms       INT,
    passed        BOOLEAN
);

-- ── Puzzles ──────────────────────────────────────────────────────────
CREATE TABLE app.puzzles (
    puzzle_id    TEXT PRIMARY KEY,
    fen          TEXT NOT NULL,
    solution_uci TEXT NOT NULL,
    rating       INT,
    themes       TEXT[] DEFAULT '{}',
    source       TEXT NOT NULL CHECK (source IN ('lichess','gm_derived','user_study')),
    eco_code     CHAR(3),
    piece_count  SMALLINT,
    phase_flag   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON app.puzzles (source);
CREATE INDEX ON app.puzzles (rating) WHERE source = 'lichess';
CREATE INDEX ON app.puzzles (eco_code) WHERE eco_code IS NOT NULL;

CREATE TABLE app.puzzle_attempts (
    attempt_id   BIGSERIAL PRIMARY KEY,
    puzzle_id    TEXT NOT NULL REFERENCES app.puzzles(puzzle_id),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    correct      BOOLEAN NOT NULL,
    hints_used   INT DEFAULT 0,
    time_ms      INT,
    move_played  TEXT
);

CREATE TABLE app.puzzle_sr (
    puzzle_id     TEXT PRIMARY KEY REFERENCES app.puzzles(puzzle_id) ON DELETE CASCADE,
    ef            REAL NOT NULL DEFAULT 2.5,
    interval_days INT NOT NULL DEFAULT 1,
    repetitions   INT NOT NULL DEFAULT 0,
    due_at        TIMESTAMPTZ DEFAULT NOW(),
    last_reviewed TIMESTAMPTZ,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON app.puzzle_sr (due_at);
