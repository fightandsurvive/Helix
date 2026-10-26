"""Nightly refresh of Helix materialized views (Windows Task Scheduler)."""
from common.db import transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


def main():
    log.info("refresh_start")
    with transaction() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY gm.position_stats")
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY gm.position_stats_by_legend")
        cur.execute("SELECT analytics.refresh_views()")
    log.info("refresh_done")


if __name__ == "__main__":
    main()
