-- ══════════════════════════════════════════════════════════════════════
-- Migration 01 — GM warehouse initial schema.
--
-- This migration establishes the SCHEMA and the ECO dictionary. The games
-- and moves tables created here use an ECO-first partition layout that
-- migration 07 replaces with a legend-first layout. Migration 07 is the
-- authoritative topology; this file's job is to give 05 (which creates
-- materialized views depending on games/moves) something to build against.
--
-- Enterprise note: the games/moves tables are effectively placeholders.
-- Do not load data into them before applying migration 07.
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS gm;

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- ECO dictionary — the Lichess opening database (~3,000 named positions).
-- Loaded by ingest/eco_loader.py. Standalone (no FK to games/moves).
CREATE TABLE gm.eco_dict (
    eco_code     CHAR(3) PRIMARY KEY,
    opening_name TEXT NOT NULL,
    pgn          TEXT NOT NULL,
    depth        SMALLINT,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Initial games table — will be DROPped and recreated by migration 07.
-- Minimal shape so migration 05's views have a dependency target.
CREATE TABLE gm.games (
    game_id      BIGINT NOT NULL,
    eco_code     CHAR(3) NOT NULL,
    legend_tag   TEXT NOT NULL DEFAULT 'general',
    white_player TEXT NOT NULL,
    black_player TEXT NOT NULL,
    result       TEXT NOT NULL,
    PRIMARY KEY (game_id, legend_tag)
);

-- Initial moves table — same story.
CREATE TABLE gm.moves (
    move_id       BIGSERIAL,
    game_id       BIGINT NOT NULL,
    legend_tag    TEXT NOT NULL DEFAULT 'general',
    ply_number    SMALLINT NOT NULL,
    fen_string    TEXT NOT NULL,
    fen_md5       UUID GENERATED ALWAYS AS (md5(fen_string)::uuid) STORED,
    move_notation TEXT NOT NULL,
    phase_flag    TEXT,
    piece_count   SMALLINT,
    PRIMARY KEY (move_id, legend_tag)
);
