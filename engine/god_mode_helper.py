"""
God mode analysis helper — spawned as a subprocess from Rust's god_mode.rs.

Input:  argv[1] = FEN
Output: JSON to stdout with stockfish (top move), brilliant (if found), alternatives (top 2-3)

Why a separate process: parsing SF's line-based UCI output in Rust is
error-prone and duplicated logic. Python's chess.engine handles the
protocol correctly in ~30 lines. The cost is ~200ms per subprocess
spawn, acceptable for a user-triggered analysis command.

Brilliant detection:
- Material delta from mover's perspective ≤ -1 pawn (sacrifice)
- Post-move evaluation ≥ -20 cp (still not clearly losing)
- Compared against SF's PV — only the top 3 SF moves are checked,
  because brilliant moves should also be good moves.
"""
from __future__ import annotations

import json
import sys

import chess
import chess.engine

from config.settings import settings


def _material(board: chess.Board) -> tuple[int, int]:
    """(white_material, black_material) in pawn-units."""
    values = {
        chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
        chess.ROOK: 5, chess.QUEEN: 9,
    }
    w = b = 0
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if p:
            v = values.get(p.piece_type, 0)
            if p.color == chess.WHITE:
                w += v
            else:
                b += v
    return w, b


def analyze(fen: str) -> dict:
    board = chess.Board(fen)
    engine = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    engine.configure({"Threads": 8, "Hash": 2048})
    try:
        # Get top 3 moves with principal variations
        info = engine.analyse(
            board, chess.engine.Limit(depth=22), multipv=3,
        )
        results: list[dict] = []
        for pv in info:
            if not pv.get("pv"):
                continue
            move = pv["pv"][0]
            score = pv["score"].relative.score(mate_score=10000) or 0
            results.append({
                "uci": move.uci(),
                "san": board.san(move),
                "eval_cp": score,
                "pv": [m.uci() for m in pv["pv"][:10]],
            })

        # Brilliant detection — from mover's perspective
        mover = board.turn
        w_before, b_before = _material(board)
        brilliant: dict | None = None
        for r in results:
            m = chess.Move.from_uci(r["uci"])
            b2 = board.copy()
            b2.push(m)
            w_after, b_after = _material(b2)
            if mover == chess.WHITE:
                delta = (w_after - w_before) - (b_after - b_before)
            else:
                delta = (b_after - b_before) - (w_after - w_before)
            # Sacrificed ≥ 1 pawn AND still not clearly losing
            if delta <= -1 and r["eval_cp"] >= -20:
                brilliant = r
                break

        return {
            "stockfish": results[0] if results else None,
            "brilliant": brilliant,
            "alternatives": results[1:],
        }
    finally:
        engine.quit()


if __name__ == "__main__":
    fen = sys.argv[1] if len(sys.argv) > 1 else "startpos"
    if fen == "startpos":
        fen = chess.STARTING_FEN
    try:
        print(json.dumps(analyze(fen)))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
