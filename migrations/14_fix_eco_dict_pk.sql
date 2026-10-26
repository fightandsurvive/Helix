-- Migration 14 - Correct the primary key on gm.eco_dict.
--
-- Original schema: PRIMARY KEY (eco_code) - wrong. There are only 500
-- possible ECO codes (A00-E99), but ~3800 named openings. Multiple
-- openings share an ECO code (e.g., "B90" has dozens of named lines).
-- ON CONFLICT (eco_code) DO UPDATE overwrote earlier openings with later
-- ones, silently capping the table at 500 rows.
--
-- Correct PK: (eco_code, pgn). The move sequence is the true unique
-- identifier; eco_code disambiguates for joins and category queries.
--
-- Safe to drop and recreate - no foreign keys reference this table.

DROP TABLE IF EXISTS gm.eco_dict CASCADE;

CREATE TABLE gm.eco_dict (
    eco_code     CHAR(3) NOT NULL CHECK (eco_code ~ '^[A-E][0-9]{2}$'),
    opening_name TEXT NOT NULL,
    pgn          TEXT NOT NULL,
    depth        SMALLINT,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (eco_code, pgn)
);

CREATE INDEX ON gm.eco_dict (eco_code);
CREATE INDEX ON gm.eco_dict USING gin (opening_name gin_trgm_ops);
