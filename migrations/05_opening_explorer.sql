-- ══════════════════════════════════════════════════════════════════════
-- Migration 05 — Opening explorer materialized views.
--
-- These aggregate per-position win rates from the GM warehouse. Refreshed
-- nightly via pg_cron (migration 06). CONCURRENTLY allows queries during
-- refresh.
--
-- NOTE: these views are DROPped and recreated in migration 07 after the
-- legend-first partitioning changes the underlying games/moves tables.
-- The definitions here and in 07 are identical — the duplication is a
-- consequence of running 05 before 07.
-- ══════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW gm.position_stats AS
SELECT
    m.fen_md5,
    MIN(m.fen_string) AS fen_string,
    m.phase_flag,
    COUNT(*)::BIGINT AS total_games,
    COUNT(*) FILTER (WHERE g.result = '1-0') AS white_wins,
    COUNT(*) FILTER (WHERE g.result = '0-1') AS black_wins,
    COUNT(*) FILTER (WHERE g.result = '1/2-1/2') AS draws,
    (COUNT(*) FILTER (WHERE g.result = '1-0')
     + 0.5 * COUNT(*) FILTER (WHERE g.result = '1/2-1/2'))::REAL
     / COUNT(*) AS white_score
FROM gm.moves m
JOIN gm.games g
    ON g.game_id = m.game_id
    AND g.legend_tag = m.legend_tag
WHERE m.ply_number <= 40
GROUP BY m.fen_md5, m.phase_flag
HAVING COUNT(*) >= 20;

CREATE UNIQUE INDEX ON gm.position_stats (fen_md5);

CREATE MATERIALIZED VIEW gm.position_stats_by_legend AS
SELECT
    m.fen_md5,
    g.legend_tag,
    COUNT(*)::BIGINT AS games,
    AVG(CASE g.result
        WHEN '1-0' THEN 1.0
        WHEN '1/2-1/2' THEN 0.5
        ELSE 0.0
    END) AS white_score
FROM gm.moves m
JOIN gm.games g
    ON g.game_id = m.game_id
    AND g.legend_tag = m.legend_tag
WHERE g.legend_tag <> 'general'
  AND m.ply_number <= 30
GROUP BY m.fen_md5, g.legend_tag
HAVING COUNT(*) >= 5;

CREATE UNIQUE INDEX ON gm.position_stats_by_legend (fen_md5, legend_tag);

CREATE MATERIALIZED VIEW eval.matchup_stats AS
SELECT
    m.fen_md5,
    m.fen_string,
    g.white_engine,
    g.black_engine,
    COUNT(*)::BIGINT AS games,
    AVG(CASE g.result
        WHEN '1-0' THEN 1.0
        WHEN '1/2-1/2' THEN 0.5
        ELSE 0.0
    END) AS white_score
FROM eval.match_moves m
JOIN eval.matches g USING (match_id)
WHERE m.ply_number <= 40
GROUP BY m.fen_md5, m.fen_string, g.white_engine, g.black_engine
HAVING COUNT(*) >= 10;

CREATE UNIQUE INDEX ON eval.matchup_stats (fen_md5, white_engine, black_engine);
