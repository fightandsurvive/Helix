-- ══════════════════════════════════════════════════════════════════════
-- Migration 06 — Nightly materialized view refreshes.
--
-- Uses pg_cron (installed via `CREATE EXTENSION pg_cron` in the setup
-- script) to refresh the analytics views during the overnight window.
-- CONCURRENTLY doesn't lock readers during refresh.
--
-- Guards with a pg_extension check so this migration is a no-op if
-- pg_cron isn't installed — the app still works, views just don't
-- refresh automatically. Manual refresh: SELECT analytics.refresh_views();
-- ══════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.schedule(
            'helix-refresh-position-stats',
            '0 3 * * *',
            'REFRESH MATERIALIZED VIEW CONCURRENTLY gm.position_stats;'
        );
        PERFORM cron.schedule(
            'helix-refresh-legend-stats',
            '15 3 * * *',
            'REFRESH MATERIALIZED VIEW CONCURRENTLY gm.position_stats_by_legend;'
        );
        PERFORM cron.schedule(
            'helix-refresh-matchup-stats',
            '30 3 * * *',
            'REFRESH MATERIALIZED VIEW CONCURRENTLY eval.matchup_stats;'
        );
    END IF;
END $$;
