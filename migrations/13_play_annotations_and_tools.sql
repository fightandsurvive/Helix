-- ══════════════════════════════════════════════════════════════════════
-- Migration 07 — Annotations, position library, daily puzzle streaks.
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS app;
-- Per-position annotations (arrows, highlights, comments).
CREATE TABLE app.game_annotations (
    annotation_id BIGSERIAL PRIMARY KEY,
    game_id       BIGINT NOT NULL REFERENCES app.personal_games(game_id) ON DELETE CASCADE,
    ply_number    SMALLINT NOT NULL,
    arrows        JSONB NOT NULL DEFAULT '[]',     -- [{from, to, color}]
    highlights    JSONB NOT NULL DEFAULT '[]',     -- [{square, color}]
    comment       TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (game_id, ply_number)
);
CREATE INDEX ON app.game_annotations (game_id);

-- Position library — saved FENs with names/notes/tags.
CREATE TABLE app.saved_positions (
    position_id  BIGSERIAL PRIMARY KEY,
    profile_id   BIGINT NOT NULL REFERENCES app.player_profiles(profile_id) ON DELETE CASCADE,
    fen          TEXT NOT NULL,
    name         TEXT NOT NULL,
    notes        TEXT,
    tags         TEXT[] DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON app.saved_positions (profile_id, created_at DESC);
CREATE INDEX ON app.saved_positions USING gin (tags);

-- Daily puzzle streak tracking.
CREATE TABLE app.daily_puzzle_state (
    profile_id      BIGINT PRIMARY KEY REFERENCES app.player_profiles(profile_id) ON DELETE CASCADE,
    current_streak  INTEGER NOT NULL DEFAULT 0,
    longest_streak  INTEGER NOT NULL DEFAULT 0,
    last_played_date DATE,
    total_played    INTEGER NOT NULL DEFAULT 0,
    total_solved    INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lichess theme lookup — computed once, cached as a view.
CREATE MATERIALIZED VIEW app.available_puzzle_themes AS
SELECT UNNEST(themes) AS theme, COUNT(*)::BIGINT AS puzzle_count
FROM app.puzzles
WHERE source = 'lichess'
GROUP BY theme;

CREATE UNIQUE INDEX ON app.available_puzzle_themes (theme);
