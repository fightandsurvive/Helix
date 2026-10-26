"""
Load ECO opening dictionary from the Lichess GitHub repo.

The repo at github.com/lichess-org/chess-openings stores openings in
five TSV files (a.tsv through e.tsv) — one per ECO family. There is no
combined all.tsv. This loader fetches each file and inserts them into
gm.eco_dict.

Robustness: tries both `master` and `main` branches, and both the root
and `dist/` subdirectory, because the repo layout has shifted over time.
Fails loudly if none of the URL variants respond.
"""

from __future__ import annotations

import csv
import io
import sys

import requests

from common.db import close_pool, transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

# Branches tried in order — master has been the canonical branch but
# some repos have migrated to main.
_BRANCHES = ("master", "main")
_LETTERS = ("a", "b", "c", "d", "e")
_BASE_TEMPLATES = (
    "https://raw.githubusercontent.com/lichess-org/chess-openings/{branch}/dist/{letter}.tsv",
    "https://raw.githubusercontent.com/lichess-org/chess-openings/{branch}/{letter}.tsv",
)


def _fetch_letter(letter: str) -> str | None:
    """Fetch one ECO letter file. Returns content or None if all URLs 404."""
    for template in _BASE_TEMPLATES:
        for branch in _BRANCHES:
            url = template.format(branch=branch, letter=letter)
            try:
                r = requests.get(url, timeout=30)
                if r.status_code == 200 and len(r.text) > 100:
                    log.info("eco_fetched", letter=letter, url=url, bytes=len(r.text))
                    return r.text
            except requests.RequestException as e:
                log.warning("eco_fetch_failed", url=url, error=str(e))
    return None


def main():
    combined: list[list[str]] = []
    for letter in _LETTERS:
        content = _fetch_letter(letter)
        if content is None:
            log.error("eco_letter_missing", letter=letter, message="All URL variants returned non-200")
            continue
        reader = csv.reader(io.StringIO(content), delimiter="\t")
        try:
            next(reader)  # skip header
        except StopIteration:
            continue
        for row in reader:
            if len(row) >= 3:
                combined.append(row)

    if not combined:
        log.error(
            "eco_fetch_all_failed",
            message="Could not fetch any ECO data. " "Check network access to raw.githubusercontent.com",
        )
        close_pool()
        sys.exit(1)

    log.info("eco_combined", total_rows=len(combined))

    n = 0
    try:
        with transaction() as cur:
            for row in combined:
                eco, name, pgn = row[0], row[1], row[2]
                cur.execute(
                    """
                    INSERT INTO gm.eco_dict (eco_code, opening_name, pgn, depth)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (eco_code, pgn) DO UPDATE SET
                        opening_name = EXCLUDED.opening_name,
                        depth = EXCLUDED.depth
                    """,
                    (eco, name, pgn, pgn.count(".")),
                )
                n += 1
        log.info("eco_loaded", count=n)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
