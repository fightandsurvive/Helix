"""
Core smoke tests — invariants that, if broken, silently corrupt training.

Run `pytest tests/` before every training session. These are cheap (~5s
total) and catch the failures that produce wasted GPU days.
"""
from __future__ import annotations

import chess
import numpy as np
import pytest
import torch

from engine.board_encoder import encode_board
from engine.model import ChessTransformer
from engine.move_encoder import NUM_MOVES, index_to_move, legal_mask, move_to_index
from engine.phase_router import DEFAULT_CONFIG, route_weights, STYLE_PROFILES


def test_policy_space_roundtrip():
    """Every legal move maps to a unique index in [0, 4672)."""
    for fen in [
        chess.STARTING_FEN,
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    ]:
        board = chess.Board(fen)
        indices = [move_to_index(m) for m in board.legal_moves]
        assert len(indices) == len(set(indices)), "index collision"
        assert all(0 <= i < NUM_MOVES for i in indices)


def test_index_roundtrip():
    """index_to_move(move_to_index(m)) == m for every legal move."""
    board = chess.Board()
    for m in board.legal_moves:
        idx = move_to_index(m)
        assert index_to_move(idx, board) == m


def test_encoder_shape():
    enc = encode_board(chess.Board())
    assert enc["squares"].shape == (64,)
    assert enc["global"].shape == (14,)


def test_model_forward():
    """Shape and finiteness at small scale (CPU-safe)."""
    model = ChessTransformer(
        num_layers=2, d_model=64, nhead=4, dim_feedforward=128,
    ).eval()
    enc = encode_board(chess.Board())
    with torch.no_grad():
        policy, value = model(enc["squares"].unsqueeze(0), enc["global"].unsqueeze(0))
    assert policy.shape == (1, NUM_MOVES)
    assert value.shape == (1, 1)
    assert torch.isfinite(policy).all()
    assert torch.isfinite(value).all()


def test_legal_mask():
    mask = legal_mask(chess.Board())
    assert mask.shape == (NUM_MOVES,)
    assert mask.sum() == 20  # starting position


def test_phase_router_sequential_opening():
    """Opening routing switches from Morphy to Petrosian at ply 9."""
    w1 = route_weights(piece_count=32, ply_number=1)
    w2 = route_weights(piece_count=32, ply_number=10)
    assert "morphy" in w1 and "petrosian" not in w1
    assert "petrosian" in w2 and "morphy" not in w2


def test_style_profiles_validate():
    for name, cfg in STYLE_PROFILES.items():
        # __post_init__ raises if weights don't sum to 1.0
        assert cfg is not None, name


def test_sprt_all_wins():
    from training.sprt import SPRT

    s = SPRT(elo0=0, elo1=5)
    for _ in range(200):
        s.update(1.0)
    assert s.status() == "h1_accepted"


def test_sprt_all_losses():
    from training.sprt import SPRT

    s = SPRT(elo0=0, elo1=5)
    for _ in range(200):
        s.update(0.0)
    assert s.status() == "h0_accepted"


def test_sm2_progression():
    from training.spaced_repetition import update_sm2

    # First perfect review: ef increases, interval 1, reps 1
    ef, iv, rep = update_sm2(2.5, 1, 0, 5)
    assert (ef, iv, rep) == (2.6, 1, 1)

    # Second perfect review: interval becomes 6
    ef, iv, rep = update_sm2(ef, iv, rep, 5)
    assert iv == 6 and rep == 2

    # Failure resets interval to 1
    ef, iv, rep = update_sm2(ef, iv, rep, 1)
    assert iv == 1 and rep == 0


def test_gambit_position_key():
    """Two boards reaching the same position via different move orders
    must have the same FEN-minus-clocks key (transposition-aware)."""
    b1 = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]:
        b1.push_san(san)

    b2 = chess.Board()
    for san in ["Nf3", "Nc6", "e4", "e5", "Bc4", "Bc5"]:
        b2.push_san(san)

    key1 = " ".join(b1.fen().split()[:4])
    key2 = " ".join(b2.fen().split()[:4])
    assert key1 == key2, "transposition detection broken"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
