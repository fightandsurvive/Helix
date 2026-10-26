"""
Per-legend stylistic reward functions.

Dual role:
- Training: multiplied into CE loss during adapter training as
  (1 + α · reward), where α=0.5. Amplifies the gradient on stylistically
  characteristic moves without drowning out the base learning signal.
- Validation: percentage of held-out moves where reward fires. This is
  the metric that determines whether the adapter learned the style
  rather than just memorizing moves.

Each function returns a float in [0, 1].

Design note: `tal` and `kasparov` share a reward function (r_tal_kasparov)
because both are attacking players who sacrifice for the initiative. The
reward fires on sacrifices and center strikes, which are the shared
signature. The adapters are still trained separately so their individual
choice preferences diverge — Tal is more romantic (wilder sacrifices),
Kasparov is more scientific (calculated piece sacrifices to open lines).
"""
from __future__ import annotations

import chess


def _material(board: chess.Board) -> dict[chess.Color, int]:
    """Sum standard material values for each side. Kings excluded."""
    values = {
        chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
        chess.ROOK: 5, chess.QUEEN: 9,
    }
    totals = {chess.WHITE: 0, chess.BLACK: 0}
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if p:
            totals[p.color] += values.get(p.piece_type, 0)
    return totals


def r_morphy(board: chess.Board, move: chess.Move) -> float:
    """
    Rapid minor-piece activation in the first 15 plies.

    Morphy's defining trait: pieces leave their starting squares early to
    maximize activity and tempo. A knight/bishop moving off its home square
    in the opening is his signature.
    """
    if board.ply() > 15:
        return 0.0
    p = board.piece_at(move.from_square)
    if not p or p.piece_type not in (chess.KNIGHT, chess.BISHOP):
        return 0.0
    starts = {
        chess.WHITE: [chess.B1, chess.G1, chess.C1, chess.F1],
        chess.BLACK: [chess.B8, chess.G8, chess.C8, chess.F8],
    }[p.color]
    return 1.0 if move.from_square in starts else 0.0


def r_petrosian(board: chess.Board, move: chess.Move) -> float:
    """
    Prophylaxis proxy: minimize opponent's legal-move count after our move.

    Petrosian's positional genius was restraint — placing pieces so that
    the opponent had no good continuation. A move that reduces opponent's
    mobility by a lot (from e.g. 30 to 15 legal moves) is prophylactic.
    """
    b = board.copy()
    b.push(move)
    return max(0.0, (20 - b.legal_moves.count()) / 20.0)


def r_tal_kasparov(board: chess.Board, move: chess.Move) -> float:
    """
    Sacrifices that trade material for attack + center-file strikes.

    Tal: piece sacrifice to tear open the enemy king position.
    Kasparov: central pawn pushes and calculated piece sacrifices to open
    lines for heavy pieces.

    The reward fires when the move loses material (from mover's perspective)
    and gains either a significant material imbalance (sacrifice) or
    occupies a central file.
    """
    b = board.copy()
    mover = b.turn
    material_before = _material(b)
    b.push(move)
    material_after = _material(b)

    # Positive when the mover gave up more material than they gained
    sac_amount = (
        (material_before[mover] - material_after[mover])
        - (material_before[not mover] - material_after[not mover])
    )
    reward = min(max(sac_amount, 0) / 9.0, 1.0) * 0.7

    # Center-file bonus — d4/d5/e4/e5 are the classic Kasparov strikes
    if move.to_square in (chess.D4, chess.E4, chess.D5, chess.E5):
        reward += 0.3

    return min(reward, 1.0)


def r_fischer(board: chess.Board, move: chess.Move) -> float:
    """
    Bishop-pair preservation + long-diagonal control.

    Fischer famously valued the bishop pair above material symmetry.
    The reward fires when (a) we preserve our bishop pair while the
    opponent doesn't have theirs, and (b) our bishop lands on a long
    diagonal (a1-h8 or a8-h1).
    """
    b = board.copy()
    mover = b.turn
    b.push(move)
    wb = len(b.pieces(chess.BISHOP, chess.WHITE))
    bb = len(b.pieces(chess.BISHOP, chess.BLACK))
    reward = 0.0

    if mover == chess.WHITE and wb >= 2 and bb < 2:
        reward += 0.4
    if mover == chess.BLACK and bb >= 2 and wb < 2:
        reward += 0.4

    long_diag = {
        chess.A1, chess.B2, chess.C3, chess.D4, chess.E5, chess.F6, chess.G7, chess.H8,
        chess.A8, chess.B7, chess.C6, chess.D5, chess.E4, chess.F3, chess.G2, chess.H1,
    }
    piece = b.piece_at(move.to_square)
    if piece and piece.piece_type == chess.BISHOP and move.to_square in long_diag:
        reward += 0.4

    return min(reward, 1.0)


def r_fischer_carlsen_endgame(board: chess.Board, move: chess.Move) -> float:
    """
    Reward opponent pawn weaknesses, penalize own — fires only in endgame.

    Fischer's endgame precision and Carlsen's 0.00-position squeeze share
    a signature: relentlessly target the opponent's structural pawn
    weaknesses (isolated, doubled) while keeping your own structure clean.
    """
    if len(board.piece_map()) > 14:
        return 0.0
    b = board.copy()
    mover = b.turn
    b.push(move)

    pawn_files = {chess.WHITE: [0] * 8, chess.BLACK: [0] * 8}
    for sq in chess.SQUARES:
        p = b.piece_at(sq)
        if p and p.piece_type == chess.PAWN:
            pawn_files[p.color][chess.square_file(sq)] += 1

    def weakness(files: list[int]) -> int:
        doubled = sum(1 for f in files if f >= 2)
        isolated = sum(
            1 for i, f in enumerate(files)
            if f and (i == 0 or not files[i - 1]) and (i == 7 or not files[i + 1])
        )
        return doubled + isolated

    score = 0.15 * weakness(pawn_files[not mover]) - 0.15 * weakness(pawn_files[mover])
    return max(0.0, min(score, 1.0))


# ── Public registry ──────────────────────────────────────────────────
# The keys `tal` and `kasparov` map to the shared reward because both
# adapters learn the same stylistic signature. `fischer_carlsen_endgame`
# is an alias used by the phase router; `carlsen` (the adapter name)
# resolves to the same function.
STYLE_REWARDS = {
    "morphy": r_morphy,
    "petrosian": r_petrosian,
    "tal": r_tal_kasparov,
    "kasparov": r_tal_kasparov,
    "fischer": r_fischer,
    "carlsen": r_fischer_carlsen_endgame,
    # Aliases preserved for compatibility with existing views / configs.
    "tal_kasparov": r_tal_kasparov,
    "fischer_carlsen_endgame": r_fischer_carlsen_endgame,
}
