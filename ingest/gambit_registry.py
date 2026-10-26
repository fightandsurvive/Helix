"""
Seed app.gambits from the Lichess opening database.

Filter: only entries whose name contains "gambit". This yields ~600
gambits spanning all ECO codes. Soundness classification:
    - A0x / B0x entries → 'unsound' (early gambits are usually refuted)
    - All others → 'speculative'
Users can hand-curate soundness over time; the default is a heuristic.
"""

from __future__ import annotations

import requests

from common.db import transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

ECO_URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/all.tsv"


def main():
    r = requests.get(ECO_URL, timeout=60)
    r.raise_for_status()

    n = 0
    with transaction() as cur:
        for line in r.text.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            eco, name, pgn = parts[0], parts[1], parts[2]
            if "gambit" not in name.lower():
                continue
            side = "white" if len(pgn.split()) % 2 == 1 else "black"
            gambit_id = f"{eco}_{name.replace(' ', '_')[:60]}"
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


if __name__ == "__main__":
    main()
