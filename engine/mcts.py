"""
PUCT Monte Carlo Tree Search.

Standard AlphaZero search:

    select:   argmax_a [ Q(s,a) + c_puct · P(s,a) · sqrt(N(s)) / (1 + N(s,a)) ]
    expand:   evaluate leaf via network, or via Syzygy if probeable
    backup:   N(s,a) += 1, W(s,a) += v, Q(s,a) = W/N  (negamax across tree)

The Syzygy branch (any node with <= max_probe_pieces) gives perfect
endgame value at zero network cost. This is the single biggest
architectural improvement over naive AlphaZero for chess — the network
never has to learn endgame theory, and search never produces endgame
blunders.

Optional Gumbel-AlphaZero: when `use_gumbel=True`, root selection uses
the Gumbel algorithm (see mcts_gumbel.py). This is currently opt-in
behind an SPRT gate — the paper claims ~3× sample efficiency at
400-800 sims, but we validate this claim experimentally before
changing the default.
"""
from __future__ import annotations

import math

import chess
import numpy as np
import torch

from config.logging_setup import get_logger
from engine import syzygy
from engine.board_encoder import encode_board
from engine.move_encoder import legal_mask, move_to_index

log = get_logger(__name__)


class Node:
    """
    One MCTS node. `__slots__` reduces per-node memory by ~40% vs a
    dataclass, which matters when the tree has 100K+ nodes per search.

    Children are stored in a plain dict rather than a dense array because
    the branching factor varies wildly (0 in terminal, ~30 typical, up to
    218 in extreme tactical positions). Dict iteration is fast for the
    small-N case which dominates.
    """

    __slots__ = ("prior", "parent", "children", "N", "W", "Q")

    def __init__(self, prior: float, parent: Node | None = None):
        self.prior = prior
        self.parent = parent
        self.children: dict[chess.Move, Node] = {}
        self.N = 0
        self.W = 0.0
        self.Q = 0.0


