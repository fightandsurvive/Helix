import { useInvoke } from "../hooks/useInvoke";
import { motion } from "framer-motion";
import { useState } from "react";

interface CandidateMove {
  source: "stockfish" | "trinstinctus" | "brilliant";
  mv: string;
  san: string;
  eval_cp: number;
  pv: string[];
  explanation: string;
}

interface Analysis {
  fen: string;
  stockfish: CandidateMove | null;
  trinstinctus: CandidateMove | null;
  brilliant: CandidateMove | null;
}

const STYLE = {
  stockfish: { color: "#3b82f6", label: "STOCKFISH", symbol: "♛" },
  trinstinctus: { color: "#22d3ee", label: "TRINSTINCTUS", symbol: "◆" },
  brilliant: { color: "#fbbf24", label: "BRILLIANT", symbol: "✨" },
};

export const GodModePanel = ({
  fen,
  onSelectMove,
}: {
  fen: string;
  onSelectMove: (uci: string) => void;
}) => {
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, loading, error } = useInvoke<Analysis>("analyze_god_mode", { fen }, {
    deps: [fen],
  });

  const candidates: CandidateMove[] = data
    ? ([data.stockfish, data.trinstinctus, data.brilliant].filter(
        (m): m is CandidateMove => m !== null
      ) as CandidateMove[])
    : [];

  return (
    <div className="god-mode-panel">
      <div className="gm-header">
        <span className="gm-title">GOD MODE</span>
        {loading && <span className="gm-loading">analyzing…</span>}
      </div>
      {error && <div className="gm-error">{error}</div>}
      {candidates.length === 0 && !loading && (
        <div className="gm-empty">No candidates — play a move or wait.</div>
      )}
      {candidates.map((c) => {
        const s = STYLE[c.source];
        const isOpen = expanded === c.source;
        return (
          <motion.div
            key={c.source}
            className="gm-card"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            style={{ borderLeftColor: s.color }}
          >
            <button
              className="gm-card-header"
              onClick={() => {
                onSelectMove(c.mv);
                setExpanded(isOpen ? null : c.source);
              }}
              aria-expanded={isOpen}
            >
              <span className="gm-source" style={{ color: s.color }}>
                {s.symbol} {s.label}
              </span>
              <span className="gm-san">{c.san}</span>
              <span className={`gm-eval ${c.eval_cp >= 0 ? "pos" : "neg"}`}>
                {c.eval_cp >= 0 ? "+" : ""}
                {(c.eval_cp / 100).toFixed(2)}
              </span>
            </button>
            {isOpen && (
              <div className="gm-explanation">
                <p>{c.explanation}</p>
                {c.pv.length > 0 && (
                  <div className="gm-pv">
                    <span className="gm-pv-label">PV:</span>
                    <span className="gm-pv-moves">{c.pv.join(" ")}</span>
                  </div>
                )}
              </div>
            )}
          </motion.div>
        );
      })}
    </div>
  );
};
