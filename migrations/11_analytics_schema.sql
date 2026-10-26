-- ══════════════════════════════════════════════════════════════════════
-- Migration 11 — Unified analytics (gambit plays + accuracy results).
--
-- Design: both sources (personal + eval) write to the SAME tables, keyed
-- by (source, ref_id). Views that aggregate across sources make queries
-- like "does Trinstinctus beat Stockfish with the Evans Gambit?" and
-- "does the user win more with the KID than the Nimzo?" the same query.
--
-- Also adds eco_code and opening_name to eval.matches so engine games
-- get the same opening analytics as personal games.
-- ══════════════════════════════════════════════════════════════════════

-- ── Extend eval.matches with opening metadata ───────────────────────
ALTER TABLE eval.matches
    ADD COLUMN IF NOT EXISTS eco_code     CHAR(3),
    ADD COLUMN IF NOT EXISTS opening_name TEXT;
CREATE INDEX IF NOT EXISTS idx_eval_matches_eco ON eval.matches (eco_code);

CREATE SCHEMA IF NOT EXISTS analytics;

-- ── Gambit plays (one row per participant per gambit per game) ──────
-- A game can reach multiple gambit positions via transposition. One row
-- per unique (source, ref_id, participant, gambit_id).
CREATE TABLE analytics.gambit_plays (
    play_id          BIGSERIAL PRIMARY KEY,
    source           TEXT NOT NULL CHECK (source IN ('personal','eval')),
    ref_id           BIGINT NOT NULL,
    participant      TEXT NOT NULL,
    participant_type TEXT NOT NULL CHECK (participant_type IN ('human','engine')),
    opponent         TEXT NOT NULL,
    color            TEXT NOT NULL CHECK (color IN ('white','black')),
    gambit_id        TEXT NOT NULL REFERENCES app.gambits(gambit_id) ON DELETE CASCADE,
    gambit_depth     SMALLINT NOT NULL,
    result           TEXT NOT NULL CHECK (result IN ('win','loss','draw')),
    played_at        TIMESTAMPTZ,
    UNIQUE (source, ref_id, participant, gambit_id)
);
CREATE INDEX ON analytics.gambit_plays (participant, gambit_id);
CREATE INDEX ON analytics.gambit_plays (gambit_id, participant);
CREATE INDEX ON analytics.gambit_plays (source, participant);

-- ── Accuracy results (one row per participant per game) ─────────────
CREATE TABLE analytics.accuracy_results (
    source           TEXT NOT NULL CHECK (source IN ('personal','eval')),
    ref_id           BIGINT NOT NULL,
    participant      TEXT NOT NULL,
    participant_type TEXT NOT NULL CHECK (participant_type IN ('human','engine')),
    color            TEXT NOT NULL CHECK (color IN ('white','black')),
    accuracy         REAL NOT NULL CHECK (accuracy BETWEEN 0 AND 100),
    acpl             REAL NOT NULL,
    moves_analyzed   INT NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, ref_id, participant)
);
CREATE INDEX ON analytics.accuracy_results (participant, accuracy DESC);

-- ── Job queue for the async accuracy worker ─────────────────────────
-- Workers claim jobs via FOR UPDATE SKIP LOCKED.
-- Priority: 10 for eval games (benchmark), 5 for personal games.
CREATE TABLE analytics.accuracy_jobs (
    job_id      BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL CHECK (source IN ('personal','eval')),
    ref_id      BIGINT NOT NULL,
    priority    SMALLINT NOT NULL DEFAULT 5,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','done','failed')),
    attempts    SMALLINT NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (source, ref_id)
);
CREATE INDEX ON analytics.accuracy_jobs (status, priority DESC, created_at)
    WHERE status = 'pending';

-- ── Materialized views ──────────────────────────────────────────────

-- Per-participant record with each gambit.
CREATE MATERIALIZED VIEW analytics.participant_gambit_stats AS
SELECT
    gp.participant,
    gp.participant_type,
    gp.gambit_id,
    g.name AS gambit_name,
    g.side,
    g.soundness,
    COUNT(*) AS games,
    COUNT(*) FILTER (WHERE gp.result = 'win')  AS wins,
    COUNT(*) FILTER (WHERE gp.result = 'loss') AS losses,
    COUNT(*) FILTER (WHERE gp.result = 'draw') AS draws,
    (COUNT(*) FILTER (WHERE gp.result = 'win')
     + 0.5 * COUNT(*) FILTER (WHERE gp.result = 'draw'))::REAL / COUNT(*) AS score,
    MAX(gp.played_at) AS last_played
FROM analytics.gambit_plays gp
JOIN app.gambits g USING (gambit_id)
GROUP BY gp.participant, gp.participant_type, gp.gambit_id,
         g.name, g.side, g.soundness;

CREATE UNIQUE INDEX ON analytics.participant_gambit_stats (participant, gambit_id);

