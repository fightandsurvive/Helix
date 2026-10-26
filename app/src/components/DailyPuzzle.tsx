import { useEffect, useState } from "react";
import { Chess } from "chess.js";
import { Chessboard } from "react-chessboard";
import { invoke } from "@tauri-apps/api/core";
import { useInvoke } from "../hooks/useInvoke";

interface Daily {
  puzzle_id: string;
  fen: string;
  solution_uci: string;
  rating: number | null;
  themes: string[];
  already_played_today: boolean;
  current_streak: number;
  longest_streak: number;
  total_solved: number;
  total_played: number;
}

export const DailyPuzzle = ({ profileId }: { profileId: number }) => {
  const { data, refetch } = useInvoke<Daily | null>(
    "get_daily_puzzle", { profileId }, { deps: [profileId] }
  );
  const [boardFen, setBoardFen] = useState("");
  const [solIdx, setSolIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!data) return;
    const b = new Chess(data.fen);
    const first = data.solution_uci.split(" ")[0];
    try { b.move({ from: first.slice(0, 2), to: first.slice(2, 4), promotion: first[4] || "q" }); } catch {}
    setBoardFen(b.fen());
    setSolIdx(1);
    setDone(data.already_played_today);
    setFeedback(data.already_played_today ? "Already played today — come back tomorrow!" : "");
  }, [data]);

  if (!data) return <div className="daily-puzzle empty">No puzzle available.</div>;

  const onMove = async (from: string, to: string) => {
    if (done) return false;
    const b = new Chess(boardFen);
    try {
      const m = b.move({ from, to, promotion: "q" });
      if (!m) return false;
      const sol = data.solution_uci.split(" ");
      if (m.from + m.to + (m.promotion || "") === sol[solIdx]) {
        setFeedback("✓ Correct!");
        const next = solIdx + 1;
        if (next >= sol.length) {
          setDone(true);
          await invoke("record_daily_attempt", { profileId, solved: true });
          refetch();
        } else {
          try { b.move({ from: sol[next].slice(0, 2), to: sol[next].slice(2, 4), promotion: sol[next][4] || "q" }); } catch {}
          setBoardFen(b.fen());
          setSolIdx(next + 1);
        }
      } else {
        setFeedback("✗ Wrong");
      }
      return true;
    } catch { return false; }
  };

  const giveUp = async () => {
    setDone(true);
    setFeedback("Better luck tomorrow");
    await invoke("record_daily_attempt", { profileId, solved: false });
    refetch();
  };

  return (
    <div className="daily-puzzle">
      <div className="dp-header">
        <h3>Daily Puzzle</h3>
        <div className="dp-streak">
          🔥 {data.current_streak} (best {data.longest_streak})
        </div>
      </div>
      <div className="dp-meta">
        <span>Rating: {data.rating ?? "—"}</span>
        <span>Themes: {data.themes.slice(0, 3).join(", ")}</span>
      </div>
      <Chessboard position={boardFen} onPieceDrop={onMove} boardWidth={420}
        arePiecesDraggable={!done} />
      <div className={`dp-feedback ${feedback.startsWith("✓") ? "ok" : "bad"}`}>{feedback}</div>
      {!done && <button className="dp-give-up" onClick={giveUp}>Give up</button>}
      <div className="dp-stats">
        {data.total_solved} / {data.total_played} solved
      </div>
    </div>
  );
};
