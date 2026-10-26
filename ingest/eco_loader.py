"""
Load ECO opening dictionary from the Lichess GitHub TSV.

One-time load. Re-running is idempotent (upsert). The TSV is ~3,000
entries, ~500KB — fetched from github.com/lichess-org/chess-openings.
"""

from __future__ import annotations

import csv
import io

import requests

from common.db import transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

ECO_URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/all.tsv"


def main():
    r = requests.get(ECO_URL, timeout=60)
    r.raise_for_status()
    reader = csv.reader(io.StringIO(r.text), delimiter="\t")
    next(reader)  # header

    n = 0
    with transaction() as cur:
        for row in reader:
            if len(row) < 3:
                continue
            eco, name, pgn = row[0], row[1], row[2]
            cur.execute(
                """
                INSERT INTO gm.eco_dict (eco_code, opening_name, pgn, depth)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (eco_code) DO UPDATE SET
                    opening_name = EXCLUDED.opening_name,
                    pgn = EXCLUDED.pgn,
                    depth = EXCLUDED.depth
                """,
                (eco, name, pgn, pgn.count(".")),
            )
            n += 1

    log.info("eco_loaded", count=n)


if __name__ == "__main__":
    main()
