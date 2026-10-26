-- ══════════════════════════════════════════════════════════════════════
-- Migration 10 — Personal game import and gambit registry.
--
-- Personal games come from Chess.com, Lichess, or Helix's own eval/selfplay
-- matches. All are stored in app.personal_games with a source-agnostic
-- schema.
--
-- Gambit registry seeds from the Lichess opening database, filtered to
-- entries whose name contains "gambit" (~600 gambits). The gambit stats
-- table aggregates per-profile play counts and win rates, populated by
-- ingest/gambit_stats.py after every import.
-- ══════════════════════════════════════════════════════════════════════

CREATE TABLE app.player_profiles (
    profile_id   BIGSERIAL PRIMARY KEY,
    platform     TEXT NOT NULL CHECK (platform IN ('helix','chesscom','lichess')),
    username     TEXT NOT NULL,
    display_name TEXT,
    last_synced  TIMESTAMPTZ,
    UNIQUE (platform, username)
);

CREATE TABLE app.personal_games (
    game_id         BIGSERIAL PRIMARY KEY,
    profile_id      BIGINT NOT NULL REFERENCES app.player_profiles(profile_id) ON DELETE CASCADE,
    platform        TEXT NOT NULL CHECK (platform IN ('helix','chesscom','lichess')),
    external_id     TEXT NOT NULL,
    played_at       TIMESTAMPTZ,
    white_player    TEXT NOT NULL,
    black_player    TEXT NOT NULL,
    user_color      TEXT NOT NULL CHECK (user_color IN ('white','black')),
    user_rating     INTEGER,
    opponent_rating INTEGER,
    result          TEXT NOT NULL CHECK (result IN ('win','loss','draw')),
    time_control    TEXT,
    eco_code        CHAR(3),
    opening_name    TEXT,
    accuracy        REAL,
    acpl            REAL,
    pgn             TEXT NOT NULL,
    review_json     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (profile_id, external_id)
);
CREATE INDEX ON app.personal_games (profile_id, eco_code);
CREATE INDEX ON app.personal_games (profile_id, played_at DESC);
CREATE INDEX ON app.personal_games (profile_id, accuracy DESC);

-- ── Gambit registry ──────────────────────────────────────────────────
-- Seed from Lichess opening DB, filtered to names containing "gambit".
-- soundness is a heuristic: A0x/B0x gambits are typically unsound; other
-- gambits are typically speculative. Users can hand-curate over time.
CREATE TABLE app.gambits (
    gambit_id TEXT PRIMARY KEY,
    eco_code  CHAR(3) NOT NULL,
    name      TEXT NOT NULL,
    side      TEXT NOT NULL CHECK (side IN ('white','black')),
    moves_uci TEXT NOT NULL,
    soundness TEXT CHECK (soundness IN ('sound','speculative','unsound')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON app.gambits (eco_code);

-- Per-profile gambit play record.
CREATE TABLE app.player_gambit_stats (
    profile_id  BIGINT NOT NULL REFERENCES app.player_profiles(profile_id) ON DELETE CASCADE,
    gambit_id   TEXT NOT NULL REFERENCES app.gambits(gambit_id),
    games       INT NOT NULL DEFAULT 0,
    wins        INT NOT NULL DEFAULT 0,
    losses      INT NOT NULL DEFAULT 0,
    draws       INT NOT NULL DEFAULT 0,
    win_rate    REAL GENERATED ALWAYS AS
        (CASE WHEN games = 0 THEN 0 ELSE wins::REAL / games END) STORED,
    last_played TIMESTAMPTZ,
    PRIMARY KEY (profile_id, gambit_id)
);
CREATE INDEX ON app.player_gambit_stats (profile_id, win_rate DESC);

-- ── Per-opening personal performance ─────────────────────────────────
CREATE MATERIALIZED VIEW app.player_opening_stats AS
SELECT
    g.profile_id,
    g.user_color,
    g.eco_code,
    g.opening_name,
    COUNT(*) AS games,
    COUNT(*) FILTER (WHERE g.result = 'win') AS wins,
    COUNT(*) FILTER (WHERE g.result = 'loss') AS losses,
    COUNT(*) FILTER (WHERE g.result = 'draw') AS draws,
    (COUNT(*) FILTER (WHERE g.result = 'win')
     + 0.5 * COUNT(*) FILTER (WHERE g.result = 'draw'))::REAL
     / COUNT(*) AS score
FROM app.personal_games g
WHERE g.eco_code IS NOT NULL
GROUP BY g.profile_id, g.user_color, g.eco_code, g.opening_name;

CREATE UNIQUE INDEX ON app.player_opening_stats
    (profile_id, user_color, eco_code, opening_name);

-- "Strongest repertoire against opening X" — returns best-scoring black
-- responses with at least N games. Used by the Analysis tab.
CREATE OR REPLACE FUNCTION app.best_repertoire_against(
    p_profile_id BIGINT,
    p_opponent_eco CHAR(3),
    p_min_games INT DEFAULT 3
)
RETURNS TABLE (eco_code CHAR(3), opening_name TEXT, games BIGINT, score REAL)
LANGUAGE sql STABLE AS $$
    SELECT g.eco_code, g.opening_name, COUNT(*),
           (COUNT(*) FILTER (WHERE g.result = 'win')
            + 0.5 * COUNT(*) FILTER (WHERE g.result = 'draw'))::REAL / COUNT(*)
    FROM app.personal_games g
    WHERE g.profile_id = p_profile_id
      AND g.user_color = 'black'
      AND g.result IS NOT NULL
    GROUP BY g.eco_code, g.opening_name
    HAVING COUNT(*) >= p_min_games
    ORDER BY score DESC, games DESC
    LIMIT 10;
$$;
