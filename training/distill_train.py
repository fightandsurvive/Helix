"""
Stage 2b — train on a Stockfish-distilled corpus.

Same architecture as supervised, different targets:
    value target = SF eval (centipawns → tanh(cp/400) → [-1, 1])
    policy target = SF best move (hard label)

Two invocations chain:
    bulk:       --base supervised.pt      --tsv bulk.tsv --out distilled_bulk.pt
    refinement: --base distilled_bulk.pt  --tsv refine.tsv --out distilled.pt

Learning rate is 1e-4 (half of supervised) — this is fine-tuning, not
from-scratch training. Larger LR would destroy the supervised foundation.
"""
from __future__ import annotations

import argparse

import chess
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm

from config.logging_setup import configure_logging, get_logger
from engine.board_encoder import encode_board
from engine.fp4 import enable_nvfp4_if_possible
from engine.model import ChessTransformer
from engine.move_encoder import move_to_index
from engine.reproducibility import seed_everything

configure_logging()
log = get_logger(__name__)


class DistillDS(Dataset):
    def __init__(self, tsv_path: str):
        self.rows: list[list[str]] = []
        with open(tsv_path) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    self.rows.append(parts)
        log.info("distill_dataset_loaded", path=tsv_path, rows=len(self.rows))

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, i: int) -> dict:
        fen, eval_cp, san, *_ = self.rows[i]
        board = chess.Board(fen)
        enc = encode_board(board)
        try:
            move = board.parse_san(san)
            move_idx = move_to_index(move)
        except Exception:
            move_idx = 0  # bad row contributes near-zero gradient
        value = max(-1.0, min(1.0, float(eval_cp) / 400.0))
        return {
            "squares": enc["squares"],
            "global": enc["global"],
            "move_idx": torch.tensor(move_idx, dtype=torch.long),
            "value": torch.tensor([value], dtype=torch.float32),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", required=True)
    ap.add_argument("--base", default=None,
                    help="Optional base checkpoint to continue training from")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--out", default="checkpoints/distilled.pt")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    seed_everything(args.seed)
    device = torch.device("cuda")
    model = ChessTransformer().to(device)
    if args.base:
        model.load_state_dict(torch.load(args.base, map_location=device, weights_only=True))
        log.info("base_loaded", path=args.base)
    model = enable_nvfp4_if_possible(model)

    ds = DistillDS(args.tsv)
    dl = DataLoader(
        ds, batch_size=args.batch, shuffle=True, num_workers=12,
        pin_memory=True, drop_last=True, persistent_workers=True,
    )
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    ce, mse = nn.CrossEntropyLoss(), nn.MSELoss()

    for epoch in range(args.epochs):
        model.train()
        for batch in tqdm(dl, desc=f"distill epoch {epoch + 1}"):
            sq = batch["squares"].to(device, non_blocking=True)
            gf = batch["global"].to(device, non_blocking=True)
            mt = batch["move_idx"].to(device, non_blocking=True)
            vt = batch["value"].to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                policy, value = model(sq, gf)
                loss = ce(policy, mt) + mse(value, vt)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

        torch.save(model.state_dict(), args.out)
        log.info("distill_ckpt_saved", path=args.out)


if __name__ == "__main__":
    main()
