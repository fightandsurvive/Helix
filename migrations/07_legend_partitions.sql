-- ══════════════════════════════════════════════════════════════════════
-- Migration 07 — Legend-first partitioning (REPLACES migration 01's
-- games/moves tables).
--
-- Why: adapter training needs "all Morphy games" queries to scan exactly
-- one partition. In the ECO-first layout, that query scans all 24 ECO
-- partitions. Legend-first is a 6× speedup on the adapter training loop,
-- which runs on every retrain of every legend.
--
-- Structure:
--   games → LIST by legend_tag:
--     'general' → LIST by LEFT(eco_code,1) → HASH 4-way (24 sub-partitions)
--     'morphy', 'petrosian', 'tal', 'kasparov', 'fischer', 'carlsen' → flat
--
-- Moves mirror this structure.
--
-- After recreating the tables, this migration ALSO recreates the
-- materialized views from migration 05 (which were dropped CASCADE).
-- ══════════════════════════════════════════════════════════════════════

-- Drop old tables and their dependent views (CASCADE handles the views).
DROP TABLE IF EXISTS gm.moves CASCADE;
DROP TABLE IF EXISTS gm.games CASCADE;
DROP MATERIALIZED VIEW IF EXISTS gm.position_stats CASCADE;
DROP MATERIALIZED VIEW IF EXISTS gm.position_stats_by_legend CASCADE;

-- ── Games ───────────────────────────────────────────────────────────
CREATE TABLE gm.games (
    game_id      BIGINT NOT NULL,
    eco_code     CHAR(3) NOT NULL CHECK (eco_code ~ '^[A-E][0-9]{2}$'),
    white_player TEXT NOT NULL,
    black_player TEXT NOT NULL,
    white_elo    INTEGER,
    black_elo    INTEGER,
    result       TEXT NOT NULL CHECK (result IN ('1-0','0-1','1/2-1/2','*')),
    date_played  DATE,
    opening_tags TEXT[] DEFAULT '{}',
    legend_tag   TEXT NOT NULL DEFAULT 'general'
                 CHECK (legend_tag IN (
                     'general','morphy','petrosian','tal',
                     'kasparov','fischer','carlsen'
                 )),
    ply_count    SMALLINT,
    source       TEXT,
    PRIMARY KEY (game_id, legend_tag)
) PARTITION BY LIST (legend_tag);

-- General branch: LIST by ECO family, then HASH 4-way per family.
CREATE TABLE gm.games_general PARTITION OF gm.games
    FOR VALUES IN ('general') PARTITION BY LIST (LEFT(eco_code, 1));

CREATE TABLE gm.games_general_a PARTITION OF gm.games_general FOR VALUES IN ('A') PARTITION BY HASH (eco_code);
CREATE TABLE gm.games_general_b PARTITION OF gm.games_general FOR VALUES IN ('B') PARTITION BY HASH (eco_code);
CREATE TABLE gm.games_general_c PARTITION OF gm.games_general FOR VALUES IN ('C') PARTITION BY HASH (eco_code);
CREATE TABLE gm.games_general_d PARTITION OF gm.games_general FOR VALUES IN ('D') PARTITION BY HASH (eco_code);
CREATE TABLE gm.games_general_e PARTITION OF gm.games_general FOR VALUES IN ('E') PARTITION BY HASH (eco_code);

DO $$
DECLARE fam TEXT; i INT;
BEGIN
    FOREACH fam IN ARRAY ARRAY['a','b','c','d','e'] LOOP
        FOR i IN 0..3 LOOP
            EXECUTE format(
                'CREATE TABLE gm.games_general_%s_%s '
                'PARTITION OF gm.games_general_%s '
                'FOR VALUES WITH (MODULUS 4, REMAINDER %s);',
                fam, i, fam, i
            );
        END LOOP;
    END LOOP;
END$$;

