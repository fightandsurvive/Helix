"""
Stage 2a — generate the Stockfish teacher corpus.

Two-tier strategy:
    Bulk run:      --positions 2000000 --depth 18  (~4-6 days)
    Refinement run: --positions 100000  --depth 24  (~1-2 days)

Depth 18 captures ~99% of SF's signal on most positions. Depth 22-24
only differs meaningfully in sharp tactical middlegames — which is
precisely what the small refinement corpus targets. Two corpora,
two runs, no single run takes a month.
"""
from __future__ import annotations

import argparse
import random

import chess
import chess.engine
from tqdm import tqdm

from common.db import read_cursor
from config.logging_setup import configure_logging, get_logger
from config.settings import settings

configure_logging()
log = get_logger(__name__)


def sample_positions(n: int) -> list[tuple[str, str]]:
    """
    Sample n middlegame positions from the GM warehouse.

    TABLESAMPLE BERNOULLI(1) on the parent partitioned table selects 1%
    of rows uniformly. For 500M rows that's ~5M candidates — enough headroom
    to draw 2M after the ply filter and random subsample.
    """
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT fen_string, move_notation
            FROM gm.moves_general TABLESAMPLE BERNOULLI(1)
            WHERE ply_number BETWEEN 6 AND 40
            LIMIT %s
            """,
            (n * 2,),
        )
        rows = cur.fetchall()
    random.shuffle(rows)
    return rows[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", type=int, default=2_000_000)
    ap.add_argument("--depth", type=int, default=18,
                    help="SF search depth — 18 for bulk, 22-24 for refinement")
    ap.add_argument("--threads", type=int, default=32)
    ap.add_argument("--hash", type=int, default=8192)
    ap.add_argument("--out", default="/tmp/sf_distill.tsv")
    args = ap.parse_args()

    log.info("distill_start", positions=args.positions, depth=args.depth)
    positions = sample_positions(args.positions)
    log.info("positions_sampled", count=len(positions))

    engine = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    engine.configure({"Threads": args.threads, "Hash": args.hash})

    try:
        with open(args.out, "w", buffering=1 << 20) as f, \
             tqdm(total=len(positions), desc=f"depth {args.depth}") as pbar:
            for fen, san in positions:
                try:
                    board = chess.Board(fen)
                    info = engine.analyse(
                        board,
                        chess.engine.Limit(depth=args.depth),
                        multipv=5,
                    )
                    eval_cp = info[0]["score"].relative.score(mate_score=10000) or 0
                    moves = [
                        pv["pv"][0].uci() if pv.get("pv") else ""
                        for pv in info[:5]
                    ]
                    f.write(f"{fen}\t{eval_cp}\t{san}\t{','.join(moves)}\n")
                except Exception as e:
                    log.warning("distill_position_failed", fen=fen, error=str(e))
                pbar.update(1)
    finally:
        engine.quit()

    log.info("distill_done", output=args.out, positions=len(positions), depth=args.depth)


if __name__ == "__main__":
    main()
