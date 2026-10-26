"""
Stage 3a — SPRT gates and match recording.

Plays Trinstinctus vs Stockfish (or any UCI engine), records every game
into eval.matches + eval.match_moves, runs transposition-aware gambit
detection, and terminates on SPRT conclusion.

Opening classification happens here too — every eval.match gets an
eco_code so analytics.participant_eco_stats can roll up engine games
the same way it rolls up personal games.
"""
from __future__ import annotations

import argparse

import chess
import chess.engine
from tqdm import tqdm

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger
from config.settings import settings
from engine.gambit_matcher import find_gambits, load_gambit_index_from_db
from training.sprt import SPRT

configure_logging()
log = get_logger(__name__)


def _load_eco_lookup() -> dict[str, tuple[str, str]]:
    """Preload gm.eco_dict into position-key → (eco, name)."""
    with read_cursor() as cur:
        cur.execute("SELECT eco_code, opening_name, pgn FROM gm.eco_dict")
        rows = cur.fetchall()

    lookup: dict[str, tuple[str, str]] = {}
    for eco, name, pgn in rows:
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
        lookup[key] = (eco, name)
    log.info("eco_lookup_loaded", entries=len(lookup))
    return lookup


def _classify_opening(
    board: chess.Board, eco_lookup: dict[str, tuple[str, str]]
) -> tuple[str | None, str | None]:
    """Walk the game from move 1, find the deepest ECO match in first 20 plies."""
    moves = list(board.move_stack)
    b = chess.Board()
    best: tuple[str, str] | None = None
    for mv in moves[:20]:
        b.push(mv)
        key = " ".join(b.fen().split()[:4])
        if key in eco_lookup:
            best = eco_lookup[key]
    return best if best else (None, None)


def _result_for_participant(match_result: str, trin_was_white: bool, participant: str) -> str:
    """Map a match result to win/loss/draw from a specific participant's POV."""
    if match_result == "1/2-1/2" or match_result == "*":
        return "draw"
    white_won = match_result == "1-0"
    participant_won = (white_won == trin_was_white) if participant == "trinstinctus" else (white_won != trin_was_white)
    return "win" if participant_won else "loss"


def run_match(
    gate_name: str,
    ckpt: str,
    ref_cmd: str,
    ref_name: str,
    ref_opts: dict,
    max_games: int = 400,
    time_s: float = 10.0,
) -> tuple[str, float]:
    trin = chess.engine.SimpleEngine.popen_uci(
        ["python", "-m", "engine.uci_engine", ckpt, "800"]
    )
    ref = chess.engine.SimpleEngine.popen_uci(ref_cmd)
    if ref_opts:
        ref.configure(ref_opts)

    sprt = SPRT()
    eco_lookup = _load_eco_lookup()
    gambit_index = load_gambit_index_from_db()

    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO eval.sprt_sprints (gate_name, h0_elo, h1_elo, status)
            VALUES (%s, 0, 5, 'running') RETURNING sprint_id
            """,
            (gate_name,),
        )
        sprint_id = cur.fetchone()[0]

    wins = losses = draws = 0
    limit = chess.engine.Limit(time=time_s)
    for i in tqdm(range(max_games)):
        trin_white = (i % 2 == 0)
        board = chess.Board()
        w_engine, b_engine = (trin, ref) if trin_white else (ref, trin)

        try:
            while not board.is_game_over():
                engine = w_engine if board.turn == chess.WHITE else b_engine
                move = engine.play(board, limit).move
                if move is None:
                    break
                board.push(move)
        except chess.engine.EngineError as e:
            log.warning("game_engine_error", error=str(e))
            continue

        result = board.result() if board.is_game_over() else "*"
        trin_won = (result == "1-0") == trin_white if result in ("1-0", "0-1") else None
        if result == "1/2-1/2" or result == "*":
            draws += 1
            score = 0.5
        elif trin_won:
            wins += 1
            score = 1.0
        else:
            losses += 1
            score = 0.0

        sprt.update(score)
        eco, opening = _classify_opening(board, eco_lookup)

        # Record the match, moves, gambits, and update sprint — one transaction.
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO eval.matches
                    (gate_name, white_engine, black_engine, result, ply_count,
                     trinstictus_ckpt, eco_code, opening_name, pgn)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING match_id
                """,
                (
                    gate_name,
                    "trinstinctus" if trin_white else ref_name,
                    ref_name if trin_white else "trinstinctus",
                    result, board.ply(), ckpt, eco, opening, str(board),
                ),
            )
            mid = cur.fetchone()[0]

            # Record every ply
            replay = chess.Board()
            moves_for_gambit = []
            for ply, mv in enumerate(board.move_stack, 1):
                san = replay.san(mv)
                cur.execute(
                    """
                    INSERT INTO eval.match_moves
                        (match_id, ply_number, fen_string, move_notation)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (mid, ply, replay.fen(), san),
                )
                moves_for_gambit.append((replay.fen(), san))
                replay.push(mv)

            # Gambit detection (transposition-aware)
            for hit in find_gambits(moves_for_gambit, gambit_index):
                # The participant is whichever engine played this side
                if hit.side == "white":
                    participant = "trinstinctus" if trin_white else ref_name
                    opponent = ref_name if trin_white else "trinstinctus"
                else:
                    participant = ref_name if trin_white else "trinstinctus"
                    opponent = "trinstinctus" if trin_white else ref_name

                result_for_participant = _result_for_participant(
                    result, trin_white, participant
                )
                cur.execute(
                    """
                    INSERT INTO analytics.gambit_plays
                        (source, ref_id, participant, participant_type, opponent,
                         color, gambit_id, gambit_depth, result, played_at)
                    VALUES ('eval', %s, %s, 'engine', %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        mid, participant, opponent, hit.side,
                        hit.gambit_id, hit.depth, result_for_participant,
                    ),
                )

            cur.execute(
                """
                UPDATE eval.sprt_sprints
                SET games_played=%s, wins=%s, losses=%s, draws=%s,
                    llr=%s, status=%s
                WHERE sprint_id=%s
                """,
                (wins + losses + draws, wins, losses, draws,
                 sprt.llr, sprt.status(), sprint_id),
            )

        if sprt.status() != "running":
            log.info(
                "sprt_concluded",
                status=sprt.status(),
                games=wins + losses + draws,
                wins=wins, losses=losses, draws=draws,
            )
            break

    trin.quit()
    ref.quit()
    total = max(wins + losses + draws, 1)
    score = (wins + 0.5 * draws) / total
    return sprt.status(), score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default="G1")
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--ref", default=str(settings.stockfish_path))
    ap.add_argument("--ref-name", default="sf17")
    ap.add_argument("--ref-skill", type=int, default=10)
    ap.add_argument("--max-games", type=int, default=400)
    ap.add_argument("--time", type=float, default=10.0)
    args = ap.parse_args()

    opts = {}
    if "stockfish" in args.ref.lower():
        opts = {"Skill Level": args.ref_skill, "Threads": 4, "Hash": 4096}

    status, score = run_match(
        args.gate, args.ckpt, args.ref, args.ref_name, opts,
        args.max_games, args.time,
    )
    log.info("final_gate", status=status, score=round(score, 3))


if __name__ == "__main__":
    main()
