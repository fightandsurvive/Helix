import { useInvoke } from "../hooks/useInvoke";
import { motion } from "framer-motion";
import { useState } from "react";

interface CandidateMove {
    source: "stockfish" | "brilliant" | "alternative";
    mv: string;
    san: string;
    eval_cp: number;
    pv: string[];
    explanation: string;
}

interface Analysis {
    fen: string;
    stockfish: CandidateMove | null;
    brilliant: CandidateMove | null;
    alternatives: CandidateMove[];
}

const STYLE: Record<string, { color: string; label: string; symbol: string }> = {
    stockfish: { color: "#3b82f6", label: "STOCKFISH", symbol: "♛" },
    brilliant: { color: "#fbbf24", label: "BRILLIANT", symbol: "✨" },
    alternative: { color: "#9ca3af", label: "ALTERNATIVE", symbol: "·" },
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

    // KEY FIX: filter null AND undefined with != null.
    // Using !== null let undefined slip through, then STYLE[c.source]
    // threw "Cannot read properties of undefined (reading 'source')".
    const candidates: CandidateMove[] = data
        ? ([data.stockfish, data.brilliant, ...(data.alternatives ?? [])]
            .filter((m): m is CandidateMove => m != null) as CandidateMove[])
        : [];

    return (
        <div className="god-mode-panel">
            <div className="gm-header">
                <span className="gm-title">GOD MODE</span>
                {loading && <span className="gm-loading">analyzing…</span>}
            </div>
            {error && <div className="gm-error">{error}</div>}
            {!loading && candidates.length === 0 && !error && (
                <div className="gm-empty">No candidates — play a move or wait.</div>
            )}
            {candidates.map((c) => {
                const s = STYLE[c.source] ?? STYLE.alternative;
                const isOpen = expanded === c.mv;
                return (
                    <motion.div
                        key={`${c.source}_${c.mv}`}
                        className="gm-card"
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        style={{ borderLeftColor: s.color }}
                    >
                        <button
                            className="gm-card-header"
                            onClick={() => {
                                onSelectMove(c.mv);
                                setExpanded(isOpen ? null : c.mv);
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
