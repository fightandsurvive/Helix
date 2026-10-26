"""
Populate app.opening_lines from gm.eco_dict.

Each ECO entry becomes an opening line with:
    - Full move list (UCI + SAN)
    - FEN before each move (for step-through replay)
    - Side (white or black — the side that "plays" the line)
    - Difficulty (1-5, based on ply count)

This populates the opening trainer's deck. Runs once after eco_loader.
"""
from __future__ import annotations

import chess

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


def main():
    with read_cursor() as cur:
        cur.execute("SELECT eco_code, opening_name, pgn FROM gm.eco_dict")
        rows = cur.fetchall()

    n = 0
    with transaction() as cur:
        for eco, name, pgn in rows:
            board = chess.Board()
            ucis: list[str] = []
            sans: list[str] = []
            fens: list[str] = []
            ok = True
            for token in pgn.split():
                tok = token.rstrip(".")
                if not tok or tok[0].isdigit() or tok.endswith("."):
                    continue
                try:
                    m = board.parse_san(tok)
                except Exception:
                    ok = False
                    break
                fens.append(board.fen())
                ucis.append(m.uci())
                sans.append(board.san(m))
                board.push(m)

            if not ok or not ucis:
                continue

            line_id = f"{eco}_{name.replace(' ', '_')[:60]}_{len(ucis)}ply"
            side = "white" if len(sans) % 2 == 1 else "black"

            cur.execute(
                """
                INSERT INTO app.opening_lines
                    (line_id, eco_code, name, moves_uci, moves_san, fens, side, difficulty)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (line_id) DO NOTHING
                """,
                (
                    line_id, eco, name, " ".join(ucis), sans, fens, side,
                    max(1, min(5, len(ucis) // 4)),
                ),
            )
            n += 1

    log.info("opening_lines_loaded", count=n)


if __name__ == "__main__":
    main()
