import { useInvoke } from "../hooks/useInvoke";
import { useEffect } from "react";

interface Analysis {
  best_move: string;
  eval: number;
}

export const EngineArena = ({
  currentFen,
  onEval,
}: {
  currentFen: string;
  onEval: (e: number) => void;
}) => {
  const { data, loading } = useInvoke<Analysis>("analyze_position", {
    fen: currentFen,
  }, { deps: [currentFen] });

  useEffect(() => {
    if (data) onEval(data.eval);
  }, [data, onEval]);

  const evalValue = data?.eval ?? 0;
  const pct = Math.max(0, Math.min(100, (evalValue + 1) * 50));

  return (
    <div className="arena">
      <h3>Engine Arena</h3>
      <div className="eval-bar">
        <div className="eval-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="eval-text">{evalValue.toFixed(2)}</span>
      <div className="best-move">
        {loading ? "analyzing…" : `Best: ${data?.best_move ?? "—"}`}
      </div>
    </div>
  );
};
