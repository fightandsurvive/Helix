"""
Seed app.gambits from the Lichess opening database.

Fetches the same a-e TSV files as eco_loader, filters to rows whose
name contains "gambit", and inserts them. Soundness is a heuristic
(A0x/B0x gambits are typically unsound; others speculative).
"""

from __future__ import annotations

import hashlib

import requests

from common.db import close_pool, transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

_BRANCHES = ("master", "main")
_LETTERS = ("a", "b", "c", "d", "e")
_BASE_TEMPLATES = (
    "https://raw.githubusercontent.com/lichess-org/chess-openings/{branch}/dist/{letter}.tsv",
    "https://raw.githubusercontent.com/lichess-org/chess-openings/{branch}/{letter}.tsv",
)


def _fetch_letter(letter: str) -> str | None:
    for template in _BASE_TEMPLATES:
        for branch in _BRANCHES:
            url = template.format(branch=branch, letter=letter)
            try:
                r = requests.get(url, timeout=30)
                if r.status_code == 200 and len(r.text) > 100:
                    return r.text
            except requests.RequestException:
                pass
    return None


def main():
    n = 0
    try:
        with transaction() as cur:
            for letter in _LETTERS:
                content = _fetch_letter(letter)
                if content is None:
                    log.warning("gambit_letter_missing", letter=letter)
                    continue
                for line in content.splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) < 3:
                        continue
                    eco, name, pgn = parts[0], parts[1], parts[2]
                    if "gambit" not in name.lower():
                        continue
                    side = "white" if len(pgn.split()) % 2 == 1 else "black"

                    # Include pgn hash to disambiguate openings that share
                    # an ECO code and a truncated name. Without this,
                    # duplicate gambit_ids were silently dropped.
                    pgn_hash = hashlib.md5(pgn.encode()).hexdigest()[:6]
                    gambit_id = f"{eco}_{name.replace(' ', '_')[:55]}_{pgn_hash}"
                    soundness = "unsound" if eco.startswith(("A0", "B0")) else "speculative"
                    cur.execute(
                        """
                        INSERT INTO app.gambits
                            (gambit_id, eco_code, name, side, moves_uci, soundness)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (gambit_id) DO NOTHING
                        """,
                        (gambit_id, eco, name, side, pgn, soundness),
                    )
                    n += 1
        log.info("gambits_seeded", count=n)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
