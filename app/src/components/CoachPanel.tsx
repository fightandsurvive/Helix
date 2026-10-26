import { useState } from "react";
import { useInvoke } from "../hooks/useInvoke";
import { motion, AnimatePresence } from "framer-motion";

interface Analysis {
    best_move_san: string;
    eval_cp: number;
    eval_formatted: string;
    pv_san: string[];
    side_to_move: string;
}

interface CoachResponse {
    analysis: Analysis;
    commentary: string;
    persona: string;
}

const PERSONAS = [
    { id: "neutral", label: "Neutral Coach" },
    { id: "morphy", label: "Morphy" },
    { id: "petrosian", label: "Petrosian" },
    { id: "tal", label: "Tal" },
    { id: "kasparov", label: "Kasparov" },
    { id: "fischer", label: "Fischer" },
    { id: "carlsen", label: "Carlsen" },
];

export const CoachPanel = ({ fen }: { fen: string }) => {
    const [persona, setPersona] = useState("neutral");
    const [expanded, setExpanded] = useState(false);

    const { data, loading, error, refetch } = useInvoke<CoachResponse>(
        "coach_analyze",
        { fen, persona },
        { immediate: expanded, deps: [fen, persona, expanded] }
    );

    if (!expanded) {
        return (
            <button className="legends-invoke" onClick={() => setExpanded(true)}>
                ◈ Consult the Coach
            </button>
        );
    }

    return (
        <div className="coach-panel">
            <div className="lp-header">
                <span className="lp-title">THE COACH</span>
                <button className="lp-collapse" onClick={() => setExpanded(false)}>
                    collapse
                </button>
            </div>

            <div className="coach-persona-row">
                <label>Persona:</label>
                <select value={persona} onChange={(e) => setPersona(e.target.value)}>
                    {PERSONAS.map((p) => (
                        <option key={p.id} value={p.id}>
                            {p.label}
                        </option>
                    ))}
                </select>
                <button onClick={refetch} className="coach-refresh" disabled={loading}>
                    {loading ? "…" : "↻"}
                </button>
            </div>

            {loading && <div className="lp-loading">Consulting Stockfish + LLM…</div>}
            {error && <div className="lp-error">{error}</div>}

            <AnimatePresence>
                {data && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                        <div className="lp-base">
                            <span className="lp-base-label">Stockfish</span>
                            <span className="lp-base-move">{data.analysis.best_move_san}</span>
                            <span className="lp-base-eval">{data.analysis.eval_formatted}</span>
                        </div>

                        {data.analysis.pv_san.length > 0 && (
                            <div className="coach-pv">
                                <span className="coach-pv-label">PV:</span>
                                <span className="coach-pv-moves">
                                    {data.analysis.pv_san.join(" ")}
                                </span>
                            </div>
                        )}

                        <div className="lp-commentary">{data.commentary}</div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};