class MCTS:
    """
    PUCT search with Syzygy leaf grounding.

    num_sims defaults to 800 in evaluation, 400 in self-play training.
    c_puct=1.5 for training (more exploration), 1.8 for evaluation (more
    exploitation) — this split is standard in the AlphaZero community.
    """

    def __init__(
        self,
        model: torch.nn.Module,
        c_puct: float = 1.5,
        num_sims: int = 800,
        dirichlet_alpha: float = 0.3,
        noise_frac: float = 0.25,
        use_gumbel: bool = False,
    ):
        self.model = model.eval()
        self.c_puct = c_puct
        self.num_sims = num_sims
        self.dirichlet_alpha = dirichlet_alpha
        self.noise_frac = noise_frac
        self.use_gumbel = use_gumbel
        self._gumbel_rng_seed = 0

    # ── Network evaluation ─────────────────────────────────────────
    @torch.no_grad()
    def _eval_net(self, board: chess.Board) -> tuple[np.ndarray, float]:
        """
        Single-position forward pass. Returns (policy softmax over all
        4672 actions, value in [-1, 1]). Legal masking happens in the
        caller during expansion.

        Batching note: MCTS naturally has a batch-1 request pattern
        unless we implement batched MCTS (which Lc0 does, using virtual
        loss). Batched MCTS is a 3-5× throughput improvement but a
        substantial rewrite. For v1, batch-1 is simpler and correct;
        optimize later if profiling shows network eval to be the
        bottleneck (it usually is not — tree traversal dominates for
        small nets).
        """
        enc = encode_board(board)
        sq = enc["squares"].unsqueeze(0).cuda(non_blocking=True)
        gf = enc["global"].unsqueeze(0).cuda(non_blocking=True)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits, value = self.model(sq, gf)
        policy = torch.softmax(logits[0].float(), dim=0).cpu().numpy()
        return policy, value.item()

    # ── Tree operations ────────────────────────────────────────────
    def _expand(self, node: Node, board: chess.Board) -> float:
        """
        Expand a leaf node: probe Syzygy, else forward-pass the network.

        Returns the leaf's value (side-to-move perspective) for backup.
        """
        tb = syzygy.probe(board)
        if tb is not None:
            # Perfect endgame value — no network call needed.
            # Uniform prior over legal moves (the search will handle
            # move selection; Syzygy is a value oracle, not a policy).
            wdl = tb["wdl"]
            value = 1.0 if wdl > 0 else (-1.0 if wdl < 0 else 0.0)
            policy = None
        else:
            policy, value = self._eval_net(board)

        legal = list(board.legal_moves)
        if not legal:
            return value

        if policy is None:
            priors = np.full(len(legal), 1.0 / len(legal))
        else:
            priors = np.array([policy[move_to_index(m)] for m in legal], dtype=np.float64)
            total = priors.sum()
            priors = priors / total if total > 0 else np.full(len(legal), 1.0 / len(legal))

        for m, p in zip(legal, priors):
            node.children[m] = Node(prior=float(p), parent=node)
        return value

    def _select(self, node: Node) -> tuple[chess.Move, Node]:
        """PUCT selection at one node. Returns (move, child)."""
        best_score = -math.inf
        best: tuple[chess.Move, Node] | None = None
        sqrt_n = math.sqrt(max(node.N, 1))
        for m, child in node.children.items():
            score = child.Q + self.c_puct * child.prior * sqrt_n / (1 + child.N)
            if score > best_score:
                best_score = score
                best = (m, child)
        assert best is not None
        return best

    def _backup(self, node: Node | None, value: float) -> None:
        """
        Negamax backup — flip sign per level. `value` starts as
        side-to-move's value at the leaf; each parent sees -value because
        the parent's side-to-move is the opponent.
        """
        while node is not None:
            node.N += 1
            node.W += value
            node.Q = node.W / node.N
            node = node.parent
            value = -value

    # ── Public search entry point ──────────────────────────────────
    def search(
        self,
        board: chess.Board,
        add_noise: bool = False,
        style_prior_fn=None,
    ) -> Node:
        """
        Run self.num_sims simulations from this board. Returns the root
        node (children hold the visit distribution used as training target).

        add_noise:       Dirichlet noise at root (self-play only)
        style_prior_fn:  optional callable(move, prior) → prior, used by
                         the phase router to blend legend priors at the
                         root only (interior nodes use pure network priors)
        """
        root = Node(prior=1.0)
        self._expand(root, board)
        if not root.children:
            return root

        if add_noise:
            moves = list(root.children.keys())
            noise = np.random.dirichlet([self.dirichlet_alpha] * len(moves))
            for m, n in zip(moves, noise):
                c = root.children[m]
                c.prior = (1 - self.noise_frac) * c.prior + self.noise_frac * float(n)

        if style_prior_fn is not None:
            for m, c in root.children.items():
                c.prior = style_prior_fn(m, c.prior)
            total = sum(c.prior for c in root.children.values())
            if total > 0:
                for c in root.children.values():
                    c.prior /= total

        # Gumbel-AZ root — separate path that overrides the PUCT loop
        # for the ROOT level only. Interior nodes still use PUCT.
        if self.use_gumbel:
            return self._search_gumbel(root, board)

        for _ in range(self.num_sims):
            node = root
            sim_board = board.copy()
            while node.children and not sim_board.is_game_over():
                m, node = self._select(node)
                sim_board.push(m)

            if sim_board.is_game_over():
                # Draw = 0, decisive = the mover who just lost is -1
                # (from their perspective; the position is theirs now)
                result = sim_board.result()
                value = 0.0 if result == "1/2-1/2" else -1.0
            else:
                value = self._expand(node, sim_board)

            self._backup(node, value)

        return root

    def _search_gumbel(self, root: Node, board: chess.Board) -> Node:
        """Gumbel-AlphaZero root search (see mcts_gumbel.py)."""
        from engine.mcts_gumbel import run_gumbel_root_search

        moves = list(root.children.keys())
        root_logits = np.array(
            [math.log(max(root.children[m].prior, 1e-9)) for m in moves],
            dtype=np.float64,
        )

        # Seeded RNG for reproducibility of individual searches. The seed
        # is incremented per search so successive calls explore differently.
        self._gumbel_rng_seed += 1
        rng = np.random.default_rng(self._gumbel_rng_seed)

        def run_sims_for_child(child_idx: int, n_sims: int):
            """Force n_sims simulations through root.children[moves[child_idx]]."""
            child = root.children[moves[child_idx]]
            for _ in range(n_sims):
                node = child
                sim_board = board.copy()
                sim_board.push(moves[child_idx])
                while node.children and not sim_board.is_game_over():
                    m, node = self._select(node)
                    sim_board.push(m)
                if sim_board.is_game_over():
                    result = sim_board.result()
                    v = 0.0 if result == "1/2-1/2" else -1.0
                else:
                    v = self._expand(node, sim_board)
                self._backup(node, v)

        best_idx = run_gumbel_root_search(
            root_logits, root.children, self.num_sims, rng, run_sims_for_child,
        )
        # Populate root's visit counts by running a final pass through
        # the selected child (or just trust the sequential halving).
        # The visit-count-based policy target comes from root.children[*].N,
        # which was accumulated during the halving. We do not need to
        # force additional visits.
        _ = best_idx
        return root

    # ── Best move selection ────────────────────────────────────────
    def best_move(
        self,
        board: chess.Board,
        temperature: float = 0.0,
        style_prior_fn=None,
        add_noise: bool = False,
    ) -> tuple[chess.Move | None, Node]:
        """
        Return (best move, root node). temperature=0 gives argmax visits;
        temperature>0 samples proportionally (used in self-play).

        The root node is returned so callers can extract the policy
        (visit distribution) for training targets.
        """
        root = self.search(board, add_noise=add_noise, style_prior_fn=style_prior_fn)
        if not root.children:
            return None, root

        moves = list(root.children.keys())
        visits = np.array([root.children[m].N for m in moves], dtype=np.float64)

        if temperature == 0.0:
            return moves[int(visits.argmax())], root

        probs = visits ** (1.0 / temperature)
        probs /= probs.sum()
        return moves[int(np.random.choice(len(moves), p=probs))], root
