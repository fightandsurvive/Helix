"""
The Legends commentary synthesis.

Takes the structured panel JSON (seven voices, base engine, Stockfish
oracle) and produces a natural-language commentary via DeepSeek-R1.

Prompt engineering notes:
- The instruction is to give a paragraph ONLY for legends whose
  recommendation differs from Stockfish. Legends that agree with the
  objective answer are noted in a consensus line, not a full paragraph.
  This focuses the LLM's output budget on where pedagogy lives.
- The prompt specifies the persona of each legend by name, so the LLM
  attributes the correct instinct ("Tal's tactical sacrifice vision")
  rather than generic "one player prefers..."
- Maximum 5 paragraphs. Hard cap enforced so the panel doesn't overflow
  the UI card.
- Temperature 0.4 — low enough to be factual, high enough to be readable.
  DeepSeek-R1's reasoning trace is discarded; we only consume the final
  response field.

Graceful degradation: if Ollama is unreachable, returns a deterministic
fallback built from the panel data. The panel UI still renders — user
just doesn't get the prose synthesis.
"""
from __future__ import annotations

import requests

from config.logging_setup import get_logger
from config.settings import settings

log = get_logger(__name__)


_PROMPT_TEMPLATE = """You are The Legends — a panel of six historical world champions plus Stockfish analyzing a position together for a serious chess student.

Position FEN: {fen}
Side to move: {side}
Base engine recommendation (Trinstinctus): {base_move} (eval {base_eval})

Legend recommendations:
{legend_lines}

Stockfish (objective oracle, depth {sf_depth}): {sf_move} (eval {sf_eval})

Produce commentary in exactly this structure:

1. **The verdict** (one paragraph): What does the objective analysis say is best? Does the base engine agree with Stockfish?

2. **The legends speak** (one short paragraph per legend that DIFFERS from Stockfish):
   - For each such legend, explain in one or two sentences WHY they prefer
     their move. Reference their characteristic instinct — Morphy's rapid
     development, Petrosian's prophylaxis, Tal's tactical sacrifices,
     Kasparov's center strikes, Fischer's bishop-pair precision, Carlsen's
     positional squeeze. Be specific to THIS position, not generic.
   - Skip legends whose recommendation matches Stockfish — they have nothing
     to add to the verdict.

3. **The consensus** (one paragraph, only if at least two voices agree on a
   move): What moves multiple voices converge on? What does that signal about
   the position?

Hard rules:
- Concise. Maximum five paragraphs total.
- If all legends agree with Stockfish, say so in one sentence and skip
  section 2 entirely.
- Name each legend you describe.
- Grandmaster tone: precise, direct, no filler.
"""


def _fallback_commentary(panel: dict, error: str) -> str:
    """
    Deterministic fallback when the LLM is unreachable.

    Produces a factual summary from the panel data — no pedagogical
    insight, but complete information. This is what renders when Ollama
    is offline or slow.
    """
    base_move = panel["base"]["best_move_san"] or "?"
    sf_voice = next((v for v in panel["voices"] if v["kind"] == "oracle"), None)
    sf_move = sf_voice["best_move_san"] if sf_voice else "?"
    agrees = [
        v["name"] for v in panel["voices"]
        if v["kind"] == "legend" and not v.get("differs_from_sf", True)
    ]
    disagrees = [
        v["name"] for v in panel["voices"]
        if v["kind"] == "legend" and v.get("differs_from_sf", False)
    ]
    parts = [f"The engine recommends {base_move}. Stockfish recommends {sf_move}."]
    if agrees:
        parts.append(f"Agreement from: {', '.join(agrees)}.")
    if disagrees:
        parts.append(f"Divergence from: {', '.join(disagrees)}.")
    parts.append(f"(LLM unavailable: {error})")
    return " ".join(parts)


def generate_commentary(panel: dict) -> str:
    """
    Feed the panel to DeepSeek-R1 and return synthesized prose.

    Called by panel_service when the client requests `commentary`.
    Blocks for ~2s while the LLM generates — the panel service serializes
    requests, so this delays any queued analyze requests by the same
    amount. Acceptable because commentary is triggered by user action
    (button click), not continuously.
    """
    legends = [v for v in panel["voices"] if v["kind"] == "legend"]
    sf_voice = next((v for v in panel["voices"] if v["kind"] == "oracle"), None)

    legend_lines = "\n".join(
        f"- {v['name']}: {v['best_move_san']} "
        f"(eval {v['eval_cp']}cp, policy confidence {v['confidence'] * 100:.0f}%)"
        + (" — DIFFERS from Stockfish" if v.get("differs_from_sf") else " — agrees with Stockfish")
        for v in legends
    ) or "- (no adapters trained yet)"

    sf_line_eval = f"{sf_voice['eval_cp']}cp" if sf_voice else "?"

    prompt = _PROMPT_TEMPLATE.format(
        fen=panel["fen"],
        side=panel["side_to_move"],
        base_move=panel["base"]["best_move_san"] or "?",
        base_eval=f"{panel['base']['eval']:+.2f}",
        legend_lines=legend_lines,
        sf_move=sf_voice["best_move_san"] if sf_voice else "unavailable",
        sf_eval=sf_line_eval,
        sf_depth=panel.get("sf_depth", 22),
    )

    try:
        r = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.4,
                    "num_predict": 800,
                    "top_p": 0.9,
                },
            },
            timeout=60,
        )
        r.raise_for_status()
        text = r.json().get("response", "").strip()
        if not text:
            return _fallback_commentary(panel, "empty response")
        return text
    except Exception as e:
        log.warning("commentary_generation_failed", error=str(e))
        return _fallback_commentary(panel, str(e))