-- Legend branches: one flat table each (small, no further partitioning).
CREATE TABLE gm.games_morphy    PARTITION OF gm.games FOR VALUES IN ('morphy');
CREATE TABLE gm.games_petrosian PARTITION OF gm.games FOR VALUES IN ('petrosian');
CREATE TABLE gm.games_tal       PARTITION OF gm.games FOR VALUES IN ('tal');
CREATE TABLE gm.games_kasparov  PARTITION OF gm.games FOR VALUES IN ('kasparov');
CREATE TABLE gm.games_fischer   PARTITION OF gm.games FOR VALUES IN ('fischer');
CREATE TABLE gm.games_carlsen   PARTITION OF gm.games FOR VALUES IN ('carlsen');

CREATE INDEX ON gm.games_morphy    (date_played);
CREATE INDEX ON gm.games_petrosian (date_played);
CREATE INDEX ON gm.games_tal       (date_played);
CREATE INDEX ON gm.games_kasparov  (date_played);
CREATE INDEX ON gm.games_fischer   (date_played);
CREATE INDEX ON gm.games_carlsen   (date_played);

-- ── Moves ───────────────────────────────────────────────────────────
CREATE TABLE gm.moves (
    move_id       BIGSERIAL,
    game_id       BIGINT NOT NULL,
    eco_code      CHAR(3) NOT NULL,
    legend_tag    TEXT NOT NULL DEFAULT 'general',
    ply_number    SMALLINT NOT NULL,
    fen_string    TEXT NOT NULL,
    fen_md5       UUID GENERATED ALWAYS AS (md5(fen_string)::uuid) STORED,
    move_notation TEXT NOT NULL,
    phase_flag    TEXT CHECK (phase_flag IN ('Opening','Middlegame','Endgame')),
    piece_count   SMALLINT,
    PRIMARY KEY (move_id, legend_tag)
) PARTITION BY LIST (legend_tag);

CREATE TABLE gm.moves_general PARTITION OF gm.moves
    FOR VALUES IN ('general') PARTITION BY HASH (game_id);

DO $$
DECLARE i INT;
BEGIN
    FOR i IN 0..7 LOOP
        EXECUTE format(
            'CREATE TABLE gm.moves_general_%s '
            'PARTITION OF gm.moves_general '
            'FOR VALUES WITH (MODULUS 8, REMAINDER %s);',
            i, i
        );
    END LOOP;
END$$;

CREATE TABLE gm.moves_morphy    PARTITION OF gm.moves FOR VALUES IN ('morphy')    PARTITION BY HASH (game_id);
CREATE TABLE gm.moves_petrosian PARTITION OF gm.moves FOR VALUES IN ('petrosian') PARTITION BY HASH (game_id);
CREATE TABLE gm.moves_tal       PARTITION OF gm.moves FOR VALUES IN ('tal')       PARTITION BY HASH (game_id);
CREATE TABLE gm.moves_kasparov  PARTITION OF gm.moves FOR VALUES IN ('kasparov')  PARTITION BY HASH (game_id);
CREATE TABLE gm.moves_fischer   PARTITION OF gm.moves FOR VALUES IN ('fischer')   PARTITION BY HASH (game_id);
CREATE TABLE gm.moves_carlsen   PARTITION OF gm.moves FOR VALUES IN ('carlsen')   PARTITION BY HASH (game_id);

DO $$
DECLARE leg TEXT; i INT;
BEGIN
    FOREACH leg IN ARRAY ARRAY['morphy','petrosian','tal','kasparov','fischer','carlsen'] LOOP
        FOR i IN 0..3 LOOP
            EXECUTE format(
                'CREATE TABLE gm.moves_%s_%s '
                'PARTITION OF gm.moves_%s '
                'FOR VALUES WITH (MODULUS 4, REMAINDER %s);',
                leg, i, leg, i
            );
        END LOOP;
        FOR i IN 0..3 LOOP
            EXECUTE format('CREATE INDEX ON gm.moves_%s_%s (game_id, ply_number);', leg, i);
            EXECUTE format('CREATE INDEX ON gm.moves_%s_%s (fen_md5);', leg, i);
        END LOOP;
    END LOOP;
END$$;

CREATE INDEX ON gm.moves_general (fen_md5);
CREATE INDEX ON gm.moves_general (game_id, ply_number);

-- ── Recreate the materialized views from migration 05 ───────────────
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
