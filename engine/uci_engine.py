"""
UCI protocol adapter — exposes Trinstinctus to chess GUIs and match runners.

Standalone process: reads UCI commands on stdin, writes responses on
stdout. Handles uci, isready, position (fen + moves), go, quit.

Enterprise considerations:
- Command parsing is defensive — malformed input doesn't crash the
  engine. Invalid FENs, illegal moves, and unknown commands are logged
  and ignored; the engine stays alive until `quit`.
- The `go` handler blocks until search completes. This matches the
  behaviour every GUI expects from an engine.
- Checkpoints are loaded once at startup. If the file is missing or
  corrupt, the engine exits with a clear error to stderr.

Usage from a GUI or match runner:
    python -m engine.uci_engine checkpoints/distilled.pt 800
    (second arg is num_sims, defaults to 800)
"""

from __future__ import annotations

import sys

import chess
import torch

from config.logging_setup import configure_logging, get_logger
from engine.mcts import MCTS
from engine.model import ChessTransformer

configure_logging()
log = get_logger("uci")


class UCIEngine:
    def __init__(self, ckpt_path: str, sims: int = 800):
        self.board = chess.Board()
        self.model = ChessTransformer().cuda().eval()

        # Tolerate both pure state_dict and wrapped {'model': state_dict}.
        try:
            state = torch.load(ckpt_path, map_location="cuda", weights_only=True)
        except Exception:
            state = torch.load(ckpt_path, map_location="cuda", weights_only=False)
            state = state.get("model", state)
        self.model.load_state_dict(state)

        self.mcts = MCTS(self.model, num_sims=sims)
        log.info("uci_ready", ckpt=ckpt_path, sims=sims)

    def _handle_position(self, parts: list[str]) -> None:
        """
        Parse `position [startpos|fen ...] [moves ...]`. Malformed input
        is logged and the board stays at its previous state — better to
        play the last valid position than to crash.
        """
        if "startpos" in parts:
            self.board = chess.Board()
        elif "fen" in parts:
            idx = parts.index("fen") + 1
            end = parts.index("moves") if "moves" in parts else len(parts)
            fen_str = " ".join(parts[idx:end])
            try:
                self.board = chess.Board(fen_str)
            except ValueError as e:
                log.warning("uci_invalid_fen", fen=fen_str, err=str(e))
                return

        if "moves" in parts:
            for mv_str in parts[parts.index("moves") + 1 :]:
                try:
                    self.board.push_uci(mv_str)
                except ValueError:
                    log.warning("uci_invalid_move", move=mv_str)
                    break

    def _handle_go(self) -> None:
        """Run search and emit bestmove. Blocks until search completes."""
        move, _ = self.mcts.best_move(self.board, temperature=0.0)
        sys.stdout.write(f"bestmove {move.uci() if move else '0000'}\n")
        sys.stdout.flush()

    def run(self) -> None:
        for line in sys.stdin:
            parts = line.strip().split()
            if not parts:
                continue
            cmd = parts[0]

            if cmd == "uci":
                sys.stdout.write(
                    "id name Trinstinctus\n"
                    "id author Helix\n"
                    "option name Hash type spin default 4096 min 1 max 65536\n"
                    "uciok\n"
                )
                sys.stdout.flush()
            elif cmd == "isready":
                sys.stdout.write("readyok\n")
                sys.stdout.flush()
            elif cmd == "position":
                self._handle_position(parts)
            elif cmd == "go":
                self._handle_go()
            elif cmd == "quit":
                break
            else:
                # Unknown command — ignore, per UCI spec.
                log.debug("uci_unknown_command", cmd=cmd)


if __name__ == "__main__":
    ckpt = sys.argv[1] if len(sys.argv) > 1 else "checkpoints/distilled.pt"
    sims = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    UCIEngine(ckpt, sims).run()
