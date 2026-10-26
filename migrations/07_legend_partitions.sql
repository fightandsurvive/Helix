-- Migration 07 - Legend-first partitioning.
--
-- Replaces the ECO-first layout from migration 01. Adapter training queries
-- ("give me all Morphy games") now scan exactly one partition per legend,
-- which is a 6x speedup over the old layout where those queries scanned
-- all 24 ECO partitions.
--
-- Design decisions:
--
-- 1. games: LIST partition by legend_tag only. The 'general' partition is
--    a flat leaf table. Sub-partitioning 'general' by eco family was tried
--    and rejected - it requires PRIMARY KEY on an expression, which
--    PostgreSQL does not support, and the marginal query speedup did not
--    justify the schema complexity.
--
-- 2. moves: LIST partition by legend_tag, then HASH by game_id inside each
--    legend partition. 528M total rows means parallel scan benefit is
--    real, and game_id is in the PRIMARY KEY so hash partitioning works.
--
-- 3. All PRIMARY KEYs include every partition key column at every level.
--    This is a PostgreSQL requirement for partitioned tables.

-- Drop existing structures (from migration 01 and any partial 07 run)
DROP TABLE IF EXISTS gm.moves CASCADE;
DROP TABLE IF EXISTS gm.games CASCADE;
DROP MATERIALIZED VIEW IF EXISTS gm.position_stats CASCADE;
DROP MATERIALIZED VIEW IF EXISTS gm.position_stats_by_legend CASCADE;

-- Games table: partitioned by legend_tag. Primary key includes legend_tag
-- (its partition key). One flat leaf table for the 'general' legend;
-- small dedicated tables for each historical legend.
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

CREATE TABLE gm.games_general  PARTITION OF gm.games FOR VALUES IN ('general');
CREATE TABLE gm.games_morphy   PARTITION OF gm.games FOR VALUES IN ('morphy');
CREATE TABLE gm.games_petrosian PARTITION OF gm.games FOR VALUES IN ('petrosian');
CREATE TABLE gm.games_tal      PARTITION OF gm.games FOR VALUES IN ('tal');
CREATE TABLE gm.games_kasparov PARTITION OF gm.games FOR VALUES IN ('kasparov');
CREATE TABLE gm.games_fischer  PARTITION OF gm.games FOR VALUES IN ('fischer');
CREATE TABLE gm.games_carlsen  PARTITION OF gm.games FOR VALUES IN ('carlsen');

-- Indexes for per-legend queries (adapter training) and by-eco (opening stats)
CREATE INDEX ON gm.games_general  (eco_code);
CREATE INDEX ON gm.games_general  (date_played);
CREATE INDEX ON gm.games_morphy   (date_played);
CREATE INDEX ON gm.games_petrosian (date_played);
CREATE INDEX ON gm.games_tal      (date_played);
CREATE INDEX ON gm.games_kasparov (date_played);
CREATE INDEX ON gm.games_fischer  (date_played);
CREATE INDEX ON gm.games_carlsen  (date_played);

-- Moves table: partitioned by legend_tag, sub-partitioned by game_id hash.
-- Primary key includes game_id (sub-partition key) and legend_tag
-- (parent partition key) to satisfy PostgreSQL's partition rules.
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
    PRIMARY KEY (move_id, legend_tag, game_id)
) PARTITION BY LIST (legend_tag);

-- General partition: hash 8-way for parallel scan on 528M rows
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

-- Legend partitions: hash 4-way each (smaller tables, less parallelism needed)
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
            EXECUTE format('CREATE INDEX ON gm.moves_%s_%s (game_id, ply_number);', leg, i);
            EXECUTE format('CREATE INDEX ON gm.moves_%s_%s (fen_md5);', leg, i);
        END LOOP;
    END LOOP;
END$$;

-- Indexes on general moves sub-partitions
DO $$
DECLARE i INT;
BEGIN
    FOR i IN 0..7 LOOP
        EXECUTE format('CREATE INDEX ON gm.moves_general_%s (game_id, ply_number);', i);
        EXECUTE format('CREATE INDEX ON gm.moves_general_%s (fen_md5);', i);
    END LOOP;
END$$;

-- Recreate materialized views from migration 05 (dropped by CASCADE above).
-- The joins use (game_id, legend_tag) which is the PRIMARY KEY of games.
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
