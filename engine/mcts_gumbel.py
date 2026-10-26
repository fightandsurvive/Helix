"""
Gumbel-AlphaZero root selection (Danihelka et al., ICLR 2022).

Why this exists: PUCT spreads simulations across all root children.
Gumbel-AZ narrows to a small candidate set via Gumbel noise, then does
sequential halving. The paper claims ~3× fewer simulations to reach
equivalent policy quality at low simulation counts (400-800) — exactly
our training regime.

The algorithm:
  1. Draw Gumbel noise g_i ~ Gumbel(0,1) for each root child.
  2. Sample m = 2·ceil(log₂(n)) candidates via argtop_k(logits_i + g_i).
  3. Sequential halving: run sims_per sims through each active candidate,
     keep top half by a combined score of logits + scaled Q.
  4. Repeat until one candidate remains; that's the choice.

`sims_per = total_sims / (active_count · log₂(active_count))` — this
budget schedule gives each round enough simulations to make a
statistically meaningful comparison.

The paper is unambiguous: the improvement over PUCT is measurable. If
our SPRT gate does not show this at 400 sims, the implementation is
wrong — not the paper. Add the A/B test to the orchestrator's gate
suite before trusting this in production.
"""
from __future__ import annotations

import math

import numpy as np


def _gumbel_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """
    Sample n iid Gumbel(0,1) values.

    Gumbel(0,1) CDF: F(x) = exp(-exp(-x)).
    Inverse CDF: x = -log(-log(u)), u ~ Uniform(0,1).
    """
    u = rng.uniform(1e-20, 1.0, size=n)
    return -np.log(-np.log(u))


def run_gumbel_root_search(
    root_logits: np.ndarray,
    root_children: dict,
    total_sims: int,
    rng: np.random.Generator,
    run_sims_for_child,
    c_scale: float = 50.0,
    c_visit: float = 50.0,
) -> int:
    """
    Full Gumbel-AlphaZero root search.

    Args:
        root_logits:      (n,) — raw log-priors for each root child
        root_children:    dict move → Node (with .N, .Q fields)
        total_sims:       total MCTS simulation budget
        rng:              numpy Generator (seeded by caller)
        run_sims_for_child: callable(child_idx, n_sims) that runs n_sims
                          simulations forced through the given root child.
                          Must update child.N and child.Q.
        c_scale, c_visit: schedule hyperparameters (50/50 is standard)

    Returns:
        Index (into root_children's iteration order) of the selected
        root child.
    """
    n = len(root_logits)
    if n == 1:
        return 0

    # ── Step 1: Gumbel noise, top-m candidates ─────────────────────
    g = _gumbel_noise(n, rng)
    top_m = min(n, 2 * max(1, int(math.ceil(math.log2(n)))))
    candidates = np.argsort(-(root_logits + g))[:top_m]

    # ── Step 2: Sequential halving ─────────────────────────────────
    visits = np.zeros(n)
    q_vals = np.zeros(n)
    remaining = list(candidates)

    while len(remaining) > 1:
        # Budget per candidate per round: total_sims / (remaining · log₂(remaining))
        log_factor = max(1, int(math.ceil(math.log2(max(len(remaining), 2)))))
        sims_per = max(1, total_sims // (len(remaining) * log_factor))

        for idx in remaining:
            run_sims_for_child(idx, sims_per)
            child = list(root_children.values())[idx]
            visits[idx] = child.N
            q_vals[idx] = child.Q

        # Combined score: logits + Q, with Q scaled down as visits grow.
        # The (1 + c_visit · N / N_total) denominator ensures early rounds
        # lean on logits (no Q signal yet) and late rounds lean on Q.
        completed = visits > 0
        sigma_q = np.where(completed, q_vals, 0.0)
        scores = root_logits + c_scale * sigma_q / (
            1.0 + c_visit * visits / max(total_sims, 1)
        )

        # Keep top half
        remaining = sorted(remaining, key=lambda i: -scores[i])[
            : max(1, len(remaining) // 2)
        ]

    return remaining[0]
