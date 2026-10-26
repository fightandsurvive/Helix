"""
Stage 1 — supervised bootstrap on the GM warehouse.

Goal: ~2900 Elo. Teaches legal moves, opening patterns, basic tactical
vision. Bootstrap is necessary because 2M distillation positions cannot
teach chess from scratch — they don't cover openings or endgames.

Saves pure state_dict to checkpoints/supervised.pt — weights_only=True
compatible for every downstream consumer.
"""
from __future__ import annotations

import argparse

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm

from config.logging_setup import configure_logging, get_logger
from engine.fp4 import enable_nvfp4_if_possible
from engine.model import ChessTransformer
from engine.reproducibility import seed_everything
from training.dataset import SupervisedDS

configure_logging()
log = get_logger(__name__)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--use-moe", action="store_true")
    ap.add_argument("--num-layers", type=int, default=24)
    ap.add_argument("--out", default="checkpoints/supervised.pt")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    seed_everything(args.seed)
    device = torch.device("cuda")
    model = ChessTransformer(num_layers=args.num_layers, use_moe=args.use_moe).to(device)
    log.info(
        "model_init",
        params_M=round(sum(p.numel() for p in model.parameters()) / 1e6, 1),
        use_moe=args.use_moe,
    )

    model = enable_nvfp4_if_possible(model)

    ds = SupervisedDS(legend="general", limit=args.limit)
    dl = DataLoader(
        ds, batch_size=args.batch, shuffle=True, num_workers=args.workers,
        pin_memory=True, drop_last=True, persistent_workers=True,
    )

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    total_steps = args.epochs * len(dl)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=total_steps, pct_start=0.03,
    )
    ce, mse = nn.CrossEntropyLoss(), nn.MSELoss()
    writer = SummaryWriter("logs/supervised")
    step = 0

    for epoch in range(args.epochs):
        model.train()
        for batch in tqdm(dl, desc=f"epoch {epoch + 1}"):
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
            sched.step()
            step += 1

            if step % 100 == 0:
                writer.add_scalar("loss/total", loss.item(), step)
                writer.add_scalar("lr", sched.get_last_lr()[0], step)

        # Pure state_dict — weights_only=True compatible downstream.
        torch.save(model.state_dict(), args.out)
        log.info("checkpoint_saved", path=args.out, step=step)

    writer.close()


if __name__ == "__main__":
    main()
