-- Migration 02 - Self-play storage.
--
-- Partitioned by generation because RL data goes stale. Training on
-- generation-30 data when the network is at generation-300 trains against
-- a policy that no longer exists - a silent form of overfitting.
--
-- Retention: 12 generations kept in full, generations 13-20 sampled to
-- 1% (regression test corpus), 21+ dropped entirely. Enforced by
-- selfplay.prune_old_generations called from the orchestrator.

CREATE SCHEMA IF NOT EXISTS selfplay;

-- Games (one row per completed self-play game)
CREATE TABLE selfplay.games (
    generation      INTEGER NOT NULL,
    game_id         BIGINT  NOT NULL,
    engine_version  TEXT    NOT NULL,
    result          TEXT    NOT NULL CHECK (result IN ('1-0','0-1','1/2-1/2','*')),
    ply_count       SMALLINT NOT NULL,
    sf_distilled    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (generation, game_id)
) PARTITION BY LIST (generation);

-- Positions (training samples, one row per ply)
-- policy_target is a 4672-element FLOAT4 array - the MCTS visit distribution.
-- value_net is the network's own prediction at play time (used to compute
-- value_residual, which drives prioritized training).
CREATE TABLE selfplay.positions (
    generation      INTEGER NOT NULL,
    position_id     BIGINT  NOT NULL,
    game_id         BIGINT  NOT NULL,
    ply_number      SMALLINT NOT NULL,
    fen_string      TEXT    NOT NULL,
    fen_md5         UUID    GENERATED ALWAYS AS (md5(fen_string)::uuid) STORED,
    policy_target   FLOAT4[] NOT NULL,
    value_target    REAL    NOT NULL,
    value_net       REAL,
    stockfish_eval  REAL,
    piece_count     SMALLINT NOT NULL,
    phase_flag      TEXT    NOT NULL,
    PRIMARY KEY (generation, position_id)
) PARTITION BY LIST (generation);

-- Regression corpus - 1% sample retained from pruned generations so we
-- can measure whether newer models regress on older data.
CREATE TABLE selfplay.positions_regression (LIKE selfplay.positions INCLUDING ALL);

-- Partition factory - called from training/train_rl.py at the start of
-- every generation.
CREATE OR REPLACE FUNCTION selfplay.create_generation_partition(gen INTEGER)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS selfplay.games_gen_%s '
        'PARTITION OF selfplay.games FOR VALUES IN (%s);',
        gen, gen
    );
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS selfplay.positions_gen_%s '
        'PARTITION OF selfplay.positions FOR VALUES IN (%s);',
        gen, gen
    );
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_pos_gen_%s_residual '
        'ON selfplay.positions_gen_%s '
        '((ABS(value_target - COALESCE(value_net, 0))) DESC);',
        gen, gen
    );
END;
$$;

-- Retention - samples old partitions to positions_regression, then drops
-- them. Idempotent, safe to run repeatedly.
CREATE OR REPLACE FUNCTION selfplay.prune_old_generations(
    current_gen INTEGER,
    keep_full   INT DEFAULT 12,
    keep_sampled INT DEFAULT 20
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE gen_rec RECORD;
BEGIN
    FOR gen_rec IN
        SELECT DISTINCT generation
        FROM selfplay.games
        WHERE generation < current_gen - keep_full
    LOOP
        IF gen_rec.generation <= current_gen - keep_sampled THEN
            EXECUTE format(
                'INSERT INTO selfplay.positions_regression '
                'SELECT * FROM selfplay.positions_gen_%s TABLESAMPLE BERNOULLI(1);',
                gen_rec.generation
            );
            EXECUTE format('DROP TABLE IF EXISTS selfplay.positions_gen_%s;', gen_rec.generation);
            EXECUTE format('DROP TABLE IF EXISTS selfplay.games_gen_%s;', gen_rec.generation);
        END IF;
    END LOOP;
END;
$$;
