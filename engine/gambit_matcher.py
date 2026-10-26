"""
Transposition-aware gambit matcher.

Prefix-matching under-counts gambits by 30-40% because transpositions
are ubiquitous in the opening. The Evans Gambit can be reached in 4
different move orders depending on what black does first; a game that
transposes into it is still an Evans Gambit, but prefix-matching misses
3 of the 4 paths.

The correct approach: index each gambit by its final position key (FEN
minus halfmove and fullmove counters — the standard transposition key),
then for each game, check whether the gambit's final position appears
anywhere in the game.

The matcher consumes `list[(fen_before, san)]` — a source-agnostic
abstraction. Both app.personal_games (via PGN) and eval.matches (via
match_moves table) produce this shape, so there is exactly one matcher
and no dual code paths.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import chess
import chess.pgn

from config.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class GambitHit:
    """A single gambit detection for one game."""
    gambit_id: str
    side: str       # 'white' or 'black' — the color that plays the gambit
    depth: int      # ply count of the gambit line


def _position_key(board: chess.Board) -> str:
    """
    FEN minus halfmove clock and fullmove number — the standard
    transposition key. Two positions with the same first four FEN fields
    are chess-identical (reachable by the same continuations).
    """
    return " ".join(board.fen().split()[:4])


def build_gambit_index(
    gambits: list[tuple[str, str, str]],
) -> dict[str, list[GambitHit]]:
    """
    Build a position-key → gambit-list index from a list of
    (gambit_id, moves_uci_space_separated, side) tuples.

    Called once per worker process. The index is O(gambits) in memory —
    ~600 gambits × ~10 hits/position × ~200 bytes/hit ≈ 1.2 MB total.
    Trivial.
    """
    index: dict[str, list[GambitHit]] = {}
    for gambit_id, uci_str, side in gambits:
        moves = uci_str.split()
        if not moves:
            continue
        board = chess.Board()
        try:
            for uci in moves:
                board.push_uci(uci)
        except (ValueError, chess.IllegalMoveError) as e:
            log.warning("gambit_bad_moves", gambit_id=gambit_id, error=str(e))
            continue
        key = _position_key(board)
        index.setdefault(key, []).append(
            GambitHit(gambit_id=gambit_id, side=side, depth=len(moves))
        )
    return index


def load_gambit_index_from_db() -> dict[str, list[GambitHit]]:
    """
    Query app.gambits and build the index. Called once at worker startup.
    """
    from common.db import read_cursor

    with read_cursor() as cur:
        cur.execute("SELECT gambit_id, moves_uci, side FROM app.gambits")
        rows = cur.fetchall()
    index = build_gambit_index(rows)
    log.info(
        "gambit_index_loaded",
        gambits=len(rows),
        positions=len(index),
    )
    return index


def find_gambits(
    moves: list[tuple[str, str]],
    index: dict[str, list[GambitHit]],
) -> list[GambitHit]:
    """
    Find all gambit positions a game reached.

    A single game can hit multiple gambits (e.g., transposition from one
    gambit into another). The deduplication rule: one hit per unique
    gambit_id, keeping the deepest match (which is the "most specific"
    identification — a 4-ply Evans Gambit hit is more informative than a
    2-ply Italian Game hit that happens to lead into it).

    Args:
        moves: (fen_before, san) pairs in game order
        index: output of build_gambit_index / load_gambit_index_from_db

    Returns:
        Deduplicated list of GambitHit
    """
    board = chess.Board()
    hits: dict[str, GambitHit] = {}
    for fen_before, san in moves:
        try:
            board.push_san(san)
        except (ValueError, chess.IllegalMoveError):
            log.warning("gambit_game_bad_move", san=san, fen=fen_before)
            break
        key = _position_key(board)
        for hit in index.get(key, ()):
            existing = hits.get(hit.gambit_id)
            if existing is None or hit.depth > existing.depth:
                hits[hit.gambit_id] = hit
    return list(hits.values())


def moves_from_pgn(pgn_text: str) -> list[tuple[str, str]]:
    """Extract (fen_before, san) pairs from a PGN string."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return []
    board = game.board()
    out: list[tuple[str, str]] = []
    for move in game.mainline_moves():
        san = board.san(move)
        out.append((board.fen(), san))
        board.push(move)
    return out


def moves_from_match(match_id: int) -> list[tuple[str, str]]:
    """Extract (fen_before, san) pairs from eval.match_moves."""
    from common.db import read_cursor

    with read_cursor() as cur:
        cur.execute(
            """
            SELECT fen_string, move_notation FROM eval.match_moves
            WHERE match_id = %s ORDER BY ply_number
            """,
            (match_id,),
        )
        return cur.fetchall()
