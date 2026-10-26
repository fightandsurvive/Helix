"""
Helix engine package.

Public modules:
    board_encoder  — chess.Board → model input tensors
    move_encoder   — chess.Move ↔ flat index in [0, 4672)
    model          — ChessTransformer (dense or MoE FFN)
    lora           — low-rank personality adapters
    phase_router   — sequential legend blending by game phase
    style_rewards  — per-legend stylistic reward functions
    fp4            — NVFP4 training conversion (Blackwell)
    syzygy         — 3-4-5 / 6 / 7-piece tablebase integration
    mcts           — PUCT search with Syzygy leaves
    mcts_gumbel    — Gumbel-AlphaZero root selection
    gambit_matcher — transposition-aware gambit detection
    panel_service  — persistent seven-voice analysis daemon
    panel_commentary — LLM synthesis for the Legends panel
    uci_engine     — UCI protocol adapter
    reproducibility — seed management + checkpoint hashing
"""

__version__ = "0.1.0"
