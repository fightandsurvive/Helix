-- ══════════════════════════════════════════════════════════════════════
-- Migration 08 — Operational telemetry.
--
-- Writes from training jobs are throttled to 1 Hz by the reporter
-- (training/progress.py). Reads from the app are cheap — the Training
-- Monitor polls training_progress at 0.5 Hz.
--
-- loss_history is separate from training_progress so pruning old
-- self-play generations doesn't destroy the loss visualisation timeline.
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE ops.training_runs (
    run_id             UUID PRIMARY KEY,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at           TIMESTAMPTZ,
    status             TEXT NOT NULL
                       CHECK (status IN ('running','paused','failed','completed')),
    config             JSONB NOT NULL,
    current_generation INTEGER DEFAULT 0
);
-- Exactly one run can be 'running' at a time. Partial index.
CREATE UNIQUE INDEX ops_one_running
    ON ops.training_runs ((status)) WHERE status = 'running';

CREATE TABLE ops.training_progress (
    progress_id     BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES ops.training_runs(run_id) ON DELETE CASCADE,
    generation      INT NOT NULL,
    stage           TEXT NOT NULL
                    CHECK (stage IN (
                        'idle','selfplay','training','gating',
                        'adapters','pruning','error'
                    )),
    stage_progress  REAL NOT NULL DEFAULT 0.0,
    games_completed INT DEFAULT 0,
    games_target    INT DEFAULT 0,
    policy_loss     REAL,
    value_loss      REAL,
    total_loss      REAL,
    elo_estimate    REAL,
    elo_stderr      REAL,
    sprt_llr        REAL,
    sprt_upper      REAL,
    sprt_lower      REAL,
    sprt_status     TEXT,
    eta_seconds     INT,
    message         TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON ops.training_progress (run_id, updated_at DESC);

CREATE TABLE ops.loss_history (
    generation  INT NOT NULL,
    step        BIGINT NOT NULL,
    policy_loss REAL,
    value_loss  REAL,
    total_loss  REAL,
    lr          REAL,
    PRIMARY KEY (generation, step)
);
