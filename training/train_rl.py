"""
Stage 3 — self-play reinforcement learning.

Per generation:
1. Play N games with MCTS (Gumbel or PUCT) — store visit distribution
   and game result per position.
2. Persist to selfplay.games_gen_N / selfplay.positions_gen_N in one
   transaction per batch (crash-safe — partial batches never persist).
3. Train on the accumulated buffer with negamax value targets.

`--resume` supports crash recovery by checking how many games have
already been written for this generation and playing only the remainder.
"""

from __future__ import annotations

import argparse
import random

import chess
import numpy as np
import torch
import torch.nn as nn
from psycopg2.extras import execute_batch  # type: ignore[import-not-found]
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger
from engine.board_encoder import encode_board
from engine.mcts import MCTS
from engine.model import ChessTransformer
from engine.move_encoder import NUM_MOVES, move_to_index
from engine.reproducibility import seed_everything

configure_logging()
log = get_logger(__name__)


def play_game(
    model: torch.nn.Module,
    mcts: MCTS,
    generation: int,
    max_ply: int = 200,
) -> tuple[list[dict], str, int]:
    """
    Play one self-play game. Returns (samples, result, plies).

    Temperature schedule: plies < 20 use proportional sampling from
    visits (exploration); plies >= 20 use argmax (exploitation). This is
    the standard AlphaZero schedule — explore openings, exploit middlegames.
    """
    board = chess.Board()
    samples: list[dict] = []
    ply = 0
    while not board.is_game_over() and ply < max_ply:
        root = mcts.search(board, add_noise=True)
        if not root.children:
            break
        moves = list(root.children.keys())
        visits = np.array([root.children[m].N for m in moves], dtype=np.float64)

        if board.ply() < 20:
            probs = visits / visits.sum()
        else:
            probs = (visits == visits.max()).astype(np.float64)
            probs /= probs.sum()

        target = np.zeros(NUM_MOVES, dtype=np.float32)
        for m, p in zip(moves, probs, strict=False):
            target[move_to_index(m)] = p

        enc = encode_board(board)
        samples.append(
            {
                "squares": enc["squares"],
                "global": enc["global"],
                "policy": torch.from_numpy(target),
                "turn": board.turn,
                "ply": ply,
                "fen": board.fen(),
            }
        )
        board.push(random.choices(moves, weights=probs)[0])
        ply += 1

    result = board.result() if board.is_game_over() else "*"
    winner = chess.WHITE if result == "1-0" else (chess.BLACK if result == "0-1" else None)
    for s in samples:
        s["value"] = torch.tensor(
            [0.0 if winner is None else (1.0 if s["turn"] == winner else -1.0)],
            dtype=torch.float32,
        )
    return samples, result, ply


def persist_batch(gen: int, batch: list[tuple[list, str, int]], version: str) -> None:
    """
    Insert completed games + positions in one transaction per batch.

    Crash safety: if we crash mid-batch, either the whole batch persists
    or none of it does. That's what makes `--resume` a simple COUNT(*) check.
    """
    with transaction() as cur:
        cur.execute("SELECT selfplay.create_generation_partition(%s)", (gen,))
        cur.execute(
            "SELECT COALESCE(MAX(position_id), 0) FROM selfplay.positions WHERE generation=%s",
            (gen,),
        )
        pid = cur.fetchone()[0]
        for samples, result, ply in batch:
            gid = random.getrandbits(48)
            cur.execute(
                """
                INSERT INTO selfplay.games
                    (generation, game_id, engine_version, result, ply_count)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (gen, gid, version, result, ply),
            )
            rows = []
            for s in samples:
                pid += 1
                pc = len(chess.Board(s["fen"]).piece_map())
                phase = "Opening" if pc >= 28 else ("Middlegame" if pc >= 12 else "Endgame")
                rows.append(
                    (
                        gen,
                        pid,
                        gid,
                        s["ply"],
                        s["fen"],
                        s["policy"].tolist(),
                        s["value"].item(),
                        pc,
                        phase,
                    )
                )
            if rows:
                execute_batch(
                    cur,
                    """
                    INSERT INTO selfplay.positions
                        (generation, position_id, game_id, ply_number, fen_string,
                         policy_target, value_target, piece_count, phase_flag)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                    page_size=200,
                )


class RLDS(Dataset):
    def __init__(self, rows: list):
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, i: int) -> dict:
        fen, policy, value = self.rows[i]
        enc = encode_board(chess.Board(fen))
        return {
            "squares": enc["squares"],
            "global": enc["global"],
            "policy": torch.tensor(policy, dtype=torch.float32),
            "value": torch.tensor([value], dtype=torch.float32),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", required=True)
    ap.add_argument("--generation", type=int, required=True)
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--sims", type=int, default=400)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--save", default="checkpoints/rl.pt")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--use-gumbel", action="store_true", help="Use Gumbel-AlphaZero root selection (SPRT-verify first)"
    )
    args = ap.parse_args()

    seed_everything(args.seed)
    device = torch.device("cuda")
    model = ChessTransformer().to(device)
    model.load_state_dict(torch.load(args.load, map_location=device, weights_only=True))
    mcts = MCTS(model, num_sims=args.sims, use_gumbel=args.use_gumbel)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    mse = nn.MSELoss()

    # ── Self-play (with resume support) ────────────────────────────
    with transaction() as cur:
        cur.execute("SELECT selfplay.create_generation_partition(%s)", (args.generation,))
        cur.execute(
            "SELECT COUNT(*) FROM selfplay.games WHERE generation=%s",
            (args.generation,),
        )
        already_played = cur.fetchone()[0]

    if already_played > 0:
        log.info(
            "selfplay_resume",
            generation=args.generation,
            already_played=already_played,
            remaining=max(0, args.games - already_played),
        )

    remaining = max(0, args.games - already_played)
    if remaining > 0:
        buffer: list = []
        pbar = tqdm(range(remaining), desc=f"gen {args.generation} self-play")
        for _ in pbar:
            samples, result, ply = play_game(model, mcts, args.generation)
            if not samples:
                continue
            buffer.append((samples, result, ply))
            pbar.set_postfix(buffer=len(buffer))
            if len(buffer) >= 50:
                persist_batch(args.generation, buffer, f"gen{args.generation}")
                buffer = []
        if buffer:
            persist_batch(args.generation, buffer, f"gen{args.generation}")

    # ── Training ───────────────────────────────────────────────────
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT fen_string, policy_target, value_target
            FROM selfplay.positions
            WHERE generation=%s
            LIMIT 500000
            """,
            (args.generation,),
        )
        rows = cur.fetchall()

    if not rows:
        log.error("no_training_rows", generation=args.generation)
        return

    dl = DataLoader(RLDS(rows), batch_size=args.batch, shuffle=True, num_workers=8, pin_memory=True)
    model.train()
    for batch in tqdm(dl, desc="train"):
        sq = batch["squares"].to(device)
        gf = batch["global"].to(device)
        pt = batch["policy"].to(device)
        vt = batch["value"].to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits, value = model(sq, gf)
            log_p = torch.log_softmax(logits.float(), dim=-1)
            loss = -(pt * log_p).sum(-1).mean() + mse(value, vt)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()

    torch.save(model.state_dict(), args.save)
    log.info("rl_ckpt_saved", path=args.save, generation=args.generation)


if __name__ == "__main__":
    main()