-- Head-to-head: how does participant A do vs opponent B in gambit X?
CREATE MATERIALIZED VIEW analytics.gambit_matchup_stats AS
SELECT
    gp.participant,
    gp.opponent,
    gp.gambit_id,
    g.name AS gambit_name,
    COUNT(*) AS games,
    COUNT(*) FILTER (WHERE gp.result = 'win')  AS wins,
    COUNT(*) FILTER (WHERE gp.result = 'loss') AS losses,
    COUNT(*) FILTER (WHERE gp.result = 'draw') AS draws,
    (COUNT(*) FILTER (WHERE gp.result = 'win')
     + 0.5 * COUNT(*) FILTER (WHERE gp.result = 'draw'))::REAL / COUNT(*) AS score
FROM analytics.gambit_plays gp
JOIN app.gambits g USING (gambit_id)
GROUP BY gp.participant, gp.opponent, gp.gambit_id, g.name;

CREATE UNIQUE INDEX ON analytics.gambit_matchup_stats (participant, opponent, gambit_id);

-- ECO stats across both sources. Each game produces TWO rows (one per
-- player) so the view supports "show me everyone's performance with the
-- Sicilian" as well as "show me Trinstinctus's".
CREATE MATERIALIZED VIEW analytics.participant_eco_stats AS
WITH combined AS (
    -- Personal games (white perspective, only when user is white)
    SELECT 'personal'::TEXT AS source, g.game_id AS ref_id,
           g.white_player AS participant, 'human'::TEXT AS participant_type,
           g.black_player AS opponent, 'white'::TEXT AS color,
           g.eco_code, g.opening_name,
           CASE g.result WHEN 'win' THEN 'win' WHEN 'loss' THEN 'loss' ELSE 'draw' END AS result
    FROM app.personal_games g
    WHERE g.eco_code IS NOT NULL AND g.user_color = 'white'

    UNION ALL

    -- Personal games (black perspective, only when user is black)
    SELECT 'personal', g.game_id, g.black_player, 'human',
           g.white_player, 'black',
           g.eco_code, g.opening_name,
           CASE g.result WHEN 'win' THEN 'win' WHEN 'loss' THEN 'loss' ELSE 'draw' END
    FROM app.personal_games g
    WHERE g.eco_code IS NOT NULL AND g.user_color = 'black'

    UNION ALL

    -- Eval matches (white engine)
    SELECT 'eval', m.match_id, m.white_engine, 'engine',
           m.black_engine, 'white',
           m.eco_code, m.opening_name,
           CASE m.result WHEN '1-0' THEN 'win' WHEN '0-1' THEN 'loss' ELSE 'draw' END
    FROM eval.matches m WHERE m.eco_code IS NOT NULL

    UNION ALL

    -- Eval matches (black engine)
    SELECT 'eval', m.match_id, m.black_engine, 'engine',
           m.white_engine, 'black',
           m.eco_code, m.opening_name,
           CASE m.result WHEN '0-1' THEN 'win' WHEN '1-0' THEN 'loss' ELSE 'draw' END
    FROM eval.matches m WHERE m.eco_code IS NOT NULL
)
SELECT
    participant, participant_type, color, eco_code,
    MIN(opening_name) AS opening_name,
    COUNT(*) AS games,
    COUNT(*) FILTER (WHERE result = 'win')  AS wins,
    COUNT(*) FILTER (WHERE result = 'loss') AS losses,
    COUNT(*) FILTER (WHERE result = 'draw') AS draws,
    (COUNT(*) FILTER (WHERE result = 'win')
     + 0.5 * COUNT(*) FILTER (WHERE result = 'draw'))::REAL / COUNT(*) AS score
FROM combined
GROUP BY participant, participant_type, color, eco_code;

CREATE UNIQUE INDEX ON analytics.participant_eco_stats (participant, color, eco_code);

-- Accuracy rankings: per-participant aggregates.
CREATE MATERIALIZED VIEW analytics.accuracy_rankings AS
SELECT
    participant, participant_type, source,
    COUNT(*) AS games,
    AVG(accuracy)::REAL AS avg_accuracy,
    STDDEV_POP(accuracy)::REAL AS stdev_accuracy,
    AVG(acpl)::REAL AS avg_acpl,
    MAX(accuracy)::REAL AS best_accuracy,
    MIN(acpl)::REAL AS best_acpl
FROM analytics.accuracy_results
GROUP BY participant, participant_type, source;

CREATE UNIQUE INDEX ON analytics.accuracy_rankings (participant, source);

-- ── Refresh function ────────────────────────────────────────────────
-- Called from pg_cron overnight. CONCURRENTLY doesn't lock readers.
CREATE OR REPLACE FUNCTION analytics.refresh_views()
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.participant_gambit_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.gambit_matchup_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.participant_eco_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.accuracy_rankings;
END;
$$;
