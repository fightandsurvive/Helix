"""
Async accuracy worker.

Consumes analytics.accuracy_jobs and writes per-participant accuracy +
ACPL into analytics.accuracy_results. Multiple workers coexist via
FOR UPDATE SKIP LOCKED — claims are race-free.

Design choice: analyse N+1 positions per game (one per ply including the
final), not 2N. The eval at position i is from side-to-move's perspective;
at position i+1 it's from the opponent's. Converting: loss = e_i + e_{i+1}_opp.
Halves the SF cost of accuracy computation.
"""
from __future__ import annotations

import math
import signal
import time

import chess
import chess.engine

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger
from config.settings import settings
from engine.gambit_matcher import moves_from_match, moves_from_pgn

configure_logging()
log = get_logger(__name__)

_SHUTDOWN = False


def _handle_signal(signum, frame):
    global _SHUTDOWN
    log.info("shutdown_requested", signal=signum)
    _SHUTDOWN = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _claim_job() -> tuple[int, str, int] | None:
    """Atomic claim. Returns (job_id, source, ref_id) or None if no pending work."""
    with transaction() as cur:
        cur.execute(
            """
            UPDATE analytics.accuracy_jobs
            SET status='running', started_at=NOW(), attempts=attempts+1
            WHERE job_id = (
                SELECT job_id FROM analytics.accuracy_jobs
                WHERE status='pending' AND attempts < 3
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING job_id, source, ref_id
            """
        )
        return cur.fetchone()


def _mark_done(job_id: int) -> None:
    with transaction() as cur:
        cur.execute(
            "UPDATE analytics.accuracy_jobs SET status='done', finished_at=NOW() WHERE job_id=%s",
            (job_id,),
        )


def _mark_failed(job_id: int, error: str) -> None:
    with transaction() as cur:
        cur.execute(
            """
            UPDATE analytics.accuracy_jobs
            SET status = CASE WHEN attempts >= 3 THEN 'failed' ELSE 'pending' END,
                last_error = %s,
                finished_at = CASE WHEN attempts >= 3 THEN NOW() ELSE NULL END
            WHERE job_id=%s
            """,
            (error[:500], job_id),
        )


