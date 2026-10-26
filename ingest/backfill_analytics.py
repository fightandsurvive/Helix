"""
One-time backfill for existing data.

Fills analytics data for games that predate the analytics schema:
    - eval.matches.eco_code / opening_name (via gm.eco_dict lookup)
    - analytics.gambit_plays (via transposition-aware matcher)
    - analytics.accuracy_jobs (queue for any game missing accuracy_results)

Idempotent. Run once after migrating from pre-analytics to current schema.
"""
from __future__ import annotations

import chess

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger
from engine.gambit_matcher import (
    find_gambits, load_gambit_index_from_db,
    moves_from_match, moves_from_pgn,
)

configure_logging()
log = get_logger(__name__)


def backfill_eco() -> None:
    """Classify ECO for eval.matches rows missing it."""
    with read_cursor() as cur:
        cur.execute("SELECT eco_code, pgn FROM gm.eco_dict")
        eco_rows = cur.fetchall()

    lookup: dict[str, tuple[str, str]] = {}
    for eco, pgn in eco_rows:
        board = chess.Board()
        try:
            for tok in pgn.split():
                t = tok.rstrip(".")
                if not t or t[0].isdigit() or t.endswith("."):
                    continue
                board.push_san(t)
        except (ValueError, chess.IllegalMoveError):
            continue
        key = " ".join(board.fen().split()[:4])
        lookup[key] = (eco, pgn)

    with read_cursor() as cur:
        cur.execute("SELECT match_id FROM eval.matches WHERE eco_code IS NULL")
        pending = [r[0] for r in cur.fetchall()]

    updated = 0
    for mid in pending:
        moves = moves_from_match(mid)
        if not moves:
            continue
        board = chess.Board()
        best: tuple[str, str] | None = None
        for _fen, san in moves[:20]:
            try:
                board.push_san(san)
            except (ValueError, chess.IllegalMoveError):
                break
            key = " ".join(board.fen().split()[:4])
            if key in lookup:
                best = lookup[key]
        if best:
            with transaction() as cur:
                cur.execute(
                    "UPDATE eval.matches SET eco_code=%s, opening_name=%s WHERE match_id=%s",
                    (best[0], best[1], mid),
                )
            updated += 1
    log.info("eco_backfill_done", updated=updated)


def backfill_gambits() -> None:
    """Insert missing gambit_plays for both personal and eval games."""
    index = load_gambit_index_from_db()
    n = 0

    # Personal games missing any gambit row
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT g.game_id, g.pgn, g.white_player, g.black_player,
                   g.user_color, g.result, g.played_at
            FROM app.personal_games g
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics.gambit_plays gp
                WHERE gp.source='personal' AND gp.ref_id = g.game_id
            )
            """
        )
        personal = cur.fetchall()

    for gid, pgn, w, b, ucolor, result, played_at in personal:
        moves = moves_from_pgn(pgn or "")
        for hit in find_gambits(moves, index):
            if hit.side != ucolor:
                continue
            participant = w if ucolor == "white" else b
            opponent = b if ucolor == "white" else w
            with transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO analytics.gambit_plays
                        (source, ref_id, participant, participant_type, opponent,
                         color, gambit_id, gambit_depth, result, played_at)
                    VALUES ('personal', %s, %s, 'human', %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        gid, participant, opponent, ucolor,
                        hit.gambit_id, hit.depth, result, played_at,
                    ),
                )
            n += 1

    # Eval matches missing any gambit row
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT m.match_id, m.white_engine, m.black_engine, m.result, m.created_at
            FROM eval.matches m
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics.gambit_plays gp
                WHERE gp.source='eval' AND gp.ref_id = m.match_id
            )
            """
        )
        eval_rows = cur.fetchall()

    for mid, w, b, result, created_at in eval_rows:
        moves = moves_from_match(mid)
        for hit in find_gambits(moves, index):
            if hit.side == "white":
                participant, opponent = w, b
                result_for_p = "win" if result == "1-0" else (
                    "loss" if result == "0-1" else "draw"
                )
            else:
                participant, opponent = b, w
                result_for_p = "win" if result == "0-1" else (
                    "loss" if result == "1-0" else "draw"
                )
            with transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO analytics.gambit_plays
                        (source, ref_id, participant, participant_type, opponent,
                         color, gambit_id, gambit_depth, result, played_at)
                    VALUES ('eval', %s, %s, 'engine', %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        mid, participant, opponent, hit.side,
                        hit.gambit_id, hit.depth, result_for_p, created_at,
                    ),
                )
            n += 1

    log.info("gambit_backfill_done", rows=n)

def backfill_per_move():
    """
    Queue accuracy recomputation for any personal game missing per-move data.

    Idempotent. Safe to run multiple times. Each game is reprocessed from
    scratch (accuracy_worker deletes existing rows for the game before
    re-inserting), so partial imports are recoverable.
    """
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO analytics.accuracy_jobs (source, ref_id, priority)
            SELECT 'personal', g.game_id, 5 FROM app.personal_games g
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics.move_losses ml WHERE ml.game_id = g.game_id
            )
            ON CONFLICT (source, ref_id) DO UPDATE SET
                status = 'pending', attempts = 0, last_error = NULL
            """
        )
    log.info("per_move_backfill_queued")

def enqueue_accuracy_jobs() -> None:
    """Queue accuracy jobs for any game missing accuracy_results."""
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO analytics.accuracy_jobs (source, ref_id, priority)
            SELECT 'personal', game_id, 5 FROM app.personal_games
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics.accuracy_results ar
                WHERE ar.source='personal' AND ar.ref_id = game_id
            )
            ON CONFLICT DO NOTHING
            """
        )
        cur.execute(
            """
            INSERT INTO analytics.accuracy_jobs (source, ref_id, priority)
            SELECT 'eval', match_id, 10 FROM eval.matches
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics.accuracy_results ar
                WHERE ar.source='eval' AND ar.ref_id = match_id
            )
            ON CONFLICT DO NOTHING
            """
        )
    log.info("accuracy_jobs_queued")


if __name__ == "__main__":
    log.info("backfill_started")
    backfill_eco()
    backfill_gambits()
    enqueue_accuracy_jobs()
    log.info("backfill_complete")
