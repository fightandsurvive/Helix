"""
Datasets for training pipelines.

SupervisedDS — reads GM games from the legend-first partitioned warehouse.
                Queries hit the parent partitioned table, so the planner
                prunes to the correct legend partition automatically.
StyleDS      — per-legend dataset for adapter training, with style reward
                attached to each sample.
"""
from __future__ import annotations

import chess
import torch
from torch.utils.data import Dataset

from common.db import read_cursor
from engine.board_encoder import encode_board
from engine.move_encoder import move_to_index
from engine.style_rewards import STYLE_REWARDS


def _result_to_value(result: str, turn: chess.Color) -> float:
    """PGN result → side-to-move value in [-1, 1]."""
    if result == "1-0":
        return 1.0 if turn == chess.WHITE else -1.0
    if result == "0-1":
        return 1.0 if turn == chess.BLACK else -1.0
    return 0.0


from torch.utils.data import IterableDataset

class SupervisedDS(IterableDataset):
    def __init__(self, legend: str = "general", limit: int | None = None):
        self.legend = legend
        self.limit = limit

    def __iter__(self):
        # Server-side cursor — fetches in chunks of `itersize` rows.
        # Memory footprint: one chunk at a time (~10K rows × 250 bytes = 2.5MB).
        with read_cursor(name=f"supervised_{uuid.uuid4().hex[:8]}") as cur:
            cur.itersize = 10_000
            q = """SELECT m.fen_string, m.move_notation, g.result
                   FROM gm.moves m JOIN gm.games g ..."""
            cur.execute(q, [self.legend])
            for fen, san, result in cur:
                yield self._make_sample(fen, san, result)

class StyleDS(Dataset):
    """
    Per-legend dataset for adapter training.

    90/10 chronological split: earlier games train, later games validate.
    Chronological (not random) is deliberate — it forces the adapter to
    generalize to the legend's *later* style, not just memorize any
    particular opening fashion.
    """

    def __init__(self, legend: str, limit: int | None = None):
        with read_cursor() as cur:
            q = """
                SELECT m.fen_string, m.move_notation, g.result
                FROM gm.moves m
                JOIN gm.games g
                  ON g.game_id = m.game_id
                 AND g.legend_tag = m.legend_tag
                WHERE m.legend_tag = %s
                ORDER BY g.date_played NULLS LAST, m.ply_number
            """
            params: list = [legend]
            if limit:
                q += " LIMIT %s"
                params.append(limit)
            cur.execute(q, params)
            rows = cur.fetchall()

        split = int(len(rows) * 0.9)
        self.train_rows = rows[:split]
        self.val_rows = rows[split:]
        self.reward_fn = STYLE_REWARDS.get(legend)

    def __len__(self) -> int:
        return len(self.train_rows)

    def __getitem__(self, idx: int) -> dict:
        fen, san, result = self.train_rows[idx]
        board = chess.Board(fen)
        move = board.parse_san(san)
        enc = encode_board(board)
        reward = self.reward_fn(board, move) if self.reward_fn else 0.0
        return {
            "squares": enc["squares"],
            "global": enc["global"],
            "move_idx": torch.tensor(move_to_index(move), dtype=torch.long),
            "value": torch.tensor(
                [_result_to_value(result, board.turn)], dtype=torch.float32
            ),
            "reward": torch.tensor([reward], dtype=torch.float32),
        }