def _load_participants(source: str, ref_id: int) -> list[dict] | None:
    if source == "eval":
        with read_cursor() as cur:
            cur.execute(
                "SELECT white_engine, black_engine FROM eval.matches WHERE match_id = %s",
                (ref_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return [
            {"name": row[0], "type": "engine", "color": "white"},
            {"name": row[1], "type": "engine", "color": "black"},
        ]
    else:
        with read_cursor() as cur:
            cur.execute(
                "SELECT white_player, black_player, user_color FROM app.personal_games WHERE game_id = %s",
                (ref_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        w, b, ucolor = row
        return [
            {
                "name": w if ucolor == "white" else b,
                "type": "human",
                "color": ucolor,
            }
        ]


def _evaluate(moves, engine, depth):
    """
    Returns (per_color_stats, move_details).

    move_details has one entry per ply with (ply_number, move_san, fen_after,
    white_eval_cp, played_by_user, user_loss_cp) — used to populate both
    analytics.move_losses (user's moves only) and analytics.game_eval_curve
    (every ply).
    """
    if not moves:
        return {}, []

    positions = [chess.Board(moves[0][0])]
    for _fen, san in moves:
        b = positions[-1].copy()
        try:
            b.push_san(san)
        except (ValueError, chess.IllegalMoveError):
            break
        positions.append(b)

    evals = []
    for pos in positions:
        try:
            info = engine.analyse(pos, chess.engine.Limit(depth=depth))
            cp = info["score"].relative.score(mate_score=10000) or 0
            evals.append(cp)
        except (chess.engine.EngineError, KeyError):
            evals.append(0)

    losses = {"white": [], "black": []}
    move_details = []

    for i in range(len(moves)):
        mover_turn = positions[i].turn
        color = "white" if mover_turn == chess.WHITE else "black"
        e_i = evals[i]
        e_next_opp = evals[i + 1] if i + 1 < len(evals) else 0
        e_next_mover = -e_next_opp
        loss = max(0, e_i - e_next_mover)
        losses[color].append(loss)

        fen_before, san = moves[i]
        board = chess.Board(fen_before)
        try:
            move_obj = board.parse_san(san)
        except (ValueError, chess.IllegalMoveError):
            continue
        piece = board.piece_at(move_obj.from_square)
        if piece is None:
            continue
        pc = len(board.piece_map())
        phase = "Opening" if pc >= 28 else ("Middlegame" if pc >= 12 else "Endgame")
        to_file = chess.square_file(move_obj.to_square)
        region = "queenside" if to_file <= 2 else ("kingside" if to_file >= 5 else "center")

        after_board = positions[i + 1] if i + 1 < len(positions) else positions[i]
        white_eval_cp = e_next_mover if color == "white" else -e_next_mover

        move_details.append({
            "ply_number": i + 1,
            "move_san": san,
            "fen_before": fen_before,
            "fen_after": after_board.fen(),
            "eval_before_cp": e_i,
            "eval_after_cp": e_next_mover,
            "loss_cp": int(loss),
            "phase": phase,
            "piece_moved": piece.symbol().upper(),
            "to_square": chess.square_name(move_obj.to_square),
            "board_region": region,
            "color_played": color,
            "white_eval_cp": white_eval_cp,
        })

    out = {}
    for color, ls in losses.items():
        if not ls:
            continue
        acpl = sum(ls) / len(ls)
        accs = [max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * l) - 3.1669)) for l in ls]
        accuracy = sum(accs) / len(accs)
        out[color] = (round(accuracy, 2), round(acpl, 1), len(ls))

    return out, move_details


def _process_job(job_id, source, ref_id):
    participants = _load_participants(source, ref_id)
    if participants is None:
        _mark_done(job_id)
        return

    if source == "eval":
        moves = moves_from_match(ref_id)
        user_color = None
    else:
        with read_cursor() as cur:
            cur.execute("SELECT pgn, user_color FROM app.personal_games WHERE game_id=%s", (ref_id,))
            row = cur.fetchone()
        if not row or not row[0]:
            _mark_done(job_id)
            return
        moves = moves_from_pgn(row[0])
        user_color = row[1]

    if not moves:
        _mark_done(job_id)
        return

    engine = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    engine.configure({"Threads": 8, "Hash": 2048})
    try:
        results, move_details = _evaluate(moves, engine, depth=16)
    finally:
        engine.quit()

    with transaction() as cur:
        for p in participants:
            color = p["color"]
            if color not in results:
                continue
            accuracy, acpl, n = results[color]
            cur.execute(
                """INSERT INTO analytics.accuracy_results
                    (source, ref_id, participant, participant_type, color, accuracy, acpl, moves_analyzed)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source, ref_id, participant) DO UPDATE SET
                     accuracy=EXCLUDED.accuracy, acpl=EXCLUDED.acpl,
                     moves_analyzed=EXCLUDED.moves_analyzed, computed_at=NOW()""",
                (source, ref_id, p["name"], p["type"], color, accuracy, acpl, n),
            )

        if source == "personal" and user_color:
            cur.execute("DELETE FROM analytics.move_losses WHERE game_id = %s", (ref_id,))
            cur.execute("DELETE FROM analytics.game_eval_curve WHERE game_id = %s", (ref_id,))

            from psycopg.extras import execute_batch

            user_moves = [d for d in move_details if d["color_played"] == user_color]
            if user_moves:
                execute_batch(
                    cur,
                    """INSERT INTO analytics.move_losses
                        (game_id, ply_number, move_san, fen_before, eval_before_cp,
                         eval_after_cp, loss_cp, phase, piece_moved, to_square, board_region)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(ref_id, d["ply_number"], d["move_san"], d["fen_before"],
                      d["eval_before_cp"], d["eval_after_cp"], d["loss_cp"],
                      d["phase"], d["piece_moved"], d["to_square"], d["board_region"])
                     for d in user_moves],
                    page_size=200,
                )

            curve_rows = [
                (ref_id, d["ply_number"], d["move_san"], d["fen_after"],
                 d["white_eval_cp"], d["color_played"] == user_color,
                 d["loss_cp"] if d["color_played"] == user_color else None)
                for d in move_details
            ]
            if curve_rows:
                execute_batch(
                    cur,
                    """INSERT INTO analytics.game_eval_curve
                        (game_id, ply_number, move_san, fen_after, white_eval_cp,
                         played_by_user, user_loss_cp)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    curve_rows,
                    page_size=200,
                )

    _mark_done(job_id)

def main():
    log.info("accuracy_worker_started")
    while not _SHUTDOWN:
        job = _claim_job()
        if job is None:
            time.sleep(2)
            continue
        job_id, source, ref_id = job
        try:
            _process_job(job_id, source, ref_id)
        except Exception as e:
            log.exception("job_failed", job_id=job_id, error=str(e))
            _mark_failed(job_id, str(e))
    log.info("accuracy_worker_stopped")


if __name__ == "__main__":
    main()
