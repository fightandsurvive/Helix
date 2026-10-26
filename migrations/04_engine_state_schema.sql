-- ══════════════════════════════════════════════════════════════════════
-- Migration 04 — Checkpoint lineage, config versions, style profiles.
--
-- Every checkpoint is recorded here with SHA256 and parent lineage.
-- Together with git commit hashes stored in config JSON, this gives
-- full provenance: any checkpoint can be traced back to the exact code
-- and config that produced it.
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS engine_state;

CREATE TABLE engine_state.checkpoints (
    checkpoint_id BIGSERIAL PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,
    generation    INTEGER,
    training_step BIGINT,
    phase         TEXT NOT NULL
                  CHECK (phase IN ('supervised','distillation','selfplay','adapter')),
    elo_estimate  REAL,
    parent_ckpt   BIGINT REFERENCES engine_state.checkpoints(checkpoint_id),
    config        JSONB,
    sha256        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON engine_state.checkpoints (phase, generation);

CREATE TABLE engine_state.config_versions (
    config_id  BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    yaml       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Style weight profiles exposed to the app's Style Weight Customizer.
-- Kept in the database (not a code constant) so users can tune without
-- redeploying. The default set matches the profiles in engine/phase_router.py.
CREATE TABLE engine_state.style_weights (
    profile_name    TEXT PRIMARY KEY,
    morphy          REAL NOT NULL DEFAULT 1.0,
    petrosian       REAL NOT NULL DEFAULT 1.0,
    tal_kasparov    REAL NOT NULL DEFAULT 1.0,
    fischer         REAL NOT NULL DEFAULT 1.0,
    fischer_endgame REAL NOT NULL DEFAULT 1.0,
    carlsen_endgame REAL NOT NULL DEFAULT 1.0,
    temperature     REAL NOT NULL DEFAULT 1.0,
    contempt        REAL NOT NULL DEFAULT 0.0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO engine_state.style_weights (profile_name) VALUES
    ('balanced'),
    ('romantic'),
    ('iron_fortress'),
    ('anti_stockfish'),
    ('morphy'),
    ('petrosian'),
    ('tal'),
    ('kasparov'),
    ('fischer'),
    ('carlsen')
ON CONFLICT DO NOTHING;
