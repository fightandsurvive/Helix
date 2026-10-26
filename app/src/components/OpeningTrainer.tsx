import { useEffect, useState } from "react";
import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import { useInvoke } from "../hooks/useInvoke";
import { invoke } from "@tauri-apps/api/core";

interface Line {
  line_id: string;
  eco_code: string;
  name: string;
  moves_san: string[];
  fens: string[];
  side: string;
}

type Mode = "visual" | "blind" | "speed" | "streak";

export const OpeningTrainer = () => {
  const { data: lines } = useInvoke<Line[]>("get_due_opening_lines", { limit: 10 });
  const [idx, setIdx] = useState(0);
  const [ply, setPly] = useState(0);
  const [correct, setCorrect] = useState(0);
  const [mode, setMode] = useState<Mode>("visual");
  const [startedAt, setStartedAt] = useState(Date.now());
  const [boardFen, setBoardFen] = useState("");
  const [feedback, setFeedback] = useState("");
  const [blindHidden, setBlindHidden] = useState(false);

  const line = lines?.[idx];

  useEffect(() => {
    if (!line) return;
    setPly(0);
    setCorrect(0);
    setBoardFen(line.fens[0]);
    setStartedAt(Date.now());
    setFeedback("");
    if (mode === "blind") {
      setBlindHidden(true);
      const t = setTimeout(() => setBlindHidden(false), 1500);
      return () => clearTimeout(t);
    }
  }, [idx, line, mode]);

  const finish = async (totalMoves: number) => {
    if (!line) return;
    const timeMs = Date.now() - startedAt;
    try {
      await invoke("record_opening_attempt", {
        a: {
          line_id: line.line_id,
          mode,
          correct_moves: correct + 1,
          total_moves: totalMoves,
          time_ms: timeMs,
        },
      });
    } catch (e) {
      console.error(e);
    }
    setTimeout(() => setIdx((i) => (i + 1) % (lines?.length ?? 1)), 800);
  };

  const handleMove = (from: string, to: string) => {
    if (!line) return false;
    const g = new Chess(boardFen);
    let m;
    try {
      m = g.move({ from, to, promotion: "q" });
    } catch {
      return false;
    }
    if (!m) return false;
    const expected = line.moves_san[ply];
    if (m.san === expected) {
      setCorrect((c) => c + 1);
      setFeedback(`✓ ${m.san}`);
      const next = ply + 1;
      setPly(next);
      if (next >= line.moves_san.length) {
        finish(line.moves_san.length);
        return true;
      }
      setBoardFen(line.fens[next]);
    } else {
      setFeedback(`✗ expected ${expected}`);
      finish(ply + 1);
    }
    return true;
  };

  if (!line) {
    return (
      <div className="opening-trainer">
        <h3>Opening Trainer</h3>
        <div className="empty">No lines due. Load some with ingest.opening_lines.</div>
      </div>
    );
  }

  return (
    <div className="opening-trainer">
      <h3>Opening Trainer</h3>
      <div className="ot-header">
        <span className="ot-eco">{line.eco_code}</span>
        <span className="ot-name">{line.name}</span>
      </div>
      <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
        <option value="visual">Visual</option>
        <option value="blind">Blind</option>
        <option value="speed">Speed</option>
        <option value="streak">Streak</option>
      </select>
      <div style={{ position: "relative", marginTop: 10 }}>
        <Chessboard
          position={boardFen}
          onPieceDrop={handleMove}
          boardWidth={420}
          customDarkSquareStyle={{ backgroundColor: "#0a0a0c" }}
          customLightSquareStyle={{ backgroundColor: "rgba(220,235,245,0.85)" }}
        />
        {blindHidden && <div className="blind-overlay">Memorize…</div>}
      </div>
      <div className="ot-progress">
        {ply}/{line.moves_san.length} — {correct} correct
      </div>
      <div className={`ot-feedback ${feedback.startsWith("✓") ? "ok" : "bad"}`}>
        {feedback}
      </div>
    </div>
  );
};
