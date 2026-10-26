"""
Puzzle sources:
  - lichess:    bulk CSV from database.lichess.org (~5M puzzles, 450MB compressed)
  - gm_derived: SF-verified positions where the GM's move was clearly best
                (best move ≥200cp above 2nd best, GM actually played it)
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import tempfile
from pathlib import Path

import chess
import chess.engine
import psycopg
import requests
from tqdm import tqdm

from common.db import read_cursor
from config.logging_setup import configure_logging, get_logger
from config.settings import settings

configure_logging()
log = get_logger(__name__)


def load_lichess():
    url = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
    _tmp = Path(tempfile.gettempdir())
    local = str(_tmp / "lichess_puzzles.csv.zst")
    if not os.path.exists(local):
        log.info("downloading_lichess_puzzles")
        r = requests.get(url, stream=True, timeout=600)
        r.raise_for_status()
        with open(local, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)

    tsv = str(_tmp / "lichess_puzzles.csv")
    subprocess.run(["zstd", "-d", "-f", local, "-o", tsv], check=True)

    n = 0
    with open(tsv) as f, psycopg.connect(str(settings.database_url), autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                reader = csv.reader(f)
                next(reader)
                batch: list[tuple] = []
                for row in tqdm(reader, desc="lichess_puzzles"):
                    if len(row) < 8:
                        continue
                    pid, fen, moves, rating = row[0], row[1], row[2], int(row[3])
                    themes = row[7].split() if row[7] else []
                    sol = " ".join(moves.split()[1:])  # skip opponent's setup move
                    try:
                        board = chess.Board(fen)
                    except Exception:
                        continue
                    pc = len(board.piece_map())
                    phase = "Opening" if pc >= 28 else ("Middlegame" if pc >= 12 else "Endgame")
                    batch.append((pid, fen, sol, rating, themes, "lichess", None, pc, phase))
                    if len(batch) >= 2000:
                        _flush_puzzles(cur, batch)
                        n += len(batch)
                        batch = []
                if batch:
                    _flush_puzzles(cur, batch)
                    n += len(batch)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    log.info("lichess_loaded", count=n)


def _flush_puzzles(cur, batch: list[tuple]) -> None:
    cur.executemany(
        """
        INSERT INTO app.puzzles
            (puzzle_id, fen, solution_uci, rating, themes, source,
             eco_code, piece_count, phase_flag)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        batch,
    )


def load_gm_derived(max_puzzles: int = 5000):
    engine = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    engine.configure({"Threads": 32, "Hash": 8192})

    with read_cursor() as cur:
        cur.execute(
            """
            SELECT game_id, eco_code, fen_string, move_notation
            FROM gm.moves_general TABLESAMPLE BERNOULLI(5)
            WHERE ply_number BETWEEN 15 AND 60
            LIMIT %s
            """,
            (max_puzzles * 3,),
        )
        rows = cur.fetchall()

    n = 0
    try:
        with psycopg.connect(str(settings.database_url), autocommit=False) as conn:
            try:
                with conn.cursor() as cur:
                    for gid, eco, fen, san in tqdm(rows, desc="gm_puzzles"):
                        if n >= max_puzzles:
                            break
                        try:
                            board = chess.Board(fen)
                            actual = board.parse_san(san)
                            info = engine.analyse(board, chess.engine.Limit(depth=22), multipv=2)
                            if len(info) < 2:
                                continue
                            s1 = info[0]["score"].relative.score(mate_score=10000) or 0
                            s2 = info[1]["score"].relative.score(mate_score=10000) or 0
                            if s1 - s2 < 200:
                                continue
                            best = info[0]["pv"][0]
                            if best != actual:
                                continue
                            pid = f"gm_{gid}_{board.ply()}"
                            pc = len(board.piece_map())
                            phase = "Opening" if pc >= 28 else "Middlegame" if pc >= 12 else "Endgame"
                            cur.execute(
                                """
                                INSERT INTO app.puzzles
                                    (puzzle_id, fen, solution_uci, rating, themes,
                                     source, eco_code, piece_count, phase_flag)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT DO NOTHING
                                """,
                                (
                                    pid,
                                    fen,
                                    best.uci(),
                                    1800,
                                    ["gm_derived"],
                                    "gm_derived",
                                    eco,
                                    pc,
                                    phase,
                                ),
                            )
                            n += 1
                            if n % 100 == 0:
                                conn.commit()
                        except Exception as e:
                            log.warning("gm_puzzle_skip", error=str(e))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    finally:
        engine.quit()

    log.info("gm_derived_loaded", count=n)


if __name__ == "__main__":
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("--source", choices=["lichess", "gm_derived"], required=True)
        ap.add_argument("--max-games", type=int, default=5000)
        args = ap.parse_args()
        if args.source == "lichess":
            load_lichess()
        else:
            load_gm_derived(args.max_games)
    finally:
        from common.db import close_pool

        close_pool()
