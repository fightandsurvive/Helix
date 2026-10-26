"""
Board → model input tensor encoding.

64 squares, one token each. Fixed token sequence (66 tokens total:
1 CLS + 1 global + 64 squares) means no padding anywhere — clean batching
and predictable VRAM usage.

The global token carries side-to-move, castling rights, en-passant file,
and halfmove clock. All four are projected to d_model in the model itself;
this module only produces the raw feature vector.
"""

from __future__ import annotations

import chess
import numpy as np
import torch

# Piece → integer index. 0 = empty. Lowercase = black, uppercase = white.
# This mapping is fixed and must never change — checkpoints are trained
# against it, and a swap invalidates every saved weight.
PIECE_TO_IDX = {
    "P": 1,
    "N": 2,
    "B": 3,
    "R": 4,
    "Q": 5,
    "K": 6,
    "p": 7,
    "n": 8,
    "b": 9,
    "r": 10,
    "q": 11,
    "k": 12,
}

# Global feature layout: [stm, castle_K, castle_Q, castle_k, castle_q,
#                          ep_file_0..7, halfmove_clock]
NUM_GLOBAL_FEATS = 1 + 4 + 8 + 1


def encode_board(board: chess.Board) -> dict:
    """
    Convert a python-chess Board into model input tensors.

    Returns:
        squares: LongTensor(64) — piece index per square
        global:  FloatTensor(14) — STM, castling, ep file, halfmove

    These two tensors are the entire network input. No move history, no
    repetition counters — those live in the search, not the network.
    """
    squares = np.zeros(64, dtype=np.int64)
    for sq in range(64):
        piece = board.piece_at(sq)
        if piece is not None:
            squares[sq] = PIECE_TO_IDX[piece.symbol()]

    stm = 1.0 if board.turn == chess.WHITE else 0.0
    castling = [
        float(board.has_kingside_castling_rights(chess.WHITE)),
        float(board.has_queenside_castling_rights(chess.WHITE)),
        float(board.has_kingside_castling_rights(chess.BLACK)),
        float(board.has_queenside_castling_rights(chess.BLACK)),
    ]
    ep_file = [-1.0] * 8
    if board.ep_square is not None:
        ep_file[chess.square_file(board.ep_square)] = 1.0
    # Cap halfmove at 100 (50-move rule threshold) — beyond that, all
    # positions look "very long" to the network anyway.
    halfmove = min(board.halfmove_clock, 100) / 100.0

    global_feats = np.array([stm, *castling, *ep_file, halfmove], dtype=np.float32)

    return {
        "squares": torch.from_numpy(squares),
        "global": torch.from_numpy(global_feats),
    }
