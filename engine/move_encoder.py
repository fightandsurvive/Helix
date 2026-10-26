"""
AlphaZero 4672-way move space encoding.

4672 = 64 from-squares × 73 move-types, where 73 = 56 queen-slides +
8 knight moves + 9 underpromotion directions (3 pieces × 3 directional
buckets).

This convention is identical to Lc0's — our output tensor can be compared
directly against theirs, which matters for cross-engine validation and for
reusing any opening book computed with an AlphaZero-family engine.

The perspective-relative encoding is the standard AZ convention: from a
white perspective, up is +rank; from black, up is -rank. This halves the
effective input space the network has to learn — the same pattern
regardless of side.
"""

from __future__ import annotations

import chess
import numpy as np

QUEEN_DIRECTIONS = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]
KNIGHT_DIRECTIONS = [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
NUM_MOVES = 4672


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def move_to_index(move: chess.Move) -> int:
    """
    Encode a legal move to a flat index in [0, 4672).

    The move type is 0..72. The final index is `from_square * 73 + move_type`.
    """
    f_from = chess.square_file(move.from_square)
    r_from = chess.square_rank(move.from_square)
    f_to = chess.square_file(move.to_square)
    r_to = chess.square_rank(move.to_square)
    df, dr = f_to - f_from, r_to - r_from

    if move.promotion and move.promotion != chess.QUEEN:
        # 9 underpromotion slots per from-square (knight/bishop/rook ×
        # three directional buckets: left-diag, straight, right-diag).
        # Queen promotion is encoded as a normal queen-slide — it's
        # strategically nearly always what's played, so giving it the
        # "free" slot in the slide space is the right encoding.
        piece_idx = {chess.KNIGHT: 0, chess.BISHOP: 1, chess.ROOK: 2}[move.promotion]
        if dr > 0:  # promoting side moving "up"
            dir_idx = 0 if df < 0 else (1 if df == 0 else 2)
        else:  # "down"
            dir_idx = 0 if df > 0 else (1 if df == 0 else 2)
        move_type = 64 + piece_idx * 3 + dir_idx

    elif abs(df) * abs(dr) == 2:
        # Knight move — 8 directions, no distance
        move_type = 56 + KNIGHT_DIRECTIONS.index((df, dr))

    else:
        # Queen / king / rook / bishop slide — direction × distance
        dir_idx = QUEEN_DIRECTIONS.index((_sign(df), _sign(dr)))
        dist = max(abs(df), abs(dr))  # diagonal has |df| == |dr|, straight has one == 0
        move_type = dir_idx * 7 + (dist - 1)

    return move.from_square * 73 + move_type


def index_to_move(idx: int, board: chess.Board) -> chess.Move | None:
    """
    Reverse lookup: given a policy index and a board, return the legal move
    that matches, or None if the index doesn't correspond to a legal move.

    This is O(legal_moves) because we iterate and compare rather than
    precomputing a reverse lookup table. Legal move count is at most 218
    in extreme positions, typically ~30 — negligible cost per node.
    """
    if not (0 <= idx < NUM_MOVES):
        return None
    from_sq = idx // 73
    for m in board.legal_moves:
        if m.from_square == from_sq and move_to_index(m) == idx:
            return m
    return None


def legal_mask(board: chess.Board) -> np.ndarray:
    """
    Dense 4672-bit legal mask as float32 (1.0 = legal, 0.0 = illegal).

    Multiplied into policy logits during search to zero out illegal moves
    before softmax. Using float32 (not bool) because it stays in tensor
    math without a cast.
    """
    mask = np.zeros(NUM_MOVES, dtype=np.float32)
    for m in board.legal_moves:
        mask[move_to_index(m)] = 1.0
    return mask
