"""
Stockfish-based chess coach.

Runs Stockfish analysis on a FEN and generates commentary using the local
Ollama LLM. Returns JSON on stdout matching the schema coach.rs expects:

    {
        "status": "ok",
        "analysis": {
            "best_move_san": str,
            "eval_cp": int,
            "eval_formatted": str,
            "pv_san": [str, ...],
            "side_to_move": "white" | "black"
        },
        "commentary": str,
        "persona": str
    }

Called via `python -m engine.coach <fen> --persona <name>`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import chess
import chess.engine
import requests

from config.logging_setup import configure_logging, get_logger
from config.settings import settings

configure_logging()
log = get_logger("coach")

PERSONAS = {
    "neutral": (
        "a patient, precise chess coach",
        "Focus on concrete tactical ideas and structural features.",
    ),
    "morphy": (
        "Paul Morphy, the romantic attacking genius",
        "Emphasize rapid development, tempo, and open lines.",
    ),
    "petrosian": (
        "Tigran Petrosian, the iron-fortress defender",
        "Emphasize prophylaxis, positional safety, and preventing counterplay.",
    ),
    "tal": (
        "Mikhail Tal, the magician from Riga",
        "Emphasize tactical complications, sacrifices, and attacking ideas.",
    ),
    "kasparov": (
        "Garry Kasparov, the aggressive world champion",
        "Emphasize center control, energetic play, and calculated risk.",
    ),
    "fischer": (
        "Bobby Fischer, the precision-focused world champion",
        "Emphasize bishop pair, positional clarity, and efficient plans.",
    ),
    "carlsen": (
        "Magnus Carlsen, the positional squeeze specialist",
        "Emphasize small advantages, endgame technique, and relentless pressure.",
    ),
}


@dataclass
class Analysis:
    best_move_san: str
    eval_cp: int
    eval_formatted: str
    pv_san: list[str]
    side_to_move: str
    fen: str


def _format_eval(cp: int) -> str:
    if abs(cp) >= 9900:
        return f"#{'+' if cp > 0 else '-'}M"
    return f"{cp / 100:+.2f}"


def run_stockfish(fen: str, depth: int = 18) -> Analysis:
    board = chess.Board(fen)
    engine = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    try:
        engine.configure({"Threads": 4, "Hash": 512})
        info = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=1)
        if not info or not info[0].get("pv"):
            raise RuntimeError("Stockfish returned no PV")
        first = info[0]
        best_move = first["pv"][0]
        eval_cp = first["score"].relative.score(mate_score=10000) or 0
        pv_san: list[str] = []
        replay = board.copy()
        for mv in first["pv"][:8]:
            if replay.is_game_over():
                break
            pv_san.append(replay.san(mv))
            replay.push(mv)
        return Analysis(
            best_move_san=board.san(best_move),
            eval_cp=eval_cp,
            eval_formatted=_format_eval(eval_cp),
            pv_san=pv_san,
            side_to_move="white" if board.turn == chess.WHITE else "black",
            fen=fen,
        )
    finally:
        engine.quit()


def build_prompt(a: Analysis, persona: str) -> str:
    desc, focus = PERSONAS.get(persona, PERSONAS["neutral"])
    return f"""You are speaking as {desc}.

Position (FEN): {a.fen}
Side to move: {a.side_to_move}
Stockfish eval: {a.eval_formatted} (positive = white advantage)
Stockfish top move: {a.best_move_san}
Principal variation: {' '.join(a.pv_san)}

Your focus: {focus}

In 2-4 sentences, explain:
1. What is the key strategic or tactical idea of this position?
2. Why is Stockfish's top move strong?
3. What should the student notice?

Rules:
- Be specific to THIS position.
- No preamble.
- Trust Stockfish's evaluation — explain it, don't contradict it.
"""


def generate_commentary(a: Analysis, persona: str) -> str:
    prompt = build_prompt(a, persona)
    try:
        r = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 300, "top_p": 0.9},
            },
            timeout=40,
        )
        r.raise_for_status()
        text = r.json().get("response", "").strip()
        if text:
            return text
    except Exception as e:
        log.warning("commentary_failed", error=str(e))

    return (
        f"Stockfish recommends {a.best_move_san} "
        f"(eval {a.eval_formatted}). "
        f"Principal variation: {' '.join(a.pv_san) or '(none)'}. "
        f"(LLM commentary unavailable — check that Ollama is running.)"
    )


def main():
    if len(sys.argv) < 2:
        print(
            json.dumps({"status": "error", "error": "usage: python -m engine.coach <fen> [--persona NAME]"})
        )
        sys.exit(1)

    fen = sys.argv[1]
    persona = "neutral"
    if "--persona" in sys.argv:
        idx = sys.argv.index("--persona")
        if idx + 1 < len(sys.argv):
            persona = sys.argv[idx + 1]
    if persona not in PERSONAS:
        persona = "neutral"

    try:
        chess.Board(fen)
    except ValueError as e:
        print(json.dumps({"status": "error", "error": f"invalid FEN: {e}"}))
        sys.exit(1)

    try:
        analysis = run_stockfish(fen)
        commentary = generate_commentary(analysis, persona)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "analysis": {
                        "best_move_san": analysis.best_move_san,
                        "eval_cp": analysis.eval_cp,
                        "eval_formatted": analysis.eval_formatted,
                        "pv_san": analysis.pv_san,
                        "side_to_move": analysis.side_to_move,
                    },
                    "commentary": commentary,
                    "persona": persona,
                }
            )
        )
    except Exception as e:
        log.exception("coach_failed", error=str(e))
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
