"""
Post-training validation for all six adapters.

Reports per-legend move-match %. Flags:
    < 40%  → undertrained (rank too small, LR too low, or data quality issue)
    > 75%  → possible overfit / validation leak

Panel service does not require this script to pass to run — it just
shows fewer voices. But the Style Weight dashboard gate uses
VALIDATION_THRESHOLDS to enable blending only when all six pass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import chess
import torch

from config.logging_setup import configure_logging, get_logger
from engine.board_encoder import encode_board
from engine.lora import attach_lora, load_adapter
from engine.model import ChessTransformer
from engine.move_encoder import move_to_index
from training.dataset import StyleDS
from training.train_adapters import RANK_BY_LEGEND

configure_logging()
log = get_logger(__name__)

LEGENDS = ["morphy", "petrosian", "tal", "kasparov", "fischer", "carlsen"]

# Below this move-match %, the adapter is treated as "not ready" by the
# app's Style Weight dashboard gate. Above 75% is suspicious.
VALIDATION_THRESHOLDS = {
    "morphy": 45,
    "petrosian": 45,
    "tal": 50,
    "kasparov": 50,
    "fischer": 50,
    "carlsen": 50,
}


def _validate_one(base_ckpt: str, legend: str, adapter_path: Path) -> tuple[float, int]:
    device = torch.device("cuda")
    model = ChessTransformer().to(device).eval()
    model.load_state_dict(torch.load(base_ckpt, map_location=device, weights_only=True))
    model = attach_lora(model, rank=RANK_BY_LEGEND[legend])
    load_adapter(model, str(adapter_path))

    ds = StyleDS(legend)
    if not ds.val_rows:
        return 0.0, 0

    correct = total = 0
    with torch.no_grad():
        for fen, san, _ in ds.val_rows[:1000]:
            board = chess.Board(fen)
            enc = encode_board(board)
            sq = enc["squares"].unsqueeze(0).to(device)
            gf = enc["global"].unsqueeze(0).to(device)
            logits, _ = model(sq, gf)
            pred = logits[0].argmax().item()
            correct += int(pred == move_to_index(board.parse_san(san)))
            total += 1
    return (100 * correct / max(total, 1), total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--adapters-dir", default="checkpoints")
    args = ap.parse_args()

    log.info("validation_start")
    for legend in LEGENDS:
        path = Path(args.adapters_dir) / f"adapter_{legend}.pt"
        if not path.exists():
            log.warning("adapter_missing", legend=legend)
            continue
        pct, n = _validate_one(args.base, legend, path)
        threshold = VALIDATION_THRESHOLDS[legend]
        status = "ok"
        if pct < threshold:
            status = "UNDERTRAINED"
        elif pct > 75:
            status = "OVERFIT"
        log.info(
            "adapter_result",
            legend=legend,
            move_match_pct=round(pct, 1),
            samples=n,
            threshold=threshold,
            status=status,
        )


if __name__ == "__main__":
    main()
