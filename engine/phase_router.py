"""
Sequential phase routing across the six-legacy adapter blend.

The core design decision: opening routing is SEQUENTIAL, not simultaneous.

    Plies 1-8:    Morphy alone         (development race)
    Plies 9-16:   Petrosian alone      (consolidation)
    Plies 17+:    Tal + Kasparov + Fischer   (middlegame blend)
    Endgame:      Fischer + Carlsen    (0.00 squeeze)

Why sequential: Morphy and Petrosian are opposites. Morphy races pieces
forward; Petrosian prevents counterplay. A simultaneous 50/50 blend
dilutes both into a weak compromise. Sequential routing captures what
strong players actually execute — race first, then consolidate.

The blend weights are hardcoded defaults exposed as `STYLE_PROFILES`.
Users can override them per-profile via the app's Style Weight dashboard;
those overrides land in `engine_state.style_weights` and are read at
runtime by the panel service and UCI engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import torch


@dataclass(frozen=True)
class PhaseConfig:
    """
    Weights for each phase. Every dict must sum to 1.0 (validated in
    __post_init__). The 'base' key refers to the frozen Trinstinctus
    network; other keys refer to adapter names.

    Splitting opening into two sub-phases (development vs consolidation)
    is the sequential routing decision. Middlegame and endgame are single
    phases.
    """

    opening_development: dict[str, float]  # plies 1-8
    opening_consolidation: dict[str, float]  # plies 9-16
    middlegame: dict[str, float]  # pc >= 12, ply > 16
    endgame: dict[str, float]  # pc < 12

    def __post_init__(self):
        for name in (
            "opening_development",
            "opening_consolidation",
            "middlegame",
            "endgame",
        ):
            weights = getattr(self, name)
            total = sum(weights.values())
            if abs(total - 1.0) > 0.01:
                raise ValueError(f"PhaseConfig.{name} weights sum to {total:.4f}, expected 1.0")


# The default profile — what "balanced" means. Weight on 'base' is high
# (50%) so personality is a flavor, not a takeover.
DEFAULT_CONFIG = PhaseConfig(
    opening_development={"base": 0.5, "morphy": 0.5},
    opening_consolidation={"base": 0.5, "petrosian": 0.5},
    middlegame={"base": 0.4, "tal": 0.2, "kasparov": 0.2, "fischer": 0.2},
    endgame={"base": 0.5, "fischer": 0.25, "carlsen": 0.25},
)


# ── Public API ───────────────────────────────────────────────────────
def piece_phase(piece_count: int) -> str:
    """Coarse phase classification by piece count."""
    if piece_count >= 28:
        return "opening"
    if piece_count >= 12:
        return "middlegame"
    return "endgame"


def route_weights(
    piece_count: int,
    ply_number: int,
    cfg: PhaseConfig = DEFAULT_CONFIG,
) -> Mapping[str, float]:
    """
    Return the adapter blend weights for the current position.

    Opens with Morphy for the development race (plies 1-8), then
    Petrosian for consolidation (plies 9-16). Middlegame and endgame
    are piece-count based.
    """
    phase = piece_phase(piece_count)
    if phase == "opening":
        return cfg.opening_development if ply_number <= 8 else cfg.opening_consolidation
    if phase == "middlegame":
        return cfg.middlegame
    return cfg.endgame


def blend_policies(
    base: torch.Tensor,
    adapters: dict[str, torch.Tensor],
    piece_count: int,
    ply_number: int,
    cfg: PhaseConfig = DEFAULT_CONFIG,
) -> torch.Tensor:
    """
    Weighted blend of base policy and adapter policies.

    `adapters` may be missing some keys — those weights are silently
    skipped. This is what allows the panel to degrade gracefully when
    only some adapters are trained.
    """
    out = torch.zeros_like(base)
    weights = route_weights(piece_count, ply_number, cfg)
    for name, w in weights.items():
        if w == 0.0:
            continue
        if name == "base":
            out = out + w * base
        elif name in adapters:
            out = out + w * adapters[name]
        # Missing adapters silently contribute zero — the total weight
        # may be < 1.0, so we renormalize.
    total = sum(w for name, w in weights.items() if name == "base" or name in adapters)
    return out / total if total > 0 else base


# ── Named profiles exposed to the app UI ─────────────────────────────
STYLE_PROFILES: dict[str, PhaseConfig] = {
    "balanced": DEFAULT_CONFIG,
    "romantic": PhaseConfig(
        opening_development={"base": 0.3, "morphy": 0.7},
        opening_consolidation={"base": 0.4, "morphy": 0.3, "petrosian": 0.3},
        middlegame={"base": 0.3, "tal": 0.4, "kasparov": 0.3},
        endgame={"base": 0.5, "fischer": 0.25, "carlsen": 0.25},
    ),
    "iron_fortress": PhaseConfig(
        opening_development={"base": 0.5, "petrosian": 0.5},
        opening_consolidation={"base": 0.3, "petrosian": 0.7},
        middlegame={"base": 0.4, "petrosian": 0.3, "fischer": 0.3},
        endgame={"base": 0.4, "carlsen": 0.6},
    ),
    "anti_stockfish": PhaseConfig(
        opening_development={"base": 0.5, "morphy": 0.5},
        opening_consolidation={"base": 0.5, "petrosian": 0.5},
        middlegame={"base": 0.3, "tal": 0.35, "kasparov": 0.35},
        endgame={"base": 0.2, "carlsen": 0.8},
    ),
}
