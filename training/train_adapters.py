"""
Stage 4 — train one LoRA adapter per legend.

Rank scales with dataset size (Morphy 400 games → r=4; Carlsen 4000+
→ r=32). Undertrained legends would overfit at higher ranks; well-
represented legends need the capacity.

After training, the adapter is registered in engine_state.checkpoints
with phase='adapter' so the panel service can discover it.
"""
from __future__ import annotations

import argparse

import chess
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tqdm import tqdm

from common.db import transaction
from config.logging_setup import configure_logging, get_logger
from engine.board_encoder import encode_board
from engine.lora import attach_lora, save_adapter
from engine.model import ChessTransformer
from engine.move_encoder import move_to_index
from engine.reproducibility import ckpt_sha256, seed_everything
from training.dataset import StyleDS

configure_logging()
log = get_logger(__name__)

# Rank and LR scale with dataset size — see engine/lora.py docstring.
RANK_BY_LEGEND = {
    "morphy": 4, "fischer": 8, "petrosian": 8,
    "kasparov": 16, "tal": 16, "carlsen": 32,
}
LR_BY_LEGEND = {
    "morphy": 5e-5, "fischer": 1e-4, "petrosian": 1e-4,
    "kasparov": 1e-4, "tal": 1e-4, "carlsen": 2e-4,
}


def _register_adapter(path: str, legend: str) -> None:
    """Record the adapter in engine_state.checkpoints for panel discovery."""
    sha = ckpt_sha256(path)
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO engine_state.checkpoints
                (path, phase, sha256, config)
            VALUES (%s, 'adapter', %s, %s)
            ON CONFLICT (path) DO UPDATE SET
                sha256 = EXCLUDED.sha256,
                config = EXCLUDED.config
            """,
            (
                path,
                sha,
                f'{{"legend": "{legend}", "rank": {RANK_BY_LEGEND[legend]}}}',
            ),
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument(
        "--legend", required=True,
        choices=["morphy", "petrosian", "tal", "kasparov", "fischer", "carlsen"],
    )
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    seed_everything(args.seed)
    device = torch.device("cuda")

    model = ChessTransformer().to(device)
    model.load_state_dict(torch.load(args.base, map_location=device, weights_only=True))
    model = attach_lora(model, rank=RANK_BY_LEGEND[args.legend])

    ds = StyleDS(args.legend)
    if len(ds) == 0:
        log.error("no_data_for_legend", legend=args.legend)
        return
    dl = DataLoader(ds, batch_size=32, shuffle=True, num_workers=8,
                    pin_memory=True, drop_last=True)

    trainable = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(trainable, lr=LR_BY_LEGEND[args.legend], weight_decay=1e-4)
    ce = nn.CrossEntropyLoss(reduction="none")
    mse = nn.MSELoss()

    model.train()
    for epoch in range(args.epochs):
        for batch in tqdm(dl, desc=f"{args.legend} ep{epoch + 1}"):
            sq = batch["squares"].to(device)
            gf = batch["global"].to(device)
            mt = batch["move_idx"].to(device)
            vt = batch["value"].to(device)
            rw = batch["reward"].to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits, value = model(sq, gf)
                base_ce = ce(logits.float(), mt)
                # Reward-weighted CE — stylistic moves get amplified gradient
                loss = ((1.0 + 0.5 * rw.squeeze(-1)) * base_ce).mean() + mse(value, vt)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            opt.step()

    out = args.out or f"checkpoints/adapter_{args.legend}.pt"
    save_adapter(model, out)

    # ── Held-out validation ────────────────────────────────────────
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for fen, san, _ in ds.val_rows[:500]:
            board = chess.Board(fen)
            enc = encode_board(board)
            sq = enc["squares"].unsqueeze(0).to(device)
            gf = enc["global"].unsqueeze(0).to(device)
            logits, _ = model(sq, gf)
            pred = logits[0].argmax().item()
            correct += int(pred == move_to_index(board.parse_san(san)))
            total += 1

    if total:
        move_match_pct = round(100 * correct / total, 1)
        log.info(
            "adapter_validation",
            legend=args.legend,
            move_match_pct=move_match_pct,
            samples=total,
        )

    _register_adapter(out, args.legend)


if __name__ == "__main__":
    main()
