"""
Persistent panel analysis service.

Loads the base Trinstinctus + all six trained LoRA adapters + Stockfish
once at startup (~10s), then answers analysis requests over stdin/stdout
as JSON lines (~350-600ms per request, dominated by Stockfish).

Protocol:
    stdin:  {"cmd": "analyze", "fen": "..."}
    stdout: {"status": "ok", "panel": {...}} | {"status": "error", "error": "..."}

    stdin:  {"cmd": "commentary", "fen": "..."}
    stdout: {"status": "ok", "panel": {...}, "commentary": "..."}
            (recomputes the panel internally so the LLM sees a
            consistent snapshot, even if the caller sends stale fen)

    stdin:  {"cmd": "health"}
    stdout: {"status": "ok"}

    stdin:  {"cmd": "shutdown"}
    stdout: {"status": "ok"} then exits

Startup behavior: on missing base checkpoint, exits with error to stderr.
On missing adapters, loads only what exists — the panel degrades to
base + SF, which is a fully functional seven-voice setup minus five
specific perspectives.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import chess
import chess.engine
import torch

from config.logging_setup import configure_logging, get_logger
from config.settings import settings
from engine.board_encoder import encode_board
from engine.lora import attach_lora, load_adapter
from engine.mcts import MCTS
from engine.model import ChessTransformer
from engine.move_encoder import index_to_move, legal_mask
from engine.panel_commentary import generate_commentary

configure_logging()
log = get_logger("panel")


# Rank must match the training configuration in training/train_adapters.py.
# If you change a rank, retrain that adapter AND update this dict — the
# LoRA matrices have rank-specific shapes and cannot load into a model
# with a mismatched rank.
RANK_BY_LEGEND = {
    "morphy": 4,
    "fischer": 8,
    "petrosian": 8,
    "kasparov": 16,
    "tal": 16,
    "carlsen": 32,
}

# Order matters for panel rendering — the UI expects a stable sequence.
LEGEND_ORDER = ["morphy", "petrosian", "tal", "kasparov", "fischer", "carlsen"]


def _load_state_dict_compat(path: Path) -> dict:
    """
    Load a state dict tolerating both pure state_dict format and
    {'model': state_dict} wrapper format.

    Pure format uses weights_only=True (safe). Wrapper format requires
    weights_only=False because the dict contains an int (step) alongside
    the tensors — but we only ever load local trusted files, and the
    wrapper form is legacy from earlier iterations of the training
    scripts. Going forward, all training scripts save pure state_dicts.
    """
    try:
        return torch.load(path, map_location="cuda", weights_only=True)
    except Exception:
        loaded = torch.load(path, map_location="cuda", weights_only=False)
        return loaded.get("model", loaded)


def _load_models() -> dict[str, torch.nn.Module]:
    """
    Load base + every adapter that exists on disk.

    Returns a dict: {'base': ChessTransformer, 'morphy': ChessTransformer, ...}
    """
    device = torch.device("cuda")
    ckpt_dir = Path(settings.checkpoints_dir)
    base_ckpt = ckpt_dir / "distilled.pt"
    if not base_ckpt.exists():
        raise RuntimeError(f"Base checkpoint not found: {base_ckpt}")

    base_state = _load_state_dict_compat(base_ckpt)

    models: dict[str, torch.nn.Module] = {}

    # Base model — reference for the panel's "engine's actual answer"
    base = ChessTransformer().to(device).eval()
    base.load_state_dict(base_state)
    models["base"] = base

    # Adapters — one model instance per adapter. Each shares the base
    # weights (loaded into a fresh model) plus its own LoRA A/B matrices.
    loaded: list[str] = []
    for legend, rank in RANK_BY_LEGEND.items():
        adapter_path = ckpt_dir / f"adapter_{legend}.pt"
        if not adapter_path.exists():
            log.warning("adapter_missing", legend=legend, path=str(adapter_path))
            continue
        m = ChessTransformer().to(device).eval()
        m.load_state_dict(base_state)
        m = attach_lora(m, rank=rank)
        load_adapter(m, str(adapter_path))
        m.eval()
        models[legend] = m
        loaded.append(legend)

    log.info(
        "models_loaded",
        base=str(base_ckpt),
        adapters=loaded,
        missing=[l for l in LEGEND_ORDER if l not in loaded],
    )
    return models


def _init_stockfish() -> chess.engine.SimpleEngine:
    """Stockfish subprocess — the objective oracle for the panel."""
    sf = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    sf.configure({"Threads": 8, "Hash": 4096})
    return sf


def _evaluate_policy(
    model: torch.nn.Module,
    board: chess.Board,
) -> tuple[str | None, int, float]:
    """
    One forward pass → (top policy move SAN, eval in centipawns, top prob).

    Used for adapter voices — they express instinct via policy, not
    search. This keeps the panel fast (~30ms per adapter) at the cost
    of ignoring deep tactical consequences. That is the *point* — the
    panel is showing "what would Morphy play on instinct," not "what
    would a Morphy-tuned engine find after 10 minutes of search."
    """
    enc = encode_board(board)
    sq = enc["squares"].unsqueeze(0).cuda()
    gf = enc["global"].unsqueeze(0).cuda()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        logits, value = model(sq, gf)

    mask = legal_mask(board)
    probs = torch.softmax(logits[0].float(), dim=0).cpu().numpy() * mask
    total = probs.sum()
    probs = probs / total if total > 0 else probs
    best_idx = int(probs.argmax())
    best_move = index_to_move(best_idx, board)
    san = board.san(best_move) if best_move else None
    eval_cp = int(value.item() * 400)
    confidence = round(float(probs[best_idx]), 3)
    return san, eval_cp, confidence


def _handle_analyze(
    models: dict[str, torch.nn.Module],
    sf: chess.engine.SimpleEngine,
    req: dict,
) -> dict:
    """
    Produce the panel analysis: base MCTS recommendation + six adapter
    voices + Stockfish oracle + consensus detection.
    """
    fen = req["fen"]
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"invalid FEN: {e}")

    # ── Base recommendation via MCTS ─────────────────────────────
    # 600 sims is enough for an accurate top-move in most positions.
    # Deeper search (3200) would slow the panel to unusable; users who
    # want depth can click "Play vs Engine" to load a full search.
    base_mcts = MCTS(models["base"], num_sims=600)
    base_move, base_root = base_mcts.best_move(board, temperature=0.0)
    base_san = board.san(base_move) if base_move else None
    base_eval = base_root.children[base_move].Q if base_move else 0.0

    # ── Legends: policy top-1 per adapter ────────────────────────
    voices: list[dict] = []
    for name in LEGEND_ORDER:
        if name not in models:
            continue
        san, eval_cp, conf = _evaluate_policy(models[name], board)
        voices.append({
            "name": name.capitalize(),
            "kind": "legend",
            "best_move_san": san,
            "eval_cp": eval_cp,
            "confidence": conf,
        })

    # ── Stockfish oracle ─────────────────────────────────────────
    from typing import cast
    import chess.engine as _ce

    sf_info = sf.analyse(board, chess.engine.Limit(depth=22), multipv=3)
    sf_best_san: str | None = None
    sf_eval: int = 0
    sf_pv: list[str] = []

    for pv in sf_info:
        pv_list = pv.get("pv")
        score_obj = pv.get("score")
        if not pv_list or not isinstance(score_obj, _ce.PovScore):
            continue
        sf_best_san = board.san(cast(list, pv_list)[0])
        rel = score_obj.relative
        cp = rel.score(mate_score=10000)
        sf_eval = int(cp) if cp is not None else 0
        replay = board.copy()
        for mv in cast(list, pv_list)[:5]:
            if replay.is_game_over():
                break
            sf_pv.append(replay.san(mv))
            replay.push(mv)
        break

    if sf_best_san is not None:
        voices.append({
            "name": "Stockfish",
            "kind": "oracle",
            "best_move_san": sf_best_san,
            "eval_cp": sf_eval,
            "pv": sf_pv,
        })

    # ── Tag legends that disagree with SF's objective answer ────
    for v in voices:
        if v["kind"] == "legend":
            v["differs_from_sf"] = v["best_move_san"] != sf_best_san

    # ── Aggregate agreements ────────────────────────────────────
    by_move: dict[str | None, list[str]] = defaultdict(list)
    if base_san:
        by_move[base_san].append("Base")
    for v in voices:
        by_move[v["best_move_san"]].append(v["name"])
    agreements = [
        {"move": mv, "voices": names, "count": len(names)}
        for mv, names in by_move.items()
        if mv is not None and len(names) >= 2
    ]
    agreements.sort(key=lambda x: -x["count"])

    return {
        "fen": fen,
        "side_to_move": "white" if board.turn == chess.WHITE else "black",
        "base": {"best_move_san": base_san, "eval": round(base_eval, 3)},
        "voices": voices,
        "agreements": agreements,
        "sf_depth": 22,
    }


def main() -> None:
    """
    Entry point. Reads commands from stdin as JSON lines, writes responses
    to stdout as JSON lines. Exits on `shutdown` or EOF.
    """
    log.info("panel_service_starting")
    try:
        models = _load_models()
        sf = _init_stockfish()
    except Exception as e:
        log.exception("panel_service_init_failed", error=str(e))
        sys.exit(1)

    log.info("panel_service_ready")
    # stderr signals "ready" — the Rust backend watches for this string
    # before sending the first analyze command.
    sys.stderr.write("READY\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "analyze":
                panel = _handle_analyze(models, sf, req)
                print(json.dumps({"status": "ok", "panel": panel}), flush=True)
            elif cmd == "commentary":
                panel = _handle_analyze(models, sf, req)
                commentary = generate_commentary(panel)
                print(
                    json.dumps({
                        "status": "ok",
                        "panel": panel,
                        "commentary": commentary,
                    }),
                    flush=True,
                )
            elif cmd == "health":
                print(json.dumps({"status": "ok"}), flush=True)
            elif cmd == "shutdown":
                sf.quit()
                print(json.dumps({"status": "ok"}), flush=True)
                break
            else:
                print(
                    json.dumps({"status": "error", "error": f"unknown cmd {cmd}"}),
                    flush=True,
                )
        except Exception as e:
            log.exception("panel_request_failed", error=str(e))
            print(json.dumps({"status": "error", "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
