-- ══════════════════════════════════════════════════════════════════════
-- Migration 03 — Evaluation & SPRT records.
--
-- Records every SPRT-gated match (Trinstinctus vs Stockfish or vs Lc0),
-- tracks SPRT progress per sprint, and stores per-generation Elo history.
--
-- Migration 11 adds eco_code and opening_name to eval.matches so gambit
-- and opening analytics work for engine games the same way they do for
-- personal games.
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS eval;

CREATE TABLE eval.matches (
    match_id         BIGSERIAL PRIMARY KEY,
    gate_name        TEXT NOT NULL,
    white_engine     TEXT NOT NULL,
    black_engine     TEXT NOT NULL,
    white_config     JSONB,
    black_config     JSONB,
    result           TEXT CHECK (result IN ('1-0','0-1','1/2-1/2','*')),
    ply_count        SMALLINT,
    pgn              TEXT,
    trinstinctus_ckpt TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON eval.matches (gate_name);
CREATE INDEX ON eval.matches (created_at DESC);

CREATE TABLE eval.match_moves (
    match_id      BIGINT NOT NULL REFERENCES eval.matches(match_id) ON DELETE CASCADE,
    ply_number    SMALLINT NOT NULL,
    fen_string    TEXT NOT NULL,
    fen_md5       UUID GENERATED ALWAYS AS (md5(fen_string)::uuid) STORED,
    move_notation TEXT NOT NULL,
    PRIMARY KEY (match_id, ply_number)
);
CREATE INDEX ON eval.match_moves (fen_md5);

CREATE TABLE eval.sprt_sprints (
    sprint_id    BIGSERIAL PRIMARY KEY,
    gate_name    TEXT NOT NULL,
    h0_elo       REAL NOT NULL,
    h1_elo       REAL NOT NULL,
    alpha        REAL NOT NULL DEFAULT 0.05,
    beta         REAL NOT NULL DEFAULT 0.05,
    games_played INT DEFAULT 0,
    wins         INT DEFAULT 0,
    losses       INT DEFAULT 0,
    draws        INT DEFAULT 0,
    llr          REAL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running','h0_accepted','h1_accepted','timeout')),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at     TIMESTAMPTZ
);
CREATE INDEX ON eval.sprt_sprints (gate_name, sprint_id DESC);

CREATE TABLE eval.elo_history (
    generation   INTEGER PRIMARY KEY,
    elo_estimate REAL NOT NULL,
    elo_stderr   REAL NOT NULL,
    games_basis  INTEGER NOT NULL,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
