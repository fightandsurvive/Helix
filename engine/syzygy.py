"""
Syzygy endgame tablebase integration.

Probes at every MCTS node with piece_count <= max_probe_pieces, where
max_probe_pieces is auto-detected from installed tablebase sets:

    3-4-5 installed  → 5
    6 installed      → 6 (plus 3-4-5)
    7 installed      → 7 (plus 6, 3-4-5)

The 6-piece set is ~149 GiB across ~365 files. The 7-piece set is ~16.7
TiB across 1,001 files — realistically a dedicated-storage project. On
reference hardware, install 3-4-5 and 6; 7 only if you buy 20TB of NVMe.

Probing gives perfect endgame values at zero network cost (~2μs per
probe from RAM-cached tables). Without it, the network wastes capacity
on endgame fundamentals it can't fully learn from 13.2M games.

Thread safety: python-chess's Tablebase object is not thread-safe. We
serialize all probes behind a mutex. In MCTS the probe is called once
per node — the mutex never contends because MCTS is single-threaded
per root search. In the panel service, probes are similarly serialized.
If you ever want threaded MCTS, the mutex becomes the bottleneck — at
that point, open one Tablebase per worker thread.
"""
from __future__ import annotations

import threading
from pathlib import Path

import chess
import chess.syzygy

from config.logging_setup import get_logger
from config.settings import settings

log = get_logger(__name__)

_lock = threading.Lock()
_tb: chess.syzygy.Tablebase | None = None
_max_probe_pieces: int = 5


def init_tablebases() -> None:
    """
    Initialize the tablebase set. Explicit call at startup so that
    misconfiguration (missing SYZYGY_PATH, empty subdirectories) fails
    loudly rather than at first probe during a critical search.
    """
    global _tb, _max_probe_pieces
    root = Path(settings.syzygy_path)
    if not root.exists():
        raise FileNotFoundError(f"SYZYGY_PATH not found: {root}")

    paths = [p for p in [root / "3-4-5", root / "6", root / "7"] if p.exists()]
    if not paths:
        raise FileNotFoundError(
            f"No syzygy subdirectories under {root}. "
            f"Expected one of: 3-4-5/, 6/, 7/"
        )

    _tb = chess.syzygy.open_tablebase(str(paths[0]))
    for extra in paths[1:]:
        _tb.add_directory(str(extra))

    # Auto-detect the deepest set installed.
    if (root / "7").exists():
        _max_probe_pieces = 7
    elif (root / "6").exists():
        _max_probe_pieces = 6
    else:
        _max_probe_pieces = 5

    log.info(
        "syzygy_ready",
        paths=[str(p) for p in paths],
        max_probe_pieces=_max_probe_pieces,
    )


def _ensure_initialized() -> None:
    """Lazy init — first probe call triggers init if not already done."""
    global _tb
    if _tb is None:
        init_tablebases()


def probe(board: chess.Board) -> dict | None:
    """
    Probe WDL/DTZ for a position.

    Returns None if piece_count > max_probe_pieces, or if the specific
    material configuration isn't covered by the installed set (which is
    common — the 6-piece set doesn't cover every 6-piece material
    distribution, only the ones with published tables).

    WDL values:
        -2 = loss
        -1 = blessed loss (loss but survives 50-move rule)
         0 = draw
        +1 = cursed win (win but 50-move rule denies)
        +2 = win
    """
    _ensure_initialized()
    if _tb is None or len(board.piece_map()) > _max_probe_pieces:
        return None
    try:
        with _lock:
            return {"wdl": _tb.probe_wdl(board), "dtz": _tb.probe_dtz(board)}
    except (KeyError, chess.syzygy.MissingTableError):
        return None


def probe_move(board: chess.Board, move: chess.Move) -> dict | None:
    """Probe the position after a specific move is played."""
    b = board.copy()
    b.push(move)
    return probe(b)


def max_probe_pieces() -> int:
    """
    Return the maximum number of pieces that will be probed. Used by
    the MCTS to decide whether to probe before evaluating the network.
    """
    _ensure_initialized()
    return _max_probe_pieces
